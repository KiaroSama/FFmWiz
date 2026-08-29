# Plan 008: Put a driver-level test under the three main-menu modes whose failure damages user data, and record a verdict for the other six

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat aaf0aed..HEAD -- ffmwiz/modes_join.py ffmwiz/modes_b.py ffmwiz/trackmanager.py tests/test_command_cut_join_folder.py tests/test_folder_job_isolation.py tests/command_gen_base.py`
> If any of those changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding; on a mismatch,
> treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: L
- **Risk**: LOW (tests only — no production file is modified)
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `aaf0aed`, 2026-08-29

## Why this matters

The main menu has fifteen modes (`FFmWiz.py:418-450`). Nine of their driver
entry points are referenced by no file under `tests/`:

```
python -c "
import pathlib
names = ['run_add_files_to_video_mode','run_extract_stream_mode','run_media_info_mode',
         'run_hardsub_encode_mode','run_video_speed_reverse_mode','run_join_videos_mode',
         'run_audio_cut_mode','run_capability_cache_menu','run_track_manager_mode']
tests = list(pathlib.Path('tests').glob('*.py'))
for n in names:
    hits = [t.name for t in tests if n in t.read_text(encoding='utf-8')]
    print('%-32s %s' % (n, hits or 'NO TEST'))"
```
→ nine `NO TEST` lines today.

The BUILDERS behind them are well covered. `build_hardsub_command` appears in 7
test modules; `build_join_encode_command` appears in 29. What is untested is
the DRIVER: the prompt sequence that fills `answers`, the applicability gates,
the Back handling, and the hand-off to the builder. That is exactly the layer
where two defects shipped this week, both invisible to every argv test beneath
them:

- The compositing question was asked, answered, and the dispatcher had no
  branch for it, so an ordinary encode ran instead. The comment now sitting on
  the branch that fixes it, `ffmwiz/wizard_b.py:222-227`, says: *"Without this
  branch the compositing question was asked, answered and then ignored: the job
  ran as an ordinary encode with no overlay and no warning, which is worse than
  not offering the feature."*
- Four config keys gate a prompt that nothing reads.
  `grep -rn 'config_value(config, "video_quick")' ffmwiz/` and the same for
  `audio_volume`, `raw_ffmpeg_args` and `video_composite` print nothing, while
  all four appear in the wizard's skip-map at `ffmwiz/wizard_flow.py:126-129`.

Both are the same shape: an answer collected and then dropped between the
prompt and the builder. Only a test that drives the real prompt sequence and
asserts the resulting argv can see it.

This plan does not attempt all nine. It puts a real suite under the three whose
failure costs the user the most, and records an explicit verdict for the other
six so nobody audits them again.

## Current state

**The nine untested drivers** (line numbers verified at `aaf0aed`):

| Mode | Driver | Location | Shape |
|---|---|---|---|
| 5 | `run_add_files_to_video_mode` | `ffmwiz/modes.py:610` | wrapper → `_run_add_files_to_video_mode_impl` at line 617 |
| 6 | `run_extract_stream_mode` | `ffmwiz/modes_b.py:99` | 4-step table via `run_mode_steps` |
| 7 | `run_media_info_mode` | `ffmwiz/modes_mediainfo.py:521` | inline, ~59 lines |
| 9 | `run_hardsub_encode_mode` | `ffmwiz/modes_b.py:195` | wrapper → `_run_hardsub_encode_mode_impl` at line 203 |
| 10 | `run_video_speed_reverse_mode` | `ffmwiz/modes_transform.py:99` | wrapper → `_impl` at line 107 |
| 12 | `run_join_videos_mode` | `ffmwiz/modes_join.py:224` | inline driver, lines 224-403 of a 408-line file |
| 14 | `run_capability_cache_menu` | `ffmwiz/modes.py:107` | inline sub-menu loop, ~27 lines |
| 15 | `run_track_manager_mode` | `ffmwiz/trackmanager.py:508` | wrapper → `_run_track_manager_mode_impl` at line 553 |
| — | `run_audio_cut_mode` | `ffmwiz/modes_transform.py:146` | wrapper → `_impl` at line 154; **no caller anywhere** |

**The three this plan covers, and what each one writes:**

- **Mode 12, Join** (`ffmwiz/modes_join.py:224-403`). The longest driver in the
  set. It collects N media files, probes each through
  `services.join_load_media_item`, decides between THREE builders, and can
  re-encode the result. It also has its own Back vocabulary that differs from
  every other mode (see the warnings below).
- **Mode 9, Hard Sub Encode** (`ffmwiz/modes_b.py:203-253`). A 14-entry `Step`
  table plus its own hand-written Back loop — a second copy of the loop
  `wizard_base.run_mode_steps` provides. It burns subtitles into the picture: a
  wrong argv is a full re-encode the user has to notice and redo.
- **Mode 15, Track Manager** (`ffmwiz/trackmanager.py:553-572`, delegating to
  `_run_track_manager_single` at lines 575-670). A six-stage state machine that
  writes `<stem>_TrackEdit<ext>` **next to the source, inside
  the user's own media folder** (`track_manager_output_path`,
  `ffmwiz/support/L02.py:708-711`), and whose folder scope repeats that for
  every media file in a directory. It is the only one of the fifteen that
  writes into the library rather than into a chosen output folder.

**Mode 12's dispatch**, `ffmwiz/modes_join.py:339-382` — the three-way branch
the new suite must pin:

```python
    print_join_summary(items, copy_compatible, reasons)
    if copy_compatible:
        cmd = build_join_copy_command(answers, items, output_path)
    elif audio_only_join:
        appio.note("These audio files cannot be joined with stream copy. Re-encoding to AAC is required.")
