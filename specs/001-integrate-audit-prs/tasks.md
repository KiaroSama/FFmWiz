# Tasks: Integrate the reviewed audit repairs into main

**Input**: [spec.md](./spec.md), [plan.md](./plan.md), [research.md](./research.md),
[data-model.md](./data-model.md), [quickstart.md](./quickstart.md)

**Tests**: the repairs arrive with their own regression modules. No new test is written unless the
review finds a defect; then it carries a red-to-green regression plus a negative control, as the
constitution requires.

**Organization**: by user story. US1 is the MVP — the repairs present and working in `main`.

## Phase 1: Setup

- [X] T001 Record the starting state — `main` SHA, working-tree status, all open PR heads and check
      results — into specs/001-integrate-audit-prs/research.md
- [X] T002 Confirm the three scoped heads still match the brief and that `main` is still the cited
      baseline, so the published combined verification is not already invalidated

## Phase 2: Foundational (blocking)

- [X] T003 Read the full diff of PR #7 with `gh pr diff 7`, not its title or body
- [X] T004 [P] Read the full diff of PR #8 with `gh pr diff 8`
- [X] T005 [P] Read the full diff of PR #10 with `gh pr diff 10`
- [X] T006 For every edited EXISTING test across the three diffs, decide whether the edit follows the
      implementation or lowers the bar, and record the verdict in research.md
- [X] T007 Re-measure every file the three diffs touch against the 800-line ceiling in
      ffmwiz/ and tests/, since all three add lines to files recently brought under it

## Phase 3: User Story 1 — The repairs are actually in the shipped program (P1) 🎯 MVP

**Goal**: all four repaired behaviours present and working in `main`.

**Independent test**: check out `main` alone and run the three targeted suites from quickstart.md;
each ends OK with no skip blaming a capability the machine has.

- [X] T008 [US1] Review PR #7's supervisor for the invariant it claims — that a post-publication
      crash or hang overrules a passing record — in tests/run_suite_process.py, and confirm the
      regression in tests/test_run_suite_final_outcome.py fails without it
- [X] T009 [US1] Review PR #7's Windows ownership path in tests/run_suite_windows.py against the
      venv-redirector trap its body describes: the real interpreter, not the launcher, must join the
      job object before it imports test code
- [X] T010 [US1] Inspect each branch's commit list first and record squash-vs-merge with its
      reason, then merge PR #7 into main through the repository's permitted process
- [X] T011 [US1] Review PR #8's grammar split in ffmwiz/support/L00_command_grammar.py — graph-token
      unescaping separated from option-token unescaping — and confirm a quoted string merely
      mentioning `subtitles=` is not treated as a filter
- [X] T012 [US1] Review PR #8's lavfi write collection in ffmwiz/support/L00_command_io.py against
      the counterexample in the brief: a `psnr=stats_file=` destination hard-linked to the source
      must be refused before Popen with the source hash unchanged
- [X] T013 [US1] Confirm PR #8 keeps a positive control that still ENCODES to a free destination —
      a guard that refuses every job is not acceptable
- [X] T014 [US1] Merge PR #8 into main, resolving any conflict in the combined tree
- [X] T015 [US1] Review PR #10's artifact retention in ffmwiz/gui/gui_worker_artifacts.py: a denied
      terminate/kill must leave the PID alive AND its file present, recorded with a reason
- [X] T016 [US1] Review PR #10's single decoder in ffmwiz/gui/gui_waveform_decode.py and confirm both
      editors delegate to it, with the old public import paths preserved as aliases
- [X] T017 [US1] Confirm PR #10's cache identity includes the fields that change decoded PCM —
      picture origin, per-segment stream, audibility — and not merely the duration
- [X] T018 [US1] Merge PR #10 into main, resolving any conflict in the combined tree
- [X] T019 [US1] Retire or refresh .github/workflows/audit-integration.yml, whose pinned inputs are
      stale the moment integration lands, as the brief directs
