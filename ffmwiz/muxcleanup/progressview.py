# Part of the FFmWiz Stream Cleanup Remux subsystem.
"""A multi-row progress block that repaints in place.

Adapted from EVdlc's `utils/progress.py` / `utils/progress_render.py`: the same
`Overall` line, `File NN:` rows, bar/percent/size/speed/ETA/elapsed layout,
sticky in-place repaint and viewport windowing. What was dropped on the way in:
the optional `rich` backend and the background ticker thread (MuxCls has no
third-party dependencies and processes one file at a time, so the poll loop that
already watches the child process drives the frames), and the byte-level stall
detection its network transfers needed.

The block is only drawn on a real terminal. With redirected output there is no
cursor to move, so the caller keeps printing one line per file instead - which
is what a log or a CI transcript wants to read anyway.
"""
from __future__ import annotations

import shutil
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .colors import ANSI_PATTERN, C, color
from .textutil import format_elapsed_time, format_stream_size, set_block_owns_screen

QUEUED = "queued"
ACTIVE = "active"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"

MAX_FRAMES_PER_SECOND = 8.0



def visible_length(text: str) -> int:
    return len(ANSI_PATTERN.sub("", text))


def truncate_visible(text: str, limit: int) -> str:
    """Cut a coloured line to `limit` printable characters.

    Counting the raw string instead would count every escape sequence as
    visible text and chop the line many columns early - a `Done` row lost its
    elapsed time that way.
    """
    if visible_length(text) <= limit:
        return text
    out: List[str] = []
    shown = 0
    index = 0
    while index < len(text) and shown < limit:
        match = ANSI_PATTERN.match(text, index)
        if match:
            out.append(match.group(0))
            index = match.end()
            continue
        out.append(text[index])
        shown += 1
        index += 1
    out.append(C.RESET)
    return "".join(out)


def supports_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", "") or ""
    try:
        "━─".encode(encoding)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def is_terminal() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def terminal_size() -> Tuple[int, int]:
    try:
        size = shutil.get_terminal_size((100, 24))
        return max(40, size.columns - 1), max(10, size.lines)
    except OSError:
        return 99, 24


def format_eta(seconds: Optional[float]) -> str:
    # Anything past a day is not a countdown any more, it is a guess.
    if seconds is None or seconds < 0 or seconds > 86400:
        return "--:--"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


@dataclass
class ProgressRow:
    """One file's line in the block."""

    name: str
    total: Optional[int] = None
    completed: int = 0
    percent: Optional[float] = None
    state: str = QUEUED
    detail: str = ""
    started_at: Optional[float] = None
    elapsed: float = 0.0

    @property
    def ratio(self) -> Optional[float]:
        """0..1 when known. An explicit percent wins: FFmpeg reports how much of
        the timeline it has written, which a copy's byte count cannot express."""
        if self.percent is not None:
            return max(0.0, min(1.0, self.percent / 100.0))
        if self.total:
            return max(0.0, min(1.0, self.completed / self.total))
        return None

    @property
    def shown_bytes(self) -> int:
        """What the size column reports. Robocopy reports a percentage and no
        byte count, so without deriving this the bar would move while the size
        beside it sat at 0 B for the whole copy."""
        if self.completed:
            return self.completed
        return int((self.ratio or 0.0) * (self.total or 0))

    def live_elapsed(self) -> float:
        if self.state == ACTIVE and self.started_at is not None:
            return time.perf_counter() - self.started_at
        return self.elapsed


def dash() -> str:
    """The placeholder a queued row shows where a live one shows a number. An
    em-dash is not encodable in every console code page, and one unencodable
    character turns the whole row into replacement glyphs."""
    return "—" if supports_unicode() else "-"


def bar_width_for(width: int) -> int:
    """Leave room for percent, size pair, state/ETA, Elapsed and the size change.

    EVdlc reserves 80 columns for the same layout. This one also ends a finished
    row with what the file gained or lost (` | -31.2 MB`), so it holds back
    another dozen - otherwise the truncation that keeps rows inside the terminal
    would eat exactly the figure that was just added.
    """
    return max(14, min(28, width - 90))


def bar(ratio: Optional[float], width: int, state: str) -> str:
    full_char, empty_char = ("━", "─") if supports_unicode() else ("=", "-")
    width = max(1, width)
    if ratio is None:
        # Unknown progress is drawn as an empty track, never as 0% - a copy that
        # cannot report bytes is not a copy that has done nothing.
        return color(empty_char * width, C.BAR_TRACK)
    filled = int(ratio * width)
    fill_color = C.BAR_FAIL if state == FAILED else C.BAR_FILL
    return color(full_char * filled, fill_color) + color(empty_char * (width - filled), C.BAR_TRACK)


