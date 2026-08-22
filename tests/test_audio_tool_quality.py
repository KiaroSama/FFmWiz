"""Regression tests: the Audio Cut / Speed / Reverse tools must not downgrade audio.

Those tools always re-encode. They used to call `audio_tool_encode_options()`
with no bitrate, so the default (`DEFAULT_SPEED_AUDIO_BITRATE_KBPS`, 128) was
applied to every output -- cutting a 320 kbps track produced a 128 kbps file --
and `AUDIO_CHANNELS` forced every result to stereo, silently downmixing 5.1.

The target is now derived from the source stream.
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


def _answers(codec="mp3", bit_rate="320000", channels=2, ext="mp3", **extra):
    stream = {"codec_type": "audio", "codec_name": codec, "channels": channels,
              "sample_rate": "48000"}
    if bit_rate is not None:
        stream["bit_rate"] = bit_rate
    answers = {
        "ffmpeg": "ffmpeg",
        "input_path": Path(tempfile.gettempdir()) / f"src.{ext}",
        "output_location": Path(tempfile.gettempdir()),
        "audio_index": 0,
        "audio_streams": [stream],
        "format": {"duration": "60"},
        "audio_keep_ranges": [(1.0, 4.0)],
    }
    answers.update(extra)
    return answers


class AudioToolBitrate(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    # ---------------- the resolver ----------------

    def test_source_bitrate_is_the_target(self):
        for kbps in (96, 160, 192, 256, 320):
            answers = _answers(bit_rate=str(kbps * 1000))
            self.assertEqual(FFmWiz.resolve_audio_tool_bitrate_kbps(answers), kbps)

    def test_lossless_source_is_clamped_not_taken_literally(self):
        # A CD-PCM estimate is ~1411 kbps; handing that to a lossy encoder is
        # nonsense, so it clamps to the tool ceiling.
        answers = _answers(codec="flac", bit_rate=None, channels=6)
        resolved = FFmWiz.resolve_audio_tool_bitrate_kbps(answers)
        self.assertEqual(resolved, FFmWiz.AUDIO_TOOL_MAX_BITRATE_KBPS)

    def test_unknown_source_falls_back_to_the_default(self):
        answers = _answers(codec=None, bit_rate=None)
        answers["audio_streams"] = []
        self.assertEqual(
            FFmWiz.resolve_audio_tool_bitrate_kbps(answers),
            FFmWiz.DEFAULT_SPEED_AUDIO_BITRATE_KBPS,
        )

    def test_an_explicit_answer_wins_over_the_source(self):
        answers = _answers(bit_rate="320000", audio_bitrate_kbps=96)
        self.assertEqual(FFmWiz.resolve_audio_tool_bitrate_kbps(answers), 96)

    def test_very_low_source_is_floored(self):
        answers = _answers(bit_rate="8000")
        self.assertEqual(
            FFmWiz.resolve_audio_tool_bitrate_kbps(answers),
            FFmWiz.AUDIO_TOOL_MIN_BITRATE_KBPS,
        )

    # ---------------- channel layout ----------------

    def test_source_channel_layout_is_preserved(self):
        for channels in (1, 2, 6, 8):
            answers = _answers(channels=channels)
            self.assertEqual(FFmWiz.resolve_audio_tool_channels(answers), channels)

    def test_exotic_channel_count_falls_back_to_the_default(self):
        answers = _answers(channels=FFmWiz.MAX_PRESERVED_AUDIO_CHANNELS + 4)
        self.assertEqual(FFmWiz.resolve_audio_tool_channels(answers), FFmWiz.AUDIO_CHANNELS)

    # ---------------- the generated commands ----------------

    def _cut_command_text(self, answers):
        return " ".join(str(part) for part in FFmWiz.build_audio_cut_command(answers))

    def test_audio_cut_command_carries_the_source_bitrate(self):
        text = self._cut_command_text(_answers(bit_rate="320000"))
        self.assertIn("-b:a 320k", text)
        self.assertNotIn("-b:a 128k", text)

    def test_audio_cut_command_carries_the_source_channels(self):
        text = self._cut_command_text(_answers(codec="aac", bit_rate="384000", channels=6, ext="m4a"))
        self.assertIn("-ac 6", text)

    def test_lossless_output_container_still_ignores_bitrate(self):
        # flac/wav are lossless; a -b:a there would be meaningless.
        for ext in ("flac", "wav"):
            options = FFmWiz.audio_tool_encode_options(ext, bitrate_kbps=320)
            self.assertNotIn("-b:a", options, f"{ext} must not receive a bitrate")

    def test_speed_reverse_command_carries_the_source_bitrate(self):
        answers = _answers(bit_rate="256000")
        answers["speed_factor"] = 1.5
        text = " ".join(str(p) for p in FFmWiz.build_audio_speed_reverse_command(answers))
        self.assertIn("-b:a 256k", text)


@unittest.skipIf(not FFMPEG or not FFPROBE, "ffmpeg/ffprobe not on PATH")
class AudioToolRealRoundTrip(unittest.TestCase):
    """One real encode: the reported symptom was about the FILE, not the string."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_audio_quality_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _probe(self, path):
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=codec_name,bit_rate,channels", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60).stdout
        stream = json.loads(out)["streams"][0]
        return stream["codec_name"], round(int(stream["bit_rate"]) / 1000), stream["channels"]

    def test_cutting_a_320k_source_keeps_320k(self):
        src = self._tmp / "src.mp3"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=6", "-c:a", "libmp3lame",
             "-b:a", "320k", "-ac", "2", str(src)], check=True, timeout=120)
        self.assertEqual(self._probe(src)[1], 320)

        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(src)],
            capture_output=True, text=True, timeout=60).stdout)
        answers = {
            "ffmpeg": FFMPEG, "input_path": src, "output_location": self._tmp,
            "audio_index": 0, "audio_streams": probe["streams"],
            "format": probe["format"], "audio_keep_ranges": [(1.0, 4.0)],
        }
        cmd = FFmWiz.build_audio_cut_command(answers)
        subprocess.run([str(part) for part in cmd], check=True, timeout=180,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        codec, kbps, channels = self._probe(answers["output_path"])
        self.assertEqual(codec, "mp3")
        self.assertEqual(channels, 2)
        self.assertEqual(kbps, 320, "the cut must not downgrade a 320 kbps source to 128")


if __name__ == "__main__":
    unittest.main()
