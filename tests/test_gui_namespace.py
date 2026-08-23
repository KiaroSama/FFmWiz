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
for _p in (str(_ROOT), str(_GUI_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_ENTRY_SOURCE = (_GUI_DIR / "ffmwiz_gui.py").read_text(encoding="utf-8")

_CHILDREN = [
    "gui_common",
    "gui_style",
    "gui_geometry",
    "gui_editor_cut",
    "gui_editor_crop",
    "gui_editor_speed",
    "gui_editor_audio",
    "gui_editor_unified",
    "gui_editor_unified_canvas",
    "gui_editor_unified_timeline",
]

# The public entry point each child exists to provide. Hard-coded on purpose:
# the exhaustive check below would still pass if a child contributed nothing at
# all, and a namespace missing `build_crop_editor` is a broken GUI.
_REPRESENTATIVE = {
    "gui_common": "main",
    "gui_style": "QSS",
    "gui_geometry": "seconds_to_timecode",
    "gui_editor_cut": "build_cut_editor",
    "gui_editor_crop": "build_crop_editor",
    "gui_editor_speed": "build_speed_editor",
    "gui_editor_audio": "build_audio_cut_editor",
    "gui_editor_unified": "build_unified_video_editor",
    "gui_editor_unified_canvas": "build_unified_preview_widgets",
    "gui_editor_unified_timeline": "build_unified_timeline_widget",
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

import ffmwiz.gui.ffmwiz_gui as entry  # noqa: E402  (must follow the snapshot)


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
