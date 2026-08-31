"""Geometry and canvas painting for the legacy Tk Crop Editor.

Split out of `guibridge_crop_tk.py` for file size. That module still owns the
window, the widgets, the preview scheduler and the playback chain; everything
here either computes a pixel position from the editor's shared `state` dict or
draws on a `tk.Canvas`.

Every variable the nested versions captured is an explicit parameter now:

* `state` and `icon_cache` are the builder's own dicts, passed by reference on
  purpose -- this code mutates them in place (`state["left"]`, `state["drag"]`,
  `state["zoom_percent"]`, the icon cache) exactly as the closures did, and the
  builder reads the same keys back. Copying either one would drop every drag.
* the fixed sizes -- `frame_width`, `source_width`, `image_x`, `min_size`,
  `handle_hit_radius`, `edge_hit_radius` -- are computed once before the window
  exists and never rebound, so `CropGeometry` can hold them as attributes.
* `volume_slider` IS rebound by the builder (declared `None`, assigned when the
  control row is built), so `draw_crop_volume_slider` takes the widget as an
  argument on every call rather than capturing it. The same reasoning keeps
  `play_button`, `speaker_button` and the two tool buttons in the builder.
* `tk` is the builder's function-local `import tkinter as tk`, so it is passed
  in as well.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from ffmwiz import appio
from ffmwiz.core.constants import CURSOR_DIR_NAME, ICON_DIR_NAME
from ffmwiz.support.L01_paths import asset_path


def clamp_value(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def format_clock(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def load_tk_icon(tk: Any, icon_cache: dict[str, Any],
                 name: str) -> Any | None:
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


def make_round_canvas_button(tk: Any, parent: Any, text: str,
                             command: Callable[[], None],
                             width: int = 92, height: int = 32) -> Any:
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


def make_icon_canvas_button(tk: Any, load_icon: Callable[[str], Any],
                            volume_var: Any, mute_var: Any, parent: Any,
                            kind: str, command: Callable[[], None],
                            width: int = 46, height: int = 36) -> Any:
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


class CropGeometry:
    """The crop editor's pixel maths over the builder's live `state` dict.

    Holds the sizes fixed at construction plus a REFERENCE to `state`, so
    `clamp_margins()` keeps writing the margins the builder reads back through
    `current_margins()`. Copying the dict here would silently discard every
    drag the user makes.
    """

    def __init__(self, state: dict[str, Any], frame_width: int, frame_height: int,
                 source_width: int, source_height: int, image_x: int, image_y: int,
                 min_size: int, handle_hit_radius: int, edge_hit_radius: int) -> None:
        self.state = state
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.source_width = source_width
        self.source_height = source_height
        self.image_x = image_x
        self.image_y = image_y
        self.min_size = min_size
        self.handle_hit_radius = handle_hit_radius
        self.edge_hit_radius = edge_hit_radius

    def display_width(self) -> int:
        return max(1, round(self.frame_width * int(self.state["zoom_percent"]) / 100))

    def display_height(self) -> int:
        return max(1, round(self.frame_height * int(self.state["zoom_percent"]) / 100))

    def image_bounds(self) -> tuple[int, int, int, int]:
        return self.image_x, self.image_y, self.image_x + self.display_width(), self.image_y + self.display_height()

    def min_source_width(self) -> int:
        return max(1, round(self.source_width * self.min_size / self.display_width()))

    def min_source_height(self) -> int:
        return max(1, round(self.source_height * self.min_size / self.display_height()))

    def clamp_margins(self) -> None:
        self.state["left"] = int(clamp_value(self.state["left"], 0, max(0, self.source_width - self.state["right"] - self.min_source_width())))
        self.state["right"] = int(clamp_value(self.state["right"], 0, max(0, self.source_width - self.state["left"] - self.min_source_width())))
        self.state["top"] = int(clamp_value(self.state["top"], 0, max(0, self.source_height - self.state["bottom"] - self.min_source_height())))
        self.state["bottom"] = int(clamp_value(self.state["bottom"], 0, max(0, self.source_height - self.state["top"] - self.min_source_height())))

    def current_margins(self) -> tuple[int, int, int, int]:
        self.clamp_margins()
        return int(self.state["top"]), int(self.state["left"]), int(self.state["right"]), int(self.state["bottom"])

    def crop_rect(self) -> tuple[int, int, int, int]:
        _, _, image_right, image_bottom = self.image_bounds()
        left = self.image_x + round(self.state["left"] * self.display_width() / self.source_width)
        top = self.image_y + round(self.state["top"] * self.display_height() / self.source_height)
        right = image_right - round(self.state["right"] * self.display_width() / self.source_width)
        bottom = image_bottom - round(self.state["bottom"] * self.display_height() / self.source_height)
        return left, top, right, bottom

    def handle_points(self) -> dict[str, tuple[int, int]]:
        left, top, right, bottom = self.crop_rect()
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

    def hit_handle(self, x_pos: float, y_pos: float) -> str:
        left, top, right, bottom = self.crop_rect()
        in_x = left - self.edge_hit_radius <= x_pos <= right + self.edge_hit_radius
        in_y = top - self.edge_hit_radius <= y_pos <= bottom + self.edge_hit_radius

        corner_zones = {
            "nw": (left, top),
            "ne": (right, top),
            "se": (right, bottom),
            "sw": (left, bottom),
        }
        for name, (corner_x, corner_y) in corner_zones.items():
            if abs(x_pos - corner_x) <= self.handle_hit_radius and abs(y_pos - corner_y) <= self.handle_hit_radius:
                return name

        for name, (handle_x, handle_y) in self.handle_points().items():
            if abs(x_pos - handle_x) <= self.handle_hit_radius and abs(y_pos - handle_y) <= self.handle_hit_radius:
                return name

        if in_x and abs(y_pos - top) <= self.edge_hit_radius:
            return "n"
        if in_x and abs(y_pos - bottom) <= self.edge_hit_radius:
            return "s"
        if in_y and abs(x_pos - left) <= self.edge_hit_radius:
            return "w"
        if in_y and abs(x_pos - right) <= self.edge_hit_radius:
            return "e"
        return ""

    def point_in_image(self, x_pos: float, y_pos: float) -> bool:
        image_left, image_top, image_right, image_bottom = self.image_bounds()
        return image_left <= x_pos <= image_right and image_top <= y_pos <= image_bottom


def cursor_for_crop_handle(handle: str) -> str:
    if handle in {"e", "w"}:
        return "sb_h_double_arrow"
    if handle in {"n", "s"}:
        return "sb_v_double_arrow"
    if handle in {"nw", "se"}:
        return "size_nw_se"
    if handle in {"ne", "sw"}:
        return "size_ne_sw"
    return ""


def alt_held(event: Any) -> bool:
    """Return True if any Alt modifier is held in the event.state mask."""
    if event is None:
        return False
    mask = getattr(event, "state", 0) or 0
    # Windows: Alt = 0x20000. Linux/X11: Mod1 = 0x0008.
    return bool(mask & 0x20000) or bool(mask & 0x0008)


def set_crop_canvas_cursor(canvas: Any, tk: Any, cursor: str) -> None:
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


def update_crop_cursor(event: Any, canvas: Any, geo: CropGeometry,
                       state: dict[str, Any],
                       set_canvas_cursor: Callable[[str], None]) -> None:
    if state["drag"]:
        return
    x_pos = canvas.canvasx(event.x)
    y_pos = canvas.canvasy(event.y)
    handle = geo.hit_handle(x_pos, y_pos)
    if handle:
        set_canvas_cursor(cursor_for_crop_handle(handle))
    elif geo.point_in_image(x_pos, y_pos):
        if state["tool"] == "zoom":
            # Use the universally-supported "crosshair" cursor
            # for Zoom Tool. The Tk "icon" cursor used previously
            # appeared as a black square on some Windows builds.
            set_canvas_cursor("crosshair")
        else:
            set_canvas_cursor("open_hand")
    else:
        set_canvas_cursor("")


def apply_crop_zoom_centered_on(x_pos: float, y_pos: float, factor: float,
                                canvas: Any, root: Any, geo: CropGeometry,
                                state: dict[str, Any], redraw: Callable[[], None],
                                pad: int, min_zoom: int, max_zoom: int) -> None:
    """Zoom the preview by 'factor' (>1 zoom in, <1 zoom out)
    keeping the canvas point (x_pos, y_pos) at the same screen
    location after the zoom."""
    if factor <= 0 or abs(factor - 1.0) < 1e-6:
        return
    old_width = geo.display_width()
    old_height = geo.display_height()
    if old_width <= 0 or old_height <= 0:
        return
    # Image-space coordinates of the focused canvas point.
    rel_x = (canvas.canvasx(x_pos) - geo.image_x) / max(1, old_width)
    rel_y = (canvas.canvasy(y_pos) - geo.image_y) / max(1, old_height)
    rel_x = max(0.0, min(1.0, rel_x))
    rel_y = max(0.0, min(1.0, rel_y))

    new_zoom = int(round(int(state["zoom_percent"]) * factor))
    new_zoom = int(clamp_value(new_zoom, min_zoom, max_zoom))
    if new_zoom == int(state["zoom_percent"]):
        return
    state["zoom_percent"] = new_zoom
    state["photo_key"] = None  # force re-render at the new size
    redraw()
    # After redraw, re-center scroll so the focused image-relative
    # point lands under the original mouse position.
    root.update_idletasks()
    new_width = geo.display_width()
    new_height = geo.display_height()
    target_canvas_x = geo.image_x + rel_x * new_width
    target_canvas_y = geo.image_y + rel_y * new_height
    desired_x = target_canvas_x - x_pos
    desired_y = target_canvas_y - y_pos
    scroll_w = max(1, new_width + pad * 2)
    scroll_h = max(1, new_height + pad * 2)
    canvas.xview_moveto(max(0.0, min(1.0, desired_x / scroll_w)))
    canvas.yview_moveto(max(0.0, min(1.0, desired_y / scroll_h)))


def draw_crop_view(canvas: Any, geo: CropGeometry, state: dict[str, Any],
                   info: Any, zoom_var: Any, time_var: Any, time_label: Any,
                   timeline_duration: float, pad: int, handle_radius: int,
                   render_photo: Callable[[], None],
                   force_image_request: bool = True) -> None:
    """Repaint the preview image, the crop overlay and the status line."""
    geo.clamp_margins()
    if force_image_request:
        try:
            render_photo()
        except Exception as exc:
            appio.error(f"Could not refresh crop preview frame: {exc}")
    canvas.delete("all")

    image_left, image_top, image_right, image_bottom = geo.image_bounds()
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
    left, top, right, bottom = geo.crop_rect()
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

    for name, (x_pos, y_pos) in geo.handle_points().items():
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
    top_m, left_m, right_m, bottom_m = geo.current_margins()
    crop_width = geo.source_width - left_m - right_m
    crop_height = geo.source_height - top_m - bottom_m
    info.configure(
        text=(
            f"Crop margins: top={top_m}, left={left_m}, right={right_m}, bottom={bottom_m} "
            f"| output crop box: {crop_width}x{crop_height} | time {format_clock(float(state['timestamp']))}"
        )
    )
    canvas.configure(scrollregion=(0, 0, image_right + pad, image_bottom + pad))
    zoom_var.set(str(int(state["zoom_percent"])))
    time_var.set(float(state["timestamp"]))
    time_label.configure(text=f"{format_clock(float(state['timestamp']))} / {format_clock(timeline_duration)}")


def make_crop_drag_handlers(
    canvas: Any,
    geo: CropGeometry,
    state: dict[str, Any],
    set_canvas_cursor: Callable[[str], None],
    apply_zoom_centered_on: Callable[[float, float, float], None],
    update_cursor: Callable[[Any], None],
    redraw: Callable[..., None],
) -> tuple[Callable[[Any], None], Callable[[Any], None], Callable[[Any], None]]:
    """Build the canvas press/motion/release handlers, in binding order.

    They stay closures because Tk binds them as one-argument callbacks;
    what changed is that the names they close over arrive as parameters.
    """
    def begin_drag(event: Any) -> None:
        x_pos = canvas.canvasx(event.x)
        y_pos = canvas.canvasy(event.y)
        state["drag"] = geo.hit_handle(x_pos, y_pos)
        if state["drag"]:
            set_canvas_cursor(cursor_for_crop_handle(state["drag"]))
            canvas.focus_set()
            return

        if geo.point_in_image(x_pos, y_pos):
            if state["tool"] == "zoom":
                # Photoshop-style: clicking zooms in (or out with Alt).
                # Hold + drag tracks vertical motion for finer control.
                state["zoom_drag_y"] = event.y
                state["drag"] = "_zoom"
                # Single-click zoom step:
                factor = 1.0 / 1.25 if alt_held(event) else 1.25
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
                if alt_held(event):
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
        image_left, image_top, image_right, image_bottom = geo.image_bounds()
        left, top, right, bottom = geo.crop_rect()
        x_pos = clamp_value(canvas.canvasx(event.x), image_left, image_right)
        y_pos = clamp_value(canvas.canvasy(event.y), image_top, image_bottom)
        if "w" in mode:
            new_left = clamp_value(x_pos, image_left, right - geo.min_size)
            state["left"] = round((new_left - image_left) * geo.source_width / geo.display_width())
        if "e" in mode:
            new_right = clamp_value(x_pos, left + geo.min_size, image_right)
            state["right"] = round((image_right - new_right) * geo.source_width / geo.display_width())
        if "n" in mode:
            new_top = clamp_value(y_pos, image_top, bottom - geo.min_size)
            state["top"] = round((new_top - image_top) * geo.source_height / geo.display_height())
        if "s" in mode:
            new_bottom = clamp_value(y_pos, top + geo.min_size, image_bottom)
            state["bottom"] = round((image_bottom - new_bottom) * geo.source_height / geo.display_height())
        redraw(force_image_request=False)

    def end_drag(_event: Any) -> None:
        state["drag"] = ""
        state["pan"] = False
        state["zoom_drag_y"] = None

    return begin_drag, drag, end_drag


def draw_crop_volume_slider(volume_slider: Any, volume_var: Any,
                            active: bool = False) -> None:
    """Repaint the custom volume track. `volume_slider` is None until the
    builder creates it, which is why it arrives as an argument.
    """
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


__all__ = [
    'CropGeometry',
    'alt_held',
    'apply_crop_zoom_centered_on',
    'clamp_value',
    'cursor_for_crop_handle',
    'draw_crop_view',
    'draw_crop_volume_slider',
    'draw_round_rect',
    'format_clock',
    'load_tk_icon',
    'make_crop_drag_handlers',
    'make_icon_canvas_button',
    'make_round_canvas_button',
    'set_crop_canvas_cursor',
    'update_crop_cursor',
]
