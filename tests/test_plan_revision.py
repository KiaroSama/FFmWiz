"""Regression: a resolved setting must not outlive the plan that produced it (B13).

`resolve_video_encoder()` reads the RESOLVED codec in preference to the
requested one, which is what makes a summary tell the truth about a join that
had to re-encode (F07/R10). Nothing ever ENDED that resolution, though, so the
map behaved like session state. Measured on real media, through the wizard's
own `step_start_now`:

    requested                     video_codec=copy, audio_codec=copy
    build 1 (join, 192x108+320x180 -> must re-encode)
                                  effective video_codec=libx265
    Back, join removed, rebuild   -c:v libx265   <-- requested copy
    output video codec            hevc           <-- source was h264

The same leak turned a GPU build's `hevc_nvenc` into the encoder for a job with
the GPU switched off, kept a container fallback's `libx265`/`aac` in force after
the container changed back, and left `cpu_two_pass=False` -- recorded because a
join cannot two-pass -- in force for a plain encode that can.

`reset_effective_settings()` closed that for the interactive step and for each
Folder item, but not for the BUILDER, which any caller can reach directly and
which `step_start_folder_now()` reaches without resetting anything. Measured on
the audited tree:

    a direct build carrying a previous plan's effective libx265
        requested  video_codec=copy
        command    -c:v libx265
    step_start_folder_now() contains reset_effective_settings   False

`begin_plan()` / `require_plan_revision()` move the boundary into the builder,
where every caller shares it: an OPEN plan (one the caller just began) is
adopted so a step and its builder resolve into the same revision, and anything
else -- a direct call, a rebuild after Back -- gets a new revision with an empty
map. A map tagged with a revision the answers do not claim is refused outright.

These tests drive the public wizard step and check the ENCODED FILE wherever a
file can settle the question: probe codec names and per-stream packet hashes,
not command substrings.
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
import cache_test_utils

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")


def _run(args, timeout=300):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, encoding="utf-8",
                          errors="replace", timeout=timeout)


class EffectiveMapLifetime(unittest.TestCase):
    """The mechanism, without ffmpeg."""

    def test_a_reset_forgets_the_previous_plans_resolutions(self):
        answers = {"video_codec": "copy"}
        FFmWiz.effective_settings(answers)["video_codec"] = "libx265"
        self.assertEqual("libx265", FFmWiz.effective_value(answers, "video_codec"))
        FFmWiz.reset_effective_settings(answers)
        self.assertEqual("copy", FFmWiz.effective_value(answers, "video_codec"))

    def test_a_reset_installs_a_new_map_so_an_older_copy_cannot_write_into_it(self):
        # A folder representative, or an earlier revision, may still be holding a
        # shallow copy. Clearing in place would let it keep writing into the new
        # plan's map; replacing the object cannot.
        answers = {"video_codec": "copy"}
        FFmWiz.effective_settings(answers)
        stale_copy = dict(answers)
        FFmWiz.reset_effective_settings(answers)
        FFmWiz.effective_settings(stale_copy)["video_codec"] = "libx265"
        self.assertEqual("copy", FFmWiz.effective_value(answers, "video_codec"))
        self.assertEqual("libx265", FFmWiz.effective_value(stale_copy, "video_codec"))

    def test_the_lease_keeps_its_own_rule(self):
        # The lease and the map share a mechanism but not a lifetime: a lease has
        # to outlive the copies so its temporary files can still be deleted.
        answers = {}
        lease = FFmWiz.artifact_lease(answers)
        FFmWiz.reset_effective_settings(answers)
        self.assertIs(lease, FFmWiz.artifact_lease(dict(answers)))


class PlanRevisionOwnership(unittest.TestCase):
    """The boundary itself, without ffmpeg."""

    def test_begin_plan_numbers_the_revision_and_tags_its_map(self):
        answers = {"video_codec": "copy"}
        first = FFmWiz.begin_plan(answers)
        self.assertEqual(first, FFmWiz.plan_revision(answers))
        self.assertEqual(first, answers[FFmWiz.EFFECTIVE_SETTINGS_KEY].revision)
        second = FFmWiz.begin_plan(answers)
        self.assertNotEqual(first, second, "two plans must not share a number")
        self.assertEqual({}, dict(answers[FFmWiz.EFFECTIVE_SETTINGS_KEY]))

    def test_a_builder_adopts_the_plan_its_caller_just_began(self):
        # A step resolves into the same map its builder writes, which is what
        # lets the summary describe the command that will actually run.
        answers = {"video_codec": "copy"}
        begun = FFmWiz.begin_plan(answers)
        opened = answers[FFmWiz.EFFECTIVE_SETTINGS_KEY]
        self.assertEqual(begun, FFmWiz.require_plan_revision(answers))
        self.assertIs(opened, answers[FFmWiz.EFFECTIVE_SETTINGS_KEY])

    def test_a_second_build_cannot_inherit_the_first_ones_resolutions(self):
        answers = {"video_codec": "copy"}
        FFmWiz.begin_plan(answers)
        first = FFmWiz.require_plan_revision(answers)
        FFmWiz.effective_settings(answers)["video_codec"] = "libx265"
        second = FFmWiz.require_plan_revision(answers)
        self.assertNotEqual(first, second)
        self.assertEqual("copy", FFmWiz.effective_value(answers, "video_codec"))

    def test_a_direct_call_with_no_plan_gets_a_fresh_one(self):
        answers = {"video_codec": "copy"}
        FFmWiz.effective_settings(answers)["video_codec"] = "libx265"
        FFmWiz.require_plan_revision(answers)
        self.assertEqual("copy", FFmWiz.effective_value(answers, "video_codec"))

    def test_a_map_owned_by_another_revision_is_refused(self):
        # Two plans spliced together. No correct caller does this, so it is a
        # defect report rather than a silent recovery.
        answers = {"video_codec": "copy"}
        FFmWiz.begin_plan(answers)
        answers[FFmWiz.EFFECTIVE_SETTINGS_KEY] = FFmWiz.EffectiveSettings(-1)
        with self.assertRaises(FFmWiz.PlanRevisionError):
            FFmWiz.require_plan_revision(answers)

    def test_the_lease_still_survives_a_new_plan(self):
        # Same rule as reset_effective_settings: a lease outlives revisions so
        # its temporary files can still be deleted.
        answers = {}
        lease = FFmWiz.artifact_lease(answers)
        FFmWiz.begin_plan(answers)
        self.assertIs(lease, FFmWiz.artifact_lease(answers))


@requires_ffmpeg
class RebuiltPlansForgetTheLastOne(NoLeakedArtifacts, unittest.TestCase):
    """Back, change the plan, rebuild -- on real media, through step_start_now."""

    def setUp(self):
        super().setUp()
        FFmWiz.appio.USE_COLOR = False
        self._cache_run_id = __import__("uuid").uuid4().hex
        self._cache_dir = cache_test_utils.create_owned_temp_cache_dir(self._cache_run_id)
        self._prev_cache = __import__("os").environ.get("FFMWIZ_CACHE_DIR")
        __import__("os").environ["FFMWIZ_CACHE_DIR"] = self._cache_dir
        FFmWiz.services._CAPABILITY_SESSION_MEMO.clear()
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_planrev_"))

    def tearDown(self):
        import os
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._prev_cache is None:
            os.environ.pop("FFMWIZ_CACHE_DIR", None)
        else:
            os.environ["FFMWIZ_CACHE_DIR"] = self._prev_cache
        FFmWiz.services._CAPABILITY_SESSION_MEMO.clear()
        cache_test_utils.safe_remove_owned_temp_dir(
            self._cache_dir, self._cache_run_id, tempfile.gettempdir())
        super().tearDown()

    # ---- fixtures ----
    def _clip(self, name, size="192x108", seconds=1.0):
        path = self._tmp / f"{name}.mkv"
        result = _run([FFMPEG, "-hide_banner", "-v", "error", "-y",
                       "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=10:duration={seconds}",
                       "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                       "-map", "0:v", "-map", "1:a",
                       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-shortest", str(path)])
        if result.returncode != 0:
            self.skipTest("could not build synthetic source: " + (result.stderr or "")[-300:])
        return path

    def _item(self, path):
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

    def _answers(self, primary, **extra):
        item = self._item(primary)
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
            "input_path": primary, "probe": item["probe"], "format": item["format"],
            "video_streams": item["video_streams"], "audio_streams": item["audio_streams"],
            "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
            "audio_tracks": [0], "subtitle_tracks": [],
            "video_codec": "copy", "audio_codec": "copy", "use_gpu": False,
            "output_location": self._tmp, "output_ext": "mkv",
            # Every real-media fixture states the range explicitly: an FFmpeg
            # build that cannot report one raises ColorRangeUnresolvedError
            # rather than silently assuming (B16).
            "color_range_choice": "tv",
            "resolution": "n",
        }
        answers.update(extra)
        self.own(answers)
        FFmWiz.artifact_lease(answers)
        return answers

    def _start_now(self, answers):
        """Run the wizard's build step; decline execution. Returns (cmd, summary)."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), \
                mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=False), \
                mock.patch.object(FFmWiz.appio, "note", lambda *a, **k: None):
            FFmWiz.step_start_now(answers)
        return ([str(part) for part in answers["cmd"]],
                re.sub(r"\x1b\[[0-9;]*m", "", buffer.getvalue()))

    def _go_back(self, answers, **changes):
        """What Back does: the requested answers change, the derived ones go."""
        for key, value in changes.items():
            if value is None:
                answers.pop(key, None)
            else:
                answers[key] = value
        for derived in ("output_path", "cmd", "final_resolution"):
            answers.pop(derived, None)

    def _execute(self, answers):
        result = _run([str(part) for part in answers["cmd"]])
        self.assertEqual(0, result.returncode,
                         "rebuilt command failed:\n" + (result.stderr or "")[-600:])
        return Path(answers["output_path"])

    def _codecs(self, path):
        probe = FFmWiz.ffprobe_full_json(FFPROBE, path)
        return [(s.get("codec_type"), s.get("codec_name")) for s in probe.get("streams", [])]

    def _packet_hash(self, path, selector):
        """MD5 of one stream's packet payloads -- equal only for a true copy."""
        result = _run([FFMPEG, "-hide_banner", "-v", "error", "-i", str(path),
                       "-map", selector, "-c", "copy", "-f", "md5", "-"])
        self.assertEqual(0, result.returncode, result.stderr)
        return (result.stdout or "").strip()

    # ---- the plan revisions ----
    def test_a_join_fallback_does_not_survive_the_rebuilt_copy_job(self):
        primary, other = self._clip("a"), self._clip("b", size="320x180")
        answers = self._answers(primary, join_input_items=[self._item(other)])
        self._start_now(answers)
        self.assertEqual("libx265", FFmWiz.effective_value(answers, "video_codec"),
                         "the join really does have to re-encode")

        self._go_back(answers, join_input_items=None)
        self._start_now(answers)
        output = self._execute(answers)
        self.assertEqual([("video", "h264"), ("audio", "aac")], self._codecs(output),
                         "a copy/copy job must not inherit the join's encoder")
        self.assertEqual(self._packet_hash(primary, "0:v:0"),
                         self._packet_hash(output, "0:v:0"),
                         "video packets must be byte-identical to the source")

    def test_the_join_build_leaves_the_requested_answers_alone(self):
        # Back has to show the user their own choice, not the fallback.
        primary, other = self._clip("a"), self._clip("b", size="320x180")
        answers = self._answers(primary, join_input_items=[self._item(other)])
        self._start_now(answers)
        self.assertEqual("copy", answers["video_codec"])
        self.assertEqual("copy", answers["audio_codec"])

    def test_a_container_fallback_does_not_survive_the_container_change(self):
        primary, other = self._clip("a"), self._clip("b", size="320x180")
        answers = self._answers(primary, join_input_items=[self._item(other)],
                                output_ext="webm")
        self._start_now(answers)
        self.assertNotEqual("copy", FFmWiz.effective_value(answers, "video_codec"))
        self.assertNotEqual("copy", FFmWiz.effective_value(answers, "audio_codec"))

        self._go_back(answers, join_input_items=None, output_ext="mkv")
        _cmd, summary = self._start_now(answers)
        output = self._execute(answers)
        self.assertEqual([("video", "h264"), ("audio", "aac")], self._codecs(output))
        self.assertEqual(self._packet_hash(primary, "0:a:0"),
                         self._packet_hash(output, "0:a:0"),
                         "audio packets must be byte-identical to the source")
        self.assertIn("audio codec: copy", summary,
                      "the summary must describe THIS plan, not the last one")
        self.assertIn("video codec: copy", summary)

    def test_a_gpu_encoder_does_not_survive_turning_the_gpu_off(self):
        primary, other = self._clip("a"), self._clip("b", size="320x180")
        answers = self._answers(primary, join_input_items=[self._item(other)],
                                use_gpu=True, video_codec="H265")
        self._start_now(answers)
        self.assertIn("nvenc", str(FFmWiz.effective_value(answers, "video_codec")),
                      "the GPU build must have resolved to an NVENC encoder")

        self._go_back(answers, join_input_items=None, use_gpu=False, video_codec="copy")
        cmd, _summary = self._start_now(answers)
        self.assertEqual(("copy", None, None), FFmWiz.resolve_video_encoder(answers))
        self.assertEqual([], [part for part in cmd if "nvenc" in part],
                         "a GPU-less plan must not carry the previous plan's NVENC encoder")
        output = self._execute(answers)
        self.assertEqual([("video", "h264"), ("audio", "aac")], self._codecs(output))

    def test_a_two_pass_downgrade_does_not_survive_into_a_job_that_supports_it(self):
        # Cuts make CPU two-pass impossible, and that is recorded. A plain
        # bitrate encode CAN two-pass, so the downgrade must not follow it.
        primary = self._clip("a", seconds=4.0)
        answers = self._answers(
            primary, video_codec="H264", audio_codec="aac", cpu_two_pass=True,
            video_bitrate_kbps=300, video_bitrate_mode="target_bitrate",
            cut_keep_ranges=[(0.0, 1.0), (2.0, 3.0)])
        self._start_now(answers)
        self.assertIs(False, FFmWiz.effective_value(answers, "cpu_two_pass"),
                      "cuts really do turn CPU two-pass off")

        self._go_back(answers, cut_keep_ranges=None, cpu_two_pass=True)
        _cmd, summary = self._start_now(answers)
        self.assertIs(True, FFmWiz.effective_value(answers, "cpu_two_pass"))
        self.assertIn("CPU two-pass: yes", summary)

    def test_a_direct_builder_call_does_not_inherit_a_previous_plans_codec(self):
        # The D14 reproduction: no wizard step, just the public builder handed
        # an answers dict that still carries an older plan's resolution.
        primary = self._clip("a")
        answers = self._answers(primary)
        FFmWiz.effective_settings(answers)["video_codec"] = "libx265"
        FFmWiz.effective_settings(answers)["audio_codec"] = "aac"
        silent = mock.patch.object(FFmWiz.appio, "note", lambda *a, **k: None)
        with contextlib.redirect_stdout(io.StringIO()), silent:
            answers["cmd"] = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
        self.assertEqual("copy", answers["video_codec"])
        self.assertEqual("copy", FFmWiz.effective_value(answers, "video_codec"))
        output = self._execute(answers)
        self.assertEqual([("video", "h264"), ("audio", "aac")], self._codecs(output))
        self.assertEqual(self._packet_hash(primary, "0:v:0"),
                         self._packet_hash(output, "0:v:0"),
                         "a stale effective codec must not re-encode a copy job")

    def test_the_folder_representative_step_forgets_its_previous_build(self):
        # step_start_folder_now() calls the builder and resets nothing itself,
        # which is the gap D14 names, so the builder has to be the boundary.
        primary = self._clip("a")
        answers = self._answers(primary, crop_enabled=True, crop_top=10,
                                crop_left=0, crop_right=0, crop_bottom=10)
        decline = mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=False)
        silent = mock.patch.object(FFmWiz.appio, "note", lambda *a, **k: None)
        with contextlib.redirect_stdout(io.StringIO()), decline, silent:
            FFmWiz.step_start_folder_now(answers)
            first = FFmWiz.plan_revision(answers)
            self.assertEqual("H265", FFmWiz.effective_value(answers, "video_codec"),
                             "crop really does force an encoder over a copy request")
            self._go_back(answers, crop_enabled=False)
            FFmWiz.step_start_folder_now(answers)
        self.assertNotEqual(first, FFmWiz.plan_revision(answers))
        self.assertEqual("copy", FFmWiz.effective_value(answers, "video_codec"))
        answers["cmd"] = [str(part) for part in answers["cmd"]]
        output = self._execute(answers)
        self.assertEqual([("video", "h264"), ("audio", "aac")], self._codecs(output))
        self.assertEqual(self._packet_hash(primary, "0:v:0"),
                         self._packet_hash(output, "0:v:0"),
                         "the crop fallback must not survive the crop")


