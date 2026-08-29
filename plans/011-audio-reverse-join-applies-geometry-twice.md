# Plan 011: Stop the audio-reverse-across-a-join path applying the geometry twice

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat aaf0aed..HEAD -- ffmwiz/encoding.py ffmwiz/reverse_stages.py`
> On any change, compare the "Current state" excerpts against the live code
> before proceeding; on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `aaf0aed`, 2026-08-29

## Why this matters

This repository has a schema — `STAGE_TRANSFORMATIONS` in
`ffmwiz/reverse_stages.py` — whose entire purpose is that a multi-stage job
applies each edit exactly once. Its comment records the measured defect that
motivated it: a 160x120 clip asking for 10 px off each side came out 120x120
instead of 140x120, because `crop=` appeared in the joined intermediate, in
every reverse segment AND in the final graph.

**The audio-reverse-across-a-join path never adopted it.** Its first stage
applies the schema correctly and its last stage does not, so a Join + audio
reverse job that also crops, changes the frame rate or resizes applies that
geometry twice: once to the joined intermediate, then again to the finished
file built from it.

The bug is not subtle in its effect and is completely silent in its symptom —
the encode succeeds, no warning is printed, and the output is simply the wrong
size.

## Current state

The whole stage sequence lives in one function in `ffmwiz/encoding.py`
(the audio-reverse encode path). Read `ffmwiz/encoding.py:316-372` before
touching anything.

**Stage 1 — the forward join. This one is correct**, `ffmwiz/encoding.py:322-327`:

```python
        joined = workspace / f"joined_forward.{INTERMEDIATE_CONTAINER_EXT}"
        # Owns the geometry and nothing else, exactly as the video pipeline's
        # forward stage does: the reversal and every later edit belong to the
        # stages after it.
        forward = intermediate_profile(
            reverse_stages.stage_answers(answers, owns=GEOMETRY_TRANSFORMATIONS))
```

Note the comment: the author knew the model and named it. The geometry is
consumed here.

**Stage 4 — the final rebuild. This one is the defect**,
`ffmwiz/encoding.py:360-364`:

```python
    final = _single_input_answers(answers, rebased)
    final["reverse_audio"] = False
    final["audio_cut_keep_ranges"] = []
    final["audio_keep_ranges"] = []
    final["output_path"] = output_path
```

It starts from the ORIGINAL `answers` — which still carries every geometry key
— and clears three audio keys by hand. There is no `stage_answers()` call and
no `validate_stage_plan()`.

**And `_single_input_answers` clears nothing**, `ffmwiz/reverse_stages.py:537-549`:

```python
def _single_input_answers(answers: dict[str, Any], source: Path) -> dict[str, Any]:
    """Re-point a job at one already-produced file, keeping its output settings."""
    probe = services.ffprobe_json(answers.get("ffprobe") or "ffprobe", source)
    streams = (probe or {}).get("streams") or []
    rebased = dict(answers)
    rebased.pop("join_input_items", None)
    rebased["input_path"] = source
    ...
    return rebased
