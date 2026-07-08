"""FFmWiz services cluster (extracted from FFmWiz.py, method الف).

Self-contained over ffmwiz.core.*, ffmwiz.support.*, and appio. Patched members
are referenced by callers as services.<name> so mock.patch keeps working.
"""
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
import concurrent.futures
import queue
import threading
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
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz import runtime  # noqa: F401


def get_video_fps(answers: dict[str, Any], default: float = 25.0) -> float:
    """Detect the input video FPS. Prefers avg_frame_rate, falls back to r_frame_rate."""
    streams = answers.get("video_streams") or []
    if not streams:
        return default
    stream = streams[0]
    for key in ("avg_frame_rate", "r_frame_rate"):
        rate = rational_to_float(stream.get(key))
        if rate and rate > 0:
            return rate
    return default


def join_source_audio_sample_rate(answers: dict[str, Any]) -> int | None:
    """Highest source audio sample rate (Hz) across all joined inputs, so the
    unified join rate does not downsample the best source."""
    rates: list[int] = []
    items = join_ordered_items_for_answers(answers) or []
    if items:
        for item in items:
            for stream in item.get("audio_streams") or []:
                rate = stream_sample_rate(stream)
                if rate:
                    rates.append(rate)
    if not rates:
        rate = source_audio_sample_rate(answers)
        if rate:
            rates.append(rate)
    return max(rates) if rates else None


def join_target_sample_rate(answers: dict[str, Any]) -> int:
    """The single sample rate (Hz) every joined input is resampled to, so the
    joined output has a uniform rate. Prefers an explicit user choice, then the
    highest source rate among inputs, then 48000 Hz."""
    rate = resolve_audio_sample_rate(answers)
    if rate:
        return rate
    return join_source_audio_sample_rate(answers) or 48000


def source_extra_preservation_features(answers: dict[str, Any]) -> list[str]:
    features: list[str] = []
    if source_metadata_tags_present(answers):
        features.append("container/stream metadata")
    if source_chapter_streams(answers):
        features.append("chapters")
    if answers.get("subtitle_streams"):
        features.append("subtitle streams")
    if embedded_attachment_streams(answers):
        features.append("embedded font/attachment streams")
    if source_data_streams(answers):
        features.append("data streams")
    if additional_source_video_streams(answers):
        features.append("additional video streams")
    return features


_CAPABILITY_SESSION_MEMO: dict[str, dict[str, Any]] = {}


def capability_environment_identity(ffmpeg: str, ffprobe: str, *, include_gpu: bool) -> dict[str, Any]:
    """Build a normalized environment-identity dict. GPU/driver are included only
    for NVENC probes so CPU-encoder entries do not depend on GPU identity."""
    identity: dict[str, Any] = {
        "os": platform.system(),
        "arch": platform.machine(),
    }
    try:
        exe = shutil.which(ffmpeg) or ffmpeg
        p = Path(exe)
        identity["ffmpeg_path"] = str(p.resolve()) if p.exists() else str(exe)
        if p.exists():
            st = p.stat()
            identity["ffmpeg_size"] = st.st_size
            identity["ffmpeg_mtime"] = int(st.st_mtime)
    except Exception:
        identity["ffmpeg_path"] = str(ffmpeg)
    try:
        r = _capability_run([ffmpeg, "-hide_banner", "-version"], timeout=15)
        lines = r.stdout.splitlines()
        identity["ffmpeg_version_line"] = lines[0].strip() if lines else ""
        identity["ffmpeg_build_hash"] = hashlib.sha256(
            r.stdout.encode("utf-8", "replace")).hexdigest()[:16]
    except Exception:
        identity["ffmpeg_version_line"] = ""
        identity["ffmpeg_build_hash"] = ""
    try:
        r = _capability_run([ffprobe, "-hide_banner", "-version"], timeout=15)
        lines = r.stdout.splitlines()
        identity["ffprobe_version_line"] = lines[0].strip() if lines else ""
    except Exception:
        identity["ffprobe_version_line"] = ""
    if include_gpu:
        gpu, driver = detect_nvidia_gpu_identity()
        identity["gpu"] = gpu
        identity["nvidia_driver"] = driver
    return identity


