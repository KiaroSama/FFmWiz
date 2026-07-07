"""FFmWiz helpers (dependency level 0) — concerns: misc(91).

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


def source_subtitles_keep_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("keep_source_subtitles", True))


def output_supports_embedded_attachments(answers: dict[str, Any]) -> bool:
    return str(answers.get("output_ext") or "").lower().lstrip(".") in ATTACHMENT_COMPATIBLE_EXTS


def clamp_speed_factor(value: Any) -> float:
    try:
        factor = float(value)
    except (TypeError, ValueError):
        raise ValueError("Speed must be a number.")
    if factor <= 0:
        raise ValueError("Speed must be greater than zero.")
    if factor < MIN_SPEED_FACTOR or factor > MAX_SPEED_FACTOR:
        raise ValueError(
            f"Speed must be between {MIN_SPEED_FACTOR:g}x and {MAX_SPEED_FACTOR:g}x."
        )
    return factor


def ffmpeg_float(value: float) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"


def video_speed_transform_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("video_speed_enabled"))


def speed_suffix(speed: float, reverse: bool) -> str:
    percent = int(round(speed * 100))
    return f"_Speed{percent}" + ("_Reverse" if reverse else "")


def write_concat_list(paths: list[Path], concat_list: Path) -> None:
    with concat_list.open("w", encoding="utf-8") as handle:
        for path in paths:
            escaped = path.as_posix().replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")


def _probe_pyside6() -> bool:
    """Fast PySide6 availability check without importing Qt in the CLI process."""
    try:
        import importlib.util

        return (
            importlib.util.find_spec("PySide6") is not None
            and importlib.util.find_spec("PySide6.QtWidgets") is not None
        )
    except Exception:
        return False


def _prune_old_logs(logs_dir: Path, retention_days: int) -> None:
    if retention_days <= 0:
        return
    cutoff = time.time() - retention_days * 86400
    for path in logs_dir.glob("ffmwiz_*.log"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except Exception:
            pass


def _compact_ffmpeg_progress_state(state: dict[str, str]) -> str:
    """Format FFmpeg -progress key/value output as one compact log line."""
    keys = [
        "frame",
        "fps",
        "stream_0_0_q",
        "bitrate",
        "total_size",
        "out_time",
        "speed",
        "progress",
    ]
    parts: list[str] = []
    seen: set[str] = set()
    for key in keys:
        value = str(state.get(key, "") or "").strip()
        if value:
            parts.append(f"{key}={value}")
            seen.add(key)
    for key, value in state.items():
        if key in seen or key.startswith("_ffmwiz_"):
            continue
        if key.endswith("_q") and key != "stream_0_0_q":
            text = str(value or "").strip()
            if text:
                parts.append(f"{key}={text}")
    return ", ".join(parts) if parts else "no progress fields"


def _inject_progress_args(cmd: list[str]) -> list[str]:
    """Insert FFmpeg progress flags right after the binary path so the
    progress + log levels apply to all outputs of the command."""
    if len(cmd) < 1:
        return cmd
    new = [cmd[0]]
    # -nostats suppresses noisy stderr summary lines, -progress pipe:1
    # streams structured key=value progress to stdout, -loglevel warning
    # keeps real warnings/errors flowing into our captured stderr.
    new.extend(["-nostats", "-stats_period", "0.5", "-progress", "pipe:1", "-loglevel", "warning"])
    new.extend(cmd[1:])
    return new


def _human_size(b: int) -> str:
    if b is None or b < 0:
        return "?"
    size = float(b)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def _smoothed_eta_rate(state: dict[str, str], current_s: float, elapsed: float) -> float | None:
    """Return a stable processing rate (media-seconds per wall-second) for ETA.

    Uses the OVERALL average (current_s / elapsed). FFmpeg's per-tick 'speed='
    is noisy and a short-window rate overshoots early (the first frames decode
    fast during priming), which made the ETA optimistic and unreliable. The
    overall average is inherently smooth (both numerator and denominator grow
    monotonically) and converges to the true rate, so the ETA is steady and
    trustworthy. 'state' is unused now but kept for signature compatibility."""
    if elapsed <= 0 or current_s <= 0:
        return None
    return current_s / elapsed


def _progress_raw_seconds_from_state(state: dict[str, str]) -> float:
    for key in ("out_time_ms", "out_time_us"):
        value = state.get(key)
        if value:
            try:
                return max(0.0, float(value) / 1_000_000.0)
            except (TypeError, ValueError):
                pass
    text = state.get("out_time")
    if text:
        try:
            parts = text.split(":")
            if len(parts) == 3:
                return max(0.0, int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]))
        except (TypeError, ValueError):
            pass
    return 0.0


def _parse_ffmpeg_bitrate_kbps(value: Any) -> float | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    multiplier = 1.0 / 1000.0
    if text.endswith("k"):
        multiplier = 1.0
        text = text[:-1].strip()
    elif text.endswith("m"):
        multiplier = 1000.0
        text = text[:-1].strip()
    try:
        parsed = float(text) * multiplier
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 and math.isfinite(parsed) else None


def even_dimension(value: float | int) -> int:
    # Round to the NEAREST even number (codecs need even dimensions). Nearest-even
    # keeps the computed edge as close as possible to the ideal aspect-ratio-
    # preserving value, instead of always rounding up (which skewed the AR).
    return max(2, int(round(float(value) / 2.0)) * 2)


def capability_cache_path() -> Path:
    """Local capability-cache file path. Honors FFMWIZ_CACHE_DIR for isolated
    test runs; otherwise uses a project-local .cache directory."""
    override = os.environ.get("FFMWIZ_CACHE_DIR")
    base = Path(override) if override else (Path(__file__).resolve().parent / CAPABILITY_CACHE_DIRNAME)
    return base / CAPABILITY_CACHE_FILENAME


def _capability_run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def resolve_video_geometry(
    coded_width: int | None,
    coded_height: int | None,
    raw_ffprobe_sar: float | None,
    raw_ffprobe_dar: float | None,
) -> dict[str, Any]:
    """Pure one-way SAR/DAR resolver.

    Inputs are the coded dimensions and the RAW ffprobe SAR/DAR values, already
    parsed to positive floats (or None when ffprobe reported nothing valid).
    This function never mutates its inputs and must never be fed its own
    resolved output as raw metadata: a calculated/fallback DAR can therefore
    never masquerade as a detected ffprobe DAR.

    Deterministic cases:
      A: raw SAR valid                 -> SAR detected; DAR calculated from SAR.
         (raw DAR, if valid, is compared; coded dims + raw SAR win on conflict)
      B: raw SAR missing, raw DAR valid -> SAR calculated from DETECTED DAR.
      C: raw SAR valid, raw DAR missing -> DAR calculated from SAR (Case A).
      D: both raw values missing        -> assume square pixels (SAR 1:1),
         labeled as a fallback; DAR calculated from the fallback SAR.
      E: invalid/missing dims           -> unresolved geometry, no division.
    """
    result: dict[str, Any] = {
        "coded_width": coded_width or None,
        "coded_height": coded_height or None,
        "raw_ffprobe_sar": raw_ffprobe_sar,
        "raw_ffprobe_dar": raw_ffprobe_dar,
        "resolved_sar": None,
        "resolved_dar": None,
        "effective_dar_decimal": None,
        "sar_source": "unknown",
        "dar_source": "unknown",
        "pixel_shape": "unknown",
        "fallback_used": False,
        "discrepancy_detected": False,
        "warning": None,
        "discrepancy": None,
    }

    if not (coded_width and coded_height and coded_width > 0 and coded_height > 0):
        # Case E: cannot safely divide; report unresolved geometry.
        result["warning"] = "Source coded dimensions are missing or invalid; geometry unresolved."
        return result

    wh = coded_width / coded_height

    if raw_ffprobe_sar:
        # Case A / Case C: raw SAR is authoritative; DAR derived from dims + SAR.
        result["resolved_sar"] = raw_ffprobe_sar
        result["sar_source"] = "detected by ffprobe"
        calc_dar = wh * raw_ffprobe_sar
        result["resolved_dar"] = calc_dar
        result["dar_source"] = "calculated from coded resolution and SAR"
        if raw_ffprobe_dar:
            if abs(calc_dar - raw_ffprobe_dar) <= 0.02:
                result["dar_source"] = "calculated from coded resolution and SAR (ffprobe DAR agrees)"
            else:
                result["discrepancy_detected"] = True
                result["discrepancy"] = (calc_dar, raw_ffprobe_dar)
                result["warning"] = (
                    "SAR/DAR discrepancy: calculated DAR %.6f disagrees with ffprobe DAR %.6f; "
                    "using coded resolution + raw SAR as the source of truth."
                    % (calc_dar, raw_ffprobe_dar)
                )
    elif raw_ffprobe_dar:
        # Case B: derive SAR from a GENUINELY DETECTED ffprobe DAR only.
        result["resolved_dar"] = raw_ffprobe_dar
        result["dar_source"] = "detected by ffprobe"
        result["resolved_sar"] = raw_ffprobe_dar / wh
        result["sar_source"] = "calculated from coded resolution and detected DAR"
    else:
        # Case D: neither raw value is available; assume square pixels (honest
        # fallback). The DAR is derived from the fallback SAR and is never
        # labeled as detected.
        result["resolved_sar"] = 1.0
        result["sar_source"] = "fallback assumption"
        result["resolved_dar"] = wh
        result["dar_source"] = "calculated from coded resolution and fallback SAR"
        result["fallback_used"] = True
        result["warning"] = "Source SAR and DAR are unavailable; assuming square pixels (SAR 1:1)."

    result["effective_dar_decimal"] = result["resolved_dar"]
    resolved_sar = result["resolved_sar"]
    if resolved_sar is not None:
        if result["fallback_used"]:
            result["pixel_shape"] = "square (assumed)"
        else:
            result["pixel_shape"] = (
                "square" if abs(resolved_sar - 1.0) < SAR_DAR_TOLERANCE else "non-square"
            )
    return result


def resize_mode_is_stretch(answers: dict[str, Any]) -> bool:
    """Return True if the user explicitly selected a stretch/exact mode."""
    resolution = answers.get("resolution", "n")
    if isinstance(resolution, dict):
        return resolution.get("mode") == "exact_stretch"
    if isinstance(resolution, tuple) and len(resolution) == 2:
        return True  # Legacy tuple mode is always stretch.
    return False


def chroma_subsampling_alignment(pix_fmt: str | None) -> tuple[int, int]:
    """Return (horizontal, vertical) chroma sample-grid alignment for a pixel
    format. The crop origin must be a multiple of these values so cropping never
    introduces a chroma-phase shift (color bleeding) on subsampled formats.

    4:2:0 -> (2, 2); 4:2:2 -> (2, 1); 4:4:0 -> (1, 2); 4:1:1 -> (4, 1);
    4:1:0 -> (4, 4); 4:4:4 / RGB / grayscale -> (1, 1). Unknown formats fall
    back to the most conservative common case (4:2:0)."""
    fmt = str(pix_fmt or "").strip().lower()
    if not fmt:
        return (2, 2)
    if fmt.startswith((
        "rgb", "bgr", "gbr", "argb", "abgr", "rgba", "bgra",
        "0rgb", "0bgr", "rgb0", "bgr0", "gray", "ya", "pal8", "monow", "monob",
    )):
        return (1, 1)
    # NV-/P-family semi-planar formats.
    if fmt in {"nv12", "nv21", "p010", "p010le", "p010be", "p016", "p016le", "p016be"}:
        return (2, 2)
    if fmt in {"nv16", "p210", "p210le", "p210be", "p216", "p216le", "p216be"}:
        return (2, 1)
    if fmt in {"nv24", "nv42", "p410", "p410le", "p410be", "p416", "p416le", "p416be"}:
        return (1, 1)
    # Packed 4:2:2.
    if fmt in {"yuyv422", "uyvy422", "yvyu422"}:
        return (2, 1)
    # Planar yuv tokens carry the subsampling in the name.
    if "444" in fmt:
        return (1, 1)
    if "440" in fmt:
        return (1, 2)
    if "422" in fmt:
        return (2, 1)
    if "411" in fmt:
        return (4, 1)
    if "410" in fmt:
        return (4, 4)
    if "420" in fmt:
        return (2, 2)
    return (2, 2)


def strip_quotes(value: str) -> str:
    value = value.strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return value


def _confirm_install(prompt: str, env_auto_name: str) -> bool:
    if os.environ.get(env_auto_name) or os.environ.get("FFMWIZ_AUTO_INSTALL"):
        return True
    if os.environ.get("FFMWIZ_NO_AUTO_INSTALL"):
        return False
    try:
        choice = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return choice in {"", "y", "yes"}


def run_capture(args: list[str]) -> str:
    result = subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def decode_subprocess_bytes(data: bytes | None, encoding: str = "utf-8-sig") -> tuple[str, str]:
    if not data:
        return "", f"{encoding} with errors=replace"
    return data.decode(encoding, errors="replace"), f"{encoding} with errors=replace"


def _safe_resolved_path(path: Path) -> str:
    try:
        return str(path.expanduser().resolve(strict=False))
    except Exception as exc:
        return f"(could not normalize path: {exc})"


def _parse_db_value(text: Any) -> float | None:
    """Parse a dB string such as '-19.8 dB' to a float, or None when unknown."""
    match = re.search(r"-?\d+(?:\.\d+)?", str(text or ""))
    return float(match.group(0)) if match else None


def _join_summary_minmax(rows: list[dict[str, Any]], key: str):
    """Return ((hi_value, hi_name), (lo_value, lo_name), unknown_count) for a
    metric across rows, or None when no row has a known value."""
    known = [(row[key], row["name"]) for row in rows if row.get(key)]
    if not known:
        return None
    hi = max(known, key=lambda pair: pair[0])
    lo = min(known, key=lambda pair: pair[0])
    return hi, lo, len(rows) - len(known)


def join_summary_total_duration(answers: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Total raw (pre-cut) duration of the selected Join inputs plus an
    approximate frame count. Returns a structured dict so both the colored and
    the plain-text renderers stay consistent."""
    durations = [row["duration"] for row in rows if row.get("duration")]
    unknown = len(rows) - len(durations)
    total = sum(durations) if durations else 0.0
    output_fps = answers.get("fps")
    frames = None
    frame_basis = None
    if durations:
        try:
            out_fps = float(output_fps) if output_fps is not None else None
        except (TypeError, ValueError):
            out_fps = None
        if out_fps and out_fps > 0:
            frames = int(round(total * out_fps))
            frame_basis = f"at selected output {out_fps:g} fps"
        elif all(row.get("fps") for row in rows if row.get("duration")):
            frames = int(round(sum((row["duration"] * row["fps"]) for row in rows if row.get("duration") and row.get("fps"))))
            frame_basis = "source-frame estimate"
    return {
        "total_seconds": total,
        "known_count": len(durations),
        "unknown_count": unknown,
        "frames": frames,
        "frame_basis": frame_basis,
    }


