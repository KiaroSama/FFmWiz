"""Split from test_loudnorm_join_progress.py (see join_test_helpers.py)."""
import contextlib
import io
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock
import FFmWiz
import cache_test_utils
from join_test_helpers import video_stream, audio_stream, make_item


class LoudnormJoinProgressTests2(unittest.TestCase):
    def test_largest_time_unit(self):
        self.assertEqual(FFmWiz.largest_time_unit(45.0), "s")
        self.assertEqual(FFmWiz.largest_time_unit(120.0), "m")
        self.assertEqual(FFmWiz.largest_time_unit(7200.0), "h")

    def test_parse_split_times_bare_number_uses_largest_unit(self):
        # 31-minute file: a bare "16" means 16 minutes.
        self.assertEqual(FFmWiz.parse_split_times_line("16", 1884.0), [960.0])
        # Short file: a bare "16" means 16 seconds.
        self.assertEqual(FFmWiz.parse_split_times_line("16", 30.0), [16.0])

    def test_parse_split_times_line_sorts_and_filters(self):
        pts = FFmWiz.parse_split_times_line("20:00:000,10:00,25:00:000", 1800.0)
        self.assertEqual(pts, [600.0, 1200.0, 1500.0])
        # Out-of-range points are dropped.
        self.assertEqual(FFmWiz.parse_split_times_line("10:00, 40:00", 1800.0), [600.0])
        # Missing milliseconds treated as 0.
        self.assertEqual(FFmWiz.parse_split_times_line("5:00", 600.0), [300.0])

    def test_build_lossless_split_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = {"ffmpeg": "ffmpeg", "input_path": Path(tmp) / "clip.mp4",
                       "output_location": Path(tmp), "audio_index": 0,
                       "audio_streams": [{"codec_type": "audio", "codec_name": "aac"}]}
            cmd, pattern = FFmWiz.build_lossless_split_command(answers, [600.0, 1200.0, 1500.0])
            self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
            self.assertEqual(cmd[cmd.index("-f") + 1], "segment")
            self.assertEqual(cmd[cmd.index("-segment_times") + 1], "600.000000,1200.000000,1500.000000")
            # Audio Cut tool: only the selected audio track is mapped (not -map 0).
            self.assertEqual(cmd[cmd.index("-map") + 1], "0:a:0")
            self.assertIn("-vn", cmd)
            self.assertNotIn("0", [cmd[i + 1] for i, t in enumerate(cmd) if t == "-map"])
            # aac source -> .m4a audio container.
            self.assertIn("_part%03d.m4a", str(pattern))

    def test_lossless_audio_copy_ext_mapping(self):
        self.assertEqual(FFmWiz.lossless_audio_copy_ext("aac", ".mp4"), "m4a")
        self.assertEqual(FFmWiz.lossless_audio_copy_ext("mp3", ".mp4"), "mp3")
        self.assertEqual(FFmWiz.lossless_audio_copy_ext("flac", ".mkv"), "flac")
        self.assertEqual(FFmWiz.lossless_audio_copy_ext("opus", ".webm"), "opus")
        # Unknown codec falls back to a universal audio container.
        self.assertEqual(FFmWiz.lossless_audio_copy_ext("weird", ".xyz"), "mka")

    def test_manual_split_flow_sets_split_points(self):
        answers = {"audio_index": 0, "format": {"duration": "1800"}}
        # menu -> manual(2); "add cuts/split?" yes; layout 5; split line.
        with mock.patch.object(FFmWiz.appio, "ask_raw", side_effect=["2", "5", "10:00,20:00,25:00"]), \
             mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
             mock.patch.object(FFmWiz.wizard, "_confirm_audio_transform_start"):
            FFmWiz.step_audio_transform_editor(answers)
        self.assertFalse(answers["_audio_transform_noop"])
        self.assertEqual(answers["_audio_transform_split_points"], [600.0, 1200.0, 1500.0])
        # Split is mutually exclusive with speed/reverse transforms.
        self.assertFalse(answers["audio_speed_enabled"])
        self.assertEqual(answers["audio_cut_keep_ranges"], [])

    # ================= Audio join in the interactive wizard =================
    def test_wizard_join_applicable_for_audio_input(self):
        audio_answers = {"input_path": Path("a.wav"), "audio_streams": [audio_stream()], "video_streams": []}
        video_answers = {"input_path": Path("v.mkv"), "audio_streams": [audio_stream()],
                         "video_streams": [video_stream()], "output_ext": "mkv"}
        no_input = {"audio_streams": [audio_stream()], "video_streams": []}
        self.assertTrue(FFmWiz.wizard_audio_join_applicable(audio_answers))
        self.assertFalse(FFmWiz.wizard_audio_join_applicable(video_answers))
        self.assertFalse(FFmWiz.wizard_audio_join_applicable(no_input))
        self.assertTrue(FFmWiz.wizard_join_inputs_applicable(audio_answers))
        self.assertTrue(FFmWiz.wizard_join_inputs_applicable(video_answers))

    # ================= Track Manager (menu 15) =================
    def test_parse_track_remove_specs(self):
        self.assertEqual(FFmWiz.parse_track_remove_specs("2, a:1 , s:0"), ["2", "a:1", "s:0"])
        self.assertEqual(FFmWiz.parse_track_remove_specs("a:0,a:0"), ["a:0"])  # dedup
        self.assertEqual(FFmWiz.parse_track_remove_specs(""), [])
        with self.assertRaises(ValueError):
            FFmWiz.parse_track_remove_specs("x:9")
        with self.assertRaises(ValueError):
            FFmWiz.parse_track_remove_specs("5", stream_count=3)

    def test_build_track_manager_command_remove_and_add(self):
        ext = {"path": Path("ext.aac"), "audio_streams": [{"codec_type": "audio"}], "subtitle_streams": []}
        cmd = FFmWiz.build_track_manager_command("ffmpeg", Path("in.mkv"), ["a:1"], [ext], Path("out.mkv"))
        # map all, drop a:1, add the external audio, copy.
        self.assertEqual(cmd[cmd.index("-map") + 1], "0")
        self.assertIn("-0:a:1", cmd)
        # External audio map must be REQUIRED (no trailing '?') and target only
        # the first audio stream of the external file (:a:0).
        self.assertIn("1:a:0", cmd)
        self.assertNotIn("1:a", cmd)
        self.assertNotIn("1:a?", cmd)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
        self.assertTrue(str(cmd[-1]).endswith("out.mkv"))

    def test_build_track_manager_command_remove_only(self):
        cmd = FFmWiz.build_track_manager_command("ffmpeg", Path("in.mkv"), ["2"], [], Path("out.mkv"))
        self.assertIn("-0:2", cmd)
        self.assertNotIn("1:a:0", cmd)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")

    def test_build_track_manager_command_loudnorm_reencodes_audio(self):
        # With loudnorm enabled, audio is re-encoded (-c:a) with a loudnorm
        # -filter:a, while the rest stays stream-copied.
        answers = {"loudnorm_enabled": True, "loudnorm_mode": "single",
                   "loudnorm_target_i": -16.0, "audio_codec": "aac", "audio_bitrate_kbps": 160}
        cmd = FFmWiz.build_track_manager_command(
            "ffmpeg", Path("in.mkv"), [], [], Path("out.mkv"), answers)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
        self.assertIn("-c:a", cmd)
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "aac")
        self.assertIn("-filter:a", cmd)
        self.assertIn("loudnorm", cmd[cmd.index("-filter:a") + 1])
        self.assertEqual(cmd[cmd.index("-b:a") + 1], "160k")

    def test_build_track_manager_command_drops_metadata_when_requested(self):
        answers = {"track_manager_keep_metadata": False}
        cmd = FFmWiz.build_track_manager_command("ffmpeg", Path("in.mkv"), [], [], Path("out.mkv"), answers)
        self.assertEqual(cmd[cmd.index("-map_metadata") + 1], "-1")
        self.assertIn("-map_chapters", cmd)
        self.assertEqual(cmd[cmd.index("-map_chapters") + 1], "-1")
        self.assertIn("-map_metadata:s", cmd)

    def test_build_track_manager_command_keeps_metadata_by_default(self):
        cmd = FFmWiz.build_track_manager_command("ffmpeg", Path("in.mkv"), [], [], Path("out.mkv"))
        self.assertEqual(cmd[cmd.index("-map_metadata") + 1], "0")
        self.assertNotIn("-map_metadata:s", cmd)

    def test_join_audio_encode_applies_loudnorm(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [make_item(Path(tmp) / "a.m4a"), make_item(Path(tmp) / "b.m4a")]
            for it in items:
                it["video_streams"] = []
            answers = {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "audio_bitrate_kbps": 160,
                       "loudnorm_enabled": True, "loudnorm_target_i": -16.0, "loudnorm_mode": "single"}
            cmd = FFmWiz.build_join_audio_encode_command(answers, items, Path(tmp) / "out.m4a")
            fc = cmd[cmd.index("-filter_complex") + 1]
            self.assertIn("concat=n=2:v=0:a=1", fc)
            self.assertIn("loudnorm", fc)
            self.assertLess(fc.index("concat=n=2"), fc.index("loudnorm"))


if __name__ == "__main__":
    unittest.main()
