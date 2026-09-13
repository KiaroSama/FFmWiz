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
import time
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


@requires_ffmpeg
class TheSingleInputWaveformFollowsThePictureClock(unittest.TestCase):
    """R05 -- one span contract for single media, not just joins.

    The joined branch was bounded; the single-input branch decoded the audio
    stream as-is. A 4-second video carrying 1 second of audio produced 4000
    samples instead of 16000, and audio delayed by a second started at sample 1
    instead of 4001 -- so the editor drew the sound in the wrong place on a
    timeline it stretched to fit.
    """

    PICTURE = 4.0

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="ffmwiz_r05_"))
        cls.short = cls.root / "short_audio.mp4"     # 4 s picture, 1 s audio
        cls.long = cls.root / "long_audio.mp4"       # 4 s picture, 6 s audio
        cls.late = cls.root / "late_audio.mp4"       # audio starts at 1 s
        cls.exact = cls.root / "exact.mp4"           # 4 s picture, 4 s audio
        cls._encode(cls.short, "sine=frequency=440:duration=1")
        cls._encode(cls.long, "sine=frequency=440:duration=6")
        cls._encode(cls.exact, "sine=frequency=440:duration=4")
        cls._encode(cls.late, "sine=frequency=990:duration=1", delay_ms=1000)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def _encode(cls, path: Path, audio: str, delay_ms: int = 0) -> None:
        filters = ["-af", f"adelay={delay_ms}|{delay_ms}"] if delay_ms else []
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", f"testsrc=size=64x48:rate=10:duration={cls.PICTURE}",
                        "-f", "lavfi", "-i", audio,
                        "-map", "0:v", "-map", "1:a", *filters,
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        str(path)], check=True, timeout=180)

    def decode(self, source: Path, duration: float | None = PICTURE):
        request = {"input_path": str(source)}
        if duration is not None:
            request["duration"] = duration
        out = self.root / "single.pcm"
        args = build_wave_decode_args(request, str(out))
        result = subprocess.run([FFMPEG, *args], capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        pcm = decode_pcm_samples(out.read_bytes())
        out.unlink(missing_ok=True)
        return pcm

    def peak(self, pcm, start: float, end: float) -> int:
        lo, hi = int(start * WAVE_RATE), int(end * WAVE_RATE)
        return max(abs(int(v)) for v in pcm[lo:hi])

    def test_short_audio_is_padded_to_the_picture_span(self):
        pcm = self.decode(self.short)
        self.assertEqual(len(pcm), int(self.PICTURE * WAVE_RATE))
        self.assertGreater(self.peak(pcm, 0.1, 0.9), 1000)
        self.assertEqual(self.peak(pcm, 1.5, 3.9), 0, "the pad is not silent")

    def test_long_audio_is_trimmed_to_the_picture_span(self):
        pcm = self.decode(self.long)
        self.assertEqual(len(pcm), int(self.PICTURE * WAVE_RATE))

    def test_matching_audio_is_unchanged(self):
        self.assertEqual(len(self.decode(self.exact)), int(self.PICTURE * WAVE_RATE))

    def test_a_delayed_audio_track_keeps_its_offset(self):
        pcm = self.decode(self.late)
        self.assertEqual(len(pcm), int(self.PICTURE * WAVE_RATE))
        self.assertEqual(self.peak(pcm, 0.0, 0.9), 0,
                         "the delayed audio was slid to zero, losing its offset")
        self.assertGreater(self.peak(pcm, 1.1, 1.9), 1000,
                           "the audible part is not where the picture puts it")

    def test_an_unknown_duration_decodes_what_is_there(self):
        pcm = self.decode(self.short, duration=None)
        self.assertGreater(len(pcm), 0)
        self.assertLess(len(pcm), int(self.PICTURE * WAVE_RATE),
                        "an unknown duration must not be invented")

    def test_the_classic_engine_uses_the_same_contract(self):
        out = self.root / "classic_single.pcm"
        args = build_classic_waveform_args({"input_path": str(self.short),
                                            "duration": self.PICTURE}, [], out)
        result = subprocess.run([FFMPEG, *args], capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        pcm = decode_pcm_samples(out.read_bytes())
        out.unlink(missing_ok=True)
        self.assertEqual(len(pcm), int(self.PICTURE * 4000))


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
        # A one-second viewport over a ONE-SAMPLE clip: the first column holds
        # that sample and the remaining seven are outside the PCM entirely.
        # They are silence. Returning a single stretched column instead was the
        # clamping defect R06 removed -- it moved real data to the wrong time.
        single = array.array("h", [32767])
        columns = waveform_window(single, None, None, WAVE_RATE, 0, 1, 8)
        self.assertEqual(len(columns), 8)
        self.assertEqual(columns[0], [32767 / 32768.0, 32767 / 32768.0])
        self.assertTrue(all(column == [0.0, 0.0] for column in columns[1:]))

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


class EveryColumnMatchesAnIndependentOracle(unittest.TestCase):
    """R06 -- each column is its own sample interval, computed independently.

    The oracle here does not share a line of code with the implementation: it
    slices the raw PCM by the column's sample bounds and takes min/max. The
    audit ran 100 seeded sparse-transient viewports through exactly this
    comparison and 45 disagreed, because whole envelope buckets were being
    counted out across columns instead of placed by sample position.
    """

    RATE = WAVE_RATE

    def oracle(self, samples, start, end, width):
        first, last = start * self.RATE, end * self.RATE
        span = last - first
        total = len(samples)
        expected = []
        for column in range(width):
            lo = first + span * column / width
            hi = first + span * (column + 1) / width
            low = max(0, int(lo) if lo >= 0 else 0)
            high = min(total, int(hi) + (1 if hi > int(hi) else 0))
            window = samples[low:high]
            if not window:
                expected.append([0.0, 0.0])
            else:
                expected.append([min(window) / 32768.0, max(window) / 32768.0])
        return expected

    def backends(self, samples):
        yield "stdlib", array.array("h", samples)
        if has_numpy():
            import numpy as np
            yield "numpy", np.array(samples, dtype=np.int16)

    def assert_matches(self, samples, start, end, width):
        expected = self.oracle(samples, start, end, width)
        for label, pcm in self.backends(samples):
            with self.subTest(backend=label, window=(start, end), width=width):
                env = build_wave_envelope(pcm)
                actual = waveform_window(pcm, *env, self.RATE, start, end, width)
                self.assertEqual(len(actual), len(expected))
                for index, (got, want) in enumerate(zip(actual, expected)):
                    self.assertAlmostEqual(got[0], want[0], places=6,
                                           msg=f"column {index} min")
                    self.assertAlmostEqual(got[1], want[1], places=6,
                                           msg=f"column {index} max")

    def test_the_audit_counterexample_lands_in_column_zero(self):
        samples = [0] * 4000
        samples[550] = 32767
        self.assert_matches(samples, 0.025, 1.0, 8)

    def test_a_viewport_longer_than_the_pcm_keeps_its_position(self):
        samples = [0] * 2000 + [32767] + [0] * 1999      # one second of PCM
        self.assert_matches(samples, 0.0, 4.0, 8)

    def test_one_hundred_seeded_sparse_transient_viewports(self):
        import random
        rng = random.Random(20260913)
        length = 40000
        samples = [0] * length
        for position in rng.sample(range(length), 40):
            samples[position] = rng.choice([32767, -32768, 20000, -15000])
        checked = 0
        for _ in range(100):
            start = rng.uniform(0.0, 9.0)
            end = start + rng.uniform(0.05, 3.0)
            width = rng.choice([4, 8, 16, 64, 200])
            self.assert_matches(samples, start, end, width)
            checked += 1
        self.assertEqual(checked, 100)

    def test_boundary_impulses_at_every_column_edge(self):
        width = 8
        for column in range(width):
            samples = [0] * 8192
            samples[column * 1024] = 32767          # exactly on a column edge
            with self.subTest(column=column):
                self.assert_matches(samples, 0.0, 8192 / self.RATE, width)

    def test_partial_first_last_and_internal_buckets(self):
        samples = [((-1) ** i) * ((i * 37) % 30000) for i in range(20000)]
        for start, end, width in ((0.013, 1.987, 8), (0.5, 4.5, 16),
                                  (0.0, 5.0, 5), (1.234, 1.235, 4)):
            self.assert_matches(samples, start, end, width)

    def test_a_view_entirely_past_the_pcm_is_silence(self):
        samples = [32767] * 1000
        for label, pcm in self.backends(samples):
            with self.subTest(backend=label):
                env = build_wave_envelope(pcm)
                columns = waveform_window(pcm, *env, self.RATE, 10.0, 12.0, 8)
                self.assertTrue(all(column == [0.0, 0.0] for column in columns))

    def test_the_work_stays_bounded(self):
        samples = [0] * 200000
        pcm = array.array("h", samples)
        env = build_wave_envelope(pcm)
        started = time.monotonic()
        columns = waveform_window(pcm, *env, self.RATE, 0.0, 50.0, 4000)
        self.assertEqual(len(columns), 4000)
        self.assertLess(time.monotonic() - started, 10.0)


if __name__ == "__main__":
    unittest.main()