```

...and the third branch at line 356 asks `"Encode with closest possible quality
to the inputs?"` before calling `build_join_near_quality_command`. Note that
Mode 12 calls `print_join_summary` with THREE arguments; the wizard's join path
at `ffmwiz/wizard_b.py:202-204` computes a `join_copy_plan` and passes it as a
fourth. Record what Mode 12 does today — do not change it.

**Mode 9's step table**, `ffmwiz/modes_b.py:205-224`:

```python
    steps = [
        Step("input_path", lambda a: True, ask_hardsub_source_video),
        Step("output_location", lambda a: True, step_hardsub_output_location),
        Step("output_format", lambda a: True, step_hardsub_output_format),
        Step("hardsub_subtitle", lambda a: True, step_hardsub_subtitle_source),
        Step("hardsub_fontsdir", lambda a: True, step_hardsub_fontsdir),
        Step("video_codec", lambda a: True, step_hardsub_video_codec),
        Step("use_gpu", lambda a: True, step_hardsub_use_gpu),
        Step(
            "nvenc_multipass",
            lambda a: nvenc_multipass_prompt_applicable(a),
            lambda a: ask_nvenc_multipass_if_applicable(a, workflow_name="HardSub", quality_oriented=True),
        ),
        Step("hardsub_quality", lambda a: True, step_hardsub_quality),
        Step("hardsub_hdr", lambda a: True, step_hardsub_hdr_handling),
        Step("hardsub_audio", lambda a: True, step_hardsub_audio_mode),
        Step("hardsub_audio_container", lambda a: True, step_hardsub_audio_container_policy),
        Step("color_range", color_range_prompt_applicable, step_color_range),
        Step("start_now", lambda a: True, step_hardsub_start_now),
    ]
```

The hand-off happens inside the LAST step:
`ffmwiz/wizard_flow_b.py:391-395`

```python
def step_hardsub_start_now(answers: dict[str, Any]) -> None:
    ensure_color_range_resolved(answers, workflow="HardSub")
    wizard_base.log_and_warn_pixel_format(answers)
    cmd = wizard_build_b.build_hardsub_command(answers)
    answers["cmd"] = cmd
```

`build_hardsub_command` has exactly one call site in the whole package
(`grep -rn "build_hardsub_command" ffmwiz/` → `wizard_build_b.py:138` the
definition, `wizard_flow_b.py:394` the call, plus `__all__` and one unrelated
log label). That single edge is what nothing tests.

**Mode 15's stage machine**, `ffmwiz/trackmanager.py:575-578` — the comment
states the contract the new suite must pin:

