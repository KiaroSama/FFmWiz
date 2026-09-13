"""FFmWiz helpers (dependency level 2) — concerns: misc(17), paths(6), encode_opts(5).

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


def final_processed_duration_for_splits(answers: dict[str, Any], source_duration: float) -> float:
    duration = max(0.0, float(source_duration or 0.0))
    keep_ranges = normalize_cut_ranges(list(answers.get("cut_keep_ranges") or []), duration)
    if keep_ranges:
        duration = total_keep_duration(keep_ranges)
    if video_speed_transform_enabled(answers):
        duration = duration / max(0.001, encode_video_speed_factor(answers))
    return max(0.0, duration)


def color_range_prompt_applicable(answers: dict[str, Any]) -> bool:
    """Show the unknown-source color-range menu only when the output video is
    re-encoded (color-range metadata may be written) and the source range is
    unknown. Pure stream-copy/remux is excluded. Applicability is stable across
    back/forward navigation (it does not depend on whether a choice was made)."""
    if not output_has_video(answers):
        return False
    if source_color_range_known(answers):
        return False
    codec = str(answers.get("video_codec") or "").strip().lower()
    if codec in {"copy", "n"} and not video_filters_required(answers):
        # Pure copy/remux: preserve the source bitstream, do not invent metadata.
        return False
    return True


def folder_batch_color_range_applicable(answers: dict[str, Any]) -> bool:
    """Show the batch color-range menu only when the folder re-encodes video and
    at least one file has an unknown source color range."""
    if not output_has_video(answers):
        return False
    codec = str(answers.get("video_codec") or "").strip().lower()
    if codec in {"copy", "n"} and not video_filters_required(answers):
        return False
    return bool(folder_items_with_unknown_color_range(answers))


def output_video_bit_depth(answers: dict[str, Any]) -> int:
    """Resolved DELIVERY bit depth for the encoded output: 8 or 10.

    FFmWiz delivers 8-bit or 10-bit HEVC. Sources above 10-bit (12/14/16-bit)
    are reduced to a 10-bit Main10 encode (with a precision-reduction warning),
    because the encoders/profiles FFmWiz drives (libx265 main/main10 and
    hevc_nvenc main/main10) target 8-bit and 10-bit delivery. An explicit
    answers['force_output_bit_depth'] (8 or 10) overrides source detection, so
    an 8-bit source can be intentionally up-converted to a 10-bit encode."""
    override = answers.get("force_output_bit_depth")
    if override in (8, 10):
        return int(override)
    depth = source_video_bit_depth(answers)
    if not depth or depth <= 8:
        return 8
    # 10/12/14/16-bit sources all deliver as 10-bit Main10.
    return 10


def can_use_full_source_map_for_simple_encode(
    answers: dict[str, Any],
    audio_indices: list[int],
    audio_transform_active: bool,
    multi_cut: bool,
) -> bool:
    if not output_has_video(answers):
        return False
    if answers.get("join_input_items"):
        return False
    if multi_cut or audio_transform_active:
        return False
    if answers.get("separator_points") or video_speed_transform_enabled(answers):
        return False
    if not (
        source_metadata_keep_enabled(answers)
        and source_chapters_keep_enabled(answers)
        and source_extra_video_keep_enabled(answers)
        and source_subtitles_keep_enabled(answers)
        and source_data_keep_enabled(answers)
    ):
        return False
    if not _explicit_all_streams_selected(answers.get("audio_tracks"), len(answers.get("audio_streams") or [])):
        return False
    if not _explicit_all_streams_selected(answers.get("subtitle_tracks"), len(answers.get("subtitle_streams") or [])):
        return False
    if embedded_attachment_streams(answers) and not embedded_attachment_keep_enabled(answers):
        return False
    # `-map 0 -c copy` copies every source subtitle verbatim, so the shortcut is
    # only legal when the target container can actually hold each of them. This
    # used to check the MP4 family alone, which let subrip -> .avi and
    # subrip -> .webm through: both reach FFmpeg and die at header-write time
    # ("Not yet implemented" / "Only VP8 or VP9 or AV1 video and Vorbis or Opus
    # audio and WebVTT subtitles are supported for WebM"). Asking the shared
    # resolver covers every container instead of just one family, and routes
    # those sources through the per-stream path that transcodes or drops them
    # with a note.
    output_ext = answers.get("output_ext", "")
    for stream in (answers.get("subtitle_streams") or []):
        if subtitle_codec_for_container(output_ext, stream.get("codec_name")) != "copy":
            return False
    return True


def append_source_data_codec_options(cmd: list[str], answers: dict[str, Any]) -> None:
    if source_data_streams(answers) and source_data_keep_enabled(answers):
        cmd.extend(["-c:d", "copy"])


def append_embedded_attachment_codec_options(cmd: list[str], answers: dict[str, Any]) -> None:
    if embedded_attachment_keep_enabled(answers):
        cmd.extend(["-c:t", "copy"])


def build_audio_speed_filter(speed: float, reverse: bool) -> str:
    speed = clamp_speed_factor(speed)
    filters: list[str] = []
    if reverse:
        filters.append("areverse")
    filters.append("asetpts=PTS-STARTPTS")
    if abs(speed - 1.0) > 1e-6:
        filters.append(atempo_filter_chain(speed))
    return ",".join(filters)


def audio_speed_transform_enabled(answers: dict[str, Any]) -> bool:
    if answers.get("audio_speed_enabled"):
        return bool(
            answers.get("reverse_audio")
            or abs(clamp_speed_factor(answers.get("audio_speed_factor", DEFAULT_SPEED_FACTOR)) - 1.0) > 1e-6
        )
    if answers.get("audio_speed_from_video"):
        return bool(
            answers.get("reverse_video")
            or abs(encode_video_speed_factor(answers) - 1.0) > 1e-6
        )
    return False


def encode_audio_speed_factor(answers: dict[str, Any]) -> float:
    if answers.get("audio_speed_enabled"):
        return clamp_speed_factor(answers.get("audio_speed_factor", DEFAULT_SPEED_FACTOR))
    if answers.get("audio_speed_from_video"):
        return encode_video_speed_factor(answers)
    return DEFAULT_SPEED_FACTOR


def _make_icon_loader(root: Any) -> Callable[[str], Any]:
    """Return a cached PNG-icon loader bound to a Tk root.

    The loader reads PNGs from `assets/icons/<name>.png` and caches the
    resulting tk.PhotoImage. Returns None if the icon does not exist.
    Both GUIs share the same on-disk icon assets so they look the same.
    """
    cache: dict[str, Any] = {}

    def loader(name: str) -> Any | None:
        if name in cache:
            return cache[name]
        path = asset_path(ICON_DIR_NAME, f"{name}.png")
        if not path.exists():
            cache[name] = None
            return None
        try:
            import tkinter as tk

            cache[name] = tk.PhotoImage(file=str(path), master=root)
        except Exception:
            cache[name] = None
        return cache[name]

    return loader


def _gui_engine_selected() -> str:
    """Selected unified-editor GUI engine: 'classic' (default) or 'qml'.
    The env var FFMWIZ_GUI_ENGINE overrides the config settings.gui_engine value."""
    env = os.environ.get("FFMWIZ_GUI_ENGINE")
    if env:
        return env.strip().lower()
    value = _config_setting_for_logging("gui_engine", "classic")
    return str(value or "classic").strip().lower()


def _truncate_ansi_visible(text: str, max_visible: int) -> str:
    if _visible_len(text) <= max_visible:
        return text
    limit = max(1, max_visible - 3)
    out: list[str] = []
    visible = 0
    idx = 0
    while idx < len(text):
        match = ANSI_ESCAPE_RE.match(text, idx)
        if match:
            out.append(match.group(0))
            idx = match.end()
            continue
        # Measure with the same oracle the early-out above used. Counting a
        # CJK or fullwidth character as one column under-measured the kept
        # portion, so the result was still wider than the terminal and
        # wrapped -- the exact thing this clamp exists to prevent.
        char_width = _visible_len(text[idx])
        if visible + char_width > limit:
            break
        out.append(text[idx])
        visible += char_width
        idx += 1
    truncated = "".join(out) + "..."
    if "\x1b[" in truncated and not truncated.endswith(Color.RESET):
        truncated += Color.RESET
    return truncated


def resolve_color_range(
    answers: dict[str, Any],
    *,
    allow_compatibility_fallback: bool = False,
    workflow: str | None = None,
) -> tuple[str, str]:
    """Resolve the output color-range decision.

    Returns (resolved, source) where resolved is 'tv', 'pc', or '' (omit), and
    source is one of: 'detected', 'user assumption', 'batch user assumption',
    'user choice', 'compatibility fallback'.

    - Known source range -> use it (detected).
    - Unknown source + a stored wizard/batch choice:
        'tv'/'pc'  -> assumption (metadata only, no pixel conversion)
        'unspecified' -> omit any forced range (user choice)
    - Unknown source + no stored choice:
        Defensive default is allow_compatibility_fallback=False: raise
        ColorRangeUnresolvedError so production builders never silently assume a
        range. Only explicitly identified legacy/direct API callers may opt in
        with allow_compatibility_fallback=True, in which case the historical
        default (tv) is returned and reported explicitly as a 'compatibility
        fallback' so it never masquerades as a detected value.
    """
    stream = source_video_stream(answers) or {}
    detected = normalize_color_range(stream.get("color_range"))
    if detected in {"tv", "pc"}:
        return detected, "detected"
    choice = str(answers.get("color_range_choice") or "").strip().lower()
    if choice == "unspecified":
        return "", "user choice"
    if choice in {"tv", "pc"}:
        source = "batch user assumption" if answers.get("_color_range_from_batch") else "user assumption"
        return choice, source
    if not allow_compatibility_fallback:
        raise ColorRangeUnresolvedError(
            _color_range_unresolved_message(answers, detected, workflow)
        )
    return COLOR_RANGE, "compatibility fallback"


def terminal_path(value: str) -> Path:
    return Path(normalize_terminal_path_text(value)).expanduser()


def stream_statistics_tags_conflict_with_container(
    stream: dict[str, Any],
    fmt: dict[str, Any] | None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> bool:
    total_size = format_size_bytes_from_metadata(fmt)
    if not total_size:
        return False
    streams = stream_statistics_siblings(stream, sibling_streams)
    tagged_sizes: list[int] = []
    for item in streams:
        size = stream_tag_size_bytes(item)
        if size is None or size <= 0:
            continue
        if size > int(total_size * 1.02):
            return True
        tagged_sizes.append(size)
    if len(tagged_sizes) > 1 and sum(tagged_sizes) > int(total_size * 1.02):
        return True
    return False


def wizard_join_inputs_applicable(answers: dict[str, Any]) -> bool:
    """The wizard offers join-another for video inputs (existing) and for
    audio-only inputs (join more audio)."""
    return output_has_video(answers) or wizard_audio_join_applicable(answers)


def video_reencode_options_applicable(answers: dict[str, Any]) -> bool:
    return output_has_video(answers) and (
        not video_codec_is_copy(answers)
        or bool(answers.get("crop_enabled"))
        or video_speed_transform_enabled(answers)
    )


def audio_only_transform_prompt_applicable(answers: dict[str, Any]) -> bool:
    if output_has_video(answers):
        return False
    if not answers.get("audio_streams"):
        return False
    if "audio_tracks" not in answers:
        return True
    return bool(selected_audio_streams(answers))


def detected_fps_limit(answers: dict[str, Any]) -> tuple[float | None, str]:
    values: list[float] = []
    items = _folder_validation_items(answers)
    if items:
        for item in items:
            item_answers = _answers_for_folder_item(answers, item)
            if not item_answers.get("video_streams"):
                continue
            value = rational_to_float(item_answers["video_streams"][0].get("avg_frame_rate"))
            if value:
                values.append(value)
        return (min(values) if values else None), "lowest detected source FPS in folder"

    if not answers.get("video_streams"):
        return None, "detected source FPS"
    value = rational_to_float(answers["video_streams"][0].get("avg_frame_rate"))
    return value, "detected source FPS"


def cpu_two_pass_applicable(answers: dict[str, Any]) -> bool:
    if not output_has_video(answers) or answers.get("use_gpu"):
        return False
    if str(answers.get("video_codec") or "").strip().lower() in {"copy", "n"}:
        return False
    if answers.get("join_input_items") or answers.get("separator_points") or answers.get("split_output_paths"):
        return False
    if answers.get("cut_keep_ranges") or answers.get("video_speed_enabled") or answers.get("reverse_video"):
        return False
    video_encoder, _tag, _profile = resolve_video_encoder(answers)
    if not encoder_supports_two_pass(video_encoder):
        return False
    return True


def cpu_two_pass_unsupported_reason(answers: dict[str, Any]) -> str:
    """Why this job cannot run CPU two-pass, in the user's terms."""
    if answers.get("join_input_items"):
        return "a join builds its video through filter_complex, which pass 1 cannot analyse"
    if answers.get("separator_points") or answers.get("split_output_paths"):
        return "a Split writes several outputs from one graph, so one stats file cannot describe them"
    if answers.get("reverse_video"):
        return "reverse is encoded in bounded segments, and a stats file cannot span them"
    if answers.get("cut_keep_ranges") or answers.get("video_speed_enabled"):
        return "cuts and speed rebuild the video timeline, so pass 1 would measure a different one"
    if answers.get("use_gpu"):
        return "the GPU encoder has its own rate-control passes"
    if str(answers.get("video_codec") or "").strip().lower() in {"copy", "n"}:
        return "the video is stream-copied, so there is nothing to encode twice"
    return "this encoder or output does not support it"