def capability_environment_key(ffmpeg: str, ffprobe: str, encoder: str) -> tuple[dict[str, Any], str]:
    """Return (identity, stable_hash). NVENC encoders bind to GPU/driver too."""
    is_nvenc = str(encoder).lower().endswith("_nvenc")
    identity = capability_environment_identity(ffmpeg, ffprobe, include_gpu=is_nvenc)
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
    return identity, digest


def probe_color_range_capability(ffmpeg: str, ffprobe: str, encoder: str, ext: Any,
                                  *, tmpdir: str | None = None) -> dict[str, Any]:
    """Encode a tiny synthetic clip (lavfi testsrc2) omitting -color_range and
    inspect the final color_range with ffprobe. Never raises; returns a result
    dict with a 'status' of verified / unsupported / probe_failed."""
    family = container_family(ext)
    safe_ext = str(ext or "mp4").strip().lower().lstrip(".") or "mp4"
    created = False
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp(prefix="ffmwiz_cap_")
        created = True
    out = Path(tmpdir) / ("cap_%s_%s.%s" % (encoder, family, safe_ext))
    extra = ["-preset", "p4"] if str(encoder).lower().endswith("_nvenc") else []
    cmd = [ffmpeg, "-y", "-hide_banner", "-v", "error", "-f", "lavfi",
           "-i", "testsrc2=size=320x240:rate=24:duration=1", "-frames:v", "12",
           "-an", "-vf", "format=yuv420p", "-c:v", str(encoder)] + extra + [str(out)]
    result: dict[str, Any] = {
        "status": "unknown", "expected_final_range": None,
        "probe_method": "real encode + ffprobe", "encoder": str(encoder),
        "container_family": family,
        "sample_command_hash": hashlib.sha256(" ".join(cmd).encode("utf-8", "replace")).hexdigest()[:16],
        "ffprobe_result": None, "verified_at_utc": _utc_now_text(), "error": None,
    }
    try:
        r = _capability_run(cmd, timeout=90)
        if r.returncode != 0:
            result["status"] = "unsupported" if str(encoder).lower().endswith("_nvenc") else "probe_failed"
            result["error"] = (r.stderr or "").strip()[-240:]
            return result
        pr = _capability_run([ffprobe, "-v", "error", "-select_streams", "v:0",
                              "-show_entries", "stream=color_range", "-of", "json", str(out)], timeout=30)
        cr = "unknown"
        try:
            cr = json.loads(pr.stdout)["streams"][0].get("color_range") or "unknown"
        except Exception:
            cr = "unknown"
        result["status"] = "verified"
        result["expected_final_range"] = cr
        result["ffprobe_result"] = cr
        return result
    except Exception as exc:
        result["status"] = "probe_failed"
        result["error"] = str(exc)
        return result
    finally:
        try:
            if out.exists():
                out.unlink()
        except OSError:
            pass
        if created:
            shutil.rmtree(tmpdir, ignore_errors=True)


