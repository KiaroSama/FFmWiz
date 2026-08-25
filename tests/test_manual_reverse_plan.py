"""Regression: the manual export must be the plan that actually runs (B04).

The summary printed the ordinary one-shot command and called it "ready to run
manually". For a Join or Split reverse that command carries a full-timeline
`reverse` filter which execution never uses -- running it by hand buffers the
whole timeline, which is precisely what the staged plan exists to avoid. And
declining execution returned from `run_one_job()` before the staged plan was
ever built, so the user was handed a different job from the one FFmWiz would
have run.

    printed command   [0:v:0]reverse,setpts=(PTS-STARTPTS)/1,...   one command
    actual execution  reverse segment -> concat -> split           several

The repair exports a runnable PowerShell plan listing the real stages in order
with stop-on-error, and says plainly that the single command is a reference.

The test below is the one that keeps the exporter honest: it declines a real
Split + reverse job, runs the exported script in a FRESH PowerShell process,
and compares the resulting media to the automatic path. A planner that drifted
from the executor would produce different files and fail here.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts
from ffmwiz import encoding

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")
class TheExportedPlanMatchesTheAutomaticRun(NoLeakedArtifacts, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_manualplan_"))
        cls.source = cls._tmp / "src.mkv"
        subprocess.run(
            [FFMPEG, "-v", "error", "-y",
             "-f", "lavfi", "-i", "color=c=red:size=160x120:rate=30:duration=2",
             "-f", "lavfi", "-i", "color=c=blue:size=160x120:rate=30:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
             "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
             "-map", "[v]", "-map", "2:a",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(cls.source)],
            check=True, capture_output=True, timeout=300)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _probe(self, path):
        return json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120).stdout)

    def _colour_at(self, path, at):
        raw = subprocess.run(
            [FFMPEG, "-v", "error", "-ss", f"{at:.3f}", "-i", str(path),
             "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=120).stdout
        if len(raw) < 3:
            return "missing"
        red, _green, blue = raw[0], raw[1], raw[2]
        if red > blue + 40:
            return "red"
        if blue > red + 40:
            return "blue"
        return f"other({red},{blue})"

    def _answers(self, out):
        info = self._probe(self.source)
        return self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.source,
            "probe": info, "format": info["format"], "output_location": out,
            "video_streams": [s for s in info["streams"] if s["codec_type"] == "video"],
            "audio_streams": [s for s in info["streams"] if s["codec_type"] == "audio"],
            "subtitle_streams": [], "output_ext": "mkv", "audio_tracks": [0],
            "color_range_choice": "tv",
            "video_encoder": "libx264", "crf": 28, "preset": "ultrafast",
            "audio_codec": "aac",
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True, "separator_points": [2.0],
        })

    def _describe(self, directory):
        return [(part.name,
                 round(float(self._probe(part)["format"]["duration"]), 1),
                 self._colour_at(part, 0.5))
                for part in sorted(directory.glob("*Part*.mkv"))]

    def _build(self, out):
        answers = self._answers(out)
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            answers["cmd"] = [str(p) for p in FFmWiz.build_ffmpeg_command(answers)]
            answers["output_path"] = (answers.get("split_output_paths")
                                      or [out / "o.mkv"])[0]
        return answers

    def test_the_printed_command_is_not_the_plan(self):
        # Guard the guard: if the one-shot command stopped carrying a
        # full-timeline reverse, the whole defect would be gone and the
        # assertions below would prove nothing.
        out = self._tmp / "printed"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        answers = self._build(out)
        filters = [answers["cmd"][index + 1]
                   for index, part in enumerate(answers["cmd"][:-1])
                   if part in {"-vf", "-filter:v", "-filter_complex"}]
        self.assertTrue(any("reverse" in value for value in filters),
                        "the reference command should still show the simple form")

    def test_the_plan_lists_several_stages(self):
        out = self._tmp / "stages"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        answers = self._build(out)
        stages = encoding.bounded_reverse_plan(answers, out / "scratch")
        self.assertGreaterEqual(len(stages), 3,
                                f"expected a multi-stage plan, got {stages}")

    def test_no_stage_reverses_an_unbounded_timeline(self):
        out = self._tmp / "bounded"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        answers = self._build(out)
        stages = encoding.bounded_reverse_plan(answers, out / "scratch")
        for label, cmd in stages:
            filters = [cmd[index + 1] for index, part in enumerate(cmd[:-1])
                       if part in {"-vf", "-filter:v", "-filter_complex"}]
            if not any("reverse" in value for value in filters):
                continue
            with self.subTest(stage=label):
                first_input = cmd.index("-i")
                self.assertIn("-t", cmd[:first_input],
                              f"{label} reverses without bounding its decode")

    @unittest.skipUnless(POWERSHELL, "powershell required to run the exported plan")
    def test_running_the_exported_plan_reproduces_the_automatic_output(self):
        automatic = self._tmp / "auto"
        shutil.rmtree(automatic, ignore_errors=True)
        automatic.mkdir()
        answers = self._build(automatic)
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            code, _elapsed = encoding.execute_encode_plan(
                answers, answers["cmd"], total_duration=4.0, label="test")
            FFmWiz.release_artifacts(answers)
        self.assertEqual(0, code, noise.getvalue()[-1500:])
        expected = self._describe(automatic)
        self.assertTrue(expected, "the automatic run produced no parts")

        manual = self._tmp / "manual"
        shutil.rmtree(manual, ignore_errors=True)
        manual.mkdir()
        declined = self._build(manual)
        with redirect_stdout(noise), redirect_stderr(noise):
            export = encoding.export_bounded_reverse_plan(
                declined, Path(declined["output_path"]))
        self.assertTrue(export.succeeded, f"no plan was exported: {export.error}")
        script = export.script
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script)],
            capture_output=True, text=True, timeout=900)
        self.assertEqual(0, result.returncode,
                         f"the exported plan failed:\n{result.stderr[-2000:]}")
        self.assertEqual(expected, self._describe(manual),
                         "the manual plan produced different media from the "
                         "automatic path")

    def test_the_plan_stops_on_the_first_failure(self):
        out = self._tmp / "stoponerror"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        answers = self._build(out)
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            export = encoding.export_bounded_reverse_plan(
                answers, Path(answers["output_path"]))
        text = Path(export.script).read_text(encoding="utf-8")
        self.assertIn("$ErrorActionPreference = 'Stop'", text)
        self.assertIn("if ($LASTEXITCODE -ne 0)", text)

    def test_the_plan_names_the_scratch_directory_to_remove(self):
        out = self._tmp / "scratchnote"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        answers = self._build(out)
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            export = encoding.export_bounded_reverse_plan(
                answers, Path(answers["output_path"]))
        text = Path(export.script).read_text(encoding="utf-8")
        self.assertIn("Scratch files live in:", text)

    def _plan_with_segments(self, out, **extra):
        """Plan a job forced into SEVERAL segments, and return the scratch dir.

        With one segment the concat order is unobservable, so the budget is
        pinned small. The order is what turns per-chunk forward encodes into a
        reversed whole; writing it in source order silently un-reverses the job.
        """
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        answers = self._build(out)
        answers.update(extra)
        scratch = out / "scratch"
        real_budget = encoding.reverse_segment_seconds
        encoding.reverse_segment_seconds = lambda _answers: 1.0
        try:
            stages = encoding.bounded_reverse_plan(answers, scratch)
        finally:
            encoding.reverse_segment_seconds = real_budget
        segments = [label for label, _cmd in stages
                    if label.startswith("Reverse segment")]
        self.assertGreaterEqual(len(segments), 3,
                                f"expected several segments, got {segments}")
        return scratch

    def _listed_order(self, listing):
        self.assertTrue(listing.exists(), f"the plan wrote no {listing.name}")
        return [line.split("'")[1] for line
                in listing.read_text(encoding="utf-8").splitlines()
                if line.startswith("file ")]

    def test_the_video_concat_list_is_written_back_to_front(self):
        # Audio left forward: the planner splits into a video list and an
        # audio list, and only the video one is reversed.
        scratch = self._plan_with_segments(self._tmp / "order_split")
        order = self._listed_order(scratch / "concat.txt")
        self.assertEqual(sorted(order, reverse=True), order,
                         "the video concat list must run back to front")
        forward = self._listed_order(scratch / "concat_audio.txt")
        self.assertEqual(sorted(forward), forward,
                         "the audio list must stay in source order")

    def test_the_single_concat_list_is_written_back_to_front(self):
        # Audio reversed too: one list feeds both streams. This is a DIFFERENT
        # branch of the planner, and it was reachable by no other test here.
        scratch = self._plan_with_segments(self._tmp / "order_single",
                                           audio_speed_from_video=True,
                                           reverse_audio=True)
        order = self._listed_order(scratch / "concat.txt")
        self.assertEqual(sorted(order, reverse=True), order,
                         "the concat list must run back to front")
        # The two branches share `concat.txt`; the separate AUDIO list is what
        # only the video-reversed-audio-forward branch writes.
        self.assertFalse((scratch / "concat_audio.txt").exists(),
                         "a reversed-audio job needs no separate audio list")

    def test_declining_a_staged_job_writes_the_plan(self):
        # Through the PUBLIC dispatcher. Every other test here calls the
        # exporter directly, so none of them would notice the decline branch
        # quietly dropping it.
        out = self._tmp / "declined"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        prepared = dict(self._answers(out))
        prepared["start_now"] = False
        real_menu = FFmWiz.ask_main_menu
        real_wizard = FFmWiz.run_wizard
        real_plan = FFmWiz.print_ffmpeg_processing_plan
        FFmWiz.ask_main_menu = lambda answers, config_path: 1
        FFmWiz.print_ffmpeg_processing_plan = lambda *a, **k: None

        def fake_wizard(answers, config=None):
            answers.update(prepared)
            answers["cmd"] = FFmWiz.build_ffmpeg_command(answers)
            answers["output_path"] = (answers.get("split_output_paths")
                                      or [out / "o.mkv"])[0]

        FFmWiz.run_wizard = fake_wizard
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                FFmWiz.run_one_job({}, out / "cfg.json")
        finally:
            FFmWiz.ask_main_menu = real_menu
            FFmWiz.run_wizard = real_wizard
            FFmWiz.print_ffmpeg_processing_plan = real_plan
        exported = list(out.glob("*.plan.ps1"))
        self.assertTrue(exported,
                        "declining a staged job exported no runnable plan")

    def test_the_decline_message_does_not_call_it_the_command(self):
        out = self._tmp / "declinedtext"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        prepared = dict(self._answers(out))
        prepared["start_now"] = False
        notes = []
        real_menu = FFmWiz.ask_main_menu
        real_wizard = FFmWiz.run_wizard
        real_plan = FFmWiz.print_ffmpeg_processing_plan
        real_note = FFmWiz.appio.note
        FFmWiz.ask_main_menu = lambda answers, config_path: 1
        FFmWiz.print_ffmpeg_processing_plan = lambda *a, **k: None
        FFmWiz.appio.note = notes.append

        def fake_wizard(answers, config=None):
            answers.update(prepared)
            answers["cmd"] = FFmWiz.build_ffmpeg_command(answers)
            answers["output_path"] = (answers.get("split_output_paths")
                                      or [out / "o.mkv"])[0]

        FFmWiz.run_wizard = fake_wizard
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                FFmWiz.run_one_job({}, out / "cfg.json")
        finally:
            FFmWiz.ask_main_menu = real_menu
            FFmWiz.run_wizard = real_wizard
            FFmWiz.print_ffmpeg_processing_plan = real_plan
            FFmWiz.appio.note = real_note
        joined = " ".join(notes).lower()
        self.assertIn("several commands", joined)
        self.assertNotIn("the command above is ready to run manually", joined)

    def test_an_ordinary_job_gets_no_plan_file(self):
        # A single-command job is genuinely reproducible by that command; an
        # export there would be noise.
        out = self._tmp / "ordinary"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        answers = self._answers(out)
        answers.pop("separator_points")
        answers["reverse_video"] = False
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            answers["cmd"] = [str(p) for p in FFmWiz.build_ffmpeg_command(answers)]
        self.assertEqual([], list(out.glob("*.plan.ps1")))


if __name__ == "__main__":
    unittest.main()