def normalize_cpu_two_pass_selection(answers: dict[str, Any]) -> str:
    """Reconcile a retained `cpu_two_pass` with what this job can really do.

    The wizard hides the question for a join, a Split, cuts, speed and reverse,
    but a config file carries `cpu_two_pass=y` straight past that. Nothing
    re-checked it once the job was known, so a two-input Join really did run
    `build_cpu_two_pass_commands`: pass 1 had no `-filter_complex` and a
    hardcoded `-map 0:v:0`, and pass 2 died with "Incomplete MB-tree stats
    file" (exit 187). On the segmented-reverse and per-part Split executors the
    runner is called directly, so the same retained flag was silently downgraded
    to a single pass while the summary still reported "CPU two-pass: yes" (F11).

    Returns "" when nothing changed, otherwise the reason it was turned off.
    """
    if not answers.get("cpu_two_pass"):
        return ""
    if cpu_two_pass_applicable(answers):
        return ""
    reason = cpu_two_pass_unsupported_reason(answers)
    answers["cpu_two_pass"] = False
    effective_settings(answers)["cpu_two_pass"] = False
    # Pure by design: this layer is below appio, and returning the reason keeps
    # the notice with the caller that owns the user's screen. A second call
    # returns "" because the flag is already off, so the message appears once.
    return reason