```python
def _run_track_manager_single(answers: dict[str, Any]) -> tuple[int, float] | None:
    # Stage machine so Back ('0') goes ONE step back instead of cancelling the
    # whole mode: source -> remove -> externals -> loudnorm -> confirm.
    stage = "source"
```

(The comment lists five stages; the code has six — `metadata` sits between
`loudnorm` and `confirm`, at line 616.) The hand-off is at line 647:

```python
            cmd = build_track_manager_command(answers["ffmpeg"], input_path, remove_specs, extra_items, output_path, answers)
```

**Where to patch.** These modules use `from ... import *`, so a call site reads
its OWN module global. Patch the module that CALLS the name, not the one that
defines it:

| Symbol | Patch on |
|---|---|
| `build_join_copy_command`, `build_join_near_quality_command`, `build_join_audio_encode_command`, `print_join_summary`, `services`, `wizard`, `appio`, `run_ffmpeg_with_progress` | `ffmwiz.modes_join` |
| every hardsub `Step` function, `nvenc_multipass_prompt_applicable`, `color_range_prompt_applicable`, `step_hardsub_start_now`, `run_ffmpeg_with_progress` | `ffmwiz.modes_b` |
| `build_hardsub_command` (called qualified, as `wizard_build_b.build_hardsub_command`) | `ffmwiz.wizard_build_b` |
| `ask_track_manager_source`, `ask_track_remove_specs`, `_track_manager_collect_externals`, `_track_manager_ask_loudnorm`, `build_track_manager_command`, `appio`, `run_ffmpeg_with_progress` | `ffmwiz.trackmanager` |

**Import those modules from the `ffmwiz` PACKAGE, not off `FFmWiz`.** The entry
script re-exports only some submodules by name: `FFmWiz.modes_join`,
`FFmWiz.trackmanager`, `FFmWiz.modes`, `FFmWiz.wizard` and `FFmWiz.appio`
resolve, but `FFmWiz.modes_b`, `FFmWiz.wizard_build_b` and
`FFmWiz.wizard_flow_b` raise `AttributeError`. `command_gen_base._home_module`
records the same trap at `tests/command_gen_base.py:26-27`: *"Resolved on the
package, not on the FFmWiz entry script, which only re-exports a few of the
submodules by name."* So write
`from ffmwiz import modes_join, modes_b, trackmanager, wizard_build_b`
alongside `import FFmWiz`.

All of these are confirmed present as module globals. Check before you rely on
one:

```
python -c "
import sys; sys.path.insert(0,'.'); import FFmWiz
from ffmwiz import modes_join, modes_b, trackmanager, wizard_build_b
print('build_join_copy_command' in vars(modes_join),
      'step_hardsub_start_now' in vars(modes_b),
      'build_hardsub_command' in vars(wizard_build_b),
      'ask_track_remove_specs' in vars(trackmanager))"
```
→ `True True True True`.

`tests/command_gen_base.py:20-34` provides `_home_module(name)`, which finds the
module that DEFINES a name. It is the right helper for restoring a patched
wizard STEP function, and the wrong one for the builders above — its search
list does not contain `modes_join`, `modes_b`, `modes_transform` or
`wizard_build_b`, so it would return the definer or the facade instead of the
module the call site actually reads. Prefer
`unittest.mock.patch.object(<module>, <name>, ...)`, which restores exactly
what it replaced.

**Structural models — read both before writing anything:**

- `tests/test_folder_job_isolation.py:157-197`, the `_drive()` helper. It is the
  closest thing in the repo to a mode-driver test: it stubs only the questions,
  runs the REAL `FFmWiz.run_folder_encode_mode`, collects every patch in a
  `contextlib.ExitStack`, spies on the hand-off, and redirects stdout into a
  buffer it returns for failure messages. Copy that shape.
- `tests/test_command_cut_join_folder.py:602-628`
  (`test_step_cuts_rejects_archived_gui_shortcut`) for scripting a text prompt
  off a finite iterator:

```python
        prompts = iter(["g", "n"])
        ...
            FFmWiz.appio.ask_raw = lambda _prompt: next(prompts)
```

