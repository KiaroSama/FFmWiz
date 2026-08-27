"""Regression: a builder resolves a fallback, it does not rewrite the request (D15).

The state contract is that the REQUESTED answers survive Back while whatever
the build had to substitute lives in `_effective_settings`. The builders broke
both halves at once -- they wrote the fallback into the requested key and left
the effective map EMPTY, so nothing recorded that a substitution had happened
and `resolve_video_encoder()` only worked because the request had been
clobbered. Measured on the audited tree, through the public builders:

    request                       command             request after  effective
    video copy + crop             -c:v libx265        H265           {}
    video H264, audio aac, .webm  libvpx-vp9/libopus  VP9 / libopus  {}
    audio copy + 2x speed         -c:a aac            aac            {}
    audio aac -> .flac            -c:a flac           flac           {}

After the repair the same four builds leave the request alone and record the
resolution instead:

    video copy + crop             -c:v libx265        copy      video_codec=H265
    video H264, audio aac, .webm  libvpx-vp9/libopus  H264/aac  VP9 + libopus
    audio copy + 2x speed         -c:a aac            copy      audio_codec=aac
    audio aac -> .flac            -c:a flac           aac       audio_codec=flac

`AllowedBuilderWrites` freezes the whole rule rather than only the codecs: a
pure builder may add or change ONLY the keys listed there, each of which is an
artifact path, a derived name or a derived geometry the summary prints -- never
a value the user answered.
"""
from __future__ import annotations

import contextlib
import io
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import FFmWiz

from artifact_guard import NoLeakedArtifacts

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")


# Everything a PURE builder is allowed to add to, or change on, the answers
# dict it was handed. Established by measuring every public builder rather than
# by excluding whatever happened to fail, and each entry earns its place:
#
#   output_path            the artifact the executor opens and the summary shows
#   output_ext             DERIVED for the audio tools, which resolve the
#                          container from the source codec; the user answers
#                          `audio_tool_output_ext`, not this
#   output_collision_suffix derived naming input (`_Speed200_Reverse`, `_AudioCut`)
#   split_output_paths     the Split plan's parts; the executor writes them and
#   split_part_intervals   the summary lists them, so they must reach the caller
#   final_resolution       geometry derived from crop + resolution, printed by
#   crop_box_dimensions    the summary and read back by the pixel-format and
#   cropped_aspect_ratio   colour-range logging
#   resolution_scale_axis
#   _resolution_warning_emitted  once-only guard so one note is not printed twice
#   _hardsub_mode          which BUILDER was called, not anything the user
#                          answered. `can_use_cuda_fast_path()` reads it to keep
#                          the libass chain off the CUDA fast path, and the flag
#                          is only true because `build_hardsub_command` is the
#                          function that ran
#   _artifact_lease        the plan containers themselves. They are opened on the
#   _effective_settings    OUTER dict before any shallow copy and mutated in
#   _plan_revision         place, so a correct caller never sees them replaced.
ALLOWED_BUILDER_WRITES = frozenset({
    "output_path",
    "output_ext",
    "output_collision_suffix",
    "split_output_paths",
    "split_part_intervals",
    "final_resolution",
    "crop_box_dimensions",
    "cropped_aspect_ratio",
    "resolution_scale_axis",
    "_resolution_warning_emitted",
    "_hardsub_mode",
    FFmWiz.ARTIFACT_LEASE_KEY,
    FFmWiz.EFFECTIVE_SETTINGS_KEY,
    FFmWiz.PLAN_REVISION_KEY,
})

# Two capability normalizations that still write their requested key. They are
# NOT codec fallbacks and are deliberately left alone here:
#
#   use_gpu        cleared only when the machine reports no usable NVENC device,
#                  i.e. a fact about the hardware rather than a plan decision,
#                  and read straight from `answers` by fifteen call sites
#                  including the join builder.
#   cpu_two_pass   cleared by `normalize_cpu_two_pass_selection()`, whose own
#                  idempotence guard and `tests/test_two_pass_normalization.py`
#                  both assert the requested key goes false.
#
# Listed so the exception is visible and argued instead of hidden inside the
# allowed set. Neither is exercised by the fixtures below.
KNOWN_REQUESTED_KEY_NORMALIZATIONS = frozenset({"use_gpu", "cpu_two_pass"})


def _run(args, timeout=300):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, encoding="utf-8",
                          errors="replace", timeout=timeout)


