# FFmWiz Architecture

This document describes how FFmWiz is organized after the decomposition of the
original single-file script into the `ffmwiz` package. It is aimed at
contributors and at anyone who wants to understand, extend, or port the project.

## Goals

- Keep the CLI entry point (`FFmWiz.py`) and the way it is launched unchanged
  (`py FFmWiz.py`, the `run.ps1` launcher, CI, and the color-preview smoke test
  all still work exactly as before).
- Keep the public API stable: `import FFmWiz; FFmWiz.<name>` still resolves to
  the same names the test suite and tooling expect.
- Split a very large monolith into cohesive, individually readable modules
  (roughly ≤ 1000 lines each, excluding single large functions and data tables).
- Preserve 100% of behavior and keep the full unittest suite green.

## Thin entry point + re-export shim

`FFmWiz.py` is a thin entry point. It does three things:

1. Re-exports every implementation module with `from ffmwiz.X import *` plus
   `from ffmwiz import X`, so both `FFmWiz.<name>` (flat, historical API) and
   `FFmWiz.<module>.<name>` (qualified) resolve.
2. Keeps only the top-level menu/dispatch functions (`ask_main_menu`,
   `ask_start_mode`, `run_one_job`, `main`).
3. Runs `main()` under `if __name__ == "__main__"`.

All real implementation lives in the `ffmwiz/` package.

## Dependency layers

Modules import only from lower layers, which keeps the import graph acyclic:

```
core/            constants, colors, exceptions, timeline        (layer 0)
support/         L00_* / L01_* pure helpers, ext00..ext12 tiers  (low level)
appio.py         interactive I/O + logging foundation
runtime.py       progress rendering, console/VT, PySide detection
services.py      ffprobe / capability / output-path / loudnorm
runner.py, guibridge.py, metadata.py, trackmanager.py
wizard*.py, modes*.py, mux*.py, encoding.py                      (subsystems)
muxcleanup/      Stream Cleanup Remux (Mode 8), self-contained
gui/             PySide6 / QtQuick GUI, run as a subprocess
```

`script_dir()` (in `ffmwiz/support/L00_paths.py`) resolves the project root as
the parent of the `ffmwiz` package, so `config.env`, `requirements.txt`, the
bundled GUI, `MediaReports/`, and `Logs/` are all found relative to
`FFmWiz.py`.

## Splitting a module safely

Because the test suite monkeypatches many functions via
`mock.patch.object(FFmWiz.<module>, "name")` (and `FFmWiz.<module>.name = ...`),
splitting a module requires care so patches still take effect:

- A source module re-exports its new sibling(s) at its end
  (`from ffmwiz.<sibling> import *` and `__all__ += sibling.__all__`), so every
  `import *` consumer keeps seeing the full set of names.
- When a moved function references a name that tests patch on the facade
  module, that reference is qualified to `<facade>.<name>` (e.g.
  `wizard.step_input_path(...)`, `guibridge._launch_qt_gui(...)`). Qualified
  references resolve through the facade module at call time, so a
  `patch.object(FFmWiz.wizard, "step_input_path")` is still observed by callers
  that now live in a sibling module.
- Modules that mutate shared module-level state via `global` keep that state and
  all its readers/writers together in one module.

This is why files such as `wizard.py`/`wizard_build.py`/`wizard_steps.py`/
`wizard_flow.py` and `guibridge.py`/`guibridge_crop_tk.py`/`guibridge_cut_tk.py`
are grouped the way they are.

## The GUI (subprocess)

The graphical editors run in a **separate process** so the Qt event loop never
shares a thread with the CLI. `ffmwiz/guibridge.py` writes a small JSON request
file, launches `ffmwiz/gui/ffmwiz_gui.py` (classic) or the QML driver, and reads
a JSON reply.

The classic GUI is split under `ffmwiz/gui/`:

- `ffmwiz_gui.py` — thin subprocess entry point. It imports the modules below
  and injects the fully assembled namespace into each so a function in one file
  can call names defined in another. This is safe because the GUI is never
  imported by tests and is not monkeypatched.
- `gui_common.py` — imports, palette/QSS, logging, icons, time/history helpers,
  shared state, and the `main()` dispatcher.
- `gui_editor_cut.py`, `gui_editor_crop.py`, `gui_editor_speed.py`,
  `gui_editor_audio.py`, `gui_editor_unified.py` — one module per editor
  builder. The unified/cut/crop/speed builders are each a single large Qt
  function; they cannot be split further without refactoring their internals,
  which would need visual/runtime verification.

The modern engine (`ffmwiz_gui_qml.py` + `qml/UnifiedEditor.qml`) is opt-in via
`gui_engine=qml` (or `FFMWIZ_GUI_ENGINE=qml`) and only handles the unified video
editor; every other GUI mode uses the classic engine.

## Stream Cleanup Remux (`ffmwiz/muxcleanup/`)

Mode 8 (Stream Cleanup Remux) runs in-process from the self-contained
`ffmwiz.muxcleanup` package (constants, colors, logging, models, prompts, media
probing, mux logic, output paths, reporting, selection, processing, and the app
menu). `ffmwiz/support/ext00.py::run_mux_cleanup_mode` calls
`ffmwiz.muxcleanup.app.main_menu()` directly.

## Running and testing

```powershell
py FFmWiz.py                       # run the wizard
py -m py_compile FFmWiz.py         # syntax check the entry
py -m compileall -q ffmwiz         # compile the whole package
py -m unittest discover -s assets/tests   # full test suite
py FFmWiz.py --preview-colors      # color/theme smoke check
```

The GitHub Actions workflow (`.github/workflows/python-smoke.yml`) reproduces
these checks on Windows across Python 3.10–3.13.

## Porting notes

Business logic in `core/` and `support/` avoids UI, network, and OS coupling
where practical; interactive I/O is funnelled through `appio`, and external
tools (ffmpeg/ffprobe/robocopy) are invoked from `services`/`runner`. Keeping
those boundaries makes a future re-implementation in another language easier,
since the domain logic is already separated from the CLI and GUI layers.