- `tests/test_command_cut_join_folder.py:541-600`
  (`test_copy_cut_back_from_method_returns_to_output_step`) for asserting the
  ORDER a driver visits its steps in, including a Back.

**Repo conventions to match**: a module docstring that states the defect or gap
the file exists for, in past tense, with measured evidence where there is any
(read the docstring of `tests/test_folder_job_isolation.py:1-22`). One
behaviour per test. A comment naming the real-world failure each test pins.
`self.own({...})` from `artifact_guard.NoLeakedArtifacts` for any answers dict
handed to a builder that may lease temp directories.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| The three new suites | `python tests/run_suite.py -k mode_driver -j 2` | `OK` |
| One suite, serial | `python tests/run_suite.py -k mode_driver_join -j 1` | `OK` |
| Neighbouring suites | `python tests/run_suite.py -k join -k hardsub -k trackmanager -j 2` | `OK` |
| Full suite, parallel | `python tests/run_suite.py -j 4` | `OK`, 0 failures |

**A repository hook blocks raw `python -m unittest`. Always go through
`tests/run_suite.py`.**

`-k` matches test MODULE names by substring and repeats union, so naming all
three files `test_mode_driver_*.py` makes `-k mode_driver` run exactly them.

## Three warnings that will otherwise cost you a debugging session

**1. A prompt stub that returns a CONSTANT hangs forever.** These drivers
re-ask on a rejected answer — `ffmwiz/modes_join.py:252-257` loops on a missing
file and on a duplicate file, `ffmwiz/trackmanager.py:560-568` loops until the
scope answer is `1` or `2`, and `run_capability_cache_menu` loops until a valid
selection. `mock.patch.object(appio, "ask_raw", return_value="x")` therefore
spins without a timeout. Every text prompt stub must run off a finite script:

```python
        replies = iter(["...", "...", ""])
        stack.enter_context(mock.patch.object(
            appio, "ask_raw", lambda *a, **k: next(replies)))
```

`StopIteration` then fails the test loudly with a stack trace pointing at the
prompt that asked one time too many. `return_value=` is safe only for a
yes/no prompt the driver asks a bounded number of times — which is why
`tests/test_folder_job_isolation.py:184` can use it for `ask_yes_no`.

**2. The back token is `0`, not `"b"`.**
`ffmwiz/core/constants.py:728` is `BACK_INPUT_TOKENS = {"0", "۰", "٠"}`, and
`is_back_value(value)` (`ffmwiz/support/L00_misc_b.py:329-333`) only accepts
`"b"`/`"back"` when a caller passes `allow_text=True`. Two local exceptions
that will mislead you if you assume one rule:

- Mode 12's file prompt has its own meaning for `b`:
  `ffmwiz/modes_join.py:242-247` treats `value.lower() in {"b", "back"}` as
  *"remove the previous file and re-enter it"*, not as Back. A script that
  answers `"b"` there will loop, not exit.
- `run_capability_cache_menu` (`ffmwiz/modes.py:122`) accepts
  `{"", "0", "b", "back"}` as return.

**3. A monkeypatch left installed leaks into the NEXT test module.**
`tests/run_suite.py:8-13` states the contract: *"Each worker process runs ONE
test module to completion, pulling the next module off the queue as it frees
up ... Module-level isolation is what keeps it safe: several suites monkeypatch
module globals such as `appio.note`, which is only sound while one module owns
its interpreter."* A patch that survives a test survives into whatever module
that worker picks up next, so the failure appears in an unrelated suite and
only at `-j 4`. Every stub must be undone by `mock.patch.object` in a `with` /
`ExitStack`, by `self.addCleanup(...)`, or by a `try/finally`. Never by a bare
assignment.

## Scope

**In scope** (create these three; modify nothing else):
- `tests/test_mode_driver_join.py` (create)
- `tests/test_mode_driver_hardsub.py` (create)
- `tests/test_mode_driver_track_manager.py` (create)

**Out of scope** (do NOT touch):
- Every file under `ffmwiz/` and `FFmWiz.py`. **This plan changes no production
  code.** These are characterization tests: they record what the drivers do
  today so a future change cannot alter it silently. If a test disagrees with
  the code, the TEST is wrong unless you can prove otherwise — see the STOP
  conditions.
