"""Logging must be precise and cheap enough to actually read.

Measured on a real 115-line session log before this work:

  * the SAME command was written four times per run -- "display command" and
    "actual argv" from log_command, then both again from the progress wrapper
    with only `-progress pipe:1 -nostats` different. 702 + 628 + 603 + 551
    characters for one invocation;
  * identical FFmpeg stderr lines repeated verbatim ("Could not find codec
    parameters for stream 1" a dozen times), burying the lines that differed;
  * 96 of 115 lines carried the component `[FFmWiz]`, so the field said nothing
    and the log could not be filtered by subsystem;
  * the stderr buffer grew for the whole encode although only its last 12 lines
    are ever read;
  * the level was pinned to DEBUG with no way to ask for a smaller file.

These tests pin each of those.
"""
import logging
import re
import unittest

import FFmWiz

from ffmwiz import appio


class CommandIsRecordedOnce(unittest.TestCase):
    def setUp(self):
        self.records = []
        self._real = appio._emit_log

        def capture(level, msg, component, exc_info=False):
            self.records.append((level, msg))

        appio._emit_log = capture

    def tearDown(self):
        appio._emit_log = self._real

    def _levels(self, level):
        return [msg for lvl, msg in self.records if lvl == level]

    def test_one_pasteable_line_at_info(self):
        appio.log_command("Encode", ["ffmpeg", "-i", "a.mkv", "out.mp4"])
        info = self._levels(logging.INFO)
        self.assertEqual(1, len(info), f"expected exactly one INFO line, got {info}")
        self.assertIn("ffmpeg", info[0])

    def test_the_exact_argv_is_debug_not_info(self):
        # The JSON argv is for debugging quoting problems; it should not double
        # the size of every normal log.
        appio.log_command("Encode", ["ffmpeg", "-i", "a b.mkv", "out.mp4"])
        debug = self._levels(logging.DEBUG)
        self.assertTrue(any("argv" in msg for msg in debug))
        self.assertFalse(any("argv" in msg for msg in self._levels(logging.INFO)))

    def test_progress_flags_are_recorded_without_repeating_the_command(self):
        cmd = ["ffmpeg", "-i", "a.mkv", "out.mp4"]
        appio.log_command("Encode", cmd, ["-progress", "pipe:1", "-nostats"])
        progress_lines = [msg for _lvl, msg in self.records if "progress args" in msg]
        self.assertEqual(1, len(progress_lines))
        # The whole command must NOT appear again on that line.
        self.assertNotIn("out.mp4", progress_lines[0])

    def test_the_total_is_three_lines_not_four_full_commands(self):
        appio.log_command("Encode", ["ffmpeg", "-i", "a.mkv", "out.mp4"],
                          ["-progress", "pipe:1"])
        self.assertLessEqual(len(self.records), 3)
        full_command_lines = [msg for _lvl, msg in self.records if "out.mp4" in msg]
        self.assertLessEqual(len(full_command_lines), 2,
                             "the command should appear at most twice: readable + exact")


class ComponentIdentifiesTheSubsystem(unittest.TestCase):
    def _component_for(self, module_name):
        record = logging.LogRecord("ffmwiz", logging.INFO, "x.py", 1, "m", None, None)
        record.module = module_name
        appio._LogContextFilter().filter(record)
        return record.component

    def test_the_component_is_the_logging_module(self):
        self.assertEqual("modes_join", self._component_for("modes_join"))

    def test_an_explicit_component_still_wins(self):
        record = logging.LogRecord("ffmwiz", logging.INFO, "x.py", 1, "m", None, None)
        record.module = "runtime"
        record.component = "Startup"
        appio._LogContextFilter().filter(record)
        self.assertEqual("Startup", record.component)

    def test_a_missing_module_falls_back_rather_than_crashing(self):
        record = logging.LogRecord("ffmwiz", logging.INFO, "x.py", 1, "m", None, None)
        record.module = ""
        appio._LogContextFilter().filter(record)
        self.assertEqual(appio._DEFAULT_LOG_COMPONENT, record.component)

    def test_secrets_are_still_redacted(self):
        # The filter does two jobs; adding the component must not drop the other.
        record = logging.LogRecord("ffmwiz", logging.INFO, "x.py", 1,
                                   "token=abc", None, None)
        record.module = "runtime"
        appio._LogContextFilter().filter(record)
        self.assertEqual(str(record.msg), FFmWiz.redact_secrets("token=abc"))


