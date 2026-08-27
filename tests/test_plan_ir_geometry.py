"""Regression: an exported plan sizes memory from the frames that WILL exist (D07).

The staged plan's descriptor for an intermediate copied the SOURCE's width and
height. For a Join the forward stage owns crop, fps and resize, so the reverse
stage receives frames the exporter never looked at -- and the reverse budget is
computed from exactly those numbers. The automatic executor probes the
intermediate it has just written and is safe; the exported plan was sized
against a picture that does not exist.

Measured on this fixture before the repair: two 160x120 sources joined and
scaled to 1920x1080 at 60 fps, with a 640 MiB cap over 512 MiB of reserved
overhead:

    EXPORTED_SEGMENTS 1
    FIRST_EXPORTED_SEGMENT   -t 5.000000  (300 frames of 1920x1080)
    SAFE_POST_TRANSFORM      37 frames = 0.616666 s
    ESTIMATED_PEAK 1.500 GiB   CAP 0.625 GiB   EXCESS 2.40x

The oracle is not the descriptor against itself: the forward stage is EXECUTED
and its output probed, and the plan is judged against that real file. The cap
is shrunk rather than the picture enlarged so the arithmetic bites on a fixture
that costs a couple of seconds instead of a 4320p encode.
"""
import json
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
from ffmwiz.support import L00_split

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

SOURCE_SIZE = "160x120"
SOURCE_FPS = 30
TARGET = {"mode": "box", "width": 1920, "height": 1080}
TARGET_FPS = 60
CLIPS = (("red", 440, 2.0), ("blue", 880, 3.0))

# Small enough that a 1920x1080 frame is expensive against it and a 160x120
# frame is free, which is the whole difference the defect is about.
CAP_BYTES = 640 * 1024 ** 2
OVERHEAD_BYTES = 512 * 1024 ** 2


