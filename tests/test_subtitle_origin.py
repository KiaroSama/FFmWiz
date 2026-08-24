"""Regression: subtitle extraction must not move the timestamp origin (F08).

`extract_subtitle_text()` ran FFmpeg without `-copyts`, so the demuxer handed
back cues it had already rebased by the CONTAINER start -- the minimum across
every stream -- while `TimelineMap` and the joined-timeline offsets both measure
from the picture. An MKV whose AAC track carries negative priming starts at
-0.023 while the video starts at 0, so a source packet at 0.200 s was read as
0.223 s and the error was then multiplied by every later transform.

Reproduced on ffmpeg 8.1.1 with the fixtures below (the offset is made explicit
and larger than real priming so it clears the encode's frame grid; this build no
longer exposes a plain AAC mux as a negative container start, so a fixture that
only muxed AAC would prove nothing here). Container start -0.500, video start 0,
source subtitle packet at 0.200:

    extracted cue           0.700   ->  0.200
    2x   output cue         0.361   ->  0.100
    0.5x output cue         1.444   ->  0.400
    reverse output cue      2.678   ->  3.200

The output numbers are subtitle PACKET PTS taken relative to the output's first
VIDEO packet, and they were checked against the picture itself: the fixture
flashes white over each cue, and after the fix the 0.5x and reverse cues land on
exactly the frames that are white.

That left one clock still split, and B08 closed it. `-ss` and `trim` counted
from the container while the unseeked chain rebased to the video, so a seeked
cut of a primed source ran early by exactly `video.start_time -
format.start_time`. Measured on the same fixture, a requested 2.0-4.0 picture
cut put its 2.5-3.5 cue at 1.0-2.0 and the white frames with it; the seek had
landed at picture 1.5. The repair adds that offset to the seek and to every
trim range, so `subtitle_source_origin` has one answer instead of two.

    single cut   cue 1.0-2.0  ->  0.5-1.5     (white frames agree)
    multi cut    cue 1.5-2.0  ->  1.0-1.5
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

SOURCE_SECONDS = 4.0
CONTAINER_LEAD = 0.500          # how far the audio starts before the picture
EARLY = (0.200, 0.800, "EARLY")  # both cues are given on the VIDEO's own clock
LATE = (2.500, 3.500, "HELLO")


def _run(args, timeout=300):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, encoding="utf-8",
                          errors="replace", timeout=timeout)


class SourceOriginRules(unittest.TestCase):
    """The two clock decisions, without touching ffmpeg."""

    def test_the_video_stream_start_beats_the_container_start(self):
        # The whole point: they disagree, and the picture wins.
        probe = {"streams": [{"start_time": "0.000000"}],
                 "format": {"start_time": "-0.023000"}}
        self.assertEqual(0.0, FFmWiz.video_timeline_origin(probe))

    def test_the_container_start_is_the_fallback(self):
        # No video stream to ask: reproduce the demuxer's own normalization
        # rather than invent an origin.
        probe = {"streams": [], "format": {"start_time": "1.250000"}}
        self.assertEqual(1.25, FFmWiz.video_timeline_origin(probe))

    def test_nothing_usable_means_no_origin_at_all(self):
        self.assertIsNone(FFmWiz.video_timeline_origin({}))
        self.assertIsNone(FFmWiz.video_timeline_origin(
            {"streams": [{"start_time": "N/A"}], "format": {}}))

    PRIMED = {"video_streams": [{"start_time": "0.000000"}],
              "format": {"start_time": "-0.500000"}}

    def test_every_encode_measures_from_the_picture(self):
        # One answer now. A seeked encode used to say -0.5 here, because the
        # seek itself was on the other clock (B08).
        self.assertEqual(0.0, FFmWiz.subtitle_source_origin(self.PRIMED))

    def test_the_offset_is_the_gap_between_the_two_clocks(self):
        self.assertEqual(0.5, FFmWiz.picture_clock_offset(self.PRIMED))

    def test_an_ordinary_file_needs_no_offset(self):
        self.assertEqual(0.0, FFmWiz.picture_clock_offset(
            {"video_streams": [{"start_time": "1.000000"}],
             "format": {"start_time": "1.000000"}}))

    def test_an_unknown_clock_shifts_nothing(self):
        # Guessing an offset would move a cut on a file that never needed one.
        self.assertEqual(0.0, FFmWiz.picture_clock_offset({}))
        self.assertEqual(0.0, FFmWiz.picture_clock_offset(
            {"video_streams": [{"start_time": "0.0"}], "format": {"start_time": "N/A"}}))

    def test_a_source_that_says_nothing_leaves_the_clock_alone(self):
        self.assertEqual(0.0, FFmWiz.subtitle_source_origin({}))
        self.assertEqual(0.0, FFmWiz.subtitle_source_origin(
            {"video_streams": [{}], "format": {"start_time": "N/A"}}))


@requires_ffmpeg
class OriginFixtures(unittest.TestCase):
    """Two real sources whose container clock and video clock disagree.

    `primed`  -- audio starts CONTAINER_LEAD before the picture, so the
                 container starts negative while the video starts at 0. That is
                 the shape a negative-AAC-priming MKV has.
    `offset`  -- every stream starts at 1.0, so the video start is non-zero but
                 the two clocks agree. Nothing here may change on it.

    The picture is black with a white flash over each cue, so a test can compare
    a cue against the frames it is supposed to sit on instead of against a
    number someone typed.
    """

    @classmethod
    def setUpClass(cls):
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_origin_"))
        srt = cls._root / "cues.srt"
        srt.write_text(
            "".join(f"{i}\n{FFmWiz.srt_timestamp(s)} --> {FFmWiz.srt_timestamp(e)}\n{b}\n\n"
                    for i, (s, e, b) in enumerate([EARLY, LATE], start=1)),
            encoding="utf-8", newline="\n")
        flash = "+".join(f"between(t,{s},{e - 0.01})" for s, e, _b in (EARLY, LATE))
        video = cls._root / "v.mkv"
        audio = cls._root / "a.mka"
        cls._build(video, [
            "-f", "lavfi", "-i",
            f"color=c=black:s=160x90:r=10:d={SOURCE_SECONDS},"
            f"drawbox=x=0:y=0:w=160:h=90:color=white:t=fill:enable='{flash}'",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "5"])
        cls._build(audio, ["-f", "lavfi", "-i", f"sine=frequency=440:duration={SOURCE_SECONDS}",
                           "-c:a", "aac"])
        cls.primed = cls._root / "primed.mkv"
        cls._build(cls.primed, [
            "-i", str(video), "-itsoffset", f"-{CONTAINER_LEAD}", "-i", str(audio),
            "-i", str(srt), "-map", "0:v", "-map", "1:a", "-map", "2:s", "-c", "copy",
            "-copyts", "-avoid_negative_ts", "disabled",
            "-metadata:s:s:0", "language=spa", "-disposition:s:0", "default"])
        cls.offset = cls._root / "offset.mkv"
        cls._build(cls.offset, [
            "-i", str(video), "-i", str(audio), "-i", str(srt),
            "-map", "0:v", "-map", "1:a", "-map", "2:s", "-c", "copy",
            "-output_ts_offset", "1.0", "-avoid_negative_ts", "disabled"])
        # Guard the guard: a fixture that does not actually separate the two
        # clocks would let every assertion below pass for the wrong reason.
        for name, expected_lead in (("primed", CONTAINER_LEAD), ("offset", 0.0)):
            source = getattr(cls, name)
            lead = cls._video_start(source) - cls._container_start(source)
            if abs(lead - expected_lead) > 0.05:
                raise unittest.SkipTest(
                    f"{name} fixture did not reproduce a {expected_lead}s container lead "
                    f"on this ffmpeg (measured {lead:.3f}s)")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    @classmethod
    def _build(cls, path, args):
        result = _run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                       *args, str(path)])
        if result.returncode != 0:
            raise unittest.SkipTest(f"could not build {path.name}: {result.stderr[-400:]}")

    # ---- probing --------------------------------------------------------
    @classmethod
    def _probe(cls, path, *args):
        return json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                *args, str(path)]).stdout or "{}")

    @classmethod
    def _container_start(cls, path):
        return float(cls._probe(path, "-show_format")["format"]["start_time"])

    @classmethod
    def _video_start(cls, path):
        return float(cls._probe(path, "-select_streams", "v:0",
                                "-show_streams")["streams"][0]["start_time"])

    def _packets(self, path, kind, first_only=False):
        args = ["-select_streams", kind, "-show_packets"]
        if first_only:
            args += ["-read_intervals", "%+#1"]
        packets = self._probe(path, *args).get("packets") or []
        return [(float(p["pts_time"]), float(p["pts_time"]) + float(p.get("duration_time") or 0))
                for p in packets]

    def _cues_against_the_picture(self, path):
        """Output subtitle spans, measured from the output's first video packet."""
        origin = self._packets(path, "v", first_only=True)[0][0]
        return [(round(start - origin, 3), round(end - origin, 3))
                for start, end in self._packets(path, "s")]

    def _white_spans(self, path):
        """Where the flash really is, in the same frame of reference."""
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "info", "-copyts",
             "-i", str(path), "-fps_mode", "passthrough",
             "-vf", "scale=1:1,format=gray,showinfo", "-f", "rawvideo", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
        import re
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

    # ---- answers --------------------------------------------------------
    def _answers(self, source, **extra):
        probe = self._probe(source, "-show_format", "-show_streams")
        streams = probe["streams"]
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": source,
            "output_location": self._tmp, "output_ext": "mkv",
            "streams": streams, "probe": probe, "format": probe["format"],
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [s for s in streams if s["codec_type"] == "subtitle"],
            "data_streams": [], "attachment_streams": [],
            "audio_tracks": [0], "subtitle_tracks": [0], "keep_source_subtitles": True,
            "video_codec": "H264", "use_gpu": False, "audio_codec": "aac",
            "audio_bitrate_kbps": 96, "resolution": "n", "color_range_choice": "tv",
        }
        answers.update(extra)
        return answers

    def _encode(self, source, **extra):
        answers = self._answers(source, **extra)
        cmd = FFmWiz.build_ffmpeg_command(answers)
        self.addCleanup(FFmWiz.release_artifacts, answers)
        result = _run(cmd)
        self.assertEqual(0, result.returncode, result.stderr[-1200:])
        return Path(answers["output_path"])

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._notes = []
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = self._notes.append
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_origin_case_"))

    def tearDown(self):
        FFmWiz.appio.note = self._real_note
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _cues_and_frames(self, path):
        return self._cues_against_the_picture(path), self._white_spans(path)

    def _assert_span(self, span, start, end, delta=0.06):
        self.assertAlmostEqual(start, span[0], delta=delta, msg=f"start of {span}")
        self.assertAlmostEqual(end, span[1], delta=delta, msg=f"end of {span}")


