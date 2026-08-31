"""Guard for the classic GUI's shared-namespace assembly (HYG1).

`ffmwiz/gui/ffmwiz_gui.py` merges ten sibling modules into one dict and injects
that dict back into every one of them, so a function in `gui_editor_cut` can
call a helper defined in `gui_common`. The merge is the only thing making the
GUI resolvable at all, and it has two failure modes:

  * copying *every* public name from a child drags its private imports along
    and lets one child silently shadow another's name of the same spelling --
    last module in `_MODULES` wins, with no error anywhere;
  * over-correcting and dropping a name some module needs at runtime breaks the
    GUI on launch, where nothing but a user notices.

So this pins both directions: the merge must honour a child's `__all__` when it
declares one, and it must still deliver every name the children really export.

No child declares `__all__` today, which makes the guard inert *right now* -- it
is the next module that gains one that would otherwise leak or be truncated.
That is why the `__all__` half is driven through synthetic modules rather than
the real ten: a test that only asserted today's shape would keep passing with
the guard ripped out.
"""
from __future__ import annotations

import ast
import importlib
import sys
import types
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_GUI_DIR = _ROOT / "ffmwiz" / "gui"
for _p in (str(_ROOT), str(_GUI_DIR),
           str(_GUI_DIR / "classic"), str(_GUI_DIR / "modern")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_ENTRY_SOURCE = (_GUI_DIR / "classic" / "ffmwiz_gui.py").read_text(encoding="utf-8")

# Package-qualified. These stopped being importable as bare top-level
# modules when the editors were made addressable as `ffmwiz.gui.<name>`
# (D10); the bare name only ever resolved because the GUI was launched
# as a script from its own folder. Loading them under the old name here
# produced a SECOND module object, so `assertIs` against the entry
# point's merged namespace could never match.
_PACKAGE = "ffmwiz.gui"
# Two prefixes now, not one: the palette, geometry and common helpers are
# SHARED by both engines and stay at `ffmwiz.gui`, while the widgets editors
# live under `ffmwiz.gui.classic`. A single flat prefix cannot address both.
_CHILDREN = ([f"{_PACKAGE}.{name}" for name in (
    "gui_common",
    "gui_style",
    "gui_geometry",
)] + [f"{_PACKAGE}.classic.{name}" for name in (
    "gui_editor_cut",
    "gui_editor_crop",
    "gui_editor_speed",
    "gui_editor_audio",
    "gui_editor_unified",
    "gui_editor_unified_canvas",
    "gui_editor_unified_timeline",
)])

# The public entry point each child exists to provide. Hard-coded on purpose:
# the exhaustive check below would still pass if a child contributed nothing at
# all, and a namespace missing `build_crop_editor` is a broken GUI.
_REPRESENTATIVE = {  # keyed by the same qualified names
    f"{_PACKAGE}.gui_common": "main",
    f"{_PACKAGE}.gui_style": "QSS",
    f"{_PACKAGE}.gui_geometry": "seconds_to_timecode",
    f"{_PACKAGE}.classic.gui_editor_cut": "build_cut_editor",
    f"{_PACKAGE}.classic.gui_editor_crop": "build_crop_editor",
    f"{_PACKAGE}.classic.gui_editor_speed": "build_speed_editor",
    f"{_PACKAGE}.classic.gui_editor_audio": "build_audio_cut_editor",
    f"{_PACKAGE}.classic.gui_editor_unified": "build_unified_video_editor",
    f"{_PACKAGE}.classic.gui_editor_unified_canvas": "build_unified_preview_widgets",
    f"{_PACKAGE}.classic.gui_editor_unified_timeline": "build_unified_timeline_widget",
}


def _snapshot(module: types.ModuleType) -> types.ModuleType:
    """A frozen stand-in for a child module.

    Taken before the entry module runs: injection gives every child every name,
    after which a child's own contribution is unrecoverable. The clone is a real
    module object so the production helper runs against it unmodified.
    """
    clone = types.ModuleType(module.__name__)
    clone.__dict__.update(vars(module))
    return clone


_PRISTINE = {name: _snapshot(importlib.import_module(name)) for name in _CHILDREN}

import ffmwiz.gui.classic.ffmwiz_gui as entry  # noqa: E402  (must follow the snapshot)


def _fake_module(name: str, **members) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__.update(members)
    return module


class ExportedNamesHonoursAll(unittest.TestCase):
    """(a) the merge respects a child's declared exports."""

    def test_a_declared_all_drops_everything_else(self):
        child = _fake_module("child", public=1, _private=2, leaked_import=3)
        child.__all__ = ["public"]
        self.assertEqual({"public": 1}, entry._exported_names(child))

    def test_no_declaration_keeps_every_public_name(self):
        # The fallback: nothing that works today may stop working.
        child = _fake_module("child", public=1, _private=2)
        self.assertEqual({"public": 1, "_private": 2}, entry._exported_names(child))

    def test_an_empty_all_is_a_declaration_not_a_missing_one(self):
        child = _fake_module("child", public=1)
        child.__all__ = []
        self.assertEqual({}, entry._exported_names(child))

    def test_a_stale_name_in_all_does_not_kill_startup(self):
        # `__all__` drifting ahead of the code must not raise while the GUI
        # entry point imports; the user would just see it fail to open.
        child = _fake_module("child", public=1)
        child.__all__ = ["public", "renamed_away"]
        self.assertEqual({"public": 1}, entry._exported_names(child))

    def test_every_merge_site_routes_through_the_guard(self):
        """Source-level: a second, unguarded merge site must not creep back in.

        The tests above only prove the helper is correct; this proves the
        assembly loop actually calls it.
        """
        tree = ast.parse(_ENTRY_SOURCE, filename="ffmwiz_gui.py")
        merges = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_ASSEMBLED"
        ]
        self.assertTrue(merges, "no _ASSEMBLED.update() call found - loop renamed?")
        for call in merges:
            guarded = (
                len(call.args) == 1
                and isinstance(call.args[0], ast.Call)
                and isinstance(call.args[0].func, ast.Name)
                and call.args[0].func.id == "_exported_names"
            )
            self.assertTrue(
                guarded,
                f"unguarded merge site at line {call.lineno}: "
                "pass the child through _exported_names()")


