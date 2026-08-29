"""R02: audio availability must follow the whole join, not input 1.

The Join BUILDER already synthesises silence and recovers track 0 when input 1
is silent. What still read input 1 alone was everything that decides whether
audio is OFFERED: the wizard's speed-sync / codec / rate / LoudNorm gates, the
request the editors are launched with, the classic editor's segment model, and
both editors' reverse preview.

Measured before the fix, with a 2 s silent input 1, a 2 s audible input 2 and
video speed 2x: the command carried no `atempo`, the video ended at 2.080 s and
the audio ran on to 4.040 s. After the fix the same edit ends at 2.080 s and
1.997 s. RealMixedTopology below re-measures exactly that.
"""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import FFmWiz
# `run_wizard` reaches these prompts through their DEFINING module now that the
# wizard facade is no longer imported by its own leaves, so a patch on the
# facade would rebind an attribute nothing reads.
from ffmwiz import wizard_base, wizard_steps  # noqa: E402

from join_test_helpers import make_item

_ROOT = Path(__file__).resolve().parents[1]
_GUI_DIR = _ROOT / "ffmwiz" / "gui"
for _p in (str(_ROOT), str(_GUI_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ffmwiz_gui_qml as Q  # noqa: E402
import gui_editor_unified as U  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")

SILENCE_FLOOR_DB = -60.0
_QML_PATH = _GUI_DIR / "qml" / "UnifiedEditor.qml"


def _answers(items, **extra):
    first = items[0]
    answers = {
        "ffmpeg": FFMPEG or "ffmpeg", "ffprobe": FFPROBE or "ffprobe",
        "input_path": first["path"], "output_location": Path("."),
        "video_streams": first["video_streams"], "audio_streams": first["audio_streams"],
        "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
        "streams": first["streams"], "format": first["format"],
        "output_ext": "mkv", "video_codec": "H264", "use_gpu": False,
        "audio_codec": "aac", "audio_bitrate_kbps": 128,
        "subtitle_tracks": [], "resolution": "n", "fps": 25,
        "video_bitrate_kbps": 400, "color_range_choice": "tv",
        "join_input_items": items[1:],
    }
    answers.update(extra)
    return answers


def _silent_first():
    return [make_item("a.mkv", 2.0, with_audio=False), make_item("b.mkv", 2.0)]


def _audible_first():
    return [make_item("a.mkv", 2.0), make_item("b.mkv", 2.0, with_audio=False)]


class Availability(unittest.TestCase):
    """any_join_audio is the single truth every audio feature gate now uses."""

    def test_a_later_audible_input_counts_as_audio(self):
        answers = _answers(_silent_first())
        self.assertEqual(FFmWiz.join_audio_segment_flags(answers), [False, True])
        self.assertTrue(FFmWiz.any_join_audio(answers))

    def test_an_entirely_silent_join_has_no_audio(self):
        items = [make_item("a.mkv", 2.0, with_audio=False),
                 make_item("b.mkv", 2.0, with_audio=False)]
        self.assertFalse(FFmWiz.any_join_audio(_answers(items)))

    def test_a_single_silent_input_has_no_audio(self):
        self.assertFalse(FFmWiz.any_join_audio({"audio_streams": []}))

    def test_recovery_names_track_0_of_the_first_audible_input(self):
        streams, tracks = FFmWiz.join_audio_recovery(_answers(_silent_first()))
        self.assertEqual(tracks, [0])
        self.assertEqual(streams, [_silent_first()[1]["audio_streams"][0]])

    def test_no_recovery_when_input_1_already_has_audio(self):
        self.assertEqual(FFmWiz.join_audio_recovery(_answers(_audible_first())), ([], []))


class JoinAudioView(unittest.TestCase):
    """The view lends the recovered track to a step and takes it back."""

    def test_the_step_sees_the_joined_audio(self):
        seen = {}

        def step(answers):
            seen["streams"] = list(answers["audio_streams"])
            seen["selected"] = FFmWiz.selected_audio_streams(answers)

        answers = _answers(_silent_first())
        FFmWiz.with_join_audio_view(step)(answers)
        self.assertEqual(len(seen["streams"]), 1)
        self.assertEqual(seen["selected"], [0])

    def test_input_1_never_keeps_a_stream_it_does_not_have(self):
        answers = _answers(_silent_first())
        FFmWiz.with_join_audio_view(lambda a: None)(answers)
        self.assertEqual(answers["audio_streams"], [])
        self.assertNotIn("audio_tracks", answers)

    def test_the_lease_is_returned_even_when_the_step_goes_back(self):
        def step(answers):
            raise FFmWiz.Back()

        answers = _answers(_silent_first())
        with self.assertRaises(FFmWiz.Back):
            FFmWiz.with_join_audio_view(step)(answers)
        self.assertEqual(answers["audio_streams"], [])


class WizardGates(unittest.TestCase):
    """The wizard's own Step predicates, captured from run_wizard."""

    class _Stop(Exception):
        pass

    def _steps(self):
        recorded = []
        real_step = FFmWiz.wizard.Step
        real_input = FFmWiz.wizard.step_input_path

        def recorder(name, applicable, run):
            step = real_step(name, applicable, run)
            recorded.append(step)
            return step

        def stop(_answers):
            raise WizardGates._Stop()

        wizard_base.Step = recorder
        wizard_steps.step_input_path = stop
        try:
            with self.assertRaises(WizardGates._Stop):
                FFmWiz.run_wizard({})
        finally:
            wizard_base.Step = real_step
            wizard_steps.step_input_path = real_input
        return {step.name: step for step in recorded}

    def test_audio_config_steps_are_offered_when_only_a_later_input_is_audible(self):
        steps = self._steps()
        answers = _answers(_silent_first(), audio_codec="aac")
        for name in ("loudnorm", "audio_codec", "audio_sample_rate"):
            self.assertTrue(steps[name].applicable(answers),
                            f"{name} must stay available when a later input has audio")

    def test_audio_config_steps_stay_hidden_for_an_entirely_silent_join(self):
        steps = self._steps()
        items = [make_item("a.mkv", 2.0, with_audio=False),
                 make_item("b.mkv", 2.0, with_audio=False)]
        answers = _answers(items, audio_codec="aac")
        for name in ("loudnorm", "audio_codec", "audio_sample_rate", "audio_bitrate"):
            self.assertFalse(steps[name].applicable(answers))

    def test_loudnorm_can_actually_be_switched_on_for_a_silent_first_input(self):
        steps = self._steps()
        answers = _answers(_silent_first())
        asked = []
        # "2" = single-pass, "" = keep the default target. A finite script on
        # purpose: an unexpected extra question fails the test instead of
        # spinning forever in the prompt's own retry loop.
        replies = iter(["2", ""])
        real_ask, real_yn = FFmWiz.appio.ask_raw, FFmWiz.appio.ask_yes_no

        def ask(prompt="", **_kwargs):
            asked.append(prompt)
            try:
                return next(replies)
            except StopIteration:
                raise AssertionError(f"unexpected extra LoudNorm question: {prompt}")

        FFmWiz.appio.ask_raw = ask
        FFmWiz.appio.ask_yes_no = lambda *_a, **_kw: False   # do not measure
        try:
            steps["loudnorm"].run(answers)
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.ask_yes_no = real_ask, real_yn
        self.assertTrue(asked, "LoudNorm must reach its prompt, not return silently")
        self.assertTrue(answers.get("loudnorm_enabled"))
        self.assertEqual(answers.get("loudnorm_mode"), "single")
        # ...and without leaving input 1 claiming a stream it does not have.
        self.assertEqual(answers["audio_streams"], [])


class SpeedSyncPrompt(unittest.TestCase):
    """The question whose absence cost the joined audio its retiming."""

    def _run_speed_step(self, answers):
        replies = iter(["y", "200%"])
        real_ask, real_yn = FFmWiz.appio.ask_raw, FFmWiz.appio.ask_yes_no

        def ask(*_args, **_kwargs):
            try:
                return next(replies)
            except StopIteration:
                raise AssertionError("the speed prompt asked more than expected")

        FFmWiz.appio.ask_raw = ask
        # Reverse -> default (False); sync -> default (True).
        FFmWiz.appio.ask_yes_no = lambda _prompt, default=False: default
        try:
            FFmWiz.wizard.step_video_speed_reverse_for_encode(answers)
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.ask_yes_no = real_ask, real_yn

    def test_a_silent_input_1_still_gets_the_sync_question(self):
        answers = _answers(_silent_first())
        self._run_speed_step(answers)
        self.assertTrue(answers.get("video_speed_enabled"))
        self.assertTrue(answers.get("audio_speed_from_video"),
                        "the joined audio must be offered the video's speed")
        self.assertTrue(FFmWiz.audio_speed_transform_enabled(answers))

    def test_an_entirely_silent_join_is_not_asked(self):
        items = [make_item("a.mkv", 2.0, with_audio=False),
                 make_item("b.mkv", 2.0, with_audio=False)]
        answers = _answers(items)
        self._run_speed_step(answers)
        self.assertNotIn("audio_speed_from_video", answers)


class EditorRequest(unittest.TestCase):
    """What guibridge hands the editors decides which controls they can offer."""

    def _request(self, items):
        captured = {}

        def fake_launch(request):
            captured.update(request)
            return {"status": "canceled"}

        # The DEFINING module. guibridge_b calls this name directly now, so a
        # patch on the facade would rebind an attribute nothing reads.
        from ffmwiz import guibridge_b
        real = guibridge_b._launch_qt_gui
        guibridge_b._launch_qt_gui = fake_launch
        try:
            FFmWiz.guibridge.open_unified_video_gui(_answers(items, probe={}))
        finally:
            guibridge_b._launch_qt_gui = real
        return captured

    def test_a_silent_input_1_still_advertises_audio(self):
        request = self._request(_silent_first())
        self.assertTrue(request["has_audio"])
        self.assertTrue(request["initial_include_audio"])
        self.assertEqual([s["has_audio"] for s in request["join_segments"]], [False, True])

    def test_an_entirely_silent_join_advertises_none(self):
        items = [make_item("a.mkv", 2.0, with_audio=False),
                 make_item("b.mkv", 2.0, with_audio=False)]
        request = self._request(items)
        self.assertFalse(request["has_audio"])
        self.assertFalse(request["initial_include_audio"])


class ClassicEditorModel(unittest.TestCase):
    """The classic editor used to drop has_audio while copying segments."""

    def _req(self, flags):
        return {
            "input_path": "a.mkv",
            "has_audio": flags[0],
            "join_segments": [
                {"path": f"v{i}.mkv", "duration": 2.0, "has_audio": flag}
                for i, flag in enumerate(flags)
            ],
        }

    def test_each_segment_keeps_its_own_audio_flag(self):
        segments = U.build_join_segment_model(self._req([True, False]))
        self.assertEqual([s["has_audio"] for s in segments], [True, False])
        self.assertEqual([s["start"] for s in segments], [0.0, 2.0])

    def test_availability_covers_the_whole_join(self):
        self.assertTrue(U.request_has_any_audio(self._req([False, True])))
        self.assertFalse(U.request_has_any_audio(self._req([False, False])))

    def test_reverse_preview_follows_the_active_segment(self):
        req = self._req([False, True])
        segments = U.build_join_segment_model(req)
        self.assertFalse(U.active_segment_has_audio(req, segments, 0))
        self.assertTrue(U.active_segment_has_audio(req, segments, 1))

    def test_a_single_input_reverse_preview_uses_the_request_flag(self):
        self.assertTrue(U.active_segment_has_audio({"has_audio": True}, [], 0))
        self.assertFalse(U.active_segment_has_audio({"has_audio": False}, [], 0))

    def test_the_reverse_proxy_call_site_uses_the_active_segment(self):
        # The helper above is only worth anything if _render_reverse_proxy calls
        # it; a short pattern would match elsewhere, so anchor on the assignment.
        source = (_GUI_DIR / "gui_editor_unified.py").read_text(encoding="utf-8")
        self.assertIn("want_audio = active_segment_has_audio(", source)
        self.assertNotIn('want_audio = bool(self.request.get("has_audio"))', source)

    def test_a_silent_segment_gets_synthesised_silence_not_a_missing_track(self):
        req = self._req([True, False])
        args = " ".join(U.build_classic_waveform_args(
            req, U.build_join_segment_model(req), "out.pcm"))
        self.assertIn("anullsrc", args)
        self.assertIn("concat=n=2:v=0:a=1", args)


class QmlEditorModel(unittest.TestCase):
    """Run the editor's own JS: segs must carry each segment's audio flag."""

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtCore import QCoreApplication
            from PySide6.QtQml import QJSEngine  # noqa: F401
        except Exception as exc:  # pragma: no cover - CI has no PySide6
            raise unittest.SkipTest(f"PySide6 QtQml required: {exc}")
        cls._app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
        src = _QML_PATH.read_text(encoding="utf-8")
        start = src.index("        hasAudio = !!req.has_audio")
        cls._init_js = src[start:src.index("        segs = list") + len("        segs = list")]
        rev = src.index("    function renderReverseChunk(")
        cls._reverse_js = src[rev:src.index("    function advanceReverse(")]

    def _engine(self, req):
        from PySide6.QtQml import QJSEngine
        engine = QJSEngine()
        prelude = ("var req = %s; var hasAudio = false; var totalDuration = 0; var segs = [];"
                   % json.dumps(req)) + chr(10)
        result = engine.evaluate(prelude + self._init_js)
        self.assertFalse(result.isError(), result.toString())
        return engine

    def _value(self, engine, expr):
        result = engine.evaluate(expr)
        self.assertFalse(result.isError(), f"{expr} -> {result.toString()}")
        return json.loads(result.toString())

    def _req(self, flags):
        return {
            "input_path": "a.mkv", "has_audio": flags[0], "duration": 4.0,
            "join_segments": [
                {"path": f"v{i}.mkv", "name": f"Video {i + 1}", "duration": 2.0, "has_audio": flag}
                for i, flag in enumerate(flags)
            ],
        }

    def test_the_switch_is_enabled_when_a_later_segment_is_audible(self):
        engine = self._engine(self._req([False, True]))
        self.assertTrue(self._value(engine, "JSON.stringify(hasAudio)"))

    def test_the_switch_stays_disabled_for_an_entirely_silent_join(self):
        engine = self._engine(self._req([False, False]))
        self.assertFalse(self._value(engine, "JSON.stringify(hasAudio)"))

    def test_each_segment_keeps_its_own_flag(self):
        engine = self._engine(self._req([False, True]))
        self.assertEqual(
            self._value(engine, "JSON.stringify([segs[0].hasAudio, segs[1].hasAudio])"),
            [False, True])

    def test_a_single_input_segment_takes_the_request_flag(self):
        engine = self._engine({"input_path": "a.mkv", "has_audio": True, "duration": 4.0,
                               "join_segments": []})
        self.assertTrue(self._value(engine, "JSON.stringify(segs[0].hasAudio)"))

    def _reverse_spec(self, flags, win_start, win_end):
        engine = self._engine(self._req(flags))
        stubs = """
        var revGen = 0, revWinStart = 0, revWinEnd = 0, captured = "";
        var previewArea = { width: 854 };
        var bridge = { renderReverse: function (s) { captured = s } };
        function segmentForTime(t) {
            for (var i = segs.length - 1; i >= 0; --i)
                if (t >= segs[i].start) return { index: i };
            return { index: 0 }
        }
        """
        result = engine.evaluate(stubs + self._reverse_js)
        self.assertFalse(result.isError(), result.toString())
        result = engine.evaluate("renderReverseChunk(%r, %r); captured" % (win_start, win_end))
        self.assertFalse(result.isError(), result.toString())
        return json.loads(result.toString())

    def test_reverse_of_an_audible_later_segment_asks_for_audio(self):
        spec = self._reverse_spec([False, True], 2.5, 3.5)
        self.assertEqual(spec["src"], "v1.mkv")
        self.assertTrue(spec["has_audio"])

    def test_reverse_of_a_silent_segment_does_not(self):
        spec = self._reverse_spec([False, True], 0.5, 1.5)
        self.assertEqual(spec["src"], "v0.mkv")
        self.assertFalse(spec["has_audio"])


class QmlReverseProxyAudio(unittest.TestCase):
    def test_the_segment_flag_wins_over_the_job_flag(self):
        self.assertTrue(Q.reverse_proxy_wants_audio({"has_audio": False}, {"has_audio": True}))
        self.assertFalse(Q.reverse_proxy_wants_audio({"has_audio": True}, {"has_audio": False}))

    def test_a_spec_without_a_flag_falls_back_to_the_request(self):
        self.assertTrue(Q.reverse_proxy_wants_audio({"has_audio": True}, {}))

    def test_the_proxy_call_site_asks_the_helper(self):
        # _do_reverse lives inside main() behind a Qt import, so the helper can
        # only guard the proxy if the proxy actually calls it.
        source = (_GUI_DIR / "ffmwiz_gui_qml.py").read_text(encoding="utf-8")
        self.assertIn("if reverse_proxy_wants_audio(self._req, spec):", source)
        self.assertNotIn('if self._req.get("has_audio"):', source)


@requires_ffmpeg
class RealMixedTopology(unittest.TestCase):
    """Encode the auditor's exact permutations and measure the result."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_joinavail_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _clip(self, name, duration, with_audio):
        path = self._tmp / f"{name}.mkv"
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"color=c=red:s=160x120:d={duration}:r=25"]
        if with_audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
                    "-map", "0:v", "-map", "1:a", "-c:a", "aac"]
        else:
            cmd += ["-map", "0:v"]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-vf", "format=yuv420p", str(path)]
        subprocess.run(cmd, check=True, timeout=180)
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60).stdout)
        streams = probe["streams"]
        return {"path": path, "streams": streams, "format": probe["format"],
                "duration": float(duration),
                "video_streams": [s for s in streams if s["codec_type"] == "video"],
                "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
                "subtitle_streams": [], "attachment_streams": [], "data_streams": []}

    def _stream_end(self, path, spec):
        """Last presented timestamp of one stream.

        Matroska writes no per-stream duration, so the packet timeline is the
        only honest measurement of "where does this stream actually stop".
        """
        packets = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", spec, "-show_packets", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120).stdout)["packets"]
        self.assertTrue(packets, f"{spec} produced no packets in {path.name}")
        return max(float(p.get("pts_time") or 0.0) + float(p.get("duration_time") or 0.0)
                   for p in packets)

    def _mean_db(self, path, start, length):
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
             "-i", str(path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180)
        for line in result.stderr.splitlines():
            if "mean_volume:" in line:
                return float(line.split("mean_volume:")[1].strip().split()[0])
        self.fail(f"volumedetect reported no mean_volume for {path}")

    def _join(self, specs, name, **extra):
        items = [self._clip(f"{name}{i}", d, a) for i, (d, a) in enumerate(specs)]
        answers = _answers(items, **extra)
        answers["output_location"] = self._tmp
        cmd = [str(part) for part in FFmWiz.build_join_encode_command(
            answers, items, self._tmp / f"{name}.mkv")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        self.assertEqual(result.returncode, 0, result.stderr[-600:])
        return Path(answers["output_path"]), cmd

    def test_silent_first_at_2x_retimes_the_joined_audio_too(self):
        # Before: no atempo, video 2.080 s, audio 4.040 s.
        output, cmd = self._join(
            [(2, False), (2, True)], "speed",
            video_speed_enabled=True, video_speed_factor=2.0, audio_speed_from_video=True)
        self.assertIn("atempo", " ".join(cmd))
        video_end = self._stream_end(output, "v:0")
        audio_end = self._stream_end(output, "a:0")
        self.assertLess(abs(video_end - audio_end), 0.15,
                        f"video ends at {video_end:.3f}s but audio at {audio_end:.3f}s")
        self.assertLess(audio_end, 2.5, "the joined audio must be halved with the video")

    def test_silent_first_reversed_keeps_both_clips_and_the_audio(self):
        output, cmd = self._join(
            [(2, False), (2, True)], "rev",
            video_speed_enabled=True, video_speed_factor=1.0,
            reverse_video=True, audio_speed_from_video=True)
        self.assertIn("areverse", " ".join(cmd),
                      "the joined audio must be reversed with the video")
        video_end = self._stream_end(output, "v:0")
        self.assertGreater(video_end, 3.5, "both clips must survive the reverse")
        streams = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(output)],
            capture_output=True, text=True, timeout=120).stdout)["streams"]
        self.assertTrue([s for s in streams if s["codec_type"] == "audio"],
                        "the joined audio must survive the reverse")
        # Whole-file measurement on purpose. `concat` -> `areverse` ->
        # `asetpts=PTS-STARTPTS` writes the samples correctly but crushes their
        # container timestamps into ~0.2 s, so a windowed -ss measurement finds
        # nothing. That is a separate, pre-existing defect of
        # build_encode_audio_processing_filter (it reproduces for an AUDIBLE
        # input 1 too) and is not what this test is proving.
        self.assertGreater(self._mean_db(output, 0.0, 10.0), SILENCE_FLOOR_DB)

    def test_the_summary_still_shows_the_audio_settings(self):
        items = [self._clip("sum0", 2, False), self._clip("sum1", 2, True)]
        answers = _answers(items)
        answers["output_location"] = self._tmp
        cmd = FFmWiz.build_join_encode_command(answers, items, self._tmp / "sum.mkv")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            FFmWiz.print_summary(answers, cmd)
        summary = buffer.getvalue()
        self.assertIn("audio codec", summary,
                      "a join with audio must show its audio settings")
        self.assertIn("audio sample rate", summary)
        self.assertIn("input 1 is silent", summary)

    def test_a_silent_later_segment_still_decodes_a_waveform(self):
        first = self._clip("wavea", 2, True)
        second = self._clip("waveb", 2, False)
        req = {
            "input_path": str(first["path"]), "has_audio": True, "ffmpeg": FFMPEG,
            "join_segments": [
                {"path": str(first["path"]), "duration": 2.0, "has_audio": True},
                {"path": str(second["path"]), "duration": 2.0, "has_audio": False},
            ],
        }
        out = self._tmp / "waveform.pcm"
        # Classic engine.
        classic = subprocess.run(
            [FFMPEG] + U.build_classic_waveform_args(req, U.build_join_segment_model(req), out),
            capture_output=True, text=True, timeout=300)
        self.assertEqual(classic.returncode, 0, classic.stderr[-600:])
        self.assertGreater(out.stat().st_size, 0, "the classic waveform decoded nothing")
        # QML engine, same topology.
        qml_out = self._tmp / "waveform_qml.pcm"
        qml = subprocess.run(
            [FFMPEG] + Q.build_wave_decode_args(req, str(qml_out)),
            capture_output=True, text=True, timeout=300)
        self.assertEqual(qml.returncode, 0, qml.stderr[-600:])
        self.assertGreater(qml_out.stat().st_size, 0, "the QML waveform decoded nothing")

    def test_a_silent_first_segment_still_decodes_a_waveform(self):
        first = self._clip("wave2a", 2, False)
        second = self._clip("wave2b", 2, True)
        req = {
            "input_path": str(first["path"]), "has_audio": False, "ffmpeg": FFMPEG,
            "join_segments": [
                {"path": str(first["path"]), "duration": 2.0, "has_audio": False},
                {"path": str(second["path"]), "duration": 2.0, "has_audio": True},
            ],
        }
        out = self._tmp / "waveform2.pcm"
        classic = subprocess.run(
            [FFMPEG] + U.build_classic_waveform_args(req, U.build_join_segment_model(req), out),
            capture_output=True, text=True, timeout=300)
        self.assertEqual(classic.returncode, 0, classic.stderr[-600:])
        self.assertGreater(out.stat().st_size, 0)
        self.assertTrue(U.request_has_any_audio(req),
                        "the editor must still offer the waveform for this join")


if __name__ == "__main__":
    unittest.main()