def size_pair(completed: Optional[int], total: Optional[int]) -> str:
    """`done / total`, the shape EVdlc's rows use. Showing only the total leaves
    the reader no idea how much of it has actually happened."""
    done_text = format_stream_size(completed) if completed else "0 B"
    total_text = format_stream_size(total) if total else "?"
    return f"{done_text} / {total_text}"


def eta_seconds(ratio: Optional[float], started_at: Optional[float]) -> Optional[float]:
    """Project the remaining time from how long this row took to reach `ratio`.

    There is no byte rate to divide by: a remux reports a position on the
    timeline, not bytes, so the same estimator has to serve both paths.
    """
    if ratio is None or not (0 < ratio < 1) or started_at is None:
        return None
    spent = time.perf_counter() - started_at
    return spent / ratio - spent if spent > 0 else None


def stats_line(row: ProgressRow, width: int, show_elapsed: bool = True) -> str:
    """`bar percent | done / total | state-or-ETA | Elapsed hh:mm:ss`."""
    bar_width = bar_width_for(width)
    ratio = row.ratio

    if row.state == QUEUED:
        return (
            f"{bar(None, bar_width, QUEUED)} "
            f"{color('Queued', C.PROGRESS_MUTED)} | "
            f"{color(format_stream_size(row.total) if row.total else '?', C.PROGRESS_MUTED)} | "
            f"{color(dash(), C.PROGRESS_MUTED)} | "
            f"{color('ETA', C.PROGRESS_ETA_LABEL)} {color(dash(), C.PROGRESS_MUTED)}"
        )

    percent_text = f"{ratio * 100:5.1f}%" if ratio is not None else " --.-%"
    line = (
        f"{bar(ratio, bar_width, row.state)} "
        f"{color(percent_text, C.PROGRESS_PERCENT)} | "
        f"{color(size_pair(row.shown_bytes, row.total), C.PROGRESS_SIZE)}"
    )

    if row.state in (DONE, FAILED, SKIPPED):
        # The finished word takes the column a live row uses for its countdown;
        # "ETA Done" would be a label with nothing behind it.
        word, tint = {DONE: ("Done", C.PROGRESS_DONE_WORD),
                      FAILED: ("Failed", C.BAR_FAIL),
                      SKIPPED: ("Skipped", C.PROGRESS_ETA_LABEL)}[row.state]
        line += f" | {color(word, tint)}"
    else:
        remaining = eta_seconds(ratio, row.started_at)
        line += (f" | {color('ETA', C.PROGRESS_ETA_LABEL)} "
                 f"{color(format_eta(remaining), C.PROGRESS_ETA_VALUE)}")

    if show_elapsed:
        line += (f" | {color('Elapsed', C.PROGRESS_ELAPSED)} "
                 f"{color(format_elapsed_time(row.live_elapsed()), C.PROGRESS_ELAPSED)}")

    # What the file gained or lost, last on the row. It goes after Elapsed
    # rather than beside the state word: in that earlier position it sat where a
    # live row shows its countdown and read as the transfer-speed column that
    # was removed. At the end it is plainly what it is - the result of the file.
    if row.detail and row.state in (DONE, FAILED, SKIPPED):
        tint = C.SUMMARY_SIZE_DIFF if row.state == DONE else C.PROGRESS_MUTED
        line += f" | {color(row.detail, tint)}"
    return line


