"""One Qt-free waveform decode contract shared by both editor engines."""
from __future__ import annotations

WAVE_RATE = 4000


def pcm_is_whole_samples(data: bytes) -> bool:
    """True when `data` is a whole number of `s16le` samples.

    Two bytes per sample, so an odd byte count is a writer that was interrupted
    mid-sample -- a decode killed part-way through, not a short waveform. The
    Classic timeline keeps whatever buffer it is handed, so half a sample would
    be drawn as if it were data.
    """
    return bool(data) and len(data) % 2 == 0


def segment_audio_stream_spec(req: dict, seg: dict | None = None) -> str:
    """Which audio stream of an input the waveform reads.

    The request advertises `audio_stream` and the cache key hashes it, so the
    waveform must honour it instead of always reading `a:0` -- a key that
    changes while the decode does not is a cache that serves the wrong picture.
    A per-segment value wins over the request-level one; both default to the
    first audio stream.
    """
    value = (seg or {}).get("audio_stream") or req.get("audio_stream") or "a:0"
    text = str(value).strip().lstrip(":")
    return text or "a:0"


def segment_audio_filter(index: int, duration: float, rate: int = WAVE_RATE, stream_spec: str = "a:0",
                         label: str | None = None, origin: float = 0.0) -> str:
    """One join segment's audio, bounded to the SEGMENT's own length.

    The timeline is built from the declared segment durations (the picture
    clock), so the waveform has to match it sample for sample. Feeding each
    input to `concat` unbounded made the waveform as long as the AUDIO instead:
    two declared 2 s segments whose first input carried 0.5 s of audio produced
    2.5 s of PCM, and 3 s of audio produced 5 s, so every marker after the
    first segment sat at the wrong place.

    `aresample=...:first_pts=0` pads a stream whose audio starts late, keeping
    the original A/V offset instead of sliding the audio to zero; `apad` then
    extends the stream to the far edge of the wanted interval and `atrim` cuts
    that interval out, so the result is exactly `duration` either way.
    """
    span = max(0.001, float(duration))
    start = max(0.0, float(origin or 0.0))
    out = label if label is not None else f"a{index}"
    # `origin` is where the PICTURE starts on the demuxer's clock. Audio before
    # it is not part of this segment's timeline, and leaving it in put every
    # sample `origin` seconds late: an impulse at container 1.5 s in a file
    # whose picture starts at 1.0 s was drawn at sample 6000 instead of 2000
    # (A04).
    # PAD BEFORE TRIM, and pad to the FAR edge of the interval. A segment whose
    # audio lies entirely before its picture has nothing left once the trim
    # runs, and the other order handed `concat` an empty stream: FFmpeg failed
    # the whole joined decode and the later segments were lost with it (A04).
    return (f"[{index}:{stream_spec}]aformat=channel_layouts=mono,"
            f"aresample={rate}:first_pts=0,"
            f"apad=whole_dur={start + span:.6f},"
            f"atrim=start={start:.6f}:end={start + span:.6f},"
            f"asetpts=PTS-STARTPTS[{out}]")


def build_wave_decode_args(req: dict, out_path: str) -> list[str]:
    """FFmpeg args that decode the (joined) audio to WAVE_RATE mono s16le PCM.
    For a join the audio of every input is concatenated on the joined timeline,
    each input bounded to its own declared segment length."""
    segs = req.get("join_segments") or []
    args = ["-hide_banner", "-loglevel", "error", "-y"]
    if segs:
        for seg in segs:
            seg_dur = max(0.001, float(seg.get("duration") or 0.0))
            if seg.get("has_audio", True):
                args += ["-i", str(seg.get("path"))]
            else:
                # A segment with no audio stream makes [i:a:0] match nothing, and
                # ffmpeg then refuses to build the WHOLE filtergraph — one silent
                # segment used to kill the waveform for the entire join (D07).
                # Feed silence of the same length so the concat arity still holds.
                args += ["-f", "lavfi", "-t", f"{seg_dur:.6f}",
                         "-i", f"anullsrc=channel_layout=mono:sample_rate={WAVE_RATE}"]
        filt = [
            segment_audio_filter(
                i, float(seg.get("duration") or 0.0), WAVE_RATE,
                "a:0" if not seg.get("has_audio", True) else segment_audio_stream_spec(req, seg),
                # Each segment carries its own picture origin; a generated
                # silence input has none (A04).
                origin=0.0 if not seg.get("has_audio", True)
                else float(seg.get("picture_clock_offset") or 0.0))
            for i, seg in enumerate(segs)
        ]
        filt.append("".join(f"[a{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=0:a=1[mix]")
        args += ["-filter_complex", ";".join(filt), "-map", "[mix]"]
    else:
        spec = segment_audio_stream_spec(req)
        # ONE picture-clock contract for single and joined media alike. The
        # single-input branch used to decode the audio stream as-is: a 4-second
        # video carrying 1 second of audio produced 1 second of PCM, which the
        # editor then drew across the 4-second timeline, and audio delayed by a
        # second started at sample 1 instead of sample 4001 (R05). The span is
        # the PICTURE's, not the audio's.
        duration = float(req.get("duration") or 0.0)
        if duration > 0:
            chain = segment_audio_filter(
                0, duration, WAVE_RATE, spec, label="mix",
                origin=float(req.get("picture_clock_offset") or 0.0))
        else:
            # No declared duration means there is no picture clock to honour;
            # bounding to a guess would be worse than decoding what is there.
            chain = (f"[0:{spec}]aformat=channel_layouts=mono,"
                     f"aresample={WAVE_RATE}:first_pts=0[mix]")
        args += [
            "-i", str(req.get("input_path") or ""),
            "-filter_complex", chain,
            "-map", "[mix]",
        ]
    args += ["-f", "s16le", "-acodec", "pcm_s16le", out_path]
    return args


