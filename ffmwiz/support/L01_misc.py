"""FFmWiz helpers (dependency level 1) — concerns: misc(36).

Extracted verbatim from FFmWiz.py; imports ffmwiz.core.* and lower levels.
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


def timeline_is_modified(answers: dict[str, Any]) -> bool:
    """Return True when the output timeline differs from the source timeline.

    Timeline-modifying operations include multi-range cuts, speed changes,
    video reversal, split into multiple parts, and join of multiple inputs.
    A single-range cut (trim) also modifies the timeline because chapter
    timestamps must be offset to begin at zero.
    """
    if answers.get("cut_keep_ranges"):
        return True
    if video_speed_transform_enabled(answers):
        return True
    if answers.get("reverse_video"):
        return True
    if answers.get("separator_points"):
        return True
    if answers.get("join_input_items"):
        return True
    return False


def source_data_keep_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("keep_source_data_streams", source_metadata_keep_enabled(answers)))


def embedded_attachment_keep_enabled(answers: dict[str, Any]) -> bool:
    return bool(
        answers.get("keep_embedded_attachments")
        and embedded_attachment_streams(answers)
        and output_supports_embedded_attachments(answers)
    )


def parse_speed_factor(value: str) -> float:
    text = str(value or "").strip().lower()
    if text.endswith("%"):
        return clamp_speed_factor(float(text[:-1].strip()) / 100.0)
    if text.endswith("x"):
        text = text[:-1].strip()
    return clamp_speed_factor(text)


def encode_video_speed_factor(answers: dict[str, Any]) -> float:
    return clamp_speed_factor(answers.get("video_speed_factor", answers.get("speed_factor", DEFAULT_SPEED_FACTOR)))


def _ffmwiz_gui_path() -> Path:
    return script_dir() / "assets" / FFMWIZ_RUNTIME_DIR_NAME / FFMWIZ_GUI_FILE_NAME


def _requirements_path() -> Path:
    return script_dir() / REQUIREMENTS_FILE_NAME


def _config_setting_for_logging(key: str, fallback: Any) -> Any:
    try:
        path = script_dir() / CONFIG_FILE_NAME
        if not path.exists():
            return fallback
        data = parse_env_config(path.read_text(encoding="utf-8-sig"))
        settings = data.get("settings", {}) if isinstance(data, dict) else {}
        if isinstance(settings, dict) and key in settings:
            return settings.get(key)
    except Exception:
        return fallback
    return fallback


def _qml_gui_path() -> Path:
    return script_dir() / "assets" / FFMWIZ_RUNTIME_DIR_NAME / "ffmwiz_gui_qml.py"


def _visible_len(text: str) -> int:
    return len(_strip_ansi(text))


def _progress_seconds_from_state(state: dict[str, str]) -> float:
    value = state.get("_ffmwiz_current_s")
    if value:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            pass
    return _progress_raw_seconds_from_state(state)


def _progress_target_mux_bitrate_kbps_from_command(cmd: list[str]) -> float | None:
    """Estimate the intended mux bitrate from the first video/audio -b options."""
    video_kbps: float | None = None
    audio_kbps: float | None = None
    for index, token in enumerate(cmd[:-1]):
        option = str(token or "").strip().lower()
        if not option.startswith("-b:"):
            continue
        value = _parse_ffmpeg_bitrate_kbps(cmd[index + 1])
        if value is None:
            continue
        if option.startswith("-b:v") and video_kbps is None:
            video_kbps = value
        elif option.startswith("-b:a") and audio_kbps is None:
            audio_kbps = value
    values = [value for value in (video_kbps, audio_kbps) if value is not None]
    return sum(values) if values else None


def _apply_output_file_size_progress(
    state: dict[str, str],
    output_paths: list[Path],
    current_s: float,
) -> bool:
    """Refresh progress size/bitrate from output files when FFmpeg total_size lags."""
    if not output_paths:
        return False
    total_size_bytes = 0
    for output_path in output_paths:
        try:
            if output_path.exists():
                total_size_bytes += max(0, output_path.stat().st_size)
        except OSError:
            continue
    if total_size_bytes <= 0:
        return False
    previous_size_text = state.get("_ffmwiz_size_text")
    last_size = 0
    last_size_s = 0.0
    previous_size = 0
    previous_size_s = 0.0
    try:
        last_size = int(float(state.get("_ffmwiz_last_file_size_bytes", "0") or 0))
        last_size_s = float(state.get("_ffmwiz_last_file_size_seconds", "0") or 0.0)
        previous_size = int(float(state.get("_ffmwiz_previous_file_size_bytes", "0") or 0))
        previous_size_s = float(state.get("_ffmwiz_previous_file_size_seconds", "0") or 0.0)
    except (TypeError, ValueError):
        last_size = 0
        last_size_s = 0.0
        previous_size = 0
        previous_size_s = 0.0

    if total_size_bytes > last_size:
        if last_size > 0:
            state["_ffmwiz_previous_file_size_bytes"] = str(last_size)
            state["_ffmwiz_previous_file_size_seconds"] = f"{last_size_s:.6f}"
            previous_size = last_size
            previous_size_s = last_size_s
        state["_ffmwiz_last_file_size_bytes"] = str(total_size_bytes)
        state["_ffmwiz_last_file_size_seconds"] = f"{max(0.0, current_s):.6f}"
        last_size = total_size_bytes
        last_size_s = max(0.0, current_s)

    display_size_bytes = total_size_bytes
    size_source = "file"
    # Single-output mp4 +faststart defers flushing, so between flushes we
    # extrapolate the size from the recent write rate (or the target bitrate)
    # to keep it moving. For a Split (multiple output parts) this extrapolation
    # is HARMFUL: the per-tick progress is byte-derived, so extrapolating at the
    # target bitrate makes the displayed size/bitrate fake and frozen at the
    # target. For Split we therefore always show the REAL summed on-disk bytes.
    multi_output_split = len(output_paths) > 1
    if (
        not multi_output_split
        and state.get("progress") != "end"
        and last_size > 0
        and current_s > last_size_s + 0.01
        and total_size_bytes <= last_size
    ):
        rate_bytes_per_s: float | None = None
        if previous_size > 0 and last_size > previous_size and last_size_s > previous_size_s + 0.01:
            rate_bytes_per_s = (last_size - previous_size) / (last_size_s - previous_size_s)
        if rate_bytes_per_s is None:
            try:
                target_kbps = float(state.get("_ffmwiz_target_bitrate_kbps", "") or 0.0)
            except (TypeError, ValueError):
                target_kbps = 0.0
            if target_kbps > 0:
                rate_bytes_per_s = target_kbps * 1000.0 / 8.0
        if rate_bytes_per_s and rate_bytes_per_s > 0:
            extrapolated_seconds = min(4.0, max(0.0, current_s - last_size_s))
            estimated = int(last_size + rate_bytes_per_s * extrapolated_seconds)
            if estimated > display_size_bytes:
                display_size_bytes = estimated
                size_source = "estimated-file"
    size_text = (
        _human_size(display_size_bytes)
        .replace("KiB", "KB")
        .replace("MiB", "MB")
        .replace("GiB", "GB")
        .replace("TiB", "TB")
    )
    changed = size_text != previous_size_text
    state["_ffmwiz_size_text"] = size_text
    state["_ffmwiz_size_source"] = size_source
    # For a Split (multiple output parts) the displayed bitrate is owned by the
    # split branch, which derives it from the REAL reconstructed media time so
    # it reflects the true average output bitrate. Recomputing it here against
    # the byte-derived current_s would re-pin it to the target and make it jump
    # between flushes, so only the (real, on-disk) size is refreshed for splits.
    if current_s > 0.001 and not multi_output_split:
        bitrate_kbps = display_size_bytes * 8.0 / 1000.0 / current_s
        bitrate_text = f"{bitrate_kbps:.1f}kbits/s"
        changed = changed or bitrate_text != state.get("_ffmwiz_bitrate_text")
        state["_ffmwiz_bitrate_text"] = bitrate_text
    return changed


def detect_nvidia_gpu_identity() -> tuple[str, str]:
    """Return (gpu_name, driver_version) via nvidia-smi, or ('unknown','unknown')."""
    try:
        r = _capability_run(["nvidia-smi", "--query-gpu=name,driver_version",
                             "--format=csv,noheader"], timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            parts = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
            name = parts[0] if parts else "unknown"
            driver = parts[1] if len(parts) > 1 else "unknown"
            return name or "unknown", driver or "unknown"
    except Exception:
        pass
    return "unknown", "unknown"


def _store_capability_entry(cache: dict[str, Any], env_key: str, identity: dict[str, Any],
                            cap_key: str, probe: dict[str, Any]) -> None:
    env = cache["environments"].setdefault(env_key, {})
    env.setdefault("created_at_utc", _utc_now_text())
    env["last_verified_at_utc"] = _utc_now_text()
    env["ffmpeg_identity"] = {
        "path": identity.get("ffmpeg_path", ""),
        "version": identity.get("ffmpeg_version_line", ""),
        "build_hash": identity.get("ffmpeg_build_hash", ""),
    }
    env["hardware_identity"] = {
        "gpu": identity.get("gpu", "n/a"),
        "driver": identity.get("nvidia_driver", "n/a"),
    }
    caps = env.setdefault("capabilities", {}).setdefault(CAPABILITY_GROUP, {})
    caps[cap_key] = {
        "status": probe["status"],
        "expected_final_range": probe.get("expected_final_range"),
        "probe_method": probe.get("probe_method"),
        "verified_at_utc": probe.get("verified_at_utc"),
        "encoder": probe.get("encoder"),
        "container_family": probe.get("container_family"),
        "sample_command_hash": probe.get("sample_command_hash"),
        "ffprobe_result": probe.get("ffprobe_result"),
        "error": probe.get("error"),
    }


def _as_int_value(value: Any) -> int | None:
    text = _plain_number_text(value)
    if not text or not re.fullmatch(r"[+-]?\d+(?:\.0+)?", text):
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _as_float_value(value: Any) -> float | None:
    text = _plain_number_text(value)
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def media_info_video_fps(stream: dict[str, Any]) -> float | None:
    return rational_to_float(stream.get("avg_frame_rate")) or rational_to_float(stream.get("r_frame_rate"))


def media_info_attachment_kind(stream: dict[str, Any]) -> str:
    tags = media_info_stream_tags(stream)
    filename = str(tags.get("filename") or tags.get("FileName") or "").lower()
    mimetype = str(tags.get("mimetype") or tags.get("MIME_TYPE") or "").lower()
    if any(filename.endswith(ext) for ext in (".ttf", ".otf", ".ttc")) or "font" in mimetype:
        return "font attachment"
    if any(token in mimetype for token in ("image/", "jpeg", "png")) or "cover" in filename:
        return "cover art attachment"
    return "attachment"


def render_info_report_html(lines: list[tuple[str, str]], input_path: Path, plain_report: str, raw_json: str, text_overview: str) -> str:
    rendered_lines: list[str] = []
    for index, (text, _color_code) in enumerate(lines):
        if text:
            rendered_lines.append(
                f'<div class="line" style="color: {media_info_html_color(index)}">{html.escape(text)}</div>'
            )
        else:
            rendered_lines.append('<div class="line blank">&nbsp;</div>')
    palette_preview = "\n".join(
        f'<span style="background:{media_info_html_color(i)}"></span>' for i in range(200)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(input_path.name)} media report</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin: 0; background: #070b10; color: #dbeafe; font-family: Consolas, 'Cascadia Mono', monospace; }}
main {{ max-width: 1500px; margin: 0 auto; padding: 24px; }}
h1 {{ margin: 0 0 6px; color: #7dd3fc; font-family: 'Segoe UI', sans-serif; }}
.path {{ color: #c4b5fd; margin-bottom: 18px; word-break: break-all; }}
.report {{ background: #0c121a; border: 1px solid #263447; border-radius: 10px; padding: 16px 18px; box-shadow: 0 16px 36px rgba(0,0,0,.35); }}
.line {{ white-space: pre-wrap; line-height: 1.42; font-size: 13px; }}
.blank {{ line-height: .7; }}
.palette {{ display: grid; grid-template-columns: repeat(50, 1fr); gap: 2px; margin: 14px 0 22px; }}
.palette span {{ height: 6px; border-radius: 2px; }}
details {{ margin-top: 16px; background: #0f1722; border: 1px solid #243244; border-radius: 8px; padding: 10px 12px; }}
summary {{ cursor: pointer; color: #fbbf24; font-weight: 700; }}
pre {{ white-space: pre-wrap; word-break: break-word; color: #d1d5db; }}
</style>
</head>
<body>
<main>
<h1>Media Info Report</h1>
<div class="path">{html.escape(str(input_path))}</div>
<div class="palette">{palette_preview}</div>
<section class="report">
{''.join(rendered_lines)}
</section>
<details>
<summary>Plain text report</summary>
<pre>{html.escape(plain_report)}</pre>
</details>
<details>
<summary>Raw ffprobe JSON</summary>
<pre>{html.escape(raw_json)}</pre>
</details>
<details>
<summary>Raw ffprobe text overview</summary>
<pre>{html.escape(text_overview.strip())}</pre>
</details>
</main>
</body>
</html>
"""


