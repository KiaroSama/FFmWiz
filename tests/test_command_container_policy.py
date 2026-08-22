"""Regression tests for container/codec decisions the muxer would otherwise reject.

Two classes of bug are covered.

1. Subtitles. `build_ffmpeg_command` only made a codec decision for the MP4
   family and emitted a blind `-c:s copy` for everything else, so mov_text into
   MKV, subrip into WebM and any subtitle into AVI all reached FFmpeg and died at
   header-write time. Dropping `-c:s` alone is not enough either: the `-map` has
   to go too, or the muxer still refuses the stream.

2. Bit depth vs encoder. `h264_nvenc` has NO 10-bit mode -- unlike hevc_nvenc and
   av1_nvenc -- but the gate was written per-family, so a 10-bit source with
   H.264 + GPU was handed p010le and failed at encoder init with
   "No capable devices found".
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


class SubtitleContainerPolicy(unittest.TestCase):
    """The resolver itself. Every expectation below was muxed for real on 8.1.1."""

    CASES = [
        # (container, source codec, expected -c:s value, or None to drop)
        ("mkv", "subrip", "copy"),
        ("mkv", "ass", "copy"),
        ("mkv", "hdmv_pgs_subtitle", "copy"),
        ("mkv", "mov_text", "srt"),
        ("mp4", "subrip", "mov_text"),
        ("mp4", "ass", "mov_text"),
        ("mp4", "hdmv_pgs_subtitle", None),
        ("mov", "subrip", "mov_text"),
        ("webm", "subrip", "webvtt"),
        ("webm", "hdmv_pgs_subtitle", None),
        ("avi", "subrip", None),
        ("avi", "hdmv_pgs_subtitle", None),
        ("ts", "subrip", "copy"),
        ("ts", "hdmv_pgs_subtitle", "copy"),
    ]

    def test_resolver_matches_what_the_muxer_accepts(self):
        for ext, codec, expected in self.CASES:
            with self.subTest(container=ext, codec=codec):
                self.assertEqual(FFmWiz.subtitle_codec_for_container(ext, codec), expected)

    def test_unknown_container_stays_permissive(self):
        # An untested container must not start silently dropping subtitles.
        self.assertEqual(FFmWiz.subtitle_codec_for_container("nut", "subrip"), "copy")

    def test_mixed_targets_are_reported_not_silently_resolved(self):
        # mov_text needs srt in MKV while subrip can be copied; one global -c:s
        # cannot express both, so that must surface as a problem.
        args, problems = FFmWiz.subtitle_codec_args_for_container("mkv", ["subrip", "mov_text"])
        self.assertTrue(problems)
        self.assertEqual(args[0], "-c:s")

    def test_impossible_subtitle_is_reported(self):
        args, problems = FFmWiz.subtitle_codec_args_for_container("avi", ["subrip"])
        self.assertEqual(args, [])
        self.assertTrue(any("avi" in p for p in problems))


def _10bit_answers(codec="H264", gpu=True):
    video = {"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080,
             "avg_frame_rate": "30/1", "r_frame_rate": "30/1", "pix_fmt": "yuv420p10le",
             "bits_per_raw_sample": "10", "color_range": "tv"}
    audio = {"codec_type": "audio", "codec_name": "aac", "channels": 2,
             "sample_rate": "48000", "bit_rate": "128000"}
    return {
        "ffmpeg": "ffmpeg", "ffprobe": "ffprobe",
        "input_path": Path(tempfile.gettempdir()) / "in.mkv",
        "output_location": Path(tempfile.gettempdir()),
        "streams": [video, audio], "video_streams": [video], "audio_streams": [audio],
        "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
        "format": {"duration": "10"}, "output_ext": "mp4",
        "video_codec": codec, "use_gpu": gpu, "gpu_available": True,
        "audio_codec": "aac", "audio_bitrate_kbps": 128, "audio_tracks": [0],
        "subtitle_tracks": [], "resolution": "n", "fps": None,
        "video_bitrate_kbps": 4000, "color_range_choice": "tv",
    }


class NvencBitDepthGate(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_h264_nvenc_is_never_paired_with_10bit(self):
        cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(_10bit_answers("H264"))]
        if FFmWiz.H264_NVENC_ENCODER in cmd:
            self.assertNotIn("p010le", " ".join(cmd),
                             "h264_nvenc has no 10-bit mode; p010le fails at encoder init")

    def test_10bit_h264_falls_back_to_a_capable_encoder(self):
        cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(_10bit_answers("H264"))]
        encoder = cmd[cmd.index("-c:v") + 1]
        self.assertNotEqual(encoder, FFmWiz.H264_NVENC_ENCODER)

    def test_10bit_hevc_still_uses_the_gpu(self):
        # hevc_nvenc DOES do Main10 -- the fix must not disable it.
        cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(_10bit_answers("H265"))]
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "hevc_nvenc")

    def test_8bit_h264_still_uses_the_gpu(self):
        answers = _10bit_answers("H264")
        answers["video_streams"][0]["pix_fmt"] = "yuv420p"
        answers["video_streams"][0]["bits_per_raw_sample"] = "8"
        cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
        self.assertEqual(cmd[cmd.index("-c:v") + 1], FFmWiz.H264_NVENC_ENCODER)

    def test_gate_is_encoder_specific_not_family_wide(self):
        answers = _10bit_answers()
        self.assertTrue(FFmWiz.high_bit_depth_requires_cpu_encoder(answers, "h264_nvenc"))
        self.assertFalse(FFmWiz.high_bit_depth_requires_cpu_encoder(answers, "hevc_nvenc"))


def _audio_only_answers(ext, chosen_codec):
    audio = {"codec_type": "audio", "codec_name": "flac", "channels": 2,
             "sample_rate": "48000"}
    return {
        "ffmpeg": "ffmpeg", "ffprobe": "ffprobe",
        "input_path": Path(tempfile.gettempdir()) / "in.mkv",
        "output_location": Path(tempfile.gettempdir()),
        "streams": [audio], "video_streams": [], "audio_streams": [audio],
        "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
        "format": {"duration": "2"}, "output_ext": ext,
        "video_codec": "copy", "use_gpu": False,
        "audio_codec": chosen_codec, "audio_bitrate_kbps": 128,
        "audio_tracks": [0], "subtitle_tracks": [], "resolution": "n", "fps": None,
        "video_bitrate_kbps": 500, "color_range_choice": "tv",
    }


class AudioCodecContainerPolicy(unittest.TestCase):
    """Only WebM used to be reconciled, so aac into .flac/.ogg/.opus was emitted."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_constrained_containers_replace_an_impossible_codec(self):
        for ext, expected in (("flac", "flac"), ("opus", "libopus"),
                              ("ogg", "libopus"), ("mp3", "libmp3lame"),
                              ("webm", "libopus")):
            with self.subTest(container=ext):
                cmd = [str(p) for p in FFmWiz.build_ffmpeg_command(_audio_only_answers(ext, "aac"))]
                self.assertEqual(cmd[cmd.index("-c:a") + 1], expected)

    def test_permissive_containers_keep_the_chosen_codec(self):
        # These really do mux on ffmpeg 8.1.1, so the guard must not reject them.
        for ext, codec in (("wav", "aac"), ("mkv", "pcm_s16le"), ("mp4", "flac")):
            with self.subTest(container=ext, codec=codec):
                cmd = [str(p) for p in FFmWiz.build_ffmpeg_command(_audio_only_answers(ext, codec))]
                self.assertEqual(cmd[cmd.index("-c:a") + 1], codec)

    def test_copy_is_always_allowed(self):
        for ext in ("flac", "opus", "ogg", "mp3", "webm"):
            with self.subTest(container=ext):
                cmd = [str(p) for p in FFmWiz.build_ffmpeg_command(_audio_only_answers(ext, "copy"))]
                self.assertEqual(cmd[cmd.index("-c:a") + 1], "copy")


