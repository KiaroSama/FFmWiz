"""Timeline painting and pointer interaction for the legacy Tk Cut Editor.

Split out of `guibridge_cut_tk.py` for file size. That module still owns the
window, the widgets, the preview scheduler and the playback chain; this one
owns the two pieces that only read and edit the shared `state` dict and paint
the timeline canvas.

Every variable the nested versions captured is an explicit parameter now.
`state` is the builder's own dict, passed by reference on purpose: the drag
handlers mutate it in place (`state["drag_target"]`, `state["cut_ranges"]`,
`state["selected_cut"]`) exactly as the closures did, and the builder reads
those same keys afterwards. Nothing passed here is ever REBOUND by the builder
after the call, so there is no late-bound widget to marshal -- `timeline` and
`palette` are created before either function is wired up.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from ffmwiz.core.timeline import normalize_cut_ranges, seconds_to_ffmpeg_time


def draw_cut_timeline(
    timeline: Any,
    palette: Any,
    state: dict[str, Any],
    width: int,
    height: int,
    time_to_x: Callable[[float], int],
    view_start: float,
    view_span: float,
    view_end: float,
) -> None:
    """Repaint the whole timeline canvas: ticks, cut boxes, markers, playhead.

    `width`/`height` are the measured canvas size and `view_*` the current zoom
    window; the nested version called `timeline_width()` / `_view_span()` for
    them, which is the same value read one frame earlier.
    """
    timeline.delete("all")
    # Larger, more breathable layout than the original.
    label_strip_top = 6
    label_strip_bottom = 24
    track_top = 30
    track_bottom = height - 18
    track_mid = (track_top + track_bottom) // 2
    pad = 14
    # Frame around the timeline so it visually reads as a panel.
    timeline.create_rectangle(
        0, 0, width, height,
        fill=palette.TIMELINE_BG, outline="",
    )
    timeline.create_rectangle(
        pad - 2, track_top, width - pad + 2, track_bottom,
        fill=palette.TIMELINE_TRACK, outline=palette.BORDER, width=1,
    )
    # Choose a tick step that yields ~7-10 labels regardless of zoom.
    span = view_span
    approx_step = span / 8.0
    exponent = math.floor(math.log10(max(approx_step, 0.001)))
    base = 10 ** exponent
    step = base
    for candidate in (1, 2, 5, 10):
        step = candidate * base
        if span / step <= 10:
            break
    start = view_start
    end = view_end
    first_tick = math.ceil(start / step) * step
    t = first_tick
    tick_font = ("Segoe UI Semibold", 10)
    sub_font = ("Segoe UI", 8)
    while t <= end + 1e-6:
        x_pos = time_to_x(t)
        if pad <= x_pos <= width - pad:
            timeline.create_line(
                x_pos, track_top - 6, x_pos, track_top,
                fill=palette.TIMELINE_TICK_HI, width=1,
            )
            timeline.create_text(
                x_pos, label_strip_top + (label_strip_bottom - label_strip_top) // 2,
                text=seconds_to_ffmpeg_time(t),
                fill=palette.TIMELINE_TICK_HI,
                font=tick_font,
            )
        t += step
    # Secondary minor ticks (no label) at step / 5.
    minor_step = step / 5
    if minor_step > 0:
        t = math.ceil(start / minor_step) * minor_step
        while t <= end + 1e-6:
            x_pos = time_to_x(t)
            if pad <= x_pos <= width - pad:
                timeline.create_line(
                    x_pos, track_top - 3, x_pos, track_top,
                    fill=palette.TIMELINE_TICK, width=1,
                )
            t += minor_step
    # Removed cut ranges (red boxes).
    for idx, (cstart, cend) in enumerate(state["cut_ranges"]):
        if cend < start or cstart > end:
            continue
        x1 = time_to_x(max(cstart, start))
        x2 = time_to_x(min(cend, end))
        fill = palette.ACCENT_RED if idx == state["selected_cut"] else palette.ACCENT_RED_DK
        timeline.create_rectangle(
            x1, track_top + 3, x2, track_bottom - 3,
            fill=fill, outline=palette.BORDER, width=1, tags=("cut", str(idx)),
        )
        if x2 - x1 > 36:
            timeline.create_text(
                (x1 + x2) // 2,
                track_mid,
                text=f"#{idx + 1}",
                fill=palette.TEXT,
                font=("Segoe UI Semibold", 10),
                tags=("cut", str(idx)),
            )
    # In / Out markers.
    in_x = time_to_x(state["in_marker"])
    out_x = time_to_x(state["out_marker"])
    marker_font = ("Segoe UI Semibold", 9)
    if pad - 8 <= in_x <= width - pad + 8:
        timeline.create_line(in_x, track_top - 4, in_x, track_bottom + 4,
                             fill=palette.ACCENT_GREEN, width=3, tags=("marker", "in"))
        timeline.create_polygon(
            in_x, track_top - 4, in_x - 8, track_top - 14, in_x + 8, track_top - 14,
            fill=palette.ACCENT_GREEN, outline=palette.BG, tags=("marker", "in"),
        )
        timeline.create_text(in_x + 12, track_top - 9, anchor="w",
                             text="IN", fill=palette.ACCENT_GREEN, font=marker_font)
    if pad - 8 <= out_x <= width - pad + 8:
        timeline.create_line(out_x, track_top - 4, out_x, track_bottom + 4,
                             fill=palette.ACCENT_YELLOW, width=3, tags=("marker", "out"))
        timeline.create_polygon(
            out_x, track_top - 4, out_x - 8, track_top - 14, out_x + 8, track_top - 14,
            fill=palette.ACCENT_YELLOW, outline=palette.BG, tags=("marker", "out"),
        )
        timeline.create_text(out_x - 12, track_top - 9, anchor="e",
                             text="OUT", fill=palette.ACCENT_YELLOW, font=marker_font)
    # Playhead.
    ph_x = time_to_x(state["timestamp"])
    if pad - 6 <= ph_x <= width - pad + 6:
        timeline.create_line(ph_x, track_top - 10, ph_x, track_bottom + 10,
                             fill=palette.PLAYHEAD, width=2, tags=("playhead",))
        timeline.create_polygon(
            ph_x - 7, track_bottom + 4,
            ph_x + 7, track_bottom + 4,
            ph_x, track_bottom + 14,
            fill=palette.PLAYHEAD, outline=palette.BG, tags=("playhead",),
        )
        timeline.create_text(
            ph_x, label_strip_bottom - 4,
            text=seconds_to_ffmpeg_time(state["timestamp"]),
            fill=palette.PLAYHEAD,
            font=sub_font,
            tags=("playhead",),
        )


def make_timeline_drag_handlers(
    timeline: Any,
    state: dict[str, Any],
    duration: float,
    x_to_time: Callable[[float], float],
    set_time: Callable[[float], None],
    refresh_cut_listbox: Callable[[], None],
    redraw_all: Callable[[], None],
) -> tuple[Callable[[Any], None], Callable[[Any], None], Callable[[Any], None]]:
    """Build the three `<Button-1>` handlers for the timeline canvas.

    Returned as a triple in binding order (press, motion, release). They stay
    closures because Tk binds them as one-argument callbacks; what changed is
    that the seven names they close over arrive as parameters instead of being
    picked up from the builder's frame.
    """
    def begin_timeline_drag(event: Any) -> None:
        items = timeline.find_overlapping(event.x - 4, event.y - 4, event.x + 4, event.y + 4)
        target = None
        for item_id in reversed(items):
            tags = timeline.gettags(item_id)
            if "marker" in tags and "in" in tags:
                target = ("in",)
                break
            if "marker" in tags and "out" in tags:
                target = ("out",)
                break
            if "playhead" in tags:
                target = ("playhead",)
                break
            if "cut" in tags:
                idx = int(tags[tags.index("cut") + 1])
                state["selected_cut"] = idx
                refresh_cut_listbox()
                target = ("cut", idx)
                break
        if target is None:
            set_time(x_to_time(event.x))
            target = ("playhead",)
        state["drag_target"] = target

    def drag_timeline(event: Any) -> None:
        target = state.get("drag_target")
        if not target:
            return
        t = x_to_time(event.x)
        if target[0] == "playhead":
            set_time(t)
        elif target[0] == "in":
            state["in_marker"] = max(0.0, min(duration, t))
            if state["out_marker"] < state["in_marker"]:
                state["out_marker"] = state["in_marker"]
            redraw_all()
        elif target[0] == "out":
            state["out_marker"] = max(0.0, min(duration, t))
            if state["in_marker"] > state["out_marker"]:
                state["in_marker"] = state["out_marker"]
            redraw_all()
        elif target[0] == "cut":
            idx = target[1]
            if 0 <= idx < len(state["cut_ranges"]):
                start, end = state["cut_ranges"][idx]
                span = end - start
                new_start = max(0.0, min(duration - span, t - span / 2))
                state["cut_ranges"][idx] = (new_start, new_start + span)
                redraw_all()

    def end_timeline_drag(_event: Any) -> None:
        if state.get("drag_target") and state["drag_target"][0] == "cut":
            state["cut_ranges"] = list(normalize_cut_ranges(state["cut_ranges"], duration))
        state["drag_target"] = None

    return begin_timeline_drag, drag_timeline, end_timeline_drag


__all__ = [
    'draw_cut_timeline',
    'make_timeline_drag_handlers',
]
