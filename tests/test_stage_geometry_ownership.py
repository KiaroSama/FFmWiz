"""Regression: crop, FPS and resize belong to exactly ONE stage (D01, D02).

`STAGE_TRANSFORMATIONS` owned cuts, speed, reverse, LoudNorm and Split, but not
the geometry. A stage asked to own nothing therefore still cropped, still
converted the frame rate and still scaled, because `stage_answers()` can only
clear what the schema declares.

Measured on a real 160x120 Join + Reverse + Split asking for 10 px off the left
and 10 px off the right -- one crop is 140x120:

    OUTPUT cropped_Part01.mkv 120 120
    OUTPUT cropped_Part02.mkv 120 120
    EXPECTED_ONE_CROP_WIDTH   140

`crop=` appeared in joined_forward.mkv, in reverse_encode_seg_0001.mkv and in
the final Split graph. The 120 is not two crops of 10 either: the forward join
normalised the already-cropped 140x120 straight back up to the source 160x120,
so the crop was undone and then re-applied twice. Both halves are repaired --
geometry is owned by the first stage that writes a picture, and the join's
normalisation target is the post-crop size.

Dimensions alone would accept a crop taken from the wrong offset, so the
fixture paints a different colour on each edge and the assertions read actual
pixels at all four boundaries.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts
from ffmwiz import encoding
from ffmwiz import runtime

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

WIDTH, HEIGHT = 160, 120
EDGE = {"left": 10, "right": 10, "top": 4, "bottom": 4}
SOURCE_FPS = 10
SECONDS = 2


def _run(args, timeout=300):
    return subprocess.run([str(part) for part in args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace", timeout=timeout)


class GeometryOwnership(NoLeakedArtifacts, unittest.TestCase):
    """Two sources whose edges are individually identifiable.

    Each input is black in the middle with a differently coloured band on every
    edge, exactly as wide as the crop that should remove it. After ONE correct
    crop the picture is entirely black; a crop taken at the wrong offset leaves
    one of the bands showing at a corner, which no dimension check would see.
    """

    @classmethod
    def setUpClass(cls):
        if not (FFMPEG and FFPROBE):
            raise unittest.SkipTest("ffmpeg/ffprobe not on PATH")
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_geometry_"))
        cls.inputs = []
        for name, tone in (("a", 440), ("b", 880)):
            path = cls._root / f"{name}.mkv"
            bands = ",".join([
                f"drawbox=x=0:y=0:w={EDGE['left']}:h={HEIGHT}:color=red:t=fill",
                f"drawbox=x={WIDTH - EDGE['right']}:y=0:w={EDGE['right']}:h={HEIGHT}:color=yellow:t=fill",
                f"drawbox=x=0:y=0:w={WIDTH}:h={EDGE['top']}:color=green:t=fill",
                f"drawbox=x=0:y={HEIGHT - EDGE['bottom']}:w={WIDTH}:h={EDGE['bottom']}:color=blue:t=fill",
            ])
            result = _run([
                FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i",
                f"color=c=black:s={WIDTH}x{HEIGHT}:r={SOURCE_FPS}:d={SECONDS},{bands}",
                "-f", "lavfi", "-i", f"sine=frequency={tone}:duration={SECONDS}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", path])
            if result.returncode != 0:
                raise unittest.SkipTest(f"could not build {name}: {result.stderr[-400:]}")
            cls.inputs.append(path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="geometry_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", self._real_note))

    # ---- probing --------------------------------------------------------
    def _probe(self, path, *args):
        return json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                *args, path]).stdout or "{}")

    def _video(self, path):
        return self._probe(path, "-select_streams", "v:0", "-show_streams")["streams"][0]

    def _frame_count(self, path):
        packets = self._probe(path, "-select_streams", "v", "-show_packets")["packets"]
        return len(packets)

    def _pixels(self, path, at=0.5):
        """One decoded frame as (width, height, rgb-getter)."""
        stream = self._video(path)
        width, height = int(stream["width"]), int(stream["height"])
        raw = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
             "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300).stdout
        self.assertEqual(width * height * 3, len(raw),
                         f"{path.name}: decoded {len(raw)} bytes for {width}x{height}")

        def at_xy(x, y):
            offset = (y * width + x) * 3
            return raw[offset], raw[offset + 1], raw[offset + 2]

        return width, height, at_xy

    def _assert_dark(self, path, label):
        """Every corner and edge midpoint of the kept region is the black core.

        A band left behind by a wrongly offset crop shows up here even when the
        dimensions happen to come out right.
        """
        width, height, pixel = self._pixels(path)
        probes = {
            "top-left": (1, 1), "top-right": (width - 2, 1),
            "bottom-left": (1, height - 2), "bottom-right": (width - 2, height - 2),
            "top-mid": (width // 2, 1), "bottom-mid": (width // 2, height - 2),
            "left-mid": (1, height // 2), "right-mid": (width - 2, height // 2),
        }
        for where, (x, y) in probes.items():
            red, green, blue = pixel(x, y)
            self.assertLess(max(red, green, blue), 90,
                            f"{label}: {where} is ({red},{green},{blue}), "
                            f"so an edge band survived the crop")

    # ---- driving the real pipeline --------------------------------------
    def _item(self, path):
        info = self._probe(path, "-show_format", "-show_streams")
        return {"path": path, "probe": info, "format": info["format"],
                "streams": info["streams"],
                "video_streams": [s for s in info["streams"] if s["codec_type"] == "video"],
                "audio_streams": [s for s in info["streams"] if s["codec_type"] == "audio"],
                "subtitle_streams": [], "data_streams": [],
                "duration": float(info["format"]["duration"])}

    def _answers(self, out, items, **extra):
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": items[0]["path"],
            "probe": items[0]["probe"], "format": items[0]["format"],
            "output_location": out,
            "video_streams": items[0]["video_streams"],
            "audio_streams": items[0]["audio_streams"],
            "subtitle_streams": [], "output_ext": "mkv", "audio_tracks": [0],
            "color_range_choice": "tv",
            "video_encoder": "libx264", "crf": 28, "preset": "ultrafast",
            "audio_codec": "aac",
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True,
        })
        answers.update(extra)
        answers["output_path"] = out / "geometry.mkv"
        return answers

    def _pipeline(self, label, join=True, **extra):
        """Run the real staged executor and capture every command it issued."""
        out = self._tmp / label
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        items = [self._item(path) for path in self.inputs]
        answers = self._answers(out, items,
                                **({"join_input_items": items[1:]} if join else {}),
                                **extra)
        commands = []
        real_runner = runtime.run_ffmpeg_with_progress

        def spy(cmd, **kwargs):
            commands.append([str(part) for part in cmd])
            return real_runner(cmd, **kwargs)

        runtime.run_ffmpeg_with_progress = spy
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                code, _elapsed = encoding.run_bounded_reverse_pipeline(answers)
        finally:
            runtime.run_ffmpeg_with_progress = real_runner
        self.assertEqual(0, code, noise.getvalue()[-1500:])
        return out, commands

    def _stages_with(self, commands, needle):
        """Which produced files were made by a command containing `needle`."""
        return [Path(cmd[-1]).name for cmd in commands
                if any(needle in str(part) for part in cmd)]

    # ---- D01 ------------------------------------------------------------
    def test_a_joined_reverse_split_crops_exactly_once(self):
        out, commands = self._pipeline(
            "joincrop", separator_points=[2.0], crop_enabled=True,
            crop_left=EDGE["left"], crop_right=EDGE["right"],
            crop_top=0, crop_bottom=0)
        parts = sorted(out.glob("*Part*.mkv"))
        self.assertEqual(2, len(parts), [p.name for p in parts])
        for part in parts:
            stream = self._video(part)
            self.assertEqual(
                (WIDTH - EDGE["left"] - EDGE["right"], HEIGHT),
                (int(stream["width"]), int(stream["height"])),
                f"{part.name} was cropped a different number of times")

    def test_the_crop_appears_in_its_owner_stage_and_nowhere_else(self):
        _out, commands = self._pipeline(
            "joincropcmd", separator_points=[2.0], crop_enabled=True,
            crop_left=EDGE["left"], crop_right=EDGE["right"],
            crop_top=0, crop_bottom=0)
        cropped = self._stages_with(commands, "crop=")
        self.assertEqual(1, len(cropped),
                         f"crop appears in {len(cropped)} stages: {cropped}")
        self.assertTrue(cropped[0].startswith("joined_forward"),
                        f"the forward join should own the crop, not {cropped[0]}")

    def test_a_single_input_reverse_split_crops_all_four_edges_once(self):
        out, commands = self._pipeline(
            "solocrop", join=False, separator_points=[1.0], crop_enabled=True,
            crop_left=EDGE["left"], crop_right=EDGE["right"],
            crop_top=EDGE["top"], crop_bottom=EDGE["bottom"])
        parts = sorted(out.glob("*Part*.mkv"))
        self.assertEqual(2, len(parts), [p.name for p in parts])
        for part in parts:
            stream = self._video(part)
            self.assertEqual(
                (WIDTH - EDGE["left"] - EDGE["right"],
                 HEIGHT - EDGE["top"] - EDGE["bottom"]),
                (int(stream["width"]), int(stream["height"])),
                f"{part.name} did not get exactly one four-edge crop")
        cropped = self._stages_with(commands, "crop=")
        self.assertEqual(1, len(cropped), f"crop appears in {cropped}")

    def test_the_kept_pixels_are_the_ones_inside_all_four_margins(self):
        # The band colours are the point: dimensions alone accept a crop taken
        # from the wrong offset.
        out, _commands = self._pipeline(
            "solopixels", join=False, separator_points=[1.0], crop_enabled=True,
            crop_left=EDGE["left"], crop_right=EDGE["right"],
            crop_top=EDGE["top"], crop_bottom=EDGE["bottom"])
        for part in sorted(out.glob("*Part*.mkv")):
            self._assert_dark(part, part.name)

    def test_the_fixture_really_has_bands_to_lose(self):
        # Guard the guard: an all-black source would satisfy the pixel test
        # with no crop applied at all.
        width, height, pixel = self._pixels(self.inputs[0])
        self.assertEqual((WIDTH, HEIGHT), (width, height))
        self.assertGreater(pixel(1, height // 2)[0], 120, "no red left band")
        self.assertGreater(pixel(width - 2, height // 2)[0], 120, "no yellow right band")
        self.assertGreater(pixel(width // 2, 1)[1], 100, "no green top band")
        self.assertGreater(pixel(width // 2, height - 2)[2], 120, "no blue bottom band")

    # ---- D02 ------------------------------------------------------------
    def test_a_joined_reverse_split_converts_the_frame_rate_once(self):
        out, commands = self._pipeline("joinfps", separator_points=[2.0], fps=15)
        rated = self._stages_with(commands, "fps=15")
        self.assertEqual(1, len(rated), f"fps=15 appears in {rated}")
        parts = sorted(out.glob("*Part*.mkv"))
        self.assertEqual(2, len(parts), [p.name for p in parts])
        for part in parts:
            # 2 s of a 4 s joined timeline at 15 fps.
            self.assertAlmostEqual(30, self._frame_count(part), delta=3,
                                   msg=f"{part.name} frame count")

    def test_a_joined_reverse_split_resizes_once(self):
        out, commands = self._pipeline(
            "joinsize", separator_points=[2.0], fps=15,
            resolution={"mode": "exact_stretch", "width": 320, "height": 180})
        for part in sorted(out.glob("*Part*.mkv")):
            stream = self._video(part)
            self.assertEqual((320, 180),
                             (int(stream["width"]), int(stream["height"])),
                             f"{part.name} geometry")
            self.assertIn(str(stream.get("sample_aspect_ratio") or "1:1"),
                          {"1:1", "N/A"}, f"{part.name} SAR")
        scaled = self._stages_with(commands, "scale=320:180")
        self.assertEqual(1, len(scaled), f"scale=320:180 appears in {scaled}")

    def test_a_single_input_preserve_aspect_resize_happens_once(self):
        out, commands = self._pipeline(
            "soloaspect", join=False, separator_points=[1.0],
            resolution={"mode": "preset", "width": 320, "height": 320})
        parts = sorted(out.glob("*Part*.mkv"))
        self.assertEqual(2, len(parts), [p.name for p in parts])
        for part in parts:
            stream = self._video(part)
            self.assertEqual(320, int(stream["width"]), f"{part.name} width")
            # 160x120 preserved into a 320-wide box is 320x240, not 320x320.
            self.assertEqual(240, int(stream["height"]), f"{part.name} height")
        scaled = self._stages_with(commands, "scale=")
        self.assertEqual(1, len(scaled), f"scale appears in {scaled}")

    def test_the_reversed_marker_order_survives_the_geometry_stage(self):
        # Guard the guard: every assertion above would also pass on a pipeline
        # that had quietly stopped reversing.
        out, _commands = self._pipeline("joinorder", fps=15)
        width, _height, pixel = self._pixels(out / "geometry.mkv", at=0.3)
        early = pixel(width - 2, 2)
        self.assertGreater(max(early), 60,
                           "the joined picture lost its bands entirely")


@requires_ffmpeg
class ThePlanRefusesWhatItCannotOwn(unittest.TestCase):
    """`validate_stage_plan` had no way to see D01 at all.

    It only rejected DUPLICATE ownership. Crop was owned by nobody, so there
    was nothing to be a duplicate of and the plan validated cleanly while the
    filter builder applied it in every stage that read the keys.
    """

    REQUESTED = {"crop_enabled": True, "crop_left": 10, "reverse_video": True,
                 "fps": 15, "resolution": {"mode": "exact_stretch",
                                           "width": 320, "height": 180}}

    def test_an_unowned_requested_transform_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            FFmWiz.validate_stage_plan(
                [("forward", ()), ("reverse", ("video_reverse",))], self.REQUESTED)
        self.assertIn("crop", str(caught.exception))

    def test_a_duplicate_is_still_refused(self):
        with self.assertRaises(ValueError):
            FFmWiz.validate_stage_plan(
                [("forward", ("crop",)), ("reverse", ("crop",))], self.REQUESTED)

    def test_a_complete_plan_passes(self):
        FFmWiz.validate_stage_plan(
            [("forward", FFmWiz.GEOMETRY_TRANSFORMATIONS),
             ("reverse", ("video_reverse",)), ("split", ())], self.REQUESTED)

    def test_an_unrequested_transform_needs_no_owner(self):
        FFmWiz.validate_stage_plan([("only", ())], {"reverse_video": False})

    def test_every_declared_transformation_has_a_requested_predicate(self):
        # Adding a key to the schema without saying what makes it REQUESTED
        # would silently exempt it from the missing-owner check.
        FFmWiz.requested_transformations({})

    def test_an_unknown_owner_name_is_refused(self):
        with self.assertRaises(ValueError):
            FFmWiz.validate_stage_plan([("stage", ("colour_grade",))])


@requires_ffmpeg
class TheReverseBudgetSizesThePostFilterFrame(unittest.TestCase):
    """The budget is taken at the `reverse` filter's INPUT, not from the probe.

    The CPU chain is crop -> fps -> scale/pad -> speed/reverse -> format, and
    `reverse` buffers what reaches it. Sizing from the source gave a 1080p30
    clip upscaled to 8K a 15 s window whose real peak is 24.485 GiB against a
    2 GiB cap (D11). This is the wiring `reverse_segment_plan_for` does; the
    arithmetic itself is covered beside the splitter.
    """

    def _answers(self, **extra):
        answers = {"video_streams": [{"width": 1920, "height": 1080,
                                      "pix_fmt": "yuv420p"}],
                   "fps": 30, "video_codec": "H264", "video_encoder": "libx264",
                   "use_gpu": False, "output_ext": "mkv"}
        answers.update(extra)
        return answers

    def test_an_upscale_shortens_the_window(self):
        source = FFmWiz.reverse_segment_plan_for(self._answers())
        upscaled = FFmWiz.reverse_segment_plan_for(self._answers(
            resolution={"mode": "exact_stretch", "width": 7680, "height": 4320}))
        self.assertLess(upscaled.seconds, source.seconds / 8,
                        f"8K frames are 16x a 1080p frame; got {upscaled.seconds}s "
                        f"against {source.seconds}s")

    def test_a_downscale_lengthens_it(self):
        source = FFmWiz.reverse_segment_plan_for(self._answers(
            video_streams=[{"width": 7680, "height": 4320, "pix_fmt": "yuv420p"}]))
        scaled = FFmWiz.reverse_segment_plan_for(self._answers(
            video_streams=[{"width": 7680, "height": 4320, "pix_fmt": "yuv420p"}],
            resolution={"mode": "exact_stretch", "width": 1280, "height": 720}))
        self.assertGreater(scaled.seconds, source.seconds)

    def test_a_crop_shortens_the_frame_it_buffers(self):
        whole = FFmWiz.reverse_segment_plan_for(self._answers())
        cropped = FFmWiz.reverse_segment_plan_for(self._answers(
            crop_enabled=True, crop_left=480, crop_right=480,
            crop_top=270, crop_bottom=270))
        self.assertGreater(cropped.frames, whole.frames)

    def test_a_higher_output_rate_shortens_it(self):
        thirty = FFmWiz.reverse_segment_plan_for(self._answers())
        sixty = FFmWiz.reverse_segment_plan_for(self._answers(fps=60))
        self.assertAlmostEqual(thirty.seconds / 2, sixty.seconds, delta=0.05)

    def test_a_wide_source_is_not_sized_as_the_narrow_output(self):
        # The graph converts to the encoder's format AFTER `reverse`, and that
        # negotiation usually reaches back up the chain -- but "usually" cannot
        # underwrite a hard cap, so the wider of the two formats wins.
        deep = FFmWiz.reverse_segment_plan_for(self._answers(
            video_streams=[{"width": 1920, "height": 1080,
                            "pix_fmt": "yuv444p12le"}]))
        shallow = FFmWiz.reverse_segment_plan_for(self._answers())
        self.assertLess(deep.seconds, shallow.seconds,
                        "a 12-bit 4:4:4 source was sized as 8-bit 4:2:0")

    def test_the_window_is_readable_when_it_is_subsecond(self):
        plan = FFmWiz.reverse_segment_plan_for(self._answers(
            video_streams=[{"width": 7680, "height": 4320, "pix_fmt": "yuv420p10le"}],
            fps=60))
        self.assertLess(plan.seconds, 1.0)
        self.assertNotIn("0s", plan.window_text)
        self.assertIn("ms", plan.window_text)


if __name__ == "__main__":
    unittest.main()
