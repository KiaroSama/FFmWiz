"""FFmWiz wizard cluster (extracted from FFmWiz.py, method الف)."""
from __future__ import annotations
from dataclasses import dataclass, field

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
import queue
import threading
import concurrent.futures
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from ffmwiz.core.constants import *  # noqa: F401,F403
from ffmwiz import wizard_look  # noqa: F401  (module, so a patch is seen)
from ffmwiz import wizard_quick  # noqa: F401  (module, so a patch is seen)
from ffmwiz import wizard_raw  # noqa: F401  (module, so a patch is seen)
from ffmwiz import wizard_composite  # noqa: F401  (module, so a patch is seen)
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
from ffmwiz.support.ext00 import *  # noqa: F401,F403
from ffmwiz.support.L01_subtitles import (  # noqa: F401
    any_join_subtitles,
    join_subtitle_streams_view,
    with_join_subtitle_view,
)
from ffmwiz.support.ext01 import *  # noqa: F401,F403
from ffmwiz.support.ext02 import *  # noqa: F401,F403
from ffmwiz.support.ext03 import *  # noqa: F401,F403
from ffmwiz.support.ext04 import *  # noqa: F401,F403
from ffmwiz.support.ext05 import *  # noqa: F401,F403
from ffmwiz.support.ext06 import *  # noqa: F401,F403
from ffmwiz.support.ext07 import *  # noqa: F401,F403
from ffmwiz.support.ext08 import *  # noqa: F401,F403
from ffmwiz.support.ext09 import *  # noqa: F401,F403
from ffmwiz.support.ext10 import *  # noqa: F401,F403
from ffmwiz.support.ext11 import *  # noqa: F401,F403
from ffmwiz.support.ext12 import *  # noqa: F401,F403
from ffmwiz.appio import *  # noqa: F401,F403
from ffmwiz import appio  # noqa: F401
from ffmwiz.guibridge import *  # noqa: F401,F403
from ffmwiz.metadata import *  # noqa: F401,F403
from ffmwiz import metadata  # noqa: F401
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.trackmanager import *  # noqa: F401,F403
from ffmwiz import trackmanager  # noqa: F401

# The facade star-import was deleted. It made this module unimportable on its
# own: the facade ends with `__all__ += <this module>.__all__` and reached that
# line while this module was still on its first statements. Names that live in
# a sibling are now addressed through that sibling's module object, imported at
# the BOTTOM of this file where nothing partial is read.


