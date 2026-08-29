# Plan 006: Declare the picture filters, fades, volume and raw options in the stage-ownership schema so a staged reverse applies each of them exactly once

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat aaf0aed..HEAD -- ffmwiz/reverse_stages.py ffmwiz/reverse_pipeline.py tests/test_reverse_stage_ownership.py tests/test_stage_geometry_ownership.py`
> If any of those changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding; on a mismatch,
> treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `aaf0aed`, 2026-08-29

## Why this matters

A reverse job that also joins or splits does not run as one FFmpeg command. It
runs as a chain of stages — forward join → segmented reverse → final split —
each of which re-encodes the file the previous one wrote. Every stage builds
its command from the same `answers` dictionary, so any edit still present in a
later stage's copy is applied to an intermediate that already carries it.

`STAGE_TRANSFORMATIONS` is the schema that prevents this: each transformation
declares the answer keys it owns, and `stage_answers()` deletes the keys a
stage does NOT own. The schema is not documentation — it is the mechanism. A
key missing from it is applied by every stage that happens to read it. The
file's own comments record what that cost the last two times: an independent
2x audio speed applied three times (4.100 s of video against 1.111 s of
audio), and a 160x120 clip asked for a 10 px crop on each side coming out
120x120 instead of 140x120, with `crop=` present in `joined_forward.mkv`, in
every reverse segment AND in the final Split graph.

**None of the five features shipped in `d0e8f69` and `0fe7e98` are in the
schema.** Rotation, flips, greyscale, denoise, sharpen, blur, colour
adjustment, fades, audio volume and the raw-ffmpeg escape hatch are all
missing, so a Join + Reverse + Split job applies every one of them three
times. A 90-degree rotation applied twice is 180 degrees. A 2x gain applied
three times is 8x. A `-b:v 500k` in the raw options re-imposes on the scratch
intermediates exactly the low bitrate `intermediate_profile` exists to strip.

The same gap breaks the boomerang quick output, which reverses its own forward
half through the same `stage_answers()` call: the second half of every
boomerang is currently rotated, denoised, faded and gain-boosted a second time
relative to the first half it is concatenated with.

## Current state

Files, and what each one does here:

- `ffmwiz/reverse_stages.py` — owns the schema, the requested-predicates, the
  neutral values and `stage_answers()`/`validate_stage_plan()`. 653 lines.
- `ffmwiz/reverse_pipeline.py` — the planner and the executor. Each assigns the
  transformations to stages, in two places that must stay identical.
- `ffmwiz/encoding.py` — two more `stage_answers()` callers (line 327, the
  audio-reverse-across-a-join forward stage; line 449, the boomerang backward
  half). **Both read the schema constants and need no edit** — they inherit the
  fix.
- `tests/test_reverse_stage_ownership.py` — the schema-level suite.
- `tests/test_stage_geometry_ownership.py` — the real-encode suite that proved
  the crop leak. It already builds the fixture and drives the real executor.

**The schema**, `ffmwiz/reverse_stages.py:211-241` (tail shown; the entries
above it are `cuts`, `audio_cuts`, `video_speed`, `audio_speed`,
`video_reverse`, `audio_reverse`, `loudnorm`, `split`):

```python
    "crop": ("crop_enabled", "crop_top", "crop_left", "crop_right",
             "crop_bottom", "crop_box_dimensions", "cropped_aspect_ratio"),
    "fps": ("fps",),
    "resize": ("resolution", "final_resolution"),
}

# Geometry travels together: cropping in one stage and resizing in another
# would make the second stage scale a frame the first already changed.
GEOMETRY_TRANSFORMATIONS: tuple[str, ...] = ("crop", "fps", "resize")
```

**The requested-predicates**, `ffmwiz/reverse_stages.py:252-270` (tail):

```python
    "crop": lambda a: (bool(a.get("crop_enabled"))
                       and any(int(a.get(f"crop_{edge}", 0) or 0)
                               for edge in ("top", "left", "right", "bottom"))),
    "fps": lambda a: a.get("fps") is not None,
    "resize": lambda a: a.get("resolution") not in (None, "n"),
}
```

**Why the fix cannot be half-done**, `ffmwiz/reverse_stages.py:280-283`:

```python
    missing = set(STAGE_TRANSFORMATIONS) - set(_TRANSFORMATION_REQUESTED)
    if missing:
        raise ValueError(
            f"transformation(s) with no requested-predicate: {sorted(missing)}")
