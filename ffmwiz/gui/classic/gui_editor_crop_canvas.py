"""Crop preview canvas and its frame-extraction worker.

Split out of `gui_editor_crop.py` as a pure code move: neither class read
anything from the builder function's closure beyond Qt symbols, which the
factory below rebinds identically.

Unlike its siblings this module is NOT in `ffmwiz_gui._MODULES`, so the
assembled GUI namespace is never injected into it -- every shared helper it
uses is imported by name below. `from gui_common import *` alone would not
do: it carries the stdlib re-exports but none of the underscore-prefixed
helpers, which only ever resolved through that injection.
"""
from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.gui_common import _gui_log_debug, _import_qt
from ffmwiz.gui.gui_style import PALETTE


def build_crop_canvas_widgets():
    """Define and return the widget class(es); PySide6 is imported here."""
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    Qt = QtCore.Qt
    Signal = QtCore.Signal
    QPointF = QtCore.QPointF
    QRectF = QtCore.QRectF
    QThread = QtCore.QThread

    QColor = QtGui.QColor
    QCursor = QtGui.QCursor
    QFont = QtGui.QFont
    QImage = QtGui.QImage
    QPainter = QtGui.QPainter
    QPen = QtGui.QPen
    QBrush = QtGui.QBrush
    QPixmap = QtGui.QPixmap

    QApplication = QtWidgets.QApplication
    QWidget = QtWidgets.QWidget
    QSizePolicy = QtWidgets.QSizePolicy

    class CropCanvas(QWidget):
        margins_drag_started = Signal()
        margins_drag_finished = Signal()
        margins_changed = Signal()
        zoom_changed = Signal()
        request_toggle_playback = Signal()

        def __init__(self, source_w, source_h):
            super().__init__()
            self.source_w = max(1, int(source_w)); self.source_h = max(1, int(source_h))
            self.image = None; self.zoom = 1.0; self.tool = "hand"
            self._scroll = QPointF(0, 0); self._scroll_origin = QPointF(0, 0)
            self._press_pos = None; self._pan_origin = None; self._drag_handle = None
            self._move_origin = None; self._move_margins = None
            self._zoom_press_y = None; self._zoom_press_pos = None; self._zoom_press_zoom = 1.0; self._zoom_focus = None
            self._zoom_drag_last_pos = None
            self._zoom_drag_started = False; self._last_zoom_drag_update = 0.0
            self._zoom_out_mode = False; self._dragged = False
            self.margins = [0, 0, 0, 0]
            self._open_hand_cursor = self._load_cursor("hand_open.png", Qt.OpenHandCursor)
            self._zoom_in_cursor = self._make_zoom_cursor(False)
            self._zoom_out_cursor = self._make_zoom_cursor(True)
            self.setMouseTracking(True)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setMinimumSize(640, 360)
            self.setStyleSheet(f"background-color: {PALETTE['timeline_bg']};border: 1px solid {PALETTE['border']}; border-radius: 8px;")
            self._refresh_cursor_at_current_pos()

        def set_image(self, image): self.image = image; self.update()
        def set_margins(self, margins):
            self.margins = [int(x) for x in margins]
            self.margins_changed.emit(); self.update()
        def set_tool(self, tool): self.tool = tool; self._refresh_cursor_at_current_pos(); self.update()
        def set_zoom_out_mode(self, enabled, force=False):
            if force or self._zoom_out_mode != bool(enabled):
                self._zoom_out_mode = bool(enabled)
                if self.tool == "zoom": self._refresh_cursor_at_current_pos()
        def pan_by(self, dx, dy): self._scroll = QPointF(self._scroll.x()+dx, self._scroll.y()+dy); self.update()

        def _load_cursor(self, file_name, fallback):
            path = ASSETS_DIR / file_name
            if path.exists():
                pix = QPixmap(str(path))
                if not pix.isNull(): return QCursor(pix, 9, 9)
            return QCursor(fallback)

        def _make_zoom_cursor(self, zoom_out):
            pix = QPixmap(40, 40); pix.fill(Qt.transparent)
            p = QPainter(pix); p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(QPen(QColor(PALETTE["text"]), 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(QBrush(QColor(13, 17, 23, 220)))
            p.drawEllipse(QPointF(16, 16), 10, 10); p.drawLine(QPointF(24, 24), QPointF(34, 34))
            p.setPen(QPen(QColor(PALETTE["warn"] if zoom_out else PALETTE["accent_text"]), 3.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(11, 16), QPointF(21, 16))
            if not zoom_out: p.drawLine(QPointF(16, 11), QPointF(16, 21))
            p.end(); return QCursor(pix, 16, 16)

        def _cursor_for_handle(self, h):
            if h in {"n", "s"}: return QCursor(Qt.SizeVerCursor)
            if h in {"e", "w"}: return QCursor(Qt.SizeHorCursor)
            if h in {"nw", "se"}: return QCursor(Qt.SizeFDiagCursor)
            if h in {"ne", "sw"}: return QCursor(Qt.SizeBDiagCursor)
            if h == "move": return QCursor(Qt.SizeAllCursor)
            return QCursor(Qt.ArrowCursor)
        def _default_tool_cursor(self): return self._zoom_out_cursor if self.tool == "zoom" and self._zoom_out_mode else self._zoom_in_cursor if self.tool == "zoom" else self._open_hand_cursor
        def _refresh_cursor_at_current_pos(self):
            p = self.mapFromGlobal(QCursor.pos())
            self._refresh_cursor_at(QPointF(p.x(), p.y())) if self.rect().contains(p) else self.setCursor(self._default_tool_cursor())
        def _refresh_cursor_at(self, pos):
            h = self._hit_handle(pos.x(), pos.y())
            if h:
                self.setCursor(self._cursor_for_handle(h))
            elif (QApplication.keyboardModifiers() & Qt.ControlModifier) and self._crop_rect_screen().contains(pos):
                self.setCursor(QCursor(Qt.SizeAllCursor))
            else:
                self.setCursor(self._default_tool_cursor())

        def _display_size(self):
            s = self.size(); scale = min(s.width()/self.source_w, s.height()/self.source_h, 1.0) * self.zoom if s.width() > 0 and s.height() > 0 else 1.0
            return max(1, int(self.source_w*scale)), max(1, int(self.source_h*scale))
        def _image_rect(self):
            w,h = self._display_size(); return QRectF(self.width()/2 + self._scroll.x() - w/2, self.height()/2 + self._scroll.y() - h/2, w, h)
        def _zoom_anchor(self, x, y):
            old = self._image_rect()
            p = QPointF(float(x), float(y))
            if old.contains(p):
                rx=max(0,min(1,(p.x()-old.left())/max(1,old.width())))
                ry=max(0,min(1,(p.y()-old.top())/max(1,old.height())))
                return p.x(), p.y(), rx, ry
            return self.width()/2, self.height()/2, 0.5, 0.5
        def _crop_rect_screen(self):
            img = self._image_rect(); w,h = self._display_size(); t,l,r,b = self.margins
            return QRectF(img.left()+l/self.source_w*w, img.top()+t/self.source_h*h, max(1, img.right()-r/self.source_w*w-(img.left()+l/self.source_w*w)), max(1, img.bottom()-b/self.source_h*h-(img.top()+t/self.source_h*h)))

        def paintEvent(self, _event):
            p = QPainter(self); p.setRenderHint(QPainter.Antialiasing, True); p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.fillRect(self.rect(), QColor(PALETTE["timeline_bg"])); img = self._image_rect()
            if self.image is not None and not self.image.isNull(): p.drawImage(img, self.image)
            else:
                p.setPen(QPen(QColor(PALETTE["text_mute"]))); p.setFont(QFont("Segoe UI", 12)); p.drawText(self.rect(), Qt.AlignCenter, "Loading preview frame...")
            crop = self._crop_rect_screen(); dim = QColor(0,0,0,165)
            p.fillRect(QRectF(img.left(), img.top(), img.width(), crop.top()-img.top()), dim); p.fillRect(QRectF(img.left(), crop.bottom(), img.width(), img.bottom()-crop.bottom()), dim)
            p.fillRect(QRectF(img.left(), crop.top(), crop.left()-img.left(), crop.height()), dim); p.fillRect(QRectF(crop.right(), crop.top(), img.right()-crop.right(), crop.height()), dim)
            p.setPen(QPen(QColor(PALETTE["warn"]), 2)); p.setBrush(Qt.NoBrush); p.drawRect(crop)
            p.setPen(QPen(QColor(PALETTE["warn"]), 1, Qt.DashLine))
            for i in (1,2):
                xp=crop.left()+crop.width()*i/3; yp=crop.top()+crop.height()*i/3; p.drawLine(QPointF(xp,crop.top()), QPointF(xp,crop.bottom())); p.drawLine(QPointF(crop.left(),yp), QPointF(crop.right(),yp))
            p.setBrush(QBrush(QColor(PALETTE["warn"]))); p.setPen(QPen(QColor(PALETTE["bg"]),1)); hs=8
            for cx,cy in self._handle_centers().values(): p.drawRect(QRectF(cx-hs, cy-hs, hs*2, hs*2))

        def _handle_centers(self):
            r=self._crop_rect_screen(); mx=(r.left()+r.right())/2; my=(r.top()+r.bottom())/2
            return {"nw":(r.left(),r.top()),"n":(mx,r.top()),"ne":(r.right(),r.top()),"e":(r.right(),my),"se":(r.right(),r.bottom()),"s":(mx,r.bottom()),"sw":(r.left(),r.bottom()),"w":(r.left(),my)}
        def _hit_handle(self, x, y):
            crop=self._crop_rect_screen(); hit=24; edge=14
            for name,(cx,cy) in self._handle_centers().items():
                if abs(x-cx)<=hit and abs(y-cy)<=hit: return name
            if crop.adjusted(-edge,-edge,edge,edge).contains(QPointF(x,y)):
                nl,nr,nt,nb = abs(x-crop.left())<=edge, abs(x-crop.right())<=edge, abs(y-crop.top())<=edge, abs(y-crop.bottom())<=edge
                if nl and nt: return "nw"
                if nr and nt: return "ne"
                if nr and nb: return "se"
                if nl and nb: return "sw"
                if nt: return "n"
                if nr: return "e"
                if nb: return "s"
                if nl: return "w"
            return None

        def mousePressEvent(self, event):
            self.setFocus(Qt.MouseFocusReason)
            if event.button() == Qt.RightButton:
                if self._image_rect().contains(event.position()): self.request_toggle_playback.emit()
                return
            if event.button() != Qt.LeftButton: return
            pos=event.position(); self._press_pos=pos; self._dragged=False; self._drag_handle=self._hit_handle(pos.x(), pos.y())
            if self._drag_handle: self.margins_drag_started.emit(); return
            if (event.modifiers() & Qt.ControlModifier) and self._crop_rect_screen().contains(pos):
                self._drag_handle = "move"
                self._move_origin = pos
                self._move_margins = list(self.margins)
                self.margins_drag_started.emit()
                self.setCursor(Qt.SizeAllCursor)
                return
            if self.tool == "zoom":
                self.set_zoom_out_mode(bool(event.modifiers() & Qt.AltModifier), force=True)
                self._zoom_press_y=pos.y(); self._zoom_press_pos=QPointF(pos.x(), pos.y())
                self._zoom_press_zoom=self.zoom; self._zoom_focus=QPointF(pos.x(), pos.y())
                self._zoom_drag_started=False; self._last_zoom_drag_update=0.0; self._zoom_drag_last_pos=QPointF(pos.x(), pos.y())
            elif self._image_rect().contains(pos):
                self._pan_origin=pos; self._scroll_origin=QPointF(self._scroll); self.setCursor(Qt.ClosedHandCursor)

        def mouseMoveEvent(self, event):
            pos=event.position()
            if self._press_pos is not None:
                d=pos-self._press_pos; self._dragged = self._dragged or abs(d.x())+abs(d.y())>6
            if self._drag_handle:
                if self._drag_handle == "move": self._move_crop(pos)
                else: self._resize_crop(self._drag_handle, pos.x(), pos.y())
                return
            if self.tool == "zoom" and self._zoom_press_y is not None and self._zoom_focus is not None:
                if event.modifiers() & Qt.AltModifier:
                    self.set_zoom_out_mode(True, force=True)
                self._apply_zoom_drag(pos)
                return
            if self._pan_origin is not None: self._scroll=self._scroll_origin+(pos-self._pan_origin); self.update(); return
            self._refresh_cursor_at(pos)

        def mouseReleaseEvent(self, event):
            zp=self._zoom_focus; zd=self._zoom_drag_started
            if self._drag_handle is not None: self.margins_drag_finished.emit()
            self._drag_handle=None; self._move_origin=None; self._move_margins=None; self._zoom_press_y=None; self._zoom_press_pos=None; self._zoom_focus=None; self._zoom_drag_last_pos=None; self._zoom_drag_started=False; self._pan_origin=None
            if self.tool == "zoom" and zp is not None and not zd and not self._dragged: self._zoom_centered(zp.x(), zp.y(), 1.0/1.25 if event.modifiers() & Qt.AltModifier else 1.25)
            elif self.tool == "zoom" and zp is not None and zd:
                self.zoom_changed.emit()
            self._press_pos=None; self._refresh_cursor_at(event.position())

        def wheelEvent(self, event):
            d=event.angleDelta().y()
            if d: self._zoom_centered(event.position().x(), event.position().y(), (1.15 if event.modifiers() & Qt.ControlModifier else 1.25) if d>0 else 1.0/(1.15 if event.modifiers() & Qt.ControlModifier else 1.25))
        def _zoom_centered(self, x, y, factor): self._set_zoom_centered(x, y, self.zoom*factor)
        def _apply_zoom_drag(self, pos):
            if self._zoom_press_pos is None or self._zoom_focus is None:
                return False
            if self._zoom_drag_last_pos is None:
                self._zoom_drag_last_pos = QPointF(pos.x(), pos.y())
                return False
            total_dy = pos.y() - self._zoom_press_pos.y()
            total_dx = pos.x() - self._zoom_press_pos.x()
            dy = pos.y() - self._zoom_drag_last_pos.y()
            dx = pos.x() - self._zoom_drag_last_pos.x()
            if abs(total_dy) < 2.0 and abs(total_dx) < 2.0:
                return False
            self._zoom_drag_last_pos = QPointF(pos.x(), pos.y())
            self._zoom_drag_started = True
            effective_dy = -dy if self._zoom_out_mode else dy
            target_zoom = self.zoom * (2.0 ** (-effective_dy / 75.0))
            if abs(target_zoom - self.zoom) < 0.0005:
                return True
            self._set_zoom_centered(self._zoom_focus.x(), self._zoom_focus.y(), target_zoom)
            if os.environ.get("FFMWIZ_DEBUG_COORDS"):
                _gui_log_debug(
                    f"Crop zoom drag dy={dy:.1f} dx={dx:.1f} total_dy={total_dy:.1f} target_zoom={target_zoom:.3f}",
                    force=True,
                )
            return True
        def _set_zoom_centered(self, x, y, target_zoom):
            x,y,rx,ry=self._zoom_anchor(x,y); oldz=self.zoom; self.zoom=max(0.10,min(32.0,target_zoom))
            if abs(self.zoom-oldz)<1e-6: return
            nw,nh=self._display_size(); self._scroll=QPointF(x-rx*nw+nw/2-self.width()/2, y-ry*nh+nh/2-self.height()/2)
            if os.environ.get("FFMWIZ_DEBUG_COORDS"):
                _gui_log_debug(
                    f"Crop zoom transform focus=({x:.1f},{y:.1f}) rel=({rx:.4f},{ry:.4f}) "
                    f"zoom={oldz:.3f}->{self.zoom:.3f} scroll=({self._scroll.x()},{self._scroll.y()})",
                    force=True,
                )
            self.update(); self.zoom_changed.emit()
        def _resize_crop(self, handle, x, y):
            img=self._image_rect(); w,h=self._display_size()
            if w<=0 or h<=0: return
            sx=max(0,min(1,(x-img.left())/w))*self.source_w; sy=max(0,min(1,(y-img.top())/h))*self.source_h; t,l,r,b=self.margins; ms=16
            if "w" in handle: l=max(0,min(self.source_w-r-ms,int(round(sx))))
            if "e" in handle: r=max(0,min(self.source_w-l-ms,int(round(self.source_w-sx))))
            if "n" in handle: t=max(0,min(self.source_h-b-ms,int(round(sy))))
            if "s" in handle: b=max(0,min(self.source_h-t-ms,int(round(self.source_h-sy))))
            self.margins=[t,l,r,b]; self.margins_changed.emit(); self.update()
        def _move_crop(self, pos):
            if self._move_origin is None or self._move_margins is None:
                return
            img=self._image_rect(); w,h=self._display_size()
            if w<=0 or h<=0:
                return
            dx=int(round((pos.x()-self._move_origin.x())/w*self.source_w))
            dy=int(round((pos.y()-self._move_origin.y())/h*self.source_h))
            t,l,r,b=[int(v) for v in self._move_margins]
            crop_w=max(1,self.source_w-l-r); crop_h=max(1,self.source_h-t-b)
            new_l=max(0,min(self.source_w-crop_w,l+dx)); new_t=max(0,min(self.source_h-crop_h,t+dy))
            new_r=self.source_w-crop_w-new_l; new_b=self.source_h-crop_h-new_t
            self.margins=[new_t,new_l,new_r,new_b]; self.margins_changed.emit(); self.update()
    class FrameExtractWorker(QThread):
        finished_with_image = Signal(QImage)

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
            tmp_dir = Path(tempfile.gettempdir()) / "ffmwiz_crop_qt"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            out_path = tmp_dir / f"frame_{int(self.timestamp * 1000)}_{self.width}x{self.height}.png"
            args = [
                self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{max(0.0, self.timestamp):.3f}",
                "-i", str(self.source),
                "-frames:v", "1",
                "-vf", f"scale={self.width}:{self.height}:flags=fast_bilinear",
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
                image = QImage(str(out_path))
                self.finished_with_image.emit(image)
            except Exception:
                if not self.isInterruptionRequested():
                    self.finished_with_image.emit(QImage())

    return CropCanvas, FrameExtractWorker
