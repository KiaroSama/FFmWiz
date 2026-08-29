"""Regression: the segmented reverse path must release what it created.

`run_one_job` has three executor branches. Two are wrapped in `try/finally`
so the artifact lease is released whichever way they exit; the segmented
reverse branch returned straight out of the function instead, so every
per-segment file it registered stayed on disk for the life of the process.
That branch is exactly the one that creates the most files.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

import FFmWiz

from ffmwiz import encoding
from ffmwiz import reverse_pipeline


class SegmentedReverseReleasesItsArtifacts(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_segrev_"))
        self._real_wizard = FFmWiz.run_wizard
        self._real_segmented = reverse_pipeline.run_segmented_reverse_main_encode
        self._real_plan = FFmWiz.print_ffmpeg_processing_plan
        self._real_menu = FFmWiz.ask_main_menu
        FFmWiz.ask_main_menu = lambda answers, config_path: 1
        FFmWiz.print_ffmpeg_processing_plan = lambda *a, **k: None

    def tearDown(self):
        FFmWiz.run_wizard = self._real_wizard
        reverse_pipeline.run_segmented_reverse_main_encode = self._real_segmented
        FFmWiz.print_ffmpeg_processing_plan = self._real_plan
        FFmWiz.ask_main_menu = self._real_menu
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _prepared(self):
        return {
            "cmd": ["ffmpeg", "-i", "in.mkv", "out.mkv"],
            "start_now": True,
            "video_streams": [{"codec_type": "video", "codec_name": "h264"}],
            "audio_streams": [],
            "output_ext": "mkv",
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True,
        }

    def _run(self, executor):
        prepared = self._prepared()

        def fake_wizard(answers, config=None):
            answers.update(prepared)

        FFmWiz.run_wizard = fake_wizard
        reverse_pipeline.run_segmented_reverse_main_encode = executor
        return FFmWiz.run_one_job({}, self._tmp / "cfg.json")

    def test_segment_files_do_not_survive_the_job(self):
        created = []

        def executor(answers):
            for index in range(3):
                part = self._tmp / f"segment_{index}.mkv"
                part.write_bytes(b"x")
                created.append(FFmWiz.artifact_lease(answers).register(part))
            return 0, 1.0

        self._run(executor)
        self.assertEqual(3, len(created), "the executor must have registered files")
        leaked = [str(path) for path in created if path.exists()]
        self.assertEqual([], leaked, "segmented reverse leaked its temporary segments")

    def test_it_still_returns_the_executor_result(self):
        self.assertEqual((0, 1.0), self._run(lambda answers: (0, 1.0)))

    def test_a_failing_executor_still_releases(self):
        created = []

        def executor(answers):
            part = self._tmp / "half_written.mkv"
            part.write_bytes(b"x")
            created.append(FFmWiz.artifact_lease(answers).register(part))
            raise RuntimeError("ffmpeg died mid-segment")

        with self.assertRaises(RuntimeError):
            self._run(executor)
        self.assertFalse(created[0].exists(),
                         "a crash mid-encode must not strand the segments")


if __name__ == "__main__":
    unittest.main()
