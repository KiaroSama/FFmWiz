"""A04: every clock field survives the copy into the segment model.

The request carries the picture origin per segment. Classic's segment model
rebuilt each segment as a fresh dict and listed the fields it knew about, so
`picture_clock_offset` was dropped on the way in and the decoder fell back to
zero: for a first input whose picture starts at 1 s, an impulse at container
1.5 s landed at waveform sample 6000 instead of 2000. Both outputs were still
16000 samples long, which is why a duration-only test saw nothing.

`has_audio` was lost the same way one round earlier. A hand-listed copy is the
defect; carrying the segment forward and overriding what the model owns is the
repair, so the next field added upstream cannot go missing in silence.

The second half is the empty-after-trim case: a segment whose audio lies
entirely before its picture must decode to bounded SILENCE, not kill the whole
joined graph.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k join_segment_clock
"""
from __future__ import annotations

import array
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ffmwiz.gui.classic.gui_editor_unified import (build_classic_waveform_args,
                                                   build_join_segment_model)
from ffmwiz.gui.modern.gui_qml_waveform import WAVE_RATE, build_wave_decode_args

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = unittest.skipUnless(bool(FFMPEG), "ffmpeg not available")


def loudest_sample(pcm: bytes) -> int:
    samples = array.array("h")
    samples.frombytes(pcm[:len(pcm) - (len(pcm) % 2)])
    peak, where = 0, 0
    for index, value in enumerate(samples):
        if abs(value) > peak:
            peak, where = abs(value), index
    return where


class TheSegmentModelCarriesEveryClockField(unittest.TestCase):
    REQUEST = {
        "input_path": "first.mkv",
        "join_segments": [
            {"path": "first.mkv", "name": "first.mkv", "duration": 2.0,
             "has_audio": True, "picture_clock_offset": 1.0, "audio_stream": "a:1"},
            {"path": "second.mkv", "name": "second.mkv", "duration": 2.0,
             "has_audio": True, "picture_clock_offset": 0.0},
        ],
    }

    def model(self):
        return build_join_segment_model(self.REQUEST)

    def test_the_picture_origin_survives(self):
        self.assertEqual(1.0, self.model()[0]["picture_clock_offset"],
                         "the origin was dropped copying the segment, so the "
                         "decoder silently used zero")

    def test_the_selected_audio_stream_survives(self):
        self.assertEqual("a:1", self.model()[0]["audio_stream"])

    def test_the_fields_the_model_owns_are_still_computed(self):
        first, second = self.model()
        self.assertEqual(0.0, first["start"])
        self.assertEqual(2.0, first["end"])
        self.assertEqual(2.0, second["start"])
        self.assertEqual("Video 2", second["label"])
        self.assertTrue(second["has_audio"])

    def test_an_unknown_upstream_field_is_carried_too(self):
        # The point of the repair: the model copies the segment and overrides
        # what it owns, so a field added upstream cannot be lost by omission.
        request = {"join_segments": [dict(self.REQUEST["join_segments"][0],
                                          some_future_field="kept")]}
        self.assertEqual("kept", build_join_segment_model(request)[0]["some_future_field"])

    def test_a_missing_origin_defaults_to_zero(self):
        request = {"join_segments": [{"path": "a.mkv", "duration": 1.0}]}
        self.assertEqual(0.0, build_join_segment_model(request)[0]["picture_clock_offset"])


