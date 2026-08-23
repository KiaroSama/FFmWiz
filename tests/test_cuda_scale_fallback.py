"""Regression: CUDA exact stretch must not silently keep the source DAR (R08).

`scale_cuda` gained `reset_sar` in the same 2025 commit as `scale` (FFmpeg
7.2/8.0 line). Older builds reject the option, so the builder probes for it --
but merely OMITTING it is not a safe downgrade for an exact stretch. Without
`reset_sar` the scale filters propagate the source display aspect ratio, so an
anamorphic source lands on the wrong geometry instead of failing. Measured here
with the CPU `scale` filter, which shares that SAR propagation:

    scale=1000:500              -> 1000x500  SAR 8:9  DAR 16:9   WRONG
    scale=1000:500:reset_sar=1  -> 1000x500  SAR 1:1  DAR  2:1   requested
    scale=1000:500,setsar=1     -> 1000x500  SAR 1:1  DAR  2:1   requested

A trailing `setsar=1` IS correct for a full-frame stretch, unlike the
force_original_aspect_ratio case in test_sar_capability.py where the source's
non-square pixels change the dimensions the filter computes. That is what the
CPU graph already emits, so on an old build the stretch leaves the GPU fast
path for the existing hwdownload/CPU/hwupload_cuda fallback.
"""
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

from ffmwiz.support import ext02

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

STRETCH = "stretch:1000x500"


def _answers(resolution: str) -> dict:
    """A 720x576 SAR 64:45 (DAR 16:9) anamorphic source bound for NVENC."""
    return {
        "video_streams": [{"codec_type": "video", "codec_name": "h264",
                           "width": 720, "height": 576,
                           "sample_aspect_ratio": "64:45"}],
        "video_codec": "H265",
        "use_gpu": True,
        "crop_enabled": False,
        "resolution": FFmWiz.parse_resolution(resolution),
        "ffmpeg": FFMPEG or "ffmpeg",
    }


def _build(reset_sar_supported: bool, resolution: str) -> str:
    """The video filter emitted for a build that does / does not have the
    option. The probe is patched where the builder looks it up."""
    real = ext02.filter_option_available
    ext02.filter_option_available = lambda *a, **k: reset_sar_supported
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return FFmWiz.build_cuda_video_filter(_answers(resolution))
    finally:
        ext02.filter_option_available = real


def _probe_geometry(path: Path):
    payload = json.loads(subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,sample_aspect_ratio,display_aspect_ratio",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60).stdout)["streams"][0]
    return (payload["width"], payload["height"],
            payload.get("sample_aspect_ratio"), payload.get("display_aspect_ratio"))


def _write_anamorphic_source(path: Path) -> None:
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=720x576:rate=25:duration=1",
         "-c:v", "libx264", "-preset", "ultrafast", "-vf", "setsar=64/45",
         str(path)], check=True, timeout=300)


class ModernBuildKeepsTheFastPath(unittest.TestCase):
    def test_an_exact_stretch_stays_on_the_gpu_and_resets_sar(self):
        chain = _build(True, STRETCH)
        self.assertTrue(chain.startswith("scale_cuda=w=1000:h=500:"), chain)
        self.assertIn(":reset_sar=1", chain)
        self.assertNotIn("hwdownload", chain)


class OldBuildLeavesTheFastPathForAnExactStretch(unittest.TestCase):
    def test_it_is_no_longer_the_bare_scale_cuda_fast_path(self):
        # The defect: `scale_cuda=w=1000:h=500:...:passthrough=0` on its own
        # keeps the source DAR, so the output is SAR 8:9 / DAR 16:9, not 2:1.
        chain = _build(False, STRETCH)
        self.assertNotIn("scale_cuda", chain)

    def test_the_shape_is_the_shared_cpu_fallback(self):
        chain = _build(False, STRETCH)
        self.assertTrue(chain.startswith("hwdownload,"), chain)
        self.assertTrue(chain.endswith("hwupload_cuda"), chain)
        self.assertIn("scale=1000:500", chain)
        self.assertIn("setsar=1", chain)

    def test_it_wraps_exactly_the_cpu_filter_not_a_second_hand_rolled_chain(self):
        answers = _answers(STRETCH)
        with contextlib.redirect_stdout(io.StringIO()):
            cpu = FFmWiz.build_cpu_video_filter(dict(answers))
            surface = FFmWiz.cuda_pixel_format_for_output(answers)
        self.assertEqual(
            f"hwdownload,format={surface},{cpu},format={surface},hwupload_cuda",
            _build(False, STRETCH))

    def test_the_downgrade_never_emits_the_unsupported_option(self):
        self.assertNotIn("reset_sar", _build(False, STRETCH))

    def test_a_legacy_tuple_resolution_is_a_stretch_too(self):
        # resize_mode_is_stretch treats the old (w, h) form as stretch; the
        # downgrade has to follow it there or those callers stay broken.
        answers = _answers(STRETCH)
        answers["resolution"] = (1000, 500)
        real = ext02.filter_option_available
        ext02.filter_option_available = lambda *a, **k: False
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                chain = FFmWiz.build_cuda_video_filter(answers)
        finally:
            ext02.filter_option_available = real
        self.assertNotIn("scale_cuda", chain)


