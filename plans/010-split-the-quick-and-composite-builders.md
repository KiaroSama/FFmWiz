# Plan 010: Move the composite and quick-output builders out of `wizard_build_b.py` into a re-exported leaf

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat aaf0aed..HEAD -- ffmwiz/wizard_build_b.py tests/test_module_reference_hygiene.py tests/test_package_imports.py tests/test_speed_frame_retention.py`
> If any of those changed since this plan was written, compare the "Current
> state" excerpts and the line numbers against the live code before proceeding;
> on a mismatch, treat it as a STOP condition. **Re-run the measurement in
> step 1 in every case** — this plan's whole safety argument is one measured
> number.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: `plans/003-loop-count-reaches-every-quick-output.md`
- **Category**: tech-debt
- **Planned at**: commit `aaf0aed`, 2026-08-29

## Why this matters

`ffmwiz/wizard_build_b.py` is 1777 lines and holds four unrelated subsystems:
hardsub, join/subtitle timeline, the picture filter chain, and — appended last
— the compositing and quick-output builders. The repo has a well-worn pattern
for exactly this (a new leaf module, star-imported and re-exported by its
parent, so every existing consumer is untouched), and the composite/quick block
was appended in a way that makes a clean cut possible: **measured at `aaf0aed`,
nothing in the first 1354 lines references a single name defined after them.**

This is a pure move. No behaviour changes, no call site outside the file
changes, no test changes. The win is a 1777-line file becoming roughly 1460,
and a new ~440-line module whose name says what is in it.

The cut is not "everything after line 1355", and the difference matters — see
the measurement below. Three GIF functions must stay behind, and the reason
they must stay is enforced by a test.

## Current state

`ffmwiz/wizard_build_b.py`, 1777 lines. Its top-level inventory, measured from
the AST at `aaf0aed`:

```
    build_hardsub_video_filter                  94- 135  (42)
    build_hardsub_command                      138- 253  (116)
    build_cut_filter_complex                   256- 327  (72)
    join_extras_outcome_notes                  330- 366  (37)
    extract_subtitle_text                      369- 434  (66)
    build_joined_subtitle_files                437- 514  (78)
    append_subtitle_track_metadata             517- 529  (13)
    encode_timeline_map                        532- 553  (22)
    slice_subtitle_tracks_into_parts           556- 591  (36)
    joined_timeline_map                        594- 614  (21)
    encode_subtitle_retiming_required          617- 627  (11)
    confirm_bitmap_subtitle_drop               630- 657  (28)
    build_retimed_subtitle_inputs              660- 735  (76)
    build_split_subtitle_inputs                738- 847  (110)
    build_join_encode_command                  850-1179  (330)
    build_cpu_video_filter                    1182-1284  (103)
    build_orientation_filters                 1287-1304  (18)
    build_look_filters                        1307-1347  (41)
    build_fade_filters                        1350-1352  (3)
    composite_active                          1355-1357  (3)
    _even                                     1360-1362  (3)
    build_composite_filter_graph              1365-1463  (99)
    build_composite_command                   1466-1540  (75)
    QUICK_OUTPUT_MODES                        1550-1550  (1)
    quick_output_mode                         1553-1567  (15)
    gif_frame_rate                            1570-1579  (10)
    gif_target_width                          1582-1595  (14)
    gif_scale_chain                           1598-1607  (10)
    gif_filter_chain                          1610-1633  (24)
    gif_input_options                         1636-1654  (19)
    gif_unsupported_answer_notes              1657-1675  (19)
    append_stream_loop                        1678-1691  (14)
    build_gif_palette_command                 1694-1700  (7)
    build_gif_write_command                   1703-1719  (17)
    build_thumbnail_command                   1722-1736  (15)
    __all__                                   1738-1771  (34)
