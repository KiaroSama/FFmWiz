# Plan 001: Make the raw-ffmpeg-options escape hatch keep Windows paths and refuse per-stream reserved options

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat aaf0aed..HEAD -- ffmwiz/wizard_raw.py tests/test_volume_and_raw_args.py`
> If either file changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding; on a mismatch,
> treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug + security
- **Planned at**: commit `aaf0aed`, 2026-08-29
- **Revised**: 2026-08-29 -- Step 2 rewritten after an executor hit its STOP
  condition honestly. `posix=False` was the wrong mechanism; clearing
  `lex.escape` is the right one. Step 1 was verified complete and is
  unchanged.

## Why this matters

FFmWiz builds an ffmpeg argument LIST and shows it to the user before running
it. The promise the whole tool rests on is that **the command you are shown is
the command that runs**. `wizard_raw.py` added an escape hatch letting the user
append their own ffmpeg options, guarded by a denylist of options the wizard
owns. Two defects break that promise:

1. The denylist matches exact strings only, so every per-stream spelling walks
   straight through it. `-c:v:0 libx265` is accepted and appended after the
   wizard's own `-c:v libx264`; ffmpeg honours the later, more specific option,
   so the file is encoded with a codec the summary never mentioned.
2. The parser uses `shlex.split(posix=True)`, which treats `\` as an escape
   character. Every Windows path a user pastes loses its separators:
   `C:\Users\me\logo.png` becomes `C:Usersmelogo.png`. This is a Windows-first
   tool; the failure is not an edge case, it is the normal case.

Both are silent. Neither raises, neither logs, neither shows up in the printed
command as anything but a plausible-looking argument.

## Current state

- `ffmwiz/wizard_raw.py` — parses the user's raw options; the only file with
  the defects. 161 lines total.
- `tests/test_volume_and_raw_args.py` — its existing suite. Follow its style.

The denylist, `ffmwiz/wizard_raw.py:35-41`:

```python
RESERVED_RAW_OPTIONS = {
    "-i", "-y", "-n", "-f", "-progress", "-nostdin", "-hide_banner",
    "-filter_complex", "-lavfi", "-vf", "-filter:v", "-af", "-filter:a",
    "-map", "-map_metadata", "-map_chapters", "-c", "-codec",
    "-c:v", "-codec:v", "-c:a", "-codec:a", "-c:s", "-c:d", "-c:t",
    "-ss", "-t", "-to", "-stream_loop",
}
```

The parser, `ffmwiz/wizard_raw.py:73-89`:

```python
def parse_raw_arguments(text: str) -> list[str]:
    """Split a user's own ffmpeg options. Raises ValueError.

    `shlex.split` with posix=True so `-metadata title="my film"` arrives as two
    arguments and keeps its spaces, which is how the line reads in every
    tutorial it will be copied from.
    """
    try:
        parts = shlex.split(text.strip(), posix=True)
    except ValueError as error:      # an unbalanced quote
        raise ValueError(f"could not read that: {error}")
    for part in parts:
        if part.lower() in RESERVED_RAW_OPTIONS:
            raise ValueError(
                f"{part} is set by the wizard itself; changing it here would "
                f"make the printed command and the job disagree")
    return parts
```

Measured on the current code (run these yourself to confirm the starting point):

```
parse_raw_arguments(r'-metadata comment=C:\Users\mobin\video')
  -> ['-metadata', 'comment=C:Usersmobinvideo']        # backslashes gone