```

and `ffmwiz/reverse_stages.py:530-534`:

```python
    unowned = sorted(requested_transformations(answers) - set(seen))
    if unowned:
        raise ValueError(
            f"transformation(s) {unowned} are requested but owned by no stage; "
            f"they would be applied in every stage that reads them")
```

So the moment a name enters `STAGE_TRANSFORMATIONS` it must also have a
predicate AND an owner, or every affected job dies with a `ValueError` at plan
time. That is deliberate. It also means Steps 1 and 2 of this plan land
together.

**The neutral values**, `ffmwiz/reverse_stages.py:287-297`:

```python
# Falsey neutral values, so a stage that reads a key without checking for its
# absence still sees "no transformation" rather than a stale truth.
_NEUTRAL_VALUES: dict[str, Any] = {
    "video_speed_factor": 1.0,
    "audio_speed_factor": 1.0,
    "loudnorm_mode": "off",
    # "n" is what the builders read as "keep the source size"; removing the key
    # would make `answers.get("resolution", "n")` agree by accident, but a
    # stage that reads it without a default would see nothing at all.
    "resolution": "n",
}
```

**The ownership assignment**, which appears TWICE and must stay identical —
`ffmwiz/reverse_pipeline.py:235-244` (the planner) and
`ffmwiz/reverse_pipeline.py:496-507` (the executor). The executor copy:

```python
    has_join = bool(answers.get("join_input_items"))
    forward_owns = GEOMETRY_TRANSFORMATIONS if has_join else ()
    reverse_owns = ("cuts", "audio_cuts", "video_speed", "audio_speed",
                    "video_reverse", "audio_reverse", "loudnorm")
    if not has_join:
        reverse_owns = reverse_owns + GEOMETRY_TRANSFORMATIONS
    # Before the join, not after: a plan that cannot be executed correctly must
    # not spend a full forward encode first.
    validate_stage_plan([("forward join", forward_owns),
                         ("reverse", reverse_owns),
                         ("split", ("split",) if split_points else ())],
                        answers)
```

The planner copy at lines 235-244 is the same four assignments followed by the same
`validate_stage_plan(...)` call; only the comment above it differs.

**The filter chain order**, `ffmwiz/wizard_build_b.py:1182-1245` — this is what
decides which group each new key belongs to. In `build_cpu_video_filter` the
order is:

1. `crop=` (lines 1184-1186)
2. `build_orientation_filters(answers)` (line 1187) — `transpose`/`hflip`/`vflip`
3. `fps=` (lines 1189-1190)
4. `scale=` / `pad=` (lines 1192-1229)
5. `build_look_filters(answers)` (line 1231) — `eq=`, `hqdn3d`, sharpen/blur
6. the speed/reverse filter (lines 1233-1234)
7. `build_fade_filters(answers, output_seconds)` (line 1244)

`build_orientation_filters`' own docstring, `ffmwiz/wizard_build_b.py:1287-1294`,
says why it sits at position 2: *"a 90-degree rotation swaps width and height,
so a resize target asked for afterwards applies to the rotated picture"*. That
is the argument for orientation belonging with the geometry: one stage cannot
rotate while a later one scales.

The audio side, `ffmwiz/support/ext01.py:115-131`: LoudNorm, then
`build_volume_filter(answers)`, then `asetpts`, then the audio fade. The raw
options are appended by `ffmwiz/wizard_build.py:886`:

```python
    cmd.extend(answers.get("raw_ffmpeg_args") or [])
```

**The measured leak.** Run this before changing anything; it is the starting
point every later verification is compared against:

```
python -c "
import sys; sys.path.insert(0,'.'); import FFmWiz
from ffmwiz import reverse_stages
from ffmwiz.wizard_build_b import build_cpu_video_filter
a = {'rotate_choice':'90cw','denoise_level':'medium','reverse_video':True,
     'video_speed_enabled':True,'video_speed_factor':1.0,'separator_points':[2.0],
     'audio_volume':2.0,'raw_ffmpeg_args':['-b:v','500k']}
rev = ('cuts','audio_cuts','video_speed','audio_speed','video_reverse','audio_reverse','loudnorm')
for label, owns in (('forward join', FFmWiz.GEOMETRY_TRANSFORMATIONS), ('reverse', rev), ('split', ('split',))):
    s = reverse_stages.stage_answers(a, owns=owns)
    f = build_cpu_video_filter(s) or ''
    print('%-13s rotate=%s denoise=%s volume=%s raw=%s' % (label, 'transpose=1' in f, 'hqdn3d' in f, s.get('audio_volume'), s.get('raw_ffmpeg_args')))"