```

The file marks the boundary itself — `ffmwiz/wizard_build_b.py:1543-1550`:

```python
# ---------------------------------------------------------------------------
# Quick outputs: the argv pieces. `wizard_build.build_quick_output_stages`
# orchestrates them, because assembling a multi-stage job needs
# `build_ffmpeg_command` and this module must not import the facade that
# re-exports it.
# ---------------------------------------------------------------------------

QUICK_OUTPUT_MODES = ("gif", "boomerang", "thumbnail")
```

### The measured seam

Measured at `aaf0aed` by building a name-level dependency graph over the file's
top-level definitions (step 1 gives you the exact script):

| candidate leaf | names | source lines | **leaf → rest** | rest → leaf |
|---|---|---|---|---|
| the whole block, `composite_active`..`build_thumbnail_command` | 16 | 383 | **1** — `build_cpu_video_filter` at line 1629 | 0 |
| the composite block alone | 4 | 180 | **0** | 0 |
| **the 13 names this plan moves** | 13 | 297 | **0** | 3 |
| the join half, `join_extras_outcome_notes`..`build_join_encode_command` | 12 | 828 | 0 | 1 |

**"leaf → rest" is the number that must be zero.** The facade star-imports the
leaf at its own end, so a name defined in the leaf resolves fine from the
facade at call time (that is what "rest → leaf" counts, and it is harmless).
The reverse does not work: the leaf may not import the facade, so a leaf
function calling a facade function raises `NameError` the first time that
branch runs.

The single blocker for taking the whole block is `gif_filter_chain` —
`ffmwiz/wizard_build_b.py:1627-1633`:

```python
    if prepared:
        return gif_scale_chain(answers)
    base = build_cpu_video_filter(answers) or ""
    parts = [part for part in base.split(",")
             if part and not part.startswith("format=")]
    parts.append(gif_scale_chain(answers))
    return ",".join(parts)
```

`build_cpu_video_filter` is defined at line 1182 and **cannot come along**. It
emits the speed filter, and a guard test pins the speed filter and its output
option to the same file —
`tests/test_speed_frame_retention.py:290-325`:

```python
class EveryBuilderThatRetimesAlsoStatesItsTiming(unittest.TestCase):
    """The filter and the output option are a pair; three builders emit the filter.
    ...
    """

    MODULES = ("ffmwiz/wizard_build_b.py",
               "ffmwiz/support/ext04b.py", "ffmwiz/support/L04.py")
    ...
    def test_the_option_lives_beside_the_filter_that_needs_it(self):
        for relative in self.MODULES:
            with self.subTest(module=relative):
                text = self._source(relative)
                self.assertIn("build_video_speed_filter(", text, ...)
                self.assertIn("VIDEO_SPEED_OUTPUT_TIMING_ARGS", text, ...)

    def test_the_guard_covers_every_builder_that_emits_the_filter(self):
        # Guard the guard: a fifth builder added later must fail here rather
        # than quietly reintroduce the defect.
        ...
        self.assertEqual(sorted(self.MODULES), emitting)
