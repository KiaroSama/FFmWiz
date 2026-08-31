# FFmWiz Documentation - Encoding reference

Video and audio encoding, cutting/splitting/joining, speed and reverse, and the graphical editors.

Part of the FFmWiz manual, split across files for size. The full table of
contents lives in [DOCUMENTATION.md](DOCUMENTATION.md).

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
