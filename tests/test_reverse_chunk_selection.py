"""The bounded reverse keeps every real forward chunk.

Split out of `test_bounded_audio_reverse.py`, which reached 878 lines. This is
a unit test over one pure function with a synthetic probe; everything left in
that file drives real FFmpeg end to end and shares the `NoLeakedArtifacts`
fixture, which this needs none of.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import FFmWiz  # noqa: E402,F401  (imported for the package side effects the rest relies on)


class TheChunkFilterDoesNotNeedADuration(unittest.TestCase):
    """CI found this: real segments discarded, exit code 0, 12% of the audio.

    `audio_reverse_chunk_paths` used to keep a forward chunk only when ffprobe
    reported `format.duration > 0`. That is not the question it means to ask --
    it means "is this the unreadable trailing stub the segment muxer leaves" --
    and a segment muxer does not have to write a duration into a matroska
    header. On the CI runners (ffmpeg 6.1.1 / 7.1.1 / 9.0.1 essentials, where
    ffprobe also logged "Could not calculate exact stream sizes with ffprobe
    packets") the reverse came out at 21776 samples instead of 176400, with a
    zero exit code, because every step it did check had succeeded.

    Measured on the collector, with a probe that reads the streams but cannot
    measure the container:

        before: kept 0/5      after: kept 4/5   (4 real + 1 stub written)
    """

    @staticmethod
    def _blind(real):
        """A probe that answers about streams but not about duration."""
        def probe(ffprobe, path, *a, **k):
            data = real(ffprobe, path, *a, **k) or {}
            fmt = dict(data.get("format") or {})
            fmt.pop("duration", None)
            out = dict(data)
            out["format"] = fmt
            for stream in out.get("streams") or []:
                stream.pop("duration", None)
                (stream.get("tags") or {}).pop("DURATION", None)
            return out
        return probe

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "ffmpeg/ffprobe required")
    def test_a_probe_with_no_duration_still_keeps_every_real_chunk(self):
        from ffmwiz import services
        from ffmwiz.support import ext04c

        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            source = work / "tone.flac"
            subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi",
                            "-i", "sine=frequency=440:sample_rate=44100:duration=4",
                            "-c:a", "flac", str(source)], check=True, timeout=120)
            subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(source),
                            "-map", "0:a:0", "-c:a", "flac",
                            "-f", "segment", "-segment_time", "1.000000",
                            "-segment_format", "matroska", "-reset_timestamps", "1",
                            str(work / "areverse_fwd_%05d.mkv")], check=True, timeout=120)
            written = sorted(work.glob("areverse_fwd_*"))
            self.assertGreaterEqual(len(written), 3, "the fixture did not segment")

            real = services.ffprobe_json
            blind = self._blind(real)
            services.ffprobe_json = blind
            ext04c.services.ffprobe_json = blind
            try:
                kept = ext04c.audio_reverse_chunk_paths(ffprobe, work, "areverse_fwd_")
            finally:
                services.ffprobe_json = real
                ext04c.services.ffprobe_json = real

            # Every file the muxer wrote except the trailing stub. The stub is
            # unreadable (ffprobe raises on its bare EBML header), which is what
            # separates it from a chunk that merely has no duration.
            self.assertEqual(len(written) - 1, len(kept),
                             "a probe that cannot measure the container must not "
                             "cost us real audio segments")
            self.assertNotIn(written[-1], kept, "the trailing stub still has to go")


if __name__ == "__main__":
    unittest.main()