class EveryOtherCudaResizeKeepsTheFastPath(unittest.TestCase):
    """Only exact stretch promises square pixels; the rest must not pay for it."""

    def test_ar_preserving_and_no_resize_stay_on_the_gpu_on_both_builds(self):
        for supported in (True, False):
            for resolution in ("720p", "h720", "w1280", "n"):
                with self.subTest(reset_sar=supported, resolution=resolution):
                    chain = _build(supported, resolution)
                    self.assertTrue(chain.startswith("scale_cuda="), chain)
                    self.assertNotIn("hwdownload", chain)
                    self.assertEqual(supported, "reset_sar=1" in chain, chain)


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")
class TheGeometryIsMeasuredNotAssumed(unittest.TestCase):
    """`scale_cuda` cannot run here, but it shares `scale`'s SAR propagation,
    so the CPU filter shows which form lands on the requested geometry."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_cuda_sar_"))
        cls._src = cls._tmp / "anamorphic.mkv"
        _write_anamorphic_source(cls._src)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _geometry(self, name, video_filter):
        out = self._tmp / f"{name}.mkv"
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(self._src),
             "-vf", video_filter, "-c:v", "libx264", "-preset", "ultrafast",
             "-frames:v", "1", str(out)],
            capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr.strip()[-300:])
        return _probe_geometry(out)

    def test_the_source_really_is_anamorphic(self):
        self.assertEqual("64:45", _probe_geometry(self._src)[2],
                         "the fixture is not anamorphic; the test proves nothing")

    def test_the_cpu_filter_the_fallback_wraps_gives_square_pixels(self):
        with contextlib.redirect_stdout(io.StringIO()):
            cpu = FFmWiz.build_cpu_video_filter(_answers(STRETCH))
        self.assertEqual((1000, 500, "1:1", "2:1"), self._geometry("fallback", cpu))

    def test_the_bare_gpu_form_would_have_kept_the_source_dar(self):
        # Pinning the defect so nobody "simplifies" the downgrade back into it.
        self.assertEqual((1000, 500, "8:9", "16:9"),
                         self._geometry("bare", "scale=1000:500"))


def _cuda_nvenc_usable() -> bool:
    """True only when a CUDA device, scale_cuda and hevc_nvenc all really run.
    Listing the encoder is not enough: full FFmpeg builds list hevc_nvenc on
    machines with no NVIDIA driver at all."""
    if not (FFMPEG and FFPROBE):
        return False
    try:
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-init_hw_device", "cuda=gpu", "-filter_hw_device", "gpu",
             "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=0.2",
             "-vf", "format=nv12,hwupload_cuda,scale_cuda=w=64:h=64:format=nv12",
             "-c:v", "hevc_nvenc", "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120)
        return result.returncode == 0
    except Exception:
        return False


@unittest.skipUnless(
    _cuda_nvenc_usable(),
    "no usable NVIDIA CUDA/hevc_nvenc hardware here (probe encode failed); "
    "the GPU chain was NOT executed")
class RealNvencStretchGeometry(unittest.TestCase):
    """Optional hardware check: run the emitted chain through NVENC and measure
    the real output geometry. Reported as skipped wherever NVENC is missing."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_cuda_hw_"))
        cls._src = cls._tmp / "anamorphic.mp4"
        _write_anamorphic_source(cls._src)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _encode(self, name, reset_sar_supported):
        chain = _build(reset_sar_supported, STRETCH)
        out = self._tmp / f"{name}.mp4"
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
             "-i", str(self._src), "-vf", chain,
             "-c:v", "hevc_nvenc", "-frames:v", "10", str(out)],
            capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0,
                         f"{chain}\n{result.stderr.strip()[-400:]}")
        return _probe_geometry(out)

    def test_the_gpu_fast_path_stretches_to_square_pixels(self):
        self.assertEqual((1000, 500, "1:1", "2:1"), self._encode("modern", True))

    def test_the_old_build_fallback_reaches_the_same_geometry(self):
        self.assertEqual((1000, 500, "1:1", "2:1"), self._encode("legacy", False))


if __name__ == "__main__":
    unittest.main()
