"""Shared base TestCase for the split command-generation test modules.

Holds setUp/tearDown and every fixture helper that the CommandGeneration test
suite used to define inline; the topical test files subclass CommandGenBase.
Also provides _home_module (used by wizard step-order tests to restore
monkeypatched step functions on their true defining module).
"""
import contextlib
import io
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock
import FFmWiz
import ffmwiz
import cache_test_utils


def _home_module(name):
    # Return the ffmwiz submodule that DEFINES `name` (checked in dependency
    # order so re-exporters don't shadow the real definer), else the facade.
    # The wizard siblings come before `wizard`: the facade re-exports all of
    # them, but it is the definer a call site now reads, so it is the definer a
    # restore has to put back. Resolved on the package, not on the FFmWiz entry
    # script, which only re-exports a few of the submodules by name.
    for _mn in ('appio', 'runtime', 'services', 'runner', 'guibridge',
                'trackmanager', 'metadata', 'wizard_base', 'wizard_steps',
                'wizard_look', 'wizard_raw',
                'wizard_b', 'wizard_flow_b', 'wizard', 'modes'):
        _m = getattr(ffmwiz, _mn, None)
        if _m is not None and name in vars(_m):
            return _m
    return FFmWiz


class CommandGenBase(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        # Isolate the FFmpeg capability cache in a uniquely-owned temp directory
        # so tests can never touch the real/default cache. The previous value of
        # FFMWIZ_CACHE_DIR is saved and restored in tearDown (even on failure).
        self._prev_cache_env = os.environ.get("FFMWIZ_CACHE_DIR")
        self._cache_run_id = uuid.uuid4().hex
        self._cache_dir = cache_test_utils.create_owned_temp_cache_dir(self._cache_run_id)
        os.environ["FFMWIZ_CACHE_DIR"] = self._cache_dir
        FFmWiz.services._CAPABILITY_SESSION_MEMO.clear()

    def tearDown(self):
        # Restore the prior environment value rather than blindly unsetting it.
        if self._prev_cache_env is None:
            os.environ.pop("FFMWIZ_CACHE_DIR", None)
        else:
            os.environ["FFMWIZ_CACHE_DIR"] = self._prev_cache_env
        FFmWiz.services._CAPABILITY_SESSION_MEMO.clear()
        # Safe, ownership-verified removal of only this test's temp cache dir.
        cache_test_utils.safe_remove_owned_temp_dir(
            self._cache_dir, self._cache_run_id, tempfile.gettempdir())

    def base_answers(self, output_dir: str) -> dict:
        return {
            "ffmpeg": "ffmpeg",
            "ffprobe": "ffprobe",
            "input_path": Path("Pato12.mkv"),
            "output_location": Path(output_dir),
            "output_ext": "mp4",
            "video_streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 3440,
                    "height": 1440,
                    "avg_frame_rate": "30/1",
                    "color_range": "tv",
                }
            ],
            "audio_streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "bit_rate": "122000",
                    "duration": "6074.221",
                }
            ],
            "subtitle_streams": [],
            "data_streams": [],
            "audio_tracks": [0],
            "subtitle_tracks": [],
            "video_codec": "H265",
            "use_gpu": True,
            "crop_enabled": True,
            "crop_top": 172,
            "crop_left": 429,
            "crop_right": 1095,
            "crop_bottom": 189,
            "video_bitrate_kbps": 400,
            "video_bitrate_mode": "quality_vbr",
            "audio_codec": "aac",
            "audio_bitrate_kbps": 122,
            "resolution": FFmWiz.parse_resolution("480p"),
            "fps": 4,
            "format": {"duration": "6074.221"},
        }

    def _ensure_test_color_range(self, answers: dict) -> None:
        """Synthetic test sources frequently omit color_range. Real completed
        wizard/job state always resolves the range before reaching a builder
        (detected source range, or an explicit wizard/batch choice), so default
        an otherwise-unresolved fixture to a TV/Limited assumption. Tests that
        exercise color-range behavior set their own stream range or
        color_range_choice and are therefore left untouched."""
        stream = (answers.get("video_streams") or [{}])[0]
        has_range = FFmWiz.normalize_color_range(stream.get("color_range")) in {"tv", "pc"}
        if not has_range and not str(answers.get("color_range_choice") or "").strip():
            answers["color_range_choice"] = "tv"

    def command_for(self, answers: dict) -> list[str]:
        self._ensure_test_color_range(answers)
        return FFmWiz.build_ffmpeg_command(answers)

    def command_text(self, answers: dict) -> str:
        return " ".join(self.command_for(answers))

    def assert_not_contains_any(self, text: str, needles: list[str]) -> None:
        for needle in needles:
            self.assertNotIn(needle, text)

    def chapter(self, start: float, end: float, title: str) -> dict:
        return {
            "time_base": "1/1000",
            "start": int(start * 1000),
            "end": int(end * 1000),
            "start_time": f"{start:.6f}",
            "end_time": f"{end:.6f}",
            "tags": {"title": title},
        }

    def hardsub_answers(self, tmp: str, input_name: str = "input.mkv", output_ext: str = "mp4") -> dict:
        root = Path(tmp)
        input_path = root / input_name
        subtitle_path = root / "subtitle.srt"
        input_path.write_bytes(b"")
        subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nText\n", encoding="utf-8")
        return {
            "ffmpeg": "ffmpeg",
            "input_path": input_path,
            "output_location": root,
            "output_ext": output_ext,
            "video_codec": "H265",
            "use_gpu": False,
            "video_streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "color_range": "tv"}],
            "audio_streams": [{"codec_type": "audio", "codec_name": "aac"}],
            "hardsub_subtitle_source": "external",
            "hardsub_subtitle_path": subtitle_path,
            "hardsub_audio_mode": "copy-all",
            "hardsub_quality_mode": "near-lossless",
            "hardsub_hdr_handling": "standard",
            "format": {"duration": "1"},
        }

    def copy_cut_answers(self, chapters: list[dict], duration: float = 900.0) -> dict:
        return {
            "ffprobe": "ffprobe",
            "input_path": Path("input.mkv"),
            "probe": {"chapters": chapters},
            "format": {"duration": str(duration)},
        }

    def _extra_recipe_answers(self):
        # Minimal answers with one video + one audio stream selected.
        return {
            "video_streams": [{"codec_name": "h264", "width": 1920, "height": 1080}],
            "audio_streams": [{"codec_name": "aac", "sample_rate": "44100"}],
            "audio_tracks": [0],
            "audio_codec": "aac",
            "output_ext": "mp4",
            "video_codec": "H265",
        }

    # ===================================================================
    # Crop normalization (chroma/encoder alignment without black padding)
    # ===================================================================

    def _crop_answers(self, width, height, pix_fmt, left, right, top, bottom, resolution="n"):
        return {
            "video_streams": [{
                "codec_type": "video", "codec_name": "h264",
                "width": width, "height": height, "pix_fmt": pix_fmt,
            }],
            "crop_enabled": True,
            "crop_left": left, "crop_right": right,
            "crop_top": top, "crop_bottom": bottom,
            "resolution": resolution,
        }

    def base_answers_with_crop(self, width, height, pix_fmt, left, right, top, bottom):
        # A full no-resize encode answers set for command-text assertions.
        answers = self.base_answers(".")
        answers.update({
            "video_streams": [{
                "codec_type": "video", "codec_name": "h264",
                "width": width, "height": height, "pix_fmt": pix_fmt,
                "avg_frame_rate": "30/1", "color_range": "tv",
            }],
            "crop_enabled": True,
            "crop_left": left, "crop_right": right,
            "crop_top": top, "crop_bottom": bottom,
            "use_gpu": False,
            "resolution": "n",
        })
        answers.pop("fps", None)
        return answers

    # ===================================================================
    # Color-range resolution and menu
    # ===================================================================

    def _encode_answers(self, tmp, color_range=None, resolution="480p"):
        answers = self.base_answers(tmp)
        answers["use_gpu"] = False
        stream = dict(answers["video_streams"][0])
        if color_range is None:
            stream.pop("color_range", None)
        else:
            stream["color_range"] = color_range
        answers["video_streams"] = [stream]
        answers["resolution"] = FFmWiz.parse_resolution(resolution) if resolution != "n" else "n"
        return answers

    # ===================================================================
    # Folder/batch color-range policy
    # ===================================================================

    def _folder_settings(self, policy=None, per_file=None, codec="H265"):
        s = {"video_codec": codec, "folder_output_location": Path("."), "use_gpu": False}
        if policy:
            s["_batch_color_range_policy"] = policy
        if per_file:
            s["_batch_color_range_per_file"] = per_file
        return s

    def _folder_job(self, color_range=None):
        stream = {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
        if color_range is not None:
            stream["color_range"] = color_range
        return {"video_streams": [stream], "video_codec": "H265"}

    # ===================================================================
    # Color-range resolution contract (strict vs compatibility fallback)
    # ===================================================================

    def _unknown_range_encode(self, output_dir: str, use_gpu: bool = False) -> dict:
        """base_answers variant whose source color range is unknown."""
        answers = self.base_answers(output_dir)
        answers["use_gpu"] = use_gpu
        for stream in answers["video_streams"]:
            stream.pop("color_range", None)
        return answers

    # ===================================================================
    # 10-bit NVENC rendered command: p010le + main10, no 8-bit override
    # ===================================================================

    def _tenbit_answers(self, output_dir: str, use_gpu: bool) -> dict:
        answers = self.base_answers(output_dir)
        answers["use_gpu"] = use_gpu
        for stream in answers["video_streams"]:
            stream["codec_name"] = "hevc"
            stream["pix_fmt"] = "yuv420p10le"
        return answers

    # ===================================================================
    # Two-pass geometry + pixel-format parity (8-bit and 10-bit)
    # ===================================================================

    def _two_pass_field(self, cmd: list[str], flag: str) -> str:
        return cmd[cmd.index(flag) + 1] if flag in cmd else ""

    def _assert_two_pass_parity(self, answers: dict) -> None:
        cmd = self.command_for(answers)
        first, second, _ = FFmWiz.build_cpu_two_pass_commands(cmd, answers)
        for flag in ("-filter:v", "-profile:v", "-b:v", "-maxrate:v", "-bufsize:v"):
            self.assertEqual(
                self._two_pass_field(first, flag),
                self._two_pass_field(second, flag),
                msg=f"two-pass mismatch for {flag}",
            )

    def _summary_text(self, answers):
        cmd = self.command_for(answers)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            FFmWiz.print_summary(answers, cmd)
        return buf.getvalue()

    # ===================================================================
    # FFmpeg capability cache
    # ===================================================================

    def _cap_answers(self, codec="H265", gpu=False, ext="mkv"):
        return {"video_codec": codec, "use_gpu": gpu, "output_ext": ext,
                "ffmpeg": "ffmpeg", "ffprobe": "ffprobe",
                "video_streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}

    def _fake_identity(self, **over):
        base = {"os": "Windows", "arch": "AMD64", "ffmpeg_path": "C:/ff/ffmpeg.exe",
                "ffmpeg_size": 100, "ffmpeg_mtime": 1, "ffmpeg_version_line": "ffmpeg 8.1.1",
                "ffmpeg_build_hash": "abcd", "ffprobe_version_line": "ffprobe 8.1.1"}
        base.update(over)
        return base

    # ===================================================================
    # Workflow-level SAR/DAR provenance (end-to-end through production paths)
    # ===================================================================

    def _assert_raw_immutable(self, stream, sar_before, dar_before):
        self.assertEqual(stream.get("sample_aspect_ratio"), sar_before)
        self.assertEqual(stream.get("display_aspect_ratio"), dar_before)

    def _assert_detected_label_only_when_raw_valid(self, info):
        """A 'detected by ffprobe' label is allowed only when the matching raw
        ffprobe field was valid."""
        if "detected by ffprobe" == info["sar_source"]:
            self.assertIsNotNone(info["raw_ffprobe_sar"])
        if "detected by ffprobe" == info["dar_source"]:
            self.assertIsNotNone(info["raw_ffprobe_dar"])
        if info["fallback_used"]:
            self.assertNotIn("detected by ffprobe", info["sar_source"])
            self.assertNotIn("detected by ffprobe", info["dar_source"])