```

In `wizard_build_b.py`, `build_video_speed_filter(` appears at lines 1040 and
1234, and `VIDEO_SPEED_OUTPUT_TIMING_ARGS` appears only at line 1148. Move
`build_cpu_video_filter` (line 1234) out and the new file emits the filter
without the option — the second test fails, and adding the new file to
`MODULES` then fails the first. So `gif_filter_chain` stays, and with it the
two functions that call it, `build_gif_palette_command` (line 1699) and
`build_gif_write_command` (line 1718).

**None of the 13 names this plan moves contains `build_video_speed_filter(`,
`VIDEO_SPEED_OUTPUT_TIMING_ARGS`, `square_pixel_scale_chain` or a `reset_sar`
string** — all of those live at lines 1002, 1040, 1148, 1209 and 1217, which
stay put. Neither `tests/test_speed_frame_retention.py` nor
`tests/test_sar_capability.py` (whose `SOURCES` at line 241 also names this
file) is affected.

### The established pattern to copy

The parent binds the leaf under a `_`-prefixed alias, star-imports it, and
merges `__all__`. Live example, `ffmwiz/wizard_build.py:900-907`:

```python
# wizard_build_b holds an overflow slice of this module (split for file size).
# Bound twice on purpose: the call sites above address it through the module
# object so a test patching the DEFINING module is seen, and the `_` alias is
# what tests/test_module_reference_hygiene reads to find re-export pairs.
from ffmwiz import wizard_build_b as _wizard_build_b  # noqa: E402
from ffmwiz import wizard_build_b  # noqa: E402,F401
from ffmwiz.wizard_build_b import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_wizard_build_b.__all__)
```

The leaf's own shape — docstring saying what was split out and why, the full
star-import prelude, `__all__` at the end. Live examples to read before you
write:
`ffmwiz/support/ext04c.py` (734 lines), `ffmwiz/guibridge_tk_common.py` (454),
`ffmwiz/wizard_flow_b.py` (763). `ext04c.py:1-11` sets the tone:

```python
"""Bounded audio reverse: the staged plan every audio-reverse path shares.

Split out of `ext04b` as its own responsibility. `areverse` buffers its ENTIRE
input, so peak memory tracks duration directly and a long track cannot be
reversed in one command; everything here exists to reverse it in bounded
lossless chunks instead, and to REFUSE rather than run unbounded when a
combination cannot be staged exactly.

`ext04b` re-exports this module at its end, so every existing
`from ffmwiz.support.ext04b import *` keeps working unchanged.
"""
```

`ffmwiz/wizard_build_b.py` already ends with a sibling module-object import that
the new leaf will need to copy — `ffmwiz/wizard_build_b.py:1774-1777`:

```python
# wizard_base holds the encode-option builders this module calls. It is a leaf:
# it imports neither wizard nor wizard_build, so nothing here can re-enter a
# facade that is still merging its __all__.
from ffmwiz import wizard_base  # noqa: E402,F401
```

`build_composite_command` uses it — `ffmwiz/wizard_build_b.py:1523`:

```python
        wizard_base.append_video_encode_options(cmd, job, video_encoder, tag, profile)
```

and `appio` at lines 1495 and 1515 (`appio.note(...)`). Both are in the prelude
at `ffmwiz/wizard_build_b.py:83` and at line 1777 respectively.

### The direction rule, and the tests that enforce it

**The new leaf must NOT import `wizard_build_b` in any form.** Not
`from ffmwiz.wizard_build_b import X`, not `from ffmwiz import wizard_build_b`,
not inside a function body. `tests/test_module_reference_hygiene.py:140-159`:

```python
    def test_no_leaf_imports_the_facade_that_reexports_it(self):
        offenders = []
        for facade, leaf in self._reexport_pairs():
            ...
            patterns = [re.compile(rf"^from {re.escape(facade)} import\b"),
                        re.compile(rf"^from {re.escape(pkg)} import {re.escape(base)}\b")]
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if any(p.match(stripped) for p in patterns):
                    offenders.append(f"{leaf} imports {facade}: {stripped[:70]}")
```

Note it matches on the **stripped** line, so an indented import inside a
function is caught too. The pair is discovered from the literal
`__all__ = list(__all__)` line — `tests/test_module_reference_hygiene.py:110-133`
— so writing the merge that way is what arms the guard. Do not write
`__all__ += ...` instead; that spelling evades discovery and leaves the defect
unguarded.

The defect the rule prevents is real, not theoretical.
`tests/test_package_imports.py:16-19` records it:

```python
The seventeen are the `X` / `X_b` file-size splits. `X_b` back-imports `X`, and
`X` ends with `__all__ += _X_b.__all__`. Import the FACADE first and `X_b` is
fully initialised by the time that line runs; import the LEAF first and `X`
reaches it while `X_b` is still on its first statement (D09).
```

`tests/test_package_imports.py` enumerates every `ffmwiz/**/*.py` automatically
(`_module_names()`, line 34) and imports each in a fresh subprocess, so the new
module is covered with no registration step.

**Repo conventions to match**: module docstring states why the split exists,
not just that it exists; comments name the observable symptom a line prevents;
every module ends with an explicit `__all__` list, one name per line, quoted
with single quotes in these builder modules.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Import direction + cycles | `python tests/run_suite.py -k module_reference_hygiene -j 1` | `OK` |
| Every module imports alone | `python tests/run_suite.py -k package_imports -j 2` | `OK` |
| Import topology | `python tests/run_suite.py -k import_topology -j 1` | `OK` |
| The moved builders' suites | `python tests/run_suite.py -k composite_inputs -k quick_outputs -j 2` | `OK` |
| The guards that scan this file | `python tests/run_suite.py -k speed_frame_retention -k sar_capability -j 2` | `OK` |
| Full suite | `python tests/run_suite.py -j 4` | `OK`, 0 failures |
| Import check | `python -c "import ffmwiz.wizard_build_c"` | exit 0 |

**A repository hook blocks raw `python -m unittest`. Always go through
`tests/run_suite.py`.**

Python files in this repo are UTF-8 with LF newlines (`.gitattributes` has
`*.py text eol=lf`). The move is a cut-and-paste of existing text — do not let
an editor reflow it or rewrite its line endings.

## Scope

**In scope**:
- `ffmwiz/wizard_build_c.py` (create)
- `ffmwiz/wizard_build_b.py` (delete the moved definitions, trim `__all__`, add
  the re-export block)

**Out of scope** (do NOT touch):
- `ffmwiz/wizard_build.py`, `ffmwiz/wizard_b.py`, `ffmwiz/encoding.py` and
  every other caller. They address these builders as
  `wizard_build_b.build_thumbnail_command(...)`,
  `wizard_build_b.build_composite_command(...)` and so on
  (`ffmwiz/wizard_build.py` lines 308, 343, 347, 350, 429, 534;
  `ffmwiz/wizard_b.py:231`). The facade's star-import puts every moved name
  back on `wizard_build_b` as a module attribute, so **not one of those lines
  changes**. If you find yourself editing a caller, the re-export block is
  wrong — fix that instead.
- Every test file. This is a pure move; the suite must pass unchanged.
  `tests/test_composite_inputs.py:707` monkeypatches
  `wizard_build_b.build_composite_command` and must keep working through the
  re-export.
- **The bodies of the moved functions.** Not one character. Any improvement you
  spot is a separate finding; a behaviour change hidden inside a 300-line move
  is unreviewable.
- **`gif_filter_chain`, `build_gif_palette_command`, `build_gif_write_command`
  and `build_cpu_video_filter`.** They stay in `wizard_build_b.py` for the
  measured reason above.
- **The join half (`join_extras_outcome_notes` through
  `build_join_encode_command`, lines 330-1179, 828 lines).** Measured and
  rejected — see Maintenance notes for the number and the real reason, which is
  not the one you might guess.
- `docs/architecture.md` — its layout line reads `wizard*.py` (line 42), a
  glob that already covers the new file, and nothing in it enumerates the
  `_b` overflow modules.

## Git workflow

- Branch: `advisor/010-split-the-quick-and-composite-builders`
- Two commits: the move (steps 2-4), then the `__all__` and re-export wiring if
  you prefer to separate them — or one commit for the lot. Conventional
  commits, short subject only — see `git log --oneline -5` (e.g.
  `fix: let the NVENC tests run on real hardware`). Suggested subject:
  `refactor: split the composite and quick-output builders into wizard_build_c`
- Do NOT push or open a PR.

## Steps

### Step 1: Re-measure the seam before touching anything

Do not trust the table above. Run this and confirm it agrees; the line numbers
in this plan are from `aaf0aed` and a single added function shifts them all.

```
python -c "import ast, collections
tree = ast.parse(open('ffmwiz/wizard_build_b.py', encoding='utf-8').read())
defs = {}
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        defs[node.name] = (node.lineno, node.end_lineno)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name): defs[t.id] = (node.lineno, node.end_lineno)
uses = collections.defaultdict(set)
for name, (lo, hi) in defs.items():
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and lo <= n.lineno <= hi and n.id in defs and n.id != name:
            uses[name].add(n.id)
