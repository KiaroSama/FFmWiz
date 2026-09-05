"""Regression: audio reverse must never buffer the whole track (D13).

`areverse` holds its ENTIRE input in RAM, exactly as `reverse` does for video,
so peak memory tracks duration directly. The video side gained a segmented
executor; audio kept executing the generated one-shot command. Measured on a
six-hour synthetic input through the public standalone builder:

    DURATION_S 21600.0
    AREVERSE_COUNT 1
    INPUT_COUNT 1
    BOUNDS_PRESENT False
    SEGMENT_OR_MEMORY_POLICY False

which is about 8.3 GiB of decoded samples at 48 kHz stereo.

`run_bounded_audio_reverse()` is the one plan every audio-reverse entry point
shares:

  1. ONE forward decode into lossless chunks, cuts applied (continuous);
  2. reverse each chunk, lossless in and lossless out (bounded);
  3. concatenate the chunks in REVERSE order, because reverse(A||B) is
     exactly reverse(B)||reverse(A);
  4. the original job again, re-pointed at the reversed scratch, applying only
     the filters whose semantics stay continuous -- atempo, LoudNorm,
     resampling. Splitting one of those per chunk would change the result at
     every boundary, which is why the REVERSAL is staged and the filter graph
     is not.

A job that already fits the budget runs the one-shot command unchanged: it is
bounded by construction and staging it would cost an extra decode for nothing.

Verified against the one-shot reverse of the same fixture rather than against
the argv. On an integer (FLAC) source the staged result is BIT-IDENTICAL; on a
float-decoded (AAC) source the 24-bit lossless scratch costs -138.5 dBFS peak /
-143.7 dBFS RMS, the quantisation floor, far below the noise floor of the codec
that produced the float samples.

The budget is forced small here by patching the SHARED peak-budget constants,
so the real budget arithmetic still runs and a four-second fixture really does
produce ten chunks.
"""
from __future__ import annotations

import contextlib
import io
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import FFmWiz

from artifact_guard import NoLeakedArtifacts
from ffmwiz import modes_transform
from ffmwiz.support import ext04b, ext04c

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

RATE = 44100
TONES = (300, 600, 1200, 2400)


def _run(args, timeout=300):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, encoding="utf-8",
                          errors="replace", timeout=timeout)


def _goertzel(values, rate, freq):
    """Energy at one frequency; enough to tell the four marker tones apart."""
    count = len(values)
    if count == 0:
        return 0.0
    bin_index = int(0.5 + count * freq / rate)
    omega = 2 * math.pi * bin_index / count
    coeff = 2 * math.cos(omega)
    first = second = 0.0
    for value in values:
        current = value + coeff * first - second
        second, first = first, current
    return math.sqrt(max(0.0, first * first + second * second
                         - coeff * first * second)) / count


def _probe_seconds(path):
    """The container duration, or None when ffprobe cannot say."""
    out = _run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nk=1:nw=1", str(path)])
    try:
        return float((out.stdout or "").strip())
    except (TypeError, ValueError):
        return None