class ExtractedCuesKeepTheSourceOrigin(OriginFixtures):
    """The defect itself: what comes back out of `extract_subtitle_text`."""

    def _extract(self, source, origin=None):
        destination = self._tmp / "out.srt"
        text = FFmWiz.extract_subtitle_text(FFMPEG, source, 0, destination, "Test",
                                            origin, FFPROBE)
        return FFmWiz.parse_srt(text)

    def test_the_fixture_really_separates_the_two_clocks(self):
        # Guard the guard. If this ever stops holding, every number below is
        # measuring nothing.
        self.assertAlmostEqual(-CONTAINER_LEAD, self._container_start(self.primed), delta=0.05)
        self.assertAlmostEqual(0.0, self._video_start(self.primed), delta=0.01)
        self.assertAlmostEqual(EARLY[0], self._packets(self.primed, "s")[0][0], delta=0.01)

    def test_a_primed_container_does_not_push_the_cues_late(self):
        # Was EARLY[0] + CONTAINER_LEAD = 0.700.
        cues = self._extract(self.primed, 0.0)
        self.assertEqual(2, len(cues), cues)
        self._assert_span(cues[0], *EARLY[:2], delta=0.005)
        self._assert_span(cues[1], *LATE[:2], delta=0.005)

    def test_the_origin_is_found_from_the_file_when_none_is_given(self):
        # The joined-subtitle builder extracts without an origin in hand.
        self._assert_span(self._extract(self.primed)[0], *EARLY[:2], delta=0.005)

    def test_a_non_zero_video_start_is_removed_too(self):
        # Every stream starts at 1.0 here, so a cue whose packet sits at 1.200
        # is 0.200 into the picture.
        self.assertAlmostEqual(EARLY[0] + 1.0, self._packets(self.offset, "s")[0][0],
                               delta=0.01)
        self._assert_span(self._extract(self.offset)[0], *EARLY[:2], delta=0.005)

    def test_the_seek_clock_is_still_available_for_a_cut(self):
        # `-ss` counts from the container start, so asking for that clock has to
        # keep giving the demuxer's own answer.
        cues = self._extract(self.primed, -CONTAINER_LEAD)
        self._assert_span(cues[0], EARLY[0] + CONTAINER_LEAD, EARLY[1] + CONTAINER_LEAD,
                          delta=0.005)

    def test_an_unreadable_source_is_reported_not_guessed(self):
        self.assertEqual([], self._extract(self._tmp / "missing.mkv"))


