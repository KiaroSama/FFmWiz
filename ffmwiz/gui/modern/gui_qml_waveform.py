"""The modern editor's waveform model — pure Python, no Qt.

Split out of `ffmwiz_gui_qml` when that module reached the project's file-size
ceiling. Everything here is a plain function over request dicts and PCM
arrays, which is also what makes the segment-length and envelope behaviour
testable against real FFmpeg output with no display.
"""
from __future__ import annotations

import array
from pathlib import Path  # noqa: F401 - part of this module's public surface

from ffmwiz.gui import gui_geometry  # type: ignore


# ----------------------------------------------------------------------------
# Waveform model (classic-quality, backend-heavy).
#
# The heavy work lives here in Python so QML only draws. Audio is decoded once
# to 4000 Hz mono int16 PCM (same rate as the classic editor) and kept in
# memory, plus a decimated min/max envelope for fast zoomed-out rendering. The
# editor then asks for a per-viewport array of [min, max] pairs (one per canvas
# pixel column) via waveform_window(); zoomed-in views read the raw PCM for full
# detail, zoomed-out views read the envelope. Amplitudes are scaled against the
# int16 full scale (32768), NOT the clip's own peak, so quiet stays quiet and
# loud stays loud. This is a hybrid of options (1) full PCM and (2) a min/max
# pyramid from the task brief.
# ----------------------------------------------------------------------------

WAVE_RATE = 4000          # mono PCM sample rate for the waveform (matches classic)
WAVE_ENV_STEP = 256       # samples per decimated envelope bucket
WAVE_MAX_BUCKETS = 4000   # cap on columns returned for one viewport


def compute_wave_key(req: dict) -> str:
    """Stable cache key for the decoded waveform. Changes only when the input
    file, the join input list, the selected audio stream, the duration, or the
    sample rate change — so the waveform is decoded once and reused otherwise."""
    import hashlib

    segs = req.get("join_segments") or []
    if segs:
        parts = [str(s.get("path") or "") + ":" + str(s.get("duration") or "") for s in segs]
    else:
        parts = [str(req.get("input_path") or "")]
    payload = "|".join(parts)
    payload += f"|dur={req.get('duration')}|rate={WAVE_RATE}|astream={req.get('audio_stream', 'a:0')}"
    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:16]


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


def segment_audio_filter(index: int, duration: float, rate: int, stream_spec: str = "a:0",
                         label: str | None = None, origin: float = 0.0) -> str:
    """One join segment's audio, bounded to the SEGMENT's own length.

    The timeline is built from the declared segment durations (the picture
    clock), so the waveform has to match it sample for sample. Feeding each
    input to `concat` unbounded made the waveform as long as the AUDIO instead:
    two declared 2 s segments whose first input carried 0.5 s of audio produced
    2.5 s of PCM, and 3 s of audio produced 5 s, so every marker after the
    first segment sat at the wrong place.

    `aresample=...:first_pts=0` pads a stream whose audio starts late, keeping
    the original A/V offset instead of sliding the audio to zero; `atrim` cuts
    a long input to the segment span and `apad=whole_dur` fills a short one
    with silence, so the result is exactly `duration` either way.
    """
    span = max(0.001, float(duration))
    start = max(0.0, float(origin or 0.0))
    out = label if label is not None else f"a{index}"
    # `origin` is where the PICTURE starts on the demuxer's clock. Audio before
    # it is not part of this segment's timeline, and leaving it in put every
    # sample `origin` seconds late: an impulse at container 1.5 s in a file
    # whose picture starts at 1.0 s was drawn at sample 6000 instead of 2000
    # (A04). `asetpts` now runs BEFORE `apad`, so `whole_dur` measures the
    # trimmed span rather than the original timeline.
    return (f"[{index}:{stream_spec}]aformat=channel_layouts=mono,"
            f"aresample={rate}:first_pts=0,"
            f"atrim=start={start:.6f}:end={start + span:.6f},"
            f"asetpts=PTS-STARTPTS,apad=whole_dur={span:.6f}[{out}]")


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


def decode_pcm_samples(data: bytes):
    """int16 samples of raw little-endian PCM `data`.

    Returns a numpy array when numpy is installed (fast path) and the stdlib
    `array('h')` otherwise. numpy is an ACCELERATOR here, not a requirement:
    it is not a declared FFmWiz dependency, and without this fallback the whole
    QML waveform degraded to a permanent "decoding waveform…" (D08)."""
    if not data:
        return None
    try:
        import numpy as np
        pcm = np.frombuffer(data[: len(data) - (len(data) % 2)], dtype=np.int16)
    except Exception:
        pcm = gui_geometry.pcm_samples(data)
    return pcm if len(pcm) else None


