"""Regression tests: a Join + Split job must not silently encode only input 0.

Split mode encodes each part as its own FFmpeg run so every part shows a clean
0->100% progress line. That runner rebuilds each part with the SINGLE-input
builder, which reads only `answers["input_path"]` and ignores `join_input_items`
entirely -- and it derived the timeline length from `answers["format"]`, which is
the first input's format.

So joining three 100 s clips and splitting at 50 s produced two parts cut out of
the FIRST file alone, clipped to 100 s instead of 300 s, with the other two files
in neither command and no error anywhere. Worse, the failure flipped on where the
split point landed: at 150 s the range clipped to input 0's 100 s, the spec list
came back empty, and the correct join command ran after all.

`build_join_encode_command` already emits a correct multi-output split graph, so
the fix is to leave joins to it.
"""
import sys
import tempfile
import unittest
from pathlib import Path

import FFmWiz

sys.path.insert(0, str(Path(__file__).resolve().parent))
from join_test_helpers import make_item  # noqa: E402

TMP = Path(tempfile.gettempdir())


def _spec_inputs(specs):
    """Just the -i values per part, so a failure prints something readable
    instead of dumping every answers dict."""
    out = []
    for spec in specs:
        command = [str(part) for part in spec["cmd"]]
        out.append([Path(command[i + 1]).name for i, part in enumerate(command) if part == "-i"])
    return out


def _join_answers(split_points, *, joined=True, duration=100.0):
    first = make_item(TMP / "A.mkv", duration=duration)
    others = [make_item(TMP / "B.mkv", duration=duration),
              make_item(TMP / "C.mkv", duration=duration)]
    answers = {
        "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "input_path": first["path"],
        "output_location": TMP, "video_streams": first["video_streams"],
        "audio_streams": first["audio_streams"], "subtitle_streams": [],
        "data_streams": [], "attachment_streams": [], "streams": first["streams"],
        "format": first["format"], "output_ext": "mp4", "video_codec": "H264",
        "use_gpu": False, "audio_codec": "aac", "audio_bitrate_kbps": 128,
        "audio_tracks": [0], "subtitle_tracks": [], "resolution": "n", "fps": 30,
        "video_bitrate_kbps": 2000, "color_range_choice": "tv",
        "separator_points": list(split_points),
    }
    if joined:
        answers["join_input_items"] = others
    return answers


class JoinSplitRouting(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_a_join_produces_no_single_input_part_specs(self):
        specs = FFmWiz.build_separator_job_specs(_join_answers([50.0]))
        self.assertEqual(
            len(specs), 0,
            "per-part specs for a join would encode only the first input; got "
            + repr(_spec_inputs(specs)),
        )

    def test_the_failure_does_not_depend_on_where_the_split_lands(self):
        # 50 s produced bad specs; 150 s happened to produce none. Both must
        # now behave the same.
        for point in (10.0, 50.0, 99.0, 150.0, 250.0):
            with self.subTest(split_at=point):
                specs = FFmWiz.build_separator_job_specs(_join_answers([point]))
                self.assertEqual(len(specs), 0, repr(_spec_inputs(specs)))

    def test_multiple_split_points_in_a_join_are_also_refused(self):
        specs = FFmWiz.build_separator_job_specs(_join_answers([50.0, 120.0, 220.0]))
        self.assertEqual(len(specs), 0, repr(_spec_inputs(specs)))

    def test_no_part_command_can_reference_only_the_first_input(self):
        specs = FFmWiz.build_separator_job_specs(_join_answers([50.0]))
        for spec in specs:
            command = [str(part) for part in spec["cmd"]]
            inputs = [command[i + 1] for i, part in enumerate(command) if part == "-i"]
            self.assertGreater(
                len(inputs), 1,
                f"a joined part was built from a single input: {inputs}")

    # ---- the single-input path must be completely unaffected ----

    def test_single_input_split_still_produces_one_spec_per_part(self):
        specs = FFmWiz.build_separator_job_specs(_join_answers([50.0], joined=False))
        self.assertEqual(len(specs), 2)
        self.assertEqual([spec["segment"] for spec in specs], [(0.0, 50.0), (50.0, 100.0)])

    def test_single_input_split_commands_target_the_input(self):
        specs = FFmWiz.build_separator_job_specs(_join_answers([50.0], joined=False))
        for spec in specs:
            command = [str(part) for part in spec["cmd"]]
            self.assertIn("-i", command)
            self.assertTrue(any("A.mkv" in part for part in command))

    def test_single_input_with_no_split_point_still_yields_nothing(self):
        specs = FFmWiz.build_separator_job_specs(_join_answers([], joined=False))
        self.assertEqual(len(specs), 0, repr(_spec_inputs(specs)))


if __name__ == "__main__":
    unittest.main()