class RetimedOutputLandsOnThePicture(OriginFixtures):
    """Speed and reverse, asserted against the flash the cue belongs to."""

    def test_double_speed(self):
        # Was 0.361 / 1.511 on this fixture.
        cues = self._cues_against_the_picture(self._encode(
            self.primed, video_speed_enabled=True, video_speed_factor=2.0,
            audio_speed_from_video=True))
        self.assertEqual(2, len(cues), cues)
        self._assert_span(cues[0], EARLY[0] / 2, EARLY[1] / 2)
        self._assert_span(cues[1], LATE[0] / 2, LATE[1] / 2)

    def test_half_speed_sits_on_the_flash(self):
        # Was 1.444 / 6.044. Half speed doubles the frame density, so the flash
        # is a ruler fine enough to compare against directly.
        output = self._encode(self.primed, video_speed_enabled=True,
                              video_speed_factor=0.5, audio_speed_from_video=True)
        cues = self._cues_against_the_picture(output)
        self._assert_span(cues[0], EARLY[0] * 2, EARLY[1] * 2)
        self._assert_span(cues[1], LATE[0] * 2, LATE[1] * 2)
        for cue, flash in zip(cues, self._white_spans(output)):
            self._assert_span(cue, *flash)

    def test_reverse_sits_on_the_flash(self):
        # Was 2.678 / 0.000 -- the mirrored container lead had pushed the first
        # cue clean off the front of the output.
        output = self._encode(self.primed, video_speed_enabled=True, video_speed_factor=1.0,
                              reverse_video=True, audio_speed_from_video=True)
        cues = self._cues_against_the_picture(output)
        self.assertEqual(2, len(cues), cues)
        self._assert_span(cues[0], SOURCE_SECONDS - LATE[1], SOURCE_SECONDS - LATE[0])
        self._assert_span(cues[1], SOURCE_SECONDS - EARLY[1], SOURCE_SECONDS - EARLY[0])
        for cue, flash in zip(cues, self._white_spans(output)):
            self._assert_span(cue, *flash)

    def test_a_split_with_a_speed_change_slices_the_corrected_track(self):
        # The Split parts are cut out of the retimed whole-timeline track, so a
        # drifting origin reached them too: part 1 used to carry 0.361-0.661.
        answers = self._answers(self.primed, separator_points=[1.0],
                                video_speed_enabled=True, video_speed_factor=2.0,
                                audio_speed_from_video=True)
        self.addCleanup(FFmWiz.release_artifacts, answers)
        cmd = FFmWiz.build_ffmpeg_command(answers)
        self.assertEqual(0, _run(cmd).returncode)
        parts = sorted(self._tmp.glob("*_Part*.mkv"))
        self.assertEqual(2, len(parts), parts)
        self._assert_span(self._cues_against_the_picture(parts[0])[0],
                          EARLY[0] / 2, EARLY[1] / 2)

    def test_an_ordinary_clock_is_left_exactly_where_it_was(self):
        # `offset.mkv` starts at 1.0 in every stream, so the fix must be a
        # no-op on it: this is the case a wrong origin would break.
        cues = self._cues_against_the_picture(self._encode(
            self.offset, video_speed_enabled=True, video_speed_factor=2.0,
            audio_speed_from_video=True))
        self._assert_span(cues[0], EARLY[0] / 2, EARLY[1] / 2)
        self._assert_span(cues[1], LATE[0] / 2, LATE[1] / 2)


