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

The manual is split across five files; every entry below links to the right one.

1. [Requirements](#1-requirements)
2. [Installation](#2-installation)
3. [Running FFmWiz](#3-running-ffmwiz)
4. [The startup menu (modes 1–15)](#4-the-startup-menu-modes-115)
5. [Mode 1 — Interactive wizard](#5-mode-1--interactive-wizard)
6. [Mode 2 — Wizard from config (ask only what is blank)](#6-mode-2--wizard-from-config-ask-only-what-is-blank)
7. [The `config.env` file (full reference)](#7-the-configenv-file-full-reference)
8. [Video encoding reference](DOCUMENTATION-ENCODING.md#8-video-encoding-reference)
9. [Audio reference](DOCUMENTATION-ENCODING.md#9-audio-reference)
10. [Cutting, splitting, and joining](DOCUMENTATION-ENCODING.md#10-cutting-splitting-and-joining)
11. [Speed and reverse](DOCUMENTATION-ENCODING.md#11-speed-and-reverse)
12. [The graphical editors (classic and QML)](DOCUMENTATION-ENCODING.md#12-the-graphical-editors-classic-and-qml)
13. [Modes 3–15 in detail](DOCUMENTATION-MODES.md#13-modes-315-in-detail)
14. [Source‑value warnings](DOCUMENTATION-MODES.md#14-source-value-warnings)
15. [Progress display](DOCUMENTATION-MODES.md#15-progress-display)
16. [Logging](DOCUMENTATION-MODES.md#16-logging)
17. [Troubleshooting / FAQ](DOCUMENTATION-MODES.md#17-troubleshooting--faq)
18. [Glossary](DOCUMENTATION-MODES.md#18-glossary)
19. [Appendix A — Mode 1 prompt-by-prompt reference](DOCUMENTATION-PROMPTS-AND-KEYS.md#appendix-a--mode-1-prompt-by-prompt-reference)
20. [Appendix B — Complete config.env key reference](DOCUMENTATION-PROMPTS-AND-KEYS.md#appendix-b--complete-configenv-key-reference)
21. [Appendix C — Recipe cookbook](DOCUMENTATION-RECIPES.md#appendix-c--recipe-cookbook)
22. [Appendix D — FFmpeg concepts in depth](DOCUMENTATION-RECIPES.md#appendix-d--ffmpeg-concepts-in-depth)
23. [Appendix E — Encoder and container compatibility](DOCUMENTATION-RECIPES.md#appendix-e--encoder-and-container-compatibility)
24. [Appendix F — Extended troubleshooting matrix](DOCUMENTATION-RECIPES.md#appendix-f--extended-troubleshooting-matrix)
25. [Appendix G — Extended FAQ](DOCUMENTATION-RECIPES.md#appendix-g--extended-faq)
26. [Appendix H — Worked example sessions](DOCUMENTATION-RECIPES.md#appendix-h--worked-example-sessions)
27. [Appendix I — ffmpeg flags FFmWiz can emit](DOCUMENTATION-RECIPES.md#appendix-i--ffmpeg-flags-ffmwiz-can-emit)
28. [Appendix J — Command-line flags](DOCUMENTATION-RECIPES.md#appendix-j--command-line-flags)
29. [Appendix K — Environment variables](DOCUMENTATION-RECIPES.md#appendix-k--environment-variables)
30. [Appendix L — Output naming and folder behavior](DOCUMENTATION-RECIPES.md#appendix-l--output-naming-and-folder-behavior)

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
no `config.env` equivalent. The compositing question (`overlay`/`pip`/`hstack`/`vstack`/`mix`,
formerly the `video_composite` key) is also always asked interactively, even on a config run,
because it needs to probe a second input file and has nobody to answer for it non-interactively.

A composite carries the rest of your answers. The geometry and look -- crop, resize,
frame rate, rotation/flip, colour, denoise/sharpen/blur -- are applied to the main
picture **before** the second input is placed on it, so the overlay lands on the
picture you asked for and a side-by-side is sized against the real result. Speed and
the fade are applied **after**, to the finished frame, so the fade covers the whole
composite and a speed change retimes both inputs together with the sound. Volume,
LoudNorm and the audio fade are applied to the mixed audio.

Cut ranges and Split points are the exception: a composite writes ONE output from the
whole timeline, so it says so and leaves them out.

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
| `audio_volume` | factor, `150%`, `+6dB` or `n` | `n` | Audio gain, 0.01-10.0. |
| `raw_ffmpeg_args` | ffmpeg options or `n` | `n` | Your own options, added last. |
| `raw_ffmpeg_valued_args` | option names or `n` | `n` | Extra options that take a value. |
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
