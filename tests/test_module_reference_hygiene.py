"""No module may name another module it did not import (D09 fallout guard).

`test_package_imports` proves every module IMPORTS on its own. That is not the
whole story: Python resolves `encoding.run_ffmpeg_with_progress` when the line
RUNS, not when the file is imported. So deleting `from ffmwiz import encoding`
leaves a file that imports perfectly and raises `NameError` the first time that
branch is taken -- which, for a branch only a real reverse encode reaches, can
be a long way from the change that broke it.

That is exactly what happened while the import cycles were being removed: the
back-imports went, the qualified call sites stayed, and nothing failed until a
test drove the code. This checks it statically, so the next such deletion fails
in a second instead of in production.

It also pins the direction the cycles were removed in: a leaf must not reach UP
to the facade that re-exports it. `docs/architecture.md` describes the shape.
"""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "ffmwiz"


def _package_modules() -> dict[str, Path]:
    """basename -> path, for every module in the package."""
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "__init__.py":
            continue
        found[path.stem] = path
    return found


def _bound_at_module_level(tree: ast.Module) -> set[str]:
    """Every name this file could resolve, from imports and definitions.

    Deliberately generous: a name bound anywhere -- including inside a function,
    a `with`, or an `except` -- counts. The point is to catch a reference to a
    module NOTHING binds, not to police scope.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bound |= {a.asname or a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            bound |= {a.asname or a.name for a in node.names if a.name != "*"}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            bound |= {a.arg for a in args.args + args.kwonlyargs + args.posonlyargs}
    # Function arguments, walked separately so nested defs are covered too.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            bound |= {a.arg for a in args.args + args.kwonlyargs + args.posonlyargs}
            if args.vararg:
                bound.add(args.vararg.arg)
            if args.kwarg:
                bound.add(args.kwarg.arg)
    return bound


class NoModuleNamesAModuleItDidNotImport(unittest.TestCase):

    def test_the_sweep_covers_the_package(self):
        # Guard the guard: a sweep that found no modules would pass silently.
        modules = _package_modules()
        self.assertGreater(len(modules), 90, sorted(modules)[:5])
        for expected in ("encoding", "reverse_stages", "wizard_build", "guibridge"):
            self.assertIn(expected, modules)

    def test_every_qualified_module_reference_is_imported(self):
        modules = _package_modules()
        offenders: list[str] = []
        for name, path in sorted(modules.items()):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            bound = _bound_at_module_level(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                base = node.value
                if not isinstance(base, ast.Name):
                    continue
                # `foo.bar` where `foo` is one of OUR module names and nothing
                # in this file binds `foo`.
                if base.id in modules and base.id != name and base.id not in bound:
                    offenders.append(
                        f"{path.relative_to(ROOT).as_posix()}:{node.lineno}: "
                        f"{base.id}.{node.attr} -- `{base.id}` is never imported here")
        self.assertEqual([], offenders, "\n  " + "\n  ".join(offenders))


class TheCyclesStayRemoved(unittest.TestCase):
    """A leaf must not import the facade that re-exports it.

    The facade ends with `__all__ = list(__all__) + list(_leaf.__all__)`. Import
    the leaf first and the facade reaches that line while the leaf is still on
    its first statements, so the attribute does not exist yet. Every such pair
    was one unimportable module.
    """

    def _reexport_pairs(self) -> list[tuple[str, str]]:
        """(facade, leaf) for every `__all__ += <leaf>.__all__` in the package."""
        pairs = []
        for path in sorted(PACKAGE.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "__all__ = list(__all__)" not in text:
                continue
            tree = ast.parse(text)
            aliases = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("ffmwiz"):
                    for a in node.names:
                        if a.asname:
                            aliases[a.asname] = f"{node.module}.{a.name}"
            for line in text.splitlines():
                if "__all__ = list(__all__)" not in line:
                    continue
                for alias, target in aliases.items():
                    if f"{alias}.__all__" in line:
                        facade = ".".join(path.relative_to(ROOT).with_suffix("").parts)
                        pairs.append((facade, target))
        return pairs

    def test_there_are_pairs_to_check(self):
        # Guard the guard: if the re-export shape ever changes, this test must
        # be rewritten rather than silently passing on an empty list.
        self.assertGreater(len(self._reexport_pairs()), 3, self._reexport_pairs())

    def test_no_leaf_imports_the_facade_that_reexports_it(self):
        offenders = []
        for facade, leaf in self._reexport_pairs():
            leaf_path = ROOT / Path(*leaf.split(".")).with_suffix(".py")
            if not leaf_path.exists():
                continue
            text = leaf_path.read_text(encoding="utf-8")
            pkg, base = facade.rsplit(".", 1)
            # Anchored on a word boundary: a plain `startswith` made
            # `from ffmwiz import wizard_build_b` look like an import of
            # `ffmwiz.wizard`, and reported a pair that does not exist.
            patterns = [re.compile(rf"^from {re.escape(facade)} import\b"),
                        re.compile(rf"^from {re.escape(pkg)} import {re.escape(base)}\b")]
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if any(p.match(stripped) for p in patterns):
                    offenders.append(f"{leaf} imports {facade}: {stripped[:70]}")
        self.assertEqual([], sorted(set(offenders)), "\n  " + "\n  ".join(sorted(set(offenders))))


if __name__ == "__main__":
    unittest.main()


class ReExportedNamesDoNotDependOnImportOrder(unittest.TestCase):
    """`from ffmwiz.support.ext04 import *` must mean the same thing always.

    This was the QUIET half of D09. Where a facade guarded its merge with
    `getattr(_leaf, "__all__", [])`, importing the leaf first did not raise --
    it skipped the merge and left the facade's `__all__` short. Measured before
    the repair:

        import ext04  first -> ext04.__all__ == 53   (correct)
        import ext04b first -> ext04.__all__ == 14   (39 names gone)
        import ext00b first -> ext00.__all__ == 60   (37 names gone, of 97)

    Nothing raised. A consumer that star-imported the facade after touching the
    leaf simply did not get most of the tier, and found out at the first
    NameError. The loud version of the same defect was the 17 unimportable
    modules; this one had no symptom at all.
    """

    FACADES = ["ffmwiz.support.ext00", "ffmwiz.support.ext01",
               "ffmwiz.support.ext04", "ffmwiz.support.L00_misc",
               "ffmwiz.wizard", "ffmwiz.services", "ffmwiz.runtime"]

    def _all_after_importing(self, first: str, facade: str) -> int:
        """len(facade.__all__) in a FRESH process that imported `first` first."""
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-c",
             f"import {first}\nimport {facade} as F\nprint(len(F.__all__))"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180)
        self.assertEqual(0, result.returncode,
                         f"importing {first} then {facade} failed:\n{result.stderr[-400:]}")
        return int(result.stdout.strip())

    def _leaves_of(self, facade: str) -> list[str]:
        path = ROOT / Path(*facade.split(".")).with_suffix(".py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        leaves = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("ffmwiz"):
                for alias in node.names:
                    if alias.asname and alias.asname.startswith("_"):
                        leaves.append(f"{node.module}.{alias.name}")
        return leaves

    def test_the_facades_have_leaves_to_check(self):
        # Guard the guard: no leaves means this test proves nothing.
        for facade in self.FACADES:
            with self.subTest(facade=facade):
                self.assertTrue(self._leaves_of(facade), f"{facade} re-exports nothing")

    def test_the_public_surface_is_the_same_from_either_side(self):
        for facade in self.FACADES:
            expected = self._all_after_importing(facade, facade)
            for leaf in self._leaves_of(facade):
                with self.subTest(facade=facade, leaf=leaf):
                    self.assertEqual(
                        expected, self._all_after_importing(leaf, facade),
                        f"{facade}.__all__ shrinks when {leaf} is imported first")
