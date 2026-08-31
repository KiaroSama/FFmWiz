# Plan 007: Guard the PySide6 pin, make the README's skip claim true, and catch the next unused import

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat aaf0aed..HEAD -- requirements.txt pyproject.toml tests/test_packaging.py tests/README.md tests/test_speed_frame_retention.py ffmwiz/wizard_look.py tests/test_module_reference_hygiene.py .github/workflows/python-smoke.yml`
> If any of those changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding; on a mismatch,
> treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt + docs
- **Planned at**: commit `aaf0aed`, 2026-08-29

## Why this matters

Three independent records in this repo currently say something that is not
true, and each one costs a future developer real time:

1. **The PySide6 version is pinned twice and nothing compares the two pins.**
   CI installs Qt from `requirements.txt`; a user installing the wheel gets the
   pin in `pyproject.toml`. Edit one and CI keeps testing a Qt that nobody
   installs, silently.
2. **`tests/README.md` tells a developer to expect "~20 fewer tests" without
   ffmpeg.** The real figure is over 400, and the run is not green either — two
   classes error instead of skipping, because that file defines a
   `requires_ffmpeg` decorator and never applies it. A number that wrong is
   worse than no number: it makes a shrunken local run look normal.
3. **`ffmwiz/wizard_look.py` imports `re` and never uses it.** Trivial alone.
   The value is the guard that catches the next one — the repo has an
   AST-walking hygiene test already, and it checks the opposite direction only.

All three are cheap. Do all three; one commit each.

## Current state

### (a) The two pins

`requirements.txt` (3 lines total):

```
# Runtime Python dependency for the dedicated Qt Cut/Crop GUIs.
# FFmWiz's core CLI uses only the Python standard library.
PySide6==6.11.2
```

`pyproject.toml:17-19`:

```toml
dependencies = [
    "PySide6==6.11.2",
]
```

CI installs Qt from `requirements.txt` only —
`.github/workflows/python-smoke.yml:149-150`, in the `gui-import` job:

```yaml
      - name: Install runtime dependencies
        run: python -m pip install --disable-pip-version-check -r requirements.txt
```

Nothing reads `pyproject.toml`'s dependency list at test time.
`tests/test_packaging.py` already parses `pyproject.toml` and asserts on it —
`tests/test_packaging.py:43-49`:

```python
@unittest.skipIf(tomllib is None, "tomllib needs Python 3.11+")
class PyprojectDeclaration(unittest.TestCase):
    """Static guard: the declarations whose absence emptied the wheel."""

    @classmethod
    def setUpClass(cls):
        cls.config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
```

and `PROJECT_ROOT` / `PYPROJECT` are defined at `tests/test_packaging.py:25-26`:

```python
PROJECT_ROOT = Path(FFmWiz.__file__).resolve().parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
```

Note `PROJECT_ROOT` is the **repo root** (`FFmWiz.py` is the top-level
launcher), so `PROJECT_ROOT / "requirements.txt"` is the right path.

### (b) The stale number

`tests/README.md:55-61` (the last two lines are the problem):

```
Some tests self-skip when a capability is missing: real ffmpeg/ffprobe on `PATH`,
`numpy` (waveform math), an NVIDIA GPU (NVENC), symlink privilege. CI installs
ffmpeg and numpy and passes `--require ffmpeg --require numpy` (plus `--require
pyside6` in the GUI job), so it **fails** if a test skipped for a reason that job
should have provided; GPU and symlink skips stay allowed.
Locally, a run without ffmpeg is green with ~20 fewer tests — check the skip count
before trusting a green local run.
```

Measured on this tree at `aaf0aed`, on Windows with Python 3.13, by running the
whole suite twice — once normally, once with every `PATH` directory containing
`ffmpeg.exe` removed:

```
with ffmpeg:     Ran 1983 tests ... OK                          (0 skips)
without ffmpeg:  Ran 1911 tests ... FAILED (failures=0, errors=2)
                 411 "skipped:" lines, 408 of them naming ffmpeg/ffprobe
