# Running the tests

From the repo root:

    python tests/run_suite.py                     # whole suite, in parallel
    python tests/run_suite.py -k practical        # only matching modules
    python tests/run_suite.py -k join -k reverse  # -k repeats, and unions
    python tests/run_suite.py -j 1                # serial, same reporting
    python tests/run_suite.py --json results.json # machine-readable result file

    python -m unittest discover -s tests          # whole suite, stdlib runner
    python -m unittest discover -s tests -p test_practical_ffmpeg.py   # one module

`run_suite.py` is what CI runs. It gives each test module its own worker process
and pulls the next module off the queue as a worker frees up: measured on a
16-core machine, ~2380 tests take ~306 s at `-j 8` — the suite is
dominated by real ffmpeg child processes, not CPU. It adds no dependency; the project keeps a
zero-test-dependency policy, so it is `unittest` plus `concurrent.futures`.
Module-per-process is also what keeps it safe: several suites monkeypatch module
globals such as `appio.note`, which is only sound while one module owns its
interpreter.

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

`--json PATH` writes the run as data — every module with its counts, failures,
errors, unexpected successes, and every skip with the capability it was
attributed to, plus the environment (Python, platform, the resolved ffmpeg and
ffprobe paths and version lines, numpy/PySide6/setuptools/wheel versions). It is
written on EVERY exit path, including the refusals that run no tests, so a red
CI job is readable from its artifact without digging through the log. Both CI
jobs upload it with `if: always()`.

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
