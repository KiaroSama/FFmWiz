"""Characterizes `ffmwiz.modes_join.run_join_videos_mode` (main-menu mode 12,
Join), whose driver had no test anywhere under `tests/` before this file --
only its builders did (`build_join_copy_command` etc. are covered by 29 hits
across the suite; the DRIVER that decides which one to call, and with what,
had none).

That gap is not theoretical. This week two features shipped exactly this
shape of defect: a question asked, answered, and then dropped between the
prompt and the builder (`ffmwiz/wizard_b.py:222-227`; four dead config keys
in `ffmwiz/wizard_flow.py:126-129`). An argv test on the builder cannot see
either one, because the builder was never given the chance to receive the
wrong answers. Only a test that drives the real prompt sequence and asserts
the resulting hand-off can. Every test below asserts the ARGUMENTS the driver
hands its builder, or that it handed none -- never merely that the mode ran.

Mode 12 is also the only one of the three drivers in this plan whose Back
vocabulary is NOT the wizard's usual `0` (`FFmWiz.BACK_INPUT_TOKENS`):
`ffmwiz/modes_join.py:242-247` treats a bare `b`/`back` at the file prompt as
"remove the previous file and re-enter it", not as Back. Nothing here answers
`b` at that prompt for that reason.

Modes deliberately left without a driver suite
------------------------------------------------
Nine of the fifteen main-menu modes' drivers were untested before this plan.
This plan puts a suite under the three whose failure costs the user the most
(this file, `test_mode_driver_hardsub.py`, `test_mode_driver_track_manager.py`)
and records the reasoning for the other six here, so nobody re-audits them:

Not worth doing:
  - Mode 7, Media Info (`ffmwiz/modes_mediainfo.py:521`) -- produces reports
    into a dedicated reports directory and never touches or produces media. A
    wrong report is visible to the person who asked for it; a wrong encode is
    not.
  - Mode 14, Capability cache menu (`ffmwiz/modes.py:107`) -- diagnostics
    only: view, re-probe, clear. Its own docstring says it never affects user
    settings, logs, or secrets. Everything it clears is regenerated on the
    next probe.

Deferred, with reason:
  - Mode 5, Add files to video (`ffmwiz/modes.py:610`) -- stream-copy only and
    refuses anything it cannot copy (`ffmwiz/modes.py:632-638`), so the blast
    radius is a container it declined to write. Worth a suite after the three
    above.
  - Mode 6, Extract stream (`ffmwiz/modes_b.py:99`) -- writes new sidecar
    files and never modifies the source. Its driver is a 4-entry table on the
    SHARED `wizard_base.run_mode_steps`, so the loop itself is exercised by
    mode 10 and the audio tools; only the four steps are unique.
  - Mode 10, Video speed / reverse (`ffmwiz/modes_transform.py:99`) -- also on
    the shared `run_mode_steps`, and its reverse path is already covered at
    the pipeline level by `test_bounded_reverse_pipeline.py` and
    `test_stage_geometry_ownership.py`.
  - `run_audio_cut_mode` -- deleted; the unified audio editor superseded it.
    `FFmWiz.run_one_job` never dispatches to it; testing it would pin dead
    code. Whether to delete it or wire it to a menu entry is a decision for a
    separate plan, not a test.
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import FFmWiz
from ffmwiz import modes_join


class ModeDriverJoinTests(unittest.TestCase):
    """Drives the real `run_join_videos_mode`; stubs only the prompts, the
    probe (`services.join_load_media_item`) and the three builders. None of
    this needs ffmpeg/ffprobe on PATH."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._tmpdir = tempfile.TemporaryDirectory(prefix="ffmwiz_mode_driver_join_")
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    # ------------------------------------------------------------- fixtures
    def _make_file(self, name: str) -> Path:
        path = self.tmp / name
        path.write_bytes(b"")
        return path

    def _item(self, path: Path, *, video: bool = True, codec: str = "h264",
              width: int = 1920, height: int = 1080, fps: str = "30/1",
              pix_fmt: str = "yuv420p", audio_codec: str = "aac",
              sample_rate: int = 48000, channels: int = 2, duration: float = 10.0,
              audio_bitrate_kbps: int | None = None) -> dict:
        """A `services.join_load_media_item`-shaped fixture. `streams` is what
        `join_copy_compatibility` actually compares (codec/resolution/fps/
        pix_fmt/etc, NOT the derived `video_streams`/`audio_streams` lists),
        so it has to carry the same fields a real ffprobe stream would."""
        streams = []
        video_streams = []
        if video:
            vstream = {
                "codec_type": "video", "codec_name": codec, "width": width,
                "height": height, "avg_frame_rate": fps, "pix_fmt": pix_fmt,
            }
            streams.append(vstream)
            video_streams.append(vstream)
        astream = {
            "codec_type": "audio", "codec_name": audio_codec,
            "sample_rate": sample_rate, "channels": channels,
            "channel_layout": "stereo",
        }
        if audio_bitrate_kbps:
            astream["bit_rate"] = str(audio_bitrate_kbps * 1000)
        streams.append(astream)
        fmt = {"duration": str(duration)}
        return {
            "path": path, "probe": {"streams": streams, "format": fmt}, "format": fmt,
            "streams": streams, "video_streams": video_streams, "audio_streams": [astream],
            "subtitle_streams": [], "data_streams": [], "duration": duration,
        }

    # --------------------------------------------------------------- driver
    def _drive(self, items_by_path: dict, *, replies, ask_yes_no=True, ask_yes_no_seq=None):
        """Run the real join driver; stub only prompts/probe/builders. Every
        text prompt is a finite iterator (never `return_value=`) because the
        file prompt re-asks on a missing/duplicate path -- a constant answer
        would spin forever (see the plan's warning 1)."""
        seen = {
            "copy_calls": [], "near_calls": [], "audio_calls": [],
            "summary_calls": [], "ffmpeg_calls": [], "errors": [],
        }
        reply_iter = iter(replies)

        def fake_load(answers, path, allow_audio_only=True):
            return items_by_path[str(path)]

        def fake_output_location(answers):
            answers["output_location"] = self.tmp

        def spy_copy(answers, items, output_path):
            seen["copy_calls"].append((answers, list(items), output_path))
            return ["ffmpeg", "-copy-cmd", str(output_path)]

        def spy_near(answers, items, output_path):
            seen["near_calls"].append((answers, list(items), output_path))
            return ["ffmpeg", "-near-cmd", str(output_path)]

        def spy_audio(answers, items, output_path):
            seen["audio_calls"].append((answers, list(items), output_path))
            return ["ffmpeg", "-audio-cmd", str(output_path)]

        def spy_summary(*args, **kwargs):
            seen["summary_calls"].append((args, kwargs))

        def fake_run_ffmpeg(cmd, **kwargs):
            seen["ffmpeg_calls"].append((cmd, kwargs))
            return (0, 0.0)

        def fake_error(message):
            seen["errors"].append(message)

        if ask_yes_no_seq is not None:
            yn_iter = iter(ask_yes_no_seq)
            ask_yes_no_fn = lambda *a, **k: next(yn_iter)
        else:
            ask_yes_no_fn = lambda *a, **k: ask_yes_no

        buffer = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(modes_join, "build_join_copy_command", spy_copy))
            stack.enter_context(mock.patch.object(modes_join, "build_join_near_quality_command", spy_near))
            stack.enter_context(mock.patch.object(modes_join, "build_join_audio_encode_command", spy_audio))
            stack.enter_context(mock.patch.object(modes_join, "print_join_summary", spy_summary))
            stack.enter_context(mock.patch.object(modes_join.services, "join_load_media_item", fake_load))
            # Avoids a real ffprobe subprocess: even a fixture stream with a
            # direct bit_rate still has get_packet_sizes(...) EVALUATED (it is
            # an eager call argument to stream_bitrate_kbps), and this repo's
            # fixture streams do not carry the "fast size metadata" tags that
            # would make packet_size_probe_needed() return False on its own.
            stack.enter_context(mock.patch.object(modes_join.services, "get_packet_sizes", lambda a: {}))
            stack.enter_context(mock.patch.object(modes_join.wizard, "ask_join_add_another", lambda prompt: False))
            stack.enter_context(mock.patch.object(modes_join.wizard, "step_output_location", fake_output_location))
            stack.enter_context(mock.patch.object(modes_join, "run_ffmpeg_with_progress", fake_run_ffmpeg))
            stack.enter_context(mock.patch.object(FFmWiz.appio, "ask_raw", lambda *a, **k: next(reply_iter)))
            stack.enter_context(mock.patch.object(FFmWiz.appio, "ask_yes_no", ask_yes_no_fn))
            stack.enter_context(mock.patch.object(FFmWiz.appio, "error", fake_error))
            stack.enter_context(contextlib.redirect_stdout(buffer))
            seen["result"] = modes_join.run_join_videos_mode({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})
        seen["output"] = buffer.getvalue()
        return seen

    # ---------------------------------------------------------------- tests
    def test_two_copy_compatible_inputs_use_the_copy_builder(self):
        one = self._make_file("one.mkv")
        two = self._make_file("two.mkv")
        items = {str(one): self._item(one), str(two): self._item(two)}
        seen = self._drive(items, replies=[str(one), str(two)])
        self.assertEqual(1, len(seen["copy_calls"]), seen["output"][-800:])
        _, called_items, _ = seen["copy_calls"][0]
        self.assertEqual([one, two], [it["path"] for it in called_items])
        self.assertEqual([], seen["near_calls"])
        self.assertEqual([], seen["audio_calls"])

    def test_audio_only_inputs_use_the_audio_encode_builder_and_default_the_bitrate(self):
        # Different sample rates so the pair is NOT copy-compatible either --
        # otherwise the driver prefers stream copy over re-encoding even for
        # audio-only inputs, and the audio-encode builder is never reached.
        one = self._make_file("one.mp3")
        two = self._make_file("two.mp3")
        items = {
            str(one): self._item(one, video=False, sample_rate=44100, audio_bitrate_kbps=128),
            str(two): self._item(two, video=False, sample_rate=48000, audio_bitrate_kbps=192),
        }
        seen = self._drive(items, replies=[str(one), str(two)])
        self.assertEqual(1, len(seen["audio_calls"]), seen["output"][-800:])
        answers, called_items, _ = seen["audio_calls"][0]
        self.assertEqual([one, two], [it["path"] for it in called_items])
        self.assertEqual(192, answers["audio_bitrate_kbps"], "must default to the HIGHEST source bitrate")
        self.assertEqual([], seen["copy_calls"])
        self.assertEqual([], seen["near_calls"])

    def test_mixing_audio_only_and_video_inputs_is_refused_before_any_builder(self):
        vid = self._make_file("vid.mkv")
        aud = self._make_file("aud.mp3")
        items = {str(vid): self._item(vid), str(aud): self._item(aud, video=False)}
        seen = self._drive(items, replies=[str(vid), str(aud)])
        # The assertion that matters is "no builder ran" -- a refusal that
        # still builds a command is the defect this pins.
        self.assertIsNone(seen["result"])
        self.assertEqual([], seen["copy_calls"])
        self.assertEqual([], seen["near_calls"])
        self.assertEqual([], seen["audio_calls"])
        self.assertTrue(any("Cannot mix audio-only and video" in msg for msg in seen["errors"]), seen["errors"])

    def test_declining_the_near_quality_question_runs_nothing(self):
        # Different codecs so the pair is not copy-compatible; both have video
        # so it is not an audio-only join either -- the only way to reach the
        # "Encode with closest possible quality?" question.
        one = self._make_file("one.mkv")
        two = self._make_file("two.mkv")
        items = {
            str(one): self._item(one, codec="h264"),
            str(two): self._item(two, codec="hevc"),
        }
        seen = self._drive(items, replies=[str(one), str(two)], ask_yes_no=False)
        self.assertIsNone(seen["result"])
        self.assertEqual([], seen["copy_calls"])
        self.assertEqual([], seen["near_calls"])
        self.assertEqual([], seen["audio_calls"])
        self.assertEqual([], seen["ffmpeg_calls"])

    def test_declining_start_now_preserves_the_generated_inputs(self):
        one = self._make_file("one.mkv")
        two = self._make_file("two.mkv")
        items = {str(one): self._item(one), str(two): self._item(two)}
        preserve_calls = []
        cleanup_calls = []
        with mock.patch.object(modes_join, "preserve_artifacts_for_manual_run",
                                lambda answers: preserve_calls.append(answers) or []), \
             mock.patch.object(modes_join, "cleanup_join_concat_list",
                                lambda answers: cleanup_calls.append(answers)):
            seen = self._drive(items, replies=[str(one), str(two)], ask_yes_no=False)
        self.assertIsNone(seen["result"])
        # Declining calls preserve_artifacts_for_manual_run, NOT
        # cleanup_join_concat_list -- the printed command still references the
        # generated inputs, so cleaning them up here would make that promise
        # false (ffmwiz/modes_join.py:390-395).
        self.assertEqual(1, len(preserve_calls))
        self.assertEqual([], cleanup_calls)
        self.assertEqual([], seen["ffmpeg_calls"])

    def test_the_summary_is_given_the_arguments_this_mode_computes(self):
        # Characterization: Mode 12 calls print_join_summary with THREE
        # positional arguments; the wizard's join path
        # (ffmwiz/wizard_b.py:202-204) computes a join_copy_plan and passes it
        # as a fourth. This records the current call shape so a future change
        # to either path is deliberate -- it does not "fix" the difference.
        one = self._make_file("one.mkv")
        two = self._make_file("two.mkv")
        items = {str(one): self._item(one), str(two): self._item(two)}
        seen = self._drive(items, replies=[str(one), str(two)])
        self.assertEqual(1, len(seen["summary_calls"]))
        args, kwargs = seen["summary_calls"][0]
        self.assertEqual(3, len(args), "Mode 12 must call print_join_summary(items, copy_compatible, reasons) "
                                        "with no fourth `plan` argument")
        self.assertEqual({}, kwargs)


if __name__ == "__main__":
    unittest.main()
