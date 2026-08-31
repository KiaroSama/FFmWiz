# FFmWiz Documentation - Prompt and key reference

The two exhaustive tables: every Mode 1 prompt in the order it is asked, and every `config.env` key.

Part of the FFmWiz manual, split across files for size. The full table of
contents lives in [DOCUMENTATION.md](DOCUMENTATION.md).

---

## Appendix A — Mode 1 prompt-by-prompt reference

This appendix documents **every question** the interactive wizard (Mode 1) can ask, in the
order the wizard evaluates them. A step is **skipped automatically** when it does not apply to
your input or to your earlier answers (for example, audio questions are skipped for a video
with no audio, and crop sub-questions are skipped when you use the graphical editor). At almost
every prompt you can type `0` to go **back** one question, and `exit` to quit FFmWiz.

The numbered question counter you see on screen only counts the prompts that actually apply, so
the numbers stay continuous even when steps are skipped.

### A.1 Input file

```
Prompt:   Drag and drop a file here or paste a path
Accepts:  a quoted or unquoted path; surrounding quotes are stripped
Notes:    Unicode and spaces are fine. The file must exist and be readable.
          FFmWiz immediately probes the file (ffprobe) and prints a Source file
          info block: container, duration, size, total bitrate, and a per-stream
          breakdown for video (size, fps, bit depth, color range, SAR/DAR, pixel
          shape) and audio (codec, channels, sample rate, bitrate, mean/max
          volume, duplicate/near-empty flags).
```

### A.2 Join additional inputs (video or audio-only inputs)

```
Prompt:   Join another file before encoding? (and the follow-up file prompts)
When:     Offered for video inputs and for audio-only inputs.
Effect:   Multiple inputs are concatenated into a single timeline before the
          encode recipe is applied. Video/audio settings apply by track number to
          every joined input. FFmWiz stream-copies compatible inputs or
          re-encodes to a common format when they differ (see §10).
```

### A.3 Output location

```
Prompt:   Output destination (folder, full file path, or base name)
Default:  the input's folder
Notes:    A bare base name is dropped into the input folder using the chosen
          output format. FFmWiz never overwrites the input; on a name collision it
          adds a numeric suffix. Split jobs append _Part01, _Part02, ...
```

### A.4 Output format (container)

```
Prompt:   Output container/extension
Accepts:  mp4, mkv, mov, webm, mp3, m4a, opus, flac, wav, ... or n to inherit
Notes:    The container constrains which codecs and features are valid (for
          example MP4/MOV convert text subtitles to mov_text and drop image subs;
          attachments need MKV). Audio-only containers switch the job to an
          audio-only encode.
```

### A.5 Video codec

```
Prompt:   Video codec
Accepts:  H265, H264, AV1, VP9, MPEG4, copy, or any literal ffmpeg encoder name
Default:  H265
copy:     stream-copies the video bitstream (no quality loss, very fast). It is
          AUTO-PROMOTED to a real encoder (H265) when any filter is required
          (crop, scale, fps change, cut, split, or speed/reverse), because a
          stream copy cannot be filtered.
```

### A.6 Use GPU (NVENC)

```
Prompt:   Use the GPU (NVIDIA NVENC) for encoding?
Default:  y when a usable NVENC GPU is detected
Effect:   y uses the NVENC variant of the chosen codec (h264_nvenc, hevc_nvenc,
          av1_nvenc) with CUDA filtering where possible; n forces CPU encoding
          (libx264/libx265/libsvtav1/libvpx-vp9/mpeg4). If NVENC is unavailable
          for the chosen codec, FFmWiz falls back to CPU automatically.
```

### A.7 Unified video editor (graphical)

```
Prompt:   Open the graphical editor to set crop / cuts / split / speed?
Effect:   Opens the classic or QML Unified Video Editor (see §12). When used, the
          crop sub-questions (A.8–A.12) and the terminal speed/cut prompts are
          skipped because you set them visually. Declining keeps the terminal
          prompts.
```

### A.8–A.12 Crop

```
A.8  Crop enabled?              y/n (skipped if you used the editor)
A.9  Crop TOP pixels            integer >= 0
A.10 Crop LEFT pixels           integer >= 0
A.11 Crop RIGHT pixels          integer >= 0
A.12 Crop BOTTOM pixels         integer >= 0
Notes: Values are PIXELS REMOVED from each side (not x/y offsets). FFmWiz snaps
       the resulting width/height to even numbers for chroma-safe output. You can
       also paste an inline "top,left,right,bottom" to answer in one line.
       At a single per-side question "0" means BACK, so type "00" when you really
       want zero pixels removed from that side. Inline "100,0,200,0" is unaffected.
```

