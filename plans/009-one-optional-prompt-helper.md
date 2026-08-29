# Plan 009: Replace five copies of the optional-prompt loop with one `appio.ask_optional`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat aaf0aed..HEAD -- ffmwiz/appio.py ffmwiz/wizard_look.py ffmwiz/wizard_quick.py ffmwiz/wizard_composite.py ffmwiz/wizard_raw.py ffmwiz/core/constants.py ffmwiz/support/L01_filters.py`
> If any of those changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding; on a mismatch,
> treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW-MED
- **Depends on**: `plans/001-raw-arguments-parsing.md`, `plans/003-loop-count-reaches-every-quick-output.md`
- **Category**: tech-debt
- **Planned at**: commit `aaf0aed`, 2026-08-29

## Why this matters

Five wizard steps ask the same shape of question — "do you want this optional
thing? `n` skips it" — and each one has its own hand-written copy of the same
14-to-31-line loop. Four different authors wrote them in one week, and four
conventions drifted apart in the process: where the parser lives, where the
answer-key tuple lives, how the previous attempt is cleared, and whether the
clear is a loop, a named function or a bare `pop`.

The duplication is not the whole cost. The loop has one subtle rule — **the
previous attempt's answer keys must be cleared BEFORE the new value is parsed,
or a rejected answer leaves half of itself behind** — and three of the five
copies carry a near-identical comment explaining it. A rule that has to be
re-explained at every copy is a rule that the sixth copy will get wrong.

One helper in `appio` fixes both: one place to read the rule, one place to fix
it. All five call sites already have direct tests covering declining, going
back, and leaving no debris on a rejected answer, so the refactor has a real
safety net — provided each call site is migrated and verified on its own.

## Current state

### The five loops

**1. `ffmwiz/wizard_look.py:34-51`** (the file is 54 lines):

```python
    while True:
        value = appio.ask_raw(
            appio.question_prompt(answers, "Extra picture filters?", hint, "n"))
        if is_back_value(value):
            raise Back()
        # Re-asking must not leave the previous attempt's keys behind, or a
        # rejected `sharpen,blur` would keep the sharpen on the second pass.
        for key in LOOK_ANSWER_KEYS:
            answers.pop(key, None)
        if not value or value.strip().lower() in {"n", "no"}:
            return
        try:
            answers.update(parse_look_tokens(value))
        except ValueError as error:
            appio.error(str(error))
            continue
        print(paint(f"Picture filters: {describe_look(answers)}", Color.LIME))
        return
```

**2. `ffmwiz/wizard_quick.py:150-169`** (the file is 208 lines):

```python
    while True:
        value = appio.ask_raw(
            appio.question_prompt(answers, "Quick output?", hint, "n"))
        if is_back_value(value):
            raise Back()
        # Re-asking must not leave the previous attempt's keys behind, or a
        # rejected `gif,boomerang` would keep the gif. Same trap the picture
        # filters have, and the same answer.
        forget_quick_answers(answers)
        if not value or value.strip().lower() in {"n", "no"}:
            return
        try:
            parsed = parse_quick_tokens(value)
        except ValueError as error:
            appio.error(str(error))
            continue
        answers.update(parsed)
        apply_quick_output_ext(answers)
        print(paint(f"Quick output: {describe_quick(answers)}", Color.LIME))
        return
```

**3. `ffmwiz/wizard_composite.py:211-241`** (the file is 245 lines):

