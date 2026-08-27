"""FFmWiz guibridge cluster (extracted from FFmWiz.py, method الف)."""
from __future__ import annotations

import os
import sys
import re
import math
import json
import time
import shutil
import subprocess
import tempfile
import platform
import datetime
import uuid
import hashlib
import html
import csv
import logging
import atexit
import queue
import threading
import concurrent.futures
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from ffmwiz.core.constants import *  # noqa: F401,F403
from ffmwiz.core.colors import *  # noqa: F401,F403
from ffmwiz.core.exceptions import *  # noqa: F401,F403
from ffmwiz.core.timeline import *  # noqa: F401,F403
from ffmwiz.support.L00_audio import *  # noqa: F401,F403
from ffmwiz.support.L00_color_range import *  # noqa: F401,F403
from ffmwiz.support.L00_encode_opts import *  # noqa: F401,F403
from ffmwiz.support.L00_filters import *  # noqa: F401,F403
from ffmwiz.support.L00_metadata import *  # noqa: F401,F403
from ffmwiz.support.L00_misc import *  # noqa: F401,F403
from ffmwiz.support.L00_naming import *  # noqa: F401,F403
from ffmwiz.support.L00_paths import *  # noqa: F401,F403
from ffmwiz.support.L00_probe import *  # noqa: F401,F403
from ffmwiz.support.L00_split import *  # noqa: F401,F403
from ffmwiz.support.L00_streams import *  # noqa: F401,F403
from ffmwiz.support.L00_text import *  # noqa: F401,F403
from ffmwiz.support.L01_audio import *  # noqa: F401,F403
from ffmwiz.support.L01_color_range import *  # noqa: F401,F403
from ffmwiz.support.L01_encode_opts import *  # noqa: F401,F403
from ffmwiz.support.L01_filters import *  # noqa: F401,F403
from ffmwiz.support.L01_metadata import *  # noqa: F401,F403
from ffmwiz.support.L01_misc import *  # noqa: F401,F403
from ffmwiz.support.L01_naming import *  # noqa: F401,F403
from ffmwiz.support.L01_paths import *  # noqa: F401,F403
from ffmwiz.support.L01_split import *  # noqa: F401,F403
from ffmwiz.support.L01_streams import *  # noqa: F401,F403
from ffmwiz.support.L01_text import *  # noqa: F401,F403
from ffmwiz.support.L02 import *  # noqa: F401,F403
from ffmwiz.support.L03 import *  # noqa: F401,F403
from ffmwiz.support.L04 import *  # noqa: F401,F403
from ffmwiz.support.L05 import *  # noqa: F401,F403
from ffmwiz.support.L06 import *  # noqa: F401,F403
from ffmwiz.support.L07 import *  # noqa: F401,F403
from ffmwiz.support.ext00 import *  # noqa: F401,F403
from ffmwiz.support.ext01 import *  # noqa: F401,F403
from ffmwiz.support.ext02 import *  # noqa: F401,F403
from ffmwiz.support.ext03 import *  # noqa: F401,F403
from ffmwiz.support.ext04 import *  # noqa: F401,F403
from ffmwiz.support.ext05 import *  # noqa: F401,F403
from ffmwiz.support.ext06 import *  # noqa: F401,F403
from ffmwiz.support.ext07 import *  # noqa: F401,F403
from ffmwiz.support.ext08 import *  # noqa: F401,F403
from ffmwiz.support.ext09 import *  # noqa: F401,F403
from ffmwiz.support.ext10 import *  # noqa: F401,F403
from ffmwiz.support.ext11 import *  # noqa: F401,F403
from ffmwiz.support.ext12 import *  # noqa: F401,F403
from ffmwiz.appio import *  # noqa: F401,F403
from ffmwiz import appio  # noqa: F401
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz.services import *  # noqa: F401,F403

from ffmwiz import guibridge  # facade for monkeypatch-stable cross-module calls  # noqa: F401
from ffmwiz.guibridge import *  # sibling helpers  # noqa: F401,F403