@requires_ffmpeg
class TheJoinedWaveformUsesEachSegmentsOrigin(unittest.TestCase):
    """Both engines, one clock, proved by where the impulse lands."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="ffmwiz_a04seg_"))
        # Picture starts at container 1.0 s; a tone sits at container 1.5 s,
        # which is picture 0.5 s -- sample 2000 at 4000 Hz.
        cls.late_picture = cls.root / "late_picture.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i",
             "sine=frequency=1000:duration=0.1,adelay=1500,apad=whole_dur=3.6",
             "-itsoffset", "1.0", "-f", "lavfi",
             "-i", "testsrc=size=160x120:rate=25:duration=2.6",
             "-map", "0:a", "-map", "1:v", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "pcm_s16le", "-t", "3.6", str(cls.late_picture)],
            check=True, timeout=240)
        # An ordinary second input with a tone of its own at 0.5 s.
        cls.plain = cls.root / "plain.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i",
             "sine=frequency=600:duration=0.1,adelay=500,apad=whole_dur=2",
             "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
             "-map", "0:a", "-map", "1:v", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "pcm_s16le", "-t", "2", str(cls.plain)],
            check=True, timeout=240)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def request(self, **overrides) -> dict:
        request = {
            "input_path": str(self.late_picture),
            "ffmpeg": FFMPEG,
            "duration": 4.0,
            "picture_clock_offset": 1.0,
            "join_segments": [
                {"path": str(self.late_picture), "name": "first", "duration": 2.0,
                 "has_audio": True, "picture_clock_offset": 1.0},
                {"path": str(self.plain), "name": "second", "duration": 2.0,
                 "has_audio": True, "picture_clock_offset": 0.0},
            ],
        }
        request.update(overrides)
        return request

    def decode(self, args: list[str], out: Path) -> bytes:
        result = subprocess.run([FFMPEG, *args], capture_output=True, timeout=240)
        self.assertEqual(0, result.returncode,
                         result.stderr.decode("utf-8", "replace")[-600:])
        return out.read_bytes()

    def test_the_qml_engine_places_the_first_impulse_on_the_picture_clock(self):
        out = self.root / "qml.pcm"
        request = self.request()
        data = self.decode(build_wave_decode_args(request, str(out)), out)
        self.assertEqual(4 * WAVE_RATE * 2, len(data), "the joined span is wrong")
        self.assertAlmostEqual(2000, loudest_sample(data[:2 * WAVE_RATE * 2]),
                               delta=int(0.12 * WAVE_RATE))

    def test_the_classic_engine_places_it_at_the_same_instant(self):
        out = self.root / "classic.pcm"
        request = self.request()
        segments = build_join_segment_model(request)
        data = self.decode(build_classic_waveform_args(request, segments, out), out)
        self.assertEqual(4 * WAVE_RATE * 2, len(data))
        self.assertAlmostEqual(2000, loudest_sample(data[:2 * WAVE_RATE * 2]),
                               delta=int(0.12 * WAVE_RATE),
                               msg="the classic model dropped the origin, so its "
                                   "waveform sits a second away from the picture")

    def test_the_later_segment_is_still_audible(self):
        out = self.root / "later.pcm"
        request = self.request()
        data = self.decode(build_wave_decode_args(request, str(out)), out)
        tail = data[2 * WAVE_RATE * 2:]
        self.assertAlmostEqual(2000, loudest_sample(tail), delta=int(0.15 * WAVE_RATE),
                               msg="the second segment's own tone was lost")


@requires_ffmpeg
class ASegmentWithNoAudioInsideItsPictureIsSilence(unittest.TestCase):
    """A04: an empty trim must produce bounded silence, not kill the graph.

    A valid input whose audio lies entirely BEFORE its first picture has, after
    trimming to the picture interval, no samples at all. Padding must therefore
    happen before the trim removes everything -- the other order left the
    filtergraph with an empty stream and FFmpeg exited 183, losing both the
    silent segment and every later one.

    This is not the same thing as a file with no audio STREAM, which is fed
    generated silence at the input stage.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="ffmwiz_a04sil_"))
        # Audio occupies 0.0-0.4 s; the picture starts at 1.0 s and runs 2 s.
        cls.pre_picture_audio = cls.root / "pre_audio.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "sine=frequency=800:duration=0.4",
             "-itsoffset", "1.0", "-f", "lavfi",
             "-i", "testsrc=size=160x120:rate=25:duration=2",
             "-map", "0:a", "-map", "1:v", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "pcm_s16le", str(cls.pre_picture_audio)],
            check=True, timeout=240)
        cls.audible = cls.root / "audible.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i",
             "sine=frequency=600:duration=0.1,adelay=500,apad=whole_dur=2",
             "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
             "-map", "0:a", "-map", "1:v", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "pcm_s16le", "-t", "2", str(cls.audible)],
            check=True, timeout=240)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def joined_request(self) -> dict:
        return {
            "input_path": str(self.pre_picture_audio),
            "ffmpeg": FFMPEG,
            "duration": 4.0,
            "join_segments": [
                {"path": str(self.pre_picture_audio), "name": "silent-after-trim",
                 "duration": 2.0, "has_audio": True, "picture_clock_offset": 1.0},
                {"path": str(self.audible), "name": "audible", "duration": 2.0,
                 "has_audio": True, "picture_clock_offset": 0.0},
            ],
        }

    def run_decode(self, args: list[str], out: Path) -> bytes:
        result = subprocess.run([FFMPEG, *args], capture_output=True, timeout=240)
        self.assertEqual(0, result.returncode,
                         "an empty trim killed the whole joined graph:\n"
                         + result.stderr.decode("utf-8", "replace")[-600:])
        return out.read_bytes()

    def test_the_alone_case_still_gives_the_picture_span_of_silence(self):
        out = self.root / "alone.pcm"
        request = {"input_path": str(self.pre_picture_audio), "ffmpeg": FFMPEG,
                   "duration": 2.0, "picture_clock_offset": 1.0}
        data = self.run_decode(build_wave_decode_args(request, str(out)), out)
        self.assertEqual(2 * WAVE_RATE * 2, len(data))

    def test_the_joined_case_keeps_both_segments(self):
        out = self.root / "joined.pcm"
        data = self.run_decode(build_wave_decode_args(self.joined_request(), str(out)), out)
        self.assertEqual(4 * WAVE_RATE * 2, len(data),
                         "the silent segment truncated the joined timeline")

    def test_the_later_segments_impulse_survives(self):
        out = self.root / "joined2.pcm"
        data = self.run_decode(build_wave_decode_args(self.joined_request(), str(out)), out)
        tail = data[2 * WAVE_RATE * 2:]
        self.assertAlmostEqual(2000, loudest_sample(tail), delta=int(0.15 * WAVE_RATE),
                               msg="the audible segment after the silent one was lost")

    def test_the_classic_engine_agrees(self):
        out = self.root / "joined_classic.pcm"
        request = self.joined_request()
        segments = build_join_segment_model(request)
        data = self.run_decode(build_classic_waveform_args(request, segments, out), out)
        self.assertEqual(4 * WAVE_RATE * 2, len(data))


if __name__ == "__main__":
    unittest.main()