```

Today it prints, for all three stages:

```
forward join  rotate=True denoise=True volume=2.0 raw=['-b:v', '500k']
reverse       rotate=True denoise=True volume=2.0 raw=['-b:v', '500k']
split         rotate=True denoise=True volume=2.0 raw=['-b:v', '500k']
```

**The boomerang leak**, same cause, different caller
(`ffmwiz/encoding.py:449`, `owns=("video_reverse", "audio_reverse")`):

```
python -c "
import sys; sys.path.insert(0,'.'); import FFmWiz
from ffmwiz import reverse_stages
from ffmwiz.wizard_build_b import build_cpu_video_filter
from ffmwiz.support.ext01 import build_encode_audio_processing_filter
fwd = {'rotate_choice':'90cw','denoise_level':'medium','fade_in_seconds':1.0,
       'audio_volume':2.0,'raw_ffmpeg_args':['-b:v','500k'],'reverse_video':True,
       'video_speed_enabled':True,'video_speed_factor':1.0,
       'audio_streams':[{'index':0}],'audio_tracks':[0]}
back = reverse_stages.stage_answers(fwd, owns=('video_reverse','audio_reverse'))
print('backward video :', build_cpu_video_filter(back))
print('backward audio :', build_encode_audio_processing_filter(back))
print('backward raw   :', back.get('raw_ffmpeg_args'))"
```

Today:

```
backward video : transpose=1,hqdn3d=4:3:6:4.5,fade=t=in:st=0:d=1.000,format=yuv420p
backward audio : volume=2,asetpts=PTS-STARTPTS,afade=t=in:st=0:d=1.000
backward raw   : ['-b:v', '500k']
```

Every one of those was already applied to the forward half this stage reads.

**Repo conventions to match**: every schema entry and every predicate carries a
comment saying WHY, naming the concrete failure it prevents, in the voice of
the existing ones. `raise ValueError` with a lowercase sentence explaining the
consequence. The module ends with an explicit alphabetised `__all__` — new
public names belong in it.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Ownership suites | `python tests/run_suite.py -k stage -j 2` | `OK` |
| Reverse pipeline suites | `python tests/run_suite.py -k reverse -j 2` | `OK` |
| Full suite | `python tests/run_suite.py -j 4` | `OK`, 0 failures |
| Import check | `python -c "import ffmwiz.reverse_stages, ffmwiz.reverse_pipeline"` | exit 0 |

**A repository hook blocks raw `python -m unittest`. Always go through
`tests/run_suite.py`.**

`tests/run_suite.py -k <text>` filters by test MODULE name substring, not by
test method, so `-k stage` runs `test_stage_geometry_ownership` and
`test_reverse_stage_ownership` together.

The real-encode tests need `ffmpeg` and `ffprobe` on PATH; they skip
themselves otherwise. Confirm you have them before starting — a plan whose
central regression silently skips has verified nothing:

```
python -c "import shutil; print(shutil.which('ffmpeg'), shutil.which('ffprobe'))"
```
→ two paths, neither `None`. If either is `None`, STOP and report.

## Scope

**In scope** (the only files you may modify):
- `ffmwiz/reverse_stages.py`
- `ffmwiz/reverse_pipeline.py`
- `tests/test_reverse_stage_ownership.py`
- `tests/test_stage_geometry_ownership.py`

**Out of scope** (do NOT touch, even though they look related):
- `ffmwiz/wizard_build_b.py`, `ffmwiz/wizard_build.py`, `ffmwiz/support/ext01.py`
  — the filter builders. They are correct: each applies the edits it is given,
  once. The defect is that the wrong stage is still being given them.
- `ffmwiz/encoding.py` — its two `stage_answers()` callers read the schema
  constants and inherit the fix. If you find yourself needing to edit it, the
  ownership assignment in `reverse_pipeline.py` is wrong instead.
- `ffmwiz/wizard_flow.py` and the prompt modules — the questions are fine.
- `build/lib/` — a stale packaged copy of the whole package. Never edit it, and
  never let a `grep` hit there change a decision.
- The GIF/thumbnail quick-output keys and the composite keys. Step 3 explains
  why, with the evidence. Do not add them.
- Any addition to `_NEUTRAL_VALUES` beyond what Step 1's check actually
  demonstrates is needed.

## Git workflow

- Branch: `advisor/006-stage-ownership-for-the-new-features`
- One commit per step group is fine. Message style is conventional commits,
  short subject only — see `git log --oneline -5` (e.g.
  `fix: let the NVENC tests run on real hardware`).
- Do NOT push or open a PR.

## Steps

### Step 1: Add the five transformations to the schema and give each one a predicate

In `ffmwiz/reverse_stages.py`, add these entries to `STAGE_TRANSFORMATIONS`
after `"resize"`, each with a comment in the style of the `crop`/`fps`/`resize`
block above it:

| Name | Keys | Why it is a transformation |
|---|---|---|
| `orientation` | `rotate_choice`, `flip_horizontal`, `flip_vertical` | Rotating twice is 180 degrees. A 90-degree rotation also swaps width and height, so it must not be split from the resize. |
| `look` | `adjust_grayscale`, `denoise_level`, `sharpen_level`, `blur_level`, `adjust_brightness`, `adjust_contrast`, `adjust_saturation`, `adjust_gamma` | Denoising twice is visibly softer, and each pass re-processes an already lossy intermediate. |
| `fade` | `fade_in_seconds`, `fade_out_seconds` | A fade per stage compounds, and a fade applied before the reverse lands at the other end of the clip. |
| `volume` | `audio_volume` | A gain applied by three stages is the gain cubed: 2x becomes 8x. |
| `raw_args` | `raw_ffmpeg_args` | The user's own output options, appended last so they override the wizard's. `intermediate_profile` deliberately strips the rate control from a scratch stage but cannot strip an opaque argv, so a `-b:v 500k` here re-imposes on `joined_forward.mkv` exactly the low bitrate that function exists to avoid. |

Spell the `adjust_*` keys out literally rather than splatting `ADJUST_RANGES` —
membership of each group is a decision, and Step 5 adds the guard that keeps
the literal list honest.

Then add one predicate per name to `_TRANSFORMATION_REQUESTED`. Every constant
you need is already in scope through `from ffmwiz.core.constants import *` at
the top of the module — confirm with

```
python -c "import sys; sys.path.insert(0,'.'); from ffmwiz import reverse_stages as r; print(all(hasattr(r,n) for n in ('ROTATE_FILTERS','DENOISE_FILTERS','SHARPEN_FILTERS','BLUR_FILTERS','ADJUST_RANGES')))"
```
→ `True`. Add no imports.

The predicates must read the keys the same way the builders read them, so a
value the builder ignores is not counted as requested:

- `orientation` — true when `str(a.get("rotate_choice") or "none").strip().lower()`
  is a key of `ROTATE_FILTERS`, or either flip is truthy. A `rotate_choice` of
  `"none"` is NOT a request.
- `look` — true when `adjust_grayscale` is truthy, or any of
  `denoise_level`/`sharpen_level`/`blur_level` is a key of its filter table
  (`DENOISE_FILTERS`/`SHARPEN_FILTERS`/`BLUR_FILTERS`, read the same
  `str(...) or "off"` way `build_look_filters` reads them at
  `ffmwiz/wizard_build_b.py:1335-1340`), or any `ADJUST_RANGES` key differs
  from its neutral. Guard the float conversion the way `build_look_filters`
  does, so a junk value is "not requested" rather than a `TypeError` raised
  inside a plan check.
- `fade` — true when either fade is a number greater than zero. There is
  already a helper for exactly this: `requested_fade_seconds(a)` returns
  `(in, out)` and swallows non-numbers
  (`ffmwiz/support/L01_filters.py:170-176`). Use it: `any(requested_fade_seconds(a))`.
- `volume` — true when `float(a.get("audio_volume") or 1.0)` differs from 1.0
  by more than `1e-9`, matching `build_volume_filter`'s own test at
  `ffmwiz/wizard_raw.py:102`. Guard the conversion.
- `raw_args` — true when `a.get("raw_ffmpeg_args")` is non-empty.

**Do NOT add anything to `_NEUTRAL_VALUES` unless you demonstrate it is
needed.** Every one of the fifteen new keys is read with a default that makes
absence mean "no transformation": `str(answers.get("rotate_choice") or "none")`,
`answers.get("flip_horizontal")`, `str(answers.get("denoise_level") or "off")`,
`float(answers.get(key, neutral))` for the adjusts,
`float(answers.get("fade_in_seconds") or 0.0)`,
`float(answers.get("audio_volume") or 1.0)`,
`answers.get("raw_ffmpeg_args") or []`. Popping them is therefore safe, which
is why `resolution` needed a neutral and these do not. Verify that rather than
trust it:

```
python -c "
import sys; sys.path.insert(0,'.')
from ffmwiz.wizard_build_b import build_cpu_video_filter
from ffmwiz.support.ext01 import build_encode_audio_processing_filter
bare = {'audio_streams':[{'index':0}],'audio_tracks':[0]}
print('video:', build_cpu_video_filter(bare))
print('audio:', build_encode_audio_processing_filter(bare))"
```
Today, and after your change:

```
video: format=yuv420p
audio: asetpts=PTS-STARTPTS
```

No `transpose`, no `hqdn3d`, no `eq=`, no `fade`, no `volume=`, no `afade`. If
any of them appears from an empty dictionary, that key DOES need a neutral
value — add it with a comment, and say so in your report.

**Verify**: the schema is now complete but unowned, so the pipeline must refuse
a rotated reverse rather than silently apply it three times:

```
python -c "
import sys; sys.path.insert(0,'.'); import FFmWiz
try:
    FFmWiz.validate_stage_plan([('forward', ()), ('reverse', ('video_reverse',))],
                               {'rotate_choice':'90cw','reverse_video':True,'audio_volume':2.0})
    print('LEAKED: the plan validated with no owner')