def resolve_capability(answers: dict[str, Any], *, allow_probe: bool = True,
                       force_reprobe: bool = False) -> dict[str, Any]:
    """Resolve the 'do not force' final-range capability for the answers' encoder
    and container, using the per-environment cache and lazy probing. Returns a
    dict describing capability_source, status, expected_final_range, and the
    environment fingerprint. Never raises; never blocks encoding."""
    encoder = str(resolve_video_encoder(answers)[0]).lower()
    ext = str(answers.get("output_ext", "")).lower()
    family = container_family(ext)
    ffmpeg = answers.get("ffmpeg") or "ffmpeg"
    ffprobe = answers.get("ffprobe") or "ffprobe"
    identity, env_key = capability_environment_key(ffmpeg, ffprobe, encoder)
    cap_key = "%s|%s" % (encoder, family)
    memo_key = "%s::%s" % (env_key, cap_key)
    result = {
        "encoder": encoder, "container_family": family, "env_key": env_key,
        "env_short": env_key[:12], "capability_source": "unavailable",
        "status": "unknown", "expected_final_range": None, "verified_at_utc": None,
    }
    if encoder == "copy":
        result["capability_source"] = "n/a (stream copy)"
        return result
    if not force_reprobe and memo_key in _CAPABILITY_SESSION_MEMO:
        return dict(_CAPABILITY_SESSION_MEMO[memo_key])

    cache = load_capability_cache()
    entry = (cache["environments"].get(env_key, {})
             .get("capabilities", {}).get(CAPABILITY_GROUP, {}).get(cap_key)
             if not force_reprobe else None)
    if entry:
        # A cached entry from THIS environment only.
        result.update(
            capability_source="verified cache" if entry.get("status") == "verified" else "cache",
            status=entry.get("status"),
            expected_final_range=entry.get("expected_final_range"),
            verified_at_utc=entry.get("verified_at_utc"),
        )
        if entry.get("status") in {"verified", "unsupported", "probe_failed"}:
            _CAPABILITY_SESSION_MEMO[memo_key] = dict(result)
            return dict(result)

    if not allow_probe:
        _CAPABILITY_SESSION_MEMO[memo_key] = dict(result)
        return dict(result)

    appio.note("Checking encoder/container range-signaling behavior...")
    probe = probe_color_range_capability(ffmpeg, ffprobe, encoder, ext)
    _store_capability_entry(cache, env_key, identity, cap_key, probe)
    save_capability_cache(cache)
    result.update(
        capability_source="fresh probe", status=probe["status"],
        expected_final_range=probe.get("expected_final_range"),
        verified_at_utc=probe.get("verified_at_utc"),
    )
    if probe["status"] == "verified":
        appio.note("Capability verified and cached.")
    else:
        appio.note("Capability probe unavailable; final encoder signaling will be treated "
             "as unknown until verified.")
    _CAPABILITY_SESSION_MEMO[memo_key] = dict(result)
    return dict(result)


def ffprobe_json(ffprobe: str, input_path: Path) -> dict[str, Any]:
    args = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        str(input_path),
    ]
    stdout_text = ""
    stderr_text = ""
    decoded_using = "not decoded"
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout_text, stdout_encoding = decode_subprocess_bytes(result.stdout, "utf-8-sig")
        stderr_text, stderr_encoding = decode_subprocess_bytes(result.stderr, "utf-8")
        decoded_using = f"stdout={stdout_encoding}; stderr={stderr_encoding}"
        if result.returncode != 0:
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe could not read the file. See log file: {_log_file_text()}")
        if not stdout_text.strip():
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe returned no JSON output. See log file: {_log_file_text()}")
        try:
            payload = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using, exc,
            )
            raise FFprobeError(f"ffprobe returned invalid JSON. See log file: {_log_file_text()}") from exc
        if not isinstance(payload, dict):
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe returned unexpected JSON. See log file: {_log_file_text()}")
        log_debug(
            f"ffprobe JSON decoded successfully for {input_path}; "
            f"stdout length={len(stdout_text)} stderr length={len(stderr_text)}")
        if os.environ.get("FFMWIZ_DEBUG"):
            try:
                safe_name = sanitize_output_stem(input_path.name)
                stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
                json_path = _logs_dir() / f"ffprobe_{stamp}_{safe_name}.json"
                json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                log_info(f"Full ffprobe JSON saved to: {json_path}")
            except Exception as exc:
                log_warn(f"Could not save ffprobe JSON debug file: {exc}")
        if stderr_text.strip():
            log_debug("ffprobe stderr:\n" + stderr_text.rstrip())
        return payload
    except FFprobeError:
        raise
    except Exception as exc:
        log_ffprobe_diagnostics(
            input_path, ffprobe, args, "not available",
            stdout_text, stderr_text, decoded_using, exc,
        )
        raise FFprobeError(f"ffprobe could not read the file. See log file: {_log_file_text()}") from exc


