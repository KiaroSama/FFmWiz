"""Regression: the near-quality Join must respect its output container (D26/U18).

`build_join_near_quality_command` takes its output extension from input 0 but
chose the video encoder from hardware availability and bit depth alone. Joining
two .webm clips therefore emitted `-c:v libx264` into a WebM muxer:

    [webm] Only VP8 or VP9 or AV1 video and Vorbis or Opus audio and WebVTT
           subtitles are supported for WebM.

The audio half already asked `container_audio_encode_args`; the video half now
asks `container_video_codec`, the same resolver the main wizard and HardSub use.

CRF does not transfer across encoder families -- 18 is near-lossless for x264 but
wastefully large for VP9 -- so the container-forced branch uses 24 with `-b:v 0`,
which is what puts libvpx into constant-quality mode at all.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

from join_test_helpers import make_item

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _answers(items, **extra):
    first = items[0]
    answers = {
        "ffmpeg": "ffmpeg", "ffprobe": "ffprobe",
        "input_path": first["path"], "output_location": Path("."),
        "video_streams": first["video_streams"],
        "audio_streams": first["audio_streams"],
        "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
        "streams": first["streams"], "format": first["format"],
        "join_input_items": items[1:], "use_gpu": False,
        # No NVENC in the list: the CPU branches are what this test pins.
        "video_encoders": [],
    }
    answers.update(extra)
    return answers


def _command(output_name, **extra):
    items = [make_item("a.mkv", 5.0), make_item("b.mkv", 4.0)]
    answers = _answers(items, **extra)
    return [str(part) for part in FFmWiz.build_join_near_quality_command(
        answers, items, Path(output_name))], answers


def _opt(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


class ContainerForcesTheEncoder(unittest.TestCase):
    def test_webm_gets_a_webm_legal_encoder(self):
        cmd, _ = _command("joined.webm")
        self.assertEqual("libvpx-vp9", _opt(cmd, "-c:v"))

    def test_webm_uses_constant_quality_mode(self):
        # Without -b:v 0 the CRF is only an upper bound and libvpx targets a
        # bitrate instead, which is not "near quality" at all.
        cmd, _ = _command("joined.webm")
        self.assertEqual("0", _opt(cmd, "-b:v"))
        self.assertEqual("24", _opt(cmd, "-crf"))

    def test_webm_audio_is_also_container_legal(self):
        cmd, _ = _command("joined.webm")
        self.assertIn(_opt(cmd, "-c:a"), {"libopus", "libvorbis"})

    def test_the_substitution_is_announced(self):
        notes = []
        real = FFmWiz.appio.note
        FFmWiz.appio.note = notes.append
        try:
            _command("joined.webm")
        finally:
            FFmWiz.appio.note = real
        joined = " ".join(notes).lower()
        self.assertIn("webm", joined)
        self.assertIn("vp9", joined)


class ContainersThatAcceptH264AreUnchanged(unittest.TestCase):
    """The fix must not disturb the common path."""

    def test_mp4_still_uses_x264_at_crf_18(self):
        cmd, _ = _command("joined.mp4")
        self.assertEqual("libx264", _opt(cmd, "-c:v"))
        self.assertEqual("18", _opt(cmd, "-crf"))

    def test_mkv_still_uses_x264(self):
        cmd, _ = _command("joined.mkv")
        self.assertEqual("libx264", _opt(cmd, "-c:v"))

    def test_mp4_does_not_get_the_libvpx_only_flags(self):
        cmd, _ = _command("joined.mp4")
        self.assertNotIn("-row-mt", cmd)
        self.assertIsNone(_opt(cmd, "-b:v"))


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")
class RealJoinMuxes(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_joinnq_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _clip(self, name, ext, vcodec, acodec, duration=2):
        path = self._tmp / f"{name}.{ext}"
        extra = ["-b:v", "200k"] if vcodec.startswith("libvpx") else ["-preset", "ultrafast"]
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"color=c=red:s=160x120:d={duration}:r=25",
             "-f", "lavfi", "-i", f"sine=duration={duration}",
             "-map", "0:v", "-map", "1:a",
             "-c:v", vcodec, "-vf", "format=yuv420p", "-c:a", acodec,
             *extra, str(path)],
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

    def _join(self, ext, vcodec, acodec):
        items = [self._clip("a", ext, vcodec, acodec),
                 self._clip("b", ext, vcodec, acodec)]
        answers = _answers(items)
        answers["output_location"] = self._tmp
        cmd = [str(part) for part in FFmWiz.build_join_near_quality_command(
            answers, items, self._tmp / f"joined.{ext}")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        self.assertEqual(result.returncode, 0, result.stderr.strip()[-400:])
        return Path(answers["output_path"])

    def test_a_webm_join_muxes(self):
        # This is the case that failed at header-write time before the fix.
        output = self._join("webm", "libvpx-vp9", "libopus")
        self.assertTrue(output.exists() and output.stat().st_size > 0)
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(output)],
            capture_output=True, text=True, timeout=60).stdout)
        codecs = {s["codec_name"] for s in probe["streams"]}
        self.assertTrue(codecs & {"vp9", "vp8", "av1"}, codecs)

    def test_an_mp4_join_still_muxes(self):
        output = self._join("mp4", "libx264", "aac")
        self.assertTrue(output.exists() and output.stat().st_size > 0)


if __name__ == "__main__":
    unittest.main()