def _open_legacy_cut_gui_tk(
    answers: dict[str, Any],
    fps: float,
    duration: float,
) -> list[tuple[float, float]] | None:
    """Premiere-inspired Cut Editor (legacy Tk fallback).

    Layout (top -> bottom):
        - Toolbar / header band with title and clip stats.
        - Large preview viewport.
        - "Now / In / Out / Kept / Cuts / Zoom" status strip.
        - Wide timeline with high-contrast ticks, markers, cut ranges.
        - Transport row (play/pause/seek/Mark In/Mark Out/cuts).
        - Tool row (timeline zoom buttons + audio mute + volume slider).
        - Cut-ranges-to-remove list.

    Returns the final list of keep ranges (the inverse of the cut ranges) or
    None on cancel. Cancel from this GUI does NOT abort the cut workflow;
    the caller loops back to the cut-method menu.
    """
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except Exception as exc:
        appio.error(f"Tkinter is not available, so the cut GUI cannot be opened: {exc}")
        return None

    if duration <= 0:
        appio.error("Cannot open the cut GUI: source duration is unknown.")
        return None

    palette = guibridge._UIPalette
    ffplay = shutil.which("ffplay")
    input_path: Path = answers["input_path"]

    try:
        with tempfile.TemporaryDirectory(prefix="ffmwizard_cut_") as temp_name:
            temp_dir = Path(temp_name)
            source_width, source_height = (1280, 720)
            try:
                source_width, source_height = first_video_size(answers)
            except Exception:
                pass
            preview_w, preview_h = preview_size(source_width, source_height)
            preview_w = max(320, min(960, preview_w))
            preview_h = max(180, round(preview_w * source_height / source_width))

            timeline_default_width = max(preview_w + 240, 920)
            timeline_height = 112

            state: dict[str, Any] = {
                "timestamp": 0.0,
                "in_marker": 0.0,
                "out_marker": min(5.0, duration),
                "cut_ranges": [],            # list of (start_s, end_s) REMOVE
                "selected_cut": -1,
                "playing": False,
                "audio_proc": None,
                "photo": None,
                "photo_key": None,
                "drag_target": None,         # ("playhead",) | ("in",) | ("out",) | ("cut", idx)
                # Timeline zoom: visible window expressed in seconds.
                "view_start_s": 0.0,
                "view_span_s": float(duration),
                # Audio.
                "volume": 50,
                "mute": False,
            }

            result: dict[str, list[tuple[float, float]] | None] = {"keep_ranges": None}

            root = tk.Tk()
            root.title("FFmWiz Cut Editor")
            root.configure(bg=palette.BG)
            _apply_tk_window_icon(root)
            guibridge._apply_app_ttk_theme(root)
            _apply_dark_title_bar(root)
            load_icon = _make_icon_loader(root)

            shell = ttk.Frame(root, padding=(16, 0, 16, 12))
            shell.pack(fill="both", expand=True)

            # ---- Header band ----------------------------------------------
            header = ttk.Frame(shell, style="Panel.TFrame", padding=(14, 12, 14, 12))
            header.pack(side="top", fill="x", pady=(8, 10))
            ttk.Label(
                header,
                text="FFmWiz Cut Editor",
                style="Title.TLabel",
            ).pack(side="left")
            ttk.Label(
                header,
                text=(
                    f"FPS {fps:.3f}    •    "
                    f"Duration {seconds_to_ffmpeg_time(duration)}    •    "
                    f"Source {Path(input_path).name}"
                ),
                style="Header.TLabel",
            ).pack(side="right")

            # ---- Preview viewport -----------------------------------------
            preview = tk.Canvas(
                shell,
                width=preview_w,
                height=preview_h,
                bg=palette.TIMELINE_BG,
                highlightthickness=1,
                highlightbackground=palette.BORDER,
            )
            preview.pack(side="top", fill="x", expand=False, pady=(0, 8))

            info_label = ttk.Label(shell, text="", anchor="w", style="Muted.TLabel")
            info_label.pack(side="top", fill="x", pady=(0, 4))

            # ---- Status strip (above the timeline) ------------------------
            status_strip = ttk.Frame(shell, style="Panel.TFrame", padding=(14, 8, 14, 8))
            status_strip.pack(side="top", fill="x", pady=(2, 6))
            time_label = ttk.Label(status_strip, text="", style="Header.TLabel")
            time_label.pack(side="left")

            # ---- Timeline -------------------------------------------------
            timeline_wrap = ttk.Frame(shell, style="Surface.TFrame", padding=(2, 2, 2, 2))
            timeline_wrap.pack(side="top", fill="x", expand=False, pady=(0, 8))
            timeline = tk.Canvas(
                timeline_wrap,
                width=timeline_default_width,
                height=timeline_height,
                bg=palette.TIMELINE_BG,
                highlightthickness=0,
            )
            timeline.pack(side="top", fill="x", expand=True)

            controls = ttk.Frame(shell)
            controls.pack(side="top", fill="x", pady=(2, 0))
            audio_row = ttk.Frame(shell)
            audio_row.pack(side="top", fill="x", pady=(8, 0))

            cut_list_frame = ttk.Frame(shell)
            cut_list_frame.pack(side="top", fill="both", expand=False, pady=(10, 4))
            ttk.Label(cut_list_frame, text="Cut ranges to remove:", style="Dim.TLabel").pack(side="top", anchor="w")
            cut_listbox = tk.Listbox(
                cut_list_frame,
                bg=palette.TIMELINE_BG,
                fg=palette.TEXT,
                selectbackground=palette.ACCENT_DARK,
                selectforeground=palette.TEXT,
                height=5,
                highlightthickness=1,
                highlightbackground=palette.BORDER,
                bd=0,
                activestyle="none",
                font=("Consolas", 10),
            )
            cut_listbox.pack(side="top", fill="x", expand=True)

            # --- Preview frame extraction (async, debounced) -----------------
            def _extract(timestamp: float, w: int, h: int) -> Path:
                return extract_crop_preview_frame(answers, temp_dir, w, h, timestamp)

            def _on_frame_ready(path: Path) -> None:
                try:
                    state["photo"] = tk.PhotoImage(file=str(path))
                except Exception:
                    return
                redraw_preview_canvas()

            scheduler = guibridge._PreviewScheduler(
                root,
                extract_fn=_extract,
                on_ready=_on_frame_ready,
                debounce_ms=70,
            )

            # --- Timeline math (zoomable) ------------------------------------
            def _view_start() -> float:
                return float(state["view_start_s"])

            def _view_span() -> float:
                return max(0.001, float(state["view_span_s"]))

            def _view_end() -> float:
                return _view_start() + _view_span()

            def timeline_width() -> int:
                return max(1, int(timeline.winfo_width()) or timeline_default_width)

            def time_to_x(seconds: float) -> int:
                width = timeline_width()
                pad = 12
                usable = max(1, width - pad * 2)
                start = _view_start()
                span = _view_span()
                return int(pad + ((seconds - start) / span) * usable)

            def x_to_time(x_pos: float) -> float:
                width = timeline_width()
                pad = 12
                usable = max(1, width - pad * 2)
                start = _view_start()
                span = _view_span()
                ratio = max(0.0, min(1.0, (x_pos - pad) / usable))
                return start + ratio * span

            def clamp_view() -> None:
                span = min(duration, max(0.05, _view_span()))
                start = max(0.0, min(duration - span, _view_start()))
                state["view_span_s"] = span
                state["view_start_s"] = start

            def zoom_around(focus_time: float, factor: float) -> None:
                """Zoom timeline by 'factor' (<1 zoom in, >1 zoom out), keeping
                focus_time at the same screen position."""
                width = timeline_width()
                pad = 12
                usable = max(1, width - pad * 2)
                # Current screen-x of focus_time before zooming.
                start = _view_start()
                span = _view_span()
                ratio = (focus_time - start) / max(0.001, span)
                new_span = max(0.05, min(duration, span * factor))
                new_start = focus_time - ratio * new_span
                state["view_span_s"] = new_span
                state["view_start_s"] = new_start
                clamp_view()
                redraw_all()

            def zoom_in(_event: Any = None) -> None:
                zoom_around(state["timestamp"], 0.5)

            def zoom_out(_event: Any = None) -> None:
                zoom_around(state["timestamp"], 2.0)

            def zoom_fit(_event: Any = None) -> None:
                state["view_start_s"] = 0.0
                state["view_span_s"] = duration
                redraw_all()

            def timeline_wheel(event: Any) -> str:
                # Wheel = zoom around cursor. Ctrl+wheel = finer zoom.
                step = 1.25 if (event.state & 0x0004) else 1.6
                factor = 1.0 / step if event.delta > 0 else step
                focus_time = x_to_time(event.x)
                zoom_around(focus_time, factor)
                return "break"

            # --- Rendering ---------------------------------------------------
            def refresh_cut_listbox() -> None:
                cut_listbox.delete(0, tk.END)
                for idx, (start, end) in enumerate(state["cut_ranges"]):
                    line = (
                        f"{idx + 1:2d}. {seconds_to_hmsf(start, fps)} -> "
                        f"{seconds_to_hmsf(end, fps)}   "
                        f"({seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)})"
                    )
                    cut_listbox.insert(tk.END, line)
                if 0 <= state["selected_cut"] < len(state["cut_ranges"]):
                    cut_listbox.selection_clear(0, tk.END)
                    cut_listbox.selection_set(state["selected_cut"])

            def refresh_time_label() -> None:
                kept = duration - sum(max(0.0, e - s) for s, e in state["cut_ranges"])
                zoom_pct = int(round(100.0 * duration / max(0.001, _view_span())))
                # Compact, high-contrast status strip. We use Unicode "Big dot"
                # separators so individual fields stay readable.
                time_label.configure(text=(
                    f"●  Now {seconds_to_hmsf(state['timestamp'], fps)}    "
                    f"{seconds_to_ffmpeg_time(state['timestamp'])} / "
                    f"{seconds_to_ffmpeg_time(duration)}        "
                    f"●  In {seconds_to_hmsf(state['in_marker'], fps)}    "
                    f"●  Out {seconds_to_hmsf(state['out_marker'], fps)}    "
                    f"●  Kept {format_duration(kept)}    "
                    f"●  Cuts {len(state['cut_ranges'])}    "
                    f"●  Zoom {zoom_pct}%"
                ))

            def redraw_preview_canvas() -> None:
                preview.delete("all")
                cached = scheduler.request(state["timestamp"], preview_w, preview_h)
                if cached is not None:
                    try:
                        state["photo"] = tk.PhotoImage(file=str(cached))
                    except Exception:
                        pass
                if state.get("photo") is not None:
                    preview.create_image(preview_w // 2, preview_h // 2, image=state["photo"])
                else:
                    preview.create_text(
                        preview_w // 2,
                        preview_h // 2,
                        text="(loading preview frame...)",
                        fill=palette.TEXT_MUTE,
                        font=("Segoe UI", 11),
                    )

            def redraw_timeline() -> None:
                timeline.delete("all")
                width = timeline_width()
                height = timeline_height
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
                span = _view_span()
                approx_step = span / 8.0
                exponent = math.floor(math.log10(max(approx_step, 0.001)))
                base = 10 ** exponent
                step = base
                for candidate in (1, 2, 5, 10):
                    step = candidate * base
                    if span / step <= 10:
                        break
                start = _view_start()
                end = _view_end()
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

            def redraw_all() -> None:
                redraw_preview_canvas()
                redraw_timeline()
                refresh_cut_listbox()
                refresh_time_label()

            # --- Audio playback (ffplay) -------------------------------------
            def stop_audio() -> None:
                proc = state.get("audio_proc")
                state["audio_proc"] = None
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=0.8)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass

            def start_audio() -> None:
                stop_audio()
                if not ffplay or state["mute"] or int(state["volume"]) <= 0:
                    return
                args = [
                    ffplay, "-nodisp", "-autoexit", "-loglevel", "error",
                    "-ss", f"{state['timestamp']:.3f}",
                    "-volume", str(int(state["volume"])),
                    str(input_path),
                ]
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                try:
                    state["audio_proc"] = subprocess.Popen(
                        args,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creation_flags,
                    )
                except Exception as exc:
                    appio.error(f"Could not start ffplay audio preview: {exc}")

            # One pending playback tick at a time. Pause+Play inside a single
            # tick interval used to leave the old chain pending while
            # toggle_playback scheduled a new one, so two chains each advanced
            # the timestamp 1 s per second -> a 2x playhead (NEW-GUI5).
            playback_tick_handle: dict[str, Any] = {"id": None}

            def cancel_playback_tick() -> None:
                if playback_tick_handle["id"] is not None:
                    try:
                        root.after_cancel(playback_tick_handle["id"])
                    except Exception:
                        pass
                    playback_tick_handle["id"] = None

            def schedule_playback_tick() -> None:
                cancel_playback_tick()
                playback_tick_handle["id"] = root.after(1000, playback_tick)

            def playback_tick() -> None:
                playback_tick_handle["id"] = None
                if not state["playing"]:
                    return
                state["timestamp"] = min(duration, state["timestamp"] + 1.0)
                redraw_all()
                if state["timestamp"] >= duration - 1e-6:
                    # Reached the end. Stop playback cleanly so the next Play
                    # press can rewind to the beginning (see toggle_playback).
                    state["playing"] = False
                    play_button.configure(text="▶ Play (Space)")
                    stop_audio()
                    return
                schedule_playback_tick()

            def toggle_playback(_event: Any = None) -> None:
                # Bug fix: if playback is at or near the end and the user
                # presses Play again, rewind to the start so playback runs
                # for the whole clip instead of ending after ~1 second.
                if not state["playing"] and state["timestamp"] >= duration - 0.5:
                    state["timestamp"] = 0.0
                    redraw_all()
                state["playing"] = not state["playing"]
                play_button.configure(text=("⏸ Pause (Space)" if state["playing"] else "▶ Play (Space)"))
                if state["playing"]:
                    start_audio()
                    schedule_playback_tick()
                else:
                    cancel_playback_tick()
                    stop_audio()

            def set_time(value: float) -> None:
                state["timestamp"] = max(0.0, min(duration, float(value)))
                redraw_all()
                if state["playing"]:
                    start_audio()

            def go_home(_event: Any = None) -> None:
                set_time(0.0)

            def go_end(_event: Any = None) -> None:
                set_time(duration)

            def set_in_marker(_event: Any = None) -> None:
                state["in_marker"] = state["timestamp"]
                if state["out_marker"] < state["in_marker"]:
                    state["out_marker"] = state["in_marker"]
                redraw_all()

            def set_out_marker(_event: Any = None) -> None:
                state["out_marker"] = state["timestamp"]
                if state["in_marker"] > state["out_marker"]:
                    state["in_marker"] = state["out_marker"]
                redraw_all()

            def add_cut_range(_event: Any = None) -> None:
                start = min(state["in_marker"], state["out_marker"])
                end = max(state["in_marker"], state["out_marker"])
                if end - start < 1e-3:
                    appio.error("Cut range is empty; place In and Out at different positions first.")
                    return
                state["cut_ranges"].append((start, end))
                state["cut_ranges"] = list(normalize_cut_ranges(state["cut_ranges"], duration))
                state["selected_cut"] = len(state["cut_ranges"]) - 1
                redraw_all()

            def remove_selected_cut(_event: Any = None) -> None:
                idx = state["selected_cut"]
                if 0 <= idx < len(state["cut_ranges"]):
                    del state["cut_ranges"][idx]
                    state["selected_cut"] = min(idx, len(state["cut_ranges"]) - 1)
                    redraw_all()

            def clear_all_cuts(_event: Any = None) -> None:
                state["cut_ranges"] = []
                state["selected_cut"] = -1
                redraw_all()

            def confirm(_event: Any = None) -> None:
                keep = invert_cut_ranges_to_keep_ranges(state["cut_ranges"], duration)
                if state["cut_ranges"] and not keep:
                    # Every frame is cut. An empty keep list is indistinguishable
                    # from "no cuts" downstream, which would silently export the
                    # untouched source, so refuse here instead (D13).
                    messagebox.showwarning(
                        "FFmWiz",
                        "Every frame is cut — nothing would remain. Adjust the cuts.",
                    )
                    return
                if not state["cut_ranges"]:
                    start = min(state["in_marker"], state["out_marker"])
                    end = max(state["in_marker"], state["out_marker"])
                    if end - start > 1e-3:
                        keep = [(start, end)]
                    else:
                        keep = [(0.0, duration)]
                result["keep_ranges"] = keep
                state["playing"] = False
                cancel_playback_tick()
                stop_audio()
                scheduler.cancel()
                root.destroy()

            def cancel(_event: Any = None) -> None:
                result["keep_ranges"] = None
                state["playing"] = False
                cancel_playback_tick()
                stop_audio()
                scheduler.cancel()
                root.destroy()

            def on_listbox_select(_event: Any) -> None:
                sel = cut_listbox.curselection()
                state["selected_cut"] = sel[0] if sel else -1
                redraw_timeline()

            # --- Timeline drag handlers --------------------------------------
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

            # --- Audio controls ----------------------------------------------
            volume_var = tk.IntVar(value=int(state["volume"]))
            mute_var = tk.BooleanVar(value=bool(state["mute"]))

            def volume_level_index() -> int:
                v = int(volume_var.get())
                if mute_var.get() or v <= 0:
                    return 0
                if v < 34:
                    return 1
                if v < 67:
                    return 2
                return 3

            def refresh_audio_label() -> None:
                lvl = volume_level_index()
                icon = volume_icons.get(lvl)
                mute_text = "Muted (M)" if state["mute"] else "Mute (M)"
                if icon is not None:
                    mute_btn.configure(text=mute_text, image=icon, compound="left")
                else:
                    mute_btn.configure(text=("🔇 " + mute_text if state["mute"] else "🔊 " + mute_text))
                volume_label.configure(text=f"Vol {int(volume_var.get()):3d}%")

            def on_volume_change(_value: Any = None) -> None:
                state["volume"] = max(0, min(100, int(volume_var.get())))
                if state["playing"]:
                    start_audio()
                refresh_audio_label()

            def toggle_mute(_event: Any = None) -> None:
                state["mute"] = not state["mute"]
                mute_var.set(state["mute"])
                if state["playing"]:
                    start_audio()
                refresh_audio_label()

            def volume_wheel(event: Any) -> str:
                step = 3 if (event.state & 0x0004) else 5
                if event.delta > 0:
                    new_val = min(100, int(volume_var.get()) + step)
                else:
                    new_val = max(0, int(volume_var.get()) - step)
                volume_var.set(new_val)
                on_volume_change()
                return "break"

            # --- Icons -------------------------------------------------------
            zoom_in_icon = load_icon("zoom_in")
            zoom_out_icon = load_icon("zoom_out")
            volume_icons = {
                0: load_icon("volume_0"),
                1: load_icon("volume_1"),
                2: load_icon("volume_2"),
                3: load_icon("volume_3"),
            }

            # --- Transport row (buttons) ------------------------------------
            play_button = ttk.Button(
                controls, text="▶ Play (Space)", command=toggle_playback, style="Accent.TButton",
            )
            play_button.pack(side="left")
            ttk.Button(controls, text="⏮ Home (Home)", command=go_home).pack(side="left", padx=(8, 0))
            ttk.Button(controls, text="-5s (Shift+←)", command=lambda: set_time(state["timestamp"] - 5.0)).pack(side="left", padx=(8, 0))
            ttk.Button(controls, text="-1s (←)", command=lambda: set_time(state["timestamp"] - 1.0)).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="+1s (→)", command=lambda: set_time(state["timestamp"] + 1.0)).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="+5s (Shift+→)", command=lambda: set_time(state["timestamp"] + 5.0)).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="End (End) ⏭", command=go_end).pack(side="left", padx=(8, 0))
            ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=10)
            ttk.Button(controls, text="Mark In (I)", command=set_in_marker).pack(side="left")
            ttk.Button(controls, text="Mark Out (O)", command=set_out_marker).pack(side="left", padx=(4, 0))
            ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=10)
            ttk.Button(controls, text="Add cut (A)", command=add_cut_range).pack(side="left")
            ttk.Button(controls, text="Delete cut (Del)", command=remove_selected_cut).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="Clear all", command=clear_all_cuts).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="Confirm (Enter)", command=confirm, style="Accent.TButton").pack(side="right")
            ttk.Button(controls, text="Cancel (Esc)", command=cancel, style="Danger.TButton").pack(side="right", padx=(0, 8))

            # --- Audio + zoom row -------------------------------------------
            zoom_in_btn = ttk.Button(audio_row, text="Zoom In (+)", command=zoom_in)
            if zoom_in_icon is not None:
                zoom_in_btn.configure(image=zoom_in_icon, compound="left")
            zoom_in_btn.pack(side="left")
            zoom_out_btn = ttk.Button(audio_row, text="Zoom Out (-)", command=zoom_out)
            if zoom_out_icon is not None:
                zoom_out_btn.configure(image=zoom_out_icon, compound="left")
            zoom_out_btn.pack(side="left", padx=(6, 0))
            ttk.Button(audio_row, text="Reset Zoom (Ctrl+R)", command=zoom_fit).pack(side="left", padx=(6, 0))
            ttk.Separator(audio_row, orient="vertical").pack(side="left", fill="y", padx=12)
            mute_btn = ttk.Button(audio_row, text="Mute (M)", command=toggle_mute)
            mute_btn.pack(side="left")
            volume_label = ttk.Label(audio_row, text="Vol  50%", style="Dim.TLabel")
            volume_label.pack(side="left", padx=(10, 6))
            volume_slider = ttk.Scale(
                audio_row,
                from_=0,
                to=100,
                orient="horizontal",
                variable=volume_var,
                command=on_volume_change,
                length=200,
            )
            volume_slider.pack(side="left")
            # Mouse-wheel volume support when hovering the slider OR the
            # label/mute button (so users have a clear hover target).
            for widget in (volume_slider, volume_label, mute_btn):
                widget.bind("<MouseWheel>", volume_wheel)
            if not ffplay:
                volume_slider.state(["disabled"])
                mute_btn.state(["disabled"])
                ttk.Label(audio_row, text="(ffplay not in PATH; audio preview disabled)",
                          style="Muted.TLabel").pack(side="left", padx=(10, 0))

            info_label.configure(text=(
                "Click or drag the playhead to seek. Mark In (I) and Mark Out (O), "
                "then Add cut (A). Mouse wheel over the timeline zooms around the cursor; "
                "Ctrl+wheel = finer zoom. Mouse wheel over the volume slider changes volume."
            ))

            # --- Timeline mouse bindings ------------------------------------
            timeline.bind("<ButtonPress-1>", begin_timeline_drag)
            timeline.bind("<B1-Motion>", drag_timeline)
            timeline.bind("<ButtonRelease-1>", end_timeline_drag)
            timeline.bind("<MouseWheel>", timeline_wheel)
            timeline.bind("<Configure>", lambda _e: redraw_timeline())
            cut_listbox.bind("<<ListboxSelect>>", on_listbox_select)

            # --- Layout-independent keyboard shortcuts ----------------------
            # Bindings target the physical key (Windows VK code) so they
            # still fire when the active keyboard layout is Persian or any
            # other non-Latin layout.

            def is_editing_text(event: Any) -> bool:
                widget = getattr(event, "widget", None)
                try:
                    cls = str(widget.winfo_class()) if widget is not None else ""
                except Exception:
                    cls = ""
                return cls in ("Entry", "TEntry", "Text", "Spinbox", "TCombobox", "TSpinbox")

            guibridge._bind_layout_independent_keys(root, [
                {"key": "space", "callback": toggle_playback},
                {"key": "i", "callback": set_in_marker},
                {"key": "o", "callback": set_out_marker},
                {"key": "a", "callback": add_cut_range},
                {"key": "m", "callback": toggle_mute},
                {"key": "f", "callback": zoom_fit},
                {"key": "r", "ctrl": True, "callback": zoom_fit},
                {"key": "plus", "callback": zoom_in},
                {"key": "minus", "callback": zoom_out},
                {"key": "home", "callback": go_home},
                {"key": "end", "callback": go_end},
                {"key": "left", "shift": False, "callback": lambda _e: set_time(state["timestamp"] - 1.0)},
                {"key": "right", "shift": False, "callback": lambda _e: set_time(state["timestamp"] + 1.0)},
                {"key": "left", "shift": True, "callback": lambda _e: set_time(state["timestamp"] - 5.0)},
                {"key": "right", "shift": True, "callback": lambda _e: set_time(state["timestamp"] + 5.0)},
                {"key": "delete", "callback": remove_selected_cut},
                {"key": "return", "callback": confirm},
                {"key": "escape", "callback": cancel},
            ], is_text_focus_fn=is_editing_text)
            root.protocol("WM_DELETE_WINDOW", cancel)

            root.update_idletasks()
            refresh_audio_label()
            redraw_all()
            root.mainloop()
    except Exception as exc:
        appio.error(f"Cut GUI failed: {exc}")
        return None

    return result.get("keep_ranges")


__all__ = [
    '_open_legacy_cut_gui_tk',
]
