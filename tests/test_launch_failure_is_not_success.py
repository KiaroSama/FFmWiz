"""A command that never started must not read as a successful run.

`muxcleanup.media.run_with_progress` cannot raise when `Popen` fails -- its
callers expect a `CompletedProcess` -- so it synthesises one. The code it puts
in that object used to be 1, and robocopy's convention (which
`output.robocopy_success` implements faithfully) is that **0-7 all mean
success**. The two effects, both silent:

  * `copying.py`'s extra-files pass logged "Extra files copied with robocopy:
    ... rc=1" having copied nothing, and never printed its failure message;
  * `copying.py`'s per-file pass skipped `raise OSError("robocopy failed ...")`
    and died a few lines later on "robocopy did not create the output file" --
    true, but it names the wrong cause, which is what a user would report.

These tests pin the RELATIONSHIP, not the number: whatever sentinel the runner
uses, `robocopy_success` must reject it, and every consumer must see failure.
Hard-coding 127 here would pass just as well with the bug reintroduced under a
different literal.
"""
import subprocess
import unittest
from unittest import mock

import FFmWiz  # noqa: F401  (installs the package path the way every suite does)
from ffmwiz.muxcleanup import media
from ffmwiz.muxcleanup.output import robocopy_success


class ALaunchFailureIsNeverSuccess(unittest.TestCase):
    def _launch_failure(self):
        """Drive run_with_progress down its `Popen` raises OSError path."""
        with mock.patch.object(media.subprocess, "Popen",
                               side_effect=OSError("no such executable")):
            return media.run_with_progress(["definitely-not-a-real-binary"])

    def test_the_runner_reports_a_failure_code(self):
        proc = self._launch_failure()
        self.assertIsInstance(proc, subprocess.CompletedProcess)
        self.assertNotEqual(0, proc.returncode,
                            "a command that never started cannot be a success")

    def test_robocopy_does_not_read_the_sentinel_as_success(self):
        proc = self._launch_failure()
        self.assertFalse(
            robocopy_success(proc.returncode),
            "robocopy_success() accepts 0-7; a launch-failure sentinel inside "
            f"that range makes a failed launch look clean (got {proc.returncode})")

    def test_the_sentinel_survives_the_capture_failure_path_too(self):
        # The second synthesised CompletedProcess, for when the temp capture
        # files cannot be opened. It had the same value and the same problem.
        with mock.patch.object(media.tempfile, "TemporaryFile",
                               side_effect=OSError("no temp space")):
            proc = media.run_with_progress(["anything"])
        self.assertFalse(robocopy_success(proc.returncode))

    def test_every_consumer_of_the_code_sees_a_failure(self):
        # logsetup and processing both test `returncode != 0` / `== 0`; this
        # states that contract so a future sentinel cannot satisfy one and not
        # the others.
        code = self._launch_failure().returncode
        self.assertNotEqual(0, code)
        self.assertFalse(robocopy_success(code))
        self.assertGreater(code, 7, "must sit above robocopy's success band")


if __name__ == "__main__":
    unittest.main()
