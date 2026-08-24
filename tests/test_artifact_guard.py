"""Guard the guard: `NoLeakedArtifacts` must actually fail on a leak (F13).

A cleanup assertion that cannot fail is worse than none -- it reads as proof.
One suite run left 35 orphaned `ffmwiz_*` directories in %TEMP% while every
test reported OK, which is exactly what this mixin exists to stop.

It also pins the reason the mixin uses a PRIVATE temp root instead of watching
the shared %TEMP%: the suite runs several workers at once, so a sibling test's
directory otherwise shows up as this test's leak. The first version of the
mixin did watch %TEMP% and failed three unrelated tests that way.
"""
import tempfile
import unittest
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts


class TheMixinCatchesALeak(unittest.TestCase):
    """Run the mixin's own lifecycle by hand and inspect the verdict."""

    def _run_case(self, body):
        outcome = {}

        class Case(NoLeakedArtifacts, unittest.TestCase):
            def runTest(inner):  # noqa: N805 - unittest naming
                body(inner)

        case = Case()
        result = case.run()
        outcome["errors"] = result.errors + result.failures
        return outcome

    def test_an_unreleased_lease_fails_the_test(self):
        def body(case):
            answers = {}
            FFmWiz.artifact_lease(answers).register(
                Path(tempfile.mkdtemp(prefix="ffmwiz_leaky_")))

        outcome = self._run_case(body)
        self.assertTrue(outcome["errors"], "a real leak was reported as a pass")
        self.assertIn("temporary artifacts behind", str(outcome["errors"]))

    def test_owning_the_answers_makes_it_pass(self):
        def body(case):
            answers = case.own({})
            FFmWiz.artifact_lease(answers).register(
                Path(tempfile.mkdtemp(prefix="ffmwiz_owned_")))

        self.assertEqual([], self._run_case(body)["errors"])

    def test_a_test_that_creates_nothing_passes(self):
        self.assertEqual([], self._run_case(lambda case: None)["errors"])

    def test_the_private_root_is_restored_afterwards(self):
        before = tempfile.tempdir
        self._run_case(lambda case: None)
        self.assertEqual(before, tempfile.tempdir,
                         "tempfile.tempdir must not stay redirected")

    def test_a_sibling_worker_directory_is_not_blamed_on_this_test(self):
        # The false positive the private root exists to prevent: another
        # worker's directory appearing in the SHARED temp while this test runs.
        # The first version of the mixin watched %TEMP% and failed three
        # unrelated tests exactly this way.
        sibling = {}

        def body(case):
            shared = case._artifact_previous_tempdir or tempfile.gettempdir()
            sibling["path"] = Path(tempfile.mkdtemp(prefix="ffmwiz_sibling_",
                                                    dir=shared))

        try:
            self.assertEqual([], self._run_case(body)["errors"],
                             "a sibling worker's directory was blamed on this test")
        finally:
            if sibling.get("path"):
                sibling["path"].rmdir()


class TheBuilderIsCoveredByIt(unittest.TestCase):
    """The leak this was written for: a direct builder call in a test."""

    def test_a_discarded_builder_dict_is_reported(self):
        outcome = []

        class Case(NoLeakedArtifacts, unittest.TestCase):
            def runTest(inner):  # noqa: N805
                answers = {}
                # What the leaking tests did: lease a scratch directory the way
                # build_ffmpeg_command does, then walk away from `answers`.
                FFmWiz.artifact_lease(answers).register(
                    Path(tempfile.mkdtemp(prefix="ffmwiz_encode_chapters_")))

        result = Case().run()
        outcome.extend(result.errors + result.failures)
        self.assertTrue(outcome)
        self.assertIn("ffmwiz_encode_chapters_", str(outcome))


if __name__ == "__main__":
    unittest.main()
