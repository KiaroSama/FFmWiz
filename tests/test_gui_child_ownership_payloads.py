"""A03: a malformed payload or a truncated PCM must not be accepted as a result.

Three shapes that reached real code paths without a guard:

* the Classic waveform callback refused a ZERO-byte PCM but not a TRUNCATED one.
  `s16le` is two bytes per sample, so a decode killed mid-write leaves an odd
  byte count -- and `timeline.set_pcm` stores the buffer it is handed before
  NumPy can refuse it, so the timeline drew a waveform built from half a sample.
  The modern engine never had this hole: `decode_pcm_samples` drops a trailing
  partial sample deliberately.
* `Bridge.submit` tested `"status" not in result` on whatever `json.loads`
  returned. Valid JSON that is not an object (`null`, `[]`, `3`) raised there, so
  no reply was ever written and `app.quit()` never ran: the editor subprocess
  hung instead of exiting.
* `Bridge.renderReverse` read `spec.get("gen", 0)` on the same unvalidated value,
  and `int()` on whatever it found.

Each case has a positive control beside it, because a guard that refuses every
payload would satisfy the negative assertions on its own.

Qt-free on purpose. Building the real Classic window in-process installs an
application-wide event filter that outlives its own spinboxes, so the module
ends on an unraisable no teardown here can prevent -- which is why the rule
itself lives in the shared decode contract and is checked there.
"""
from __future__ import annotations

import inspect
import json
import unittest
from unittest import mock

from ffmwiz.gui import gui_waveform_decode
from ffmwiz.gui.classic import gui_editor_unified
from ffmwiz.gui.gui_child_owner import ChildProcessOwner
from ffmwiz.gui.gui_waveform_decode import pcm_is_whole_samples
from ffmwiz.gui.modern.gui_qml_bridge import build_bridge

import test_gui_child_ownership_artifacts as harness


class PcmSampleShape(unittest.TestCase):
    def test_a_whole_sample_buffer_is_usable(self):
        self.assertTrue(pcm_is_whole_samples(b"\0\0" * 4000))
        self.assertTrue(pcm_is_whole_samples(b"\0\0"))

    def test_odd_pcm_sample_is_not_usable(self):
        for size in (1, 3, 7999):
            with self.subTest(size=size):
                self.assertFalse(pcm_is_whole_samples(b"\0" * size))

    def test_empty_pcm_is_not_usable(self):
        self.assertFalse(pcm_is_whole_samples(b""))

    def test_the_classic_callback_applies_the_shared_rule(self):
        # The window itself cannot be built here, so pin the delegation: the
        # callback must ask the shared contract rather than carry its own copy
        # of the rule, or the two engines drift apart again.
        source = inspect.getsource(gui_editor_unified)
        body = source[source.index("def _waveform_finished"):]
        body = body[:body.index("def resizeEvent")]
        self.assertIn("pcm_is_whole_samples(data)", body)
        self.assertNotIn("% 2", body)
        self.assertIs(gui_editor_unified.pcm_is_whole_samples,
                      gui_waveform_decode.pcm_is_whole_samples)


class BridgePayloadShape(unittest.TestCase):
    def setUp(self):
        self.replies: list = []
        self.app = mock.Mock()
        cls = build_bridge(object, harness.plain_slot, harness.DummySignal,
                           harness.plain_property, self.replies.append,
                           lambda level, text: None, lambda req, spec: False,
                           lambda width: "reverse")
        with mock.patch("ffmwiz.gui.modern.gui_qml_bridge.ChildProcessOwner",
                        side_effect=lambda: ChildProcessOwner(kill_timeout=0.1)):
            self.bridge = cls(self.app, {"input_path": "unused.mkv", "duration": 1,
                                         "has_audio": True, "ffmpeg": "unused"})
        self.addCleanup(self.bridge.cleanup_reverse)

    def test_an_object_result_is_still_submitted(self):
        self.bridge.submit(json.dumps({"status": "ok", "margins": []}))
        self.assertEqual("ok", self.replies[-1]["status"])
        self.assertEqual(1, self.app.quit.call_count)

    def test_valid_json_of_the_wrong_type_returns_an_error_reply(self):
        for value in (None, [], 3, "not an object"):
            with self.subTest(value=value):
                self.bridge.submit(json.dumps(value))
                self.assertEqual("error", self.replies[-1]["status"])
        # The reply is only half of it: an editor that never quits leaves the
        # parent waiting on a process that has nothing left to say.
        self.assertEqual(4, self.app.quit.call_count)

    def test_invalid_reverse_payload_never_registers_work(self):
        for payload in ("null", "[]", "3", '"text"', '{"gen": "invalid"}'):
            with self.subTest(payload=payload):
                self.bridge.renderReverse(payload)
                self.assertEqual(0, self.bridge._reverse_children.active_workers())
                self.assertEqual(0, self.bridge._reverse_children.active_children())

    def test_a_well_formed_reverse_payload_still_registers_work(self):
        with mock.patch.object(harness.threading.Thread, "start") as start:
            self.bridge.renderReverse(json.dumps({"gen": 2}))
        start.assert_called_once()
        self.assertEqual(1, self.bridge._reverse_children.active_workers())
        self.bridge._reverse_children.worker_finished()


if __name__ == "__main__":
    unittest.main()
