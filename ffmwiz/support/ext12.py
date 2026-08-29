"""FFmWiz extracted helper tier ext12 (post-services layer).

Imports core, support, and top-level ffmwiz modules; acyclic.
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
import queue
import threading
import concurrent.futures
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from ffmwiz.core.constants import *  # noqa: F401,F403
from ffmwiz import wizard_raw  # noqa: F401  (module, so a patch is seen)
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
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.support.ext04 import *  # noqa: F401,F403
from ffmwiz.support.ext05 import *  # noqa: F401,F403
from ffmwiz.support.ext06 import *  # noqa: F401,F403
from ffmwiz.support.ext07 import *  # noqa: F401,F403
from ffmwiz.support.ext08 import *  # noqa: F401,F403
from ffmwiz.support.ext09 import *  # noqa: F401,F403
from ffmwiz.support.ext10 import *  # noqa: F401,F403
from ffmwiz.support.ext11 import *  # noqa: F401,F403


def apply_config_settings_after_input(
    answers: dict[str, Any],
    config: dict[str, Any],
    *,
    skip_crop: bool = False,
    force_video_options: bool = False,
) -> None:
    """Apply every config.env setting to `answers` once the input file's metadata
    is already loaded. Shared by Mode 2 (config wizard) and the legacy loader."""
    answers["detect_duplicate_audio"] = parse_bool_config(config_value(config, "detect_duplicate_audio"), True)
    apply_output_location_value(answers, config_value(config, "output_path"))

    input_path = Path(str(answers.get("input_path") or ""))
    input_ext = input_path.suffix.lstrip(".") or "mp4"
    default_ext = "mp4" if answers.get("video_streams") else "mp3"
    output_format = config_value(config, "output_format") or default_ext
    answers["output_ext"] = normalize_format(output_format, input_ext)

    if output_has_video(answers):
        apply_config_video_options(answers, config, skip_crop=skip_crop, force_video_options=force_video_options)

    apply_config_audio_options(answers, config)
    apply_config_source_extra_options(answers, config)
    apply_config_subtitle_options(answers, config)
    apply_config_extra_recipe_options(answers, config)
    apply_config_raw_options(answers, config)


def apply_config_raw_options(answers: dict[str, Any], config: dict[str, Any]) -> None:
    """Read `raw_ffmpeg_args` from config.env.

    The interactive step asks for it on every job, video or audio-only alike
    -- its Step gate in `wizard_flow` carries no applicability condition at
    all. That is why this is its own applier rather than living inside
    `apply_config_video_options` (skipped whenever `output_has_video` is
    False, e.g. converting to mp3) or `apply_config_audio_options` (skipped
    whenever the source has no `audio_streams`, e.g. a silent video): either
    of those would silently drop the key for the job type it does not cover.
    """
    answers.pop("raw_ffmpeg_args", None)
    raw_args = (config_value(config, "raw_ffmpeg_args") or "").strip()
    if not raw_args or raw_args.lower() in {"n", "no"}:
        return
    try:
        parsed = wizard_raw.parse_raw_arguments(raw_args)
    except ValueError as error:
        fail(f"raw_ffmpeg_args in config.env is not valid: {error}")
    if parsed:
        answers["raw_ffmpeg_args"] = parsed


__all__ = [
    'apply_config_settings_after_input',
    'apply_config_raw_options',
]