- The existing suites named as structural models. Read them; do not edit them.
- `build/lib/` — a stale packaged copy of the whole package.
- The six modes listed in "Verdicts for the other six". Do not write suites for
  them in this plan.

## Git workflow

- Branch: `advisor/008-mode-driver-characterization-tests`
- One commit per suite is fine. Message style is conventional commits, short
  subject only — see `git log --oneline -5` (e.g.
  `fix: let the NVENC tests run on real hardware`).
- Do NOT push or open a PR.

## Steps

### Step 1: Read the two structural models and confirm the harness runs

Before writing a line, read `tests/test_folder_job_isolation.py` in full and
`tests/test_command_cut_join_folder.py:541-660`. Then confirm the existing
driver tests still pass on your machine, so a later failure is yours:

**Verify**: `python tests/run_suite.py -k folder_job_isolation -k command_cut_join_folder -j 2` → `OK`

### Step 2: `tests/test_mode_driver_join.py` — mode 12

Drive the real `FFmWiz.run_join_videos_mode` with every question stubbed and
every builder spied on. Stub these, all on `ffmwiz.modes_join`:

- `appio.ask_raw` — a finite script of file paths, then whatever the branch
  needs. Create the paths as real empty files under a `tempfile` directory the
  test cleans up: the driver checks `path.exists()` and `path.is_file()` at
  `ffmwiz/modes_join.py:252` before it probes anything.
- `services.join_load_media_item(answers, path, allow_audio_only=True)` — return
  a fixture item dict. Copy the shape from
  `tests/test_stage_geometry_ownership.py:154-161` (`_item`): it needs at least
  `path`, `probe`, `format`, `streams`, `video_streams`, `audio_streams`,
  `duration`. Stubbing this is what removes the ffprobe dependency, so the
  suite runs everywhere.
- `wizard.ask_join_add_another(prompt)` — returns `bool | str`
  (`ffmwiz/wizard_steps.py`); return `False` to stop collecting files.
- `wizard.step_output_location(answers)` — set `output_location` on the dict.
- `appio.ask_yes_no` — the near-quality question and "Start FFmpeg now?".
- `run_ffmpeg_with_progress` — return `(0, 0.0)` and record the argv. Nothing
  may actually execute.

Tests to write:

1. `test_two_copy_compatible_inputs_use_the_copy_builder` — the argv the driver
   hands to `build_join_copy_command` names both inputs, in the order they were
   entered, and neither of the other two builders was called.
2. `test_audio_only_inputs_use_the_audio_encode_builder_and_default_the_bitrate`
   — items with no `video_streams` reach `build_join_audio_encode_command`, and
   `answers["audio_bitrate_kbps"]` was set from the highest source bitrate
   before the call (`ffmwiz/modes_join.py:344-352`).
3. `test_mixing_audio_only_and_video_inputs_is_refused_before_any_builder` —
   `ffmwiz/modes_join.py:306-312` returns `None` and calls no builder. **The
   assertion that matters is "no builder ran" — a refusal that still builds a
   command is the defect this pins.**
4. `test_declining_the_near_quality_question_runs_nothing` — answering `n` at
   `ffmwiz/modes_join.py:356` returns `None` with no
   `run_ffmpeg_with_progress` call.
5. `test_declining_start_now_preserves_the_generated_inputs` —
   `ffmwiz/modes_join.py:390-395` calls `preserve_artifacts_for_manual_run`
   rather than cleaning up, because the printed command references those files.
6. `test_the_summary_is_given_the_arguments_this_mode_computes` — a
   characterization test: Mode 12 calls `print_join_summary` with three
   positional arguments, where the wizard's join path passes a fourth
   (`ffmwiz/wizard_b.py:202-204`). Record the current call shape so a future
   change to either path is deliberate. Do not "fix" the difference here.

**Verify**: `python tests/run_suite.py -k mode_driver_join -j 1` → `OK`

### Step 3: `tests/test_mode_driver_hardsub.py` — mode 9