LEAF = ['composite_active','_even','build_composite_filter_graph','build_composite_command',
        'QUICK_OUTPUT_MODES','quick_output_mode','gif_frame_rate','gif_target_width',
        'gif_scale_chain','gif_input_options','gif_unsupported_answer_notes',
        'append_stream_loop','build_thumbnail_command']
missing = [n for n in LEAF if n not in defs]
print('MISSING FROM THE FILE:', missing)
leaf = set(LEAF)
out = sorted({d for n in leaf for d in uses[n] if d not in leaf})
back = sorted({n for n in defs if n not in leaf and uses[n] & leaf})
print('LEAF -> REST (must be []):', out)
print('REST -> LEAF (fine, expect 3):', back)
print('lines to move:', sum(defs[n][1]-defs[n][0]+1 for n in leaf))
for n in LEAF: print(f'   {n:32s} {defs[n][0]:5d}-{defs[n][1]:5d}')"
```

**Expected at `aaf0aed`**:
```
MISSING FROM THE FILE: []
LEAF -> REST (must be []): []
REST -> LEAF (fine, expect 3): ['build_gif_palette_command', 'build_gif_write_command', 'gif_filter_chain']
lines to move: 297
```

If `LEAF -> REST` is not empty, **STOP**. Something now depends upward and the
split as written would produce a `NameError` at runtime that no import check
catches.

### Step 2: Create `ffmwiz/wizard_build_c.py`

Write the new file in this order:

1. **Module docstring.** Say what is in it (the compositing builders and the
   quick-output argv pieces), that it was split out of `wizard_build_b` for
   file size, and — the part a future reader needs — that
   `gif_filter_chain`/`build_gif_palette_command`/`build_gif_write_command`
   deliberately stayed behind because they route through
   `build_cpu_video_filter`, which a guard test pins to `wizard_build_b.py`.
   End with the `ext04c.py:9-10` sentence adapted: `wizard_build_b` re-exports
   this module at its end, so every existing
   `from ffmwiz.wizard_build_b import *` keeps working unchanged.
2. **The import prelude, copied verbatim from `ffmwiz/wizard_build_b.py:6-91`**
   — `from __future__ import annotations` through
   `from ffmwiz.trackmanager import *`. Copy the whole block rather than
   trimming it to what the moved code appears to need; that is exactly what
   `ext04c.py` and `wizard_flow_b.py` do, and `tests/test_package_imports.py`
   is what proves it imports. Do **not** copy any line that names
   `wizard_build_b`.
3. **The 13 definitions**, in their current file order, byte-for-byte:
   `composite_active`, `_even`, `build_composite_filter_graph`,
   `build_composite_command`, `QUICK_OUTPUT_MODES`, `quick_output_mode`,
   `gif_frame_rate`, `gif_target_width`, `gif_scale_chain`,
   `gif_input_options`, `gif_unsupported_answer_notes`, `append_stream_loop`,
   `build_thumbnail_command`. Bring the section banner at
   `wizard_build_b.py:1543-1548` with them, edited so its wording still makes
   sense in the new file.
4. **`__all__`**, listing the 12 public names (everything above except the
   private `_even`), in the same single-quoted one-per-line style as
   `wizard_build_b.py:1738-1771`.
5. **The `wizard_base` tail**, copied from `wizard_build_b.py:1774-1777` with
   its comment: `build_composite_command` calls
   `wizard_base.append_video_encode_options` (line 1523 today).

**Verify**:
```
python -c "import ffmwiz.wizard_build_c as c; print(len(c.__all__), sorted(c.__all__)[:3])"
```
→ `12` and the first three names alphabetically.

```
grep -n "wizard_build_b" ffmwiz/wizard_build_c.py
```
→ **no output outside the docstring**. If the docstring mentions
`wizard_build_b` (it should), confirm by eye that every hit is inside the
triple-quoted docstring and none is an import.

### Step 3: Delete the moved definitions from `ffmwiz/wizard_build_b.py`

Delete exactly the 13 definitions and the section banner you moved. Leave
`gif_filter_chain` (1610-1633), `build_gif_palette_command` (1694-1700) and
`build_gif_write_command` (1703-1719) exactly where they are, along with
everything at line 1352 and below it in the inventory table.

Then remove the 12 moved names from `wizard_build_b.__all__` (lines 1738-1771):
`QUICK_OUTPUT_MODES`, `append_stream_loop`, `build_thumbnail_command`,
`gif_frame_rate`, `gif_input_options`, `gif_scale_chain`,
`gif_unsupported_answer_notes`, `gif_target_width`, `quick_output_mode`,
`composite_active`, `build_composite_filter_graph`, `build_composite_command`.
They come back through the merge in step 4; leaving them in both lists produces
duplicates in `wizard_build.__all__`.

The three entries that must **stay** in that list are
`'build_gif_palette_command'` (line 1741), `'build_gif_write_command'` (1742)
and `'gif_filter_chain'` (1745), along with every name below them.

At this point the module is temporarily broken — `gif_filter_chain` calls
`gif_scale_chain`, which now lives elsewhere. Step 4 fixes it. Do not run the
suite yet.

### Step 4: Re-export the leaf from `wizard_build_b.py`

Append to the very end of `ffmwiz/wizard_build_b.py`, after the existing
`from ffmwiz import wizard_base` line:

```python
# The compositing and quick-output builders live in their own module now;
# re-exported here so every existing `from ffmwiz.wizard_build_b import *`,
# and every `wizard_build_b.<name>` call site, is unchanged.
from ffmwiz import wizard_build_c as _wizard_build_c  # noqa: E402
from ffmwiz.wizard_build_c import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_wizard_build_c.__all__)
```

Three details:

- Use `__all__ = list(__all__) + list(_wizard_build_c.__all__)` **exactly**.
  `tests/test_module_reference_hygiene.py:117` discovers re-export pairs by
  searching for that literal string; `__all__ += ...` would not be discovered
  and the direction guard would never run on this pair.
- Never guard it with `getattr(_wizard_build_c, "__all__", [])`.
  `docs/architecture.md:170-173` records what that cost last time: the error
  disappears, the merge is silently skipped, and the facade's `__all__` comes
  out short.
- `wizard_build.py` binds its leaf twice (plain plus `_` alias) because its own
  call sites say `wizard_build_b.<name>`. Nothing inside `wizard_build_b.py`
  will address `wizard_build_c` as a module object — the surviving GIF
  functions call `gif_scale_chain` and `gif_input_options` as bare names, which
  resolve from module globals at call time — so the plain binding is not needed
  here. Do not add it.

**Verify**, in this order:
```
python -c "import ffmwiz.wizard_build_c; print('leaf first: ok')"
python -c "import ffmwiz.wizard_build_b; print('facade first: ok')"
python -c "import ffmwiz.wizard_build_b as b; print(len(b.__all__)); print(all(hasattr(b, n) for n in b.__all__))"
```
→ both `ok` lines (the first is the import order that broke 17 modules before),
then the merged `__all__` length and `True`.

```
python -c "import sys; sys.path.insert(0,'.'); import ffmwiz.wizard_build_b as b
for n in ('build_composite_command','build_thumbnail_command','quick_output_mode','append_stream_loop','gif_input_options','gif_scale_chain','gif_filter_chain','build_gif_palette_command','build_gif_write_command'):
    print(n, getattr(b, n).__module__)"