- [X] T020 [US1] Run the three targeted suites from quickstart.md as the light check on the combined
      tree; the full suite belongs to CI
- [X] T020a [US1] Diff main-before against main-after for deletions nobody asked for, since this
      session restructured the same files the three diffs touch (FR-010)
- [X] T020b [US1] Execute quickstart.md section 5 and observe all four repaired behaviours from main
      itself, not only through their test modules (SC-001)

## Phase 4: User Story 2 — The review is a review, not a countersignature (P1)

**Goal**: findings that name a file, a line and a consequence, including what the submissions' own
reports did not mention.

**Independent test**: read research.md against the diffs; every finding is anchored and every
deliberate non-fix carries its reason.

- [X] T021 [US2] Write each submission's findings into research.md under its heading, anchored to
      file and line, with resolution fixed / accepted-with-reason / out-of-scope-with-reason
- [X] T022 [US2] Record every path the reviewing machine could not exercise — GPU, private HDR
      media, the absent upstream checkout — naming the capability and the exact operation that
      would close it
- [X] T023 [US2] For any defect the review finds, add a failing-then-passing regression plus a
      control proving that removing the fix restores the failure, and restore every temporary
      mutation before the final verification

## Phase 5: User Story 3 — Nothing is left half-integrated (P2)

**Goal**: every scoped submission ends with a recorded disposition.

**Independent test**: list the open pull requests and branches afterwards; each scoped one is merged
or closed with its integrating commit linked.

- [X] T024 [US3] Compare PR #9's unique test modules against PR #10's behaviour and record in
      research.md whether #10 genuinely covers them
- [X] T025 [US3] Apply the agreed disposition to PR #9 — close as superseded with the integrating
      commit linked if T024 proves coverage, otherwise leave it open and report what it alone carries
- [X] T026 [US3] Confirm each scoped PR is merged, and that no scoped PR is recorded done while its
      content is absent from main

## Phase 6: Polish & Cross-Cutting

- [X] T027 Push main and verify every required check against that exact SHA, per the constitution's
      third principle
- [X] T028 [P] Update .ai/ memory — the router, plus BUGS/DECISIONS/TESTING_NOTES where they gained
      durable value — and write the transferable lesson to the cross-project store
- [X] T029 [P] Re-index the Codebase Memory graph and refresh the Graphify graph, since the
      integration changes structure
- [X] T030 Run the survivor sweep and confirm no process, temp directory or bytecode cache the work
      started is left behind
- [X] T031 Deliver the closure table: reviewed head, issue IDs, regression evidence, exact
      counts/skips, integrating commit, resulting main SHA and PR disposition, with IMPLEMENTED /
      VERIFIED / INTEGRATED kept distinct

## Dependencies

- Phase 1 → Phase 2 → Phase 3. T003-T007 block every review task.
- Within US1 the order is per submission: review → merge. #7 → #8 → #10 is the brief's recommended
  order and is also the conflict-minimising one (runner, then support tier, then GUI).
- T020 depends on all three merges; it verifies the COMBINED tree, which is the only state whose
  result counts.
- US2 (T021-T023) runs alongside US1's reviews — findings are recorded as they are made, not after.
- US3 (T024-T026) depends on T018, because the disposition needs the integrating commit.
- T027 depends on every merge and on T019.
- T020a and T020b depend on all three merges; they are what close FR-010 and SC-001,
  which the analysis pass found uncovered.

## Parallel opportunities

- T004 and T005 are independent of T003: three diffs, no shared state.
- T028 and T029 touch different stores and can run together once T027 is green.
- The three merges are NOT parallel: they share files and must be resolved in one combined tree.

## Implementation strategy

MVP is User Story 1 alone: the four repairs present in `main` and their regressions passing. US2 is
what makes the integration honest rather than a rubber stamp, and is written as the reviews happen.
US3 closes the queue without leaving a request recorded as done while its content is missing.