Replace each of the 14 `Step` functions on `ffmwiz.modes_b` with a filler that
writes the answers that step is responsible for, then let the REAL
`step_hardsub_start_now` run so `build_hardsub_command` is exercised through
its only call site. Spy on `wizard_build_b.build_hardsub_command` to
capture the answers it received, and stub
`modes_b.run_ffmpeg_with_progress`.

This is the same technique as
`tests/test_command_cut_join_folder.py:541-596`, which replaces
`FFmWiz.wizard.step_input_path` and friends and then asserts the visiting
order.

Tests to write:

1. `test_the_driver_visits_every_applicable_step_in_order` — each filler
   appends its name to a list; assert the exact sequence. This is what would
   have caught a question asked but never wired.
2. `test_the_subtitle_answers_reach_the_builder` — the argv from
   `build_hardsub_command` carries the subtitle filter for the answers the
   fillers wrote (internal-subtitle case).
3. `test_an_external_subtitle_file_reaches_the_builder` — the other branch of
   `step_hardsub_subtitle_source`.
4. `test_a_back_from_a_middle_step_re_asks_the_previous_one` — raise
   `FFmWiz.Back()` from one filler on its first call; the previous step's
   filler must run a second time. The loop under test is
   `ffmwiz/modes_b.py:226-237`, which is a hand-written copy of
   `wizard_base.run_mode_steps` (`ffmwiz/wizard_base.py:518-540`) — two copies
   of a Back loop, only one of which has ever been executed by a test.
5. `test_back_from_the_first_step_leaves_the_mode` — `ffmwiz/modes_b.py:234`
   re-raises, and the wrapper at `ffmwiz/modes_b.py:195-200` catches it and
   returns `None`.
6. `test_declining_start_now_runs_nothing` — `answers["start_now"] = False`
   returns `None` with no `run_ffmpeg_with_progress` call
   (`ffmwiz/modes_b.py:239-241`).
7. `test_the_nvenc_step_is_skipped_when_it_does_not_apply` — stub
   `nvenc_multipass_prompt_applicable` to `False` and assert its filler never
   ran. It is the only conditional gate in the table; the others are
   `lambda a: True`.

**Verify**: `python tests/run_suite.py -k mode_driver_hardsub -j 1` → `OK`

### Step 4: `tests/test_mode_driver_track_manager.py` — mode 15

Drive `FFmWiz.run_track_manager_mode`. Stub, all on `ffmwiz.trackmanager`:
`ask_track_manager_source`, `ask_track_remove_specs`,
`_track_manager_collect_externals`, `_track_manager_ask_loudnorm`,
`appio.ask_raw` (the scope menu — a finite script), `appio.ask_yes_no` (the
metadata question and "Start FFmpeg now?"), and `run_ffmpeg_with_progress`.
Spy on `build_track_manager_command`.

Tests to write:

1. `test_the_removal_specs_and_externals_reach_the_builder` — assert the exact
   positional arguments `build_track_manager_command` received
   (`ffmwiz/trackmanager.py:647`): ffmpeg, input path, normalised remove specs,
   external items, output path, answers.
2. `test_the_output_is_a_TrackEdit_sibling_of_the_source` — the fifth argument
   is `<stem>_TrackEdit<ext>` in the SOURCE directory
   (`ffmwiz/support/L02.py:708-711`). **This is the assertion that matters most
   in the whole plan: this mode is the only one that writes into the user's own
   media folder, and a wrong path here overwrites or clutters a library.**
3. `test_a_job_that_changes_nothing_builds_no_command` — no removals, no
   externals, loudnorm off → `ffmwiz/trackmanager.py:632-634` notes "nothing to
   do" and returns `None` with no builder call.
4. `test_a_subtitle_the_container_cannot_carry_is_refused_before_the_builder` —
   `ffmwiz/trackmanager.py:640-645` returns `None` after printing the problems.
   Assert no builder call and no execution: the comment there says the point is
   to refuse *"instead of failing at header-write time and leaving a 0-byte file
   next to the source."*
5. `test_back_from_the_externals_stage_returns_to_the_remove_stage` — raise
   `FFmWiz.Back()` from `_track_manager_collect_externals` on its first call and
   assert `ask_track_remove_specs` ran twice. The stage machine at
   `ffmwiz/trackmanager.py:575-630` is six stages of hand-rolled Back handling
   with no test.
