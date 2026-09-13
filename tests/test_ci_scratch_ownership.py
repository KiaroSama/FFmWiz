"""R01: CI cleanup may delete only what the job owns.

The cleanup step used to enumerate `$env:TEMP` for `ffmwiz_*` and recursively
delete every match. On the self-hosted runner `%TEMP%` is the DESKTOP USER's
temp directory -- the same one the user's own editor sessions, generated
chapter/subtitle/concat inputs and retained manual-command dependencies live in.
A name prefix was being treated as proof of ownership; it is not.

These tests extract the real cleanup script out of the workflow and execute it
in native PowerShell against sentinel directories, then compare hashes. A
foreign `ffmwiz_*` directory that survives is the whole point.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k ci_scratch_ownership
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

WORKFLOW = Path(FFmWiz.__file__).resolve().parent / ".github" / "workflows" / "python-smoke.yml"

PWSH = shutil.which("pwsh") or shutil.which("powershell")
requires_powershell = unittest.skipUnless(bool(PWSH), "No usable PowerShell host on PATH")


def cleanup_scripts() -> list[str]:
    """The `run:` body of every "Remove this job's scratch files" step."""
    text = WORKFLOW.read_text(encoding="utf-8")
    blocks = []
    for match in re.finditer(r"- name: Remove this job's scratch files\n(.*?)(?=\n      - name: |\n  \w|\Z)",
                             text, re.S):
        body = match.group(1)
        run = re.search(r"run: \|\n(.*)", body, re.S)
        if not run:
            continue
        lines = [line[10:] if line.startswith(" " * 10) else line
                 for line in run.group(1).splitlines()]
        blocks.append("\n".join(lines).rstrip())
    assert blocks, "no cleanup step found in the workflow"
    return blocks


def tree_hash(root: Path) -> str:
    """A stable digest of every file under `root`, so any loss is visible."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


