# Plan 003: Make `loop=N` reach boomerang and thumbnail, or refuse the combination

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat aaf0aed..HEAD -- ffmwiz/wizard_quick.py ffmwiz/wizard_build_b.py tests/test_quick_outputs.py`
> On any change, compare the "Current state" excerpts against the live code
> before proceeding; on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `aaf0aed`, 2026-08-29

## Why this matters

The quick-output prompt accepts a comma-separated answer: a mode plus options.
`loop=N` parses successfully alongside any mode:

```
parse_quick_tokens('gif,loop=3')        -> {'quick_output': 'gif',       'loop_count': 3}
parse_quick_tokens('boomerang,loop=2')  -> {'quick_output': 'boomerang', 'loop_count': 2}
```

But `loop_count` is consumed in exactly one place — `gif_input_options`, which
only the GIF passes call. A user who answers `boomerang,loop=2` or
`thumb,loop=5` gets their answer accepted, sees no complaint, and receives an
output that ignores the loop entirely.

Either spelling is defensible: make the loop work everywhere it makes sense, or
refuse the combination at parse time. What is not defensible is accepting it
and dropping it, which is what happens now. **This plan makes it work where it
is meaningful and refuse where it is not** — see Step 1 for the decision, which
is not left to the executor.

## Current state

- `ffmwiz/wizard_quick.py` — the prompt and `parse_quick_tokens` (the parser
  that accepts `loop=N` unconditionally, lines 80-92).
- `ffmwiz/wizard_build_b.py` — the builders. `append_stream_loop` is defined at
  line 1678 and called at exactly ONE site, line 1644, inside
  `gif_input_options`.
- `ffmwiz/encoding.py:385` `run_quick_output_stages` — runs the staged plan.

The single consumer, `ffmwiz/wizard_build_b.py:1636-1644`:

```python
def gif_input_options(answers: dict[str, Any]) -> list[str]:
    """Input options BOTH GIF passes must carry, in front of their `-i`.
    ...
    """
    options: list[str] = []
    append_stream_loop(options, answers)
