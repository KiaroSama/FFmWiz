"""Test-only helpers for creating and safely removing owned temporary FFmpeg
capability-cache directories.

These helpers exist solely to support the test suite and practical validation.
They are intentionally NOT part of production FFmWiz code: FFmWiz never creates
test-run ownership markers, test caches, or recursive cleanup logic at runtime.

All protected-path, symlink-resolution, containment, marker-validation, and
idempotency guarantees from the original implementation are preserved here.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import FFmWiz

# Ownership marker written into temporary cache directories created by tests or
# practical validation, so cleanup can prove it owns a directory before removal.
TEST_CACHE_OWNER_MARKER = ".ffmwiz_test_cache_owner"


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