class TheWorkflowNeverSweepsSharedTemp(unittest.TestCase):
    """The defect, stated as a property of the file itself."""

    def test_no_step_enumerates_the_shared_temp_directory(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("Get-ChildItem $env:TEMP", text,
                         "a global %TEMP% sweep is back; a name prefix is not ownership")
        self.assertNotIn('-Filter "ffmwiz_*"', text)

    def test_every_job_that_runs_tests_owns_its_scratch(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(text.count("Create a job-owned scratch directory"), 2)
        # TEMP, TMP and TMPDIR are all redirected: Python, pip and bash each
        # read a different one, and a single miss puts files back in the
        # shared directory this defect is about.
        for variable in ("TEMP=$scratch", "TMP=$scratch", "TMPDIR=$scratch"):
            with self.subTest(variable=variable):
                self.assertEqual(text.count(variable), 2)

    def test_the_cleanup_validates_containment_and_reparse_points(self):
        for script in cleanup_scripts():
            with self.subTest(script=script[:40]):
                # Containment is tested against the root PLUS a separator, so a
                # sibling that merely shares the prefix is outside; the root
                # itself is rejected outright. The behaviour is proved below --
                # this states the shape so the weaker `StartsWith($root` form
                # cannot come back unnoticed.
                self.assertIn("StartsWith($prefix", script)
                self.assertNotIn("StartsWith($root", script)
                self.assertIn("-eq $root", script)
                self.assertIn("ReparsePoint", script)

    def test_no_step_sweeps_runner_temp_by_name(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn('Join-Path $env:RUNNER_TEMP "ffmpeg-*"', text,
                         "a name sweep of RUNNER_TEMP is back; it deletes the "
                         "ffmpeg build a concurrent job is using")


@requires_powershell
class TheCleanupDeletesOnlyOwnedPaths(unittest.TestCase):
    """The real script, real PowerShell, real sentinels."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_r01_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        # The runner's own temp, and the SHARED desktop temp beside it.
        self.runner_temp = self.root / "runner temp"
        self.shared_temp = self.root / "shared temp"
        self.runner_temp.mkdir()
        self.shared_temp.mkdir()

        # Sentinels a prefix sweep would have destroyed.
        self.foreign = []
        for name in ("ffmwiz_editor_session", "ffmwiz_manual_command_inputs",
                     "ffmwiz_دیگر_session", "ffmwiz_other job"):
            directory = self.shared_temp / name
            (directory / "nested").mkdir(parents=True)
            (directory / "nested" / "keep.txt").write_text(name, encoding="utf-8")
            self.foreign.append(directory)
        self.foreign_hashes = {d: tree_hash(d) for d in self.foreign}

        # What this job genuinely owns.
        self.scratch = self.runner_temp / "ffmwiz-scratch-42-tests-3.13-latest"
        (self.scratch / "ffmwiz_owned_run").mkdir(parents=True)
        (self.scratch / "ffmwiz_owned_run" / "artifact.bin").write_bytes(b"owned")
        # The job's own ffmpeg build lives INSIDE its scratch now. It used to sit
        # beside it in RUNNER_TEMP and be swept by the name `ffmpeg-*`, which
        # deleted the build a concurrent job on the same runner was using -- the
        # R01 mistake one directory down. Owned means owned by path, not by name.
        self.ffmpeg_tree = self.scratch / "ffmpeg-7.1.1"
        self.ffmpeg_tree.mkdir(parents=True)
        (self.ffmpeg_tree / "ffmpeg.exe").write_bytes(b"binary")

    def run_cleanup(self, script: str, scratch: Path | None = None,
                    runner_temp: Path | None = None) -> subprocess.CompletedProcess:
        preamble = (
            f'$env:RUNNER_TEMP = "{runner_temp or self.runner_temp}"\n'
            f'$env:FFMWIZ_SCRATCH = "{scratch if scratch is not None else self.scratch}"\n'
            f'$env:TEMP = "{self.shared_temp}"\n'
            f'$env:TMP = "{self.shared_temp}"\n'
        )
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-Command", preamble + script],
            capture_output=True, text=True, encoding="utf-8", timeout=180)

    def assert_foreign_untouched(self) -> None:
        for directory, digest in self.foreign_hashes.items():
            self.assertTrue(directory.is_dir(), f"{directory.name} was deleted")
            self.assertEqual(tree_hash(directory), digest,
                             f"{directory.name} was modified")

    def test_the_owned_scratch_goes_and_every_foreign_sentinel_stays(self):
        for index, script in enumerate(cleanup_scripts()):
            with self.subTest(step=index):
                self.setUp()
                result = self.run_cleanup(script)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(self.scratch.exists(), "the job's own scratch survived")
                self.assertFalse(self.ffmpeg_tree.exists(), "the job's ffmpeg tree survived")
                self.assert_foreign_untouched()

    def test_a_second_job_scratch_is_left_alone(self):
        other = self.runner_temp / "ffmwiz-scratch-99-gui-3.13"
        other.mkdir()
        (other / "still running.txt").write_text("other job", encoding="utf-8")
        result = self.run_cleanup(cleanup_scripts()[0])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(other.is_dir(), "another job's scratch was deleted")
        self.assertFalse(self.scratch.exists())

    def test_a_scratch_outside_runner_temp_is_refused(self):
        outside = self.shared_temp / "ffmwiz_not_ours"
        outside.mkdir()
        (outside / "keep.txt").write_text("x", encoding="utf-8")
        result = self.run_cleanup(cleanup_scripts()[0], scratch=outside)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(outside.is_dir(), "a path outside RUNNER_TEMP was deleted")
        self.assertIn("refusing to clean outside", result.stdout)

    def test_a_reparse_point_is_not_followed(self):
        if os.name != "nt":
            self.skipTest("directory junctions are a Windows feature")
        victim = self.shared_temp / "ffmwiz_precious"
        victim.mkdir()
        (victim / "keep.txt").write_text("precious", encoding="utf-8")
        junction = self.runner_temp / "ffmwiz-scratch-42-tests-junction"
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(victim)],
                              capture_output=True, text=True, timeout=120)
        if made.returncode != 0:
            self.skipTest(f"could not create a junction: {made.stderr or made.stdout}")
        result = self.run_cleanup(cleanup_scripts()[0], scratch=junction)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((victim / "keep.txt").is_file(),
                        "the cleanup followed a junction out of the owned area")
        self.assertIn("refusing to follow a reparse point", result.stdout)

    def test_an_absent_scratch_is_not_an_error(self):
        shutil.rmtree(self.scratch)
        result = self.run_cleanup(cleanup_scripts()[0])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_foreign_untouched()

    def test_an_empty_scratch_variable_deletes_nothing_shared(self):
        """A cancelled job never reaches the scratch step; the variable is unset."""
        result = self.run_cleanup(cleanup_scripts()[0], scratch=Path(""))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_foreign_untouched()


if __name__ == "__main__":
    unittest.main()


@requires_powershell
class TheCleanupProvesOwnershipByPathComponent(TheCleanupDeletesOnlyOwnedPaths):
    """A01/CI hardening: `StartsWith` is not containment, and a prefix is not
    ownership even inside RUNNER_TEMP.

    `C:\runner temp2` starts with `C:\runner temp`, so a string prefix test
    calls a SIBLING directory contained. And `ffmpeg-*` swept by name deletes
    the build another concurrent job on the same runner is using -- the same
    mistake R01 fixed for the shared %TEMP%, one directory down.
    """

    def test_a_sibling_sharing_the_prefix_is_not_inside(self):
        sibling = self.root / "runner temp2"
        (sibling / "another job").mkdir(parents=True)
        (sibling / "another job" / "keep.bin").write_bytes(b"not ours")
        before = tree_hash(sibling)
        for script in cleanup_scripts():
            with self.subTest(script=script[:40]):
                result = self.run_cleanup(script, scratch=sibling / "another job")
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(before, tree_hash(sibling),
                                 "a prefix match was treated as containment")
                self.assertIn("refusing", (result.stdout + result.stderr).lower())

    def test_runner_temp_itself_is_never_the_target(self):
        before = tree_hash(self.runner_temp)
        for script in cleanup_scripts():
            with self.subTest(script=script[:40]):
                result = self.run_cleanup(script, scratch=self.runner_temp)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertTrue(self.runner_temp.exists(),
                                "the cleanup deleted RUNNER_TEMP itself")
                self.assertEqual(before, tree_hash(self.runner_temp))

    def test_another_jobs_ffmpeg_build_survives(self):
        foreign = self.runner_temp / "ffmpeg-6.1.1"
        foreign.mkdir()
        (foreign / "ffmpeg.exe").write_bytes(b"another job is using this")
        before = tree_hash(foreign)
        for script in cleanup_scripts():
            with self.subTest(script=script[:40]):
                self.run_cleanup(script)
                self.assertEqual(before, tree_hash(foreign),
                                 "a concurrent job's ffmpeg build was swept by name")

    def test_cleaned_is_printed_only_for_a_path_that_is_gone(self):
        for script in cleanup_scripts():
            with self.subTest(script=script[:40]):
                result = self.run_cleanup(script)
                for line in result.stdout.splitlines():
                    if line.startswith("cleaned: "):
                        gone = Path(line[len("cleaned: "):].strip())
                        self.assertFalse(gone.exists(),
                                         f"reported clean but {gone} is still there")
