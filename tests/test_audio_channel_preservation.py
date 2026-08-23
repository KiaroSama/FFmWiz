"""Regression: a 5.1/7.1 source must not be silently downmixed (USER-5-2).

`AUDIO_CHANNELS` defaulted to 2 and was applied unconditionally on every encode
path, so a lossless FLAC cut of a 5.1 track came back stereo -- four channels
destroyed, with no prompt and no note. Verified against real ffmpeg 8.1.1: a
6-channel FLAC through `-c:a flac -ac 2` yields `2,stereo`.

The policy now mirrors `resolve_audio_sample_rate`: an explicit request wins,
otherwise keep what the source has. Joins are the one place that genuinely needs
a single common layout -- the concat filter refuses mismatched inputs -- so they
normalise to the WIDEST input rather than to stereo, and the synthesised-silence
segments have to use that same layout or concat fails.

Measured after the fix on a real 5.1 FLAC:
    audio cut -> -ac 6, output ('flac', 6, '5.1')
    join      -> graph channel_layouts=5.1, output ('flac', 6, '5.1')
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

MAX_KEPT = FFmWiz.MAX_PRESERVED_AUDIO_CHANNELS


def _answers(channels, **extra):
    answers = {
        "audio_streams": [{"codec_type": "audio", "codec_name": "flac",
                           "channels": channels, "sample_rate": "48000"}],
        "audio_tracks": [0],
    }
    answers.update(extra)
    return answers


class ChannelResolution(unittest.TestCase):
    def test_a_51_source_keeps_six_channels(self):
        self.assertEqual(6, FFmWiz.resolve_audio_channels(_answers(6)))

    def test_a_71_source_keeps_eight_channels(self):
        self.assertEqual(8, FFmWiz.resolve_audio_channels(_answers(8)))

    def test_stereo_stays_stereo(self):
        self.assertEqual(2, FFmWiz.resolve_audio_channels(_answers(2)))

    def test_mono_stays_mono(self):
        self.assertEqual(1, FFmWiz.resolve_audio_channels(_answers(1)))

    def test_an_explicit_request_wins_over_the_source(self):
        # Asking for a downmix must still work.
        self.assertEqual(2, FFmWiz.resolve_audio_channels(_answers(6, audio_channels=2)))

    def test_the_layout_follows_the_SELECTED_track(self):
        answers = {
            "audio_streams": [{"channels": 2}, {"channels": 6}],
            "audio_tracks": [1],
        }
        self.assertEqual(6, FFmWiz.resolve_audio_channels(answers))

    def test_an_unknown_source_layout_is_not_forced(self):
        # Forcing stereo on an unknown source is the original bug in miniature.
        self.assertIsNone(FFmWiz.resolve_audio_channels({"audio_streams": []}))

    def test_a_layout_wider_than_the_cap_is_not_preserved(self):
        # Beyond MAX_PRESERVED_AUDIO_CHANNELS the odds of the encoder or
        # container refusing the layout outweigh preserving it.
        self.assertIsNone(FFmWiz.resolve_audio_channels(_answers(MAX_KEPT + 8)))

    def test_the_module_default_no_longer_forces_stereo(self):
        self.assertIsNone(FFmWiz.AUDIO_CHANNELS)


class JoinLayoutNormalisation(unittest.TestCase):
    """A join needs ONE layout; it must be the widest, not always stereo."""

    @staticmethod
    def _items(*channel_counts):
        return [{"audio_streams": [{"channels": n}] if n else []} for n in channel_counts]

    def test_all_51_inputs_normalise_to_51(self):
        self.assertEqual("5.1", FFmWiz.join_target_channel_layout(self._items(6, 6)))

    def test_a_mixed_join_widens_instead_of_narrowing(self):
        # Widening a stereo input costs nothing; narrowing the 5.1 one destroys it.
        self.assertEqual("5.1", FFmWiz.join_target_channel_layout(self._items(2, 6)))

    def test_all_stereo_inputs_stay_stereo(self):
        self.assertEqual("stereo", FFmWiz.join_target_channel_layout(self._items(2, 2)))

    def test_71_wins_over_51(self):
        self.assertEqual("7.1", FFmWiz.join_target_channel_layout(self._items(6, 8)))

    def test_a_silent_input_does_not_drag_the_layout_down(self):
        self.assertEqual("5.1", FFmWiz.join_target_channel_layout(self._items(6, 0)))

    def test_an_unnameable_count_falls_back_to_stereo(self):
        # There is no single obvious FFmpeg layout name for e.g. 3 channels.
        self.assertEqual("stereo", FFmWiz.join_target_channel_layout(self._items(3, 3)))

    def test_no_inputs_is_stereo(self):
        self.assertEqual("stereo", FFmWiz.join_target_channel_layout([]))

    def test_the_prep_filter_carries_the_layout(self):
        self.assertIn("channel_layouts=5.1",
                      FFmWiz.join_audio_prep_filter(48000, "5.1"))


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")
class RealFiveOneSurvives(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_51_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_51(self, name, duration=3):
        path = self._tmp / f"{name}.flac"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"sine=frequency=220:duration={duration}",
             "-af", "pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0", str(path)],
            check=True, timeout=300)
        return path

    def _probe(self, path):
        return json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json",
             str(path)], capture_output=True, text=True, timeout=60).stdout)

    def _channels(self, path):
        streams = self._probe(path).get("streams", [])
        return [s.get("channels") for s in streams if s["codec_type"] == "audio"]

    def _item(self, path):
        probe = self._probe(path)
        streams = probe["streams"]
        return {"path": path, "streams": streams, "format": probe["format"],
                "duration": float(probe["format"]["duration"]),
                "video_streams": [], "subtitle_streams": [],
                "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
                "attachment_streams": [], "data_streams": []}

    def _base(self, path, item):
        return {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": path,
            "output_location": self._tmp, "streams": item["streams"],
            "format": item["format"], "video_streams": [],
            "audio_streams": item["audio_streams"], "subtitle_streams": [],
            "data_streams": [], "attachment_streams": [], "audio_tracks": [0],
        }

    def test_an_audio_cut_keeps_all_six_channels(self):
        src = self._make_51("a")
        self.assertEqual([6], self._channels(src), "fixture is not 5.1")
        item = self._item(src)
        answers = self._base(src, item)
        answers.update({"output_ext": "flac", "audio_index": 0,
                        "audio_keep_ranges": [(0.5, 2.0)]})
        cmd = [str(part) for part in FFmWiz.build_audio_cut_command(answers)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr.strip()[-300:])
        self.assertEqual([6], self._channels(Path(answers["output_path"])),
                         "the cut downmixed a 5.1 source")

    def test_joining_two_51_sources_keeps_51(self):
        items = [self._item(self._make_51("a")), self._item(self._make_51("b", 2))]
        answers = self._base(items[0]["path"], items[0])
        answers["join_input_items"] = items[1:]
        cmd = [str(part) for part in FFmWiz.build_join_audio_encode_command(
            answers, items, self._tmp / "joined.flac")]
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("channel_layouts=5.1", graph)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stderr.strip()[-300:])
        self.assertEqual([6], self._channels(Path(answers["output_path"])))


if __name__ == "__main__":
    unittest.main()
