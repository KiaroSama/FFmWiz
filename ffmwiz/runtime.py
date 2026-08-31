"""FFmWiz runtime cluster (extracted from FFmWiz.py, method الف).

Self-contained over ffmwiz.core.*, ffmwiz.support.*, and appio. Patched members
are referenced by callers as runtime.<name> so mock.patch keeps working.
"""
from __future__ import annotations

import os
import sys
import re
import math
import json
import time
import shutil
import signal
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
import concurrent.futures
import queue
import threading
from collections import deque
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
from ffmwiz.appio import *  # noqa: F401,F403
from ffmwiz import appio  # noqa: F401

# The PySide6 availability/install subsystem lives in a sibling: it owns its
# own module-level cache, so it moved whole rather than leaving a `global`
# split across two files.
from ffmwiz.runtime_pyside6 import *  # noqa: E402,F401,F403
from ffmwiz import runtime_pyside6 as _runtime_pyside6  # noqa: E402
from ffmwiz import runtime_pyside6  # noqa: E402,F401




# ponytail: single-renderer module state, deliberately not thread-safe. Every
# run_ffmpeg_with_progress / _begin_progress_render call site is on the main
# thread, so one progress run exists at a time. If a parallel/batch mode is ever
# added, this state belongs on a renderer object created per run -- two
# concurrent runs would otherwise interleave and steal each other's final line.
_VT_MODE_ATTEMPTED = False
_PROGRESS_FINALIZED = False
_WINDOWS_CONSOLE_CHECKED = False
_WINDOWS_CONSOLE_OK = False


def _enable_windows_vt_mode() -> None:
    global _VT_MODE_ATTEMPTED
    if _VT_MODE_ATTEMPTED or os.name != "nt":
        return
    _VT_MODE_ATTEMPTED = True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if handle and kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception as exc:
        log_debug(f"Could not enable Windows VT console mode: {exc}")