### A.13 Video bitrate

```
Prompt:   Target average video bitrate in kbps
Accepts:  an integer (e.g. 400, 1500, 4500) or n to keep the detected source rate
Warning:  if you enter a value above the detected source bitrate, FFmWiz warns and
          defaults to "no" so you do not waste space upscaling bitrate.
Estimate: after a number is entered, FFmWiz prints the approximate output size at that
          bitrate over the output duration (units scale B/KB/MB/GB), plus a note that the
          real bitrate can differ and that 2-pass encoding brings it closer to the target.
Pairs with: the bitrate MODE (quality_vbr vs strict_size) from config / defaults.
```

### A.14 NVENC multipass

```
Prompt:   NVENC multipass mode
When:     only when the resolved encoder is NVENC AND exposes -multipass
Options:  1 = Disabled (fastest, single pass)
          2 = qres   (two-pass, quarter-resolution first pass)
          3 = fullres(two-pass, full-resolution first pass; best quality, slowest)
```

### A.15 CPU two-pass

```
Prompt:   Use two-pass CPU video encoding?
When:     only for CPU encoders that support -pass (libx264, libx265, libvpx-vp9,
          libaom-av1, libsvtav1, mpeg4) and only for single-output encodes
Effect:   runs an analysis pass then a final pass for accurate target bitrate.
          Skipped for joins, splits, cuts, and speed/reverse workflows.
```

### A.16 Resolution

```
Prompt:   Output resolution
Accepts:  presets 144p..2160p (closest-edge, aspect-preserving), a single edge
          like w1280 or 720h, a fit box WIDTHxHEIGHT, stretch:WIDTHxHEIGHT to
          force distortion, or n to keep the source size.
Notes:    FFmWiz keeps even dimensions; you are warned before upscaling above the
          source resolution.
```

### A.17 FPS

```
Prompt:   Output frame rate (integer) or n to keep the source rate
Notes:    Lowering fps drops frames; raising it duplicates frames. You are warned
          before exceeding the source rate.
```

### A.18 Video speed / reverse

```
Prompt:   Change video speed and/or reverse the video?
When:     terminal prompt only if you did NOT use the graphical editor
Accepts:  a speed multiplier 0.10..8.0 (1.0 = no change) and a reverse y/n
Effect:   changing speed or reversing forces a re-encode; chapter timestamps are
          remapped to the new timeline.
```

### A.19 Cuts (keep ranges)

```
Prompt:   Cut the video (keep/remove ranges)?
When:     re-encode workflows without the graphical editor
Format:   times are h:m:s:frame, converted to seconds using the detected fps
Layouts:  keep one range, remove one range, remove multiple ranges, keep multiple
          ranges (see §10).
```

### A.20 Audio tracks

```
Prompt:   Which audio streams to keep?
Accepts:  0 | 0,1,2 | all | d (drop duplicates) | e (drop empty/near-empty) | de
Default:  de (drop duplicates and empty/near-empty) when duplicate detection is on
Notes:    duplicate detection compares codec/layout/rate/bitrate, then confirms
          with a full audio hash before dropping anything.
```

### A.21 Loudness normalization (loudnorm)

```
Prompt:   Audio loudness normalization
Options:  1 = Off
          2 = Single-pass loudnorm on the final output audio
          3 = Two-pass loudnorm (measures first, then normalizes)
Follow-up:if your audio is set to copy, FFmWiz offers to switch to AAC (loudnorm
          requires re-encoding). Two-pass asks to measure current loudness first.
Target:   EBU R128 integrated loudness (LUFS); TP and LRA ceilings are applied.
```

### A.22 Audio cut

```
Prompt:   Cut the audio (keep/remove ranges)?
When:     audio-only transform workflows
Behavior: same range model as video cuts; output keeps everything outside the
          removed ranges.
```

### A.23 Audio speed / reverse

```
Prompt:   Change audio speed and/or reverse the audio?
Accepts:  a multiplier 0.10..8.0 (atempo chain) and reverse y/n
Notes:    when audio follows video speed, the same factor is reused so A/V stay in
          sync.
```

### A.24 Audio codec

```
Prompt:   Audio codec
Accepts:  aac, libopus (opus alias), libmp3lame, flac, pcm_s16le, copy, or an
          encoder name
Notes:    the container picks a sensible default (e.g. opus for webm). flac/pcm_*
          ignore bitrate; copy skips audio re-encoding.
```