def separator_output_path(answers: dict[str, Any], index: int) -> Path:
    base = build_separator_base_output_path(answers)
    suffix = base.suffix or ("." + str(answers.get("output_ext") or "mp4").lstrip("."))
    return unique_numbered_path(base.with_name(f"{sanitize_output_stem(base.stem)}_Part{int(index):02d}{suffix}"))


def can_use_cuda_fast_path(answers: dict[str, Any], video_encoder: str | None) -> bool:
    cut_ranges = list(answers.get("cut_keep_ranges") or [])
    if has_crop(answers) and not cuda_decoder_for_source(answers):
        return False
    # AR-preserving resize with padding cannot be done purely in CUDA; fall back
    # to the complex graph path so CPU scale+pad filters are used with NVENC encode.
    if _cuda_fast_path_needs_ar_padding(answers):
        return False
    return bool(
        answers.get("use_gpu")
        and output_has_video(answers)
        and video_encoder
        and video_encoder != "copy"
        and str(video_encoder).endswith("_nvenc")
        and not answers.get("_hardsub_mode")
        and len(cut_ranges) <= 1
        and not answers.get("_force_cpu_video_filter")
        and not answers.get("separator_points")
        and not video_speed_transform_enabled(answers)
        # Everything the CUDA path CANNOT express. `build_cuda_video_filter`
        # emits exactly one filter -- `scale_cuda` -- so a job that also asked
        # for a rotate/flip, a colour adjustment or denoise/sharpen/blur took
        # this path and lost it silently: measured on rotate, grayscale and
        # denoise, the whole video chain was `scale_cuda=...` and nothing else.
        # A fade was worse than lost -- `afade` was still emitted, so the sound
        # faded over a picture that did not.
        #
        # NOT `not video_filters_required(answers)`: that also covers crop and
        # resize, which this path DOES handle (decoder crop + scale_cuda), so
        # rejecting on it would disable the GPU path for the jobs it exists for.
        # `fps` is not here either: it is applied as the `-r:v` OUTPUT option,
        # which needs no filter and works on CUDA frames.
        and not look_filters_requested(answers)
        and not any(requested_fade_seconds(answers))
    )


