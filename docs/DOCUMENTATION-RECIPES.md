# FFmWiz Documentation - Recipes, concepts and flags

Appendices C-L: the cookbook, FFmpeg concepts, encoder/container compatibility, the troubleshooting matrix, the extended FAQ, worked sessions, and the flag/env-var lists.

Part of the FFmWiz manual, split across files for size. The full table of
contents lives in [DOCUMENTATION.md](DOCUMENTATION.md).

---

## Appendix C — Recipe cookbook

Ready-to-paste `config.env` recipes for common goals. Set `input_path` (and usually
`output_path`) for your own files; the keys shown are the ones that matter for each goal.
Anything omitted keeps the interactive default.

### C.1 Lossless remux (change container only)

```ini
output_format=mkv
video_codec=copy
audio_codec=copy
subtitle_tracks=all
keep_source_metadata=y
```

Use this to move a stream into MKV (or MP4) without re-encoding. Fast and lossless. If the
target container cannot hold a codec or subtitle type, switch that stream to a compatible codec.

### C.2 Web-friendly 1080p H.264 (broad compatibility)

```ini
output_format=mp4
video_codec=H264
use_gpu=y
resolution=1080p
video_bitrate_kbps=6000
video_bitrate_mode=quality_vbr
audio_codec=aac
audio_bitrate_kbps=160
audio_sample_rate=48000
```

H.264 in MP4 plays almost everywhere. Faststart is added automatically for progressive playback.

### C.3 Efficient 1080p H.265 (smaller files)

```ini
output_format=mp4
video_codec=H265
use_gpu=y
resolution=1080p
video_bitrate_kbps=3500
nvenc_multipass=fullres
audio_codec=aac
audio_bitrate_kbps=160
```

HEVC at a lower bitrate matches H.264 quality at a smaller size. `fullres` multipass improves
NVENC bitrate distribution. For Apple compatibility, the `hvc1` tag is added for HEVC-in-MP4.

### C.4 Archival AV1 (CPU, SVT-AV1)

```ini
output_format=mkv
video_codec=AV1
use_gpu=n
video_bitrate_kbps=2500
cpu_two_pass=y
audio_codec=libopus
audio_bitrate_kbps=128
```

SVT-AV1 gives excellent compression. CPU two-pass improves bitrate accuracy. AV1 emits `-b:v`
only (no HRD maxrate/bufsize), matching its rate control.

### C.5 Normalized lecture, sped up 1.5×

```ini
output_format=mp4
video_codec=H264
use_gpu=y
audio_tracks=0
audio_codec=aac
audio_bitrate_kbps=128
audio_sample_rate=48000
loudnorm=on
loudnorm_target_i=-16
video_speed=1.5
audio_speed=match_video
```

Speeds the video to 1.5× and keeps audio in sync, then normalizes loudness to a comfortable
level. Great for long talks/recordings.

### C.6 Extract one audio track to MP3

```ini
output_format=mp3
audio_tracks=0
audio_codec=libmp3lame
audio_bitrate_kbps=192
```

The video is dropped (audio-only container), leaving a single MP3.

### C.7 Loud-safe podcast master (-14 LUFS)

```ini
output_format=m4a
audio_tracks=0
audio_codec=aac
audio_bitrate_kbps=192
audio_sample_rate=48000
loudnorm=on
loudnorm_target_i=-14
```

### C.8 Downscale 4K to 720p for sharing

```ini
output_format=mp4
video_codec=H265
use_gpu=y
resolution=720p
video_bitrate_kbps=2500
audio_codec=aac
audio_bitrate_kbps=128
```

### C.9 Reverse a clip (boomerang-style)

```ini
output_format=mp4
video_codec=H264
use_gpu=y
reverse_video=y
audio_speed=match_video
```

### C.10 Strip everything except video + first audio

```ini
output_format=mp4
video_codec=copy
audio_tracks=0
audio_codec=copy
subtitle_tracks=none
keep_source_metadata=n
```

---

## Appendix D — FFmpeg concepts in depth

This appendix explains the ffmpeg concepts FFmWiz exposes, so the prompts make sense even if
you are new to video encoding.

### D.1 Containers vs codecs

A **container** (MP4, MKV, MOV, WebM, ...) is the file wrapper that holds streams and metadata.
A **codec** is how each stream is compressed (H.264, HEVC/H.265, AV1, VP9 for video; AAC, Opus,
MP3, FLAC, PCM for audio). The container limits which codecs and features are legal:

- **MP4/MOV** — H.264/HEVC/AV1 video, AAC/AC3 audio, `mov_text` subtitles only (image subs are
  dropped), supports faststart, needs `hvc1` tag for HEVC on Apple.
- **MKV** — almost any codec, all subtitle types, font/attachment streams, chapters.
- **WebM** — VP9/AV1 video, Opus/Vorbis audio, WebVTT subtitles.

Choosing a container in FFmWiz (`output_format`) therefore changes which downstream options are
valid.

### D.2 Stream copy vs re-encode

**Stream copy** (`-c copy`) repackages the existing bitstream without decoding it: instant,
lossless, but it cannot apply filters and cuts land on keyframes. **Re-encoding** decodes and
re-compresses, which is required for crop, scale, fps change, frame-accurate cuts, speed,
reverse, loudnorm, or hardsub. FFmWiz auto-promotes a `copy` request to a real encoder whenever
a filter is required, so you never get a silently broken command.

### D.3 Bitrate control: VBR, CRF, CQ

- **Average bitrate (VBR)** targets a size. FFmWiz's `quality_vbr` mode sets `-b:v X -maxrate 2X
  -bufsize 4X` (loose ceiling, better quality); `strict_size` sets `-b:v X -maxrate X -bufsize
  2X` (tight ceiling, predictable size).
- **CRF (Constant Rate Factor)** targets a quality level on CPU encoders (`-crf`); smaller is
  higher quality and larger files. NVENC's equivalent is `-cq:v`.
- **Two-pass** (CPU) and **multipass** (NVENC) analyze the video first so the encoder can
  distribute bits more accurately for a target average bitrate.

### D.4 Pixel formats and bit depth

A **pixel format** describes how color is stored. Common ones FFmWiz uses:

- `yuv420p` — 8-bit planar, the universal default for delivery.
- `nv12` — 8-bit, NVENC's native input layout.
- `p010le` — 10-bit, used for NVENC 10-bit input.
- `yuv420p10le` — 10-bit planar for CPU (libx265 Main10).

FFmWiz delivers **8-bit or 10-bit**: 8-bit sources stay 8-bit; 10-bit sources stay 10-bit;
12-bit-and-up sources are reduced to 10-bit (Main10) with a precision note.

### D.5 Color range and signaling

**Color range** is `tv`/limited (luma 16–235) or `pc`/full (0–255). The wrong flag makes video
look washed-out or crushed in some players. FFmWiz does **not** convert pixel values; it only
writes the correct **signaling**. When the source range is unknown and you re-encode, the
color-range prompt (or `color_range` config key) decides what gets written.

### D.6 faststart

For MP4/MOV, FFmWiz adds `-movflags +faststart`, moving the `moov` atom to the front so a player
can begin before the whole file downloads — important for web playback.

### D.7 Loudness normalization (EBU R128)

`loudnorm` measures perceived loudness in **LUFS** and adjusts gain toward a target integrated
loudness, with true-peak (TP) and loudness-range (LRA) ceilings. Single-pass applies the target
directly; two-pass measures first for more accurate results. Typical targets: `-14` LUFS
(streaming), `-23` LUFS (broadcast).

### D.8 Speed and reverse

Speed changes use `setpts` (video) and an `atempo` chain (audio), with `-fps_mode passthrough`
on the output so a faster picture keeps every frame instead of being resampled back to the
source rate (see §11). Reverse uses `reverse`/`areverse`. Both force a re-encode and FFmWiz
remaps chapter timestamps to the new timeline.

---

## Appendix E — Encoder and container compatibility

### E.1 Codec → encoder mapping

| Alias | CPU encoder | NVENC encoder | Typical containers |
|-------|-------------|---------------|--------------------|
| H264  | libx264     | h264_nvenc    | mp4, mkv, mov |
| H265  | libx265     | hevc_nvenc    | mp4 (hvc1), mkv, mov |
| AV1   | libsvtav1   | av1_nvenc     | mkv, webm, mp4 |
| VP9   | libvpx-vp9  | (none)        | webm, mkv |
| MPEG4 | mpeg4       | (none)        | mp4, mkv, avi |
| copy  | (passthrough) | (passthrough) | matching container |

NVENC is used only when `use_gpu=y` and your ffmpeg build + GPU/driver support that encoder.
Otherwise FFmWiz uses the CPU encoder.

### E.2 Multipass / two-pass support

| Encoder | NVENC multipass | CPU two-pass |
|---------|-----------------|--------------|
| h264_nvenc / hevc_nvenc / av1_nvenc | yes (`-multipass`, detected) | n/a |
| libx264 / libx265 | n/a | yes (`-pass 1/2`) |
| libsvtav1 / libaom-av1 | n/a | yes |
| libvpx-vp9 | n/a | yes |
| mpeg4 | n/a | yes |

Multipass support is detected at runtime from `ffmpeg -h encoder=<name>`; CPU two-pass uses a
verified encoder set. Two-pass is skipped for join/split/cut/speed/reverse workflows.

### E.3 Audio codec by container (typical defaults)

| Container | Default audio | Also valid |
|-----------|---------------|-----------|
| mp4 / m4a / mov | aac | ac3, alac |
| mkv | aac | libopus, flac, mp3, pcm |
| webm | libopus | libvorbis |
| mp3 | libmp3lame | — |
| opus | libopus | — |
| flac | flac | — |
| wav | pcm_s16le | — |

### E.4 Subtitles by container

| Container | Text subtitles | Image subtitles |
|-----------|----------------|------------------|
| mkv | ASS/SSA/SRT/WebVTT | PGS/VobSub kept |
| mp4 / mov | converted to `mov_text` | dropped |
| webm | WebVTT | dropped |

---

## Appendix F — Extended troubleshooting matrix

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Python was not found` from `run.ps1` | Python not installed / not in PATH | Install Python 3.10+ from python.org or `winget install Python.Python.3.12`, reopen the terminal. |
| `ffmpeg is not installed` | FFmpeg not on PATH | Accept the install prompt, or `winget install Gyan.FFmpeg`, then reopen the terminal. |
| GUI editor does not open | PySide6 missing | Accept the PySide6 install prompt, or `py -3 -m pip install --upgrade -r requirements.txt`. |
| GUI opens but reports an internal error | runtime/Qt issue | Set `FFMWIZ_DEBUG=1` and re-run to get a full traceback; attach the log. |
| No native playback for a clip | Qt Multimedia lacks that decoder | Timeline/frame extraction still work; use the manual `h:m:s:frame` flow in Mode 3 if needed. |
| NVENC not used despite `use_gpu=y` | ffmpeg build lacks NVENC, or GPU/driver unsupported | Check `ffmpeg -hide_banner -encoders`; FFmWiz falls back to CPU automatically. |
| `.ps1` will not run on double-click | Execution policy | `powershell -ExecutionPolicy Bypass -File ".\run.ps1"` or right-click → Run with PowerShell. |
| Multipass / two-pass prompt missing | Encoder does not support it, or workflow excludes it | Multipass needs an NVENC encoder exposing `-multipass`; CPU two-pass is skipped for join/split/cut/speed. |
| Output looks washed-out / crushed | Wrong color-range signaling | Set `color_range` (or answer the color-range prompt) to `tv` or `pc` to match the source. |
| Output bigger than expected | Bitrate above source, or loose VBR | Lower `video_bitrate_kbps`, or use `video_bitrate_mode=strict_size`. |
| Audio out of sync after speed change | Audio not following video | Use `audio_speed=match_video` so audio tracks the video speed/reverse. |
| Subtitles disappeared in MP4 | Image subs / MP4 limits | MP4 keeps only text subs (as `mov_text`); use MKV to keep PGS/VobSub. |
| Attachments (fonts) lost | Non-MKV output | Keep MKV output and set `keep_embedded_attachments=y`. |
| Mode 2 ignores my `crop` value | By design | Mode 2 always asks crop interactively; set the rest of the recipe in `config.env`. |
| Want to see what ffmpeg can do | Capability reference | FFmWiz writes `ffmwiz-ffmpeg-reference.txt` next to the script; delete it to regenerate. |

