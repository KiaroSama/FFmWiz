"""Regression: the PRIMARY dispatcher must not delete what it just printed (F03).

`run_one_job()` says "the command above is ready to run manually" and then
called `cleanup_join_concat_list()` + `cleanup_encode_chapter_metadata()`. Only
the standalone Mode 12 branch in `modes_join.py` had been repaired, so the
existing regression inspected that file and never saw this one.

Reproduced through the public path -- register a generated input, set
`start_now=False`, call `run_one_job()`:

    ROOT_EXISTS_AFTER_RETURN  false
    AUX_EXISTS_AFTER_RETURN   false

The test below is deliberately end-to-end: it declines a real chaptered cut
job, then RUNS the printed command in a fresh subprocess. A command that only
survives as text is not "ready to run manually".
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


class DeclineKeepsLeasedInputs(unittest.TestCase):
    """The lease contract, without an encode."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_manual1_"))
        self._real_menu = FFmWiz.ask_main_menu
        self._real_wizard = FFmWiz.run_wizard
        FFmWiz.ask_main_menu = lambda answers, config_path: 1

    def tearDown(self):
        FFmWiz.ask_main_menu = self._real_menu
        FFmWiz.run_wizard = self._real_wizard
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _decline_with(self, register):
        def fake_wizard(answers, config=None):
            answers["cmd"] = ["ffmpeg", "-i", "in.mkv", "out.mkv"]
            answers["start_now"] = False
            register(answers)

        FFmWiz.run_wizard = fake_wizard
        FFmWiz.run_one_job({}, self._tmp / "cfg.json")

    def test_a_leased_generated_input_survives_the_return(self):
        kept = {}

        def register(answers):
            path = self._tmp / "joined.srt"
            path.write_text("1\n", encoding="utf-8")
            kept["path"] = FFmWiz.artifact_lease(answers).register(path)

        self._decline_with(register)
        self.assertTrue(kept["path"].exists(),
                        "the primary dispatcher deleted a generated input")

    def test_the_concat_list_survives_too(self):
        concat = self._tmp / "list.ffconcat"

        def register(answers):
            concat.write_text("ffconcat version 1.0\n", encoding="utf-8")
            answers["_join_concat_list"] = str(concat)

        self._decline_with(register)
        self.assertTrue(concat.exists())

    def test_the_chapter_metadata_directory_survives(self):
        kept = {}

        def register(answers):
            directory = Path(tempfile.mkdtemp(prefix="ffmwiz_encode_chapters_",
                                              dir=self._tmp))
            (directory / "chapters.ffmetadata").write_text(
                ";FFMETADATA1\n", encoding="utf-8")
            kept["dir"] = FFmWiz.artifact_lease(answers).register(directory)

        self._decline_with(register)
        self.assertTrue(kept["dir"].exists())

    def test_accepting_still_cleans_up(self):
        # Preserving must not become the default for a job that really runs.
        kept = {}
        real_runner = FFmWiz.run_ffmpeg_with_progress
        real_plan = FFmWiz.print_ffmpeg_processing_plan
        FFmWiz.run_ffmpeg_with_progress = lambda cmd, **kwargs: (0, 0.0)
        FFmWiz.print_ffmpeg_processing_plan = lambda *a, **k: None

        def fake_wizard(answers, config=None):
            answers["cmd"] = ["ffmpeg", "-i", "in.mkv", "out.mkv"]
            answers["start_now"] = True
            answers["output_path"] = self._tmp / "out.mkv"
            path = self._tmp / "scratch.srt"
            path.write_text("1\n", encoding="utf-8")
            kept["path"] = FFmWiz.artifact_lease(answers).register(path)

        FFmWiz.run_wizard = fake_wizard
        try:
            FFmWiz.run_one_job({}, self._tmp / "cfg.json")
        finally:
            FFmWiz.run_ffmpeg_with_progress = real_runner
            FFmWiz.print_ffmpeg_processing_plan = real_plan
        self.assertFalse(kept["path"].exists(),
                         "a job that ran must still clean its scratch files")


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")
class ThePrintedCommandActuallyRuns(unittest.TestCase):
    """End-to-end: decline, then execute the command in a fresh process."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_manual1_run_"))
        self._real_menu = FFmWiz.ask_main_menu
        self._real_wizard = FFmWiz.run_wizard
        FFmWiz.ask_main_menu = lambda answers, config_path: 1

    def tearDown(self):
        FFmWiz.ask_main_menu = self._real_menu
        FFmWiz.run_wizard = self._real_wizard
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _chaptered_source(self):
        meta = self._tmp / "ch.txt"
        meta.write_text(
            ";FFMETADATA1\n"
            "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=2000\ntitle=ONE\n"
            "[CHAPTER]\nTIMEBASE=1/1000\nSTART=2000\nEND=4000\ntitle=TWO\n",
            encoding="utf-8", newline="\n")
        source = self._tmp / "src.mkv"
        subprocess.run(
            [FFMPEG, "-v", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=4",
             "-i", str(meta), "-map", "0:v", "-map_metadata", "1",
             "-c:v", "libx264", "-preset", "ultrafast", str(source)],
            check=True, capture_output=True, timeout=300)
        return source

    def test_a_declined_cut_command_still_runs_afterwards(self):
        source = self._chaptered_source()
        out = self._tmp / "out"
        out.mkdir()
        info = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_chapters",
             "-show_format", "-of", "json", str(source)],
            capture_output=True, text=True, timeout=120).stdout)
        holder = {}

        def fake_wizard(answers, config=None):
            answers.update({
                "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
                "input_path": source, "probe": info, "format": info["format"],
                "output_location": out,
                "video_streams": [s for s in info["streams"]
                                  if s["codec_type"] == "video"],
                "audio_streams": [], "subtitle_streams": [],
                "output_ext": "mkv", "keep_source_chapters": True,
                "video_encoder": "libx264", "crf": 28, "preset": "ultrafast",
                "cut_keep_ranges": [(1.0, 3.0)],
                "start_now": False,
            })
            answers["cmd"] = FFmWiz.build_ffmpeg_command(answers)
            holder["cmd"] = [str(part) for part in answers["cmd"]]

        FFmWiz.run_wizard = fake_wizard
        FFmWiz.run_one_job({}, self._tmp / "cfg.json")

        cmd = holder["cmd"]
        referenced = [cmd[index + 1] for index, part in enumerate(cmd) if part == "-i"]
        self.assertGreater(len(referenced), 1,
                           "expected a generated auxiliary input in this job")
        for path in referenced:
            self.assertTrue(Path(path).exists(),
                            f"the printed command references a deleted file: {path}")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        self.assertEqual(0, result.returncode,
                         f"the preserved command failed:\n{result.stderr[-1500:]}")
        produced = list(out.glob("*.mkv"))
        self.assertTrue(produced, "the manual run produced no output")

        # Preserving hands ownership to the USER, so nothing in the program
        # will ever delete these. That is the feature -- and it means this test
        # owns the cleanup, or the suite grows an ffmwiz_* directory per run.
        for path in referenced[1:]:
            target = Path(path)
            shutil.rmtree(target if target.is_dir() else target.parent,
                          ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
