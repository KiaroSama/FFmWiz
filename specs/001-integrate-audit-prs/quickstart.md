# Verifying the integrated result

Prerequisites: the repository at the post-integration `main`, FFmpeg and ffprobe on `PATH`, NumPy,
and PowerShell. Qt (PySide6) and the GPU cases belong to CI.

## 1. The tree compiles and imports

```text
python -m compileall -q ffmwiz tests
python -c "import FFmWiz"
```

Expected: no output, exit 0. A failure here means a merge conflict was resolved into broken syntax.

## 2. Each repair's own regressions, locally (the light check)

```text
python tests/run_suite.py -k run_suite --json runner-review.json
python tests/run_suite.py -k command_io -k command_read_write_manifest -k source_alias --json source-review.json
python tests/run_suite.py -k gui_child_ownership -k gui_child_fault -k join_segment_clock -k waveform_stream_contract -k gui_editors --json gui-review.json
```

Expected: each ends `OK`. A skip that blames a capability this machine has is a failure, not a pass.

## 3. The file-size ceiling still holds

```text
git ls-files '*.py' | while read -r f; do n=$(wc -l < "$f"); [ "$n" -ge 800 ] && echo "$f: $n"; done
```

Expected: no output. All three submissions add lines to files that were recently brought under 800.

## 4. The heavy pass — CI, on the exact SHA

Push, then read the run for that commit and correlate every required check with its `headSha`.
Expected: all seven checks green on the final `main` commit, plus whichever of the submissions'
own workflows survive integration.

## 5. The four behaviours, observable from main

| Repair | What to observe |
|---|---|
| A06 | A module that exits abnormally after writing a passing record makes the run fail |
| A01 | A job whose statistics file is a hard link to its source is refused before FFmpeg starts, and the source hash is unchanged |
| A03 | A Reverse whose child cannot be stopped keeps its output file, recorded as retained with a reason |
| A04 | Selecting the second audio track produces identical PCM in both editors |

Each is covered by a regression module; this table is what a human checks when the tests are not
the question.
