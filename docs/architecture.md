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

## Project layout

```text
FFmWiz.py              # thin entry point: re-exports the ffmwiz package + main()
config.env             # personal Mode-2 defaults (git-ignored; created on first run)
config.env.example     # committed sample config (copy to config.env and edit)
requirements.txt
pyproject.toml
run.ps1                # canonical local launcher
install-command.ps1
docs/
  DOCUMENTATION.md     # the authoritative usage/configuration reference
  architecture.md      # this document
  FFMPEG-REFERENCE.md  # build-specific FFmpeg capability reference
MediaReports/          # generated media info reports (git-ignored)
Logs/                  # per-run UTC logs (git-ignored)
.github/
  workflows/
ffmwiz/                # the application package (all implementation lives here)
  core/                # constants, colors, exceptions, timeline (dependency layer 0)
  support/             # pure/low-level helpers (L00_*/L01_*/L02..L07 + ext tiers)
  appio.py             # interactive I/O + logging foundation
  runtime*.py          # progress rendering, console/VT, PySide detection
  services*.py         # ffprobe/capability/output-path/loudnorm services
  wizard*.py           # main wizard (wizard, wizard_build, wizard_steps, wizard_flow)
  modes*.py            # per-mode runners (modes, modes_b, modes_mediainfo,
                       #   modes_join, modes_transform)
  guibridge*.py        # GUI subprocess bridge (+ legacy-Tk crop/cut siblings)
  metadata.py, trackmanager.py, runner.py, encoding.py
  reverse_pipeline.py   # the staged reverse pipeline: plan it, export it, run it
                       #   (`reverse` buffers every decoded frame, so a Join,
                       #    a Split or a long input runs as bounded stages)
  reverse_stages.py     # what ONE stage is: the transformations it owns, the
                       #   file it will write, its frame budget, its stream map
  muxcleanup/          # Stream Cleanup Remux subsystem (Mode 8, in-process)
  gui/                 # the bundled PySide6 / QtQuick GUI (subprocess)
    ffmwiz_gui.py      # classic GUI entry point (imports the modules below)
    gui_common.py      # palette/QSS, logging, icons, helpers, main() dispatcher
    gui_editor_*.py    # cut / crop / speed / audio / unified editor builders
                       # (unified also has _canvas / _timeline widget modules)
    ffmwiz_gui_qml.py  # modern QtQuick unified editor driver (opt-in)
    qml/               # QML UI files for the modern engine (UnifiedEditor.qml)
  assets/              # runtime resources bundled inside the package
    icons/             # includes ffmwiz_app.ico / ffmwiz_app.png (window/taskbar icon)
    cursors/
tests/                 # unittest suite at the project root
```

Keep the `assets` folder inside the `ffmwiz` package (`ffmwiz/assets/`). The GUI
app icon is loaded from `ffmwiz/assets/icons/ffmwiz_app.ico` or `.png`; toolbar
and cursor icons come from `ffmwiz/assets/icons/` and `ffmwiz/assets/cursors/`.
Missing icon assets are logged but never stop the GUI from opening.

## Repository conventions

- `requirements.txt` and `pyproject.toml` both pin PySide6, the only runtime
  Python dependency. The core CLI itself uses only the standard library.
- `.github/workflows/python-smoke.yml` compiles `FFmWiz.py` and the bundled GUI,
  compiles the whole `ffmwiz` package, validates `config.env.example`, runs an
  import/API smoke check, and runs the command-generation regression tests on
  Windows with Python 3.10-3.13.
- `.github/dependabot.yml` checks Python and GitHub Actions updates weekly.
- `.gitattributes` normalizes text line endings and marks image assets binary;
  `.editorconfig` keeps indentation, UTF-8, and final-newline rules consistent.
- `.gitignore` excludes runtime logs, generated media info reports, Python
  caches, generated command shims, virtual environments, build outputs, and the
  local `ffmwiz-ffmpeg-reference.txt` capability snapshot.
- Your personal `config.env` stays local; only `config.env.example` is committed.

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

Modules import only from lower layers, so the *layer* graph is acyclic:

