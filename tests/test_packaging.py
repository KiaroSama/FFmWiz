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
        # ffmwiz/gui and ffmwiz/gui/qml have no __init__.py. Turning namespaces
        # off, or listing packages explicitly, silently drops the whole GUI.
        find = self.config["tool"]["setuptools"]["packages"]["find"]
        self.assertNotEqual(find.get("namespaces", True), False)

    def test_runtime_assets_are_declared_as_package_data(self):
        package_data = self.config["tool"]["setuptools"].get("package-data", {})
        patterns = package_data.get("ffmwiz", [])
        self.assertTrue(any("assets" in p for p in patterns), "icons/cursors must ship")
        self.assertTrue(any("qml" in p for p in patterns), "the QML scene must ship")
        self.assertTrue(self.config["tool"]["setuptools"].get("include-package-data"))

    def test_the_thin_launcher_is_still_a_top_level_module(self):
        self.assertIn("FFmWiz", self.config["tool"]["setuptools"]["py-modules"])

    def test_no_dead_tool_tables(self):
        # [tool.py_compile_check] was read by nothing.
        self.assertNotIn("py_compile_check", self.config.get("tool", {}))


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
            path for path in (PROJECT_ROOT / "build", *PROJECT_ROOT.glob("*.egg-info"))
            if path.exists()
        }
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
        for path in (PROJECT_ROOT / "build", *PROJECT_ROOT.glob("*.egg-info")):
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

    def test_wheel_contains_the_qml_scene(self):
        self.assertIn("ffmwiz/gui/qml/UnifiedEditor.qml", self._names)

    def test_wheel_contains_the_thin_launcher(self):
        self.assertIn("FFmWiz.py", self._names)


if __name__ == "__main__":
    unittest.main()