class TheMemoryBudget(unittest.TestCase):
    """The arithmetic, without ffmpeg."""

    def test_the_budget_shrinks_as_the_stream_gets_heavier(self):
        stereo = ext04b.reverse_audio_segment_seconds_for(48000, 2, "s32")
        mono = ext04b.reverse_audio_segment_seconds_for(48000, 1, "s32")
        wide = ext04b.reverse_audio_segment_seconds_for(192000, 8, "s32")
        self.assertAlmostEqual(mono, stereo * 2, delta=1.0)
        self.assertLess(wide, stereo / 10)

    def test_a_segment_never_exceeds_the_shared_peak_budget(self):
        for rate, channels, fmt in ((48000, 2, "s32"), (44100, 1, "s16"),
                                    (192000, 8, "s32"), (8000, 1, "u8")):
            seconds = ext04b.reverse_audio_segment_seconds_for(rate, channels, fmt)
            held = (seconds * rate * channels
                    * ext04b.decoded_bytes_per_sample(fmt)
                    * FFmWiz.REVERSE_FRAME_SAFETY)
            self.assertLessEqual(
                held + FFmWiz.REVERSE_FIXED_OVERHEAD_BYTES,
                FFmWiz.REVERSE_PEAK_BUDGET_BYTES,
                f"{rate}Hz {channels}ch {fmt} exceeds the peak budget")

    def test_unknown_geometry_budgets_for_a_demanding_case(self):
        # Same policy as the video splitter: assume something expensive rather
        # than something convenient.
        self.assertEqual(ext04b.reverse_audio_segment_seconds_for(192000, 8, "s32"),
                         ext04b.reverse_audio_segment_seconds_for(None, None))

    def test_a_float_source_is_carried_at_24_bits_and_an_integer_one_is_not_widened(self):
        self.assertEqual("s32", ext04b.intermediate_audio_sample_fmt("fltp"))
        self.assertEqual("s32", ext04b.intermediate_audio_sample_fmt("s32p"))
        self.assertEqual("s16", ext04b.intermediate_audio_sample_fmt("s16p"))

    def test_more_than_eight_channels_leaves_flac_for_pcm(self):
        # FLAC tops out at eight channels; downmixing silently would be worse.
        self.assertIn("flac", ext04b.lossless_scratch_audio_args(
            [{"channels": 6, "sample_fmt": "s16p"}]))
        self.assertIn("pcm_s32le", ext04b.lossless_scratch_audio_args(
            [{"channels": 12, "sample_fmt": "fltp"}]))

    def test_the_widest_selected_track_also_sets_the_scratch_width(self):
        # Taking the FIRST stream's format carried a float-decoded second track
        # at 16 bits because track 0 happened to be 16-bit PCM.
        mixed = [{"channels": 2, "sample_fmt": "s16p"},
                 {"channels": 2, "sample_fmt": "fltp"}]
        self.assertEqual(["-c:a", "flac", "-sample_fmt", "s32"],
                         ext04b.lossless_scratch_audio_args(mixed))
        self.assertEqual(["-c:a", "flac", "-sample_fmt", "s16"],
                         ext04b.lossless_scratch_audio_args(mixed[:1]))

    # --- the aggregate budget (D05) -------------------------------------
    #
    # This class used to assert that the WIDEST selected track set the segment
    # length. That was the defect, written down: `areverse` buffers every
    # selected stream in one process, so their decoded buffers coexist and the
    # cost is the SUM. Eight worst-case tracks were planned at 227.951302 s,
    # an estimated 12.5 GiB peak against a 2.00 GiB cap -- 6.25x over.

    def _aggregate_peak_gib(self, streams, seconds):
        per_second = sum(
            stream["sample_rate"] * stream["channels"]
            * ext04b.decoded_bytes_per_sample(
                ext04b.intermediate_audio_sample_fmt(stream["sample_fmt"]))
            * FFmWiz.REVERSE_FRAME_SAFETY
            for stream in streams)
        return ((FFmWiz.REVERSE_FIXED_OVERHEAD_BYTES + per_second * seconds)
                / 1024 ** 3)

    def test_every_selected_track_is_paid_for(self):
        wide = {"sample_rate": 192000, "channels": 8, "sample_fmt": "fltp"}
        one = ext04b.reverse_audio_segment_seconds_for_streams([wide])
        eight = ext04b.reverse_audio_segment_seconds_for_streams([dict(wide)] * 8)
        self.assertAlmostEqual(one / 8, eight, delta=one / 800,
                               msg="eight identical tracks must cost eight times")

    def test_the_aggregate_peak_stays_inside_the_cap(self):
        cap = FFmWiz.REVERSE_PEAK_BUDGET_BYTES / 1024 ** 3
        cases = {
            "one 48k stereo": [{"sample_rate": 48000, "channels": 2, "sample_fmt": "s16p"}],
            "heterogeneous": [
                {"sample_rate": 44100, "channels": 1, "sample_fmt": "s16p"},
                {"sample_rate": 192000, "channels": 8, "sample_fmt": "fltp"},
            ],
            "eight worst-case": [
                {"sample_rate": 192000, "channels": 8, "sample_fmt": "fltp"}] * 8,
        }
        for label, streams in cases.items():
            with self.subTest(case=label):
                seconds = ext04b.reverse_audio_segment_seconds_for_streams(streams)
                peak = self._aggregate_peak_gib(streams, seconds)
                self.assertLessEqual(peak, cap * 1.001,
                                     f"{label}: {peak:.2f} GiB against a {cap:.2f} GiB cap")

    def test_the_entry_the_executor_calls_uses_the_aggregate(self):
        # The tests above exercise the arithmetic directly. This is the
        # answers-level entry the executor actually reaches, and it is where
        # the `min(per-stream window)` lived.
        answers = {"audio_streams": [
            {"sample_rate": 44100, "channels": 1, "sample_fmt": "s16p"},
            {"sample_rate": 192000, "channels": 8, "sample_fmt": "fltp"},
        ]}
        self.assertEqual(
            ext04b.reverse_audio_segment_seconds_for_streams(answers["audio_streams"]),
            ext04b.audio_reverse_segment_seconds(answers, [0, 1]))
        self.assertLess(
            ext04b.audio_reverse_segment_seconds(answers, [0, 1]),
            ext04b.reverse_audio_segment_seconds_for(192000, 8, "s32"),
            "budgeting only the widest track is what overran the cap")

    def test_the_old_widest_track_policy_really_did_overrun(self):
        # Guard the guard: if the cap ever grew enough for the old number, the
        # assertions above would prove nothing about the defect.
        eight = [{"sample_rate": 192000, "channels": 8, "sample_fmt": "fltp"}] * 8
        widest = ext04b.reverse_audio_segment_seconds_for(192000, 8, "s32")
        self.assertGreater(self._aggregate_peak_gib(eight, widest),
                           4 * FFmWiz.REVERSE_PEAK_BUDGET_BYTES / 1024 ** 3)

    def test_a_heterogeneous_selection_lands_on_a_whole_sample(self):
        streams = [{"sample_rate": 44100, "channels": 2, "sample_fmt": "s16p"},
                   {"sample_rate": 48000, "channels": 2, "sample_fmt": "s16p"}]
        seconds = ext04b.reverse_audio_segment_seconds_for_streams(streams)
        self.assertAlmostEqual(round(seconds * 48000), seconds * 48000, places=6,
                               msg="the window must land on the highest rate's grid")

    def test_unknown_metadata_is_budgeted_as_the_demanding_case(self):
        unknown = ext04b.reverse_audio_segment_seconds_for_streams([{}])
        worst = ext04b.reverse_audio_segment_seconds_for_streams(
            [{"sample_rate": 192000, "channels": 8, "sample_fmt": "s32"}])
        self.assertEqual(worst, unknown)

    def test_a_selection_that_cannot_fit_one_sample_is_refused(self):
        # Never an arbitrary minimum duration: refuse, which is what the cap
        # means.
        streams = [{"sample_rate": 192000, "channels": 8, "sample_fmt": "fltp"}] * 8
        with mock.patch.object(ext04c, "REVERSE_PEAK_BUDGET_BYTES",
                               FFmWiz.REVERSE_FIXED_OVERHEAD_BYTES + 8):
            with self.assertRaises(FFmWiz.ReverseBudgetError):
                ext04b.reverse_audio_segment_seconds_for_streams(streams)

    # --- the selection itself (D04) --------------------------------------
    def test_the_main_executors_track_selection_is_honoured(self):
        # `audio_index` alone was the whole answer, so a two-track selection
        # expressed as `audio_tracks` silently reversed track 0 and dropped the
        # rest.
        self.assertEqual([0, 1], ext04b.audio_reverse_indices(
            {"audio_tracks": [0, 1], "audio_streams": [{}, {}]}))

    def test_the_single_track_tools_still_get_their_track(self):
        self.assertEqual([1], ext04b.audio_reverse_indices(
            {"audio_index": 1, "audio_streams": [{}, {}]}))

    def test_a_missing_key_is_not_read_as_keep_them_all(self):
        self.assertEqual([0], ext04b.audio_reverse_indices(
            {"audio_streams": [{}, {}]}))

    def test_duplicates_collapse_and_order_is_the_selection_order(self):
        self.assertEqual([1, 0], ext04b.audio_reverse_indices(
            {"audio_tracks": [1, 1, 0], "audio_streams": [{}, {}]}))

    def test_an_impossible_index_is_refused_not_silently_changed(self):
        with self.assertRaises(ValueError):
            ext04b.audio_reverse_indices(
                {"audio_tracks": [5], "audio_streams": [{}, {}]})


