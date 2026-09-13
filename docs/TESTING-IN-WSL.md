# Running the FFmWiz suite in WSL

FFmWiz is a Windows application, and its CI matrix runs on Windows. WSL is a
second, much faster place to run the **portable** part of the suite while you
work: measured on 2026-09-13 at **224 seconds for 2433 tests across 6 workers**,
against roughly five minutes on Windows. (The suite has grown since that
measurement; the ratio is the point, not the exact count.)

It does not replace the Windows run, and it is not meant to. See
[What WSL cannot cover](#what-wsl-cannot-cover).

## Layout

The project lives in one folder; its prerequisites are shared machine-wide, so
other projects on the same machine use the same ones.

| | Where | Shared? |
|---|---|---|
| CPython 3.13 / 3.10 | `/opt/uv-python`, managed by `uv` | shared by every project |
| ffmpeg / ffprobe | the distro's own packages | shared by every project |
| project virtualenv | `.venv-wsl` inside this folder | project-only, git-ignored |

`/etc/profile.d/uv-shared-python.sh` exports `UV_PYTHON_INSTALL_DIR=/opt/uv-python`,
so any shell on the machine finds the shared interpreters without extra setup.

## One-time machine setup

Only needed on a machine that has never run this:

```bash
# ffmpeg and ffprobe, shared
sudo apt-get install -y ffmpeg

# uv, shared, in /usr/local/bin
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh

# the interpreters this project supports, shared and world-readable
sudo env UV_PYTHON_INSTALL_DIR=/opt/uv-python uv python install 3.13 3.10
sudo chmod -R a+rX /opt/uv-python
echo 'export UV_PYTHON_INSTALL_DIR=/opt/uv-python' | sudo tee /etc/profile.d/uv-shared-python.sh
```

Ubuntu 26.04 ships **Python 3.14 only**. `pyproject.toml` declares
`requires-python = ">=3.10"` with no upper bound, but CI proves 3.10 and 3.13
and nothing has been verified on 3.14 — so the interpreters come from `uv`,
pinned to the versions CI actually tests, rather than from `apt`.

## Running

From Windows, or from inside WSL:

```bash
wsl bash "tools/run-tests-wsl.sh"
```

Any `run_suite.py` argument passes straight through:

```bash
wsl bash "tools/run-tests-wsl.sh" -k waveform
wsl bash "tools/run-tests-wsl.sh" -j 4 --json /tmp/results.json
```

The script creates `.venv-wsl` on first use, installs `numpy`, `setuptools` and
`wheel` into it, and requires `ffmpeg`, `numpy` and `wheel` of the run. It does
**not** require `powershell`: see below.

## What WSL cannot cover

Some of this suite exists to prove Windows behaviour, and on Linux those tests
skip or are gated by platform. A clean WSL run currently reports about **42
skips** for exactly this reason:

- **Native Windows PowerShell 5.1.** `command_to_powershell` renders a command
  a user pastes into a real Windows shell; PowerShell 7 on Linux is a different
  host and does not settle the question. This is the whole point of the
  displayed-command work.
- **NTFS junctions and hardlink identity.** Source-overwrite protection is
  proved against real junctions, symlinks and hardlinks; Linux has different
  semantics.
- **`win32` bindings and the Windows launcher/installer scripts**, which are
  driven through `.cmd` shims.

Note that PowerShell 7 IS usually installed in WSL, so "is pwsh present" is the
wrong gate for those tests; the gate is the platform. A WSL run that shows them
as skipped is correct, not degraded.

**Therefore:** a green WSL run means "the portable half is healthy". It is never
evidence that a release is verified. The Windows CI matrix is what settles that.