def _run(args, timeout=600):
    return subprocess.run([str(part) for part in args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace", timeout=timeout)


class ExportedPlanSizesPostTransformFrames(NoLeakedArtifacts, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not (FFMPEG and FFPROBE):
            raise unittest.SkipTest("ffmpeg/ffprobe not on PATH")
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_planir_"))
        cls.sources = []
        for name, (colour, tone, seconds) in zip("ab", CLIPS):
            path = cls._root / f"{name}.mkv"
            result = _run([
                FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i",
                f"color=c={colour}:s={SOURCE_SIZE}:r={SOURCE_FPS}:d={seconds}",
                "-f", "lavfi", "-i", f"sine=frequency={tone}:duration={seconds}",
                "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset",
                "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-t", str(seconds), path])
            if result.returncode != 0:
                raise unittest.SkipTest(f"could not build {name}: {result.stderr[-400:]}")
            cls.sources.append(path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="planir_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", self._real_note))
        for name, value in (("REVERSE_PEAK_BUDGET_BYTES", CAP_BYTES),
                            ("REVERSE_FIXED_OVERHEAD_BYTES", OVERHEAD_BYTES)):
            self.addCleanup(setattr, L00_split, name, getattr(L00_split, name))
            setattr(L00_split, name, value)

    # ---- fixtures --------------------------------------------------------
    def _probe(self, path, *args):
        return json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                *args, path]).stdout or "{}")

    def _item(self, path):
        info = self._probe(path, "-show_format", "-show_streams")
        return {"path": path, "probe": info, "format": info["format"],
                "streams": info["streams"],
                "video_streams": [s for s in info["streams"] if s["codec_type"] == "video"],
                "audio_streams": [s for s in info["streams"] if s["codec_type"] == "audio"],
                "subtitle_streams": [], "data_streams": [],
                "duration": float(info["format"]["duration"])}

    def _answers(self, out):
        items = [self._item(path) for path in self.sources]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": items[0]["path"],
            "probe": items[0]["probe"], "format": items[0]["format"],
            "output_location": out, "output_ext": "mkv",
            "video_streams": items[0]["video_streams"],
            "audio_streams": items[0]["audio_streams"],
            "subtitle_streams": [], "subtitle_tracks": [],
            "keep_source_subtitles": False, "audio_tracks": [0],
            "color_range_choice": "tv", "video_encoder": "libx264", "crf": 28,
            "preset": "ultrafast", "audio_codec": "aac",
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True,
            # Owned by the FORWARD stage, so the reverse stage reads frames
            # 216 times the area of the source at twice its rate.
            "resolution": dict(TARGET), "fps": TARGET_FPS,
            "join_input_items": items[1:],
        })
        answers["output_path"] = out / "result.mkv"
        FFmWiz.artifact_lease(answers)
        return answers

    def _plan(self):
        """The exported stages, plus the intermediate the first one WRITES."""
        workspace = self._tmp / "ws"
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            stages = encoding.bounded_reverse_plan(self._answers(self._tmp), workspace)
        self.assertTrue(stages, "the plan produced no stages")
        label, forward = stages[0]
        result = _run(forward)
        self.assertEqual(0, result.returncode,
                         f"the forward stage {label!r} failed: {result.stderr[-500:]}")
        joined = next(workspace.glob("joined_forward.*"))
        video = [s for s in self._probe(joined, "-show_streams")["streams"]
                 if s["codec_type"] == "video"][0]
        return stages, video

    @staticmethod
    def _reverse_windows(stages):
        return [float(cmd[cmd.index("-t") + 1]) for label, cmd in stages
                if label.startswith("Reverse segment") and "-t" in cmd]

    # ---- the defect ------------------------------------------------------
    def test_the_descriptor_matches_the_intermediate_the_stage_writes(self):
        """Every property the next stage reads, against the real file.

        Geometry, rate, pixel format and the clock, all of which the descriptor
        used to copy from the source: 160x120 at 30 fps for a file that is
        1920x1080 at 60, and a container start borrowed from a source whose
        audio leads its picture.
        """
        stages, probed = self._plan()
        seen = []
        real_stage = encoding.stage_answers
        encoding.stage_answers = lambda a, owns: (seen.append(a) or real_stage(a, owns))
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                encoding.bounded_reverse_plan(self._answers(self._tmp),
                                              self._tmp / "ws2")
        finally:
            encoding.stage_answers = real_stage
        described = [a for a in seen
                     if str(a.get("input_path", "")).endswith("joined_forward.mkv")]
        self.assertTrue(described, "the joined intermediate was never described")
        stream = (described[0].get("video_streams") or [{}])[0]

        self.assertEqual((int(probed["width"]), int(probed["height"])),
                         (int(stream.get("width") or 0), int(stream.get("height") or 0)),
                         "the descriptor reports the source geometry, not the "
                         "geometry the forward stage writes")
        self.assertAlmostEqual(
            FFmWiz.rational_to_float(probed.get("avg_frame_rate")) or 0.0,
            FFmWiz.rational_to_float(stream.get("avg_frame_rate")) or 0.0,
            places=3, msg="the descriptor reports the source frame rate")
        self.assertEqual(probed.get("pix_fmt"), stream.get("pix_fmt"),
                         "the descriptor reports the source pixel format")
        self.assertAlmostEqual(float(probed.get("start_time") or 0.0),
                               float(stream.get("start_time") or 0.0), places=3,
                               msg="the descriptor invents a start the file has not got")

    def test_every_exported_reverse_chunk_fits_the_cap(self):
        """The user-visible consequence: the advertised hard cap is real.

        Judged with the PROBED descriptor, because that is the picture the
        exported script will actually put in the reverse buffer.
        """
        stages, probed = self._plan()
        windows = self._reverse_windows(stages)
        self.assertTrue(windows, "no bounded reverse segment was planned")
        fps = FFmWiz.rational_to_float(probed.get("avg_frame_rate")) or float(TARGET_FPS)
        safe = FFmWiz.reverse_segment_plan(
            probed["width"], probed["height"], fps, probed.get("pix_fmt"),
            cap_bytes=CAP_BYTES, overhead_bytes=OVERHEAD_BYTES)
        for index, window in enumerate(windows, start=1):
            frames = math.ceil(window * fps - 1e-9)
            peak = OVERHEAD_BYTES + safe.bytes_per_frame * frames
            self.assertLessEqual(
                peak, CAP_BYTES,
                f"exported reverse segment {index}/{len(windows)} holds {frames} "
                f"frame(s) of {probed['width']}x{probed['height']} "
                f"{probed.get('pix_fmt')} = {peak / 1024 ** 3:.3f} GiB against a "
                f"{CAP_BYTES / 1024 ** 3:.3f} GiB cap "
                f"({peak / CAP_BYTES:.2f}x over); the safe window is "
                f"{safe.frames} frame(s) = {safe.seconds:.6f}s")


if __name__ == "__main__":
    unittest.main()