```python
    while True:
        value = appio.ask_raw(
            appio.question_prompt(answers, "Combine with another input?", hint, "n"))
        if is_back_value(value):
            raise Back()
        # A rejected attempt must leave nothing behind, or `overlay,vstack`
        # would keep the overlay's corner on the second pass.
        for key in COMPOSITE_ANSWER_KEYS:
            answers.pop(key, None)
        if not value or value.strip().lower() in {"n", "no"}:
            return
        try:
            chosen = parse_composite_tokens(value)
        except ValueError as error:
            appio.error(str(error))
            continue
        answers.update(chosen)
        if chosen.get("composite_mode"):
            item = _ask_partner(
                answers, "Second picture file",
                "the video or image to composite with this input", False)
            answers["composite_item"] = item
            answers["composite_path"] = item["path"]
        if chosen.get("composite_audio_mix"):
            item = _ask_partner(
                answers, "Audio file to mix in",
                "music or narration to lay under the main audio", True)
            answers["composite_audio_item"] = item
            answers["composite_audio_path"] = item["path"]
        print(paint(f"Compositing: {describe_composite(answers)}", Color.LIME))
        return
```

**4. `ffmwiz/wizard_raw.py:122-136`**, inside `step_audio_volume` (the file is
166 lines):

```python
    while True:
        value = appio.ask_raw(
            appio.question_prompt(answers, "Change the audio volume?", hint, "n"))
        if is_back_value(value):
            raise Back()
        answers.pop("audio_volume", None)
        if not value or value.strip().lower() in {"n", "no"}:
            return
        try:
            answers["audio_volume"] = parse_volume(value)
        except ValueError as error:
            appio.error(str(error))
            continue
        print(paint(f"Audio volume: {answers['audio_volume']:g}x", Color.LIME))
        return
```

