"""State that must survive a shallow-copied answers dict.

Two problems, one mechanism. Builders work on `join_answers = dict(answers)`,
so anything they record as a KEY on that copy is invisible to the outer
executor: temporary files leaked because their cleanup path died with the copy
(R06), and resolved codecs were lost the same way, leaving the summary showing
what the user REQUESTED rather than what the command actually does (R10).

`dict()` copies keys but shares mutable VALUES, so a container object placed in
answers before the copy is the same object on both sides.

Builders routinely work on a SHALLOW COPY of the answers dict
(`join_answers = dict(answers)`), and a cleanup key written onto that copy never
reaches the outer executor -- which pops from the original. The joined-subtitle
temp directory leaked on every run for exactly that reason: the build created
`ffmwiz_join_subs_*`, stored the path on the copy, and the executor's cleanup
found nothing to remove.

A lease sidesteps the whole class of bug. `dict(answers)` copies the KEY but
shares the OBJECT, so an artifact registered through any copy is still owned by
the original, however many times the dict was shallow-copied on the way down.

Deliberately dependency-free (no logging, no project imports) so it can be used
from any tier, including the level-0 helpers.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Iterator

# Stdlib only, so the "no project imports" rule above still holds. The name is a
# child of the logger appio configures, so this reaches the session log file.
_LOG = logging.getLogger("ffmwiz.artifacts")

ARTIFACT_LEASE_KEY = "_artifact_lease"
EFFECTIVE_SETTINGS_KEY = "_effective_settings"


class ArtifactLease:
    """Every temporary path one job created, and the single place they die.

    `release()` is idempotent and removes only paths this lease was told about,
    so it is safe to call from a `finally` that may run more than once and can
    never touch a file the process did not create.
    """

    def __init__(self) -> None:
        self._paths: list[Path] = []

    def register(self, path: Any) -> Path:
        """Take ownership of `path` and return it, so callers can inline this."""
        resolved = Path(path)
        if resolved not in self._paths:
            self._paths.append(resolved)
        return resolved

    def forget(self, path: Any) -> None:
        """Give up ownership without deleting.

        Used when an artifact is deliberately handed to the user -- a command
        printed for manual execution still needs its generated inputs.
        """
        resolved = Path(path)
        self._paths = [item for item in self._paths if item != resolved]

    def release(self) -> list[Path]:
        """Delete everything owned. Returns the paths that really went away.

        Ownership is given up only once the path is GONE. `rmtree(ignore_errors)`
        swallows a Windows sharing violation and returns normally, so the old
        code reported a surviving directory as removed and dropped it from the
        lease: with one file inside it held open, `release()` answered
        "removed", the directory was still there, and the retry the comment
        below promises could never happen because nothing was left to retry.
        """
        removed: list[Path] = []
        for path in list(self._paths):
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.exists():
                    path.unlink()
            except OSError:
                pass
            if path.exists():
                # A locked file must not stop the rest of the cleanup; it stays
                # owned so a later release can retry it.
                _LOG.warning("Leased temporary artifact %s could not be removed; "
                             "it stays owned so a later release can retry it", path)
                continue
            removed.append(path)
            self._paths = [item for item in self._paths if item != path]
        return removed

    def __len__(self) -> int:
        return len(self._paths)

    def __iter__(self) -> Iterator[Path]:
        return iter(list(self._paths))

    def __contains__(self, path: Any) -> bool:
        return Path(path) in self._paths


def artifact_lease(answers: dict[str, Any]) -> ArtifactLease:
    """The lease owning this job's temporary files, created on first use.

    Call it with whichever answers dict is in hand -- original or shallow copy.

    ONE RULE: open the lease on the outer dict BEFORE any shallow copy is made.
    `dict()` shares the object only if the key is already there; copy first and
    the copy silently gets a lease of its own, which is the exact bug this class
    exists to prevent.
    """
    lease = answers.get(ARTIFACT_LEASE_KEY)
    if not isinstance(lease, ArtifactLease):
        lease = ArtifactLease()
        answers[ARTIFACT_LEASE_KEY] = lease
    return lease


def release_artifacts(answers: dict[str, Any]) -> list[Path]:
    """Release this job's lease if it has one. Safe to call unconditionally."""
    lease = answers.get(ARTIFACT_LEASE_KEY)
    if isinstance(lease, ArtifactLease):
        return lease.release()
    return []


__all__ = [
    "ARTIFACT_LEASE_KEY",
    "EFFECTIVE_SETTINGS_KEY",
    "ArtifactLease",
    "artifact_lease",
    "release_artifacts",
    "effective_settings",
    "effective_value",
    "reset_effective_settings",
]


def effective_settings(answers: dict[str, Any]) -> dict[str, Any]:
    """What the build actually resolved, as opposed to what was requested.

    Same sharing rule as the lease: open it on the outer dict before any
    shallow copy, and a builder working on the copy still reaches it.

    Kept separate from the requested values on purpose -- Back/reopen has to
    show the user their own choice, while summaries, logs and "what will this
    command do?" must show the resolved one.
    """
    resolved = answers.get(EFFECTIVE_SETTINGS_KEY)
    if not isinstance(resolved, dict):
        resolved = {}
        answers[EFFECTIVE_SETTINGS_KEY] = resolved
    return resolved


def reset_effective_settings(answers: dict[str, Any]) -> dict[str, Any]:
    """Begin a new plan revision: forget everything the LAST build resolved.

    The lease and the effective map share one mechanism but not one lifetime.
    A lease has to outlive the copies so its temporary files can still be
    deleted; a RESOLUTION is only true for the plan that produced it. Nothing
    ever ended that plan, so the map behaved like session state: request
    copy/copy, build a Join (which legitimately resolves to libx265/aac), press
    Back, drop the Join and rebuild an ordinary single-input job -- and
    `resolve_video_encoder` still answered libx265, so a job the user asked to
    stream-copy was re-encoded to HEVC (B13).

    Call it on the OUTER answers dict at the START of a build, before any
    `dict(answers)` a builder makes. A FRESH map is installed rather than the
    old one cleared, so copies taken for an EARLIER plan -- a folder
    representative, a previous revision -- keep their own and cannot write into
    this one.
    """
    resolved: dict[str, Any] = {}
    answers[EFFECTIVE_SETTINGS_KEY] = resolved
    return resolved


def effective_value(answers: dict[str, Any], name: str, default: Any = None) -> Any:
    """The resolved value for `name`, falling back to the requested one."""
    resolved = answers.get(EFFECTIVE_SETTINGS_KEY)
    if isinstance(resolved, dict) and name in resolved:
        return resolved[name]
    return answers.get(name, default)
