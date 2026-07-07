"""Test-only helpers for creating and safely removing owned temporary FFmpeg
capability-cache directories.

These helpers exist solely to support the test suite and practical validation.
They are intentionally NOT part of production FFmWiz code: FFmWiz never creates
test-run ownership markers, test caches, or recursive cleanup logic at runtime.

All protected-path, symlink-resolution, containment, marker-validation, and
idempotency guarantees from the original implementation are preserved here.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import FFmWiz

# Ownership marker written into temporary cache directories created by tests or
# practical validation, so cleanup can prove it owns a directory before removal.
TEST_CACHE_OWNER_MARKER = ".ffmwiz_test_cache_owner"

# The exact capability-cache filenames FFmWiz itself writes. Used only for
# read-only snapshotting; never created or modified by these helpers.
CAPABILITY_CACHE_OWNED_FILENAMES = ("ffmpeg_capabilities.json", "ffmpeg_capabilities.corrupt")


def protected_cleanup_paths() -> set[Path]:
    """Resolved absolute paths that cleanup must never delete: the project root,
    the project/default capability-cache directory, the user home, the current
    working directory, the system temp root, and filesystem roots."""
    project_root = Path(FFmWiz.__file__).resolve().parent
    protected: set[Path] = {
        project_root,
        project_root / FFmWiz.CAPABILITY_CACHE_DIRNAME,  # default runtime capability cache
        Path.home().resolve(),
        Path.cwd().resolve(),
        Path(tempfile.gettempdir()).resolve(),
    }
    # Filesystem roots / drive anchors for the protected base directories.
    for base in (project_root, Path.cwd().resolve(), Path.home().resolve()):
        anchor = base.anchor
        if anchor:
            protected.add(Path(anchor))
    return protected


def safe_remove_owned_temp_dir(owned_path: Any, expected_marker: str,
                               allowed_temp_root: Any) -> bool:
    """Safely remove ONLY a temporary directory created and owned by the caller.

    Refuses to delete protected paths, anything outside allowed_temp_root, or a
    directory whose ownership marker is missing or does not match. Resolves
    symlinks before validation (a symlink to a protected directory is refused).
    Idempotent: returns False if the owned path is already absent. Returns True
    when a directory was actually removed.
    """
    if not owned_path or not str(owned_path).strip():
        raise ValueError("Cleanup safety: refusing to act on an empty path.")
    if not expected_marker or not str(expected_marker).strip():
        raise ValueError("Cleanup safety: an ownership marker value is required.")
    owned = Path(owned_path).resolve()
    temp_root = Path(allowed_temp_root).resolve()

    # Protection checks run BEFORE any existence short-circuit so a protected
    # path is refused even when it does not currently exist.
    protected = protected_cleanup_paths()
    for parent in [owned, *owned.parents]:
        if parent == parent.parent:  # filesystem root / drive anchor
            protected.add(parent)
    if owned in protected:
        raise RuntimeError("Cleanup safety: refusing to delete protected path: %s" % owned)
    if owned == temp_root:
        raise RuntimeError("Cleanup safety: refusing to delete the temp root itself: %s" % owned)
    try:
        owned.relative_to(temp_root)
    except ValueError:
        raise RuntimeError(
            "Cleanup safety: %s is outside the allowed temp root %s" % (owned, temp_root))

    if not owned.exists():
        return False  # idempotent for a legitimate, already-removed owned path

    marker = owned / TEST_CACHE_OWNER_MARKER
    if not marker.is_file():
        raise RuntimeError("Cleanup safety: ownership marker missing in %s" % owned)
    try:
        recorded = marker.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("Cleanup safety: cannot read ownership marker in %s (%s)" % (owned, exc))
    if recorded != str(expected_marker).strip():
        raise RuntimeError("Cleanup safety: ownership marker mismatch in %s" % owned)

    shutil.rmtree(owned)
    return True


def create_owned_temp_cache_dir(run_id: str) -> str:
    """Create a unique temporary cache directory under the system temp root and
    stamp it with an ownership marker so it can be safely removed later."""
    path = tempfile.mkdtemp(prefix="ffmwiz_test_cache_")
    (Path(path) / TEST_CACHE_OWNER_MARKER).write_text(str(run_id), encoding="utf-8")
    return path


@contextlib.contextmanager
def isolated_cache_env(run_id: str | None = None):
    """Context manager that redirects FFMWIZ_CACHE_DIR to a uniquely-owned
    temporary directory and ALWAYS restores the previous value, even on error.
    The temporary directory is removed via the ownership-verified safe helper."""
    run_id = run_id or uuid.uuid4().hex
    previous = os.environ.get("FFMWIZ_CACHE_DIR")
    path = create_owned_temp_cache_dir(run_id)
    os.environ["FFMWIZ_CACHE_DIR"] = path
    try:
        yield path, run_id
    finally:
        if previous is None:
            os.environ.pop("FFMWIZ_CACHE_DIR", None)
        else:
            os.environ["FFMWIZ_CACHE_DIR"] = previous
        safe_remove_owned_temp_dir(path, run_id, tempfile.gettempdir())


# ------------------------------------------------------------------
# Read-only snapshot of the REAL runtime capability cache. These helpers never
# create, open-for-write, move, rename, or delete any real cache file. They are
# used only to prove that running the test suite leaves the user's real cache
# untouched.
# ------------------------------------------------------------------

def runtime_cache_dir() -> Path:
    """The default runtime capability-cache directory, ignoring any test-time
    FFMWIZ_CACHE_DIR override and never creating it."""
    return Path(FFmWiz.__file__).resolve().parent / FFmWiz.CAPABILITY_CACHE_DIRNAME


def _read_only_file_state(path: Path) -> dict[str, Any]:
    """Capture read-only metadata for a single path without modifying it."""
    state: dict[str, Any] = {
        "path": str(path), "exists": False, "is_file": False,
        "size": None, "mtime_ns": None, "sha256": None, "error": None,
    }
    try:
        if not path.exists():
            return state
        state["exists"] = True
        if path.is_symlink() or not path.is_file():
            state["is_file"] = path.is_file()
            return state
        state["is_file"] = True
        st = path.stat()
        state["size"] = st.st_size
        state["mtime_ns"] = st.st_mtime_ns
        digest = hashlib.sha256()
        with open(path, "rb") as fh:  # read-only binary
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        state["sha256"] = digest.hexdigest()
    except OSError as exc:
        state["error"] = str(exc)
    return state


def snapshot_runtime_cache_state(cache_dir: Any = None) -> dict[str, dict[str, Any]]:
    """Return a read-only snapshot of the FFmWiz-owned cache files. Never creates
    the directory or any file. Defaults to the real runtime cache directory."""
    base = Path(cache_dir) if cache_dir is not None else runtime_cache_dir()
    return {name: _read_only_file_state(base / name)
            for name in CAPABILITY_CACHE_OWNED_FILENAMES}


# ------------------------------------------------------------------
# Safe, uniquely-named validation sentinel. Never reuses a production cache
# filename; created with exclusive semantics; removed only when ownership is
# proven.
# ------------------------------------------------------------------

def create_validation_sentinel(cache_dir: Any, run_id: str | None = None) -> dict[str, Any]:
    """Create a uniquely-named sentinel inside an EXISTING writable cache dir
    using exclusive creation. Returns ownership info. Refuses to overwrite an
    existing path or to use a production cache filename."""
    base = Path(cache_dir)
    if not base.is_dir():
        raise RuntimeError("Sentinel: cache directory does not exist: %s" % base)
    run_id = run_id or uuid.uuid4().hex
    token = uuid.uuid4().hex
    name = ".ffmwiz_validation_sentinel_%s" % run_id
    if name in CAPABILITY_CACHE_OWNED_FILENAMES:
        raise RuntimeError("Sentinel: refusing to use a production cache filename.")
    path = base / name
    if path.exists():
        raise RuntimeError("Sentinel: path already exists, refusing to overwrite: %s" % path)
    # Exclusive creation: fails if the file appears between the check and open.
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(token)
    return {"path": str(path), "run_id": run_id, "token": token, "name": name}


def remove_validation_sentinel(info: dict[str, Any], cache_dir: Any) -> bool:
    """Remove only the exact owned sentinel after verifying path, run-id in the
    filename, regular-file (non-symlink) status, location, non-production name,
    and matching ownership token."""
    base = Path(cache_dir).resolve()
    path = Path(info["path"])
    if not path.exists():
        return False  # idempotent
    if info["run_id"] not in path.name:
        raise RuntimeError("Sentinel cleanup: run id not present in filename: %s" % path.name)
    if path.name in CAPABILITY_CACHE_OWNED_FILENAMES:
        raise RuntimeError("Sentinel cleanup: refusing to delete a production cache filename.")
    if path.is_symlink():
        raise RuntimeError("Sentinel cleanup: refusing to delete a symlink: %s" % path)
    if not path.is_file():
        raise RuntimeError("Sentinel cleanup: not a regular file: %s" % path)
    if path.resolve().parent != base:
        raise RuntimeError("Sentinel cleanup: sentinel is outside the expected cache dir.")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("Sentinel cleanup: cannot read sentinel (%s)." % exc)
    if content != info["token"]:
        raise RuntimeError("Sentinel cleanup: ownership token mismatch.")
    path.unlink()
    return True