**5. `ffmwiz/wizard_raw.py:142-161`**, inside `step_raw_ffmpeg_args` (note the
plan brief's "143-161" was one line short; `while True:` is line **142**):

```python
    while True:
        value = appio.ask_raw(
            appio.question_prompt(answers, "Extra ffmpeg options?", hint, "n"))
        if is_back_value(value):
            raise Back()
        answers.pop("raw_ffmpeg_args", None)
        if not value or value.strip().lower() in {"n", "no"}:
            return
        try:
            parsed = parse_raw_arguments(value)
        except ValueError as error:
            appio.error(str(error))
            continue
        if not parsed:
            return
        answers["raw_ffmpeg_args"] = parsed
        print(paint("These go in unchanged, just before the output path. "
                    "Check the command below before starting.", Color.YELLOW))
        print(paint("Extra options: " + " ".join(parsed), Color.LIME))
        return
```

### The four drifted conventions

| | look | quick | composite | raw (both) |
|---|---|---|---|---|
| parse/describe lives in | `ffmwiz/support/L01_filters.py` | the step module | the step module | the step module |
| answer-key tuple lives in | `ffmwiz/support/L01_filters.py:78` | `ffmwiz/core/constants.py:372` | `ffmwiz/wizard_composite.py:45` | no tuple at all |
| clear-on-re-ask | inline `for key in ...` loop | `forget_quick_answers()` | inline `for key in ...` loop | one bare `answers.pop` |
| extra work after parse | none | `apply_quick_output_ext()` | two more interactive prompts | `if not parsed: return`, plus a YELLOW warning line |

The three answer-key tuples, verbatim:

`ffmwiz/core/constants.py:372-379`:

```python
QUICK_ANSWER_KEYS = (
    "quick_output",
    "gif_fps",
    "gif_width",
    "loop_count",
    "thumbnail_seconds",
    "thumbnail_ext",
)
```

`ffmwiz/support/L01_filters.py:78-81` — note it splices `ADJUST_RANGES`, which
is defined at `ffmwiz/core/constants.py:957`:

```python
LOOK_ANSWER_KEYS = ("rotate_choice", "flip_horizontal", "flip_vertical",
              "adjust_grayscale", "denoise_level", "sharpen_level",
              "blur_level", "fade_in_seconds", "fade_out_seconds",
              *ADJUST_RANGES)
```

`ffmwiz/wizard_composite.py:45-50`:

```python
COMPOSITE_ANSWER_KEYS = (
    "composite_mode", "composite_corner", "composite_margin",
    "composite_opacity", "composite_scale", "composite_path",
    "composite_item", "composite_audio_mix", "composite_audio_weight",
    "composite_audio_path", "composite_audio_item",
)
```

### Why `forget` must be a callable, not a key tuple

`ffmwiz/wizard_quick.py:189-198`:

```python
def forget_quick_answers(answers: dict[str, Any]) -> None:
    """Clear the whole answer set, and put the previous container back."""
    for key in QUICK_ANSWER_KEYS:
        answers.pop(key, None)
    # Restore whatever the format question chose. Popping the key instead would
    # leave the job with no container at all.
    if "_output_ext_before_quick" in answers:
        previous = answers.pop("_output_ext_before_quick")
        if previous is not None:
            answers["output_ext"] = previous
```

Clearing the quick answers is not "pop these keys" — it also **restores**
`output_ext` from a saved value. A `keys: tuple[str, ...]` parameter cannot
express that. The helper takes a `forget(answers)` callable, and the three
inline styles become one-liners against it.

### What is already in scope inside `appio.py`

`ffmwiz/appio.py` (484 lines) star-imports the whole `core` and `L00_*`/`L01_*`
tier — `ffmwiz/appio.py:33-59` begins:

```python
from ffmwiz.core.constants import *  # noqa: F401,F403
from ffmwiz.core.colors import *  # noqa: F401,F403
from ffmwiz.core.exceptions import *  # noqa: F401,F403
```

Verified at `aaf0aed`:

```
python -c "import sys; sys.path.insert(0,'.'); import ffmwiz.appio as a; print([hasattr(a,n) for n in ('is_back_value','paint','Color','Back','ask_raw','question_prompt','error')])"
  -> [True, True, True, True, True, True, True]
```

So `ask_optional` needs **no new import in `appio.py`** and introduces no cycle.
Its docstring already describes the module as holding "the interactive
primitives (error, note, ask_*, question_prompt, paint)".

### The back token

`ffmwiz/core/constants.py:728`:

```python
BACK_INPUT_TOKENS = {"0", "۰", "٠"}
```

and `ffmwiz/support/L00_misc_b.py:329-333`:

```python
def is_back_value(value: str, *, allow_text: bool = False) -> bool:
    lowered = str(value).strip().lower()
    if lowered in BACK_INPUT_TOKENS:
        return True
    return allow_text and lowered in {"b", "back"}
```

None of the five call sites passes `allow_text=True`, so **`"b"` does not go
back — the token is `"0"`.**

**Repo conventions to match**: docstrings state the reason for a choice, not
just what it does; comments name the observable symptom a line prevents; every
module ends with an explicit `__all__` list.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Look suite | `python tests/run_suite.py -k look_filters -j 2` | `OK` |
| Quick suite | `python tests/run_suite.py -k quick_outputs -j 2` | `OK` |
| Composite suite | `python tests/run_suite.py -k composite_inputs -j 2` | `OK` |
| Raw/volume suite | `python tests/run_suite.py -k volume_and_raw_args -j 2` | `OK` |
| All four at once | `python tests/run_suite.py -k look_filters -k quick_outputs -k composite_inputs -k volume_and_raw_args -j 4` | `OK` |
| Import direction | `python tests/run_suite.py -k module_reference_hygiene -j 1` | `OK` |
| Every module imports alone | `python tests/run_suite.py -k package_imports -j 2` | `OK` |
| Full suite | `python tests/run_suite.py -j 4` | `OK`, 0 failures |

**A repository hook blocks raw `python -m unittest`. Always go through
`tests/run_suite.py`.**

Python files in this repo are UTF-8 with LF newlines (`.gitattributes` has
`*.py text eol=lf`). Do not let an editor rewrite line endings.

## Scope

**In scope**:
- `ffmwiz/appio.py` — add `ask_optional`, add it to `__all__`
- `ffmwiz/wizard_look.py`
- `ffmwiz/wizard_quick.py`
- `ffmwiz/wizard_composite.py`
- `ffmwiz/wizard_raw.py`
- `ffmwiz/core/constants.py` — only for step 7, and only if it stays cycle-free

**Out of scope** (do NOT touch):
- Any parser (`parse_look_tokens`, `parse_quick_tokens`,
  `parse_composite_tokens`, `parse_volume`, `parse_raw_arguments`) or any
  `describe_*` function. This plan moves the LOOP, not the logic. If a parser
  looks wrong, that is a separate finding.
- The four test files. They must pass **unchanged**; that is what proves the
  refactor is behaviour-preserving. Adding tests is fine in step 8; changing an
  existing assertion is a STOP condition.
- `ffmwiz/support/ext08.py:438` — another consumer of `LOOK_ANSWER_KEYS`,
  unrelated to the prompt loop.
- **`_ask_partner` in `ffmwiz/wizard_composite.py:167-201`.** It has a
  `while True:` and an `is_back_value` check and looks like a sixth copy. It is
  not: it is a REQUIRED prompt — no default, no `n`/`no` decline, no answer keys
  to clear, and it validates and probes a file across five `continue` branches.
  It stays exactly as it is.
- `ffmwiz/support/L01_filters.py` — leave `LOOK_ANSWER_KEYS` where it is unless
  step 7's cycle check says otherwise; see step 7.

## Git workflow

- Branch: `advisor/009-one-optional-prompt-helper`
- **One commit per step from step 1 onward** — the helper, then one per
  migrated call site, then the import cleanup and the key consolidation. Eight
  small commits are correct here: if a later step has to be reverted, the
  earlier ones stay. Conventional commits, short subject only — see
  `git log --oneline -5` (e.g. `fix: let the NVENC tests run on real hardware`).
- Do NOT push or open a PR.

## Steps

### Step 1: Add `appio.ask_optional`

Add one function to `ffmwiz/appio.py`, near the other `ask_*` primitives, and
add `'ask_optional'` to the module's `__all__` list (currently at the end of
the file, alphabetically ordered around `'ask_raw', 'ask_required',
'ask_yes_no'`).

