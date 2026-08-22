"""Regression tests: crop margins in a join must fit EVERY input, not just the first.

A join applies one crop string to every input. The validator measured only
input 0, so margins that are legal for a 1920x1080 first clip produced
`crop=iw-800-800` on a 640x480 later clip -- a width of -960. FFmpeg rejected the
whole job at filter-graph setup with "Invalid too big or non positive size for
width '-960'", after the wizard had already accepted the answer and printed the
command.
"""
import sys
import tempfile
import unittest
from pathlib import Path

import FFmWiz

sys.path.insert(0, str(Path(__file__).resolve().parent))
from join_test_helpers import make_item, video_stream  # noqa: E402


def _item(name, width, height, duration=5.0):
    item = make_item(Path(tempfile.gettempdir()) / name, duration=duration)
    item["video_streams"] = [video_stream(width=width, height=height)]
    item["streams"] = item["video_streams"] + item["audio_streams"]
    return item


def _join_answers(first, others, **crop):
    tmp = Path(tempfile.gettempdir())
    answers = {
        "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "input_path": tmp / "a.mp4",
        "output_location": tmp, "video_streams": first["video_streams"],
        "audio_streams": first["audio_streams"], "subtitle_streams": [],
        "data_streams": [], "attachment_streams": [], "streams": first["streams"],
        "format": first["format"], "output_ext": "mp4", "video_codec": "H264",
        "use_gpu": False, "audio_codec": "aac", "audio_bitrate_kbps": 128,
        "audio_tracks": [0], "subtitle_tracks": [], "resolution": "n", "fps": 30,
        "video_bitrate_kbps": 2000, "color_range_choice": "tv",
        "join_input_items": others, "crop_enabled": True,
        "crop_left": 0, "crop_right": 0, "crop_top": 0, "crop_bottom": 0,
    }
    answers.update(crop)
    return answers


class SmallestVideoSize(unittest.TestCase):
    def test_reports_the_minimum_across_all_joined_inputs(self):
        big, small = _item("a.mp4", 1920, 1080), _item("b.mp4", 640, 480)
        answers = _join_answers(big, [small])
        self.assertEqual(FFmWiz.smallest_video_size(answers), (640, 480))

    def test_mixed_orientations_take_the_minimum_of_each_axis(self):
        wide, tall = _item("a.mp4", 1920, 1080), _item("b.mp4", 720, 1280)
        answers = _join_answers(wide, [tall])
        self.assertEqual(FFmWiz.smallest_video_size(answers), (720, 1080))

    def test_single_input_matches_first_video_size(self):
        only = _item("a.mp4", 1280, 720)
        answers = _join_answers(only, [])
        self.assertEqual(FFmWiz.smallest_video_size(answers), FFmWiz.first_video_size(answers))


class JoinCropValidation(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_margin_illegal_for_a_later_input_is_rejected(self):
        big, small = _item("a.mp4", 1920, 1080), _item("b.mp4", 640, 480)
        answers = _join_answers(big, [small], crop_left=800, crop_right=800)
        with self.assertRaises(ValueError) as caught:
            FFmWiz.build_join_encode_command(answers, [big, small], Path(tempfile.gettempdir()) / "o.mp4")
        message = str(caught.exception)
        self.assertIn("640", message, "the error must name the limit that actually applies")
        self.assertIn("crop", message.lower())

    def test_vertical_margin_illegal_for_a_later_input_is_rejected(self):
        big, small = _item("a.mp4", 1920, 1080), _item("b.mp4", 640, 480)
        answers = _join_answers(big, [small], crop_top=300, crop_bottom=300)
        with self.assertRaises(ValueError) as caught:
            FFmWiz.build_join_encode_command(answers, [big, small], Path(tempfile.gettempdir()) / "o.mp4")
        self.assertIn("480", str(caught.exception))

    def test_margin_legal_for_every_input_still_builds(self):
        big, small = _item("a.mp4", 1920, 1080), _item("b.mp4", 640, 480)
        answers = _join_answers(big, [small], crop_left=100, crop_right=100)
        cmd = FFmWiz.build_join_encode_command(answers, [big, small], Path(tempfile.gettempdir()) / "o.mp4")
        self.assertIn("crop=", " ".join(str(part) for part in cmd))

    def test_validation_message_helper_uses_the_smallest_input(self):
        big, small = _item("a.mp4", 1920, 1080), _item("b.mp4", 640, 480)
        answers = _join_answers(big, [small])
        message = FFmWiz.crop_margins_validation_message(answers, 0, 400, 400, 0)
        self.assertIsNotNone(message, "800 px of horizontal crop cannot fit a 640 px input")
        self.assertIsNone(FFmWiz.crop_margins_validation_message(answers, 0, 100, 100, 0))


if __name__ == "__main__":
    unittest.main()
