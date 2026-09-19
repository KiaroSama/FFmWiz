"""Tests for HDR10 / Dolby Vision detection and the paths it gates.

`video_hdr_dolby_info` decides two user-visible behaviours: Mode 9 defaults to
H.265 when a source is HDR or Dolby Vision, and it only shows the HDR /
tone-map / Dolby prompt when one of those is set. README also promises a warning
when Dolby Vision is detected, because DV dynamic metadata cannot survive a
hard-sub re-encode.

Dolby Vision cannot be synthesised -- FFmpeg has no RPU encoder -- so those two
tests stay gated on a local sample. HDR10 CAN be: the earlier note that a
synthetic PQ clip "comes out with `color_transfer=unknown`" is true of the
MATROSKA muxer only. MP4 writes the `colr` atom, so an x265 clip tagged
`bt2020`/`smpte2084`/`bt2020nc` probes as real HDR10 and the detector reads it as
`hdr=True, dolby=False`. That is what `_hdr10_sample()` builds when the local
file is absent, which is what lets CI cover the HDR10 half at all.

The dict-driven half below always runs -- detection is a pure function of an
ffprobe stream dict. The real-media half is gated on local sample files, because
the assumption most likely to be wrong is whether REAL ffprobe output actually
contains the substrings the matcher looks for.
"""
import atexit
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

# Local-only samples; see .ai/TEST_MEDIA.md. Overridable so the location is not
# hard-wired to one machine.
MEDIA_DIR = Path(os.environ.get("FFMWIZ_TEST_MEDIA_DIR", r"I:/Video Templates"))
HDR10_CURATED = MEDIA_DIR / "HDR10-PQ-BT2020-colorbars.mp4"
DOVI = MEDIA_DIR / "DolbyVision-plus-HDR10-sample.mkv"
DOVI_UNTAGGED = MEDIA_DIR / "DolbyVision-no-container-tags.mkv"


def _hdr10_sample() -> Path:
    """The curated HDR10 clip, or an equivalent one built here.

    The curated file is local-only and carries no redistribution licence, so CI
    never has it. A generated clip is not a stand-in here: HDR10 *is* container
    signalling plus a 10-bit PQ/BT.2020 bitstream, and every assertion in this
    module reads exactly those fields. `-color_range tv` is stated explicitly
    because a fixture that leaves the range unresolved errors on the project's
    declared FFmpeg floor (B16).
    """
    if HDR10_CURATED.exists() or not FFMPEG:
        return HDR10_CURATED
    directory = Path(tempfile.mkdtemp(prefix="ffmwiz_hdr10_"))
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    built = directory / "HDR10-PQ-BT2020-synthetic.mp4"
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=3,format=yuv420p10le",
         "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-crf", "30",
         "-color_primaries", "bt2020", "-color_trc", "smpte2084",
         "-colorspace", "bt2020nc", "-color_range", "tv",
         "-x265-params",
         "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:"
         "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):"
         "max-cll=1000,400",
         str(built)],
        capture_output=True, timeout=180)
    return built if result.returncode == 0 and built.exists() else HDR10_CURATED


HDR10 = _hdr10_sample()


class HdrDolbyDetectionLogic(unittest.TestCase):
    """No media needed: detection is a pure function of the probe dict."""

    def test_pq_transfer_is_hdr(self):
        info = FFmWiz.video_hdr_dolby_info({"color_transfer": "smpte2084"})
        self.assertTrue(info.get("hdr"))
        self.assertFalse(info.get("dolby"))

    def test_hlg_transfer_is_hdr(self):
        self.assertTrue(FFmWiz.video_hdr_dolby_info({"color_transfer": "arib-std-b67"}).get("hdr"))

    def test_sdr_transfer_is_not_hdr(self):
        for transfer in ("bt709", "unknown", None, ""):
            with self.subTest(transfer=transfer):
                self.assertFalse(FFmWiz.video_hdr_dolby_info({"color_transfer": transfer}).get("hdr"))

    def test_dovi_side_data_is_dolby(self):
        stream = {"side_data_list": [{"side_data_type": "DOVI configuration record"}]}
        self.assertTrue(FFmWiz.video_hdr_dolby_info(stream).get("dolby"))

    def test_dolby_vision_in_tags_is_dolby(self):
        self.assertTrue(
            FFmWiz.video_hdr_dolby_info({"tags": {"comment": "Dolby Vision profile 8"}}).get("dolby"))

    def test_plain_stream_is_neither(self):
        info = FFmWiz.video_hdr_dolby_info({"codec_name": "h264", "color_transfer": "bt709"})
        self.assertFalse(info.get("hdr"))
        self.assertFalse(info.get("dolby"))


@unittest.skipIf(not FFPROBE, "ffprobe not on PATH")
@unittest.skipIf(not HDR10.exists(),
                 f"HDR/Dolby samples not present in {MEDIA_DIR}")
