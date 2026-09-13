"""A04: the editor is told the PICTURE's span and origin, not the container's.

R05 fixed the decoders. The request that reaches them was never fixed, and the
request is where the clock is decided:

* a 2 s picture inside a 4 s container was announced as 4 s, so the waveform was
  built over 4 s of audio and every marker sat on the wrong clock;
* a join summed CONTAINER durations, so 2 s + 2 s of picture was announced as
  6 s;
* a file whose picture starts at container time 1 s carried no origin at all, so
  an impulse at container 1.5 s landed 1 s away from where the picture clock
  puts it.

`video_stream_span_seconds`, `join_item_picture_span` and `picture_clock_offset`
already existed and already encode this contract -- the request simply did not
ask them. These tests go through the real producer, `open_unified_video_gui`,
with only the subprocess launch replaced.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k editor_request_timeline
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ffmwiz import guibridge_b

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(bool(FFMPEG and FFPROBE), "ffmpeg/ffprobe not available")


def probe(path: Path) -> dict:
    result = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(path)],
                            capture_output=True, text=True, timeout=180)
    return json.loads(result.stdout or "{}")


def answers_for(path: Path) -> dict:
    """The answers dict the wizard hands the editor, built from a real probe."""
    data = probe(path)
    streams = data.get("streams") or []
    return {
        "input_path": path,
        "probe": data,
        "format": data.get("format") or {},
        "video_streams": [s for s in streams if s.get("codec_type") == "video"],
        "audio_streams": [s for s in streams if s.get("codec_type") == "audio"],
        "ffmpeg": FFMPEG,
    }


@requires_ffmpeg
class TheRequestCarriesThePictureClock(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="ffmwiz_a04_"))
        # 2 s of picture, 4 s of audio: the container is 4 s, the editor's
        # timeline is 2 s.
        cls.short_picture = cls.root / "short_picture.mkv"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        str(cls.short_picture)], check=True, timeout=240)
        # An ordinary 2 s clip, for the join arithmetic.
        cls.plain = cls.root / "plain.mkv"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
                        "-f", "lavfi", "-i", "sine=frequency=330:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(cls.plain)], check=True, timeout=240)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self) -> None:
        self.requests: list[dict] = []
        original = guibridge_b._launch_qt_gui

        def capture(request):
            self.requests.append(request)
            return {"status": "cancelled"}

        guibridge_b._launch_qt_gui = capture
        self.addCleanup(setattr, guibridge_b, "_launch_qt_gui", original)

    def build(self, answers: dict) -> dict:
        guibridge_b.open_unified_video_gui(answers)
        self.assertTrue(self.requests, "the producer never built a request")
        return self.requests[-1]

    def test_the_duration_is_the_pictures_not_the_containers(self):
        request = self.build(answers_for(self.short_picture))
        self.assertAlmostEqual(2.0, float(request["duration"]), delta=0.15,
                               msg="the editor was told the container's length, "
                                   "so its whole timeline is the wrong length")

    def test_an_ordinary_file_is_unchanged(self):
        request = self.build(answers_for(self.plain))
        self.assertAlmostEqual(2.0, float(request["duration"]), delta=0.15)

    def test_a_join_sums_picture_spans_not_containers(self):
        answers = answers_for(self.short_picture)
        second = answers_for(self.plain)
        answers["join_input_items"] = [{
            "path": self.plain,
            "duration": float((second["format"] or {}).get("duration") or 0.0),
            "probe": second["probe"],
            "video_streams": second["video_streams"],
            "audio_streams": second["audio_streams"],
        }]
        request = self.build(answers)
        self.assertAlmostEqual(4.0, float(request["duration"]), delta=0.3,
                               msg="the joined timeline summed containers, so every "
                                   "later input sits at the wrong offset")

    def test_each_join_segment_reports_its_own_picture_span(self):
        answers = answers_for(self.short_picture)
        second = answers_for(self.plain)
        answers["join_input_items"] = [{
            "path": self.plain,
            "duration": float((second["format"] or {}).get("duration") or 0.0),
            "probe": second["probe"],
            "video_streams": second["video_streams"],
            "audio_streams": second["audio_streams"],
        }]
        request = self.build(answers)
        spans = [float(segment["duration"]) for segment in request["join_segments"]]
        self.assertEqual(2, len(spans))
        self.assertAlmostEqual(2.0, spans[0], delta=0.15,
                               msg="segment 1 still reports its container length")

    def test_the_request_states_the_picture_origin(self):
        request = self.build(answers_for(self.short_picture))
        self.assertIn("picture_clock_offset", request,
                      "consumers cannot place audio on the picture clock without "
                      "being told where the picture starts")
        self.assertIsInstance(request["picture_clock_offset"], float)

    def test_the_origin_is_zero_for_an_ordinary_file(self):
        request = self.build(answers_for(self.plain))
        self.assertAlmostEqual(0.0, float(request["picture_clock_offset"]), delta=0.05)



@requires_ffmpeg
class TheWaveformSitsOnThePictureClock(unittest.TestCase):
    """The consumer half: an impulse must land where the PICTURE says it is.

    Carrying the origin in the request is only half the contract. A file whose
    picture starts at container time 1.0 s and whose audio carries an impulse at
    container 1.5 s must show that impulse at picture 0.5 s -- waveform sample
    2000 at 4000 Hz -- not at sample 6000 (A04).
    """

    @classmethod
    def setUpClass(cls) -> None:
        from ffmwiz.gui.modern.gui_qml_waveform import WAVE_RATE
        cls.rate = WAVE_RATE
        cls.root = Path(tempfile.mkdtemp(prefix="ffmwiz_a04w_"))
        # Audio: 0.5 s silence, a 0.1 s tone, then silence. Picture: starts one
        # second into the container, courtesy of -itsoffset on the video input.
        cls.source = cls.root / "offset_picture.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i",
             "sine=frequency=1000:duration=0.1,adelay=1500,apad=whole_dur=3.6",
             "-itsoffset", "1.0", "-f", "lavfi",
             "-i", "testsrc=size=160x120:rate=25:duration=2.6",
             "-map", "0:a", "-map", "1:v", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "pcm_s16le", "-t", "3.6", str(cls.source)],
            check=True, timeout=240)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def decode(self, request: dict) -> bytes:
        from ffmwiz.gui.modern.gui_qml_waveform import build_wave_decode_args
        out = self.root / f"pcm_{abs(hash(json.dumps(request, default=str))) % 10000}.raw"
        args = build_wave_decode_args(request, str(out))
        result = subprocess.run([FFMPEG, *args], capture_output=True, timeout=240)
        self.assertEqual(0, result.returncode,
                         result.stderr.decode("utf-8", "replace"))
        return out.read_bytes()

    def loudest_sample(self, pcm: bytes) -> int:
        import array
        samples = array.array("h")
        samples.frombytes(pcm[:len(pcm) - (len(pcm) % 2)])
        peak, where = 0, 0
        for index, value in enumerate(samples):
            if abs(value) > peak:
                peak, where = abs(value), index
        return where

    def test_the_impulse_lands_on_the_picture_clock(self):
        data = probe(self.source)
        streams = data.get("streams") or []
        answers = {
            "input_path": self.source, "probe": data,
            "format": data.get("format") or {},
            "video_streams": [s for s in streams if s.get("codec_type") == "video"],
            "audio_streams": [s for s in streams if s.get("codec_type") == "audio"],
            "ffmpeg": FFMPEG,
        }
        captured: list[dict] = []
        original = guibridge_b._launch_qt_gui
        guibridge_b._launch_qt_gui = lambda request: (captured.append(request),
                                                      {"status": "cancelled"})[1]
        self.addCleanup(setattr, guibridge_b, "_launch_qt_gui", original)
        guibridge_b.open_unified_video_gui(answers)
        request = captured[-1]

        offset = float(request.get("picture_clock_offset") or 0.0)
        self.assertGreater(offset, 0.5,
                           "the fixture's picture does not start late; the premise is gone")
        where = self.loudest_sample(self.decode(request))
        expected = int((1.5 - offset) * self.rate)
        self.assertAlmostEqual(expected, where, delta=int(0.12 * self.rate),
                               msg=f"the impulse is at sample {where}, but the picture "
                                   f"clock puts it at {expected}")



@requires_ffmpeg
class EverySegmentDeclaresItsOwnOrigin(unittest.TestCase):
    """A join is several clocks, not one. Each segment carries its own."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="ffmwiz_a04seg_"))
        cls.plain = cls.root / "plain.mkv"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
                        "-f", "lavfi", "-i", "sine=frequency=330:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(cls.plain)], check=True, timeout=240)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_each_segment_carries_a_picture_clock_offset(self):
        answers = answers_for(self.plain)
        second = answers_for(self.plain)
        answers["join_input_items"] = [{
            "path": self.plain, "duration": 2.0, "probe": second["probe"],
            "video_streams": second["video_streams"],
            "audio_streams": second["audio_streams"],
        }]
        captured: list[dict] = []
        original = guibridge_b._launch_qt_gui
        guibridge_b._launch_qt_gui = lambda request: (captured.append(request),
                                                      {"status": "cancelled"})[1]
        self.addCleanup(setattr, guibridge_b, "_launch_qt_gui", original)
        guibridge_b.open_unified_video_gui(answers)
        for index, segment in enumerate(captured[-1]["join_segments"]):
            with self.subTest(segment=index):
                self.assertIn("picture_clock_offset", segment)
                self.assertAlmostEqual(0.0, float(segment["picture_clock_offset"]),
                                       delta=0.05)


if __name__ == "__main__":
    unittest.main()