def mux_compact_labels(values: list[str], max_items: int = 3) -> str:
    unique: list[str] = []
    for value in values:
        label = mux_language_label(value)
        if label not in unique:
            unique.append(label)
    if not unique:
        return ""
    return "+".join(unique[:max_items]) + ("+" if len(unique) > max_items else "")


def list_muxers(ffmpeg: str) -> list[str]:
    try:
        output = run_capture([ffmpeg, "-hide_banner", "-muxers"])
    except Exception:
        return ["mp4", "mkv", "mov", "webm", "mp3", "m4a", "wav", "flac", "opus"]

    muxers: list[str] = []
    for line in output.splitlines():
        line = line.rstrip()
        match = re.match(r"\s*E\s+([^\s]+)", line)
        if match:
            muxers.extend(name for name in match.group(1).split(",") if name)
    return sorted(set(muxers))


def default_config_path() -> Path:
    return script_dir() / CONFIG_FILE_NAME


def default_launcher_path() -> Path:
    return script_dir() / LAUNCHER_FILE_NAME


def default_ffmpeg_reference_path() -> Path:
    return script_dir() / FFMPEG_REFERENCE_FILE_NAME


def config_value(config: dict[str, Any], key: str, fallback: str = "") -> str:
    value = config_settings(config).get(key, fallback)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "y" if value else "n"
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return strip_quotes(str(value).strip())


