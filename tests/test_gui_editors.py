"""Editor-contract tests for the QML and classic unified video editors.

These cover the pure, Qt-free parts of the two engines: the stdlib PCM
waveform floor (numpy is only an accelerator), the join waveform decode
graph, chapter normalisation on the wire, the reverse-preview window mapping,
and the cut-everything reply contract.
"""
import array
import importlib
import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_GUI_DIR = _ROOT / "ffmwiz" / "gui"
for _p in (str(_ROOT), str(_GUI_DIR),
           str(_GUI_DIR / "classic"), str(_GUI_DIR / "modern")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _classic_unified_source() -> str:
    """The classic unified editor's whole source set.

    It spans several modules (builder plus extracted canvas/timeline widgets),
    so glob them: an assertion pinned to one filename silently stops covering
    code the moment it moves.
    """
    return "\n".join(path.read_text(encoding="utf-8")
                     for path in sorted((_GUI_DIR / "classic").glob("gui_editor_unified*.py")))

import gui_geometry  # noqa: E402
import ffmwiz_gui_qml as Q  # noqa: E402


class _BlockImport:
    """meta_path finder that makes `import <name>` fail, to prove the stdlib
    fallback paths actually work (CI has no numpy)."""

    def __init__(self, name):
        self.name = name

    def find_module(self, fullname, path=None):  # pragma: no cover - legacy API
        return None

    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.name or fullname.startswith(self.name + "."):
            raise ImportError(f"{self.name} blocked for test")
        return None


@contextmanager
def numpy_blocked():
    finder = _BlockImport("numpy")
    saved = {k: v for k, v in sys.modules.items() if k == "numpy" or k.startswith("numpy.")}
    for k in saved:
        del sys.modules[k]
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        sys.modules.update(saved)


def _pcm_bytes(samples):
    return array.array("h", samples).tobytes()


class StdlibPcmHelperTests(unittest.TestCase):
    """D09: the classic editor's audioop fallback is gone in Python 3.13, so the
    peak/minmax floor must be pure stdlib."""

    def test_pcm_minmax_matches_samples(self):
        data = _pcm_bytes([0, 1000, -32768, 32767, -5])
        self.assertEqual(gui_geometry.pcm_minmax(data), (-32768, 32767))

    def test_pcm_peak_is_absolute_and_survives_int16_min(self):
        # -(-32768) must not wrap: audioop.max returned 32768 here too.
        self.assertEqual(gui_geometry.pcm_peak(_pcm_bytes([-32768, 10])), 32768)
        self.assertEqual(gui_geometry.pcm_peak(_pcm_bytes([300, -100])), 300)

    def test_empty_and_odd_length_input_is_safe(self):
        self.assertEqual(gui_geometry.pcm_minmax(b""), (0, 0))
        self.assertEqual(gui_geometry.pcm_peak(b""), 0)
        self.assertEqual(gui_geometry.pcm_minmax(_pcm_bytes([700]) + b"\x01"), (700, 700))

    def test_no_audioop_import_remains_in_the_classic_editor(self):
        src = _classic_unified_source()
        self.assertNotIn("import audioop", src)


class WaveformWithoutNumpyTests(unittest.TestCase):
    """D08: the QML waveform must still decode and render without numpy."""

    RATE = Q.WAVE_RATE

    def test_decode_pcm_samples_without_numpy(self):
        data = _pcm_bytes([0, 5000, -5000] * 100)
        with numpy_blocked():
            pcm = Q.decode_pcm_samples(data)
        self.assertIsNotNone(pcm)
        self.assertEqual(len(pcm), 300)

    def test_waveform_window_without_numpy_returns_real_pairs(self):
        n = self.RATE * 4
        samples = [int(16384 * (1 if (i // 200) % 2 else -1)) for i in range(n)]
        data = _pcm_bytes(samples)
        with numpy_blocked():
            pcm = Q.decode_pcm_samples(data)
            emn, emx = Q.build_wave_envelope(pcm)
            pairs = Q.waveform_window(pcm, emn, emx, self.RATE, 0.0, 4.0, 200)
        self.assertGreater(len(pairs), 1)
        for mn, mx in pairs:
            self.assertLessEqual(mn, mx)
            self.assertGreaterEqual(mn, -1.0 - 1e-6)
            self.assertLessEqual(mx, 1.0 + 1e-6)
        self.assertTrue(any(mn < -0.4 for mn, _ in pairs))
        self.assertAlmostEqual(max(mx for _, mx in pairs), 0.5, delta=0.02)

    def test_stdlib_and_numpy_paths_agree(self):
        try:
            import numpy  # noqa: F401
        except Exception:  # pragma: no cover - CI has no numpy
            self.skipTest("numpy not installed; nothing to compare against")
        n = self.RATE * 3
        samples = [int(12000 * ((i % 97) - 48) / 48.0) for i in range(n)]
        data = _pcm_bytes(samples)
        fast_pcm = Q.decode_pcm_samples(data)
        fast = Q.waveform_window(fast_pcm, *Q.build_wave_envelope(fast_pcm),
                                 self.RATE, 0.0, 3.0, 150)
        with numpy_blocked():
            slow_pcm = Q.decode_pcm_samples(data)
            slow = Q.waveform_window(slow_pcm, *Q.build_wave_envelope(slow_pcm),
                                     self.RATE, 0.0, 3.0, 150)
        self.assertEqual(len(fast), len(slow))
        for (a0, a1), (b0, b1) in zip(fast, slow):
            self.assertAlmostEqual(a0, b0, places=6)
            self.assertAlmostEqual(a1, b1, places=6)


_QML_DIR = _GUI_DIR / "modern" / "qml"
_QML_PATH = _QML_DIR / "UnifiedEditor.qml"


def _all_qml() -> str:
    """Every .qml of the modern engine.

    These guards count occurrences of a call, and the engine is several files
    now -- a single-file read counts one of a pair that lives across two and
    fails without anything being wrong.
    """
    return "\n".join(path.read_text(encoding="utf-8")
                     for path in sorted(_QML_DIR.glob("*.qml")))


def _qml_range_js():
    """The verbatim cut/keep range maths block out of UnifiedEditor.qml."""
    src = _QML_PATH.read_text(encoding="utf-8")
    start = src.index("    function normRanges(")
    end = src.index("    function cutSelection(")
    return src[start:end]


class QmlRangeMathTests(unittest.TestCase):
    """D12/D13: run the editor's own JS in a real JS engine."""

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtCore import QCoreApplication
            from PySide6.QtQml import QJSEngine  # noqa: F401
        except Exception as exc:  # pragma: no cover - CI has no PySide6
            raise unittest.SkipTest(f"PySide6 QtQml required: {exc}")
        # QJSEngine aborts the process without a QCoreApplication.
        cls._app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])

    def _engine(self, total=6.0, cuts=()):
        from PySide6.QtQml import QJSEngine
        eng = QJSEngine()
        prelude = ("var totalDuration = %r; var cuts = %s;"
                   % (float(total), json.dumps([list(c) for c in cuts])) + chr(10))
        res = eng.evaluate(prelude + _qml_range_js())
        self.assertFalse(res.isError(), f"QML JS failed to evaluate: {res.toString()}")
        return eng

    def _call(self, eng, expr):
        res = eng.evaluate(expr)
        self.assertFalse(res.isError(), f"{expr} -> {res.toString()}")
        return json.loads(res.toString())

    def test_fresh_edit_opens_with_no_cuts(self):
        eng = self._engine()
        self.assertEqual(self._call(eng, "JSON.stringify(cutsFromInitialKeep([]))"), [])
        self.assertEqual(self._call(eng, "JSON.stringify(cutsFromInitialKeep(null))"), [])

    def test_carried_over_keep_list_is_still_inverted(self):
        eng = self._engine()
        self.assertEqual(
            self._call(eng, "JSON.stringify(cutsFromInitialKeep([[1,2]]))"),
            [[0, 1], [2, 6]],
        )

    def test_cut_everything_and_no_cuts_produce_different_replies(self):
        no_cuts = self._engine(cuts=[])
        all_cut = self._engine(cuts=[[0, 6]])
        self.assertEqual(self._call(no_cuts, "JSON.stringify(keepRangesForResult())"), [])
        self.assertEqual(self._call(all_cut, "JSON.stringify(keepRangesForResult())"), [])
        # Identical keep lists — only the cuts_applied flag separates them.
        self.assertEqual(self._call(no_cuts, "JSON.stringify(cuts.length > 0)"), False)
        self.assertEqual(self._call(all_cut, "JSON.stringify(cuts.length > 0)"), True)

    def test_partial_cuts_still_produce_the_kept_ranges(self):
        eng = self._engine(cuts=[[1, 2], [4, 5]])
        self.assertEqual(
            self._call(eng, "JSON.stringify(keepRangesForResult())"),
            [[0, 1], [2, 4], [5, 6]],
        )

    def test_result_carries_the_cut_flag(self):
        qml = _all_qml()
        self.assertIn("cuts_applied: cuts.length > 0", qml)


class CutEverythingRejectionTests(unittest.TestCase):
    """D13: the wire boundary must refuse an all-cut edit instead of silently
    returning the untouched source."""

    def _bridge(self, reply):
        from ffmwiz import guibridge_b
        # The DEFINING module. guibridge_b calls this name directly now, so a
        # patch on the facade would rebind an attribute nothing reads.
        original = guibridge_b._launch_qt_gui
        guibridge_b._launch_qt_gui = lambda request: reply
        self.addCleanup(setattr, guibridge_b, "_launch_qt_gui", original)
        return guibridge_b

    def _answers(self):
        return {"input_path": "x.mkv", "format": {"duration": "6.0"},
                "audio_streams": [], "probe": {}}

    def test_unified_all_cut_is_rejected(self):
        bridge = self._bridge({"status": "ok", "margins": [0, 0, 0, 0],
                               "keep_ranges": [], "cuts_applied": True,
                               "separator_points": [], "speed": 1.0,
                               "reverse": False, "include_audio": True})
        self.assertIsNone(bridge.open_unified_video_gui(self._answers()))

    def test_unified_no_cuts_is_accepted(self):
        bridge = self._bridge({"status": "ok", "margins": [0, 0, 0, 0],
                               "keep_ranges": [], "cuts_applied": False,
                               "separator_points": [], "speed": 1.0,
                               "reverse": False, "include_audio": True})
        result = bridge.open_unified_video_gui(self._answers())
        self.assertIsInstance(result, dict)
        self.assertEqual(result["keep_ranges"], [])

    def test_cut_editor_all_cut_is_rejected(self):
        bridge = self._bridge({"status": "ok", "keep_ranges": [], "cuts_applied": True})
        self.assertIsNone(bridge.open_cut_gui(self._answers(), 30.0, 6.0))

    def test_cut_editor_no_cuts_is_accepted(self):
        bridge = self._bridge({"status": "ok", "keep_ranges": [[0.0, 6.0]],
                               "cuts_applied": False})
        self.assertEqual(bridge.open_cut_gui(self._answers(), 30.0, 6.0), [(0.0, 6.0)])


class JoinWaveformGraphTests(unittest.TestCase):
    """D07: one silent segment must not kill the waveform for the whole join."""

    def test_silent_segment_gets_matching_silence(self):
        req = {"join_segments": [
            {"path": "a.mkv", "duration": 5.0, "has_audio": True},
            {"path": "b.mkv", "duration": 4.0, "has_audio": False},
        ]}
        args = Q.build_wave_decode_args(req, "o.pcm")
        joined = " ".join(args)
        self.assertIn("concat=n=2:v=0:a=1", joined)
        # The silent segment is an anullsrc input of the same length, so the
        # concat arity holds and [1:a:0] still binds.
        self.assertIn("anullsrc=channel_layout=mono:sample_rate=4000", joined)
        self.assertIn("lavfi", args)
        self.assertIn("4.000000", args)
        self.assertNotIn("b.mkv", args)
        # Each segment is bounded to its own declared length, so the joined
        # waveform spans exactly 5 + 4 seconds whatever the inputs contain.
        self.assertIn("atrim=end=5.000000,apad=whole_dur=5.000000", joined)
        self.assertIn("atrim=end=4.000000,apad=whole_dur=4.000000", joined)

    def test_all_audio_segments_are_unchanged(self):
        req = {"join_segments": [
            {"path": "a.mkv", "duration": 5.0, "has_audio": True},
            {"path": "b.mkv", "duration": 4.0, "has_audio": True},
        ]}
        args = Q.build_wave_decode_args(req, "o.pcm")
        self.assertNotIn("anullsrc", " ".join(args))
        self.assertIn("b.mkv", args)
        self.assertEqual(args.count("-i"), 2)

    def test_missing_has_audio_key_keeps_the_old_behaviour(self):
        req = {"join_segments": [{"path": "a.mkv", "duration": 5.0}]}
        self.assertIn("a.mkv", Q.build_wave_decode_args(req, "o.pcm"))

    def test_classic_engine_has_the_same_silence_branch(self):
        src = _classic_unified_source()
        self.assertIn("anullsrc=channel_layout=mono:sample_rate=4000", src)
        self.assertIn('segment.get("has_audio", True)', src)


class ReverseProxyTests(unittest.TestCase):
    def test_scale_runs_before_reverse(self):
        """NEW-GUI1: reverse buffers the whole window, so scale must come first."""
        vf = Q.build_reverse_proxy_vf(854)
        self.assertLess(vf.index("scale"), vf.index("reverse"))
        self.assertIn("854:-2", vf)

    def test_chunk_does_not_cross_a_segment_boundary(self):
        """D16: the window is shortened to the end segment, not mis-sourced."""
        segments = [(0.0, 10.0), (10.0, 10.0)]
        index, ss, dur, eff = gui_geometry.reverse_chunk_spec(segments, 9.5, 11.5)
        self.assertEqual(index, 1)
        self.assertAlmostEqual(ss, 0.0)
        self.assertAlmostEqual(dur, 1.5)      # not 2.0
        self.assertAlmostEqual(eff, 10.0)     # next chunk resumes here

    def test_chunk_inside_one_segment_is_untouched(self):
        segments = [(0.0, 10.0), (10.0, 10.0)]
        index, ss, dur, eff = gui_geometry.reverse_chunk_spec(segments, 2.0, 7.0)
        self.assertEqual((index, ss, dur, eff), (0, 2.0, 5.0, 2.0))

    def test_window_ending_on_a_boundary_stays_in_the_earlier_segment(self):
        segments = [(0.0, 10.0), (10.0, 10.0)]
        index, ss, dur, eff = gui_geometry.reverse_chunk_spec(segments, 5.0, 10.0)
        self.assertEqual(index, 0)
        self.assertAlmostEqual(ss, 5.0)
        self.assertAlmostEqual(dur, 5.0)

    def test_single_input_needs_no_segment_list(self):
        self.assertEqual(gui_geometry.reverse_chunk_spec([], 3.0, 8.0), (0, 3.0, 5.0, 3.0))

    def test_qml_clips_the_window_to_the_end_segment(self):
        qml = _all_qml()
        self.assertIn("var eff = Math.max(winStart, segs[s.index].start)", qml)
        self.assertIn("revWinStart = eff", qml)


class ReverseProxyLifecycleTests(unittest.TestCase):
    """D17/NEW-GUI2/NEW-GUI3: structural guarantees of the QML reverse render.

    Bridge is defined inside main(), so these assert on the shipped source
    rather than instantiating it; each one fails if its fix is reverted."""

    SRC = None

    @classmethod
    def setUpClass(cls):
        # The modern engine is three modules now (driver, Bridge, waveform),
        # so read them all: a check pinned to one file passes for the wrong
        # reason the moment the code it guards moves to a sibling.
        cls.SRC = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((_GUI_DIR / "modern").glob("*.py")))

    def test_exit_code_and_size_are_checked_before_emitting(self):
        self.assertIn("os.path.getsize(out) > 0", self.SRC)
        self.assertIn("proc.returncode == 0", self.SRC)

    def test_superseded_render_is_killed(self):
        self.assertIn("def cancelReverse", self.SRC)
        # The child is spawned THROUGH the owner, never with a bare Popen: a
        # bare Popen registers only after it returns, and a cancel landing in
        # that gap reported "clean" while the process ran on (F08).
        self.assertIn("self._children.start(args, generation=gen", self.SRC)
        self.assertNotIn("subprocess.Popen(args", self.SRC)
        # renderReverse claims the generation and cancels before spawning.
        head = self.SRC[self.SRC.index("def renderReverse"):self.SRC.index("def cancelReverse")]
        self.assertIn("self._children.bump_generation(", head)
        self.assertIn("self.cancelReverse()", head)

    def test_proxies_live_in_one_owned_temp_dir(self):
        self.assertIn('tempfile.TemporaryDirectory(prefix="ffmwiz_qmlrev_")', self.SRC)
        self.assertNotIn('mkstemp(suffix=".mp4"', self.SRC)
        self.assertIn("self._rev_temp.cleanup()", self.SRC)

    def test_qml_cancels_the_render_when_reverse_is_abandoned(self):
        qml = _all_qml()
        self.assertEqual(qml.count("bridge.cancelReverse()"), 2)

    def test_qml_leaves_reverse_mode_when_a_chunk_fails(self):
        qml = _all_qml()
        self.assertIn('if (path === "") {', qml)
        self.assertIn("Reverse preview couldn't render here", qml)

    def test_busy_waveform_request_is_answered(self):
        """NEW-GUI6: a concurrent startWaveform must not end in silence."""
        self.assertIn("self._wave_pending = key", self.SRC)
        self.assertIn("if self._wave_pending is not None:", self.SRC)


class TkPlaybackTickTests(unittest.TestCase):
    """NEW-GUI5: exactly one pending 1 Hz chain per Tk editor."""

    FILES = ("guibridge_cut_tk.py", "guibridge_crop_tk.py")

    def test_tick_is_scheduled_through_one_guarded_helper(self):
        for name in self.FILES:
            src = (_ROOT / "ffmwiz" / name).read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertEqual(src.count("root.after(1000, playback_tick)"), 1)
                self.assertIn("def schedule_playback_tick", src)
                self.assertIn("root.after_cancel(playback_tick_handle[", src)
                # Pause cancels the pending chain instead of leaving it armed.
                self.assertIn("cancel_playback_tick()" + chr(10) + "                    stop_audio()", src)


class UnifiedRequestTests(unittest.TestCase):
    """What the bridge actually puts on the wire for the editors."""

    def _capture(self, answers):
        from ffmwiz import guibridge_b
        seen = {}

        def fake(request):
            seen["request"] = request
            return {"status": "canceled"}

        # The DEFINING module. guibridge_b calls this name directly now, so a
        # patch on the facade would rebind an attribute nothing reads.
        original = guibridge_b._launch_qt_gui
        guibridge_b._launch_qt_gui = fake
        self.addCleanup(setattr, guibridge_b, "_launch_qt_gui", original)
        guibridge_b.open_unified_video_gui(answers)
        return seen["request"]

    def test_join_segments_carry_their_own_audio_flag(self):
        """D07: the request-level has_audio is input 0 only."""
        request = self._capture({
            "input_path": "a.mkv", "format": {"duration": "5.0"},
            "audio_streams": [{"index": 1}], "probe": {},
            "join_input_items": [
                {"path": "b.mkv", "duration": 4.0, "audio_streams": [], "probe": {}},
                {"path": "c.mkv", "duration": 3.0, "audio_streams": [{"index": 1}], "probe": {}},
            ],
        })
        self.assertEqual([seg["has_audio"] for seg in request["join_segments"]],
                         [True, False, True])


class QmlChapterNormalisationTests(unittest.TestCase):
    """D14: the QML engine must see chapters in seconds with real titles."""

    def test_ticks_become_seconds_and_tag_titles_survive(self):
        request = {"duration": 6.0, "chapters": [
            {"id": 0, "time_base": "1/1000", "start": 0, "start_time": "0.000000",
             "end": 3000, "end_time": "3.000000", "tags": {"title": "Intro"}},
            {"id": 1, "time_base": "1/1000", "start": 3000, "start_time": "3.000000",
             "end": 6000, "end_time": "6.000000", "tags": {"title": "Outro"}},
        ]}
        self.assertEqual(Q.normalize_request_chapters(request), [
            {"start": 0.0, "end": 3.0, "title": "Intro"},
            {"start": 3.0, "end": 6.0, "title": "Outro"},
        ])

    def test_it_is_idempotent(self):
        once = Q.normalize_request_chapters({"duration": 6.0, "chapters": [
            {"time_base": "1/1000", "start": 3000, "start_time": "3.000000",
             "end": 6000, "end_time": "6.000000", "tags": {"title": "Outro"}}]})
        twice = Q.normalize_request_chapters({"duration": 6.0, "chapters": once})
        self.assertEqual(once, twice)

    def test_qml_reads_the_normalised_shape(self):
        qml = _all_qml()
        self.assertIn("var ct = Number(chs[ci].start)", qml)
        self.assertNotIn("chs[ci].name", qml)


class QmlSelfTestWiringTests(unittest.TestCase):
    """D15 / USER-2-2 / NEW-GUI4: the QML entry point must reuse the classic
    engine's identity, watchdog and shared log instead of its own weaker copy.

    Runs the real main() in a subprocess (offscreen) because the process can
    only ever own one QCoreApplication."""

    # The PACKAGE root, and the package names. A bare `import gui_common` next
    # to a `ffmwiz_gui_qml` that now says `from ffmwiz.gui.gui_common import *`
    # loads the file TWICE under two names (D10): the stubs below would land on
    # one object while the code under test used the other, and `fired` came
    # back empty with nothing saying why.
    DRIVER = """
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(GUI_DIR)))
from ffmwiz.gui import gui_common
from ffmwiz.gui.modern import ffmwiz_gui_qml
fired = []
gui_common._set_windows_app_id = lambda: fired.append("app_id")
gui_common._set_qt_application_icon = lambda app: fired.append("icon")
gui_common._apply_native_windows_icon = lambda w: fired.append("native_icon")
gui_common._install_parent_watchdog = lambda app: fired.append("watchdog:%s" % gui_common._PARENT_PID)
sys.argv = ["ffmwiz_gui_qml.py", "--request", REQ, "--reply", REP]
rc = ffmwiz_gui_qml.main()
print("RESULT" + json.dumps({"rc": rc, "fired": fired}))
"""

    def test_identity_watchdog_and_shared_log_are_wired(self):
        import os
        import subprocess
        import tempfile
        try:
            import PySide6.QtQuick  # noqa: F401
        except Exception as exc:  # pragma: no cover - CI has no PySide6
            self.skipTest(f"PySide6 QtQuick required: {exc}")
        with tempfile.TemporaryDirectory(prefix="ffmwiz_qmltest_") as tmp:
            tmp = Path(tmp)
            log = tmp / "ffmwiz.log"
            req = tmp / "request.json"
            rep = tmp / "reply.json"
            req.write_text(json.dumps({
                "mode": "video_unified", "input_path": "a.mkv", "duration": 6.0,
                "fps": 30.0, "source_w": 1920, "source_h": 1080, "has_audio": False,
                "chapters": [], "join_segments": [], "log_path": str(log),
                "parent_pid": os.getpid(),
            }), encoding="utf-8")
            driver = tmp / "driver.py"
            driver.write_text(
                "GUI_DIR = %r" % str(_GUI_DIR) + chr(10)
                + "REQ = %r" % str(req) + chr(10)
                + "REP = %r" % str(rep) + chr(10)
                + self.DRIVER, encoding="utf-8")
            env = dict(os.environ, FFMWIZ_QML_SELFTEST="1", QT_QPA_PLATFORM="offscreen")
            proc = subprocess.run([sys.executable, str(driver)], capture_output=True,
                                  text=True, timeout=180, env=env)
            self.assertIn("RESULT", proc.stdout, msg=proc.stderr[-2000:])
            payload = json.loads(proc.stdout.split("RESULT", 1)[1].splitlines()[0])
            self.assertEqual(payload["rc"], 0, msg=proc.stderr[-2000:])
            fired = payload["fired"]
            self.assertIn("app_id", fired)
            self.assertIn("icon", fired)
            self.assertIn("native_icon", fired)
            self.assertIn("watchdog:%d" % os.getpid(), fired)
            # NEW-GUI4: diagnostics reach the shared FFmWiz log, not just stderr.
            self.assertTrue(log.exists(), msg=proc.stderr[-2000:])
            text = log.read_text(encoding="utf-8")
            self.assertRegex(text, r"\[INFO\] \[GUI\] \[QML\] QML editor loaded")


if __name__ == "__main__":
    unittest.main()