parse_raw_arguments('-c:v:0 libx265')     -> accepted   # bypass
parse_raw_arguments('-vf:0 scale=2:2')    -> accepted   # bypass
parse_raw_arguments('-map_metadata:s:0 1')-> accepted   # bypass
parse_raw_arguments('-codec:a:1 mp3')     -> accepted   # bypass
```

**Repo conventions to match**: raising `ValueError` with a lowercase,
user-facing sentence explaining *why* (see the existing message above);
docstrings that state the reason for a choice, not just what it does; module
ends with an explicit `__all__` list.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| This suite | `python tests/run_suite.py -k volume_and_raw_args -j 2` | `OK` |
| Full suite | `python tests/run_suite.py -j 4` | `OK`, 0 failures |
| Import check | `python -c "import ffmwiz.wizard_raw"` | exit 0 |

**A repository hook blocks raw `python -m unittest`. Always go through
`tests/run_suite.py`.**

## Scope

**In scope** (the only files you may modify):
- `ffmwiz/wizard_raw.py`
- `tests/test_volume_and_raw_args.py`

**Out of scope** (do NOT touch):
- `ffmwiz/wizard_build_b.py` — where the parsed options are appended to the
  command. The append order is correct and deliberate; only the parse is wrong.
- The `parse_volume` function in the same file — unrelated and correct.
- Any change that makes the raw options run through a shell. The list-not-string
  design is the tool's core safety property.

## Git workflow

- Branch: `advisor/001-raw-arguments-parsing`
- One commit. Message style is conventional commits, short subject only — see
  `git log --oneline -5` (e.g. `fix: let the NVENC tests run on real hardware`).
- Do NOT push or open a PR.

## Steps

### Step 1: Reject every per-stream spelling of a reserved option

Replace the exact-set membership test with a normalising check. An ffmpeg
option specifier is the base option followed by colon-separated qualifiers:
`-c:v:0`, `-map_metadata:s:0`, `-codec:a:1`, `-vf:0`. Strip the qualifiers and
compare the base.

The rule to implement, in `parse_raw_arguments`:

- Lowercase the argument.
- If it starts with `-`, take everything up to the first `:` as the base
  (`-c:v:0` → `-c`, `-vf:0` → `-vf`, `-map_metadata:s:0` → `-map_metadata`).
- Reject when EITHER the full argument OR the base is in the denylist.

Add `-map_channel`, `-filter`, `-fpre`, `-vpre`, `-apre` to
`RESERVED_RAW_OPTIONS` while you are there: the first is a mapping option in
the same family as `-map`, and the `*pre` family reads an option file that can
set anything the wizard owns.

Keep the base set spelled with full names AND short names where both exist
(`-c` and `-codec` are both already there — that stays correct because both are
bases).

Add a comment above the check saying why the base is compared, naming
`-c:v:0` as the concrete bypass, in the style of the existing comments.

**Verify**:
```
python -c "import sys; sys.path.insert(0,'.'); from ffmwiz.wizard_raw import parse_raw_arguments
for probe in ('-c:v:0 x', '-vf:0 x', '-map_metadata:s:0 1', '-codec:a:1 mp3', '-c:v x', '-map 0:a'):
    try:
        parse_raw_arguments(probe); print('LEAKED:', probe)
    except ValueError: print('refused:', probe)"
```
→ six `refused:` lines, no `LEAKED:`.

### Step 2: Keep backslashes in Windows paths

**Do NOT use `posix=False`.** That was this plan's first instruction and it is
wrong: `posix=False` also stops shlex raising on an unbalanced quote, which
breaks `test_an_unbalanced_quote_is_reported_not_guessed`, and it leaves quote
characters attached to the token, which breaks
`test_a_quoted_value_survives_with_its_spaces`. Both of those tests are right
and must keep passing.

The correct change keeps posix quoting and disables ONLY the escape character:

```python
    lex = shlex.shlex(text.strip(), posix=True)
    lex.whitespace_split = True
    lex.escape = ""      # a Windows path is not an escape sequence
    try:
        parts = list(lex)
    except ValueError as error:      # an unbalanced quote
        raise ValueError(f"could not read that: {error}")
```

`shlex.split(text, posix=True)` is a convenience wrapper around exactly this
object, with `escape` left at its default backslash. Setting it to `""` makes
backslash an ordinary character, while quote handling is unchanged — the quotes
are still consumed, and an unbalanced one still raises `No closing quotation`.

Measured on this repository's Python before this revision was written:

```
r'-metadata comment=C:\Users\mobin\video'
    -> ['-metadata', 'comment=C:\\Users\\mobin\\video']     backslashes kept
'-metadata title="My film" -tune film'
    -> ['-metadata', 'title=My film', '-tune', 'film']      quotes consumed
'-metadata title="unclosed'
    -> raises ValueError("No closing quotation")            still detected