def tag_int(stream: dict[str, Any], names: list[str]) -> int | None:
    tags = stream.get("tags", {})
    normalized = {str(key).upper(): value for key, value in tags.items()}
    for name in names:
        value = normalized.get(name.upper())
        if value is None:
            continue
        try:
            return int(value)
        except ValueError:
            pass
    return None


def describe_bitrate(kbps: int | None) -> str:
    return f"{kbps} kbps" if kbps else "unknown"


def display_language(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"und", "undefined", "unknown"}:
        return "unknown"
    return text


def close_enough_bitrate(left: int | None, right: int | None) -> bool:
    if not left or not right:
        return True
    return abs(left - right) <= max(8, round(max(left, right) * 0.05))


def median_int(values: list[int]) -> int | None:
    if not values:
        return None
    sorted_values = sorted(values)
    middle = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[middle]
    return round((sorted_values[middle - 1] + sorted_values[middle]) / 2)


def duplicate_tracks_to_drop(report: dict[str, Any]) -> set[int]:
    parent: dict[int, int] = {}

    def find(value: int) -> int:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for left, right in report.get("confirmed_pairs", []):
        union(left, right)

    groups: dict[int, set[int]] = {}
    for value in list(parent):
        groups.setdefault(find(value), set()).add(value)

    drop: set[int] = set()
    for members in groups.values():
        keep = min(members)
        drop.update(member for member in members if member != keep)
    return drop