### A.25 Audio bitrate

```
Prompt:   Audio bitrate per stream in kbps
Accepts:  64, 128, 192, 256, 320, ... or n to keep the source bitrate
Default:  the DETECTED source bitrate, capped at 320 kbps. A 320 kbps track offers 320,
          so pressing Enter does not down-rate it. The default is never raised above the
          source, and a source whose bitrate cannot be detected falls back to 128.
          In a Join, the default is the highest source bitrate among the joined inputs.
When:     only for lossy codecs that use a bitrate (not flac/pcm_*/copy)
Estimate: after a number is entered, FFmWiz prints the approximate output size at that
          audio bitrate plus the same accuracy note as the video bitrate prompt.
```

### A.26 Audio sample rate

```
Prompt:   Output audio sample rate in Hz
Accepts:  44100, 48000, 96000, ... or n/keep to keep the source rate
Warning:  you are warned before choosing a rate above the source rate.
```

### A.27 Source extras (metadata / chapters / extra streams)

```
Prompt:   Keep source metadata, chapters, subtitles, attachments, extra streams?
Effect:   y preserves container/stream metadata, chapters, subtitle selection,
          data streams, extra video streams, and (optionally) MKV attachments. n
          strips them from the encode.
Emits:    y -> "-map_metadata 0"; chapters are mapped from the source when the
          timeline is untouched, or remapped onto the processed timeline when
          cuts/splits/speed changed it (and dropped if remapping is impossible).
          n -> "-map_metadata -1 -map_chapters -1", and extra source video, data,
          and subtitle streams are not mapped at all.
```

### A.28 Subtitle tracks

```
Prompt:   Which subtitles to keep?
Accepts:  selection (0 | 0,1 | all) or none/clear/delete to drop all
When:     video output with kept metadata and at least one subtitle stream
Notes:    MP4/MOV convert text subtitles to mov_text and drop image subs
          (PGS/VobSub).
```

### A.29 Color range (unknown source range)

```
Prompt:   Select source color range
Options:  1 = Assume TV/Limited; 2 = Do not force a range; 3 = Assume PC/Full
When:     only when the source range is unknown and the video is re-encoded
Notes:    no pixel-value conversion is performed; this only affects range
          signaling written into the output.
```

### A.30 Start now

```
Prompt:   Start FFmpeg now?
Default:  y
Notes:    declining prints the final PowerShell command and the settings summary
          so you can run or adapt it manually. A Join or Split combined with
          Reverse is the exception: it does not run as one command, so the
          printed command is a readable reference only — running it would buffer
          the whole timeline. For those jobs declining also writes the real
          multi-stage plan next to the output as <name>.plan.ps1, which stops on
          the first failure and names the scratch directory to remove afterwards.
          If that file cannot be written the failure is reported with its reason
          and the printed command is explicitly NOT offered as a substitute.
          The plan carries the job's subtitles too, including a Split's
          per-part tracks: it builds them from the source files rather than
          from an intermediate it has not produced yet.
          The summary includes an "estimated
          output size" line computed from the TOTAL target bitrate (video + audio)
          over the output duration. It shows N/A when the size cannot be derived from
          a target bitrate — constant-quality (CRF/CQ) mode or a pure video stream copy.
```

---

## Appendix B — Complete config.env key reference

This appendix expands every `config.env` key with its purpose, accepted values, interactions,
and gotchas. The short table is in §7; this is the prose reference. All keys are optional
except `input_path`. Empty values fall back to the interactive default, so a minimal
`config.env` only needs `input_path`.

### B.1 Input / output

**`input_path`** — Absolute path to the source file. Required for Mode 2. May contain spaces
and Unicode. Forward or back slashes both work and need no escaping. The file must exist and be
a readable media file; otherwise Mode 2 stops with a clear error.

**`output_path`** — Where to write the result. Accepts a folder (output is named from the
input), a full file path, or a bare base name (placed in the input folder using
`output_format`). Empty means the input's folder. FFmWiz never overwrites the input; a numeric
suffix is added on collision.

**`output_format`** — The output container extension without a dot (`mp4`, `mkv`, `mov`,
`webm`, `mp3`, `m4a`, `opus`, `flac`, `wav`, ...). `n` inherits the input's extension. The
container limits valid codecs and features (subtitles, attachments, faststart).

### B.2 Video

**`video_codec`** — `H265`, `H264`, `AV1`, `VP9`, `MPEG4`, `copy`, or any literal ffmpeg
encoder name. Aliases map to a CPU encoder or its NVENC variant depending on `use_gpu`. `copy`
is promoted to `H265` automatically when a filter is required.

