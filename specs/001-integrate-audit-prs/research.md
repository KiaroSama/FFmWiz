# Review findings: the three audit submissions

**Date**: 2026-09-19 | **Baseline main**: `10649901c3a97b98bf0445d560b1510f3e43d5e8`

This is Phase 0 of the plan: what an independent read of each diff found, recorded before any
merge. It is updated as each submission is reviewed.

## Measured facts (recorded before reading any diff)

| Submission | Head | Branch | Files | +/- | Checks |
|---|---|---|---|---|---|
| #7 (A06) | `a5c92f28e779b14f9dd42a86c14a6315dbb2ddad` | `audit/20260919-runner-lifecycle` | 10 | +732 / -184 | CLEAN |
| #8 (A01) | `5cc24361b2064c76b0fcce5263ad1fc5ee8cbd61` | `audit/20260919-transitive-file-safety` | 5 | +554 / -110 | CLEAN |
| #10 (A03/A04) | `3a7df0aa775fb5aaf22dca43e7f33b0eb3ce8b39` | `audit/20260919-editor-resource-clock` | 9 | +611 / -215 | CLEAN |
| #9 (A03/A04, unlisted) | `9a43519333998c9b8ee2e151622e60f0d924817e` | `audit/20260919-gui-failure-ownership` | 8 | +605 / -47 | **2 FAILING** |

All three scoped heads match the brief exactly, and `main` is still the brief's baseline, so the
combined verification the brief cites has not been invalidated by a moved input.

## Finding R-0: the brief omits an open pull request on the same workstream

**Decision**: report it, and apply the maintainer's chosen disposition only on proof.

**Rationale**: #9 is not an earlier revision of #10 — the two are *different implementations* of the
same A03/A04 repair. They share only three files (`gui_editor_unified.py`, `gui_qml_bridge.py`,
`test_gui_editors.py`). #9 adds a module `gui_resources.py` and three test modules that #10 does not
have; #10 adds `gui_waveform_decode.py`, `gui_worker_artifacts.py` and two test modules that #9 does
not have. "Superseded" therefore cannot be settled by comparing file lists: it needs #9's unique
tests checked against #10's behaviour.

**Alternatives considered**: closing #9 on the strength of its two red checks alone — rejected,
because a failing check says the submission does not pass, not that its content is already covered.

## Findings per submission

Recorded as each diff is read. Each finding names a file, a line and a consequence; each deliberate
non-fix names its reason.

### PR #7 — A06 runner final outcome and owned process lifetime

_Pending review._

### PR #8 — A01 transitive reads and side-effect writes

_Pending review._

### PR #10 — A03/A04 GUI artifact lifetime and waveform stream/clock

_Pending review._

## Review findings (T021)

Reviewed from the diffs and confirmed in the source, not from the submissions'
own reports. Resolution is one of fixed / accepted-with-reason / out-of-scope.

### PR #7 - runner final outcomes (head a5c92f2)

