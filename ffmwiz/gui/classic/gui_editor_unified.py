"""The classic unified video editor window.

The builder below owns the window itself: its state, the preview-zoom and
timeline-view controls, the refresh pass and the confirm/cancel contract.
The bulk of the window is four sibling mixins, split off when this file
passed 2500 lines -- construction, playback, the edit model and input
routing. They are mixins rather than helpers because every method in them
is a `self` method on this window.
"""
from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.classic.gui_editor_unified_edits import UnifiedEditorEditMixin
from ffmwiz.gui.classic.gui_editor_unified_input import build_unified_editor_input_mixin
from ffmwiz.gui.classic.gui_editor_unified_layout import build_unified_editor_layout_mixin
from ffmwiz.gui.classic.gui_editor_unified_player import build_unified_editor_player_mixin


def build_join_segment_model(req: dict[str, Any]) -> list[dict[str, Any]]:
    """The join segments laid out on one timeline, per-segment audio included.

    `has_audio` used to be dropped while copying each segment dict, and the two
    consumers read it back with opposite wrong defaults: the waveform saw
    "False" for every segment and fell back to the input-1 flag, while its
    decode graph saw "True" and asked a silent segment for [i:a:0] -- which made
    FFmpeg refuse the whole filtergraph and lose the waveform for the join (R02).
    """
    segments: list[dict[str, Any]] = []
    offset = 0.0
    for idx, segment in enumerate(req.get("join_segments") or []):
        duration = max(0.001, float(segment.get("duration") or 0.001))
        path = Path(segment.get("path") or req.get("input_path") or "")
        # The segment is CARRIED and then overridden, never rebuilt from a list
        # of remembered field names. `has_audio` was lost that way once and
        # `picture_clock_offset` the next time -- the decoder then defaulted the
        # origin to zero and put a first-input impulse a whole second from where
        # the picture clock says it is (A04). Copying first means a field added
        # upstream reaches the consumers without this function being edited.
        model = dict(segment)
        model.update({
            "index": idx,
            "path": path,
            "name": str(segment.get("name") or path.name or f"Video {idx + 1}"),
            "start": offset,
            "end": offset + duration,
            "duration": duration,
            "label": f"Video {idx + 1}",
            "has_audio": bool(segment.get("has_audio", True)),
            "picture_clock_offset": float(segment.get("picture_clock_offset") or 0.0),
        })
        segments.append(model)
        offset += duration
    return segments


# Retain these public entry points while both engines use one decode contract.
from ffmwiz.gui.gui_waveform_decode import segment_audio_filter, build_wave_decode_args


def build_classic_waveform_args(
    req: dict[str, Any], segments: list[dict[str, Any]], out_path: Any
) -> list[str]:
    """Decode the actual Classic model, including per-segment stream choices."""
    request = dict(req, join_segments=segments)
    return build_wave_decode_args(request, str(out_path))


def request_has_any_audio(req: dict[str, Any]) -> bool:
    """True when ANY input of this request carries audio.

    Feature availability (the sync switch, the waveform) belongs to the whole
    join, not to input 1: the request-level flag alone disabled audio for a join
    whose first clip is silent.
    """
    return bool(req.get("has_audio")) or any(
        segment.get("has_audio") for segment in (req.get("join_segments") or []))


def active_segment_has_audio(req: dict[str, Any], segments: list[dict[str, Any]], index: int) -> bool:
    """Whether the segment the preview is playing has audio of its own."""
    if not segments:
        return bool(req.get("has_audio"))
    return bool(segments[max(0, min(len(segments) - 1, int(index)))].get("has_audio"))