except ValueError as error:
    print('refused:', error)"
```
→ one `refused:` line naming both `orientation` and `volume`. If it prints
`LEAKED`, the predicates are wrong.

Then confirm `requested_transformations` still accepts an empty job — this is
the check that a schema name without a predicate fails loudly:

```
python -c "import sys; sys.path.insert(0,'.'); import FFmWiz; print(sorted(FFmWiz.requested_transformations({})))"
```
→ `[]`. A `ValueError` here means you added a schema name without a predicate.

### Step 2: Give every new transformation exactly one owner, in both copies

`ffmwiz/reverse_pipeline.py` assigns ownership twice — the planner at lines
235-244 and the executor at lines 496-507. **Both must change identically.** The module's
own comment above the planner copy says why: *"Two copies of this decision is
how the exported plan drifted from the job it claimed to describe."*

Three changes:

1. Extend `GEOMETRY_TRANSFORMATIONS` (`ffmwiz/reverse_stages.py:241`) to
   `("crop", "fps", "resize", "orientation")`. Orientation genuinely is
   geometry — it changes the frame the resize is sized against — so it travels
   with the group and the constant's name stays honest. Update the comment
   above it to say so. No call site changes for this one: both copies and
   `ffmwiz/encoding.py:327` read the constant.
2. Add `"look"`, `"fade"` and `"volume"` to the `reverse_owns` tuple in BOTH
   copies. They belong to the stage that produces the reversed timeline:
   `build_look_filters` runs after the scale, and `build_fade_filters` runs
   after the reverse, so a fade-in applied by a forward stage would end up at
   the tail once the picture is mirrored.
3. Give `raw_args` to the LAST stage, using the pattern the geometry already
   uses for "the first stage that writes a picture":

```python
    split_owns = ("split", "raw_args") if split_points else ()
    if not split_points:
        reverse_owns = reverse_owns + ("raw_args",)
