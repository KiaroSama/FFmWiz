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
4. [The startup menu (modes 1–15)](#4-the-startup-menu-modes-115)
5. [Mode 1 — Interactive wizard](#5-mode-1--interactive-wizard)
6. [Mode 2 — Wizard from config (ask only what is blank)](#6-mode-2--wizard-from-config-ask-only-what-is-blank)
7. [The `config.env` file (full reference)](#7-the-configenv-file-full-reference)
8. [Video encoding reference](#8-video-encoding-reference)
9. [Audio reference](#9-audio-reference)
10. [Cutting, splitting, and joining](#10-cutting-splitting-and-joining)
11. [Speed and reverse](#11-speed-and-reverse)
12. [The graphical editors (classic and QML)](#12-the-graphical-editors-classic-and-qml)
13. [Modes 3–15 in detail](#13-modes-315-in-detail)
14. [Source‑value warnings](#14-source-value-warnings)
15. [Progress display](#15-progress-display)
16. [Logging](#16-logging)
17. [Troubleshooting / FAQ](#17-troubleshooting--faq)
18. [Glossary](#18-glossary)
19. [Appendix A — Mode 1 prompt-by-prompt reference](#appendix-a--mode-1-prompt-by-prompt-reference)
20. [Appendix B — Complete config.env key reference](#appendix-b--complete-configenv-key-reference)
21. [Appendix C — Recipe cookbook](#appendix-c--recipe-cookbook)
22. [Appendix D — FFmpeg concepts in depth](#appendix-d--ffmpeg-concepts-in-depth)
23. [Appendix E — Encoder and container compatibility](#appendix-e--encoder-and-container-compatibility)
24. [Appendix F — Extended troubleshooting matrix](#appendix-f--extended-troubleshooting-matrix)
25. [Appendix G — Extended FAQ](#appendix-g--extended-faq)
26. [Appendix H — Worked example sessions](#appendix-h--worked-example-sessions)
27. [Appendix I — ffmpeg flags FFmWiz can emit](#appendix-i--ffmpeg-flags-ffmwiz-can-emit)
28. [Appendix J — Command-line flags](#appendix-j--command-line-flags)
29. [Appendix K — Environment variables](#appendix-k--environment-variables)
30. [Appendix L — Output naming and folder behavior](#appendix-l--output-naming-and-folder-behavior)

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

1. Clone or download the repository, keeping `FFmWiz.py`, `run.ps1`, and the `ffmwiz/` package
   (which now bundles `ffmwiz/assets/`) together in the same directory.
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
- `--refresh-ffmpeg-reference` — regenerate `ffmwiz-ffmpeg-reference.txt` from your installed ffmpeg, then continue.
- See **Appendix J** for the full flag list and **Appendix K** for all environment variables.
- See **[FFMPEG-REFERENCE.md](FFMPEG-REFERENCE.md)** for the build-specific capability
  snapshot, the capability cache, and how to inspect your own `ffmpeg.exe`.
- Common environment variables:
  - `FFMWIZ_GUI_ENGINE=classic|qml` — choose the unified‑editor engine (overrides `config.env`).
  - `FFMWIZ_DEBUG=1` — print full tracebacks if a GUI reports an internal error.

---

## 4. The startup menu (modes 1–15)

```text
1  = Interactive wizard
2  = Wizard from config (ask only what is blank)
3  = Cut video only with copy
4  = Folder Encode
5  = Add files to video
6  = Extract Stream
7  = Media info report
8  = Stream Cleanup Remux
9  = Hard Sub Encode
10 = Video Speed / Reverse
11 = Audio Cut / Speed / Reverse
12 = Join Audios and Videos
13 = Metadata Editor
14 = FFmpeg capability cache (diagnostics)
15 = Track Manager (remove / add / replace tracks, normalize loudness)
```

Mode 1 is the default (press Enter); any other value is rejected with
"Enter a menu number from 1 to 15." The sections below explain each mode.

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
7. **Video bitrate** (or **CRF/quality** if you choose quality mode). After a numeric
   bitrate is entered, FFmWiz prints an estimated output size at that bitrate plus a note
   that the real bitrate may differ (2‑pass encoding gets it closer).
8. **NVENC multipass** — shown only for NVENC encoders that support it (see §8).
9. **CPU two‑pass** — shown only for CPU encoders that support it (see §8).
10. **Resolution** — preset (`480p`/`720p`/`1080p`), `w1280`/`720h`, `WIDTHxHEIGHT`,
    `stretch:WIDTHxHEIGHT`, or `n` to keep source.
11. **FPS** — integer, or `n` to keep source.
12. **Audio tracks** — which streams to keep (`0`, `0,1,2`, `all`, `d`, `e`, `de`).
13. **Audio codec** — `aac`, `libopus`, `libmp3lame`, `flac`, `pcm_s16le`, `copy`, ...
14. **Audio bitrate**. After a numeric bitrate is entered, FFmWiz prints an estimated
    output size at that audio bitrate plus the same accuracy note.
15. **Audio sample rate (Hz)** — e.g. `48000`; `n` keeps the source rate (see §9).
16. **Subtitle tracks** (when keeping source metadata).
17. **Keep source metadata / extra streams** — keep vs strip metadata, chapters, extra
    video/data streams, attachments.
18. **Start confirmation** — the final PowerShell command is shown; confirm to run.

`copy` is incompatible with filters (crop/scale/fps/setparams/cuts) and is auto‑promoted to
H.265 when a filter is required.

---

## 6. Mode 2 — Wizard from config (ask only what is blank)

Mode 2 runs the **full** Mode 1 wizard, but every question whose value is set in
**`config.env`** (see §7) is auto-applied and skipped. Only the questions you leave blank
are asked. It is not a "crop only" mode: leave three keys blank and you are asked three
questions. If `config.env` is missing, it is created from the template on first run.

Two questions are **never** auto-skipped, however complete your config is:

- the **unified graphical editor** offer (and the crop/cut/split work you do inside it), and
- the final **"Start FFmpeg now?"** confirmation.

The join-inputs, cuts, and audio-cut questions are likewise always asked, because they have
no `config.env` equivalent.

`input_path` is **not** required. If it is set, the file is loaded and validated before the
first question (a path that does not exist stops the run with a clear error); if it is blank,
Mode 2 simply asks for the input file like Mode 1 does.

A question backed by several keys is skipped only when **all** of them are filled — for
example the video speed/reverse question needs both `video_speed` and `reverse_video`.

Beyond the core video/audio settings, Mode 2 also reads the optional recipe keys for audio
sample rate, single‑pass loudnorm, NVENC multipass, CPU two‑pass, color range, and global
video/audio speed + reverse (see the table in §7). Anything you leave blank keeps the normal
interactive default, so an old `config.env` keeps working unchanged.

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
To start from the template by hand:

```powershell
Copy-Item config.env.example config.env
```

Every key is also documented inline in `config.env.example`.

### Settings

| Key | Values | Default | Meaning |
|-----|--------|---------|---------|
| `input_path` | absolute path | (empty) | Source file. Optional: if blank, Mode 2 asks for it. |
| `output_path` | folder / file / base name | (empty) | Output destination. Empty = input folder. |
| `output_format` | mp4, mkv, mov, webm, mp3, m4a, opus, flac, ... or `n` | `n` | Output container; `n` inherits input. |
| `video_codec` | H265, H264, AV1, VP9, MPEG4, copy, or encoder name | `H265` | Video encoder (aliases map to CPU/NVENC). |
| `use_gpu` | y/n | `y` | Use NVENC/CUDA when supported. |
| `crop` | n / y / `top,left,right,bottom` | `n` | Crop margins (pixels removed). Set it and Mode 2 skips the crop question; leave it blank to be asked. |
| `crop_top`/`crop_left`/`crop_right`/`crop_bottom` | integer ≥ 0 | `0` | Per‑side crop when `crop=y`. |
| `video_bitrate_kbps` | integer or `n` | `n` | Target average video bitrate (kbps). |
| `video_bitrate_mode` | quality_vbr / strict_size | `quality_vbr` | VBR shape (see §8). |
| `resolution` | preset / `w1280` / `720h` / `WxH` / `stretch:WxH` / `n` | `n` | Output scale. |
| `fps` | integer or `n` | `n` | Output frame rate. |
| `video_look` | filter list or `n` | `n` | Rotate, mirror, colour, denoise, sharpen/blur, fades. |
| `video_quick` | `gif` / `boomerang` / `thumb` / `loop=N` / `n` | `n` | Quick outputs; see below. |
| `video_composite` | `overlay` / `pip` / `hstack` / `vstack` / `mix` / `n` | `n` | Combine a second input. |
| `audio_volume` | factor, `150%`, `+6dB` or `n` | `n` | Audio gain, 0.01-10.0. |
| `raw_ffmpeg_args` | ffmpeg options or `n` | `n` | Your own options, added last. |
| `audio_tracks` | 0 / 0,1,2 / all / d / e / de | `de` | Which audio streams to keep. |
| `audio_codec` | aac, libopus, opus, libmp3lame, flac, pcm_s16le, copy, ... | `aac` | Audio encoder. |
| `audio_bitrate_kbps` | integer or `n` | `n` | Audio bitrate per stream (kbps). |
| `audio_sample_rate` | integer Hz or `n` | `n` | Output audio sample rate; `n` keeps source. Ignored for `copy`. |
| `keep_source_metadata` | y/n | `y` | Keep metadata/chapters/extra streams + subtitle selection. |
| `subtitle_tracks` | selection / none / clear / delete | `none` | Which subtitles to keep. |
| `keep_embedded_attachments` | y/n | `n` | Keep MKV attachment streams (fonts). |
| `detect_duplicate_audio` | y/n | `y` | Flag duplicate/empty audio for d/e/de. |
| `loudnorm` | off / on | `off` | Single‑pass EBU R128 loudness normalization (switches `copy`→AAC). |
| `loudnorm_target_i` | LUFS (e.g. -16, -14, -23) | `-16` | Integrated‑loudness target when `loudnorm=on`. |
| `nvenc_multipass` | disabled / qres / fullres | `disabled` | NVENC multi‑pass quality (NVENC encoders only). |
| `cpu_two_pass` | y/n | `n` | CPU two‑pass encoding (supported CPU encoders only). |
| `color_range` | source / tv / pc / unspecified | `source` | Range signaling when the source range is unknown (no pixel conversion). |
| `video_speed` | number 0.10–8.0 or `n` | `n` | Global video speed multiplier (forces re‑encode). |
| `reverse_video` | y/n | `n` | Reverse the whole video (forces re‑encode). |
| `audio_speed` | number 0.10–8.0 / match_video / `n` | `n` | Global audio speed; `match_video` follows the video speed/reverse. |
| `reverse_audio` | y/n | `n` | Reverse the audio (ignored when `audio_speed=match_video`). |
| `logging_enabled` | y/n | `y` | Write dated logs into `logs/`. |
| `log_retention_days` | integer | `0` | `0` keeps forever; N deletes logs older than N days. |
| `log_level` | debug / info / warning / error / critical | `debug` | File log detail; the console is unaffected (env `FFMWIZ_LOG_LEVEL` overrides). |
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

```ini
# Normalized, slightly faster lecture: 2x speed, loudness -14 LUFS, 48 kHz
output_format=mp4
video_codec=H264
use_gpu=y
audio_tracks=0
audio_codec=aac
audio_bitrate_kbps=160
audio_sample_rate=48000
loudnorm=on
loudnorm_target_i=-14
video_speed=2
audio_speed=match_video
```

```ini
# High-quality CPU two-pass H.265 at a fixed size target
output_format=mp4
video_codec=H265
use_gpu=n
video_bitrate_kbps=3000
video_bitrate_mode=strict_size
cpu_two_pass=y
audio_codec=aac
audio_bitrate_kbps=160
```

```ini
# NVENC HEVC with full-resolution two-pass (best NVENC quality)
output_format=mp4
video_codec=H265
use_gpu=y
video_bitrate_kbps=6000
nvenc_multipass=fullres
audio_codec=aac
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

### GPU processing paths

Answering `y` to GPU usage does not force one fixed pipeline. FFmWiz prints the path it chose
just above the command as **processing path**, and logs the reasoning:

| Path | When | What happens |
|------|------|--------------|
| CUDA fast path | a simple graph (crop/scale only) | NVDEC/CUDA decode -> CUDA filters (`scale_cuda`) -> NVENC encode, with frames kept on the GPU |
| Hybrid GPU path | a `-filter_complex` graph is unavoidable | CUDA/NVDEC decode -> CPU filter graph (crop/fps/scale/pad/concat/trim/speed/split/audio) -> NVENC encode |
| NVENC encode path | no filter graph and no CUDA frames | CPU decode/filter -> NVENC encode |
| CPU filter graph / standard | GPU off or unsupported | everything on the CPU |

Multi-range frame-accurate cuts, splits, joins, and speed/reverse need the CPU filter graph,
so they land on the hybrid path: NVENC still encodes, but the frames make a GPU->CPU->GPU trip.
A single continuous cut range is expressed with input/output timing instead of a filter, so it
keeps the CUDA fast path.

Video `copy` cannot be combined with filters. If crop, fps, scale, SAR, or color-metadata
filters are required, FFmWiz promotes the stream copy to an encoder.

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

After the ranges are entered, both the re‑encode and the copy path confirm with:

```text
Continue? [Y/n] {0=back, quit=exit}:
```

Enter continues, `n` cancels, `0` returns to the previous step, `quit` leaves FFmWiz.

### Split points

Add split points to cut one timeline into multiple output parts (`_Part01`, `_Part02`, ...).
Splitting is performed in a single FFmpeg command with a filter graph; progress is
reconstructed per part (see §15).

Each part is a complete file on its own clock, so the extras are rebuilt per part rather than
copied whole:

- **Subtitles** — text tracks are sliced to the part's interval and shifted back to zero, so a
  cue that belonged to Part 2 starts where it should instead of at its whole-timeline position.
  A cut or speed change is applied first, then the slice. Bitmap tracks (PGS, VobSub, DVB)
  cannot be sliced and are dropped after you confirm.
- **Chapters** — remapped per part and clipped to that part's interval, each part starting at
  zero.

### Join (Mode 12 — "Join Audios and Videos")

Join multiple inputs into one file. FFmWiz stream‑copies when the inputs are compatible,
otherwise re‑encodes. The join input summary reports per‑input colors, total raw duration and
frame count, and mean/max volume extremes. Audio is resampled to one uniform sample rate.

**Video join or audio-only join is auto-detected.** If none of the selected inputs has a video
stream, FFmWiz reports *"Detected audio-only inputs: performing an audio join."* and joins the
audio; the frame-rate questions below are skipped, because they do not apply.

**Mixing the two is rejected.** If you select a video file and an audio-only file in the same
join, FFmWiz names the offending files and returns to the menu:

```text
Cannot mix audio-only and video inputs in one join. Audio-only: <files>.
Select all video files, or all audio files.
```

Join every video first, then join the audio separately, or add the audio to the video with
Mode 5 (Add files to video) instead.

#### Subtitles on a joined timeline

FFmpeg's `concat` filter cannot carry subtitle streams, so when a join is re‑encoded FFmWiz
rebuilds them: each input's text subtitle is extracted, its cues are shifted by the total
duration of the inputs before it, cues running past their own input are clipped, and the result
is muxed back in. An input with no subtitle still advances the offset, so later cues stay
aligned.

One merged track is produced **per selected logical track**, not one in total. Track numbering
is the relative position within each input (track 0, track 1, ...), which is the numbering the
subtitle question shows; an input that lacks a given track contributes an empty stretch so the
other inputs' cues stay in place. The question itself counts tracks across **all** inputs, so a
join whose first file has no subtitles still asks which of the later files' tracks to keep.

**An edited join keeps its subtitles too.** The merged track is assembled on the unedited joined
clock and then put through the same timeline transform the picture and audio get, so cuts, a
speed change and reverse all compose; a Split slices the transformed track into its parts and
rebases each to zero. Measured on two 2‑second inputs whose cues read `FIRST` and `SECOND`:

| edit | resulting cues |
| --- | --- |
| none | `FIRST 0.5–1.5`, `SECOND 2.5–3.5` |
| 2× speed | `FIRST 0.25–0.75`, `SECOND 1.25–1.75` |
| reverse | `SECOND 0.5–1.5`, `FIRST 2.5–3.5` |
| keep 0–1 s and 2–4 s | `FIRST 0.5–1.0`, `SECOND 1.5–2.5` |
| split at 2 s | Part 01 `FIRST`, Part 02 `SECOND 0.5–1.5` |

It is still refused, with the reason printed before you confirm, when:

- an input has no known duration, so the per‑input cue offsets cannot be computed;
- the inputs carry only **bitmap** subtitles (PGS, VobSub, DVB) — these are pictures with no cue
  text to shift.

#### Audio when some inputs are silent

Audio is planned per input, not from the first file. If a selected track is missing from an
input, FFmWiz synthesises silence for exactly that input's duration and says so; if the *first*
input is silent but later ones have audio, it joins track 0 of the others rather than dropping
audio entirely. Tracks beyond the number the track question could offer are reported as not
included, instead of disappearing without a word.

#### Choosing `copy` for a re-encode join

A join that goes through the concat **filter** — anything but a plain stream-copy join —
re-encodes by definition, so a requested `copy` codec cannot be honoured. FFmWiz says so
explicitly for both streams, e.g.

```
Video copy cannot be used across a join; the joined timeline is re-encoded with libx265.
Audio copy cannot be used after Split/filter processing. AAC was selected for audio.
```

The selected-settings summary then shows the codec that will actually be used, not the one
you asked for. Your original choice is still what you return to if you go **back**.

#### Mixed frame rates (constant vs variable)

When you join two or more videos whose source frame rates differ, FFmWiz asks
**"Make all frame rates the same?"** (default **yes**). This question is asked in both
Mode 12 and the Mode 1 wizard join, and it applies to both stream‑copy and re‑encode joins.

- **Yes (unify → constant frame rate):** FFmWiz then asks **"Enter frames per second for all
  joined videos"**, defaulting to the **highest** source rate. Every segment is converted to
  that single rate, producing a normal constant‑frame‑rate (CFR) output. The fps question
  always comes *after* the unify question.
- **No (variable frame rate):** each input keeps its own frame rate and the output is a
  variable‑frame‑rate (VFR) file.
  - When the inputs match on everything except frame rate, FFmWiz joins them with the
    `concat` demuxer and `-c copy` — no re‑encode — which preserves each segment's native
    timing (a genuinely variable output).
  - When a re‑encode is unavoidable (different codecs, resolution, etc.), FFmWiz drops the
    per‑input `fps=` filter and adds `-fps_mode vfr` to keep variable timing instead of
    resampling every frame to one constant rate.

---

## 11. Speed and reverse

Speed/reverse is available in Mode 1, Mode 4 (folder), the standalone Mode 10 (video) and
Mode 11 (audio), and inside the Unified Editor. Export filters:

- video speed: `setpts=(PTS-STARTPTS)/speed`, plus `-fps_mode passthrough` on the output
- video reverse: `reverse,setpts=(PTS-STARTPTS)/speed`
- audio speed: `atempo`, split into safe chained stages when outside one stage's range
- audio reverse: `areverse`

`setpts` moves frames without changing how many there are, so speeding up delivers them
faster than the source frame rate. Without `-fps_mode passthrough` the encoder keeps the
source rate and drops whatever arrives early — measured at 2x, 40 frames became 22, and the
picture that survived no longer lined up with its own subtitles. Passthrough keeps the
filter graph's own timing, so every frame is written. At 1x and in slow motion the output is
byte-identical either way.

Because FFmpeg's `reverse`/`areverse` buffer the whole clip in memory, FFmWiz reverses video
in short segments and concatenates them in reverse order to avoid RAM spikes.

**The segment length follows the frame size, not the clock.** What `reverse` holds is every
decoded frame of its input, so a fixed number of seconds means wildly different amounts of
memory. Each segment is planned against a **2 GiB peak** — 512 MiB reserved for the
decoder/encoder working set plus the decoded frames themselves, sized from the stream's real
pixel format and carrying a 15% allowance for filter queues:

| source | segment | peak |
| --- | --- | --- |
| 480p30 8-bit | 60.000 s (1800 frames, the ceiling) | 1.69 GiB |
| 720p30 8-bit | 33.767 s (1013 frames) | 2.00 GiB |
| 1080p30 8-bit | 15.000 s (450 frames) | 2.00 GiB |
| 1080p60 8-bit | 7.500 s (450 frames) | 2.00 GiB |
| 4K30 8-bit | 3.733 s (112 frames) | 1.99 GiB |
| 4K60 10-bit | 933 ms (56 frames) | 1.99 GiB |
| 8K60 10-bit | 233 ms (14 frames) | 1.99 GiB |

A segment is a whole number of **frames**, and one frame is the smallest legal segment — there
is no minimum expressed in seconds, because a one-second minimum is 2.10 GiB at 4K60 10-bit and
6.90 GiB at 8K60 10-bit, which breaks the very cap it sits under. Large formats therefore get
subsecond windows, and progress notices report them in milliseconds and frames.

The size is measured at the **input of the `reverse` filter**, not at the source stream. The CPU
filter chain is crop → fps → scale/pad → speed/reverse, so a 1080p30 clip upscaled to 8K is
budgeted as 8K: sizing it from the source would hand it the 1080p window of 15 s, which is
24.5 GiB of 8K frames.

If the geometry, frame rate, or pixel format the budget needs cannot be read, FFmWiz **refuses**
rather than assuming one — a warning cannot turn an unbounded allocation into a bound. The same
applies when a single decoded frame plus the reserved overhead already exceeds the cap: raise
the cap (see `FFMWIZ_REVERSE_PEAK_BUDGET_MB` in Appendix K) or reverse a smaller frame.

Each segment is bounded **before** the filter sees it (`-ss`/`-t` on the source input), so the
decoder stops at the segment boundary rather than reading the whole file and trimming after.

**A join or a Split reverses in stages**, so the filter is never handed a whole timeline:

1. **Join** the inputs forward into a temporary file. The segmented executor understands one
   input, so pointing it at a join would reverse the first file alone.
2. **Reverse** that single file in the bounded segments above, applying the cuts and speed
   along with it.
3. **Split** the reversed result. Split points are chosen on the final processed timeline,
   which is exactly what stage 2 produced.

Stages 1 and 3 are skipped when they do not apply. One pass instead would need memory
proportional to the whole timeline — roughly 336 GiB of decoded frames for an hour of joined
1080p30, or 56 GiB for ten split minutes of it — so neither is attempted. Each stage costs one
extra encode; the temporary files are written at a visually lossless quality and removed with
the rest of the job's temporary files, and the output parts keep the names the summary showed
you before the run started.

**Audio reverse is bounded the same way.** `areverse` buffers every decoded sample, so six
hours of 48 kHz stereo is about 8.3 GiB. Audio shares the 2 GiB peak with video, sized from the
stream's real sample rate, channel count and sample format:

| track | segment |
| --- | --- |
| 44.1 kHz mono 16-bit | 4:24:39 |
| 48 kHz stereo 24-bit | 1:00:47 |
| 192 kHz 7.1 24-bit | 3:48 |

There is no 60-second ceiling here: a second of 48 kHz stereo is about 0.4 MB against 250 MB
for a second of 4K30, so capping audio at a minute would only multiply FFmpeg invocations
without changing the bound. A track that already fits its budget runs the ordinary one-shot
command, which is bounded by construction.

A longer track is staged so that only the reversal is chunked:

1. **Decode forward once** into lossless chunks, applying the cuts as it goes. The chunking is
   done by the segment muxer rather than by seeking, so every packet lands in exactly one
   chunk and the joined sample count matches a single decode to the sample.
2. **Reverse each chunk**, lossless in and lossless out.
3. **Concatenate in reverse order** — `reverse(A‖B)` is exactly `reverse(B)‖reverse(A)`.
4. **Run the original job** against the joined result, applying only the filters whose
   semantics stay continuous: `atempo`, LoudNorm and resampling. Splitting one of those per
   chunk would renormalise or re-window every boundary, which is why the reversal is staged
   and the filter graph is not.

The scratch chunks are FLAC in Matroska, at 24 bits for a float-decoded source and at the
source's own width for an integer one. An integer source therefore round-trips **bit-identically**
against the one-shot reverse; a lossy source costs the 24-bit quantisation floor, measured at
-138.5 dBFS peak and -143.7 dBFS RMS. Above eight channels the scratch becomes PCM, because
FLAC cannot carry a wider layout. Cover art is carried into the final stage from the original
file rather than replicated into every chunk.

If the duration cannot be read, or the plan would need more than 512 segments, FFmWiz states
the calculation and **refuses** rather than falling back to the unbounded one-shot.

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

Mouse controls in the preview and on the timeline:

```text
Wheel on the preview canvas     Zoom around the cursor
Wheel on the playback timeline  Seek backward / forward
Wheel on the volume slider      Volume down / up
Ctrl + drag inside the crop box Move the crop box without resizing it
Right-click (Hand / Zoom tool)  Play / Pause
```

The Zoom tool zooms **in** by default; hold `Alt` to zoom out (the cursor and button icon
switch between plus and minus as you press and release `Alt`). Dragging mostly upward zooms in
smoothly, mostly downward zooms out, always focused around the drag origin. Crop edges have a
hit area larger than the visible handle, so a precise edge grab does not need pixel-perfect
mouse placement.

Live reverse preview (when **Reverse** is on) renders short reversed proxy windows on demand
and plays them back while the CTI moves forward.

### Archived legacy Tk editors

The standalone Tk **Cut Editor** and **Crop Editor** that predate the unified editor are still
in the repository for archive/debug reference, but no CLI prompt exposes them any more: there
is no `g=Show Graphical Cut Editor` or `g=Show Graphical Crop Editor` answer, Mode 3's
stream-copy cut is manual-only, and declining the unified editor in Mode 1 keeps crop, cuts,
and speed/reverse in the terminal. Use the Unified Video Editor for all graphical work.

Two behaviours of the archived crop tool are worth recording because nothing replaced them:
its undo history tracked only crop-rectangle changes (move, resize, reset) and never view
state such as tool selection, zoom, or pan; and its audio preview required `ffplay` in PATH —
without it the crop preview still worked, only the audio scrubbing was disabled. The active Qt
editors play audio through Qt Multimedia and need no `ffplay`.

<details>
<summary>Archived crop-tool controls (kept for reference only)</summary>

```text
H              Switch to Hand Tool (pan)
Z              Switch to Zoom Tool (click = zoom in, Alt+click = zoom out,
               drag up = zoom in, drag down = zoom out)
Ctrl + R       Reset crop margins
Ctrl + 0       Reset zoom to 100%
Ctrl + (+)     Zoom in around the preview cursor
Ctrl + (-)     Zoom out around the preview cursor
Ctrl + Z       Undo last crop edit
Ctrl + Y       Redo last undone crop edit  (Ctrl+Shift+Z also works)
Home / End     Jump to start / end of the audio-preview timeline
Shift + arrow  Skip -10s / +10s
Enter          Apply crop and return to terminal
Esc            Cancel and discard the selection
```

Mouse: drag crop edges or corners, drag inside the image with the Hand Tool to pan a zoomed
preview, wheel over the playback timeline to seek, `Ctrl` + wheel to zoom the canvas, `Reset
Crop` to restore the full frame. Zoom presets were reachable from the arrow inside the zoom
percentage field.

</details>

---

## 13. Modes 3–15 in detail

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

### Mode 12 — Join Audios and Videos
See §10. Stream‑copy when compatible, otherwise re‑encode; uniform audio sample rate; rich
join input summary. When source frame rates differ, FFmWiz asks whether to unify them to one
constant rate (default) or keep a variable frame rate (VFR) output.

### Mode 13 — Metadata Editor
Inspects streams/chapters/tags/dispositions/attached pictures/bitstream metadata via ffprobe,
then applies targeted stream‑copy changes with `-map 0 -c copy`. Edits per‑stream
title/language/custom tags, dispositions, chapter metadata, attached pictures, H.264/HEVC
bitstream color/SAR metadata, and metadata reports. (No global file‑level metadata editor.)

### Mode 14 — FFmpeg capability cache (diagnostics)
A small diagnostics sub-menu for the cached results of FFmWiz's live encoder/container probes.
It never changes your settings, logs, or media:

```text
1 = View cached capability results
2 = Re-probe current encoder/container capabilities
3 = Clear capability cache
0 = Back
```

**Re-probe** re-tests `libx264`, `libx265`, `h264_nvenc`, and `hevc_nvenc` against `mp4` and
`mkv` and stores the fresh verdicts. **Clear** deletes only the two files FFmWiz owns
(`ffmpeg_capabilities.json` and its `.corrupt` recovery backup) and leaves the enclosing
`.cache` directory and every unrelated file untouched. Clearing costs a few seconds on the
next encode — an empty cache just means FFmWiz probes again.

Use this after upgrading FFmpeg or switching GPU drivers, when a capability-dependent question
(color range, NVENC multipass) looks wrong. The cache location and the `FFMWIZ_CACHE_DIR`
override are described in **[FFMPEG-REFERENCE.md](FFMPEG-REFERENCE.md)**.

### Mode 15 — Track Manager
Remove, add, or replace tracks in an existing file and optionally normalize its loudness — all
as a stream-copy remux where possible, so nothing is re-encoded unless loudnorm requires it.
First choose the scope:

```text
1 = Single file
2 = Folder (apply the same change to every media file)
```

Then, for the single-file flow, each step in turn (`0` goes back exactly one step):

1. **Source** — the file to edit; its streams are listed with indexes.
2. **Remove** — comma-separated stream specs. `a:1`, `s:0` (type + index) or a bare absolute
   ffprobe index. Enter removes nothing.
3. **Add external tracks** — optionally pull audio/subtitle tracks in from other files, the
   same picker Mode 5 uses.
4. **Loudness** — the standard Off / single-pass / two-pass EBU R128 choice (see §9).
   Skipped silently when the file has no audio.
5. **Metadata** — keep container tags, chapters, and stream titles/languages, or strip them
   all for a clean output.
6. **Summary and confirmation** — the final command is shown before anything runs.

The output is written next to the source as `<name>_TrackEdit<same extension>`; the input is
never modified. If you remove nothing, add nothing, and leave loudnorm off, FFmWiz reports
"No track was removed or added; nothing to do." and returns to the menu.

**Folder scope** applies one recipe to every media file in the folder, so removals must use
`type:index` specs (`a:1`) — absolute indexes are rejected, because they differ per file. The
optional two-pass loudness measurement is taken from the first file and reused for the rest,
which makes single-pass the safer choice when the files differ in loudness. Recognized
extensions are the common video and audio containers (`.mkv`, `.mp4`, `.mov`, `.m4v`, `.webm`,
`.avi`, `.ts`, `.mpg`, `.mpeg`, `.wmv`, `.flv`, `.m4a`, `.mka`, `.mp3`, `.aac`, `.flac`,
`.wav`, `.opus`, `.ogg`, `.ac3`, `.eac3`, `.dts`).

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

### Cancelling a running encode

Press **Ctrl+C** while an encode is running. FFmWiz asks FFmpeg to stop the way a console
interrupt would, so FFmpeg finalises the container before exiting and the partial output
stays **playable** — a cancelled 60‑second encode stopped after 3 seconds leaves a readable
file holding the seconds already encoded, rather than an unreadable fragment. If FFmpeg does
not respond it is terminated, and finally its whole process tree is killed, so nothing is
left holding the output file.

The cancel takes effect immediately; FFmWiz does not wait out any encode timeout first.

---

## 16. Logging

Logging is enabled by default. Each run writes a dated UTF‑8 log to `Logs/` next to the script
(`ffmwiz_YYYY-MM-DD_HH-mm-ss_UTC.log`), preserving Unicode paths. Control it in `config.env`:

```ini
logging_enabled=y       # set n to stop creating logs
log_retention_days=0    # 0 keeps forever; N prunes logs older than N days
log_level=debug         # debug | info | warning | error | critical
```

`log_level` controls the **file** only; console output never changes. More than half of a
typical log is DEBUG detail (per-line FFmpeg stderr, progress-parser events, the exact
argv), so `log_level=info` gives a much smaller file. Keep `debug` when collecting evidence
for a bug report. The environment variable `FFMWIZ_LOG_LEVEL` overrides the config value,
and an unrecognized value falls back to `debug`.

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
  from your installed `ffmpeg.exe`. See **[FFMPEG-REFERENCE.md](FFMPEG-REFERENCE.md)**.
- **FFmpeg or ffprobe not found** → FFmWiz offers to install the full FFmpeg package for you,
  trying `winget` first and then Chocolatey. If PATH changes during the install, restart
  PowerShell so the new `ffmpeg`/`ffprobe` commands become visible.
- **`FFmWiz` command not found** → run `install-command.ps1` once, then open a **new** terminal
  window so the updated PATH is picked up.
- **ffprobe cannot read a file** → Unicode and Persian paths are supported; the console prints
  a short message plus the log path. The log holds the input path, the normalized path, the
  ffprobe executable, the arguments, the return code, stdout/stderr previews, the decoding
  mode, and any traceback.
- **Icons or cursors missing in the GUI** → keep the `assets` folder inside the package
  (`ffmwiz/assets/`). The app icon loads from `ffmwiz/assets/icons/ffmwiz_app.ico` or `.png`;
  toolbar and cursor art from `ffmwiz/assets/icons/` and `ffmwiz/assets/cursors/`. Missing
  assets are logged but never stop the GUI from opening.

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

**`video_composite`** — Combine a SECOND input with the first, or `n`. One of
`overlay` (a logo or watermark at its own size), `pip` (a second video scaled
down), `hstack` / `vstack` (the two side by side or stacked), or `mix` (mix a
second audio source under the first). Corners are `tl`, `tr`, `bl`, `br` or
`center` (default `br`) with `composite_margin` pixels of inset (default 10);
`composite_opacity` fades the overlay and `composite_scale` sizes the
picture-in-picture (default 0.25). For `mix`, `composite_audio_weight` sets how
far the second source sits under the first (default 0.3). `hstack` and `vstack`
scale the inputs to a common edge first and say so rather than letterboxing in
silence. This is not Join: Join plays inputs one after another, these play them
at the same time.

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

## Donate

If this project helps you, donations are appreciated.

| Currency | Network | Address |
| --- | --- | --- |
| Bitcoin (BTC) | Bitcoin | `bc1qmth5m03pu5hujw5xw5jmywam3jj3sqwqupesdt` |
| USDT, BNB, USDC, etc. | BEP20 | `0x0Bd0BA443a8B9cf15922bf7f0Bb0a4b495fD06Ef` |
| USDT, TRX, USDC, etc. | TRC20 | `TWBA3xFTqgZAeAYMxqo85xWnzvty3DcAhw` |
| Ethereum (ETH) | ERC20 | `0x0Bd0BA443a8B9cf15922bf7f0Bb0a4b495fD06Ef` |
| TON | TON | `UQCN8Umo_OfOWqImZetQsrNStPcmLkMAKajFyiCOhso23NDb` |
| Litecoin (LTC) | LTC | `ltc1qntqnnrunadurnw4cshv3qgspywrueyyeyngwuy` |
| Solana (SOL) | Solana | `7B2wkczUjmkDhETwQuknBL8sUsbuV7nErxc317TmQuwR` |
| Polygon (POL) | Polygon | `0x0Bd0BA443a8B9cf15922bf7f0Bb0a4b495fD06Ef` |

---

*This documentation is kept in sync with FFmWiz. If a prompt or option differs from what you
see, the latest behavior in `FFmWiz.py` is authoritative — please report the mismatch.*