def probe_packet_sizes(ffprobe: str, input_path: Path) -> dict[int, int]:
    args = [
        ffprobe,
        "-v",
        "error",
        "-show_packets",
        "-show_entries",
        "packet=stream_index,size",
        "-of",
        "csv=p=0",
        str(input_path),
    ]
    sizes: dict[int, int] = {}
    try:
        process = subprocess.Popen(
            args,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, OSError) as exc:
        # ffprobe is unavailable on this machine; degrade gracefully to "no
        # packet sizes" instead of crashing. Callers treat {} as unknown size.
        appio.note(f"Could not run ffprobe for exact stream sizes ({exc}); treating sizes as unknown.")
        log_warn(f"probe_packet_sizes: ffprobe unavailable: {exc}")
        return {}
    assert process.stdout is not None
    for line in process.stdout:
        numbers = re.findall(r"\d+", line)
        if len(numbers) < 2:
            continue
        stream_index = int(numbers[0])
        packet_size = int(numbers[1])
        sizes[stream_index] = sizes.get(stream_index, 0) + packet_size
    stderr = process.stderr.read() if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        appio.note(f"Could not calculate exact stream sizes with ffprobe packets: {stderr.strip()}")
        return {}
    return sizes


def _run_loudnorm_analysis(
    args: list[str],
    total_duration: float | None,
    context: str,
) -> dict[str, float] | None:
    """Run a prepared loudnorm analysis FFmpeg command (already containing
    -progress pipe:1 and -f null output) and parse the final JSON object.

    Shared by single-input and Join (multi-input) measurement so both parse
    identically and report progress identically. Returns the measured-stats
    dict, or None on failure / parse error."""
    log_info("LoudNorm measurement command: " + command_to_powershell(args))
    log_command("LoudNorm measurement", args)
    runtime._begin_progress_render()
    started_at = time.perf_counter()
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    state: dict[str, str] = {}
    log_info(
        "LoudNorm measurement uses null output; size/bitrate progress fields "
        "are omitted unless FFmpeg reports real output values."
    )
    last_render = ""
    progress_events = 0
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        def _capture_stderr() -> None:
            try:
                for line in process.stderr:  # type: ignore[union-attr]
                    stripped = line.rstrip()
                    if stripped:
                        stderr_lines.append(stripped)
                        log_debug(f"LoudNorm measurement stderr: {stripped}")
            except Exception:
                pass

        stderr_thread = threading.Thread(target=_capture_stderr, daemon=True)
        stderr_thread.start()
        initial_render = paint("LoudNorm measurement: analyzing audio loudness...", Color.GRAY) if USE_COLOR else "LoudNorm measurement: analyzing audio loudness..."
        runtime._write_progress_line(initial_render)
        last_render = initial_render
        for line in process.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if line:
                stdout_lines.append(line)
            if "=" not in line:
                if line:
                    log_debug(f"LoudNorm measurement stdout: {line}")
                continue
            key, _, value = line.partition("=")
            state[key.strip()] = value.strip()
            if key.strip() != "progress":
                continue
            log_debug(f"LoudNorm measurement stdout progress: {_compact_ffmpeg_progress_state(state)}")
            progress_events += 1
            current_s = _progress_raw_seconds_from_state(state)
            previous_s = float(state.get("_ffmwiz_current_s", "0") or 0.0)
            state["_ffmwiz_current_s"] = str(max(previous_s, current_s))
            rendered = _render_progress_line(state, total_duration, started_at)
            runtime._write_progress_line(rendered)
            last_render = rendered
            if value.strip() == "end":
                runtime._finish_progress_line(rendered)
                last_render = ""
        process.wait()
        stderr_thread.join()
        if last_render:
            runtime._finish_progress_line(last_render)
        combined = "\n".join(stderr_lines + stdout_lines)
        stats = parse_loudnorm_measurement_output(combined)
        log_info(
            "LoudNorm measurement finished: "
            f"{context}; returncode={process.returncode}; "
            f"elapsed={time.perf_counter() - started_at:.3f}s; "
            f"progress_events={progress_events}; stats={stats or '{}'}"
        )
        if process.returncode != 0 or stats is None:
            log_error("LoudNorm measurement output:\n" + _text_preview(combined, 4000))
        return stats
    except Exception:
        log_exception(f"LoudNorm measurement failed: {context}")
        return None


