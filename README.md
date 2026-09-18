<p align="center">
  <img src="docs/assets/ffmwiz-logo.png" alt="FFmWiz logo" width="160">
</p>

<h1 align="center">FFmWiz</h1>

FFmWiz is a Windows-focused interactive FFmpeg command builder. It inspects a source file, asks practical encode questions, builds a PowerShell-safe command, shows a final summary, and can run FFmpeg immediately.

It is designed for repeated local encoding work with NVIDIA/NVENC support, config-driven presets, audio-track cleanup helpers, and a unified graphical video editor.

> 📖 **This README is the overview.** Every mode, prompt, option, editor, and `config.env` key is documented in **[docs/DOCUMENTATION.md](docs/DOCUMENTATION.md)**, which is the authoritative reference.

## Features

- Fifteen menu modes covering encoding, lossless cutting, remuxing, joining, hard-subbing, stream extraction, media reports, metadata editing, and track management.
- A unified graphical video editor: preview with crop overlay, waveform, multi-range cuts, split points, timeline zoom/pan, and speed/reverse in one window.
- One-line picture filters in the encode wizard: rotate, mirror, brightness/contrast/saturation, grayscale, denoise, sharpen or blur, and fades that apply to picture and sound together.
- CUDA/NVENC acceleration when the chosen encoder and your FFmpeg build support it, with automatic CPU fallback.
- Real per-stream size detection via ffprobe packet scanning, plus empty, near-empty, and duplicate audio-track detection.
- Config-driven repeatable jobs, EBU R128 loudness normalization, and estimated output size before you start.
- Unicode and Persian paths, terminal drag-and-drop input, and PowerShell-safe command generation.
- The input file is never modified, and an output that would collide with the input is renamed automatically.

## Requirements

- Windows 11 is the primary target.
- Python 3 available as `py -3` or `python`.
- FFmpeg and ffprobe in PATH, **6.1 or newer**. On startup FFmWiz checks for them and can offer to install the full FFmpeg package with winget or Chocolatey. CI runs the whole suite against 6.1.1, 7.1.1 and the current release; older builds are untested. Some conveniences need a newer build and fall back automatically when it is missing — the exact-stretch square-pixel path uses `scale`'s `reset_sar`, added in the 7.2/8.0 line.
- NVIDIA GPU acceleration needs an FFmpeg build with CUDA/NVENC support.
- **PySide6** for the graphical editors. Install the runtime dependencies once:

  ```powershell
  py -3 -m pip install --upgrade -r requirements.txt
  ```

  Without PySide6 the CLI still works fully in terminal/manual mode.

## Quick Start

From PowerShell, in the project root:

```powershell
.\run.ps1
```

Or run Python directly:

```powershell
py -3 .\FFmWiz.py
```

If your execution policy blocks `.ps1` files:

```powershell
powershell -ExecutionPolicy Bypass -File ".\run.ps1"
```

Type `exit` at any prompt to leave FFmWiz. Type `0` to go back one step; the main menu has no previous step, so it does not offer it. After each completed FFmpeg run the wizard returns to the startup menu, so you can build another command without reopening the script.

### Optional terminal command

Run the installer once, then open a new PowerShell window:

```powershell
powershell -ExecutionPolicy Bypass -File ".\install-command.ps1"
FFmWiz
```

The installer adds the local `Commands` folder to your User PATH and adds or updates a marked `FFmWiz` function in your PowerShell profile. The PATH shim is a CMD wrapper that runs PowerShell with `ExecutionPolicy Bypass`, so the command still works where profile loading is blocked.

## Startup Modes

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

Mode 1 is the default: press Enter at the menu. Mode 2 runs the same wizard but skips every question already answered in `config.env` and asks the rest.

Each mode is explained in full in [docs/DOCUMENTATION-MODES.md §13](docs/DOCUMENTATION-MODES.md#13-modes-315-in-detail).

## Graphical editor

When PySide6 is available, video work in Mode 1 can open the **Unified Video Editor** — a dark Premiere-style workspace with real-time playback, a custom timeline, crop handles on the preview, and speed/reverse controls. It runs as a separate process over JSON IPC, so the Qt event loop never shares a thread with the CLI. Decline it and every edit stays available as a terminal prompt.

Two engines exist: `classic` (default, PySide6 widgets, full feature set) and `qml` (QtQuick, GPU scene graph, aspect-correct preview for mixed-orientation joins). Select one with `gui_engine=classic|qml` in `config.env` or the environment variable `FFMWIZ_GUI_ENGINE`, which overrides the config. The QML engine handles only the unified editor; if its files are missing, FFmWiz falls back to classic automatically.

Details, shortcuts, and the waveform model: [docs/DOCUMENTATION-ENCODING.md §12](docs/DOCUMENTATION-ENCODING.md#12-the-graphical-editors-classic-and-qml).

## Config file

Mode 2 reads its default answers from `config.env`, a simple `key=value` file created from `config.env.example` on first run. Your `config.env` is git-ignored; only the sanitized example is committed.

```powershell
Copy-Item config.env.example config.env
```

Every key is documented inline in the example file and in full in [docs/DOCUMENTATION-PROMPTS-AND-KEYS.md Appendix B](docs/DOCUMENTATION-PROMPTS-AND-KEYS.md#appendix-b--complete-configenv-key-reference).

## Logs

Every run writes a dated UTF-8 log to `Logs/` next to the script, preserving Unicode paths:

```text
Logs/ffmwiz_2026-05-14_22-30-15.log
```

The log holds the exact FFmpeg command, full FFmpeg output, ffprobe diagnostics, warnings, tracebacks, and the final elapsed time. Console output stays short; when something fails, FFmWiz prints a brief message **and** the log path. Set `logging_enabled=n` to stop creating logs and `log_retention_days=N` to prune old ones.

## Safety

- The input file is never changed.
- The final command is shown before FFmpeg starts.
- Overwriting applies only to the selected output path; if output and input would be the same file, the output name is changed automatically.
- Duplicate or empty audio tracks are removed only when you explicitly choose `d`, `e`, or `de`.

## Documentation

| Document | What is in it |
| --- | --- |
| [docs/DOCUMENTATION.md](docs/DOCUMENTATION.md) | The authoritative reference: every mode, prompt, flag, environment variable, and `config.env` key, plus recipes and worked sessions. |
| [docs/architecture.md](docs/architecture.md) | Project layout, package layering, the thin-entry re-export pattern, and how the GUI subprocess is wired. |
| [docs/FFMPEG-REFERENCE.md](docs/FFMPEG-REFERENCE.md) | Why FFmpeg support is build-specific, the generated capability snapshot, the capability cache, and how to inspect your own build. |

## License

FFmWiz is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public License** as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details. The full text is in the `LICENSE` file at the repository root, and at <https://www.gnu.org/licenses/>.

Two things this project carries that are not its own:

- `ffmwiz/muxcleanup/` is a vendored copy of [MuxCls](https://github.com/KiaroSama/MuxCls), by the same author, which is published under the MIT License. MIT terms permit its inclusion here; the vendored copy is distributed as part of this program under the GPL, and `tests/test_mux_cleanup_port.py` records exactly which lines diverge from upstream and why.
- FFmpeg and ffprobe are **not** bundled. FFmWiz invokes whatever build is on your `PATH` as a separate program, so their own licensing (LGPL or GPL, depending on how that build was configured) is a matter between you and the build you installed.

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