6. `test_the_metadata_answer_reaches_the_builder` — the `y/n` at
   `ffmwiz/trackmanager.py:615-630` becomes
   `answers["track_manager_keep_metadata"]`, and the builder sees it.
7. `test_declining_start_now_runs_nothing` — `ffmwiz/trackmanager.py:664-666`.

**Verify**: `python tests/run_suite.py -k mode_driver_track_manager -j 1` → `OK`

### Step 5: The mutation check — one per suite

A driver test that passes whether or not the hand-off happens is worse than no
test, because it looks like coverage. Prove each suite bites, then restore the
code:

| Suite | Break | Expected |
|---|---|---|
| join | In `ffmwiz/modes_join.py:340`, swap `build_join_copy_command` for `build_join_near_quality_command` | at least one join test FAILS |
| hardsub | In `ffmwiz/wizard_flow_b.py:395`, change `answers["cmd"] = cmd` to `answers["cmd"] = []` | at least one hardsub test FAILS |
| track manager | In `ffmwiz/trackmanager.py:647`, pass `[]` for `remove_specs` | at least one track-manager test FAILS |

After each, run `python tests/run_suite.py -k mode_driver -j 2`, note which test
failed, and **restore the file with `git checkout -- <path>`**. Report all three
results.

**Verify**: `git status --short` afterwards shows the three new test files and
nothing else. Any `ffmwiz/` file listed means a mutation was not restored.

### Step 6: Parallel-safety and the full suite

Run the new suites in the same worker pool as everything else. A patch that
leaks out of a module only fails here:

**Verify**: `python tests/run_suite.py -j 4` → `OK`, 0 failures. Run it twice.
A failure that moves to a different module between runs is a leaked stub in one
of the new files — find it before finishing.

## Verdicts for the other six

Write these into the module docstring of `tests/test_mode_driver_join.py`, under
a heading like `Modes deliberately left without a driver suite`, so the next
person auditing coverage finds the reasoning next to the work rather than
repeating it.

**Not worth doing:**

- **Mode 7, Media Info** (`ffmwiz/modes_mediainfo.py:521`) — produces reports
  into a dedicated reports directory (`services.default_media_reports_dir()`)
  and never touches or produces media. A wrong report is visible to the person
  who asked for it; a wrong encode is not.
- **Mode 14, Capability cache menu** (`ffmwiz/modes.py:107`) — diagnostics only:
  view, re-probe, clear. Its own docstring says *"Never affects user settings,
  logs, or secrets."* Everything it clears is regenerated on the next probe.

**Deferred, with reason:**

- **Mode 5, Add files to video** (`ffmwiz/modes.py:610`) — stream-copy only and
  it refuses anything it cannot copy (`ffmwiz/modes.py:632-638`), so the blast
  radius is a container it declined to write. Worth a suite after the three
  above.
- **Mode 6, Extract stream** (`ffmwiz/modes_b.py:99`) — writes new sidecar files
  and never modifies the source. Its driver is a 4-entry table on the SHARED
  `wizard_base.run_mode_steps`, so the loop itself is exercised by mode 10 and
  the audio tools; only the four steps are unique.
- **Mode 10, Video speed / reverse** (`ffmwiz/modes_transform.py:99`) — also on
  the shared `run_mode_steps`, and its reverse path is already covered at the
  pipeline level by `tests/test_bounded_reverse_pipeline.py` and
  `tests/test_stage_geometry_ownership.py`.
- **`run_audio_cut_mode`** (`ffmwiz/modes_transform.py:146`) — **not reachable.**
  `grep -rn "run_audio_cut_mode" --include=*.py .` (ignoring `build/lib/`) finds
  only its own definition and its `__all__` entry; `FFmWiz.run_one_job` never
  dispatches to it. Testing it would pin dead code. The real question is whether
  it should be deleted or wired to a menu entry, and that is a decision for a
  separate plan, not a test.

## Test plan

Three new modules, no production change:

