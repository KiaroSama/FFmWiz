# Plan 002: Dispatch the composite builder for an audio-only mix, not just a picture mode

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat aaf0aed..HEAD -- ffmwiz/wizard_b.py ffmwiz/wizard_composite.py tests/test_composite_inputs.py`
> If any changed since this plan was written, compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, treat it as
> a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `aaf0aed`, 2026-08-29

## Why this matters

The compositing feature accepts five modes: `overlay`, `pip`, `hstack`,
`vstack` and `mix`. The first four place a second *picture*; `mix` blends a
second *audio* track. The dispatcher that decides to call the composite builder
tests only `composite_mode`, which the parser sets for the four picture modes
and never for `mix`.

So a user who answers `mix` is asked for a second audio file, gives one, sees
the wizard accept it — and gets an ordinary single-input encode with no mixed
audio, no warning and no error. The answer is recorded and silently discarded.

This is the same class of defect the codebase already documented once: the
comment on the very branch being fixed says compositing "was asked, answered
and then ignored ... which is worse than not offering the feature." That fix
covered the picture modes. `mix` was left on the wrong side of the same gate.

## Current state

The dispatcher, `ffmwiz/wizard_b.py:222-231`:

```python
    elif answers.get("composite_mode"):
        # Without this branch the compositing question was asked, answered and
        # then ignored: the job ran as an ordinary encode with no overlay and
        # no warning, which is worse than not offering the feature. Compositing
        # needs its own command because it maps a SECOND input into the graph,
        # which `build_ffmpeg_command` has no shape for.
        output_path = services.build_output_path(answers)
        answers["output_path"] = output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = wizard_build_b.build_composite_command(answers, output_path)
    else:
        cmd = wizard_build.build_ffmpeg_command(answers)
```

The parser sets a DIFFERENT key for `mix`, `ffmwiz/wizard_composite.py:87-97`:

```python
        if token in _PICTURE_MODES:
            mode = _PICTURE_MODES[token]
            ...
            chosen["composite_mode"] = mode
            continue
        if token in _MIX_MODES:
            chosen["composite_audio_mix"] = True
            continue
```

Confirmed by running it:

```
parse_composite_tokens('mix')      -> {'composite_audio_mix': True}
parse_composite_tokens('overlay')  -> {'composite_mode': 'overlay'}
```

The builder already handles the audio-only case correctly —
`ffmwiz/wizard_build_b.py:1473-1477` accepts either input and only raises when
BOTH are absent:

```python
    picture_path = answers.get("composite_path")
    audio_path = answers.get("composite_audio_path")
    if not (picture_path or audio_path):
        raise ValueError("build_composite_command needs a second input")
```

**So the builder is right and only the dispatch condition is wrong.** Do not
change the builder.

**Repo conventions**: comments explain the defect a line prevents, with the
observable symptom. Match that voice — the existing comment on this branch is
the model.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| This suite | `python tests/run_suite.py -k composite_inputs -j 2` | `OK` |
| Full suite | `python tests/run_suite.py -j 4` | `OK`, 0 failures |

**A repository hook blocks raw `python -m unittest`. Always go through
`tests/run_suite.py`.**

## Scope

**In scope**:
- `ffmwiz/wizard_b.py` — the dispatch condition only
- `tests/test_composite_inputs.py`

**Out of scope**:
- `ffmwiz/wizard_build_b.py` — `build_composite_command` already handles an
  audio-only composite. Changing it is not needed and risks the picture path.
- `ffmwiz/wizard_composite.py` — the parser's two-key shape is deliberate
  (`mix` can combine with a picture mode: `overlay,mix` is a valid answer that
  sets both keys). Do NOT collapse them into one key.

## Git workflow

- Branch: `advisor/002-composite-audio-mix-dispatch`
- One commit, conventional-commit subject only (see `git log --oneline -5`).
- Do NOT push or open a PR.

## Steps

### Step 1: Widen the dispatch condition

Change the branch condition at `ffmwiz/wizard_b.py:222` so it fires when the
job asks for compositing in EITHER form:

```python
    elif answers.get("composite_mode") or answers.get("composite_audio_mix"):
```

Extend the existing comment with one sentence naming the new case: `mix` sets
`composite_audio_mix` and never `composite_mode`, so an audio-only composite
used to fall through to the ordinary encode and lose the second track.

**Verify**: `python -c "import sys; sys.path.insert(0,'.'); import inspect; from ffmwiz import wizard_b; s=inspect.getsource(wizard_b); assert 'composite_audio_mix' in s.split('elif answers.get(\"composite_mode\")')[1][:200]"` → exit 0

### Step 2: Prove the dispatcher actually chooses the composite builder

This defect existed *because* every test called the builder directly. The test
must drive the dispatcher and observe which builder it picked.

There is already a test in `tests/test_composite_inputs.py` named along the
lines of `TheAnswerActuallyReachesTheCommand` that does this for a picture
mode — read it and follow its exact mechanism (it monkeypatches the builders
and asserts which one was called). Add the `mix` case to it.

**Verify**: `python tests/run_suite.py -k composite_inputs -j 2` → `OK`

### Step 3: Full suite

**Verify**: `python tests/run_suite.py -j 4` → `OK`, 0 failures

## Test plan

Add to `tests/test_composite_inputs.py`, in the class that drives the real
dispatcher (not the builder-level classes):

1. `test_an_audio_only_mix_reaches_the_composite_builder` — answers with
   `composite_audio_mix=True`, `composite_audio_path` set and NO
   `composite_mode`; assert `build_composite_command` was the builder called,
   not `build_ffmpeg_command`.
2. `test_a_picture_mode_still_reaches_it` — the existing behaviour, so the
   widened condition is shown not to have broken the original case.
3. `test_a_job_with_neither_key_still_takes_the_ordinary_encode` — the
   negative control. Without this, a condition of `True` would pass tests 1
   and 2.

Model them on the existing dispatcher test in the same file.

**Verify**: `python tests/run_suite.py -k composite_inputs -j 2` → `OK`, 3 more
tests than before.

## Done criteria

ALL must hold:

- [ ] `python tests/run_suite.py -k composite_inputs -j 2` → `OK`
- [ ] `python tests/run_suite.py -j 4` → `OK`, 0 failures
- [ ] The three new tests exist and pass
- [ ] `git status --short` shows only the two in-scope files

## STOP conditions

Stop and report if:

- The excerpts do not match the live code (drift since `aaf0aed`).
- `build_composite_command` raises on an audio-only job — that would mean the
  builder does NOT handle this case and the plan's premise is wrong. Report
  the exact traceback; do not start changing the builder.
- The existing dispatcher test uses a mechanism you cannot extend to `mix`
  without changing what it asserts for the picture case.

## Maintenance notes

- The parser can legitimately set both keys (`overlay,mix`). The dispatch
  condition is an OR for exactly that reason; a future refactor that collapses
  the two answer keys into one must keep `overlay,mix` expressible.
- A reviewer should check test 3 — the negative control is what proves the
  condition still discriminates.
- Related: plan 004 wires the `video_composite` config key. That plan may
  decide the composite feature is prompt-only; this fix is needed either way,
  because it is on the interactive path.
