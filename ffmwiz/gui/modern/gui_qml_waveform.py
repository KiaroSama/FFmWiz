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


def segment_audio_filter(index: int, duration: float, rate: int, stream_spec: str = "a:0") -> str:
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
    return (f"[{index}:{stream_spec}]aformat=channel_layouts=mono,"
            f"aresample={rate}:first_pts=0,"
            f"atrim=end={span:.6f},apad=whole_dur={span:.6f},"
            f"asetpts=PTS-STARTPTS[a{index}]")


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
                "a:0" if not seg.get("has_audio", True) else segment_audio_stream_spec(req, seg))
            for i, seg in enumerate(segs)
        ]
        filt.append("".join(f"[a{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=0:a=1[mix]")
        args += ["-filter_complex", ";".join(filt), "-map", "[mix]"]
    else:
        spec = segment_audio_stream_spec(req)
        args += [
            "-i", str(req.get("input_path") or ""),
            "-filter_complex", f"[0:{spec}]aformat=channel_layouts=mono,aresample={WAVE_RATE}[mix]",
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


def waveform_window(pcm, env_min, env_max, rate, start, end, width,
                    env_step: int = WAVE_ENV_STEP):
    """Return up to `width` [min, max] amplitude pairs (each in -1..1) covering
    the time window [start, end]. Zoomed-in windows read raw PCM for full detail;
    zoomed-out windows read the decimated envelope. Pure function (no Qt) so it
    is unit-testable with a synthetic PCM array. Works with or without numpy."""
    if pcm is None or rate <= 0 or width <= 0:
        return []
    total = int(len(pcm))
    if total <= 0:
        return []
    s0 = max(0, min(total, int(float(start) * rate)))
    s1 = max(s0 + 1, min(total, int(float(end) * rate)))
    nwin = s1 - s0
    if nwin <= 0:
        return []
    width = int(min(width, WAVE_MAX_BUCKETS))
    fs = 32768.0
    use_env = (env_min is not None and env_max is not None and (nwin / max(1, width)) > env_step)
    if use_env:
        # The envelope bucket that CONTAINS s1 has to be included or the last
        # fraction of the viewport disappears; a floor-aligned end dropped it.
        # Both edge buckets straddle the viewport, so they are recomputed from
        # the raw samples actually inside it -- including the whole bucket
        # instead would paint a peak that is not in view.
        e0 = max(0, s0 // env_step)
        e1 = max(e0 + 1, min(len(env_min), -(-s1 // env_step)))
        src_min = list(env_min[e0:e1])
        src_max = list(env_max[e0:e1])
        for slot in ({0, len(src_min) - 1} if src_min else set()):
            lo = max(s0, (e0 + slot) * env_step)
            hi = min(s1, (e0 + slot + 1) * env_step)
            if hi <= lo or (lo == (e0 + slot) * env_step and hi == (e0 + slot + 1) * env_step):
                continue        # the bucket is fully inside the viewport
            edge = pcm[lo:hi]
            if len(edge):
                src_min[slot] = min(edge)
                src_max[slot] = max(edge)
    else:
        seg = pcm[s0:s1]
        src_min = seg
        src_max = seg
    m = len(src_min)
    if m <= 0:
        return []
    buckets = int(min(width, m))
    try:
        import numpy as np
        if isinstance(src_min, np.ndarray):
            starts = ((np.arange(buckets, dtype=np.int64) * m) // buckets)
            mins = np.minimum.reduceat(src_min, starts)
            maxs = np.maximum.reduceat(src_max, starts)
            return [[float(mins[i]) / fs, float(maxs[i]) / fs] for i in range(int(mins.size))]
    except Exception:
        pass
    out = []
    for i in range(buckets):
        a = (i * m) // buckets
        b = max(a + 1, ((i + 1) * m) // buckets)
        out.append([min(src_min[a:b]) / fs, max(src_max[a:b]) / fs])
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
