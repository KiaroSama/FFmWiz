"""Regression: the ffprobe diagnostic block must not raise on its way out.

`log_ffprobe_diagnostics()` formats the causing exception with
`traceback.format_exception(...)`, and `ffmwiz/support/ext00c.py` never
imported `traceback`. The line only runs when an exception object is actually
passed, which is exactly the case nothing exercised, so it sat there until an
unreadable Matroska stub hit it during a bounded audio reverse:

    NameError: name 'traceback' is not defined

Reproduced 2026-08-25. The cost is worse than a missing traceback: the
diagnostics helper is called from the FAILURE path, so its own `NameError`
replaced the `FFprobeError` the caller was about to raise, and the real reason
the probe failed never reached the user.
"""
import unittest

import FFmWiz

from ffmwiz import appio
from ffmwiz.support import ext00c
from pathlib import Path


class TheDiagnosticBlockSurvivesAnException(unittest.TestCase):

    def setUp(self):
        self.logged = []
        self._real_error = appio.log_error
        appio.log_error = lambda msg, component=None: self.logged.append(str(msg))
        # ext00c resolved `log_error` into its own globals at import time, so
        # patching `appio` alone would not reach the call.
        self._real_module_error = ext00c.log_error
        ext00c.log_error = appio.log_error
        self.addCleanup(self._restore)

    def _restore(self):
        appio.log_error = self._real_error
        ext00c.log_error = self._real_module_error

    def _raised(self, message="probe blew up"):
        """A real exception with a real traceback attached."""
        try:
            raise ValueError(message)
        except ValueError as exc:
            return exc

    def _diagnose(self, exc):
        ext00c.log_ffprobe_diagnostics(
            Path("unreadable.mkv"), "ffprobe",
            ["ffprobe", "-show_format", "unreadable.mkv"],
            1, "", "Invalid data found when processing input", "utf-8", exc)

    def test_passing_an_exception_does_not_raise(self):
        self._diagnose(self._raised())

    def test_the_formatted_traceback_reaches_the_log(self):
        self._diagnose(self._raised("probe blew up"))
        joined = "\n".join(self.logged)
        self.assertIn("exception traceback:", joined)
        self.assertIn("ValueError: probe blew up", joined)
        self.assertIn("Traceback (most recent call last)", joined)

    def test_the_block_is_still_complete_around_it(self):
        # Guard the guard: a helper that bailed out early would also "not
        # raise", and the diagnostics are the whole point of the call.
        self._diagnose(self._raised())
        joined = "\n".join(self.logged)
        for expected in ("ffprobe diagnostic block begin", "input path:",
                         "return code: 1", "stderr preview:",
                         "ffprobe diagnostic block end"):
            self.assertIn(expected, joined, f"the block lost {expected!r}")

    def test_no_exception_still_logs_the_rest(self):
        ext00c.log_ffprobe_diagnostics(
            Path("unreadable.mkv"), "ffprobe", ["ffprobe", "unreadable.mkv"],
            1, "", "boom", "utf-8")
        joined = "\n".join(self.logged)
        self.assertIn("ffprobe diagnostic block end", joined)
        self.assertNotIn("exception traceback:", joined)

    def test_the_module_really_imports_traceback(self):
        # The defect in one line: the name has to exist in THIS module's
        # globals, because that is where the call resolves it.
        self.assertTrue(hasattr(ext00c, "traceback"),
                        "ext00c calls traceback.format_exception but does not import it")


class NoModuleCallsAnUnimportedStdlibName(unittest.TestCase):
    """The mechanical guard, so the next one is caught by the suite.

    Fixing `ext00c` alone would have left the identical defect in
    `ffmwiz/guibridge_b.py`, which calls `traceback.format_exc()` twice on the
    Qt GUI subprocess FAILURE path and never imported it either. Both were
    invisible for the same reason: the line only runs when something has
    already gone wrong.

    `ffmwiz/gui/` is exempt and stays exempt: those modules run in a subprocess
    whose entry point injects a fully assembled namespace, so their names are
    genuinely not resolved from their own imports.
    """

    # Names whose absence turns a failure path into a NameError. Kept short and
    # explicit rather than "every stdlib module": a wide list would drown the
    # real signal in `stat.st_size` style false positives (a local os.stat()
    # result, not the module -- two of those turned up in this very scan).
    WATCHED = ("traceback", "subprocess", "tempfile", "shutil", "json")

    def test_every_module_can_reach_the_names_it_calls(self):
        import ast
        import importlib

        root = Path(FFmWiz.__file__).resolve().parent / "ffmwiz"
        offenders = []
        for path in sorted(root.rglob("*.py")):
            if "gui" in path.parts or path.name == "__init__.py":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
                continue
            called = {node.value.id for node in ast.walk(tree)
                      if isinstance(node, ast.Attribute)
                      and isinstance(node.value, ast.Name)
                      and node.value.id in self.WATCHED}
            if not called:
                continue
            module = importlib.import_module(
                "ffmwiz." + str(path.relative_to(root).with_suffix("")).replace("\\", ".").replace("/", "."))
            for name in sorted(called):
                if not hasattr(module, name):
                    offenders.append(f"{path.name} calls {name}.* but cannot reach {name}")
        self.assertEqual([], offenders, "\n  ".join([""] + offenders))

    def test_the_guard_would_notice(self):
        # Guard the guard: the scan must actually be looking at modules that
        # DO call these names, or an empty result proves nothing.
        import ast
        root = Path(FFmWiz.__file__).resolve().parent / "ffmwiz"
        watchers = 0
        for path in root.rglob("*.py"):
            if "gui" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            if any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                   and n.value.id in self.WATCHED for n in ast.walk(tree)):
                watchers += 1
        self.assertGreater(watchers, 10, "the scan found almost nothing to check")


if __name__ == "__main__":
    unittest.main()