---

## Appendix G — Extended FAQ

**Q: Does Mode 1 use `config.env`?**
No. Mode 1 is the full interactive wizard and ignores the config file. `config.env` is for
Mode 2 (and the other config-driven paths).

**Q: Will FFmWiz overwrite my input file?**
No. It writes to a different path and adds a numeric suffix on name collisions.

**Q: Why was my `copy` codec changed to an encoder?**
A stream copy cannot be filtered. When you request crop/scale/fps/cut/split/speed/reverse,
FFmWiz promotes `copy` to a real encoder (H.265) so the filter can run.

**Q: What is the difference between `qres` and `fullres` multipass?**
Both are two-pass NVENC. `qres` runs the first (analysis) pass at quarter resolution (faster);
`fullres` runs it at full resolution (slightly slower, best quality).

**Q: Why can't I set two-pass loudnorm in `config.env`?**
Two-pass loudnorm needs a live loudness measurement of your specific file, which a static
config cannot supply. Config drives single-pass loudnorm with a target; use the interactive
prompt for measured two-pass.

**Q: How do I keep all audio tracks?**
Set `audio_tracks=all`. To drop duplicates/empty tracks, use `d`, `e`, or `de`.

**Q: How do I change only the container without re-encoding?**
Set `video_codec=copy` and `audio_codec=copy` and pick the new `output_format`.