```

It re-points the input and re-probes. Every transformation key survives.

Stages 2 and 3 (the bounded audio reverse, and the remux with `-c copy`) apply
no filters, so they are not part of this.

**The model to copy** is `stage_answers(answers, owns=(...))`, defined in
`ffmwiz/reverse_stages.py` around line 489. Read it and
`validate_stage_plan()` beside it before writing any code — `validate_stage_plan`
rejects both duplicate AND missing owners across a stage list, which is the
check that would have caught this.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Ownership suites | `python tests/run_suite.py -k stage_geometry_ownership -j 2` | `OK` |
| Audio reverse | `python tests/run_suite.py -k audio_reverse -j 2` | `OK` |
| Full suite | `python tests/run_suite.py -j 4` | `OK`, 0 failures |

**A repository hook blocks raw `python -m unittest`. Always go through
`tests/run_suite.py`.**

## Scope

**In scope**:
- `ffmwiz/encoding.py` — the final rebuild in the audio-reverse path only
- `tests/test_audio_reverse_workflows.py` (or the audio-reverse suite you find
  with `grep -rln "bounded_audio_reverse\|audio reverse" tests/`)

**Out of scope**:
- `ffmwiz/reverse_stages.py` — `stage_answers`, `_single_input_answers` and the
  schema are correct. You are calling them, not changing them.
- The video reverse pipeline in `ffmwiz/reverse_pipeline.py` — it already
  applies the schema; this plan is only about the audio path.
- Stage 1's ownership. It is right.

## Git workflow

- Branch: `advisor/011-audio-reverse-join-geometry`
- One commit, conventional-commit subject only.
- Do NOT push or open a PR.

## Steps

### Step 1: Reproduce it first

Do not fix anything yet. Prove the defect exists, so you know the test you
write later actually pins something.

Build a Join + audio-reverse + crop job and inspect the commands the pipeline
produces. The cheapest honest way is to stub the ffmpeg runner and capture
every argv, the way the existing ownership tests do — read
`tests/test_stage_geometry_ownership.py` first and follow its mechanism.

What you are looking for: `crop=` appearing in BOTH the join command and the
final command.

Record what you observed in your report. **If `crop=` appears only once, STOP**
— the premise is wrong and the plan must be revised, not worked around.

### Step 2: Give the final stage its ownership

The final rebuild is a stage like any other. It should own everything the
earlier stages did not: the geometry was consumed by stage 1, so the final
stage must NOT own it.

Work out the correct `owns=` tuple from the sequence, not by guessing:

- stage 1 (join) owns `GEOMETRY_TRANSFORMATIONS`
- stage 2 (bounded audio reverse) owns `audio_reverse`
- stages 3 (remux, `-c copy`) owns nothing
- **stage 4 (final) owns everything else the job asked for** — cuts, speed,
  loudnorm, split, and the new picture/volume/raw transformations if plan 006
  has landed.

Replace the hand-cleared shape with a `stage_answers()` call carrying that
tuple. Keep the three explicit audio-key clears only if `stage_answers` does
not already cover them — check before deleting them.

There is a subtlety: `final` is built from `answers`, but stage 1 was also
built from `answers`, and the join path re-points `source_answers` in between.
Read lines 316-341 carefully and make sure the final stage is derived from the
right dict.

**Verify**: `python tests/run_suite.py -k audio_reverse -j 2` → `OK`

### Step 3: Add the validation that would have caught it

`validate_stage_plan(stages, answers)` exists to reject a stage list where a
transformation is owned twice or not at all. This path never calls it.

Add the call, so a future edit to this sequence fails loudly instead of
silently double-applying. Read the function's signature and what it expects a
"stage list" to look like before wiring it — if the audio path's shape does not
fit what it wants, say so in your report and explain what you did instead
rather than forcing it.

**Verify**: `python tests/run_suite.py -k audio_reverse -j 2` and
`-k stage_geometry_ownership -j 2` → both `OK`

### Step 4: Full suite

**Verify**: `python tests/run_suite.py -j 4` → `OK`, 0 failures

## Test plan

Add to the audio-reverse suite, modelled on `tests/test_stage_geometry_ownership.py`
(read it first — it uses coloured edge bands and real pixel probing, which is
the strongest available evidence for a geometry defect):

1. `test_a_joined_audio_reverse_crops_once` — the regression. A Join + audio
   reverse + crop job; assert `crop=` appears in exactly ONE of the commands
   the pipeline issues. Assert the count, not just presence.
2. `test_the_finished_file_has_the_asked_for_size` — the real-media version.
   Two small clips, a known crop, a real run; probe the output's width and
   height and assert they match what a single crop gives. **This is the test
   that would have caught the original defect**; the argv test can be satisfied
   by a wrong fix that moves the crop rather than removing the duplicate.
3. `test_the_frame_rate_is_not_applied_twice` — same shape for `fps`, which
   leaks by the same mechanism.
4. `test_a_job_with_no_geometry_is_unchanged` — negative control.

Then mutation-check: revert your `owns=` tuple to the old hand-cleared shape,
confirm tests 1 and 2 go red, restore. Report the result.

## Done criteria

ALL must hold:

- [ ] `python tests/run_suite.py -k audio_reverse -j 2` → `OK`
- [ ] `python tests/run_suite.py -k stage_geometry_ownership -j 2` → `OK`
- [ ] `python tests/run_suite.py -j 4` → `OK`, 0 failures
- [ ] `grep -n "stage_answers" ffmwiz/encoding.py` shows a call in the final
      rebuild, not only in the join stage
- [ ] The mutation check went red and was restored (report it)
- [ ] `git status --short` shows only the in-scope files

## STOP conditions

Stop and report if:

- The excerpts do not match the live code (drift since `aaf0aed`).
- Step 1 shows `crop=` appearing only once — the premise is wrong.
- Giving the final stage an `owns=` tuple breaks an existing audio-reverse test
  whose expectation you would have to change to pass. That means the current
  behaviour is depended on somewhere and the fix needs a decision, not a
  workaround.
- `validate_stage_plan` cannot express this path's stage sequence.

## Maintenance notes

- The rule this path was missing: **every stage in a multi-stage pipeline gets
  an explicit `owns=`, including the last one.** A hand-cleared dict is how a
  transformation goes unclaimed.
- Two related repetitions found in the same audit are deliberately NOT fixed
  here, so this plan stays reviewable:
  - the boomerang's backward half re-applies the picture filters (plan 006
    covers it by extending the schema);
  - a segmented reverse repeats time-anchored filters like `fade` per SEGMENT,
    because segments are not stages — a crop per segment is right, a fade per
    segment is not. That one needs its own decision and has no plan yet.
- A reviewer should check test 2 hardest. An argv assertion can be satisfied by
  a fix that moves the duplicate rather than removing it; only the probed
  output size proves the picture is right.
