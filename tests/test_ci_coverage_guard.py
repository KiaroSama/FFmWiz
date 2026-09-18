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


WORKFLOW = TESTS_DIR.parent / ".github" / "workflows" / "python-smoke.yml"


def _imports_pyside(path: Path) -> bool:
    """True when the file IMPORTS PySide6, anywhere, at any nesting level.

    An import, not a mention: several suites name PySide6 only inside a skip
    reason or a guard's own pattern list, and flagging those would demand CI
    selection for modules that run perfectly well without Qt.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "PySide6" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "PySide6":
                return True
    return False


def qt_test_modules() -> list[str]:
    """tests/ modules that cannot run without PySide6 installed."""
    return [path.stem for path in sorted(TESTS_DIR.glob("test*.py"))
            if _defines_tests(path) and _imports_pyside(path)]


def gui_job_selection() -> list[str]:
    """The `-k` substrings the only PySide6-equipped CI job selects."""
    for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
        if "run_suite.py" in line and "--require pyside6" in line:
            tokens = line.split()
            return [tokens[index + 1] for index, token in enumerate(tokens)
                    if token == "-k" and index + 1 < len(tokens)]
    raise AssertionError("no PySide6 test step found in the workflow")


class QtSuitesAreActuallySelectedInCi(unittest.TestCase):
    """A hand-maintained `-k` list silently drops new suites.

    Only one CI job installs PySide6, and it selects modules by substring. A new
    Qt suite that nobody adds to that line runs NOWHERE: it is skipped in every
    other job for want of PySide6 and never selected in the one that has it, and
    the run still reports OK.
    """

    def test_every_qt_suite_is_selected_by_the_gui_job(self):
        selection = gui_job_selection()
        missing = [name for name in qt_test_modules()
                   if not any(pattern in name for pattern in selection)]
        self.assertEqual([], missing,
                         f"Qt suites no CI job runs: {missing}; add each to the "
                         f"-k list of the gui-import job")

    def test_the_selection_is_not_empty(self):
        self.assertTrue(gui_job_selection())

    def test_every_selected_pattern_still_matches_something(self):
        names = [path.stem for path in TESTS_DIR.glob("test*.py")]
        for pattern in gui_job_selection():
            with self.subTest(pattern=pattern):
                self.assertTrue([name for name in names if pattern in name],
                                f"-k {pattern} matches no test module any more")

    def test_the_guard_notices_an_unselected_suite(self):
        selection = ["gui_editors"]
        modules = ["test_gui_editors", "test_brand_new_qml_suite"]
        missing = [name for name in modules
                   if not any(pattern in name for pattern in selection)]
        self.assertEqual(["test_brand_new_qml_suite"], missing)


class TheJobsRequireWhatTheyProvide(unittest.TestCase):
    """A capability the runner has must not be excusable by a skip.

    The PowerShell suites parametrise over Windows PowerShell 5.1 and pwsh 7
    and skip when neither is on PATH. The main test job runs on a Windows
    machine that has both, so such a skip means the suite shrank -- but the
    command did not pass `--require powershell`, so it shrank silently.
    """

    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def main_test_command(self) -> str:
        for line in self.text.splitlines():
            if "run_suite.py" in line and "--require ffmpeg" in line and "-k " not in line:
                return line
        raise AssertionError("no unfiltered test step found in the workflow")

    def test_the_main_test_job_requires_powershell(self):
        self.assertIn("--require powershell", self.main_test_command())

    def test_it_still_requires_the_other_installed_capabilities(self):
        command = self.main_test_command()
        for capability in ("ffmpeg", "numpy", "wheel"):
            with self.subTest(capability=capability):
                self.assertIn(f"--require {capability}", command)


if __name__ == "__main__":
    unittest.main()
