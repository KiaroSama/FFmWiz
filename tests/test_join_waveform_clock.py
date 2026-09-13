"""F06/F07: the waveform must span the timeline it is drawn under.

F06 -- each joined input was fed to `concat` unbounded, so the decoded PCM was
as long as the AUDIO, not as long as the declared SEGMENTS. Two 2 s segments
whose first input carried 0.5 s of audio produced 2.5 s of waveform, so every
marker after the first segment sat at the wrong time.
F07 -- the envelope truncated to whole 256-sample buckets and the viewport used
floor-aligned bounds, so a peak in the final fraction of a clip read back as
silence in every zoomed-out view.

Sample counts are checked exactly, against real FFmpeg output.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k join_waveform_clock
"""
from __future__ import annotations

import array
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz
from ffmwiz.gui.classic.gui_editor_unified import build_classic_waveform_args
from ffmwiz.gui.modern.gui_qml_waveform import (WAVE_ENV_STEP, WAVE_RATE,
                                                build_wave_decode_args,
                                                build_wave_envelope,
                                                decode_pcm_samples,
                                                segment_audio_stream_spec,
                                                waveform_window)

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = unittest.skipUnless(bool(FFMPEG), "ffmpeg is not available")


def has_numpy() -> bool:
    import importlib.util
    return importlib.util.find_spec("numpy") is not None


