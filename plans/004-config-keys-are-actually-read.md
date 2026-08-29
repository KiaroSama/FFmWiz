# Plan 004: Make the four documented config keys work, and add the guard that stops the next dead key

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat aaf0aed..HEAD -- ffmwiz/support/ext08.py ffmwiz/wizard_flow.py ffmwiz/core/constants_config_template.py config.env.example docs/DOCUMENTATION.md`
> On any change, compare the "Current state" excerpts against the live code
> before proceeding; on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW-MED
- **Depends on**: 002 (the composite dispatch fix — this plan's composite
  decision assumes the interactive path is correct)
- **Category**: bug + docs
- **Planned at**: commit `aaf0aed`, 2026-08-29

## Why this matters

Mode 2 is documented as "wizard from config: every question whose value is set
in `config.env` is skipped". Four keys — `video_quick`, `video_composite`,
`audio_volume`, `raw_ffmpeg_args` — are documented in `docs/DOCUMENTATION.md`
(the settings table around line 280 and the full key reference in Appendix B),
and they are wired into the wizard's skip logic. But **nothing reads them**.

The result is the worst possible shape. Both branches lose:

- Key present in `config.env` → `cfg_has(key)` is true → the prompt is skipped
  → the value is never parsed → the setting vanishes.
- Key absent → the gate is false → the prompt is skipped anyway on a config run.

So a user who writes `video_quick=gif` gets an ordinary mp4, with no warning,
no log line and no error. The docs promise a feature that does not exist, which
is worse than an undocumented gap: it is a written instruction that silently
fails.

`video_look` is the counter-example that proves the shape works — it IS read,
at `ffmwiz/support/ext08.py:437`, and it has a test. It is also the only one of
the five that works.

## Current state

**The exemplar to copy**, `ffmwiz/support/ext08.py:437-444`, inside
`apply_config_video_options`:

```python
    look = (config_value(config, "video_look") or "").strip()
    for key in LOOK_ANSWER_KEYS:
        answers.pop(key, None)
    if look and look.lower() not in {"n", "no"}:
        try:
            answers.update(parse_look_tokens(look))
        except ValueError as error:
            fail(f"video_look in config.env is not valid: {error}")
```

Note the shape: read → clear the previous keys → skip on `n`/`no` → parse in a
`try` → `fail()` with a message naming the key on invalid input.

**The gates**, `ffmwiz/wizard_flow.py:203-231` — each new step carries
`and (not config_mode or cfg_has("<key>"))`. Example:

```python
        wizard_base.Step("video_quick",
                    lambda a: (video_reencode_options_applicable(a)
                               and not a.get("_unified_video_editor_used")
                               and not a.get("_unified_video_editor_declined")
                               and (not config_mode or cfg_has("video_quick"))),
                    wizard_quick.step_quick_output),
```

And `cfg_has`, `ffmwiz/wizard_flow.py:112-113`:

```python
    def cfg_has(key: str) -> bool:
        return config_mode and config_value(config, key).strip() != ""