```

All three at once, which is what `posix=False` could not do.

Update the `parse_raw_arguments` docstring. It currently justifies `posix=True`
for quoted values — keep that reason and add the escape one, saying a Windows
path is why `escape` is cleared. Without that sentence someone will restore the
default later believing it is a tidy-up.

No `_unquote` helper is needed. If you wrote one for the earlier version of this
step, delete it.

**Verify**:

```
python -c "import sys; sys.path.insert(0,'.'); from ffmwiz.wizard_raw import parse_raw_arguments
print(parse_raw_arguments(r'-metadata comment=C:\Users\me\clip'))
print(parse_raw_arguments('-metadata title=\"my film\"'))
try:
    parse_raw_arguments('-metadata title=\"unclosed')
    print('UNBALANCED NOT CAUGHT')
except ValueError as e:
    print('unbalanced raises:', e)"
```

Expected: the first line shows the backslashes intact, the second shows
`title=my film` with no quote characters, and the third prints
`unbalanced raises: No closing quotation`.

### Step 3: Confirm nothing else regressed

**Verify**: `python tests/run_suite.py -k volume_and_raw_args -j 2` → `OK`

## Test plan

Add to `tests/test_volume_and_raw_args.py`, following the structure of the
tests already there (a class per behaviour, one assertion idea per test, a
comment naming the real-world failure each test pins):

1. `test_a_windows_path_keeps_its_separators` — `parse_raw_arguments` on a
   `C:\Users\...\file.png` string returns the backslashes intact.
2. `test_a_quoted_value_keeps_its_spaces_and_loses_its_quotes` —
   `-metadata title="my film"` → `['-metadata', 'title=my film']`.
3. `test_every_per_stream_spelling_of_a_reserved_option_is_refused` — a
   subTest loop over at least `-c:v:0`, `-codec:v:0`, `-vf:0`, `-af:1`,
   `-map_metadata:s:0`, `-c:a:1`; each raises `ValueError`.
4. `test_an_unrelated_per_stream_option_is_still_allowed` — the guard must not
   over-block: `-b:v:0 2M` and `-metadata:s:v:0 rotate=90` are NOT wizard-owned
   and must be accepted. **This is the test that stops the fix from becoming a
   different bug.**
5. `test_the_option_file_family_is_refused` — `-fpre`, `-vpre`, `-apre`.

**Verify**: `python tests/run_suite.py -k volume_and_raw_args -j 2` → `OK`,
with at least 5 more tests than before your change.

## Done criteria

ALL must hold:

- [ ] `python tests/run_suite.py -k volume_and_raw_args -j 2` → `OK`
- [ ] `python tests/run_suite.py -j 4` → `OK`, 0 failures
- [ ] The six probes in Step 1 all print `refused:`
- [ ] `python -c "import sys; sys.path.insert(0,'.'); from ffmwiz.wizard_raw import parse_raw_arguments; assert '\\\\' in parse_raw_arguments(r'-x C:\\a\\b')[1]"` exits 0
- [ ] `git status --short` shows only the two in-scope files

## STOP conditions

Stop and report (do not improvise) if:

- The excerpts above do not match the live code (drift since `aaf0aed`).
- Clearing `lex.escape` breaks an existing test in
  `test_volume_and_raw_args.py`. (An earlier version of this plan said to use
  `posix=False`; that DID break two tests, which is why the instruction
  changed. If `lex.escape = ""` breaks something too, STOP -- do not fall back
  to `posix=False`.)
- The base-option check turns out to block something the existing suite
  expects to be allowed — report which, do not weaken the check silently.
- You find the parsed options are joined into a string anywhere downstream.

## Maintenance notes

- The denylist is the security boundary for this feature. Anything added to the
  wizard's own command later (a new `-map_*`, a new filter flag) must be added
  here at the same time, or the escape hatch can silently override it.
- A reviewer should check test 4 hardest: an over-broad prefix match that
  blocks `-b:v:0` would make the feature useless while looking like a fix.
- Deferred deliberately: validating that the user's options are *valid ffmpeg*.
  That is ffmpeg's job and it reports it clearly; this plan only enforces the
  wizard's ownership boundary.
