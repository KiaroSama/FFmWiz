# Plan 005: Show picture filters, quick outputs, compositing, volume and raw options in the settings summary

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat aaf0aed..HEAD -- ffmwiz/wizard_flow_b.py`
> On any change, compare the "Current state" excerpt against the live code
> before proceeding; on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `aaf0aed`, 2026-08-29

## Why this matters

`print_summary` is the last thing a user reads before committing to an encode
that may take an hour. It exists so nothing surprising happens: every answer
that changes the output is listed back.

Five features shipped this week — picture filters, quick outputs, compositing,
audio volume, raw ffmpeg options — and **none of them appear in the summary**.
A user who asked for a boomerang at 200% volume with a logo overlay sees a
summary describing an ordinary encode, then a command they are not going to
read argument-by-argument.

Every one of these modules already has a `describe_*` function written for
exactly this purpose. They are simply never called:

```
grep -rn "describe_look\|describe_quick\|describe_composite\|describe_raw" \
     ffmwiz/wizard_flow.py ffmwiz/wizard_flow_b.py ffmwiz/support/
```
→ prints nothing today. The describers exist in their own modules and have
tests; only the call site is missing.

## Current state

`ffmwiz/wizard_flow_b.py:421` `print_summary(answers, cmd)` does two things:

1. Logs a one-line machine summary (`log_info("Selected settings: ...")`) at
   lines 433-440.
2. Prints the human summary — `print(paint("Selected settings summary:", ...))`
   at line 453, then a run of `print("  " + field_text(<label>, <value>, <Color>))`
   lines, one per setting, from line 454 onward.

The house style for one row, `ffmwiz/wizard_flow_b.py:454`:

```python
    print("  " + field_text("input", answers["input_path"], Color.WHITE))
```

Conditional rows guard on the answer, `ffmwiz/wizard_flow_b.py:489`:

```python
        if answers.get("crop_enabled"):
            print("  " + field_text("crop", "yes, " + format_crop_margins(answers), Color.ORANGE))
```

**Find the describers yourself** — do not guess their names or signatures:

```
grep -rn "^def describe_" ffmwiz/wizard_look.py ffmwiz/wizard_quick.py \
        ffmwiz/wizard_composite.py ffmwiz/wizard_raw.py ffmwiz/support/L01_filters.py
```

Each returns a short human string (the look one returns `"none"` when nothing
is set — check what each returns for an empty job before deciding the guard).

**Repo conventions**: two-space indent for summary rows; `field_text(label,
value, Color.X)`; a colour that matches the family of the setting (geometry is
ORANGE, codec is CYAN, output is LIME, warnings are YELLOW). Pick colours
already used in this function rather than introducing new ones.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Summary tests | `python tests/run_suite.py -k summary -j 2` | `OK` |
| Feature suites | `python tests/run_suite.py -k look_filters -j 2`, `-k quick_outputs`, `-k composite_inputs`, `-k volume_and_raw_args` | `OK` |
| Full suite | `python tests/run_suite.py -j 4` | `OK`, 0 failures |

**A repository hook blocks raw `python -m unittest`. Always go through
`tests/run_suite.py`.**

## Scope

**In scope**:
- `ffmwiz/wizard_flow_b.py` — `print_summary` only
- One test file for the summary rows (find the existing one with
  `grep -rln "print_summary" tests/`; create `tests/test_summary_rows.py` only
  if none exists)

**Out of scope**:
- Every `describe_*` function — they are written and tested. Call them.
- The `log_info("Selected settings: ...")` machine line at 433-440. Adding to
  it is fine ONLY if you keep its existing `key=value; key=value` shape; if
  that is awkward, leave it and do the human summary only.
- Any reordering of the existing summary rows.

## Git workflow

- Branch: `advisor/005-summary-shows-the-new-features`
- One commit, conventional-commit subject only.
- Do NOT push or open a PR.

## Steps

### Step 1: Find the describers and what they return for an empty job

```
python -c "import sys; sys.path.insert(0,'.'); import FFmWiz
from ffmwiz import wizard_look, wizard_quick, wizard_composite, wizard_raw
for m in (wizard_look, wizard_quick, wizard_composite, wizard_raw):
    print(m.__name__, [n for n in dir(m) if n.startswith('describe')])"
