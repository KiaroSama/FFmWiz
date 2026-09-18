"""The FFmpeg option/arity contract, in one place for every layer.

Which options take a value is not a wizard concern: the source-safety guard at
the execution boundary needs the same answer to tell an option's VALUE from an
output path, and it lives two layers below the wizard (A01). Keeping one table
here is what stops the two from drifting into different opinions about the same
argv -- which is exactly how `-an out.mkv` came to mean different things to the
parser and to the guard.

Arity is DECLARED, never guessed from whether the next token looks like a file.
Guessing is what let `-report extra.wav` through, and FFmpeg then wrote BOTH
`extra.wav` and the planned output with exit 0 (R03).
"""
from __future__ import annotations

FFMPEG_VALUELESS_OPTIONS = {
    "-an", "-vn", "-sn", "-dn", "-nostats", "-stats", "-copyts", "-re",
    "-start_at_zero", "-shortest", "-ignore_unknown", "-copy_unknown",
    "-benchmark", "-benchmark_all", "-dump", "-hex", "-xerror", "-bitexact",
    "-fix_sub_duration", "-copyinkf", "-autorotate", "-noautorotate",
    "-autoscale", "-noautoscale", "-accurate_seek", "-noaccurate_seek",
    "-debug_ts", "-psnr", "-vstats", "-stdin", "-auto_conversion_filters",
    "-noauto_conversion_filters", "-nostdin",
}

FFMPEG_VALUED_OPTIONS = {
    # rate control and quality
    "-b", "-crf", "-cq", "-qp", "-maxrate", "-minrate", "-bufsize", "-qmin",
    "-qmax", "-qdiff", "-qcomp", "-aq", "-aq-strength", "-compression_level",
    "-global_quality", "-rc", "-cbr", "-multipass", "-rc-lookahead",
    # encoder tuning
    "-preset", "-tune", "-profile", "-level", "-coder", "-trellis", "-subq",
    "-refs", "-bf", "-g", "-keyint_min", "-sc_threshold", "-me_method",
    "-x264-params", "-x264opts", "-x265-params", "-svtav1-params", "-tier",
    "-spatial_aq", "-temporal_aq", "-aq-mode", "-b_ref_mode", "-gpu",
    # picture and audio shape
    "-pix_fmt", "-s", "-r", "-aspect", "-sample_fmt", "-ar", "-ac",
    "-channel_layout", "-color_primaries", "-color_trc", "-colorspace",
    "-color_range", "-field_order", "-sws_flags", "-swr_flags",
    # container and muxing
    "-movflags", "-fflags", "-flags", "-max_muxing_queue_size", "-muxdelay",
    "-muxpreload", "-avoid_negative_ts", "-vsync", "-fps_mode", "-async",
    "-itsoffset", "-itsscale", "-timestamp", "-metadata", "-disposition",
    "-attach", "-map_channel", "-shortest_buf_duration", "-segment_time",
    # process control
    "-threads", "-filter_threads", "-filter_complex_threads", "-thread_queue_size",
    "-hwaccel", "-hwaccel_device", "-hwaccel_output_format", "-init_hw_device",
    "-filter_hw_device", "-loglevel", "-v", "-max_alloc", "-abort_on",
    "-dts_delta_threshold", "-dts_error_threshold", "-seek_timestamp",
    "-reinit_filter", "-vstats_file", "-frames", "-vframes", "-aframes",
    "-q", "-qscale", "-bt", "-bitrate", "-maxrate:v", "-pass", "-passlogfile",
    # Expert options the first table missed. Each was verified against a real
    # FFmpeg run before being added: refusing `-brand iso6` and `-strict -2`
    # rejected commands FFmpeg accepts, and the refusal then recommended an
    # `-opt=value` spelling FFmpeg exits 8 on (A05).
    "-brand", "-strict", "-tag", "-bsf", "-top", "-force_key_frames",
    "-video_track_timescale", "-max_interleave_delta", "-time_base",
    "-enc_time_base", "-ch_layout", "-write_tmcd",
}

# Options whose VALUE is a file this command reads. They are dependencies, not
# inputs in the `-i` sense, and the guard has to protect them just the same: an
# attachment consumed by `-attach` was being written over by its own job (A01).
FFMPEG_FILE_VALUED_OPTIONS = {
    "-attach", "-filter_script", "-filter_complex_script",
    "-fpre", "-vpre", "-apre",
}

# Options whose value is a file the run WRITES. Arity and DIRECTION are separate
# properties, and treating these as reads is not a smaller mistake -- it is the
# opposite one: `-vstats_file <input>` replaces that media with encoder
# statistics while the real output goes elsewhere, and FFmpeg exits 0 (A01).
# `-passlogfile x` writes `x-0.log`; the prefix expansion is a destination too.
FFMPEG_WRITE_VALUED_OPTIONS = {
    "-vstats_file", "-passlogfile",
}

__all__ = [
    "FFMPEG_VALUELESS_OPTIONS",
    "FFMPEG_VALUED_OPTIONS",
    "FFMPEG_FILE_VALUED_OPTIONS",
    "FFMPEG_WRITE_VALUED_OPTIONS",
]

# The options FFmWiz itself emits that take a value. They are refused as RAW
# arguments (the wizard owns them), so they never needed an arity entry -- but
# the execution-boundary guard reads the command FFmWiz BUILT, where they are
# everywhere. Without them `-vf scale=2:2` reads as "an option, then a
# positional token", and the guard starts calling filtergraphs outputs (A01).
FFMPEG_OWNED_VALUED_OPTIONS = {
    "-i", "-f", "-progress", "-filter_complex", "-lavfi", "-vf", "-filter:v",
    "-af", "-filter:a", "-filter", "-map", "-map_metadata", "-map_chapters",
    "-map_channel", "-c", "-codec", "-c:v", "-codec:v", "-c:a", "-codec:a",
    "-c:s", "-c:d", "-c:t", "-ss", "-t", "-to", "-stream_loop",
}

# Every option known to take exactly one value, whoever wrote it.
FFMPEG_ALL_VALUED_OPTIONS = (FFMPEG_VALUED_OPTIONS | FFMPEG_FILE_VALUED_OPTIONS
                             | FFMPEG_OWNED_VALUED_OPTIONS)

__all__ += [
    "FFMPEG_OWNED_VALUED_OPTIONS",
    "FFMPEG_ALL_VALUED_OPTIONS",
]
