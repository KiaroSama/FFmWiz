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
setup.cfg              # only `build_base = .build`, so the staging tree
                       # cannot be imported as a module named `build`
run.ps1                # canonical local launcher
install-command.ps1
docs/
  DOCUMENTATION.md     # manual: entry point, table of contents, setup, config.env
  DOCUMENTATION-ENCODING.md         # video/audio, cut-split-join, speed, editors
  DOCUMENTATION-MODES.md            # modes 3-15, logging, troubleshooting
  DOCUMENTATION-PROMPTS-AND-KEYS.md # appendices A-B: every prompt, every key
  DOCUMENTATION-RECIPES.md          # appendices C-L: cookbook, concepts, flags
  architecture.md      # this document
  FFMPEG-REFERENCE.md  # build-specific FFmpeg capability reference
  TESTING-IN-WSL.md    # the fast portable test pass, and what it cannot cover
MediaReports/          # generated media info reports (git-ignored)
Logs/                  # per-run UTC logs (git-ignored)
.github/
  workflows/
ffmwiz/                # the application package (all implementation lives here)
  core/                # constants, colors, exceptions, timeline (dependency layer 0)
                       #   constants_ffmpeg_options.py holds the ffmpeg
                       #   option/arity contract, read by the wizard's raw
                       #   parser AND by the execution-boundary guard
  support/             # pure/low-level helpers (L00_*/L01_*/L02..L07 + ext tiers)
  appio.py             # interactive I/O + logging foundation
  runtime*.py          # progress rendering, console/VT, PySide detection
  services*.py         # ffprobe/capability/output-path/loudnorm services
  wizard*.py           # main wizard (wizard, wizard_build, wizard_steps, wizard_flow)
  wizard_base.py       # the pieces every wizard/mode module needs: `Step`,
                       #   `run_mode_steps`, the auto-back-skip rule. Below all
                       #   of them, so none has to import a facade to get them
  modes*.py            # per-mode runners (modes, modes_b, modes_mediainfo,
                       #   modes_join, modes_transform)
  guibridge*.py        # GUI subprocess bridge (+ legacy-Tk crop/cut siblings)
  guibridge_tk_common.py # palette, theme and key-binding helpers the two Tk
                       #   editors share, so neither imports guibridge back
  metadata.py, trackmanager.py, runner.py, encoding.py
  reverse_pipeline*.py  # the staged reverse pipeline: plan it, export it, run
                       #   it. `_sources` holds the subtitle/span helpers it
                       #   calls and that call nothing back.
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
    gui_child_owner.py # one bounded owner for every GUI child process
    ffmwiz_gui_qml.py  # modern QtQuick unified editor driver (opt-in)
    gui_qml_bridge.py  # the modern engine's Bridge object (built by a factory)
    gui_qml_waveform.py# the modern engine's Qt-free waveform model
    qml/               # QML UI files for the modern engine (UnifiedEditor.qml)
  assets/              # runtime resources bundled inside the package
    icons/             # includes ffmwiz_app.ico / ffmwiz_app.png (window/taskbar icon)
    cursors/
tests/                 # unittest suite at the project root
tools/                 # developer/CI scripts; no runtime code imports them
  run-tests-wsl.sh     # the fast portable pass in WSL (docs/TESTING-IN-WSL.md)
  ci_result_summary.py # restates a CI job's result in the log and step summary,
                       #   because the artifact upload is best-effort