def output_has_video(answers: dict[str, Any]) -> bool:
    return bool(answers.get("video_streams")) and not output_is_audio_only(answers)


def _answers_for_folder_item(settings_answers: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    item_answers = dict(settings_answers)
    copy_media_metadata(item_answers, item.get("answers", {}))
    item_answers["_quiet_packet_size_probe"] = True
    return item_answers


def _append_cut_suffix(path: Path) -> Path:
    """Backward-compatible helper for callers that need an explicit cut suffix."""
    return unique_numbered_path(path.with_name(f"{sanitize_output_stem(path.stem)}_cut{path.suffix}"))


def cuda_decoder_for_source(answers: dict[str, Any]) -> str | None:
    return CUDA_CUVID_DECODER_BY_CODEC.get(source_video_codec_name(answers))


def _cuda_fast_path_needs_ar_padding(answers: dict[str, Any]) -> bool:
    """Return True when the requested resize requires a pad filter
    and therefore cannot use the pure scale_cuda fast path.
    Only 'box' mode needs padding because the target canvas may differ from the
    AR-preserving scaled dimensions. Preset/height/width modes already compute
    AR-preserving dimensions where pad is a no-op."""
    resolution = answers.get("resolution", "n")
    if resolution is None or resolution == "n":
        return False
    if resize_mode_is_stretch(answers):
        return False
    if isinstance(resolution, dict) and resolution.get("mode") == "box":
        return True
    return False


def command_to_powershell(args: list[str]) -> str:
    return " ".join(ps_quote(arg) for arg in args)


def build_signalstats_command(
    input_path: Path,
    video_stream_index: int,
    sampling_mode: str,
    ffmpeg: str,
    output_path: Path,
    use_cuda_decode: bool = False,
) -> list[str]:
    step = {"fast": 120, "balanced": 30, "detailed": 5}.get(sampling_mode, 30)
    vf = f"select='not(mod(n,{step}))',signalstats,metadata=mode=print:file={metadata_filter_path(output_path)}"
    args = [ffmpeg, "-hide_banner", "-nostats", "-v", "warning"]
    if use_cuda_decode:
        args.extend(["-hwaccel", "cuda", "-hwaccel_device", str(GPU_DEVICE_INDEX)])
    args.extend(["-i", str(input_path), "-map", f"0:{video_stream_index}", "-vf", vf, "-an", "-sn", "-f", "null", os.devnull])
    return args


def build_copy_cut_concat_command(
    ffmpeg: str,
    overwrite: str,
    concat_list: Path,
    output_path: Path,
    chapter_plan: dict[str, Any] | None = None,
) -> list[str]:
    cmd = [
        ffmpeg, overwrite, "-hide_banner",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
    ]
    if chapter_plan and chapter_plan.get("mode") == "metadata":
        cmd.extend(["-i", str(chapter_plan["metadata_path"])])
    cmd.extend(["-map", "0", "-map_metadata", "0"])
    cmd.extend(copy_cut_chapter_map_args(chapter_plan, 1))
    cmd.extend(["-c", "copy", "-avoid_negative_ts", "make_zero", str(output_path)])
    return cmd


def join_first_video_frame_rate(item: dict[str, Any]) -> float | None:
    """Return the first video stream's frame rate (avg, then r_frame_rate)."""
    video_streams = item.get("video_streams") or [
        stream for stream in item.get("streams") or [] if stream.get("codec_type") == "video"
    ]
    if not video_streams:
        return None
    video = video_streams[0]
    return rational_to_float(video.get("avg_frame_rate")) or rational_to_float(video.get("r_frame_rate"))


def write_join_concat_list(items: list[dict[str, Any]], output_path: Path) -> Path:
    list_path = unique_numbered_path(output_path.with_name(f".{sanitize_output_stem(output_path.stem)}_ffconcat.txt"))
    lines = ["ffconcat version 1.0"]
    for item in items:
        lines.append(f"file '{ffconcat_quote_path(item['path'])}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


__all__ = [
    'timeline_is_modified',
    'source_data_keep_enabled',
    'embedded_attachment_keep_enabled',
    'parse_speed_factor',
    'encode_video_speed_factor',
    '_ffmwiz_gui_path',
    '_requirements_path',
    '_config_setting_for_logging',
    '_qml_gui_path',
    '_visible_len',
    '_progress_seconds_from_state',
    '_progress_target_mux_bitrate_kbps_from_command',
    '_apply_output_file_size_progress',
    'detect_nvidia_gpu_identity',
    '_store_capability_entry',
    '_as_int_value',
    '_as_float_value',
    'media_info_video_fps',
    'media_info_attachment_kind',
    'render_info_report_html',
    'mux_compact_labels',
    'list_muxers',
    'default_config_path',
    'default_launcher_path',
    'default_ffmpeg_reference_path',
    'config_value',
    'output_has_video',
    '_answers_for_folder_item',
    '_append_cut_suffix',
    'cuda_decoder_for_source',
    '_cuda_fast_path_needs_ar_padding',
    'command_to_powershell',
    'build_signalstats_command',
    'build_copy_cut_concat_command',
    'join_first_video_frame_rate',
    'write_join_concat_list',
]
