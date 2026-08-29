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

# The facade back-import was deleted: every name this module uses comes
# from the LOWER tiers above, which the facade only re-exported. Importing
# it here bought nothing and made this module unimportable on its own,
# because the facade ends with `__all__ += <this module>.__all__` and
# reached that line while this module was still on its first statements.


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
    # NOT Path(__file__).name: this module used to BE FFmWiz.py, so after the
    # package split the template started pointing every fresh launcher at
    # 'L00_misc_b.py' and every clean install got a run.ps1 that cannot start.
    script_name = MAIN_SCRIPT_FILE_NAME
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
    # Probe the VERSION, not just runnability. `import sys` succeeds on Python
    # 3.9 too, so the launcher used to pick an interpreter FFmWiz cannot run on
    # and then fail deep inside the app instead of trying the next candidate.
    $oldErrorActionPreference = $ErrorActionPreference
    try {{
        $ErrorActionPreference = 'Continue'
        $reported = & $Exe @Args -c "import sys; sys.stdout.write('.'.join(map(str, sys.version_info[:3])))" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $reported) {{ return $false }}
        $parts = ([string]$reported).Trim().Split('.')
        $major = 0; $minor = 0
        [void][int]::TryParse($parts[0], [ref]$major)
        if ($parts.Length -gt 1) {{ [void][int]::TryParse($parts[1], [ref]$minor) }}
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {{ return $true }}
        Write-Host "Skipping $Exe $Args - Python $reported is older than the required 3.10." -ForegroundColor DarkYellow
        return $false
    }} catch {{
        return $false
    }} finally {{
        $oldErrorActionPreference | Out-Null
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


def smallest_video_size(answers: dict[str, Any]) -> tuple[int, int]:
    """Smallest width/height across the primary input AND every joined input.

    Crop margins are validated once and then applied to every input of a join,
    so validating them against input 0 alone let a margin that is legal for a
    1920x1080 first clip produce `crop=iw-800-800` on a 640x480 later clip --
    a negative width that FFmpeg rejects with "Invalid too big or non positive
    size for width '-960'".
    """
    widths: list[int] = []
    heights: list[int] = []
    streams = list(answers.get("video_streams") or [])
    for item in answers.get("join_input_items") or []:
        streams.extend(list(item.get("video_streams") or [])[:1])
    for stream in streams:
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        if width > 0 and height > 0:
            widths.append(width)
            heights.append(height)
    if not widths:
        return first_video_size(answers)
    return min(widths), min(heights)


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
    'smallest_video_size',
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