def should_use_cuda_decode_for_complex_graph(
    answers: dict[str, Any],
    video_encoder: str | None,
    using_cuda_fast_path: bool,
) -> bool:
    if using_cuda_fast_path:
        return False
    if not (
        answers.get("use_gpu")
        and output_has_video(answers)
        and video_encoder
        and video_encoder != "copy"
        and str(video_encoder).endswith("_nvenc")
    ):
        return False
    cut_ranges = list(answers.get("cut_keep_ranges") or [])
    return bool(
        answers.get("_join_complex_graph")
        or answers.get("_force_cpu_video_filter")
        or len(cut_ranges) > 1
        or video_speed_transform_enabled(answers)
        or video_filters_required(answers)
    )


def split_parts_share_the_source_clock(answers: dict[str, Any]) -> bool:
    """True when a Split point means the same instant in the source and output.

    Split points are chosen on the FINAL processed timeline, but the per-part
    executor rebuilds each part as its own single-input job whose keep range is
    read straight off the SOURCE clock. That is only equivalent while nothing
    has moved the clock: a cut collapses it, a speed change scales it, reverse
    mirrors it.

    Measured on a 4 s red/blue source (F02): a 2x Split at the 1.0 s processed
    point produced parts of 0.700 s and 1.700 s instead of two ~1 s parts, and a
    reversed Split returned red then blue when the reversed timeline starts
    blue. When this returns False the caller must execute the already-built
    multi-output graph, which is planned on the processed clock.
    """
    if answers.get("cut_keep_ranges"):
        return False
    if video_speed_transform_enabled(answers):
        return False
    if answers.get("reverse_video"):
        return False
    return True