def build_unified_video_editor(request: dict[str, Any]):
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    Qt = QtCore.Qt
    QMainWindow = QtWidgets.QMainWindow
    QSlider = QtWidgets.QSlider

    UnifiedPreviewCanvas, FrameExtractWorker = build_unified_preview_widgets()
    UnifiedTimelineWidget = build_unified_timeline_widget()

    LayoutMixin = build_unified_editor_layout_mixin(UnifiedPreviewCanvas, UnifiedTimelineWidget)
    PlayerMixin = build_unified_editor_player_mixin(
        FrameExtractWorker, active_segment_has_audio, _gui_atempo_chain, _ffmpeg_float)
    InputMixin = build_unified_editor_input_mixin()

    class UnifiedVideoEditorWindow(LayoutMixin, PlayerMixin, UnifiedEditorEditMixin,
                                   InputMixin, QMainWindow):
        def __init__(self, req):
            super().__init__()
            self.request = req
            self.join_segments: list[dict[str, object]] = build_join_segment_model(req)
            self.any_audio = request_has_any_audio(req)
            offset = sum(float(segment["duration"]) for segment in self.join_segments)
            self.duration = float(offset if self.join_segments else (req.get("duration") or 0.0))
            self.fps = float(req.get("fps") or 25.0)
            self.source_w = int(req.get("source_w") or 1920)
            self.source_h = int(req.get("source_h") or 1080)
            self.input_path = Path(req.get("input_path") or "")
            self._active_segment_index = 0
            self._pending_segment_position_ms = None
            self._pending_segment_play = False
            self._switching_segment = False
            self.chapters = normalize_chapters(req.get("chapters") or [], self.duration)
            self.result = {"status": "canceled"}
            self._icon = _icon_loader(self, self.style())
            self._wave_temp = tempfile.TemporaryDirectory(prefix="ffmwiz_unified_waveform_")
            self._wave_path = Path(self._wave_temp.name) / "waveform.pcm"
            self._wave_proc = None
            # FFmWiz hands back KEEP ranges (the segments to keep). Internally the
            # editor stores CUT (removed) ranges, so convert KEEP -> CUT (the
            # complement) here; otherwise reopening would store cuts inverted.
            _initial_keep = [
                (float(s), float(e)) for s, e in (req.get("initial_keep_ranges") or [])
                if float(e) > float(s)
            ]
            if _initial_keep:
                self._cut_ranges: list[tuple[float, float]] = [
                    (float(s), float(e))
                    for s, e in invert_cuts_to_keep(
                        normalize_ranges(_initial_keep, self.duration), self.duration
                    )
                ]
            else:
                self._cut_ranges = []
            self._separator_points: list[float] = sorted({
                round(float(v), 6) for v in (req.get("initial_separator_points") or [])
                if 0.0 < float(v) < self.duration
            })
            self._mark_in: float | None = None
            self._mark_out: float | None = None
            # Crop/speed/reverse/include-audio need their widgets, so they are
            # applied after the UI is built (see _apply_initial_session_state).
            # Reopening the editor restores the previous session's edits instead
            # of starting from zero.
            self._initial_margins = [int(v) for v in (req.get("initial_margins") or [0, 0, 0, 0])][:4]
            while len(self._initial_margins) < 4:
                self._initial_margins.append(0)
            self._initial_speed = float(req.get("initial_speed") or 1.0)
            self._initial_reverse = bool(req.get("initial_reverse"))
            self._initial_include_audio = bool(req.get("initial_include_audio", self.any_audio))
            self._history = HistoryStack(self._snapshot(), max_size=120)
            self._syncing_zoom = False
            self._syncing_view = False
            self._view_scroll_scale = 10000
            self._view_handle_width_px = None
            self._syncing_preview_zoom_control = False
            self._syncing_crop_controls = False
            self._zoom_presets = (10, 15, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 800, 1200, 1600, 3200)
            self._shortcuts = []
            self._restoring_snapshot = False
            self.player = None
            self.audio = None
            self.video_sink = None
            self._frame_worker = None
            self._pending_frame_request = None
            self._frame_request_started = 0.0
            self.setWindowTitle("FFmWiz Unified Video Editor")
            _apply_window_icon(self, self._icon)
            # PREVIEW: minimum wide enough for two 250px side columns plus the
            # 520px preview canvas (+ splitter handles/margins) so nothing clips.
            self.setMinimumSize(1120, 640)
            # Kick off the waveform decode NOW (async ffmpeg) so it runs in PARALLEL
            # with building the UI. By the time the window is shown the PCM is usually
            # ready, instead of the waveform appearing seconds after the window.
            # PERF: each build phase is timed so the dominant cold-start cost is
            # visible in the log (window-build vs deferred multimedia backend load).
            _phase_t = time.perf_counter()
            self._start_waveform()
            _gui_log_debug(f"unified _start_waveform in {time.perf_counter() - _phase_t:.3f}s", force=True)
            _phase_t = time.perf_counter()
            self._build_ui()
            _gui_log_debug(f"unified _build_ui in {time.perf_counter() - _phase_t:.3f}s", force=True)
            _phase_t = time.perf_counter()
            self._apply_initial_session_state()
            _gui_log_debug(f"unified _apply_initial_session_state in {time.perf_counter() - _phase_t:.3f}s", force=True)
            _phase_t = time.perf_counter()
            self._refresh_all()
            _gui_log_debug(f"unified _refresh_all in {time.perf_counter() - _phase_t:.3f}s", force=True)
            QtCore.QTimer.singleShot(120, self._setup_player)
            # Stream the (already-built) side-column panels in after the first paint so
            # the window appears fast instead of blocking on the column's layout/polish.
            QtCore.QTimer.singleShot(16, self._attach_next_panel)

        def _update_tool_buttons(self):
            tool = getattr(getattr(self, "preview", None), "_tool", "hand")
            for button, name in (
                (getattr(self, "btn_hand_tool", None), "hand"),
                (getattr(self, "btn_zoom_tool", None), "zoom"),
            ):
                if button is not None:
                    button.setProperty("active", "true" if tool == name else "false")
                    button.style().unpolish(button)
                    button.style().polish(button)

        def set_preview_tool(self, tool: str):
            if hasattr(self, "preview"):
                self.preview.set_tool(tool)
            self._update_tool_buttons()

        def _set_zoom_out_mode(self, enabled: bool):
            if hasattr(self, "preview"):
                self.preview.set_zoom_out_mode(bool(enabled))
            if hasattr(self, "btn_zoom_tool"):
                self.btn_zoom_tool.setIcon(self._icon("zoom_tool_out_orange" if enabled else "zoom_tool_orange", None))

        def toggle_crop_overlay(self):
            if not hasattr(self, "preview"):
                return
            visible = not bool(self.preview._show_crop_overlay)
            self.preview.set_crop_overlay_visible(visible)
            if hasattr(self, "btn_crop_overlay"):
                self.btn_crop_overlay.setText("Hide Crop Overlay (Ctrl+U)" if visible else "Show Crop Overlay (Ctrl+U)")

        def _preview_zoom_focus_source(self):
            return (self.preview.source_w / 2.0, self.preview.source_h / 2.0)

        def _sync_preview_zoom_combo(self, zoom: float | None = None):
            if not hasattr(self, "preview_zoom_combo"):
                return
            value = int(round((self.preview.zoom if zoom is None else float(zoom)) * 100.0))
            self._syncing_preview_zoom_control = True
            try:
                self.preview_zoom_combo.blockSignals(True)
                self.preview_zoom_combo.setCurrentText(f"{value}%")
                self.preview_zoom_combo.blockSignals(False)
            finally:
                self._syncing_preview_zoom_control = False

        def _apply_preview_zoom_text(self):
            if getattr(self, "_syncing_preview_zoom_control", False) or not hasattr(self, "preview_zoom_combo"):
                return
            text = self.preview_zoom_combo.currentText().strip()
            match = re.search(r"\d+(?:[.,]\d+)?", text)
            if not match:
                self._sync_preview_zoom_combo()
                return
            value = max(10.0, min(3200.0, float(match.group(0).replace(",", "."))))
            self.preview._zoom_centered(value / 100.0, self._preview_zoom_focus_source())
            self._sync_preview_zoom_combo()

        def preview_zoom_in(self):
            if hasattr(self, "preview"):
                self.preview._zoom_centered(self.preview.zoom * 1.25)

        def preview_zoom_out(self):
            if hasattr(self, "preview"):
                self.preview._zoom_centered(self.preview.zoom / 1.25)

        def reset_view(self):
            self.preview.reset_view()
            self.timeline.view_start = 0.0
            self.timeline.view_span = self.timeline.duration
            self._sync_timeline_controls()
            self.timeline.update()

        def reset_panels(self):
            if hasattr(self, "editor_splitter"):
                self.editor_splitter.setSizes([780, 210])

        def _on_zoom_slider(self, value):
            if self._syncing_zoom:
                return
            dur = max(0.001, float(self.timeline.duration))
            min_span = min(dur, 0.05)
            frac = max(0.0, min(1.0, float(value) / 100.0))
            span = dur * (min_span / dur) ** frac          # log: 0 -> full view, 100 -> deepest zoom
            self.timeline.set_zoom_ratio(dur / span, focus_time=self.timeline.playhead)
            self._sync_timeline_controls()

        def _on_view_scroll(self, value):
            if self._syncing_view:
                return
            max_start = max(0.0, float(self.timeline.duration) - float(self.timeline.view_span))
            scale = max(1.0, float(getattr(self, "_view_scroll_scale", 10000)))
            start = 0.0 if max_start <= 1e-9 else (float(value) / scale) * max_start
            self.timeline.set_view_start(start)

        def _nudge_view_scroll(self, direction: int):
            step = max(0.25, self.timeline.view_span * 0.08)
            self.timeline.scroll_view(float(direction) * step)
            self._sync_timeline_controls()

        def _nudge_timeline_zoom(self, direction: int):
            value = max(0, min(100, self.zoom_slider.value() + int(direction) * 4))
            self.zoom_slider.setValue(value)

        def _sync_timeline_controls(self):
            if not hasattr(self, "zoom_slider"):
                return
            dur = max(0.001, float(self.timeline.duration))
            min_span = min(dur, 0.05)
            span = max(min_span, min(dur, float(self.timeline.view_span)))
            denom = math.log(min_span / dur) if dur > min_span else -1.0
            frac = (math.log(span / dur) / denom) if denom else 0.0
            value = int(round(max(0.0, min(1.0, frac)) * 100.0))
            self._syncing_zoom = True
            self.zoom_slider.setValue(max(0, min(100, value)))
            self._syncing_zoom = False
            if hasattr(self, "zoom_value_label"):
                self.zoom_value_label.setText(f"{max(0, min(100, value))}%")
            max_start_seconds = max(0.0, float(self.timeline.duration) - float(self.timeline.view_span))
            scale = max(1, int(getattr(self, "_view_scroll_scale", 10000)))
            span_ratio = max(0.001, min(1.0, float(self.timeline.view_span) / max(0.001, float(self.timeline.duration))))
            self._syncing_view = True
            self.view_scroll.setRange(0, 0 if max_start_seconds <= 1e-9 else scale)
            page = max(1, int(round(scale * span_ratio)))
            self.view_scroll.setPageStep(page)
            self.view_scroll.setSingleStep(max(1, page // 10))
            value = 0 if max_start_seconds <= 1e-9 else int(round((float(self.timeline.view_start) / max_start_seconds) * scale))
            self.view_scroll.setValue(max(0, min(self.view_scroll.maximum(), value)))
            self._syncing_view = False
            self._sync_timeline_view_handle()

        def _sync_timeline_view_handle(self):
            if not hasattr(self, "view_scroll"):
                return
            ratio = max(0.02, min(1.0, float(self.timeline.view_span) / max(0.001, float(self.timeline.duration))))
            track_width = max(40, self.view_scroll.width() - 24)
            width = int(max(34, min(track_width, track_width * ratio)))
            if getattr(self, "_view_handle_width_px", None) == width:
                return
            self._view_handle_width_px = width
            # Re-applying a stylesheet forces a full style recompute; during a zoom
            # gesture the handle width changes every step, so debounce it — apply
            # once motion settles. (The thumb size just lags a frame; cheap & invisible.)
            timer = getattr(self, "_view_handle_timer", None)
            if timer is None:
                timer = QtCore.QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(self._apply_view_handle_style)
                self._view_handle_timer = timer
            timer.start(90)

        def _apply_view_handle_style(self):
            if not hasattr(self, "view_scroll"):
                return
            width = int(getattr(self, "_view_handle_width_px", 34) or 34)
            self.view_scroll.setStyleSheet(
                "QSlider#timelineViewSlider::groove:horizontal {"
                "background: #18212b; height: 5px; border-radius: 3px;"
                "}"
                "QSlider#timelineViewSlider::sub-page:horizontal {"
                "background: #18212b; border-radius: 3px;"
                "}"
                "QSlider#timelineViewSlider::handle:horizontal {"
                f"background: #8cff9d; width: {width}px; height: 12px; margin: -4px 0; "
                "border-radius: 6px; border: 1px solid #2ea043;"
                "}"
                "QSlider#timelineViewSlider::handle:horizontal:hover { background: #c7ffd0; }"
            )

        def _refresh_all(self):
            self._cut_ranges = normalize_ranges(self._cut_ranges, self.duration)
            self._separator_points = sorted(
                set(
                    round(float(value), 6)
                    for value in self._separator_points
                    if 1e-6 < float(value) < self.duration - 1e-6
                )
            )
            self.timeline.set_cut_ranges(self._cut_ranges)
            self.timeline.set_separator_points(self._separator_points)
            self.timeline.set_marks(self._mark_in, self._mark_out)
            self.timeline.selected_cut = min(self.timeline.selected_cut, len(self._cut_ranges) - 1)
            self.timeline.selected_separator = min(self.timeline.selected_separator, len(self._separator_points) - 1)
            has_cuts = bool(self._cut_ranges)
            self.btn_delete_cut.setEnabled(0 <= self.timeline.selected_cut < len(self._cut_ranges))
            self.btn_delete_separator.setEnabled(0 <= self.timeline.selected_separator < len(self._separator_points))
            self.btn_delete_markers.setEnabled(self.timeline.selected_marker in {"in", "out"})
            self.btn_delete_all.setEnabled(has_cuts)
            self.btn_invert.setEnabled(has_cuts)
            self._sync_marker_convert_button()
            self._sync_crop_fields()
            top, left, right, bottom = self.preview.margins
            crop_w = max(1, self.source_w - left - right)
            crop_h = max(1, self.source_h - top - bottom)
            in_text = seconds_to_timecode(self._mark_in) if self._mark_in is not None else "--"
            out_text = seconds_to_timecode(self._mark_out) if self._mark_out is not None else "--"
            summary = (
                f"Crop {crop_w}x{crop_h}  |  Mark In {in_text}  |  Mark Out {out_text}  |  "
                f"Cuts {len(self._cut_ranges)}  |  Splits {len(self._separator_points)}  |  Videos {max(1, len(self.join_segments))}  |  Chapters {len(self.chapters)}  |  Speed {self._speed():g}x  |  "
                f"Reverse {'yes' if self.reverse_box.isChecked() else 'no'}"
            )
            # Tidy, grouped multi-line version for the Speed panel (avoids the
            # tangled single-line wrap in the narrow column).
            panel_summary = (
                f"Crop&nbsp; <b>{crop_w}×{crop_h}</b><br>"
                f"In {in_text} &nbsp;·&nbsp; Out {out_text}<br>"
                f"Cuts {len(self._cut_ranges)} &nbsp;·&nbsp; Splits {len(self._separator_points)} "
                f"&nbsp;·&nbsp; Speed {self._speed():g}× &nbsp;·&nbsp; "
                f"{'Reversed' if self.reverse_box.isChecked() else 'Forward'}"
            )
            self.summary_label.setText(panel_summary)
            self.status.setText(summary)
            parts: list[str] = []
            if self._cut_ranges:
                shown = "   ".join(
                    f"#{idx + 1} {seconds_to_timecode(s)}->{seconds_to_timecode(e)}"
                    for idx, (s, e) in enumerate(self._cut_ranges[:5])
                )
                more = f"   +{len(self._cut_ranges) - 5} more" if len(self._cut_ranges) > 5 else ""
                parts.append("Cut ranges: " + shown + more)
            if self._separator_points:
                shown = "   ".join(
                    f"#{idx + 1} {seconds_to_timecode(value)}"
                    for idx, value in enumerate(self._separator_points[:8])
                )
                more = f"   +{len(self._separator_points) - 8} more" if len(self._separator_points) > 8 else ""
                parts.append("Splits: " + shown + more)
            if parts:
                self.cut_list_label.setText("   |   ".join(parts))
            else:
                self.cut_list_label.setText("Cut ranges and Splits: none. Mark In/Out and press Add Cut(s), or press S at the CTI to split the final output into parts.")
            self._update_undo_redo()
            self._sync_timeline_controls()
            self.timeline.update()

        def _start_waveform(self):
            # A join whose FIRST input is silent still has audio to draw: the
            # global has_audio flag is derived from input 0 alone (D07).
            if not self.any_audio:
                if hasattr(self, "status"):
                    self.status.setText("No audio stream is available for waveform preview.")
                return
            args = build_classic_waveform_args(self.request, self.join_segments, self._wave_path)
            self._wave_proc = QtCore.QProcess(self)
            self._wave_proc.finished.connect(self._waveform_finished)
            self._wave_proc.start(str(self.request.get("ffmpeg") or "ffmpeg"), args)

        def _waveform_finished(self, *_args):
            if not self._wave_path.exists() or self._wave_path.stat().st_size == 0:
                self.status.setText("Waveform preview could not be generated.")
                return
            try:
                data = self._wave_path.read_bytes()
                if not data:
                    return
                # Hand the raw mono PCM to the timeline; it renders a crisp,
                # detailed per-pixel waveform from it at any zoom.
                self.timeline.set_pcm(data, 4000)
            except Exception as exc:  # noqa: BLE001
                self.status.setText(f"Waveform preview could not be generated ({exc}).")

        def resizeEvent(self, event):
            super().resizeEvent(event)
            # Guard: a resize can fire from the early empty-shell show() before
            # _build_ui has created self.timeline.
            if hasattr(self, "timeline"):
                self.timeline.update()

        def confirm(self):
            self._snap_crop_to_even()
            margins = self._normalize_crop_margins_even([int(v) for v in self.preview.margins])
            cuts = normalize_ranges(self._cut_ranges, self.duration)
            keep_ranges = [[float(s), float(e)] for s, e in (invert_cuts_to_keep(cuts, self.duration) if cuts else [])]
            speed = float(self._speed())
            reverse = bool(self.reverse_box.isChecked())
            include_audio = bool(self.include_audio_box.isChecked())
            self.result = {
                "status": "ok",
                "margins": margins,
                "keep_ranges": keep_ranges,
                # Tells the caller that an EMPTY keep list means "every frame is
                # cut", not "nothing was cut" (D13).
                "cuts_applied": bool(cuts),
                "separator_points": [float(v) for v in self._separator_points],
                "speed": speed,
                "reverse": reverse,
                "include_audio": include_audio,
            }
            self.close()

        def cancel(self):
            self.result = {"status": "canceled"}
            self.close()

        def closeEvent(self, event):
            self._closing = True
            try:
                self.player.stop()
            except Exception:
                pass
            # Stop the frame-extract QThread first — destroying a running QThread on
            # teardown crashes Qt (0xC0000409).
            try:
                self._stop_preview_frame_worker()
            except Exception:
                pass
            for _attr in ("_rev_proc", "_wave_proc"):
                try:
                    proc = getattr(self, _attr, None)
                    if proc is not None and proc.state() != QtCore.QProcess.NotRunning:
                        proc.kill()
                        proc.waitForFinished(1000)
                except Exception:
                    pass
            try:
                self._wave_temp.cleanup()
            except Exception:
                pass
            super().closeEvent(event)

    return UnifiedVideoEditorWindow(request)