class HdrDolbyRealMedia(unittest.TestCase):
    """Real files: does actual ffprobe output carry what the matcher expects?"""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def _video_stream(self, path):
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(path)],
            capture_output=True, encoding="utf-8", errors="replace", timeout=120).stdout
        return [s for s in json.loads(out)["streams"] if s["codec_type"] == "video"][0]

    def test_real_hdr10_file_is_detected_as_hdr_only(self):
        info = FFmWiz.video_hdr_dolby_info(self._video_stream(HDR10))
        self.assertTrue(info.get("hdr"), "a real smpte2084 file must read as HDR")
        self.assertFalse(info.get("dolby"))

    @unittest.skipIf(not DOVI.exists(), "Dolby Vision sample not present; no RPU encoder exists")
    def test_real_dolby_vision_file_is_detected_as_both(self):
        # DV normally rides on top of an HDR10 base layer, so both must fire.
        info = FFmWiz.video_hdr_dolby_info(self._video_stream(DOVI))
        self.assertTrue(info.get("dolby"), "a real DV RPU must read as Dolby Vision")
        self.assertTrue(info.get("hdr"))

    @unittest.skipIf(not DOVI_UNTAGGED.exists(), "untagged DV sample not present; no RPU encoder exists")
    def test_dolby_without_container_tags_still_detected(self):
        # The edge case: a DV RPU whose container carries no colour tags, so the
        # Dolby half of the detector has to fire on its own.
        info = FFmWiz.video_hdr_dolby_info(self._video_stream(DOVI_UNTAGGED))
        self.assertTrue(info.get("dolby"))
        self.assertFalse(info.get("hdr"))


@unittest.skipIf(not FFMPEG or not FFPROBE, "ffmpeg/ffprobe not on PATH")
@unittest.skipIf(not HDR10.exists(), f"HDR10 sample not present in {MEDIA_DIR}")
class HardSubPreservesHdr(unittest.TestCase):
    """A hard-sub burn-in re-encodes the video, so the HDR signalling has to be
    carried across deliberately -- otherwise the output silently becomes SDR."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_hdrsub_"))
        self._srt = self._tmp / "s.srt"
        self._srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nhi\n\n", encoding="utf-8")
        # Burn-in re-encodes every frame, and the sample is 41 s of 1080p, which
        # costs ~25 s of libx265. Stream-copy a 3 s head first: the copy keeps
        # the HDR signalling and side data intact, which is all this asserts.
        self._source = self._tmp / "hdr_head.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-t", "3",
             "-i", str(HDR10), "-map", "0:v:0", "-c", "copy", str(self._source)],
            check=True, timeout=300)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _answers(self, source):
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)],
            capture_output=True, encoding="utf-8", errors="replace", timeout=120).stdout)
        video = [s for s in probe["streams"] if s["codec_type"] == "video"]
        return {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": source,
            "output_location": self._tmp, "probe": probe, "streams": probe["streams"],
            "video_streams": video,
            "audio_streams": [s for s in probe["streams"] if s["codec_type"] == "audio"],
            "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
            "format": probe["format"], "output_ext": "mkv",
            "video_codec": "H265", "use_gpu": False,
            "hardsub_source": "external", "hardsub_subtitle_path": self._srt,
            "hardsub_audio_mode": "none", "hardsub_audio_container_policy": "copy",
            "hardsub_quality": "balanced",
            "hardsub_hdr_info": FFmWiz.video_hdr_dolby_info(video[0]),
            "hardsub_hdr_handling": "preserve",
            "color_range_choice": "tv", "resolution": "n", "fps": None,
        }

    def test_hdr10_signalling_survives_the_burn_in(self):
        answers = self._answers(self._source)
        cmd = [str(part) for part in FFmWiz.build_hardsub_command(answers)]
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                                errors="replace", timeout=900)
        self.assertEqual(result.returncode, 0,
                         (result.stderr or "").strip().splitlines()[-1] if result.stderr else "")
        out = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=color_transfer,color_primaries,pix_fmt", "-of", "json",
             str(answers["output_path"])],
            capture_output=True, encoding="utf-8", errors="replace", timeout=120).stdout)["streams"][0]
        self.assertEqual(out.get("color_transfer"), "smpte2084",
                         "the burn-in must not silently drop HDR to SDR")
        self.assertEqual(out.get("color_primaries"), "bt2020")
        self.assertIn("10le", out.get("pix_fmt", ""), "HDR output must stay 10-bit")

    def test_an_hdr_source_defaults_to_h265(self):
        # H.264 has no practical HDR10 path; the mode is supposed to prefer H265.
        answers = self._answers(self._source)
        self.assertTrue(answers["hardsub_hdr_info"].get("hdr"))
        cmd = [str(part) for part in FFmWiz.build_hardsub_command(answers)]
        self.assertIn(cmd[cmd.index("-c:v") + 1], {"libx265", "hevc_nvenc"})


if __name__ == "__main__":
    unittest.main()
