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
from ffmwiz import services  # noqa: F401
from ffmwiz.guibridge_tk_common import (  # noqa: F401
    _PreviewScheduler,
    _UIPalette,
    _apply_app_ttk_theme,
    _bind_layout_independent_keys,
)

# The facade back-import was deleted: every name this module uses comes
# from the LOWER tiers above, which the facade only re-exported. Importing
# it here bought nothing and made this module unimportable on its own,
# because the facade ends with `__all__ += <this module>.__all__` and
# reached that line while this module was still on its first statements.


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
            palette = _UIPalette
            root = tk.Tk()
            root.title("FFmWiz Crop Editor")
            root.configure(bg=palette.BG)
            _apply_tk_window_icon(root)
            _apply_app_ttk_theme(root)
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
                return load_tk_icon(tk, icon_cache, name)

            def make_round_button(parent: Any, text: str, command: Callable[[], None], width: int = 92, height: int = 32) -> Any:
                return make_round_canvas_button(tk, parent, text, command, width, height)

            def make_icon_button(parent: Any, kind: str, command: Callable[[], None], width: int = 46, height: int = 36) -> Any:
                return make_icon_canvas_button(tk, load_icon, volume_var, mute_var,
                                               parent, kind, command, width, height)

            # The pixel maths lives in guibridge_crop_tk_canvas. `state` goes
            # in BY REFERENCE: the geometry writes the clamped margins back
            # into it and the builder reads them out again below, so a copy
            # here would throw away every drag.
            geo = CropGeometry(
                state, frame_width, frame_height, source_width, source_height,
                image_x, image_y, min_size, handle_hit_radius, edge_hit_radius,
            )
            # Aliases, so the call sites below read exactly as they did when
            # these were nested closures.
            clamp = clamp_value
            display_width = geo.display_width
            display_height = geo.display_height
            current_margins = geo.current_margins

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

            crop_scheduler = _PreviewScheduler(
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

            def redraw(force_image_request: bool = True) -> None:
                draw_crop_view(canvas, geo, state, info, zoom_var, time_var,
                               time_label, timeline_duration, pad, handle_radius,
                               render_photo, force_image_request)

            def set_canvas_cursor(cursor: str) -> None:
                set_crop_canvas_cursor(canvas, tk, cursor)

            def update_cursor(event: Any) -> None:
                update_crop_cursor(event, canvas, geo, state, set_canvas_cursor)

            def apply_zoom_centered_on(x_pos: float, y_pos: float, factor: float) -> None:
                apply_crop_zoom_centered_on(x_pos, y_pos, factor, canvas, root, geo,
                                            state, redraw, pad, min_zoom, max_zoom)

            begin_drag, drag, end_drag = make_crop_drag_handlers(
                canvas, geo, state, set_canvas_cursor, apply_zoom_centered_on,
                update_cursor, redraw,
            )

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
                schedule_playback_tick()

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
                    schedule_playback_tick()
                else:
                    cancel_playback_tick()
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
                # volume_slider is still None until the control row is built,
                # so it is read HERE, on every call, never captured.
                draw_crop_volume_slider(volume_slider, volume_var, active)

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
                cancel_playback_tick()
                stop_audio()
                try:
                    crop_scheduler.cancel()
                except Exception:
                    pass
                root.destroy()

            def cancel_crop() -> None:
                result["margins"] = None
                state["playing"] = False
                cancel_playback_tick()
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
            _bind_layout_independent_keys(root, [
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
            _bind_layout_independent_keys(root, [
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


# guibridge_crop_tk_canvas holds this editor's geometry and canvas painting
# (split for file size). Bound twice on purpose: the `_` alias is what
# tests/test_module_reference_hygiene reads to find re-export pairs, and the
# plain name keeps the module object addressable.
from ffmwiz import guibridge_crop_tk_canvas as _guibridge_crop_tk_canvas  # noqa: E402
from ffmwiz import guibridge_crop_tk_canvas  # noqa: E402,F401
from ffmwiz.guibridge_crop_tk_canvas import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_guibridge_crop_tk_canvas.__all__)