@requires_ffmpeg
class TheJoinedWaveformFollowsTheSegmentClock(unittest.TestCase):
    """F06 -- exact sample counts at WAVE_RATE, with a boundary probe."""

    SEGMENT = 2.0

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="ffmwiz_f06_"))
        cls.short = cls.root / "short.mp4"        # 2 s picture, 0.5 s of audio
        cls.long = cls.root / "long.mp4"          # 2 s picture, 3 s of audio
        cls.equal = cls.root / "equal.mp4"        # 2 s picture, 2 s of audio
        cls.late = cls.root / "late.mp4"          # audio starting at 1 s
        cls.silent = cls.root / "silent.mp4"      # no audio stream at all
        cls._encode(cls.short, "sine=frequency=440:duration=0.5")
        cls._encode(cls.long, "sine=frequency=880:duration=3")
        cls._encode(cls.equal, "sine=frequency=660:duration=2")
        cls._encode(cls.late, "adelay=1000|1000", source="sine=frequency=990:duration=1")
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.silent)], check=True, timeout=180)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def _encode(cls, path: Path, audio: str, source: str | None = None) -> None:
        if source is None:
            args = ["-f", "lavfi", "-i", audio]
            filters: list[str] = []
        else:
            args = ["-f", "lavfi", "-i", source]
            filters = ["-af", audio]
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=2",
                        *args, "-map", "0:v", "-map", "1:a", *filters,
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        str(path)], check=True, timeout=180)

    def decode(self, segments, builder=build_wave_decode_args):
        out = self.root / "wave.pcm"
        args = builder({"join_segments": segments}, str(out)) if builder is build_wave_decode_args \
            else builder({}, segments, str(out))
        result = subprocess.run([FFMPEG, *args], capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        pcm = decode_pcm_samples(out.read_bytes())
        out.unlink(missing_ok=True)
        return pcm

    def segments(self, *paths, has_audio=None):
        flags = has_audio or [True] * len(paths)
        return [{"path": str(path), "duration": self.SEGMENT, "has_audio": flag}
                for path, flag in zip(paths, flags)]

    def assert_spans(self, pcm, seconds: float) -> None:
        self.assertEqual(len(pcm), int(round(seconds * WAVE_RATE)),
                         f"the waveform does not span {seconds} s")

    def peak(self, pcm, start: float, end: float) -> int:
        lo, hi = int(start * WAVE_RATE), int(end * WAVE_RATE)
        return max(abs(int(value)) for value in pcm[lo:hi])

    def test_a_short_first_segment_is_padded_to_its_declared_length(self):
        pcm = self.decode(self.segments(self.short, self.equal))
        self.assert_spans(pcm, 4.0)
        self.assertEqual(self.peak(pcm, 1.6, 1.99), 0, "the pad is not silent")
        self.assertGreater(self.peak(pcm, 2.01, 2.4), 1000,
                           "segment two does not start at 2 s")

    def test_a_long_first_segment_is_trimmed_to_its_declared_length(self):
        pcm = self.decode(self.segments(self.long, self.short))
        self.assert_spans(pcm, 4.0)
        self.assertGreater(self.peak(pcm, 1.6, 1.99), 1000)
        self.assertGreater(self.peak(pcm, 2.01, 2.4), 1000,
                           "segment two does not start at 2 s")
        self.assertEqual(self.peak(pcm, 3.0, 3.9), 0,
                         "segment two's short audio was not padded")

    def test_equal_lengths_are_unchanged(self):
        pcm = self.decode(self.segments(self.equal, self.equal))
        self.assert_spans(pcm, 4.0)

    def test_a_silent_first_input_still_places_the_second_at_two_seconds(self):
        pcm = self.decode(self.segments(self.silent, self.equal, has_audio=[False, True]))
        self.assert_spans(pcm, 4.0)
        self.assertEqual(self.peak(pcm, 0.0, 1.99), 0)
        self.assertGreater(self.peak(pcm, 2.01, 2.4), 1000)

    def test_a_silent_later_input_keeps_the_timeline_length(self):
        pcm = self.decode(self.segments(self.equal, self.silent, has_audio=[True, False]))
        self.assert_spans(pcm, 4.0)
        self.assertEqual(self.peak(pcm, 2.5, 3.9), 0)

    def test_a_nonzero_audio_origin_keeps_its_offset(self):
        pcm = self.decode(self.segments(self.late, self.equal))
        self.assert_spans(pcm, 4.0)
        self.assertEqual(self.peak(pcm, 0.0, 0.9), 0,
                         "the late audio was slid back to zero, losing its offset")
        self.assertGreater(self.peak(pcm, 1.1, 1.9), 1000)

    def test_three_segments_span_the_whole_timeline(self):
        pcm = self.decode(self.segments(self.short, self.long, self.equal))
        self.assert_spans(pcm, 6.0)

    def test_the_classic_editor_lays_audio_on_the_same_clock(self):
        segments = [{"path": self.short, "duration": self.SEGMENT, "has_audio": True},
                    {"path": self.equal, "duration": self.SEGMENT, "has_audio": True}]
        out = self.root / "classic.pcm"
        args = build_classic_waveform_args({}, segments, out)
        result = subprocess.run([FFMPEG, *args], capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        pcm = decode_pcm_samples(out.read_bytes())
        out.unlink(missing_ok=True)
        self.assertEqual(len(pcm), int(4.0 * 4000))

    def test_the_waveform_is_not_stretched_to_hide_missing_time(self):
        """A shorter array scaled to the timeline would pass a span check that
        only looked at the drawn width; the sample count cannot be faked."""
        pcm = self.decode(self.segments(self.short, self.short))
        self.assert_spans(pcm, 4.0)
        self.assertEqual(self.peak(pcm, 0.6, 1.9), 0)
        self.assertEqual(self.peak(pcm, 2.6, 3.9), 0)


class TheAdvertisedAudioStreamIsHonoured(unittest.TestCase):
    """F06 -- the cache key hashes `audio_stream`, so the decode must use it."""

    def test_the_default_is_the_first_audio_stream(self):
        self.assertEqual(segment_audio_stream_spec({}), "a:0")

    def test_a_request_level_selection_reaches_the_filtergraph(self):
        req = {"audio_stream": "a:1", "join_segments": [
            {"path": "a.mkv", "duration": 1.0, "has_audio": True}]}
        joined = " ".join(build_wave_decode_args(req, "o.pcm"))
        self.assertIn("[0:a:1]", joined)

    def test_a_per_segment_selection_wins(self):
        req = {"audio_stream": "a:1", "join_segments": [
            {"path": "a.mkv", "duration": 1.0, "has_audio": True, "audio_stream": "a:2"}]}
        self.assertIn("[0:a:2]", " ".join(build_wave_decode_args(req, "o.pcm")))

    def test_the_single_input_path_honours_it_too(self):
        joined = " ".join(build_wave_decode_args({"input_path": "a.mkv", "audio_stream": "a:3"}, "o.pcm"))
        self.assertIn("[0:a:3]", joined)


class TheEnvelopeKeepsItsLastBucket(unittest.TestCase):
    """F07 -- with both backends, on windows that really use the envelope.

    `waveform_window` only reads the envelope when one output column spans more
    than `WAVE_ENV_STEP` samples. A test drawn at a width that falls under that
    silently exercises the raw path instead and proves nothing about the bug,
    so every case here asserts the envelope path was actually taken.
    """

    def envelope_backends(self, samples):
        """The same PCM as a stdlib array and (when installed) as numpy."""
        yield "stdlib", array.array("h", samples)
        if has_numpy():
            import numpy as np
            yield "numpy", np.array(samples, dtype=np.int16)

    def assert_uses_envelope(self, total, start, end, width):
        span = int(end * WAVE_RATE) - int(start * WAVE_RATE)
        self.assertGreater(span / max(1, width), WAVE_ENV_STEP,
                           "this window reads raw PCM, so it cannot test the envelope")
        self.assertLessEqual(int(end * WAVE_RATE), total + WAVE_RATE)

    def raw_reference(self, samples, start, end):
        lo, hi = int(start * WAVE_RATE), int(end * WAVE_RATE)
        window = samples[lo:hi] or [0]
        return min(window) / 32768.0, max(window) / 32768.0

    def test_a_peak_in_the_final_partial_bucket_survives(self):
        # 40000 samples is not a multiple of 256; the last bucket is partial
        # and holds the only nonzero sample in the clip.
        samples = [0] * 39999 + [32767]
        self.assert_uses_envelope(len(samples), 0.0, 10.0, 8)
        for label, pcm in self.envelope_backends(samples):
            with self.subTest(backend=label):
                env_min, env_max = build_wave_envelope(pcm)
                coarse = waveform_window(pcm, env_min, env_max, WAVE_RATE, 0.0, 10.0, 8)
                detail = waveform_window(pcm, None, None, WAVE_RATE, 0.0, 10.0, 8)
                self.assertAlmostEqual(max(pair[1] for pair in coarse),
                                       max(pair[1] for pair in detail), places=6)
                self.assertAlmostEqual(max(pair[1] for pair in coarse),
                                       32767 / 32768.0, places=6)

    def test_the_envelope_covers_every_sample(self):
        for length in (256, 257, 300, 4000, 4001, 5000):
            for label, pcm in self.envelope_backends([0] * (length - 1) + [32767]):
                with self.subTest(length=length, backend=label):
                    env_min, env_max = build_wave_envelope(pcm)
                    if env_max is None:
                        continue
                    self.assertEqual(len(env_max), -(-length // WAVE_ENV_STEP))
                    self.assertEqual(max(env_max), 32767)

    def test_the_first_and_last_samples_are_reachable(self):
        samples = [32767] + [0] * 39998 + [-32768]
        self.assert_uses_envelope(len(samples), 0.0, 10.0, 8)
        for label, pcm in self.envelope_backends(samples):
            with self.subTest(backend=label):
                env = build_wave_envelope(pcm)
                whole = waveform_window(pcm, *env, WAVE_RATE, 0.0, 10.0, 8)
                self.assertAlmostEqual(max(pair[1] for pair in whole), 32767 / 32768.0, places=6)
                self.assertAlmostEqual(min(pair[0] for pair in whole), -1.0, places=6)

    def test_an_out_of_viewport_peak_is_not_drawn(self):
        # One spike at 10.0 s inside a 20 s clip, read through the envelope.
        samples = [0] * 40000 + [32767] + [0] * 39999
        self.assert_uses_envelope(len(samples), 0.0, 9.9, 8)
        for label, pcm in self.envelope_backends(samples):
            with self.subTest(backend=label):
                env = build_wave_envelope(pcm)
                before = waveform_window(pcm, *env, WAVE_RATE, 0.0, 9.9, 8)
                self.assertEqual(max(pair[1] for pair in before), 0.0,
                                 "a peak past the right edge leaked into the view")
                after = waveform_window(pcm, *env, WAVE_RATE, 10.1, 20.0, 8)
                self.assertEqual(max(pair[1] for pair in after), 0.0,
                                 "a peak before the left edge leaked into the view")
                across = waveform_window(pcm, *env, WAVE_RATE, 0.0, 12.0, 8)
                self.assertAlmostEqual(max(pair[1] for pair in across), 32767 / 32768.0, places=6)

    def test_a_whole_clip_view_reports_the_true_extremes(self):
        samples = [((-1) ** i) * ((i * 997) % 32768) for i in range(43210)]
        low, high = self.raw_reference(samples, 0.0, len(samples) / WAVE_RATE)
        self.assert_uses_envelope(len(samples), 0.0, len(samples) / WAVE_RATE, 8)
        for label, pcm in self.envelope_backends(samples):
            with self.subTest(backend=label):
                env = build_wave_envelope(pcm)
                pairs = waveform_window(pcm, *env, WAVE_RATE, 0.0,
                                        len(samples) / WAVE_RATE, 8)
                self.assertAlmostEqual(max(pair[1] for pair in pairs), high, places=6)
                self.assertAlmostEqual(min(pair[0] for pair in pairs), low, places=6)

    def test_arbitrary_windows_never_exceed_the_raw_samples(self):
        samples = [((-1) ** i) * ((i * 997) % 32768) for i in range(43210)]
        for label, pcm in self.envelope_backends(samples):
            for start, end in ((0.0, 10.0), (1.1, 3.35), (5.0, 10.8001), (0.0, 2.05)):
                with self.subTest(backend=label, window=(start, end)):
                    env = build_wave_envelope(pcm)
                    pairs = waveform_window(pcm, *env, WAVE_RATE, start, end, 8)
                    low, high = self.raw_reference(samples, start, end)
                    self.assertLessEqual(max(pair[1] for pair in pairs), high + 1e-6)
                    self.assertGreaterEqual(min(pair[0] for pair in pairs), low - 1e-6)

    def test_a_sub_bucket_view_reads_raw_samples(self):
        samples = [0] * 200 + [32767] + [0] * 3799
        for label, pcm in self.envelope_backends(samples):
            with self.subTest(backend=label):
                env = build_wave_envelope(pcm)
                pairs = waveform_window(pcm, *env, WAVE_RATE, 0.045, 0.055, 8)
                self.assertAlmostEqual(max(pair[1] for pair in pairs), 32767 / 32768.0, places=6)

    def test_degenerate_inputs_do_not_raise(self):
        self.assertEqual(build_wave_envelope(None), (None, None))
        self.assertEqual(build_wave_envelope(array.array("h", [])), (None, None))
        self.assertEqual(build_wave_envelope(array.array("h", [7])), (None, None))
        self.assertEqual(waveform_window(None, None, None, WAVE_RATE, 0, 1, 8), [])
        single = array.array("h", [32767])
        self.assertEqual(waveform_window(single, None, None, WAVE_RATE, 0, 1, 8),
                         [[32767 / 32768.0, 32767 / 32768.0]])

    def test_silence_and_alternating_extrema(self):
        for samples in ([0] * 40000, [32767, -32768] * 20000):
            for label, pcm in self.envelope_backends(samples):
                with self.subTest(backend=label, kind=samples[0]):
                    env = build_wave_envelope(pcm)
                    pairs = waveform_window(pcm, *env, WAVE_RATE, 0.0, 10.0, 8)
                    self.assertAlmostEqual(max(pair[1] for pair in pairs),
                                           max(samples) / 32768.0, places=6)
                    self.assertAlmostEqual(min(pair[0] for pair in pairs),
                                           min(samples) / 32768.0, places=6)


if __name__ == "__main__":
    unittest.main()