def build_wave_envelope(pcm, step: int = WAVE_ENV_STEP):
    """Decimated (min, max) envelope of the int16 PCM for fast zoomed-out views.
    Returns (env_min, env_max) as numpy arrays or stdlib arrays, or (None, None)
    when the clip is too short for an envelope to help."""
    if pcm is None or len(pcm) == 0:
        return None, None
    total = len(pcm)
    # CEIL, not floor: truncating to whole buckets threw away up to `step - 1`
    # samples at the END of the clip, so a peak in the last fraction of a second
    # was simply absent from every zoomed-out view -- a final 32767 read back as
    # 0. The terminal bucket is short; it is not missing.
    full = total // step
    m = -(-total // step)
    if m < 2:
        return None, None
    try:
        import numpy as np
        if isinstance(pcm, np.ndarray):
            block = pcm[: full * step].reshape(full, step)
            env_min, env_max = block.min(axis=1), block.max(axis=1)
            if total > full * step:
                tail = pcm[full * step:]
                env_min = np.append(env_min, tail.min())
                env_max = np.append(env_max, tail.max())
            return env_min, env_max
    except Exception:
        pass
    env_min = array.array("h")
    env_max = array.array("h")
    for i in range(m):
        chunk = pcm[i * step:min(total, (i + 1) * step)]
        env_min.append(min(chunk))
        env_max.append(max(chunk))
    return env_min, env_max


def _extremes(values):
    """(min, max) of a slice, for a numpy array or a stdlib array alike."""
    if len(values) == 0:
        return None
    try:
        import numpy as np
        if isinstance(values, np.ndarray):
            return int(values.min()), int(values.max())
    except Exception:      # noqa: BLE001 - the stdlib path below is the floor
        pass
    return min(values), max(values)


def _column_extremes(pcm, env_min, env_max, lo: int, hi: int, env_step: int, use_env: bool):
    """(min, max) over the raw sample interval [lo, hi), or None when empty.

    With the envelope in play this combines the complete buckets that lie
    INSIDE the interval with raw fragments at BOTH edges. Whole buckets were
    previously counted out across columns instead of being placed by their
    sample positions, which slid transients into the neighbouring column.
    """
    if hi <= lo:
        return None
    if not use_env:
        return _extremes(pcm[lo:hi])

    first_full = -(-lo // env_step)                 # ceil
    last_full = hi // env_step                      # floor
    results = []
    if last_full > first_full:
        available = min(last_full, len(env_min))
        if available > first_full:
            low = _extremes(env_min[first_full:available])
            high = _extremes(env_max[first_full:available])
            if low is not None and high is not None:
                results.append((low[0], high[1]))
    # The partial bucket at each edge is read from the raw samples, so a column
    # boundary that falls inside a bucket is still exact.
    head_end = min(hi, first_full * env_step)
    if head_end > lo:
        found = _extremes(pcm[lo:head_end])
        if found is not None:
            results.append(found)
    tail_start = max(lo, last_full * env_step)
    if hi > tail_start:
        found = _extremes(pcm[tail_start:hi])
        if found is not None:
            results.append(found)
    if not results:
        return _extremes(pcm[lo:hi])
    return min(r[0] for r in results), max(r[1] for r in results)


def waveform_window(pcm, env_min, env_max, rate, start, end, width,
                    env_step: int = WAVE_ENV_STEP):
    """Up to `width` [min, max] pairs (each in -1..1) for the window [start, end].

    Every column's bounds are computed in the ORIGINAL SAMPLE CLOCK and then
    read from the data that actually falls inside them. Two defects came from
    not doing that (R06):

    * bucket COUNTS were spread across the columns rather than sample
      POSITIONS, so a peak at sample 550 viewed over [0.025, 1.0] s at width 8
      landed in column 1 instead of column 0;
    * a viewport longer than the available PCM was clamped to the data, which
      stretched what existed across the full width -- a half-second peak inside
      one second of PCM, viewed over four seconds, appeared at column 4 instead
      of column 1.

    A column with no samples is silence, which is what "no data here" looks
    like; it is never filled by borrowing from a column that does have data.
    Pure function (no Qt), identical results with and without numpy.
    """
    if pcm is None or rate <= 0 or width <= 0:
        return []
    total = int(len(pcm))
    if total <= 0:
        return []
    first = float(start) * rate
    last = float(end) * rate
    if last <= first:
        return []
    width = int(min(int(width), WAVE_MAX_BUCKETS))
    span = last - first
    fs = 32768.0
    # The envelope is a speed-up, worth reading only when one column covers
    # more than a whole bucket; below that the raw samples are both cheaper
    # and exact.
    use_env = (env_min is not None and env_max is not None
               and len(env_min) > 0 and (span / width) > env_step)

    out = []
    for column in range(width):
        lo = first + span * column / width
        hi = first + span * (column + 1) / width
        low_index = max(0, int(lo) if lo >= 0 else 0)
        high_index = min(total, int(hi) + (1 if hi > int(hi) else 0))
        found = _column_extremes(pcm, env_min, env_max, low_index, high_index,
                                 env_step, use_env)
        if found is None:
            out.append([0.0, 0.0])          # outside the PCM: silence, not a stretch
        else:
            out.append([float(found[0]) / fs, float(found[1]) / fs])
    return out



__all__ = [
    "WAVE_RATE",
    "WAVE_ENV_STEP",
    "WAVE_MAX_BUCKETS",
    "compute_wave_key",
    "segment_audio_stream_spec",
    "segment_audio_filter",
    "build_wave_decode_args",
    "decode_pcm_samples",
    "build_wave_envelope",
    "waveform_window",
]
