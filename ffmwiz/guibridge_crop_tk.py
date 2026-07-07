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
from ffmwiz import runner  # noqa: F401
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz import runtime  # noqa: F401
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401

from ffmwiz import guibridge  # facade for monkeypatch-stable cross-module calls  # noqa: F401
from ffmwiz.guibridge import *  # sibling helpers  # noqa: F401,F403


def _choose_crop_graphically_tk(answers: dict[str, Any]) -> tuple[int, int, int, int] | None:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as exc:
        appio.error(f"Tkinter is not available, so graphical crop editor cannot be opened: {exc}")
        return None

    try:
        source_width, source_height = first_video_size(answers)
        frame_width, frame_height = preview_size(source_width, source_height)
        duration = services.stream_duration_seconds(answers.get("video_streams", [{}])[0], answers.get("format"))
        if duration is None:
            duration = services.stream_duration_seconds({}, answers.get("format"))
        timeline_duration = max(1.0, duration or 60.0)
        initial_timestamp = min(30.0, max(0.0, timeline_duration * 0.25))
        ffplay = shutil.which("ffplay")
        with tempfile.TemporaryDirectory(prefix="ffmwizard_crop_") as temp_name:
            temp_dir = Path(temp_name)

            result: dict[str, tuple[int, int, int, int] | None] = {"margins": None}
            palette = guibridge._UIPalette
            root = tk.Tk()
            root.title("FFmWiz Crop Editor")
            root.configure(bg=palette.BG)
            _apply_tk_window_icon(root)
            guibridge._apply_app_ttk_theme(root)
            try:
                style = ttk.Style(root)
                # Combobox styling specific to this GUI (zoom dropdown).
                style.configure(
                    "TCombobox",
                    fieldbackground=palette.SURFACE,
                    background=palette.SURFACE,
                    foreground=palette.TEXT,
                    arrowcolor=palette.ACCENT_YELLOW,
                )
                style.map(
                    "TCombobox",
                    fieldbackground=[("readonly", palette.SURFACE)],
                    foreground=[("readonly", palette.TEXT)],
                )
            except Exception:
                pass
            _apply_dark_title_bar(root)
            load_icon_image = _make_icon_loader(root)

            pad = 32
            image_x = pad
            image_y = pad
            min_size = 24
            handle_radius = 8
            handle_hit_radius = 26
            edge_hit_radius = 14
            min_zoom = 25
            max_zoom = 1000

            state: dict[str, Any] = {
                "left": 0,
                "top": 0,
                "right": 0,
                "bottom": 0,
                "drag": "",
                "pan": False,
                "timestamp": initial_timestamp,
                "zoom_percent": 100,
                "photo": None,
                "photo_key": None,
                "playing": False,
                "audio_proc": None,
                "volume": 50,
                "mute": False,
                # Active tool: "hand" pans the image, "zoom" zooms on click/drag.
                # Alt held while dragging in zoom mode inverts the zoom direction.
                "tool": "hand",
                "zoom_drag_y": None,
            }

            viewport_width = min(frame_width + pad * 2, 1280)
            viewport_height = min(frame_height + pad * 2, 760)
            root.minsize(min(viewport_width + 70, 1350), min(viewport_height + 150, 930))

            shell = ttk.Frame(root, padding=(14, 14, 14, 12))
            shell.pack(fill="both", expand=True)
            shell.columnconfigure(0, weight=1)
            shell.rowconfigure(0, weight=1)
            canvas = tk.Canvas(
                shell,
                width=viewport_width,
                height=viewport_height,
                bg="#11151d",
                highlightthickness=0,
            )
            canvas.grid(row=0, column=0, sticky="nsew")
            y_scroll = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview, style="Dark.Vertical.TScrollbar")
            x_scroll = ttk.Scrollbar(shell, orient="horizontal", command=canvas.xview, style="Dark.Horizontal.TScrollbar")
            y_scroll.grid(row=0, column=1, sticky="ns")
            x_scroll.grid(row=1, column=0, sticky="ew")
            canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)

            info = ttk.Label(shell, text="", anchor="w")
            info.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 8))
            controls = ttk.Frame(shell)
            controls.grid(row=3, column=0, columnspan=2, sticky="ew")
            media_controls = ttk.Frame(shell)
            media_controls.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
            zoom_var = tk.StringVar(value="100")
            time_var = tk.DoubleVar(value=initial_timestamp)
            time_label = ttk.Label(media_controls, text="")
            play_button: Any | None = None
            volume_var = tk.IntVar(value=50)
            mute_var = tk.BooleanVar(value=False)
            frame_cache: dict[tuple[int, int, int], Path] = {}
            icon_cache: dict[str, Any] = {}
            zoom_presets = ["25", "33", "50", "67", "75", "90", "100", "110", "125", "150", "175", "200", "250", "300", "400", "500", "600", "800", "1000"]
            speaker_button: Any | None = None
            volume_slider: Any | None = None

            def load_icon(name: str) -> Any | None:
                if name in icon_cache:
                    return icon_cache[name]
                path = asset_path(ICON_DIR_NAME, f"{name}.png")
                if not path.exists():
                    icon_cache[name] = None
                    return None
                try:
                    icon_cache[name] = tk.PhotoImage(file=str(path))
                except tk.TclError:
                    icon_cache[name] = None
                return icon_cache[name]

            def draw_round_rect(target: Any, x1: int, y1: int, x2: int, y2: int, radius: int, fill: str, outline: str) -> None:
                radius = min(radius, max(1, (x2 - x1) // 2), max(1, (y2 - y1) // 2))
                centers = [
                    (x2 - radius, y1 + radius, -90, 0),
                    (x2 - radius, y2 - radius, 0, 90),
                    (x1 + radius, y2 - radius, 90, 180),
                    (x1 + radius, y1 + radius, 180, 270),
                ]
                points: list[float] = []
                for cx, cy, start, end in centers:
                    for angle in range(start, end + 1, 15):
                        radians = math.radians(angle)
                        points.extend([cx + math.cos(radians) * radius, cy + math.sin(radians) * radius])
                target.create_polygon(points, fill=fill, outline=outline, width=1, smooth=True)

            def make_round_button(parent: Any, text: str, command: Callable[[], None], width: int = 92, height: int = 32) -> Any:
                button = tk.Canvas(parent, width=width, height=height, bg="#0b0f17", highlightthickness=0, bd=0, relief="flat")
                label = {"text": text}

                def draw(active: bool = False) -> None:
                    button.delete("all")
                    draw_round_rect(button, 2, 2, width - 3, height - 3, 12, "#263550" if active else "#1b2433", "#5b6f91")
                    button.create_text(width // 2, height // 2, text=label["text"], fill="#f5f7fb", font=("Segoe UI", 9))

                def set_text(new_text: str) -> None:
                    label["text"] = new_text
                    draw(False)

                def on_press(_event: Any) -> None:
                    draw(True)

                def on_release(_event: Any) -> None:
                    draw(False)
                    command()

                button.bind("<ButtonPress-1>", on_press)
                button.bind("<ButtonRelease-1>", on_release)
                button.bind("<Enter>", lambda _event: button.configure(cursor="hand2"))
                button.bind("<Leave>", lambda _event: draw(False))
                button.set_text = set_text  # type: ignore[attr-defined]
                draw(False)
                return button

            def make_icon_button(parent: Any, kind: str, command: Callable[[], None], width: int = 46, height: int = 36) -> Any:
                button = tk.Canvas(parent, width=width, height=height, bg="#0b0f17", highlightthickness=0, bd=0, relief="flat")
                button.pack_propagate(False)

                def draw_icon(active: bool = False) -> None:
                    button.delete("all")
                    bg = "#263550" if active else "#1b2433"
                    fg = "#f8fbff"
                    accent = "#f5d66a"
                    muted_line = "#46556d"
                    draw_round_rect(button, 2, 2, width - 3, height - 3, 12, bg, "#5b6f91")
                    icon_name = kind
                    if kind == "speaker":
                        volume = int(volume_var.get())
                        level = 0 if mute_var.get() or volume <= 0 else 1 if volume < 34 else 2 if volume < 67 else 3
                        icon_name = f"volume_{level}"
                    icon = load_icon(icon_name)
                    if icon is not None:
                        button.create_image(width // 2, height // 2, image=icon)
                    elif kind in {"zoom_in", "zoom_out"}:
                        button.create_oval(9, 6, 23, 20, outline=accent, width=2)
                        button.create_line(21, 19, 30, 26, fill=accent, width=2)
                        button.create_line(13, 13, 19, 13, fill=fg, width=2)
                        if kind == "zoom_in":
                            button.create_line(16, 10, 16, 16, fill=fg, width=2)
                    elif kind == "speaker":
                        volume = int(volume_var.get())
                        level = 0 if mute_var.get() or volume <= 0 else 1 if volume <= 33 else 2 if volume <= 66 else 3
                        button.create_polygon(7, 13, 13, 13, 20, 7, 20, 23, 13, 17, 7, 17, fill=accent, outline="")
                        if mute_var.get():
                            button.create_line(25, 10, 33, 20, fill="#ff6f6f", width=2)
                            button.create_line(33, 10, 25, 20, fill="#ff6f6f", width=2)
                        else:
                            button.create_arc(21, 11, 28, 19, start=-35, extent=70, style="arc", outline=fg if level >= 1 else muted_line, width=2)
                            button.create_arc(19, 8, 33, 22, start=-35, extent=70, style="arc", outline=fg if level >= 2 else muted_line, width=2)
                            button.create_arc(17, 5, 38, 25, start=-35, extent=70, style="arc", outline=fg if level >= 3 else muted_line, width=2)

                def on_press(_event: Any) -> None:
                    draw_icon(True)

                def on_release(_event: Any) -> None:
                    draw_icon(False)
                    command()

                button.bind("<ButtonPress-1>", on_press)
                button.bind("<ButtonRelease-1>", on_release)
                button.bind("<Enter>", lambda _event: button.configure(cursor="hand2"))
                button.bind("<Leave>", lambda _event: draw_icon(False))
                draw_icon(False)
                button.redraw_icon = draw_icon  # type: ignore[attr-defined]
                return button

            def clamp(value: float, low: float, high: float) -> float:
                return max(low, min(high, value))

            def display_width() -> int:
                return max(1, round(frame_width * int(state["zoom_percent"]) / 100))

            def display_height() -> int:
                return max(1, round(frame_height * int(state["zoom_percent"]) / 100))

            def image_bounds() -> tuple[int, int, int, int]:
                return image_x, image_y, image_x + display_width(), image_y + display_height()

            def format_time(seconds: float) -> str:
                total = max(0, int(round(seconds)))
                minutes, secs = divmod(total, 60)
                hours, minutes = divmod(minutes, 60)
                if hours:
                    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
                return f"{minutes:02d}:{secs:02d}"

            # --- Async preview scheduler -------------------------------------
            # Background-extract frames on a worker thread with debouncing and
            # caching. The cache is keyed by (timestamp_ms, width, height) so
            # zoom changes invalidate naturally.

            def _crop_extract(timestamp: float, w: int, h: int) -> Path:
                return extract_crop_preview_frame(answers, temp_dir, w, h, timestamp)

            def _crop_on_ready(path: Path) -> None:
                try:
                    state["photo"] = tk.PhotoImage(file=str(path))
                except Exception:
                    return
                state["photo_key"] = (
                    int(round(float(state["timestamp"]) * 1000)),
                    display_width(),
                    display_height(),
                )
                redraw(force_image_request=False)

            crop_scheduler = guibridge._PreviewScheduler(
                root,
                extract_fn=_crop_extract,
                on_ready=_crop_on_ready,
                debounce_ms=70,
            )

            def render_photo(force: bool = True) -> None:
                """Request the current frame from the scheduler.

                If a cached path is available, set state['photo'] immediately
                so the next canvas draw shows the correct frame. Otherwise the
                scheduler will deliver it asynchronously and trigger a redraw.
                """
                key = (
                    int(round(float(state["timestamp"]) * 1000)),
                    display_width(),
                    display_height(),
                )
                if state.get("photo_key") == key and state.get("photo") is not None:
                    return
                cached = crop_scheduler.request(float(state["timestamp"]), key[1], key[2])
                if cached is not None:
                    try:
                        state["photo"] = tk.PhotoImage(file=str(cached))
                        state["photo_key"] = key
                    except Exception:
                        state["photo_key"] = None
                # Else: keep showing the previous frame; the worker will
                # deliver the new one and trigger a redraw via _crop_on_ready.

            def min_source_width() -> int:
                return max(1, round(source_width * min_size / display_width()))

            def min_source_height() -> int:
                return max(1, round(source_height * min_size / display_height()))

            def clamp_margins() -> None:
                state["left"] = int(clamp(state["left"], 0, max(0, source_width - state["right"] - min_source_width())))
                state["right"] = int(clamp(state["right"], 0, max(0, source_width - state["left"] - min_source_width())))
                state["top"] = int(clamp(state["top"], 0, max(0, source_height - state["bottom"] - min_source_height())))
                state["bottom"] = int(clamp(state["bottom"], 0, max(0, source_height - state["top"] - min_source_height())))

            def current_margins() -> tuple[int, int, int, int]:
                clamp_margins()
                return int(state["top"]), int(state["left"]), int(state["right"]), int(state["bottom"])

            def crop_rect() -> tuple[int, int, int, int]:
                _, _, image_right, image_bottom = image_bounds()
                left = image_x + round(state["left"] * display_width() / source_width)
                top = image_y + round(state["top"] * display_height() / source_height)
                right = image_right - round(state["right"] * display_width() / source_width)
                bottom = image_bottom - round(state["bottom"] * display_height() / source_height)
                return left, top, right, bottom

            def handle_points() -> dict[str, tuple[int, int]]:
                left, top, right, bottom = crop_rect()
                mid_x = round((left + right) / 2)
                mid_y = round((top + bottom) / 2)
                return {
                    "nw": (left, top),
                    "n": (mid_x, top),
                    "ne": (right, top),
                    "e": (right, mid_y),
                    "se": (right, bottom),
                    "s": (mid_x, bottom),
                    "sw": (left, bottom),
                    "w": (left, mid_y),
                }

            def redraw(force_image_request: bool = True) -> None:
                clamp_margins()
                if force_image_request:
                    try:
                        render_photo()
                    except Exception as exc:
                        appio.error(f"Could not refresh crop preview frame: {exc}")
                canvas.delete("all")

                image_left, image_top, image_right, image_bottom = image_bounds()
                if state.get("photo") is not None:
                    canvas.create_image(image_left, image_top, image=state["photo"], anchor="nw")
                else:
                    # Show a placeholder until the worker delivers the first frame.
                    canvas.create_rectangle(
                        image_left, image_top, image_right, image_bottom,
                        fill="#11151d", outline="#2e3a4f", width=1,
                    )
                    canvas.create_text(
                        (image_left + image_right) // 2,
                        (image_top + image_bottom) // 2,
                        text="(loading preview frame...)",
                        fill="#7c8aa6",
                    )
                left, top, right, bottom = crop_rect()
                canvas.create_rectangle(image_left, image_top, image_right, top, fill="#000000", stipple="gray50", outline="")
                canvas.create_rectangle(image_left, bottom, image_right, image_bottom, fill="#000000", stipple="gray50", outline="")
                canvas.create_rectangle(image_left, top, left, bottom, fill="#000000", stipple="gray50", outline="")
                canvas.create_rectangle(right, top, image_right, bottom, fill="#000000", stipple="gray50", outline="")
                canvas.create_rectangle(left, top, right, bottom, outline="#ffcc33", width=2)

                third_x = (right - left) / 3
                third_y = (bottom - top) / 3
                for pos in (left + third_x, left + third_x * 2):
                    canvas.create_line(pos, top, pos, bottom, fill="#ffcc33", dash=(4, 5), width=1)
                for pos in (top + third_y, top + third_y * 2):
                    canvas.create_line(left, pos, right, pos, fill="#ffcc33", dash=(4, 5), width=1)

                for name, (x_pos, y_pos) in handle_points().items():
                    fill = "#f8fbff" if len(name) == 1 else "#ffcc33"
                    canvas.create_rectangle(
                        x_pos - handle_radius,
                        y_pos - handle_radius,
                        x_pos + handle_radius,
                        y_pos + handle_radius,
                        fill=fill,
                        outline="#11151d",
                        width=1,
                    )
                top_m, left_m, right_m, bottom_m = current_margins()
                crop_width = source_width - left_m - right_m
                crop_height = source_height - top_m - bottom_m
                info.configure(
                    text=(
                        f"Crop margins: top={top_m}, left={left_m}, right={right_m}, bottom={bottom_m} "
                        f"| output crop box: {crop_width}x{crop_height} | time {format_time(float(state['timestamp']))}"
                    )
                )
                canvas.configure(scrollregion=(0, 0, image_right + pad, image_bottom + pad))
                zoom_var.set(str(int(state["zoom_percent"])))
                time_var.set(float(state["timestamp"]))
                time_label.configure(text=f"{format_time(float(state['timestamp']))} / {format_time(timeline_duration)}")

            def hit_handle(x_pos: float, y_pos: float) -> str:
                left, top, right, bottom = crop_rect()
                in_x = left - edge_hit_radius <= x_pos <= right + edge_hit_radius
                in_y = top - edge_hit_radius <= y_pos <= bottom + edge_hit_radius

                corner_zones = {
                    "nw": (left, top),
                    "ne": (right, top),
                    "se": (right, bottom),
                    "sw": (left, bottom),
                }
                for name, (corner_x, corner_y) in corner_zones.items():
                    if abs(x_pos - corner_x) <= handle_hit_radius and abs(y_pos - corner_y) <= handle_hit_radius:
                        return name

                for name, (handle_x, handle_y) in handle_points().items():
                    if abs(x_pos - handle_x) <= handle_hit_radius and abs(y_pos - handle_y) <= handle_hit_radius:
                        return name

                if in_x and abs(y_pos - top) <= edge_hit_radius:
                    return "n"
                if in_x and abs(y_pos - bottom) <= edge_hit_radius:
                    return "s"
                if in_y and abs(x_pos - left) <= edge_hit_radius:
                    return "w"
                if in_y and abs(x_pos - right) <= edge_hit_radius:
                    return "e"
                return ""

            def cursor_for_handle(handle: str) -> str:
                if handle in {"e", "w"}:
                    return "sb_h_double_arrow"
                if handle in {"n", "s"}:
                    return "sb_v_double_arrow"
                if handle in {"nw", "se"}:
                    return "size_nw_se"
                if handle in {"ne", "sw"}:
                    return "size_ne_sw"
                return ""

            def point_in_image(x_pos: float, y_pos: float) -> bool:
                image_left, image_top, image_right, image_bottom = image_bounds()
                return image_left <= x_pos <= image_right and image_top <= y_pos <= image_bottom

            def set_canvas_cursor(cursor: str) -> None:
                try:
                    if cursor == "open_hand":
                        cursor_file = asset_path(CURSOR_DIR_NAME, "open_hand.xbm")
                        if cursor_file.exists():
                            canvas.configure(cursor=f"@{cursor_file}")
                            return
                        canvas.configure(cursor="hand1")
                        return
                    canvas.configure(cursor=cursor)
                except tk.TclError:
                    fallback = "fleur" if cursor == "open_hand" else "crosshair" if cursor else ""
                    canvas.configure(cursor=fallback)

            def _alt_held(event: Any) -> bool:
                """Return True if any Alt modifier is held in the event.state mask."""
                if event is None:
                    return False
                mask = getattr(event, "state", 0) or 0
                # Windows: Alt = 0x20000. Linux/X11: Mod1 = 0x0008.
                return bool(mask & 0x20000) or bool(mask & 0x0008)

            def update_cursor(event: Any) -> None:
                if state["drag"]:
                    return
                x_pos = canvas.canvasx(event.x)
                y_pos = canvas.canvasy(event.y)
                handle = hit_handle(x_pos, y_pos)
                if handle:
                    set_canvas_cursor(cursor_for_handle(handle))
                elif point_in_image(x_pos, y_pos):
                    if state["tool"] == "zoom":
                        # Use the universally-supported "crosshair" cursor
                        # for Zoom Tool. The Tk "icon" cursor used previously
                        # appeared as a black square on some Windows builds.
                        set_canvas_cursor("crosshair")
                    else:
                        set_canvas_cursor("open_hand")
                else:
                    set_canvas_cursor("")

            def apply_zoom_centered_on(x_pos: float, y_pos: float, factor: float) -> None:
                """Zoom the preview by 'factor' (>1 zoom in, <1 zoom out)
                keeping the canvas point (x_pos, y_pos) at the same screen
                location after the zoom."""
                if factor <= 0 or abs(factor - 1.0) < 1e-6:
                    return
                old_width = display_width()
                old_height = display_height()
                if old_width <= 0 or old_height <= 0:
                    return
                # Image-space coordinates of the focused canvas point.
                rel_x = (canvas.canvasx(x_pos) - image_x) / max(1, old_width)
                rel_y = (canvas.canvasy(y_pos) - image_y) / max(1, old_height)
                rel_x = max(0.0, min(1.0, rel_x))
                rel_y = max(0.0, min(1.0, rel_y))

                new_zoom = int(round(int(state["zoom_percent"]) * factor))
                new_zoom = int(clamp(new_zoom, min_zoom, max_zoom))
                if new_zoom == int(state["zoom_percent"]):
                    return
                state["zoom_percent"] = new_zoom
                state["photo_key"] = None  # force re-render at the new size
                redraw()
                # After redraw, re-center scroll so the focused image-relative
                # point lands under the original mouse position.
                root.update_idletasks()
                new_width = display_width()
                new_height = display_height()
                target_canvas_x = image_x + rel_x * new_width
                target_canvas_y = image_y + rel_y * new_height
                desired_x = target_canvas_x - x_pos
                desired_y = target_canvas_y - y_pos
                scroll_w = max(1, new_width + pad * 2)
                scroll_h = max(1, new_height + pad * 2)
                canvas.xview_moveto(max(0.0, min(1.0, desired_x / scroll_w)))
                canvas.yview_moveto(max(0.0, min(1.0, desired_y / scroll_h)))

            def begin_drag(event: Any) -> None:
                x_pos = canvas.canvasx(event.x)
                y_pos = canvas.canvasy(event.y)
                state["drag"] = hit_handle(x_pos, y_pos)
                if state["drag"]:
                    set_canvas_cursor(cursor_for_handle(state["drag"]))
                    canvas.focus_set()
                    return

                if point_in_image(x_pos, y_pos):
                    if state["tool"] == "zoom":
                        # Photoshop-style: clicking zooms in (or out with Alt).
                        # Hold + drag tracks vertical motion for finer control.
                        state["zoom_drag_y"] = event.y
                        state["drag"] = "_zoom"
                        # Single-click zoom step:
                        factor = 1.0 / 1.25 if _alt_held(event) else 1.25
                        apply_zoom_centered_on(event.x, event.y, factor)
                        update_cursor(event)
                    else:
                        # Hand tool: pan.
                        state["pan"] = True
                        canvas.scan_mark(event.x, event.y)
                        set_canvas_cursor("open_hand")
                canvas.focus_set()

            def drag(event: Any) -> None:
                if state.get("drag") == "_zoom" and state.get("zoom_drag_y") is not None:
                    dy = event.y - int(state["zoom_drag_y"])
                    if abs(dy) >= 6:
                        # Up = zoom in, down = zoom out. Alt inverts.
                        zoom_in_dir = dy < 0
                        if _alt_held(event):
                            zoom_in_dir = not zoom_in_dir
                        factor = 1.07 if zoom_in_dir else (1.0 / 1.07)
                        apply_zoom_centered_on(event.x, event.y, factor)
                        state["zoom_drag_y"] = event.y
                    return
                if state["pan"]:
                    canvas.scan_dragto(event.x, event.y, gain=1)
                    return
                mode = state["drag"]
                if not mode:
                    return
                image_left, image_top, image_right, image_bottom = image_bounds()
                left, top, right, bottom = crop_rect()
                x_pos = clamp(canvas.canvasx(event.x), image_left, image_right)
                y_pos = clamp(canvas.canvasy(event.y), image_top, image_bottom)
                if "w" in mode:
                    new_left = clamp(x_pos, image_left, right - min_size)
                    state["left"] = round((new_left - image_left) * source_width / display_width())
                if "e" in mode:
                    new_right = clamp(x_pos, left + min_size, image_right)
                    state["right"] = round((image_right - new_right) * source_width / display_width())
                if "n" in mode:
                    new_top = clamp(y_pos, image_top, bottom - min_size)
                    state["top"] = round((new_top - image_top) * source_height / display_height())
                if "s" in mode:
                    new_bottom = clamp(y_pos, top + min_size, image_bottom)
                    state["bottom"] = round((image_bottom - new_bottom) * source_height / display_height())
                redraw(force_image_request=False)

            def end_drag(_event: Any) -> None:
                state["drag"] = ""
                state["pan"] = False
                state["zoom_drag_y"] = None

            def set_tool_hand(_event: Any = None) -> None:
                state["tool"] = "hand"
                refresh_tool_label()

            def set_tool_zoom(_event: Any = None) -> None:
                state["tool"] = "zoom"
                refresh_tool_label()

            def refresh_tool_label() -> None:
                # The two tool buttons exist as attributes set later; update
                # their visible labels so the active tool is obvious.
                try:
                    hand_button.configure(
                        text=("Hand Tool* (H)" if state["tool"] == "hand" else "Hand Tool (H)")
                    )
                    zoom_button.configure(
                        text=("Zoom Tool* (Z)" if state["tool"] == "zoom" else "Zoom Tool (Z)")
                    )
                except Exception:
                    pass

            def reset_crop() -> None:
                state["left"] = 0
                state["top"] = 0
                state["right"] = 0
                state["bottom"] = 0
                redraw()

            def set_zoom_percent(value: int | str) -> None:
                try:
                    number = int(str(value).strip().rstrip("%"))
                except ValueError:
                    appio.error("Zoom must be an integer percent, for example 150.")
                    return
                state["zoom_percent"] = int(clamp(number, min_zoom, max_zoom))
                state["photo_key"] = None
                redraw()

            def zoom_in() -> None:
                set_zoom_percent(int(state["zoom_percent"]) + 25)

            def zoom_out() -> None:
                set_zoom_percent(int(state["zoom_percent"]) - 25)

            def reset_zoom() -> None:
                set_zoom_percent(100)

            def set_time(value: float, restart_audio: bool = True) -> None:
                state["timestamp"] = clamp(float(value), 0.0, timeline_duration)
                state["photo_key"] = None
                redraw()
                if restart_audio and state["playing"]:
                    start_audio()

            def step_time(delta: float) -> None:
                set_time(float(state["timestamp"]) + delta)

            def stop_audio() -> None:
                process = state.get("audio_proc")
                state["audio_proc"] = None
                if process and process.poll() is None:
                    try:
                        process.terminate()
                        process.wait(timeout=0.8)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass

            def start_audio() -> None:
                stop_audio()
                if not ffplay or state["mute"] or int(state["volume"]) <= 0:
                    return
                args = [
                    ffplay,
                    "-nodisp",
                    "-autoexit",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{float(state['timestamp']):.3f}",
                    "-volume",
                    str(int(state["volume"])),
                    str(answers["input_path"]),
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

            def playback_tick() -> None:
                if not state["playing"]:
                    return
                next_time = float(state["timestamp"]) + 1.0
                if next_time >= timeline_duration:
                    state["timestamp"] = timeline_duration
                    state["playing"] = False
                    if play_button is not None:
                        play_button.set_text("▶ Play (Space)")  # type: ignore[attr-defined]
                    stop_audio()
                    redraw()
                    return
                state["timestamp"] = next_time
                state["photo_key"] = None
                redraw()
                root.after(1000, playback_tick)

            def toggle_playback() -> None:
                # Bug fix: if playback reached the end and the user presses
                # Play again, rewind to the start so playback continues
                # normally instead of ending after ~1 second.
                if not state["playing"] and float(state["timestamp"]) >= timeline_duration - 0.5:
                    state["timestamp"] = 0.0
                    state["photo_key"] = None
                    redraw()
                state["playing"] = not state["playing"]
                if play_button is not None:
                    play_button.set_text("⏸ Pause (Space)" if state["playing"] else "▶ Play (Space)")  # type: ignore[attr-defined]
                if state["playing"]:
                    start_audio()
                    root.after(1000, playback_tick)
                else:
                    stop_audio()

            def update_audio_settings(_event: Any | None = None, restart: bool = False) -> None:
                state["volume"] = int(volume_var.get())
                state["mute"] = bool(mute_var.get())
                if speaker_button is not None:
                    speaker_button.redraw_icon(False)  # type: ignore[attr-defined]
                draw_volume_slider()
                if state["playing"] and restart:
                    start_audio()

            def toggle_mute() -> None:
                mute_var.set(not mute_var.get())
                update_audio_settings(restart=True)
                if speaker_button is not None:
                    speaker_button.redraw_icon(False)  # type: ignore[attr-defined]

            def slider_value_from_click(widget: Any, event: Any, low: float, high: float) -> float:
                width = max(1, widget.winfo_width())
                ratio = clamp(event.x / width, 0.0, 1.0)
                return low + (high - low) * ratio

            def seek_time_from_click(event: Any) -> None:
                set_time(slider_value_from_click(time_slider, event, 0.0, timeline_duration))

            def set_volume_from_click(event: Any) -> None:
                set_volume_from_x(event.x, restart=True)

            def draw_volume_slider(active: bool = False) -> None:
                if volume_slider is None:
                    return
                width = int(volume_slider["width"])
                height = int(volume_slider["height"])
                left = 9
                right = width - 9
                center = height // 2
                volume_slider.delete("all")
                draw_round_rect(volume_slider, left, center - 4, right, center + 4, 4, "#101827", "#3f5576")
                fill_right = left + round((right - left) * int(volume_var.get()) / 100)
                if fill_right > left:
                    draw_round_rect(volume_slider, left, center - 4, fill_right, center + 4, 4, "#f5d66a", "#f5d66a")
                thumb_x = max(left, min(right, fill_right))
                thumb_fill = "#ffffff" if active else "#dfeaff"
                volume_slider.create_oval(thumb_x - 7, center - 7, thumb_x + 7, center + 7, fill=thumb_fill, outline="#6f8dc1", width=2)

            def set_volume_value(value: float, restart: bool = False) -> None:
                volume_var.set(int(clamp(round(value), 0, 100)))
                update_audio_settings(restart=restart)

            def set_volume_from_x(x_pos: float, restart: bool = False) -> None:
                if volume_slider is None:
                    return
                width = int(volume_slider["width"])
                left = 9
                right = width - 9
                ratio = clamp((x_pos - left) / max(1, right - left), 0.0, 1.0)
                set_volume_value(ratio * 100, restart=restart)

            def drag_volume(event: Any) -> str:
                set_volume_from_x(event.x, restart=False)
                return "break"

            def release_volume(event: Any) -> str:
                set_volume_from_x(event.x, restart=True)
                return "break"

            def volume_wheel(event: Any) -> str:
                delta = 3 if event.delta > 0 else -3
                set_volume_value(int(volume_var.get()) + delta, restart=True)
                return "break"

            def mouse_wheel(event: Any) -> str:
                step = -1 if event.delta > 0 else 1
                if event.state & 0x0001:
                    canvas.xview_scroll(step, "units")
                else:
                    canvas.yview_scroll(step, "units")
                return "break"

            def x_scroll_wheel(event: Any) -> str:
                canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")
                return "break"

            def y_scroll_wheel(event: Any) -> str:
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
                return "break"

            def time_slider_wheel(event: Any) -> str:
                step_time(-5 if event.delta > 0 else 5)
                return "break"

            def ctrl_mouse_wheel(event: Any) -> str:
                if event.delta > 0:
                    zoom_in()
                else:
                    zoom_out()
                return "break"

            def pan_with_key(event: Any) -> str:
                moves = {
                    "Left": (-1, 0),
                    "Right": (1, 0),
                    "Up": (0, -1),
                    "Down": (0, 1),
                }
                dx, dy = moves.get(event.keysym, (0, 0))
                if dx:
                    canvas.xview_scroll(dx, "units")
                if dy:
                    canvas.yview_scroll(dy, "units")
                return "break"

            def apply_crop() -> None:
                result["margins"] = current_margins()
                state["playing"] = False
                stop_audio()
                try:
                    crop_scheduler.cancel()
                except Exception:
                    pass
                root.destroy()

            def cancel_crop() -> None:
                result["margins"] = None
                state["playing"] = False
                stop_audio()
                try:
                    crop_scheduler.cancel()
                except Exception:
                    pass
                root.destroy()

            def is_editing_text(event: Any) -> bool:
                """True if the current keyboard focus is on a text-entry widget."""
                widget = getattr(event, "widget", None) if event is not None else None
                try:
                    cls = str(widget.winfo_class()) if widget is not None else ""
                except Exception:
                    cls = ""
                return cls in ("Entry", "TEntry", "Text", "Spinbox", "TCombobox", "TSpinbox")

            def space_toggle_playback(event: Any) -> str | None:
                if is_editing_text(event):
                    return None
                toggle_playback()
                return "break"

            canvas.bind("<ButtonPress-1>", begin_drag)
            canvas.bind("<B1-Motion>", drag)
            canvas.bind("<ButtonRelease-1>", end_drag)
            canvas.bind("<Motion>", update_cursor)
            canvas.bind("<Leave>", lambda _event: set_canvas_cursor(""))
            canvas.bind("<MouseWheel>", mouse_wheel)
            canvas.bind("<Control-MouseWheel>", ctrl_mouse_wheel)
            x_scroll.bind("<MouseWheel>", x_scroll_wheel)
            y_scroll.bind("<MouseWheel>", y_scroll_wheel)
            root.bind("<Control-plus>", lambda _event: zoom_in())
            root.bind("<Control-equal>", lambda _event: zoom_in())
            root.bind("<Control-minus>", lambda _event: zoom_out())
            for key_name in ("<Left>", "<Right>", "<Up>", "<Down>"):
                root.bind(key_name, pan_with_key)

            # Layout-independent shortcuts so the Crop GUI still responds
            # when the active keyboard language is Persian or another
            # non-Latin layout (these bindings match by Windows VK code).
            guibridge._bind_layout_independent_keys(root, [
                {"key": "space", "callback": lambda _e: space_toggle_playback(_e)},
                {"key": "h", "ctrl": False, "alt": False, "callback": set_tool_hand},
                {"key": "z", "ctrl": False, "alt": False, "callback": set_tool_zoom},
                {"key": "r", "ctrl": False, "callback": lambda _e: reset_crop()},
                {"key": "r", "ctrl": True, "callback": lambda _e: reset_crop()},
                {"key": "m", "ctrl": False, "callback": lambda _e: toggle_mute()},
                {"key": "0", "ctrl": True, "callback": lambda _e: reset_zoom()},
                {"key": "plus", "ctrl": True, "callback": lambda _e: zoom_in()},
                {"key": "minus", "ctrl": True, "callback": lambda _e: zoom_out()},
                {"key": "plus", "ctrl": False, "callback": lambda _e: zoom_in()},
                {"key": "minus", "ctrl": False, "callback": lambda _e: zoom_out()},
                {"key": "left", "shift": True, "callback": lambda _e: step_time(-10)},
                {"key": "right", "shift": True, "callback": lambda _e: step_time(10)},
                {"key": "return", "callback": lambda _e: apply_crop()},
                {"key": "escape", "callback": lambda _e: cancel_crop()},
            ], is_text_focus_fn=is_editing_text)

            root.protocol("WM_DELETE_WINDOW", cancel_crop)
            # Tool selector buttons. Hand is the default to preserve the
            # previous panning behavior; Zoom Tool is opt-in via Z.
            hand_button = make_round_button(controls, "Hand Tool* (H)", set_tool_hand, width=120)
            hand_button.pack(side="left")
            zoom_button = make_round_button(controls, "Zoom Tool (Z)", set_tool_zoom, width=120)
            zoom_button.pack(side="left", padx=(6, 12))
            make_icon_button(controls, "zoom_out", zoom_out).pack(side="left")
            ttk.Label(controls, text="Zoom %").pack(side="left", padx=(8, 4))
            zoom_box = tk.Frame(
                controls,
                bg="#101827",
                highlightthickness=1,
                highlightbackground="#3a4b65",
                highlightcolor="#f5d66a",
            )
            zoom_box.pack(side="left")
            zoom_entry = tk.Entry(
                zoom_box,
                textvariable=zoom_var,
                width=5,
                bg="#101827",
                fg="#f5f7fb",
                insertbackground="#f5d66a",
                relief="flat",
                highlightthickness=0,
                justify="center",
            )
            zoom_entry.pack(side="left", ipady=5)
            zoom_entry.bind("<Return>", lambda _event: set_zoom_percent(zoom_var.get()))
            zoom_arrow = tk.Canvas(zoom_box, width=22, height=28, bg="#101827", highlightthickness=0, bd=0, relief="flat")
            zoom_arrow.pack(side="left")
            zoom_arrow.create_polygon(7, 10, 15, 10, 11, 16, fill="#f5d66a", outline="")
            zoom_menu = tk.Menu(
                root,
                tearoff=False,
                bg="#101827",
                fg="#f5f7fb",
                activebackground="#263550",
                activeforeground="#ffffff",
                bd=1,
                relief="solid",
            )
            for preset in zoom_presets:
                zoom_menu.add_command(label=f"{preset}%", command=lambda value=preset: set_zoom_percent(value))

            def show_zoom_menu() -> None:
                zoom_menu.tk_popup(zoom_box.winfo_rootx(), zoom_box.winfo_rooty() + zoom_box.winfo_height())

            zoom_arrow.bind("<Button-1>", lambda _event: show_zoom_menu())
            zoom_arrow.bind("<Enter>", lambda _event: zoom_arrow.configure(cursor="hand2"))
            make_icon_button(controls, "zoom_in", zoom_in).pack(side="left", padx=(12, 0))
            make_round_button(controls, "Reset Zoom (Ctrl+0)", reset_zoom, width=146).pack(side="left", padx=(8, 0))
            make_round_button(controls, "Reset Crop (Ctrl+R)", reset_crop, width=154).pack(side="left", padx=(8, 0))
            make_round_button(controls, "Cancel (Esc)", cancel_crop, width=104).pack(side="right", padx=(8, 0))
            make_round_button(controls, "Apply (Enter)", apply_crop, width=110).pack(side="right")

            play_button = make_round_button(media_controls, "▶ Play (Space)", toggle_playback, width=120)
            play_button.pack(side="left")
            make_round_button(media_controls, "-10s (Shift+←)", lambda: step_time(-10), width=110).pack(side="left", padx=(8, 0))
            time_slider = ttk.Scale(
                media_controls,
                from_=0.0,
                to=timeline_duration,
                orient="horizontal",
                variable=time_var,
            )
            time_slider.pack(side="left", fill="x", expand=True, padx=8)
            time_slider.bind("<Button-1>", seek_time_from_click)
            time_slider.bind("<ButtonRelease-1>", lambda _event: set_time(time_var.get()))
            time_slider.bind("<MouseWheel>", time_slider_wheel)
            make_round_button(media_controls, "+10s (Shift+→)", lambda: step_time(10), width=110).pack(side="left")
            time_label.pack(side="left", padx=(8, 16))
            speaker_button = make_icon_button(media_controls, "speaker", toggle_mute, width=48, height=36)
            speaker_button.pack(side="left")
            # Home / End jump to start / end of the audio-preview timeline.
            ttk.Button(media_controls, text="⏮", width=3,
                       command=lambda: set_time(0.0)).pack(side="left", padx=(8, 0))
            ttk.Button(media_controls, text="⏭", width=3,
                       command=lambda: set_time(timeline_duration)).pack(side="left", padx=(4, 0))
            guibridge._bind_layout_independent_keys(root, [
                {"key": "home", "callback": lambda _e: set_time(0.0)},
                {"key": "end", "callback": lambda _e: set_time(timeline_duration)},
            ], is_text_focus_fn=is_editing_text)
            volume_slider = tk.Canvas(
                media_controls,
                width=142,
                height=30,
                bg="#0b0f17",
                highlightthickness=0,
                bd=0,
                relief="flat",
            )
            volume_slider.pack(side="left", padx=(8, 0))
            volume_slider.bind("<Button-1>", set_volume_from_click)
            volume_slider.bind("<B1-Motion>", drag_volume)
            volume_slider.bind("<ButtonRelease-1>", release_volume)
            volume_slider.bind("<MouseWheel>", volume_wheel)
            volume_slider.bind("<Enter>", lambda _event: volume_slider.configure(cursor="hand2") if volume_slider is not None else None)
            if not ffplay:
                speaker_button.unbind("<ButtonPress-1>")
                speaker_button.unbind("<ButtonRelease-1>")
                if volume_slider is not None:
                    volume_slider.unbind("<Button-1>")
                    volume_slider.unbind("<B1-Motion>")
                    volume_slider.unbind("<ButtonRelease-1>")
                    volume_slider.unbind("<MouseWheel>")
                ttk.Label(media_controls, text="Audio preview needs ffplay").pack(side="left", padx=(8, 0))
            draw_volume_slider()
            redraw()
            canvas.focus_set()
            root.mainloop()
            return result["margins"]
    except subprocess.CalledProcessError as exc:
        appio.error(f"FFmpeg could not create a crop preview frame: {exc}")
    except Exception as exc:
        appio.error(f"Graphical crop preview failed: {exc}")
    return None


__all__ = [
    '_choose_crop_graphically_tk',
]
