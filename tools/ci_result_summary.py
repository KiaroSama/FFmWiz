#!/usr/bin/env python3
"""Restate a run's test result where an artifact quota cannot evict it.

`tests/run_suite.py --json` writes the only machine-readable record of what ran,
what skipped and why, and CI uploads it as an artifact. That upload is
best-effort: the account's artifact storage quota is an EXTERNAL condition, and
when it is full `actions/upload-artifact` fails the step. `continue-on-error`
stops that from turning a green suite red, but it does not bring the evidence
back -- the job then reports a verdict with nothing behind it.

So the essentials are written twice: to the job log, and to the run's step
summary page, neither of which the quota can take away. A truncated summary of a
real run beats a complete artifact that was never stored.

    python tools/ci_result_summary.py <results.json> [label]

Exit status is always 0. This step reports; it never decides. The suite's own
exit code is the verdict, and a summary that failed to render must not be able
to fail a job whose tests passed.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

# A step summary has a 1 MiB ceiling and is read by a human, so the long tails
# are capped rather than dumped.
MAX_NAMES = 25
MAX_SKIP_REASONS = 12
MAX_SLOW = 5


def render(payload: dict, label: str) -> str:
    """A compact Markdown summary of one run. Pure: no I/O, no environment."""
    modules = payload.get("modules") or []
    tests = sum(int(item.get("tests") or 0) for item in modules)
    failures = [name for item in modules for name in (item.get("failures") or [])]
    errors = [name for item in modules for name in (item.get("errors") or [])]
    unexpected = [name for item in modules for name in (item.get("unexpected") or [])]
    skips = [entry for item in modules for entry in (item.get("skipped") or [])]

    verdict = str(payload.get("verdict") or "unknown")
    mark = "PASS" if verdict == "ok" else "FAIL"
    lines = [f"### {mark} - {label}" if label else f"### {mark}",
             "",
             f"- tests: **{tests}** in {payload.get('seconds', '?')}s "
             f"across {payload.get('workers', '?')} worker(s)",
             f"- failures: **{len(failures)}**, errors: **{len(errors)}**, "
             f"unexpected successes: **{len(unexpected)}**, skipped: **{len(skips)}**",
             f"- exit code: `{payload.get('exit_code', '?')}`"]

    required = payload.get("required") or []
    if required:
        lines.append(f"- required capabilities: {', '.join(sorted(map(str, required)))}")
    selection = payload.get("selection") or []
    if selection:
        lines.append(f"- selection: {', '.join(map(str, selection))}")

    for title, names in (("Failures", failures), ("Errors", errors),
                         ("Unexpected successes", unexpected)):
        if not names:
            continue
        lines += ["", f"#### {title} ({len(names)})", ""]
        lines += [f"- `{name}`" for name in names[:MAX_NAMES]]
        if len(names) > MAX_NAMES:
            lines.append(f"- ... and {len(names) - MAX_NAMES} more")

    if skips:
        # Grouped by the capability run_suite.py classified them under, because
        # "42 skips" answers nothing while "42 skips, all pyside6" answers it.
        grouped = Counter(str(entry.get("capability") or "other") for entry in skips)
        lines += ["", "#### Skips by capability", ""]
        lines += [f"- {name}: {count}" for name, count in sorted(grouped.items())]
        reasons = Counter(str(entry.get("reason") or "").strip() for entry in skips)
        lines += ["", "<details><summary>Skip reasons</summary>", ""]
        for reason, count in reasons.most_common(MAX_SKIP_REASONS):
            lines.append(f"- ({count}x) {reason or 'no reason given'}")
        if len(reasons) > MAX_SKIP_REASONS:
            lines.append(f"- ... and {len(reasons) - MAX_SKIP_REASONS} more distinct reasons")
        lines += ["", "</details>"]

    slowest = sorted(modules, key=lambda item: float(item.get("seconds") or 0),
                     reverse=True)[:MAX_SLOW]
    if slowest:
        lines += ["", "#### Slowest modules", ""]
        lines += [f"- {item.get('module')}: {item.get('seconds')}s" for item in slowest]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else None
    label = argv[2] if len(argv) > 2 else ""
    if path is None:
        text = "### No results file was named; nothing to summarise.\n"
    else:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # The suite may have died before writing, which is itself worth
            # saying out loud: silence here reads as "the run was fine".
            text = (f"### No readable test record at `{path}`\n\n"
                    f"- reason: `{exc}`\n"
                    "- the job's own exit code is the verdict; this step only reports.\n")
        else:
            text = render(payload, label)

    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            print(f"could not append to GITHUB_STEP_SUMMARY: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
