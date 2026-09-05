"""Regression tests for the built distribution.

`pyproject.toml` declared only `py-modules = ["FFmWiz"]`, so the wheel contained
six entries: the thin launcher plus dist-info. The entire `ffmwiz` package, all
41 asset files and the QML scene were missing, and an installed wheel died on
`import FFmWiz` with `ModuleNotFoundError: No module named 'ffmwiz'`. The
published artifact was not asset-poor -- it was non-functional.

The static checks below are the cheap guard. The build test is the only thing
that actually proves the artifact works, so it runs for real; it is skipped only
when the build backend genuinely is not installed.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import FFmWiz

PROJECT_ROOT = Path(FFmWiz.__file__).resolve().parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    tomllib = None


def _setuptools_available():
    try:
        import setuptools  # noqa: F401
        import wheel  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


@unittest.skipIf(tomllib is None, "tomllib needs Python 3.11+")
class PyprojectDeclaration(unittest.TestCase):
    """Static guard: the declarations whose absence emptied the wheel."""

    @classmethod
    def setUpClass(cls):
        cls.config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    def test_the_package_itself_is_discovered(self):
        find = self.config["tool"]["setuptools"].get("packages", {}).get("find")
        self.assertIsNotNone(find, "without packages.find the wheel ships no ffmwiz package")
        self.assertIn("ffmwiz*", find.get("include", []))

    def test_namespace_discovery_stays_on(self):
        # ffmwiz/gui and gui/modern/qml have no __init__.py. Turning namespaces
        # off, or listing packages explicitly, silently drops the whole GUI.
        find = self.config["tool"]["setuptools"]["packages"]["find"]
        self.assertNotEqual(find.get("namespaces", True), False)

    def test_runtime_assets_are_declared_as_package_data(self):
        package_data = self.config["tool"]["setuptools"].get("package-data", {})
        patterns = package_data.get("ffmwiz", [])
        self.assertTrue(any("assets" in p for p in patterns), "icons/cursors must ship")
        self.assertTrue(any("qml" in p for p in patterns), "the QML scene must ship")
        self.assertTrue(self.config["tool"]["setuptools"].get("include-package-data"))

    def test_the_build_tree_is_staged_where_it_cannot_shadow_a_module(self):
        """`build/` is a valid identifier and the cwd leads sys.path.

        So setuptools' default staging directory is importable as a
        namespace package, and `python -m build` then blames the tool
        instead of reporting that the build package is not installed. It
        also made a stale staging tree look like ordinary source.
        """
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(PROJECT_ROOT / "setup.cfg", encoding="utf-8")
        base = cfg.get("build", "build_base", fallback="")
        self.assertTrue(base.startswith("."),
                        f"build_base={base!r} can be imported as a module name")

    def test_the_thin_launcher_is_still_a_top_level_module(self):
        self.assertIn("FFmWiz", self.config["tool"]["setuptools"]["py-modules"])

    def test_no_dead_tool_tables(self):
        # [tool.py_compile_check] was read by nothing.
        self.assertNotIn("py_compile_check", self.config.get("tool", {}))

    def test_the_pyside6_pin_is_the_same_in_both_files(self):
        # CI installs Qt from requirements.txt; `pip install .` installs the pin
        # declared here instead. Nothing else compares them, so editing one lets
        # CI silently test a different Qt than the one a user gets.
        requirements = PROJECT_ROOT / "requirements.txt"
        lines = [
            line.strip()
            for line in requirements.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        requirements_pins = [line for line in lines if line.lower().startswith("pyside6")]
        pyproject_pins = [
            dep.strip() for dep in self.config["project"]["dependencies"]
            if dep.strip().lower().startswith("pyside6")
        ]
        self.assertTrue(requirements_pins, "requirements.txt no longer pins PySide6")
        self.assertTrue(pyproject_pins, "pyproject.toml no longer pins PySide6")
        self.assertEqual(
            requirements_pins[0], pyproject_pins[0],
            "requirements.txt and pyproject.toml pin different PySide6 versions -- "
            "edit both to match. CI installs requirements.txt "
            "(.github/workflows/python-smoke.yml), so a drift means CI tests a "
            "different Qt than `pip install .` gives a user, and nothing reports it.",
        )


class PySide6PinAgreement(unittest.TestCase):
    """Every file that names a PySide6 version must name the same one.

    Four files pin it. `requirements.txt` and `pyproject.toml` are the
    manifests. The other two are the FALLBACKS taken when requirements.txt is
    not on disk -- which is exactly the installed-wheel case, because the wheel
    does not ship it: `runtime_pyside6.py` installs `PYSIDE6_PIP_SPEC` and
    `install-command.ps1` installs its own literal. Both had drifted to 6.11.1
    while the manifests moved to 6.11.2, so a wheel user who accepted the
    offered install got a Qt the project never tested, silently.

    The older guard compared the two manifests only, which is why it stayed
    green through that drift.
    """

    PIN_FILES = (
        "requirements.txt",
        "pyproject.toml",
        "install-command.ps1",
        "ffmwiz/core/constants.py",
    )

    def test_every_pyside6_pin_agrees(self):
        found = {}
        for name in self.PIN_FILES:
            # utf-8-sig: the PowerShell installer may carry a BOM.
            text = (PROJECT_ROOT / name).read_text(encoding="utf-8-sig")
            pins = sorted(set(re.findall(r"PySide6==[0-9][0-9A-Za-z.-]*", text)))
            self.assertTrue(
                pins,
                f"{name} no longer pins PySide6 -- either the pin moved (update "
                f"PIN_FILES) or a fallback lost its version and now installs "
                f"whatever is newest.")
            found[name] = pins
        distinct = {pin for pins in found.values() for pin in pins}
        self.assertEqual(
            len(distinct), 1,
            "PySide6 pins disagree across the repo: "
            + "; ".join(f"{name}={','.join(pins)}" for name, pins in found.items()))


class WheelProofIsNotSkippableInCi(unittest.TestCase):
    """The CI job that runs the whole suite must be unable to skip the build test.

    `WheelContents` below self-skips when the build backend is absent, and since
    Python 3.12 `ensurepip` installs neither setuptools nor wheel -- verified by
    creating a 3.13 venv, where both import as ModuleNotFoundError. The workflow
    installed only numpy and required only ffmpeg/numpy, so the sole proof that
    the artifact still contains the `ffmwiz` package skipped silently while the
    job reported OK. That is the exact false green `--require` exists to stop.
    """

    WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "python-smoke.yml"
    JOB_HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):[ \t]*$")
    FILTERED = re.compile(r"run_suite\.py[^\n]*\s-k\s")

    @classmethod
    def _job_blocks(cls, text):
        """{job name: its yaml body}, split on the two-space `name:` headers."""
        jobs, name, lines = {}, None, []
        for line in text.splitlines():
            header = cls.JOB_HEADER.match(line)
            if header:
                if name:
                    jobs[name] = "\n".join(lines)
                name, lines = header.group(1), []
            elif name is not None:
                lines.append(line)
        if name:
            jobs[name] = "\n".join(lines)
        return jobs

    def test_the_unfiltered_suite_job_installs_and_requires_the_build_backend(self):
        jobs = self._job_blocks(self.WORKFLOW.read_text(encoding="utf-8"))
        runners = {name: body for name, body in jobs.items() if "run_suite.py" in body}
        self.assertTrue(runners, "no CI job runs tests/run_suite.py any more")
        unfiltered = {name: body for name, body in runners.items()
                      if not self.FILTERED.search(body)}
        self.assertTrue(
            unfiltered,
            "every CI job now runs a -k filtered subset, so nothing runs the whole "
            "suite and a module can drop out unnoticed")
        for name, body in unfiltered.items():
            with self.subTest(job=name):
                # assertTrue, not assertIn: assertIn echoes the whole job body.
                self.assertTrue(
                    "--require wheel" in body,
                    f"job `{name}` runs the whole suite but does not require the "
                    f"wheel capability, so WheelContents can skip and still report OK")
                installs = " ".join(line for line in body.splitlines()
                                    if "pip install" in line and "requirements.txt" not in line)
                for package in ("setuptools", "wheel"):
                    self.assertTrue(
                        package in installs,
                        f"job `{name}` requires the wheel capability but never installs "
                        f"`{package}`, so the build test would fail the job instead of "
                        f"proving anything")


@unittest.skipIf(not _setuptools_available(), "setuptools/wheel not installed")
class WheelContents(unittest.TestCase):
    """Build the real artifact and look inside it."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="ffmwiz_wheel_test_")
        # setuptools writes build/ and *.egg-info NEXT TO THE SOURCE, so record
        # what already existed and remove only what this test created. Leaving
        # them behind would put build residue in the working tree on every run.
        cls._preexisting = {
            path for path in (PROJECT_ROOT / ".build", PROJECT_ROOT / "build",
                              *PROJECT_ROOT.glob("*.egg-info"))
            if path.exists()
        }
        # Clean the staging tree BEFORE building, not only after.
        # setuptools reuses it incrementally, so a tree left by an earlier
        # build answers for files the current source no longer has -- which
        # is exactly how a deleted QML path kept appearing in wheels. This
        # test may only report on the source it was handed.
        for path in (PROJECT_ROOT / ".build", PROJECT_ROOT / "build"):
            shutil.rmtree(path, ignore_errors=True)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", str(PROJECT_ROOT),
             "--no-deps", "-w", cls._tmp, "--no-build-isolation", "-q"],
            capture_output=True, text=True, timeout=900)
        if result.returncode != 0:
            cls._clean()
            raise unittest.SkipTest(f"wheel build unavailable: {result.stderr.strip()[:300]}")
        wheels = glob.glob(os.path.join(cls._tmp, "*.whl"))
        cls._names = zipfile.ZipFile(wheels[0]).namelist()

    @classmethod
    def _clean(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)
        for path in (PROJECT_ROOT / ".build", PROJECT_ROOT / "build",
                     *PROJECT_ROOT.glob("*.egg-info")):
            if path.exists() and path not in cls._preexisting:
                shutil.rmtree(path, ignore_errors=True)

    @classmethod
    def tearDownClass(cls):
        cls._clean()

    def test_wheel_contains_the_package_modules(self):
        modules = [n for n in self._names if n.startswith("ffmwiz/") and n.endswith(".py")]
        self.assertGreater(len(modules), 100, "the ffmwiz package is missing from the wheel")

    def test_wheel_contains_the_gui_subpackage(self):
        self.assertTrue(any(n.startswith("ffmwiz/gui/") for n in self._names))

    def test_wheel_contains_the_runtime_assets(self):
        self.assertIn("ffmwiz/assets/icons/ffmwiz_app.ico", self._names)
        self.assertIn("ffmwiz/assets/icons/ffmwiz_app.png", self._names)
        self.assertTrue(any("assets/cursors/" in n for n in self._names))

    def test_wheel_ships_every_qml_file_the_source_has(self):
        """Compared against the SOURCE, because one hardcoded path lied.

        This used to assert `ffmwiz/gui/qml/UnifiedEditor.qml` and passed
        while the live scene shipped nowhere: the editor had moved to
        `gui/modern/qml/`, the package-data glob still named the old
        directory, and a stale `build/lib` from before that move still held
        a file at the old path -- setuptools reuses that tree, so the wheel
        carried the PRE-SPLIT 102 KB scene (the live one is 37 KB) and the
        assertion was satisfied by an artefact months out of date.

        Naming a path can only prove that SOMETHING sits there. Counting
        the source is what proves the right things shipped.
        """
        source = {"ffmwiz/" + str(path.relative_to(PROJECT_ROOT / "ffmwiz")).replace("\\", "/")
                  for path in (PROJECT_ROOT / "ffmwiz").rglob("*.qml")}
        self.assertTrue(source, "no .qml in the source; this guard is watching nothing")
        missing = sorted(source - set(self._names))
        self.assertEqual([], missing,
                         f"{len(missing)} of {len(source)} QML files are not in the wheel")

    def test_the_wheel_carries_no_qml_the_source_no_longer_has(self):
        # The other direction: a stale staging tree adds files that were
        # deleted, which is how the defect above stayed invisible.
        source = {"ffmwiz/" + str(path.relative_to(PROJECT_ROOT / "ffmwiz")).replace("\\", "/")
                  for path in (PROJECT_ROOT / "ffmwiz").rglob("*.qml")}
        stale = sorted(n for n in self._names if n.endswith(".qml") and n not in source)
        self.assertEqual([], stale,
                         "the wheel carries QML the source does not have -- "
                         "almost certainly a stale build tree being reused")

    def test_wheel_contains_the_thin_launcher(self):
        self.assertIn("FFmWiz.py", self._names)


