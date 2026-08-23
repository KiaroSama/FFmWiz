"""Regression: the displayed settings must match the executed command (R10).

`build_join_encode_command()` resolves codecs on `join_answers = dict(answers)`
and copied almost nothing back. Requesting copy/copy produced

    -c:v libx265 ... -c:a aac

while `answers` still said `copy` for both, so the selected-settings summary
contradicted the command that would actually run. The video substitution was
not even announced.

Same root cause as the artifact leak: state written onto a shallow copy is
invisible outside it. The fix is the same mechanism -- a container placed in
answers before the copy, which `dict()` shares by reference.

The requested value is deliberately KEPT: Back/reopen has to show the user
their own choice, while the summary shows what will happen.
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


class SharedContainerSemantics(unittest.TestCase):
    def test_a_shallow_copy_writes_through(self):
        answers = {"video_codec": "copy"}
        FFmWiz.effective_settings(answers)
        copy = dict(answers)
        FFmWiz.effective_settings(copy)["video_codec"] = "libx265"
        self.assertEqual("libx265", FFmWiz.effective_value(answers, "video_codec"))

    def test_the_requested_value_is_not_overwritten(self):
        answers = {"video_codec": "copy"}
        FFmWiz.effective_settings(answers)["video_codec"] = "libx265"
        self.assertEqual("copy", answers["video_codec"],
                         "Back/reopen must still show the user's own choice")

    def test_an_unresolved_name_falls_back_to_the_request(self):
        self.assertEqual("H264", FFmWiz.effective_value({"video_codec": "H264"},
                                                        "video_codec"))

    def test_a_missing_name_uses_the_default(self):
        self.assertEqual("x", FFmWiz.effective_value({}, "nothing", "x"))


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")
class JoinReportsWhatItWillActuallyDo(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_eff_"))
        self._notes = []
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = self._notes.append

    def tearDown(self):
        FFmWiz.appio.note = self._real_note
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _clip(self, name, duration=2):
        path = self._tmp / f"{name}.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"color=c=red:s=160x120:d={duration}:r=25",
             "-f", "lavfi", "-i", f"sine=duration={duration}",
             "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
             "-vf", "format=yuv420p", "-c:a", "aac", str(path)],
            check=True, timeout=300)
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json",
             str(path)], capture_output=True, text=True, timeout=60).stdout)
        streams = probe["streams"]
        return {"path": path, "streams": streams, "format": probe["format"],
                "duration": float(duration),
                "video_streams": [s for s in streams if s["codec_type"] == "video"],
                "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
                "subtitle_streams": [], "attachment_streams": [], "data_streams": []}

    def _build_requesting_copy(self):
        items = [self._clip("a"), self._clip("b")]
        first = items[0]
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": first["path"],
            "output_location": self._tmp, "streams": first["streams"],
            "format": first["format"], "video_streams": first["video_streams"],
            "audio_streams": first["audio_streams"],
            "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
            "output_ext": "mkv",
            # A join always re-encodes through the concat filter, so neither of
            # these can be honoured -- which is exactly the case that used to
            # be misreported.
            "video_codec": "copy", "audio_codec": "copy",
            "use_gpu": False, "audio_bitrate_kbps": 128,
            "audio_tracks": [0], "subtitle_tracks": [],
            "resolution": "n", "fps": 25, "video_bitrate_kbps": 400,
            "color_range_choice": "tv", "join_input_items": items[1:],
        }
        command = [str(part) for part in FFmWiz.build_join_encode_command(
            answers, items, self._tmp / "joined.mkv")]
        return answers, command

    def test_the_effective_codecs_equal_the_ones_in_the_command(self):
        answers, command = self._build_requesting_copy()
        self.assertEqual(command[command.index("-c:v") + 1],
                         FFmWiz.effective_value(answers, "video_codec"))
        self.assertEqual(command[command.index("-c:a") + 1],
                         FFmWiz.effective_value(answers, "audio_codec"))

    def test_neither_effective_codec_is_still_copy(self):
        answers, _command = self._build_requesting_copy()
        for name in ("video_codec", "audio_codec"):
            with self.subTest(name):
                self.assertNotEqual("copy", FFmWiz.effective_value(answers, name))

    def test_the_request_survives_for_back_navigation(self):
        answers, _command = self._build_requesting_copy()
        self.assertEqual("copy", answers["video_codec"])

    def test_the_video_substitution_is_announced(self):
        # It used to happen in silence; only the audio one was mentioned.
        self._build_requesting_copy()
        joined = " ".join(self._notes).lower()
        self.assertIn("video copy cannot be used", joined)

    def test_the_audio_substitution_is_still_announced(self):
        self._build_requesting_copy()
        joined = " ".join(self._notes).lower()
        self.assertIn("audio copy cannot be used", joined)


if __name__ == "__main__":
    unittest.main()