def reverse_video_needs_segmented_main_encode(answers: dict[str, Any]) -> bool:
    """Should this reverse run through the bounded segmented executor?

    NOT for a join. The segmented executor rebuilds every segment with the
    single-input builder, which reads only answers["input_path"], so a joined
    job silently reversed input 1 alone: a red+blue 2+2 s join came back as
    2.12 s of red with input 2 missing entirely (R01). The join command already
    reverses the complete joined timeline correctly -- verified 4.04 s, blue
    then red -- so a join must execute its own command instead.

    This mirrors the guard build_separator_job_specs already applies for the
    same reason, and lives here rather than at the call sites so every caller
    is covered.
    """
    if answers.get("join_input_items"):
        return False
    return bool(output_has_video(answers) and video_speed_transform_enabled(answers) and answers.get("reverse_video"))


def parse_crop_config_value(answers: dict[str, Any], value: str, config: dict[str, Any]) -> None:
    lowered = value.lower().strip()
    if not lowered or lowered in {"n", "no", "false", "0", "off"}:
        answers["crop_enabled"] = False
        answers["crop_values_inline"] = False
        for key in ("crop_top", "crop_left", "crop_right", "crop_bottom"):
            answers.pop(key, None)
        return

    if lowered in {"y", "yes", "true", "1", "on"}:
        top = int(parse_int_config(config_value(config, "crop_top"), 0, allow_n=False) or 0)
        left = int(parse_int_config(config_value(config, "crop_left"), 0, allow_n=False) or 0)
        right = int(parse_int_config(config_value(config, "crop_right"), 0, allow_n=False) or 0)
        bottom = int(parse_int_config(config_value(config, "crop_bottom"), 0, allow_n=False) or 0)
    else:
        pieces = [piece.strip() for piece in value.split(",")]
        if len(pieces) != 4 or any(not re.fullmatch(r"\d+", piece) for piece in pieces):
            raise ValueError("crop must be n, y, or top,left,right,bottom")
        top, left, right, bottom = [int(piece) for piece in pieces]

    message = crop_margins_validation_message(answers, top, left, right, bottom)
    if message:
        raise ValueError(message)
    answers["crop_enabled"] = any((top, left, right, bottom))
    answers["crop_values_inline"] = True
    answers["crop_top"] = top
    answers["crop_left"] = left
    answers["crop_right"] = right
    answers["crop_bottom"] = bottom