class NoSilentShadowing(unittest.TestCase):
    """(b) two children must never contribute one name with different values."""

    def test_the_child_list_matches_the_real_one(self):
        # Otherwise this whole suite quietly stops covering a module.
        self.assertEqual(_CHILDREN, [module.__name__ for module in entry._MODULES])

    def test_no_name_is_contributed_with_conflicting_values(self):
        owners: dict[str, list[tuple[str, object]]] = {}
        for child, module in _PRISTINE.items():
            for key, value in entry._exported_names(module).items():
                owners.setdefault(key, []).append((child, value))

        # Compared by value, not identity: several children each build their own
        # equal `ASSETS_DIR` Path, which is duplication, not shadowing. Only a
        # name that means two DIFFERENT things is a bug the merge would hide.
        conflicts = {
            key: [owner for owner, _ in entries]
            for key, entries in owners.items()
            if len(entries) > 1 and any(value != entries[0][1] for _, value in entries)
        }
        self.assertEqual({}, conflicts, f"shadowed names: {conflicts}")


class NothingRealWasRemoved(unittest.TestCase):
    """(c) the guard must not have cost the namespace a single usable name."""

    def test_each_child_reaches_the_namespace_through_its_entry_point(self):
        for child, name in _REPRESENTATIVE.items():
            with self.subTest(child=child):
                self.assertIn(name, entry._ASSEMBLED)
                self.assertIs(getattr(_PRISTINE[child], name), entry._ASSEMBLED[name])

    def test_every_exported_name_survives_the_merge(self):
        for child, module in _PRISTINE.items():
            for key, value in entry._exported_names(module).items():
                with self.subTest(child=child, name=key):
                    self.assertIn(key, entry._ASSEMBLED)
                    self.assertEqual(value, entry._ASSEMBLED[key])

    def test_the_namespace_is_injected_back_into_every_child(self):
        # The merge is only useful because each child then sees the whole dict:
        # `build_cut_editor` has to be reachable from inside `gui_editor_crop`.
        for module in entry._MODULES:
            with self.subTest(child=module.__name__):
                self.assertTrue(hasattr(module, "build_cut_editor"))
                self.assertTrue(hasattr(module, "seconds_to_timecode"))


