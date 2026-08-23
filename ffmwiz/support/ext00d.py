"""FFmWiz ext00b overflow (ext00d) — split for file size.

Back-imports ext00b and is re-exported by it, so every consumer of
`from ffmwiz.support.ext00b import *` still sees the full set. Monkeypatch-safe.
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
from ffmwiz.core.artifacts import *  # noqa: F401,F403
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
from ffmwiz.appio import *  # noqa: F401,F403
from ffmwiz import appio  # qualified primitives

from ffmwiz.support.ext00 import *  # sibling helpers  # noqa: F401,F403
from ffmwiz.support.ext00b import *  # noqa: E402,F401,F403  (back-import)


def step_embedded_attachments(answers: dict[str, Any]) -> None:
    streams = embedded_attachment_streams(answers)
    print()
    print(paint(f"Detected {len(streams)} embedded attachment(s):", Color.BOLD + Color.PINK))
    for idx, stream in enumerate(streams):
        print("  " + paint(embedded_attachment_display_line(stream, idx), Color.WHITE))
    if not output_supports_embedded_attachments(answers):
        appio.note(
            "Embedded font/attachment streams can only be kept reliably in MKV output here. "
            f"Current output format is {answers.get('output_ext')}."
        )
        keep = appio.ask_yes_no(
            appio.question_prompt(
                answers,
                "Change output format to MKV and keep embedded font/attachment streams?",
                "y/n; required if you want embedded MKV fonts or other attachment streams copied",
                "n",
            ),
            False,
        )
        if keep:
            answers["output_ext"] = "mkv"
        answers["keep_embedded_attachments"] = keep
        log_info(
            f"User choice: keep_embedded_attachments={keep}; "
            f"attachment_count={len(streams)}; output_ext={answers.get('output_ext')}"
        )
        return
    keep = appio.ask_yes_no(
        appio.question_prompt(
            answers,
            "Keep embedded font/attachment streams in the encode?",
            "y/n; MKV output only; copies attachment streams without re-encoding",
            "n",
        ),
        False,
    )
    answers["keep_embedded_attachments"] = keep
    log_info(
        f"User choice: keep_embedded_attachments={keep}; "
        f"attachment_count={len(streams)}; output_ext={answers.get('output_ext')}"
    )


def apply_config_extra_recipe_options(answers: dict[str, Any], config: dict[str, Any]) -> None:
    """Apply optional Mode-2 recipe answers that go beyond the core video/audio
    blocks: NVENC multipass, CPU two-pass, color range, audio sample rate,
    single-pass loudnorm, and global video/audio speed + reverse.

    Every key here is optional. Missing or empty keys keep the interactive
    defaults, so older config.env files (and config.env.example users who only
    fill the basics) keep working unchanged. This applier runs AFTER the
    video/audio/subtitle appliers so it can see the resolved codec and the
    selected audio streams. Downstream applicability checks (NVENC vs CPU,
    encoder two-pass support, known vs unknown color range) still decide
    whether a value is actually used, so setting a value is always safe."""

    # --- Color range (only meaningful when video is re-encoded; harmless if
    # the source range is already known, since resolve_color_range ignores it). ---
    if output_has_video(answers):
        color_range = config_value(config, "color_range").strip().lower()
        if color_range and color_range not in {"n", "source", "auto", "keep", ""}:
            cr_map = {
                "tv": "tv", "limited": "tv",
                "pc": "pc", "full": "pc",
                "unspecified": "unspecified", "none": "unspecified",
            }
            choice = cr_map.get(color_range)
            if choice is None:
                raise ValueError(f"Invalid color_range: {color_range} (use source/tv/pc/unspecified)")
            answers["color_range_choice"] = choice

    # --- NVENC multipass (consumed only when the resolved encoder is NVENC). ---
    multipass = config_value(config, "nvenc_multipass").strip().lower()
    if multipass:
        mp_map = {
            "disabled": "disabled", "off": "disabled", "n": "disabled", "no": "disabled", "0": "disabled",
            "qres": "qres", "quarter": "qres", "1": "qres",
            "fullres": "fullres", "full": "fullres", "2": "fullres",
        }
        mode = mp_map.get(multipass)
        if mode is None:
            raise ValueError(f"Invalid nvenc_multipass: {multipass} (use disabled/qres/fullres)")
        answers["nvenc_multipass"] = mode

    # --- CPU two-pass (consumed only when the resolved CPU encoder supports it). ---
    two_pass_value = config_value(config, "cpu_two_pass")
    if two_pass_value:
        answers["cpu_two_pass"] = parse_bool_config(two_pass_value, False)

    has_audio = bool(answers.get("audio_streams")) and bool(selected_audio_streams(answers))
    audio_codec = str(answers.get("audio_codec") or "")

    # --- Output audio sample rate (Hz). 'n'/keep keeps the source rate. ---
    if has_audio and audio_codec != "copy":
        rate_value = parse_int_config(config_value(config, "audio_sample_rate"), None, allow_n=True)
        if rate_value not in (None, ""):
            if rate_value == "n":
                answers["audio_sample_rate"] = source_audio_sample_rate(answers)
                answers["audio_sample_rate_keep"] = True
            else:
                answers["audio_sample_rate"] = int(rate_value)
                answers["audio_sample_rate_keep"] = False

    # --- Loudnorm (single-pass only). Two-pass needs a live measurement that a
    # static config cannot supply, so config drives single-pass with a target. ---
    loudnorm_value = config_value(config, "loudnorm").strip().lower()
    if loudnorm_value and loudnorm_value not in {"off", "n", "no", "false", "0", ""}:
        if loudnorm_value not in {"on", "y", "yes", "true", "1", "single", "single_pass", "singlepass"}:
            raise ValueError(f"Invalid loudnorm: {loudnorm_value} (use off or on)")
        if has_audio:
            if audio_codec == "copy":
                answers["audio_codec"] = DEFAULT_AUDIO_CODEC
                answers.setdefault("audio_bitrate_kbps", DEFAULT_AUDIO_BITRATE_KBPS)
                appio.note("LoudNorm requires re-encoding; audio codec switched from copy to AAC.")
            target = parse_float_config(
                config_value(config, "loudnorm_target_i"), LOUDNORM_DEFAULT_TARGET_I, allow_n=False
            )
            answers["loudnorm_enabled"] = True
            answers["loudnorm_mode"] = "single"
            answers["loudnorm_target_i"] = float(target if target not in (None, "n") else LOUDNORM_DEFAULT_TARGET_I)
            answers.pop("loudnorm_measured", None)

    # --- Global video speed + reverse (a single factor for the whole clip). ---
    if output_has_video(answers):
        speed_value = parse_float_config(config_value(config, "video_speed"), None, allow_n=True)
        reverse_video = parse_bool_config(config_value(config, "reverse_video"), False)
        factor = DEFAULT_SPEED_FACTOR
        if speed_value not in (None, "n"):
            factor = clamp_speed_factor(speed_value)
        if (speed_value not in (None, "n") and abs(factor - 1.0) > 1e-6) or reverse_video:
            answers["video_speed_enabled"] = True
            answers["video_speed_factor"] = factor
            answers["reverse_video"] = reverse_video

    # --- Global audio speed + reverse. 'match_video' ties audio to the video
    # speed/reverse so A/V stay in sync; an explicit number is independent. ---
    if has_audio:
        audio_speed_raw = config_value(config, "audio_speed").strip().lower()
        reverse_audio = parse_bool_config(config_value(config, "reverse_audio"), False)
        if audio_speed_raw in {"match_video", "match", "video"}:
            answers["audio_speed_from_video"] = True
        elif audio_speed_raw and audio_speed_raw not in {"n", "keep"}:
            factor = clamp_speed_factor(parse_float_config(audio_speed_raw, DEFAULT_SPEED_FACTOR, allow_n=False))
            if abs(factor - 1.0) > 1e-6 or reverse_audio:
                answers["audio_speed_enabled"] = True
                answers["audio_speed_factor"] = factor
                answers["reverse_audio"] = reverse_audio
        elif reverse_audio:
            answers["audio_speed_enabled"] = True
            answers["audio_speed_factor"] = DEFAULT_SPEED_FACTOR
            answers["reverse_audio"] = True


def colored_unified_editor_hint() -> str:
    return paint("(combines crop, cuts, speed/reverse, and audio waveform preview)", Color.HINT_YELLOW)


def metadata_prompt(answers: dict[str, Any], title: str, details: str | None = None,
                    default: str | None = None, back: str = "back=0, quit=exit") -> str:
    answers["_metadata_question_number"] = int(answers.get("_metadata_question_number", 0)) + 1
    saved = answers.get("_question_number")
    answers["_question_number"] = answers["_metadata_question_number"]
    try:
        return appio.question_prompt(answers, title, details, default, back)
    finally:
        if saved is None:
            answers.pop("_question_number", None)
        else:
            answers["_question_number"] = saved


def metadata_menu_item(number: int, label: str, default: bool = False) -> str:
    marker = f" {paint('[' + str(number) + ']', Color.GREEN)}" if default else ""
    return f"  {paint(str(number) + '.', Color.LIGHT_BLUE)} {label}{marker}"


def probe_media_json(input_path: Path, ffprobe: str | None = None) -> dict[str, Any]:
    ffprobe_bin = ffprobe or shutil.which("ffprobe") or "ffprobe"
    args = [
        ffprobe_bin,
        "-hide_banner",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        "-of",
        "json",
        str(input_path),
    ]
    log_info("Metadata Editor ffprobe command: " + command_to_powershell(args))
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        log_error("Metadata Editor ffprobe failed:\n" + (result.stderr or result.stdout or "").strip())
        raise FFprobeError(f"ffprobe could not read this file. See log file: {_log_file_text()}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        log_error("Metadata Editor ffprobe returned invalid JSON:\n" + _text_preview(result.stdout, 3000))
        raise FFprobeError(f"ffprobe returned invalid JSON. See log file: {_log_file_text()}") from exc
    if not isinstance(payload, dict):
        raise FFprobeError("ffprobe returned an unexpected JSON payload.")
    return payload


def ask_hmsf_time(
    answers: dict[str, Any],
    title: str,
    fps: float,
    duration: float | None = None,
    default: str | None = None,
    details_extra: str = "",
) -> float:
    """Prompt for a single h:m:s:frame time and return seconds."""
    base_details = f"h:m:s:frame at fps={fps:.3f}; example: 00:01:30:12"
    if duration:
        base_details += f"; clip duration ~ {format_duration(duration)}"
    if details_extra:
        base_details += f"; {details_extra}"
    while True:
        value = appio.ask_raw(appio.question_prompt(answers, title, base_details, default))
        if is_back_value(value):
            raise Back()
        if not value:
            if default is None:
                appio.error("Enter a time value in h:m:s:frame.")
                continue
            value = default
        try:
            seconds = parse_hmsf_time(value, fps)
        except ValueError as exc:
            appio.error(str(exc))
            continue
        if duration and seconds > duration + 1.0:
            appio.error(
                f"Time {seconds_to_hmsf(seconds, fps)} ({seconds:.3f}s) is past the file "
                f"duration ({format_duration(duration)}). Try again."
            )
            continue
        return seconds


def build_lossless_split_command(answers: dict[str, Any], points: list[float]) -> tuple[list[str], Path]:
    """Build a stream-copy segment command that splits ONLY the selected audio
    track into contiguous parts at the given times. This is the Audio Cut tool,
    so video/other streams are intentionally excluded and the output is an audio
    container that can hold the source audio codec without re-encoding (the user
    may pick the extension; otherwise the codec's preferred container is used).
    Because it copies (no re-encode), concatenating the parts reproduces the
    original audio stream."""
    ffmpeg = answers["ffmpeg"]
    input_path = Path(answers["input_path"])
    audio_index = int(answers.get("audio_index", 0))
    audio_streams = answers.get("audio_streams") or []
    codec = ""
    if 0 <= audio_index < len(audio_streams):
        codec = str(audio_streams[audio_index].get("codec_name", "") or "")
    chosen_ext = str(answers.get("lossless_split_ext") or lossless_audio_copy_ext(codec, input_path.suffix))
    ext = "." + chosen_ext.lower().lstrip(".")
    location = Path(answers.get("output_location") or input_path.parent)
    if location.suffix:
        out_dir = location.parent
        stem = sanitize_output_stem(location.stem)
    else:
        out_dir = location
        stem = sanitize_output_stem(answers.get("output_name_stem") or input_path.stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / f"{stem}_part%03d{ext}"
    segment_times = ",".join(f"{p:.6f}" for p in points)
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-i",
        str(input_path),
        # Audio Cut tool: map ONLY the selected audio track (lossless copy).
        "-map",
        f"0:a:{audio_index}",
        "-c",
        "copy",
        "-vn",
        "-sn",
        "-dn",
        "-f",
        "segment",
        "-segment_times",
        segment_times,
        "-reset_timestamps",
        "1",
        "-segment_start_number",
        "1",
        str(pattern),
    ]
    log_info(
        f"Lossless audio split: audio_index={audio_index}; codec={codec or 'unknown'}; "
        f"points={points}; parts={len(points) + 1}; pattern={pattern}"
    )
    return cmd, pattern


def ask_split_points_terminal(answers: dict[str, Any], duration: float) -> list[float]:
    """Prompt for comma-separated split times and return sorted split points."""
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter split times (comma-separated)",
                "format MM:SS:mmm or HH:MM:SS:mmm or seconds; milliseconds optional; "
                "a bare number uses the file's largest unit (e.g. 16 = 16 min for a minutes-long file); "
                "example: 10:00,20:00:000,25:30",
                None,
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            appio.error("Enter at least one split time, or 0 to go back.")
            continue
        try:
            points = parse_split_times_line(value, duration)
        except ValueError as exc:
            appio.error(str(exc))
            continue
        if not points:
            appio.error("No valid split times inside the file duration. Try again.")
            continue
        print(paint(format_split_points_for_summary(points, float(answers.get("fps") or 25.0)), Color.LIME))
        return points


def ass_ssa_has_embedded_fonts(path: Path) -> bool:
    if path.suffix.lower() not in {".ass", ".ssa"}:
        return False
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        log_exception(f"Could not scan ASS/SSA embedded fonts: {path}")
        return False
    in_fonts_section = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_fonts_section = line.lower() == "[fonts]"
            continue
        if in_fonts_section and line.lower().startswith("fontname:") and line.split(":", 1)[1].strip():
            return True
    return False


def step_hardsub_audio_container_policy(answers: dict[str, Any]) -> None:
    if answers.get("hardsub_audio_mode") == "none":
        answers["hardsub_audio_container_policy"] = "none"
        return
    input_ext, output_ext = hardsub_input_output_exts(answers)
    if input_ext == output_ext:
        answers["hardsub_audio_container_policy"] = "copy-anyway"
        return
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Choose audio handling for different output container",
                (
                    "1=copy audio anyway; 2=transcode selected/copied audio to AAC stereo; "
                    "3=change output format to match source container; 4=no audio"
                ),
                "2",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "2"
        if value == "1":
            appio.note("Audio copy across different containers may fail if the codec is not supported by the output container.")
            answers["hardsub_audio_container_policy"] = "copy-anyway"
            return
        if value == "2":
            answers["hardsub_audio_container_policy"] = "aac"
            answers["hardsub_audio_bitrate_kbps"] = DEFAULT_AUDIO_BITRATE_KBPS
            return
        if value == "3":
            answers["output_ext"] = input_ext
            answers["hardsub_audio_container_policy"] = "match-source-container"
            appio.note(f"HardSub output format changed to match the source container: {input_ext}")
            return
        if value == "4":
            answers["hardsub_audio_container_policy"] = "none"
            return
        appio.error("Enter 1, 2, 3, or 4.")


def preserve_artifacts_for_manual_run(answers: dict[str, Any]) -> list[Path]:
    """Keep the generated inputs a printed command still needs, and say so.

    Declining execution used to print "the command above is ready to run
    manually" and then immediately delete the files that command references --
    the generated `.ffconcat` list, chapter metadata, the merged joined-subtitle
    track. The printed command was not runnable by the time the user read it
    (R07).

    Ownership is handed back rather than the cleanup being skipped, so a later
    cleanup call cannot delete them either. The user is told exactly what was
    kept, because these are now files only they can remove.
    """
    lease = answers.get(ARTIFACT_LEASE_KEY)
    kept: list[Path] = []
    if isinstance(lease, ArtifactLease):
        for path in list(lease):
            lease.forget(path)
            kept.append(Path(path))
    list_path = answers.pop("_join_concat_list", None)
    if list_path:
        kept.append(Path(list_path))
    existing = [path for path in kept if path.exists()]
    if existing:
        appio.note(
            "Generated input(s) the command needs were KEPT so it stays runnable. "
            "Delete them yourself when you are done:")
        for path in existing:
            appio.note(f"    {path}")
        log_info(f"Manual run: preserved {len(existing)} generated input(s)")
    return existing


def cleanup_join_concat_list(answers: dict[str, Any]) -> None:
    # The merged joined-subtitle file lives in its own temp directory; it is
    # created during command build, so it has to be cleaned on the same paths
    # that clean the concat list -- success, cancel and failure alike.
    # Everything a builder leased, however many shallow copies of `answers` it
    # passed through on the way down.
    for path in release_artifacts(answers):
        log_debug(f"Removed leased temporary artifact: {path}")

    list_path = answers.pop("_join_concat_list", None)
    if not list_path:
        return
    try:
        path = Path(list_path)
        if path.exists() and path.is_file():
            path.unlink()
            log_debug(f"Removed temporary join concat list: {path}")
    except Exception:
        log_exception("Could not remove temporary join concat list")


def cleanup_encode_chapter_metadata(answers: dict[str, Any]) -> None:
    """Remove the temporary directory used for encode chapter metadata files."""
    temp_dir = answers.pop("_chapter_metadata_temp_dir", None)
    answers.pop("_chapter_metadata_input_index", None)
    if not temp_dir:
        return
    try:
        dir_path = Path(temp_dir)
        if dir_path.exists() and dir_path.is_dir():
            shutil.rmtree(dir_path, ignore_errors=True)
            log_debug(f"Removed temporary chapter metadata directory: {dir_path}")
    except Exception:
        log_exception("Could not remove temporary chapter metadata directory")


def append_join_trim_concat_filter(
    filters: list[str],
    input_label: str,
    keep_ranges: list[tuple[float, float]],
    media_type: str,
    output_label: str,
) -> str:
    if not keep_ranges:
        return input_label
    labels: list[str] = []
    trim_name = "trim" if media_type == "video" else "atrim"
    pts_filter = "setpts=PTS-STARTPTS" if media_type == "video" else "asetpts=PTS-STARTPTS"
    source_labels: list[str]
    if len(keep_ranges) > 1:
        split_name = "split" if media_type == "video" else "asplit"
        source_labels = [f"{output_label}_src{idx}" for idx in range(len(keep_ranges))]
        filters.append(f"[{input_label}]{split_name}={len(keep_ranges)}{''.join(f'[{label}]' for label in source_labels)}")
        log_info(
            f"Filter graph decision: inserted {split_name}={len(keep_ranges)} before multi-range "
            f"{trim_name} on [{input_label}] to avoid reusing one filter output."
        )
    else:
        source_labels = [input_label]
    for idx, (start, end) in enumerate(keep_ranges):
        label = f"{output_label}_{idx}"
        filters.append(f"[{source_labels[idx]}]{trim_name}=start={start:.6f}:end={end:.6f},{pts_filter}[{label}]")
        labels.append(f"[{label}]")
    if len(labels) == 1:
        return f"{output_label}_0"
    if media_type == "video":
        filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[{output_label}]")
    else:
        filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[{output_label}]")
    return output_label


__all__ = [
    'step_embedded_attachments',
    'apply_config_extra_recipe_options',
    'colored_unified_editor_hint',
    'metadata_prompt',
    'metadata_menu_item',
    'probe_media_json',
    'ask_hmsf_time',
    'build_lossless_split_command',
    'ask_split_points_terminal',
    'ass_ssa_has_embedded_fonts',
    'step_hardsub_audio_container_policy',
    'cleanup_join_concat_list',
    'preserve_artifacts_for_manual_run',
    'cleanup_encode_chapter_metadata',
    'append_join_trim_concat_filter',
]