def apply_config_subtitle_options(answers: dict[str, Any], config: dict[str, Any]) -> None:
    if not output_has_video(answers) or not answers.get("subtitle_streams"):
        return
    if not source_subtitles_keep_enabled(answers):
        answers["subtitle_tracks"] = []
        return
    answers["subtitle_tracks"] = parse_selection_config(
        config_value(config, "subtitle_tracks"),
        len(answers["subtitle_streams"]),
        [0],
        allow_none=True,
    )


def apply_config_source_extra_options(answers: dict[str, Any], config: dict[str, Any]) -> None:
    if not output_has_video(answers):
        return
    keep = parse_bool_config(config_value(config, "keep_source_metadata", "y"), True)
    answers["keep_source_metadata"] = keep
    answers["keep_source_chapters"] = keep
    answers["keep_source_subtitles"] = keep
    answers["keep_source_data_streams"] = keep
    answers["keep_source_extra_video_streams"] = keep
    if not keep:
        answers["subtitle_tracks"] = []
        answers["keep_embedded_attachments"] = False
        return
    keep_attachments = parse_bool_config(config_value(config, "keep_embedded_attachments", "n"), False)
    answers["keep_embedded_attachments"] = bool(
        keep_attachments and embedded_attachment_streams(answers) and output_supports_embedded_attachments(answers)
    )


def seed_unified_editor_from_config(answers: dict[str, Any], config: dict[str, Any]) -> None:
    """Seed the unified graphical editor's initial state from config so that any
    crop margins, speed, reverse, or audio settings provided in config.env are
    visible (and pre-applied) when the editor opens in the config wizard.

    Crop margins are read from answers['crop_*'] directly by the editor; here we
    seed the editor-specific keys for speed / reverse / include-audio."""
    if config_value(config, "video_speed").strip() or config_value(config, "reverse_video").strip():
        answers["_unified_video_speed"] = encode_video_speed_factor(answers)
        answers["_unified_reverse_video"] = bool(answers.get("reverse_video"))
    has_audio = bool(answers.get("audio_streams")) and bool(selected_audio_streams(answers))
    answers["_unified_include_audio"] = has_audio


