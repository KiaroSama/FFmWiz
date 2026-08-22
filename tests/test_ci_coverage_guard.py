"""Guard: every file in tests/ that holds real tests must actually be run.

`unittest discover` only loads files matching `test*.py`, so a module renamed to
`broken_x.py`, or a new suite named `mux_selection_tests.py`, is skipped in
complete silence -- the run still reports OK, just with fewer tests. Nothing in
CI notices, because the expected count lives in a doc comment.

The check is mechanical: any tests/*.py that defines a class with a `test_*`
method must appear in the set of modules discovery loaded. Files are inspected
with `ast`, never imported, so a broken module cannot break the guard itself.
"""
from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def _defines_tests(path: Path) -> bool:
    """True when the file declares a class containing a `test_*` method."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return True  # unparseable but present: let discovery's failure surface it
    return any(
        isinstance(node, ast.ClassDef)
        and any(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name.startswith("test_")
                for child in node.body)
        for node in ast.walk(tree)
    )


def unrun_test_modules(start_dir: Path) -> list[str]:
    """Modules in start_dir that define tests but that discovery does not load."""
    with_tests = {p.stem for p in start_dir.glob("*.py") if _defines_tests(p)}
    # A fresh loader, not the default one: discover() remembers its top-level dir
    # on the loader instance, and the shared default is already pointed at the
    # running suite's own start directory.
    loaded = {
        type(test).__module__.rsplit(".", 1)[-1]
        for test in _flatten(unittest.TestLoader().discover(str(start_dir)))
    }
    return sorted(with_tests - loaded)


class CiCoverageGuardTests(unittest.TestCase):
    def test_every_module_holding_tests_is_discovered(self):
        unrun = unrun_test_modules(TESTS_DIR)
        self.assertEqual([], unrun,
                         f"tests/ modules that define tests but never run: {unrun}")

    def test_guard_reports_a_module_discovery_would_skip(self):
        # Proves the guard can fail: a suite whose filename discovery ignores.
        saved_path = list(sys.path)
        body = ("import unittest\n\n\nclass T(unittest.TestCase):\n"
                "    def test_x(self):\n        pass\n")
        with tempfile.TemporaryDirectory() as td:
            probe = Path(td)
            (probe / "test_seen.py").write_text(body, encoding="utf-8")
            (probe / "renamed_suite.py").write_text(body, encoding="utf-8")
            (probe / "helper_no_tests.py").write_text(
                "def make_thing():\n    return 1\n", encoding="utf-8")
            try:
                self.assertEqual(["renamed_suite"], unrun_test_modules(probe))
            finally:
                sys.path[:] = saved_path
                sys.modules.pop("test_seen", None)


if __name__ == "__main__":
    unittest.main()
