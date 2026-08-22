"""install-command.ps1: a failed dependency install must stop the installer.

The script wraps native pip in try/catch, but PowerShell does not turn a native
command's non-zero exit code into a terminating error (the experimental
$PSNativeCommandUseErrorActionPreference is off by default even on 7.6), so the
catch block never ran and the installer went on to create launchers, mutate the
User PATH and rewrite both PowerShell profiles after pip had failed.

Only the dependency-install section of the real script is executed here: running
the whole file would mutate this machine's User PATH and PowerShell profiles.
The section is sliced out of the shipped file verbatim, so it is the real code
under test, and a marker line stands in for the launcher-creation step that must
not be reached.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

PROJECT_ROOT = Path(FFmWiz.__file__).resolve().parent
INSTALLER = PROJECT_ROOT / "install-command.ps1"
PWSH = shutil.which("pwsh") or shutil.which("powershell")
LAUNCHER_STEP = "New-Item -ItemType Directory -Path $commandDir -Force | Out-Null"
MARKER = "REACHED-LAUNCHER-STEP"


@unittest.skipIf(not PWSH, "PowerShell not available")
class InstallerDependencyFailureTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_installer_"))
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def _prepare(self, python_exit_code: int) -> Path:
        text = INSTALLER.read_text(encoding="utf-8-sig")
        self.assertIn(LAUNCHER_STEP, text, "installer layout changed; update this test")
        prefix = text.split(LAUNCHER_STEP)[0]
        script = self._tmp / "install-command.ps1"
        script.write_text(prefix + f"Write-Host '{MARKER}'\n", encoding="utf-8")
        # The installer refuses to run without the launcher next to it.
        (self._tmp / "run.ps1").write_text("exit 0\n", encoding="utf-8")
        (self._tmp / "requirements.txt").write_text("PySide6==6.11.1\n", encoding="utf-8")
        shims = self._tmp / "shims"
        shims.mkdir()
        for name in ("py.cmd", "python.cmd", "python3.cmd"):
            (shims / name).write_text(
                "@echo off\r\necho fake pip run\r\nexit /b %d\r\n" % python_exit_code,
                encoding="ascii")
        return shims

    def _run_installer(self, python_exit_code: int) -> subprocess.CompletedProcess:
        shims = self._prepare(python_exit_code)
        env = dict(os.environ)
        env["FFMWIZ_INSTALL_PYSIDE"] = "Y"
        env["PATH"] = str(shims) + os.pathsep + env.get("PATH", "")
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(self._tmp / "install-command.ps1")],
            capture_output=True, text=True, timeout=180, cwd=str(self._tmp), env=env)

    def test_failed_pip_aborts_before_the_launcher_step(self):
        result = self._run_installer(2)
        combined = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, combined[:800])
        self.assertIn("pip exit 2", combined)
        self.assertNotIn(MARKER, combined)

    def test_successful_pip_continues(self):
        result = self._run_installer(0)
        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, combined[:800])
        self.assertIn(MARKER, combined)


if __name__ == "__main__":
    unittest.main()