```

A second, cheaper method agrees: loading (not running) every `tests/test*.py`
with `shutil.which` lying about ffmpeg/ffprobe marks **401 tests in 58 modules**
as skipped for ffmpeg. The 7-test gap between 401 and 408 is runtime
`self.skipTest` calls that only a real run reaches. Either number is more than
twenty times the "~20" the README hands the reader.

The two errors are `tests/test_speed_frame_retention.py`. It defines the
decorator and never uses it — `tests/test_speed_frame_retention.py:42-44`:

```python
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")
```

`requires_ffmpeg` appears on that one line and nowhere else in the file. The
class that builds the fixtures with real ffmpeg is undecorated —
`tests/test_speed_frame_retention.py:59`:

```python
class SpeedFixtures(NoLeakedArtifacts, unittest.TestCase):
```

`SpeedKeepsEveryFrame` (line 169) and `AJoinedSpeedChangeStatesItToo` (line 239)
both inherit from it, and both raise `FileNotFoundError` out of `setUpClass`
when ffmpeg is absent. `EveryBuilderThatRetimesAlsoStatesItsTiming` (line 290)
is a pure source scan, does **not** inherit `SpeedFixtures`, and must keep
running without ffmpeg.

CI's real gate is the flag, not a count —
`.github/workflows/python-smoke.yml:131`:

```yaml
        run: python tests/run_suite.py --require ffmpeg --require numpy
```

and `.github/workflows/python-smoke.yml:184`:

```yaml
        run: python tests/run_suite.py --require pyside6 --require numpy --require ffmpeg -j 2 -k gui_editors -k qml_waveform -k theme_tokens -k join_audio_availability -k command_generation_2
```

### (c) The unused import

`ffmwiz/wizard_look.py:13-25` (the file is 54 lines total):

```python
from __future__ import annotations

import re
from typing import Any

from ffmwiz import appio
from ffmwiz.appio import paint
from ffmwiz.core.colors import Color
from ffmwiz.core.constants import ROTATE_FILTERS
from ffmwiz.support.L01_filters import (LOOK_ANSWER_KEYS, describe_look,
                                        parse_look_tokens)
from ffmwiz.core.exceptions import Back
from ffmwiz.support.L00_misc_b import is_back_value
```

`grep -nw "re" ffmwiz/wizard_look.py` returns exactly one line — line 15, the
import itself. (`grep -n "re\." ffmwiz/wizard_look.py` is NOT a valid check
here: it matches `ffmwiz.core.colors`, `ffmwiz.core.constants` and
`ffmwiz.core.exceptions`, and looks like three uses.)

The guard belongs in `tests/test_module_reference_hygiene.py` (224 lines). It
already enumerates the package —
`tests/test_module_reference_hygiene.py:27-34`:

```python
def _package_modules() -> dict[str, Path]:
    """basename -> path, for every module in the package."""
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "__init__.py":
            continue
        found[path.stem] = path
    return found
