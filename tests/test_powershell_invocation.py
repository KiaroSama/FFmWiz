"""F12: the command FFmWiz prints has to be a command PowerShell will run.

`command_to_powershell` quoted the executable, and PowerShell parses a quoted
string in command position as an EXPRESSION -- so the line FFmWiz printed for
an FFmpeg under `C:\\Program Files\\...` printed its own path instead of running
anything. The call operator `&` is what turns it back into an invocation.

These tests execute the generated strings in real Windows PowerShell 5.1 and
PowerShell 7 and read back what the child actually received, including through
real FFmpeg metadata under a spaced, non-ASCII path.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k powershell_invocation
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import FFmWiz
from ffmwiz.support.L00_misc_b import ps_quote
from ffmwiz.support.L01_misc import command_to_powershell

WINDOWS_POWERSHELL = shutil.which("powershell")
PWSH = shutil.which("pwsh")
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

HOSTS = {name: path for name, path in
         (("powershell-5.1", WINDOWS_POWERSHELL), ("pwsh-7", PWSH)) if path}
requires_powershell = unittest.skipUnless(bool(HOSTS), "No usable PowerShell host on PATH")
requires_ffmpeg = unittest.skipUnless(bool(FFMPEG and FFPROBE), "ffmpeg/ffprobe not available")

RECORDER = '''import json, sys
json.dump(sys.argv[1:], open(sys.argv[0] + ".argv.json", "w", encoding="utf-8"))
'''


class TheExecutableIsInvokedNotPrinted(unittest.TestCase):
    def test_a_quoted_executable_gets_the_call_operator(self):
        rendered = command_to_powershell([r"C:\Program Files\FFmpeg\ffmpeg.exe", "-version"])
        self.assertTrue(rendered.startswith("& '"), rendered)
        self.assertIn(r"C:\Program Files\FFmpeg\ffmpeg.exe", rendered)

    def test_a_bare_executable_name_needs_no_operator(self):
        self.assertEqual(command_to_powershell(["ffmpeg", "-version"]), "ffmpeg -version")

    def test_arguments_stay_individually_quoted(self):
        rendered = command_to_powershell(["ffmpeg", "-i", "my film.mkv", "out.mp4"])
        self.assertEqual(rendered, "ffmpeg -i 'my film.mkv' out.mp4")
        self.assertNotIn("Invoke-Expression", rendered)

    def test_the_whole_command_is_never_one_string(self):
        rendered = command_to_powershell([r"C:\Program Files\x\ffmpeg.exe", "-i", "a b.mkv"])
        self.assertEqual(rendered.count("& "), 1)
        self.assertTrue(rendered.endswith("'a b.mkv'"))

    def test_a_unicode_executable_path_is_quoted_and_invoked(self):
        rendered = command_to_powershell([r"C:\ابزار من\ffmpeg.exe", "-version"])
        self.assertTrue(rendered.startswith("& '"))

    def test_an_empty_command_renders_as_nothing(self):
        self.assertEqual(command_to_powershell([]), "")

    def test_an_apostrophe_is_doubled_inside_the_quotes(self):
        self.assertEqual(ps_quote("it's"), "'it''s'")


@requires_powershell
class GeneratedCommandsRunInARealShell(unittest.TestCase):
    """Every host on this machine executes the generated line for real."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_f12_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.recorder = self.root / "record args.py"
        self.recorder.write_text(RECORDER, encoding="utf-8")

    def run_in(self, host: str, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [HOSTS[host], "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", timeout=120)

    def record(self, host: str, arguments: list[str]) -> list[str]:
        target = self.recorder.with_suffix(".py.argv.json")
        target.unlink(missing_ok=True)
        command = command_to_powershell([sys.executable, str(self.recorder), *arguments])
        result = self.run_in(host, command + "; exit $LASTEXITCODE")
        self.assertEqual(result.returncode, 0, f"{command}\n{result.stderr}")
        return json.loads(target.read_text(encoding="utf-8"))

    def test_a_spaced_script_path_reaches_the_child_intact(self):
        for host in HOSTS:
            with self.subTest(host=host):
                self.assertEqual(self.record(host, ["plain"]), ["plain"])

    def test_awkward_argument_characters_survive(self):
        cases = ["a b", "it's", "$HOME", "back`tick", "hash#value",
                 "paren(s)", "semi;colon", "amp&ersand", "title=My Film",
                 "فیلم من", "C:\\Media\\clip#2.png"]
        for host in HOSTS:
            with self.subTest(host=host):
                self.assertEqual(self.record(host, cases), cases)

    def test_a_metadata_string_arrives_as_one_argument(self):
        for host in HOSTS:
            with self.subTest(host=host):
                self.assertEqual(self.record(host, ["-metadata", "title=My Film (2026)"]),
                                 ["-metadata", "title=My Film (2026)"])

    def test_a_nonzero_exit_code_propagates(self):
        for host in HOSTS:
            with self.subTest(host=host):
                command = command_to_powershell([sys.executable, "-c", "raise SystemExit(7)"])
                result = self.run_in(host, command + "; exit $LASTEXITCODE")
                self.assertEqual(result.returncode, 7)

    def test_a_zero_exit_code_propagates(self):
        for host in HOSTS:
            with self.subTest(host=host):
                command = command_to_powershell([sys.executable, "-c", "pass"])
                result = self.run_in(host, command + "; exit $LASTEXITCODE")
                self.assertEqual(result.returncode, 0)

    def test_an_empty_argument_is_documented_per_host(self):
        """PowerShell 5.1 drops `''` when calling a native program; 7 does not.

        Recorded rather than asserted away: FFmWiz never generates an empty
        argument, and pretending both hosts behave the same would be a lie in
        whichever direction it was written.
        """
        observed = {host: self.record(host, ["", "after"]) for host in HOSTS}
        for host, arguments in observed.items():
            with self.subTest(host=host):
                self.assertIn("after", arguments)
                if host == "pwsh-7":
                    self.assertEqual(arguments, ["", "after"])


@requires_powershell
@requires_ffmpeg
class RealFFmpegRunsFromASpacedPath(unittest.TestCase):
    """The exact failure: a quoted executable in command position."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_f12x_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def linked_ffmpeg(self) -> Path:
        """FFmpeg under a spaced, non-ASCII directory, hardlinked not copied."""
        real = Path(os.path.realpath(FFMPEG))
        spaced = Path(tempfile.gettempdir()) / "ffmwiz ps بررسی"
        if real.drive.lower() != spaced.drive.lower():
            spaced = real.parent.parent / "ffmwiz ps بررسی"
        spaced.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, spaced, ignore_errors=True)
        target = spaced / "ffmpeg.exe"
        if not target.exists():
            try:
                os.link(real, target)
            except OSError as exc:
                self.skipTest(f"cannot place ffmpeg under a spaced path: {exc}")
        return target

    def test_ffmpeg_under_a_spaced_unicode_path_actually_runs(self):
        ffmpeg = self.linked_ffmpeg()
        command = command_to_powershell([str(ffmpeg), "-hide_banner", "-version"])
        self.assertTrue(command.startswith("& '"), command)
        for host, executable in HOSTS.items():
            with self.subTest(host=host):
                result = subprocess.run(
                    [executable, "-NoLogo", "-NoProfile", "-NonInteractive",
                     "-ExecutionPolicy", "Bypass", "-Command", command],
                    capture_output=True, text=True, encoding="utf-8", timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("ffmpeg version", result.stdout,
                              "the shell printed the path instead of running it")

    def test_an_encode_with_awkward_metadata_round_trips(self):
        ffmpeg = self.linked_ffmpeg()
        out = self.root / "out put.mp4"
        title = "It's $HOME (2026) #1"
        command = command_to_powershell([
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-metadata", f"title={title}", str(out)])
        host = "pwsh-7" if "pwsh-7" in HOSTS else next(iter(HOSTS))
        result = subprocess.run(
            [HOSTS[host], "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", command + "; exit $LASTEXITCODE"],
            capture_output=True, text=True, encoding="utf-8", timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr)
        probe = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                                "format_tags=title", "-of", "default=nw=1:nk=1", str(out)],
                               capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(probe.stdout.strip(), title)


if __name__ == "__main__":
    unittest.main()
