"""Shared test helper: no test may leave an `ffmwiz_*` temp directory behind.

`build_ffmpeg_command()` creates its scratch directories eagerly and hands
ownership to the job's `ArtifactLease`. In production `run_one_job()` always
releases that lease in a `finally`, but a test that calls the builder directly
and then discards `answers` releases nothing -- one suite run left 35 orphaned
directories in %TEMP% (F13).

The guard points `tempfile.tempdir` at a PRIVATE root for the duration of each
test. The shared %TEMP% cannot be used as the yardstick: the suite runs several
workers at once, so a sibling test's directory shows up as this test's leak.
That false positive is not hypothetical -- it is what the first version of this
file did.

Usage -- put the mixin BEFORE `unittest.TestCase` in the bases:

    class Something(NoLeakedArtifacts, unittest.TestCase):
        def setUp(self):
            super().setUp()
            ...

        def test_x(self):
            answers = self.own({...})      # released during cleanup
            FFmWiz.build_ffmpeg_command(answers)

Anything the builder leases and the test fails to release is reported by name.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import FFmWiz

PREFIX = "ffmwiz_"


class NoLeakedArtifacts:
    """Mixin for `unittest.TestCase`. Add BEFORE TestCase in the bases."""

    def setUp(self):  # noqa: N802 - unittest naming
        super().setUp()
        self._artifact_root = Path(tempfile.mkdtemp(prefix="ffmwiz_guard_"))
        self._artifact_previous_tempdir = tempfile.tempdir
        tempfile.tempdir = str(self._artifact_root)
        self._owned_answers: list[dict] = []
        self.addCleanup(self._release_and_check)

    def own(self, answers: dict) -> dict:
        """Release this answers dict's lease when the test finishes."""
        self._owned_answers.append(answers)
        return answers

    def _release_and_check(self) -> None:
        tempfile.tempdir = self._artifact_previous_tempdir
        for answers in self._owned_answers:
            try:
                FFmWiz.release_artifacts(answers)
            except Exception:  # pragma: no cover - cleanup must not mask a failure
                pass
        leaked = sorted(
            str(entry) for entry in self._artifact_root.glob(PREFIX + "*"))
        shutil.rmtree(self._artifact_root, ignore_errors=True)
        if leaked:
            raise AssertionError(
                "test left temporary artifacts behind; call self.own(answers) "
                "on every dict passed to a builder:\n  " + "\n  ".join(leaked))