if __name__ == "__main__":
    unittest.main()


class AnUninjectedSiblingIsSelfSufficient(unittest.TestCase):
    """The other half of the `_MODULES` contract, which nothing guarded.

    `ffmwiz_gui.py`'s own docstring states it: a sibling NOT listed in
    `_MODULES` "gets nothing injected ... so those modules import their shared
    helpers by name instead". Ten of the twenty classic modules are listed; the
    other ten-plus rely on that promise.

    Nothing checked it. `test_module_reference_hygiene` catches only QUALIFIED
    references (`gui_common.helper`) -- a BARE `helper(...)` in an uninjected
    module imports fine, passes every existing test, and raises `NameError` the
    first time a user opens that editor. The failure lands in a GUI subprocess,
    where the traceback goes to a log rather than to anyone watching.

    Measured when this was written: the seven modules with unresolved bare names
    are exactly the seven that ARE injected, and every uninjected sibling
    resolved cleanly. This pins that split so the next module added on the wrong
    side of it fails here instead of in front of a user.
    """

    @staticmethod
    def _free_names(tree):
        """Names a module reads inside its defs but binds nowhere at module level."""
        import builtins
        local = {x.name for x in tree.body
                 if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = ([node.target] if isinstance(getattr(node, "target", None), ast.Name)
                           else getattr(node, "targets", []))
                local.update(t.id for t in targets if isinstance(t, ast.Name))
            elif isinstance(node, (ast.Import, ast.ImportFrom, ast.If, ast.Try)):
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        local.update((a.asname or a.name).split(".")[0] for a in sub.names)
                    elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        local.add(sub.id)
        free = set()
        for fn in tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            bound = set()
            for w in ast.walk(fn):
                if isinstance(w, ast.Name) and isinstance(w.ctx, ast.Store):
                    bound.add(w.id)
                elif isinstance(w, ast.arg):
                    bound.add(w.arg)
                elif isinstance(w, (ast.Import, ast.ImportFrom)):
                    bound.update((a.asname or a.name).split(".")[0] for a in w.names)
                elif isinstance(w, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    bound.add(w.name)
                elif isinstance(w, ast.ExceptHandler) and w.name:
                    bound.add(w.name)
            for w in ast.walk(fn):
                if (isinstance(w, ast.Name) and isinstance(w.ctx, ast.Load)
                        and w.id not in local and w.id not in bound
                        and not hasattr(builtins, w.id)):
                    free.add(w.id)
        return free

    def test_every_uninjected_classic_module_binds_what_it_reads(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        classic = Path(importlib.import_module("ffmwiz.gui.classic.ffmwiz_gui").__file__).parent
        injected = {m.__name__.rsplit(".", 1)[-1]
                    for m in importlib.import_module("ffmwiz.gui.classic.ffmwiz_gui")._MODULES}
        self.assertTrue(injected, "_MODULES is empty; this guard is pointed at the wrong thing")
        offenders = {}
        checked = 0
        for path in sorted(classic.glob("*.py")):
            if path.stem in ("__init__", "ffmwiz_gui") or path.stem in injected:
                continue
            checked += 1
            module = importlib.import_module(f"ffmwiz.gui.classic.{path.stem}")
            free = self._free_names(ast.parse(path.read_text(encoding="utf-8")))
            unresolved = sorted(n for n in free if not hasattr(module, n))
            if unresolved:
                offenders[path.name] = unresolved
        self.assertGreater(checked, 5, "found almost no uninjected siblings to check")
        self.assertEqual(
            {}, offenders,
            "these modules are NOT in _MODULES, so nothing is injected into them, "
            "yet they read names they never bind -- each is a NameError waiting for "
            f"a user to open that editor: {offenders}")