**Q: Where are the logs?**
In the `Logs/` folder next to `FFmWiz.py`, one dated UTF-8 file per run. Control them with
`logging_enabled`, `log_retention_days`, and `log_level`.

**Q: How do I pick the modern editor?**
Set `gui_engine=qml` (or the `FFMWIZ_GUI_ENGINE=qml` environment variable). It falls back to
classic if the QML files are missing.

**Q: Which bitrate mode should I use?**
`quality_vbr` for the best quality at roughly a target size; `strict_size` when you must stay
close to a size budget.

**Q: How do I make a file for an Apple device?**
Use MP4 with H.265 (FFmWiz adds the `hvc1` tag) or H.264, and AAC audio.

---

## Appendix H — Worked example sessions

These transcripts show the shape of a typical run. Exact wording may vary slightly with your
FFmpeg build and source file; the flow and decisions are what matter.

### H.1 Mode 1 — 4K ultrawide lecture to 1080p H.265, normalized, 1.5×

```text
> .\run.ps1
                 FFmpeg Wizard (FFmWiz)
================================================
Prerequisite check:
  Python: 3.11.9 (OK)
  FFmpeg: found (...\ffmpeg.EXE)
  FFprobe: found (...\ffprobe.EXE)
  PySide6 (GUI): installed
FFmpeg: found.
GPU: detected - NVIDIA GeForce RTX 4070 Ti (NVENC hardware encoding).

[1] Drag and drop a file here or paste a path: I:\...\Bac13.mkv
Source file info
  Container: matroska,webm   Duration: 01:13:13   Size: 358.3 MB
  Video 0: h264 3440x1440 30fps 8-bit  Color range: TV
  Audio 0: aac 48000 Hz  (CONFIRMED duplicate of 1)
  Audio 1: aac 48000 Hz  (CONFIRMED duplicate of 0)
  Audio 2: aac 48000 Hz  (NEAR-EMPTY)

[2] Output destination [<input folder>]: <Enter>
[3] Output container [mkv]: mp4
[4] Video codec [H265]: <Enter>
[5] Use the GPU (NVENC)? [y]: y
[6] Open the graphical editor? [n]: n
[7] Crop? [n]: n
[8] Target video bitrate kbps [keep source]: 3500
[9] NVENC multipass [1=Disabled]: 3        # fullres
[10] Resolution [keep]: 1080p
[11] FPS [keep]: <Enter>
[12] Change speed / reverse? speed [1.0]: 1.5   reverse [n]: n
[13] Audio tracks [de]: de                 # drops the duplicate + near-empty
[14] Loudness normalization [1=Off]: 2     # single-pass
     Target LUFS [-16]: -16
[15] Audio codec [aac]: aac
[16] Audio bitrate kbps [keep]: 160
[17] Audio sample rate Hz [keep]: 48000
[18] Keep source metadata/chapters/subs? [y]: y
[19] Start FFmpeg now? [y]: y

Final PowerShell command:
  ffmpeg -i "...Bac13.mkv" -map 0:v:0 -vf "scale=...,setpts=(PTS-STARTPTS)/1.5"
    -fps_mode passthrough
    -c:v hevc_nvenc -preset p4 -tune hq -rc vbr -b:v 3500k -multipass fullres
    -map 0:a:0 -c:a aac -b:a 160k -ar 48000 -af "atempo=1.5,loudnorm=I=-16:..."
    -movflags +faststart "...\Bac13.mp4"
[#####.....] 41%  fps=...  speed=...  ETA 02:31
```

### H.2 Mode 2 — fixed recipe, only `crop` left blank in config.env