def probe_loudnorm_measurement(
    ffmpeg: str,
    input_path: Path,
    audio_index: int,
    target_i: float = LOUDNORM_DEFAULT_TARGET_I,
    total_duration: float | None = None,
) -> dict[str, float] | None:
    """Single-input two-pass measurement: analyze one input audio track."""
    args = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-stats_period",
        "0.5",
        "-progress",
        "pipe:1",
        "-i",
        str(input_path),
        "-map",
        f"0:a:{int(audio_index)}",
        "-vn",
        "-sn",
        "-dn",
        "-af",
        loudnorm_analysis_filter(target_i),
        "-f",
        "null",
        os.devnull,
    ]
    return _run_loudnorm_analysis(args, total_duration, f"input={input_path}; audio_index={audio_index}")


def build_join_loudnorm_analysis_args(
    answers: dict[str, Any],
    items: list[dict[str, Any]],
    audio_index: int,
    target_i: float = LOUDNORM_DEFAULT_TARGET_I,
) -> list[str]:
    """Build the Pass-1 measurement command for the FINAL joined audio.

    This reconstructs the exact audio assembly the final Join encode produces
    (same per-input preparation, same cut trims, same concat order, same
    speed/reverse), then applies loudnorm=...:print_format=json and maps ONLY
    the analysis audio to a null output (no video encode, no media file). The
    measurement therefore reflects every selected input's audio, not just the
    first input."""
    ffmpeg = str(answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg")
    args: list[str] = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-stats_period",
        "0.5",
        "-progress",
        "pipe:1",
    ]
    for item in items:
        args.extend(["-i", str(item["path"])])
    filters: list[str] = []
    concat_inputs: list[str] = []
    prep = join_audio_prep_filter(join_target_sample_rate(answers))
    for input_idx, _item in enumerate(items):
        filters.append(f"[{input_idx}:a:{int(audio_index)}]{prep}[mja{input_idx}]")
        concat_inputs.append(f"[mja{input_idx}]")
    filters.append(f"{''.join(concat_inputs)}concat=n={len(items)}:v=0:a=1[mjcat]")
    # Same cut trims as the final encode (across the joined timeline).
    source_join_duration = sum(float(item.get("duration") or 0.0) for item in items)
    keep_ranges = normalize_cut_ranges(list(answers.get("cut_keep_ranges") or []), source_join_duration)
    label = append_join_trim_concat_filter(filters, "mjcat", keep_ranges, "audio", "mjcut")
    # Same speed/reverse preparation as the final encode (before loudnorm).
    speed_parts = audio_speed_reverse_filter_parts(answers)
    if speed_parts:
        filters.append(f"[{label}]{','.join(speed_parts)},asetpts=PTS-STARTPTS[mjspeed]")
        label = "mjspeed"
    filters.append(f"[{label}]{loudnorm_analysis_filter(target_i)}[mjanalysis]")
    args.extend([
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[mjanalysis]",
        "-vn",
        "-sn",
        "-dn",
        "-f",
        "null",
        os.devnull,
    ])
    return args


def probe_join_loudnorm_measurement(
    answers: dict[str, Any],
    items: list[dict[str, Any]],
    audio_index: int,
    target_i: float = LOUDNORM_DEFAULT_TARGET_I,
    total_duration: float | None = None,
) -> dict[str, float] | None:
    """Two-pass measurement over the complete joined audio (all inputs)."""
    args = build_join_loudnorm_analysis_args(answers, items, audio_index, target_i)
    return _run_loudnorm_analysis(
        args,
        total_duration,
        f"join_inputs={len(items)}; audio_index={audio_index}",
    )


