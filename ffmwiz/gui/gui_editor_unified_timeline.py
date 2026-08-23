"""Timeline strip for the classic unified video editor.

Split out of `gui_editor_unified.py`, which had grown to 3993 lines as a
single builder function. This is a pure code move: the class is unchanged,
and it captured no state from that function -- only Qt symbols, which the
factory below rebinds identically.

The class is defined inside a factory rather than at module level because it
subclasses QWidget, and PySide6 must stay importable-on-demand: most CI jobs
install no Qt at all.
"""
from __future__ import annotations
import gui_common  # noqa: F401
from gui_common import *  # noqa: F401,F403


def build_unified_timeline_widget():
    """Define and return the widget class(es); PySide6 is imported here."""
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    Qt = QtCore.Qt
    Signal = QtCore.Signal
    QPointF = QtCore.QPointF
    QRectF = QtCore.QRectF
    QWidget = QtWidgets.QWidget
    QSizePolicy = QtWidgets.QSizePolicy

    class UnifiedTimelineWidget(QWidget):
        seek_requested = Signal(float)
        cut_selected = Signal(int)
        separator_selected = Signal(int)
        marker_moved = Signal(str, float)
        separator_moved = Signal(int, float)
        edit_finished = Signal()
        view_changed = Signal()

        PAD = 18

        def __init__(self, duration: float, fps: float):
            super().__init__()
            self.duration = max(0.001, float(duration or 0.001))
            self.fps = max(1.0, float(fps or 25.0))
            self.playhead = 0.0
            self.view_start = 0.0
            self.view_span = self.duration
            self.cut_ranges: list[tuple[float, float]] = []
            self.selected_cut = -1
            self.separator_points: list[float] = []
            self.selected_separator = -1
            self.selected_marker: str | None = None
            self.snap_enabled = True        # magnetic snapping to CTI / marks / splits
            self.wave_pix = None
            self._pcm = None                # decoded mono PCM (s16le) for the crisp waveform
            self._pcm_np = None             # numpy int16 view of _pcm (fast per-pixel min/max)
            self._pcm_rate = 4000
            self._pcm_gmax = 1
            self._wave_cache = None         # ((start, span, w, h), QPixmap) — crisp render
            self._reverse_view = False      # mirror the waveform when previewing reverse
            self._reverse_anchor = 0.0      # mirror is taken around THIS time (no jump)
            self._wave_timer = QtCore.QTimer(self)   # debounce: rebuild after motion settles
            self._wave_timer.setSingleShot(True)
            self._wave_timer.timeout.connect(self._rebuild_wave)
            self.chapters: list[dict[str, object]] = []
            self.join_segments: list[dict[str, object]] = []
            self.mark_in: float | None = None
            self.mark_out: float | None = None
            self._dragging = False
            self._drag_kind = None
            self._drag_target = None
            self._drag_origin = None
            self._last_drag_pos = None
            self._drag_playhead_start = 0.0
            self._vertical_zoom_lock = False
            self._cti_zoom_focus_time = 0.0
            self._cti_zoom_start_y = 0.0
            self._start_span = self.view_span
            self.setMinimumHeight(150)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setMouseTracking(True)
            self.setStyleSheet(
                f"background-color: {PALETTE['timeline_bg']};"
                f"border: 1px solid {PALETTE['border_strong']}; border-radius: 8px;"
            )

        def set_waveform(self, path: Path) -> None:
            pix = QtGui.QPixmap(str(path))
            if not pix.isNull():
                self.wave_pix = pix
                self.update()

        def set_pcm(self, data, rate) -> None:
            self._pcm = data or None
            self._pcm_rate = max(1, int(rate))
            self._wave_cache = None
            self._pcm_np = None
            self._env_min = None
            self._env_max = None
            self._env_step = 256
            if self._pcm:
                try:
                    import numpy as _np
                    self._pcm_np = _np.frombuffer(self._pcm, dtype=_np.int16)
                    # int() BEFORE negating: -np.int16(-32768) overflows (stays negative).
                    self._pcm_gmax = max(1, int(self._pcm_np.max()), -int(self._pcm_np.min()))
                    # Decimated min/max envelope: lets zoomed-out views render from a
                    # tiny array instead of scanning all ~22M samples (keeps it ~1ms).
                    D = self._env_step
                    m = self._pcm_np.size // D
                    if m >= 2:
                        block = self._pcm_np[:m * D].reshape(m, D)
                        self._env_max = block.max(axis=1)
                        self._env_min = block.min(axis=1)
                except Exception:
                    self._pcm_np = None
                    # numpy is only an accelerator: fall back to the shared
                    # stdlib peak (audioop is gone in 3.13 — D09).
                    self._pcm_gmax = max(1, pcm_peak(self._pcm))
            self.update()

        def _render_wave_pixmap(self, start_t, span, width_px, wh):
            """Render the waveform for one view into a QPixmap.

            ROOT FIX for "heavy": the common min/max case is filled with a SINGLE
            vectorised numpy op (no thousands-of-points QPainterPath loop), so a
            full render is ~1ms at any zoom. Only the extreme-zoom smooth curve
            (few samples) uses a short cubic path. Cheap enough to run inline on
            the paint path — no worker thread, so no GIL contention."""
            width_px = max(1, int(width_px)); wh = max(1, int(wh))
            cyl = wh / 2.0
            # Leave a clear top/bottom margin inside the waveform lane so even a
            # ceiling-clamped (loud) peak never touches the lane edges / borders.
            half = wh * 0.42
            # Reverse preview: mirror the WHOLE clip around its midpoint — one single
            # consistent flip for the entire timeline (timeline t <-> source[dur - t]),
            # so moving the CTI never re-mirrors. Render the mirrored window + flip it.
            flip = bool(getattr(self, "_reverse_view", False))
            if flip:
                start_t = max(0.0, min(self.duration, self.duration - (start_t + span)))

            def _out(img):
                return QtGui.QPixmap.fromImage(img.mirrored(True, False) if flip else img)

            def _blank():
                im = QtGui.QImage(width_px, wh, QtGui.QImage.Format_ARGB32)
                im.fill(0)
                return QtGui.QPixmap.fromImage(im)

            if not self._pcm:
                return _blank()
            rate = self._pcm_rate
            total = len(self._pcm) // 2
            # Absolute amplitude: scale against full-scale int16, NOT the clip's
            # own peak. This way quiet audio renders a short waveform and loud
            # audio a tall one, instead of every clip being normalized to fill the
            # same height regardless of its real loudness. The value is clamped so
            # amplitude above the ceiling reference caps at full height.
            gmax = WAVEFORM_CEILING_PEAK
            col = QtGui.QColor(PALETTE["waveform"])

            if self._pcm_np is not None:
                try:
                    import numpy as _np
                    s0 = max(0, min(total, int(start_t * rate)))
                    s1 = max(s0 + 1, min(total, int((start_t + span) * rate)))
                    seg = self._pcm_np[s0:s1]
                    n = int(seg.size)
                    if 1 < n < width_px:
                        # Fewer samples than pixels: interpolate to one value per pixel
                        # with a Catmull-Rom spline (ROUNDED peaks, not angular like
                        # linear), then fill a 2px connected trace. All vectorised numpy
                        # -> ~1ms at any zoom (no per-point cubic/polyline, which was 16-60ms).
                        pos = _np.arange(width_px, dtype=_np.float64) * (n - 1) / max(1.0, width_px - 1)
                        i = _np.floor(pos).astype(_np.int64)
                        frac = pos - i
                        s = seg.astype(_np.float64)
                        p0 = s[_np.clip(i - 1, 0, n - 1)]
                        p1 = s[_np.clip(i, 0, n - 1)]
                        p2 = s[_np.clip(i + 1, 0, n - 1)]
                        p3 = s[_np.clip(i + 2, 0, n - 1)]
                        f2 = frac * frac
                        f3 = f2 * frac
                        v = (0.5 * (2.0 * p1 + (-p0 + p2) * frac
                                    + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * f2
                                    + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * f3)) / gmax
                        v = _np.clip(v, -1.0, 1.0)   # cap at the ceiling reference
                        vpx = cyl - v * half
                        v2 = _np.empty_like(vpx)
                        v2[:-1] = vpx[1:]; v2[-1] = vpx[-1]   # connect each column to the next
                        top = _np.minimum(vpx, v2) - 1.0      # ~2px line (1px each side)
                        bot = _np.maximum(vpx, v2) + 1.0
                        # Anti-alias in numpy: per-pixel fractional coverage -> alpha, so
                        # the curve's edges are smooth instead of stair-stepped/pixelated.
                        rows = _np.arange(wh, dtype=_np.float64)[:, None]
                        cov = _np.clip(_np.minimum(rows + 1.0, bot[None, :]) - _np.maximum(rows, top[None, :]), 0.0, 1.0)
                        alpha = (cov * 255.0).astype(_np.uint32)
                        buf = (alpha << 24) | _np.uint32(0x003A8BFF)   # ARGB w/ soft edges
                        img = QtGui.QImage(buf.tobytes(), width_px, wh, width_px * 4,
                                           QtGui.QImage.Format_ARGB32).copy()
                        return _out(img)
                    # Per-pixel min/max bars, filled in ONE numpy op. For zoomed-out
                    # views read the decimated envelope so we never scan all samples.
                    use_env = (self._env_max is not None
                               and n > width_px * self._env_step)
                    if n <= 0:
                        top = _np.zeros(width_px, dtype=_np.float64)
                        bot = top
                    elif use_env:
                        D = self._env_step
                        e0 = max(0, min(self._env_max.size, s0 // D))
                        e1 = max(e0 + 1, min(self._env_max.size, s1 // D))
                        em = self._env_max[e0:e1]; en = self._env_min[e0:e1]
                        m_env = em.size
                        idx = (_np.arange(width_px + 1, dtype=_np.int64) * m_env) // (width_px + 1)
                        _np.clip(idx, 0, m_env - 1, out=idx)
                        top = _np.maximum.reduceat(em, idx)[:width_px].astype(_np.float64) / gmax
                        bot = _np.minimum.reduceat(en, idx)[:width_px].astype(_np.float64) / gmax
                    else:
                        idx = (_np.arange(width_px + 1, dtype=_np.int64) * n) // (width_px + 1)
                        _np.clip(idx, 0, n - 1, out=idx)
                        top = _np.maximum.reduceat(seg, idx)[:width_px].astype(_np.float64) / gmax
                        bot = _np.minimum.reduceat(seg, idx)[:width_px].astype(_np.float64) / gmax
                    # Cap at the ceiling reference so loud peaks clamp instead of
                    # overflowing the waveform area.
                    top = _np.clip(top, -1.0, 1.0)
                    bot = _np.clip(bot, -1.0, 1.0)
                    top_px = cyl - top * half
                    bot_px = cyl - bot * half
                    # Always span the centre line so adjacent columns stay connected — a
                    # continuous filled waveform instead of disjoint specks when there
                    # are only a couple of samples per pixel (sub-second zoom).
                    lo = _np.minimum(_np.minimum(top_px, bot_px), cyl)
                    hi = _np.maximum(_np.maximum(top_px, bot_px), cyl)
                    y_lo = _np.clip(_np.floor(lo), 0, wh - 1).astype(_np.int32)
                    y_hi = _np.clip(_np.ceil(hi), 0, wh - 1).astype(_np.int32)
                    rows = _np.arange(wh, dtype=_np.int32)[:, None]
                    mask = (rows >= y_lo[None, :]) & (rows <= y_hi[None, :])
                    buf = _np.zeros((wh, width_px), dtype=_np.uint32)
                    # ARGB (little-endian / Windows) from the shared waveform token.
                    buf[mask] = 0xFF000000 | (QtGui.QColor(PALETTE["waveform"]).rgb() & 0xFFFFFF)
                    img = QtGui.QImage(buf.tobytes(), width_px, wh, width_px * 4,
                                       QtGui.QImage.Format_ARGB32).copy()
                    return _out(img)
                except Exception:
                    pass  # fall through to the stdlib path on any numpy mishap

            # No numpy -> stdlib fallback (rare): cheap vertical bars via drawLines.
            img = QtGui.QImage(width_px, wh, QtGui.QImage.Format_ARGB32)
            img.fill(0)
            try:
                lines = []
                for px in range(width_px):
                    t0 = start_t + (px / width_px) * span
                    t1 = start_t + ((px + 1) / width_px) * span
                    s0 = max(0, min(total - 1, int(t0 * rate))) if total else 0
                    s1 = max(s0 + 1, min(total, int(t1 * rate)))
                    frag = self._pcm[s0 * 2:s1 * 2]
                    mn, mx = pcm_minmax(frag) if frag else (0, 0)
                    mxv = max(-1.0, min(1.0, mx / gmax))
                    mnv = max(-1.0, min(1.0, mn / gmax))
                    lines.append(QtCore.QLineF(px + 0.5, cyl - mxv * half,
                                               px + 0.5, cyl - mnv * half))
                pp = QtGui.QPainter(img)
                pp.setPen(QtGui.QPen(col, 1.0))
                pp.drawLines(lines)
                pp.end()
            except Exception:
                pass
            return _out(img)

        def set_reverse_view(self, on: bool, anchor: float = 0.0) -> None:
            # Whole-clip mirror around the midpoint; `anchor` kept for call
            # compatibility but unused (single consistent flip).
            on = bool(on)
            if self._reverse_view == on:
                return
            self._reverse_view = on
            self._wave_cache = None     # force a re-render with the new orientation
            self.update()

        def _schedule_wave_rebuild(self):
            # Fire ~60ms after motion (debounce); if motion is sustained, don't keep
            # resetting it — let it fire periodically so the render chases the view.
            if not self._wave_timer.isActive():
                self._wave_timer.start(60)

        def _rebuild_wave(self):
            """Render the crisp waveform for the current view and cache it.

            Runs ~1ms (vectorised), so doing it inline after a 60ms debounce is
            cheap. Skips work when the cache already matches the view."""
            if not self._pcm:
                return
            wave = self._wave_rect()
            width_px = max(1, int(wave.width()))
            wh = max(1, int(wave.height()))
            start_t = self.view_start
            span = max(1e-9, self.view_span)
            key = (round(start_t, 4), round(span, 5), width_px, wh)
            if self._wave_cache is not None and self._wave_cache[0] == key:
                return
            self._wave_cache = (key, self._render_wave_pixmap(start_t, span, width_px, wh))
            self.update()

        def set_playhead(self, seconds: float, follow: bool = False) -> None:
            t = max(0.0, min(self.duration, float(seconds)))
            # Snap to video midpoint when within a small pixel threshold.
            center_time = self.duration / 2.0
            if self.view_span > 0:
                wave_w = self._wave_rect().width()
                pixels_per_second = wave_w / max(0.001, self.view_span)
                snap_threshold_px = 6
                if abs(t - center_time) * pixels_per_second < snap_threshold_px:
                    t = center_time
            self.playhead = t
            if follow:
                self._ensure_visible(self.playhead)
            self.update()

        def set_cut_ranges(self, ranges) -> None:
            self.cut_ranges = normalize_ranges(ranges, self.duration)
            self.selected_cut = min(self.selected_cut, len(self.cut_ranges) - 1)
            self.update()

        def set_separator_points(self, points) -> None:
            cleaned: list[float] = []
            for value in points or []:
                try:
                    point = float(value)
                except (TypeError, ValueError):
                    continue
                if 1e-6 < point < self.duration - 1e-6:
                    cleaned.append(point)
            cleaned = sorted(set(round(point, 6) for point in cleaned))
            self.separator_points = cleaned
            self.selected_separator = min(self.selected_separator, len(self.separator_points) - 1)
            self.update()

        def set_chapters(self, chapters) -> None:
            self.chapters = list(chapters or [])
            self.update()

        def set_join_segments(self, segments) -> None:
            self.join_segments = list(segments or [])
            self.update()

        def set_marks(self, mark_in, mark_out) -> None:
            self.mark_in = None if mark_in is None else max(0.0, min(self.duration, float(mark_in)))
            self.mark_out = None if mark_out is None else max(0.0, min(self.duration, float(mark_out)))
            if self.selected_marker == "in" and self.mark_in is None:
                self.selected_marker = None
            if self.selected_marker == "out" and self.mark_out is None:
                self.selected_marker = None
            self.update()

        def _timeline_rect(self):
            return QRectF(
                self.PAD,
                8,
                max(1, self.width() - self.PAD * 2),
                max(150, self.height() - 16),
            )

        def _ruler_rect(self):
            r = self._timeline_rect()
            return QRectF(r.left() + 8, r.top() + 8, max(1, r.width() - 16), 38)

        def _wave_rect(self):
            r = self._timeline_rect()
            return QRectF(r.left() + 8, r.top() + 72, max(1, r.width() - 16), max(76, r.height() - 84))

        def _cut_bar_rect(self, start_s: float, end_s: float):
            wave = self._wave_rect()
            x1 = self._time_to_x(start_s)
            x2 = self._time_to_x(end_s)
            return QRectF(x1, wave.top() - 22, max(4, x2 - x1), 18)

        def _clamp_view(self):
            self.view_span = max(0.05, min(self.duration, self.view_span))
            self.view_start = max(0.0, min(max(0.0, self.duration - self.view_span), self.view_start))

        def _ensure_visible(self, t):
            # PAGE scroll: when the playhead leaves the visible window, jump the view
            # one page so the playhead reappears at the LEADING edge and keeps sweeping
            # (instead of gluing the playhead to the edge and scrolling continuously).
            self._clamp_view()
            if t > self.view_start + self.view_span:
                self.view_start = t                      # forward: playhead -> left edge
            elif t < self.view_start:
                self.view_start = t - self.view_span     # backward: playhead -> right edge
            self._clamp_view()
            self.view_changed.emit()

        def _time_to_x(self, t):
            r = self._wave_rect()
            return r.left() + (float(t) - self.view_start) / max(0.001, self.view_span) * r.width()

        def _x_to_time(self, x):
            r = self._wave_rect()
            ratio = max(0.0, min(1.0, (float(x) - r.left()) / max(1, r.width())))
            return self.view_start + ratio * self.view_span

        def _snap_targets(self, exclude_separator=None, exclude_marker=None):
            """Times the magnet can snap to: CTI, mark in/out, split points,
            joined-video boundaries, clip ends."""
            targets = [self.playhead, 0.0, self.duration]
            if self.mark_in is not None and exclude_marker != "in":
                targets.append(float(self.mark_in))
            if self.mark_out is not None and exclude_marker != "out":
                targets.append(float(self.mark_out))
            for i, sp in enumerate(self.separator_points):
                if i != exclude_separator:
                    targets.append(float(sp))
            # Joined-video boundaries: markers/CTI snap to where each new video
            # starts (a little stickiness so cuts/marks land exactly on a join).
            for segment in getattr(self, "join_segments", None) or []:
                start_t = float(segment.get("start", 0.0))
                if start_t > 1e-6:
                    targets.append(start_t)
            return targets

        def _snap_time(self, seconds, exclude_separator=None, exclude_marker=None):
            """Snap a time to the nearest magnet target (CTI / marks / splits / clip
            ends) when magnetic snapping is enabled. The tolerance is PURELY pixel
            based, so zooming in tightens the pull (precise adjustment); switch
            self.snap_enabled off for completely free-hand placement."""
            seconds = max(0.0, min(self.duration, float(seconds)))
            if not self.snap_enabled:
                return seconds
            seconds_per_px = self.view_span / max(1.0, self._wave_rect().width())
            tolerance = seconds_per_px * 8.0          # ~8 px pull; shrinks as you zoom in
            best, best_dist = seconds, tolerance
            for target in self._snap_targets(exclude_separator, exclude_marker):
                d = abs(seconds - target)
                if d <= best_dist:
                    best, best_dist = target, d
            return best

        def _snap_time_to_cti(self, seconds: float) -> float:
            return self._snap_time(seconds)

        def set_zoom_ratio(self, ratio, focus_time=None):
            old_span = self.view_span
            ratio = max(1.0, min(self.duration / 0.05, float(ratio)))   # allow the same depth as drag/wheel
            focus = self.playhead if focus_time is None else max(0.0, min(self.duration, float(focus_time)))
            focus_ratio = (focus - self.view_start) / max(0.001, old_span)
            self.view_span = self.duration / ratio
            self.view_start = focus - focus_ratio * self.view_span
            self._clamp_view()
            self.update()
            self.view_changed.emit()

        def zoom_ratio(self):
            return max(1.0, self.duration / max(0.001, self.view_span))

        def set_view_start(self, seconds: float) -> None:
            self.view_start = float(seconds)
            self._clamp_view()
            self.update()
            self.view_changed.emit()

        def scroll_view(self, seconds: float) -> None:
            self.set_view_start(self.view_start + float(seconds))

        def paintEvent(self, _event):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.fillRect(self.rect(), QtGui.QColor(PALETTE["timeline_bg"]))
            r = self._timeline_rect()
            ruler = self._ruler_rect()
            wave = self._wave_rect()
            p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["border"]), 1))
            p.setBrush(QtGui.QBrush(QtGui.QColor(PALETTE["timeline_track"])))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["border_soft"]), 1))
            p.setBrush(QtGui.QBrush(QtGui.QColor(PALETTE["panel"])))
            p.drawRoundedRect(ruler, 5, 5)
            p.setBrush(QtGui.QBrush(QtGui.QColor(PALETTE["timeline_bg"])))
            p.drawRoundedRect(wave, 4, 4)
            grid_pen = QtGui.QPen(QtGui.QColor(PALETTE["border"]), 1)
            p.setPen(grid_pen)
            for i in range(1, 12):
                x = wave.left() + wave.width() * i / 12.0
                p.drawLine(QPointF(x, wave.top()), QPointF(x, wave.bottom()))
            for i in range(1, 4):
                y = wave.top() + wave.height() * i / 4.0
                p.drawLine(QPointF(wave.left(), y), QPointF(wave.right(), y))
            span = max(0.001, self.view_span)
            target_ticks = max(8, min(15, int(ruler.width() // 70)))
            approx = span / max(1, target_ticks)
            exp = math.floor(math.log10(max(approx, 0.001)))
            base = 10 ** exp
            step = base
            for candidate in (1, 2, 2.5, 5, 10):
                step = candidate * base
                if span / step <= target_ticks:
                    break
            start = self.view_start
            end = start + span
            for idx, segment in enumerate(self.join_segments):
                seg_start = float(segment.get("start", 0.0))
                seg_end = float(segment.get("end", 0.0))
                if seg_end < start or seg_start > end:
                    continue
                x1 = self._time_to_x(max(seg_start, start))
                x2 = self._time_to_x(min(seg_end, end))
                if x2 <= x1:
                    continue
                fill = QtGui.QColor(PALETTE["panel"] if idx % 2 == 0 else PALETTE["panel_alt"])
                fill.setAlpha(150)
                p.setBrush(QtGui.QBrush(fill))
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["border"]), 1))
                p.drawRect(QRectF(x1, ruler.top(), x2 - x1, wave.bottom() - ruler.top()))
            p.setFont(QtGui.QFont("Segoe UI Semibold", 9))
            tick_label_width = 100
            tick_positions: list[float] = []
            last_label_right: float = -999.0  # Track rightmost label edge to prevent overlap.
            t = math.ceil(start / step) * step
            while t <= end + 1e-6:
                x = self._time_to_x(t)
                if abs(t - start) <= max(0.001, step * 0.04) or abs(t - end) <= max(0.001, step * 0.04):
                    t += step
                    continue
                label_left = max(ruler.left(), min(x - tick_label_width / 2, ruler.right() - tick_label_width))
                # Skip label if it overlaps the previous one.
                if label_left < last_label_right + 8:
                    t += step
                    continue
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["tick_hi"]), 1))
                p.drawLine(QPointF(x, ruler.bottom() - 11), QPointF(x, ruler.bottom() - 3))
                tick_positions.append(float(x))
                p.drawText(
                    QRectF(
                        label_left,
                        ruler.top() + 5,
                        tick_label_width,
                        18,
                    ),
                    Qt.AlignCenter,
                    seconds_to_timecode(t),
                )
                last_label_right = label_left + tick_label_width
                t += step
            for edge_t in (start, end):
                x = self._time_to_x(edge_t)
                label_left = max(ruler.left(), min(x - tick_label_width / 2, ruler.right() - tick_label_width))
                label_right = label_left + tick_label_width
                # Skip if overlaps any existing tick label or the previous edge label.
                if label_left < last_label_right + 8:
                    continue
                too_close = any(abs(x - tx) < tick_label_width * 0.85 for tx in tick_positions)
                if too_close:
                    continue
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["tick_hi"]), 1))
                p.drawLine(QPointF(x, ruler.bottom() - 12), QPointF(x, ruler.bottom() - 2))
                label_rect = QRectF(label_left, ruler.top() + 5, tick_label_width, 18)
                p.drawText(label_rect, Qt.AlignCenter, seconds_to_timecode(edge_t))
                last_label_right = label_right
                tick_positions.append(float(x))
            for chapter_idx, chapter in enumerate(self.chapters, start=1):
                cs = float(chapter.get("start", 0.0))
                if cs < start or cs > end:
                    continue
                x = self._time_to_x(cs)
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["chapter"]), 2, Qt.DashLine))
                p.drawLine(QPointF(x, ruler.top() + 4), QPointF(x, ruler.bottom() - 4))
                title = str(chapter.get("title") or "Chapter")
                number = chapter_idx
                label = f"Chapter {number}" if title == f"Chapter {number}" else f"Chapter {number}: {title}"
                label = short_gui_label(label, 32)
                p.setFont(QtGui.QFont("Segoe UI Semibold", 8))
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["chapter_text"]), 1))
                p.drawText(
                    QRectF(max(ruler.left(), min(x + 4, ruler.right() - 210)), ruler.top() + 20, 210, 16),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    label,
                )
            if self._pcm:
                # Render the crisp waveform for the EXACT current view (~1ms, cached).
                # No stretched transient blit — that's what briefly distorted the
                # waveform mid-zoom — and the render is cheap enough to do per view.
                width_px = max(1, int(wave.width()))
                wh = max(1, int(wave.height()))
                start_t = self.view_start
                span = max(1e-9, self.view_span)
                cur = (round(start_t, 4), round(span, 5), width_px, wh)
                c = self._wave_cache
                if c is None or c[0] != cur:
                    c = (cur, self._render_wave_pixmap(start_t, span, width_px, wh))
                    self._wave_cache = c
                p.drawPixmap(int(wave.left()), int(wave.top()), c[1])
            else:
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["text_mute"])))
                p.drawText(wave, Qt.AlignCenter, "Audio waveform loading...")
            grid_overlay = QtGui.QColor(PALETTE["border"])
            grid_overlay.setAlpha(120)
            p.setPen(QtGui.QPen(grid_overlay, 1))
            for i in range(1, 12):
                x = wave.left() + wave.width() * i / 12.0
                p.drawLine(QPointF(x, wave.top()), QPointF(x, wave.bottom()))
            for i in range(1, 4):
                y = wave.top() + wave.height() * i / 4.0
                p.drawLine(QPointF(wave.left(), y), QPointF(wave.right(), y))
            tick_guide = QtGui.QColor(PALETTE["tick_lo"])
            tick_guide.setAlpha(78)
            p.setPen(QtGui.QPen(tick_guide, 1))
            for x in tick_positions:
                p.drawLine(QPointF(x, ruler.bottom()), QPointF(x, wave.bottom()))
            for idx, (s, e) in enumerate(self.cut_ranges):
                if e < start or s > end:
                    continue
                x1 = self._time_to_x(max(s, start))
                x2 = self._time_to_x(min(e, end))
                color = QtGui.QColor(PALETTE["cut_red"] if idx == self.selected_cut else PALETTE["cut_red_dim"])
                color.setAlpha(150 if idx == self.selected_cut else 105)
                border = QtGui.QColor(PALETTE["danger_text"] if idx == self.selected_cut else PALETTE["cut_red"])
                border.setAlpha(230 if idx == self.selected_cut else 190)
                p.setPen(QtGui.QPen(border, 2))
                p.setBrush(QtGui.QBrush(color))
                cut_rect = QRectF(x1, wave.top(), max(3, x2 - x1), wave.height()).adjusted(0.5, 0.5, -0.5, -0.5)
                p.drawRoundedRect(cut_rect, 5, 5)
                if cut_rect.width() >= 34:
                    p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["text_on_accent"]), 1))
                    p.setFont(QtGui.QFont("Segoe UI Semibold", 8))
                    p.drawText(cut_rect.adjusted(5, 3, -5, -3), Qt.AlignCenter, f"Cut #{idx + 1}")
                bar_rect = self._cut_bar_rect(max(s, start), min(e, end))
                bar_fill = QtGui.QColor(PALETTE["cut_bar"] if idx == self.selected_cut else PALETTE["cut_bar_dim"])
                bar_fill.setAlpha(245 if idx == self.selected_cut else 210)
                bar_border = QtGui.QColor(PALETTE["danger_text"] if idx == self.selected_cut else PALETTE["cut_red"])
                bar_border.setAlpha(245 if idx == self.selected_cut else 190)
                p.setBrush(QtGui.QBrush(bar_fill))
                p.setPen(QtGui.QPen(bar_border, 2 if idx == self.selected_cut else 1))
                p.drawRoundedRect(bar_rect, 4, 4)
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["text_on_accent"]), 1))
                p.setFont(QtGui.QFont("Segoe UI Semibold", 8))
                p.drawText(bar_rect.adjusted(7, 1, -7, -1), Qt.AlignCenter, f"Cut #{idx + 1}")
                handle_fill = QtGui.QColor(PALETTE["text_on_accent"])
                handle_fill.setAlpha(245 if idx == self.selected_cut else 205)
                handle_border = QtGui.QColor(PALETTE["danger_text"])
                handle_border.setAlpha(245 if idx == self.selected_cut else 190)
                p.setBrush(QtGui.QBrush(handle_fill))
                p.setPen(QtGui.QPen(handle_border, 1))
                y_top = bar_rect.top() + 1
                y_bottom = bar_rect.bottom() - 1
                left_x = bar_rect.left()
                right_x = bar_rect.right()
                tab_w = min(13.0, max(8.0, bar_rect.width() * 0.22))
                p.drawPolygon(QtGui.QPolygonF([
                    QPointF(left_x, y_top),
                    QPointF(left_x + tab_w, y_top),
                    QPointF(left_x, y_bottom),
                ]))
                p.drawPolygon(QtGui.QPolygonF([
                    QPointF(right_x, y_top),
                    QPointF(right_x - tab_w, y_top),
                    QPointF(right_x, y_bottom),
                ]))
            for segment in self.join_segments:
                boundary = float(segment.get("start", 0.0))
                if boundary <= 1e-6 or boundary < start or boundary > end:
                    continue
                x = self._time_to_x(boundary)
                # Subtle dotted boundary between joined videos: thin, faint and
                # small (like the centre guide) instead of a thick coloured bar,
                # so many joined clips stay readable and uncluttered.
                backing = QtGui.QColor(6, 10, 16, 200)
                line_color = QtGui.QColor(232, 178, 120, 200)  # amber, a little stronger
                top_y = ruler.bottom() + 2
                bot_y = wave.bottom() + 4
                p.setPen(QtGui.QPen(backing, 2))
                p.drawLine(QPointF(x, top_y), QPointF(x, bot_y))
                p.setPen(QtGui.QPen(line_color, 1, Qt.DotLine))
                p.drawLine(QPointF(x, top_y), QPointF(x, bot_y))
                # Centre the label on the dotted line (like the Center guide),
                # not left-aligned beside it.
                p.setPen(QtGui.QPen(QtGui.QColor(240, 200, 150, 235), 1))
                p.setFont(QtGui.QFont("Segoe UI", 7))
                label = short_gui_label(str(segment.get("label") or "Video"), 14)
                _lbl_w = 84.0
                p.drawText(QRectF(x - _lbl_w / 2.0, top_y, _lbl_w, 12), Qt.AlignCenter, label)
            for idx, value in enumerate(self.separator_points):
                if value < start or value > end:
                    continue
                x = self._time_to_x(value)
                selected = idx == self.selected_separator
                sep_color = QtGui.QColor(PALETTE["split_marker_sel" if selected else "split_marker"])
                stroke = QtGui.QColor(PALETTE["playhead_halo"])
                stroke.setAlpha(150 if selected else 95)
                line_top = ruler.bottom() + (14 if selected else 12)
                line_bottom = wave.bottom() + (8 if selected else 5)
                p.setPen(QtGui.QPen(stroke, 8 if selected else 6))
                p.drawLine(QPointF(x, line_top), QPointF(x, line_bottom))
                p.setPen(QtGui.QPen(sep_color, 4 if selected else 3))
                p.drawLine(QPointF(x, line_top), QPointF(x, line_bottom))
                p.setBrush(QtGui.QBrush(sep_color))
                p.setPen(QtGui.QPen(stroke, 2))
                half = 12 if selected else 9
                tip = ruler.bottom() + (14 if selected else 12)
                p.drawPolygon(QtGui.QPolygonF([
                    QPointF(x - half, ruler.bottom() - 1),
                    QPointF(x + half, ruler.bottom() - 1),
                    QPointF(x, tip),
                ]))
                p.setPen(QtGui.QPen(sep_color, 1))
                p.setFont(QtGui.QFont("Segoe UI Semibold", 9 if selected else 8))
                p.drawText(QRectF(x + 5, ruler.bottom() + 2, 72, 16), Qt.AlignLeft | Qt.AlignVCenter, "SPLIT")
            for label, value, color_name in (
                ("IN", self.mark_in, "marker_in"),
                ("OUT", self.mark_out, "marker_out"),
            ):
                if value is None or value < start or value > end:
                    continue
                x = self._time_to_x(value)
                marker_color = QtGui.QColor(PALETTE[color_name])
                selected = self.selected_marker == label.lower()
                stroke = QtGui.QColor(PALETTE["playhead_halo"])
                stroke.setAlpha(155 if selected else 100)
                # Anchor at the ruler bottom (exactly like SPLIT) so IN/OUT are the SAME
                # height as the split markers instead of starting lower at the wave top.
                line_top = ruler.bottom() + (14 if selected else 12)
                line_bottom = wave.bottom() + (8 if selected else 5)
                p.setPen(QtGui.QPen(stroke, 8 if selected else 6))
                p.drawLine(QPointF(x, line_top), QPointF(x, line_bottom))
                p.setPen(QtGui.QPen(marker_color, 4 if selected else 3))
                p.drawLine(QPointF(x, line_top), QPointF(x, line_bottom))
                p.setBrush(QtGui.QBrush(marker_color))
                p.setPen(QtGui.QPen(stroke, 2))
                half = 12 if selected else 9
                tip = ruler.bottom() + (14 if selected else 12)
                p.drawPolygon(QtGui.QPolygonF([
                    QPointF(x - half, ruler.bottom() - 1),
                    QPointF(x + half, ruler.bottom() - 1),
                    QPointF(x, tip),
                ]))
                p.setPen(QtGui.QPen(marker_color, 1))
                p.setFont(QtGui.QFont("Segoe UI Semibold", 9 if selected else 8))
                p.drawText(QRectF(x + 5, ruler.bottom() + 2, 42, 16), Qt.AlignLeft | Qt.AlignVCenter, label)
            # Video midpoint guide: fixed at the exact centre of the entire video
            # duration. Drawn like the CTI/playhead (arrow at the top of the ruler
            # plus a full-height line) in a distinct violet so it is always clearly
            # visible and not confused with the red playhead.
            center_time = self.duration / 2.0
            cx = self._time_to_x(center_time)
            if ruler.left() - 2 <= cx <= ruler.right() + 2:
                guide_color = QtGui.QColor(PALETTE["center_guide"])
                guide_backing = QtGui.QColor(10, 6, 18, 220)
                arrow_top = ruler.top() + 1
                arrow_tip = ruler.top() + 19
                # Full-height guide line (same span as the playhead). A dark backing
                # line gives contrast where it crosses the bright blue waveform.
                p.setPen(QtGui.QPen(guide_backing, 3))
                p.drawLine(QPointF(cx, arrow_tip), QPointF(cx, wave.bottom() + 8))
                p.setPen(QtGui.QPen(guide_color, 2, Qt.DashLine))
                p.drawLine(QPointF(cx, arrow_tip), QPointF(cx, wave.bottom() + 8))
                # Down-pointing arrow at the top of the ruler, the same height as
                # the CTI arrow.
                p.setBrush(QtGui.QBrush(guide_color))
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["bg"]), 1))
                p.drawPolygon(QtGui.QPolygonF([
                    QPointF(cx - 9, arrow_top),
                    QPointF(cx + 9, arrow_top),
                    QPointF(cx, arrow_tip),
                ]))
                # Timecode pill centered horizontally on the centre marker, placed
                # just below the ruler tick numbers (in the ruler->waveform gap)
                # so it reads as the marker's label and never collides with the
                # time labels above it.
                _ctc = seconds_to_timecode(center_time)
                p.setFont(QtGui.QFont("Segoe UI Semibold", 8))
                pill_w = 124
                pill_h = 16
                pill_y = ruler.bottom() + 3
                pill_x = max(ruler.left() + 2, min(cx - pill_w / 2.0, ruler.right() - pill_w - 2))
                _pill = QRectF(pill_x, pill_y, pill_w, pill_h)
                p.setBrush(QtGui.QBrush(QtGui.QColor(20, 12, 32, 235)))
                p.setPen(QtGui.QPen(guide_color, 1))
                p.drawRoundedRect(_pill, 4, 4)
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["center_guide_text"]), 1))
                p.drawText(_pill, Qt.AlignCenter, f"Center  {_ctc}")
            ph_x = self._time_to_x(self.playhead)
            if wave.left() - 4 <= ph_x <= wave.right() + 4:
                halo = QtGui.QColor(PALETTE["playhead_halo"])
                halo.setAlpha(210)
                arrow_top = ruler.top() + 1
                arrow_tip = ruler.top() + 19
                p.setPen(QtGui.QPen(halo, 9))
                p.drawLine(QPointF(ph_x, arrow_tip), QPointF(ph_x, wave.bottom() + 8))
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["playhead"]), 5))
                p.drawLine(QPointF(ph_x, arrow_tip), QPointF(ph_x, wave.bottom() + 8))
                p.setBrush(QtGui.QBrush(QtGui.QColor(PALETTE["playhead"])))
                p.setPen(QtGui.QPen(halo, 2))
                p.drawPolygon(QtGui.QPolygonF([
                    QPointF(ph_x - 12, arrow_top),
                    QPointF(ph_x + 12, arrow_top),
                    QPointF(ph_x, arrow_tip),
                ]))

        def _hit_cut(self, x, y):
            r = self._wave_rect()
            if not r.contains(QPointF(x, y)):
                return -1
            start = self.view_start
            end = start + self.view_span
            for idx, (s, e) in enumerate(self.cut_ranges):
                if e < start or s > end:
                    continue
                if self._time_to_x(max(s, start)) - 3 <= x <= self._time_to_x(min(e, end)) + 3:
                    return idx
            return -1

        def _hit_cut_edge(self, x, y):
            r = self._wave_rect().adjusted(0, -28, 0, 0)
            if not r.contains(QPointF(x, y)):
                return None
            start = self.view_start
            end = start + self.view_span
            for idx, (s, e) in enumerate(self.cut_ranges):
                if e < start or s > end:
                    continue
                x1 = self._time_to_x(max(s, start))
                x2 = self._time_to_x(min(e, end))
                if abs(x - x1) <= 10:
                    return idx, "start"
                if abs(x - x2) <= 10:
                    return idx, "end"
            return None

        def _hit_cut_bar(self, x, y):
            point = QPointF(x, y)
            start = self.view_start
            end = start + self.view_span
            for idx, (s, e) in enumerate(self.cut_ranges):
                if e < start or s > end:
                    continue
                if self._cut_bar_rect(max(s, start), min(e, end)).contains(point):
                    return idx
            return -1

        def _hit_separator(self, x, y):
            r = self._timeline_rect().adjusted(0, -4, 0, 4)
            if not r.contains(QPointF(x, y)):
                return -1
            start = self.view_start
            end = start + self.view_span
            for idx, value in enumerate(self.separator_points):
                if value < start or value > end:
                    continue
                if abs(self._time_to_x(value) - x) <= 18:
                    return idx
            return -1

        def _hit_marker(self, x, y):
            r = self._timeline_rect().adjusted(0, -4, 0, 4)
            if not r.contains(QPointF(x, y)):
                return None
            start = self.view_start
            end = start + self.view_span
            for name, value in (("in", self.mark_in), ("out", self.mark_out)):
                if value is None or value < start or value > end:
                    continue
                if abs(self._time_to_x(value) - x) <= 18:
                    return name
            return None

        def mousePressEvent(self, event):
            pos = event.position()
            if event.button() == Qt.RightButton:
                idx = self._hit_cut(pos.x(), pos.y())
                if idx >= 0:
                    self.selected_cut = idx
                    self.selected_separator = -1
                    self.cut_selected.emit(idx)
                    self.update()
                return
            if event.button() != Qt.LeftButton:
                return
            cut_edge = self._hit_cut_edge(pos.x(), pos.y())
            if cut_edge is not None:
                self.selected_cut = int(cut_edge[0])
                self.selected_separator = -1
                self.selected_marker = None
                self._dragging = True
                self._drag_kind = "cut_edge"
                self._drag_target = cut_edge
                self._drag_origin = QPointF(pos.x(), pos.y())
                self._start_span = self.view_span
                self.cut_selected.emit(self.selected_cut)
                self.update()
                return
            cut_bar = self._hit_cut_bar(pos.x(), pos.y())
            if cut_bar >= 0:
                self.selected_cut = int(cut_bar)
                self.selected_separator = -1
                self.selected_marker = None
                self._dragging = True
                self._drag_kind = "cut_move"
                self._drag_target = int(cut_bar)
                self._drag_origin = QPointF(pos.x(), pos.y())
                self._drag_playhead_start = self._x_to_time(pos.x())
                self._move_origin = self.cut_ranges[int(cut_bar)]
                self.cut_selected.emit(self.selected_cut)
                self.update()
                return
            marker_name = self._hit_marker(pos.x(), pos.y())
            if marker_name is not None:
                self.selected_marker = marker_name
                self.selected_separator = -1
                self.selected_cut = -1
                self._dragging = True
                self._drag_kind = "marker"
                self._drag_target = marker_name
                self._drag_origin = QPointF(pos.x(), pos.y())
                self._start_span = self.view_span
                self.cut_selected.emit(-1)
                self.separator_selected.emit(-1)
                self.update()
                return
            sep_idx = self._hit_separator(pos.x(), pos.y())
            if sep_idx >= 0:
                self.selected_separator = sep_idx
                self.selected_cut = -1
                self.selected_marker = None
                self._dragging = True
                self._drag_kind = "separator"
                self._drag_target = sep_idx
                self._drag_origin = QPointF(pos.x(), pos.y())
                self._start_span = self.view_span
                self.separator_selected.emit(sep_idx)
                self.update()
                return
            self.selected_marker = None
            self.selected_separator = -1
            self.selected_cut = -1
            self.cut_selected.emit(-1)
            self.separator_selected.emit(-1)
            self._dragging = True
            self._drag_kind = "playhead"
            self._drag_target = None
            self._drag_origin = QPointF(pos.x(), pos.y())
            self._last_drag_pos = QPointF(pos.x(), pos.y())
            self._vertical_zoom_lock = False
            t = self._x_to_time(pos.x())
            self._drag_playhead_start = t
            self._cti_zoom_focus_time = t
            self._cti_zoom_start_y = pos.y()
            self._start_span = self.view_span
            self.set_playhead(t)
            self.seek_requested.emit(t)

        def mouseMoveEvent(self, event):
            if not self._dragging:
                pos = event.position()
                if self._hit_cut_edge(pos.x(), pos.y()) is not None:
                    self.setCursor(Qt.SizeHorCursor)
                elif self._hit_cut_bar(pos.x(), pos.y()) >= 0:
                    self.setCursor(Qt.SizeAllCursor)
                elif self._hit_marker(pos.x(), pos.y()) is not None or self._hit_separator(pos.x(), pos.y()) >= 0:
                    self.setCursor(Qt.SizeAllCursor)
                else:
                    self.unsetCursor()
                return
            pos = event.position()
            t = self._x_to_time(pos.x())
            if self._drag_kind == "marker" and self._drag_target in {"in", "out"}:
                t = self._snap_time(t, exclude_marker=str(self._drag_target))
                if self._drag_target == "in":
                    self.mark_in = t
                else:
                    self.mark_out = t
                self.marker_moved.emit(str(self._drag_target), t)
                self.update()
                return
            if self._drag_kind == "separator" and isinstance(self._drag_target, int):
                idx = int(self._drag_target)
                if 0 <= idx < len(self.separator_points):
                    t = self._snap_time(t, exclude_separator=idx)
                    t = max(1e-6, min(self.duration - 1e-6, t))
                    self.separator_points[idx] = t
                    self.separator_moved.emit(idx, t)
                    self.update()
                return
            if self._drag_kind == "cut_edge" and isinstance(self._drag_target, tuple):
                idx, side = self._drag_target
                idx = int(idx)
                if 0 <= idx < len(self.cut_ranges):
                    s, e = self.cut_ranges[idx]
                    t = self._snap_time(t)
                    if side == "start":
                        s = min(t, e - 0.001)
                    else:
                        e = max(t, s + 0.001)
                    self.cut_ranges[idx] = (s, e)
                    self.selected_cut = idx
                    self.cut_selected.emit(idx)
                    self.update()
                return
            if self._drag_kind == "cut_move" and isinstance(self._drag_target, int):
                idx = int(self._drag_target)
                origin = getattr(self, "_move_origin", None)
                if origin is not None and 0 <= idx < len(self.cut_ranges):
                    s0, e0 = origin
                    width = max(0.001, e0 - s0)
                    delta = t - float(getattr(self, "_drag_playhead_start", t))
                    s = max(0.0, min(self.duration - width, s0 + delta))
                    self.cut_ranges[idx] = (s, s + width)
                    self.selected_cut = idx
                    self.cut_selected.emit(idx)
                    self.update()
                return
            if self._drag_origin is not None:
                last_pos = self._last_drag_pos or self._drag_origin
                inc_dx = pos.x() - last_pos.x()
                inc_dy = pos.y() - last_pos.y()
                total_dy = pos.y() - self._drag_origin.y()
                if (
                    not self._vertical_zoom_lock
                    and abs(total_dy) >= 4
                    and abs(inc_dy) > max(3.0, abs(inc_dx) * 0.85)
                ):
                    self._cti_zoom_focus_time = max(0.0, min(self.duration, t))
                    self._cti_zoom_start_y = pos.y()
                    self._start_span = self.view_span
                    if not self._vertical_zoom_lock:
                        self.set_playhead(self._cti_zoom_focus_time)
                        self.seek_requested.emit(self._cti_zoom_focus_time)
                    self._vertical_zoom_lock = True
                if self._vertical_zoom_lock:
                    focus = max(0.0, min(self.duration, self._cti_zoom_focus_time))
                    dy_zoom = pos.y() - float(getattr(self, "_cti_zoom_start_y", pos.y()))
                    self.view_span = max(0.05, min(self.duration, self._start_span * (2.0 ** (dy_zoom / 150.0))))
                    self.view_start = focus - 0.5 * self.view_span
                    self._clamp_view()
                    self.view_changed.emit()
                    self.set_playhead(focus)
                    self.seek_requested.emit(focus)
                    self.update()
                    self._last_drag_pos = QPointF(pos.x(), pos.y())
                    return
            self.set_playhead(t)
            self.seek_requested.emit(t)
            self._cti_zoom_focus_time = self.playhead
            self._last_drag_pos = QPointF(pos.x(), pos.y())

        def mouseReleaseEvent(self, _event):
            edited = self._drag_kind in {"marker", "separator", "cut_edge", "cut_move"}
            was_playhead = self._drag_kind == "playhead"
            if self._drag_kind == "separator":
                self.separator_points = sorted(set(round(float(value), 6) for value in self.separator_points))
            if self._drag_kind == "cut_edge":
                self.cut_ranges = normalize_ranges(self.cut_ranges, self.duration)
            self._dragging = False
            self._drag_kind = None
            self._drag_target = None
            self._drag_origin = None
            self._last_drag_pos = None
            self._move_origin = None
            self._vertical_zoom_lock = False
            self._cti_zoom_focus_time = self.playhead
            if edited:
                self.edit_finished.emit()
            # PREVIEW: after a playhead drag ends, request one final seek so the
            # preview frame for the landing position is decoded (it was skipped
            # during the drag to keep things responsive).
            if was_playhead:
                self.seek_requested.emit(self.playhead)

        def wheelEvent(self, event):
            delta = event.angleDelta().y()
            if not delta:
                return
            factor = 1 / 1.16 if delta > 0 else 1.16
            self.view_span = max(0.05, min(self.duration, self.view_span * factor))
            focus = max(0.0, min(self.duration, self.playhead))
            self.view_start = focus - 0.5 * self.view_span
            self._clamp_view()
            self.update()
            self.view_changed.emit()

    return UnifiedTimelineWidget