def build_cpu_two_pass_commands(cmd: list[str], answers: dict[str, Any]) -> tuple[list[str], list[str], Path]:
    if len(cmd) < 2:
        raise ValueError("FFmpeg command is too short for two-pass encoding.")
    passlog = cpu_two_pass_log_prefix(answers)
    input_end = ffmpeg_input_section_end(cmd)
    input_args = list(cmd[:input_end])
    output_args = list(cmd[input_end:-1])
    video_args = cpu_two_pass_video_output_args(output_args)
    first = (
        input_args
        + ["-map", "0:v:0"]
        + video_args
        + ["-pass", "1", "-passlogfile", str(passlog), "-an", "-sn", "-dn", "-f", "null", os.devnull]
    )
    second = list(cmd[:-1]) + ["-pass", "2", "-passlogfile", str(passlog), str(cmd[-1])]
    return first, second, passlog


def metadata_stream_relative_index(probe_json: dict[str, Any], stream: dict[str, Any]) -> int:
    spec = metadata_stream_spec(probe_json, stream)
    try:
        return int(spec.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return 0


def choose_add_files_output_path(input_path: Path, extra_items: list[dict[str, Any]]) -> Path:
    _ = extra_items
    output_suffix = input_path.suffix or ".mkv"
    output_path = input_path.with_name(
        f"{sanitize_output_stem(input_path.stem)}{ADD_FILES_OUTPUT_SUFFIX}{output_suffix}"
    )
    return resolve_output_collision(unique_numbered_path(output_path), input_path, ADD_FILES_OUTPUT_SUFFIX)


def track_manager_output_path(input_path: Path) -> Path:
    suffix = input_path.suffix or ".mkv"
    candidate = input_path.with_name(f"{sanitize_output_stem(input_path.stem)}_TrackEdit{suffix}")
    return resolve_output_collision(candidate, input_path, "_TrackEdit")


def _track_manager_loudnorm_summary(answers: dict[str, Any]) -> str:
    mode = loudnorm_mode(answers)
    if mode == "off" or not loudnorm_transform_enabled(answers):
        return "(off)"
    target = loudnorm_number(float(answers.get("loudnorm_target_i", LOUDNORM_DEFAULT_TARGET_I)))
    label = "two-pass" if mode == "two_pass" else "single-pass"
    return f"{label}, target I={target} LUFS"


def choose_hardsub_output_path(input_path: Path, output_ext: str, output_location: Path, output_name_stem: str | None = None,
                               output_location_is_dir: bool = False) -> Path:
    if output_name_stem:
        candidate = output_location / f"{sanitize_output_stem(output_name_stem)}.{output_ext}"
    elif output_location_names_a_file(output_location, output_location_is_dir):
        candidate = output_location.with_suffix("." + output_ext)
    else:
        candidate = output_location / f"{sanitize_output_stem(input_path.stem)}{HARDSUB_OUTPUT_SUFFIX}.{output_ext}"
    return resolve_output_collision(unique_numbered_path(candidate), input_path, HARDSUB_OUTPUT_SUFFIX)


def join_copy_compatibility(items: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    if len(items) < 2:
        return False, ["at least two video inputs are required"]
    reasons: list[str] = []
    first_ext = items[0]["path"].suffix.lower()
    first_sig = join_stream_signature(items[0])
    for item in items[1:]:
        if item["path"].suffix.lower() != first_ext:
            reasons.append("input containers/extensions differ")
            break
        if join_stream_signature(item) != first_sig:
            reasons.append(
                "stream layout, codec, resolution, fps, pixel format, aspect ratio, "
                "codec profile/level, or audio layout differs"
            )
            break
    return not reasons, reasons


def join_video_frame_rates(items: list[dict[str, Any]]) -> list[float]:
    """Per-item first-video frame rates (only items that actually have video)."""
    rates: list[float] = []
    for item in items:
        rate = join_first_video_frame_rate(item)
        if rate:
            rates.append(rate)
    return rates


def join_signature_without_fps(item: dict[str, Any]) -> list[tuple[Any, ...]]:
    """join_stream_signature with the video frame-rate element removed, so two
    inputs that differ ONLY in frame rate compare equal. Used to decide whether
    a VFR join can still use the concat demuxer (stream copy), which preserves
    each segment's native frame rate and produces a genuinely variable-fps file
    without re-encoding."""
    result: list[tuple[Any, ...]] = []
    for entry in join_stream_signature(item):
        if entry and entry[0] == "video":
            # video tuple: ("video", codec, w, h, fps, pix_fmt, sar, profile,
            # level, field_order) -- drop only the fps element at index 4.
            result.append(entry[:4] + entry[5:])
        else:
            result.append(entry)
    return result


def join_default_output_path(answers: dict[str, Any], first_input: Path) -> Path:
    output_location = Path(answers.get("output_location") or first_input.parent)
    suffix = first_input.suffix or ".mkv"
    if answers.get("output_name_stem"):
        candidate = output_location / f"{sanitize_output_stem(answers['output_name_stem'])}{suffix}"
    elif output_location_names_a_file(output_location, bool(answers.get("output_location_is_dir"))):
        candidate = output_location.with_suffix(suffix)
    else:
        candidate = output_location / f"{sanitize_output_stem(first_input.stem)}_Joined{suffix}"
    return resolve_output_collision(candidate, first_input, "_Joined")


__all__ = [
    'final_processed_duration_for_splits',
    'color_range_prompt_applicable',
    'folder_batch_color_range_applicable',
    'output_video_bit_depth',
    'can_use_full_source_map_for_simple_encode',
    'append_source_data_codec_options',
    'append_embedded_attachment_codec_options',
    'build_audio_speed_filter',
    'audio_speed_transform_enabled',
    'encode_audio_speed_factor',
    '_make_icon_loader',
    '_gui_engine_selected',
    '_truncate_ansi_visible',
    'resolve_color_range',
    'terminal_path',
    'stream_statistics_tags_conflict_with_container',
    'wizard_join_inputs_applicable',
    'video_reencode_options_applicable',
    'audio_only_transform_prompt_applicable',
    'detected_fps_limit',
    'cpu_two_pass_applicable',
    'cpu_two_pass_unsupported_reason',
    'normalize_cpu_two_pass_selection',
    'separator_output_path',
    'can_use_cuda_fast_path',
    'should_use_cuda_decode_for_complex_graph',
    'reverse_video_needs_segmented_main_encode',
    'split_parts_share_the_source_clock',
    'parse_crop_config_value',
    'apply_config_subtitle_options',
    'apply_config_source_extra_options',
    'seed_unified_editor_from_config',
    'build_cpu_two_pass_commands',
    'metadata_stream_relative_index',
    'choose_add_files_output_path',
    'track_manager_output_path',
    '_track_manager_loudnorm_summary',
    'choose_hardsub_output_path',
    'join_copy_compatibility',
    'join_video_frame_rates',
    'join_signature_without_fps',
    'join_default_output_path',
]


# L02_media_info holds the probe-description half of this module,
# split off by responsibility. Bound twice on purpose: the plain name keeps
# the sibling addressable as a module, and the `_` alias is what
# tests/test_module_reference_hygiene reads to find re-export pairs. It
# imports only lower tiers and never reaches back up into here.
from ffmwiz.support import L02_media_info as _L02_media_info  # noqa: E402
from ffmwiz.support import L02_media_info  # noqa: E402,F401
from ffmwiz.support.L02_media_info import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_L02_media_info.__all__)
