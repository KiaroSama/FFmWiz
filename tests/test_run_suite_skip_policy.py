"""Guard the guard: the runner's skip policy must fail on a missing dependency.

`--strict-skips` classified skips with one fuzzy alternation:

    re.compile(r"ffmpeg|ffprobe|numpy|powershell", re.I)

PySide6, QtQml and QtQuick appear nowhere in it. The CI job that installs
PySide6 specifically to run the QML behaviour suites therefore reported success
with those classes skipped:

    python tests/run_suite.py --strict-skips -j 2 -k gui_editors -k qml_waveform ...
    Ran 140 tests in 2.18s across 2 worker(s)
    OK (skipped=3)
    exit code 0

All three skips were missing PySide6 QtQml/QtQuick classes -- a false green for
the exact dependency the flag was added to enforce (B17).

The runner now names capabilities explicitly and a job declares which ones IT
provides with `--require`. These tests drive that classification directly,
because a runner guard that is only exercised by the runs it guards cannot fail
in the way that matters.
"""
import unittest

import run_suite


class SkipClassification(unittest.TestCase):
    def test_a_pyside6_skip_is_attributed(self):
        for reason in (
            "PySide6 not installed",
            "QtQml is unavailable",
            "QtQuick could not be imported",
            "qml scene requires PySide6",
        ):
            with self.subTest(reason=reason):
                self.assertEqual("pyside6", run_suite.classify_skip(reason))

    def test_the_previously_known_capabilities_still_work(self):
        self.assertEqual("ffmpeg", run_suite.classify_skip("ffmpeg/ffprobe not on PATH"))
        self.assertEqual("numpy", run_suite.classify_skip("numpy required for waveform math"))
        self.assertEqual("powershell", run_suite.classify_skip("powershell not available"))
        self.assertEqual("wheel", run_suite.classify_skip("setuptools/wheel not installed"))

    def test_hardware_and_privilege_skips_are_not_a_missing_install(self):
        # These must stay legitimate on any runner; blaming them on the job's
        # own dependencies would make the guard unusable and get it switched off.
        for reason in (
            "no usable NVIDIA CUDA/hevc_nvenc hardware here (probe encode failed)",
            "symlink privilege required",
            "no NVENC hardware",
        ):
            with self.subTest(reason=reason):
                self.assertEqual("", run_suite.classify_skip(reason))

    def test_an_nvenc_skip_is_not_blamed_on_ffmpeg(self):
        # The old guard special-cased the substring "nvenc" AFTER matching
        # "ffmpeg", which is the sort of ordering that silently rots.
        reason = "no usable NVIDIA CUDA/hevc_nvenc hardware; the ffmpeg chain was NOT executed"
        self.assertEqual("", run_suite.classify_skip(reason))

    def test_an_unrelated_skip_is_not_attributed(self):
        self.assertEqual("", run_suite.classify_skip("nothing to compare against"))


class TheGuardIsWiredToTheSameNames(unittest.TestCase):
    def test_every_declared_capability_can_be_required(self):
        # `--require` validates against this map, so a capability that exists
        # only in the docs would be rejected at the command line.
        for name in ("ffmpeg", "numpy", "powershell", "pyside6", "wheel"):
            self.assertIn(name, run_suite.CAPABILITY_PATTERNS)

    def test_the_qml_capability_covers_the_names_tests_actually_use(self):
        pattern = run_suite.CAPABILITY_PATTERNS["pyside6"]
        for token in ("PySide6", "QtQml", "QtQuick", "QML"):
            self.assertTrue(pattern.search(token), f"{token} is not recognised")

    def test_environment_skips_are_checked_before_capabilities(self):
        # Order matters: an environmental reason that happens to mention a
        # capability must stay environmental.
        self.assertTrue(run_suite.ENVIRONMENT_SKIP_RE.search(
            "no usable NVIDIA CUDA/hevc_nvenc hardware"))


if __name__ == "__main__":
    unittest.main()
