"""Regression: standalone reverse must plan and PRINT the shared budget (D15/D08).

Two defects on one line of `run_segmented_reverse_video_speed`, both measured
against `reverse_segment_plan()` on the frozen tree:

    7680x4320   60 fps yuv420p10le   0.233333 s   printed as "0s"
    15360x8640 120 fps yuv444p12le   0.008333 s   printed as "0s"

`f"{segment_seconds:.0f}s"` renders every legitimate subsecond window as
nothing, so the notice that exists to explain the safety plan tells the user
the plan is zero seconds long. The main executor already prints the shared
`budget.window_text` -- milliseconds plus the frame count -- while standalone
reverse kept a second calculation and a second formatter (D15).

The same call dropped the frame RATE on the way to the splitter, and without a
rate the splitter's 1 ms floor is coarser than one frame above 1000 fps:

    15360x8640 1200 fps yuv444p12le
        planner    1 frame   0.000833 s   1.353 GiB
        no fps     2 frames  0.001000 s   2.206 GiB   <-- the cap is 2.000 GiB
        fps-aware  1 frame   0.000833 s   1.353 GiB

So the advertised hard cap was false on this path by 206 MiB, on the smallest
chunk the plan can make (D08, third production caller).

Nothing here mocks the planner: the answers describe the extreme source and the
real shared budget does the arithmetic. Only the ffmpeg runner is replaced, so
every assertion is about the ranges and the notice the executor really produced.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import FFmWiz
from ffmwiz.support import ext04b
from ffmwiz import runtime
from ffmwiz.support import L00_split

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _stream(width, height, fps, pix_fmt):
    return {"codec_type": "video", "width": width, "height": height,
            "pix_fmt": pix_fmt, "avg_frame_rate": f"{fps}/1",
            "r_frame_rate": f"{fps}/1"}


class TheStandaloneReverseUsesTheSharedBudget(unittest.TestCase):
    """Drive the real executor; replace only the process it would spawn."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_stdrev_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _answers(self, width, height, fps, pix_fmt, duration):
        return {
            "ffmpeg": FFMPEG or "ffmpeg", "ffprobe": FFPROBE or "ffprobe",
            "input_path": self._tmp / "in.mkv",
            "output_path": self._tmp / "out.mkv",
            "output_ext": "mkv", "output_location": self._tmp,
            "video_streams": [_stream(width, height, fps, pix_fmt)],
            "audio_streams": [], "subtitle_streams": [],
            "format": {"duration": str(duration)},
            "reverse_video": True, "speed_factor": 1.0, "include_audio": False,
            "video_codec": "H264", "use_gpu": False, "resolution": "n",
        }

    def _drive(self, answers):
        """Returns (notice lines, the ranges the executor asked ffmpeg for)."""
        notes: list[str] = []
        ranges: list[tuple[float, float]] = []

        def fake_runner(cmd, **_kwargs):
            argv = [str(part) for part in cmd]
            if "-ss" in argv and "-t" in argv:
                start = float(argv[argv.index("-ss") + 1])
                ranges.append((start, start + float(argv[argv.index("-t") + 1])))
            return 0, 0.0

        # `runtime` DEFINES it. ext04b used to hold its own star-imported copy,
        # so a double installed there reached that one caller and no other.
        with mock.patch.object(runtime, "run_ffmpeg_with_progress", fake_runner), \
                mock.patch.object(FFmWiz.appio, "note",
                                  lambda text: notes.append(str(text))):
            code, _elapsed = ext04b.run_segmented_reverse_video_speed(answers)
        self.assertEqual(0, code)
        self.assertTrue(ranges, "the executor produced no reverse segment at all")
        return notes, ranges

    def _window_notice(self, notes):
        return next(line for line in notes if "segment(s) of up to" in line)

    # ---- D15: the window has to be readable ----
    def test_a_subsecond_window_is_not_announced_as_zero_seconds(self):
        plan = FFmWiz.reverse_segment_plan(7680, 4320, 60, "yuv420p10le")
        self.assertLess(plan.seconds, 1.0,
                        "the fixture must produce a subsecond window")
        self.assertEqual("0s", f"{plan.seconds:.0f}s",
                         "this is what the discarded formatter printed")

        notes, _ranges = self._drive(self._answers(7680, 4320, 60, "yuv420p10le", 2.0))
        notice = self._window_notice(notes)
        self.assertNotIn("up to 0s", notice)
        self.assertIn("233 ms (14 frames)", notice,
                      "the notice must carry the shared window text")
        self.assertEqual("233 ms (14 frames)", plan.window_text)

    def test_a_one_frame_window_says_one_frame(self):
        notes, _ranges = self._drive(
            self._answers(15360, 8640, 120, "yuv444p12le", 0.5))
        self.assertIn("8 ms (1 frame)", self._window_notice(notes))

    def test_an_ordinary_window_still_reads_in_seconds(self):
        notes, _ranges = self._drive(self._answers(320, 240, 30, "yuv420p", 5.0))
        self.assertRegex(self._window_notice(notes),
                         r"up to \d+\.\d{3} s \(\d+ frames\)")

    def test_an_unreadable_descriptor_is_refused_rather_than_assumed(self):
        # The shared plan refuses what it cannot size; a warning cannot turn an
        # unbounded allocation into a bound.
        with self.assertRaises(FFmWiz.ReverseBudgetError):
            self._drive(self._answers(1920, 1080, 30, "some_future_format", 2.0))

    # ---- D08: the splitter has to cut on the frame grid ----
    def test_every_produced_range_stays_inside_the_peak_cap(self):
        for width, height, fps, pix_fmt in (
            (15360, 8640, 1200, "yuv444p12le"),   # the measured overrun
            (15360, 8640, 120, "yuv444p12le"),
            (7680, 4320, 60, "yuv420p10le"),
            (3840, 2160, 30, "yuv420p"),
        ):
            with self.subTest(source=f"{width}x{height}@{fps}"):
                plan = FFmWiz.reverse_segment_plan(width, height, fps, pix_fmt)
                _notes, ranges = self._drive(
                    self._answers(width, height, fps, pix_fmt, 10.0 / fps))
                for start, end in ranges:
                    frames = math.ceil((end - start) * fps - 1e-9)
                    peak = plan.overhead_bytes + plan.bytes_per_frame * frames
                    self.assertLessEqual(
                        peak, plan.cap_bytes,
                        f"{start:.6f}->{end:.6f} decodes {frames} frame(s) = "
                        f"{peak / 1024 ** 3:.3f} GiB against a "
                        f"{plan.cap_bytes / 1024 ** 3:.3f} GiB cap")
                    self.assertLessEqual(frames, plan.frames)

    def test_the_extreme_rate_gets_one_frame_and_not_the_millisecond_floor(self):
        # The exact arithmetic the audit measured, at the EXECUTOR rather than
        # at the helper: the helper-level test passes because it supplies fps.
        plan = FFmWiz.reverse_segment_plan(15360, 8640, 1200, "yuv444p12le")
        self.assertEqual(1, plan.frames)
        # This used to be the negative control: the rate-less splitter had a
        # flat 1 ms floor and RAISED a window the caller had already measured,
        # so 0.000833 s came back as 0.001 s = 2 frames = 2.206 GiB against a
        # 2 GiB cap. The floor is now the command grid, so it no longer inflates
        # a measured window and the two paths agree (D08).
        floored = FFmWiz.split_ranges_for_reverse_segments([], 0.01, plan.seconds)
        self.assertEqual(
            1, math.ceil((floored[0][1] - floored[0][0]) * 1200 - 1e-9),
            "the splitter must not widen a window the caller measured")
        self.assertAlmostEqual(plan.seconds, floored[0][1] - floored[0][0], places=6)

        _notes, ranges = self._drive(
            self._answers(15360, 8640, 1200, "yuv444p12le", 0.01))
        for start, end in ranges:
            self.assertEqual(1, math.ceil((end - start) * 1200 - 1e-9))


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")
class TheNoticeMatchesTheMediaItPlans(unittest.TestCase):
    """The same notice, on a real encode that really is cut into segments."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_stdrev_media_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._src = self._tmp / "in.mkv"
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-v", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=1",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             str(self._src)], capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=300)
        if result.returncode != 0:
            self.skipTest("could not build the fixture: " + (result.stderr or "")[-300:])
        self._probe = FFmWiz.ffprobe_full_json(FFPROBE, self._src)

    def test_a_real_segmented_reverse_announces_the_window_it_used(self):
        streams = self._probe.get("streams", [])
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self._src,
            "output_path": self._tmp / "out.mkv", "output_ext": "mkv",
            "output_location": self._tmp,
            "video_streams": [s for s in streams if s.get("codec_type") == "video"],
            "audio_streams": [], "subtitle_streams": [],
            "format": self._probe.get("format", {}),
            "reverse_video": True, "speed_factor": 1.0, "include_audio": False,
            "video_codec": "H264", "use_gpu": False, "resolution": "n",
        }
        # Six frames per segment: small enough that the one-second clip is
        # really cut up, large enough that the run stays cheap.
        reference = FFmWiz.reverse_segment_plan(320, 240, 30, "yuv420p")
        six_frames = int(reference.overhead_bytes + reference.bytes_per_frame * 6)
        notes: list[str] = []
        with mock.patch.object(FFmWiz.appio, "note",
                               lambda text: notes.append(str(text))), \
                mock.patch.object(L00_split, "REVERSE_PEAK_BUDGET_BYTES", six_frames):
            code, _elapsed = ext04b.run_segmented_reverse_video_speed(answers)

        self.assertEqual(0, code, "the segmented reverse itself has to succeed")
        notice = next(line for line in notes if "segment(s) of up to" in line)
        self.assertIn("200 ms (6 frames)", notice)
        self.assertIn("5 segment(s)", notice)
        output = Path(answers["output_path"])
        self.assertTrue(output.exists())
        probe = FFmWiz.ffprobe_full_json(FFPROBE, output)
        self.assertAlmostEqual(
            1.0, float(probe.get("format", {}).get("duration") or 0.0), delta=0.15,
            msg="the concatenated reverse must still be the source's length")


if __name__ == "__main__":
    unittest.main()