class SpeedReverseFormatRestriction(unittest.TestCase):
    """The mode always maps video and hardcodes H.264 + AAC, so the prompt must
    not offer audio-only containers or WebM."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_allowed_list_excludes_what_the_builder_cannot_produce(self):
        allowed = set(FFmWiz.VIDEO_SPEED_REVERSE_FORMATS)
        for impossible in ("webm", "mp3", "flac", "opus", "ogg", "wav", "m4a", "aac"):
            self.assertNotIn(impossible, allowed)
        for possible in ("mp4", "mkv", "mov"):
            self.assertIn(possible, allowed)

    def test_prompt_rejects_a_container_outside_the_allow_list(self):
        import contextlib
        import io
        from ffmwiz import wizard_steps

        answers = {"input_path": Path("/tmp/in.mkv"),
                   "video_streams": [{"codec_type": "video"}], "_question_number": 1}
        replies = iter(["webm", "mp3", "mkv"])
        original = FFmWiz.appio.ask_raw
        buffer = io.StringIO()
        try:
            FFmWiz.appio.ask_raw = lambda *a, **k: next(replies)
            with contextlib.redirect_stdout(buffer):
                wizard_steps.step_output_format(
                    answers, allowed=FFmWiz.VIDEO_SPEED_REVERSE_FORMATS)
        finally:
            FFmWiz.appio.ask_raw = original
        printed = buffer.getvalue()
        self.assertIn("cannot produce .webm", printed)
        self.assertIn("cannot produce .mp3", printed)
        self.assertEqual(answers["output_ext"], "mkv")

    def test_unrestricted_prompt_still_accepts_everything(self):
        import contextlib
        import io
        from ffmwiz import wizard_steps

        answers = {"input_path": Path("/tmp/in.mkv"),
                   "video_streams": [{"codec_type": "video"}], "_question_number": 1}
        original = FFmWiz.appio.ask_raw
        try:
            FFmWiz.appio.ask_raw = lambda *a, **k: "webm"
            with contextlib.redirect_stdout(io.StringIO()):
                wizard_steps.step_output_format(answers)
        finally:
            FFmWiz.appio.ask_raw = original
        self.assertEqual(answers["output_ext"], "webm")


class SharedContainerResolvers(unittest.TestCase):
    """One resolver for HardSub, standalone Join and the main wizard, instead of
    three independent (and two missing) WebM special cases."""

    def test_video_codec_is_reconciled_only_where_needed(self):
        self.assertEqual(FFmWiz.container_video_codec("webm", "H264")[0], "VP9")
        self.assertEqual(FFmWiz.container_video_codec("webm", "VP9")[0], "VP9")
        self.assertEqual(FFmWiz.container_video_codec("webm", "AV1")[0], "AV1")
        for ext in ("mp4", "mkv", "mov", "ts"):
            self.assertEqual(FFmWiz.container_video_codec(ext, "H264")[0], "H264")

    def test_a_change_always_comes_with_an_explanation(self):
        _, note = FFmWiz.container_video_codec("webm", "H264")
        self.assertIsNotNone(note)
        self.assertIsNone(FFmWiz.container_video_codec("mp4", "H264")[1])

    def test_stream_copy_is_validated_against_the_source_codec(self):
        # Copying AAC into WebM fails exactly like encoding it would.
        args, note = FFmWiz.container_audio_encode_args("webm", "copy", None, source_codec="aac")
        self.assertEqual(args, ["-c:a", "libopus"])
        self.assertIsNotNone(note)
        args, note = FFmWiz.container_audio_encode_args("webm", "copy", None, source_codec="opus")
        self.assertEqual(args, ["-c:a", "copy"])
        self.assertIsNone(note)

    def test_lossless_targets_never_receive_a_bitrate(self):
        args, _ = FFmWiz.container_audio_encode_args("flac", "aac", 320)
        self.assertNotIn("-b:a", args)

    def test_unconstrained_container_keeps_copy(self):
        args, note = FFmWiz.container_audio_encode_args("mkv", "copy", None, source_codec="aac")
        self.assertEqual(args, ["-c:a", "copy"])
        self.assertIsNone(note)


@unittest.skipIf(not FFMPEG or not FFPROBE, "ffmpeg/ffprobe not on PATH")
class HardSubContainerRealMux(unittest.TestCase):
    """HardSub asked for container, video codec and audio policy independently,
    so H.264 + copied AAC into WebM reached the muxer and failed."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_hardsub_"))
        srt = cls._tmp / "s.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nhi\n\n", encoding="utf-8")
        cls._srt = srt
        cls._src = cls._tmp / "in.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=2",
             "-f", "lavfi", "-i", "sine=duration=2",
             "-c:v", "libx264", "-preset", "ultrafast", "-vf", "format=yuv420p",
             "-c:a", "aac", str(cls._src)], check=True, timeout=180)
        cls._probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(cls._src)],
            capture_output=True, text=True, timeout=60).stdout)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def _answers(self, ext, policy):
        streams = self._probe["streams"]
        return {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self._src,
            "output_location": self._tmp, "probe": self._probe, "streams": streams,
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
            "format": self._probe["format"], "output_ext": ext,
            "video_codec": "H264", "use_gpu": False,
            "hardsub_source": "external", "hardsub_subtitle_path": self._srt,
            "hardsub_audio_mode": "copy-all", "hardsub_audio_container_policy": policy,
            "hardsub_audio_bitrate_kbps": 128, "hardsub_quality": "balanced",
            "color_range_choice": "tv", "resolution": "n", "fps": None,
        }

    def test_webm_never_gets_h264_or_a_copied_aac_track(self):
        for policy in ("copy", "aac"):
            with self.subTest(audio_policy=policy):
                cmd = [str(p) for p in FFmWiz.build_hardsub_command(self._answers("webm", policy))]
                self.assertNotIn("libx264", cmd)
                self.assertNotIn("h264_nvenc", cmd)
                self.assertNotEqual(cmd[cmd.index("-c:a") + 1], "copy")

    def test_every_hardsub_combination_actually_muxes(self):
        for ext, policy in (("webm", "copy"), ("webm", "aac"), ("mp4", "aac"), ("mkv", "copy")):
            with self.subTest(container=ext, audio_policy=policy):
                cmd = [str(p) for p in FFmWiz.build_hardsub_command(self._answers(ext, policy))]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                self.assertEqual(
                    result.returncode, 0,
                    result.stderr.strip().splitlines()[-1] if result.stderr else "")

    def test_unconstrained_containers_keep_the_requested_codecs(self):
        cmd = [str(p) for p in FFmWiz.build_hardsub_command(self._answers("mp4", "aac"))]
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "libx264")
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "aac")