**`use_gpu`** — `y` selects the NVENC variant when available; `n` forces CPU. Falls back to CPU
if NVENC is unavailable for the chosen codec.

**`crop`, `crop_top`, `crop_left`, `crop_right`, `crop_bottom`** — `crop=n` disables cropping;
`crop=y` uses the four margin values (pixels removed per side); an inline
`crop=top,left,right,bottom` sets all four at once. **Mode 2 always asks the crop question
interactively and ignores these**, since crop is the one thing Mode 2 is designed to vary per
file. The keys still apply to other config-driven paths.

**`video_bitrate_kbps`** — Target average video bitrate in kbps, or `n` to keep the detected
source rate. You are warned before exceeding the source.

**`video_bitrate_mode`** — `quality_vbr` (`-b:v X -maxrate 2X -bufsize 4X`, better quality) or
`strict_size` (`-b:v X -maxrate X -bufsize 2X`, tighter size).

**`resolution`** — A preset (`480p`/`720p`/`1080p`/...), one edge (`w1280` or `720h`), a fit box
(`1280x720`), `stretch:1280x720` to force distortion, or `n` to keep the source size.

**`fps`** — Output frame rate as an integer, or `n` to keep the source rate.

**`video_look`** — Extra picture filters as a comma-separated list, or `n` for none.
Accepted tokens: `90cw` / `90ccw` / `180`; `hflip`, `vflip`; `gray`; `bright=N`,
`contrast=N`, `sat=N`; `denoise`, `sharpen`, `blur`, each optionally `=light`,
`=medium` or `=heavy`; `fadein=SECONDS`, `fadeout=SECONDS`. Example:
`video_look=90cw,gray,denoise,fadein=1.5`.

They join the same filter chain as crop, resize, speed and reverse, in this
order: crop, rotate/mirror, frame rate, scale/pad, colour, denoise,
sharpen/blur, speed/reverse, fade, pixel format. Two consequences worth knowing:
a rotation is applied before the resize, so a portrait target of a landscape
source fills the canvas instead of letterboxing it; and fade seconds are
measured on the OUTPUT, so a one-second fade lasts one second even alongside a
speed change. `sharpen` and `blur` cancel each other and are rejected together.
A fade-out longer than the output is dropped with a warning rather than
producing an invalid start time. The fade applies to picture and sound
together.

**`video_quick`** — A one-shot output shape, or `n`. One of `gif`, `boomerang`,
`thumb`, or `loop=N`.

`gif` writes an animated GIF through a TWO-PASS palette: the first pass builds
an optimal 256-colour palette with `palettegen`, the second applies it with
`paletteuse`. A single-command `split` variant exists but buffers the stream to
build the palette, which is the memory shape this project avoids elsewhere.
Defaults are 15 fps and 480 px wide; `gif_fps` and `gif_width` override them,
and a GIF carries no audio. `boomerang` plays the clip forward then reversed,
reusing the bounded reverse pipeline rather than a second implementation, so a
long input is still segmented against the frame budget. `thumb` extracts one
frame -- `thumbnail_seconds` picks the moment, `thumbnail_ext` the format
(default `png`). `loop=N` repeats the input N times with `-stream_loop`, which
is an INPUT option and so sits before `-i`.

**`audio_volume`** — Audio gain as a factor (`1.5`), a percentage (`150%`) or
decibels (`+6dB`), or `n`. Accepted range is 0.01 to 10.0, about -40 dB to
+20 dB; anything outside it is refused as more likely a typo than an intention.
It is applied AFTER LoudNorm, because LoudNorm normalises to a target and would
undo a gain applied before it.

**`raw_ffmpeg_args`** — Your own ffmpeg options, or `n`. They are split the way
a shell would quote them, so `-metadata title="My film"` keeps its spaces, and
they are placed LAST, immediately before the output path: ffmpeg reads output
options in order, so options here can override what the wizard chose. Options
the wizard owns -- `-i`, `-vf`, `-c:v`, `-map`, `-ss`, `-y`, `-filter_complex`
and the rest -- are refused, because the settings summary, the output path and
the exported plan all read those back from the answers, and letting an argument
change one would make the printed command disagree with the job. The full
command is always shown for review before anything runs.

Omit the key entirely and Mode 2 never asks about it. In the interactive
wizard the same question is offered once, after the crop questions, and is
skipped on the unified-editor path.

### B.3 Audio