@requires_ffmpeg
class EveryPublicBuilderOwnsTheBoundary(RebuiltPlansForgetTheLastOne):
    """HardSub and Join reached the encoder without entering a plan revision.

    `build_ffmpeg_command` took the boundary in D14; the other two public
    builders did not, so a map left over from an earlier plan still decided
    what they encoded. Measured on the audited tree, by direct call:

        HardSub  FRESH_REQUEST H264  STALE_EFFECTIVE VP9  ENCODER libvpx-vp9
                 PLAN_REVISION 2 -> 2        (an already-built revision reused)
        Join     FRESH_REQUEST H264  STALE_EFFECTIVE VP9  ENCODER libvpx-vp9
                 PLAN_REVISION None -> None  (no revision was ever opened)

    A direct public-builder call has to be as safe as a complete wizard flow,
    because Folder items, Back/rebuild and the reverse pipeline's stages all
    reach these builders without a step in between.
    """

    def _subtitle(self):
        path = self._tmp / "s.srt"
        path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n\n", encoding="utf-8")
        return path

    def _hardsub_answers(self, primary, **extra):
        return self._answers(
            primary, video_codec="H264",
            hardsub_subtitle_source="external", hardsub_subtitle_path=self._subtitle(),
            hardsub_audio_mode="copy-all", hardsub_audio_container_policy="aac",
            hardsub_audio_bitrate_kbps=128, hardsub_quality="balanced", **extra)

    def _build(self, builder, answers, *args):
        silent = mock.patch.object(FFmWiz.appio, "note", lambda *a, **k: None)
        with contextlib.redirect_stdout(io.StringIO()), silent:
            return [str(part) for part in builder(answers, *args)]

    def _encoder(self, cmd):
        for name in ("-c:v", "-c:v:0"):
            if name in cmd:
                return cmd[cmd.index(name) + 1]
        return None

    # ---- D12 ----
    def test_the_hardsub_builder_does_not_inherit_a_previous_plans_codec(self):
        answers = self._hardsub_answers(self._clip("a"))
        stale = FFmWiz.begin_plan(answers)
        FFmWiz.effective_settings(answers)["video_codec"] = "VP9"
        FFmWiz.require_plan_revision(answers)      # that plan was BUILT

        cmd = self._build(FFmWiz.build_hardsub_command, answers)
        self.assertEqual("libx264", self._encoder(cmd),
                         "the fresh H264 request must control the resolution")
        self.assertNotEqual(stale, FFmWiz.plan_revision(answers))
        self.assertEqual("H264", answers["video_codec"])
        answers["cmd"] = cmd
        self.assertEqual("h264", dict(self._codecs(self._execute(answers))).get("video"))

    # ---- D13 ----
    def test_the_join_builder_does_not_inherit_a_previous_plans_codec(self):
        primary, other = self._clip("a"), self._clip("b", size="320x180")
        items = [self._item(primary), self._item(other)]
        answers = self._answers(primary, video_codec="H264",
                                join_input_items=items[1:])
        FFmWiz.effective_settings(answers)["video_codec"] = "VP9"

        joined = self._tmp / "joined.mkv"
        cmd = self._build(FFmWiz.build_join_encode_command, answers, items, joined)
        self.assertEqual("libx264", self._encoder(cmd))
        self.assertIsNotNone(FFmWiz.plan_revision(answers),
                             "the builder has to OPEN a revision, not skip one")
        self.assertEqual("H264", answers["video_codec"])
        answers["cmd"] = cmd
        self.assertEqual("h264", dict(self._codecs(self._execute(answers))).get("video"))

    def test_a_second_direct_build_gets_its_own_revision(self):
        # Back, change the container, rebuild -- with nothing but the builder
        # in between. The webm fallback must not survive into the .mkv rebuild.
        answers = self._hardsub_answers(self._clip("a"), output_ext="webm")
        first_cmd = self._build(FFmWiz.build_hardsub_command, answers)
        first = FFmWiz.plan_revision(answers)
        self.assertEqual("libvpx-vp9", self._encoder(first_cmd))
        self.assertEqual("VP9", FFmWiz.effective_value(answers, "video_codec"))

        self._go_back(answers, output_ext="mkv")
        second_cmd = self._build(FFmWiz.build_hardsub_command, answers)
        self.assertNotEqual(first, FFmWiz.plan_revision(answers))
        self.assertEqual("libx264", self._encoder(second_cmd))
        answers["cmd"] = second_cmd
        self.assertEqual("h264", dict(self._codecs(self._execute(answers))).get("video"))

    def test_a_stage_copy_still_shares_the_lease_it_was_handed(self):
        # The boundary must not cost the reverse pipeline its cleanup: a stage
        # copy carries the OUTER lease object, and taking a fresh revision on
        # the copy may not replace it.
        primary, other = self._clip("a"), self._clip("b", size="320x180")
        items = [self._item(primary), self._item(other)]
        answers = self._answers(primary, video_codec="H264")
        lease = FFmWiz.artifact_lease(answers)
        stage = dict(answers)
        self._build(FFmWiz.build_join_encode_command, stage, items,
                    self._tmp / "staged.mkv")
        self.assertIs(lease, FFmWiz.artifact_lease(stage))
        self.assertIsNot(FFmWiz.effective_settings(stage),
                         FFmWiz.effective_settings(answers),
                         "a stage's resolutions belong to the stage, not the job")

    def test_no_public_builder_lets_a_stale_map_change_its_command(self):
        """The audit D13 asks for, as an assertion rather than a claim.

        Every builder below is called twice with identical requested answers --
        once from a clean dict, once from one carrying another plan's resolved
        codecs. A builder that reads the effective map without opening its own
        revision produces a different command for the second call.
        """
        primary = self._clip("a")
        cases = (
            ("main encode", FFmWiz.build_ffmpeg_command, dict(), ()),
            ("hardsub", FFmWiz.build_hardsub_command, "hardsub", ()),
            ("video speed/reverse", FFmWiz.build_video_speed_reverse_command,
             dict(reverse_video=True, speed_factor=1.0, include_audio=True), ()),
            ("audio speed/reverse", FFmWiz.build_audio_speed_reverse_command,
             dict(audio_index=0, reverse_audio=True, speed_factor=2.0,
                  audio_tool_output_ext="flac"), ()),
            ("audio cut", FFmWiz.build_audio_cut_command,
             dict(audio_index=0, audio_keep_ranges=[(0.0, 0.5)],
                  audio_tool_output_ext="flac"), ()),
            ("audio transform", FFmWiz.build_audio_transform_command,
             dict(audio_index=0, reverse_audio=True, audio_speed_enabled=True,
                  audio_speed_factor=1.5, audio_cut_keep_ranges=[(0.0, 0.5)],
                  audio_tool_output_ext="flac"), ()),
        )
        for label, builder, extra, args in cases:
            with self.subTest(builder=label):
                def make(stale):
                    answers = (self._hardsub_answers(primary) if extra == "hardsub"
                               else self._answers(primary, **extra))
                    if stale:
                        FFmWiz.effective_settings(answers)["video_codec"] = "VP9"
                        FFmWiz.effective_settings(answers)["audio_codec"] = "libopus"
                    return self._build(builder, answers, *args)
                self.assertEqual(make(False), make(True),
                                 f"{label} resolved into a map it does not own")

    def test_the_join_builder_is_covered_by_the_same_audit(self):
        # Separate only because it needs the join items the others do not.
        primary, other = self._clip("a"), self._clip("b", size="320x180")
        items = [self._item(primary), self._item(other)]

        def make(stale):
            answers = self._answers(primary, video_codec="H264",
                                    join_input_items=items[1:])
            if stale:
                FFmWiz.effective_settings(answers)["video_codec"] = "VP9"
                FFmWiz.effective_settings(answers)["audio_codec"] = "libopus"
            return self._build(FFmWiz.build_join_encode_command, answers, items,
                               self._tmp / "audit.mkv")
        self.assertEqual(make(False), make(True))


if __name__ == "__main__":
    unittest.main()