```text
> .\run.ps1
... Prerequisite check ...
Main menu: 2          # Wizard from config; only blank keys are asked
Loaded config.env (input_path=..., video_codec=H265, loudnorm=on, ...)
[1] Crop? top,left,right,bottom or n [n]: 0,140,140,0
Final PowerShell command: ffmpeg -i ... -vf "crop=..." -c:v hevc_nvenc ...
[#######...] 63% ...
```

### H.3 Mode 3 — lossless cut (stream copy)

```text
Main menu: 3
[1] Input: clip.mp4
[2] Output [<input folder>]: <Enter>
[3] Cut layout: keep one range
    Start h:m:s:frame: 0:1:30:0
    End   h:m:s:frame: 0:2:45:0
Final command: ffmpeg -ss 90 -i "clip.mp4" -t 75 -c copy "clip_cut.mp4"
```

### H.4 Mode 12 — join two clips

```text
Main menu: 12
[1] First input: a.mp4
[2] Join another? y -> b.mp4 ; Join another? n
Join input summary: 2 inputs, compatible -> stream copy concat
Final command: ffmpeg -f concat -safe 0 -i list.txt -c copy "a_joined.mp4"
```

### H.5 Mode 7 — media info report

```text
Main menu: 7
[1] File or folder: movie.mkv
Wrote: MediaReports\movie_info.txt and movie_info.html (dark mode)
```

---

## Appendix I — ffmpeg flags FFmWiz can emit

A reference for the main flags FFmWiz builds, so you can read or adapt the final command.

| Flag | Meaning | When FFmWiz emits it |
|------|---------|----------------------|
| `-i <input>` | Input file | Every job (one per input; joins use a concat list). |
| `-map 0:v:0` / `-map 0:a:N` | Select streams | Stream selection per your track choices. |
| `-c:v copy` | Copy video bitstream | `video_codec=copy` with no filters. |
| `-c:v libx264/libx265/libsvtav1/libvpx-vp9/mpeg4` | CPU video encoder | `use_gpu=n` or NVENC unavailable. |
| `-c:v h264_nvenc/hevc_nvenc/av1_nvenc` | NVENC encoder | `use_gpu=y` and supported. |
| `-preset p4 -tune hq -rc vbr` | NVENC quality settings | NVENC encodes. |
| `-multipass qres/fullres` | NVENC two-pass | `nvenc_multipass` on a capable NVENC encoder. |
| `-pass 1` / `-pass 2` | CPU two-pass | `cpu_two_pass=y` on a supported CPU encoder. |
| `-preset 6 -svtav1-params tune=0` | SVT-AV1 tuning | AV1 CPU encodes (libsvtav1). |
| `-b:v Xk -maxrate -bufsize` | Average bitrate + ceiling | `video_bitrate_kbps` set (shape from `video_bitrate_mode`). |
| `-crf` / `-cq:v` | Quality-based rate | Quality mode (CPU `-crf`, NVENC `-cq:v`). |
| `-vf "crop=..."` | Crop filter | Crop enabled (margins normalized to even sizes). |
| `-vf "scale=..."` / `scale_cuda` | Resize | `resolution` set (CUDA path when on GPU). |
| `-r N` / `fps=N` | Frame rate | `fps` set. |
| `-vf "setpts=..."` / `reverse` | Speed / reverse video | `video_speed`/`reverse_video`. |
| `-pix_fmt yuv420p/p010le/yuv420p10le` | Pixel format | Chosen for encoder + bit depth. |
| `-color_range tv/pc` | Range signaling | Resolved color range (no pixel conversion). |
| `-tag:v hvc1` | Apple HEVC tag | HEVC in MP4/MOV. |
| `-c:a aac/libopus/libmp3lame/flac/pcm_s16le/copy` | Audio codec | Per `audio_codec`. |
| `-b:a Xk` | Audio bitrate | Lossy audio codecs. |
| `-ar N` | Audio sample rate | `audio_sample_rate` set. |
| `-af "atempo=...,areverse,loudnorm=..."` | Audio filters | Speed/reverse/loudnorm. |
| `-map_metadata 0` / `-1` | Keep / drop metadata | `keep_source_metadata`. |
| `-map_chapters 0` / `-1` / `<idx>` | Chapters | Kept, dropped, or remapped to a modified timeline. |
| `-movflags +faststart` | Web-friendly MP4 | MP4/MOV outputs. |
| `-ss <start> -t <dur> -c copy` | Lossless cut | Mode 3 stream-copy cuts. |
| `-f concat -safe 0 -i list.txt` | Join inputs | Mode 12 compatible joins (also VFR joins that differ only in frame rate). |
| `-fps_mode vfr` | Keep variable frame rate | Re‑encoded joins where you declined to unify mixed source frame rates. |

