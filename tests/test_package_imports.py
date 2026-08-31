"""Regression: every packaged module imports on its own (D09, D10).

The launcher imports the `FFmWiz` facade first, and that one order happens to
work. Nothing else does. Importing each packaged module in its OWN fresh
interpreter -- which is what an installed wheel's consumer does, and what a
`python -m ffmwiz.gui.classic.ffmwiz_gui` launch does -- failed for 24 of 119:

    7   ModuleNotFoundError: No module named 'gui_common'
    17  AttributeError: partially initialized module ... has no attribute '__all__'

The seven are the GUI editors, which addressed their siblings as bare
top-level modules. That only ever resolves when the GUI's own directory is on
`sys.path`, which is true when it is run as a script from that folder and false
for the installed package (D10).

The seventeen are the `X` / `X_b` file-size splits. `X_b` back-imports `X`, and
`X` ends with `__all__ += _X_b.__all__`. Import the FACADE first and `X_b` is
fully initialised by the time that line runs; import the LEAF first and `X`
reaches it while `X_b` is still on its first statement (D09).

This test is the reason the repair cannot regress: it imports every module in
a separate process, so no earlier import can hide the next cycle.
"""
import concurrent.futures
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "ffmwiz"


def _module_names() -> list[str]:
    names = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(ROOT).with_suffix("").parts
        name = ".".join(parts)
        names.append(name[: -len(".__init__")] if name.endswith(".__init__") else name)
    return names


def _import_alone(name: str) -> tuple[str, str]:
    """Import `name` in a FRESH interpreter. Returns (name, "") when it works."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {name}"], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    if result.returncode == 0:
        return name, ""
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    return name, (lines[-1] if lines else f"exit {result.returncode}")


class EveryModuleImportsInAFreshProcess(unittest.TestCase):

    def test_the_sweep_covers_the_whole_package(self):
        # Guard the guard: a sweep that enumerated nothing would pass silently.
        names = _module_names()
        self.assertGreater(len(names), 100, names[:5])
        for expected in ("ffmwiz.encoding", "ffmwiz.reverse_pipeline",
                         "ffmwiz.gui.gui_common", "ffmwiz.support.ext04c"):
            self.assertIn(expected, names)

    def test_no_module_depends_on_being_imported_second(self):
        names = _module_names()
        # A bounded pool: one fresh interpreter per module is the point, and
        # 119 of them in sequence is a minute of wall time for no extra proof.
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_import_alone, names))
        failed = {name: error for name, error in results if error}
        self.assertEqual({}, failed,
                         "these modules only import when something else was "
                         "imported first:\n  "
                         + "\n  ".join(f"{n}: {e}" for n, e in sorted(failed.items())))


class TheGuiIsStillLaunchable(unittest.TestCase):
    """The GUI runs as a SUBPROCESS SCRIPT, not as an imported module.

    Making the editors package-importable must not break that: they are started
    as `python ffmwiz/gui/ffmwiz_gui.py --request ... --reply ...`, and the
    entry point puts the PACKAGE ROOT on `sys.path` so `ffmwiz.gui.<name>`
    resolves the same way it does for the installed wheel.
    """

    def _launch(self, script: str) -> str:
        entry = PACKAGE / "gui" / script
        if not entry.exists():
            self.skipTest(f"{script} is not present")
        reply = ROOT / f"_import_smoke_{script}.json"
        try:
            subprocess.run(
                [sys.executable, str(entry), "--request", str(ROOT / "_nope.json"),
                 "--reply", str(reply)],
                capture_output=True, text=True, timeout=300, cwd=str(ROOT))
            return reply.read_text(encoding="utf-8") if reply.exists() else ""
        finally:
            reply.unlink(missing_ok=True)

    def test_the_classic_gui_entry_point_still_starts(self):
        # A missing request file is the cheapest way to prove the process got
        # far enough to parse arguments and answer, without opening a window.
        self.assertIn("Bad request JSON", self._launch("ffmwiz_gui.py"))


if __name__ == "__main__":
    unittest.main()
