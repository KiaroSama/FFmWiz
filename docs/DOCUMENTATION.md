# FFmWiz — Complete Documentation

This is the authoritative, full reference for **FFmWiz**, a Windows‑focused interactive
FFmpeg command builder and media toolkit. It explains every startup mode, every prompt
and option, the GUI editors, the `config.env` settings file, encoding behavior
(NVENC/CPU, AV1, multipass/two‑pass, color range, bit depth), cutting/splitting/joining,
speed/reverse, metadata editing, logging, and troubleshooting.

The [README](../README.md) is a quick overview. **This document is the complete guide.**

FFmWiz never modifies your input file. The final FFmpeg command is always shown before it
runs, and you can cancel.

---

## Table of contents

1. [Requirements](#1-requirements)
2. [Installation](#2-installation)
3. [Running FFmWiz](#3-running-ffmwiz)
4. [The startup menu (modes 1–13)](#4-the-startup-menu-modes-113)
5. [Mode 1 — Interactive wizard](#5-mode-1--interactive-wizard)
6. [Mode 2 — Load config and ask crop only](#6-mode-2--load-config-and-ask-crop-only)
7. [The `config.env` file (full reference)](#7-the-configenv-file-full-reference)
8. [Video encoding reference](#8-video-encoding-reference)
9. [Audio reference](#9-audio-reference)
10. [Cutting, splitting, and joining](#10-cutting-splitting-and-joining)
11. [Speed and reverse](#11-speed-and-reverse)
12. [The graphical editors (classic and QML)](#12-the-graphical-editors-classic-and-qml)
13. [Modes 3–13 in detail](#13-modes-313-in-detail)
14. [Source‑value warnings](#14-source-value-warnings)
15. [Progress display](#15-progress-display)
16. [Logging](#16-logging)
17. [Troubleshooting / FAQ](#17-troubleshooting--faq)
18. [Glossary](#18-glossary)

---

## 1. Requirements

- **Windows 11** is the primary target (Windows 10 generally works).
- **Python 3.10+**, available as `py -3` or `python` on `PATH`.
- **FFmpeg** and **ffprobe** on `PATH`. On startup FFmWiz checks for them and can offer to
  install the full FFmpeg package via `winget` or Chocolatey.
- **PySide6** for the graphical editors (Unified Video Editor, Speed/Reverse, Audio editor):

  ```powershell
  py -3 -m pip install --upgrade -r requirements.txt
  ```

  Without PySide6 the CLI still works; only the GUI windows are unavailable.
- **NVIDIA GPU + driver** and an FFmpeg build compiled with CUDA/NVENC for GPU acceleration.
  FFmWiz silently falls back to CPU encoding when NVENC is not available for the chosen codec.

The core CLI uses only the Python standard library; PySide6 is the only extra runtime package.

---

## 2. Installation

1. Clone or download the repository, keeping `FFmWiz.py`, `run.ps1`, and the `assets/` folder
   together in the same directory.
2. Install the optional GUI dependency:

   ```powershell
   py -3 -m pip install --upgrade -r requirements.txt
   ```

3. (Optional) Install the global terminal command so you can type `FFmWiz` anywhere:

   ```powershell
   powershell -ExecutionPolicy Bypass -File ".\install-command.ps1"
   ```

   This adds the local `Commands` folder to your user `PATH` and installs a marked `FFmWiz`
   function in your PowerShell profile. The PATH shim runs PowerShell with
   `-ExecutionPolicy Bypass`, so it works even when profile script execution is restricted.

4. (Optional) Copy the sample config so Mode 2 has defaults to load:

   ```powershell
   Copy-Item config.env.example config.env
   ```

   `config.env` is git‑ignored (it holds your personal paths); only `config.env.example`
   is published.

---

## 3. Running FFmWiz

From the project root in PowerShell:

```powershell
.\run.ps1            # canonical launcher (recommended)
py -3 .\FFmWiz.py    # run Python directly
```

If your execution policy blocks `.ps1` files:

```powershell
powershell -ExecutionPolicy Bypass -File ".\run.ps1"
```

`run.ps1` resolves paths relative to itself (works from any working directory), prefers
`py -3`, then `python`, then `python3`, forwards all arguments unchanged, and returns the
Python exit code.

At any prompt you can type:

- `exit` (or `quit`) — leave FFmWiz.
- `0` or `back` — go back one step (where supported).
- Press **Enter** — accept the default shown in green brackets like `[1]`.

After each completed FFmpeg run, FFmWiz returns to the startup menu so you can build another
command without reopening the script.

### Command‑line flags

- `--preview-colors` — print the ANSI color palette used by prompts/progress and exit.
- Environment variables:
  - `FFMWIZ_GUI_ENGINE=classic|qml` — choose the unified‑editor engine (overrides `config.env`).
  - `FFMWIZ_DEBUG=1` — print full tracebacks if a GUI reports an internal error.

---

## 4. The startup menu (modes 1–13)

```text
1  = Interactive wizard
2  = Load config and ask crop only
3  = Cut video only with copy
4  = Folder Encode
5  = Add files to video
6  = Extract Stream
7  = Media info report
8  = Stream Cleanup Remux
9  = Hard Sub Encode
10 = Video Speed / Reverse
11 = Audio Cut / Speed / Reverse
12 = Join Videos
13 = Metadata Editor
```

Mode 1 is the default (press Enter). The sections below explain each mode.

---

## 5. Mode 1 — Interactive wizard

The full wizard inspects the source and asks every relevant question. For video inputs it can
open the **Unified Video Editor** (crop, cuts, split points, waveform, speed/reverse in one
window). If you decline the editor, the same edits are available as terminal prompts.

Typical question order for a video re‑encode:

1. **Input file path** — paste a path or drag‑and‑drop the file into the terminal, then Enter.
   Unicode/Persian paths are supported.
2. **Output path** — a folder, a full file path, or a bare base name (placed in the input
   folder using the chosen format). Empty = input folder. The input is never overwritten;
   on a name collision FFmWiz appends `_Encode` (encode) or `_cut` (cut‑only), and a numeric
   `(2)` suffix if needed.
3. **Output format** — container extension without the dot (`mp4`, `mkv`, `mov`, `webm`,
   `mp3`, `m4a`, ...). `n` inherits the input extension. Unknown formats are rejected with a
   suggestion.
4. **Video codec** — `H265`, `H264`, `AV1`, `VP9`, `MPEG4`, `copy`, or any encoder name.
5. **GPU usage** — `y` uses NVENC/CUDA when supported; otherwise CPU.
6. **Crop** — off / on with margins / inline `top,left,right,bottom`.
7. **Video bitrate** (or **CRF/quality** if you choose quality mode).
8. **NVENC multipass** — shown only for NVENC encoders that support it (see §8).
9. **CPU two‑pass** — shown only for CPU encoders that support it (see §8).
10. **Resolution** — preset (`480p`/`720p`/`1080p`), `w1280`/`720h`, `WIDTHxHEIGHT`,
    `stretch:WIDTHxHEIGHT`, or `n` to keep source.
11. **FPS** — integer, or `n` to keep source.
12. **Audio tracks** — which streams to keep (`0`, `0,1,2`, `all`, `d`, `e`, `de`).
13. **Audio codec** — `aac`, `libopus`, `libmp3lame`, `flac`, `pcm_s16le`, `copy`, ...
14. **Audio bitrate**.
15. **Audio sample rate (Hz)** — e.g. `48000`; `n` keeps the source rate (see §9).
16. **Subtitle tracks** (when keeping source metadata).
17. **Keep source metadata / extra streams** — keep vs strip metadata, chapters, extra
    video/data streams, attachments.
18. **Start confirmation** — the final PowerShell command is shown; confirm to run.

`copy` is incompatible with filters (crop/scale/fps/setparams/cuts) and is auto‑promoted to
H.265 when a filter is required.

---

## 6. Mode 2 — Load config and ask crop only

Mode 2 reads default answers from **`config.env`** (see §7), then asks **only** the crop
question. Use it when your encode recipe is fixed and only the crop changes per file. If
`config.env` is missing, it is created from the template on first run. `input_path` is
required in `config.env` for this mode.

---

## 7. The `config.env` file (full reference)

`config.env` is a **dotenv‑style** file (replacing the old `config.json`). It stores the
default answers Mode 2 loads.

- One setting per line: `key=value`.
- Lines starting with `#` are comments; blank lines are ignored.
- Values are **not quoted** (surrounding quotes are stripped if present). Paths may contain
  spaces and Unicode directly. Forward slashes work on Windows; backslashes also work and do
  **not** need escaping.
- An optional leading `export ` is accepted.
- Missing keys / empty values fall back to interactive defaults.
- Use `n` where supported to mean "keep source / no change".
- Booleans accept `y/yes/true/1/on` or `n/no/false/0/off`.

`config.env` is git‑ignored (personal); the committed template is `config.env.example`.

### Settings

| Key | Values | Default | Meaning |
|-----|--------|---------|---------|
| `input_path` | absolute path | (empty) | Source file. **Required** for Mode 2. |
| `output_path` | folder / file / base name | (empty) | Output destination. Empty = input folder. |
| `output_format` | mp4, mkv, mov, webm, mp3, m4a, opus, flac, ... or `n` | `n` | Output container; `n` inherits input. |
| `video_codec` | H265, H264, AV1, VP9, MPEG4, copy, or encoder name | `H265` | Video encoder (aliases map to CPU/NVENC). |
| `use_gpu` | y/n | `y` | Use NVENC/CUDA when supported. |
| `crop` | n / y / `top,left,right,bottom` | `n` | Crop margins (pixels removed). Mode 2 always asks crop interactively. |
| `crop_top`/`crop_left`/`crop_right`/`crop_bottom` | integer ≥ 0 | `0` | Per‑side crop when `crop=y`. |
| `video_bitrate_kbps` | integer or `n` | `n` | Target average video bitrate (kbps). |
| `video_bitrate_mode` | quality_vbr / strict_size | `quality_vbr` | VBR shape (see §8). |
| `resolution` | preset / `w1280` / `720h` / `WxH` / `stretch:WxH` / `n` | `n` | Output scale. |
| `fps` | integer or `n` | `n` | Output frame rate. |
| `audio_tracks` | 0 / 0,1,2 / all / d / e / de | `de` | Which audio streams to keep. |
| `audio_codec` | aac, libopus, opus, libmp3lame, flac, pcm_s16le, copy, ... | `aac` | Audio encoder. |
| `audio_bitrate_kbps` | integer or `n` | `n` | Audio bitrate per stream (kbps). |
| `keep_source_metadata` | y/n | `y` | Keep metadata/chapters/extra streams + subtitle selection. |
| `subtitle_tracks` | selection / none / clear / delete | `none` | Which subtitles to keep. |
| `keep_embedded_attachments` | y/n | `n` | Keep MKV attachment streams (fonts). |
| `detect_duplicate_audio` | y/n | `y` | Flag duplicate/empty audio for d/e/de. |
| `logging_enabled` | y/n | `y` | Write dated logs into `logs/`. |
| `log_retention_days` | integer | `0` | `0` keeps forever; N deletes logs older than N days. |
| `gui_engine` | classic / qml | `classic` | Unified‑editor engine (env `FFMWIZ_GUI_ENGINE` overrides). |

### Example recipes

```ini
# Fast stream copy, same container (no re-encode)
output_format=n
video_codec=copy
use_gpu=n
audio_codec=copy
subtitle_tracks=none
```

```ini
# 1080p H.265 NVENC, AAC 160k
output_format=mp4
video_codec=H265
use_gpu=y
resolution=1080p
video_bitrate_kbps=4500
audio_codec=aac
audio_bitrate_kbps=160
```

```ini
# AV1 (CPU, SVT-AV1) WebM with Opus
output_format=webm
video_codec=AV1
use_gpu=n
video_bitrate_kbps=2500
audio_codec=libopus
audio_bitrate_kbps=96
```

```ini
# Audio only: extract first track as AAC m4a
output_format=m4a
audio_tracks=0
audio_codec=aac
audio_bitrate_kbps=192
subtitle_tracks=none
```

---

## 8. Video encoding reference

### Codecs and encoder mapping

| Alias | CPU encoder | GPU (NVENC) encoder |
|-------|-------------|---------------------|
| `H265` / `HEVC` | `libx265` | `hevc_nvenc` |
| `H264` / `AVC` | `libx264` | `h264_nvenc` |
| `AV1` | `libsvtav1` | `av1_nvenc` |
| `VP9` | `libvpx-vp9` | (CPU only) |
| `MPEG4` | `mpeg4` | (CPU only) |
| `copy` | stream copy (no encode) | — |

You may also type any encoder name from `ffmpeg -encoders` directly.

**AV1 on CPU uses SVT‑AV1 (`libsvtav1`)**, the recommended fast AV1 encoder. FFmWiz applies
`-preset 6` (a balanced speed/quality preset on SVT‑AV1's 0–13 scale) and
`-svtav1-params tune=0` (subjective visual quality). For bitrate targets it uses simple VBR
(`-b:v`) without HRD `-maxrate`/`-bufsize`, matching SVT‑AV1's rate control.

### Bitrate vs quality (CRF)

- **Bitrate mode** targets an average size. `video_bitrate_mode`:
  - `quality_vbr` → `-b:v X -maxrate:v 2X -bufsize:v 4X` (looser, better quality).
  - `strict_size` → `-b:v X -maxrate:v X -bufsize:v 2X` (tighter size control).
- **Quality/CRF mode** targets a perceptual quality level. On NVENC this maps to `constqp`
  with `-cq:v`; on CPU encoders it uses `-crf`.

### NVENC settings

NVENC encodes use `-preset p4 -tune hq -rc vbr` by default. HEVC adds the appropriate profile
(`main`/`main10`). For HEVC→MP4, `-tag:v hvc1` is added for Apple compatibility.

### NVENC multipass

The wizard asks **"Use NVENC multipass?"** only for encoders that actually expose the
`-multipass` option. FFmWiz **detects this dynamically** by probing
`ffmpeg -h encoder=<name>` (cached per encoder), so any current/future capable encoder is
covered — not a hardcoded list. On current builds this is `h264_nvenc`, `hevc_nvenc`, and
`av1_nvenc`.

```text
1 = Disabled / fastest
2 = qres / quarter-resolution first pass
3 = fullres / best quality, slower
```

### CPU two‑pass

For CPU encoders, the wizard asks **"Use two‑pass CPU video encoding?"**. Two‑pass runs an
analysis pass then a final pass for a more accurate target bitrate. It is enabled for the CPU
encoders verified to support FFmpeg's `-pass 1/2` mechanism:
`libx264`, `libx265`, `libvpx-vp9`, `libaom-av1`, `libsvtav1`, and `mpeg4`. The first pass
writes stats to a temporary `-passlogfile` and the encoder‑specific params
(`-x264-params`/`-x265-params`/`-svtav1-params`/`-aom-params`) are preserved across both
passes. Two‑pass is skipped for joins, splits, cuts, and speed/reverse workflows.

### Resolution

- Presets (`144p`…`2160p`) and plain numbers (`480`) scale to the closest matching edge while
  preserving aspect ratio.
- `w1280` / `1280w` set an explicit width; `h720` / `720h` set an explicit height.
- `WIDTHxHEIGHT` fits inside a target box preserving aspect ratio.
- `stretch:WIDTHxHEIGHT` forces exact dimensions (intentional distortion).
- `n` keeps the source/cropped size. SAR is forced to 1 by default; you are warned before
  upscaling above the source resolution.

### Crop

Crop margins are **pixels removed** from each side (`top,left,right,bottom`), not x/y offsets.
The Unified Editor lets you drag edges/corners on the preview. On confirm, FFmWiz snaps crop
margins so the output width/height stay **even** (chroma‑safe).

### Color range and pixel format / bit depth

- Default color range is `tv` (limited) for compatibility; `pc` (full) is available.
- 10‑bit sources are preserved: HEVC NVENC uses `p010le` (or `scale_cuda=format=p010le` on the
  CUDA graph), libx265 uses `yuv420p10le`. H.264 cannot safely carry >10‑bit here, so FFmWiz
  switches to H.265 to preserve high bit depth and tells you.
- 12‑bit+ is capped to 10‑bit (Main10) with a precision note.

### faststart

MP4/MOV outputs get `-movflags +faststart` so playback can begin before the whole file
downloads.

---

## 9. Audio reference

### Codecs

`aac`, `libopus` (the `opus` alias normalizes to `libopus`), `libmp3lame`, `flac`,
`pcm_s16le`, `copy`, or any encoder name. Container rules are enforced automatically: WebM
forces `libopus`; `flac`/`pcm_*` ignore bitrate; `copy` skips re‑encoding.

### Track selection and duplicate/empty detection

`audio_tracks` accepts explicit indices (`0`, `0,1,2`), `all`, or the cleanup shortcuts:

- `d` — drop confirmed duplicate tracks.
- `e` — drop empty / near‑empty tracks.
- `de` — both (default).

When `detect_duplicate_audio=y`, FFmWiz uses stream metadata plus exact packet sizes to flag
empty/near‑empty tracks, prechecks likely duplicates with short sampled hashes, then confirms
with a full audio hash before dropping anything.

### Sample rate (Hz)

For re‑encoded audio the wizard asks **"Enter audio sample rate in Hz"** (examples 44100,
48000, 96000). Press Enter or `n` to keep the source rate. The chosen rate is applied with
`-ar` across all audio paths (encode, track manager, speed/reverse). For **Join**, all inputs
are resampled to one uniform rate so the joined output is consistent. If you choose a rate
**higher than the source**, FFmWiz warns (default `n`) because upsampling cannot add real
detail and only grows the file — the same warning behavior as video bitrate, FPS, and
resolution.

### Loudness normalization (loudnorm)

The Track Manager and several flows offer EBU R128 loudness normalization:

```text
1 = Off
2 = Single-pass loudnorm
3 = Two-pass loudnorm (measured first, more accurate)
```

Two‑pass measures the integrated loudness/true‑peak first, then applies a linear correction.
For Join, the measurement analyzes the full joined audio timeline so it matches the encoded
output.

---

## 10. Cutting, splitting, and joining

### Time format

Cut times are entered as `h:m:s:frame` and converted to seconds using the file's detected FPS
(`avg_frame_rate`, falling back to `r_frame_rate`). Fractional rates like `24000/1001` are
handled. Examples:

```text
00:00:10:15  -> 10.5 s at 30 fps
00:00:10:12  -> 10.5005 s at 24000/1001
0:0:10:45    -> 11.5 s at 30 fps (frame overflow normalized)
```

### Cut layouts

```text
1 = Keep one range
2 = Remove one range
3 = Remove multiple ranges  (kept ranges are computed by inverting)
4 = Keep multiple ranges
```

### Stream‑copy cuts (Mode 3)

- One range: a single `ffmpeg -ss <start> -i <input> -t <duration> -c copy` call.
- Multiple ranges: each kept range is extracted to an MKV segment with `-c copy`, then joined
  through the `concat` demuxer. `-map 0`, `-map_metadata 0`, and `-c copy` preserve all
  streams and metadata. Chapters are remapped or rebuilt around removed ranges.
- Stream copy is **keyframe‑bound**; cut points snap to nearby keyframes. Use Mode 1 for
  frame‑accurate cuts (re‑encode).

### Split points

Add split points to cut one timeline into multiple output parts (`_Part01`, `_Part02`, ...).
Splitting is performed in a single FFmpeg command with a filter graph; progress is
reconstructed per part (see §15).

### Join (Mode 12)

Join multiple videos. FFmWiz stream‑copies when the inputs are compatible, otherwise
re‑encodes. The join input summary reports per‑input colors, total raw duration and frame
count, and mean/max volume extremes. Audio is resampled to one uniform sample rate.

---

## 11. Speed and reverse

Speed/reverse is available in Mode 1, Mode 4 (folder), the standalone Mode 10 (video) and
Mode 11 (audio), and inside the Unified Editor. Export filters:

- video speed: `setpts=(PTS-STARTPTS)/speed`
- video reverse: `reverse,setpts=(PTS-STARTPTS)/speed`
- audio speed: `atempo`, split into safe chained stages when outside one stage's range
- audio reverse: `areverse`

Because FFmpeg's `reverse`/`areverse` buffer the whole clip in memory, FFmWiz reverses video
in short segments and concatenates them in reverse order to avoid RAM spikes.

---

## 12. The graphical editors (classic and QML)

The Unified Video Editor combines crop, cuts, split points, an audio waveform, timeline
zoom/pan, and speed/reverse in one Premiere‑style workspace. It runs as a separate process via
JSON request/reply files, keeping the Qt event loop isolated from the CLI.

### Choosing the engine

- `classic` (default): stable PySide6‑widgets editor with the full feature set.
- `qml`: modern QtQuick editor (GPU scene graph, no white flash, aspect‑correct preview).

Select via `config.env` `gui_engine=classic|qml` or env `FFMWIZ_GUI_ENGINE=qml` (overrides
config). The QML engine handles only the unified editor; other GUI modes always use classic.
If the QML files are missing, FFmWiz falls back to classic automatically.

### Shared capabilities

Both engines provide: video preview with crop overlay (draggable edges **and corners**),
a timeline with **time‑ruler labels**, audio waveform, mark in/out, multi‑range cuts, split
points, magnetic snapping, frame‑accurate scrubbing, timeline zoom/pan (wheel, slider,
`−`/`+`/`Fit`), volume + mute, full transport (Home/End, ±1s, ±5s, prev/next cut edge), speed
(editable up to 10×), reverse, include‑audio, undo/redo, and confirm/cancel. The playhead is
an independent overlay so playback never regenerates the waveform.

### Waveform model

Audio is decoded once to **4000 Hz mono PCM** and cached, plus a decimated min/max envelope.
The editor requests per‑viewport **min/max** amplitude pairs (one per pixel column) only when
the zoom/pan/width changes — heavy work stays in the backend. Amplitudes are scaled against
int16 full scale (32768), so quiet stays quiet and loud stays loud. Deep zoom reads raw PCM
for full detail; zoomed‑out views use the envelope.

### Keyboard shortcuts (Unified Editor)

```text
Space            Play / Pause
I / O            Mark In / Mark Out
A                Add cut
S                Add split
Delete           Delete the selected marker / split / cut
M                Mute / unmute
Ctrl+I / Ctrl+O  Convert selected marker In<->Out
Ctrl+Shift+I     Invert cuts
Ctrl+R           Reset crop
Ctrl+U           Toggle crop overlay
Ctrl+Z / Ctrl+Y  Undo / Redo (Ctrl+Shift+Z also redo)
Home / End       Jump to start / end
Left / Right     Seek -1s / +1s
Shift+Left/Right Seek -5s / +5s
Ctrl+Alt+Left/Right  Previous / next cut edge
H / Z            Hand tool / Zoom tool (preview pan & zoom)
Ctrl + / Ctrl -  Preview zoom in / out;  Ctrl+0 reset preview view
+ / = / -        Timeline zoom
Enter            Confirm and return to the CLI
Esc              Cancel
```

Live reverse preview (when **Reverse** is on) renders short reversed proxy windows on demand
and plays them back while the CTI moves forward.

---

## 13. Modes 3–13 in detail

### Mode 3 — Cut video only with copy
Dedicated stream‑copy cut tool (no re‑encode; very fast, lossless, keyframe‑bound). Uses the
manual `h:m:s:frame` layouts above. See §10.

### Mode 4 — Folder Encode
Encodes every supported audio/video file in a folder with one shared settings pass. Shows a
per‑file media summary (codec/size/fps/bit depth/color range/bitrate, plus each audio track's
codec/sample rate/bitrate/size), asks one set of settings, then runs FFmpeg sequentially.
Graphical editors are disabled here so identical settings apply to every file. Source‑value
warnings use the **lowest** detected value in the folder. Empty output folder → a sibling
`<folder>_Encode`.

### Mode 5 — Add files to video
Adds external audio/subtitle files as extra soft tracks **without re‑encoding**. The first
input is the source video; add one audio/subtitle file at a time, optionally setting
`language,title` metadata per added stream (e.g. `eng,English commentary`,
`fas,Persian subtitle`). External audio's first stream is mapped explicitly (`-map 1:a:0`).
Cover‑art/MJPEG streams in audio files are ignored, not added as video. Pure remux: the output
extension always matches the source video; incompatible muxes are rejected with a clear error.
`0` goes back one step; `done` finishes.

### Mode 6 — Extract Stream
Extracts one stream by ffprobe index (`-map 0:<index>`). Audio/video are stream‑copied when
possible; text subtitles needing a portable format are exported as `.srt`.

### Mode 7 — Media info report
Writes detailed ffprobe (+ optional read‑only ffmpeg) reports. For one file it prints an
organized report and saves TXT + dark‑mode HTML to `MediaReports/<name>_info.txt`/`.html`,
plus sidecars (raw ffprobe JSON, stream‑summary CSV, and optional per‑second bitrate/GOP CSVs).
Optional PSNR/SSIM/VMAF vs a reference and sample screenshots. For a folder it scans
recursively and writes one report per readable file.

### Mode 8 — Stream Cleanup Remux
Remuxes with `-c copy` keeping only selected audio/subtitle streams. Scans one file or a folder
recursively; prints indexes, languages, titles, codecs, channels, default flags, size
estimates, and a unique‑stream summary; selects by index or language/title rules. Can edit
kept‑stream metadata, keep MKV font attachments, keep/remove metadata+chapters, copy unchanged
videos with `robocopy`, and copy non‑video side files. Output suffix matches each input; no
container/subtitle/audio conversion.

### Mode 9 — Hard Sub Encode
Burns internal or external subtitles into the video (re‑encodes). Internal HardSub accepts text
subtitle codecs (ASS/SSA/SRT/WebVTT) and rejects bitmap subs (PGS/VobSub/DVDSub). ASS/SSA
`[Fonts]` are detected; `fontsdir` is optional. Audio is copied when the container matches; for
a different output container you can copy, transcode to AAC stereo, change format, or drop
audio. HDR/Dolby Vision are detected: preserve best‑effort, tone‑map to SDR, or encode
normally (Dolby Vision dynamic metadata cannot be reliably preserved after hard‑sub re‑encode).

### Mode 10 — Video Speed / Reverse
See §11. Opens the Qt speed editor (or terminal entry). Used standalone and inside Mode 1/4.

### Mode 11 — Audio Cut / Speed / Reverse
Combined audio editor: waveform cut editor + speed/reverse in one window. Mark ranges to
remove (Mark In/Out/Add Cut); output keeps everything outside the marked ranges. Includes
waveform zoom, time ticks, invert cuts, undo/redo. Also available for audio‑only outputs in
Mode 1/4.

### Mode 12 — Join Videos
See §10. Stream‑copy when compatible, otherwise re‑encode; uniform audio sample rate; rich
join input summary.

### Mode 13 — Metadata Editor
Inspects streams/chapters/tags/dispositions/attached pictures/bitstream metadata via ffprobe,
then applies targeted stream‑copy changes with `-map 0 -c copy`. Edits per‑stream
title/language/custom tags, dispositions, chapter metadata, attached pictures, H.264/HEVC
bitstream color/SAR metadata, and metadata reports. (No global file‑level metadata editor.)

---

## 14. Source‑value warnings

When you manually enter a target **video bitrate, resolution, FPS, audio bitrate, or audio
sample rate** that is **higher than the detected source**, FFmWiz asks for confirmation with
`n` as the default and explains the consequence. Choosing `n` returns to the same question.
In Folder Encode the comparison uses the lowest detected source value across the folder.

---

## 15. Progress display

During a run FFmWiz shows a single in‑place progress line, e.g.:

```text
42.0%  •  time 00:30:00 / 01:11:00  •  speed 1.34x  •  size 512.0 MB  •  bitrate 1500.0kbits/s  •  elapsed 22:21  •  ETA 30:11
```

- All fields (percent, time, size, bitrate, ETA) refresh **together** on each real FFmpeg
  tick; the line is held steady between ticks so no field drifts faster than the others.
- ETA uses a stable overall‑average rate (not FFmpeg's jumpy per‑tick speed) and is cached to
  the media position so it does not creep between ticks.
- For **splits**, percent/time/part transitions are reconstructed from FFmpeg's real
  `out_time` (multi‑output counters are unreliable), and the displayed size is real on‑disk
  bytes; the bitrate reflects the real average rather than being pinned to the target.
- `Total time elapsed` reported at the end counts the FFmpeg run only, not time spent waiting
  for your answers.

---

## 16. Logging

Logging is enabled by default. Each run writes a dated UTF‑8 log to `Logs/` next to the script
(`ffmwiz_YYYY-MM-DD_HH-mm-ss_UTC.log`), preserving Unicode paths. Control it in `config.env`:

```ini
logging_enabled=y       # set n to stop creating logs
log_retention_days=0    # 0 keeps forever; N prunes logs older than N days
```

Logs record startup, resolved paths, the exact command/argv, ffprobe/ffmpeg activity, and
errors with context. Attach the relevant log when reporting a problem.

---

## 17. Troubleshooting / FAQ

- **GUI doesn't open** → install PySide6: `py -3 -m pip install --upgrade -r requirements.txt`.
  If a started GUI reports an internal error, set `FFMWIZ_DEBUG=1` for a full traceback.
- **No native playback for some media** → Qt Multimedia may not decode that codec; the
  timeline/frame extraction still works. Use the manual `h:m:s:frame` flow in Mode 3 if needed.
- **NVENC not used** → your FFmpeg build lacks NVENC for that codec, or the GPU/driver is
  unsupported; FFmWiz falls back to CPU. Verify with `ffmpeg -hide_banner -encoders`.
- **`.ps1` won't run on double‑click** → use `powershell -ExecutionPolicy Bypass -File ".\run.ps1"`
  or right‑click → Run with PowerShell.
- **Multipass/two‑pass question missing** → it only appears for encoders that support it
  (detected from `ffmpeg -h encoder=...` for multipass; a verified set for CPU two‑pass).
- **Inspect your FFmpeg capabilities** → FFmWiz writes `ffmwiz-ffmpeg-reference.txt` next to the
  script (delete it to regenerate). It captures `-formats/-encoders/-decoders/-filters/...`
  from your installed `ffmpeg.exe`.

---

## 18. Glossary

- **Stream copy (`-c copy`)** — remux without re‑encoding; fast, lossless, keyframe‑bound.
- **CRF** — Constant Rate Factor; targets a quality level (smaller = higher quality).
- **VBR / maxrate / bufsize** — variable bitrate with a ceiling and buffer for size control.
- **preset / tune / profile** — speed/quality tradeoff, content hint, and stream profile.
- **GOP** — keyframe interval; larger = better compression but slower seeking.
- **faststart** — moves the MP4 `moov` atom to the front for progressive playback.
- **hvc1 vs hev1** — HEVC‑in‑MP4 tag; `hvc1` is required by Apple devices/some browsers.
- **tv vs pc color range** — limited (16–235) vs full (0–255); FFmWiz defaults to `tv`.
- **yuv420p / nv12 / p010le** — common 8‑bit CPU / NVENC / 10‑bit pixel formats.
- **multipass (NVENC)** — NVENC's `-multipass` (disabled/qres/fullres) first‑pass analysis.
- **two‑pass (CPU)** — FFmpeg's `-pass 1/2` analysis + final pass for accurate target bitrate.

---

*This documentation is kept in sync with FFmWiz. If a prompt or option differs from what you
see, the latest behavior in `FFmWiz.py` is authoritative — please report the mismatch.*