def join_ordered_items_for_answers(answers: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ordered Join input set [primary, *join_input_items].

    This mirrors exactly the items list the final Join encode builds (the
    primary input is index 0), so the two-pass measurement analyzes the same
    inputs in the same order as the encoded output."""
    join_extra = list(answers.get("join_input_items") or [])
    if not join_extra:
        return []
    primary = {
        "path": answers["input_path"],
        "probe": answers.get("probe") or {},
        "format": answers.get("format") or {},
        "streams": (
            list(answers.get("video_streams") or [])
            + list(answers.get("audio_streams") or [])
            + list(answers.get("subtitle_streams") or [])
            + list(answers.get("attachment_streams") or [])
            + list(answers.get("data_streams") or [])
        ),
        "video_streams": answers.get("video_streams") or [],
        "audio_streams": answers.get("audio_streams") or [],
        "data_streams": answers.get("data_streams") or [],
        "duration": stream_duration_seconds({}, answers.get("format")) or 0.0,
        "audio_volume_stats": answers.get("audio_volume_stats"),
    }
    return [primary, *join_extra]


def probe_audio_volume_stats(ffmpeg: str, input_path: Path, audio_index: int) -> dict[str, str]:
    args = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        str(input_path),
        "-map",
        f"0:a:{int(audio_index)}",
        "-vn",
        "-sn",
        "-dn",
        "-af",
        "volumedetect",
        "-f",
        "null",
        os.devnull,
    ]
    started_at = time.perf_counter()
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout_text, stdout_encoding = decode_subprocess_bytes(result.stdout, "utf-8")
        stderr_text, stderr_encoding = decode_subprocess_bytes(result.stderr, "utf-8")
        combined = stderr_text + "\n" + stdout_text
        stats = parse_volumedetect_output(combined)
        log_debug(
            "Audio volume scan: "
            f"input={input_path}; audio_index={audio_index}; returncode={result.returncode}; "
            f"elapsed={time.perf_counter() - started_at:.3f}s; "
            f"decoded=stdout:{stdout_encoding},stderr:{stderr_encoding}; stats={stats or '{}'}"
        )
        if result.returncode != 0 and not stats:
            log_debug("Audio volume scan stderr:\n" + stderr_text.strip())
        return stats
    except Exception:
        log_exception(f"Audio volume scan failed: input={input_path}; audio_index={audio_index}")
        return {}


def get_audio_volume_stats(answers: dict[str, Any]) -> dict[int, dict[str, str]]:
    if "audio_volume_stats" in answers:
        return answers["audio_volume_stats"]
    ffmpeg = answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg"
    input_path = answers.get("input_path")
    streams = list(answers.get("audio_streams") or [])
    stats: dict[int, dict[str, str]] = {}
    if input_path and streams:
        worker_count = min(VOLUME_SCAN_WORKERS, len(streams))
        log_debug(
            f"Audio volume scan batch: input={input_path}; streams={len(streams)}; workers={worker_count}"
        )
        if worker_count > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {
                    executor.submit(probe_audio_volume_stats, str(ffmpeg), Path(input_path), idx): idx
                    for idx, _stream in enumerate(streams)
                }
                for future in concurrent.futures.as_completed(future_map):
                    idx = future_map[future]
                    try:
                        stats[idx] = future.result()
                    except Exception:
                        log_exception(f"Audio volume scan worker failed: input={input_path}; audio_index={idx}")
                        stats[idx] = {}
        else:
            for idx, _stream in enumerate(streams):
                stats[idx] = probe_audio_volume_stats(str(ffmpeg), Path(input_path), idx)
    answers["audio_volume_stats"] = stats
    return stats


def get_packet_sizes(answers: dict[str, Any]) -> dict[int, int]:
    if "packet_sizes" not in answers:
        if packet_size_probe_allowed(answers):
            log_debug(f"Running exact packet-size probe for {answers.get('input_path')}")
            answers["packet_sizes"] = probe_packet_sizes(answers["ffprobe"], answers["input_path"])
        else:
            reason = "stream size metadata is sufficient"
            log_debug(f"Skipping exact packet-size probe for {answers.get('input_path')}: {reason}")
            answers["packet_sizes"] = {}
    return answers["packet_sizes"]


__all__ = [
    'build_join_loudnorm_analysis_args',
    'capability_environment_identity',
    'capability_environment_key',
    'ffprobe_json',
    'get_audio_volume_stats',
    'get_packet_sizes',
    'get_video_fps',
    'join_ordered_items_for_answers',
    'join_source_audio_sample_rate',
    'join_target_sample_rate',
    'probe_audio_volume_stats',
    'probe_color_range_capability',
    'probe_join_loudnorm_measurement',
    'probe_loudnorm_measurement',
    'probe_packet_sizes',
    'resolve_capability',
    'source_extra_preservation_features',
    '_run_loudnorm_analysis',
    '_CAPABILITY_SESSION_MEMO',
]


# services_b holds an overflow slice of this module (split for file size).
from ffmwiz import services_b as _services_b  # noqa: E402
from ffmwiz.services_b import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_services_b.__all__)
