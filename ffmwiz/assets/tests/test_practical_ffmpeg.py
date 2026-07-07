"""Permanent practical (real-ffmpeg) validation suite for FFmWiz.

Unlike the command-generation tests (which never invoke ffmpeg and run in CI),
these tests execute REAL ffmpeg/ffprobe on tiny synthetic media to prove that
the commands FFmWiz generates actually work end-to-end. They consolidate the
ad-hoc practical checks that were previously written as throwaway harnesses, so
they are maintained in one place and re-run on every local test pass.

Design:
- Every test self-skips when ffmpeg/ffprobe (or NVENC, for GPU tests) is not
  available, so the suite stays green in CI runners that have no ffmpeg.
- Synthetic sources are generated with lavfi (testsrc2/sine); no real user
  media is required.
- The FFmpeg capability cache is isolated to an owned temp dir, and all
  generated media is removed after each test.

Run just this file locally:
    python -m unittest assets.tests.test_practical_ffmpeg -v
or, from the assets/tests directory:
    python -m unittest test_practical_ffmpeg -v
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

import FFmWiz
import cache_test_utils

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
_HAVE_FFMPEG = bool(FFMPEG and FFPROBE)


def _nvenc_listed() -> bool:
    """True when the installed ffmpeg lists the hevc_nvenc encoder. A real
    encode is still attempted in the test and skips gracefully on driver/HW
    failure, so this only gates obviously-unsupported builds."""
    if not _HAVE_FFMPEG:
        return False
    try:
        result = subprocess.run([FFMPEG, "-hide_banner", "-encoders"],
                                capture_output=True, text=True, timeout=30)
        return "hevc_nvenc" in (result.stdout or "")
    except Exception:
        return False


_HAVE_NVENC = _nvenc_listed()

requires_ffmpeg = unittest.skipUnless(_HAVE_FFMPEG, "ffmpeg/ffprobe not available")
requires_nvenc = unittest.skipUnless(_HAVE_NVENC, "hevc_nvenc encoder not available")


@requires_ffmpeg
class PracticalFFmpegTests(unittest.TestCase):
    """Real-ffmpeg validation for Track Manager, loudnorm, and 10-bit Main10
    pixel-format selection (CPU and GPU/NVENC)."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._run_id = uuid.uuid4().hex
        self._prev_cache = __import__("os").environ.get("FFMWIZ_CACHE_DIR")
        self._cache_dir = cache_test_utils.create_owned_temp_cache_dir(self._run_id)
        __import__("os").environ["FFMWIZ_CACHE_DIR"] = self._cache_dir
        FFmWiz.services._CAPABILITY_SESSION_MEMO.clear()
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_practical_"))

    def tearDown(self):
        import os
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._prev_cache is None:
            os.environ.pop("FFMWIZ_CACHE_DIR", None)
        else:
            os.environ["FFMWIZ_CACHE_DIR"] = self._prev_cache
        FFmWiz.services._CAPABILITY_SESSION_MEMO.clear()
        cache_test_utils.safe_remove_owned_temp_dir(
            self._cache_dir, self._run_id, tempfile.gettempdir())

    # ---- synthetic media helpers ----
    def _run(self, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run([str(a) for a in args], capture_output=True, timeout=timeout)

    def _make_av(self, name: str, *, audio_tracks: int = 1, depth: int = 8,
                 duration: float = 0.4, volume: float | None = None) -> Path:
        """Create a tiny synthetic video file with N audio tracks. Kept very
        small/short on purpose so the real-ffmpeg suite stays fast."""
        path = self._tmp / name
        args = [FFMPEG, "-hide_banner", "-y",
                "-f", "lavfi", "-i", f"testsrc2=size=256x144:rate=10:duration={duration}"]
        for freq in (440, 880, 660)[:audio_tracks]:
            args += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}"]
        args += ["-map", "0:v"]
        for i in range(audio_tracks):
            args += ["-map", f"{i + 1}:a"]
        pix = "yuv420p10le" if depth == 10 else "yuv420p"
        vcodec = "libx265" if depth == 10 else "libx264"
        args += ["-c:v", vcodec, "-preset", "ultrafast", "-pix_fmt", pix, "-c:a", "aac"]
        if volume is not None:
            args += ["-filter:a", f"volume={volume}"]
        args += ["-shortest", str(path)]
        result = self._run(args)
        if result.returncode != 0:
            self.skipTest("Could not build synthetic source: "
                          + result.stderr.decode(errors="replace")[-400:])
        return path

    def _probe(self, path: Path) -> dict:
        return FFmWiz.ffprobe_full_json(FFPROBE, path)

    def _audio_streams(self, path: Path) -> list[dict]:
        return [s for s in self._probe(path).get("streams", []) if s.get("codec_type") == "audio"]

    def _video_stream(self, path: Path) -> dict:
        vs = [s for s in self._probe(path).get("streams", []) if s.get("codec_type") == "video"]
        return vs[0] if vs else {}

    # ================= Track Manager =================
    def test_track_manager_removes_audio_by_normalized_spec(self):
        src = self._make_av("src.mkv", audio_tracks=2)
        streams = self._probe(src).get("streams", [])
        specs = FFmWiz.normalize_track_remove_specs(["1"], streams)
        self.assertEqual(specs, ["a:0"])
        out = self._tmp / "out.mkv"
        cmd = FFmWiz.build_track_manager_command(FFMPEG, src, specs, [], out)
        self.assertIn("-0:a:0", cmd)
        self.assertEqual(self._run(cmd).returncode, 0)
        self.assertEqual(len(self._audio_streams(out)), 1)

    def test_track_manager_external_audio_replace_required_map(self):
        src = self._make_av("src.mkv", audio_tracks=1)
        ext = self._tmp / "ext.aac"
        self.assertEqual(self._run(
            [FFMPEG, "-hide_banner", "-y", "-f", "lavfi",
             "-i", "sine=frequency=880:duration=0.4", "-c:a", "aac", str(ext)]).returncode, 0)
        ext_item = {"path": ext,
                    "audio_streams": self._audio_streams(ext),
                    "subtitle_streams": []}
        out = self._tmp / "replaced.mkv"
        cmd = FFmWiz.build_track_manager_command(FFMPEG, src, ["a:0"], [ext_item], out)
        # External map must be required (no optional '?') and target only :a:0.
        self.assertIn("1:a:0", cmd)
        self.assertNotIn("1:a", cmd)
        self.assertNotIn("1:a?", cmd)
        self.assertEqual(self._run(cmd).returncode, 0)
        self.assertEqual(len(self._audio_streams(out)), 1)

    def test_track_manager_loudnorm_reencodes_audio(self):
        src = self._make_av("quiet.mkv", audio_tracks=1, volume=0.2)
        answers = {"loudnorm_enabled": True, "loudnorm_mode": "single",
                   "loudnorm_target_i": -16.0, "audio_codec": "aac", "audio_bitrate_kbps": 128}
        out = self._tmp / "loud.mkv"
        cmd = FFmWiz.build_track_manager_command(FFMPEG, src, [], [], out, answers)
        self.assertIn("-filter:a", cmd)
        self.assertIn("loudnorm", cmd[cmd.index("-filter:a") + 1])
        self.assertEqual(self._run(cmd).returncode, 0)
        self.assertTrue(out.exists() and out.stat().st_size > 0)
        self.assertEqual(len(self._audio_streams(out)), 1)

    # ================= 10-bit Main10 pixel format =================
    def _encode_with_filter(self, src: Path, vfilter: str, encoder: str,
                            profile: str, out: Path) -> subprocess.CompletedProcess:
        # Fastest preset per encoder family (NVENC uses p1..p7, x26x uses words).
        preset = "p1" if str(encoder).endswith("_nvenc") else "ultrafast"
        return self._run([FFMPEG, "-hide_banner", "-y", "-i", str(src),
                          "-filter:v", vfilter, "-c:v", encoder, "-preset", preset,
                          "-profile:v", profile, str(out)])

    def test_cpu_main10_encode_produces_10bit_hevc(self):
        src = self._make_av("src10.mkv", depth=10, audio_tracks=0)
        answers = {"video_streams": [self._video_stream(src)], "video_codec": "H265", "use_gpu": False}
        vfilter = FFmWiz.build_cpu_video_filter(answers)
        self.assertTrue(vfilter.endswith("format=yuv420p10le"), vfilter)
        self.assertEqual(FFmWiz.hevc_profile_for_output(answers, "main"), "main10")
        out = self._tmp / "cpu10.mp4"
        result = self._encode_with_filter(src, vfilter, "libx265", "main10", out)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-400:])
        vs = self._video_stream(out)
        self.assertEqual(vs.get("codec_name"), "hevc")
        self.assertIn("10", str(vs.get("bits_per_raw_sample") or vs.get("pix_fmt")))

    @requires_nvenc
    def test_gpu_nvenc_main10_encode_produces_10bit_hevc(self):
        src = self._make_av("src10.mkv", depth=10, audio_tracks=0)
        answers = {"video_streams": [self._video_stream(src)], "video_codec": "H265", "use_gpu": True}
        vfilter = FFmWiz.build_cpu_video_filter(answers)
        # CPU filter graph feeding NVENC must terminate in p010le for 10-bit.
        self.assertTrue(vfilter.endswith("format=p010le"), vfilter)
        out = self._tmp / "gpu10.mp4"
        result = self._encode_with_filter(src, vfilter, "hevc_nvenc", "main10", out)
        if result.returncode != 0:
            self.skipTest("NVENC encode failed (driver/hardware): "
                          + result.stderr.decode(errors="replace")[-300:])
        vs = self._video_stream(out)
        self.assertEqual(vs.get("codec_name"), "hevc")
        self.assertIn("10", str(vs.get("bits_per_raw_sample") or vs.get("pix_fmt")))

    # ================= Join Main10 (CPU) =================
    def test_join_cpu_main10_real_encode(self):
        a = self._make_av("ja.mkv", depth=10, audio_tracks=1)
        b = self._make_av("jb.mkv", depth=10, audio_tracks=1)
        v = self._video_stream(a)
        au = self._audio_streams(a)[0]

        def item(path):
            return {"path": path, "streams": [v, au], "video_streams": [v],
                    "audio_streams": [au], "format": {"duration": "1"}, "duration": 1.0}

        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": a, "output_ext": "mp4",
            "video_codec": "H265", "use_gpu": False, "video_streams": [v],
            "audio_streams": [au], "subtitle_streams": [], "data_streams": [],
            "attachment_streams": [], "audio_tracks": [0], "subtitle_tracks": [],
            "audio_codec": "aac", "audio_bitrate_kbps": 128, "resolution": "n", "fps": 30,
            "video_bitrate_kbps": 2000, "format": {"duration": "1"}, "color_range_choice": "tv",
            "join_input_items": [item(b)],
        }
        items = [item(a), item(b)]
        out = self._tmp / "joined.mp4"
        cmd = FFmWiz.build_join_encode_command(answers, items, out)
        text = " ".join(str(c) for c in cmd)
        self.assertIn("format=yuv420p10le", text)
        self.assertIn("-profile:v main10", text)
        self.assertEqual(self._run(cmd).returncode, 0)
        self.assertEqual(self._video_stream(out).get("codec_name"), "hevc")

    # ================= progress / ETA rendering =================
    def test_run_with_progress_renders_and_completes(self):
        # Exercise run_ffmpeg_with_progress (and the smoothed-ETA renderer) on a
        # real short encode; it must complete cleanly without raising.
        src = self._make_av("psrc.mkv", audio_tracks=1, duration=0.6)
        out = self._tmp / "pout.mp4"
        cmd = [FFMPEG, "-hide_banner", "-y", "-i", str(src),
               "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(out)]
        rc, elapsed = FFmWiz.run_ffmpeg_with_progress(cmd, total_duration=0.6, label="PracticalProgress")
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(elapsed, 0.0)
        self.assertTrue(out.exists() and out.stat().st_size > 0)

    # ================= Audio Cut lossless split (audio-only) =================
    def test_lossless_audio_split_from_video_excludes_video(self):
        # Mode 11 (Audio Cut) on a VIDEO input must split ONLY the selected
        # audio track losslessly and produce audio-only parts (no video).
        src = self._make_av("vid.mp4", audio_tracks=1, depth=8, duration=1.0)
        answers = {
            "ffmpeg": FFMPEG, "input_path": src, "output_location": self._tmp,
            "audio_index": 0, "audio_streams": self._audio_streams(src),
        }
        cmd, pattern = FFmWiz.build_lossless_split_command(answers, [0.5])
        text = " ".join(str(c) for c in cmd)
        self.assertIn("-map 0:a:0", text)
        self.assertIn("-vn", text)
        self.assertIn("_part%03d.m4a", str(pattern))
        rc = self._run(cmd).returncode
        self.assertEqual(rc, 0)
        part1 = self._tmp / "vid_part001.m4a"
        part2 = self._tmp / "vid_part002.m4a"
        self.assertTrue(part1.exists() and part2.exists())
        # Parts must contain audio and NO video stream.
        for part in (part1, part2):
            streams = self._probe(part).get("streams", [])
            self.assertTrue(any(s.get("codec_type") == "audio" for s in streams), part)
            self.assertFalse(any(s.get("codec_type") == "video" for s in streams), part)

    # ================= Track Manager metadata keep/drop =================
    def test_track_manager_drop_metadata_strips_tags(self):
        # Build a source with global + per-stream metadata, then drop it.
        src = self._tmp / "tagged.mkv"
        self.assertEqual(self._run(
            [FFMPEG, "-hide_banner", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=128x72:rate=10:duration=0.4",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
             "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "aac",
             "-metadata", "title=MyMovie", "-metadata:s:a:0", "title=MyAudio",
             "-metadata:s:a:0", "language=eng", "-shortest", str(src)]).returncode, 0)
        out = self._tmp / "clean.mkv"
        answers = {"track_manager_keep_metadata": False}
        cmd = FFmWiz.build_track_manager_command(FFMPEG, src, [], [], out, answers)
        self.assertEqual(self._run(cmd).returncode, 0)
        probe = self._probe(out)
        # Global title removed.
        self.assertNotIn("title", {k.lower() for k in (probe.get("format", {}).get("tags") or {})})
        # Per-stream title/language removed.
        for stream in probe.get("streams", []):
            tags = {k.lower(): v for k, v in (stream.get("tags") or {}).items()}
            self.assertNotIn("title", tags)
            self.assertNotIn("language", tags)

    def test_lossless_audio_split_user_chosen_extension(self):
        # User picks a copy-compatible extension (.aac/ADTS) for an aac source.
        src = self._make_av("vid2.mp4", audio_tracks=1, depth=8, duration=1.0)
        answers = {
            "ffmpeg": FFMPEG, "input_path": src, "output_location": self._tmp,
            "audio_index": 0, "audio_streams": self._audio_streams(src),
            "lossless_split_ext": "aac",
        }
        cmd, pattern = FFmWiz.build_lossless_split_command(answers, [0.5])
        self.assertIn("_part%03d.aac", str(pattern))
        self.assertEqual(self._run(cmd).returncode, 0)
        part1 = self._tmp / "vid2_part001.aac"
        self.assertTrue(part1.exists() and part1.stat().st_size > 0)
        streams = self._probe(part1).get("streams", [])
        self.assertTrue(any(s.get("codec_type") == "audio" for s in streams))
        self.assertFalse(any(s.get("codec_type") == "video" for s in streams))


if __name__ == "__main__":
    unittest.main(verbosity=2)