```

and pass `split_owns` to `validate_stage_plan` in place of the inline
`("split",) if split_points else ()`. The user's options describe the FINAL
file; a scratch intermediate must not receive them.

Add a comment at each site naming the reason, matching the existing
`# Geometry is owned by the FIRST stage that writes a picture` note.

**Verify**: re-run the leak probe from "Current state", first updating its
`rev = (...)` line to the `reverse_owns` tuple you actually wrote. It must now
show exactly one owning stage per column:

```
forward join  rotate=True denoise=False volume=None raw=None
reverse       rotate=False denoise=True volume=2.0 raw=None
split         rotate=False denoise=False volume=None raw=['-b:v', '500k']
```

**Verify**: `python tests/run_suite.py -k stage -j 2` → `OK`. A `ValueError`
mentioning "owned by no stage" means one of the three changes is missing from
one of the two copies.

### Step 3: Confirm the quick-output and composite keys stay OUT, and record why

Do not add `quick_output`, the other `QUICK_ANSWER_KEYS`, or any
`COMPOSITE_ANSWER_KEYS` to the schema. Both are excluded on evidence, not on
judgement, and a dead schema entry is worse than none — it makes the next
reader believe a path is covered.

Confirm both exclusions yourself before moving on.

**Quick outputs never reach a staged reverse.** `ffmwiz/encoding.py:512-516`
routes any job carrying a `quick_output_plan` to `run_quick_output_stages`
BEFORE the reverse dispatch below it is reached, and
`ffmwiz/wizard_build.py:371-381` (`_quick_sub_job`) pops every
`QUICK_ANSWER_KEYS` entry from the sub-job it hands to `build_ffmpeg_command`,
with the docstring *"leaving `quick_output` set would make
`build_ffmpeg_command` plan a second quick output ... without end."*

