"""Temporary directories must not delete files that a live worker still owns."""
from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import weakref


def _idle(owners) -> bool:
    return all(not (owner.active_children() or owner.stopping() or owner.active_workers())
               for owner in owners)


def _finalize_directory(name: str, owners, log) -> None:
    # A background thread retains its Bridge while running. A process may
    # survive a failed stop without retaining the Bridge; keep its files then.
    if not _idle(owners):
        log("WARNING", f"Retaining worker directory {name}: an owner is still active")
        return
    try:
        shutil.rmtree(name)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log("WARNING", f"Could not finalize worker directory {name}: {exc}")


class WorkerTemporaryDirectory:
    """An explicit retryable cleanup with a writer-aware GC fallback."""

    def __init__(self, owners, log, prefix="ffmwiz_qmlrev_") -> None:
        self.name = tempfile.mkdtemp(prefix=prefix)
        self._owners = tuple(owners)
        self._finalizer = weakref.finalize(self, _finalize_directory,
                                          self.name, self._owners, log)

    def cleanup(self) -> None:
        if not _idle(self._owners):
            raise OSError(f"worker directory is still owned: {self.name}")
        if Path(self.name).exists():
            shutil.rmtree(self.name)
        self._finalizer.detach()
