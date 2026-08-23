"""Guards that keep the documentation honest about the code.

Three facts in the docs are mechanically checkable against the source, and each
one has drifted in the past: the main-menu mode list (modes 14 and 15 shipped
undocumented), the `FFMWIZ_*` environment variables (nine were missing from the
appendix that calls itself complete), and the `path.py::function` pointers in
docs/architecture.md (one named a module that never held the function).

The lists are parsed out of the source every run, never hard-coded, so adding a
mode or an environment variable fails these tests until the docs catch up.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "FFmWiz.py"
README = ROOT / "README.md"
DOCUMENTATION = ROOT / "docs" / "DOCUMENTATION.md"
ARCHITECTURE = ROOT / "docs" / "architecture.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 1. Main-menu labels
# --------------------------------------------------------------------------

def menu_labels(source: str) -> list[tuple[str, str]]:
    """(number, label) for every entry `ask_main_menu` prints.

    Each entry is one f-string of the shape
        f"  {paint('12.', ...)} Join Audios and Videos"
    so the number comes from the first `paint()` argument and the label is the
    literal text around it. Mode 1 carries a trailing `{paint('[1]', ...)}`
    default marker, which drops out because only the literal parts are kept.
    """
    tree = ast.parse(source, filename=str(ENTRY))
    func = next((node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name == "ask_main_menu"), None)
    assert func is not None, "ask_main_menu not found in FFmWiz.py"

    entries: list[tuple[str, str]] = []
    for joined in (n for n in ast.walk(func) if isinstance(n, ast.JoinedStr)):
        literals = "".join(v.value for v in joined.values
                           if isinstance(v, ast.Constant) and isinstance(v.value, str))
        numbers = [c.value for c in ast.walk(joined)
                   if isinstance(c, ast.Constant) and isinstance(c.value, str)
                   and re.fullmatch(r"\d+\.", c.value)]
        if numbers and literals.strip():
            entries.append((numbers[0].rstrip("."), literals.strip()))
    return entries


class MainMenuDocumentedTests(unittest.TestCase):
    """Every menu entry the app prints must appear verbatim in both documents."""

    def setUp(self):
        self.entries = menu_labels(_read(ENTRY))

    def test_menu_was_parsed(self):
        # A parser that silently found nothing would make every check below pass.
        self.assertGreaterEqual(len(self.entries), 15)
        self.assertEqual([n for n, _ in self.entries],
                         [str(i) for i in range(1, len(self.entries) + 1)])

    def test_every_label_is_in_readme(self):
        text = _read(README)
        missing = [f"{n}. {label}" for n, label in self.entries if label not in text]
        self.assertEqual([], missing, f"menu entries missing from README.md: {missing}")

    def test_every_label_is_in_documentation(self):
        text = _read(DOCUMENTATION)
        missing = [f"{n}. {label}" for n, label in self.entries if label not in text]
        self.assertEqual([], missing,
                         f"menu entries missing from docs/DOCUMENTATION.md: {missing}")


# --------------------------------------------------------------------------
# 2. FFMWIZ_* environment variables
# --------------------------------------------------------------------------

ENV_NAME_RE = re.compile(r"FFMWIZ_[A-Z0-9_]+")


def env_names() -> set[str]:
    """Every FFMWIZ_* token in the shipped package and the entry point."""
    sources = [ENTRY, *sorted((ROOT / "ffmwiz").rglob("*.py"))]
    return {name for path in sources for name in ENV_NAME_RE.findall(_read(path))}


def appendix_k(text: str) -> str:
    """The environment-variable appendix alone.

    Scoped on purpose: a name mentioned in passing elsewhere in the document is
    not what Appendix K promises, and a whole-document search let a deleted
    table row pass while a stray prose mention survived.
    """
    start = text.index("## Appendix K")
    end = text.find("\n## ", start)
    return text[start:] if end < 0 else text[start:end]


class EnvironmentVariablesDocumentedTests(unittest.TestCase):
    def test_every_ffmwiz_name_appears_in_documentation(self):
        """Appendix K claims to list every variable FFmWiz reads.

        A name is allowed to appear as a documented variable *or* in the
        appendix's explicit internal-constants note -- what is not allowed is
        for it to be absent, which is how nine names once went unmentioned.
        """
        text = appendix_k(_read(DOCUMENTATION))
        names = env_names()
        self.assertGreater(len(names), 10, "FFMWIZ_* scan found suspiciously few names")
        missing = sorted(n for n in names if n not in text)
        self.assertEqual([], missing,
                         "FFMWIZ_* names read by the code but absent from "
                         f"docs/DOCUMENTATION.md Appendix K: {missing}")

    def test_internal_only_names_are_declared_as_such(self):
        """Names excluded from the tables must be called out, not just omitted."""
        text = appendix_k(_read(DOCUMENTATION))
        note = "internal Python constants"
        self.assertIn(note, text)
        for name in ("FFMWIZ_GUI_DIR_NAME", "FFMWIZ_GUI_FILE_NAME", "FFMWIZ_RUNTIME_DIR_NAME"):
            self.assertIn(name, text)


# --------------------------------------------------------------------------
# 3. architecture.md `path.py::name` pointers
# --------------------------------------------------------------------------

CODE_REF_RE = re.compile(r"([A-Za-z0-9_./-]+\.py)::([A-Za-z_][A-Za-z0-9_]*)")


def _resolve(ref: str) -> Path:
    """A repo-relative path, or a bare filename that lives in tests/."""
    direct = ROOT / ref
    return direct if direct.exists() else ROOT / "tests" / ref


def _defined_names(path: Path) -> set[str]:
    tree = ast.parse(_read(path), filename=str(path))
    names = {node.name for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    for node in tree.body:  # module-level constants are legitimate targets too
        if isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def broken_code_refs(text: str) -> list[str]:
    """`file.py::name` references whose file is missing or lacks the name."""
    broken = []
    for ref, name in CODE_REF_RE.findall(text):
        path = _resolve(ref)
        if not path.exists():
            broken.append(f"{ref}::{name} (no such file)")
        elif name not in _defined_names(path):
            broken.append(f"{ref}::{name} (file does not define {name})")
    return broken


class ArchitectureCodeRefTests(unittest.TestCase):
    def test_every_code_reference_resolves(self):
        text = _read(ARCHITECTURE)
        refs = CODE_REF_RE.findall(text)
        self.assertGreater(len(refs), 0, "no path.py::name references found to check")
        broken = broken_code_refs(text)
        self.assertEqual([], broken, f"stale references in docs/architecture.md: {broken}")

    def test_guard_rejects_a_reference_to_the_wrong_module(self):
        # Proves the check can fail: ext00.py exists but never held this function.
        self.assertEqual(
            ["ffmwiz/support/ext00.py::run_mux_cleanup_mode "
             "(file does not define run_mux_cleanup_mode)"],
            broken_code_refs("`ffmwiz/support/ext00.py::run_mux_cleanup_mode` calls ..."))


if __name__ == "__main__":
    unittest.main()
