# Implementation Plan: Integrate the reviewed audit repairs into main

**Branch**: `main` (no feature branch; the three published PR branches are reused) | **Date**: 2026-09-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-integrate-audit-prs/spec.md`

## Summary

Three published pull requests carry four audit repairs that are absent from `main`. This plan
reviews each one against its own diff and the repository source, corrects what the review finds,
integrates the accepted work into `main`, and verifies the resulting commit. The guiding constraint
comes from this project's own recorded experience: a green CI run plus a confident author's report
is not a review, and the defects that survive green CI are the ones the report did not think to
mention.

## Technical Context

**Language/Version**: Python 3.10-3.13 (floor 3.10, enforced by CI)

**Primary Dependencies**: PySide6 6.11.2 at runtime; FFmpeg/ffprobe invoked as external programs;
the test suite and its runner are standard library only

**Storage**: N/A — the program writes media files the user names; no database

**Testing**: `tests/run_suite.py`, one supervised child interpreter per module; the heavy pass runs
in GitHub CI on the pushed SHA

**Target Platform**: Windows (primary); the portable subset also runs on Linux in CI

**Project Type**: desktop-app + CLI (single repository, layered package)

**Performance Goals**: N/A for this work — no performance change is intended or claimed

**Constraints**: no new runtime dependency; no file above 800 lines; every repair carries a
red-to-green regression plus a negative control; integration happens directly on `main`

**Scale/Scope**: 24 changed files across three submissions — roughly 1,900 added and 510 removed
lines, touching the test runner, the FFmpeg read/write boundary and both GUI editors

## Constitution Check

| Principle | Gate for this work | Status |
|---|---|---|
| I. Source media is never collateral | PR #8 widens the refusal to transitive reads and lavfi statistics writes; the review must confirm refusal happens before `Popen` and that a positive control still encodes | Applies — verified during review |
| II. A process is owned until proven gone | PR #7 (runner) and PR #10 (GUI artifacts) both act on this; the review must confirm ownership is released only on confirmed exit and that retained artifacts carry a reason | Applies — verified during review |
| III. Heavy pass in CI on the exact SHA | The combined tree, not the three branches, is what must be green; the final `main` commit must be correlated with every required check | Applies — gates completion |
| IV. No file above 800 lines | Three of these submissions add code to files this session just brought under the ceiling; the review must re-measure every touched file | Applies — re-measured after integration |
| V. A green result must be earned | No submitted change may weaken an assertion, remove a capability requirement, or add an expected-failure marker; the review must check the diffs for exactly that | Applies — checked per diff |

No principle is violated by the plan. Two carry a real risk the review is responsible for catching:
Principle IV, because all three submissions add lines to already-large files, and Principle V,
because two of the submissions also edit existing tests.

## Project Structure

### Documentation (this feature)

```text
specs/001-integrate-audit-prs/
├── spec.md              # what integration must achieve
├── plan.md              # this file
├── research.md          # the review findings, per submission
├── data-model.md        # the entities the integration reasons about
├── quickstart.md        # how to verify the integrated result
├── checklists/
│   └── requirements.md  # spec quality gate
└── tasks.md             # produced by /speckit-tasks
```

### Source Code (repository root)

```text
ffmwiz/
├── support/
│   ├── L00_command_io.py        # PR #8: the read/write contract
│   └── L00_command_grammar.py   # PR #8: new — filter/option token grammar
├── gui/
│   ├── gui_waveform_decode.py   # PR #10: new — the one shared decoder
│   ├── gui_worker_artifacts.py  # PR #10: new — retained-artifact registry
│   ├── classic/gui_editor_unified.py
│   └── modern/{gui_qml_bridge,gui_qml_waveform}.py
tests/
├── run_suite.py, run_suite_child.py
├── run_suite_process.py         # PR #7: new — the supervisor
├── run_suite_windows.py         # PR #7: new — Job Object ownership
└── test_*.py                    # the regression modules each PR adds
.github/workflows/
├── python-smoke.yml             # the existing seven checks
├── portable-tests.yml           # PR #7: new
├── source-safety.yml            # PR #8: new
└── audit-integration.yml        # PR #10: new, one-shot, to be retired on integration
```

**Structure Decision**: unchanged. Every new module lands in an existing tier — `support/` for the
command contract, `gui/` for the shared decoder and artifact registry, `tests/` for the runner
halves. No new top-level directory, no new dependency, no layer inversion.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Integration directly on `main` rather than a feature branch | The brief forbids manufacturing a branch to repeat published work, and the repository's closure rule permits a verified direct merge | A new branch would duplicate three branches that already exist and already carry green checks |
| Four new workflow files across the three submissions | Each submission verifies a capability the existing seven checks do not cover (portable Linux, source-safety, one-shot combined Qt) | Folding them into `python-smoke.yml` would make one job's failure hide another's, and the one-shot job is designed to be retired |

## Review procedure (Phase 0 → research.md)

Per submission, in the order the brief recommends (#7 → #8 → #10):

1. Read the full diff, not the title or the author's summary.
2. For every new guard, find the test that fails without it; for every edited existing test, decide
   whether the edit follows the implementation or lowers the bar.
3. Re-measure every touched file against the 800-line ceiling.
4. Check the claims in the PR body against the repository: file, line, behaviour.
5. Record findings and deliberate non-fixes; both are review outcomes, silence is not.

## Integration procedure (Phase 1 → quickstart.md)

1. Merge in dependency order, resolving conflicts in the combined tree rather than on a branch.
2. Retire the one-shot `audit-integration.yml` as the brief directs, since its pinned inputs are
   stale the moment integration lands.
3. Run the targeted suites locally as the light check; the full suite is CI's, on the pushed SHA.
4. Verify every required check against the final `main` commit.
5. Compare #9's unique tests against #10's behaviour before applying the agreed disposition.