def _trim_float(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _plural_unit(count: int | float, singular: str, plural: str | None = None) -> str:
    return singular if float(count) == 1.0 else (plural or singular + "s")


def parse_colon_duration_seconds(value: str) -> float | None:
    match = re.fullmatch(r"(\d{1,3}):(\d{2}):(\d{2}(?:\.\d+)?)", value.strip())
    if not match:
        return None
    try:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
    except ValueError:
        return None
    return hours * 3600 + minutes * 60 + seconds


def media_info_display_key(key: str) -> str:
    normalized = str(key or "").lower().replace("-", "_").replace(" ", "_")
    if normalized == "color_range":
        return "Color range"
    return key


def media_info_sidecar_path(info_path: Path, suffix: str, extension: str) -> Path:
    extension = extension if extension.startswith(".") else "." + extension
    return info_path.with_name(f"{info_path.stem}_{suffix}{extension}")


def media_info_percentile(values: list[float], percentile: float) -> float | None:
    cleaned = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return cleaned[0]
    position = (len(cleaned) - 1) * percentile / 100.0
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return cleaned[lower]
    fraction = position - lower
    return cleaned[lower] * (1.0 - fraction) + cleaned[upper] * fraction


def media_info_csv_time(value: str) -> float | None:
    try:
        if value and value.upper() != "N/A":
            return float(value)
    except (TypeError, ValueError):
        return None
    return None


def media_info_video_codec_family(codec: str) -> str:
    normalized = str(codec or "").lower()
    if normalized in {"h264", "avc1"}:
        return "H.264/AVC"
    if normalized in {"hevc", "h265", "hev1", "hvc1"}:
        return "H.265/HEVC"
    if normalized == "av1":
        return "AV1"
    if normalized in {"vp9", "vp8"}:
        return normalized.upper()
    return codec or "unknown"


def media_info_subtitle_kind(codec: str) -> str:
    normalized = str(codec or "").lower()
    if normalized in TEXT_SUBTITLE_CODECS:
        return "text subtitle"
    if normalized in BITMAP_SUBTITLE_CODECS:
        return "bitmap subtitle"
    return "unknown"


def media_info_payload_for_report(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(payload)
    cleaned.pop("program_version", None)
    cleaned.pop("library_versions", None)
    return cleaned


def media_info_html_color(index: int) -> str:
    hue = (index * 137) % 360
    phase = index % 5
    saturation = 74 + phase * 4
    light = 58 + ((index * 3) % 18)
    chroma = saturation / 100.0
    x = chroma * (1 - abs((hue / 60.0) % 2 - 1))
    m = light / 100.0 - chroma / 2
    if hue < 60:
        r, g, b = chroma, x, 0
    elif hue < 120:
        r, g, b = x, chroma, 0
    elif hue < 180:
        r, g, b = 0, chroma, x
    elif hue < 240:
        r, g, b = 0, x, chroma
    elif hue < 300:
        r, g, b = x, 0, chroma
    else:
        r, g, b = chroma, 0, x
    return f"rgb({max(0, min(255, int((r + m) * 255)))}, {max(0, min(255, int((g + m) * 255)))}, {max(0, min(255, int((b + m) * 255)))})"


def mux_find_video_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in MUX_CLEANUP_VIDEO_EXTS else []
    if not input_path.is_dir():
        return []
    return sorted(
        (path for path in input_path.rglob("*") if path.is_file() and path.suffix.lower() in MUX_CLEANUP_VIDEO_EXTS),
        key=lambda path: str(path.relative_to(input_path)).lower(),
    )


def mux_normalize_language(value: str) -> str:
    text = str(value or "").strip().lower()
    return "unknown" if text in {"", "und", "undefined"} else text


def mux_terminal_width() -> int:
    try:
        return max(72, shutil.get_terminal_size((100, 20)).columns)
    except Exception:
        return 100


def mux_assign_prompt_number(answers: dict[str, Any]) -> int:
    number = int(answers.get("_mux_next_question_number") or answers.get("_question_number") or 1)
    answers["_question_number"] = number
    answers["_mux_next_question_number"] = number + 1
    return number


def mux_language_label(value: str) -> str:
    labels = {
        "ja": "JA",
        "jpn": "JA",
        "japanese": "JA",
        "en": "EN",
        "eng": "EN",
        "english": "EN",
        "fa": "FA",
        "fas": "FA",
        "per": "FA",
        "persian": "FA",
    }
    normalized = (value or "und").lower()
    return labels.get(normalized, normalized.upper())


def mux_unique_directory_path(path: Path) -> Path:
    if not path.exists():
        return path
    for counter in range(2, 10000):
        candidate = path.with_name(f"{path.name} ({counter})")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find available output folder for: {path}")


def mux_path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def mux_destination_snapshot(paths: list[Path]) -> dict[Path, tuple[int, int]]:
    snapshot: dict[Path, tuple[int, int]] = {}
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[path] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def mux_robocopy_success(returncode: int) -> bool:
    return 0 <= int(returncode) <= 7


def is_folder_media_candidate(path: Path) -> bool:
    return path.is_file() and path.suffix.lower().lstrip(".") in FOLDER_MEDIA_EXTS


def path_is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def choose_folder_representative(items: list[dict[str, Any]]) -> dict[str, Any]:
    for item in items:
        if item.get("has_video") and item.get("has_audio"):
            return item
    for item in items:
        if item.get("has_video"):
            return item
    return items[0]


def launcher_content() -> str:
    script_name = Path(__file__).name
    return f"""param(
    [Parameter(ValueFromRemainingArguments = $true)]
    $ScriptArgs
)

# Canonical PowerShell launcher for FFmWiz.
# - Resolves project root and {script_name} relative to this script's location, so
#   the launcher works regardless of the caller's current working directory.
# - Prefers the Windows Python launcher (py -3), then python, then python3.
# - Forwards every argument unchanged to {script_name}.
# - Returns the same exit code as the Python process.
# - Does not require admin rights and does not hard-code user-specific paths.

$ErrorActionPreference = 'Stop'

# Resolve project root relative to this launcher.
$scriptDir = if ($PSScriptRoot) {{
    $PSScriptRoot
}} elseif ($PSCommandPath) {{
    Split-Path -Parent $PSCommandPath
}} else {{
    $null
}}

if (-not $scriptDir -or -not (Test-Path -LiteralPath $scriptDir)) {{
    Write-Host "run.ps1 could not determine its own directory. Re-run it as a file (not piped into PowerShell)." -ForegroundColor Red
    exit 1
}}

$projectRoot = (Resolve-Path -LiteralPath $scriptDir).Path
$scriptPath = Join-Path -Path $projectRoot -ChildPath '{script_name}'

if (-not (Test-Path -LiteralPath $scriptPath)) {{
    Write-Host "{script_name} was not found next to run.ps1. Expected at: $scriptPath" -ForegroundColor Red
    Write-Host "Make sure run.ps1 sits in the FFmWiz repository root alongside {script_name}." -ForegroundColor Red
    exit 1
}}

# Forward every CLI argument unchanged. ValueFromRemainingArguments preserves
# user-supplied flags including paths with spaces.
$forwarded = @()
if ($ScriptArgs) {{ $forwarded = @($ScriptArgs) }}

function Test-FFmWizPython {{
    param(
        [Parameter(Mandatory = $true)]
        [string]$Exe,
        [string[]]$Args = @()
    )
    $oldErrorActionPreference = $ErrorActionPreference
    try {{
        $ErrorActionPreference = 'Continue'
        & $Exe @Args -c "import sys" > $null 2>&1
        return $LASTEXITCODE -eq 0
    }} catch {{
        return $false
    }} finally {{
        $ErrorActionPreference = $oldErrorActionPreference
    }}
}}

# Probe interpreters in priority order: py -3, python, python3.
$pythonExe = $null
$pythonArgs = @()

if (Get-Command py -ErrorAction SilentlyContinue) {{
    if (Test-FFmWizPython -Exe 'py' -Args @('-3')) {{
        $pythonExe = 'py'
        $pythonArgs = @('-3')
    }}
}}
if (-not $pythonExe -and (Get-Command python -ErrorAction SilentlyContinue)) {{
    if (Test-FFmWizPython -Exe 'python') {{
        $pythonExe = 'python'
        $pythonArgs = @()
    }}
}}
if (-not $pythonExe -and (Get-Command python3 -ErrorAction SilentlyContinue)) {{
    if (Test-FFmWizPython -Exe 'python3') {{
        $pythonExe = 'python3'
        $pythonArgs = @()
    }}
}}

if (-not $pythonExe) {{
    Write-Host "Python was not found in PATH. Install Python 3.10+ and reopen the terminal." -ForegroundColor Red
    Write-Host "Tried: py -3, python, python3." -ForegroundColor Red
    exit 9009
}}

# Run {script_name} and propagate its exit code unchanged.
& $pythonExe @pythonArgs $scriptPath @forwarded
$pythonExitCode = if ($null -ne $LASTEXITCODE) {{ $LASTEXITCODE }} else {{ 0 }}
exit $pythonExitCode
"""


def parse_env_config(text: str) -> dict[str, Any]:
    """Parse a config.env (dotenv-style) file into the same {"settings": {...}}
    shape the rest of FFmWiz expects, so all config_value() consumers are
    unchanged. Lines are key=value; '#' lines and blanks are ignored; an
    optional leading 'export ' is accepted; surrounding quotes are stripped."""
    settings: dict[str, Any] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line[:7].lower() == "export ":
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            settings[key] = value
    return {"settings": settings}


def config_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = config.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("config file must contain settings (key=value lines).")
    return settings


def parse_bool_config(value: str, default: bool) -> bool:
    if not value:
        return default
    lowered = value.lower()
    if lowered in {"y", "yes", "true", "1", "on"}:
        return True
    if lowered in {"n", "no", "false", "0", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def parse_int_config(value: str, default: int | None = None, allow_n: bool = True) -> int | str | None:
    if not value:
        return default
    lowered = value.lower()
    if lowered == "n" and allow_n:
        return "n"
    if not re.fullmatch(r"\d+", value):
        raise ValueError(f"Invalid integer value: {value}")
    return int(value)


def parse_float_config(value: str, default: float | None = None, allow_n: bool = True) -> float | str | None:
    """Parse a float config value. Empty -> default; 'n'/'keep' -> 'n' (when
    allowed) to mean "no change / keep source"; otherwise a float."""
    if not value:
        return default
    lowered = value.lower()
    if lowered in {"n", "keep"} and allow_n:
        return "n"
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid number value: {value}") from exc


def parse_selection_config(value: str, max_count: int, default: list[int], allow_none: bool = False) -> list[int] | str:
    lowered = value.lower().strip()
    if not lowered:
        return default
    if lowered in {"n", "all"}:
        return "all"
    if allow_none and lowered in {"none", "no", "clear", "delete"}:
        return []
    pieces = [piece.strip() for piece in lowered.split(",") if piece.strip()]
    if any(not re.fullmatch(r"\d+", piece) for piece in pieces):
        raise ValueError(f"Invalid stream selection: {value}")
    numbers = sorted(set(int(piece) for piece in pieces))
    bad = [number for number in numbers if number < 0 or number >= max_count]
    if bad:
        raise ValueError(f"Invalid stream number(s): {bad}. Allowed range: 0 to {max_count - 1}")
    return numbers


def is_back_value(value: str, *, allow_text: bool = False) -> bool:
    lowered = str(value).strip().lower()
    if lowered in BACK_INPUT_TOKENS:
        return True
    return allow_text and lowered in {"b", "back"}


def first_video_size(answers: dict[str, Any]) -> tuple[int, int]:
    stream = answers["video_streams"][0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("Could not detect source video dimensions.")
    return width, height


def preview_size(source_width: int, source_height: int) -> tuple[int, int]:
    scale = min(1280 / source_width, 720 / source_height, 1.0)
    return max(1, round(source_width * scale)), max(1, round(source_height * scale))


def video_codec_is_copy(answers: dict[str, Any]) -> bool:
    return str(answers.get("video_codec", "")).lower() == "copy"


def _folder_validation_items(answers: dict[str, Any]) -> list[dict[str, Any]]:
    items = answers.get("_folder_items")
    return items if isinstance(items, list) else []


def looks_like_generated_output_file(path: Path) -> bool:
    stem = path.stem.lower()
    return any(stem.endswith(suffix.lower()) for suffix in GENERATED_OUTPUT_SUFFIXES)


def join_video_files_in_folder(folder: Path) -> list[Path]:
    """Return video files directly inside `folder`, sorted by name (case-insensitive)."""
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    return sorted(
        (p for p in entries if p.is_file() and p.suffix.lower().lstrip(".") in FOLDER_VIDEO_EXTS),
        key=lambda p: p.name.lower(),
    )


def is_single_contiguous_cut(answers: dict[str, Any]) -> bool:
    return len(list(answers.get("cut_keep_ranges") or [])) == 1


def video_bitrate_mode(answers: dict[str, Any]) -> str:
    mode = str(answers.get("video_bitrate_mode") or "quality_vbr").strip().lower()
    return mode if mode in {"quality_vbr", "strict_size"} else "quality_vbr"


def ps_quote(arg: str) -> str:
    if arg == "":
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_./:+=-]+", arg):
        return arg
    return "'" + arg.replace("'", "''") + "'"


def build_concat_copy_command(ffmpeg: str, concat_list: Path, output_path: Path) -> list[str]:
    cmd = [
        ffmpeg,
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-hide_banner",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-map",
        "0",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
    ]
    if output_path.suffix.lstrip(".").lower() in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])
    cmd.append(str(output_path))
    return cmd


def ffmpeg_input_section_end(cmd: list[str]) -> int:
    idx = 0
    end = 1
    while idx < len(cmd):
        if cmd[idx] == "-i" and idx + 1 < len(cmd):
            end = idx + 2
            idx += 2
            continue
        idx += 1
    return end


def ensure_video_input(answers: dict[str, Any]) -> None:
    if not answers.get("video_streams"):
        raise ValueError("This mode needs a video stream.")


def ffmpeg_initial_progress_detail(answers: dict[str, Any], cmd: list[str]) -> str:
    text = " ".join(str(part) for part in cmd)
    has_nvdec = "-hwaccel cuda" in text
    has_cuda_frames = "-hwaccel_output_format cuda" in text
    has_nvenc = "_nvenc" in text
    has_complex = "-filter_complex" in cmd
    if has_cuda_frames:
        return "launching CUDA decode/filter path and waiting for first encoded timestamp"
    if has_nvdec and has_nvenc and has_complex:
        return "CUDA/NVDEC decoding -> CPU filter_complex -> NVENC encoding; waiting for first encoded timestamp"
    if has_nvenc and has_complex:
        return "CPU decode/filter_complex -> NVENC encoding; waiting for first encoded timestamp"
    if has_complex:
        return "CPU filter_complex is starting; waiting for first encoded timestamp"
    return "starting FFmpeg and waiting for first progress timestamp"


def largest_time_unit(duration: float) -> str:
    """Largest natural time unit for a duration: 'h' (>=1h), 'm' (>=1min), 's'."""
    if duration and duration >= 3600:
        return "h"
    if duration and duration >= 60:
        return "m"
    return "s"


def describe_additional_track_file(item: dict[str, Any]) -> str:
    parts: list[str] = []
    audio_count = len(item.get("audio_streams") or [])
    subtitle_count = len(item.get("subtitle_streams") or [])
    ignored_video_count = len(item.get("video_streams") or [])
    if audio_count:
        parts.append(f"audio streams: {audio_count}")
    if subtitle_count:
        parts.append(f"subtitle streams: {subtitle_count}")
    if ignored_video_count:
        parts.append(f"ignored cover/video streams: {ignored_video_count}")
    return " | ".join(parts) if parts else "no addable streams"


def build_add_files_to_video_command(
    ffmpeg: str,
    input_path: Path,
    extra_items: list[dict[str, Any]],
    output_path: Path,
    source_audio_count: int = 0,
    source_subtitle_count: int = 0,
) -> list[str]:
    cmd: list[str] = [ffmpeg, "-y" if OVERWRITE_OUTPUT else "-n", "-i", str(input_path)]
    for item in extra_items:
        cmd.extend(["-i", str(item["path"])])
    cmd.extend(["-map", "0"])
    for input_number, item in enumerate(extra_items, start=1):
        if item.get("audio_streams"):
            cmd.extend(["-map", f"{input_number}:a?"])
        if item.get("subtitle_streams"):
            cmd.extend(["-map", f"{input_number}:s?"])
    cmd.extend(["-map_metadata", "0", "-c", "copy"])

    output_audio_index = source_audio_count
    output_subtitle_index = source_subtitle_count
    for item in extra_items:
        for metadata in item.get("audio_metadata") or [{} for _ in item.get("audio_streams", [])]:
            for key, value in metadata.items():
                cmd.extend([f"-metadata:s:a:{output_audio_index}", f"{key}={value}"])
            output_audio_index += 1
        for metadata in item.get("subtitle_metadata") or [{} for _ in item.get("subtitle_streams", [])]:
            for key, value in metadata.items():
                cmd.extend([f"-metadata:s:s:{output_subtitle_index}", f"{key}={value}"])
            output_subtitle_index += 1

    cmd.append(str(output_path))
    return cmd


def parse_track_remove_specs(text: str, stream_count: int | None = None) -> list[str]:
    """Parse comma-separated stream specifiers to REMOVE. Accepts an absolute
    index (e.g. 2) or an ffmpeg type:index (e.g. a:1, s:0, v:0)."""
    specs: list[str] = []
    for token in str(text or "").split(","):
        token = token.strip().lower()
        if not token:
            continue
        if re.fullmatch(r"\d+", token):
            index = int(token)
            if stream_count is not None and index >= stream_count:
                raise ValueError(f"Stream index {index} is out of range (file has {stream_count} streams).")
            specs.append(token)
        elif re.fullmatch(r"[vas]:\d+", token):
            specs.append(token)
        else:
            raise ValueError(f"Invalid stream spec: {token!r}. Use an index like 2, or type:index like a:1.")
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for spec in specs:
        if spec not in seen:
            seen.add(spec)
            unique.append(spec)
    return unique


def normalize_track_remove_specs(specs: list[str], streams: list[dict[str, Any]]) -> list[str]:
    """Convert absolute-index removal specs to clearer type:index specs using the
    probe streams (e.g. '1' -> 'a:0' = the first audio track), which is more
    explicit and robust than a bare stream index."""
    if not streams:
        return list(specs)
    abs_to_typed: dict[str, str] = {}
    type_counts: dict[str, int] = {}
    for stream in streams:
        index = stream.get("index")
        letter = {"video": "v", "audio": "a", "subtitle": "s"}.get(stream.get("codec_type"))
        if letter is None or index is None:
            continue
        rel = type_counts.get(letter, 0)
        abs_to_typed[str(index)] = f"{letter}:{rel}"
        type_counts[letter] = rel + 1
    return [abs_to_typed.get(spec, spec) for spec in specs]


def source_video_codec_family(answers: dict[str, Any]) -> str:
    codec = str((answers.get("video_streams") or [{}])[0].get("codec_name", "")).lower()
    if codec in {"h264", "avc1"}:
        return "H264"
    if codec in {"hevc", "h265"}:
        return "H265"
    if codec == "av1":
        return "AV1"
    if codec == "vp9":
        return "VP9"
    return "H265"


def hardsub_internal_subtitle_codec_is_supported(codec: str) -> bool:
    return str(codec or "").strip().lower() in TEXT_SUBTITLE_CODECS


def hardsub_internal_subtitle_error(codec: str) -> str | None:
    normalized = str(codec or "").strip().lower()
    if normalized in TEXT_SUBTITLE_CODECS:
        return None
    if normalized in BITMAP_SUBTITLE_CODECS:
        return HARDSUB_BITMAP_SUBTITLE_ERROR
    return (
        f"Subtitle codec {normalized or 'unknown'} is not supported by this HardSub mode. "
        "Choose a text subtitle stream or use an external .srt/.ass/.ssa/.vtt/.webvtt file."
    )


def hardsub_quality_value(answers: dict[str, Any], video_encoder: str) -> int:
    if answers.get("hardsub_quality_mode") == "custom":
        return int(answers.get("hardsub_quality_value", 18))
    mode = answers.get("hardsub_quality_mode", "near-lossless")
    preset = HARDSUB_QUALITY_PRESETS.get(mode, HARDSUB_QUALITY_PRESETS["near-lossless"])
    if video_encoder.endswith("_nvenc"):
        return preset["nvenc"]
    if video_encoder in {"libx265", "libaom-av1", "libvpx-vp9"}:
        return preset["cpu_hevc"]
    return preset["cpu"]


def ffconcat_quote_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", r"'\''")


def join_item_answers(base_answers: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ffmpeg": base_answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "ffprobe": base_answers.get("ffprobe") or shutil.which("ffprobe") or "ffprobe",
        "detect_duplicate_audio": base_answers.get("detect_duplicate_audio", True),
        "input_path": item["path"],
        "probe": item.get("probe") or {},
        "format": item.get("format") or {},
        "video_streams": item.get("video_streams") or [],
        "audio_streams": item.get("audio_streams") or [],
        "subtitle_streams": item.get("subtitle_streams") or [],
        "attachment_streams": item.get("attachment_streams") or [],
        "data_streams": item.get("data_streams") or [],
    }


__all__ = [
    'source_subtitles_keep_enabled',
    'output_supports_embedded_attachments',
    'clamp_speed_factor',
    'ffmpeg_float',
    'video_speed_transform_enabled',
    'speed_suffix',
    'write_concat_list',
    '_probe_pyside6',
    '_prune_old_logs',
    '_compact_ffmpeg_progress_state',
    '_inject_progress_args',
    '_human_size',
    '_strip_ansi',
    '_smoothed_eta_rate',
    '_progress_raw_seconds_from_state',
    '_parse_ffmpeg_bitrate_kbps',
    'even_dimension',
    'capability_cache_path',
    '_capability_run',
    'resolve_video_geometry',
    'resize_mode_is_stretch',
    'chroma_subsampling_alignment',
    'strip_quotes',
    '_confirm_install',
    'run_capture',
    'decode_subprocess_bytes',
    '_safe_resolved_path',
    '_parse_db_value',
    '_join_summary_minmax',
    'join_summary_total_duration',
    'tag_int',
    'describe_bitrate',
    'display_language',
    'close_enough_bitrate',
    'median_int',
    'duplicate_tracks_to_drop',
    '_trim_float',
    '_plural_unit',
    'parse_colon_duration_seconds',
    'media_info_display_key',
    'media_info_sidecar_path',
    'media_info_percentile',
    'media_info_csv_time',
    'media_info_video_codec_family',
    'media_info_subtitle_kind',
    'media_info_payload_for_report',
    'media_info_html_color',
    'mux_find_video_files',
    'mux_normalize_language',
    'mux_terminal_width',
    'mux_assign_prompt_number',
    'mux_language_label',
    'mux_unique_directory_path',
    'mux_path_is_under',
    'mux_destination_snapshot',
    'mux_robocopy_success',
    'is_folder_media_candidate',
    'path_is_inside',
    'choose_folder_representative',
    'launcher_content',
    'parse_env_config',
    'config_settings',
    'parse_bool_config',
    'parse_int_config',
    'parse_float_config',
    'parse_selection_config',
    'is_back_value',
    'first_video_size',
    'preview_size',
    'video_codec_is_copy',
    '_folder_validation_items',
    'looks_like_generated_output_file',
    'join_video_files_in_folder',
    'is_single_contiguous_cut',
    'video_bitrate_mode',
    'ps_quote',
    'build_concat_copy_command',
    'ffmpeg_input_section_end',
    'ensure_video_input',
    'ffmpeg_initial_progress_detail',
    'largest_time_unit',
    'describe_additional_track_file',
    'build_add_files_to_video_command',
    'parse_track_remove_specs',
    'normalize_track_remove_specs',
    'source_video_codec_family',
    'hardsub_internal_subtitle_codec_is_supported',
    'hardsub_internal_subtitle_error',
    'hardsub_quality_value',
    'ffconcat_quote_path',
    'join_item_answers',
]