@unittest.skipIf(not FFMPEG or not FFPROBE, "ffmpeg/ffprobe not on PATH")
class JoinAudioContainerRealMux(unittest.TestCase):
    """The join output extension is inherited from input 0 while the builder
    hardcoded `-c:a aac`, so a FLAC join produced AAC-in-.flac."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_joinaudio_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def _make(self, name, codec_args, rate):
        path = self._tmp / name
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"sine=duration=2:sample_rate={rate}",
             *codec_args, str(path)], check=True, timeout=180)
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60).stdout)
        return {"path": path, "streams": probe["streams"], "format": probe["format"],
                "video_streams": [], "duration": 2.0,
                "audio_streams": [s for s in probe["streams"] if s["codec_type"] == "audio"]}

    def test_audio_join_uses_a_codec_the_container_accepts(self):
        cases = (("flac", ["-c:a", "flac"], "flac"),
                 ("ogg", ["-c:a", "libvorbis"], "libopus"),
                 ("mp3", ["-c:a", "libmp3lame"], "libmp3lame"))
        for ext, codec_args, expected in cases:
            with self.subTest(container=ext):
                # Different sample rates force the re-encode path.
                first = self._make(f"a.{ext}", codec_args, "48000")
                second = self._make(f"b.{ext}", codec_args, "44100")
                answers = {
                    "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": first["path"],
                    "output_location": self._tmp, "audio_streams": first["audio_streams"],
                    "video_streams": [], "subtitle_streams": [], "data_streams": [],
                    "attachment_streams": [], "streams": first["streams"],
                    "format": first["format"], "audio_bitrate_kbps": 192,
                    "audio_tracks": [0], "join_input_items": [second], "output_ext": ext,
                }
                out = self._tmp / f"joined.{ext}"
                cmd = [str(p) for p in FFmWiz.build_join_audio_encode_command(
                    answers, [first, second], out)]
                self.assertEqual(cmd[cmd.index("-c:a") + 1], expected)
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                self.assertEqual(
                    result.returncode, 0,
                    result.stderr.strip().splitlines()[-1] if result.stderr else "")


@unittest.skipIf(not FFMPEG or not FFPROBE, "ffmpeg/ffprobe not on PATH")
class SubtitleContainerRealMux(unittest.TestCase):
    """Build the real command per container and prove the muxer accepts it."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_subpolicy_"))
        srt = self._tmp / "s.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nhello\n\n", encoding="utf-8")
        self._src = self._tmp / "src.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=3",
             "-f", "lavfi", "-i", "sine=duration=3", "-i", str(srt),
             "-map", "0:v", "-map", "1:a", "-map", "2:s",
             "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-c:s", "srt",
             str(self._src)], check=True, timeout=180)
        self._probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(self._src)],
            capture_output=True, text=True, timeout=60).stdout)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _answers(self, ext):
        streams = self._probe["streams"]
        return {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self._src,
            "output_location": self._tmp, "probe": self._probe, "streams": streams,
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [s for s in streams if s["codec_type"] == "subtitle"],
            "data_streams": [], "attachment_streams": [], "format": self._probe["format"],
            "output_ext": ext,
            "video_codec": "VP9" if ext == "webm" else "H264", "use_gpu": False,
            "audio_codec": "libopus" if ext == "webm" else "aac", "audio_bitrate_kbps": 128,
            "audio_tracks": [0], "subtitle_tracks": [0], "resolution": "n", "fps": None,
            "video_bitrate_kbps": 500, "color_range_choice": "tv",
        }

    def test_every_container_produces_a_muxable_command(self):
        for ext in ("mkv", "mp4", "webm", "avi", "ts"):
            with self.subTest(container=ext):
                cmd = [str(p) for p in FFmWiz.build_ffmpeg_command(self._answers(ext))]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                self.assertEqual(
                    result.returncode, 0,
                    f".{ext} failed: {result.stderr.strip().splitlines()[-1] if result.stderr else ''}")

    def test_avi_drops_the_subtitle_map_entirely(self):
        cmd = [str(p) for p in FFmWiz.build_ffmpeg_command(self._answers("avi"))]
        subtitle_maps = [cmd[i + 1] for i, part in enumerate(cmd)
                         if part == "-map" and cmd[i + 1].startswith("0:s")]
        self.assertEqual(subtitle_maps, [], "AVI cannot carry subtitles; the map must be dropped too")


if __name__ == "__main__":
    unittest.main()