class EveryEditKeepsThePictureClock(OriginFixtures):
    """One clock for the seek, the trim ranges, the cues and the frames.

    `offset` starts at 1.0 on every stream, so its two clocks agree and nothing
    here may move on it. `primed` starts its audio CONTAINER_LEAD before its
    picture, which is where the seek and the trim ranges used to be read on the
    wrong one. Each cue is checked against the white frames it is supposed to
    sit on, not against a number: an offset applied to only one of the two
    would satisfy a cue assertion on its own.
    """


    def test_a_single_range_cut(self):
        cues = self._cues_against_the_picture(
            self._encode(self.offset, cut_keep_ranges=[(2.0, 4.0)]))
        self.assertEqual(1, len(cues), cues)
        self._assert_span(cues[0], LATE[0] - 2.0, LATE[1] - 2.0)

    def test_a_multi_range_cut(self):
        cues = self._cues_against_the_picture(self._encode(
            self.offset, cut_keep_ranges=[(0.0, 1.0), (3.0, 4.0)]))
        self.assertEqual(2, len(cues), cues)
        self._assert_span(cues[0], *EARLY[:2])
        self._assert_span(cues[1], 1.0, 1.0 + (LATE[1] - 3.0))

    def test_a_plain_split(self):
        answers = self._answers(self.offset, separator_points=[2.0])
        self.addCleanup(FFmWiz.release_artifacts, answers)
        self.assertEqual(0, _run(FFmWiz.build_ffmpeg_command(answers)).returncode)
        parts = sorted(self._tmp.glob("*_Part*.mkv"))
        self.assertEqual(2, len(parts), parts)
        self._assert_span(self._cues_against_the_picture(parts[0])[0], *EARLY[:2])
        self._assert_span(self._cues_against_the_picture(parts[1])[0],
                          LATE[0] - 2.0, LATE[1] - 2.0)

    # ---- the primed source: where the two clocks used to be read apart ---
    def test_a_single_cut_on_a_primed_container(self):
        # Was 1.0-2.0, one CONTAINER_LEAD late, with the white frames late too:
        # `-ss 2.0` landed at picture 1.5 because the seek counted from the
        # container. The cue AND the frames have to move together.
        cues, frames = self._cues_and_frames(
            self._encode(self.primed, cut_keep_ranges=[(2.0, 4.0)]))
        self.assertEqual(1, len(cues), cues)
        self._assert_span(cues[0], LATE[0] - 2.0, LATE[1] - 2.0)
        self.assertEqual(1, len(frames), frames)
        self._assert_span(frames[0], LATE[0] - 2.0, LATE[1] - 2.0, delta=0.12)

    def test_a_multi_range_cut_on_a_primed_container(self):
        # The trim path, not the seek path: `trim` reads the demuxer's already
        # rebased frames, so it needed the same offset for a different reason.
        cues, frames = self._cues_and_frames(self._encode(
            self.primed, cut_keep_ranges=[(0.0, 1.0), (3.0, 4.0)]))
        self.assertEqual(2, len(cues), cues)
        self._assert_span(cues[0], *EARLY)
        self._assert_span(cues[1], 1.0, 1.0 + (LATE[1] - 3.0))
        self.assertEqual(2, len(frames), frames)
        self._assert_span(frames[0], EARLY[0], EARLY[1], delta=0.12)
        self._assert_span(frames[1], 1.0, 1.0 + (LATE[1] - 3.0), delta=0.12)

    def test_a_split_of_a_primed_container(self):
        answers = self._answers(self.primed, separator_points=[2.0])
        self.addCleanup(FFmWiz.release_artifacts, answers)
        self.assertEqual(0, _run(FFmWiz.build_ffmpeg_command(answers)).returncode)
        parts = sorted(self._tmp.glob("*_Part*.mkv"))
        self.assertEqual(2, len(parts), parts)
        self._assert_span(self._cues_against_the_picture(parts[0])[0], *EARLY)
        self._assert_span(self._cues_against_the_picture(parts[1])[0],
                          LATE[0] - 2.0, LATE[1] - 2.0)

    def test_a_cut_then_slow_motion_on_a_primed_container(self):
        # 0.5x, not 2x, and deliberately: slowing down MULTIPLIES an origin
        # error, so the half-second the seek used to lose would arrive here as
        # a full second. It is the strongest of these for that reason.
        cues, frames = self._cues_and_frames(self._encode(
            self.primed, cut_keep_ranges=[(2.0, 4.0)],
            video_speed_enabled=True, video_speed_factor=0.5,
            audio_speed_from_video=True))
        self.assertEqual(1, len(cues), cues)
        self._assert_span(cues[0], (LATE[0] - 2.0) * 2, (LATE[1] - 2.0) * 2)
        self._assert_span(frames[0], (LATE[0] - 2.0) * 2, (LATE[1] - 2.0) * 2,
                          delta=0.12)

    def test_speeding_up_keeps_the_cue_on_the_picture_clock(self):
        # The cue is what this defect governs, and it is exact.
        #
        # The PICTURE is not asserted at 2x, and not because it agrees: it does
        # not. Measured on `offset`, whose two clocks agree, with no cut at all
        # -- so neither the seek nor the trim is involved:
        #
        #     1x     cues (0.2, 0.8) (2.5, 3.5)   frames identical
        #     0.5x   cues (0.4, 1.6) (5.0, 7.0)   frames identical
        #     2x     cues (0.1, 0.4) (1.25, 1.75) frames (0.2, 0.6) (1.4, 1.9)
        #
        # Speeding up leaves the output frame rate at the SOURCE rate, so a
        # graph now producing twice the frames is quantised back onto the old
        # grid: the bands shift late and widen. That is a frame-rate decision
        # on the encode side, reproducible without any clock disagreement, and
        # a separate defect from this one. Asserting it here either way would
        # tie this regression to an unrelated repair.
        cues = self._cues_against_the_picture(self._encode(
            self.primed, cut_keep_ranges=[(2.0, 4.0)],
            video_speed_enabled=True, video_speed_factor=2.0,
            audio_speed_from_video=True))
        self.assertEqual(1, len(cues), cues)
        self._assert_span(cues[0], (LATE[0] - 2.0) / 2, (LATE[1] - 2.0) / 2)

    def test_a_cut_then_reverse_on_a_primed_container(self):
        # Mirrored inside the kept window, so the cue lands at
        # window - end .. window - start. A seek that started early would
        # mirror around the wrong window and move it the other way.
        cues, frames = self._cues_and_frames(self._encode(
            self.primed, cut_keep_ranges=[(2.0, 4.0)], reverse_video=True))
        self.assertEqual(1, len(cues), cues)
        self._assert_span(cues[0], 2.0 - (LATE[1] - 2.0), 2.0 - (LATE[0] - 2.0))
        self._assert_span(frames[0], 2.0 - (LATE[1] - 2.0), 2.0 - (LATE[0] - 2.0),
                          delta=0.16)

    def test_the_two_clocks_still_disagree_on_this_fixture(self):
        # Guard the guard: on a source whose clocks agreed, every assertion in
        # this class would pass with the offset removed.
        self.assertAlmostEqual(CONTAINER_LEAD, FFmWiz.picture_clock_offset(
            self._answers(self.primed)), delta=0.05)
        self.assertEqual(0.0, FFmWiz.picture_clock_offset(self._answers(self.offset)))