```

Then call each with `{}` and record what it returns. That answer decides the
guard for each row: a describer returning `"none"` needs an explicit guard on
the underlying answer key, not a truthiness test on the string.

### Step 2: Add the rows

Add one conditional row per feature, after the geometry rows and before the
audio rows (so the order follows the wizard's own question order). Each row:

- guards on the ANSWER, not on the describer's output;
- calls the module's `describe_*` for the value;
- uses `field_text` and a Color already used in this function.

Suggested labels and guards — adjust to the real answer keys you find:

| Label | Guard | Value |
|---|---|---|
| `picture filters` | any look answer key set | `describe_look(answers)` |
| `quick output` | `answers.get("quick_output")` or `loop_count` | the quick describer |
| `composite` | `composite_mode` or `composite_audio_mix` | the composite describer |
| `volume` | `audio_volume` set and != 1.0 | the raw/volume describer |
| `raw options` | `answers.get("raw_ffmpeg_args")` | the raw describer |

**The `raw options` row is the most important of the five.** It is the one
setting that can override anything else in the command, so it is the one a user
most needs to see echoed back. Give it `Color.YELLOW` (the warning family) —
it is not an error, but it deserves the eye.

Add a comment above the block, in the file's voice, saying these five rows
exist because the summary is the last chance to catch a wrong answer before a
long encode.

**Verify**: `python tests/run_suite.py -j 4` → `OK`, 0 failures

### Step 3: Prove the rows appear, and only when asked for

Write the tests below and confirm.

**Verify**: the summary suite → `OK`

## Test plan

In the summary test file (existing, or `tests/test_summary_rows.py`):

Capture stdout with `contextlib.redirect_stdout(io.StringIO())` around
`print_summary(answers, cmd)` — check whether the existing summary tests
already have a helper for this and reuse it.

1. `test_every_new_feature_appears_when_asked_for` — one job with all five set;
   assert each label appears in the captured output AND that the describer's
   text does too (not just the label).
2. `test_a_plain_encode_shows_none_of_them` — a job with none set; assert none
   of the five labels appear. **This is the test that stops the rows becoming
   permanent noise.**
3. `test_the_raw_options_row_shows_the_actual_options` — assert the user's own
   option string is visible verbatim, since the whole point is that they can
   see what will override their other answers.
4. `test_a_volume_of_one_is_not_shown` — 1.0 is "no change"; showing it is
   noise. (Skip this test only if the volume answer is absent rather than 1.0
   when unset — say which in your report.)

**Verify**: `python tests/run_suite.py -k <that suite> -j 2` → `OK`

## Done criteria

ALL must hold:

- [ ] `grep -c "describe_" ffmwiz/wizard_flow_b.py` returns at least 4
- [ ] `python tests/run_suite.py -j 4` → `OK`, 0 failures
- [ ] The four tests above exist and pass
- [ ] A plain encode's summary is unchanged from before this plan (test 2)
- [ ] `git status --short` shows only the in-scope files

## STOP conditions

Stop and report if:

- The excerpt at `wizard_flow_b.py:421-454` does not match the live code.
- A `describe_*` function does not exist for one of the five features — report
  which; do not write one.
- Importing a describer into `wizard_flow_b.py` creates an import cycle
  (`python -c "import ffmwiz.wizard_flow_b"` fails, or
  `python tests/run_suite.py -k module_reference_hygiene -j 1` fails). The
  repo has a guard for exactly this; if it fires, report rather than working
  around it.
- Adding rows breaks an existing summary test that asserts an exact line count
  or full output equality.

## Maintenance notes

- The rule: a wizard question that changes the output gets a summary row in the
  same change. The summary is a promise, and a silent answer breaks it.
- A reviewer should check test 2 — five unconditional rows would be a
  regression in the opposite direction.
- `ffmwiz/wizard_flow_b.py` is 438+ lines and `print_summary` is its largest
  function. If it grows much further, the summary is a real candidate for its
  own module — but do NOT do that in this plan.