class ProgressView:
    """The whole block: an Overall line, one group per file, and a status line.

    Frames are driven by the caller's existing poll loop; there is no thread.
    """

    def __init__(self, rows: List[ProgressRow], enabled: Optional[bool] = None) -> None:
        self.rows = rows
        self.enabled = is_terminal() if enabled is None else enabled
        self.status = ""
        self._started_at = time.perf_counter()
        self._lines_drawn = 0
        self._last_frame = 0.0
        self._min_interval = 1.0 / MAX_FRAMES_PER_SECOND
        self._cursor_hidden = False
        # Claim the screen so nothing else writes underneath the block.
        set_block_owns_screen(self.enabled)

    # -- row lifecycle ----------------------------------------------------
    def start(self, index: int, status: str = "") -> None:
        row = self.rows[index]
        row.state = ACTIVE
        row.started_at = time.perf_counter()
        if status:
            self.status = status
        self.render(force=True)

    def update(self, index: int, percent: Optional[float] = None,
               completed: Optional[int] = None) -> None:
        row = self.rows[index]
        if percent is not None:
            row.percent = percent
        if completed is not None:
            row.completed = completed
        self.render()

    def finish(self, index: int, state: str, detail: str = "") -> None:
        row = self.rows[index]
        # Freeze the elapsed time BEFORE the state changes. live_elapsed() only
        # measures from started_at while the row is ACTIVE, so setting the state
        # first made every finished row report 00:00:00.
        row.elapsed = row.live_elapsed()
        row.state = state
        row.detail = detail
        row.started_at = None
        if state == DONE:
            row.percent = 100.0
            if row.total:
                row.completed = row.total
        self.render(force=True)

    # -- drawing ----------------------------------------------------------
    def overall_line(self, width: int) -> str:
        finished = sum(1 for row in self.rows if row.state in (DONE, FAILED, SKIPPED))
        total_files = len(self.rows)
        ratio = finished / total_files if total_files else 0.0
        # The in-flight file contributes its own fraction, so the overall bar
        # advances during a long file instead of standing still until it ends.
        active = next((r for r in self.rows if r.state == ACTIVE), None)
        if active is not None and active.ratio is not None and total_files:
            ratio += active.ratio / total_files

        total_bytes = sum(row.total or 0 for row in self.rows)
        done_bytes = sum(row.shown_bytes for row in self.rows)
        bar_width = bar_width_for(width)
        elapsed = time.perf_counter() - self._started_at
        remaining = eta_seconds(ratio if 0 < ratio < 1 else None, self._started_at)

        return (
            f"{bar(ratio, bar_width, ACTIVE)} "
            f"{color(f'{ratio * 100:5.1f}%', C.PROGRESS_PERCENT)} | "
            f"{color(size_pair(done_bytes, total_bytes), C.PROGRESS_SIZE)} | "
            f"{color(f'{finished}/{total_files} files', C.PROGRESS_OVERALL)} | "
            f"{color('ETA', C.PROGRESS_ETA_LABEL)} {color(format_eta(remaining), C.PROGRESS_ETA_VALUE)} | "
            f"{color('Elapsed', C.PROGRESS_ELAPSED)} "
            f"{color(format_elapsed_time(elapsed), C.PROGRESS_ELAPSED)}"
        )

    def compose(self, width: int, height: int) -> List[str]:
        # Overall is its own labelled group, exactly like a file group, so the
        # two read as the same kind of thing.
        lines = [color("Overall", C.PROGRESS_OVERALL), self.overall_line(width), ""]

        groups: List[List[str]] = []
        for index, row in enumerate(self.rows, start=1):
            name_color = C.PROGRESS_MUTED if row.state == QUEUED else C.PROGRESS_SIZE
            groups.append([
                color(f"File {index:02d}: ", C.PROGRESS_OVERALL) + color(row.name, name_color),
                stats_line(row, width),
            ])

        # Keep the whole block inside the viewport so it repaints without
        # scrolling; when it cannot fit, follow the active file and say how many
        # rows are hidden rather than letting Overall scroll away.
        budget = max(4, height - 5)
        if sum(len(g) for g in groups) <= budget:
            for group in groups:
                lines.extend(group)
        else:
            first_active = next(
                (i for i, r in enumerate(self.rows) if r.state in (QUEUED, ACTIVE)),
                max(0, len(groups) - 1),
            )
            shown: List[int] = []
            used = 0
            cursor = first_active
            while cursor < len(groups) and used + len(groups[cursor]) <= budget - 2:
                shown.append(cursor)
                used += len(groups[cursor])
                cursor += 1
            back = first_active - 1
            while back >= 0 and used + len(groups[back]) <= budget - 2:
                shown.insert(0, back)
                used += len(groups[back])
                back -= 1
            if not shown:
                shown = [first_active]
            if shown[0] > 0:
                lines.append(color(f"... {shown[0]} file(s) above", C.PROGRESS_MUTED))
            for i in shown:
                lines.extend(groups[i])
            hidden_below = len(groups) - 1 - shown[-1]
            if hidden_below > 0:
                lines.append(color(f"... +{hidden_below} more file(s) below", C.PROGRESS_MUTED))

        if self.status:
            lines.append(color(f"Status: {self.status}", C.PROGRESS_MUTED))
        return lines

    def render(self, force: bool = False) -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        if not force and now - self._last_frame < self._min_interval:
            return
        self._last_frame = now

        width, height = terminal_size()
        lines = [truncate_visible(line, width) for line in self.compose(width, height)]

        buffer: List[str] = []
        if not self._cursor_hidden:
            buffer.append("\x1b[?25l")
            self._cursor_hidden = True

        max_visible = max(2, height - 1)
        oversized = len(lines) > max_visible
        if self._lines_drawn:
            visible = lines[-max_visible:] if oversized else lines
            buffer.append(f"\x1b[{self._lines_drawn}F\x1b[J")
            buffer.append("\n".join(visible) + "\n")
            self._lines_drawn = len(visible)
        else:
            buffer.append("\n".join(lines) + "\n")
            self._lines_drawn = max_visible if oversized else len(lines)

        sys.stdout.write("".join(buffer))
        sys.stdout.flush()

    def close(self) -> None:
        # Released even when disabled, so a redirected run cannot leave the flag
        # set for whatever draws next.
        set_block_owns_screen(False)
        if not self.enabled:
            return
        self.status = ""
        self.render(force=True)
        if self._cursor_hidden:
            sys.stdout.write("\x1b[?25h")
            self._cursor_hidden = False
        sys.stdout.flush()
        self._lines_drawn = 0