```
→ every name resolves; the first six report `ffmwiz.wizard_build_c`, the last
three `ffmwiz.wizard_build_b`.

### Step 5: Run the guards, then everything

In this order, stopping at the first failure:

```
python tests/run_suite.py -k package_imports -j 2
python tests/run_suite.py -k module_reference_hygiene -j 1
python tests/run_suite.py -k import_topology -j 1
python tests/run_suite.py -k speed_frame_retention -k sar_capability -j 2
python tests/run_suite.py -k composite_inputs -k quick_outputs -j 2
python tests/run_suite.py -j 4
```

All `OK`. The full run must have the same test count as at `aaf0aed` (1983 on
a machine with ffmpeg, numpy and PySide6) — this plan adds no tests and removes
none.

### Step 6: Confirm the file actually shrank

```
wc -l ffmwiz/wizard_build_b.py ffmwiz/wizard_build_c.py
```
→ roughly `1460` and `440` (±25 each; the prelude is ~90 lines and the
docstring and `__all__` add the rest). If `wizard_build_b.py` is still over
1600, definitions were copied rather than moved — check for duplicates:

```
python -c "import ast, collections
t = ast.parse(open('ffmwiz/wizard_build_b.py', encoding='utf-8').read())
names = [n.name for n in t.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
moved = {'composite_active','_even','build_composite_filter_graph','build_composite_command',
         'quick_output_mode','gif_frame_rate','gif_target_width','gif_scale_chain',
         'gif_input_options','gif_unsupported_answer_notes','append_stream_loop','build_thumbnail_command'}
print('still defined in wizard_build_b:', sorted(set(names) & moved))"
```
→ `still defined in wizard_build_b: []`

## Test plan

**No new tests, and no test file may be modified.** That is the point: this is
a move, and the existing suite is the proof it changed nothing. Four suites
carry the weight, and each must pass unchanged:

- `tests/test_package_imports.py` — imports every `ffmwiz/**/*.py` in a fresh
  subprocess, class `EveryModuleImportsInAFreshProcess`. Picks the new module up
  automatically (`_module_names()`, line 34). This is what catches a leaf that
  imports its facade.
- `tests/test_module_reference_hygiene.py` — three classes, all relevant:
  - `NoModuleNamesAModuleItDidNotImport` (line 71) — no module may name another
    module it did not import.
  - `TheCyclesStayRemoved` (line 101) —
    `test_no_leaf_imports_the_facade_that_reexports_it` (line 140) is the
    direction rule for the new pair, and `test_there_are_pairs_to_check`
    (line 135) proves the pair was discovered at all.
  - `ReExportedNamesDoNotDependOnImportOrder` (line 166) — its `FACADES` list
    (line 184) does not include `ffmwiz.wizard_build_b`, so it does not cover
    the new pair. Do not add it there; that is a separate change with its own
    subprocess cost.
- `tests/test_composite_inputs.py` — direct tests of the moved composite
  builders, including the monkeypatch of
  `wizard_build_b.build_composite_command` at lines 707 and 720, whose caller is
  `ffmwiz/wizard_b.py:231`. The patch targets the facade attribute and the
  caller reads the facade attribute, so it keeps working through the
  re-export — but it is the single most likely thing to break, so run this
  suite on its own before the full run.
- `tests/test_quick_outputs.py` — direct tests of the moved quick-output
  builders.

**Verify**: `python tests/run_suite.py -j 4` → `OK`, 0 failures, same test
count as before the change.

## Done criteria

ALL must hold:

- [ ] `python tests/run_suite.py -j 4` → `OK`, 0 failures, **same** test count as at `aaf0aed`
- [ ] `python tests/run_suite.py -k package_imports -j 2` → `OK`
- [ ] `python tests/run_suite.py -k module_reference_hygiene -j 1` → `OK`
- [ ] `python -c "import ffmwiz.wizard_build_c"` exits 0 (leaf imported FIRST, in a fresh process)
- [ ] `grep -c "wizard_build_b" ffmwiz/wizard_build_c.py` → hits only inside the module docstring; no import line
- [ ] `grep -c "__all__ = list(__all__)" ffmwiz/wizard_build_b.py` → 1
- [ ] The duplicate check in step 6 prints `[]`
- [ ] `wc -l ffmwiz/wizard_build_b.py` → under 1500
- [ ] `git status --short` shows exactly two files: `ffmwiz/wizard_build_b.py` (modified) and `ffmwiz/wizard_build_c.py` (added)
- [ ] `git diff --stat aaf0aed..HEAD -- tests/ ffmwiz/wizard_build.py ffmwiz/wizard_b.py` → empty

## STOP conditions

Stop and report (do not improvise) if:

- Step 1 prints a non-empty `LEAF -> REST`. The seam has closed since
  `aaf0aed`; report which name and stop.
- Step 1 prints anything in `MISSING FROM THE FILE`. The file has drifted.
- `test_no_leaf_imports_the_facade_that_reexports_it` fails. Do **not** fix it
  by changing the merge to `__all__ += ...` or by moving the import inside a
  function — both hide the guard rather than satisfy it. Report which name the
  leaf needs from the facade.
- `test_there_are_pairs_to_check` (line 135) fails or the pair count does not
  grow by one. That means the merge line was written in a spelling the
  discovery does not recognise, so the direction guard is not actually running
  on this pair.
- `tests/test_speed_frame_retention.py` fails. That means something containing
  `build_video_speed_filter(` ended up in the new module; only lines 1040 and
  1234 have it and neither is in the move list.
- Any test outside the four named in the test plan changes behaviour. A pure
  move cannot do that; find out what else came along.
- You conclude a caller must be edited. It must not — see Scope.

## Maintenance notes

- **The join half was measured and rejected, and the usual reason for rejecting
  it is wrong.** Its 12 functions (`join_extras_outcome_notes` through
  `build_join_encode_command`, lines 330-1179, 828 source lines) reference
  **zero** names defined outside themselves, and only one name from them is
  used elsewhere (`encode_timeline_map`, called at line 1237 inside
  `build_cpu_video_filter`). By reference count it is a *cleaner* seam than the
  one this plan uses. The real blocker is the guard quoted above:
  `build_join_encode_command` is the only thing in this file that emits
  `VIDEO_SPEED_OUTPUT_TIMING_ARGS` (line 1148), and `build_cpu_video_filter`
  (line 1234) is the only other thing that emits `build_video_speed_filter(`.
  Move the join half out and `wizard_build_b.py` keeps the filter and loses the
  option, so `test_the_option_lives_beside_the_filter_that_needs_it` fails on
  `wizard_build_b.py` itself. The two functions are pinned to one file by that
  test, and splitting either half away from the other needs that pairing sorted
  out first. Anyone who wants the join split should start there, not with a cut.
- The three GIF functions left behind are the odd shape in the result:
  `gif_scale_chain` moves and `gif_filter_chain` does not, so the GIF family is
  split across two files. That is the honest consequence of one dependency, and
  it is recorded in the new module's docstring so the next reader does not
  "tidy" it by dragging `build_cpu_video_filter` across.
- If `build_cpu_video_filter` ever grows its own
  `VIDEO_SPEED_OUTPUT_TIMING_ARGS` emit — or the guard in
  `tests/test_speed_frame_retention.py` learns to pair the filter with the
  option across a re-export instead of within a file — the remaining three GIF
  functions can follow the rest into `wizard_build_c.py` and the seam becomes
  the full 383 lines.
- `tests/test_import_topology.py:23-31` keeps a hand-maintained
  `OVERFLOW_MODULES` list of leaves worth importing standalone. It does not
  include `wizard_build_b` today, so `wizard_build_c` was not added either;
  `tests/test_package_imports.py` covers the same ground automatically. If that
  list is ever made authoritative, add both.
- A reviewer should check exactly two things: that `git diff` shows the moved
  function bodies as pure deletions on one side and pure additions on the other
  (no edits smuggled in), and that no file outside the two in scope changed.