```

Keep the `assets` folder inside the `ffmwiz` package (`ffmwiz/assets/`). The GUI
app icon is loaded from `ffmwiz/assets/icons/ffmwiz_app.ico` or `.png`; toolbar
and cursor icons come from `ffmwiz/assets/icons/` and `ffmwiz/assets/cursors/`.
Missing icon assets are logged but never stop the GUI from opening.

## Repository conventions

- PySide6 is the only runtime Python dependency; the core CLI itself uses only
  the standard library. It is pinned in **four** places, not two:
  `requirements.txt`, `pyproject.toml`, `PYSIDE6_PIP_SPEC` in
  `ffmwiz/core/constants.py`, and `install-command.ps1`. The last two are the
  FALLBACK used when `requirements.txt` is absent -- which is the installed-
  wheel case, since the wheel does not ship it. They drifted once and a guard
  that compared only the two manifests stayed green through it;
  `tests/test_packaging.py` now checks all four.
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

> This holds for individual MODULES too, not just layers. The `<name>` /
> `<name>_b` sibling pairs used to be an exception -- the sibling back-imported
> its source and the source re-exported the sibling at its end -- and that cycle
> made 17 modules impossible to import on their own. It is gone: a sibling now
> imports only lower tiers, and the source re-exports it one-directionally.
> `tests/test_package_imports.py` and `tests/test_module_reference_hygiene.py`
> keep it that way.

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
- **The sibling must not import the source back.** That is the direction that
  made the pair a cycle: the source reaches `__all__ += sibling.__all__` while
  the sibling is still on its first statements. If the sibling needs a name the
  source defines, move the DEFINITION down to a module both can import -- as
  `Step`, `step_is_auto_back_skip` and `run_mode_steps` went to `wizard_base.py`,
  the shared Tk helpers to `guibridge_tk_common.py`, and
  `audio_tool_picture_args` to `support/L01_cover.py`.
- When a moved function references a name that tests patch, that reference is
  qualified to the module that **defines** it -- `runtime.run_ffmpeg_with_progress`,
  `reverse_stages.stage_answers`, `L00_split.split_ranges_for_reverse_segments` --
  never to a facade that merely re-exports it. One definer means one patch
  point that every caller observes. Qualifying through a facade instead gave
  each caller its own copy, so a double installed on one of them silently
  missed the others, and the test passed while testing nothing.
- Never guard the re-export with `getattr(_sibling, "__all__", [])`. It stops
  the error without stopping the cycle: the merge is skipped and the source's
  `__all__` is silently short. Measured before removal, `ext04.__all__` was 53
  names imported directly and 14 with its sibling imported first.
- Modules that mutate shared module-level state via `global` keep that state and
  all its readers/writers together in one module.

This is why files such as `wizard.py`/`wizard_build.py`/`wizard_steps.py`/
`wizard_flow.py` and `guibridge.py`/`guibridge_crop_tk.py`/`guibridge_cut_tk.py`
are grouped the way they are.

## The GUI (subprocess)

The graphical editors run in a **separate process** so the Qt event loop never
shares a thread with the CLI. `ffmwiz/guibridge.py` writes a small JSON request
file, launches `ffmwiz/gui/classic/ffmwiz_gui.py` or the QML driver, and reads
a JSON reply.

`ffmwiz/gui/` holds what BOTH engines share, and one folder per engine:

- `gui_common.py`, `gui_style.py`, `gui_geometry.py` — imports, palette/QSS,
  logging, icons, time/history helpers, shared state, and the `main()`
  dispatcher. Shared deliberately: neither engine may reach into the other's
  folder, so anything both need lives here.
- `classic/` — the PySide6-widgets engine.
- `modern/` — the QtQuick engine and its `qml/`.

Inside `classic/`:

- `ffmwiz_gui.py` — thin subprocess entry point. It imports the editor modules
  and injects the fully assembled namespace into each so a function in one file
  can call names defined in another. This is safe because the GUI is never
  imported by tests and is not monkeypatched.
- One module per editor builder, each split further by responsibility so no file
  runs long: `gui_editor_cut{,_layout,_widgets,_input}.py`,
  `gui_editor_crop{,_canvas,_layout}.py`,
  `gui_editor_speed{,_transport,_reverse}.py`,
  `gui_editor_audio{,_waveform}.py`, and the unified family below.
- `gui_editor_unified.py` is the window shell; `_layout` builds the widget tree,
  `_player` owns QtMultimedia and join-segment routing, `_edits` holds the edit
  model and undo/redo, `_input` the key handling, `_canvas` the preview canvas
  and frame-extract worker, and `_timeline{,_input}` the timeline strip and its
  pointer interaction.

**The trap when adding a module here.** Only the modules listed in
`ffmwiz_gui.py`'s `_MODULES` receive the assembled namespace. A new sibling gets
nothing injected, and `from gui_common import *` skips underscore names — so a
helper like `_import_qt` is a `NameError` on first run. New modules therefore
import their shared helpers by name from `gui_common`/`gui_geometry`/`gui_style`,
and the few names reachable ONLY through the assembled namespace are passed in
as factory arguments by the parent.

The modern engine is three modules plus `modern/qml/` (whose reusable controls
and panels are one file each):

- `ffmwiz_gui_qml.py` — the subprocess driver: argv, the request/reply contract,
  the QML engine, and the app identity.
- `gui_qml_bridge.py` — `build_bridge(...)`, a FACTORY rather than a module-level
  class: `QObject`/`Slot`/`Signal`/`Property` exist only once PySide6 has
  imported, and this module has to stay importable without it. Making it a
  factory is also what lets the child-process lifecycle be tested at all — while
  the class lived inside `main()` nothing could construct it.
- `gui_qml_waveform.py` — the waveform model. Plain functions over request dicts
  and PCM arrays, no Qt, so segment lengths and envelopes can be checked against
  real FFmpeg output with no display.

A source-text guard over the modern engine must read ALL THREE, not just the
driver. The engine is opt-in via `gui_engine=qml` (or `FFMWIZ_GUI_ENGINE=qml`)
and only handles the unified video editor; every other GUI mode uses the classic
engine.

`gui/gui_child_owner.py` is shared by both engines' background workers. It
spawns and registers a child process under ONE lock, so a cancel or a window
close can never land in a gap where the child exists and nothing owns it, and it
terminates, kills and reaps what it owns within a bound.

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
sibling holds the rest and imports only LOWER tiers, and the source re-exports
the sibling at its end (`from ffmwiz.<sibling> import *`;
`__all__ += <sibling>.__all__`). The dependency runs one way. Where a sibling
needs a name the source owns, the definition moves down to a shared module
rather than the sibling reaching back up. Calls to monkeypatched names are
qualified at the module that DEFINES the name, so one `mock.patch` covers every
caller. The pure-data modules
`core/constants_config_template.py` and `core/constants_tables.py` are leaf
siblings of `constants.py`.

**No file in the repository is above 800 lines** -- source, tests and the
manual included. The GUI builders and the Tk crop/cut windows, once listed
here as permanent exceptions, are split too.

Two traps this pattern hides, both of which cost a debugging round:

- **A sibling does not inherit the source's TAIL imports.** Several modules
  import a leaf (`wizard_base`, `wizard_build_c`) *after* their `__all__`, to
  avoid re-entering a facade mid-merge. Copy only the top header into a new
  sibling and every name from those tail imports is silently unbound until the
  branch runs. `test_module_reference_hygiene` catches only *qualified*
  references (`mod.name`), not bare ones -- so after any split, resolve each
  new module's free names against the imported module and fix what is missing.
- **Patching a re-export leaves the definer's global intact.** A test doing
  `wizard_build_b.confirm_bitmap_subtitle_drop = fake` stops working the moment
  both that function and its caller move to a sibling: the caller reads its own
  module global, not the facade attribute. Point such tests at the module that
  DEFINES the name -- the same rule the call sites follow.

A source-text guard must name the SET of files, never one path. Three more
went stale in this split (`test_split_subtitles`, `test_speed_frame_retention`,
`test_docs_guards`); each now globs, or names the consumer that carries the
other half of the pair.

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
