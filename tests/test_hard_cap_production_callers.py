"""Regression: every production reverse caller reaches the FPS-aware cap (D08).

`split_ranges_for_reverse_segments` has a one-frame path that keeps a
sub-millisecond safe window from being rounded up to the rate-less 1 ms floor.
The helper's own unit test passed because it supplied the rate; the executors
did not, so the advertised hard cap was false on exactly the descriptors that
need it most. Measured for 15360x8640 at 1200 fps in `yuv444p12le`, against the
2 GiB cap:

    PLANNER_SAFE_UNIT   1 frame,  0.000833 s, 1.353 GiB
    PRODUCTION_NO_FPS   2 frames, 0.001000 s, 2.206 GiB   <-- over the cap
    FPS_AWARE_SPLIT     1 frame,  0.000833 s, 1.353 GiB

and at the caller, `run_segmented_reverse_main_encode` tiled a 1 s timeline
into 1000 chunks of exactly 1 ms rather than 1200 chunks of one frame.

A helper-level test is not enough -- that is the whole defect -- so this drives
the PUBLIC entry points with a mocked budget and asserts the ranges they hand
the chunker. The ffmpeg runner is mocked because the arithmetic, not the
encode, is what is under test; the segment commands are still built for real.

The repair is in the tiler rather than in each caller: a floor whose only job
is to keep the step above the command grid had no business raising a window the
caller had already measured. Fixing it once covers the callers that cannot pass
a rate as well as the one that does.
"""
import math
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts
from ffmwiz import encoding
from ffmwiz.support import ext04b
from ffmwiz import reverse_pipeline
from ffmwiz import reverse_stages
from ffmwiz import runtime
from ffmwiz.support import L00_split

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

# The descriptor from the measurement above. One frame of it is 0.853 GiB, so
# two frames cannot fit under the 2 GiB cap and the defect is arithmetic rather
# than a matter of degree.
WIDTH, HEIGHT, FPS, PIX_FMT = 15360, 8640, 1200.0, "yuv444p12le"
SOURCE_SECONDS = 0.1


