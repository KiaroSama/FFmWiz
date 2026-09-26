# Running the tests

From the repo root:

    python tests/run_suite.py                     # whole suite, in parallel
    python tests/run_suite.py -k practical        # only matching modules
    python tests/run_suite.py -k join -k reverse  # -k repeats, and unions
    python tests/run_suite.py -j 1                # serial, same shape and reporting
    python tests/run_suite.py --json results.json # machine-readable result file
    python tests/run_suite.py --module-timeout 60 # a tighter per-module ceiling

    python -m unittest discover -s tests          # whole suite, stdlib runner
    python -m unittest discover -s tests -p test_practical_ffmpeg.py   # one module

`run_suite.py` is what CI runs. It gives each test module its own child
interpreter — `run_suite_child.py`, one process per module, supervised by
`run_suite_process.py` (and `run_suite_windows.py` for the Windows ownership
boundary) — and `-j` only decides how many run at once: GitHub CI measures 2663
tests in 174-224 s at `-j 2`, because the suite is dominated by real ffmpeg child
processes rather than CPU. It adds no dependency; the project keeps a zero-test-dependency policy, so it
is `unittest` plus `subprocess`. Module-per-process is also what keeps it safe:
several suites monkeypatch module globals such as `appio.note`, which is only
sound while one module owns its interpreter.

Serial and parallel runs are the SAME shape, which is what makes a module's own
failures survivable:

* a module is bounded by `--module-timeout` (default 600 s), from its first import
  to its last thread. Nothing inside a hung interpreter can end it — a stuck
  non-daemon thread blocks shutdown forever — so the ceiling belongs to the parent,
  which ends the module's whole owned process scope and records a **timeout**. That
  scope is a new session on POSIX and a kill-on-close Job Object on Windows; the
  child joins its job over a stdin gate BEFORE it imports any test code, so a
  descendant started by a venv redirector cannot escape it.
* a module that takes its own process down (`os._exit`, a segfault in a C
  extension, an OOM kill) loses only itself and is recorded as a **crash** with its
  exit code and console tail. In `-j 1` it used to take the runner with it.
* every record is written to the `--json` file the moment it arrives, so a run that
  is cancelled, killed or hangs still leaves the evidence of what had finished.
* the child keeps its exception hooks installed for its whole life, so a thread
  started BY a thread — which no snapshot of "threads this module started" contains
  — is still attributed to the module that started it.

`ok`, `timeout`, `crash`, `cancelled` and `not-run` are five different statuses in
that file and in the CI step summary. None of them reads as a pass.

The FINAL outcome decides, not the record the module published: a module whose
interpreter exits abnormally after writing a passing result, or that leaves a live
child process behind, is recorded as a **crash** and fails the run. A module result
that is malformed, oversized or names a different module is rejected the same way.

A skip is classified by the CAPABILITY it names — `ffmpeg`, `numpy`, `powershell`,
`pyside6`, `wheel` — and two flags act on that:

    python tests/run_suite.py --require ffmpeg --require numpy   # what CI uses
    python tests/run_suite.py --strict-skips                     # every capability

`--require` fails the run when a suite skipped for a capability THIS job installs,
so the suite cannot silently shrink. It also PREFLIGHTS: the capability is proved
present before anything runs, rather than inferred from the absence of a matching
skip, so a missing dependency fails on itself instead of on the wording of
whatever skip happened to mention it. `--strict-skips` is the same check with every
known capability required, which is only correct in a job that really installs them
all — locally it fails on whatever is genuinely absent. Hardware and privilege
skips (NVENC, symlink) are never capabilities and stay allowed either way. The
phrase "no usable X" is NOT a hardware exemption: "No usable FFmpeg binary" is a
missing ffmpeg, and it used to slip past `--require` because a broad pattern
matched it first.

The NVENC suite is LOCAL BY DESIGN. `test_cuda_scale_fallback` probes for a
usable CUDA/`hevc_nvenc` pair and encodes for real when it finds one; no
GitHub-hosted runner has an NVIDIA GPU, and this repository must not point
`vars.CI_RUNNER` at a self-hosted one while it is public, because a fork's pull
request would then execute on that machine. So the three hardware tests skip in
CI and are run on a developer's own GPU instead:

    python tests/run_suite.py -k cuda_scale_fallback

Verified 2026-09-19 on an RTX 4070 Ti (driver 616.92, ffmpeg with
`h264_nvenc`/`hevc_nvenc`/`av1_nvenc` and the `cuda` hwaccel): the gate opened and
all 14 tests passed, including the three CI reports as skipped.

A requested JSON report is part of the runner's success contract. An unwritable
path fails before modules start. Incremental or final publication failure makes
the run fail even if every test passed or a later publication recovers. Results
are written through an exclusively created sibling temporary, flushed and
replaced atomically. A pre-existing `results.json.partial` is never touched.
`test_run_suite_publication` covers all three publication stages, previous-report
preservation, foreign partial-file aliases and concurrent temporary ownership.

`--json PATH` writes the run as data — every module with its counts, failures,
errors, unexpected successes, and every skip with the capability it was
attributed to, plus the environment (Python, platform, the resolved ffmpeg and
ffprobe paths and version lines, numpy/PySide6/setuptools/wheel versions). It is
written on EVERY exit path, including the refusals that run no tests, so a red
CI job is readable without digging through the log.

Both CI jobs upload it with `if: always()`, and that upload is allowed to fail:
the account's artifact storage quota is currently full, so `continue-on-error`
keeps an external condition from turning a green suite red. Because a
best-effort step must never be the only copy of the evidence,
`tools/ci_result_summary.py` then restates the same facts — commit SHA, tool
versions, counts, failures by name, skips grouped by capability, and whether an
artifact was actually retained — in the job log and `GITHUB_STEP_SUMMARY`. It
always exits 0: it reports, it never decides.

The stdlib `discover` form still works and is the fallback. Always `discover`,
always from the repo root. Two reasons:

- **The dotted form does not work.** `python -m unittest tests.test_command_audio`
  fails with `ModuleNotFoundError: No module named 'command_gen_base'`. The test
  modules import their helpers as top-level modules (`from command_gen_base import
  CommandGenBase`), which only resolves because `discover -s tests` puts `tests/`
  on `sys.path`; the dotted form does not. Fixing that would mean a `tests/__init__.py`
  plus rewriting the helper imports in every module — not worth it while `discover`
  works.
- **The repo root must be the working directory.** Most modules reach `import FFmWiz`
  through the `sys.path` entry for the current directory, so running from anywhere
  else breaks the import.

Files in `tests/` that do not match `test*.py` are helpers, not suites:
`command_gen_base.py` (shared `TestCase` base), `join_test_helpers.py`,
`cache_test_utils.py` (owned temp cache dirs). `test_ci_coverage_guard.py` fails the
build if a file holding tests is named so that discovery would skip it.

## Tests that need something extra

Some tests self-skip when a capability is missing: real ffmpeg/ffprobe on `PATH`,
`numpy` (waveform math), an NVIDIA GPU (NVENC), symlink privilege. CI installs
ffmpeg and numpy and passes `--require ffmpeg --require numpy` (plus `--require
pyside6` in the GUI job), so it **fails** if a test skipped for a reason that job
should have provided; GPU and symlink skips stay allowed.
Without ffmpeg on `PATH`, a local run is a different run: hundreds of tests
self-skip, so a green result there proves far less than it looks like it does.
The check is `python tests/run_suite.py --require ffmpeg --require numpy` —
the same gate `.github/workflows/python-smoke.yml` uses — which fails the run
when a suite skipped for a capability the run should have provided.
