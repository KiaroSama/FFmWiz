"""Compositing proved in pixels and in tones, not in the command string.

Split out of `test_composite_inputs` for file size. That suite reads the argv;
this one drives real ffmpeg and measures the OUTPUT: a logo must appear at the
corner that was asked for and at none of the others, the left half of a side-by-side
must be input 1, and a mix must carry both tones with the second measurably under
the first. Shared fixtures live in `composite_test_helpers`.
"""
import json
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts
from composite_test_helpers import (
    FFMPEG, FFPROBE, MAIN_TONE, BED_TONE, CONTROL_TONE, _run)


class TheCompositeReallyComposites(NoLeakedArtifacts, unittest.TestCase):
    """Real encodes: the picture and the sound are the contract."""

    @classmethod
    def setUpClass(cls):
        if not (FFMPEG and FFPROBE):
            raise unittest.SkipTest("ffmpeg/ffprobe not on PATH")
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_composite_"))
        cls.main = cls._root / "main.mkv"
        cls.silent = cls._root / "silent.mkv"
        cls.partner = cls._root / "partner.mkv"
        cls.logo = cls._root / "logo.png"
        cls.bed = cls._root / "bed.flac"
        cls.long_bed = cls._root / "long_bed.flac"
        cls.short_bed = cls._root / "short_bed.flac"
        builds = [
            # Solid green with a 440 Hz tone at half scale, so the mix below
            # cannot clip and distort the very ratio it is measuring.
            ([  "-f", "lavfi", "-i", "color=c=green:s=320x180:r=15:d=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2,volume=0.5",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "flac"], cls.main),
            ([  "-f", "lavfi", "-i", "color=c=green:s=320x180:r=15:d=2",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"],
             cls.silent),
            ([  "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=15:d=2",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"],
             cls.partner),
            ([  "-f", "lavfi", "-i", "color=c=red:s=40x40:d=1", "-frames:v", "1"],
             cls.logo),
            ([  "-f", "lavfi", "-i", "sine=frequency=880:duration=2,volume=0.5",
                "-c:a", "flac"], cls.bed),
            # Five seconds against a two second main input: the file that used
            # to truncate the output when `-shortest` was on the command.
            ([  "-f", "lavfi", "-i", "sine=frequency=880:duration=5,volume=0.5",
                "-c:a", "flac"], cls.long_bed),
            # And the other way round: the sting that ends long before the film
            # does. `-shortest` would end the whole output with it.
            ([  "-f", "lavfi", "-i", "sine=frequency=880:duration=0.8,volume=0.5",
                "-c:a", "flac"], cls.short_bed),
        ]
        for args, target in builds:
            result = _run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
                           "-y", *args, target])
            if result.returncode != 0:
                raise unittest.SkipTest(
                    f"could not build {target.name}: {result.stderr[-300:]}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="composite_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", real_note))

    # ---- building and running ---------------------------------------------
    def _encode(self, label, source=None, **extra):
        source = source or self.main
        info = json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                "-show_format", "-show_streams", source]).stdout)
        streams = info["streams"]
        out = self._tmp / label
        out.mkdir()
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": source,
            "probe": info, "format": info["format"], "streams": streams,
            "output_location": out, "output_ext": "mkv",
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [], "data_streams": [],
            "color_range_choice": "tv", "video_codec": "H264",
            "video_crf": 28, "audio_codec": "flac", "use_gpu": False,
        })
        answers.update(extra)
        cmd = FFmWiz.build_composite_command(answers, out / f"{label}.mkv")
        result = _run([str(part) for part in cmd], timeout=600)
        self.assertEqual(0, result.returncode, result.stderr[-900:])
        return Path(answers["output_path"])

    # ---- measuring the produced media -------------------------------------
    def _geometry(self, path):
        stream = json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                  "-select_streams", "v:0", "-show_streams",
                                  path]).stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])

    def _duration(self, path):
        return float(json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                      "-show_format", path]).stdout)["format"]["duration"])

    def _colour_at(self, path, x, y):
        """The name of the colour in an 8x8 box at ABSOLUTE (x, y).

        Absolute rather than relative because a corner is an absolute place:
        a margin of 10 px is 10 px whatever the picture is scaled to, and a
        relative sampler would drift off the logo as soon as the geometry
        changed. `crop` then `scale=1:1` because the scaler blends whole
        regions -- a red box beside a green field averages to something that
        is neither, which is how a corner test passes without a corner.
        """
        raw = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
             "-i", str(path), "-frames:v", "1",
             "-vf", f"crop=8:8:{x - 4}:{y - 4},scale=1:1",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300).stdout
        self.assertEqual(3, len(raw), f"no pixel sampled at ({x}, {y})")
        red, green, blue = raw[0], raw[1], raw[2]
        return {red: "red", green: "green", blue: "blue"}[max(red, green, blue)]

    def _tone_power(self, path, tone, seconds=1.0, at=0.0):
        """Goertzel power in one bin over `seconds` of PCM starting at `at`."""
        raw = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
             "-ss", f"{at:.2f}", "-i", str(path), "-t", f"{seconds:.2f}",
             "-map", "0:a:0",
             "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "8000", "-ac", "1", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300).stdout
        samples = [int.from_bytes(raw[i:i + 2], "little", signed=True)
                   for i in range(0, len(raw) - 1, 2)]
        self.assertGreater(len(samples), 2048, "not enough audio was decoded")
        omega = 2 * math.pi * tone / 8000
        coeff = 2 * math.cos(omega)
        s1 = s2 = 0.0
        for sample in samples:
            s0 = sample + coeff * s1 - s2
            s2, s1 = s1, s0
        return s1 * s1 + s2 * s2 - coeff * s1 * s2

    # ---- the fixtures themselves ------------------------------------------
    def test_the_fixtures_are_what_the_tests_assume(self):
        # Guard the guard. A green logo on a green field, or a main input that
        # already carried the bed's tone, would make everything below pass.
        self.assertEqual("green", self._colour_at(self.main, 160, 90))
        self.assertEqual("blue", self._colour_at(self.partner, 160, 90))
        self.assertEqual("red", self._colour_at(self.logo, 20, 20))
        self.assertEqual((320, 180), self._geometry(self.main))
        self.assertGreater(self._tone_power(self.main, MAIN_TONE),
                           40 * self._tone_power(self.main, BED_TONE))

    # ---- 1. logo / watermark overlay --------------------------------------
    def test_a_logo_lands_in_the_corner_it_was_asked_for_and_nowhere_else(self):
        # 40x40 logo, 10 px in: its centre is 30 px from each edge it touches.
        expected = {"tl": (30, 30), "tr": (290, 30), "bl": (30, 150), "br": (290, 150)}
        for corner, (logo_x, logo_y) in expected.items():
            with self.subTest(corner=corner):
                produced = self._encode(f"logo_{corner}",
                                        composite_mode="overlay",
                                        composite_corner=corner,
                                        composite_path=self.logo)
                self.assertEqual("red", self._colour_at(produced, logo_x, logo_y),
                                 f"no logo at the {corner} corner")
                for other, (x, y) in expected.items():
                    if other == corner:
                        continue
                    self.assertEqual("green", self._colour_at(produced, x, y),
                                     f"{corner} logo also appeared at {other}")

    def test_a_half_opaque_logo_lets_the_picture_through(self):
        produced = self._encode("logo_faded", composite_mode="overlay",
                                composite_corner="br", composite_opacity=0.5,
                                composite_path=self.logo)
        raw = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
             "-i", str(produced), "-frames:v", "1",
             "-vf", "crop=8:8:286:146,scale=1:1",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300).stdout
        red, green, _blue = raw[0], raw[1], raw[2]
        self.assertGreater(red, 40, "the logo is not there at all")
        self.assertGreater(green, 40, "the picture behind it was fully covered")

    def test_the_logo_is_held_for_the_whole_clip(self):
        # A single-frame PNG input ends after one frame. Without
        # eof_action=repeat the output ends with it.
        produced = self._encode("logo_len", composite_mode="overlay",
                                composite_path=self.logo)
        self.assertGreater(self._duration(produced), 1.5)

    # ---- 2. picture in picture --------------------------------------------
    def test_an_inset_video_sits_in_its_corner_at_the_size_asked_for(self):
        produced = self._encode("pip", composite_mode="pip",
                                composite_corner="br", composite_scale=0.25,
                                composite_path=self.partner)
        self.assertEqual((320, 180), self._geometry(produced),
                         "the inset must not change the canvas")
        # 0.25 of 320 is 80 wide, so 45 -> 44 tall: the box spans x 230..310,
        # y 126..170. Its centre is (270, 148).
        self.assertEqual("blue", self._colour_at(produced, 270, 148))
        self.assertEqual("green", self._colour_at(produced, 60, 60),
                         "the inset covered the main picture")

    # ---- 3. side by side ---------------------------------------------------
    def test_side_by_side_keeps_the_inputs_in_order(self):
        produced = self._encode("sbs", composite_mode="hstack",
                                composite_path=self.partner)
        self.assertEqual((640, 180), self._geometry(produced))
        self.assertEqual("green", self._colour_at(produced, 160, 90),
                         "the LEFT half must be the first input")
        self.assertEqual("blue", self._colour_at(produced, 480, 90),
                         "the RIGHT half must be the second input")

    def test_stacking_puts_the_second_input_underneath(self):
        produced = self._encode("vstack", composite_mode="vstack",
                                composite_path=self.partner)
        self.assertEqual((320, 360), self._geometry(produced))
        self.assertEqual("green", self._colour_at(produced, 160, 90))
        self.assertEqual("blue", self._colour_at(produced, 160, 270))

    def test_a_partner_of_another_shape_is_scaled_not_letterboxed(self):
        tall = self._tmp / "tall.mkv"
        _run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
              "-f", "lavfi", "-i", "color=c=blue:s=90x180:r=15:d=2",
              "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", tall])
        produced = self._encode("sbs_tall", composite_mode="hstack",
                                composite_path=tall)
        width, height = self._geometry(produced)
        self.assertEqual(180, height)
        self.assertEqual(410, width, "the partner kept its 90x180 shape beside 320")
        # No black bars: the partner's own edge column is still blue.
        self.assertEqual("blue", self._colour_at(produced, 325, 90))

    # ---- 4. audio mix ------------------------------------------------------
    def test_both_tones_survive_the_mix_with_the_bed_underneath(self):
        produced = self._encode("amix", composite_audio_path=self.bed,
                                composite_audio_weight=0.3)
        main = self._tone_power(produced, MAIN_TONE)
        bed = self._tone_power(produced, BED_TONE)
        control = self._tone_power(produced, CONTROL_TONE)
        self.assertGreater(bed, 50 * control, "the music bed is not in the output")
        self.assertGreater(main, 50 * control, "the main audio is not in the output")
        # A weight of 0.3 is roughly a tenth of the power. Without the weight
        # the two sit at the same level and this fails.
        self.assertGreater(main, 4 * bed,
                           f"the bed is not under the main audio (main={main:.3g}, bed={bed:.3g})")

    def test_a_music_bed_longer_than_the_film_does_not_stretch_it(self):
        # Two independent guards, and this exercises BOTH: `duration=first`
        # ends the mix with the main track, and the output `-t` catches the
        # cases the mix never sees -- a silent film (below) or a longer partner
        # video, where the graph itself happily runs on past the first input.
        produced = self._encode("amix_long", composite_audio_path=self.long_bed)
        self.assertLess(self._duration(produced), 2.6,
                        "a 5 s bed under a 2 s clip must not extend the output")
        self.assertGreater(self._tone_power(produced, BED_TONE),
                           50 * self._tone_power(produced, CONTROL_TONE))

    def test_a_long_bed_under_a_silent_film_is_cut_to_the_film(self):
        # No mix here -- the bed is mapped straight through, so `duration=first`
        # cannot help and only the output `-t` keeps the result 2 s long.
        produced = self._encode("amix_long_silent", source=self.silent,
                                composite_audio_path=self.long_bed)
        self.assertLess(self._duration(produced), 2.6,
                        "the output ran on for the length of the music")

    def test_a_longer_partner_video_does_not_extend_a_side_by_side(self):
        long_partner = self._tmp / "long_partner.mkv"
        _run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
              "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=15:d=5",
              "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
              long_partner])
        produced = self._encode("sbs_long", composite_mode="hstack",
                                composite_path=long_partner)
        self.assertLess(self._duration(produced), 2.6,
                        "hstack ran on for the length of the SECOND input")

    def test_a_bed_shorter_than_the_film_does_not_cut_it_short(self):
        # The lesson this feature inherited: `-shortest` measures EVERY input,
        # so a 0.8 s sting truncated a 2 s clip. `amix=duration=first` and an
        # output `-t` from the main input are the replacement.
        produced = self._encode("amix_short", composite_audio_path=self.short_bed)
        self.assertGreater(self._duration(produced), 1.8,
                           "a short music bed truncated the output")
        # Sampled at 1.5 s, i.e. AFTER the bed has ended. `duration=shortest`
        # leaves silence there while the container length still looks right,
        # so measuring the start would not notice.
        self.assertGreater(self._tone_power(produced, MAIN_TONE, seconds=0.4, at=1.5),
                           50 * self._tone_power(produced, CONTROL_TONE, seconds=0.4, at=1.5),
                           "the main audio stopped when the short bed did")

    def test_a_bed_under_a_silent_film_is_used_on_its_own(self):
        produced = self._encode("amix_silent", source=self.silent,
                                composite_audio_path=self.bed)
        self.assertGreater(self._tone_power(produced, BED_TONE),
                           50 * self._tone_power(produced, CONTROL_TONE))
        self.assertEqual("green", self._colour_at(produced, 160, 90))

    # ---- the three-input job ----------------------------------------------
    def test_a_logo_and_a_bed_together_reach_the_right_inputs(self):
        # Three inputs, and both halves of the graph have to pick the right
        # one. If the mix read input 1 it would find no audio and the encode
        # would fail; if the overlay read input 2 it would find no picture.
        produced = self._encode("logo_and_bed", composite_mode="overlay",
                                composite_corner="tl",
                                composite_path=self.logo,
                                composite_audio_path=self.bed,
                                composite_audio_weight=0.3)
        self.assertEqual("red", self._colour_at(produced, 30, 30))
        self.assertEqual("green", self._colour_at(produced, 290, 150))
        main = self._tone_power(produced, MAIN_TONE)
        bed = self._tone_power(produced, BED_TONE)
        self.assertGreater(bed, 50 * self._tone_power(produced, CONTROL_TONE))
        self.assertGreater(main, 4 * bed)


if __name__ == "__main__":
    unittest.main()