```
python -c "
import sys; sys.path.insert(0,'.'); import inspect
from ffmwiz import wizard_build
print('sub-job strips QUICK_ANSWER_KEYS:',
      'QUICK_ANSWER_KEYS' in inspect.getsource(wizard_build._quick_sub_job))"
```
→ `sub-job strips QUICK_ANSWER_KEYS: True`. (`FFmWiz.wizard_build` does not
exist — the entry script re-exports only some submodules by name, so import the
module from the package.)

**Compositing is dropped by a staged reverse, not repeated.** The composite
builder is reached from one branch only — `ffmwiz/wizard_b.py:222`,
`elif answers.get("composite_mode"):` → `build_composite_command` — and every
reverse stage rebuilds its command through `build_ffmpeg_command` or
`build_join_encode_command`, neither of which has a composite branch:

```
grep -n "composite" ffmwiz/wizard_build.py
```
→ no hit anywhere in the file, so `build_ffmpeg_command` cannot emit a
composite graph at all. Adding the composite keys to the ownership schema would
not change that; the defect is a missing branch, which is a different fix.

Write both exclusions into the comment block above the new schema entries, one
sentence each with the file:line, so nobody re-audits them.

**Verify**: `grep -n "quick_output\|composite_" ffmwiz/reverse_stages.py` →
hits inside comments only, never inside `STAGE_TRANSFORMATIONS` or
`_TRANSFORMATION_REQUESTED`.

### Step 4: The real-encode regression

Argv assertions alone would have passed while the pipeline still applied
everything three times — that is exactly how the crop leak survived. The new
regression must decode actual pixels.

Add it to `tests/test_stage_geometry_ownership.py`, inside the existing
`GeometryOwnership` class. Read the whole file first. It already provides
everything you need, and building a second fixture would only duplicate it:

- `setUpClass` (line 68) builds two 160x120 clips with a differently coloured
  band on each edge and a tone on the audio.
- `_pipeline(label, join=True, **extra)` (line 181) runs the REAL
  `encoding.run_bounded_reverse_pipeline` and returns
  `(output_dir, every_command_it_issued)`.
- `_stages_with(commands, needle)` (line 207) returns the produced filenames
  whose command contained a needle — this is how the crop test proves "exactly
  one stage".
- `_video(path)`, `_frame_count(path)` and `_pixels(path)` probe the result.

Model the new tests on `test_the_crop_appears_in_its_owner_stage_and_nowhere_else`
(line 227) and `test_a_joined_reverse_split_crops_exactly_once` (line 213).

Extend the module docstring with the new evidence — which keys were missing and
what a doubled rotation looks like — in the voice of the existing one. Keep the
file name: `-k stage_geometry_ownership` is a filter people already use, and the
file is now the stage-ownership regression for every picture-affecting edit.

**Verify**: `python tests/run_suite.py -k stage_geometry_ownership -j 1` → `OK`,
with no `skipped` line for any new test.

### Step 5: The guard that catches the next missing key

`LOOK_ANSWER_KEYS` (`ffmwiz/support/L01_filters.py:78-81`) is the single list of
every key the picture-filter question may write. Today it resolves to exactly
the union of the new `orientation`, `look` and `fade` groups:

```
('rotate_choice', 'flip_horizontal', 'flip_vertical', 'adjust_grayscale',
 'denoise_level', 'sharpen_level', 'blur_level', 'fade_in_seconds',
 'fade_out_seconds', 'adjust_brightness', 'adjust_contrast',
 'adjust_saturation', 'adjust_gamma')
```

Add a test to `tests/test_reverse_stage_ownership.py` asserting that equality,
with a failure message naming the keys each side lacks. A new look token added
to `LOOK_ANSWER_KEYS` without a schema entry then fails here instead of leaking
into three stages.