@requires_ffmpeg
class BoundedAudioReverse(NoLeakedArtifacts, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._class_tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_arevfix_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._class_tmp, ignore_errors=True)

    def setUp(self):
        super().setUp()
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_arevcase_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    # ---- fixtures ----
    def _tone_source(self, name, codec, *, channels=1, rate=RATE, tones=TONES,
                     cover=False):
        """One second per marker tone, so the chunk ORDER is readable back out."""
        path = self._class_tmp / name
        if path.exists():
            return path
        inputs, labels = [], []
        for index, freq in enumerate(tones):
            inputs += ["-f", "lavfi", "-i",
                       f"sine=frequency={freq}:duration=1:sample_rate={rate}"]
            labels.append(f"[{index}:a]")
        args = [FFMPEG, "-hide_banner", "-v", "error", "-y", *inputs]
        if cover:
            art = self._class_tmp / "cover.png"
            if not art.exists():
                _run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                      "color=c=orange:size=64x64:duration=0.04", "-frames:v", "1",
                      str(art)])
            args += ["-i", str(art)]
        args += ["-filter_complex",
                 "".join(labels) + f"concat=n={len(tones)}:v=0:a=1[a]",
                 "-map", "[a]"]
        if cover:
            args += ["-map", f"{len(tones)}:v", "-c:v", "copy",
                     "-disposition:v:0", "attached_pic"]
        args += ["-ac", str(channels), "-c:a", codec, str(path)]
        result = _run(args)
        if result.returncode != 0:
            self.skipTest("could not build the fixture: " + (result.stderr or "")[-300:])
        # VERIFY the fixture before any test measures something downstream
        # of it. It is cached in `_class_tmp` and shared by every case in
        # the class, so one short build makes a whole suite fail with
        # sample counts that look like a pipeline defect. On CI, several
        # unrelated cases -- mono and 6-channel alike -- all reported the
        # SAME 21776 samples, which is the signature of a shared input,
        # not of the code under test.
        seconds = _probe_seconds(path)
        expected = float(len(tones))
        if seconds is None or abs(seconds - expected) > 0.05:
            raise AssertionError(
                f"the {expected:.0f}s fixture {name} came out at "
                f"{seconds if seconds is None else round(seconds, 3)}s; every "
                "measurement taken from it would be meaningless. Built with: "
                + " ".join(str(part) for part in args[-14:]))
        return path

    def _answers(self, source, ext, **extra):
        probe = FFmWiz.ffprobe_full_json(FFPROBE, source)
        streams = probe.get("streams", [])
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": source,
            "probe": probe, "format": probe.get("format", {}),
            "video_streams": [s for s in streams if s.get("codec_type") == "video"],
            "audio_streams": [s for s in streams if s.get("codec_type") == "audio"],
            "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
            "audio_index": 0, "reverse_audio": True, "speed_factor": 1.0,
            # A FILE path, not a folder: resolve_audio_tool_output_ext() reads
            # the suffix, so these jobs write lossless FLAC and the comparison
            # against the one-shot is not muddied by a lossy encode.
            "output_location": self._tmp / f"result.{ext}",
        }
        answers.update(extra)
        self.own(answers)
        FFmWiz.artifact_lease(answers)
        FFmWiz.begin_plan(answers)
        return answers

    # ---- the executor ----
    def _tiny_budget(self, rate, channels, sample_fmt, seconds=0.5):
        """Force a budget that makes a four-second fixture chunk many times.

        The SHARED constants are patched, not the segment function, so the real
        budget arithmetic is what produces the chunk count.
        """
        per_second = (rate * channels
                      * ext04b.decoded_bytes_per_sample(sample_fmt)
                      * FFmWiz.REVERSE_FRAME_SAFETY)
        # `ext04c` owns the bounded-reverse arithmetic since the split; the
        # constants it reads are its OWN module globals, so patching `ext04b`
        # -- which merely re-exports it -- reaches nothing.
        return [mock.patch.object(ext04c, "REVERSE_PEAK_BUDGET_BYTES",
                                  int(per_second * seconds)),
                mock.patch.object(ext04c, "REVERSE_FIXED_OVERHEAD_BYTES", 0)]

    def _execute(self, answers, builder, patches=()):
        with contextlib.redirect_stdout(io.StringIO()):
            answers["cmd"] = [str(part) for part in builder(answers)]
        noise = io.StringIO()
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            stack.enter_context(contextlib.redirect_stdout(noise))
            code, _elapsed = FFmWiz.run_bounded_audio_reverse(
                answers, builder, label="Audio Speed / Reverse")
        return code, Path(answers["output_path"]), noise.getvalue()

    def _one_shot_reference(self, answers):
        """The very command the builder produced, run unstaged.

        The only oracle worth having: a hand-written reference command would be
        a second implementation of the job, and any disagreement would then be
        ambiguous. This one answers exactly the question the repair has to --
        does the staged plan produce what the user was shown?
        """
        reference = list(answers["cmd"])
        target = self._tmp / ("reference" + Path(answers["output_path"]).suffix)
        reference[-1] = str(target)
        result = _run(reference)
        self.assertEqual(0, result.returncode, (result.stderr or "")[-600:])
        return target

    # ---- measurements ----
    def _samples(self, path, channels=1):
        result = subprocess.run(
            [FFMPEG, "-v", "error", "-i", str(path), "-map", "0:a:0",
             "-f", "s16le", "-c:a", "pcm_s16le", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
        return len(result.stdout) // (2 * channels)

    def _mono_digest(self, path):
        """MD5 of the decoded mono samples.

        Compared instead of the sample tuples themselves: `assertEqual` on two
        176,400-element sequences makes unittest build a full unified diff on
        failure, which turned a one-line mismatch into a run that never
        finished. A failing comparison has to fail FAST.
        """
        import hashlib
        result = subprocess.run(
            [FFMPEG, "-v", "error", "-i", str(path), "-map", "0:a:0",
             "-f", "s16le", "-c:a", "pcm_s16le", "-ac", "1", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
        return hashlib.md5(result.stdout).hexdigest()

    def _mono_pcm(self, path):
        result = subprocess.run(
            [FFMPEG, "-v", "error", "-i", str(path), "-map", "0:a:0",
             "-f", "s16le", "-c:a", "pcm_s16le", "-ac", "1", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
        raw = result.stdout
        return struct.unpack("<%dh" % (len(raw) // 2), raw)

    def _tone_order(self, values, windows=4, rate=RATE):
        """The dominant marker tone in each of `windows` equal slices.

        Sliced by fraction rather than by seconds, so a tempo change does not
        move the window boundaries off the tones.
        """
        order = []
        width = len(values) // max(1, windows)
        for index in range(windows):
            window = values[index * width:(index + 1) * width]
            energies = {tone: _goertzel(window, rate, tone) for tone in TONES}
            order.append(max(energies, key=energies.get))
        return order

    def _streams(self, path):
        probe = FFmWiz.ffprobe_full_json(FFPROBE, path)
        return probe.get("streams", [])

    def _surviving_workspaces(self):
        """Both scratch roots the executor opens, named exactly.

        NoLeakedArtifacts points `tempfile.tempdir` at a private root, so a
        surviving workspace cannot be confused with a sibling worker's.
        """
        root = Path(tempfile.gettempdir())
        return sorted(path.name for prefix in ("ffmwiz_areverse_", "ffmwiz_arevjob_")
                      for path in root.glob(prefix + "*"))

    # ---- tests ----
    def test_a_short_job_still_runs_the_command_the_user_was_shown(self):
        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac")
        code, output, noise = self._execute(
            answers, FFmWiz.build_audio_speed_reverse_command)
        self.assertEqual(0, code, noise[-600:])
        self.assertNotIn("lossless segment", noise,
                         "four seconds fits the real budget many times over")
        self.assertEqual(4 * RATE, self._samples(output))

    def test_a_short_join_refuses_instead_of_reporting_success(self):
        """The last unguarded step: nothing measured what the concat produced.

        Every stage checks its own exit code, and the concat demuxer exits 0 for
        whatever it managed to read -- so a truncated reverse reached the user as
        a success. Joining only the first chunk stands in for any cause of that.
        """
        # Patch the JOIN, not the list: the parts now reach ffmpeg as its own
        # inputs through the concat filter, so shortening the list file no
        # longer shortens the result.
        from ffmwiz.support import ext04c
        real = ext04c.build_audio_concat_filter_command

        def only_the_first(ffmpeg, parts, target, *a, **k):
            return real(ffmpeg, list(parts)[:1], target, *a, **k)

        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac")
        code, output, noise = self._execute(
            answers, FFmWiz.build_audio_speed_reverse_command,
            list(self._tiny_budget(RATE, 1, "s16"))
            + [mock.patch.object(ext04c, "build_audio_concat_filter_command", only_the_first)])
        self.assertNotEqual(0, code, "a short join must fail the run")
        self.assertIn("Refusing to report a truncated result", noise)

    def test_losing_a_segment_refuses_instead_of_writing_short_audio(self):
        """The net under the fix above: any FUTURE cause of chunk loss is loud.

        The concat joins whatever list it is handed, so nothing downstream can
        notice a missing piece -- which is how CI got 21776 samples out of
        176400 with exit code 0. Dropping a MIDDLE chunk here stands in for any
        such cause: the run must refuse, not truncate.
        """
        from ffmwiz.support import ext04c
        real = ext04c.audio_reverse_chunk_paths

        def lose_one(*a, **k):
            kept = real(*a, **k)
            return kept[:1] + kept[2:] if len(kept) > 2 else kept

        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac")
        code, output, noise = self._execute(
            answers, FFmWiz.build_audio_speed_reverse_command,
            list(self._tiny_budget(RATE, 1, "s16"))
            + [mock.patch.object(ext04c, "audio_reverse_chunk_paths", lose_one)])
        self.assertNotEqual(0, code, "a lost segment must fail the run")
        self.assertIn("Refusing to write truncated audio", noise)
        self.assertFalse(output.exists() and output.stat().st_size > 0,
                         "no truncated output may be left behind")

    def test_many_chunks_reverse_to_a_bit_identical_result(self):
        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac")
        code, output, noise = self._execute(
            answers, FFmWiz.build_audio_speed_reverse_command,
            self._tiny_budget(RATE, 1, "s16"))
        self.assertEqual(0, code, noise[-600:])
        self.assertIn("lossless segment", noise, "the staged plan must have run")
        reference = self._one_shot_reference(answers)
        self.assertEqual(self._samples(reference), self._samples(output),
                         "the sample count must match the one-shot reverse exactly")
        self.assertEqual(4 * RATE, self._samples(output))
        self.assertEqual(self._mono_digest(reference), self._mono_digest(output),
                         "an integer source must round-trip bit-identically")

    def test_the_chunks_come_back_in_the_right_order(self):
        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac")
        code, output, noise = self._execute(
            answers, FFmWiz.build_audio_speed_reverse_command,
            self._tiny_budget(RATE, 1, "s16"))
        self.assertEqual(0, code, noise[-600:])
        self.assertEqual([2400, 1200, 600, 300], self._tone_order(self._mono_pcm(output)),
                         "the marker tones must arrive in reverse source order")

    def test_a_float_decoded_source_stays_below_the_24_bit_floor(self):
        source = self._tone_source("stereo.m4a", "aac", channels=2)
        answers = self._answers(source, "flac")
        code, output, noise = self._execute(
            answers, FFmWiz.build_audio_speed_reverse_command,
            self._tiny_budget(RATE, 2, "s32"))
        self.assertEqual(0, code, noise[-600:])
        reference = self._one_shot_reference(answers)
        self.assertEqual(self._samples(reference), self._samples(output))
        staged, expected = self._mono_pcm(output), self._mono_pcm(reference)
        self.assertEqual(len(expected), len(staged))
        peak = max(abs(a - b) for a, b in zip(staged, expected))
        # The 24-bit lossless scratch costs the source about -138 dBFS, which
        # is below the 16-bit floor this comparison uses: measured 0.
        self.assertLessEqual(peak, 2, f"peak sample difference {peak}")

    def test_a_multichannel_source_keeps_every_channel(self):
        source = self._tone_source("surround.flac", "flac", channels=6)
        answers = self._answers(source, "flac")
        code, output, noise = self._execute(
            answers, FFmWiz.build_audio_speed_reverse_command,
            self._tiny_budget(RATE, 6, "s16"))
        self.assertEqual(0, code, noise[-600:])
        self.assertEqual(6, int(self._streams(output)[0].get("channels")))
        self.assertEqual(4 * RATE, self._samples(output, channels=6))

    def test_a_different_sample_rate_survives_the_staging(self):
        source = self._tone_source("hires.flac", "flac", rate=96000)
        answers = self._answers(source, "flac")
        code, output, noise = self._execute(
            answers, FFmWiz.build_audio_speed_reverse_command,
            self._tiny_budget(96000, 1, "s16"))
        self.assertEqual(0, code, noise[-600:])
        self.assertEqual(96000, int(self._streams(output)[0].get("sample_rate")))
        self.assertEqual(4 * 96000, self._samples(output))

    def test_speed_is_applied_once_after_the_reversal(self):
        # atempo is stateful: applying it per chunk would change the result at
        # every boundary, so it belongs to the continuous final stage.
        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac", speed_factor=2.0)
        code, output, noise = self._execute(
            answers, FFmWiz.build_audio_speed_reverse_command,
            self._tiny_budget(RATE, 1, "s16"))
        self.assertEqual(0, code, noise[-600:])
        self.assertAlmostEqual(2.0, self._samples(output) / RATE, delta=0.05,
                               msg="4 s at 2x must come back as 2 s, applied once")
        self.assertEqual([2400, 1200, 600, 300],
                         self._tone_order(self._mono_pcm(output)),
                         "the reversed order must survive the tempo change")

    def test_cuts_are_applied_once_on_the_forward_pass(self):
        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac",
                                audio_cut_keep_ranges=[(0.0, 1.0), (2.0, 3.0)],
                                audio_speed_enabled=True, audio_speed_factor=1.0,
                                reverse_audio=True)
        code, output, noise = self._execute(
            answers, FFmWiz.build_audio_transform_command,
            self._tiny_budget(RATE, 1, "s16", seconds=0.25))
        self.assertEqual(0, code, noise[-600:])
        self.assertAlmostEqual(2.0, self._samples(output) / RATE, delta=0.05,
                               msg="two one-second keep ranges, applied once")
        # Kept 300 Hz then 1200 Hz; reversed, 1200 Hz has to come first.
        self.assertEqual([1200, 300], self._tone_order(self._mono_pcm(output), windows=2))

    def test_loudnorm_runs_once_on_the_joined_result(self):
        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac", loudnorm_enabled=True,
                                loudnorm_mode="single", audio_speed_enabled=True,
                                audio_speed_factor=1.0)
        code, output, noise = self._execute(
            answers, FFmWiz.build_audio_transform_command,
            self._tiny_budget(RATE, 1, "s16"))
        self.assertEqual(0, code, noise[-600:])
        # LoudNorm resamples to 48 kHz and its gating moves energy around, so
        # the one-shot -- not a guessed tone order -- is the oracle. Applying
        # it per chunk would renormalise every boundary and could not match.
        reference = self._one_shot_reference(answers)
        self.assertEqual(self._samples(reference, channels=1),
                         self._samples(output, channels=1))
        self.assertEqual(self._mono_digest(reference), self._mono_digest(output),
                         "LoudNorm must run once, on the joined result")

    def test_cover_art_survives_the_staged_pipeline(self):
        source = self._tone_source("cover.flac", "flac", cover=True)
        if not any(s.get("codec_type") == "video" for s in self._streams(source)):
            self.skipTest("this FFmpeg build did not attach the cover art")
        answers = self._answers(source, "flac")
        code, output, noise = self._execute(
            answers, FFmWiz.build_audio_speed_reverse_command,
            self._tiny_budget(RATE, 1, "s16"))
        self.assertEqual(0, code, noise[-600:])
        kinds = [s.get("codec_type") for s in self._streams(output)]
        self.assertIn("video", kinds, "the cover art must reach the output")
        self.assertEqual(4 * RATE, self._samples(output))

    def test_declining_the_run_says_the_printed_command_is_unbounded(self):
        # "Start now? n" hands the user the ONE-SHOT command. For a track past
        # the budget that IS the unbounded reverse, so it must not look
        # equivalent to what FFmWiz would have run (B04's lesson).
        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac")
        self.assertEqual("", ext04b.audio_reverse_one_shot_warning(answers),
                         "a short track really does run in one bounded pass")
        with contextlib.ExitStack() as stack:
            for patch in self._tiny_budget(RATE, 1, "s16"):
                stack.enter_context(patch)
            warning = ext04b.audio_reverse_one_shot_warning(answers)
        self.assertIn("GiB", warning, warning)
        self.assertIn("bounded lossless", warning)

    def test_a_job_with_no_reverse_is_never_warned_about(self):
        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac", reverse_audio=False)
        with contextlib.ExitStack() as stack:
            for patch in self._tiny_budget(RATE, 1, "s16"):
                stack.enter_context(patch)
            self.assertEqual("", ext04b.audio_reverse_one_shot_warning(answers))

    def test_a_container_that_under_reports_does_not_shrink_the_job(self):
        """The root cause of the CI failure, pinned.

        `audio_reverse_content_seconds` read `format.duration` and nothing else,
        so a container whose header under-reports sized the WHOLE job from a
        wrong number: the segment count, the peak-budget decision and the length
        check on the joined result all agreed on a track that was not there.
        Nothing complained and the reverse came out short -- CI measured 21776
        samples of a 176400-sample track with exit code 0.

        Measured on the real function, 4 s source, 0.5 s segments:

            container            before        after
            healthy              4.000s / 8    4.000s / 8
            says 0.494s          0.494s / 1    4.000s / 8
            no duration at all   0.000s / 1    4.000s / 8
        """
        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac")
        truth, _content, _ranges = ext04c.audio_reverse_content_seconds(answers)
        self.assertAlmostEqual(4.0, truth, delta=0.05, msg="fixture is not 4 s")

        for label, fmt in (
                ("under-reporting", {**answers["format"], "duration": "0.494000"}),
                ("no duration", {k: v for k, v in answers["format"].items()
                                 if k != "duration"})):
            with self.subTest(container=label):
                probe = dict(answers)
                probe["format"] = fmt
                duration, _c, _r = ext04c.audio_reverse_content_seconds(probe)
                self.assertAlmostEqual(
                    truth, duration, delta=0.05,
                    msg=f"a {label} container still sizes the job; the audio "
                        "stream's own duration must win")

    def test_an_unknown_duration_is_refused_rather_than_run_unbounded(self):
        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac")
        with contextlib.redirect_stdout(io.StringIO()):
            answers["cmd"] = [str(part) for part in
                              FFmWiz.build_audio_speed_reverse_command(answers)]
        # BOTH sources, not just the container. The duration is read from the
        # audio stream first and the container second, so blanking only the
        # container no longer describes a job whose length is unknown -- it
        # describes one the stream can still answer for. The assertion below
        # is unchanged; only the situation it sets up is honest again.
        answers["format"] = {}
        answers["audio_streams"] = [
            {key: value for key, value in stream.items()
             if key not in ("duration", "tags")}
            for stream in (answers.get("audio_streams") or [])]
        noise = io.StringIO()
        with contextlib.redirect_stdout(noise):
            code, _elapsed = FFmWiz.run_bounded_audio_reverse(
                answers, FFmWiz.build_audio_speed_reverse_command, label="check")
        self.assertEqual(1, code)
        self.assertIn("bounded reverse", noise.getvalue())
        self.assertFalse(Path(answers["output_path"]).exists(),
                         "nothing may be produced by a refused job")

    def test_too_many_segments_is_calculated_and_refused(self):
        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac")
        with contextlib.redirect_stdout(io.StringIO()):
            answers["cmd"] = [str(part) for part in
                              FFmWiz.build_audio_speed_reverse_command(answers)]
        noise = io.StringIO()
        patches = self._tiny_budget(RATE, 1, "s16", seconds=0.001)
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            stack.enter_context(contextlib.redirect_stdout(noise))
            code, _elapsed = FFmWiz.run_bounded_audio_reverse(
                answers, FFmWiz.build_audio_speed_reverse_command, label="check")
        self.assertEqual(1, code)
        self.assertIn(str(ext04b.AUDIO_REVERSE_MAX_SEGMENTS), noise.getvalue(),
                      "the refusal must state the limit it calculated against")
        self.assertFalse(Path(answers["output_path"]).exists())

    def test_a_failing_stage_leaves_no_scratch_behind(self):
        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac")
        with contextlib.redirect_stdout(io.StringIO()):
            answers["cmd"] = [str(part) for part in
                              FFmWiz.build_audio_speed_reverse_command(answers)]
        patches = self._tiny_budget(RATE, 1, "s16")
        calls = {"count": 0}
        real_runner = ext04b.run_ffmpeg_with_progress

        def failing(cmd, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:      # the first chunk reversal
                return 1, 0.0
            return real_runner(cmd, *args, **kwargs)

        noise = io.StringIO()
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            stack.enter_context(mock.patch.object(
                ext04c, "run_ffmpeg_with_progress", failing))
            stack.enter_context(contextlib.redirect_stdout(noise))
            code, _elapsed = FFmWiz.run_bounded_audio_reverse(
                answers, FFmWiz.build_audio_speed_reverse_command, label="check")
        self.assertEqual(1, code)
        # NoLeakedArtifacts points tempfile.tempdir at a private root, so a
        # surviving workspace shows up as a leak in cleanup.
        self.assertEqual([], self._surviving_workspaces())

    def test_a_cancelled_run_leaves_no_scratch_behind(self):
        source = self._tone_source("mono.flac", "flac")
        answers = self._answers(source, "flac")
        with contextlib.redirect_stdout(io.StringIO()):
            answers["cmd"] = [str(part) for part in
                              FFmWiz.build_audio_speed_reverse_command(answers)]
        patches = self._tiny_budget(RATE, 1, "s16")

        def cancelled(*args, **kwargs):
            raise KeyboardInterrupt()

        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            stack.enter_context(mock.patch.object(
                ext04c, "run_ffmpeg_with_progress", cancelled))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            with self.assertRaises(KeyboardInterrupt):
                FFmWiz.run_bounded_audio_reverse(
                    answers, FFmWiz.build_audio_speed_reverse_command, label="check")
        self.assertEqual([], self._surviving_workspaces())


@requires_ffmpeg
class ThePublicDispatchersUseIt(NoLeakedArtifacts, unittest.TestCase):
    """The two standalone modes, driven through their real dispatchers."""

    def setUp(self):
        super().setUp()
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_arevmode_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.source = self._tmp / "tones.flac"
        inputs, labels = [], []
        for index, freq in enumerate(TONES):
            inputs += ["-f", "lavfi", "-i",
                       f"sine=frequency={freq}:duration=1:sample_rate={RATE}"]
            labels.append(f"[{index}:a]")
        result = _run([FFMPEG, "-hide_banner", "-v", "error", "-y", *inputs,
                       "-filter_complex",
                       "".join(labels) + f"concat=n={len(TONES)}:v=0:a=1[a]",
                       "-map", "[a]", "-ac", "1", "-c:a", "flac", str(self.source)])
        if result.returncode != 0:
            self.skipTest("could not build the fixture")

    def _drive(self, dispatcher, steps):
        """Run the real mode impl with only the interactive steps stubbed."""
        calls = {"bounded": 0}
        real = modes_transform.run_bounded_audio_reverse

        def spy(*args, **kwargs):
            calls["bounded"] += 1
            return real(*args, **kwargs)

        with contextlib.ExitStack() as stack:
            for target, replacement in steps:
                stack.enter_context(mock.patch.object(
                    modes_transform, target, replacement))
            stack.enter_context(mock.patch.object(
                modes_transform, "run_bounded_audio_reverse", spy))
            stack.enter_context(mock.patch.object(
                modes_transform.wizard, "step_input_path",
                lambda a: None))
            stack.enter_context(mock.patch.object(
                modes_transform.wizard, "step_output_location",
                lambda a: None))
            stack.enter_context(mock.patch.object(
                FFmWiz.appio, "ask_yes_no", return_value=True))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            result = dispatcher({
                "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
                "input_path": self.source,
                "output_location": self._tmp,
                "audio_tool_output_ext": "flac",
            })
        return result, calls

    def test_the_standalone_speed_reverse_mode_routes_through_the_bounded_plan(self):
        probe = FFmWiz.ffprobe_full_json(FFPROBE, self.source)

        def track(answers):
            answers.update({
                "probe": probe, "format": probe.get("format", {}),
                "video_streams": [], "subtitle_streams": [],
                "audio_streams": [s for s in probe.get("streams", [])
                                  if s.get("codec_type") == "audio"],
                "audio_index": 0,
            })

        def options(answers):
            answers.update({"reverse_audio": True, "speed_factor": 1.0,
                            "_speed_reverse_noop": False})

        result, calls = self._drive(
            FFmWiz.run_audio_speed_reverse_mode,
            [("step_audio_track_for_tool", track),
             ("step_audio_speed_reverse_options", options)])
        self.assertIsNotNone(result)
        self.assertEqual(0, result[0])
        self.assertEqual(1, calls["bounded"],
                         "the mode must reach the bounded executor exactly once")

    def test_the_standalone_transform_mode_routes_through_the_bounded_plan(self):
        probe = FFmWiz.ffprobe_full_json(FFPROBE, self.source)

        def track(answers):
            answers.update({
                "probe": probe, "format": probe.get("format", {}),
                "video_streams": [], "subtitle_streams": [],
                "audio_streams": [s for s in probe.get("streams", [])
                                  if s.get("codec_type") == "audio"],
                "audio_index": 0,
            })

        def editor(answers):
            answers.update({"reverse_audio": True, "audio_speed_enabled": True,
                            "audio_speed_factor": 1.0,
                            "audio_cut_keep_ranges": [(0.0, 2.0)],
                            "_audio_transform_noop": False})

        result, calls = self._drive(
            FFmWiz.run_audio_transform_mode,
            [("step_audio_track_for_tool", track),
             ("step_audio_transform_editor", editor)])
        self.assertIsNotNone(result)
        self.assertEqual(0, result[0])
        self.assertEqual(1, calls["bounded"])


class TheJoinCarriesEveryPart(unittest.TestCase):
    """The concat DEMUXER silently drops FLAC parts; the filter does not.

    Every FLAC file carries its own STREAMINFO, and the demuxer applies the
    FIRST part's header to all of them. Parts recorded with a different
    blocksize then fail to decode --

        [flac @ ...] blocksize 1024 > 272
        [flac @ ...] decode_frame() failed

    -- and are dropped WITH EXIT CODE 0, under a container header still
    claiming the full length. Measured on ffmpeg 9.0.1, eight half-second parts
    holding 4.0000 s between them:

        -c copy (any flag combination)         0.4938s
        per-part `duration` in the list        0.4938s
        re-encode THROUGH the demuxer          0.4938s
        concat FILTER, one input per part      4.0000s

    0.4938 s at 44100 is 21776 samples, which is what CI reported for five
    commits while every stage guard called its own step healthy.

    This decodes the result instead of reading its duration, because the header
    is the thing that lied.
    """

    @staticmethod
    def _decoded_seconds(path, rate=RATE):
        run = subprocess.run(
            [FFMPEG, "-v", "error", "-i", str(path), "-map", "0:a:0",
             "-f", "s16le", "-ac", "1", "-ar", str(rate), "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
        return len(run.stdout) / 2 / float(rate)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_parts_with_different_blocksizes_all_survive_the_join(self):
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            parts = []
            # Different blocksizes on purpose: that is the divergence the
            # demuxer cannot represent, and a uniform set would prove nothing.
            for index, (freq, block) in enumerate(
                    ((440, 4096), (660, 1024), (880, 512), (1100, 256))):
                part = work / f"part{index}.mkv"
                _run([FFMPEG, "-v", "error", "-y", "-f", "lavfi",
                      "-i", f"sine=frequency={freq}:sample_rate={RATE}:duration=0.5",
                      "-c:a", "flac", "-blocksize", str(block), str(part)])
                parts.append(part)

            expected = sum(self._decoded_seconds(p) for p in parts)
            self.assertAlmostEqual(2.0, expected, delta=0.05,
                                   msg="the fixture parts are not half a second each")

            joined = work / "joined.mkv"
            cmd = FFmWiz.build_audio_concat_filter_command(FFMPEG, parts, joined)
            result = _run([str(part) for part in cmd])
            self.assertEqual(0, result.returncode, (result.stderr or "")[-400:])

            got = self._decoded_seconds(joined)
            self.assertAlmostEqual(
                expected, got, delta=0.05,
                msg=f"the join carried {got:.4f}s of the {expected:.4f}s it was "
                    "given; parts were dropped")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_the_builder_opens_each_part_as_its_own_input(self):
        # The shape is the fix: one `-i` per part. A single `-i` on a list file
        # is the demuxer, which is what dropped them.
        parts = [Path(f"p{i}.mkv") for i in range(4)]
        cmd = FFmWiz.build_audio_concat_filter_command("ffmpeg", parts, Path("out.mkv"))
        self.assertEqual(4, cmd.count("-i"), "each part needs its own input")
        self.assertIn("-filter_complex", cmd)
        self.assertNotIn("concat", [str(part) for part in cmd][:4],
                         "the demuxer must not be used for this")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_every_track_and_its_tags_are_carried(self):
        # A filter output has no source stream, so both of these are lost
        # unless the builder asks for them.
        parts = [Path(f"p{i}.mkv") for i in range(2)]
        cmd = [str(part) for part in
               FFmWiz.build_audio_concat_filter_command("ffmpeg", parts,
                                                        Path("out.mkv"), tracks=3)]
        graph = cmd[cmd.index("-filter_complex") + 1]
        for track in range(3):
            self.assertIn(f"[aout{track}]", graph, f"track {track} has no chain")
            self.assertIn(f"[0:a:{track}]", graph)
            self.assertIn(f"-map_metadata:s:a:{track}", cmd,
                          f"track {track} would lose its language and title")


if __name__ == "__main__":
    unittest.main()
