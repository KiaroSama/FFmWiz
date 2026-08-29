"""Regression: every public audio reverse reaches the bounded plan (D13).

`areverse` buffers its whole input, so an audio reverse is unbounded for the
same reason a video one is. The two standalone audio tools were given a bounded
executor; the three paths that run through `execute_encode_plan` were not:

    main encode with audio reverse but no video reverse
    Join with audio reverse only
    Folder Encode with audio reverse

Those reached `run_ffmpeg_with_progress` with the one-shot command. Measured on
a six-hour synthetic input before the repair:

    AREVERSE_COUNT 1   INPUT_COUNT 1
    BOUNDS_PRESENT False   SEGMENT_OR_MEMORY_POLICY False

`execute_encode_plan` is the one dispatcher all three share, which is where the
routing belongs -- the same argument that put the segmented VIDEO reverse there
after Folder Encode was found calling the runner directly (R09).
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
from ffmwiz import reverse_pipeline
from ffmwiz import runtime

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

SECONDS = 4.0
# One tone per second, so the ORDER of the finished audio is readable.
TONES = (440, 880, 1320, 1760)


def _run(args, timeout=600):
    return subprocess.run([str(part) for part in args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace", timeout=timeout)


class EveryPathIsBounded(NoLeakedArtifacts, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not (FFMPEG and FFPROBE):
            raise unittest.SkipTest("ffmpeg/ffprobe not on PATH")
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_audioroute_"))
        cls.source = cls._root / "tones.mkv"
        graph = "".join(
            f"sine=frequency={tone}:duration=1[t{index}];"
            for index, tone in enumerate(TONES))
        graph += "".join(f"[t{index}]" for index in range(len(TONES)))
        graph += f"concat=n={len(TONES)}:v=0:a=1[a]"
        result = _run([
            FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=red:s=160x120:r=30:d={SECONDS}",
            "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "flac", cls.source])
        if result.returncode != 0:
            raise unittest.SkipTest(f"could not build the source: {result.stderr[-400:]}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="audioroute_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", self._real_note))

    def _probe(self, path, *args):
        return json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                *args, path]).stdout or "{}")

    def _answers(self, out, **extra):
        info = self._probe(self.source, "-show_format", "-show_streams")
        streams = info["streams"]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.source,
            "probe": info, "format": info["format"], "streams": streams,
            "output_location": out, "output_ext": "mkv",
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [], "attachment_streams": [], "data_streams": [],
            "audio_tracks": [0], "color_range_choice": "tv",
            "video_encoder": "libx264", "crf": 28, "preset": "ultrafast",
            "audio_codec": "flac",
            "audio_speed_enabled": True, "audio_speed_factor": 1.0,
            "reverse_audio": True,
        })
        answers.update(extra)
        FFmWiz.artifact_lease(answers)
        return answers

    def _routed(self, **extra):
        """Which executor `execute_encode_plan` chose, without running it.

        The watched name matters: `run_bounded_audio_reverse` is the standalone
        AUDIO TOOLS' plan and this router never calls it, so a stub on that
        name reports the one-shot command the real encode helper ends with --
        indistinguishable from no bounding at all.
        """
        out = self._tmp / "routing"
        out.mkdir(parents=True, exist_ok=True)
        answers = self._answers(out, **extra)
        answers["output_path"] = out / "routed.mkv"
        chosen = []
        real_bounded = encoding.run_bounded_audio_reverse_encode
        real_runner = runtime.run_ffmpeg_with_progress
        real_two_pass = encoding.run_cpu_two_pass_ffmpeg
        encoding.run_bounded_audio_reverse_encode = (
            lambda *a, **k: (chosen.append("bounded audio") or (0, 0.0)))
        runtime.run_ffmpeg_with_progress = (
            lambda *a, **k: (chosen.append("one shot") or (0, 0.0)))
        encoding.run_cpu_two_pass_ffmpeg = (
            lambda *a, **k: (chosen.append("two pass") or (0, 0.0)))
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(dict(answers))]
                encoding.execute_encode_plan(answers, cmd, total_duration=SECONDS,
                                             label="routing")
        finally:
            encoding.run_bounded_audio_reverse_encode = real_bounded
            runtime.run_ffmpeg_with_progress = real_runner
            encoding.run_cpu_two_pass_ffmpeg = real_two_pass
        return chosen

    # ---- routing ---------------------------------------------------------
    def test_a_main_encode_audio_reverse_is_bounded(self):
        self.assertEqual(["bounded audio"], self._routed())

    def test_a_join_reversing_only_its_audio_is_bounded(self):
        item = {"path": self.source,
                "probe": self._probe(self.source, "-show_format", "-show_streams")}
        item["format"] = item["probe"]["format"]
        item["streams"] = item["probe"]["streams"]
        item["video_streams"] = [s for s in item["streams"] if s["codec_type"] == "video"]
        item["audio_streams"] = [s for s in item["streams"] if s["codec_type"] == "audio"]
        item["subtitle_streams"] = []
        item["data_streams"] = []
        item["duration"] = SECONDS
        self.assertEqual(["bounded audio"], self._routed(join_input_items=[item]))

    def test_a_split_reversing_only_its_audio_is_bounded(self):
        self.assertEqual(["bounded audio"], self._routed(separator_points=[2.0]))

    def test_a_video_reverse_still_goes_to_the_segmented_plan(self):
        # Guard the guard: the new branch must not swallow the VIDEO route.
        #
        # Note for whoever mutation-tests this: broadening the branch to
        # `reverse_audio or reverse_video` changes nothing observable, because
        # the two video branches are evaluated first and a video reverse never
        # reaches it. The `and not reverse_video` clause is there to survive a
        # future reordering, not because it is reachable today.
        out = self._tmp / "video"
        out.mkdir(parents=True, exist_ok=True)
        answers = self._answers(out, reverse_video=True,
                                video_speed_enabled=True, video_speed_factor=1.0)
        answers["output_path"] = out / "video.mkv"
        chosen = []
        real_segmented = reverse_pipeline.run_segmented_reverse_main_encode
        real_bounded = encoding.run_bounded_audio_reverse_encode
        reverse_pipeline.run_segmented_reverse_main_encode = (
            lambda *a, **k: (chosen.append("segmented video") or (0, 0.0)))
        encoding.run_bounded_audio_reverse_encode = (
            lambda *a, **k: (chosen.append("bounded audio") or (0, 0.0)))
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(dict(answers))]
                encoding.execute_encode_plan(answers, cmd, total_duration=SECONDS,
                                             label="video")
        finally:
            reverse_pipeline.run_segmented_reverse_main_encode = real_segmented
            encoding.run_bounded_audio_reverse_encode = real_bounded
        self.assertEqual(["segmented video"], chosen)

    def test_an_ordinary_encode_is_untouched(self):
        # Guard the guard: nothing that does not reverse audio may be diverted.
        self.assertEqual(["one shot"], self._routed(reverse_audio=False))

    # ---- content ---------------------------------------------------------
    def _tone_at(self, path, at):
        """The dominant frequency near `at`, by Goertzel over decoded PCM."""
        raw = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
             "-ss", f"{at:.3f}", "-i", str(path), "-t", "0.30",
             "-map", "0:a:0", "-f", "s16le", "-acodec", "pcm_s16le",
             "-ar", "8000", "-ac", "1", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300).stdout
        samples = [int.from_bytes(raw[i:i + 2], "little", signed=True)
                   for i in range(0, len(raw) - 1, 2)]
        if len(samples) < 256:
            return None
        best, best_power = None, 0.0
        import math
        for tone in TONES:
            omega = 2 * math.pi * tone / 8000
            coeff = 2 * math.cos(omega)
            s1 = s2 = 0.0
            for sample in samples:
                s0 = sample + coeff * s1 - s2
                s2, s1 = s1, s0
            power = s1 * s1 + s2 * s2 - coeff * s1 * s2
            if power > best_power:
                best, best_power = tone, power
        return best

    def test_the_bounded_plan_still_reverses_the_content(self):
        # Several chunks, so the ORDER of the reassembled audio is observable.
        out = self._tmp / "content"
        out.mkdir(parents=True, exist_ok=True)
        answers = self._answers(out)
        # Patch the module that DEFINES it: `run_bounded_audio_reverse` resolves
        # the name from its own globals, and both `FFmWiz` and `encoding` only
        # re-export it.
        from ffmwiz.support import ext04c
        real_budget = ext04c.audio_reverse_segment_seconds
        ext04c.audio_reverse_segment_seconds = lambda _a, _i=None: 1.0
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                # The BUILDER resolves the output path and writes it onto the
                # dict it is given. Building into a copy and then reading the
                # outer dict points at a file nothing ever wrote.
                cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
                code, _elapsed = encoding.execute_encode_plan(
                    answers, cmd, total_duration=SECONDS, label="content")
        finally:
            ext04c.audio_reverse_segment_seconds = real_budget
        self.assertEqual(0, code, noise.getvalue()[-1500:])
        produced = Path(answers["output_path"])
        heard = [self._tone_at(produced, at) for at in (0.35, 1.35, 2.35, 3.35)]
        self.assertEqual(list(reversed(TONES)), heard,
                         "the bounded plan did not reverse the tones")

    def test_the_fixture_really_runs_forwards(self):
        # Guard the guard: if the source were already reversed, or the tones
        # indistinguishable, the assertion above would prove nothing.
        heard = [self._tone_at(self.source, at) for at in (0.35, 1.35, 2.35, 3.35)]
        self.assertEqual(list(TONES), heard)


if __name__ == "__main__":
    unittest.main()
