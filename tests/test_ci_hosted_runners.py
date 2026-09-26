"""Every CI job runs on a GitHub-hosted runner.

The repository is public, so a job on a self-hosted runner would execute a
fork's pull request on that machine. The workflows therefore name hosted labels
literally: no `self-hosted` label and no `vars.` indirection that could switch a
job onto one without an edit to the file. The pip cache is on wherever
`setup-python` is used, because hosted machines start empty.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k ci_hosted_runners
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import FFmWiz

WORKFLOWS = Path(FFmWiz.__file__).resolve().parent / ".github" / "workflows"
HOSTED = re.compile(r"^(windows|ubuntu|macos)-(latest|\d[\d.]*)$")


def runs_on_values() -> list[tuple[str, str]]:
    found = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for value in re.findall(r"^\s*runs-on:\s*(.+?)\s*$", workflow.read_text(encoding="utf-8"), re.M):
            found.append((workflow.name, value))
    return found


class EveryJobIsHosted(unittest.TestCase):
    def test_the_workflows_declare_jobs(self):
        self.assertTrue(runs_on_values(), "no runs-on found; the parser no longer matches the workflows")

    def test_every_runs_on_is_a_literal_hosted_label(self):
        for name, value in runs_on_values():
            with self.subTest(workflow=name, runs_on=value):
                self.assertRegex(value, HOSTED)

    def test_the_parser_rejects_the_switches_it_guards_against(self):
        for value in ("self-hosted", "[self-hosted, windows]", "${{ vars.CI_RUNNER || 'windows-latest' }}"):
            with self.subTest(runs_on=value):
                self.assertNotRegex(value, HOSTED)


class ThePipCacheIsOn(unittest.TestCase):
    def test_every_setup_python_with_a_cache_uses_pip(self):
        text = (WORKFLOWS / "python-smoke.yml").read_text(encoding="utf-8")
        caches = re.findall(r"^\s*cache:\s*(.+?)\s*$", text, re.M)
        self.assertEqual(len(caches), 2, "the tests and gui-import jobs each configure the cache")
        self.assertEqual(set(caches), {"pip"})


if __name__ == "__main__":
    unittest.main()
