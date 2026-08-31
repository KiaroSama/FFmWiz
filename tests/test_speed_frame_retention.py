"""Regression: a speed change must not throw the picture away.

`setpts` retimes frames without changing how many there are, but the output
frame rate stayed whatever FFmpeg guessed from the SOURCE. Speeding up
therefore handed the encoder more frames per second than that rate could carry
and it silently dropped the excess. Measured on a 10 fps, 40-frame source:

    2.0x   40 frames -> 22   white band (0.20, 0.60) instead of (0.10, 0.40)
    1.5x   40 frames -> 28   white band (0.20, 0.70) instead of (0.13, 0.53)

Two separate damages from one cause: nearly half the picture was gone, and what
survived no longer sat on its own subtitles -- the bands moved late AND grew
wider, because each surviving frame was held for the whole of the old slot.

Found while closing B08. It is NOT B08: it reproduces with no cut at all, on a
source whose container and picture clocks agree, so neither the seek nor the
trim is involved. It was recorded separately rather than folded into that
repair, and this is the module that closes it.

The fix is one output option beside the filter that needs it
(`VIDEO_SPEED_OUTPUT_TIMING_ARGS`). `-fps_mode vfr` does not help -- it still
drops against the guessed rate -- and `-r` cannot be combined with a non-CFR
mode at all, so passthrough is the only option that keeps the graph's timing.
At 1.0x and 0.5x the output is byte-identical to not passing it.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

SOURCE_SECONDS = 4.0
SOURCE_FPS = 10
SOURCE_FRAMES = int(SOURCE_SECONDS * SOURCE_FPS)
EARLY = (0.2, 0.8)
LATE = (2.5, 3.5)


def _run(args, timeout=300):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, encoding="utf-8",
                          errors="replace", timeout=timeout)


@requires_ffmpeg
class SpeedFixtures(NoLeakedArtifacts, unittest.TestCase):
    """One real source and the tools to count and look at what came out.

    Fixtures only, no tests: the join case needs the same source but must not
    re-run the single-input encodes to get it.
    """

    @classmethod
    def setUpClass(cls):
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_speedframes_"))
        cls.source = cls._root / "source.mkv"
        flash = "+".join(f"between(t,{start},{end - 0.01})" for start, end in (EARLY, LATE))
        picture = cls._root / "picture.mkv"
        cls._build(picture, [
            "-f", "lavfi", "-i",
            f"color=c=black:s=160x90:r={SOURCE_FPS}:d={SOURCE_SECONDS},"
            f"drawbox=x=0:y=0:w=160:h=90:color=white:t=fill:enable='{flash}'",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "5"])
        audio = cls._root / "audio.mka"
        cls._build(audio, ["-f", "lavfi", "-i", f"sine=frequency=440:duration={SOURCE_SECONDS}",
                           "-c:a", "aac"])
        cls._build(cls.source, ["-i", str(picture), "-i", str(audio),
                                "-map", "0:v", "-map", "1:a", "-c", "copy"])
        # Guard the guard: a source that did not really carry SOURCE_FRAMES
        # frames would make every count below meaningless.
        counted = len(cls._packets(cls.source))
        if counted != SOURCE_FRAMES:
            raise unittest.SkipTest(
                f"the fixture encoded {counted} frames, not {SOURCE_FRAMES}, on this ffmpeg")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    @classmethod
    def _build(cls, path, args):
        result = _run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                       *args, str(path)])
        if result.returncode != 0:
            raise unittest.SkipTest(f"could not build {path.name}: {result.stderr[-400:]}")

    @classmethod
    def _probe(cls, path, *args):
        return json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                *args, str(path)]).stdout or "{}")

    @classmethod
    def _packets(cls, path):
        return cls._probe(path, "-select_streams", "v", "-show_packets").get("packets") or []

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="speedframes_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", self._real_note))

    def _answers(self, **extra):
        probe = self._probe(self.source, "-show_format", "-show_streams")
        streams = probe["streams"]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.source,
            "probe": probe, "format": probe["format"], "streams": streams,
            "output_location": self._tmp, "output_ext": "mkv",
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
            "audio_tracks": [0], "keep_source_subtitles": False,
            "video_codec": "H264", "use_gpu": False, "audio_codec": "aac",
            "audio_bitrate_kbps": 96, "resolution": "n", "color_range_choice": "tv",
        })
        answers.update(extra)
        return answers

    def _encode(self, factor, **extra):
        answers = self._answers(video_speed_enabled=True, video_speed_factor=factor,
                                audio_speed_from_video=True, **extra)
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            cmd = FFmWiz.build_ffmpeg_command(answers)
        result = _run(cmd)
        self.assertEqual(0, result.returncode, result.stderr[-1200:])
        return Path(answers["output_path"]), [str(part) for part in cmd]

    def _white_spans(self, path):
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "info", "-copyts",
             "-i", str(path), "-fps_mode", "passthrough",
             "-vf", "scale=1:1,format=gray,showinfo", "-f", "rawvideo", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
        times = [float(m) for m in re.findall(
            r"pts_time:(-?[\d.]+)", result.stderr.decode("utf-8", "replace"))]
        lumas = list(result.stdout)
        self.assertEqual(len(times), len(lumas), "frame timing and luma got out of step")
        origin = times[0]
        spans, start = [], None
        for moment, luma in zip(times, lumas):
            if luma >= 128 and start is None:
                start = moment
            elif luma < 128 and start is not None:
                spans.append((round(start - origin, 3), round(moment - origin, 3)))
                start = None
        return spans

    def _assert_span(self, span, start, end, delta=0.12):
        self.assertAlmostEqual(start, span[0], delta=delta, msg=f"start of {span}")
        self.assertAlmostEqual(end, span[1], delta=delta, msg=f"end of {span}")


class SpeedKeepsEveryFrame(SpeedFixtures):
    """One real source, encoded at several factors, counted and looked at."""

    def test_speeding_up_keeps_every_frame(self):
        for factor, was in ((2.0, 22), (1.5, 28)):
            with self.subTest(factor=factor):
                output, _cmd = self._encode(factor)
                kept = len(self._packets(output))
                self.assertEqual(
                    SOURCE_FRAMES, kept,
                    f"{factor}x kept {kept} of {SOURCE_FRAMES} frames "
                    f"(it used to keep {was})")

    def test_the_picture_still_sits_on_its_own_cues(self):
        # The second damage: the surviving frames moved late and each band grew
        # wider, because one frame was held for a whole old slot.
        output, _cmd = self._encode(2.0)
        spans = self._white_spans(output)
        self.assertEqual(2, len(spans), spans)
        self._assert_span(spans[0], EARLY[0] / 2, EARLY[1] / 2)
        self._assert_span(spans[1], LATE[0] / 2, LATE[1] / 2)
        for span, (start, end) in zip(spans, (EARLY, LATE)):
            self.assertAlmostEqual((end - start) / 2, span[1] - span[0], delta=0.08,
                                   msg=f"band {span} is the wrong width")

    def test_a_cut_then_a_speed_up_keeps_its_frames_too(self):
        # The seeked path, which is where this was first noticed.
        output, _cmd = self._encode(2.0, cut_keep_ranges=[(2.0, 4.0)])
        kept = len(self._packets(output))
        self.assertEqual(SOURCE_FRAMES // 2, kept,
                         f"a 2 s window of a {SOURCE_FPS} fps source is "
                         f"{SOURCE_FRAMES // 2} frames, got {kept}")

    # ---- the paths that already worked must not move ---------------------
    def test_slowing_down_is_unchanged(self):
        output, _cmd = self._encode(0.5)
        self.assertEqual(SOURCE_FRAMES, len(self._packets(output)))
        spans = self._white_spans(output)
        self._assert_span(spans[0], EARLY[0] * 2, EARLY[1] * 2)
        self._assert_span(spans[1], LATE[0] * 2, LATE[1] * 2)

    def test_a_job_with_no_speed_change_gets_no_timing_option(self):
        # Guard the guard: an option added unconditionally would make every
        # assertion above pass while changing jobs that never asked for it.
        answers = self._answers()
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
        self.assertNotIn("-fps_mode", cmd)

    def test_the_option_reaches_the_command(self):
        _output, cmd = self._encode(2.0)
        self.assertIn("-fps_mode", cmd)
        self.assertEqual("passthrough", cmd[cmd.index("-fps_mode") + 1])

    def test_only_one_timing_option_is_ever_emitted(self):
        # FFmpeg refuses `-r` together with a non-CFR -fps_mode, and two
        # -fps_mode options on one output is an error in its own right.
        for factor in (0.5, 1.5, 2.0):
            with self.subTest(factor=factor):
                answers = self._answers(video_speed_enabled=True,
                                        video_speed_factor=factor,
                                        audio_speed_from_video=True)
                noise = StringIO()
                with redirect_stdout(noise), redirect_stderr(noise):
                    cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
                modes = [part for part in cmd if part.startswith("-fps_mode")]
                self.assertLessEqual(len(modes), 1, cmd)


class AJoinedSpeedChangeStatesItToo(SpeedFixtures):
    """The join builds its own command, so it needs its own proof.

    It also already had an `-fps_mode vfr` of its own for a VFR join, and only
    one may be given -- `vfr` does not prevent the drop, so a speed change has
    to win that contest.
    """

    def _join_command(self, **extra):
        probe = self._probe(self.source, "-show_format", "-show_streams")
        streams = probe["streams"]
        item = {
            "path": self.source, "probe": probe, "format": probe["format"],
            "streams": streams, "duration": SOURCE_SECONDS,
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [], "attachment_streams": [], "data_streams": [],
        }
        answers = self._answers(join_input_items=[item], **extra)
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            cmd = FFmWiz.build_join_encode_command(
                answers, [dict(item), item], self._tmp / "joined.mkv")
        return [str(part) for part in cmd], answers

    def test_a_joined_speed_change_keeps_its_frames(self):
        cmd, answers = self._join_command(video_speed_enabled=True,
                                          video_speed_factor=2.0,
                                          audio_speed_from_video=True)
        self.assertIn("-fps_mode", cmd)
        self.assertEqual("passthrough", cmd[cmd.index("-fps_mode") + 1])
        result = _run(cmd)
        self.assertEqual(0, result.returncode, result.stderr[-1200:])
        kept = len(self._packets(Path(answers["output_path"])))
        self.assertEqual(SOURCE_FRAMES * 2, kept,
                         f"two {SOURCE_FRAMES}-frame inputs joined at 2x kept {kept}")

    def test_a_plain_join_is_untouched(self):
        cmd, _answers = self._join_command()
        self.assertNotIn("passthrough", cmd)

    def test_a_speed_change_outranks_the_vfr_choice(self):
        # Both would emit -fps_mode; exactly one may be given, and `vfr` is the
        # one that does not stop the drop.
        cmd, _answers = self._join_command(join_vfr=True, video_speed_enabled=True,
                                           video_speed_factor=2.0,
                                           audio_speed_from_video=True)
        modes = [cmd[index + 1] for index, part in enumerate(cmd) if part == "-fps_mode"]
        self.assertEqual(["passthrough"], modes, cmd)


class EveryBuilderThatRetimesAlsoStatesItsTiming(unittest.TestCase):
    """The filter and the output option are a pair; three builders emit the filter.

    A mechanical guard, because the defect was exactly this pair coming apart:
    the filter shipped in several places and the option in none. The list is
    checked against the source every run, so a builder that moves file -- as
    build_cpu_video_filter did, from wizard_build to wizard_build_b -- fails
    here until the list follows it.
    """

    # Modules that BUILD A COMMAND and emit the retiming filter: the option has
    # to sit in the same file, because the same function writes both.
    MODULES = ("ffmwiz/wizard_build_b.py",
               "ffmwiz/support/ext04b.py", "ffmwiz/support/L04.py",
               # The composite builder retimes too, since it started
               # carrying the picture chain.
               "ffmwiz/wizard_build_c.py")

    # Modules that only COMPOSE THE FILTER STRING and hand it to a command
    # builder that lives elsewhere. build_cpu_video_filter moved here in the
    # 800-line split and returns a chain; wizard_build.build_ffmpeg_command is
    # what turns it into a command, so that is where the option must appear.
    # Mapping, not a bare list: naming the consumer is what keeps this from
    # being an exemption.
    #
    # This module also writes the two GIF commands, and those correctly carry
    # NO -fps_mode. Measured: the GIF chain is
    #     setpts=(PTS-STARTPTS)/2,fps=15,scale=480:-1:flags=lanczos
    # -- the `fps=` filter sits AFTER the retiming and regenerates the cadence
    # outright, so 'keep the input timestamps' would contradict it. The plain
    # encode chain has no such filter (setpts,format=yuv420p), which is why
    # wizard_build must state the option there. Do not 'fix' the GIF path by
    # adding it.
    FILTER_ONLY = {"ffmwiz/wizard_build_filters.py": ("ffmwiz/wizard_build.py",)}

    def _source(self, relative):
        return (Path(FFmWiz.__file__).resolve().parent / relative).read_text(encoding="utf-8")

    def test_the_option_lives_beside_the_filter_that_needs_it(self):
        for relative in self.MODULES:
            with self.subTest(module=relative):
                text = self._source(relative)
                self.assertIn("build_video_speed_filter(", text,
                              "this guard is watching the wrong module")
                self.assertIn("VIDEO_SPEED_OUTPUT_TIMING_ARGS", text,
                              "this builder retimes the picture but never says "
                              "to keep the retimed frames")

    def test_the_guard_covers_every_builder_that_emits_the_filter(self):
        # Guard the guard: a fifth builder added later must fail here rather
        # than quietly reintroduce the defect.
        root = Path(FFmWiz.__file__).resolve().parent / "ffmwiz"
        emitting = sorted(
            str(path.relative_to(root.parent)).replace("\\", "/")
            for path in root.rglob("*.py")
            if "build_video_speed_filter(" in path.read_text(encoding="utf-8")
            and "def build_video_speed_filter" not in path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(self.MODULES) + sorted(self.FILTER_ONLY), sorted(emitting))

    def test_a_filter_only_module_has_a_consumer_that_states_the_timing(self):
        # The pair may cross a file boundary, but it may not simply vanish:
        # every filter-only module names the command builder that carries the
        # option, and that builder is checked here.
        for relative, consumers in self.FILTER_ONLY.items():
            with self.subTest(module=relative):
                self.assertIn("build_video_speed_filter(", self._source(relative),
                              "this module is listed as filter-only but emits no filter")
                self.assertNotIn("VIDEO_SPEED_OUTPUT_TIMING_ARGS", self._source(relative),
                                 "this module states the timing itself, so it belongs "
                                 "in MODULES rather than FILTER_ONLY")
                self.assertTrue(consumers, "a filter-only module must name its consumer")
                for consumer in consumers:
                    self.assertIn("VIDEO_SPEED_OUTPUT_TIMING_ARGS", self._source(consumer),
                                  f"{consumer} turns {relative}'s chain into a command "
                                  "but never says to keep the retimed frames")


if __name__ == "__main__":
    unittest.main()