---

---

## Appendix J — Command-line flags

FFmWiz is normally run with no arguments (interactive). These optional flags are recognized;
unknown arguments are ignored with a note.

| Flag | Alias | Effect |
|------|-------|--------|
| `--preview-colors` | `--preview-progress-colors` | Print the ANSI color palette used by prompts/progress and exit (no menu). Useful to check terminal color support. |
| `--refresh-ffmpeg-reference` | `--regen-ffmpeg-reference` | Force-regenerate `ffmwiz-ffmpeg-reference.txt` from your installed `ffmpeg.exe` at startup, then continue normally. |

Examples:

```powershell
py -3 .\FFmWiz.py --preview-colors
py -3 .\FFmWiz.py --refresh-ffmpeg-reference
.\run.ps1 --refresh-ffmpeg-reference
```

Internal flags `--request` / `--reply` are used only when FFmWiz launches the Qt GUI as a
subprocess (JSON IPC); you never pass them by hand.

---

## Appendix K — Environment variables

These environment variables tune behavior. Set them in the shell before launching (for example
`$env:FFMWIZ_GUI_ENGINE = "qml"` in PowerShell). All are optional.

### Behavior

| Variable | Values | Effect |
|----------|--------|--------|
| `FFMWIZ_GUI_ENGINE` | classic / qml | Overrides the `gui_engine` config value for the unified editor. |
| `FFMWIZ_DEBUG` | set / unset | When set, also prints GUI subprocess tracebacks to the console (in addition to the log). |
| `FFMWIZ_NO_AUTO_INSTALL` | set / unset | Skip the PySide6 install prompt entirely; GUI editors stay unavailable until you install it yourself. |
| `FFMWIZ_AUTO_INSTALL` / `FFMWIZ_AUTO_INSTALL_PYSIDE` | set / unset | Install PySide6 without asking (good for unattended/CI). |
| `FFMWIZ_AUTO_INSTALL_FFMPEG` | set / unset | Allow the FFmpeg auto-install fallback to proceed without asking. |
| `FFMWIZ_LOG_LEVEL` | debug / info / warning / error / critical | Overrides the `log_level` config key for the run's log file; an unrecognized value falls back to `debug`. |
| `FFMWIZ_CACHE_DIR` | directory path | Relocate the FFmpeg capability cache (default `ffmwiz/support/.cache/`). Useful for an isolated or throwaway run; the test suite uses it for isolation. |
| `FFMWIZ_ALLOW_ESTIMATED_STREAM_SIZES` | set / unset | When a stream's real size cannot be measured from packets or trusted tags, fall back to `bitrate x duration` instead of reporting it as unknown. Changes reported sizes, so it is off by default. |
| `NO_COLOR` | set / unset | Standard "no color" convention; disables ANSI coloring of console output. |
| `QT_QPA_PLATFORM` | e.g. `offscreen` | Standard Qt platform selector; `offscreen` runs the GUI headless (used for tests). |

### Diagnostics

| Variable | Values | Effect |
|----------|--------|--------|
| `FFMWIZ_DEBUG_GUI` | set / unset | Same effect as `FFMWIZ_DEBUG` for the GUI subprocess: forces GUI debug logging on its own. |
| `FFMWIZ_DEBUG_PROGRESS` | set / unset | Log every rendered progress line (stripped of ANSI) to the run log. Very verbose; use it only when diagnosing the progress display. |
| `FFMWIZ_DEBUG_COORDS` | set / unset | Log crop-editor zoom/pan coordinate math (focus point, zoom factor, scroll offset) while dragging. |
| `FFMWIZ_QML_SELFTEST` | `1` | Run the QML unified editor headlessly and exit — a smoke check for the QtQuick engine. Pair it with `QT_QPA_PLATFORM=offscreen`. |
| `FFMWIZ_QML_SHOT` | file path | With `FFMWIZ_QML_SELFTEST=1`, save a PNG of the editor's rendered scene to that path and exit. Under `QT_QPA_PLATFORM=offscreen` no window is ever created, so the interface can be reviewed visually without it appearing on screen. |
| `FFMWIZ_QML_SHOT_SIZE` | e.g. `1600x980` | Resize the editor before `FFMWIZ_QML_SHOT` grabs it. The offscreen platform's virtual screen is a fixed 800x800, which clamps the grab unless the window is resized first. |
| `FFMWIZ_GUI_SHOT` | file path | The classic editor's counterpart of `FFMWIZ_QML_SHOT`: render the window to a PNG and exit instead of running the editor. Pair it with `QT_QPA_PLATFORM=offscreen`, where no window is created at all. |
| `FFMWIZ_GUI_SHOT_SIZE` | e.g. `1600x980` | Resize the classic window before `FFMWIZ_GUI_SHOT` grabs it. |

