from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403


def build_crop_editor(request: dict[str, Any]):
    QtCore, QtGui, QtWidgets, QtMultimedia = _import_qt()
    Qt = QtCore.Qt
    Signal = QtCore.Signal
    QPoint = QtCore.QPoint
    QPointF = QtCore.QPointF
    QRectF = QtCore.QRectF
    QSize = QtCore.QSize
    QTimer = QtCore.QTimer
    QThread = QtCore.QThread
    QUrl = QtCore.QUrl

    QColor = QtGui.QColor
    QFont = QtGui.QFont
    QImage = QtGui.QImage
    QPainter = QtGui.QPainter
    QPen = QtGui.QPen
    QBrush = QtGui.QBrush
    QPixmap = QtGui.QPixmap
    QCursor = QtGui.QCursor
    QShortcut = QtGui.QShortcut
    QKeySequence = QtGui.QKeySequence

    QApplication = QtWidgets.QApplication
    QStyle = QtWidgets.QStyle
    QMainWindow = QtWidgets.QMainWindow
    QWidget = QtWidgets.QWidget
    QFrame = QtWidgets.QFrame
    QLabel = QtWidgets.QLabel
    QPushButton = QtWidgets.QPushButton
    QSlider = QtWidgets.QSlider
    QComboBox = QtWidgets.QComboBox
    QHBoxLayout = QtWidgets.QHBoxLayout
    QVBoxLayout = QtWidgets.QVBoxLayout
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

    class CropEditorWindow(QMainWindow):
        def __init__(self, request):
            init_start = time.perf_counter()
            super().__init__()
            self.request = request
            self.input_path = Path(request["input_path"])
            requested_fps = request.get("fps")
            self.fps = float(requested_fps or 25.0)
            if not requested_fps:
                print("Crop Editor FPS fallback: using 25.000 fps.", file=sys.stderr)
            self.duration = float(request.get("duration") or 0.0)
            self.source_w = int(request.get("source_w") or 1920)
            self.source_h = int(request.get("source_h") or 1080)
            self.ffmpeg = request.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg"
            self.result = {"status": "canceled", "margins": [0, 0, 0, 0]}
            self._worker = None
            self._pending_request = None
            self._frame_request_started = 0.0
            self._syncing_zoom_control = False
            self._zoom_editor_active = False
            self._zoom_select_all_pending = False
            self._zoom_presets = (10, 15, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 800, 1200, 1600, 3200)

            # CRITICAL: state initialized BEFORE _build_ui() so it can read
            # self._timestamp during widget construction (previous _timestamp
            # AttributeError fix).
            self._timestamp = min(30.0, max(0.0, self.duration * 0.25))
            self._drag_snapshot = None
            self._history = HistoryStack(CropSnapshot(margins=(0, 0, 0, 0)), max_size=100)

            self.setWindowTitle("FFmWiz Crop Editor")
            self._icon = _icon_loader(self, self.style())
            _apply_window_icon(self, self._icon)
            self.setMinimumSize(1080, 720)
            self.resize(1280, 820)

            self._build_ui()
            self._install_shortcuts()
            _gui_log_debug(
                f"Crop GUI widgets initialized in {time.perf_counter() - init_start:.3f}s",
                force=True,
            )
            QtCore.QTimer.singleShot(150, self._deferred_media_startup)

        def _deferred_media_startup(self):
            start = time.perf_counter()
            self._setup_player()
            self._seek(self._timestamp)
            _gui_log_debug(
                f"Crop GUI media startup completed in {time.perf_counter() - start:.3f}s",
                force=True,
            )

        def _take_snapshot(self):
            t, l, r, b = self.canvas.margins
            return CropSnapshot(margins=(int(t), int(l), int(r), int(b)))

        def _commit_history(self):
            self._history.push(self._take_snapshot())
            self._update_undo_redo_state()

        def _restore_snapshot(self, snap):
            self.canvas.set_margins(list(snap.margins))
            self._refresh_info()

        def _undo(self):
            snap = self._history.undo()
            if snap is not None:
                self._restore_snapshot(snap)
                self._update_undo_redo_state()

        def _redo(self):
            snap = self._history.redo()
            if snap is not None:
                self._restore_snapshot(snap)
                self._update_undo_redo_state()

        def _update_undo_redo_state(self):
            self.btn_undo.setEnabled(self._history.can_undo())
            self.btn_redo.setEnabled(self._history.can_redo())

        def _build_ui(self):
            layout_start = time.perf_counter()
            central = QWidget()
            central.setObjectName("central")
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(14, 10, 14, 10)
            root.setSpacing(8)

            self.btn_undo = QPushButton(self._icon("undo", QStyle.SP_ArrowBack), "Undo (Ctrl+Z)")
            self.btn_undo.setToolTip("Undo last crop edit")
            self.btn_undo.clicked.connect(self._undo)
            self.btn_redo = QPushButton(self._icon("redo", QStyle.SP_ArrowForward), "Redo (Ctrl+Y)")
            self.btn_redo.setToolTip("Redo last undone crop edit")
            self.btn_redo.clicked.connect(self._redo)
            self.btn_undo.setEnabled(False)
            self.btn_redo.setEnabled(False)

            header = QFrame()
            header.setObjectName("header")
            hlay = QHBoxLayout(header)
            hlay.setContentsMargins(14, 10, 14, 10)
            hlay.setSpacing(10)
            title = QLabel("FFmWiz Crop Editor")
            title.setObjectName("title")
            hlay.addWidget(title)
            hlay.addSpacing(12)
            hlay.addWidget(self.btn_undo)
            hlay.addWidget(self.btn_redo)
            hlay.addStretch(1)
            info = QLabel(f"FPS  {self.fps:.3f}      •      Duration  "
                          f"{seconds_to_hmsf(self.duration, self.fps)}      •      Source  "
                          f"{self.input_path.name}")
            info.setObjectName("headerInfo")
            hlay.addWidget(info)
            root.addWidget(header)

            tool_row = QHBoxLayout()
            self.btn_hand = QPushButton(self._icon("hand_open", None), "  Hand Tool  (H)")
            self.btn_hand.setObjectName("tool")
            self.btn_hand.setProperty("active", "true")
            self.btn_hand.clicked.connect(self.activate_hand)
            tool_row.addWidget(self.btn_hand)
            self.btn_zoom = QPushButton(self._icon("zoom_in", None), "  Zoom Tool  (Z)")
            self.btn_zoom.setObjectName("tool")
            self.btn_zoom.clicked.connect(self.activate_zoom)
            tool_row.addWidget(self.btn_zoom)
            self.zoom_percent_box = QFrame()
            self.zoom_percent_box.setObjectName("zoomPercentBox")
            zoom_box_layout = QHBoxLayout(self.zoom_percent_box)
            zoom_box_layout.setContentsMargins(0, 0, 0, 0)
            zoom_box_layout.setSpacing(0)
            self.zoom_percent_combo = QComboBox()
            self.zoom_percent_combo.setObjectName("zoomPercentCombo")
            self.zoom_percent_combo.setEditable(True)
            self.zoom_percent_combo.setInsertPolicy(QComboBox.NoInsert)
            self.zoom_percent_combo.setFixedWidth(112)
            self.zoom_percent_combo.setToolTip(
                "Preview zoom presets. Type a percent value and press Enter to zoom manually."
            )
            self.zoom_percent_combo.addItems([f"{value}%" for value in self._zoom_presets])
            self.zoom_percent_combo.setCurrentText("100%")
            if self.zoom_percent_combo.lineEdit() is not None:
                self.zoom_percent_combo.lineEdit().installEventFilter(self)
                self.zoom_percent_combo.lineEdit().returnPressed.connect(self._apply_zoom_percent_text)
                self.zoom_percent_combo.lineEdit().editingFinished.connect(self._apply_zoom_percent_text)
            self.zoom_percent_combo.activated.connect(lambda _idx: self._apply_zoom_percent_text())
            zoom_box_layout.addWidget(self.zoom_percent_combo)
            tool_row.addWidget(self.zoom_percent_box)
            tool_row.addStretch(1)
            tool_row.addWidget(self._btn("Reset Zoom  (Ctrl+0)", self.reset_zoom))
            tool_row.addWidget(self._btn("Reset Crop  (Ctrl+R)", self.reset_crop))
            root.addLayout(tool_row)

            self.canvas = CropCanvas(self.source_w, self.source_h)
            self.canvas.margins_drag_started.connect(self._on_drag_started)
            self.canvas.margins_drag_finished.connect(self._on_drag_finished)
            self.canvas.margins_changed.connect(self._refresh_info)
            self.canvas.zoom_changed.connect(self._on_canvas_zoom_changed)
            self.canvas.request_toggle_playback.connect(self.toggle_playback)
            self.canvas.setFocusPolicy(Qt.StrongFocus)
            self.canvas.installEventFilter(self)
            QApplication.instance().installEventFilter(self)
            root.addWidget(self.canvas, 1)

            self.info_label = QLabel("")
            self.info_label.setObjectName("dim")
            root.addWidget(self.info_label)

            seek_row = QHBoxLayout()
            seek_row.setSpacing(6)
            self.btn_play = QPushButton(self._icon("play", QStyle.SP_MediaPlay), " Play  (Space)")
            self.btn_play.setObjectName("primary")
            self.btn_play.clicked.connect(self.toggle_playback)
            seek_row.addWidget(self.btn_play)
            seek_row.addWidget(self._btn("⏮  Home", self.go_home))
            seek_row.addWidget(self._btn("−10s (Shift+←)", lambda: self._seek_relative(-10.0)))
            seek_row.addWidget(self._btn("+10s (Shift+→)", lambda: self._seek_relative(10.0)))
            seek_row.addWidget(self._btn("End  ⏭", self.go_end))
            seek_row.addStretch(1)
            self.btn_mute = QPushButton(self._icon("volume_meter_3", QStyle.SP_MediaVolume), "  Mute (M)")
            self.btn_mute.setMinimumWidth(118)
            self.btn_mute.setIconSize(QtCore.QSize(20, 20))
            self.btn_mute.setToolTip("Mute / unmute (M)")
            self.btn_mute.clicked.connect(self.toggle_mute)
            seek_row.addWidget(self.btn_mute)
            self.volume_slider = QSlider(Qt.Horizontal)
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setValue(60)
            self.volume_slider.setFixedWidth(160)
            self.volume_slider.valueChanged.connect(self._on_volume_changed)
            self.volume_slider.installEventFilter(self)
            seek_row.addWidget(self.volume_slider)
            self.volume_label = QLabel("60%")
            self.volume_label.setObjectName("dim")
            seek_row.addWidget(self.volume_label)
            root.addLayout(seek_row)

            time_row = QHBoxLayout()
            time_row.setSpacing(8)
            time_frame = QFrame()
            time_frame.setObjectName("timelineViewFrame")
            time_frame.setStyleSheet(f"""
                QFrame#timelineViewFrame {{
                    background: #111820;
                    border: 1px solid {PALETTE['border_strong']};
                    border-radius: 9px;
                }}
            """)
            time_frame.setMinimumWidth(760)
            time_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            time_frame_layout = QHBoxLayout(time_frame)
            time_frame_layout.setContentsMargins(3, 1, 3, 1)
            time_frame_layout.setSpacing(3)
            self.btn_time_seek_left = QPushButton("◀")
            self.btn_time_seek_left.setObjectName("timelineZoomArrow")
            self.btn_time_seek_left.setFixedSize(14, 14)
            self.btn_time_seek_left.setAutoRepeat(True)
            self.btn_time_seek_left.setAutoRepeatDelay(220)
            self.btn_time_seek_left.setAutoRepeatInterval(70)
            self.btn_time_seek_left.setToolTip("Seek preview backward")
            self.btn_time_seek_left.clicked.connect(lambda: self._nudge_time_slider(-1))
            time_frame_layout.addWidget(self.btn_time_seek_left)
            self.time_slider = QSlider(Qt.Horizontal)
            self.time_slider.setRange(0, max(1, int(self.duration * 1000)))
            self.time_slider.setTracking(True)
            self.time_slider.setSingleStep(max(1, int(1000 / max(1.0, self.fps))))
            self.time_slider.setPageStep(5000)
            self.time_slider.valueChanged.connect(lambda v: self._seek(v / 1000.0))
            self.time_slider.sliderMoved.connect(lambda v: self._seek(v / 1000.0))
            self.time_slider.installEventFilter(self)
            time_frame_layout.addWidget(self.time_slider, 1)
            self.btn_time_seek_right = QPushButton("▶")
            self.btn_time_seek_right.setObjectName("timelineZoomArrow")
            self.btn_time_seek_right.setFixedSize(14, 14)
            self.btn_time_seek_right.setAutoRepeat(True)
            self.btn_time_seek_right.setAutoRepeatDelay(220)
            self.btn_time_seek_right.setAutoRepeatInterval(70)
            self.btn_time_seek_right.setToolTip("Seek preview forward")
            self.btn_time_seek_right.clicked.connect(lambda: self._nudge_time_slider(1))
            time_frame_layout.addWidget(self.btn_time_seek_right)
            time_row.addWidget(time_frame, 1)
            self.time_label = QLabel("")
            self.time_label.setObjectName("dim")
            self.time_label.setMinimumWidth(168)
            self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            time_row.addWidget(self.time_label)
            root.addLayout(time_row)

            confirm_row = QHBoxLayout()
            tip = QLabel("Tip: Ctrl+drag moves crop. Mouse wheel over timeline seeks. Ctrl+(+) / Ctrl+(-) zooms preview.\n"
                         "Double-click Hand Tool resets zoom.")
            tip.setObjectName("tip")
            tip.setWordWrap(False)
            tip.setMinimumWidth(760)
            tip.setMinimumHeight(42)
            confirm_row.addWidget(tip, 1)
            confirm_row.addStretch(1)
            cancel_btn = QPushButton("Cancel  (Esc)")
            cancel_btn.setObjectName("danger")
            cancel_btn.clicked.connect(self.cancel)
            confirm_row.addWidget(cancel_btn)
            apply_btn = QPushButton(self._icon("check", QStyle.SP_DialogOkButton),
                                     "  Apply  (Enter)")
            apply_btn.setObjectName("primary")
            apply_btn.clicked.connect(self.confirm)
            confirm_row.addWidget(apply_btn)
            root.addLayout(confirm_row)

            self._refresh_info()
            self._update_undo_redo_state()
            self._update_zoom_tool_icon()
            _gui_log_debug(
                f"Crop GUI layout initialized in {time.perf_counter() - layout_start:.3f}s",
                force=True,
            )

        def _btn(self, text, slot):
            b = QPushButton(text)
            b.clicked.connect(slot)
            return b

        def _setup_player(self):
            QtMultimedia = _import_qt_multimedia()
            self._media_player_cls = QtMultimedia.QMediaPlayer
            self.player = QtMultimedia.QMediaPlayer(self)
            self.audio = QtMultimedia.QAudioOutput(self)
            self.audio.setVolume(self.volume_slider.value() / 100.0)
            self.player.setAudioOutput(self.audio)
            self.video_sink = QtMultimedia.QVideoSink(self)
            self.player.setVideoSink(self.video_sink)
            self.video_sink.videoFrameChanged.connect(self._on_video_frame)
            self.player.positionChanged.connect(self._on_player_position)
            self.player.playbackStateChanged.connect(self._on_playback_state_changed)
            self.player.errorOccurred.connect(self._on_player_error)
            self.player.setSource(QUrl.fromLocalFile(str(self.input_path)))
            self.player.pause()
            self._update_volume_icon()

        def eventFilter(self, obj, event):
            canvas = getattr(self, "canvas", None)
            volume_slider = getattr(self, "volume_slider", None)
            time_slider = getattr(self, "time_slider", None)
            if obj is getattr(self, "btn_hand", None) and event.type() == QtCore.QEvent.MouseButtonDblClick:
                self.reset_zoom()
                return True
            zoom_line = self.zoom_percent_combo.lineEdit() if hasattr(self, "zoom_percent_combo") and self.zoom_percent_combo.lineEdit() is not None else None
            if zoom_line is not None and event.type() == QtCore.QEvent.MouseButtonPress:
                try:
                    clicked_zoom_control = obj is zoom_line or obj is self.zoom_percent_combo or self.zoom_percent_combo.isAncestorOf(obj)
                except Exception:
                    clicked_zoom_control = False
                if self._zoom_editor_active and not clicked_zoom_control:
                    self._apply_zoom_percent_text()
                    zoom_line.deselect()
                    zoom_line.clearFocus()
                    self.zoom_percent_combo.clearFocus()
                    self._zoom_editor_active = False
                    if obj is canvas:
                        canvas.setFocus(Qt.MouseFocusReason)
                    else:
                        self.setFocus(Qt.MouseFocusReason)
            if obj is zoom_line:
                if event.type() == QtCore.QEvent.MouseButtonPress:
                    self._zoom_editor_active = True
                    if not zoom_line.hasFocus() or not zoom_line.hasSelectedText():
                        self._zoom_select_all_pending = True
                        QTimer.singleShot(0, self._select_zoom_percent_text_once)
                elif event.type() == QtCore.QEvent.FocusIn:
                    self._zoom_editor_active = True
                    self._zoom_select_all_pending = True
                    QTimer.singleShot(0, self._select_zoom_percent_text_once)
                elif event.type() == QtCore.QEvent.FocusOut:
                    self._apply_zoom_percent_text()
                    zoom_line.deselect()
                    self._zoom_editor_active = False
                    self._zoom_select_all_pending = False
            if obj is canvas and event.type() == QtCore.QEvent.KeyPress:
                if event.nativeVirtualKey() == WIN_VK.get("alt") or (event.modifiers() & Qt.AltModifier):
                    self._update_zoom_tool_icon(True, force=True)
                    return True
            if obj is canvas and event.type() == QtCore.QEvent.KeyRelease:
                if event.nativeVirtualKey() == WIN_VK.get("alt"):
                    self._update_zoom_tool_icon(False, force=True)
                    return True
            if obj is volume_slider and event.type() == QtCore.QEvent.Wheel:
                delta = event.angleDelta().y()
                step = 3 if (event.modifiers() & Qt.ControlModifier) else 5
                volume_slider.setValue(max(0, min(100, volume_slider.value() + (step if delta > 0 else -step))))
                return True
            if obj is time_slider and event.type() == QtCore.QEvent.Wheel:
                delta = event.angleDelta().y()
                step = 10.0 if (event.modifiers() & Qt.ShiftModifier) else 1.0 if (event.modifiers() & Qt.ControlModifier) else 5.0
                self._seek(self._timestamp + (step if delta > 0 else -step))
                event.accept()
                return True
            if obj is time_slider and event.type() in {
                QtCore.QEvent.MouseButtonPress,
                QtCore.QEvent.MouseMove,
                QtCore.QEvent.MouseButtonRelease,
            }:
                buttons = event.buttons() if hasattr(event, "buttons") else Qt.NoButton
                if event.type() == QtCore.QEvent.MouseButtonPress and getattr(event, "button", lambda: Qt.NoButton)() != Qt.LeftButton:
                    return False
                if event.type() == QtCore.QEvent.MouseMove and not (buttons & Qt.LeftButton):
                    return False
                if event.type() != QtCore.QEvent.MouseButtonRelease or getattr(event, "button", lambda: Qt.LeftButton)() == Qt.LeftButton:
                    x = int(max(0, min(time_slider.width(), event.position().x())))
                    value = QStyle.sliderValueFromPosition(
                        time_slider.minimum(),
                        time_slider.maximum(),
                        x,
                        max(1, time_slider.width()),
                    )
                    self._seek(value / 1000.0)
                    event.accept()
                    return True
            return super().eventFilter(obj, event)

        def _is_playing(self):
            if not hasattr(self, "player"):
                return False
            return self.player.playbackState() == self._media_player_cls.PlayingState

        def toggle_playback(self):
            if not hasattr(self, "player"):
                return
            if self._is_playing():
                self.player.pause()
                self._request_frame()
            else:
                pos_ms = self.player.position()
                dur_ms = max(0, self.player.duration() or int(self.duration * 1000))
                if dur_ms > 0 and pos_ms >= dur_ms - 500:
                    self.player.setPosition(0)
                self.player.play()
            self._on_playback_state_changed(self.player.playbackState())

        def _on_playback_state_changed(self, _state):
            if self._is_playing():
                self.btn_play.setText(" Pause  (Space)")
                self.btn_play.setIcon(self._icon("pause", QStyle.SP_MediaPause))
            else:
                self.btn_play.setText(" Play  (Space)")
                self.btn_play.setIcon(self._icon("play", QStyle.SP_MediaPlay))

        def _on_video_frame(self, frame):
            try:
                image = frame.toImage()
            except Exception:
                image = None
            if image is not None and not image.isNull():
                self.canvas.set_image(image)

        def _on_player_position(self, ms):
            self._timestamp = max(0.0, min(self.duration, ms / 1000.0))
            self.time_slider.blockSignals(True)
            self.time_slider.setValue(int(round(self._timestamp * 1000)))
            self.time_slider.blockSignals(False)
            self.time_label.setText(f"{seconds_to_hmsf(self._timestamp, self.fps)} / {seconds_to_hmsf(self.duration, self.fps)}")
            self._refresh_info()

        def _on_player_error(self, _err, msg):
            if msg:
                self.info_label.setText(f"Player message: {msg}")

        def _on_volume_changed(self, value):
            if not hasattr(self, "audio"):
                return
            self.audio.setVolume(max(0, min(100, value)) / 100.0)
            self.volume_label.setText(f"{value}%")
            self._update_volume_icon()

        def toggle_mute(self):
            if not hasattr(self, "audio"):
                return
            self.audio.setMuted(not self.audio.isMuted())
            self._update_volume_icon()

        def _update_volume_icon(self):
            value = self.volume_slider.value()
            if hasattr(self, "audio") and self.audio.isMuted():
                icon = "volume_meter_muted"
            elif value <= 0:
                icon = "volume_meter_muted"
            elif value <= 25:
                icon = "volume_meter_1"
            elif value <= 50:
                icon = "volume_meter_2"
            elif value <= 75:
                icon = "volume_meter_3"
            else:
                icon = "volume_meter_4"
            self.btn_mute.setIcon(self._icon(icon, QStyle.SP_MediaVolume))

        def _update_zoom_tool_icon(self, zoom_out=None, force=False):
            if zoom_out is None:
                zoom_out = bool(QApplication.keyboardModifiers() & Qt.AltModifier)
            self.canvas.set_zoom_out_mode(bool(zoom_out), force=force)
            self.btn_zoom.setIcon(self._icon("zoom_out" if zoom_out else "zoom_in", None))
            _gui_log_debug(f"Crop zoom Alt mode set to {'out' if zoom_out else 'in'}", force=force)

        def _on_drag_started(self):
            self._drag_snapshot = self._take_snapshot()

        def _on_drag_finished(self):
            if self._drag_snapshot is None:
                return
            current = self._take_snapshot()
            pre = self._drag_snapshot
            self._drag_snapshot = None
            self.canvas.margins = list(pre.margins)
            self._history.push(self._take_snapshot())
            self.canvas.margins = list(current.margins)
            self._history.push(self._take_snapshot())
            self.canvas.update()
            self._refresh_info()
            self._update_undo_redo_state()

        def activate_hand(self):
            self.canvas.set_tool("hand")
            self.btn_hand.setProperty("active", "true")
            self.btn_zoom.setProperty("active", "false")
            self._restyle_tools()

        def activate_zoom(self):
            self.canvas.set_tool("zoom")
            self.btn_hand.setProperty("active", "false")
            self.btn_zoom.setProperty("active", "true")
            self._update_zoom_tool_icon(force=True)
            self._restyle_tools()

        def _restyle_tools(self):
            for w in (self.btn_hand, self.btn_zoom):
                w.style().unpolish(w); w.style().polish(w); w.update()

        def reset_zoom(self):
            self.canvas.zoom = 1.0
            self.canvas._scroll = QPointF(0, 0)
            self.canvas.update()
            self._sync_zoom_percent_control()
            self._refresh_info()

        def _zoom_percent_value(self):
            return int(round(self.canvas.zoom * 100))

        def _sync_zoom_percent_control(self):
            if not hasattr(self, "zoom_percent_combo"):
                return
            value = self._zoom_percent_value()
            text = f"{value}%"
            self._syncing_zoom_control = True
            try:
                self.zoom_percent_combo.blockSignals(True)
                self.zoom_percent_combo.setCurrentText(text)
                self.zoom_percent_combo.blockSignals(False)
            finally:
                self._syncing_zoom_control = False

        def _focus_zoom_percent_editor(self):
            self._zoom_editor_active = True
            self.zoom_percent_combo.setFocus(Qt.MouseFocusReason)
            if self.zoom_percent_combo.lineEdit() is not None:
                self._zoom_select_all_pending = True
                QTimer.singleShot(0, self._select_zoom_percent_text_once)
            self.zoom_percent_combo.showPopup()

        def _select_zoom_percent_text_once(self):
            if not self._zoom_select_all_pending:
                return
            if self.zoom_percent_combo.lineEdit() is not None:
                self.zoom_percent_combo.lineEdit().selectAll()
            self._zoom_select_all_pending = False

        def _apply_zoom_percent_text(self):
            if self._syncing_zoom_control or not hasattr(self, "zoom_percent_combo"):
                return
            text = self.zoom_percent_combo.currentText().strip()
            match = re.search(r"\d+(?:[.,]\d+)?", text)
            if not match:
                self._sync_zoom_percent_control()
                return
            value = float(match.group(0).replace(",", "."))
            value = max(10.0, min(3200.0, value))
            self._set_preview_zoom_percent(value)

        def _set_preview_zoom_percent(self, value):
            p = self._preview_zoom_focus()
            self.canvas._set_zoom_centered(p.x(), p.y(), float(value) / 100.0)
            self._sync_zoom_percent_control()
            self._refresh_info()

        def _on_canvas_zoom_changed(self):
            self._sync_zoom_percent_control()
            self._refresh_info()

        def _preview_zoom_focus(self):
            p = self.canvas.mapFromGlobal(QCursor.pos())
            if self.canvas.rect().contains(p):
                return QPointF(p.x(), p.y())
            return QPointF(self.canvas.width() / 2.0, self.canvas.height() / 2.0)

        def zoom_preview_in(self):
            p = self._preview_zoom_focus()
            self.canvas._zoom_centered(p.x(), p.y(), 1.15)
            self._refresh_info()

        def zoom_preview_out(self):
            p = self._preview_zoom_focus()
            self.canvas._zoom_centered(p.x(), p.y(), 1.0 / 1.15)
            self._refresh_info()

        def reset_crop(self):
            if self.canvas.margins == [0, 0, 0, 0]:
                return
            self.canvas.set_margins([0, 0, 0, 0])
            self._refresh_info()
            self._commit_history()

        def go_home(self):
            self._seek(0.0)

        def go_end(self):
            self._seek(self.duration)

        def _seek_relative(self, dt):
            self._seek(self._timestamp + dt)

        def _nudge_time_slider(self, direction):
            frame_step = 1.0 / max(1.0, self.fps)
            self._seek(self._timestamp + float(direction) * max(frame_step, 0.25))

        def _seek(self, t):
            self._timestamp = max(0.0, min(self.duration, float(t)))
            target_ms = int(round(self._timestamp * 1000))
            if hasattr(self, "player") and abs(self.player.position() - target_ms) > 40:
                self.player.setPosition(target_ms)
            self.time_slider.blockSignals(True)
            self.time_slider.setValue(target_ms)
            self.time_slider.blockSignals(False)
            self.time_label.setText(f"{seconds_to_hmsf(self._timestamp, self.fps)} / {seconds_to_hmsf(self.duration, self.fps)}")
            if not hasattr(self, "player") or not self._is_playing():
                self._request_frame()
            self._refresh_info()

        def _request_frame(self):
            target_w = max(320, self.canvas.width())
            target_h = max(180, self.canvas.height())
            req = (round(self._timestamp * 1000), target_w, target_h)
            self._pending_request = req
            if self._worker is not None and self._worker.isRunning():
                return
            self._launch_worker()

        def _launch_worker(self):
            if not self._pending_request:
                return
            ts_ms, w, h = self._pending_request
            self._pending_request = None
            self._frame_request_started = time.perf_counter()
            self._worker = FrameExtractWorker(self.ffmpeg, self.input_path,
                                              ts_ms / 1000.0, w, h, parent=self)
            self._worker.finished_with_image.connect(self._on_frame_extracted)
            self._worker.finished.connect(self._worker.deleteLater)
            self._worker.start()

        def _on_frame_extracted(self, image):
            if not image.isNull():
                self.canvas.set_image(image)
            if self._frame_request_started:
                _gui_log_debug(
                    f"Crop preview frame loaded in {time.perf_counter() - self._frame_request_started:.3f}s",
                    force=True,
                )
                self._frame_request_started = 0.0
            self._worker = None
            if self._pending_request:
                self._launch_worker()

        def _stop_worker(self):
            self._pending_request = None
            worker = self._worker
            self._worker = None
            if worker is not None and worker.isRunning():
                worker.stop()
                if not worker.wait(3000):
                    worker.terminate()
                    worker.wait(1000)

        def _refresh_info(self):
            t, l, r, b = self.canvas.margins
            self.info_label.setText(
                f"Crop margins:  top={t} px  left={l} px  right={r} px  bottom={b} px      "
                f"Output crop box:  {max(1, self.source_w - l - r)}x{max(1, self.source_h - t - b)}      "
                f"Time:  {seconds_to_hmsf(self._timestamp, self.fps)} / {seconds_to_hmsf(self.duration, self.fps)}      "
                f"Tool:  {'Zoom' if self.canvas.tool == 'zoom' else 'Hand'}      "
                f"Zoom:  {int(round(self.canvas.zoom * 100))}%"
            )

        def _install_shortcuts(self):
            QShortcut(QKeySequence("Space"), self).activated.connect(self.toggle_playback)
            QShortcut(QKeySequence("M"), self).activated.connect(self.toggle_mute)
            QShortcut(QKeySequence("H"), self).activated.connect(self.activate_hand)
            QShortcut(QKeySequence("Z"), self).activated.connect(self.activate_zoom)
            QShortcut(QKeySequence("R"), self).activated.connect(self.reset_crop)
            QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.reset_crop)
            QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self.reset_zoom)
            QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self.zoom_preview_in)
            QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self.zoom_preview_in)
            QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self.zoom_preview_out)
            QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._undo)
            QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self._redo)
            QShortcut(QKeySequence("Ctrl+Shift+Z"), self).activated.connect(self._redo)
            QShortcut(QKeySequence(Qt.Key_Home), self).activated.connect(self.go_home)
            QShortcut(QKeySequence(Qt.Key_End), self).activated.connect(self.go_end)
            QShortcut(QKeySequence("Shift+Left"), self).activated.connect(lambda: self._seek_relative(-10.0))
            QShortcut(QKeySequence("Shift+Right"), self).activated.connect(lambda: self._seek_relative(10.0))
            QShortcut(QKeySequence("Return"), self).activated.connect(self._confirm_or_apply_zoom_editor)
            QShortcut(QKeySequence("Enter"), self).activated.connect(self._confirm_or_apply_zoom_editor)
            QShortcut(QKeySequence("Escape"), self).activated.connect(self.cancel)

        def _zoom_editor_has_focus(self):
            if not hasattr(self, "zoom_percent_combo") or self.zoom_percent_combo.lineEdit() is None:
                return False
            focus = QApplication.focusWidget()
            return (
                self._zoom_editor_active
                or focus is self.zoom_percent_combo
                or focus is self.zoom_percent_combo.lineEdit()
                or self.zoom_percent_combo.hasFocus()
                or self.zoom_percent_combo.lineEdit().hasFocus()
            )

        def _confirm_or_apply_zoom_editor(self):
            if self._zoom_editor_has_focus():
                self._apply_zoom_percent_text()
                return
            self.confirm()

        def keyPressEvent(self, event):
            vk = event.nativeVirtualKey()
            mods = event.modifiers()
            ctrl = bool(mods & Qt.ControlModifier)
            shift = bool(mods & Qt.ShiftModifier)
            if (mods & Qt.AltModifier) or vk == WIN_VK.get("alt"):
                self._update_zoom_tool_icon(True, force=True)
                if vk == WIN_VK.get("alt"):
                    event.accept()
                    return
            if ctrl and not shift:
                if vk == WIN_VK["z"]: self._undo(); return
                if vk == WIN_VK["y"]: self._redo(); return
                if vk == WIN_VK["r"]: self.reset_crop(); return
                if vk == WIN_VK["0"]: self.reset_zoom(); return
                if vk in (WIN_VK["plus"], WIN_VK["equal"], WIN_VK["kp_add"]): self.zoom_preview_in(); return
                if vk in (WIN_VK["minus"], WIN_VK["kp_subtract"]): self.zoom_preview_out(); return
            if ctrl and shift and vk == WIN_VK["z"]:
                self._redo(); return
            if shift and not ctrl and vk == WIN_VK["left"]:
                self._seek_relative(-10.0); return
            if shift and not ctrl and vk == WIN_VK["right"]:
                self._seek_relative(10.0); return
            if not ctrl and not shift:
                pan_step = max(8, int(min(self.canvas.width(), self.canvas.height()) * 0.035))
                if vk == WIN_VK["left"]: self.canvas.pan_by(pan_step, 0); return
                if vk == WIN_VK["right"]: self.canvas.pan_by(-pan_step, 0); return
                if vk == WIN_VK["up"]: self.canvas.pan_by(0, pan_step); return
                if vk == WIN_VK["down"]: self.canvas.pan_by(0, -pan_step); return
                actions = {
                    WIN_VK["space"]: self.toggle_playback,
                    WIN_VK["m"]: self.toggle_mute,
                    WIN_VK["h"]: self.activate_hand,
                    WIN_VK["z"]: self.activate_zoom,
                    WIN_VK["r"]: self.reset_crop,
                    WIN_VK["home"]: self.go_home,
                    WIN_VK["end"]: self.go_end,
                    WIN_VK["return"]: self._confirm_or_apply_zoom_editor,
                    WIN_VK["escape"]: self.cancel,
                }
                fn = actions.get(vk)
                if fn is not None:
                    fn(); return
            super().keyPressEvent(event)

        def keyReleaseEvent(self, event):
            vk = event.nativeVirtualKey()
            if vk == WIN_VK.get("alt"):
                self._update_zoom_tool_icon(False, force=True)
                event.accept()
                return
            self._update_zoom_tool_icon(force=True)
            super().keyReleaseEvent(event)

        def confirm(self):
            self.result = {"status": "ok", "margins": snap_crop_margins_even(self.canvas.margins, int(self.source_w), int(self.source_h))}
            try:
                self.player.stop()
            except Exception:
                pass
            self._stop_worker()
            self.close()

        def cancel(self):
            self.result = {"status": "canceled", "margins": [0, 0, 0, 0]}
            try:
                self.player.stop()
            except Exception:
                pass
            self._stop_worker()
            self.close()

        def closeEvent(self, event):
            try:
                self.player.stop()
            except Exception:
                pass
            self._stop_worker()
            super().closeEvent(event)
    return CropEditorWindow(request)
