#!/usr/bin/env bash
# Run the FFmWiz suite inside WSL.
#
# WHAT THIS IS FOR, and what it is not:
#   FFmWiz is a Windows application. Part of its suite proves Windows-only
#   behaviour -- native Windows PowerShell 5.1 invocation, NTFS junctions,
#   win32 bindings -- and none of that exists on Linux. Running here therefore
#   gives fast, cheap coverage of everything portable, and it CANNOT replace the
#   Windows legs. `--require powershell` is deliberately not passed for that
#   reason. Treat a green run here as "the portable half is healthy", never as
#   "the product is verified".
#
# Layout, matching the project's rule that the project lives in ONE folder while
# its prerequisites are shared machine-wide:
#   * shared, installed once for every project:  /opt/uv-python (CPython builds
#     managed by uv) and the distro's own ffmpeg/ffprobe;
#   * project-local, git-ignored:                .venv-wsl inside this folder.
#
# Usage, from anywhere:
#   wsl bash "tools/run-tests-wsl.sh"              # whole portable suite
#   wsl bash "tools/run-tests-wsl.sh" -k waveform  # any run_suite.py arguments
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

SHARED_PYTHON_DIR="${UV_PYTHON_INSTALL_DIR:-/opt/uv-python}"
PYTHON_VERSION="${FFMWIZ_WSL_PYTHON:-3.13}"
VENV_DIR="$PROJECT_DIR/.venv-wsl"

interpreter() {
    local candidate
    candidate="$(printf '%s\n' "$SHARED_PYTHON_DIR"/cpython-"$PYTHON_VERSION"*/bin/python"$PYTHON_VERSION" 2>/dev/null | head -1)"
    [ -x "$candidate" ] && { printf '%s' "$candidate"; return 0; }
    return 1
}

if ! PYTHON="$(interpreter)"; then
    echo "No shared CPython $PYTHON_VERSION under $SHARED_PYTHON_DIR." >&2
    echo "Install it once, for every project on this machine:" >&2
    echo "  sudo env UV_PYTHON_INSTALL_DIR=$SHARED_PYTHON_DIR uv python install $PYTHON_VERSION" >&2
    exit 2
fi

for tool in ffmpeg ffprobe; do
    command -v "$tool" >/dev/null || {
        echo "$tool is not installed. It is a SHARED prerequisite: sudo apt-get install -y ffmpeg" >&2
        exit 2
    }
done

if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "creating the project venv at $VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install -q --disable-pip-version-check numpy setuptools wheel

export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
echo "python : $("$VENV_DIR/bin/python" --version)"
echo "ffmpeg : $(ffmpeg -version | head -1)"
echo

# --require names only what THIS environment genuinely provides. PowerShell is
# absent by design, so requiring it here would be a false failure; the Windows
# jobs are where that capability is proved.
exec "$VENV_DIR/bin/python" tests/run_suite.py \
    --require ffmpeg --require numpy --require wheel "$@"