```

**The parsers already exist and are already tested** — you are wiring, not
writing:

| Key | Parser | Answer-key tuple |
|---|---|---|
| `video_quick` | `ffmwiz.wizard_quick.parse_quick_tokens` | `QUICK_ANSWER_KEYS` in `ffmwiz/core/constants.py` |
| `audio_volume` | `ffmwiz.wizard_raw.parse_volume` | writes `audio_volume` only |
| `raw_ffmpeg_args` | `ffmwiz.wizard_raw.parse_raw_arguments` | writes `raw_ffmpeg_args` only |
| `video_composite` | `ffmwiz.wizard_composite.parse_composite_tokens` | `COMPOSITE_ANSWER_KEYS` in `ffmwiz/wizard_composite.py` |

**Confirm nothing reads them today**:
`grep -rn 'config_value(config, "video_quick")\|config_value(config, "audio_volume")\|config_value(config, "raw_ffmpeg_args")\|config_value(config, "video_composite")' ffmwiz/`
→ must print nothing before your change.

**Template state**: `config.env.example` contains `video_look` and NOT the other
four (`grep -cE "^#? ?(video_look|video_quick|video_composite|audio_volume|raw_ffmpeg_args)=" config.env.example` → `1`). The template lives in
`ffmwiz/core/constants_config_template.py` as `CONFIG_TEMPLATE` and CI asserts
the two are byte-identical — see the "Validate config.env template" step in
`.github/workflows/python-smoke.yml`. **You must edit BOTH or CI fails.**

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Template drift check | `python -c "import FFmWiz, pathlib; assert FFmWiz.CONFIG_TEMPLATE==pathlib.Path('config.env.example').read_text(encoding='utf-8')"` | exit 0 |
| Related suites | `python tests/run_suite.py -k quick_outputs -j 2` and `-k volume_and_raw_args` and `-k composite_inputs` and `-k look_filters` | `OK` |
| Full suite | `python tests/run_suite.py -j 4` | `OK`, 0 failures |

**A repository hook blocks raw `python -m unittest`. Always go through
`tests/run_suite.py`.**

## Scope

**In scope**:
- `ffmwiz/support/ext08.py` — add the readers beside the `video_look` one
- `ffmwiz/core/constants_config_template.py` — add the keys to `CONFIG_TEMPLATE`
- `config.env.example` — the identical addition
- `ffmwiz/wizard_flow.py` — ONLY if `video_composite` becomes prompt-only (Step 4)
- `docs/DOCUMENTATION.md` — only the `video_composite` row, and only if Step 4 says so
- `tests/test_volume_and_raw_args.py`, `tests/test_quick_outputs.py` — config tests
- `tests/test_module_reference_hygiene.py` — the new static guard

**Out of scope**:
- Every parser listed above. They are correct and tested; you are calling them.
- `ffmwiz/wizard_look.py` and the `video_look` reader — the working example.
- The skip-map at `ffmwiz/wizard_flow.py:118-146` — leave the entries in place;
  they become correct once the readers exist.

## Git workflow

- Branch: `advisor/004-config-keys-are-actually-read`
- Commit per step group is fine; conventional-commit subjects only.
- Do NOT push or open a PR.

## Steps

### Step 1: Read `audio_volume` and `raw_ffmpeg_args`

Both are single-key parses with no file probing, so they drop straight into
`apply_config_video_options` in `ffmwiz/support/ext08.py`, next to the
`video_look` block. **But `audio_volume` belongs on the audio side** — check
whether `apply_config_audio_options` (same tier) is the better home and put it
there if so; the `video_look` block is the *shape* to copy, not necessarily the
location.

Follow the exemplar exactly: read → clear → skip on `n`/`no` → parse in a
`try` → `fail(f"<key> in config.env is not valid: {error}")`.

**Verify**:
```
python -c "import sys; sys.path.insert(0,'.')
import subprocess
print(subprocess.run([sys.executable,'-c','''
import FFmWiz
from ffmwiz.support import ext08, ext12
import inspect
src = inspect.getsource(ext08)
assert \\'config_value(config, \"audio_volume\")\\' in src, \"audio_volume not read\"
assert \\'config_value(config, \"raw_ffmpeg_args\")\\' in src, \"raw not read\"
print(\"both read\")'''],capture_output=True,text=True).stdout)"
```
→ prints `both read`. (Adjust the module if you put `audio_volume` elsewhere;
the point is that a `config_value` call for each key exists.)

### Step 2: Read `video_quick`

Same shape. `parse_quick_tokens` returns a dict of answer keys; clear
`QUICK_ANSWER_KEYS` first, exactly as the look block clears `LOOK_ANSWER_KEYS`.

Import `QUICK_ANSWER_KEYS` from where it is defined (`ffmwiz/core/constants.py`)
— check it is already in scope via the tier's star-imports before adding an
import line.

**Verify**: `python -c "import sys; sys.path.insert(0,'.'); import inspect; from ffmwiz.support import ext08; assert 'config_value(config, \"video_quick\")' in inspect.getsource(ext08)"` → exit 0

### Step 3: End-to-end proof for the three wired keys

The unit-level check above proves a call exists. This step proves the VALUE
reaches `answers` — which is the thing that was broken.

Write the tests described in the test plan now, before Step 4, and confirm they
pass. Use `tests/test_look_filters.py`'s config test
(`test_the_key_is_parsed_into_the_same_answers_the_prompt_writes`) as the
structural model — read it first.

**Verify**: `python tests/run_suite.py -k volume_and_raw_args -j 2` and
`-k quick_outputs -j 2` → both `OK`

### Step 4: Make `video_composite` prompt-only — the decision is made

An earlier draft of this plan left this open. It is now settled, with evidence,
so you do not have to investigate: **take route (b), remove it.**

Why: the interactive step calls `_ask_partner`
(`ffmwiz/wizard_composite.py:167-201`), which is a `while True:` loop that
validates a second input file and recovers from EVERY failure by calling
`appio.error(...)` and asking again — file not found, same as the main input,
looks like a generated output, no usable streams, no audio to mix. Its probe is
`services.join_load_media_item`.

A config run has nobody to answer those prompts. Wiring `video_composite` as a
config key would mean either duplicating that validation with `fail()` instead
of a re-ask, or accepting a path unprobed and letting the builder discover the
problem — and the builder needs the stream lists the probe returns. Neither is
a small change, and neither belongs in this plan.

So:

1. Remove `"video_composite"` from the skip-map in `ffmwiz/wizard_flow.py`.
2. Drop the `and (not config_mode or cfg_has("video_composite"))` clause from
   its `Step` gate in the same file — **and check what that leaves behind**: if
   removing the clause means the composite question now gets asked on a config
   run where it previously did not, that is the intended behaviour (it is
   prompt-only), but say so explicitly in your report.
3. Remove its row from the settings table in `docs/DOCUMENTATION.md` (around
   line 281) and its entry from the Appendix B key reference.
4. Add one sentence to the Mode 2 section of `docs/DOCUMENTATION.md` saying
   compositing is asked interactively even on a config run, because it needs to
   probe a second input file.
5. Do NOT add `video_composite` to `config.env.example` or `CONFIG_TEMPLATE`.

The other three keys — `video_quick`, `audio_volume`, `raw_ffmpeg_args` — are
pure text-to-answers parses with no file probing, and they get real readers as
Steps 1 and 2 describe.

**Verify**: `grep -rn "video_composite" ffmwiz/ docs/DOCUMENTATION.md config.env.example`
→ hits only in `ffmwiz/wizard_composite.py` (the step itself), the Mode 2
sentence you added, and nowhere in the skip-map, the gate's config clause, the
settings table, Appendix B, or the template.

### Step 5: Add the keys to the template — both copies

Add the newly-working keys to `CONFIG_TEMPLATE` in
`ffmwiz/core/constants_config_template.py` AND to `config.env.example`,
commented out with a one-line explanation, matching how `video_look` appears
there. The two files must stay byte-identical.

**Verify**: `python -c "import FFmWiz, pathlib; assert FFmWiz.CONFIG_TEMPLATE==pathlib.Path('config.env.example').read_text(encoding='utf-8'); print('template OK')"` → prints `template OK`

### Step 6: The guard that makes this class of bug impossible

Add a static test asserting that **every key in the wizard's skip-map is either
read by a `config_value(config, "<key>")` call somewhere in `ffmwiz/`, or is
listed in an explicit prompt-only allowlist inside the test**.

Put it in `tests/test_module_reference_hygiene.py` — that file already walks the
package with `ast` and already has the module-scanning machinery. Read it first
and follow its style: a `test_the_sweep_covers_...` guard-the-guard test, then
the real assertion with a message that names the offending keys.

The allowlist must be a named constant in the test with a comment per entry
explaining why that key is prompt-only. An empty allowlist is fine if Step 4
went route (a).

**Verify**: `python tests/run_suite.py -k module_reference_hygiene -j 1` → `OK`.
Then temporarily comment out one of your new readers, re-run, confirm the guard
FAILS and names that key, and restore it. Report that you did this.

### Step 7: Full suite

**Verify**: `python tests/run_suite.py -j 4` → `OK`, 0 failures

## Test plan

New tests, modelled on `tests/test_look_filters.py:470-484`:

In `tests/test_volume_and_raw_args.py`:
1. `test_audio_volume_from_config_reaches_the_answers` — a config dict with
   `audio_volume=150%` produces the same `answers["audio_volume"]` the prompt
   would have written.
2. `test_raw_args_from_config_reach_the_answers` — likewise for a valid option
   string.
3. `test_an_invalid_audio_volume_in_config_fails_loudly` — a junk value calls
   `fail()` / raises, rather than being silently ignored. **This is the
   behaviour that was missing.**

In `tests/test_quick_outputs.py`:
4. `test_video_quick_from_config_reaches_the_answers` — `video_quick=gif`
   produces `answers["quick_output"] == "gif"`.
5. `test_an_absent_video_quick_leaves_the_job_alone` — negative control.

In `tests/test_module_reference_hygiene.py`:
6. The skip-map guard from Step 6, plus its guard-the-guard.

If Step 4 took route (a), add the composite equivalents of 4 and 5.

**Verify**: each suite `OK`; the full suite `OK`.

## Done criteria

ALL must hold:

- [ ] `grep -rn 'config_value(config, "video_quick")' ffmwiz/` → at least one hit
- [ ] `grep -rn 'config_value(config, "audio_volume")' ffmwiz/` → at least one hit
- [ ] `grep -rn 'config_value(config, "raw_ffmpeg_args")' ffmwiz/` → at least one hit
- [ ] `video_composite` is consistently wired OR consistently removed (Step 4)
- [ ] `python -c "import FFmWiz, pathlib; assert FFmWiz.CONFIG_TEMPLATE==pathlib.Path('config.env.example').read_text(encoding='utf-8')"` exits 0
- [ ] `python tests/run_suite.py -j 4` → `OK`, 0 failures
- [ ] The skip-map guard fails when a reader is removed (you demonstrated this)
- [ ] `git status --short` shows only in-scope files

## STOP conditions

Stop and report if:

- The excerpts do not match the live code (drift since `aaf0aed`).
- `apply_config_video_options` has no place a new reader fits without
  restructuring the function.
- Wiring `video_quick` from config makes an existing quick-output test fail —
  that would mean the interactive path sets something the parser does not, and
  the plan's premise is wrong.
- The template equality check fails and you cannot see why.
- Step 4's investigation shows the composite step does something neither (a)
  nor (b) describes.

## Maintenance notes

- **The rule this establishes**: a key in the skip-map without a reader is a
  bug, and the Step 6 guard now enforces it. Any future wizard question with a
  config key must add the reader in the same change.
- The three-way coupling to remember: the gate in `wizard_flow.py`, the reader
  in the `ext08` tier, and the template in *two* files that CI compares.
- A reviewer should check test 3 hardest — silently ignoring an invalid config
  value is how this whole finding started.
