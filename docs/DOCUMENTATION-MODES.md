# FFmWiz Documentation - Modes 3-15, output and troubleshooting

The remaining startup modes, source-value warnings, the progress display, logging, troubleshooting and the glossary.

Part of the FFmWiz manual, split across files for size. The full table of
contents lives in [DOCUMENTATION.md](DOCUMENTATION.md).

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
