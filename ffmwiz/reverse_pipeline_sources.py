"""Source-side facts a bounded reverse plan needs (subtitles, span).

Split out of ffmwiz/reverse_pipeline.py for file size. These three are the leaf
of that module's call graph -- `bounded_reverse_plan` and
`run_segmented_reverse_main_encode` call them and they call nothing back, which
is the direction that keeps the pair from becoming a cycle.

Every borrowed name is reached through the module that DEFINES it rather than
imported bare, so one `mock.patch` on that definer is seen here too.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ffmwiz import appio  # noqa: F401  (defines log_warn)
from ffmwiz import services  # noqa: F401
from ffmwiz import wizard  # noqa: F401
from ffmwiz import wizard_build_b  # noqa: F401  (defines the timeline/subtitle text helpers)
from ffmwiz.support import L00_misc  # noqa: F401
from ffmwiz.support import L00_probe  # noqa: F401
from ffmwiz.support import L00_streams  # noqa: F401
from ffmwiz.support import L01_subtitles  # noqa: F401


def plan_subtitle_sources(answers: dict[str, Any], items: list[dict[str, Any]],
                          workspace: Path) -> list[dict[str, Any]]:
    """The cue TEXT of every selected track, read from files that exist NOW.

    For a join that is the merged track the forward stage will write, built
    here from the sources with the same helper the join builder uses; for a
    single input it is the source's own track. Either way the plan never has to
    read a file it has not produced.
    """
    if not (L00_misc.source_subtitles_keep_enabled(answers) and answers.get("subtitle_streams")):
        return []
    sources: list[dict[str, Any]] = []
    try:
        # The planner may be called with a workspace nobody has created yet.
        workspace.mkdir(parents=True, exist_ok=True)
        if items:
            for merged in wizard.build_joined_subtitle_files(answers, items):
                path = Path(merged["path"])
                if path.exists():
                    sources.append({"text": path.read_text(encoding="utf-8"),
                                    "index": int(merged.get("index") or 0),
                                    "language": merged.get("language", ""),
                                    "title": merged.get("title", ""),
                                    "default": bool(merged.get("default")),
                                    "forced": bool(merged.get("forced"))})
            return sources
        streams = list(answers.get("subtitle_streams") or [])
        origin = L01_subtitles.subtitle_source_origin(answers)
        for index in L00_streams.selected_subtitle_streams(answers):
            index = int(index)
            if not (0 <= index < len(streams)) or not L01_subtitles.is_text_subtitle(streams[index]):
                continue
            raw = workspace / f"plan_source{index:02d}.srt"
            text = wizard_build_b.extract_subtitle_text(
                answers.get("ffmpeg") or "ffmpeg", Path(answers["input_path"]),
                index, raw, "Planned subtitles", origin, answers.get("ffprobe"))
            if text:
                sources.append({"text": text, "index": index,
                                **L01_subtitles.subtitle_track_metadata(streams[index])})
    except Exception as exc:  # a plan must not fail over a subtitle it cannot read
        appio.log_warn(f"Planned subtitles: could not read the source tracks: {exc}")
    return sources


def planned_retimed_subtitles(stage: dict[str, Any], sources: list[dict[str, Any]],
                              workspace: Path) -> list[dict[str, Any]]:
    """Retimed subtitle tracks for a stage whose INPUT does not exist yet.

    `build_retimed_subtitle_inputs()` extracts the cues from the file the stage
    will read. That works for the executor, which has just written it, and not
    at all for the exported plan: the planner's reverse stage reads
    `joined_forward.mkv` and its split stage reads `reversed_whole.mkv`, so
    extraction found nothing and the stage was emitted with `-sn`. The plan
    therefore DROPPED subtitles a staged reverse keeps -- planned against
    executed, that is `['-sn']` where the run has
    `['-c:s', '-disposition:s:0', '-metadata:s:s', '1:s:0', 'copy',
    'retimed00.srt']`.

    The cues themselves are never in doubt: the SOURCES exist at plan time.
    `sources` is [{"text", "index", metadata...}] already merged for a join,
    and this applies the stage's own `TimelineMap` to them and writes the
    result into the workspace, which is also what makes the exported script
    runnable as it stands.
    """
    if not sources:
        return []
    workspace.mkdir(parents=True, exist_ok=True)
    timeline = wizard_build_b.encode_timeline_map(stage)
    built: list[dict[str, Any]] = []
    for source in sources:
        cues = L01_subtitles.retime_cues(L01_subtitles.parse_srt(source.get("text") or ""), timeline)
        if not cues:
            continue
        index = int(source.get("index") or 0)
        path = workspace / f"retimed{index:02d}.srt"
        path.write_text(L01_subtitles.render_srt(cues), encoding="utf-8", newline="\n")
        built.append({"path": path, "source_index": index,
                      "language": source.get("language", ""),
                      "title": source.get("title", ""),
                      "default": bool(source.get("default")),
                      "forced": bool(source.get("forced"))})
    return built


def reverse_source_seconds(answers: dict[str, Any]) -> float:
    """The PICTURE span of the file a reverse stage will decode.

    The executor probes the intermediate it just wrote and read
    `format.duration`; the planner describes the same file from the inputs'
    picture spans. On a joined pair that is 5.039 against 5.000 -- the AAC tail
    the container carries past the last frame -- so the two bounded their
    reverse segments differently for the same job. Everything else in this
    pipeline was moved onto the picture clock already (B07/B08); this is the
    read that was left behind.
    """
    streams = list(answers.get("video_streams") or [])
    fmt = answers.get("format") or {}
    if streams:
        span = L00_probe.video_stream_span_seconds(streams[0], fmt)
        if span:
            return span
    return services.stream_duration_seconds({}, fmt) or 0.0


__all__ = [
    'plan_subtitle_sources',
    'planned_retimed_subtitles',
    'reverse_source_seconds',
]
