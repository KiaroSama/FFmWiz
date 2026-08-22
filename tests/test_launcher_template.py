"""Regression tests for the generated PowerShell launcher (run.ps1).

`launcher_content()` used to derive the entry-point filename from
`Path(__file__).name`. That was correct while the whole app lived in FFmWiz.py,
but after the package split `__file__` became `ffmwiz/support/L00_misc_b.py`, so
the template started emitting a launcher that runs `L00_misc_b.py`. Every fresh
install got a run.ps1 that cannot start, and every existing install logged
"Existing launcher file differs from the FFmWiz template" on startup -- which is
the only reason a working run.ps1 survived: FFmWiz refuses to overwrite one.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import FFmWiz

PROJECT_ROOT = Path(FFmWiz.__file__).resolve().parent
PWSH = shutil.which("pwsh") or shutil.which("powershell")


class LauncherTemplate(unittest.TestCase):
    def test_template_targets_the_real_entry_point(self):
        text = FFmWiz.launcher_content()
        self.assertIn("FFmWiz.py", text)
        self.assertNotIn("L00_misc_b", text)
        self.assertNotIn("ffmwiz/support", text)

    def test_template_matches_the_shipped_launcher(self):
        # The drift guard. FFmWiz compares these two at startup and warns when
        # they differ, so they must stay byte-identical.
        shipped = (PROJECT_ROOT / FFmWiz.LAUNCHER_FILE_NAME).read_text(encoding="utf-8-sig")
        self.assertEqual(
            FFmWiz.launcher_content(), shipped,
            "run.ps1 and launcher_content() have drifted; a fresh install would get a different launcher",
        )

    def test_entry_point_constant_is_not_derived_from_a_module_filename(self):
        self.assertEqual(FFmWiz.MAIN_SCRIPT_FILE_NAME, "FFmWiz.py")

    def test_template_requires_python_310(self):
        # `import sys` succeeds on 3.9 too, so probing only runnability let the
        # launcher select an interpreter FFmWiz cannot run on.
        text = FFmWiz.launcher_content()
        self.assertIn("version_info", text)
        self.assertIn("3.10", text)

    @unittest.skipIf(not PWSH, "PowerShell not available")
    def test_launcher_skips_an_interpreter_older_than_310(self):
        # Drive the real launcher with shims: `py` reports 3.9, `python` reports
        # a supported version. The launcher must reject the first, say so, and
        # fall through to the second instead of failing inside the app.
        tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_launcher_ver_"))
        try:
            shims = tmp / "shims"
            shims.mkdir()
            (tmp / "run.ps1").write_text(FFmWiz.launcher_content(), encoding="utf-8")
            (tmp / "FFmWiz.py").write_text(
                "import sys\nprint('stub ok')\nsys.exit(0)\n", encoding="utf-8")
            (shims / "py.cmd").write_text(
                "@echo off\r\n"
                "if \"%~1\"==\"-3\" shift\r\n"
                "if \"%~1\"==\"-c\" (\r\n  echo 3.9.13\r\n  exit /b 0\r\n)\r\n"
                "exit /b 1\r\n", encoding="utf-8")
            (shims / "python.cmd").write_text(
                "@echo off\r\n"
                "if \"%~1\"==\"-c\" (\r\n  echo 3.12.4\r\n  exit /b 0\r\n)\r\n"
                f"\"{sys.executable}\" %*\r\n", encoding="utf-8")
            env = dict(os.environ)
            env["PATH"] = f"{shims}{os.pathsep}{env['PATH']}"
            result = subprocess.run(
                [PWSH, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(tmp / "run.ps1")],
                capture_output=True, text=True, timeout=300, cwd=str(tmp), env=env)
            combined = result.stdout + result.stderr
            self.assertIn("3.9.13", combined, f"the rejected version must be named: {combined[:400]}")
            self.assertIn("stub ok", combined, f"the next candidate must be used: {combined[:400]}")
            self.assertEqual(result.returncode, 0, combined[:400])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipIf(not PWSH, "PowerShell not available")
    def test_generated_launcher_actually_finds_the_entry_point(self):
        # End-to-end: drop the template next to a stub entry point and run it.
        # Before the fix this printed "L00_misc_b.py was not found" and exited 1.
        tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_launcher_"))
        try:
            (tmp / "run.ps1").write_text(FFmWiz.launcher_content(), encoding="utf-8")
            (tmp / "FFmWiz.py").write_text(
                "import sys\nprint('stub entry point ok')\nsys.exit(0)\n", encoding="utf-8")
            result = subprocess.run(
                [PWSH, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(tmp / "run.ps1")],
                capture_output=True, text=True, timeout=180, cwd=str(tmp))
            combined = result.stdout + result.stderr
            self.assertNotIn("was not found", combined, combined[:800])
            self.assertEqual(result.returncode, 0, combined[:800])
            self.assertIn("stub entry point ok", combined)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
