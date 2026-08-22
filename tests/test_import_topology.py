"""Regression tests for the overflow-module import cycle.

Several `support/ext*.py` modules were split into a parent plus an overflow
sibling. The parent back-imports the child at the bottom and merges its
`__all__`; the child imports the parent at the top. That resolves fine when the
PARENT is imported first, which is what `import FFmWiz` does.

Importing the CHILD first re-enters the parent while the child is still only
partially initialised, so it has no `__all__` yet and the merge raised
`AttributeError: partially initialized module ... has no attribute '__all__'`.
Every overflow module was affected, not just the one the audit happened to hit.
"""
import subprocess
import sys
import unittest
from pathlib import Path

import FFmWiz

PROJECT_ROOT = Path(FFmWiz.__file__).resolve().parent

# Every module that is back-imported by a parent in the same tier.
OVERFLOW_MODULES = [
    "ffmwiz.support.ext00b",
    "ffmwiz.support.ext00c",
    "ffmwiz.support.ext00d",
    "ffmwiz.support.ext01b",
    "ffmwiz.support.ext01c",
    "ffmwiz.support.ext01d",
    "ffmwiz.support.ext04b",
]


def _import_in_fresh_interpreter(module: str):
    """Import `module` FIRST in a clean interpreter, with nothing else loaded."""
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=180)


class OverflowModuleImports(unittest.TestCase):
    def test_every_overflow_module_imports_standalone(self):
        for module in OVERFLOW_MODULES:
            with self.subTest(module=module):
                result = _import_in_fresh_interpreter(module)
                self.assertEqual(
                    result.returncode, 0,
                    f"{module} cannot be imported directly:\n"
                    f"{result.stderr.strip()[-500:]}")

    def test_parent_first_import_still_re_exports_the_child_tier(self):
        # The guard must not silently drop the merge on the normal path.
        import ffmwiz.support.ext00 as ext00
        import ffmwiz.support.ext01 as ext01
        self.assertIn("run_mux_cleanup_mode", ext00.__all__)
        self.assertTrue(hasattr(ext00, "run_mux_cleanup_mode"))
        self.assertTrue(hasattr(ext01, "enforce_bit_depth_compatible_video_encoder"))

    def test_public_api_is_unchanged(self):
        # The facade is what the whole test suite and the CI smoke import through.
        for name in ("run_mux_cleanup_mode", "build_ffmpeg_command", "build_hardsub_command",
                     "build_join_encode_command", "run_ffmpeg_with_progress"):
            self.assertTrue(hasattr(FFmWiz, name), f"FFmWiz.{name} disappeared")

    def test_the_live_stream_cleanup_handler_is_the_embedded_one(self):
        # Menu 8 must keep resolving to the maintained subsystem, not the
        # dormant duplicate in ffmwiz/mux_rules_b.py.
        self.assertEqual(FFmWiz.run_mux_cleanup_mode.__module__, "ffmwiz.support.ext00b")


if __name__ == "__main__":
    unittest.main()
