"""Preview canvas and frame-extraction worker for the unified video editor.

Split out of `gui_editor_unified.py` as a pure code move; neither class
captured state from the builder function, only Qt symbols.

Both are defined inside a factory so PySide6 is imported on demand -- see
`gui_editor_unified_timeline.py` for the same reasoning.
"""
from __future__ import annotations
import gui_common  # noqa: F401
from gui_common import *  # noqa: F401,F403


def build_unified_preview_widgets():
    """Define and return the widget class(es); PySide6 is imported here."""
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    Qt = QtCore.Qt
    Signal = QtCore.Signal
    QPointF = QtCore.QPointF
    QRectF = QtCore.QRectF
    QWidget = QtWidgets.QWidget
    QSizePolicy = QtWidgets.QSizePolicy

    class UnifiedPreviewCanvas(QWidget):
        margins_changed = Signal()
        edit_finished = Signal()
        seek_requested = Signal(float)
        toggle_playback_requested = Signal()
        zoom_changed = Signal(float)

        def __init__(self, source_w: int, source_h: int, duration: float):
            super().__init__()
            self.source_w = max(1, int(source_w or 1920))
            self.source_h = max(1, int(source_h or 1080))
            self.duration = max(0.0, float(duration or 0.0))
            self.image = None
            self.margins = [0, 0, 0, 0]
            self.zoom = 1.0
            self._scroll = QPointF(0, 0)
            self._press_pos = None
            self._pan_origin = None
            self._scroll_origin = QPointF(0, 0)
            self._drag_handle = None
            self._move_origin = None
            self._move_margins = None
            self._tool = "hand"
            self._zoom_origin = None
            self._zoom_start = 1.0
            self._zoom_focus_source = (self.source_w / 2.0, self.source_h / 2.0)
            self._zoom_focus_screen = None
            self._toggle_click_candidate = False
            self._zoom_out_mode = False
            self._show_crop_overlay = True
            self._zoom_in_cursor = self._make_zoom_cursor(False)
            self._zoom_out_cursor = self._make_zoom_cursor(True)
            self.setMouseTracking(True)
            self.setMinimumSize(520, 260)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setStyleSheet(
                f"background-color: {PALETTE['timeline_bg']};"
                f"border: 1px solid {PALETTE['border_strong']}; border-radius: 8px;"
            )

        def set_image(self, image) -> None:
            self.image = image
            self.update()

        def set_margins(self, margins) -> None:
            self.margins = self._clamped_margins(margins)
            self.margins_changed.emit()
            self.update()

        def _clamped_margins(self, margins) -> list[int]:
            values = [int(round(float(v or 0))) for v in list(margins)[:4]]
            while len(values) < 4:
                values.append(0)
            top, left, right, bottom = [max(0, value) for value in values]
            min_size = 16
            top = min(top, max(0, self.source_h - min_size))
            bottom = min(bottom, max(0, self.source_h - top - min_size))
            left = min(left, max(0, self.source_w - min_size))
            right = min(right, max(0, self.source_w - left - min_size))
            return [top, left, right, bottom]

        def reset_crop(self) -> None:
            self.set_margins([0, 0, 0, 0])

        def reset_view(self) -> None:
            self.zoom = 1.0
            self._scroll = QPointF(0, 0)
            self._constrain_scroll()
            self._refresh_cursor_at_current_pos()
            self.zoom_changed.emit(self.zoom)
            self.update()

        def set_tool(self, tool: str) -> None:
            self._tool = "zoom" if tool == "zoom" else "hand"
            self._refresh_cursor_at_current_pos()

        def set_zoom_out_mode(self, enabled: bool) -> None:
            if self._zoom_out_mode == bool(enabled):
                return
            self._zoom_out_mode = bool(enabled)
            self._refresh_cursor_at_current_pos()

        def set_crop_overlay_visible(self, visible: bool) -> None:
            self._show_crop_overlay = bool(visible)
            self._refresh_cursor_at_current_pos()
            self.update()

        def _display_size(self):
            available_w = max(80, self.width() - 32)
            available_h = max(80, self.height() - 32)
            scale = min(
                available_w / self.source_w,
                available_h / self.source_h,
            ) * self.zoom if self.width() > 0 and self.height() > 0 else 1.0
            return max(1, int(self.source_w * scale)), max(1, int(self.source_h * scale))

        def _image_rect(self):
            w, h = self._display_size()
            return QRectF(
                self.width() / 2 + self._scroll.x() - w / 2,
                self.height() / 2 + self._scroll.y() - h / 2,
                w,
                h,
            )

        def _constrain_scroll(self) -> None:
            w, h = self._display_size()
            if w <= self.width():
                max_x = max(0.0, (self.width() - w) * 0.05)
            else:
                max_x = max(0.0, (w - self.width()) / 2 + self.width() * 0.5)
            if h <= self.height():
                max_y = max(0.0, (self.height() - h) * 0.05)
            else:
                max_y = max(0.0, (h - self.height()) / 2 + self.height() * 0.5)
            self._scroll = QPointF(
                max(-max_x, min(max_x, self._scroll.x())),
                max(-max_y, min(max_y, self._scroll.y())),
            )

        def _crop_rect_screen(self):
            img = self._image_rect()
            w, h = self._display_size()
            top, left, right, bottom = self.margins
            x1 = img.left() + left / self.source_w * w
            y1 = img.top() + top / self.source_h * h
            x2 = img.right() - right / self.source_w * w
            y2 = img.bottom() - bottom / self.source_h * h
            return QRectF(x1, y1, max(1, x2 - x1), max(1, y2 - y1))

        def _handle_centers(self):
            r = self._crop_rect_screen()
            mx = (r.left() + r.right()) / 2
            my = (r.top() + r.bottom()) / 2
            return {
                "nw": (r.left(), r.top()), "n": (mx, r.top()), "ne": (r.right(), r.top()),
                "e": (r.right(), my), "se": (r.right(), r.bottom()), "s": (mx, r.bottom()),
                "sw": (r.left(), r.bottom()), "w": (r.left(), my),
            }

        def _hit_handle(self, x, y):
            if not self._show_crop_overlay:
                return None
            hit = 18
            for name, (cx, cy) in self._handle_centers().items():
                if abs(x - cx) <= hit and abs(y - cy) <= hit:
                    return name
            crop = self._crop_rect_screen()
            edge_hit = 9
            point = QPointF(x, y)
            if crop.adjusted(-edge_hit, -edge_hit, edge_hit, edge_hit).contains(point):
                near_left = abs(x - crop.left()) <= edge_hit
                near_right = abs(x - crop.right()) <= edge_hit
                near_top = abs(y - crop.top()) <= edge_hit
                near_bottom = abs(y - crop.bottom()) <= edge_hit
                inside_x = crop.left() - edge_hit <= x <= crop.right() + edge_hit
                inside_y = crop.top() - edge_hit <= y <= crop.bottom() + edge_hit
                if near_left and inside_y:
                    return "w"
                if near_right and inside_y:
                    return "e"
                if near_top and inside_x:
                    return "n"
                if near_bottom and inside_x:
                    return "s"
            return None

        def _cursor_for_handle(self, handle):
            if handle in {"n", "s"}:
                return Qt.SizeVerCursor
            if handle in {"e", "w"}:
                return Qt.SizeHorCursor
            if handle in {"nw", "se"}:
                return Qt.SizeFDiagCursor
            if handle in {"ne", "sw"}:
                return Qt.SizeBDiagCursor
            return None

        def _refresh_cursor_at(self, pos) -> None:
            if self._drag_handle:
                return
            handle = self._hit_handle(pos.x(), pos.y())
            cursor = self._cursor_for_handle(handle)
            if cursor is not None:
                self.setCursor(cursor)
                return
            if self._tool == "zoom":
                self.setCursor(self._zoom_out_cursor if self._zoom_out_mode else self._zoom_in_cursor)
            else:
                self.setCursor(Qt.OpenHandCursor)

        def _make_zoom_cursor(self, zoom_out: bool):
            pix = QtGui.QPixmap(34, 34)
            pix.fill(Qt.transparent)
            p = QtGui.QPainter(pix)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.setPen(QtGui.QPen(QtGui.QColor("#050505"), 5.0, Qt.SolidLine, Qt.RoundCap))
            p.drawEllipse(QPointF(14, 14), 9, 9)
            p.drawLine(QPointF(21, 21), QPointF(30, 30))
            p.setPen(QtGui.QPen(QtGui.QColor("#ff9f1a"), 3.0, Qt.SolidLine, Qt.RoundCap))
            p.drawEllipse(QPointF(14, 14), 9, 9)
            p.drawLine(QPointF(21, 21), QPointF(30, 30))
            p.setPen(QtGui.QPen(QtGui.QColor("#050505"), 5.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(9, 14), QPointF(19, 14))
            if not zoom_out:
                p.drawLine(QPointF(14, 9), QPointF(14, 19))
            p.setPen(QtGui.QPen(QtGui.QColor("#2ea8ff"), 3.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(9, 14), QPointF(19, 14))
            if not zoom_out:
                p.drawLine(QPointF(14, 9), QPointF(14, 19))
            p.end()
            return QtGui.QCursor(pix, 14, 14)

        def _refresh_cursor_at_current_pos(self) -> None:
            self._refresh_cursor_at(self.mapFromGlobal(QtGui.QCursor.pos()))

        def _source_point_for_pos(self, pos):
            img = self._image_rect()
            w, h = self._display_size()
            sx = max(0.0, min(1.0, (pos.x() - img.left()) / max(1, w))) * self.source_w
            sy = max(0.0, min(1.0, (pos.y() - img.top()) / max(1, h))) * self.source_h
            return sx, sy

        def _zoom_centered(self, new_zoom: float, focus_source=None, focus_screen=None) -> None:
            focus_source = focus_source or self._zoom_focus_source
            old_rect = self._image_rect()
            focus_x, focus_y = focus_source
            if focus_screen is None:
                focus_screen = QPointF(
                    old_rect.left() + focus_x / self.source_w * old_rect.width(),
                    old_rect.top() + focus_y / self.source_h * old_rect.height(),
                )
            else:
                focus_screen = QPointF(float(focus_screen.x()), float(focus_screen.y()))
            self.zoom = max(0.08, min(64.0, float(new_zoom)))
            new_w, new_h = self._display_size()
            self._scroll = QPointF(
                focus_screen.x() - self.width() / 2 + new_w / 2 - focus_x / self.source_w * new_w,
                focus_screen.y() - self.height() / 2 + new_h / 2 - focus_y / self.source_h * new_h,
            )
            self._constrain_scroll()
            self.zoom_changed.emit(self.zoom)
            self.update()

        def paintEvent(self, _event):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            p.fillRect(self.rect(), QtGui.QColor(PALETTE["timeline_bg"]))
            img = self._image_rect()
            if self.image is not None and not self.image.isNull():
                # Preserve the frame's own aspect ratio inside the canvas rect.
                # Joined clips can have different aspect ratios (e.g. a vertical
                # clip after a horizontal one); fit-with-black instead of
                # stretching, matching the encode's scale+pad output.
                iw = self.image.width()
                ih = self.image.height()
                if iw > 0 and ih > 0:
                    fit = min(img.width() / iw, img.height() / ih)
                    dw = iw * fit
                    dh = ih * fit
                    dst = QRectF(
                        img.center().x() - dw / 2.0,
                        img.center().y() - dh / 2.0,
                        dw,
                        dh,
                    )
                    p.drawImage(dst, self.image)
                else:
                    p.drawImage(img, self.image)
            else:
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["text_mute"])))
                p.setFont(QtGui.QFont("Segoe UI", 12))
                p.drawText(self.rect(), Qt.AlignCenter, "Loading video preview...")
            center_line = QtGui.QColor("#b9c7d8")
            center_line.setAlpha(82)
            p.setPen(QtGui.QPen(center_line, 1))
            center_x = img.left() + img.width() * 0.5
            p.drawLine(QPointF(center_x, img.top()), QPointF(center_x, img.bottom()))
            if not self._show_crop_overlay:
                return
            crop = self._crop_rect_screen()
            dim = QtGui.QColor(0, 0, 0, 150)
            p.fillRect(QRectF(img.left(), img.top(), img.width(), max(0, crop.top() - img.top())), dim)
            p.fillRect(QRectF(img.left(), crop.bottom(), img.width(), max(0, img.bottom() - crop.bottom())), dim)
            p.fillRect(QRectF(img.left(), crop.top(), max(0, crop.left() - img.left()), crop.height()), dim)
            p.fillRect(QRectF(crop.right(), crop.top(), max(0, img.right() - crop.right()), crop.height()), dim)
            p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["warn"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(crop)
            p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["warn"]), 1, Qt.DashLine))
            for i in (1, 2):
                x = crop.left() + crop.width() * i / 3
                y = crop.top() + crop.height() * i / 3
                p.drawLine(QPointF(x, crop.top()), QPointF(x, crop.bottom()))
                p.drawLine(QPointF(crop.left(), y), QPointF(crop.right(), y))
            p.setBrush(QtGui.QBrush(QtGui.QColor(PALETTE["warn"])))
            p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["bg"]), 1))
            for cx, cy in self._handle_centers().values():
                p.drawRoundedRect(QRectF(cx - 7, cy - 7, 14, 14), 3, 3)

        def mousePressEvent(self, event):
            if event.button() == Qt.RightButton:
                self.toggle_playback_requested.emit()
                return
            if event.button() != Qt.LeftButton:
                return
            pos = event.position()
            self._press_pos = pos
            self._drag_handle = self._hit_handle(pos.x(), pos.y())
            if self._drag_handle:
                self.setCursor(self._cursor_for_handle(self._drag_handle) or Qt.ClosedHandCursor)
                return
            if event.modifiers() & Qt.ControlModifier:
                self._drag_handle = "move"
                self._move_origin = pos
                self._move_margins = list(self.margins)
                self.setCursor(Qt.SizeAllCursor)
                return
            if self._tool == "zoom":
                self._zoom_origin = pos
                self._zoom_start = self.zoom
                self._zoom_focus_source = self._source_point_for_pos(pos)
                self._zoom_focus_screen = QPointF(pos)
                self.set_zoom_out_mode(bool(event.modifiers() & Qt.AltModifier))
                return
            self._pan_origin = pos
            self._scroll_origin = QPointF(self._scroll)
            self._toggle_click_candidate = True
            self.setCursor(Qt.ClosedHandCursor)

        def mouseMoveEvent(self, event):
            pos = event.position()
            if self._drag_handle:
                if self._drag_handle == "move":
                    self._move_crop(pos)
                else:
                    self._resize_crop(self._drag_handle, pos.x(), pos.y())
                return
            if self._zoom_origin is not None:
                dy = self._zoom_origin.y() - pos.y()
                self._zoom_centered(
                    self._zoom_start * (2.0 ** (dy / 110.0)),
                    self._zoom_focus_source,
                    self._zoom_focus_screen,
                )
                return
            if self._pan_origin is not None:
                if abs(pos.x() - self._pan_origin.x()) + abs(pos.y() - self._pan_origin.y()) > 6:
                    self._toggle_click_candidate = False
                self._scroll = self._scroll_origin + (pos - self._pan_origin)
                self._constrain_scroll()
                self.update()
                return
            self._refresh_cursor_at(pos)

        def mouseReleaseEvent(self, _event):
            crop_edit = bool(self._drag_handle)
            if self._zoom_origin is not None and self._press_pos is not None:
                pos = _event.position()
                if abs(pos.x() - self._press_pos.x()) + abs(pos.y() - self._press_pos.y()) < 4:
                    factor = 1 / 1.35 if self._zoom_out_mode else 1.35
                    self._zoom_centered(self.zoom * factor, self._source_point_for_pos(pos), pos)
            self._drag_handle = None
            self._move_origin = None
            self._move_margins = None
            self._pan_origin = None
            self._zoom_origin = None
            self._zoom_focus_screen = None
            self._toggle_click_candidate = False
            self._refresh_cursor_at_current_pos()
            if crop_edit:
                self.edit_finished.emit()

        def wheelEvent(self, event):
            delta = event.angleDelta().y()
            if not delta:
                return
            factor = 1.18 if delta > 0 else 1 / 1.18
            self._zoom_centered(self.zoom * factor, self._source_point_for_pos(event.position()), event.position())

        def _resize_crop(self, handle, x, y):
            img = self._image_rect()
            w, h = self._display_size()
            sx = max(0, min(1, (x - img.left()) / max(1, w))) * self.source_w
            sy = max(0, min(1, (y - img.top()) / max(1, h))) * self.source_h
            top, left, right, bottom = self.margins
            min_size = 16
            if "w" in handle:
                left = max(0, min(self.source_w - right - min_size, int(round(sx))))
            if "e" in handle:
                right = max(0, min(self.source_w - left - min_size, int(round(self.source_w - sx))))
            if "n" in handle:
                top = max(0, min(self.source_h - bottom - min_size, int(round(sy))))
            if "s" in handle:
                bottom = max(0, min(self.source_h - top - min_size, int(round(self.source_h - sy))))
            self.margins = [top, left, right, bottom]
            self.margins_changed.emit()
            self.update()

        def _move_crop(self, pos):
            if self._move_origin is None or self._move_margins is None:
                return
            img = self._image_rect()
            w, h = self._display_size()
            dx = int(round((pos.x() - self._move_origin.x()) / max(1, w) * self.source_w))
            dy = int(round((pos.y() - self._move_origin.y()) / max(1, h) * self.source_h))
            top, left, right, bottom = [int(v) for v in self._move_margins]
            crop_w = max(1, self.source_w - left - right)
            crop_h = max(1, self.source_h - top - bottom)
            new_left = max(0, min(self.source_w - crop_w, left + dx))
            new_top = max(0, min(self.source_h - crop_h, top + dy))
            self.margins = [new_top, new_left, self.source_w - crop_w - new_left, self.source_h - crop_h - new_top]
            self.margins_changed.emit()
            self.update()

    class FrameExtractWorker(QtCore.QThread):
        # PREVIEW FIX: the original referenced FrameExtractWorker here but the class
        # is only defined inside build_crop_editor's scope, so paused-seek frame
        # extraction raised NameError in the unified editor. Give it its own copy.
        finished_with_image = Signal(QtGui.QImage)

        def __init__(self, ffmpeg, source, timestamp, width, height, parent=None):
            super().__init__(parent)
            self.ffmpeg = ffmpeg
            self.source = source
            self.timestamp = timestamp
            self.width = width
            self.height = height
            self._process = None

        def stop(self):
            self.requestInterruption()
            proc = self._process
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass

        def run(self):
            tmp_dir = Path(tempfile.gettempdir()) / "ffmwiz_unified_qt"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            out_path = tmp_dir / f"frame_{int(self.timestamp * 1000)}_{self.width}x{self.height}.png"
            # PREVIEW FIX (paused-frame stretch): the requested width/height come
            # from the preview WIDGET size, whose aspect ratio rarely matches the
            # video. A plain "scale=W:H" forces the frame into the widget aspect,
            # so the paused frame looked stretched while live playback (which feeds
            # native-resolution frames) looked correct. force_original_aspect_ratio
            # =decrease fits the frame INSIDE W:H while preserving the source aspect
            # ratio, so the preview's own letterbox logic renders it undistorted.
            args = [
                self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{max(0.0, self.timestamp):.3f}",
                "-i", str(self.source),
                "-frames:v", "1",
                "-vf", f"scale={self.width}:{self.height}:flags=fast_bilinear:force_original_aspect_ratio=decrease",
                str(out_path),
            ]
            try:
                self._process = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                _stdout, stderr = self._process.communicate()
                return_code = self._process.returncode
                self._process = None
                if self.isInterruptionRequested():
                    return
                if return_code != 0:
                    message = (stderr or b"").decode("utf-8", "replace").strip()
                    raise RuntimeError(message or f"ffmpeg exited with code {return_code}")
                image = QtGui.QImage(str(out_path))
                self.finished_with_image.emit(image)
            except Exception:
                if not self.isInterruptionRequested():
                    self.finished_with_image.emit(QtGui.QImage())

    return UnifiedPreviewCanvas, FrameExtractWorker