class TheWorkflowOnlyNamesFilesThatExist(unittest.TestCase):
    """A path in a workflow is a claim nothing verifies until CI runs.

    The classic/modern GUI split left `.github/workflows/python-smoke.yml`
    pointing at `ffmwiz/gui/ffmwiz_gui.py` and `.../ffmwiz_gui_qml.py`, which
    no longer exist. The compile job stayed GREEN on them: measured, both
    `python -m py_compile <missing>` and `python -m compileall -q <missing>`
    exit 0. Only the gui-import job, which does a real import, ever noticed --
    and that job had never run, because Actions was billing-blocked.

    So this checks every repo-relative .py/.qml path the workflow mentions.
    """

    WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "python-smoke.yml"

    def test_every_python_path_in_the_workflow_resolves(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        # Repo-relative paths only: skip anything with a scheme, a drive or a
        # leading slash, and skip the runner's own temp paths.
        named = sorted(set(re.findall(r"(?<![\w/.\\:-])((?:[\w.-]+/)*[\w.-]+\.(?:py|qml))", text)))
        self.assertTrue(named, "no paths found; this guard is watching nothing")
        missing = [p for p in named
                   if not (PROJECT_ROOT / p).is_file() and "/" in p]
        self.assertEqual([], missing,
                         f"the workflow names {len(missing)} file(s) that do not exist: {missing}")

    def test_the_compile_step_actually_fails_on_a_missing_file(self):
        # py_compile and compileall both exit 0 for a path that is not there,
        # so the step has to assert existence itself.
        text = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("missing: ", text,
                      "the compile step no longer checks that its files exist, "
                      "so a renamed entry point would compile nothing and pass")


if __name__ == "__main__":
    unittest.main()