def run_wizard(answers: dict[str, Any], config: dict[str, Any] | None = None) -> None:
    # config is None for Mode 1 (full wizard). In Mode 2 it is the parsed
    # config.env: every question whose config value is non-empty is auto-applied
    # and SKIPPED; empty ones are asked (just like Mode 1). The unified graphical
    # editor and the final "start now?" question are ALWAYS asked.
    config_mode = config is not None

    def cfg_has(key: str) -> bool:
        return config_mode and config_value(config, key).strip() != ""

    # step name -> config key(s). A step is skipped only when ALL its keys are
    # provided. Steps not listed (join_inputs, unified_video_editor, cuts,
    # audio_cut, start_now) are NEVER auto-skipped, so they are always asked.
    skip_map: dict[str, tuple[str, ...]] = {
        "input_path": ("input_path",),
        "output_location": ("output_path",),
        "output_format": ("output_format",),
        "video_codec": ("video_codec",),
        "use_gpu": ("use_gpu",),
        "crop_enabled": ("crop",),
        "crop_top": ("crop",), "crop_left": ("crop",), "crop_right": ("crop",), "crop_bottom": ("crop",),
        "video_look": ("video_look",),
        "video_quick": ("video_quick",),
        "audio_volume": ("audio_volume",),
        "raw_ffmpeg_args": ("raw_ffmpeg_args",),
        "video_bitrate": ("video_bitrate_kbps",),
        "nvenc_multipass": ("nvenc_multipass",),
        "cpu_two_pass": ("cpu_two_pass",),
        "resolution": ("resolution",),
        "fps": ("fps",),
        "video_speed_reverse": ("video_speed", "reverse_video"),
        "audio_tracks": ("audio_tracks",),
        "loudnorm": ("loudnorm",),
        "audio_speed_reverse": ("audio_speed", "reverse_audio"),
        "audio_codec": ("audio_codec",),
        "audio_bitrate": ("audio_bitrate_kbps",),
        "audio_sample_rate": ("audio_sample_rate",),
        "source_extras": ("keep_source_metadata",),
        "subtitle_tracks": ("subtitle_tracks",),
        "color_range": ("color_range",),
    }

    applied = {"done": False}

    def ensure_config_applied() -> None:
        if not config_mode or applied["done"]:
            return
        if not answers.get("input_path"):
            return
        if not (answers.get("video_streams") or answers.get("audio_streams")):
            return
        apply_config_settings_after_input(
            answers, config, skip_crop=not cfg_has("crop"), force_video_options=True
        )
        seed_unified_editor_from_config(answers, config)
        answers["_config_mode"] = True
        applied["done"] = True

    def is_config_skipped(step: "Step") -> bool:
        if not config_mode or not applied["done"]:
            return False
        keys = skip_map.get(step.name)
        if not keys:
            return False
        return all(cfg_has(k) for k in keys)

    steps = [
        wizard_base.Step("input_path", lambda a: True, wizard_steps.step_input_path),
        wizard_base.Step("join_inputs", wizard_join_inputs_applicable, wizard_steps.step_join_additional_inputs_for_encode),
        wizard_base.Step("output_location", lambda a: True, wizard_steps.step_output_location),
        wizard_base.Step("output_format", lambda a: True, wizard_steps.step_output_format),
        wizard_base.Step("video_codec", output_has_video, wizard_steps.step_video_codec),
        wizard_base.Step("use_gpu", output_has_video, wizard_steps.step_use_gpu),
        wizard_base.Step("unified_video_editor", output_has_video, wizard_steps.step_unified_video_editor_for_encode),
        wizard_base.Step("crop_enabled", lambda a: output_has_video(a) and not a.get("_unified_video_editor_declined"), wizard_steps.step_crop_enabled),
        wizard_base.Step("crop_top", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_top),
        wizard_base.Step("crop_left", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_left),
        wizard_base.Step("crop_right", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_right),
        wizard_base.Step("crop_bottom", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_bottom),
        # Filters need a re-encode, so this rides the same gate as the
        # bitrate and resolution questions rather than crop's. In config
        # mode it is skipped entirely unless the config names it: every
        # one of these filters is off by default, so a config that does
        # not mention them is asking for none -- and a non-interactive
        # run has no one to answer the prompt. The unified editor path is
        # contractually prompt-free, so it is skipped there too.
        #
        # NOT `_unified_video_editor_declined`, which the crop questions above
        # do carry. Copying their gate is what made this question, `video_quick`
        # and `video_composite` unreachable: the editor step always sets ONE of
        # the two keys, so `not used and not declined` was false on both
        # branches and none of the three prompts could ever run. Declining the
        # crop questions is meaningful -- declining the editor zeroes the crop
        # margins -- but declining a GRAPHICAL editor says nothing about
        # denoise, a GIF or a watermark, none of which that editor can set.
        wizard_base.Step("video_look",
                    lambda a: (video_reencode_options_applicable(a)
                               and not a.get("_unified_video_editor_used")
                               and (not config_mode or cfg_has("video_look"))),
                    wizard_look.step_video_look),
        # Straight after the picture filters, on the same gate. A quick output
        # changes what the job PRODUCES -- a GIF, a boomerang, a still frame --
        # so it has to be answered before the questions describing the rate and
        # the size, which it reads for its own defaults.
        #
        # Not on a join, for the reason `cpu_two_pass_applicable` excludes one:
        # `step_start_now` sends a join to `build_join_encode_command`, which
        # knows nothing about the quick-output plan. A GIF asked for there
        # forced `output_ext=gif` and then built an ordinary `-c:v libx264
        # -c:a aac` encode into a .gif, which the muxer refuses outright --
        # and `.gif` is in neither container-compatibility table, so no
        # fallback caught it either.
        wizard_base.Step("video_quick",
                    lambda a: (video_reencode_options_applicable(a)
                               and not a.get("join_input_items")
                               and not a.get("_unified_video_editor_used")
                               and (not config_mode or cfg_has("video_quick"))),
                    wizard_quick.step_quick_output),
        # Compositing is a filter graph too, so it rides the same re-encode
        # gate -- widened, because an audio-only output has no picture to
        # composite but can still take a music bed under its own track.
        # Prompt-only, even on a config run: `_ask_partner` is a `while True`
        # loop that recovers from every failure (missing file, same as the
        # main input, no usable streams) by re-asking, which a non-interactive
        # run has nobody to answer. There is no config_mode/cfg_has clause
        # here on purpose, and no `video_composite` config key either -- which
        # is why the `_unified_video_editor_declined` clause mattered most
        # here: prompt-only plus a prompt that could never fire left the whole
        # feature with no entry point at all for a video output.
        # Not on a join either, and for the same reason: `step_start_now`
        # dispatches a join BEFORE it looks at `composite_mode`, so the answer
        # was collected, summarised and then never reached a builder at all.
        wizard_base.Step("video_composite",
                    lambda a: ((video_reencode_options_applicable(a)
                                or bool(a.get("audio_streams")))
                               and not a.get("join_input_items")
                               and not a.get("_unified_video_editor_used")),
                    wizard_composite.step_video_composite),
        # Volume rides the AUDIO gate, not the video one: an audio-only
        # output has no picture but still has a level to set. Both follow
        # `video_look` in skipping the prompt on a config run that did not
        # ask for them -- a non-interactive run has no one to answer.
        wizard_base.Step("audio_volume",
                    lambda a: (bool(a.get("audio_streams"))
                               and (not config_mode or cfg_has("audio_volume"))),
                    wizard_raw.step_audio_volume),
        # Last question before the summary: it is the escape hatch, so the
        # user should have seen every other answer before reaching for it.
        wizard_base.Step("raw_ffmpeg_args",
                    lambda a: (not config_mode or cfg_has("raw_ffmpeg_args")),
                    wizard_raw.step_raw_ffmpeg_args),
        wizard_base.Step("video_bitrate", video_reencode_options_applicable, wizard_steps.step_video_bitrate),
        wizard_base.Step("nvenc_multipass", wizard_base.nvenc_multipass_prompt_applicable, wizard_base.step_nvenc_multipass),
        wizard_base.Step("cpu_two_pass", cpu_two_pass_applicable, wizard_steps.step_cpu_two_pass),
        wizard_base.Step("resolution", video_reencode_options_applicable, wizard_steps.step_resolution),
        wizard_base.Step("fps", video_reencode_options_applicable, wizard_steps.step_fps),
        wizard_base.Step("video_speed_reverse", lambda a: output_has_video(a) and not a.get("_unified_video_editor_used") and not a.get("_unified_video_editor_declined"), wizard_flow_b.step_video_speed_reverse_for_encode),
        wizard_base.Step("cuts", lambda a: video_reencode_options_applicable(a) and not a.get("_unified_video_editor_used") and not a.get("_unified_video_editor_declined"), wizard_flow_b.step_cuts),
        # The track question itself gets the same treatment: it used to be
        # gated by and sized from input 1's list, so a track only a LATER input
        # carries could not be selected at all -- the prompt rejected the index
        # as out of range and the builder then reported the track as missing
        # from the output (F05).
        wizard_base.Step("audio_tracks", lambda a: any_join_audio(a), with_join_audio_view(step_audio_tracks)),
        # any_join_audio, not a.get("audio_streams"): a join whose FIRST input is
        # silent still produces audio, and gating on input 1 hid every question
        # that configures it (R02). with_join_audio_view lends the joined track
        # list to the steps whose bodies index input 1's list directly.
        wizard_base.Step("loudnorm", lambda a: any_join_audio(a) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), with_join_audio_view(step_loudnorm)),
        wizard_base.Step("audio_cut", audio_only_transform_prompt_applicable, wizard_b.step_audio_cut_for_encode),
        wizard_base.Step("audio_speed_reverse", audio_only_transform_prompt_applicable, wizard_flow_b.step_audio_speed_reverse_for_encode),
        wizard_base.Step("audio_codec", lambda a: any_join_audio(a) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), with_join_audio_view(step_audio_codec)),
        wizard_base.Step("audio_bitrate", lambda a: any_join_audio(a) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy" and audio_codec_uses_bitrate(str(a.get("audio_codec") or default_audio_codec_for_ext(a.get("output_ext", "")))), with_join_audio_view(step_audio_bitrate)),
        wizard_base.Step("audio_sample_rate", lambda a: any_join_audio(a) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy", with_join_audio_view(step_audio_sample_rate)),
        wizard_base.Step("source_extras", source_extra_policy_applicable, step_source_extra_policy),
        # Runs after the extras answer AND after speed/cuts/Split are known, so
        # the drop is stated and confirmed while the edit can still change --
        # not logged silently and discovered in the output (R11).
        wizard_base.Step("source_extra_outcomes", lambda a: bool(source_extra_stream_outcome_notes(a)), confirm_source_extra_stream_outcomes),
        # any_join_subtitles, not a.get("subtitle_streams"): a join whose FIRST
        # input has no subtitles still carries the later inputs' tracks, so
        # gating on input 1 meant the question was skipped and ALL of them
        # were kept without asking.
        wizard_base.Step("subtitle_tracks", lambda a: output_has_video(a) and source_subtitles_keep_enabled(a) and any_join_subtitles(a), with_join_subtitle_view(step_subtitle_tracks)),
        wizard_base.Step("color_range", color_range_prompt_applicable, wizard_base.step_color_range),
        wizard_base.Step("start_now", lambda a: True, wizard_b.step_start_now),
    ]

    def runnable(pos: int) -> bool:
        return steps[pos].applicable(answers) and not is_config_skipped(steps[pos])

    def next_index(start: int) -> int:
        idx = start
        while idx < len(steps) and not runnable(idx):
            idx += 1
        return idx

    def prev_index(start: int) -> int:
        idx = start
        while idx > 0 and (
            not runnable(idx)
            or is_auto_unified_crop_step(idx)
            or wizard_base.step_is_auto_back_skip(steps[idx], answers)
        ):
            idx -= 1
        return max(0, idx)

    def is_auto_unified_crop_step(pos: int) -> bool:
        return bool(
            answers.get("_unified_video_editor_used")
            and steps[pos].name in {"crop_enabled", "crop_top", "crop_left", "crop_right", "crop_bottom"}
        )

    def question_number(current: int) -> int:
        count = 0
        join_pos = next((pos for pos, step in enumerate(steps) if step.name == "join_inputs"), -1)
        for pos in range(current + 1):
            if runnable(pos) and not is_auto_unified_crop_step(pos):
                count += 1
        extra = int(answers.get("_join_question_extra", 0) or 0) if join_pos >= 0 and current >= join_pos else 0
        return answers.get("_question_offset", 0) + count + extra

    # In config mode, pre-load the input file from config (so the input question
    # is skipped) and apply every provided setting before the first question.
    if config_mode and cfg_has("input_path"):
        cfg_input = terminal_path(config_value(config, "input_path"))
        if not cfg_input.exists() or not cfg_input.is_file():
            fail(f"input_path in config.env does not exist: {cfg_input}")
        load_input_metadata(answers, cfg_input)
        trackmanager.print_source_info(answers)
    ensure_config_applied()

    idx = next_index(0)
    while idx < len(steps):
        try:
            answers["_question_number"] = question_number(idx)
            steps[idx].run(answers)
            apply_unified_video_editor_answers(answers)
            ensure_config_applied()
            idx = next_index(idx + 1)
        except Back:
            if idx == 0:
                raise
            idx = prev_index(idx - 1)


__all__ = [
    'run_wizard',
]


# wizard_flow_b holds an overflow slice of this module (split for file size).
# Bound twice on purpose: run_wizard addresses it through the module object so a
# test patching the DEFINING module is seen, and the `_` alias is what
# tests/test_module_reference_hygiene reads to find re-export pairs.
from ffmwiz import wizard_flow_b as _wizard_flow_b  # noqa: E402
from ffmwiz import wizard_flow_b  # noqa: E402,F401
from ffmwiz.wizard_flow_b import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_wizard_flow_b.__all__)

# The step prompts run_wizard schedules, and Step itself, live in these leaves.
# None of them imports this module or wizard, so the wizard tier stays acyclic.
from ffmwiz import wizard_base  # noqa: E402,F401
from ffmwiz import wizard_steps  # noqa: E402,F401
from ffmwiz import wizard_b  # noqa: E402,F401