Names such as `FFMWIZ_GUI_DIR_NAME`, `FFMWIZ_GUI_FILE_NAME`, and `FFMWIZ_RUNTIME_DIR_NAME`
appear in the source but are **internal Python constants**, not environment variables — setting
them in your shell has no effect. The tables above list every variable FFmWiz actually reads
from the environment.

### Performance tuning (advanced)

| Variable | Default | Effect |
|----------|---------|--------|
| `FFMWIZ_PACKET_SCAN_MAX_MB` | 64 | Max megabytes scanned when estimating per-stream sizes from packets. |
| `FFMWIZ_DUP_HASH_SECONDS` | 8.0 | Seconds of audio hashed when confirming duplicate audio tracks. |
| `FFMWIZ_DUP_HASH_WORKERS` | 2 | Parallel workers for duplicate-audio hashing. |
| `FFMWIZ_VOLUME_SCAN_WORKERS` | 3 | Parallel workers for audio mean/max volume scans. |
| `FFMWIZ_FOLDER_PROBE_WORKERS` | 4 | Parallel workers for probing files in Folder Encode. |
| `FFMWIZ_REVERSE_PEAK_BUDGET_MB` | 2048 | Peak megabytes one reverse segment may occupy: the reserved 512 MiB decoder/encoder working set plus its decoded frames. Lower it on a small machine, raise it to reverse a frame the default cannot hold. |

Larger worker counts speed up scanning of many tracks/files on fast disks and CPUs; lower them
on slow storage or to reduce load. The packet-scan cap balances size-estimate accuracy against
probe time on very large files.

`FFMWIZ_REVERSE_PEAK_BUDGET_MB` is a **hard cap**, not a hint: the reverse segment is planned as
a whole number of decoded frames that fits inside it, and a plan that cannot fit is refused
rather than shortened to something that does not. Setting it below the 512 MiB reserved overhead
is rejected outright, since that leaves nothing for frames. Raising it to fit an unusually large
frame is the supported way to reverse 12-bit 4:4:4 8K and above.

---

## Appendix L — Output naming and folder behavior

FFmWiz never overwrites your input and follows predictable naming rules.

- **Single output** — written to `output_path` if given, otherwise the input's folder using the
  chosen `output_format`. If the chosen name would collide with an existing file (or equal the
  input), a numeric suffix is appended so nothing is overwritten.
- **Split parts** — when split points are set, parts are named `<base>_Part01`, `<base>_Part02`,
  ... in order along the processed timeline.
- **Folder Encode (Mode 4)** — outputs go into a sibling folder named `<folder>_Encode`,
  preserving each input's base name with the chosen output format.
- **Stream Cleanup Remux (Mode 8)** — writes into an output base folder, mirroring the input
  folder structure for recursive scans.
- **Media info (Mode 7)** — reports are written to `MediaReports/<name>_info.txt` and
  `MediaReports/<name>_info.html` (plus a raw ffprobe JSON sidecar). Folder mode writes one
  report per readable file.
- **Metadata reports (Mode 13 / inspect)** — written next to the source with suffixes like
  `_metadata_report`, `_metadata_tags`, `_metadata_color`, `_metadata_disposition`,
  `_metadata_chapters`.
- **Capability reference** — `ffmwiz-ffmpeg-reference.txt` is generated next to `FFmWiz.py`
  (delete it or use `--refresh-ffmpeg-reference` to regenerate).
- **Logs** — dated UTF-8 files in `Logs/` next to the script.

All generated/output folders (`Logs/`, `MediaReports/`, `output/`, `*_Encode`, the capability
reference, and caches) are git-ignored so your repository stays clean.

---