The contract, which every one of the five loops must fit:

```python
def ask_optional(answers, title, hint, forget, record, describe, default="n"):
    """One optional question: ask, allow Back, clear, decline, parse, confirm.

    ... docstring must state WHY `forget` runs before the value is looked at ...
    """
    while True:
        value = ask_raw(question_prompt(answers, title, hint, default))
        if is_back_value(value):
            raise Back()
        forget(answers)
        if not value or value.strip().lower() in {"n", "no"}:
            return
        try:
            recorded = record(value, answers)
        except ValueError as problem:
            error(str(problem))
            continue
        if not recorded:
            return
        line = describe(answers)
        if line:
            print(paint(line, Color.LIME))
        return
```

Five details that are load-bearing, not stylistic:

1. **`forget(answers)` runs before the decline check and before `record`.**
   Answering `n` on the second pass must clear the first pass's keys too. All
   five current copies do this; keep the order exactly.
2. **`record(value, answers)` owns the parse AND the write.** It is not a pure
   parser: `wizard_quick` must call `apply_quick_output_ext(answers)` after
   updating, and `wizard_composite` must run two more interactive prompts. Let
   `record` mutate `answers` and return something truthy when it recorded
   anything.
3. **A falsy return from `record` means "nothing recorded, stop asking".** That
   is `step_raw_ffmpeg_args`'s `if not parsed: return` — an empty parse must
   return silently, without printing a confirmation line.
