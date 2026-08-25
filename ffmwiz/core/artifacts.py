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

import itertools
import logging
import shutil
from pathlib import Path
from typing import Any, Iterator

# Stdlib only, so the "no project imports" rule above still holds. The name is a
# child of the logger appio configures, so this reaches the session log file.
_LOG = logging.getLogger("ffmwiz.artifacts")

ARTIFACT_LEASE_KEY = "_artifact_lease"
EFFECTIVE_SETTINGS_KEY = "_effective_settings"
PLAN_REVISION_KEY = "_plan_revision"

# Plan revisions are numbered per process, so a map can name the plan that owns
# it and two plans can never share a number.
_PLAN_REVISIONS = itertools.count(1)


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
    "PLAN_REVISION_KEY",
    "ArtifactLease",
    "EffectiveSettings",
    "PlanRevisionError",
    "artifact_lease",
    "release_artifacts",
    "effective_settings",
    "effective_value",
    "reset_effective_settings",
    "begin_plan",
    "plan_revision",
    "require_plan_revision",
]


class PlanRevisionError(RuntimeError):
    """An effective map was used by a plan revision that does not own it."""


class EffectiveSettings(dict):
    """One plan revision's resolved values, tagged with the plan that owns them.

    A plain dict could not say WHICH plan resolved it, so a map handed to a
    later builder looked exactly like a fresh one and `resolve_video_encoder`
    happily answered with the previous plan's fallback (B13/D14). The tag is an
    attribute rather than a key so `effective_value()` and every existing
    consumer still see only real settings.

    `pending` marks a plan that was begun but has not been built yet. It is what
    lets one boundary serve both callers: a step that calls `begin_plan()` and
    then a builder gets ITS revision, while a direct or repeated builder call
    -- which has no open plan -- gets a brand-new one instead of inheriting the
    last build's resolutions.
    """

    __slots__ = ("revision", "pending")

    def __init__(self, revision: int | None = None, pending: bool = False) -> None:
        super().__init__()
        self.revision = revision
        self.pending = pending


def begin_plan(answers: dict[str, Any]) -> int:
    """Open a new plan revision on `answers`. Returns its number.

    The explicit top-level boundary: everything the LAST build resolved is
    forgotten and the new map is tagged with the new revision, so a map that
    reaches a builder from anywhere else can be recognised and refused.

    Call it on the OUTER answers dict, before any `dict(answers)` a builder
    makes -- the same rule the lease has, and for the same reason.
    """
    return _install_plan(answers, pending=True)


def _install_plan(answers: dict[str, Any], *, pending: bool) -> int:
    revision = next(_PLAN_REVISIONS)
    answers[EFFECTIVE_SETTINGS_KEY] = EffectiveSettings(revision, pending=pending)
    answers[PLAN_REVISION_KEY] = revision
    return revision


def plan_revision(answers: dict[str, Any]) -> int | None:
    """The plan revision `answers` belongs to, or None if none was begun."""
    revision = answers.get(PLAN_REVISION_KEY)
    return revision if isinstance(revision, int) else None


def require_plan_revision(answers: dict[str, Any]) -> int:
    """The revision this build resolves into. Every PUBLIC builder calls it first.

    `reset_effective_settings()` was called by the interactive `step_start_now()`
    and once per Folder item, but a public builder could still be called with an
    older map -- directly, or a second time after Back -- and silently resolve
    into it. Measured: a request of `copy` carrying a previous plan's effective
    `libx265` built `-c:v libx265` (D14).

    So the builder, not its callers, owns the boundary:

    - an open plan (the caller just called `begin_plan()`) is consumed here and
      keeps its map, so a step can resolve into the same revision it began;
    - anything else -- no plan, or a plan already built -- gets a NEW revision
      with an empty map, so no resolution can outlive the plan that made it;
    - a map tagged with a DIFFERENT revision than the dict claims means two
      plans were spliced together, which no correct caller does, so it raises.

    Helper builders that work on `dict(answers)` must NOT call this: they have
    to keep writing into the outer map, which is how the summary learns what
    the command really does.
    """
    resolved = answers.get(EFFECTIVE_SETTINGS_KEY)
    revision = plan_revision(answers)
    if isinstance(resolved, EffectiveSettings) and revision is not None:
        if resolved.revision != revision:
            raise PlanRevisionError(
                f"effective settings belong to plan revision {resolved.revision!r}, "
                f"but the answers claim revision {revision!r}")
        if resolved.pending:
            resolved.pending = False
            return revision
    # Not pending: this call IS the build, so the new plan is consumed at once.
    # Leaving it open let the NEXT build inherit this one's resolutions, which
    # is the leak the boundary exists to close.
    return _install_plan(answers, pending=False)


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
        resolved = EffectiveSettings()
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

    Same operation as `begin_plan()`, which is the name to prefer: this one
    kept its own map untagged, and an untagged map on a dict that still claimed
    an older revision is exactly what `require_plan_revision()` refuses. One
    implementation, so the two spellings cannot drift apart.
    """
    begin_plan(answers)
    return answers[EFFECTIVE_SETTINGS_KEY]


def effective_value(answers: dict[str, Any], name: str, default: Any = None) -> Any:
    """The resolved value for `name`, falling back to the requested one."""
    resolved = answers.get(EFFECTIVE_SETTINGS_KEY)
    if isinstance(resolved, dict) and name in resolved:
        return resolved[name]
    return answers.get(name, default)
