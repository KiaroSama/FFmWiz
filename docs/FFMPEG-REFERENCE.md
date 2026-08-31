# FFmpeg capability reference

FFmpeg support is **build-specific**. A codec, muxer, filter, protocol, or
hardware accelerator can be documented upstream and still be missing from the
`ffmpeg.exe` on your machine, because that build was compiled without the
required library. FFmWiz therefore never assumes a capability: it reads the
installed binary and keeps the answers close at hand.

This page covers the three places those answers live. For everything else, see
the [full documentation](DOCUMENTATION.md).

---

## 1. `ffmwiz-ffmpeg-reference.txt` — a snapshot of your build

The first time FFmWiz runs it writes a plain-text capability snapshot next to
`FFmWiz.py`:

```text
ffmwiz-ffmpeg-reference.txt
```

The file is git-ignored, because it describes *your* FFmpeg build and nobody
else's. It is a straight capture of the following commands, one section each,
in this order:

| Section | Command |
|---------|---------|
| FFmpeg version + build configuration | `ffmpeg -hide_banner -version` |
| Supported formats (container names) | `ffmpeg -hide_banner -formats` |
| Output muxers | `ffmpeg -hide_banner -muxers` |
| Input demuxers | `ffmpeg -hide_banner -demuxers` |
| Codecs (all) | `ffmpeg -hide_banner -codecs` |
| Encoders | `ffmpeg -hide_banner -encoders` |
| Decoders | `ffmpeg -hide_banner -decoders` |
| Bitstream filters | `ffmpeg -hide_banner -bsfs` |
| Filters | `ffmpeg -hide_banner -filters` |
| Protocols | `ffmpeg -hide_banner -protocols` |
| Hardware acceleration methods | `ffmpeg -hide_banner -hwaccels` |
| Pixel formats | `ffmpeg -hide_banner -pix_fmts` |
| Sample (audio) formats | `ffmpeg -hide_banner -sample_fmts` |
| Channel layouts | `ffmpeg -hide_banner -layouts` |
| Colors / color spaces | `ffmpeg -hide_banner -colors` |

### Regenerating it after an FFmpeg upgrade

The snapshot is not refreshed automatically, so upgrade FFmpeg and then do one
of the following:

```powershell
# Easiest: delete it. It is recreated on the next run.
Remove-Item .\ffmwiz-ffmpeg-reference.txt

# Or force a refresh at startup (both spellings work):
.\run.ps1 --refresh-ffmpeg-reference
FFmWiz --regen-ffmpeg-reference
py -3 .\FFmWiz.py --refresh-ffmpeg-reference
```

---

## 2. The capability cache (Mode 14)

Some questions cannot be answered by a `-list` command — for example "does this
encoder plus this container actually preserve full color range?". FFmWiz probes
those by running a tiny real encode once, then caches the verdict per FFmpeg
environment so later runs stay fast:

```text
ffmwiz/support/.cache/ffmpeg_capabilities.json
```

Menu entry **14. FFmpeg capability cache (diagnostics)** manages that cache:

```text
1 = View cached capability results
2 = Re-probe current encoder/container capabilities
3 = Clear capability cache
0 = Back
```

- **Re-probe** re-tests `libx264`, `libx265`, `h264_nvenc`, and `hevc_nvenc`
  against `mp4` and `mkv`, and stores the fresh results.
- **Clear** deletes only the two files FFmWiz owns
  (`ffmpeg_capabilities.json` and its `.corrupt` recovery backup). The `.cache`
  directory itself, your settings, and your logs are never touched.

Set `FFMWIZ_CACHE_DIR` to relocate the cache — useful for an isolated or
throwaway run. See [Appendix K](DOCUMENTATION-RECIPES.md#appendix-k--environment-variables).

Clearing or re-probing the cache costs a few seconds on the next encode, never
correctness: an empty cache simply means FFmWiz probes again.

---

## 3. The in-code reference block

A long comment block explains FFmpeg's format/codec landscape and the
build-specific caveat directly in the source. Search any editor for the tag:

```text
FULL_FFMPEG_FORMAT_CODEC_LISTS
```

You will get two hits: a short pointer near the top of `FFmWiz.py`, and the
reference block itself in `ffmwiz/core/constants.py`.

---

## Inspecting your build by hand

The snapshot is a convenience, not a requirement. These are the commands worth
knowing when you need to check one thing quickly:

```powershell
ffmpeg -hide_banner -encoders          # every encoder this build has
ffmpeg -hide_banner -hwaccels          # available hardware acceleration
ffmpeg -h encoder=hevc_nvenc           # options for one encoder
ffmpeg -h muxer=mp4                    # options and defaults for one muxer
ffmpeg -hide_banner -filters           # available filters (e.g. subtitles, scale_cuda)
```

If an encoder you expect is absent, the build lacks it — reinstall a fuller
FFmpeg package rather than changing FFmWiz settings. FFmWiz falls back to CPU
encoding automatically when an NVENC encoder is unavailable.