- `tests/test_mode_driver_join.py` — 6 tests (Step 2), modelled on
  `tests/test_folder_job_isolation.py:157-197` for the harness and
  `tests/test_command_cut_join_folder.py:603-628` for the prompt script.
- `tests/test_mode_driver_hardsub.py` — 7 tests (Step 3), modelled on
  `tests/test_command_cut_join_folder.py:541-600` for the step-order assertion.
- `tests/test_mode_driver_track_manager.py` — 7 tests (Step 4), same harness.

None of them needs `ffmpeg` or `ffprobe`: the probe seam
(`services.join_load_media_item`, `ask_track_manager_source`) and the executor
(`run_ffmpeg_with_progress`) are both stubbed. Keep it that way — a driver
suite that skips on a machine without ffmpeg is coverage that disappears
exactly when someone is refactoring on a laptop.

Every test asserts the ARGUMENTS the driver hands its builder, or that it
handed none. "The mode ran" is not an assertion.

**Verify**: `python tests/run_suite.py -k mode_driver -j 2` → `OK`, 20 tests;
`python tests/run_suite.py -j 4` → `OK`, 0 failures.

## Done criteria

ALL must hold:

- [ ] The three files exist and `python tests/run_suite.py -k mode_driver -j 2` → `OK`
- [ ] `python tests/run_suite.py -j 4` → `OK`, 0 failures, run twice
- [ ] The nine-driver probe from "Why this matters" now shows a test file for `run_join_videos_mode`, `run_hardsub_encode_mode` and `run_track_manager_mode`
- [ ] No suite reports `skipped` for a missing capability — the new tests need none
- [ ] The Step 5 mutation check was performed for all three suites; each went red; each production file was restored
- [ ] `git status --short` lists exactly three new files under `tests/` and nothing under `ffmwiz/` or `FFmWiz.py`
- [ ] The six verdicts are recorded in `tests/test_mode_driver_join.py`'s docstring
- [ ] `grep -rn "return_value" tests/test_mode_driver_*.py` — every hit is on a yes/no prompt or a builder spy, never on `ask_raw`

## STOP conditions

Stop and report back (do not improvise) if:

- The excerpts or line numbers above do not match the live code (drift since
  `aaf0aed`).
- A test hangs. That is warning 1: a prompt stub returned a constant to a step
  that re-asks. Do not add a timeout — replace the constant with a finite
  script.
- Writing a test reveals what looks like a real defect in a driver. **Do not fix
  it in this plan.** Write the characterization test for the behaviour as it is,
  mark it with a comment naming what you think is wrong, and report it. This
  plan's contract is that no production file changes.
- A driver cannot be driven without patching something inside `ffmwiz/` that is
  not a prompt, a probe, a builder or the executor. That means the seam does not
  exist yet, and adding one is a production change — report which driver and
  which call.
- The full suite fails only at `-j 4` and passes at `-j 1`. That is warning 3: a
  stub leaked out of a module. Find it; do not lower the worker count.
- A mutation from Step 5 leaves every new test green. The suite is not pinning
  the hand-off it claims to.

## Maintenance notes

For whoever owns this code next:

- **The rule this establishes**: a mode driver is testable by stubbing four
  kinds of seam — the prompts (`appio.ask_raw`/`ask_yes_no` and the `step_*`
  functions), the probe (`services.join_load_media_item` and friends), the
  builder, and `run_ffmpeg_with_progress`. A new mode that cannot be driven
  that way has a seam missing, and that is worth noticing at review time rather
  than at test time.
- Six drivers remain untested. The verdicts section says which and why; extend
  it rather than re-deriving it.
- Mode 9's Back loop (`ffmwiz/modes_b.py:226-237`) is a hand-written duplicate
  of `wizard_base.run_mode_steps` (`ffmwiz/wizard_base.py:518-540`). Once the
  Step 3 tests exist, replacing it with a call to the shared helper becomes a
  safe, small refactor — deliberately not attempted here, because this plan
  changes no production code.
- A reviewer should check the Step 5 mutation results hardest. Characterization
  tests are the easiest kind to write green-by-accident, and a suite that passes
  against a broken hand-off is a false green in the exact layer this plan exists
  to cover.