Add a second guard in the same file: `audio_volume` and `raw_ffmpeg_args` are
each owned by exactly one transformation, asserted by scanning
`STAGE_TRANSFORMATIONS` rather than by naming the transformation — so renaming
a group cannot quietly orphan a key.

**Verify**: `python tests/run_suite.py -k reverse_stage_ownership -j 1` → `OK`.
Then temporarily delete `"fade_in_seconds"` from the `fade` entry, re-run,
confirm the guard FAILS and names that key, and restore it. Report that you
did this.

### Step 6: The mutation check, and the full suite

Remove the whole `"orientation"` entry from `STAGE_TRANSFORMATIONS` and its
predicate from `_TRANSFORMATION_REQUESTED`, then run
`python tests/run_suite.py -k stage -j 2`. The Step 4 rotation test must FAIL.
If it still passes, it is asserting something the pipeline would satisfy either
way and it is not a regression test. Restore both.

Do the same for `"raw_args"` and confirm the raw-options test fails.

**Verify**: after restoring, `python tests/run_suite.py -j 4` → `OK`,
0 failures.

## Test plan

In `tests/test_stage_geometry_ownership.py`, added to `GeometryOwnership`
(real encodes; joined reverse + split unless stated):

1. `test_a_joined_reverse_split_rotates_exactly_once` — `rotate_choice="90cw"`.
   The parts must be 120x160 (the 160x120 source rotated once), NOT 160x120
   (rotated twice, back to the original shape). **The dimension check is the
   whole point: a doubled 90-degree rotation is indistinguishable from none by
   any "did it rotate" assertion.**
2. `test_the_rotation_appears_in_its_owner_stage_and_nowhere_else` —
   `_stages_with(commands, "transpose=")` has length 1 and that stage is
   `joined_forward*`.
3. `test_a_joined_reverse_split_denoises_once` — `denoise_level="medium"`;
   `_stages_with(commands, "hqdn3d")` has length 1, and that stage is NOT the
   forward join (the reverse owns `look`).
4. `test_a_flip_is_applied_once` — `flip_horizontal=True` on the single-input
   path (`join=False`). Assert on PIXELS via `_pixels`: the red left band must
   end up on the right. A flip applied twice is the identity, so dimensions
   cannot see it.
5. `test_the_raw_options_reach_only_the_final_stage` —
   `raw_ffmpeg_args=["-metadata", "comment=ffmwiz006"]`;
   `_stages_with(commands, "comment=ffmwiz006")` has length 1 and it is the
   Split stage, not `joined_forward*`.
6. `test_the_volume_gain_is_applied_once` — `audio_volume=2.0`;
   `_stages_with(commands, "volume=2")` has length 1.

In `tests/test_reverse_stage_ownership.py` (no ffmpeg needed — follow the
existing `EVERY_EDIT` / `stage_answers` style at lines 39-144):

7. `test_a_stage_that_owns_nothing_carries_no_picture_filter` — every one of
   the fifteen new keys is absent from `stage_answers(EVERY_EDIT, owns=())`.
8. `test_every_look_answer_key_is_owned_by_some_transformation` — the
   `LOOK_ANSWER_KEYS` guard from Step 5.
9. `test_the_volume_and_raw_keys_are_owned_exactly_once` — the second Step 5
   guard.
10. `test_a_rotation_with_no_owner_is_refused` — `validate_stage_plan` raises
    and the message names `orientation`.
11. `test_the_boomerang_backward_half_carries_no_forward_edit` —
    `stage_answers(forward, owns=("video_reverse", "audio_reverse"))` drops
    rotation, denoise, fade, volume and raw args. This is the
    `ffmwiz/encoding.py:449` caller, the one this plan fixes for free — pin it
    so a future ownership change cannot un-fix it.

Structural models to read first: `tests/test_stage_geometry_ownership.py` for
1-6, `tests/test_reverse_stage_ownership.py` for 7-11.

**Verify**: `python tests/run_suite.py -k stage -j 2` → `OK` with at least 11
more tests than before; `python tests/run_suite.py -j 4` → `OK`.

## Done criteria

ALL must hold:

