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

# Prove the venv is the one this script just resolved. A .venv-wsl left over
# from a different interpreter -- an older uv build, or the distro's Python 3.14
# -- keeps working and silently runs the suite on a version this project does
# not support. The Windows CI jobs assert exactly this about their own venv;
# there is no reason for the fast local pass to be the one that does not.
VENV_BASE="$("$VENV_DIR/bin/python" -c 'import sys; print(sys.base_prefix)')"
WANT_BASE="$("$PYTHON" -c 'import sys; print(sys.base_prefix)')"
if [ "$VENV_BASE" != "$WANT_BASE" ]; then
    echo "$VENV_DIR was built from $VENV_BASE, not the shared $WANT_BASE." >&2
    echo "Delete it and run again:  rm -rf '$VENV_DIR'" >&2
    exit 2
fi
VENV_VERSION="$("$VENV_DIR/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [ "$VENV_VERSION" != "$PYTHON_VERSION" ]; then
    echo "$VENV_DIR runs Python $VENV_VERSION, but $PYTHON_VERSION was requested." >&2
    echo "Delete it and run again:  rm -rf '$VENV_DIR'" >&2
    exit 2
fi

"$VENV_DIR/bin/python" -m pip install -q --disable-pip-version-check numpy setuptools wheel

export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
echo "base   : $VENV_BASE"
echo "python : $("$VENV_DIR/bin/python" --version)"
echo "ffmpeg : $(ffmpeg -version | head -1)"
echo

# --require names only what THIS environment genuinely provides. PowerShell is
# absent by design, so requiring it here would be a false failure; the Windows
# jobs are where that capability is proved.
exec "$VENV_DIR/bin/python" tests/run_suite.py \
    --require ffmpeg --require numpy --require wheel "$@"