@requires_ffmpeg
class RequestedSettingsSurviveTheBuild(NoLeakedArtifacts, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._class_tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_reqkeys_"))
        cls.source = cls._class_tmp / "src.mkv"
        result = _run([FFMPEG, "-hide_banner", "-v", "error", "-y",
                       "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=2",
                       "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                       "-map", "0:v", "-map", "1:a",
                       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-shortest", str(cls.source)])
        if result.returncode != 0:
            raise unittest.SkipTest("could not build the fixture: " + (result.stderr or "")[-300:])
        cls.probe = FFmWiz.ffprobe_full_json(FFPROBE, cls.source)
        cls.subtitle = cls._class_tmp / "s.srt"
        cls.subtitle.write_text("1\n00:00:00,000 --> 00:00:02,000\nhi\n\n",
                                encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._class_tmp, ignore_errors=True)

    def setUp(self):
        super().setUp()
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_reqkeys_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    # ---- fixtures ----
    def _answers(self, **extra):
        streams = self.probe.get("streams", [])
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.source,
            "probe": self.probe, "format": self.probe.get("format", {}),
            "video_streams": [s for s in streams if s.get("codec_type") == "video"],
            "audio_streams": [s for s in streams if s.get("codec_type") == "audio"],
            "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
            "audio_tracks": [0], "subtitle_tracks": [],
            "video_codec": "copy", "audio_codec": "copy", "use_gpu": False,
            "output_location": self._tmp, "output_ext": "mkv",
            # Stated explicitly: an FFmpeg build that cannot report a range
            # raises ColorRangeUnresolvedError rather than silently assuming.
            "color_range_choice": "tv",
            "resolution": "n",
        }
        answers.update(extra)
        self.own(answers)
        FFmWiz.artifact_lease(answers)
        FFmWiz.begin_plan(answers)
        return answers

    def _build(self, builder, answers, *args):
        """Run a builder with the console silenced. Returns the argv as strings."""
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(FFmWiz.appio, "note", lambda *a, **k: None):
            return [str(part) for part in builder(answers, *args)]

    def _unexpected_writes(self, before, answers):
        """Keys the builder added or replaced that it had no business touching."""
        added = {key for key in answers if key not in before}
        changed = {key for key in before
                   if key in answers and answers[key] is not before[key]
                   and answers[key] != before[key]}
        return sorted((added | changed) - ALLOWED_BUILDER_WRITES)

    def _option(self, cmd, *names):
        for name in names:
            if name in cmd:
                return cmd[cmd.index(name) + 1]
        return None

    def _codecs(self, path):
        probe = FFmWiz.ffprobe_full_json(FFPROBE, path)
        return [(s.get("codec_type"), s.get("codec_name")) for s in probe.get("streams", [])]

    def _execute(self, answers):
        result = _run([str(part) for part in answers["cmd"]])
        self.assertEqual(0, result.returncode,
                         "command failed:\n" + (result.stderr or "")[-600:])
        return Path(answers["output_path"])

    # ---- the four measured reproductions ----
    def test_crop_over_a_copy_request_resolves_without_rewriting_the_request(self):
        answers = self._answers(crop_enabled=True, crop_top=10, crop_left=0,
                                crop_right=0, crop_bottom=10)
        before = dict(answers)
        cmd = self._build(FFmWiz.build_ffmpeg_command, answers)
        self.assertEqual("libx265", self._option(cmd, "-c:v", "-c:v:0"),
                         "crop really does force an encoder over a copy request")
        self.assertEqual("copy", answers["video_codec"],
                         "Back must still see the codec the user asked for")
        self.assertEqual("H265", FFmWiz.effective_value(answers, "video_codec"),
                         "and the substitution has to be recorded somewhere")
        self.assertEqual([], self._unexpected_writes(before, answers))

    def test_a_webm_container_fallback_leaves_both_requests_alone(self):
        answers = self._answers(video_codec="H264", audio_codec="aac", output_ext="webm")
        before = dict(answers)
        cmd = self._build(FFmWiz.build_ffmpeg_command, answers)
        self.assertEqual("libvpx-vp9", self._option(cmd, "-c:v", "-c:v:0"))
        self.assertEqual("libopus", self._option(cmd, "-c:a"))
        self.assertEqual("H264", answers["video_codec"])
        self.assertEqual("aac", answers["audio_codec"])
        self.assertEqual("VP9", FFmWiz.effective_value(answers, "video_codec"))
        self.assertEqual("libopus", FFmWiz.effective_value(answers, "audio_codec"))
        self.assertEqual([], self._unexpected_writes(before, answers))

    def test_an_audio_filter_over_a_copy_request_resolves_without_rewriting_it(self):
        answers = self._answers(audio_speed_enabled=True, audio_speed_factor=2.0)
        before = dict(answers)
        cmd = self._build(FFmWiz.build_ffmpeg_command, answers)
        self.assertEqual("aac", self._option(cmd, "-c:a"))
        self.assertEqual("copy", answers["audio_codec"])
        self.assertEqual("aac", FFmWiz.effective_value(answers, "audio_codec"))
        self.assertEqual([], self._unexpected_writes(before, answers))

    def test_a_container_incompatible_audio_codec_is_replaced_only_in_effect(self):
        answers = self._answers(video_codec="copy", audio_codec="aac", output_ext="flac")
        before = dict(answers)
        cmd = self._build(FFmWiz.build_ffmpeg_command, answers)
        self.assertEqual("flac", self._option(cmd, "-c:a"))
        self.assertEqual("aac", answers["audio_codec"])
        self.assertEqual("flac", FFmWiz.effective_value(answers, "audio_codec"))
        self.assertEqual([], self._unexpected_writes(before, answers))

    # ---- the same rule across the other public builders ----
    def test_the_split_audio_fallback_does_not_rewrite_the_request(self):
        # append_audio_encode_options(): Split re-encodes, so a copy request
        # cannot stand -- but it is still the user's answer.
        answers = self._answers(video_codec="H264", audio_codec="copy",
                                separator_points=[1.0])
        before = dict(answers)
        cmd = self._build(FFmWiz.build_ffmpeg_command, answers)
        self.assertEqual("aac", self._option(cmd, "-c:a"))
        self.assertEqual("copy", answers["audio_codec"])
        self.assertEqual("aac", FFmWiz.effective_value(answers, "audio_codec"))
        self.assertEqual([], self._unexpected_writes(before, answers))

    def test_the_audio_tool_builders_only_write_their_documented_keys(self):
        for name, builder, extra in (
            ("audio speed/reverse", FFmWiz.build_audio_speed_reverse_command,
             dict(audio_index=0, reverse_audio=True, speed_factor=2.0,
                  audio_tool_output_ext="flac")),
            ("audio cut", FFmWiz.build_audio_cut_command,
             dict(audio_index=0, audio_keep_ranges=[(0.0, 1.0)],
                  audio_tool_output_ext="flac")),
            ("audio transform", FFmWiz.build_audio_transform_command,
             dict(audio_index=0, reverse_audio=True, audio_speed_enabled=True,
                  audio_speed_factor=1.5, audio_cut_keep_ranges=[(0.0, 1.0)],
                  audio_tool_output_ext="flac")),
            ("video speed/reverse", FFmWiz.build_video_speed_reverse_command,
             dict(reverse_video=True, speed_factor=1.0, include_audio=True)),
        ):
            with self.subTest(builder=name):
                answers = self._answers(**extra)
                before = dict(answers)
                self._build(builder, answers)
                self.assertEqual([], self._unexpected_writes(before, answers))

    def _hardsub_answers(self, **extra):
        extra.setdefault("video_codec", "H264")
        answers = self._answers(
            hardsub_subtitle_source="external",
            hardsub_subtitle_path=self.subtitle,
            hardsub_audio_mode="copy-all",
            hardsub_audio_container_policy="aac",
            hardsub_audio_bitrate_kbps=128,
            hardsub_quality="balanced",
            **extra)
        return answers

    def test_the_hardsub_container_fallback_is_recorded_and_not_written_back(self):
        # Measured on the audited tree, through the public HardSub builder:
        #     REQUEST_BEFORE H264   REQUEST_AFTER VP9
        #     EFFECTIVE_CODEC None  ENCODER libvpx-vp9
        # The fallback is right for .webm; overwriting the request is not, and
        # it left the effective map EMPTY so nothing recorded the substitution
        # at all (D11).
        answers = self._hardsub_answers(output_ext="webm")
        before = dict(answers)
        cmd = self._build(FFmWiz.build_hardsub_command, answers)
        self.assertEqual("libvpx-vp9", self._option(cmd, "-c:v", "-c:v:0"),
                         ".webm really does force VP9 over an H264 request")
        self.assertEqual("H264", answers["video_codec"],
                         "Back must still see the codec the user asked for")
        self.assertEqual("VP9", FFmWiz.effective_value(answers, "video_codec"),
                         "and the substitution has to be recorded somewhere")
        self.assertEqual([], self._unexpected_writes(before, answers))

    def test_a_hardsub_container_that_allows_the_request_records_nothing(self):
        # The effective map is for SUBSTITUTIONS. Writing to it unconditionally
        # would make every build look like a fallback.
        answers = self._hardsub_answers(output_ext="mkv")
        before = dict(answers)
        cmd = self._build(FFmWiz.build_hardsub_command, answers)
        self.assertEqual("libx264", self._option(cmd, "-c:v", "-c:v:0"))
        self.assertEqual("H264", answers["video_codec"])
        self.assertNotIn("video_codec", FFmWiz.effective_settings(answers))
        self.assertEqual([], self._unexpected_writes(before, answers))

    def test_a_hardsub_copy_request_records_the_encoder_it_really_uses(self):
        # HardSub burns the subtitles in, so `copy` can never stand. The
        # summary line prints `effective_value(answers, "video_codec")`, so
        # leaving the request in place there would have it announce a copy over
        # a `-c:v libx265` command.
        answers = self._hardsub_answers(video_codec="copy", output_ext="mkv")
        before = dict(answers)
        cmd = self._build(FFmWiz.build_hardsub_command, answers)
        self.assertEqual("libx265", self._option(cmd, "-c:v", "-c:v:0"))
        self.assertEqual("copy", answers["video_codec"])
        self.assertEqual("H265", FFmWiz.effective_value(answers, "video_codec"))
        self.assertEqual([], self._unexpected_writes(before, answers))

    def test_the_join_builder_only_writes_its_documented_keys(self):
        item = self._join_item(self.source)
        answers = self._answers(join_input_items=[item, item])
        before = dict(answers)
        self._build(FFmWiz.build_join_encode_command, answers, [item, item],
                    self._tmp / "joined.mkv")
        self.assertEqual("copy", answers["video_codec"])
        self.assertEqual("libx265", FFmWiz.effective_value(answers, "video_codec"),
                         "a join across the concat filter really does re-encode")
        self.assertEqual([], self._unexpected_writes(before, answers))

    def _join_item(self, path):
        probe = FFmWiz.ffprobe_full_json(FFPROBE, path)
        streams = probe.get("streams", [])
        return {
            "path": path, "probe": probe, "format": probe.get("format", {}),
            "streams": streams,
            "video_streams": [s for s in streams if s.get("codec_type") == "video"],
            "audio_streams": [s for s in streams if s.get("codec_type") == "audio"],
            "subtitle_streams": [], "attachment_streams": [], "data_streams": [],
            "duration": float(probe.get("format", {}).get("duration") or 0.0),
        }

    def test_the_two_known_normalizations_are_not_quietly_inside_the_allowed_set(self):
        # The exception list is only honest while it stays an exception.
        self.assertEqual(frozenset(),
                         KNOWN_REQUESTED_KEY_NORMALIZATIONS & ALLOWED_BUILDER_WRITES)

    # ---- the media, not the argv ----
    def test_back_after_a_crop_fallback_rebuilds_the_copy_the_user_asked_for(self):
        answers = self._answers(crop_enabled=True, crop_top=10, crop_left=0,
                                crop_right=0, crop_bottom=10)
        answers["cmd"] = self._build(FFmWiz.build_ffmpeg_command, answers)
        cropped = self._execute(answers)
        self.assertEqual("hevc", dict(self._codecs(cropped)).get("video"))

        # Back: the requested answers change, the derived ones go.
        answers["crop_enabled"] = False
        for derived in ("output_path", "cmd", "final_resolution",
                        "crop_box_dimensions", "cropped_aspect_ratio"):
            answers.pop(derived, None)
        answers["cmd"] = self._build(FFmWiz.build_ffmpeg_command, answers)
        plain = self._execute(answers)
        self.assertEqual([("video", "h264"), ("audio", "aac")], self._codecs(plain),
                         "the crop fallback must not survive the crop")
        self.assertEqual(self._packet_hash(self.source, "0:v:0"),
                         self._packet_hash(plain, "0:v:0"),
                         "video packets must be byte-identical to the source")

    def _packet_hash(self, path, selector):
        result = _run([FFMPEG, "-hide_banner", "-v", "error", "-i", str(path),
                       "-map", selector, "-c", "copy", "-f", "md5", "-"])
        self.assertEqual(0, result.returncode, result.stderr)
        return (result.stdout or "").strip()

    def test_an_audio_copy_request_with_loudnorm_still_reads_copy_after_the_build(self):
        answers = self._answers(video_codec="copy", audio_codec="copy",
                                loudnorm_enabled=True, loudnorm_mode="single")
        answers["cmd"] = self._build(FFmWiz.build_ffmpeg_command, answers)
        output = self._execute(answers)
        self.assertEqual("aac", dict(self._codecs(output)).get("audio"),
                         "LoudNorm really does force an audio encoder")
        self.assertEqual("copy", answers["audio_codec"])
        self.assertEqual("aac", FFmWiz.effective_value(answers, "audio_codec"))


if __name__ == "__main__":
    unittest.main()