- [ ] `python -c "import sys; sys.path.insert(0,'.'); import FFmWiz; st=FFmWiz.STAGE_TRANSFORMATIONS; owned=set().union(*map(set, st.values())); print(all(k in owned for k in ('rotate_choice','flip_horizontal','flip_vertical','adjust_grayscale','denoise_level','sharpen_level','blur_level','adjust_brightness','adjust_contrast','adjust_saturation','adjust_gamma','fade_in_seconds','fade_out_seconds','audio_volume','raw_ffmpeg_args')))"` prints `True`
- [ ] `python -c "import sys; sys.path.insert(0,'.'); import FFmWiz; print(sorted(FFmWiz.requested_transformations({})))"` prints `[]` (every schema name has a predicate)
- [ ] The Step 2 leak probe shows exactly one owning stage per column
- [ ] `grep -n "quick_output\|composite_" ffmwiz/reverse_stages.py` hits comments only
- [ ] `grep -n "reverse_owns = " ffmwiz/reverse_pipeline.py` shows two identical tuples
- [ ] `python tests/run_suite.py -k stage -j 2` → `OK`, no new test skipped
- [ ] `python tests/run_suite.py -j 4` → `OK`, 0 failures
- [ ] The Step 6 mutation check was performed for `orientation` AND `raw_args`, both went red, both restored
- [ ] `git status --short` shows only the four in-scope files

## STOP conditions

Stop and report back (do not improvise) if:

- The excerpts above do not match the live code (drift since `aaf0aed`).
- `ffmpeg`/`ffprobe` are not on PATH, so the Step 4 regression can only skip.
- Adding the predicates makes an EXISTING test fail. That would mean a current
  stage plan leaves one of these transformations unowned on a path this plan
  did not examine — report which test and which transformation; do not weaken
  the predicate to make it pass.
- A new transformation cannot be given exactly one owner without moving a
  stage's boundaries. Ownership is the whole mechanism; a shared owner is not a
  compromise, it is the bug.
- The Step 4 rotation test still passes with `"orientation"` removed from the
  schema — the test is not pinning the behaviour it claims to.
- `grep -rn "stage_answers(" ffmwiz/` finds a caller outside
  `ffmwiz/reverse_pipeline.py` and `ffmwiz/encoding.py`. There should be
  exactly four, plus the definition.
- The two ownership assignments in `reverse_pipeline.py` already differed from
  each other before your change.

## Maintenance notes

For whoever owns this code next:

- **The rule this establishes**: any answer key that changes the OUTPUT and can
  be read by more than one stage must be declared in `STAGE_TRANSFORMATIONS`
  with a predicate and exactly one owner, in the same change that introduces
  the key. The `LOOK_ANSWER_KEYS` guard from Step 5 enforces it for the
  picture-filter family; the volume and raw-options keys have their own.
- The three-way coupling to remember: the schema in `reverse_stages.py`, the
  ownership assignment in the TWO copies in `reverse_pipeline.py`, and the
  filter-chain order in `build_cpu_video_filter` that decides which group a new
  key belongs to.
- A reviewer should scrutinise tests 1 and 4 hardest. A doubled 90-degree
  rotation and a doubled horizontal flip both look like "nothing happened" to
  any assertion that checks neither dimensions nor pixels.
- **Deferred, with the evidence, so nobody re-discovers it blind:** a fade is
  still applied once per SEGMENT inside `run_segmented_reverse_main_encode`.
  `build_main_encode_reverse_segment_command` (`ffmwiz/reverse_stages.py:106-124`)
  copies `answers`, sets `cut_keep_ranges = [(start, end)]` and calls
  `build_ffmpeg_command`, so each tile of the timeline gets its own
  `fade=t=in:st=0`. Segments are not stages — they are concatenated, so a crop
  or a rotation per segment is correct — but a time-anchored filter is not.
  This plan makes the reverse stage the single OWNER of the fade, which removes
  the join and split duplication; it does not change the per-segment
  repetition, which needs the whole-timeline offset passed into the segment
  builder and is a separate change.
- **Second deferred finding, also out of scope here:** on the
  audio-reverse-across-a-join path, `ffmwiz/encoding.py:327` stages the forward
  join with `owns=GEOMETRY_TRANSFORMATIONS`, but the final rebuild at
  `ffmwiz/encoding.py:361` is a plain `_single_input_answers(answers, rebased)`
  with no `stage_answers()` call and no `validate_stage_plan()`, so it appears
  to carry the geometry a second time. That is the same shape as the crop leak
  this schema was built for, on a path the schema is not applied to. Confirm
  and file separately.
- Compositing is silently DROPPED by every staged reverse (see Step 3). That is
  a missing branch in the reverse rebuild, not an ownership problem, and it is
  not fixed here.