4. **Only `ValueError` is caught.** `Back` raised from inside `record` (which
   `wizard_composite`'s partner prompts can do) must propagate untouched.
   `tests/test_composite_inputs.py:610`
   (`test_back_out_of_the_file_question_leaves_no_half_answer`) is the test
   that pins this.
5. **Name the caught exception something other than `error`.** Inside
   `appio.py`, `error` is the module's own reporting function; `except
   ValueError as error` would shadow it and the next line would call a string.
   The five call sites get away with it only because they reach it as
   `appio.error`.

Do not add a `keys` convenience parameter alongside `forget`. One way to spell
it — see the Maintenance notes.

**Verify**:
```
python -c "import sys; sys.path.insert(0,'.'); import ffmwiz.appio as a; print(callable(a.ask_optional), 'ask_optional' in a.__all__)"
```
→ `True True`

```
python tests/run_suite.py -k module_reference_hygiene -j 1
```
→ `OK` (nothing may import upward out of `appio`).

```
python tests/run_suite.py -j 4
```
→ `OK`, 0 failures. Nothing calls the helper yet, so the suite must be
unchanged.

### Step 2: Migrate `ffmwiz/wizard_raw.py` `step_audio_volume` first

Start with the simplest of the five: one key, one parser, one confirmation
line. `step_audio_volume` becomes roughly:

```python
def step_audio_volume(answers: dict[str, Any]) -> None:
    hint = (...)  # unchanged

    def forget(answers):
        answers.pop("audio_volume", None)

    def record(value, answers):
        answers["audio_volume"] = parse_volume(value)
        return True

    def describe(answers):
        return f"Audio volume: {answers['audio_volume']:g}x"

    appio.ask_optional(answers, "Change the audio volume?", hint,
                       forget, record, describe)
```

Keep the prompt title, the hint and the confirmation text byte-identical to
what is there now — the tests assert on them.

**Verify**: `python tests/run_suite.py -k volume_and_raw_args -j 2` → `OK`,
same test count as before.

### Step 3: Migrate `ffmwiz/wizard_raw.py` `step_raw_ffmpeg_args`

Same shape. Two extra things this one needs:

- The `if not parsed: return` path is expressed by `record` returning the
  parsed list (falsy when empty) instead of `True`.
- The YELLOW warning line has no place in the helper (which prints one LIME
  line). Print it from inside `record`, immediately after the parse succeeds
  and before returning, so the ordering the user sees is unchanged:
  warning first, then `Extra options: ...`.

**Verify**: `python tests/run_suite.py -k volume_and_raw_args -j 2` → `OK`.

Also confirm the two-line output by hand:
```
python -c "import sys; sys.path.insert(0,'.'); import ffmwiz.appio as appio, ffmwiz.wizard_raw as wr
replies = iter(['-tune film', 'n'])
appio.ask_raw = lambda *a, **k: next(replies)
answers = {}
wr.step_raw_ffmpeg_args(answers)
print('RECORDED:', answers.get('raw_ffmpeg_args'))"
```
→ prints the yellow warning line, then `Extra options: -tune film`, then
`RECORDED: ['-tune', 'film']`. Run it BEFORE step 3 as well and confirm the
three lines are identical — that is the whole test.

### Step 4: Migrate `ffmwiz/wizard_look.py`

`forget` is `for key in LOOK_ANSWER_KEYS: answers.pop(key, None)`. `record` is
`answers.update(parse_look_tokens(value)); return True`. `describe` is
`f"Picture filters: {describe_look(answers)}"`.

Move the "Re-asking must not leave the previous attempt's keys behind" comment
into `ask_optional`'s docstring rather than deleting it — it is the only
written statement of the rule and must survive somewhere. Keep a one-line
pointer at the call site only if it says something the helper cannot.

**Verify**: `python tests/run_suite.py -k look_filters -j 2` → `OK`.

### Step 5: Migrate `ffmwiz/wizard_quick.py`

`forget` is the existing `forget_quick_answers` — pass it directly, do not
inline it. `record` must do all three of: `answers.update(parse_quick_tokens(
value))`, then `apply_quick_output_ext(answers)`, then return the parsed dict.
`describe` is `f"Quick output: {describe_quick(answers)}"`.

**Verify**: `python tests/run_suite.py -k quick_outputs -j 2` → `OK`.

The container-restore path is the one most likely to break here. Confirm it
explicitly:
```
python -c "import sys; sys.path.insert(0,'.'); import ffmwiz.appio as appio, ffmwiz.wizard_quick as wq
replies = iter(['gif', 'n'])
appio.ask_raw = lambda *a, **k: next(replies)
answers = {'output_ext': 'mp4'}
wq.step_quick_output(answers); print('after gif:', answers.get('output_ext'))
wq.step_quick_output(answers); print('after n  :', answers.get('output_ext'))"
```
→ `after gif: gif` then `after n  : mp4`. If the second line is not `mp4`, the
`forget` callable is not being run before the decline check.

### Step 6: Migrate `ffmwiz/wizard_composite.py`

The hardest of the five, because `record` also runs two interactive
`_ask_partner` prompts and those can raise `Back`. Move the whole block from
`answers.update(chosen)` down to the second `answers["composite_audio_path"] =
item["path"]` into `record`, and return `chosen`.

**Verify**: `python tests/run_suite.py -k composite_inputs -j 2` → `OK`, with
`test_back_out_of_the_file_question_leaves_no_half_answer` (line 610) passing.

### Step 7: Give the answer-key tuples one home — only if it stays cycle-free

`ffmwiz/core/constants.py` already holds `QUICK_ANSWER_KEYS` (line 372) and is
the bottom of the import graph, so nothing can cycle by moving a plain tuple
into it. Move both of the others there:

- `COMPOSITE_ANSWER_KEYS` (`ffmwiz/wizard_composite.py:45-50`) — plain strings,
  a straight move. Import it back into `wizard_composite.py` and keep it in
  that module's `__all__` (line 244) so
  `from ffmwiz.wizard_composite import COMPOSITE_ANSWER_KEYS` keeps working.
- `LOOK_ANSWER_KEYS` (`ffmwiz/support/L01_filters.py:78-81`) — this one splices
  `*ADJUST_RANGES`, which lives at `ffmwiz/core/constants.py:957`. **It must
  therefore be defined AFTER line 957 in `constants.py`, not next to
  `QUICK_ANSWER_KEYS` at line 372**, or it will raise `NameError` at import.
  Keep `LOOK_ANSWER_KEYS` in `L01_filters.__all__` (line 321) — `ext08.py:438`
  and `tests/test_look_filters.py:479` both reach it through the old path.

Add each moved name to the `__all__` list in `ffmwiz/core/constants.py`
(`'QUICK_ANSWER_KEYS'` is at line 828 and `'ADJUST_RANGES'` at line 770 —
follow whichever ordering the surrounding block uses).

**Verify** — run all four, in this order:
```
python tests/run_suite.py -k module_reference_hygiene -j 1
python tests/run_suite.py -k package_imports -j 2
python tests/run_suite.py -k import_topology -j 1
python tests/run_suite.py -j 4
```
→ all `OK`. `module_reference_hygiene` is the import-direction guard and
`package_imports` imports every module in a fresh subprocess; between them they
are what proves the move created no cycle.

If either of the first two fails, **revert step 7 only** and report. The helper
in steps 1-6 is the value of this plan; the tuple consolidation is a bonus and
is not worth a cycle.

### Step 8: Drop the imports the five loops took with them

`is_back_value` and `Back` were used by nothing else in three of the four step
modules. At `aaf0aed` the counts are:

| file | `is_back_value` at | `Back` at | after the migration |
|---|---|---|---|
| `ffmwiz/wizard_look.py` | 25 (import), 37 | 24 (import), 38 | both imports go |
| `ffmwiz/wizard_quick.py` | 30 (import), 153 | 28 (import), 154 | both imports go |
| `ffmwiz/wizard_raw.py` | 24 (import), 125, 145 | 23 (import), 126, 146 | both imports go |
| `ffmwiz/wizard_composite.py` | 38 (import), 177, 214 | 37 (import), 178, 215 | **both stay** — `_ask_partner` (line 175) still uses them |

Delete `from ffmwiz.support.L00_misc_b import is_back_value` and
`from ffmwiz.core.exceptions import Back` from `wizard_look.py`,
`wizard_quick.py` and `wizard_raw.py`. In `wizard_composite.py`, note that
`is_back_value` is imported together with `looks_like_generated_output_file`
(lines 38-39) — keep both.

**Verify**:
```
python -c "import ffmwiz.wizard_look, ffmwiz.wizard_quick, ffmwiz.wizard_composite, ffmwiz.wizard_raw; print('ok')"
```
→ prints `ok`.

### Step 9: Full suite and a test for the helper itself

**Verify**: `python tests/run_suite.py -j 4` → `OK`, 0 failures.

## Test plan

The four existing suites are the real test of this refactor and must pass
**unchanged**:

- `tests/test_look_filters.py` (504 lines) — `test_declining_writes_nothing`
  (332), `test_a_rejected_answer_leaves_no_debris` (347),
  `test_back_still_works` (362)
- `tests/test_quick_outputs.py` (573 lines) —
  `test_declining_writes_nothing_and_keeps_the_container` (114),
  `test_a_rejected_answer_leaves_no_debris` (130), `test_back_still_works` (137)
- `tests/test_composite_inputs.py` (736 lines) —
  `test_declining_writes_nothing` (547),
  `test_a_rejected_answer_leaves_no_debris` (593), `test_back_still_works`
  (596), `test_back_out_of_the_file_question_leaves_no_half_answer` (610)
- `tests/test_volume_and_raw_args.py` (293 lines) —
  `test_declining_writes_nothing` (251),
  `test_a_rejected_answer_leaves_no_debris` (255), `test_back_works_on_both` (267)

**Critical: a prompt stub that returns a CONSTANT hangs forever.** These loops
re-ask after a rejected answer, so a stub like `lambda *a, **k: "sharpen,blur"`
never terminates. Every stub must run off a finite script. The pattern the four
suites already use — copy it, do not invent another
(`tests/test_quick_outputs.py:102-111`):

```python
    def _prompted(self, *replies):
        canned = iter(replies)
        real_ask, real_error = FFmWiz.appio.ask_raw, FFmWiz.appio.error
        FFmWiz.appio.ask_raw = lambda *a, **k: next(canned)
        ...
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.error = real_ask, real_error
```

A constant stub is only safe for the Back tests, because Back raises on the
first reply. The back token is **`"0"`**, not `"b"` — see `BACK_INPUT_TOKENS`
in Current state.

Add ONE new test class for the helper itself. Put it in
`tests/test_volume_and_raw_args.py` (the smallest of the four, and
`step_audio_volume` is the thinnest call site), following its existing `_ask`
helper at line 233:

1. `test_declining_on_the_second_pass_still_clears_the_first` — script
   `["1.5", "n"]` through `appio.ask_optional` with a `forget` that pops a key;
   assert the key is gone. This is the rule the helper exists to hold.
2. `test_a_value_error_re_asks_instead_of_raising` — a `record` that raises
   `ValueError` once then succeeds; assert `appio.error` was called once and
   the second value was recorded.
3. `test_back_propagates_and_records_nothing` — reply `"0"`; assert `Back` is
   raised and `answers` is untouched.
4. `test_a_falsy_record_prints_no_confirmation` — a `record` returning `{}`;
   assert `describe` was never called.
5. `test_a_back_raised_inside_record_is_not_swallowed` — a `record` that raises
   `Back`; assert it propagates. This is the composite partner-prompt path.

**Verify**: `python tests/run_suite.py -k volume_and_raw_args -j 2` → `OK`,
with 5 more tests than before.

## Done criteria

ALL must hold:

- [ ] `python tests/run_suite.py -j 4` → `OK`, 0 failures, 5 more tests than at `aaf0aed`
- [ ] `python tests/run_suite.py -k look_filters -k quick_outputs -k composite_inputs -k volume_and_raw_args -j 4` → `OK`
- [ ] `python tests/run_suite.py -k module_reference_hygiene -j 1` → `OK`
- [ ] `python tests/run_suite.py -k package_imports -j 2` → `OK`
- [ ] `grep -c "while True:" ffmwiz/wizard_look.py ffmwiz/wizard_quick.py ffmwiz/wizard_composite.py ffmwiz/wizard_raw.py`
      → `1`, `1`, `2`, `2` at `aaf0aed`; must be `0`, `0`, **`1`**, `0` after.
      The one that survives in `wizard_composite.py` is `_ask_partner` (line
      175), which is a required prompt and out of scope.
- [ ] `grep -c "is_back_value" ffmwiz/wizard_look.py ffmwiz/wizard_quick.py ffmwiz/wizard_composite.py ffmwiz/wizard_raw.py`
      → `2`, `2`, `3`, `3` at `aaf0aed`; must be `0`, `0`, **`2`**, `0` after.
      The helper owns the Back check for the five optional prompts, so the
      now-unused `from ffmwiz.support.L00_misc_b import is_back_value` must be
      deleted from `wizard_look.py`, `wizard_quick.py` and `wizard_raw.py`.
      `wizard_composite.py` keeps its import for `_ask_partner`.
- [ ] `grep -cw "Back" ffmwiz/wizard_look.py ffmwiz/wizard_quick.py ffmwiz/wizard_composite.py ffmwiz/wizard_raw.py`
      → `2`, `2`, `3`, `3` at `aaf0aed`; must be `0`, `0`, **`2`**, `0` after
      (`wizard_composite.py` keeps the import at line 37 and the `raise Back()`
      inside `_ask_partner`; the one at line 215 moves into the helper).
- [ ] `python -c "import sys; sys.path.insert(0,'.'); import ffmwiz.appio as a; print('ask_optional' in a.__all__)"` → `True`
- [ ] `git diff --stat aaf0aed..HEAD -- tests/` shows only `tests/test_volume_and_raw_args.py`, and only as additions
- [ ] `git status --short` shows only in-scope files

## STOP conditions

Stop and report (do not improvise) if:

- The excerpts above do not match the live code (drift since `aaf0aed`).
- Any existing test in the four suites fails and the fix would require changing
  what that test asserts. The suites are the contract; the helper is what
  changes.
- A migration would change the ORDER or WORDING of what the user sees. The
  YELLOW-then-LIME pair in `step_raw_ffmpeg_args` is the one case with two
  lines; if any other call site needs more than one confirmation line, stop
  rather than widening the helper's contract.
- `ask_optional` needs any import that is not already resolvable inside
  `appio.py`. That would mean it is reaching above its tier and belongs
  somewhere else.
- Step 7 breaks `module_reference_hygiene` or `package_imports`. Revert step 7
  only; keep steps 1-6.
- A test run appears to hang. That is almost certainly a constant prompt stub —
  see the warning in the test plan. Kill it and fix the stub; do not add a
  timeout or an escape counter to `ask_optional`.

## Maintenance notes

- The helper's parameter order is `(answers, title, hint, forget, record,
  describe)` — the three callables last, in the order they run. A sixth
  optional prompt should be able to reach for it without reading its body.
- **Resist adding a `keys=` shortcut next to `forget=`.** It looks like it
  would save three lines at the look and composite call sites, and it cannot
  express `forget_quick_answers`, so the codebase would end up with two ways to
  spell the same thing — which is the exact condition this plan is removing.
- The `record` callable owning the write (rather than returning a dict the
  helper merges) is deliberate: `wizard_quick` must run
  `apply_quick_output_ext` between the update and the confirmation, and
  `wizard_composite` must run two more prompts there. A merge-in-the-helper
  design cannot sequence either.
- A reviewer should check that `forget` still runs before the `n`/`no` decline
  check at every call site. Moving it one line later is invisible in a diff
  review, passes the happy path, and silently reintroduces the exact bug the
  three duplicated comments were written about.
- After this lands, `appio.ask_optional` is the only place the `n`/`no`/empty
  vocabulary of an optional prompt is defined. If a future question wants
  different words (`skip`, `none`), extend the helper — do not write a sixth
  loop.