def _stdout_supports_in_place_progress() -> bool:
    try:
        if not sys.stdout.isatty():
            return False
    except Exception:
        return False
    if os.name != "nt":
        return True
    global _WINDOWS_CONSOLE_CHECKED, _WINDOWS_CONSOLE_OK
    if _WINDOWS_CONSOLE_CHECKED:
        return _WINDOWS_CONSOLE_OK
    _WINDOWS_CONSOLE_CHECKED = True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        _WINDOWS_CONSOLE_OK = bool(handle and kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except Exception as exc:
        log_debug(f"Windows console progress support check failed: {exc}")
        _WINDOWS_CONSOLE_OK = False
    return _WINDOWS_CONSOLE_OK


def _begin_progress_render() -> None:
    """Reset the per-run progress finalize guard. Call once before a run's first
    progress line so a fresh final line can be committed for this run."""
    global _PROGRESS_FINALIZED
    _PROGRESS_FINALIZED = False


def _write_progress_line(rendered: str) -> None:
    # The run's final line was already committed; ignore late repaints so the
    # completed 100% line is not duplicated by post-end queue-drain ticks.
    if _PROGRESS_FINALIZED:
        return
    if not _stdout_supports_in_place_progress():
        return
    _enable_windows_vt_mode()
    width = _progress_terminal_width()
    # Clamp the status to a SINGLE terminal row so it never wraps. Line wrapping
    # is what made the in-place redraw walk up too far and eat earlier lines
    # (e.g. the "Final PowerShell command:" line). One column of margin avoids
    # the deferred-wrap edge case on some terminals. With no wrapping, a plain
    # carriage-return + clear-line is always correct.
    rendered = _truncate_ansi_visible(rendered, max(1, width - 1))
    sys.stdout.write("\r\033[2K" + rendered)
    sys.stdout.flush()


def _finish_progress_line(rendered: str | None) -> None:
    global _PROGRESS_FINALIZED
    # Only the first finalize for a run commits the final line; subsequent
    # finalize calls (post-loop fallback, late ticks) are ignored.
    if _PROGRESS_FINALIZED:
        return
    _PROGRESS_FINALIZED = True
    if rendered:
        if not _stdout_supports_in_place_progress():
            sys.stdout.write(rendered + "\n")
            sys.stdout.flush()
            return
        _enable_windows_vt_mode()
        width = _progress_terminal_width()
        rendered = _truncate_ansi_visible(rendered, max(1, width - 1))
        sys.stdout.write("\r\033[2K" + rendered + "\n")
    else:
        sys.stdout.write("")
    sys.stdout.flush()


def _render_initial_progress_line(label: str, detail: str, started_at: float) -> str:
    elapsed = time.perf_counter() - started_at
    segments = [
        (label, PROGRESS_COLORS["percent"]),
        (detail, Color.GRAY),
        (f"elapsed {format_progress_elapsed_dot(elapsed)}", PROGRESS_COLORS["elapsed"]),
    ]
    return _join_progress_segments(segments, USE_COLOR)


def _signal_graceful_stop(process, label: str, own_process_group: bool) -> bool:
    """Ask the child to stop the way it would on a console Ctrl+C.

    Windows `terminate()` is TerminateProcess: the child dies instantly and
    FFmpeg never runs av_write_trailer, so the half-written output has no index
    and most players will not open it. CTRL_BREAK_EVENT reaches a child started
    in its own process group and lets FFmpeg finalise the container first.

    `own_process_group` is NOT optional on Windows: CTRL_BREAK_EVENT is
    delivered to a whole process GROUP, so sending it to a child that shares our
    console group takes down FFmWiz itself -- and, when a suite is running, the
    test runner with it (observed: exit 130 mid-suite). Only the caller knows
    whether it passed CREATE_NEW_PROCESS_GROUP, so it has to say so.

    Returns True when a stop signal was actually delivered.
    """
    try:
        if sys.platform == "win32":
            if not own_process_group:
                return False
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            # POSIX SIGINT goes to the one process, so it is always safe here.
            process.send_signal(signal.SIGINT)
        return True
    except (OSError, ValueError, AttributeError):
        return False


def _kill_process_tree(process, label: str) -> None:
    """Kill the child AND anything it spawned.

    `process.kill()` reaps ffmpeg.exe alone; a helper it started keeps the
    output file locked and the pipes open.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True, timeout=10.0, check=False,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def reap_subprocess(process, threads=(), *, wait_timeout: float = 60.0,
                    join_timeout: float = 5.0, label: str = "process",
                    cancelled: bool = False,
                    own_process_group: bool = False) -> None:
    """Reap a child process deterministically: bounded wait, bounded reader-thread
    join, then close the PIPE handles.

    Every `Popen(..., stdout=PIPE, stderr=PIPE)` in FFmWiz must end here. Leaving
    the pipes to the garbage collector is what produced `ResourceWarning: unclosed
    file`, and an unbounded `wait()`/`join()` would let one wedged child hang the
    whole wizard with no way out. The pipes are closed only AFTER the reader
    threads are joined, so a reader never races a closed handle.

    `cancelled=True` means the user pressed Ctrl+C: skip straight to the stop
    ladder instead of waiting out `wait_timeout`. Without it a Ctrl+C during a
    long encode left the wizard apparently frozen for a full minute while this
    function politely waited for a process the user had already abandoned.

    The stop ladder is graceful-first so a cancelled encode still leaves a
    playable file: signal -> bounded wait -> terminate -> bounded wait -> kill
    the whole process tree.
    """
    if process is None:
        return
    if not cancelled:
        try:
            process.wait(timeout=wait_timeout)
        except subprocess.TimeoutExpired:
            log_warn(f"{label} did not exit within {wait_timeout:.0f}s; stopping it.")
        except OSError:
            pass

    if process.poll() is None:
        if cancelled:
            log_info(f"{label} cancelled by the user; asking it to finalise the output.")
        if _signal_graceful_stop(process, label, own_process_group):
            try:
                process.wait(timeout=GRACEFUL_STOP_TIMEOUT)
            except (subprocess.TimeoutExpired, OSError):
                pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=GRACEFUL_STOP_TIMEOUT)
            except (subprocess.TimeoutExpired, OSError):
                pass
        if process.poll() is None:
            log_warn(f"{label} ignored the stop request; killing its process tree.")
            _kill_process_tree(process, label)
            try:
                process.wait(timeout=GRACEFUL_STOP_TIMEOUT)
            except (subprocess.TimeoutExpired, OSError):
                pass

    for thread in threads:
        if thread is not None and thread.is_alive():
            thread.join(timeout=join_timeout)
    for pipe in (process.stdout, process.stderr, process.stdin):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


def run_ffmpeg_with_progress(
    cmd: list[str],
    total_duration: float | None = None,
    label: str = "FFmpeg",
    *,
    split_progress_fps: float | None = None,
    split_progress_part_durations: list[float] | None = None,
    initial_detail: str | None = None,
    progress_output_paths: list[Path] | None = None,
) -> tuple[int, float]:
    """Run an FFmpeg command and render an in-place progress line.

    - Injects -nostats -progress pipe:1 -loglevel warning.
    - stdout is parsed as key=value progress.
    - stderr is captured into the log file (not the console) so warnings
      and errors are preserved without flooding the terminal.
    - When FFmpeg signals 'progress=end', the final line is committed
      with a newline so subsequent output starts cleanly.

    Returns (returncode, elapsed_seconds).
    """
    progress_cmd = _inject_progress_args(cmd)
    # Record the difference, not the whole command again: the progress variant
    # is the same argv plus a couple of reporting flags.
    injected = [part for part in progress_cmd if part not in cmd]
    log_command(label, cmd, injected)

    _begin_progress_render()
    started_at = time.perf_counter()
    state: dict[str, str] = {}
    target_mux_bitrate_kbps = _progress_target_mux_bitrate_kbps_from_command(cmd)
    if target_mux_bitrate_kbps:
        state["_ffmwiz_target_bitrate_kbps"] = f"{target_mux_bitrate_kbps:.6f}"
        log_info(f"{label} progress target mux bitrate estimate: {target_mux_bitrate_kbps:.1f} kbits/s")
    last_render = ""
    # Only the last few lines are ever read (the failure tail below), so an
    # unbounded list just grows for the length of the encode. A chatty filter
    # on a long job can emit tens of thousands of lines.
    stderr_lines: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
    final_emitted = False
    progress_events = 0
    output_paths = [Path(path) for path in (progress_output_paths or [])]
    if not output_paths:
        output_paths = _progress_output_paths_from_command(cmd)
    if output_paths:
        log_info(f"{label} progress output size paths: {[str(path) for path in output_paths]}")
    split_part_durations: list[float] = []
    for value in split_progress_part_durations or []:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            split_part_durations.append(duration)
    split_previous_output_sizes = [0 for _ in output_paths]
    split_active_part = 0
    split_active_part_start_raw = 0.0
    split_previous_raw_s: float | None = None
    cancelled = False

    try:
        process = subprocess.Popen(
            progress_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            # Its own group so a cancel can send CTRL_BREAK_EVENT to FFmpeg
            # alone -- letting it write the container trailer -- instead of
            # TerminateProcess, which truncates the output. Without the flag the
            # event would also hit the wizard's own console.
            **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
               if sys.platform == "win32" else {}),
        )
    except Exception as exc:
        log_exception(f"Failed to start {label}")
        appio.error(f"Failed to start {label}: {exc}")
        return 1, 0.0

    stdout_queue: queue.Queue[str] = queue.Queue()

    def _capture_stdout() -> None:
        try:
            for raw_line in process.stdout:  # type: ignore[union-attr]
                stdout_queue.put(raw_line)
        except Exception:
            pass

    # Capture stderr into the log in a worker so the main thread can
    # render progress without blocking on stderr drain.
    def _capture_stderr() -> None:
        # FFmpeg repeats the same warning once per stream, per pass, sometimes
        # per packet -- one real log had "Could not find codec parameters for
        # stream 1" a dozen times verbatim. Logging each copy buries the lines
        # that differ, so collapse a run of identical lines into one entry with
        # a count.
        previous = None
        repeats = 0

        def flush_repeats() -> None:
            if repeats:
                log_debug(f"{label} stderr: (previous line repeated {repeats}x)")

        try:
            for line in process.stderr:  # type: ignore[union-attr]
                stripped = line.rstrip()
                if not stripped:
                    continue
                stderr_lines.append(stripped)
                if stripped == previous:
                    repeats += 1
                    continue
                flush_repeats()
                repeats = 0
                previous = stripped
                log_debug(f"{label} stderr: {stripped}")
            flush_repeats()
        except Exception:
            pass

    stdout_thread = threading.Thread(target=_capture_stdout, daemon=True)
    stdout_thread.start()
    stderr_thread = threading.Thread(target=_capture_stderr, daemon=True)
    stderr_thread.start()
    initial_detail = initial_detail or "starting process / initializing filters / decoding first frames"
    if split_part_durations:
        initial_detail = f"Part 1/{len(split_part_durations)} — " + (initial_detail or "starting process / initializing filters / decoding first frames")
        state["_ffmwiz_split_part_label"] = f"Part 1/{len(split_part_durations)}"
    initial_render = _render_initial_progress_line(label, initial_detail, started_at)
    _write_progress_line(initial_render)
    last_render = initial_render

    try:
        while process.poll() is None or not stdout_queue.empty() or stdout_thread.is_alive():
            try:
                line = stdout_queue.get(timeout=0.25)
            except queue.Empty:
                if progress_events == 0 and split_part_durations and output_paths and total_duration and total_duration > 0:
                    # Multi-output FFmpeg may write Part 1 entirely before
                    # emitting any progress events. Estimate Part 1 progress
                    # from its output file size growth.
                    try:
                        part1_size = output_paths[0].stat().st_size if output_paths[0].exists() else 0
                    except OSError:
                        part1_size = 0
                    if part1_size > 0:
                        # Part 1 is being written. Estimate progress using the
                        # target bitrate or a linear assumption within Part 1.
                        part1_duration = split_part_durations[0]
                        target_kbps = float(state.get("_ffmwiz_target_bitrate_kbps", "0") or 0)
                        if target_kbps > 0:
                            estimated_total_bytes = target_kbps * 1000.0 / 8.0 * part1_duration
                            part1_pct = min(1.0, part1_size / max(1, estimated_total_bytes))
                        else:
                            # Without a bitrate target, assume linear write.
                            part1_pct = min(0.95, part1_size / max(1, part1_size + 1024 * 1024))
                        estimated_s = part1_pct * part1_duration
                        state["_ffmwiz_current_s"] = str(estimated_s)
                        state["_ffmwiz_prefer_elapsed_speed"] = "1"
                        state["_ffmwiz_split_part_label"] = f"Part 1/{len(split_part_durations)}"
                        elapsed_now = max(0.001, time.perf_counter() - started_at)
                        if estimated_s > 0.5:
                            state["_ffmwiz_speed_text"] = f"{estimated_s / elapsed_now:.3g}x"
                        state["_ffmwiz_size_text"] = (
                            _human_size(part1_size)
                            .replace("KiB", "KB").replace("MiB", "MB")
                            .replace("GiB", "GB").replace("TiB", "TB")
                        )
                        if estimated_s > 0.5:
                            state["_ffmwiz_bitrate_text"] = f"{part1_size * 8.0 / 1000.0 / estimated_s:.1f}kbits/s"
                        rendered = _render_progress_line(state, total_duration, started_at, max_width=max(1, _progress_terminal_width() - 1))
                        _write_progress_line(rendered)
                        last_render = rendered
                    else:
                        last_render = _render_initial_progress_line(label, initial_detail, started_at)
                        _write_progress_line(last_render)
                elif progress_events == 0:
                    last_render = _render_initial_progress_line(label, initial_detail, started_at)
                    _write_progress_line(last_render)
                elif state and last_render:
                    # Between real FFmpeg progress ticks, keep the last rendered
                    # line as-is instead of re-rendering. Re-rendering here updated
                    # the wall-clock ETA and the on-disk size/bitrate while the
                    # FFmpeg-derived percent/time stayed frozen, so those fields
                    # refreshed faster than the rest. Holding the line makes EVERY
                    # field (percent, bitrate, size, ETA, elapsed) refresh together
                    # on the next real tick.
                    _write_progress_line(last_render)
                continue
            line = line.strip()
            if "=" not in line:
                if line:
                    log_debug(f"{label} stdout: {line}")
                continue
            key, _, value = line.partition("=")
            state[key.strip()] = value.strip()
            if key.strip() != "progress":
                continue
            log_debug(f"{label} stdout progress: {_compact_ffmpeg_progress_state(state)}")
            progress_events += 1
            raw_current_s = _progress_raw_seconds_from_state(state)
            current_s = raw_current_s
            if split_progress_fps and split_progress_fps > 0:
                frame_text = str(state.get("frame", "") or "").strip()
                try:
                    frame_seconds = max(0.0, float(frame_text) / float(split_progress_fps))
                except (TypeError, ValueError):
                    frame_seconds = 0.0
                output_sizes: list[int] = []
                if output_paths:
                    for output_path in output_paths:
                        try:
                            output_sizes.append(output_path.stat().st_size)
                        except OSError:
                            output_sizes.append(0)
                # recon_s is the media position from FFmpeg's real out_time/frame
                # reconstruction, BEFORE the byte-based override below. It is the
                # only clock that is independent of how many bytes have flushed to
                # disk, so it is used as the bitrate denominator (a true average
                # bitrate) instead of the byte-derived current_s (which would pin
                # the bitrate to the target).
                recon_s = current_s
                if split_part_durations:
                    current_s, split_active_part, split_active_part_start_raw = _split_progress_seconds(
                        raw_current_s,
                        frame_seconds,
                        split_part_durations,
                        output_sizes,
                        split_previous_output_sizes,
                        split_active_part,
                        split_previous_raw_s,
                        split_active_part_start_raw,
                    )
                    split_previous_output_sizes = output_sizes
                    split_previous_raw_s = raw_current_s
                    recon_s = current_s
                    # ROBUST AGGREGATE PROGRESS: FFmpeg's multi-output -progress
                    # counters are unreliable for Split (frozen `frame`, frozen
                    # `total_size`, and `out_time` that only covers one output),
                    # which is far worse when Split is combined with cut
                    # trim/concat. When a target bitrate is known, derive
                    # monotonic, bitrate-accurate progress from the TOTAL bytes
                    # written across all parts on disk vs the expected total
                    # bytes (target bitrate x program duration). This does not
                    # depend on FFmpeg's ambiguous counters at all.
                    try:
                        _target_kbps = float(state.get("_ffmwiz_target_bitrate_kbps", "0") or 0.0)
                    except (TypeError, ValueError):
                        _target_kbps = 0.0
                    _total_out_bytes = sum(output_sizes)
                    if _target_kbps > 0 and _total_out_bytes > 0 and total_duration and total_duration > 0:
                        _expected_bps = _target_kbps * 1000.0 / 8.0
                        if _expected_bps > 0:
                            _fs_current_s = _total_out_bytes / _expected_bps
                            # Hold just below 100% until FFmpeg signals end so a
                            # bitrate overshoot cannot park the bar at 100% while
                            # encoding is still running.
                            if state.get("progress") != "end":
                                _fs_current_s = min(_fs_current_s, float(total_duration) * 0.99)
                            current_s = max(current_s, _fs_current_s)
                    # Label the active part from the aggregate position so the
                    # "Part X/Y" label matches the displayed percent (the
                    # out_time reconstruction's active index can lag or stick).
                    _cum = 0.0
                    _disp_part = 0
                    for _i, _d in enumerate(split_part_durations):
                        _disp_part = _i
                        if current_s < _cum + _d - 1e-6:
                            break
                        _cum += _d
                    state["_ffmwiz_split_part_label"] = (
                        f"Part {_disp_part + 1}/{len(split_part_durations)}"
                    )
                    log_debug(
                        f"{label} split progress: raw_out_time={raw_current_s:.2f}s "
                        f"frame_s={frame_seconds:.2f}s sizes={output_sizes} "
                        f"target_kbps={_target_kbps:.1f} current_s={current_s:.2f}s "
                        f"recon_active={split_active_part} disp_part={_disp_part}"
                    )
                elif frame_seconds > 0.0:
                    # Fallback for callers that only provide FPS. This avoids
                    # double-counting but cannot infer later Split parts.
                    current_s = max(raw_current_s, frame_seconds)
                    recon_s = current_s
                if current_s > 0.0:
                    if total_duration and total_duration > 0:
                        current_s = min(current_s, float(total_duration))
                    state["_ffmwiz_prefer_elapsed_speed"] = "1"
                    elapsed_now = max(0.001, time.perf_counter() - started_at)
                    if current_s > 0.001:
                        aggregate_speed = current_s / elapsed_now
                        state["_ffmwiz_speed_text"] = f"{aggregate_speed:.3g}x"
                        total_size_bytes = sum(output_sizes)
                        total_size_text = str(state.get("total_size", "") or "").strip()
                        if total_size_bytes > 0:
                            state["_ffmwiz_size_text"] = (
                                _human_size(total_size_bytes)
                                .replace("KiB", "KB")
                                .replace("MiB", "MB")
                                .replace("GiB", "GB")
                                .replace("TiB", "TB")
                            )
                            # Bitrate from the REAL reconstructed media time, not
                            # the byte-derived current_s (which would be pinned to
                            # the target and look frozen). While recon_s advances
                            # (the active part), this is a true running average.
                            # When recon_s stalls (later parts, where FFmpeg's
                            # out_time/frame freeze), hold the last real value
                            # rather than recomputing against a frozen clock. At
                            # the very end the full program duration is known, so
                            # show the exact overall average bitrate.
                            try:
                                prev_recon = float(state.get("_ffmwiz_split_recon_s", "0") or 0.0)
                            except (TypeError, ValueError):
                                prev_recon = 0.0
                            if state.get("progress") == "end" and total_duration and total_duration > 0:
                                bitrate_kbps = total_size_bytes * 8.0 / 1000.0 / float(total_duration)
                                state["_ffmwiz_bitrate_text"] = f"{bitrate_kbps:.1f}kbits/s"
                            elif recon_s > 0.05 and recon_s > prev_recon + 0.05:
                                bitrate_kbps = total_size_bytes * 8.0 / 1000.0 / recon_s
                                state["_ffmwiz_bitrate_text"] = f"{bitrate_kbps:.1f}kbits/s"
                                state["_ffmwiz_split_recon_s"] = f"{recon_s:.6f}"
                            elif not state.get("_ffmwiz_bitrate_text"):
                                bitrate_kbps = total_size_bytes * 8.0 / 1000.0 / current_s
                                state["_ffmwiz_bitrate_text"] = f"{bitrate_kbps:.1f}kbits/s"
                        elif total_size_text.isdigit():
                            bitrate_kbps = int(total_size_text) * 8.0 / 1000.0 / current_s
                            state["_ffmwiz_bitrate_text"] = f"{bitrate_kbps:.1f}kbits/s"
            previous_s = float(state.get("_ffmwiz_current_s", "0") or 0.0)
            state["_ffmwiz_current_s"] = str(max(previous_s, current_s))
            _apply_output_file_size_progress(state, output_paths, max(previous_s, current_s))
            rendered = _render_progress_line(state, total_duration, started_at, max_width=max(1, _progress_terminal_width() - 1))
            _write_progress_line(rendered)
            last_render = rendered
            if os.environ.get("FFMWIZ_DEBUG_PROGRESS"):
                log_debug(f"{label} progress event #{progress_events}: {_strip_ansi(rendered)}")
            if value.strip() == "end":
                _finish_progress_line(rendered)
                final_emitted = True
                # FFmpeg's progress=end is terminal; stop the render loop so
                # post-end queue-drain ticks cannot repaint a second 100% line.
                break
    except KeyboardInterrupt:
        # `except Exception` does NOT catch this, so Ctrl+C used to fall through
        # to the bare finally below and sit in reap_subprocess's 60 s wait -- the
        # wizard looked frozen for a full minute after the user cancelled.
        # Reaping with cancelled=True skips the wait and goes straight to the
        # graceful stop ladder, so FFmpeg still gets to write its trailer.
        cancelled = True
        _finish_progress_line(last_render or None)
        appio.note(f"{label} cancelled; stopping FFmpeg and finalising the output.")
        raise
    except Exception:
        log_exception(f"{label} progress reader crashed")
    finally:
        reap_subprocess(process, (stdout_thread, stderr_thread), label=label,
                        cancelled=cancelled,
                        own_process_group=(sys.platform == "win32"))

    elapsed = time.perf_counter() - started_at

    if not final_emitted:
        _finish_progress_line(last_render or None)
    log_debug(f"{label} progress parser events: {progress_events}")
    log_info(f"{label} raw FFmpeg progress/stderr captured in the main log; no stdout/stderr sidecar files were created.")

    if process.returncode != 0:
        log_error(f"{label} exited with code {process.returncode}")
        tail = "\n".join(list(stderr_lines)[-STDERR_TAIL_REPORT_LINES:])
        if tail:
            log_error(f"{label} stderr tail:\n{tail}")
        # Ask appio for the live path. `_LOG_PATH` is a private module global of
        # appio that `import *` never exports, so the bare name raised NameError
        # here and every FFmpeg failure crashed instead of reporting itself.
        log_file = appio.log_path()
        if log_file is not None:
            appio.error(f"{label} failed. Full FFmpeg output is in: {log_file}")
        else:
            appio.error(f"{label} failed.")
    else:
        log_info(f"{label} completed successfully in {format_elapsed(elapsed)}")

    return process.returncode, elapsed


__all__ = [
    'reap_subprocess',
    'run_ffmpeg_with_progress',
    '_begin_progress_render',
    '_enable_windows_vt_mode',
    '_finish_progress_line',
    '_render_initial_progress_line',
    '_stdout_supports_in_place_progress',
    '_write_progress_line',
]


# Progress-line rendering helpers live in a sibling module (split for file size).
from ffmwiz import runtime_render as _runtime_render  # noqa: E402
from ffmwiz.runtime_render import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_runtime_render.__all__)
__all__ = list(__all__) + list(_runtime_pyside6.__all__)