**`audio_tracks`** — `0`, `0,1,2`, `all`, or the cleanup shortcuts `d` (drop duplicates), `e`
(drop empty/near-empty), `de` (both). Defaults to `de`.

**`audio_codec`** — `aac`, `libopus` (alias `opus`), `libmp3lame`, `flac`, `pcm_s16le`, `copy`,
or an encoder name. The container default applies if omitted.

**`audio_bitrate_kbps`** — Per-stream bitrate in kbps, or `n` to keep source. Ignored for
`flac`/`pcm_*`/`copy`.

**`audio_sample_rate`** — Output sample rate in Hz (`44100`, `48000`, `96000`), or `n` to keep
the source rate. Ignored for `copy`. You are warned before exceeding the source rate.

### B.4 Streams / metadata

**`keep_source_metadata`** — `y` keeps container/stream metadata, chapters, extra video/data
streams, and enables subtitle selection; `n` strips them from the encode.

**`subtitle_tracks`** — A selection (`0`, `0,1`, `all`) or `none`/`clear`/`delete` to drop all.
Only used when `keep_source_metadata=y` and the source has subtitles.

**`keep_embedded_attachments`** — `y` copies MKV attachment streams (e.g. subtitle fonts) when
the output container supports them.

**`detect_duplicate_audio`** — `y` lets FFmWiz flag duplicate/near-empty audio for the
`d`/`e`/`de` shortcuts.

### B.5 Loudness / speed / advanced (optional)

**`loudnorm`** — `off` (default) or `on`. `on` applies single-pass EBU R128 loudness
normalization to the output audio and, if the audio codec was `copy`, switches it to AAC
(loudnorm requires re-encoding). Two-pass/measured loudnorm is interactive only because it
needs a live measurement that a static file cannot provide.

**`loudnorm_target_i`** — Integrated loudness target in LUFS for `loudnorm=on`. Common values:
`-14` (streaming), `-16`, `-23` (broadcast EBU R128). Default `-16`.

**`nvenc_multipass`** — `disabled`, `qres`, or `fullres`. Used only when the resolved encoder is
NVENC and exposes `-multipass`. `qres` does a quarter-resolution first pass; `fullres` does a
full-resolution first pass (best quality, slowest). Ignored on CPU encoders.

**`cpu_two_pass`** — `y`/`n`. Enables FFmpeg `-pass 1/2` for supported CPU encoders (libx264,
libx265, libvpx-vp9, libaom-av1, libsvtav1, mpeg4). Not available on NVENC, unsupported
encoders, or join/split/cut/speed/reverse workflows: pass 1 has to analyse the same video
timeline pass 2 encodes, and those rebuild it. The wizard hides the question for them, and a
config file that sets it anyway is told before the command is confirmed:

```text
CPU two-pass was turned off for this job: a join builds its video through
filter_complex, which pass 1 cannot analyse.
```

The settings summary then reports it as off, so it can never claim a two-pass encode that is
not running.

**`color_range`** — `source` (default, do nothing special), `tv` (limited), `pc` (full), or
`unspecified` (do not force a range). Only meaningful when the source range is unknown and the
video is re-encoded. No pixel-value conversion is performed; only range signaling changes.

**`video_speed`** — A multiplier `0.10`–`8.0` (`2` = twice as fast, `0.5` = half speed) or `n`
for no change. Changing speed forces a re-encode and remaps chapters.

**`reverse_video`** — `y`/`n`. Reverses the whole video (forces a re-encode).

**`audio_speed`** — A multiplier `0.10`–`8.0`, `match_video` (follow `video_speed` and
`reverse_video` so audio and video stay in sync), or `n` for no change.

**`reverse_audio`** — `y`/`n`. Reverses the audio. Ignored when `audio_speed=match_video`
(the audio then follows the video's reverse flag instead).

### B.6 App behaviour

**`logging_enabled`** — `y` writes dated UTF-8 logs into `Logs/` next to `FFmWiz.py`.

**`log_retention_days`** — `0` keeps logs forever; a positive integer deletes FFmWiz logs older
than that many days when logging starts.

**`log_level`** — `debug` (default), `info`, `warning`, `error`, or `critical`. Sets how much
reaches the log file; the console is unaffected. The environment variable `FFMWIZ_LOG_LEVEL`
overrides this value, and an unknown value falls back to `debug`.

**`gui_engine`** — `classic` (default, PySide6 widgets) or `qml` (QtQuick). The environment
variable `FFMWIZ_GUI_ENGINE` overrides this value.

---