class LevelIsConfigurable(unittest.TestCase):
    def setUp(self):
        self._env = FFmWiz.os.environ.get("FFMWIZ_LOG_LEVEL")

    def tearDown(self):
        if self._env is None:
            FFmWiz.os.environ.pop("FFMWIZ_LOG_LEVEL", None)
        else:
            FFmWiz.os.environ["FFMWIZ_LOG_LEVEL"] = self._env

    def test_the_default_keeps_debug_so_evidence_is_not_lost(self):
        FFmWiz.os.environ.pop("FFMWIZ_LOG_LEVEL", None)
        self.assertEqual(logging.DEBUG, appio._log_level_from_config())

    def test_the_environment_can_ask_for_a_lean_log(self):
        FFmWiz.os.environ["FFMWIZ_LOG_LEVEL"] = "info"
        self.assertEqual(logging.INFO, appio._log_level_from_config())

    def test_every_documented_name_resolves(self):
        for name, expected in (("debug", logging.DEBUG), ("info", logging.INFO),
                               ("warning", logging.WARNING), ("error", logging.ERROR),
                               ("critical", logging.CRITICAL)):
            with self.subTest(name):
                FFmWiz.os.environ["FFMWIZ_LOG_LEVEL"] = name
                self.assertEqual(expected, appio._log_level_from_config())

    def test_an_unknown_name_does_not_silence_the_log(self):
        # Falling back to a HIGHER level would quietly lose entries.
        FFmWiz.os.environ["FFMWIZ_LOG_LEVEL"] = "chatty"
        self.assertEqual(logging.DEBUG, appio._log_level_from_config())

    def test_the_option_is_documented_for_users(self):
        template = FFmWiz.config_template_text() if hasattr(FFmWiz, "config_template_text") else ""
        if not template:
            from ffmwiz.core import constants_config_template as tpl
            template = "\n".join(
                str(getattr(tpl, name)) for name in dir(tpl) if name.isupper())
        self.assertIn("log_level", template)


class StderrBufferIsBounded(unittest.TestCase):
    def test_the_cap_is_declared_and_larger_than_what_a_report_quotes(self):
        self.assertGreater(FFmWiz.STDERR_TAIL_LINES, FFmWiz.STDERR_TAIL_REPORT_LINES)

    def test_the_runner_uses_a_bounded_buffer(self):
        from pathlib import Path
        source = (Path(FFmWiz.__file__).resolve().parent / "ffmwiz" / "runtime.py").read_text(
            encoding="utf-8")
        self.assertIn("deque(maxlen=STDERR_TAIL_LINES)", source,
                      "an unbounded list grows for the whole encode")
        self.assertNotIn("stderr_lines[-12:]", source,
                         "the report length should come from the constant")

    def test_repeated_stderr_lines_are_collapsed(self):
        from pathlib import Path
        source = (Path(FFmWiz.__file__).resolve().parent / "ffmwiz" / "runtime.py").read_text(
            encoding="utf-8")
        self.assertIn("repeated", source,
                      "identical consecutive stderr lines must collapse to a count")
        # The collapse has to be flushed when the stream ends too, or a trailing
        # run of duplicates is dropped entirely.
        body = source.split("def _capture_stderr")[1].split("stdout_thread =")[0]
        self.assertGreaterEqual(
            len(re.findall(r"flush_repeats\(\)", body)), 2,
            "flush_repeats must be called mid-stream AND after the loop ends")


if __name__ == "__main__":
    unittest.main()