| ID | Anchor | Finding | Resolution |
|----|--------|---------|------------|
| F7-1 | `tests/run_suite.py`, scratch creation | `mkdir(parents=True, exist_ok=False)` replaces the previous delete-then-create. A recycled PID whose directory survived a killed runner now aborts the run with an unhandled `FileExistsError` instead of a diagnosed refusal. | accepted-with-reason: refusing to delete a directory this process cannot prove it owns is the safer failure, and the abort is loud. The message quality is cosmetic. |
| F7-2 | `tests/run_suite_windows.py`, `Job.__init__` | The job is a *named* kernel object in the `Local\` namespace, so any process in the same session could open it by name. | accepted-with-reason: the name carries a `uuid4`, so it is unguessable, and the child validates the `Local\FFmWizSuite-` prefix before joining. |
| F7-3 | `tests/run_suite_process.py`, `ProcessScope.stop` | `process.wait(timeout=STOP_SECONDS)` may raise `TimeoutExpired`, which is not caught locally. | accepted-with-reason: it propagates to `_execute`, which records a `crash`. A stop the OS refused must never read as a pass, and it does not. |
| F7-4 | `tests/test_command_io_grammar.py` | The edited existing test replaced `assertNotEqual(0, returncode)` with an identity check plus an unconditional source-digest assertion. | fixed (strengthening): the old assertion encoded a platform fact about FFmpeg URI handling and proved nothing about the guard. The new one checks the actual safety property on both branches. |
| F7-5 | `tests/test_powershell_invocation.py` | The `skipTest` on a denied hardlink became a `copy2` fallback, and a new test proves the fallback path. | fixed (strengthening): a skip for a capability the runner has is a suite that silently shrank. |

### PR #8 - transitive reads and side-effect writes (head 5cc2436)

| ID | Anchor | Finding | Resolution |
|----|--------|---------|------------|
| F8-1 | `L00_command_io.looks_like_concat_list` | Detection narrowed: the old code did `.strip().lower()` on a decoded line, the new code compares raw bytes against `ffconcat version` at offset 0. Leading whitespace or different case is no longer auto-detected. | accepted-with-reason, and correct: this now matches the libavformat concat probe, which is a case-sensitive `memcmp` at offset 0. A file FFmpeg would not auto-detect as a concat list is one whose members it never reads. Explicit `-f concat` still forces member expansion. |
| F8-2 | `L00_command_io.looks_like_concat_list` | The size pre-check that silently returned `False` for an oversized list was removed. | fixed: an oversized list used to be unprotected *silently*. The refusal now comes from `concat_list_members` as a reported problem, and `test_oversized_autodetected_concat_cannot_hide_a_member` proves it reaches the guard. |
| F8-3 | `L00_command_io`, module surface | `FILTER_FILE_OPTIONS` remains in `__all__` but no longer has an internal caller after `filter_graph_files` was rewritten. | out-of-scope: it is part of the declared public surface, and removing an exported name is an API change this assignment did not ask for. |
| F8-4 | `L00_command_io.command_writes` | On a POSIX-style `/dev/null` argument evaluated on Windows, `_names_a_file` does not recognise the foreign spelling, so a path named `null` is collected as a write. | out-of-scope and pre-existing: `_names_a_file` predates this submission, and the only consequence is an extra write entry that can at worst cause a refusal, never an unguarded overwrite. |
| F8-5 | `tests/test_command_io_lavfi.py` | Carries `test_lavfi_statistics_with_an_unrelated_path_remain_usable`, which encodes to a free destination and asserts the source is byte-identical. | positive control present: the guard is proven not to refuse every job. |

### PR #10 - GUI artifact lifetime and waveform clock (head 3a7df0a)

| ID | Anchor | Finding | Resolution |
|----|--------|---------|------------|
| F10-1 | `gui_qml_bridge._discard_proxy` | Changed from a `@staticmethod` to an instance method taking `proc`. | verified: all four internal call sites are updated in the same diff, and the added `proc=None` default keeps the single-argument form working. |
| F10-2 | `gui_worker_artifacts.WorkerTemporaryDirectory.cleanup` | Raises `OSError` when an owner is still active, where the replaced `tempfile.TemporaryDirectory.cleanup` did not. | verified against the unchanged caller: `cleanup_reverse` already wraps the call in `try/except OSError` with a bounded five-attempt retry and returns `False`. The new type is the one that caller already handles. |
| F10-3 | `gui_waveform_decode.py` | New shared module, both engines re-export from it. | verified: no import cycle (the new module imports nothing from either engine), and the old public import paths are preserved as aliases, as the split rule requires. |
| F10-4 | `gui_qml_waveform.compute_wave_key` | The key is now a structured, sorted JSON document hashed with SHA-256, including per-segment `picture_clock_offset`, `audio_stream` and `has_audio` plus a `st_dev`/`st_ino`/`st_size`/`st_mtime_ns` stamp. | verified: every field that changes the decoded PCM changes the key, which is the A04 defect. `test_each_decode_relevant_segment_field_changes_the_key` fails without it. |
| F10-5 | `tests/test_gui_editors.py` | Two existing tests were edited. | judged: `test_classic_engine_has_the_same_silence_branch` moved from grepping module source to calling `build_classic_waveform_args` and asserting on the produced arguments, which is a strengthening. `test_proxies_live_in_one_owned_temp_dir` swapped one source-text assertion for another because the implementation changed: neutral, still a source-grep test. |

### Uncovered paths (T022)

| Path | Missing capability | Exact operation that would close it |
|------|--------------------|--------------------------------------|
| Native Qt branches of `test_gui_child_ownership_artifacts` | PySide6 is not installed on the reviewing workstation | the `gui` job in `python-smoke.yml`, which installs PySide6 and runs with `QT_QPA_PLATFORM=offscreen` |
| Windows Job Object ownership (`run_suite_windows.py`) | exercised only on the Windows runner | the `tests` matrix job in `python-smoke.yml` |
| POSIX session and `/proc` branches of `run_suite_process._group_active` | this workstation is Windows | the `portable` job in `portable-tests.yml` (ubuntu-24.04) |
| NVENC and GPU encoder paths | no NVIDIA GPU on any available runner | unchanged from before this integration, and no submission claimed to cover it |

### PR #9 disposition analysis (T024)

PR #9 (`audit/20260919-gui-failure-ownership`) is a *different implementation* of the
same A03/A04 workstream, not an earlier revision of #10. It is branched from the old
baseline, so merging it now would revert PRs #7 and #8.

Mapping its unique modules against `main` after #10:

| #9 carries | Superseded by | Verdict |
|------------|---------------|---------|
| `tests/test_join_segment_clock_selection.py` (4 cases) | `tests/test_waveform_stream_contract.py` - single-input selection, join inheritance, per-segment override and silent-segment handling are each covered, and additionally compared sample-for-sample against real decoded PCM | fully superseded |
| `ffmwiz/gui/gui_resources.py` (`OwnedTemporaryDirectory`) | `ffmwiz/gui/gui_worker_artifacts.py` (`WorkerTemporaryDirectory`) - same job, stricter: the finalizer itself checks owner idleness rather than relying on a caller having invoked `preserve()` | superseded by the stronger form |
| `test_gui_child_ownership_residual.py`: reverse pipe failure, finalizer-vs-failed-shutdown, both thread-launch rollbacks, PCM delete failure | `test_gui_child_ownership_artifacts.py` plus the pre-existing `test_gui_child_ownership.py` (`test_a_launch_failure_is_reported_and_owns_nothing`, `test_a_failed_communicate_does_not_release_a_live_child`) | superseded |
| `test_gui_child_ownership_classic_wave.py`: failed decode not cached, decoder that cannot start not cached | pre-existing `test_gui_child_ownership.py` (`test_a_partial_decode_that_exits_nonzero_never_becomes_the_cache`, `test_a_decoder_that_cannot_even_start_is_reported_not_cached`) | superseded |
| **`test_odd_pcm_sample_is_not_cached`, `test_empty_pcm_is_not_cached`** | nothing in `main` | **NOT superseded** |
| **`test_valid_json_of_the_wrong_type_returns_an_error_reply`, `test_invalid_reverse_payload_never_registers_work`** | nothing in `main` | **NOT superseded** |

Conclusion: every *repair* in #9 is present in `main` in an equal or stronger form, so
closing it as superseded does not lose a fix. Four *test cases* it alone carries are
not covered anywhere in `main`. They are recorded here as follow-up work rather than
being lost with the request.

### Optional decisions from the brief (section 7)

| Decision | Status | Reason |
|----------|--------|--------|
| Retire or refresh the pinned combined-verification workflow | IMPLEMENTED | `.github/workflows/audit-integration.yml` pinned the baseline and two companion head SHAs and applied them as patches. Those patches are now in `main`, so `git apply --check` would fail on any manual dispatch. Its Windows/Qt/PowerShell/FFmpeg-7.1.1 coverage is already provided by the unfiltered full-suite job in `python-smoke.yml`, so the file was removed rather than re-pinned. |
| Keep the two new Linux workflows | IMPLEMENTED | `portable-tests.yml` and `source-safety.yml` add POSIX grammar and filesystem coverage the Windows matrix cannot provide. Both run on push to `main`. |
| Squash the submitted branches into single commits | REJECTED | Each branch history was inspected first. All commits carry coherent conventional subjects with no fixup or WIP noise, and PR #7 contains eight independent fixes whose separation is worth keeping for bisection. Merge commits were used. |
| Delete the merged submission branches | IMPLEMENTED | Each was deleted with its merge, their content being provably in `main`. GitHub retains restore capability. |
| Add regression coverage for defects found in review | DEFERRED | No review finding required a correction, so the red-to-green plus negative-control obligation was not triggered. The four uncovered cases inherited from PR #9 are recorded above as follow-up. |