```

The helper itself, `ffmwiz/wizard_build_b.py:1678-1691`:

```python
def append_stream_loop(cmd: list[str], answers: dict[str, Any]) -> None:
    """`-stream_loop N`, which is an INPUT option: it must precede its `-i`.
    ...
        count = int(answers.get("loop_count") or 0)
    ...
        cmd.extend(["-stream_loop", str(count)])
```

Confirm the single-consumer claim yourself:
`grep -n "append_stream_loop\|loop_count" ffmwiz/wizard_build_b.py`
→ the only call site is inside `gif_input_options`.

**The four quick modes** are `gif`, `boomerang`, `thumb`, and plain `loop=N`
with no mode (looping the ordinary encode). Read `_MODES` in
`ffmwiz/wizard_quick.py` to confirm the exact names before you rely on them.

**Repo conventions**: `ValueError` with a lowercase user-facing sentence naming
what to do instead; comments state the observable symptom a line prevents.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| This suite | `python tests/run_suite.py -k quick_outputs -j 2` | `OK` |
| Full suite | `python tests/run_suite.py -j 4` | `OK`, 0 failures |

**A repository hook blocks raw `python -m unittest`. Always go through
`tests/run_suite.py`.**

## Scope

**In scope**:
- `ffmwiz/wizard_quick.py`
- `ffmwiz/wizard_build_b.py` — only the boomerang input-options path
- `tests/test_quick_outputs.py`

**Out of scope**:
- `ffmwiz/encoding.py` — the stage runner. If the loop needs a change there,
  that is a STOP condition, not a licence to edit it.
- The GIF palette two-pass logic — it already handles the loop correctly.

## Steps

### Step 1: Apply this decision (do not re-decide it)

| Mode | `loop=N` | Why |
|---|---|---|
| `gif` | works today — leave alone | already correct |
| `boomerang` | **make it work** | a boomerang is a loop by nature; repeating it is the obvious want |
| plain `loop=N`, no mode | **make it work** | this is the whole meaning of the answer |
| `thumb` | **refuse at parse time** | a thumbnail is one still frame; `-stream_loop` on it is meaningless |

### Step 2: Refuse `thumb` + `loop`

In `parse_quick_tokens`, after the whole answer is parsed (so order does not
matter — `loop=2,thumb` must fail exactly like `thumb,loop=2`), raise
`ValueError` when the mode is the thumbnail mode and `loop_count` is set. Word
it like the existing messages: say a thumbnail is a single frame so there is
nothing to loop.

**Verify**:
```
python -c "import sys; sys.path.insert(0,'.'); from ffmwiz.wizard_quick import parse_quick_tokens
for probe in ('thumb,loop=2', 'loop=2,thumb'):
    try: parse_quick_tokens(probe); print('ACCEPTED:', probe)
    except ValueError as e: print('refused:', probe, '--', e)
print('gif+loop still ok:', parse_quick_tokens('gif,loop=3'))"
```
→ two `refused:` lines, then the gif dict with `loop_count: 3`.

### Step 3: Make boomerang honour the loop

Find where the boomerang builder assembles its input options in
`ffmwiz/wizard_build_b.py` (search for the boomerang command builder near
`gif_input_options`). Call `append_stream_loop` on its input options in the
same position `gif_input_options` does — **before** the `-i`, because
`-stream_loop` is an input option and has no effect after one.

A boomerang is built from two halves (forward + reversed) that are then
concatenated. Read the builder before choosing where the loop goes: looping the
INPUT of each half is not the same as looping the finished boomerang. **The
user means the finished boomerang plays N+1 times.** If the builder's shape
makes that impossible without touching `ffmwiz/encoding.py`, that is a STOP
condition — report which and stop.

**Verify**: build a boomerang command with `loop_count` set and assert
`-stream_loop` appears before the relevant `-i` — write this as the test in the
test plan rather than an ad-hoc command.

### Step 4: Make a bare `loop=N` reach the ordinary encode

A `loop=N` answer with no mode must put `-stream_loop N` in front of the main
input of the ordinary encode command. Confirm whether that already happens:

```
python -c "import sys; sys.path.insert(0,'.'); from ffmwiz.wizard_quick import parse_quick_tokens; print(parse_quick_tokens('loop=2'))"
```

If it yields only `{'loop_count': 2}` with no `quick_output`, trace whether any
builder reads it on the ordinary path. If nothing does, wire it the same way —
`append_stream_loop` on the main command's input options. If the ordinary path
turns out to have no input-options seam, STOP and report rather than inventing
one.

**Verify**: covered by the test plan below.

### Step 5: Full suite

**Verify**: `python tests/run_suite.py -j 4` → `OK`, 0 failures

## Test plan

Add to `tests/test_quick_outputs.py`, following its existing structure:

1. `test_a_thumbnail_refuses_a_loop` — both orderings raise `ValueError`.
2. `test_a_boomerang_loops_the_finished_clip` — build with `loop_count=2`,
   assert `-stream_loop 2` is present AND appears at an index lower than the
   `-i` it applies to. Assert the position, not just presence: an option in the
   wrong place is silently ignored by ffmpeg, which is the bug class here.
3. `test_a_bare_loop_reaches_the_ordinary_encode` — as decided in Step 4.
4. `test_a_gif_still_loops` — the existing behaviour must not regress.
5. `test_no_loop_answer_emits_no_stream_loop` — negative control.

**Verify**: `python tests/run_suite.py -k quick_outputs -j 2` → `OK`, at least
5 more tests than before.

## Done criteria

ALL must hold:

- [ ] `python tests/run_suite.py -k quick_outputs -j 2` → `OK`
- [ ] `python tests/run_suite.py -j 4` → `OK`, 0 failures
- [ ] Both `thumb,loop=2` and `loop=2,thumb` raise `ValueError`
- [ ] `grep -c "append_stream_loop" ffmwiz/wizard_build_b.py` returns at least 4
      (definition, `__all__`, gif site, boomerang site)
- [ ] `git status --short` shows only the three in-scope files

## STOP conditions

Stop and report if:

- The excerpts do not match the live code (drift since `aaf0aed`).
- Looping the finished boomerang requires changing `ffmwiz/encoding.py` or the
  stage plan shape.
- The ordinary encode path has no place to put an input option.
- Any existing quick-output test fails and the fix would require changing what
  that test asserts.

## Maintenance notes

- `-stream_loop` is an INPUT option. Every future use must go before its `-i`;
  the position assertion in test 2 is what protects that.
- If a fifth quick mode is added, decide explicitly whether `loop` applies to
  it. The parse-time refusal in Step 2 is the pattern: an unmeaning
  combination should be refused, never accepted and dropped.
- A reviewer should check that test 2 asserts the INDEX, not just membership.