```

and it already has the mirror-image helper to model the new one on —
`tests/test_module_reference_hygiene.py:37-49`:

```python
def _bound_at_module_level(tree: ast.Module) -> set[str]:
    """Every name this file could resolve, from imports and definitions.

    Deliberately generous: a name bound anywhere -- including inside a function,
    a `with`, or an `except` -- counts. The point is to catch a reference to a
    module NOTHING binds, not to police scope.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bound |= {a.asname or a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            bound |= {a.asname or a.name for a in node.names if a.name != "*"}
```

**Repo conventions to match**: every test class has a docstring naming the
defect it pins; several carry a "Guard the guard" test that fails if the sweep
finds nothing (see lines 73-78 and 135-138); comments state the observable
symptom a line prevents, not what the line does.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Packaging suite | `python tests/run_suite.py -k packaging -j 2` | `OK` |
| Hygiene suite | `python tests/run_suite.py -k module_reference_hygiene -j 1` | `OK` |
| Speed suite | `python tests/run_suite.py -k speed_frame_retention -j 1` | `OK` |
| Look suite | `python tests/run_suite.py -k look_filters -j 2` | `OK` |
| Full suite | `python tests/run_suite.py -j 4` | `OK`, 0 failures |
| Import check | `python -c "import ffmwiz.wizard_look"` | exit 0 |

**A repository hook blocks raw `python -m unittest`. Always go through
`tests/run_suite.py`.**

Python and Markdown files in this repo are UTF-8 with LF newlines
(`.gitattributes` has `*.py text eol=lf` and `*.md text eol=lf`). Do not let an
editor rewrite line endings on the files you touch.

## Scope

**In scope** (the only files you may modify):
- `tests/test_packaging.py`
- `tests/README.md`
- `tests/test_speed_frame_retention.py`
- `ffmwiz/wizard_look.py`
- `tests/test_module_reference_hygiene.py`

**Out of scope** (do NOT touch, even though they look related):
- `requirements.txt` and `pyproject.toml` — the pins already agree. This plan
  adds the assert that keeps them agreeing; it does not change a version.
- `.github/workflows/python-smoke.yml` — the `--require ffmpeg` gate is already
  correct and is what step 3 points the README at.
- `ffmwiz/gui/*.py` — 13 more imports there look unused to a static scan and
  are **not** safely removable. See step 5 for why; the new check must skip them.
- Adding a `ruff`, `flake8` or `mypy` configuration. Deliberately rejected —
  see Maintenance notes.

## Git workflow

- Branch: `advisor/007-hygiene-pin-guard-and-stale-numbers`
- **Three commits**, one per item, in the order below. Conventional commits,
  short subject only — see `git log --oneline -5` (e.g.
  `fix: let the NVENC tests run on real hardware`). Suggested subjects:
  - `test: assert the two PySide6 pins agree`
  - `test: make a green local run mean what the README says`
  - `test: fail on an unused plain import`
- Do NOT push or open a PR.

## Steps

### Step 1: Assert the two PySide6 pins agree

Add one test to the existing `PyprojectDeclaration` class in
`tests/test_packaging.py`. Do not create a new file or a new class.

The test must:

- read `PROJECT_ROOT / "requirements.txt"` with `encoding="utf-8"`;
- take the requirement lines (skip blanks and lines starting with `#`);
- find the one naming `PySide6` (case-insensitively) and compare it, stripped,
  against the matching entry in `self.config["project"]["dependencies"]`;
- fail with a message that says which files to edit and names the consequence —
  CI installs `requirements.txt`, so a drift means CI tests a different Qt than
  `pip install .` gives a user, and nothing reports it.

Name it `test_the_pyside6_pin_is_the_same_in_both_files`, with a docstring in
the style of the class's existing ones.

The class carries `@unittest.skipIf(tomllib is None, ...)`, so this guard runs
on Python 3.11+ only. That is fine and is not a reason to write a second
parser: CI's `tests` job runs a 3.13 leg.

**Verify**:
```
python tests/run_suite.py -k packaging -j 2
```
→ `OK`, with one more test than before.

Then prove the guard actually bites. Temporarily change the version in
`requirements.txt` (e.g. to `6.11.3`), re-run the same command, confirm it
**fails** naming both files, then restore the original `6.11.2` and re-run to
`OK`. `git diff -- requirements.txt` must be empty afterwards.

Commit.

### Step 2: Apply the decorator the speed suite already defines

In `tests/test_speed_frame_retention.py`, decorate the fixture base class at
line 59 with the `requires_ffmpeg` alias defined at line 44:

```python
@requires_ffmpeg
class SpeedFixtures(NoLeakedArtifacts, unittest.TestCase):
```

Do **not** decorate `EveryBuilderThatRetimesAlsoStatesItsTiming` (line 290) —
it reads source files and must keep running on a machine with no ffmpeg.

**Verify** — this is the before/after gate for the whole item. Run the module
with every `PATH` directory that holds an ffmpeg binary removed:

```
python -c "import os,pathlib,subprocess,sys
dirs=[d for d in os.environ['PATH'].split(os.pathsep) if d]
def has(d):
    p=pathlib.Path(d)
    return (p/'ffmpeg.exe').exists() or (p/'ffmpeg').exists()
env=dict(os.environ, PATH=os.pathsep.join(d for d in dirs if not has(d)))
sys.exit(subprocess.run([sys.executable,'tests/run_suite.py','-k','speed_frame_retention','-j','1'],env=env).returncode)"
```

→ **before** the decorator: `FAILED (failures=0, errors=2)`.
→ **after** the decorator: `OK (skipped=N)` with N > 0 and every skip line
reading `ffmpeg/ffprobe not on PATH`.

Then confirm nothing regressed with ffmpeg present:
`python tests/run_suite.py -k speed_frame_retention -j 1` → `OK` with **no**
skip line. A skip here would mean the decorator's condition is wrong.

### Step 3: Replace the stale sentence with the flag CI actually uses

In `tests/README.md`, replace the final two lines (currently lines 60-61):

```
Locally, a run without ffmpeg is green with ~20 fewer tests — check the skip count
before trusting a green local run.
```

with a sentence that (i) does not restate a count that will rot again, and
(ii) hands the reader the flag CI uses. Required content, in the file's own
voice — plain prose, no bullet list, wrapped to the same width as the rest of
the file:

- Without ffmpeg on `PATH` a local run is a *different* run: hundreds of tests
  self-skip, so a green result proves much less than it looks like it does.
- The check is `python tests/run_suite.py --require ffmpeg --require numpy` —
  the same gate `.github/workflows/python-smoke.yml` uses — which fails the run
  when a suite skipped for a capability the run should have provided.
- Do not name a specific number of tests anywhere in the replacement.

**Verify**:
```
grep -n "~20 fewer" tests/README.md
```
→ no output (exit 1).

```
grep -c "require ffmpeg" tests/README.md
```
→ at least 2 (the pre-existing line 25 and the new sentence).

Then confirm the recommended command behaves as documented on this machine
(which has ffmpeg): `python tests/run_suite.py --require ffmpeg -j 4` → `OK`.

Commit steps 2 and 3 together — they are one concern, "what a green local run
means".

### Step 4: Delete the unused import

Delete line 15 of `ffmwiz/wizard_look.py` (`import re`). Delete only that line;
leave the rest of the import block exactly as it is.

**Verify**:
```
python -c "import ffmwiz.wizard_look; print('ok')"
```
→ prints `ok`.

```
python tests/run_suite.py -k look_filters -j 2
```
→ `OK`.

Do not commit yet — step 5 goes in the same commit.

### Step 5: Add the guard that catches the next one

Add a new `unittest.TestCase` class to `tests/test_module_reference_hygiene.py`
— for example `NoModuleImportsSomethingItNeverUses` — with a docstring naming
the concrete defect (`ffmwiz/wizard_look.py` imported `re` and never used it)
and why a static check is the right tool for it.

Reuse `_package_modules()` (line 27) for enumeration. Write a small
`_loaded_anywhere(tree) -> set[str]` helper next to `_bound_at_module_level`
(line 37), mirroring its `ast.walk` idiom in the opposite direction: collect
`ast.Name` nodes whose `ctx` is `ast.Load`, plus `node.value.id` for every
`ast.Attribute` whose `.value` is an `ast.Name`.

The check, for every module `_package_modules()` returns:

1. **Skip any file whose text contains `import *`.** With a star import in
   play a name can be re-exported or shadowed in ways the AST walk cannot see,
   and the check would report false positives. 83 of the package's modules are
   in this class, so this skip is the difference between a useful check and an
   unusable one. Say that in a comment.
2. **Skip any file under `ffmwiz/gui/`** (`"gui" in path.parts`).
   `ffmwiz/gui/classic/ffmwiz_gui.py:59-81` assembles ONE namespace out of every GUI
   module and `setattr`s it onto all of them, and its `_exported_names` helper
   falls back to `vars(module)` when a module declares no `__all__` — which
   none of the 25 GUI modules does. An import that looks unused in
   `gui_geometry.py` may therefore be the only binding another GUI file
   resolves through. Say that in a comment; it is the reason, and a future
   reader will otherwise "fix" the skip.
3. Skip any `ast.Import` whose source line contains `# noqa`.
4. Consider only `ast.Import` (a plain `import X` / `import X as Y`), never
   `ast.ImportFrom`. `from x import name` is routinely a deliberate re-export
   in this codebase.
5. For each alias, the bound name is `alias.asname or alias.name.split(".")[0]`.
   Report it when that name is not in `_loaded_anywhere(tree)` **and** does not
   appear in any string constant in the file (`ast.Constant` with a `str`
   value) — string annotations and `__all__` entries are real uses.
6. Collect offenders as `path:lineno: import X -- never used` and
   `assertEqual([], offenders, ...)`, exactly the shape
   `test_every_qualified_module_reference_is_imported` (line 80, assertion at
   line 98) uses.

Add a second "Guard the guard" test in the same class, matching the convention
at lines 73-78 and 135-138: assert the sweep actually examined a non-trivial
number of modules, so a scan that silently enumerated nothing fails instead of
passing.

**Verify**:
```
python tests/run_suite.py -k module_reference_hygiene -j 1
```
→ `OK`, with 2 more tests than before.

Then prove the guard bites: temporarily re-add `import re` as line 15 of
`ffmwiz/wizard_look.py`, re-run the same command, confirm it **fails** naming
`ffmwiz/wizard_look.py:15`, then remove it again and re-run to `OK`.

Commit steps 4 and 5 together.

### Step 6: Full suite

**Verify**: `python tests/run_suite.py -j 4` → `OK`, 0 failures.

## Test plan

No new test file. Three additions to existing files:

1. `tests/test_packaging.py`, class `PyprojectDeclaration` —
   `test_the_pyside6_pin_is_the_same_in_both_files`. Model it on
   `test_the_thin_launcher_is_still_a_top_level_module` (line 69): one idea,
   one assertion, a message that says what to do.
2. `tests/test_module_reference_hygiene.py`, one new class holding the
   unused-import check plus its guard-the-guard companion (2 tests).
3. `tests/test_speed_frame_retention.py` — no new test; one decorator applied
   so the two existing encode classes skip instead of erroring.

Each "prove the guard bites" check in steps 1 and 5 is a temporary edit that
must be reverted before committing.

**Verify**: `python tests/run_suite.py -j 4` → `OK`, with 3 more tests than
before this plan (2 hygiene + 1 packaging).

## Done criteria

ALL must hold:

- [ ] `python tests/run_suite.py -j 4` → `OK`, 0 failures, 3 more tests than at `aaf0aed`
- [ ] `grep -c "6.11.2" requirements.txt pyproject.toml` → 1 each, unchanged
- [ ] Changing the pin in `requirements.txt` alone makes `-k packaging` fail (verified, then reverted)
- [ ] `grep -n "~20 fewer" tests/README.md` → no output
- [ ] `grep -nw "re" ffmwiz/wizard_look.py` → no output
- [ ] The no-ffmpeg run of `-k speed_frame_retention` in step 2 ends `OK (skipped=N)`, not `FAILED`
- [ ] Re-adding `import re` to `ffmwiz/wizard_look.py` makes `-k module_reference_hygiene` fail (verified, then reverted)
- [ ] `git status --short` shows only the five in-scope files
- [ ] `git log --oneline -3` shows three commits, one per item

## STOP conditions

Stop and report (do not improvise) if:

- The excerpts above do not match the live code (drift since `aaf0aed`).
- The two PySide6 pins already **disagree** when you read them. That is a live
  defect, not a guard to add; report both values and stop rather than picking
  one.
- The unused-import check reports any offender other than
  `ffmwiz/wizard_look.py:15` once the two skips in step 5 are in place. At
  `aaf0aed` it reported exactly one. More than one means either the skips are
  wrong or the tree has drifted — list them and stop; do not delete imports the
  plan did not name.
- Applying `@requires_ffmpeg` to `SpeedFixtures` causes
  `EveryBuilderThatRetimesAlsoStatesItsTiming` to skip. That class must keep
  running; if it skips, the decorator went on the wrong class.
- `python tests/run_suite.py --require ffmpeg -j 4` fails on your machine
  because ffmpeg is genuinely absent. Install ffmpeg or report the block; do
  not weaken the README sentence to match one machine.

## Maintenance notes

- **A full `ruff` or `mypy` configuration was considered and rejected. Do not
  re-litigate it.** There is no linter config anywhere in the repo today
  (`pyproject.toml` has no `[tool.ruff]`, `[tool.flake8]` or `[tool.mypy]`
  table, and there is no `setup.cfg` or `tox.ini`), yet the tree carries 2938
  `# noqa` comments — 2667 of them the `# noqa: F401,F403` pair on star-import
  lines — written for a tool that was never wired up. Turning one on would not
  pay:
  - 83 package modules outside `ffmwiz/gui/` contain `from x import *`. In any
    module with a star import, pyflakes downgrades undefined-name detection
    from F821 to F405 ("may be undefined, or defined from star imports"). The
    one check that would genuinely catch bugs here is neutered by the very
    pattern the architecture is built on.
  - mypy over ~120 stdlib-only modules that pass `dict[str, Any]` answer bags
    everywhere would produce thousands of findings and change nothing about
    correctness.
  - The 1983-test suite — 401 of them gated on real ffmpeg — is the stronger
    signal. This plan takes the narrow version of the win instead: one assert
    inside the AST-walking test that already exists.
- The unused-import check's two skips are load-bearing, not laziness. If
  someone later gives each GUI module an `__all__`,
  `ffmwiz/gui/classic/ffmwiz_gui.py:67-71` stops sharing their private imports and the
  `gui` skip can be dropped — at which point 13 unused imports in
  `gui_common.py` and `gui_geometry.py` become deletable. Until then, deleting
  them can break a sibling GUI file at runtime with no test to catch it.
- A reviewer should scrutinise the string-constant exemption in step 5 hardest.
  Too generous and the check never fires; drop it and every `if TYPE_CHECKING`
  string annotation becomes a false positive.
- The number in step 3 was deliberately removed rather than corrected. Anyone
  who wants it back can re-derive it in one command:
  ```
  python -c "import shutil, sys, unittest, pathlib
  sys.path.insert(0, '.'); sys.path.insert(0, 'tests')
  real = shutil.which
  shutil.which = lambda c, *a, **k: None if str(c).lower().removesuffix('.exe') in ('ffmpeg','ffprobe') else real(c,*a,**k)
  def walk(s):
      for i in s:
          yield from walk(i) if isinstance(i, unittest.TestSuite) else (i,)
  n = 0; mods = set()
  for p in sorted(pathlib.Path('tests').glob('test*.py')):
      for t in walk(unittest.defaultTestLoader.loadTestsFromName(p.stem)):
          m = getattr(t, t._testMethodName)
          why = ((getattr(type(t), '__unittest_skip_why__', '') if getattr(type(t), '__unittest_skip__', 0) else '')
                 + ' ' + (getattr(m, '__unittest_skip_why__', '') if getattr(m, '__unittest_skip__', 0) else ''))
          if 'ffmpeg' in why.lower() or 'ffprobe' in why.lower():
              n += 1; mods.add(p.stem)
  print('ffmpeg-gated tests:', n, 'in', len(mods), 'modules')"
  ```
  At `aaf0aed` this prints `ffmpeg-gated tests: 401 in 58 modules`. It belongs
  in a commit message or a report, not in a file that has to stay true.