> The one deliberate exception is the `<name>` / `<name>_b` sibling pair described
> under [Module size policy](#module-size-policy): a sibling back-imports from its
> source and the source re-exports the sibling at its end. That cycle is intentional
> and lives *inside* one logical module, not between layers.

```
core/            constants, colors, exceptions, timeline        (layer 0)
support/         L00_* / L01_* pure helpers, ext00..ext12 tiers  (low level)
appio.py         interactive I/O + logging foundation
runtime*.py      progress rendering (runtime, runtime_render), console/VT,
                 PySide detection
services*.py     ffprobe / capability / output-path / loudnorm
runner.py, guibridge*.py, metadata.py, trackmanager.py
wizard*.py, modes*.py, encoding.py                               (subsystems)
muxcleanup/      Stream Cleanup Remux (Mode 8), self-contained
gui/             PySide6 / QtQuick GUI, run as a subprocess
```

`script_dir()` (in `ffmwiz/support/L00_paths.py`) resolves the project root as
the parent of the `ffmwiz` package, so `config.env`, `requirements.txt`, the
bundled GUI, `MediaReports/`, and `Logs/` are all found relative to
`FFmWiz.py`.

The large Mode-2 `config.env` template lives in `ffmwiz/core/constants_config_template.py`
(a leaf module holding only the `CONFIG_TEMPLATE` string) and is re-exported by
`constants.py`, keeping the constants module scannable while the public name
`FFmWiz.CONFIG_TEMPLATE` stays unchanged.

The encode file-size estimate is a small, dependency-light feature: the pure
math and formatting live in `ffmwiz/support/L00_naming.py`
(`estimate_size_bytes_from_bitrate`, `format_estimated_size`), and the
duration/orchestration lives in `ffmwiz/services.py`
(`estimated_encode_duration_seconds`, `print_encode_size_estimate`). The video
and audio bitrate steps and the wizard summary all call the same helpers.

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
  builder.
- `gui_editor_unified_canvas.py` (preview canvas + frame-extract worker) and
  `gui_editor_unified_timeline.py` (timeline strip) — extracted from
  `gui_editor_unified.py`, which was a single 3993-line function. Each holds a
  factory that defines and returns its widget class, so PySide6 is still
  imported on demand. The classes captured no state from the builder, only Qt
  symbols, which is what made the move safe; it was verified by comparing the
  built window's whole widget tree and public API before and after.
  The remaining builders are each one large Qt function whose nested pieces do
  close over builder state, so they cannot be moved the same way.

The modern engine (`ffmwiz_gui_qml.py` + `qml/UnifiedEditor.qml`) is opt-in via
`gui_engine=qml` (or `FFMWIZ_GUI_ENGINE=qml`) and only handles the unified video
editor; every other GUI mode uses the classic engine.

## Stream Cleanup Remux (`ffmwiz/muxcleanup/`)

Mode 8 (Stream Cleanup Remux) runs in-process from the self-contained
`ffmwiz.muxcleanup` package (constants, colors, logging, models, prompts, media
probing, mux logic, output paths, reporting, selection, processing, and the app
menu). `ffmwiz/support/ext00b.py::run_mux_cleanup_mode` calls
`ffmwiz.muxcleanup.app.main_menu()` directly.

## Running and testing

```powershell
py FFmWiz.py                       # run the wizard
py -m py_compile FFmWiz.py         # syntax check the entry
py -m compileall -q ffmwiz         # compile the whole package
py -m unittest discover -s tests   # full test suite
py FFmWiz.py --preview-colors      # color/theme smoke check
```

The GitHub Actions workflow (`.github/workflows/python-smoke.yml`) reproduces
these checks on Windows across Python 3.10–3.13.

## Module size policy

Every module is kept under ~800 lines. Oversized modules are split with the
sibling pattern: the source keeps the smaller half, a `<name>_b`/`<name>c`
sibling holds the rest, does `from ffmwiz.<source> import *` (back-import) plus
`from ffmwiz import <source>`, and the source re-exports the sibling at its end
(`from ffmwiz.<sibling> import *`; `__all__ += <sibling>.__all__`). For a
monkeypatched facade (services, wizard, guibridge, modes, runtime), bare calls
in the sibling to patched names are rewritten to `<facade>.<name>` so
`mock.patch` targets still resolve. The pure-data modules
`core/constants_config_template.py` and `core/constants_tables.py` are leaf
siblings of `constants.py`.

The only files intentionally left above 800 lines are single-function GUI
builders that cannot be split without visual/runtime verification of the Qt/Tk
window: `gui/gui_editor_unified.py`, `gui/gui_editor_cut.py`,
`gui/gui_editor_speed.py`, `gui/gui_editor_crop.py`, `guibridge_crop_tk.py`,
and `guibridge_cut_tk.py`.

## Test layout

The unittest suite lives in `tests/` at the project root (the conventional
location); only the runtime resources `ffmwiz/assets/{icons,cursors}` are bundled
inside the package. The two originally-huge test files were split by
responsibility, then further split so no test file exceeds 800 lines, while
preserving every test (the full suite count is the guardrail):

- The command-generation suite shares one fixture base,
  `command_gen_base.py::CommandGenBase` (setUp/tearDown plus every `*_answers`
  builder and the module-level `_home_module` helper). The tests subclass it in
  `test_command_generation*.py`, `test_command_color_and_pixel*.py`,
  `test_command_audio.py`, `test_command_cut_join_folder*.py`, and
  `test_command_hardsub_and_encode*.py`.
- The Join/loudnorm classes share `join_test_helpers.py` (`video_stream`,
  `audio_stream`, `make_item`, `_vstream`) and live in
  `test_loudnorm_join_progress*.py`, `test_join_pixel_and_progress.py`, and
  `test_trackmanager_and_audio.py`.

Helper modules are named so unittest discovery (`test*.py`) skips them
(`command_gen_base.py`, `join_test_helpers.py`, `cache_test_utils.py`).

## Porting notes

Business logic in `core/` and `support/` avoids UI, network, and OS coupling
where practical; interactive I/O is funnelled through `appio`, and external
tools (ffmpeg/ffprobe/robocopy) are invoked from `services`/`runner`. Keeping
those boundaries makes a future re-implementation in another language easier,
since the domain logic is already separated from the CLI and GUI layers.
