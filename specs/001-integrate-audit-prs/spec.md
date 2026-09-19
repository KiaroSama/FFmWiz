# Feature Specification: Integrate the reviewed audit repairs into main

**Feature Branch**: `main` (the brief forbids a new branch; the three published PR branches are reused)
**Created**: 2026-09-19
**Status**: Draft
**Input**: Review and integrate open PRs #7 (A06), #8 (A01) and #10 (A03/A04) into main at baseline
`10649901c3a97b98bf0445d560b1510f3e43d5e8`; an unlisted PR #9 covers the same A03/A04 workstream
with two failing checks and needs a disposition.

## Clarifications

### Session 2026-09-19

- Q: The brief scopes the work to #7, #8 and #10, but #9 is also open, covers the same A03/A04
  workstream and has two failing checks. What disposition should it receive? → A: close it as
  superseded once #10 is integrated, and only after comparing both diffs proves #10 carries
  everything #9 does; if #9 contains anything #10 does not, report it and leave it open.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The repairs are actually in the shipped program (Priority: P1)

Someone installs FFmWiz from `main` and gets the four fixed behaviours: a test run that cannot
report success after its process crashed, a job that refuses before FFmpeg can overwrite a file it
is reading, a Reverse that keeps its output when the operating system refuses to stop the writer,
and a waveform that draws the audio track the user actually selected.

**Why this priority**: the repairs exist only on unmerged branches. Until they are in `main`,
every user of the program still has all four defects, and the work done so far has bought nothing.

**Independent Test**: check out `main` and confirm each repaired behaviour is present and its
regression test runs, without consulting any branch.

**Acceptance Scenarios**:

1. **Given** `main` after integration, **When** a test module's finaliser exits abnormally after a
   passing record was written, **Then** the run reports failure rather than success.
2. **Given** `main` after integration, **When** a job would write a statistics file that is a hard
   link to its own source, **Then** it is refused before FFmpeg starts and the source is unchanged.
3. **Given** `main` after integration, **When** a Reverse worker's child cannot be stopped, **Then**
   its output file still exists and is recorded as retained with a reason.
4. **Given** `main` after integration, **When** a second audio track is selected, **Then** both
   editors decode the same PCM from that track.

### User Story 2 - The review is a review, not a countersignature (Priority: P1)

The maintainer can see what an independent pass found in the submitted work, including anything the
authors' own reports did not mention, and what was deliberately left alone.

**Why this priority**: the submissions arrive with green CI and confident reports. Accepting them on
that basis would make the review ceremonial, and this project's own recorded experience is that the
defects that survive green CI are the ones no report thought to mention.

**Independent Test**: read the review findings against the diffs and confirm each finding names a
file, a line and a consequence, and that each accepted risk is stated rather than omitted.

**Acceptance Scenarios**:

1. **Given** three submitted diffs, **When** they are reviewed, **Then** every finding is anchored in
   the source and every deliberate non-fix is reported with its reason.
2. **Given** a submitted claim of passing checks, **When** it is verified, **Then** the verification
   names the exact commit the checks ran against.

### User Story 3 - Nothing is left half-integrated (Priority: P2)

Every scoped pull request ends in a recorded disposition, and no branch, PR or artifact is left
behind in a state a later reader has to reconstruct.

**Why this priority**: a queue emptied without integration, or integration without closing the
request that carried it, is the failure mode this assignment names explicitly.

**Independent Test**: list the repository's open pull requests and branches afterwards and confirm
each scoped one is either merged or closed with a link to the commit that carries its content.

**Acceptance Scenarios**:

1. **Given** integration is complete, **When** the pull requests are listed, **Then** each scoped one
   is merged or closed as superseded with its integrating commit linked.
2. **Given** an unlisted pull request on the same workstream, **When** integration is complete,
   **Then** its disposition is recorded rather than silently ignored.

### Edge Cases

- A submitted head no longer matches the one reviewed: the combined verification is invalidated and
  repeated rather than inherited.
- Two submissions touch the same file: the combined tree, not the individual branches, is what must
  be verified.
- A review finds a real defect: it is corrected on the existing branch, never by opening a duplicate
  request or by lowering the test that caught it.
- A capability the reviewing machine lacks (GPU, private media, a second toolchain): reported as an
  uncovered path, never claimed as verified and never removed from the equipped CI job.
- The unlisted request turns out to contain something the scoped one does not: it is reported before
  any disposition is applied to it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each scoped submission MUST be reviewed against its own diff and the repository's
  source, not against its author's summary.
- **FR-002**: Each accepted repair MUST be present in `main` before its request is recorded as done.
- **FR-003**: Integration MUST NOT create a duplicate request or a new branch to repeat work that
  already exists on a published branch.
- **FR-004**: The combined result MUST be verified as one tree; per-submission green results MUST NOT
  be treated as evidence for the combination.
- **FR-005**: The final state of `main` MUST be verified by correlating every required check with
  that exact commit.
- **FR-006**: A correction found during review MUST carry a failing-then-passing regression plus a
  control proving that removing the fix restores the failure, with every temporary mutation undone
  before the final verification.
- **FR-007**: No existing test, assertion, capability requirement, hook, or protection may be
  weakened, skipped, or bypassed to reach a passing result.
- **FR-008**: Every pull request in scope MUST end with a recorded disposition. The unlisted
  same-workstream request MUST be closed as superseded once the scoped one is integrated,
  and only after a diff comparison proves the scoped one carries everything it does; if it
  carries anything the scoped one does not, that is reported and it stays open.
- **FR-009**: Anything the review could not verify MUST be reported as unverified, naming the missing
  capability and the exact operation that would close it.
- **FR-010**: Work already completed and merged MUST be preserved; no unrelated change may be
  reverted, reformatted, or dropped by the integration.

### Key Entities

- **Scoped submission**: a published, unmerged change set with an identifier, a head commit, a branch
  and a set of independent check results.
- **Combined tree**: the single state produced by taking all accepted submissions together; the only
  state whose verification counts for integration.
- **Disposition**: the recorded end state of a submission — integrated, superseded by a named commit,
  or explicitly left open with a reason.
- **Uncovered path**: a behaviour no available machine could exercise, recorded with the capability
  it needs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All four repaired behaviours are demonstrable from `main` alone, with no branch checked
  out.
- **SC-002**: Every required check passes on the exact final `main` commit, and that commit identifier
  appears in the report.
- **SC-003**: Zero scoped submissions remain without a recorded disposition, and zero are recorded as
  done while their content is absent from `main`.
- **SC-004**: The test suite covering the four repairs runs on `main` with no failures and no skip
  that blames a capability the running job provides.
- **SC-005**: Every review finding is traceable to a file and a line, and every uncovered path is
  named with the capability it requires.

## Assumptions

- The published heads named in the brief are the ones to review; a head that has moved invalidates
  the combined verification and is re-verified rather than assumed.
- The reviewing workstation has FFmpeg, NumPy and PowerShell but the equipped Qt and GPU coverage
  belongs to CI; local absence of a capability never removes it from the CI job.
- "Do not touch unrelated PRs" scopes the *actions* taken, not the *observations* reported. The
  unlisted same-workstream request was reported and its disposition decided by the maintainer
  (see Clarifications), not assumed.
- Integration happens directly on `main` because the brief forbids manufacturing a branch, and the
  repository's own closure rule permits it for verified work.
