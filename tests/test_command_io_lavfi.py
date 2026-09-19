"""A01: a lavfi input may write sidecars as well as read media."""
from pathlib import Path
import os
import unittest

from ffmwiz import runtime
import test_command_io_transitive as fixtures


@unittest.skipUnless(fixtures.FFMPEG, "ffmpeg required for lavfi sidecar tests")
class LavfiSidecars(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExecutionGuard()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_lavfi_filter_sidecar_is_guarded_before_launch(self):
        graph = "movie=video.mkv,split[a][b];[a][b]psnr=stats_file=quality.txt"
        self.fixture.guarded(["-f", "lavfi", "-i", graph, "-f", "null", os.devnull],
                             "video.mkv", "quality.txt")

    def test_lavfi_statistics_with_an_unrelated_path_remain_usable(self):
        graph = "movie=video.mkv,split[a][b];[a][b]psnr=stats_file=quality.txt"
        before = Path("video.mkv").read_bytes()
        code, _ = runtime.run_ffmpeg_with_progress(
            [fixtures.FFMPEG, "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
             "-i", graph, "-f", "null", os.devnull], label="lavfi positive control")
        self.assertEqual(0, code)
        self.assertTrue(Path("quality.txt").stat().st_size)
        self.assertEqual(before, Path("video.mkv").read_bytes())


if __name__ == "__main__":
    unittest.main()