def _run(args, timeout=600):
    return subprocess.run([str(part) for part in args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace", timeout=timeout)


class EveryReverseCallerHonoursTheCap(NoLeakedArtifacts, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not (FFMPEG and FFPROBE):
            raise unittest.SkipTest("ffmpeg/ffprobe not on PATH")
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_hardcap_"))
        cls.source = cls._root / "source.mkv"
        result = _run([
            FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=red:s=160x120:r=30:d={SOURCE_SECONDS}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={SOURCE_SECONDS}",
            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", str(SOURCE_SECONDS), cls.source])
        if result.returncode != 0:
            raise unittest.SkipTest(f"could not build the source: {result.stderr[-400:]}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="hardcap_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", self._real_note))
        self.budget = FFmWiz.reverse_segment_plan(WIDTH, HEIGHT, FPS, PIX_FMT)
        self.assertEqual(1, self.budget.frames,
                         "the fixture descriptor no longer resolves to one frame, so "
                         "the sub-millisecond window this defect lives in is gone")

    # ---- driving one entry point ----------------------------------------
    def _answers(self):
        probe = FFmWiz.services.ffprobe_json(FFPROBE, self.source)
        streams = probe.get("streams") or []
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.source,
            "probe": probe, "format": probe.get("format") or {},
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [], "subtitle_tracks": [],
            "keep_source_subtitles": False, "audio_tracks": [0],
            "output_location": self._tmp, "output_ext": "mkv",
            "color_range_choice": "tv", "video_encoder": "libx264", "crf": 28,
            "preset": "ultrafast", "audio_codec": "aac",
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "speed_factor": 1.0, "reverse_video": True, "reverse": True,
            "cmd": [FFMPEG, "-i", str(self.source), str(self._tmp / "result.mkv")],
        })
        answers["output_path"] = self._tmp / "result.mkv"
        FFmWiz.artifact_lease(answers)
        return answers

    def _ranges_from(self, drive):
        """Every (fps, chunks) pair `drive` hands the shared chunker.

        One namespace now, not two. The callers used to reach this helper
        differently -- the pipeline through the `encoding` facade, the
        standalone mode through its own module globals -- so a spy had to be
        installed in both, and a caller that found a third route would have
        been reported as clean. Every caller now goes through the module that
        DEFINES it, so patching that one module is what proves coverage.
        """
        calls = []
        real_split = FFmWiz.split_ranges_for_reverse_segments

        def spy(ranges, duration, segment_seconds=None, fps=None):
            chunks = real_split(ranges, duration, segment_seconds, fps)
            calls.append((fps, chunks))
            return chunks

        patched = [(L00_split, "split_ranges_for_reverse_segments"),
                   (reverse_stages, "reverse_segment_plan_for"),
                   (runtime, "run_ffmpeg_with_progress")]
        saved = [(module, name, getattr(module, name)) for module, name in patched]
        for module, name in patched[:1]:
            setattr(module, name, spy)
        setattr(reverse_stages, "reverse_segment_plan_for",
                lambda answers, best_effort=False: self.budget)
        for module, name in patched[2:]:
            setattr(module, name, lambda cmd, **kwargs: (0, 0.0))
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                drive()
        finally:
            for module, name, original in saved:
                setattr(module, name, original)
        self.assertTrue(calls, "this entry point never reached the shared chunker")
        return calls

    def _assert_within_cap(self, entry_point, calls):
        """The RANGES, not the argument list that produced them.

        Whether a caller hands over the rate is a mechanism; two of them cannot
        without breaking the doubles that stand in for this helper elsewhere,
        and the tiler is fixed so neither needs to. What every caller owes is
        the outcome: no chunk holds more decoded frames than the budget allows.
        """
        for _fps, chunks in calls:
            self.assertTrue(chunks, f"{entry_point} produced no reverse range")
            for index, (start, end) in enumerate(chunks, start=1):
                frames = math.ceil((end - start) * self.budget.fps - 1e-9)
                peak = (self.budget.overhead_bytes
                        + self.budget.bytes_per_frame * frames)
                self.assertLessEqual(
                    peak, self.budget.cap_bytes,
                    f"{entry_point} range {index}/{len(chunks)} spans "
                    f"{end - start:.6f}s = {frames} decoded frame(s) = "
                    f"{peak / 1024 ** 3:.3f} GiB against a "
                    f"{self.budget.cap_bytes / 1024 ** 3:.3f} GiB cap; the budget "
                    f"allows {self.budget.frames} frame(s) "
                    f"({self.budget.seconds:.6f}s)")

    # ---- the entry points ------------------------------------------------
    def test_the_segmented_main_encode_honours_the_cap(self):
        answers = self._answers()
        self._assert_within_cap(
            "run_segmented_reverse_main_encode",
            self._ranges_from(
                lambda: reverse_pipeline.run_segmented_reverse_main_encode(answers)))

    def test_the_bounded_reverse_pipeline_honours_the_cap(self):
        answers = self._answers()
        self._assert_within_cap(
            "run_bounded_reverse_pipeline",
            self._ranges_from(
                lambda: encoding.run_bounded_reverse_pipeline(answers)))

    def test_the_exported_planner_honours_the_cap(self):
        answers = self._answers()
        self._assert_within_cap(
            "bounded_reverse_plan",
            self._ranges_from(
                lambda: reverse_pipeline.bounded_reverse_plan(answers, self._tmp / "ws")))

    def test_the_standalone_reverse_honours_the_cap(self):
        answers = self._answers()
        self._assert_within_cap(
            "run_segmented_reverse_video_speed",
            self._ranges_from(
                lambda: encoding.run_segmented_reverse_video_speed(answers)))


class AnUnreadableRateIsNotQuietlyAssumed(unittest.TestCase):
    """A hard cap promised against a guessed frame rate is not a hard cap.

    `services.get_video_fps` defaults to 25.0. The reverse stage called it
    without a default, so a stream whose rate the probe cannot read was
    budgeted at 25 fps and still reported `hard_capped=True`. Measured on
    7680x4320 `yuv420p10le` with `avg_frame_rate=0/0`: a 560 ms window of 14
    frames, which at a real 120 fps decodes 68 frames = 7.749 GiB against the
    2 GiB cap -- 3.9x over, with nothing anywhere saying so.

    Refusing is the correct outcome. The planner already offers best-effort for
    a caller willing to accept an unbounded reverse; what it must never do is
    promise a bound it derived from a number nobody measured.
    """

    def _answers(self, rate):
        return {"video_streams": [{"width": 7680, "height": 4320,
                                   "pix_fmt": "yuv420p10le",
                                   "avg_frame_rate": rate, "r_frame_rate": rate}],
                "format": {"duration": "60.0"}}

    def test_an_unreadable_rate_refuses_instead_of_promising_a_cap(self):
        with self.assertRaises(FFmWiz.ReverseBudgetError) as caught:
            FFmWiz.reverse_segment_plan_for(self._answers("0/0"))
        self.assertIn("frame rate", str(caught.exception))

    def test_a_readable_rate_is_unaffected(self):
        # Guard the guard: a fix that refused everything would also pass the
        # test above.
        plan = FFmWiz.reverse_segment_plan_for(self._answers("30/1"))
        self.assertEqual(30.0, plan.fps)
        self.assertTrue(plan.hard_capped)

    def test_best_effort_still_plans_without_a_rate(self):
        # The escape hatch has to stay open, and it must say it is not capped.
        plan = FFmWiz.reverse_segment_plan_for(self._answers("0/0"),
                                               best_effort=True)
        self.assertFalse(plan.hard_capped)
        self.assertTrue(plan.assumptions, "a best-effort plan must say why")


if __name__ == "__main__":
    unittest.main()
