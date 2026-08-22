# Running the tests

From the repo root:

    python -m unittest discover -s tests          # whole suite
    python -m unittest discover -s tests -p test_practical_ffmpeg.py   # one module

Always `discover`, always from the repo root. Two reasons:

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
ffmpeg and numpy and then **fails** if a test skipped for a reason it should have
provided, so the suite cannot silently shrink; GPU and symlink skips stay allowed.
Locally, a run without ffmpeg is green with ~20 fewer tests — check the skip count
before trusting a green local run.