class JoinedSubtitlesUseTheSameOrigin(OriginFixtures):
    """A join shifts each input's cues by everything before it, so a per-input
    origin error lands in the merged track and grows with the input count."""

    def _joined_track(self, first, second):
        answers = self._answers(first)
        self.addCleanup(FFmWiz.release_artifacts, answers)
        second_probe = self._probe(second, "-show_format", "-show_streams")
        item = {
            "path": second, "probe": second_probe, "format": second_probe["format"],
            "streams": second_probe["streams"],
            "duration": SOURCE_SECONDS,
            "subtitle_streams": [s for s in second_probe["streams"]
                                 if s["codec_type"] == "subtitle"],
        }
        built = FFmWiz.build_joined_subtitle_files(answers, [
            {**item, "path": first,
             "streams": answers["streams"], "format": answers["format"]},
            item,
        ])
        self.assertEqual(1, len(built), built)
        return FFmWiz.parse_srt(Path(built[0]["path"]).read_text(encoding="utf-8"))

    def test_a_primed_input_does_not_push_the_merged_cues_late(self):
        # Both inputs used to arrive CONTAINER_LEAD late, so the second input's
        # cues landed at 4.700 instead of 4.200.
        cues = self._joined_track(self.primed, self.primed)
        self.assertEqual(4, len(cues), cues)
        self._assert_span(cues[0], *EARLY[:2], delta=0.005)
        self._assert_span(cues[2], EARLY[0] + SOURCE_SECONDS, EARLY[1] + SOURCE_SECONDS,
                          delta=0.005)

    def test_a_joined_encode_puts_its_cues_on_its_own_frames(self):
        # The merged arithmetic above is checked against numbers; this checks
        # it against the picture the join actually produces. Two primed inputs,
        # so a per-input origin error would land twice and grow.
        answers = self._answers(self.primed)
        self.addCleanup(FFmWiz.release_artifacts, answers)
        probe = self._probe(self.primed, "-show_format", "-show_streams")
        item = {
            "path": self.primed, "probe": probe, "format": probe["format"],
            "streams": probe["streams"], "duration": SOURCE_SECONDS,
            "video_streams": [s for s in probe["streams"] if s["codec_type"] == "video"],
            "audio_streams": [s for s in probe["streams"] if s["codec_type"] == "audio"],
            "subtitle_streams": [s for s in probe["streams"] if s["codec_type"] == "subtitle"],
            "attachment_streams": [], "data_streams": [],
        }
        answers["join_input_items"] = [item]
        cmd = FFmWiz.build_join_encode_command(
            answers, [dict(item), item], self._tmp / "joined.mkv")
        result = _run(cmd)
        self.assertEqual(0, result.returncode, result.stderr[-1200:])
        joined = Path(answers["output_path"])
        cues, frames = self._cues_and_frames(joined)
        expected = [EARLY[:2], LATE[:2],
                    (EARLY[0] + SOURCE_SECONDS, EARLY[1] + SOURCE_SECONDS),
                    (LATE[0] + SOURCE_SECONDS, LATE[1] + SOURCE_SECONDS)]
        self.assertEqual(len(expected), len(cues), cues)
        self.assertEqual(len(expected), len(frames), frames)
        for index, (start, end) in enumerate(expected):
            self._assert_span(cues[index], start, end, delta=0.08)
            self._assert_span(frames[index], start, end, delta=0.14)

    def test_a_non_zero_video_start_joins_unchanged(self):
        cues = self._joined_track(self.offset, self.offset)
        self._assert_span(cues[0], *EARLY[:2], delta=0.005)
        self._assert_span(cues[2], EARLY[0] + SOURCE_SECONDS, EARLY[1] + SOURCE_SECONDS,
                          delta=0.005)


if __name__ == "__main__":
    unittest.main()
