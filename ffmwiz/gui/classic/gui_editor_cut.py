from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.classic.gui_editor_cut_input import build_cut_input_mixin
from ffmwiz.gui.classic.gui_editor_cut_layout import build_cut_editor_layout
from ffmwiz.gui.classic.gui_editor_cut_widgets import build_cut_editor_widgets


def build_cut_editor(request: dict[str, Any]):
    QtCore, QtGui, QtWidgets, QtMultimedia = _import_qt()

    QUrl = QtCore.QUrl

    QAction = QtGui.QAction

    QApplication = QtWidgets.QApplication
    QStyle = QtWidgets.QStyle
    QMainWindow = QtWidgets.QMainWindow
    QPushButton = QtWidgets.QPushButton
    QListWidgetItem = QtWidgets.QListWidgetItem
    QMessageBox = QtWidgets.QMessageBox
    QMenu = QtWidgets.QMenu
    QGraphicsOpacityEffect = QtWidgets.QGraphicsOpacityEffect

    HeaderBand, StatusStrip, VideoPreview, TimelineWidget = build_cut_editor_widgets()
    CutEditorInputMixin = build_cut_input_mixin()

    class CutEditorWindow(CutEditorInputMixin, QMainWindow):
        def __init__(self, request):
            init_start = time.perf_counter()
            super().__init__()
            self.request = request
            self.input_path = Path(request["input_path"])
            requested_fps = request.get("fps")
            self.fps = float(requested_fps or 25.0)
            if not requested_fps:
                print("Cut Editor FPS fallback: using 25.000 fps.", file=sys.stderr)
            self.duration = float(request.get("duration") or 0.0)
            self.chapters = normalize_chapters(request.get("chapters") or [], self.duration)
            self._log_path = Path(request["log_path"]) if request.get("log_path") else None
            self.result = {"status": "canceled", "keep_ranges": []}

            # Marker model state. Cut ranges are derived from the markers.
            self._next_marker_id = 1
            self._markers: list = []
            self._selected_marker_ids: set[int] = set()
            self._selected_cut = -1
            self._drag_snapshot = None
            self._syncing_timeline_zoom = False
            self._syncing_timeline_navigation = False
            self._media_ready = False

            self._history = HistoryStack(self._take_snapshot(), max_size=100)

            self.setWindowTitle("FFmWiz Cut Editor")
            self._icon = _icon_loader(self, self.style())
            _apply_window_icon(self, self._icon)
            self.setMinimumSize(1180, 860)
            self.resize(1360, 900)

            build_cut_editor_layout(self, HeaderBand, StatusStrip,
                                    VideoPreview, TimelineWidget)
            self._install_shortcuts()
            self._refresh_all()
            _gui_log_debug(
                f"Cut GUI widgets initialized in {time.perf_counter() - init_start:.3f}s",
                force=True,
            )
            QtCore.QTimer.singleShot(150, self._deferred_media_startup)

        def _deferred_media_startup(self):
            start = time.perf_counter()
            self._setup_player()
            self._media_ready = True
            self._refresh_all()
            _gui_log_debug(
                f"Cut GUI media startup completed in {time.perf_counter() - start:.3f}s",
                force=True,
            )

        def _take_snapshot(self):
            return CutSnapshot(
                markers=[copy.copy(m) for m in self._markers],
                selected_marker_ids=tuple(sorted(self._selected_marker_ids)),
            )

        def _commit_history(self):
            self._history.push(self._take_snapshot())
            self._update_undo_redo_state()

        def _restore_snapshot(self, snap):
            self._markers = [copy.copy(m) for m in snap.markers]
            self._selected_marker_ids = set(getattr(snap, "selected_marker_ids", ()))
            self._selected_cut = -1
            self._refresh_all_no_history()

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

        def _apply_disabled_opacity(self, button):
            if button.isEnabled():
                button.setGraphicsEffect(None)
                return
            effect = button.graphicsEffect()
            if not isinstance(effect, QGraphicsOpacityEffect):
                effect = QGraphicsOpacityEffect(button)
                button.setGraphicsEffect(effect)
            effect.setOpacity(0.32)

        def _selected_marker(self):
            if len(self._selected_marker_ids) != 1:
                return None
            marker_id = next(iter(self._selected_marker_ids))
            for m in self._markers:
                if m.id == marker_id:
                    return m
            return None

        def _set_selected_marker(self, marker_id, additive=False):
            if marker_id < 0:
                self._selected_marker_ids.clear()
            elif additive:
                if marker_id in self._selected_marker_ids:
                    self._selected_marker_ids.remove(marker_id)
                else:
                    self._selected_marker_ids.add(marker_id)
            else:
                self._selected_marker_ids = {marker_id}
            self._selected_cut = -1
            self._refresh_all_no_history()

        def _clear_marker_selection(self):
            self._selected_marker_ids.clear()

        def _add_marker(self, time, kind):
            m = Marker(id=self._next_marker_id, time=time, kind=kind)
            self._next_marker_id += 1
            self._markers.append(m)
            return m

        def _replace_cut_ranges_with_markers(self, ranges):
            self._markers = []
            for start, end in normalize_ranges(ranges, self.duration):
                self._add_marker(start, "in")
                self._add_marker(end, "out")

        def _cuts(self):
            return compute_cut_ranges(self._markers, self.duration)

        def _log_debug(self, message):
            line = f"Cut Editor DEBUG: {message}\n"
            if self._log_path is not None:
                try:
                    with self._log_path.open("a", encoding="utf-8") as handle:
                        handle.write(line)
                    return
                except Exception:
                    pass
            print(line.rstrip(), file=sys.stderr)

        def _tbtn(self, icon, label, tip, slot):
            b = QPushButton(label) if icon is None else QPushButton(icon, " " + label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        def _setup_player(self):
            QtMultimedia = _import_qt_multimedia()
            self._media_player_cls = QtMultimedia.QMediaPlayer
            self.player = QtMultimedia.QMediaPlayer(self)
            self.audio = QtMultimedia.QAudioOutput(self)
            self.audio.setVolume(0.6)
            self.player.setAudioOutput(self.audio)
            self.video_sink = QtMultimedia.QVideoSink(self)
            self.player.setVideoSink(self.video_sink)
            self.video_sink.videoFrameChanged.connect(self.preview.on_frame)
            self.player.positionChanged.connect(self._on_player_position)
            self.player.errorOccurred.connect(self._on_player_error)
            self.player.setSource(QUrl.fromLocalFile(str(self.input_path)))
            self.player.pause()

        def _on_player_error(self, _err, msg):
            if msg:
                self.status_strip._label.setText(f"Player message: {msg}")

        def _is_playing(self):
            if not hasattr(self, "player"):
                return False
            return self.player.playbackState() == self._media_player_cls.PlayingState

        def toggle_playback(self):
            if not hasattr(self, "player"):
                return
            if self._is_playing():
                self.player.pause()
                self.btn_play.setText(" Play (Space)")
                self.btn_play.setIcon(self._icon("play", QStyle.SP_MediaPlay))
            else:
                pos_ms = self.player.position()
                dur_ms = max(0, self.player.duration() or int(self.duration * 1000))
                if dur_ms > 0 and pos_ms >= dur_ms - 500:
                    self.player.setPosition(0)
                self.player.play()
                self.btn_play.setText(" Pause (Space)")
                self.btn_play.setIcon(self._icon("pause", QStyle.SP_MediaPause))

        def go_home(self):
            if hasattr(self, "player"):
                self.player.setPosition(0)
            self.timeline.set_playhead(0.0, follow=True, force_visible=True)

        def go_end(self):
            dur_ms = int(self.duration * 1000)
            if hasattr(self, "player"):
                dur_ms = max(0, self.player.duration() or dur_ms)
                self.player.setPosition(dur_ms)
            self.timeline.set_playhead(dur_ms / 1000.0, follow=True, force_visible=True)

        def _seek_relative(self, dt):
            cur = self._current_time()
            target = max(0.0, min(self.duration, cur + dt))
            if hasattr(self, "player"):
                self.player.setPosition(int(round(target * 1000)))
            self.timeline.set_playhead(target, follow=True)

        def _on_player_position(self, ms):
            self.timeline.set_playhead(ms / 1000.0, follow=True)
            self._refresh_status()

        def _on_timeline_seek(self, t):
            if hasattr(self, "player"):
                self.player.setPosition(int(round(t * 1000)))
            self.timeline.set_playhead(t, follow=True)
            self._refresh_status()

        def _on_volume_changed(self, value):
            # Pure audio-mixer change; does NOT seek or interrupt playback.
            if not hasattr(self, "audio"):
                return
            self.audio.setVolume(max(0, min(100, value)) / 100.0)
            self.volume_label.setText(f"{value}%")
            level = 0 if value <= 0 else 1 if value <= 25 else 2 if value <= 50 else 3 if value <= 75 else 4
            if value <= 0:
                self.btn_mute.setIcon(self._icon("volume_meter_muted", QStyle.SP_MediaVolumeMuted))
            elif not self.audio.isMuted():
                self.btn_mute.setIcon(self._icon(f"volume_meter_{level}", QStyle.SP_MediaVolume))

        def toggle_mute(self):
            if not hasattr(self, "audio"):
                return
            muted = not self.audio.isMuted()
            self.audio.setMuted(muted)
            if muted:
                self.btn_mute.setIcon(self._icon("volume_meter_muted", QStyle.SP_MediaVolumeMuted))
            else:
                value = self.volume_slider.value()
                level = 0 if value <= 0 else 1 if value <= 25 else 2 if value <= 50 else 3 if value <= 75 else 4
                icon = "volume_meter_muted" if value <= 0 else f"volume_meter_{level}"
                self.btn_mute.setIcon(self._icon(icon, QStyle.SP_MediaVolume))

        def _current_time(self):
            if not hasattr(self, "player"):
                return 0.0
            return self.player.position() / 1000.0

        def mark_in(self):
            sel = self._selected_marker()
            if sel is not None and sel.kind == "out":
                sel.kind = "in"
                self._refresh_all_no_history()
                self._commit_history()
                return
            self._add_marker(self._current_time(), "in")
            self._clear_marker_selection()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def mark_out(self):
            sel = self._selected_marker()
            if sel is not None and sel.kind == "in":
                sel.kind = "out"
                self._refresh_all_no_history()
                self._commit_history()
                return
            self._add_marker(self._current_time(), "out")
            self._clear_marker_selection()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def _unpaired_marker_summary(self):
            ordered = sorted(self._markers, key=lambda m: (m.time, 0 if m.kind == "in" else 1))
            warnings = []
            i = 0
            while i < len(ordered):
                marker = ordered[i]
                nxt = ordered[i + 1] if i + 1 < len(ordered) else None
                if marker.kind == "in" and nxt is not None and nxt.kind == "out" and nxt.time > marker.time:
                    i += 2
                    continue
                warnings.append(f"{marker.kind.upper()} at {seconds_to_hmsf(marker.time, self.fps)}")
                i += 1
            return warnings

        def add_cuts(self):
            cuts = self._cuts()
            if cuts:
                warnings = self._unpaired_marker_summary()
                message = f"Add Cut(s): {len(cuts)} valid adjacent pair(s) are active."
                if warnings:
                    message += " Unpaired marker(s): " + "; ".join(warnings[:4])
                    if len(warnings) > 4:
                        message += f"; +{len(warnings) - 4} more"
                self.status_strip._label.setText(message)
                self._refresh_all_no_history()
                return
            t = self._current_time()
            out_t = min(self.duration, max(t + 1.0, t + 0.1))
            self._add_marker(t, "in")
            self._add_marker(out_t, "out")
            self._clear_marker_selection()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def invert_cuts(self):
            if not math.isfinite(self.duration) or self.duration <= 0:
                self._log_debug(
                    f"Invert cuts refused: invalid duration={self.duration!r}; "
                    f"input_path={self.input_path}")
                QMessageBox.critical(
                    self,
                    "Cannot invert cuts",
                    "Cannot invert cut ranges because the video duration is unknown.")
                return

            before = normalize_ranges(self._cuts(), self.duration)
            if not before:
                self._log_debug(
                    f"Invert cuts refused: no valid cut ranges; input_path={self.input_path}")
                QMessageBox.warning(
                    self,
                    "No cut ranges",
                    "There are no valid cut ranges to invert.")
                return

            after = invert_cut_ranges(before, self.duration)
            self._log_debug(
                "Invert cuts before="
                + _format_debug_ranges(before)
                + " after="
                + _format_debug_ranges(after)
                + f" duration={self.duration:.6f}")

            self._replace_cut_ranges_with_markers(after)
            self._selected_marker_ids.clear()
            self._selected_cut = 0 if after else -1
            self._refresh_all_no_history()
            self._commit_history()
            self.status_strip._label.setText(
                f"Inverted cuts. Kept the previous {len(before)} cut range(s); "
                f"now removing {len(after)} range(s).")

        def delete_selected_item(self):
            if self._selected_marker_ids:
                self.delete_selected_markers()
            elif self._selected_cut >= 0:
                self.delete_selected_cut()

        def delete_selected_markers(self):
            if not self._selected_marker_ids:
                return
            selected = set(self._selected_marker_ids)
            before = len(self._markers)
            self._markers = [m for m in self._markers if m.id not in selected]
            if len(self._markers) == before:
                return
            self._selected_marker_ids.clear()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def delete_selected_cut(self):
            if self._selected_cut < 0:
                return
            cuts = self._cuts()
            if 0 <= self._selected_cut < len(cuts):
                cs, ce = cuts[self._selected_cut]
                self._markers = [
                    m for m in self._markers
                    if not (abs(m.time - cs) < 1e-6 and m.kind == "in")
                    and not (abs(m.time - ce) < 1e-6 and m.kind == "out")
                ]
                self._selected_cut = -1
                self._selected_marker_ids.clear()
                self._refresh_all_no_history()
                self._commit_history()

        def delete_all_markers(self):
            if not self._markers:
                return
            box = QMessageBox(self)
            box.setWindowTitle("Delete all markers")
            box.setText("Remove every marker? This can be undone with Ctrl+Z.")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.No)
            if box.exec() != QMessageBox.Yes:
                return
            self._markers = []
            self._selected_marker_ids.clear()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def snap_marker_prev(self):
            self._snap(direction=-1)

        def snap_marker_next(self):
            self._snap(direction=1)

        def _snap(self, direction):
            now = self._current_time()
            candidates = sorted({m.time for m in self._markers})
            target = None
            if direction < 0:
                for c in reversed(candidates):
                    if c < now - 1e-4:
                        target = c
                        break
            else:
                for c in candidates:
                    if c > now + 1e-4:
                        target = c
                        break
            if target is not None:
                if hasattr(self, "player"):
                    self.player.setPosition(int(round(target * 1000)))
                self.timeline.set_playhead(target, follow=True)

        def _on_marker_selection_requested(self, marker_id, additive):
            self._set_selected_marker(marker_id, additive=additive)

        def _on_marker_drag_started(self):
            self._drag_snapshot = self._take_snapshot()

        def _on_marker_drag_moved(self, marker_id, new_time):
            for m in self._markers:
                if m.id == marker_id:
                    m.time = max(0.0, min(self.duration, new_time))
                    break
            self._refresh_all_no_history()

        def _on_marker_drag_finished(self):
            if self._drag_snapshot is None:
                return
            current = self._take_snapshot()
            pre = self._drag_snapshot
            self._drag_snapshot = None
            self._markers = [copy.copy(m) for m in pre.markers]
            self._selected_marker_ids = set(pre.selected_marker_ids)
            self._history.push(self._take_snapshot())
            self._markers = [copy.copy(m) for m in current.markers]
            self._selected_marker_ids = set(current.selected_marker_ids)
            self._history.push(self._take_snapshot())
            self._refresh_all_no_history()

        def _on_cut_selected_from_timeline(self, idx):
            self._selected_cut = idx
            if idx >= 0:
                self._selected_marker_ids.clear()
            if idx < 0:
                self.cut_list.blockSignals(True)
                self.cut_list.clearSelection()
                self.cut_list.blockSignals(False)
                self.timeline.set_selected_cut(-1)
                self._update_button_states()
                return
            self.cut_list.blockSignals(True)
            self.cut_list.setCurrentRow(idx)
            self.cut_list.blockSignals(False)
            self.timeline.set_selected_cut(idx)
            self._update_button_states()

        def _on_cut_context(self, idx, global_pos):
            menu = QMenu(self)
            act_seek = QAction("Seek playhead to this cut", self)
            act_seek.triggered.connect(lambda: self._seek_to_cut(idx))
            menu.addAction(act_seek)
            menu.addSeparator()
            act_del = QAction("Delete this cut", self)
            act_del.triggered.connect(self.delete_selected_cut)
            menu.addAction(act_del)
            menu.exec(global_pos)

        def _seek_to_cut(self, idx):
            cuts = self._cuts()
            if 0 <= idx < len(cuts):
                if hasattr(self, "player"):
                    self.player.setPosition(int(round(cuts[idx][0] * 1000)))
                self.timeline.set_playhead(cuts[idx][0], follow=True)

        def _on_cut_list_select(self):
            items = self.cut_list.selectedItems()
            self._selected_cut = self.cut_list.row(items[0]) if items else -1
            if self._selected_cut >= 0:
                self._selected_marker_ids.clear()
            self.timeline.set_selected_cut(self._selected_cut)
            self._update_button_states()

        def timeline_zoom_in(self):
            self.timeline.zoom_in_at_playhead()
            self._sync_timeline_zoom_slider()
            self._sync_timeline_navigation_slider()

        def timeline_zoom_out(self):
            self.timeline.zoom_out_at_playhead()
            self._sync_timeline_zoom_slider()
            self._sync_timeline_navigation_slider()

        def timeline_fit(self):
            self.timeline.fit_view()
            self._sync_timeline_zoom_slider()
            self._sync_timeline_navigation_slider()

        def _on_timeline_zoom_slider(self, value):
            if self._syncing_timeline_zoom:
                return
            ratio = 64.0 ** (float(value) / 100.0)
            self.timeline.set_zoom_ratio(ratio)
            self._sync_timeline_navigation_slider()
            self._update_timeline_zoom_arrow_states()

        def _sync_timeline_zoom_slider(self):
            if not hasattr(self, "timeline_zoom_slider"):
                return
            ratio = self.timeline.zoom_ratio()
            ratio = max(1.0, min(64.0, ratio))
            value = int(round(math.log(ratio, 64.0) * 100.0))
            self._syncing_timeline_zoom = True
            self.timeline_zoom_slider.setValue(max(0, min(100, value)))
            self._syncing_timeline_zoom = False
            self._update_timeline_zoom_arrow_states()

        def _nudge_timeline_zoom(self, direction):
            if not hasattr(self, "timeline_zoom_slider"):
                return
            step = 2
            value = max(
                self.timeline_zoom_slider.minimum(),
                min(
                    self.timeline_zoom_slider.maximum(),
                    self.timeline_zoom_slider.value() + int(direction) * step,
                ),
            )
            self.timeline_zoom_slider.setValue(value)

        def _update_timeline_zoom_arrow_states(self):
            slider = getattr(self, "timeline_zoom_slider", None)
            if slider is None:
                return
            left = getattr(self, "btn_timeline_zoom_left", None)
            right = getattr(self, "btn_timeline_zoom_right", None)
            if left is not None:
                left.setEnabled(slider.value() > slider.minimum())
            if right is not None:
                right.setEnabled(slider.value() < slider.maximum())

        def _on_timeline_navigation_slider(self, value):
            if self._syncing_timeline_navigation:
                return
            self.timeline.set_view_start(float(value) / 1000.0)

        def _nudge_timeline_view(self, direction):
            span = max(0.001, self.timeline.state.view_span)
            self.timeline.scroll_view(float(direction) * span * 0.10)
            self._sync_timeline_navigation_slider()

        def _sync_timeline_navigation_slider(self):
            if not hasattr(self, "timeline_navigation_slider"):
                return
            duration_ms = max(1, int(round(max(0.001, self.timeline.state.duration) * 1000.0)))
            span_ms = max(1, int(round(max(0.001, self.timeline.state.view_span) * 1000.0)))
            max_start = max(0, duration_ms - span_ms)
            value = max(0, min(max_start, int(round(max(0.0, self.timeline.state.view_start) * 1000.0))))
            single_step = max(1, int(round(span_ms * 0.05)))
            self._syncing_timeline_navigation = True
            self.timeline_navigation_slider.setRange(0, max_start)
            self.timeline_navigation_slider.setPageStep(max(1, span_ms))
            self.timeline_navigation_slider.setSingleStep(single_step)
            self.timeline_navigation_slider.setEnabled(max_start > 0)
            self.timeline_navigation_slider.setValue(value)
            self._syncing_timeline_navigation = False
            for button_name in ("btn_timeline_view_left", "btn_timeline_view_right"):
                button = getattr(self, button_name, None)
                if button is not None:
                    button.setEnabled(max_start > 0)

        def _refresh_all(self):
            self._refresh_all_no_history()

        def _refresh_all_no_history(self):
            self.timeline.set_markers(self._markers, self._selected_marker_ids)
            self.timeline.set_selected_cut(self._selected_cut)
            self._refresh_marker_list()
            self._refresh_status()
            self._update_button_states()
            self._update_undo_redo_state()
            self._sync_timeline_zoom_slider()
            self._sync_timeline_navigation_slider()

        def _refresh_marker_list(self):
            self.cut_list.blockSignals(True)
            self.cut_list.clear()
            cuts = self._cuts()
            for idx, (s, e) in enumerate(cuts):
                item = QListWidgetItem(
                    f"{idx + 1:>2}.  {seconds_to_hmsf(s, self.fps)}  ➜  "
                    f"{seconds_to_hmsf(e, self.fps)}      "
                    f"({seconds_to_timecode(s)}  ➜  {seconds_to_timecode(e)})"
                )
                self.cut_list.addItem(item)
            if 0 <= self._selected_cut < self.cut_list.count():
                self.cut_list.setCurrentRow(self._selected_cut)
            self.cut_list.blockSignals(False)

        def _refresh_status(self):
            sel = self._selected_marker()
            in_time = sel.time if sel and sel.kind == "in" else None
            out_time = sel.time if sel and sel.kind == "out" else None
            now = self._current_time()
            self.status_strip.update_status(
                now, in_time, out_time, self._cuts(),
                self.timeline.state.view_span / max(0.001, self.duration),
            )

        def _update_button_states(self):
            has_marker_sel = bool(self._selected_marker_ids)
            has_cut_sel = self._selected_cut >= 0
            self.btn_delete_markers.setEnabled(has_marker_sel)
            self.btn_delete_selected_cut.setEnabled(has_cut_sel)
            self.btn_delete_all.setEnabled(bool(self._markers))
            self.btn_invert_cuts.setEnabled(bool(self._cuts()) and self.duration > 0)
            for button in (self.btn_delete_markers, self.btn_delete_selected_cut, self.btn_delete_all):
                self._apply_disabled_opacity(button)
            sel = self._selected_marker()
            if sel and sel.kind == "out":
                self.btn_mark_in.setText(" Convert → In (I)")
            else:
                self.btn_mark_in.setText(" Mark In (I)")
            if sel and sel.kind == "in":
                self.btn_mark_out.setText(" Convert → Out (O)")
            else:
                self.btn_mark_out.setText(" Mark Out (O)")

        def confirm(self):
            cuts = self._cuts()
            keep = invert_cuts_to_keep(cuts, self.duration) if cuts else [(0.0, self.duration)]
            self.result = {"status": "ok",
                           "keep_ranges": [[s, e] for s, e in keep],
                           # Empty keep + cuts_applied means "everything is cut"
                           # rather than "no cuts" (D13).
                           "cuts_applied": bool(cuts)}
            if hasattr(self, "player"):
                self.player.stop()
            self.close()

        def cancel(self):
            self.result = {"status": "canceled", "keep_ranges": []}
            if hasattr(self, "player"):
                self.player.stop()
            self.close()

        def closeEvent(self, event):
            try:
                QApplication.instance().removeEventFilter(self)
            except Exception:
                pass
            try:
                self.player.stop()
            except Exception:
                pass
            super().closeEvent(event)

    return CutEditorWindow(request)
