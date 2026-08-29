"""Multi-input compositing: overlay, picture-in-picture, stacks, audio mix.

The risk here is not filter syntax, it is INPUT INDICES. A job with a logo and
a music bed has three inputs, and the two halves of the graph address different
ones: `[0:v][1:v]overlay` for the picture, `[0:a][2:a]amix` for the sound. An
`amix` that assumed "the extra input" was 1 would reach for the logo's audio,
which does not exist -- the same class of mistake as the `-map_chapters 1` that
addressed an .srt because a subtitle input had shifted the base.

So the argv tests below always use THREE inputs, and the real-media ones prove
the result in pixels and in tones rather than in the command string: a logo
must appear at the corner that was asked for and at none of the others, the
left half of a side-by-side must be input 1, and a mix must carry both tones
with the second one measurably under the first.
"""
import json
import math
import shutil
import subprocess
import tempfile
import contextlib
import io
import unittest
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

MAIN_TONE = 440
BED_TONE = 880
CONTROL_TONE = 1500


def _run(args, timeout=300):
    return subprocess.run([str(part) for part in args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace", timeout=timeout)


class TheGraphAddressesTheRightInputs(unittest.TestCase):
    """Pure graph construction -- no encoding, three inputs throughout."""

    def _answers(self, **extra):
        answers = {
            "video_streams": [{"width": 320, "height": 180, "pix_fmt": "yuv420p"}],
            "audio_streams": [{"codec_type": "audio"}],
            "format": {"duration": "10.0"},
            "video_encoder": "libx264", "use_gpu": False, "output_ext": "mkv",
        }
        answers.update(extra)
        return answers

    def _graph(self, picture_input, audio_input, **extra):
        return FFmWiz.build_composite_filter_graph(
            self._answers(**extra), picture_input, audio_input)

    def test_a_logo_and_a_music_bed_address_different_inputs(self):
        # THE test this feature exists to keep honest. Input 1 is the logo and
        # input 2 is the music; swapping them silently produces a command that
        # either fails or mixes the wrong thing.
        chains, video, audio, _notes = self._graph(
            1, 2, composite_mode="overlay", composite_audio_weight=0.3)
        graph = ";".join(chains)
        self.assertIn("[0:v][1:v]overlay=", graph)
        self.assertIn("[2:a:0]", graph, "the mix must read the THIRD input")
        self.assertNotIn("[1:a", graph, "input 1 is the logo; it has no audio")
        self.assertEqual("[vout]", video)
        self.assertEqual("[aout]", audio)

    def test_the_picture_partner_is_never_read_from_the_audio_index(self):
        chains, _v, _a, _n = self._graph(1, 2, composite_mode="hstack")
        self.assertIn("[1:v]scale=", ";".join(chains))
        self.assertNotIn("[2:v]", ";".join(chains))

    def test_an_audio_only_partner_sits_at_input_one(self):
        # No picture partner, so the music is input 1 -- not 2. A builder that
        # hard-coded "the mix input is 2" would emit a stream that is not there.
        chains, video, audio, _notes = self._graph(None, 1)
        self.assertEqual("0:v:0", video, "an amix-only job keeps the source picture")
        self.assertIn("[0:a:0][1:a:0]amix=", ";".join(chains))
        self.assertEqual("[aout]", audio)

    def test_each_corner_produces_its_own_expression(self):
        seen = {}
        for corner in ("tl", "tr", "bl", "br"):
            chains, _v, _a, _n = self._graph(
                1, None, composite_mode="overlay", composite_corner=corner,
                composite_margin=10)
            seen[corner] = next(c for c in chains if "overlay=" in c).split("overlay=")[1]
        self.assertEqual(4, len(set(seen.values())), seen)
        self.assertTrue(seen["tl"].startswith("10:10"), seen["tl"])
        self.assertTrue(seen["tr"].startswith("W-w-10:10"), seen["tr"])
        self.assertTrue(seen["bl"].startswith("10:H-h-10"), seen["bl"])
        self.assertTrue(seen["br"].startswith("W-w-10:H-h-10"), seen["br"])

    def test_a_stack_scales_the_partner_to_the_shared_edge_and_says_so(self):
        # "Say what you did rather than silently letterboxing" -- the note is
        # part of the contract, not decoration.
        chains, _v, _a, notes = self._graph(1, None, composite_mode="hstack")
        self.assertIn("[1:v]scale=-2:180", ";".join(chains))
        self.assertTrue(any("180 px tall" in note for note in notes), notes)
        chains, _v, _a, notes = self._graph(1, None, composite_mode="vstack")
        self.assertIn("[1:v]scale=320:-2", ";".join(chains))
        self.assertTrue(any("320 px wide" in note for note in notes), notes)

    def test_the_inset_is_scaled_from_the_main_picture_width(self):
        chains, _v, _a, _n = self._graph(1, None, composite_mode="pip",
                                         composite_scale=0.25)
        self.assertIn("[1:v]scale=80:-2", ";".join(chains))

    def test_a_full_opacity_overlay_adds_no_alpha_filter(self):
        chains, _v, _a, _n = self._graph(1, None, composite_mode="overlay")
        self.assertNotIn("colorchannelmixer", ";".join(chains))
        chains, _v, _a, _n = self._graph(1, None, composite_mode="overlay",
                                         composite_opacity=0.5)
        # format=rgba first: without it a source with no alpha channel has
        # nothing for the mixer to scale and the overlay stays opaque.
        self.assertIn("format=rgba,colorchannelmixer=aa=0.5", ";".join(chains))

    def test_the_mix_ends_with_the_first_input_not_the_shortest(self):
        # `-shortest` counts every input, and a 3 s music bed under a 2 minute
        # film truncated the output to 3 s. duration=first is the fix.
        chains, _v, _a, _n = self._graph(None, 1)
        mix = next(c for c in chains if "amix=" in c)
        self.assertIn("duration=first", mix)
        self.assertNotIn("shortest", mix)

    def test_the_weight_is_carried_and_not_renormalised_away(self):
        chains, _v, _a, _n = self._graph(None, 1, composite_audio_weight=0.3)
        mix = next(c for c in chains if "amix=" in c)
        self.assertIn("weights=1 0.3", mix)
        # normalize=1 (the default) divides by the sum of the weights, so the
        # main track would be quietened by the very option meant to quieten the
        # bed.
        self.assertIn("normalize=0", mix)

    def test_a_silent_main_input_uses_the_bed_on_its_own(self):
        chains, _v, audio, notes = self._graph(None, 1, audio_streams=[])
        self.assertEqual("1:a:0", audio)
        self.assertFalse([c for c in chains if "amix=" in c],
                         "a mix of one source is not a mix")
        self.assertTrue(any("no audio" in note for note in notes), notes)

    def test_nothing_is_built_for_a_job_that_asked_for_none_of_this(self):
        chains, video, audio, notes = self._graph(None, None)
        self.assertEqual([], chains)
        self.assertEqual(("0:v:0", "0:a:0", []), (video, audio, notes))


class TheCommandCarriesTheInputsInOrder(NoLeakedArtifacts, unittest.TestCase):

    def _answers(self, **extra):
        answers = self.own({
            "ffmpeg": "ffmpeg", "ffprobe": "ffprobe",
            "input_path": Path("main.mkv"),
            "video_streams": [{"width": 320, "height": 180, "pix_fmt": "yuv420p"}],
            "audio_streams": [{"codec_type": "audio"}],
            "format": {"duration": "12.5"},
            "output_ext": "mkv", "video_codec": "H264", "use_gpu": False,
            "color_range_choice": "tv", "video_crf": 28, "audio_codec": "aac",
        })
        answers.update(extra)
        return answers

    def test_three_inputs_arrive_in_the_order_the_graph_assumes(self):
        cmd = FFmWiz.build_composite_command(
            self._answers(composite_mode="overlay",
                          composite_path=Path("logo.png"),
                          composite_audio_path=Path("bed.flac")),
            Path("out.mkv"))
        inputs = [cmd[i + 1] for i, part in enumerate(cmd) if part == "-i"]
        self.assertEqual(["main.mkv", "logo.png", "bed.flac"], inputs)
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[0:v][1:v]overlay=", graph)
        self.assertIn("[2:a:0]", graph)

    def test_the_output_is_capped_at_the_first_inputs_length(self):
        cmd = FFmWiz.build_composite_command(
            self._answers(composite_audio_path=Path("bed.flac")), Path("out.mkv"))
        self.assertNotIn("-shortest", cmd)
        self.assertEqual("12.500", cmd[cmd.index("-t") + 1])

    def test_a_copy_request_is_resolved_to_a_real_encoder(self):
        answers = self._answers(video_codec="copy",
                                composite_mode="overlay",
                                composite_path=Path("logo.png"))
        real = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        try:
            cmd = FFmWiz.build_composite_command(answers, Path("out.mkv"))
        finally:
            FFmWiz.appio.note = real
        self.assertNotIn("copy", cmd[cmd.index("-c:v") + 1])

    def test_a_job_with_nothing_to_composite_is_refused(self):
        with self.assertRaises(ValueError):
            FFmWiz.build_composite_command(self._answers(), Path("out.mkv"))

    def test_composite_active_reads_the_answers_the_step_writes(self):
        self.assertFalse(FFmWiz.composite_active({}))
        self.assertTrue(FFmWiz.composite_active({"composite_path": Path("a.png")}))
        self.assertTrue(FFmWiz.composite_active({"composite_audio_path": Path("a.wav")}))


@requires_ffmpeg
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


class TheWizardQuestionReachesTheBuilder(unittest.TestCase):
    """The step writes answer keys; the builder reads them.

    That seam breaks silently: renaming `composite_corner` on one side leaves a
    wizard that accepts the answer and a command that ignores it.
    """

    def _prompted(self, *replies):
        """Run the step with a FINITE script of answers.

        Finite because the step re-asks on a rejected value: a constant stub
        never terminates, and one such stub has already burned a wall timeout
        in this suite.
        """
        from ffmwiz import wizard_composite
        answers = {}
        script = iter(replies)
        real_ask, real_error = FFmWiz.appio.ask_raw, FFmWiz.appio.error
        FFmWiz.appio.ask_raw = lambda *a, **k: next(script)
        FFmWiz.appio.error = lambda *a, **k: None
        try:
            wizard_composite.step_video_composite(answers)
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.error = real_ask, real_error
        return answers

    def test_declining_writes_nothing(self):
        self.assertEqual({}, self._prompted("n"))
        self.assertEqual({}, self._prompted(""))

    def test_every_token_the_prompt_offers_reaches_the_graph(self):
        # A token the parser accepts but no builder acts on is a lie in the
        # hint text.
        from ffmwiz.wizard_composite import parse_composite_tokens
        base = {"video_streams": [{"width": 320, "height": 180}],
                "audio_streams": [{"codec_type": "audio"}]}
        cases = ["overlay", "logo", "watermark", "pip", "sbs", "hstack",
                 "vstack", "amix", "mix", "music",
                 "overlay,tl", "overlay,tr", "overlay,bl", "overlay,br",
                 "overlay,center", "overlay,margin=20", "overlay,opacity=0.4",
                 "pip,size=0.4", "amix,weight=0.5"]
        for text in cases:
            with self.subTest(token=text):
                answers = dict(base, **parse_composite_tokens(text))
                picture = 1 if answers.get("composite_mode") else None
                audio = 2 if answers.get("composite_audio_mix") else None
                chains, _v, _a, _n = FFmWiz.build_composite_filter_graph(
                    answers, picture, audio)
                self.assertTrue(chains, f"{text} reached no builder")

    def test_two_picture_modes_are_refused(self):
        from ffmwiz.wizard_composite import parse_composite_tokens
        with self.assertRaises(ValueError):
            parse_composite_tokens("overlay,vstack")

    def test_an_option_with_nothing_to_apply_to_is_refused(self):
        # Accepting `sbs,opacity=0.5` and ignoring the opacity is how a hint
        # ends up promising something the command never does.
        from ffmwiz.wizard_composite import parse_composite_tokens
        for text in ("sbs,opacity=0.5", "sbs,tr", "overlay,size=0.5",
                     "overlay,weight=0.5", "tr", "margin=10"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_composite_tokens(text)

    def test_out_of_range_numbers_are_refused_not_clamped(self):
        from ffmwiz.wizard_composite import parse_composite_tokens
        for text in ("overlay,opacity=2", "overlay,opacity=-1", "pip,size=3"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_composite_tokens(text)

    def test_a_rejected_answer_leaves_no_debris(self):
        self.assertEqual({}, self._prompted("overlay,vstack", "n"))

    def test_back_still_works(self):
        # The back token is `0`, the same one every other step accepts -- not
        # "b". A step that answered only to "b" would trap the user here.
        from ffmwiz import wizard_composite
        for token in sorted(FFmWiz.BACK_INPUT_TOKENS):
            with self.subTest(token=token):
                real_ask = FFmWiz.appio.ask_raw
                FFmWiz.appio.ask_raw = lambda *a, **k: token
                try:
                    with self.assertRaises(FFmWiz.Back):
                        wizard_composite.step_video_composite({})
                finally:
                    FFmWiz.appio.ask_raw = real_ask

    def test_back_out_of_the_file_question_leaves_no_half_answer(self):
        from ffmwiz import wizard_composite
        answers = {}
        script = iter(["overlay", "0"])
        real_ask, real_error = FFmWiz.appio.ask_raw, FFmWiz.appio.error
        FFmWiz.appio.ask_raw = lambda *a, **k: next(script)
        FFmWiz.appio.error = lambda *a, **k: None
        try:
            with self.assertRaises(FFmWiz.Back):
                wizard_composite.step_video_composite(answers)
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.error = real_ask, real_error
        self.assertNotIn("composite_path", answers)

    def test_an_answer_survives_into_a_command(self):
        tmp = Path(tempfile.mkdtemp(prefix="composite_step_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        logo = tmp / "logo.png"
        logo.write_bytes(b"not really a png")
        answers = {"input_path": tmp / "main.mkv", "_question_number": 3}
        script = iter(["overlay,tl,margin=25", str(logo)])
        real_ask, real_error = FFmWiz.appio.ask_raw, FFmWiz.appio.error
        real_load = FFmWiz.services.join_load_media_item
        FFmWiz.appio.ask_raw = lambda *a, **k: next(script)
        FFmWiz.appio.error = lambda *a, **k: None
        FFmWiz.services.join_load_media_item = lambda a, path, **k: {
            "path": path, "video_streams": [{"width": 40, "height": 40}],
            "audio_streams": []}
        try:
            from ffmwiz import wizard_composite
            wizard_composite.step_video_composite(answers)
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.error = real_ask, real_error
            FFmWiz.services.join_load_media_item = real_load
        self.assertEqual("overlay", answers["composite_mode"])
        self.assertEqual(logo, answers["composite_path"])
        chains, _v, _a, _n = FFmWiz.build_composite_filter_graph(
            dict(answers, video_streams=[{"width": 320, "height": 180}],
                 audio_streams=[]), 1, None)
        self.assertIn("overlay=25:25", ";".join(chains))
        self.assertIn("logo.png", wizard_composite.describe_composite(answers))

    def test_the_step_is_on_the_encode_path(self):
        # Registered but unreachable is the same as absent.
        import inspect
        from ffmwiz import wizard_flow
        self.assertIn('wizard_base.Step("video_composite"',
                      inspect.getsource(wizard_flow.run_wizard))


if __name__ == "__main__":
    unittest.main()


class TheAnswerActuallyReachesTheCommand(unittest.TestCase):
    """A question whose answer is ignored is worse than a missing feature.

    `build_composite_command` existed, was tested, and was never CALLED: the
    wizard asked for an overlay, recorded the answer, and then ran an ordinary
    encode with no overlay and no warning. Every argv test in this file passed
    the whole time, because they all called the builder directly.

    So this drives `step_start_now` -- the real dispatcher -- and checks which
    builder it chose.
    """

    def _dispatch(self, **extra):
        """Run step_start_now far enough to see which builder it picked."""
        from ffmwiz import wizard_b, wizard_build, wizard_build_b
        picked = []
        tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_dispatch_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        source = tmp / "in.mkv"
        source.write_bytes(b"")

        answers = {
            "ffmpeg": FFMPEG or "ffmpeg", "ffprobe": FFPROBE or "ffprobe",
            "input_path": source, "output_location": tmp, "output_ext": "mkv",
            "video_streams": [{"codec_type": "video", "width": 640, "height": 480,
                               "pix_fmt": "yuv420p"}],
            "audio_streams": [], "subtitle_streams": [], "data_streams": [],
            "streams": [], "probe": {"streams": [], "format": {}},
            "format": {"duration": "10.0"}, "audio_tracks": [],
            "video_codec": "H264", "video_encoder": "libx264", "use_gpu": False,
            "color_range_choice": "tv", "crf": 28, "preset": "ultrafast",
        }
        answers.update(extra)

        real = {
            "ordinary": wizard_build.build_ffmpeg_command,
            "composite": wizard_build_b.build_composite_command,
            "ask": FFmWiz.appio.ask_yes_no,
            "note": FFmWiz.appio.note,
            "summary": None,
        }
        wizard_build.build_ffmpeg_command = (
            lambda a: picked.append("ordinary") or ["ffmpeg", "out.mkv"])
        wizard_build_b.build_composite_command = (
            lambda a, out: picked.append("composite") or ["ffmpeg", str(out)])
        FFmWiz.appio.ask_yes_no = lambda *a, **k: False
        FFmWiz.appio.note = lambda *a, **k: None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                wizard_b.step_start_now(answers)
        except Exception:
            # The summary/colour-range machinery beyond the dispatch is not what
            # this test measures; the builder choice is already recorded.
            pass
        finally:
            wizard_build.build_ffmpeg_command = real["ordinary"]
            wizard_build_b.build_composite_command = real["composite"]
            FFmWiz.appio.ask_yes_no = real["ask"]
            FFmWiz.appio.note = real["note"]
        return picked

    def test_a_composite_answer_selects_the_composite_builder(self):
        picked = self._dispatch(composite_mode="overlay",
                                composite_corner="bottom-right",
                                composite_margin=10,
                                composite_input_path="logo.png")
        self.assertIn("composite", picked,
                      f"the overlay answer was ignored; builder chosen: {picked}")
        self.assertNotIn("ordinary", picked)

    def test_a_job_without_one_still_takes_the_ordinary_path(self):
        picked = self._dispatch()
        self.assertEqual(["ordinary"], picked)

    def test_an_audio_only_mix_reaches_the_composite_builder(self):
        picked = self._dispatch(composite_audio_mix=True,
                                composite_audio_path="bed.mp3")
        self.assertIn("composite", picked,
                      f"the mix answer was ignored; builder chosen: {picked}")
        self.assertNotIn("ordinary", picked)

    def test_a_picture_mode_still_reaches_it(self):
        picked = self._dispatch(composite_mode="overlay",
                                composite_input_path="logo.png")
        self.assertIn("composite", picked,
                      f"the overlay answer was ignored; builder chosen: {picked}")
        self.assertNotIn("ordinary", picked)

    def test_a_job_with_neither_key_still_takes_the_ordinary_encode(self):
        picked = self._dispatch()
        self.assertEqual(["ordinary"], picked)
