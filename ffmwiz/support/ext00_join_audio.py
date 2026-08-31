"""The joined input list, and the LOGICAL audio tracks it carries.

Split out of `ext00` as its own responsibility. A joined audio track is not one
stream: it is the i-th audio stream of each input in turn, spliced by `concat`,
with silence synthesised for the inputs that do not have it. Everything here
exists to describe the joined program that way rather than from input 1's
stream list alone -- reading input 1 is what hid every audio question behind a
silent first input, and judged a later input's track by the primary file's
caches.

Re-exported by `ext00`, so every consumer of `from ffmwiz.support.ext00 import
*` still sees the full set. Imports only lower tiers; it never reaches back up
into `ext00`.
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


def item_audio_streams(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Every audio stream of one join input, in file order.

    Mirrors `item_subtitle_streams`: some paths assemble a join item without an
    `audio_streams` key -- its audio only appears inside `streams` -- and
    reading the key alone made such an input look silent.
    """
    streams = item.get("audio_streams")
    if streams is None:
        streams = [stream for stream in (item.get("streams") or [])
                   if str(stream.get("codec_type") or "").lower() == "audio"]
    return list(streams or [])


def _format_duration_seconds(fmt: dict[str, Any] | None) -> float:
    try:
        return float((fmt or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def join_items_from_answers(answers: dict[str, Any]) -> list[dict[str, Any]]:
    """The COMPLETE join item list, input 1 rebuilt from the top-level answers.

    `answers["join_input_items"]` holds inputs 2..N only; input 1 lives in the
    ordinary answer keys, so anything that needs the whole list has to
    reassemble it. Doing that by hand in more than one place is how input 1's
    `subtitle_streams` came to be missing from one of the copies (R04).

    Returns [] when this is not a join.
    """
    extra = list(answers.get("join_input_items") or [])
    if not extra:
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
        "subtitle_streams": answers.get("subtitle_streams") or [],
        "data_streams": answers.get("data_streams") or [],
        # The container duration, which stays the answer only for an audio-only
        # join item: with no picture there is nothing else to measure.
        "duration": _format_duration_seconds(answers.get("format")),
    }
    # Every other item's `duration` is its PICTURE span, because that is what `concat`
    # actually splices and what every offset downstream measures. `format.duration`
    # is the container's, and a trailing subtitle or audio pad inflates it: a
    # 2.000 s picture in a 3.000 s MKV pushed the next input a second late (B07).
    # Copied, never written back -- the caller's stored items stay untouched.
    return [{**item, "duration": join_item_picture_span(item)}
            for item in (primary, *extra)]


def join_audio_segment_flags(answers: dict[str, Any]) -> list[bool]:
    """Audio presence per joined input, input 1 first."""
    flags = [bool(answers.get("audio_streams"))]
    flags.extend(bool(item_audio_streams(item)) for item in (answers.get("join_input_items") or []))
    return flags


def join_audio_track_count(items: list[dict[str, Any]]) -> int:
    """How many LOGICAL audio tracks the joined set offers.

    Taken across ALL inputs, not input 1. Sizing the track question from input
    1 is what made a track only a later input carries impossible to select, and
    then reported it as "NOT in the joined output" (F05).
    """
    return max((len(item_audio_streams(item)) for item in items), default=0)


def join_audio_streams_view(answers: dict[str, Any]) -> list[dict[str, Any]]:
    """One representative stream per LOGICAL joined audio track.

    The exact analogue of `join_subtitle_streams_view`. Track i is described by
    the FIRST input that actually has an i-th audio stream, so the track,
    codec, bitrate, sample-rate and LoudNorm questions describe the stream the
    joined output really carries instead of input 1's list alone.
    """
    items = answers.get("join_input_items") or []
    if not items:
        return list(answers.get("audio_streams") or [])
    all_items = [{"audio_streams": list(answers.get("audio_streams") or [])}, *items]
    view: list[dict[str, Any]] = []
    for index in range(join_audio_track_count(all_items)):
        for item in all_items:
            streams = item_audio_streams(item)
            if index < len(streams):
                view.append(streams[index])
                break
    return view


def any_join_audio(answers: dict[str, Any]) -> bool:
    """True when ANY input carries audio.

    Every audio feature gate used to read input 1's stream list alone, so a
    silent first input hid the speed-sync, codec, sample-rate and LoudNorm
    questions for a join whose later inputs are audible -- the joined audio then
    played at 1x under a 2x video and outlived it by its whole length (R02).
    """
    return bool(join_audio_streams_view(answers))


def join_audio_selection(answers: dict[str, Any],
                         items: list[dict[str, Any]] | None = None) -> tuple[str, list[int]]:
    """The audio-track answer as an explicit STATE, not a truthiness guess.

    * `"unasked"` -- the key was never created. The track question is gated on
      the primary input having audio, so an absent key is the ONLY case in
      which a join may recover a later input's track.
    * `"none"` -- the user answered with an empty selection. That is an
      authoritative video-only request; the silent-first recovery used to
      overwrite it because an empty list and a missing key both looked falsy
      (F04). `selected_join_subtitle_tracks` already drew this line.
    * `"all"` / `"indices"` -- normalised to a LOGICAL index list, so a
      selection can be compared as a SET against the complete set instead of by
      representation (`[0]` vs `"all"`, which is what F06 got wrong).
    """
    if "audio_tracks" not in answers:
        return "unasked", []
    selected = answers.get("audio_tracks")
    count = (join_audio_track_count(items) if items is not None
             else len(join_audio_streams_view(answers)))
    if selected == "all":
        return "all", list(range(count))
    if not selected:
        return "none", []
    indices: list[int] = []
    for value in selected:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= index < count and index not in indices:
            indices.append(index)
    return "indices", sorted(indices)


def join_audio_recovery(answers: dict[str, Any]) -> tuple[list[dict[str, Any]], list[int]]:
    """The audio the joined output really carries when the question was never asked.

    Mirrors what `build_join_encode_command` does: track 0 of the first audible
    input, with silence synthesised for the inputs that lack it. Returns
    ([], []) when the user ANSWERED the track question -- an explicit answer,
    including an empty one, is authoritative (F04) -- and when input 1 has audio
    or no input does.
    """
    if "audio_tracks" in answers:
        return [], []
    if answers.get("audio_streams") or not answers.get("join_input_items"):
        return [], []
    for item in answers.get("join_input_items") or []:
        streams = item_audio_streams(item)
        if streams:
            return [streams[0]], [0]
    return [], []


def joined_audio_track_model(answers: dict[str, Any]) -> dict[str, Any]:
    """Every LOGICAL joined audio track, described BY ITS OWN CARRIERS.

    A joined audio track is not one stream. It is the i-th audio stream of each
    input in turn, spliced by `concat`, with synthesised silence for the inputs
    that do not have it. `join_audio_streams_view` lends one representative
    stream dict per track, but every cache that JUDGES a stream --
    `packet_sizes`, `audio_volume_stats`, `audio_duplicate_report` -- belongs to
    the PRIMARY file and is keyed by ABSOLUTE stream index, so a track borrowed
    from input 2 was measured against whatever input 1 happens to hold at that
    index. Measured: primary audio at absolute index 1, a German track only
    input 2 carries at absolute index 2, and input 1's absolute index 2 is a
    2-byte subtitle. The lent view was [1, 2] and
    `auto_select_audio_tracks(..., "de")` read the German track as 2 bytes
    instead of 46,964 and 1 kbps instead of 125, then dropped it as empty (B12).

    Returns {"view", "packet_sizes", "volume_stats", "report", "carriers",
    "signature"}. The view's stream dicts are COPIES renumbered to their
    LOGICAL position and carrying their own carrier's duration, so two inputs
    that both use absolute index 2 can no longer collide inside the lent caches.
    `carriers[i]` is [(input position, that input's path, its stream), ...] --
    the provenance itself, without a reference back to the answers dict the
    model is cached on.

    Program-level rules, applied over the carriers a track actually HAS -- an
    input that lacks the track contributes silence, not a carrier:

    * empty       -- every carrier is empty. One real carrier makes the track
                     real; a track whose FIRST input is silent is still audio.
    * near-empty  -- not empty, and every carrier is empty or near-empty.
    * duplicate   -- confirmed in every input that carries BOTH tracks, and at
                     least one does. Dropping a track as redundant is only safe
                     when it is redundant for the whole joined program.

    `detect_duplicate_audio=False` still classifies empty/near-empty per
    carrier -- that is what track selection needs -- but reports no pairs and
    hashes nothing.
    """
    extra = list(answers.get("join_input_items") or [])
    if not extra:
        return {"view": list(answers.get("audio_streams") or []), "packet_sizes": {},
                "volume_stats": {}, "report": {}, "carriers": {}, "signature": ""}
    signature = _audio_report_signature(answers)
    cached = answers.get("_joined_audio_track_model")
    if isinstance(cached, dict) and cached.get("signature") == signature:
        return cached

    # Lazy: duplicate/sparse detection and the probe caches all sit ABOVE this
    # tier, and `services` imports this module. Resolving them at call time also
    # keeps the monkeypatch seam the tests already use.
    from ffmwiz import services
    from ffmwiz.support import ext08, ext09

    detect = bool(answers.get("detect_duplicate_audio", True))
    # The primary carrier IS `answers`, so its probe caches are computed once
    # and stay where the rest of the wizard reads them.
    inputs: list[tuple[dict[str, Any], list[dict[str, Any]]]] = [
        (answers, list(answers.get("audio_streams") or []))]
    for item in extra:
        inputs.append((join_item_answers(answers, item), item_audio_streams(item)))

    reports: list[dict[str, Any]] = []
    for carrier, streams in inputs:
        if not streams:
            reports.append({})
        elif detect:
            reports.append(ext09.detect_duplicate_audio(carrier))
        else:
            empty, near = ext08.classify_sparse_audio_tracks(
                streams, carrier.get("format") or {}, services.get_packet_sizes(carrier))
            reports.append({"empty_tracks": empty, "near_empty_tracks": near,
                            "possible_pairs": [], "confirmed_pairs": []})

    count = max((len(streams) for _carrier, streams in inputs), default=0)
    view: list[dict[str, Any]] = []
    packet_sizes: dict[int, int] = {}
    volume_stats: dict[int, dict[str, str]] = {}
    carriers: dict[int, list[tuple[int, Any, dict[str, Any]]]] = {}
    empty_tracks: set[int] = set()
    near_empty_tracks: set[int] = set()
    for index in range(count):
        holders = [(position, carrier, streams[index])
                   for position, (carrier, streams) in enumerate(inputs)
                   if index < len(streams)]
        carriers[index] = [(position, carrier.get("input_path"), stream)
                           for position, carrier, stream in holders]
        _position, first_carrier, first_stream = holders[0]
        representative = dict(first_stream)
        representative["index"] = index
        duration = services.stream_duration_seconds(first_stream, first_carrier.get("format"))
        if duration:
            # Its OWN file's duration. Without it `stream_duration_seconds`
            # falls through to the primary's container and turns a correct byte
            # count back into a wrong bitrate.
            representative["duration"] = f"{float(duration):.6f}"
        view.append(representative)
        size = services.get_packet_sizes(first_carrier).get(first_stream.get("index"))
        if size is not None:
            packet_sizes[index] = size
        stats = (services.get_audio_volume_stats(first_carrier) or {}).get(index)
        if stats:
            volume_stats[index] = stats
        states = [(index in (reports[position].get("empty_tracks") or set()),
                   index in (reports[position].get("near_empty_tracks") or set()))
                  for position, _carrier, _stream in holders]
        if all(is_empty for is_empty, _is_near in states):
            empty_tracks.add(index)
        elif all(is_empty or is_near for is_empty, is_near in states):
            near_empty_tracks.add(index)

    confirmed: list[tuple[int, int]] = []
    possible: list[tuple[int, int]] = []
    for left in range(count):
        for right in range(left + 1, count):
            shared = [reports[position]
                      for position, (_carrier, streams) in enumerate(inputs)
                      if left < len(streams) and right < len(streams)]
            if not shared:
                continue
            confirmed_sets = [{tuple(pair) for pair in (report.get("confirmed_pairs") or [])}
                              for report in shared]
            possible_sets = [pairs | {tuple(pair) for pair in (report.get("possible_pairs") or [])}
                             for report, pairs in zip(shared, confirmed_sets)]
            if all((left, right) in pairs for pairs in confirmed_sets):
                confirmed.append((left, right))
            if all((left, right) in pairs for pairs in possible_sets):
                possible.append((left, right))

    model = {
        "view": view,
        "packet_sizes": packet_sizes,
        "volume_stats": volume_stats,
        "carriers": carriers,
        "report": {"possible_pairs": possible, "confirmed_pairs": confirmed,
                   "empty_tracks": empty_tracks, "near_empty_tracks": near_empty_tracks,
                   "hashes": {}, "sample_hashes": {}},
        "signature": signature,
    }
    answers["_joined_audio_track_model"] = model
    return model


def with_join_audio_view(step: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
    """Run an audio step against the JOIN's audio instead of input 1's.

    The audio questions live behind `answers["audio_streams"]` -- input 1 alone
    -- so a join whose first input is silent lost LoudNorm and the codec/rate
    questions, and a track only a later input carries could not be reached at
    all. Lend the joined view to the step and take it straight back: nothing
    outside the step may see input 1 claiming a stream it does not have.

    The stream list alone was never enough. `packet_sizes`,
    `audio_volume_stats` and `audio_duplicate_report` are all keyed off the
    PRIMARY file, so lending only the streams left every borrowed track judged
    by unrelated primary data, and a real later-only track was auto-dropped as
    empty (B12). Lend the whole per-carrier model, and take all of it back.
    """

    def run(answers: dict[str, Any]) -> None:
        model = joined_audio_track_model(answers)
        view = model["view"]
        _streams, lent_tracks = join_audio_recovery(answers)
        if not lent_tracks and view == list(answers.get("audio_streams") or []):
            step(answers)
            return
        missing = object()
        lent = {
            "audio_streams": view,
            "packet_sizes": model["packet_sizes"],
            "audio_volume_stats": model["volume_stats"],
            "audio_duplicate_report": model["report"],
        }
        saved = {key: answers.get(key, missing) for key in (*lent, "audio_tracks")}
        answers.update(lent)
        if lent_tracks:
            answers["audio_tracks"] = lent_tracks
        try:
            step(answers)
        finally:
            for key in lent:
                if saved[key] is missing:
                    answers.pop(key, None)
                else:
                    answers[key] = saved[key]
            # The TRACK question writes audio_tracks itself. Taking the lend
            # back by position would throw the user's answer away, so only the
            # object that was actually lent is reclaimed.
            if lent_tracks and answers.get("audio_tracks") is lent_tracks:
                if saved["audio_tracks"] is missing:
                    answers.pop("audio_tracks", None)
                else:
                    answers["audio_tracks"] = saved["audio_tracks"]

    return run


__all__ = [
    'item_audio_streams',
    'join_items_from_answers',
    'join_audio_segment_flags',
    'join_audio_track_count',
    'join_audio_streams_view',
    'any_join_audio',
    'join_audio_selection',
    'join_audio_recovery',
    'joined_audio_track_model',
    'with_join_audio_view',
]
