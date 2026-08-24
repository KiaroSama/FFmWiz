"""Regression: speed/reverse/cut must retime ordinary source subtitles (R03).

`build_ffmpeg_command()` mapped the selected subtitle streams straight through
(`-map 0:s:N -c:s copy`) while the video and audio filters rewrote the clock.
Measured on a 4 s source whose SRT cue sits at 2.500-3.500 s, encoded at 2x:

    cue  2.500-3.500  (should be 1.250-1.750)
    container 3.521 s (processed A/V was ~2 s)

Reverse kept the same 2.500-3.500 cue on a timeline that runs backwards.

The repair is one transform -- `TimelineMap` -- shared by cut, speed and
reverse, plus rebuilt SRT tracks fed back as real inputs. These tests extract
the OUTPUT subtitle track and compare cue times NUMERICALLY; a substring check
would pass on the stale timestamps too.

The rebuilt tracks and the remapped-chapter metadata both live in temporary
directories `build_ffmpeg_command` creates, so the artifact-ownership
regressions for that builder (R06) live here too.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts
import cache_test_utils

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

SOURCE_SECONDS = 4.0
# Cue positions chosen so every case below lands on a distinct, checkable value.
EARLY_CUE = (0.200, 0.800, "EARLY")
LATE_CUE = (2.500, 3.500, "HELLO")


def _run(args, timeout=300):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, encoding="utf-8",
                          errors="replace", timeout=timeout)


class TimelineMapMaths(unittest.TestCase):
    """The shared transform, without touching ffmpeg."""

    def test_no_edit_is_the_identity(self):
        timeline = FFmWiz.TimelineMap(source_duration=10.0)
        self.assertTrue(timeline.is_identity)
        self.assertEqual([(2.0, 3.0)], timeline.map_interval(2.0, 3.0))
        self.assertEqual(10.0, timeline.output_duration)

    def test_speed_divides_the_clock(self):
        timeline = FFmWiz.TimelineMap(source_duration=4.0, speed=2.0)
        self.assertEqual([(1.25, 1.75)], timeline.map_interval(2.5, 3.5))
        self.assertEqual(2.0, timeline.output_duration)

    def test_slow_motion_stretches_it(self):
        timeline = FFmWiz.TimelineMap(source_duration=4.0, speed=0.5)
        self.assertEqual([(5.0, 7.0)], timeline.map_interval(2.5, 3.5))
        self.assertEqual(8.0, timeline.output_duration)

    def test_reverse_mirrors_the_retained_clock(self):
        timeline = FFmWiz.TimelineMap(source_duration=4.0, reverse=True)
        self.assertEqual([(0.5, 1.5)], timeline.map_interval(2.5, 3.5))

    def test_reverse_is_applied_before_speed(self):
        # setpts=(PTS-STARTPTS)/speed runs AFTER the reverse filter, so mirroring
        # on the sped-up clock would land the cue in the wrong place.
        timeline = FFmWiz.TimelineMap(source_duration=4.0, speed=2.0, reverse=True)
        self.assertEqual([(0.25, 0.75)], timeline.map_interval(2.5, 3.5))

    def test_a_cut_collapses_the_removed_time(self):
        # The 1 s hole between the kept ranges disappears, so source 3.5 lands
        # at 1.5: one second of kept material precedes it.
        timeline = FFmWiz.TimelineMap([(0.0, 1.0), (3.0, 4.0)], source_duration=4.0)
        self.assertEqual([(1.5, 2.0)], timeline.map_interval(3.5, 4.0))
        self.assertEqual(2.0, timeline.output_duration)

    def test_a_cue_spanning_a_removed_range_survives_as_two_pieces(self):
        # Stretching it across the hole would put words over the wrong picture.
        timeline = FFmWiz.TimelineMap([(0.0, 1.0), (3.0, 4.0)], source_duration=4.0)
        self.assertEqual([(0.5, 1.0), (1.0, 1.5)], timeline.map_interval(0.5, 3.5))

    def test_a_cue_inside_a_removed_range_disappears(self):
        timeline = FFmWiz.TimelineMap([(0.0, 1.0), (3.0, 4.0)], source_duration=4.0)
        self.assertEqual([], timeline.map_interval(1.5, 2.5))

    def test_cut_then_reverse_then_speed_compose(self):
        timeline = FFmWiz.TimelineMap([(0.0, 1.0), (3.0, 4.0)], source_duration=4.0,
                                      speed=2.0, reverse=True)
        # source 3.5-4.0 -> cut 1.5-2.0 -> reverse 0.0-0.5 -> speed 0.0-0.25
        self.assertEqual([(0.0, 0.25)], timeline.map_interval(3.5, 4.0))

    def test_a_zero_or_bad_speed_falls_back_to_real_time(self):
        # Never divide the timeline by zero because a field arrived empty.
        self.assertEqual(1.0, FFmWiz.TimelineMap(source_duration=1.0, speed=0).speed)
        self.assertEqual(1.0, FFmWiz.TimelineMap(source_duration=1.0, speed="x").speed)

    def test_no_cue_can_outlast_the_processed_output(self):
        timeline = FFmWiz.TimelineMap(source_duration=4.0, speed=2.0)
        for _start, end in timeline.map_interval(0.0, 4.0):
            self.assertLessEqual(end, timeline.output_duration + 1e-9)


class RetimeCues(unittest.TestCase):
    def test_reversing_renumbers_the_track_back_to_front(self):
        cues = [(0.0, 1.0, "first"), (3.0, 4.0, "last")]
        timeline = FFmWiz.TimelineMap(source_duration=4.0, reverse=True)
        rendered = FFmWiz.render_srt(FFmWiz.retime_cues(cues, timeline))
        self.assertLess(rendered.index("last"), rendered.index("first"))

    def test_a_cue_removed_by_a_cut_is_not_rendered(self):
        timeline = FFmWiz.TimelineMap([(0.0, 1.0)], source_duration=4.0)
        self.assertEqual([], FFmWiz.retime_cues([(2.0, 3.0, "gone")], timeline))


@requires_ffmpeg
class RealEncodeBase(NoLeakedArtifacts, unittest.TestCase):
    """Shared fixtures: build the sources once, encode, read the result back."""

    @classmethod
    def setUpClass(cls):
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_retime_"))
        cls._source = cls._build_source(cls._root, "src.mkv", [EARLY_CUE, LATE_CUE])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    @classmethod
    def _build_source(cls, root, name, cues):
        srt = root / (name + ".srt")
        srt.write_text(
            "".join(f"{index}\n{FFmWiz.srt_timestamp(start)} --> {FFmWiz.srt_timestamp(end)}\n{body}\n\n"
                    for index, (start, end, body) in enumerate(cues, start=1)),
            encoding="utf-8")
        path = root / name
        result = _run([
            FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=10:duration={SOURCE_SECONDS}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={SOURCE_SECONDS}",
            "-i", str(srt),
            "-map", "0:v", "-map", "1:a", "-map", "2:s",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-c:s", "srt",
            "-metadata:s:s:0", "language=spa", "-metadata:s:s:0", "title=Comentario",
            "-disposition:s:0", "default", str(path)])
        if result.returncode != 0:
            raise unittest.SkipTest("could not build the synthetic source: "
                                    + result.stderr[-400:])
        return path

    def setUp(self):
        super().setUp()
        FFmWiz.appio.USE_COLOR = False
        self._notes = []
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = self._notes.append
        self._prev_cache = os.environ.get("FFMWIZ_CACHE_DIR")
        self._cache_run_id = uuid.uuid4().hex
        self._cache_dir = cache_test_utils.create_owned_temp_cache_dir(self._cache_run_id)
        os.environ["FFMWIZ_CACHE_DIR"] = self._cache_dir
        FFmWiz.services._CAPABILITY_SESSION_MEMO.clear()
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_retime_case_"))

    def tearDown(self):
        FFmWiz.appio.note = self._real_note
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._prev_cache is None:
            os.environ.pop("FFMWIZ_CACHE_DIR", None)
        else:
            os.environ["FFMWIZ_CACHE_DIR"] = self._prev_cache
        FFmWiz.services._CAPABILITY_SESSION_MEMO.clear()
        cache_test_utils.safe_remove_owned_temp_dir(
            self._cache_dir, self._cache_run_id, tempfile.gettempdir())

    # ---- fixtures -------------------------------------------------------
    def _probe(self, path):
        result = _run([FFPROBE, "-v", "error", "-print_format", "json",
                       "-show_format", "-show_streams", str(path)])
        return json.loads(result.stdout or "{}")

    def _answers(self, **extra):
        # self.own(): every subclass reaches the builder through here, so one
        # call covers all of them. Without it these direct-builder tests left
        # the leased retimed-subtitle and chapter directories in %TEMP% (F13).
        source = self._source
        probe = self._probe(source)
        streams = probe["streams"]
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
            "input_path": source, "output_location": self._tmp,
            "output_ext": "mkv", "streams": streams, "probe": probe,
            "format": probe["format"],
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [s for s in streams if s["codec_type"] == "subtitle"],
            "data_streams": [], "attachment_streams": [],
            "audio_tracks": [0], "subtitle_tracks": [0],
            "keep_source_subtitles": True,
            "video_codec": "H264", "use_gpu": False,
            "audio_codec": "aac", "audio_bitrate_kbps": 96,
            "resolution": "n", "color_range_choice": "tv",
        }
        answers.update(extra)
        return self.own(answers)

    def _encode(self, **extra):
        """Build the real command, run it, and read the result back."""
        answers = self._answers(**extra)
        cmd = FFmWiz.build_ffmpeg_command(answers)
        result = _run(cmd)
        self.assertEqual(0, result.returncode,
                         f"ffmpeg failed:\n{result.stderr[-1500:]}\n{' '.join(str(c) for c in cmd)}")
        output = Path(answers["output_path"])
        self.addCleanup(FFmWiz.release_artifacts, answers)
        return answers, output

    def _output_subtitles(self, path):
        """Extract every output subtitle track and parse its cues."""
        probe = self._probe(path)
        tracks = []
        for position, stream in enumerate(
                [s for s in probe["streams"] if s["codec_type"] == "subtitle"]):
            destination = self._tmp / f"extracted{position}.srt"
            if destination.exists():
                destination.unlink()
            _run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                  "-i", str(path), "-map", f"0:s:{position}", "-c:s", "srt",
                  str(destination)])
            text = destination.read_text(encoding="utf-8", errors="replace") if destination.exists() else ""
            tracks.append({"stream": stream, "cues": FFmWiz.parse_srt(text)})
        return tracks

    def _container_seconds(self, path):
        return float(self._probe(path)["format"]["duration"])

    def _assert_cue(self, cue, start, end, body):
        self.assertAlmostEqual(start, cue[0], delta=0.06, msg=f"start of {cue}")
        self.assertAlmostEqual(end, cue[1], delta=0.06, msg=f"end of {cue}")
        self.assertEqual(body, cue[2])


class RetimedSubtitlesInRealOutput(RealEncodeBase):
    """The mandatory speed/reverse/cut cases, asserted numerically."""

    def test_double_speed_halves_every_cue_time(self):
        _answers, output = self._encode(video_speed_enabled=True, video_speed_factor=2.0,
                                        audio_speed_from_video=True)
        cues = self._output_subtitles(output)[0]["cues"]
        self.assertEqual(2, len(cues), cues)
        self._assert_cue(cues[0], 0.100, 0.400, "EARLY")
        self._assert_cue(cues[1], 1.250, 1.750, "HELLO")

    def test_double_speed_container_follows_the_processed_av(self):
        # The stale 3.5 s subtitle packet used to keep the container open long
        # after the picture and sound had finished.
        _answers, output = self._encode(video_speed_enabled=True, video_speed_factor=2.0,
                                        audio_speed_from_video=True)
        self.assertAlmostEqual(SOURCE_SECONDS / 2.0, self._container_seconds(output),
                               delta=0.25)

    def test_half_speed_stretches_every_cue_time(self):
        _answers, output = self._encode(video_speed_enabled=True, video_speed_factor=0.5,
                                        audio_speed_from_video=True)
        cues = self._output_subtitles(output)[0]["cues"]
        self._assert_cue(cues[0], 0.400, 1.600, "EARLY")
        self._assert_cue(cues[1], 5.000, 7.000, "HELLO")
        self.assertAlmostEqual(SOURCE_SECONDS * 2.0, self._container_seconds(output),
                               delta=0.35)

    def test_reverse_mirrors_the_cues_and_their_order(self):
        _answers, output = self._encode(video_speed_enabled=True, video_speed_factor=1.0,
                                        reverse_video=True, audio_speed_from_video=True)
        cues = self._output_subtitles(output)[0]["cues"]
        self.assertEqual(2, len(cues), cues)
        # The last cue of the source is the first cue of a reversed output.
        self._assert_cue(cues[0], 0.500, 1.500, "HELLO")
        self._assert_cue(cues[1], 3.200, 3.800, "EARLY")

    def test_reverse_with_speed_composes_both(self):
        _answers, output = self._encode(video_speed_enabled=True, video_speed_factor=2.0,
                                        reverse_video=True, audio_speed_from_video=True)
        cues = self._output_subtitles(output)[0]["cues"]
        self._assert_cue(cues[0], 0.250, 0.750, "HELLO")
        self._assert_cue(cues[1], 1.600, 1.900, "EARLY")
        self.assertAlmostEqual(SOURCE_SECONDS / 2.0, self._container_seconds(output),
                               delta=0.25)

    def test_multi_range_cut_with_speed_keeps_only_the_retained_cues(self):
        # Multi-range cuts used to drop subtitles outright with a note.
        _answers, output = self._encode(
            cut_keep_ranges=[(0.0, 1.0), (3.0, 4.0)],
            video_speed_enabled=True, video_speed_factor=2.0, audio_speed_from_video=True)
        cues = self._output_subtitles(output)[0]["cues"]
        self.assertEqual(2, len(cues), cues)
        self._assert_cue(cues[0], 0.100, 0.400, "EARLY")
        # 2.500-3.500 clipped to the kept 3.0-4.0 range, then halved.
        self._assert_cue(cues[1], 0.500, 0.750, "HELLO")
        self.assertAlmostEqual(1.0, self._container_seconds(output), delta=0.25)

    def test_mp4_transcodes_the_rebuilt_track_and_keeps_the_new_times(self):
        _answers, output = self._encode(output_ext="mp4", video_speed_enabled=True,
                                        video_speed_factor=2.0, audio_speed_from_video=True)
        cues = self._output_subtitles(output)[0]["cues"]
        self._assert_cue(cues[1], 1.250, 1.750, "HELLO")

    def test_a_container_that_cannot_carry_text_drops_them_instead_of_failing(self):
        # Mapping a stream the muxer refuses kills the whole output at
        # header-write time, which is worse than losing the subtitles.
        answers = self._answers(output_ext="avi", video_speed_enabled=True,
                                video_speed_factor=2.0, audio_speed_from_video=True)
        cmd = FFmWiz.build_ffmpeg_command(answers)
        self.addCleanup(FFmWiz.release_artifacts, answers)
        self.assertIn("-sn", cmd)
        self.assertNotIn("-c:s", cmd)
        result = _run(cmd)
        self.assertEqual(0, result.returncode, result.stderr[-1200:])

    def test_the_rebuilt_track_keeps_language_title_and_disposition(self):
        _answers, output = self._encode(video_speed_enabled=True, video_speed_factor=2.0,
                                        audio_speed_from_video=True)
        stream = self._output_subtitles(output)[0]["stream"]
        tags = {key.lower(): value for key, value in (stream.get("tags") or {}).items()}
        self.assertEqual("spa", tags.get("language"))
        self.assertEqual("Comentario", tags.get("title"))
        self.assertEqual(1, stream.get("disposition", {}).get("default"))

    def test_an_untouched_timeline_still_copies_the_source_track(self):
        # Guard the guard: retiming must not switch on for a plain re-encode.
        _answers, output = self._encode()
        cues = self._output_subtitles(output)[0]["cues"]
        self._assert_cue(cues[1], 2.500, 3.500, "HELLO")

    def test_the_segmented_reverse_executor_lands_on_the_same_cues(self):
        """A long reverse is encoded in chunks and concatenated back to front.

        Each chunk is rebuilt from a shallow copy carrying only that chunk's
        keep range, so the per-chunk retiming has to add up to the same answer
        the single-command reverse gives. Chunked here at 2 s rather than the
        production 60 s so a 4 s source really produces two of them.
        """
        answers = self._answers(video_speed_enabled=True, video_speed_factor=1.0,
                                reverse_video=True, audio_speed_from_video=True)
        self.addCleanup(FFmWiz.release_artifacts, answers)
        answers["output_path"] = self._tmp / "reversed.mkv"
        chunks = FFmWiz.split_ranges_for_reverse_segments(
            [], SOURCE_SECONDS, segment_seconds=2.0)
        self.assertEqual(2, len(chunks), chunks)
        segments = []
        for index, (start, end) in enumerate(chunks, start=1):
            segment = self._tmp / f"seg{index}.mkv"
            cmd = FFmWiz.build_main_encode_reverse_segment_command(
                answers, start, end, segment)
            result = _run(cmd)
            self.assertEqual(0, result.returncode, result.stderr[-1200:])
            segments.append(segment)
        concat_list = self._tmp / "concat.txt"
        FFmWiz.write_concat_list(list(reversed(segments)), concat_list)
        result = _run(FFmWiz.build_concat_copy_command(
            FFMPEG, concat_list, Path(answers["output_path"])))
        self.assertEqual(0, result.returncode, result.stderr[-1200:])
        cues = self._output_subtitles(Path(answers["output_path"]))[0]["cues"]
        self.assertEqual(2, len(cues), cues)
        self._assert_cue(cues[0], 0.500, 1.500, "HELLO")
        self._assert_cue(cues[1], 3.200, 3.800, "EARLY")

    def test_a_plain_trim_rebases_the_cues_too(self):
        # `-ss` before the source input does NOT carry its subtitle packets with
        # the picture: this trim used to shift the cues by 0.2 s instead of 2 s
        # and leave one running 1.3 s past the end of the output.
        _answers, output = self._encode(cut_keep_ranges=[(2.0, 4.0)])
        cues = self._output_subtitles(output)[0]["cues"]
        self.assertEqual(1, len(cues), cues)
        self._assert_cue(cues[0], 0.500, 1.500, "HELLO")
        self.assertAlmostEqual(2.0, self._container_seconds(output), delta=0.25)


class BitmapSubtitlesAreNeverMappedStale(RealEncodeBase):
    """A picture subtitle has no cue times to move, so it can only be dropped --
    and only after the user has been told and has agreed.

    The installed FFmpeg refuses to encode text subtitles into a bitmap codec
    ("only possible from text to text or bitmap to bitmap"), so the fixture
    declares the bitmap codec on the probed stream. That is exactly the field
    the decision reads, and the encode below is still a real one.
    """

    def _bitmap_answers(self, **extra):
        answers = self._answers(**extra)
        answers["subtitle_streams"] = [
            {**answers["subtitle_streams"][0], "codec_name": "hdmv_pgs_subtitle"}]
        return answers

    def test_declining_the_drop_stops_the_build(self):
        answers = self._bitmap_answers(
            video_speed_enabled=True, video_speed_factor=2.0,
            audio_speed_from_video=True, bitmap_subtitle_drop_confirmed=False)
        with self.assertRaises(RuntimeError) as raised:
            FFmWiz.build_ffmpeg_command(answers)
        self.assertIn("bitmap", str(raised.exception).lower())

    def test_the_outcome_is_stated_before_the_question_is_asked(self):
        answers = self._bitmap_answers(
            video_speed_enabled=True, video_speed_factor=2.0, audio_speed_from_video=True)
        asked = []
        real_ask = FFmWiz.appio.ask_yes_no
        FFmWiz.appio.ask_yes_no = lambda prompt, default: asked.append(prompt) or False
        try:
            FFmWiz.confirm_bitmap_subtitle_drop(answers, [(0, "hdmv_pgs_subtitle")])
        finally:
            FFmWiz.appio.ask_yes_no = real_ask
        stated = " ".join(self._notes).lower()
        self.assertIn("cannot be retimed", stated)
        self.assertIn("0:s:0", stated)
        self.assertEqual(1, len(asked), "the drop must be confirmed, not assumed")

    def test_confirming_the_drop_emits_no_stale_subtitle_map(self):
        answers = self._bitmap_answers(
            video_speed_enabled=True, video_speed_factor=2.0,
            audio_speed_from_video=True, bitmap_subtitle_drop_confirmed=True)
        cmd = FFmWiz.build_ffmpeg_command(answers)
        self.addCleanup(FFmWiz.release_artifacts, answers)
        self.assertNotIn("0:s:0", cmd)
        self.assertIn("-sn", cmd)

    def test_the_dropped_bitmap_output_really_has_no_subtitle_stream(self):
        answers = self._bitmap_answers(
            video_speed_enabled=True, video_speed_factor=2.0,
            audio_speed_from_video=True, bitmap_subtitle_drop_confirmed=True)
        cmd = FFmWiz.build_ffmpeg_command(answers)
        self.addCleanup(FFmWiz.release_artifacts, answers)
        result = _run(cmd)
        self.assertEqual(0, result.returncode, result.stderr[-1200:])
        self.assertEqual([], self._output_subtitles(Path(answers["output_path"])))

    def test_an_unedited_timeline_still_carries_the_bitmap_track(self):
        # Nothing moved, so there is nothing to confirm and nothing to drop.
        answers = self._bitmap_answers()
        self.assertFalse(FFmWiz.encode_subtitle_retiming_required(answers))
        cmd = FFmWiz.build_ffmpeg_command(answers)
        self.addCleanup(FFmWiz.release_artifacts, answers)
        self.assertIn("0:s:0", cmd)


@requires_ffmpeg
class EncodeTemporaryArtifactsAreOwned(RealEncodeBase):
    """Nothing `build_ffmpeg_command` writes to %TEMP% may outlive the job (R06).

    Split rebuilds every part from a SHALLOW COPY of answers, and a cleanup path
    recorded as a KEY on that copy never reaches the executor, which pops from
    the original -- so both chapter-metadata directories stayed behind after a
    real two-part Split run. The lease fixes the class: `dict()` copies the key
    but shares the object.
    """

    def setUp(self):
        super().setUp()
        # A private tempfile root: scanning the SHARED %TEMP% for ffmwiz_* is not
        # parallel-safe, another worker's directory would look like this leak.
        self._saved_tempdir = tempfile.tempdir
        self._temp_root = self._tmp / "temproot"
        self._temp_root.mkdir()
        tempfile.tempdir = str(self._temp_root)

    def tearDown(self):
        tempfile.tempdir = self._saved_tempdir
        super().tearDown()

    def _owned_temp_paths(self):
        return set(self._temp_root.glob("ffmwiz_*"))

    def _chaptered_answers(self, **extra):
        answers = self._answers(**extra)
        answers["probe"] = {
            **answers["probe"],
            "chapters": [
                {"time_base": "1/1000", "start": 0, "end": 2000,
                 "start_time": "0.000000", "end_time": "2.000000",
                 "tags": {"title": "One"}},
                {"time_base": "1/1000", "start": 2000, "end": 4000,
                 "start_time": "2.000000", "end_time": "4.000000",
                 "tags": {"title": "Two"}},
            ],
        }
        answers["keep_source_chapters"] = True
        return answers

    def _split_build(self):
        """A real Split-with-chapters build, then the per-part rebuild the
        executor performs from a shallow copy."""
        answers = self._chaptered_answers(separator_points=[2.0])
        FFmWiz.build_ffmpeg_command(answers)
        duration = float(answers["format"]["duration"])
        for segment in FFmWiz.separator_ranges(answers["separator_points"], duration):
            part = dict(answers)
            part["cut_keep_ranges"] = [segment]
            part.pop("separator_points", None)
            part.pop("output_path", None)
            part.pop("_chapter_metadata_temp_dir", None)
            FFmWiz.build_ffmpeg_command(part)
        return answers

    def test_the_split_build_really_creates_temp_directories(self):
        # Guard the guard: if the build stopped creating any, the leak test
        # below would pass for the wrong reason.
        before = self._owned_temp_paths()
        answers = self._split_build()
        self.addCleanup(FFmWiz.release_artifacts, answers)
        self.assertTrue(self._owned_temp_paths() - before,
                        "the Split build created no temporary directory")

    def test_the_outer_answers_owns_every_part_directory(self):
        # The precise thing that was broken: ownership must be visible from the
        # dict the executor holds, not only from each part's private copy.
        before = self._owned_temp_paths()
        answers = self._split_build()
        self.addCleanup(FFmWiz.release_artifacts, answers)
        owned = {Path(path) for path in FFmWiz.artifact_lease(answers)}
        self.assertEqual(self._owned_temp_paths() - before, owned)

    def test_release_leaves_nothing_behind_after_a_split_build(self):
        before = self._owned_temp_paths()
        answers = self._split_build()
        FFmWiz.release_artifacts(answers)
        self.assertEqual(set(), self._owned_temp_paths() - before,
                         "a temporary artifact outlived the workflow")

    def test_a_part_that_is_the_first_to_need_a_directory_is_still_owned(self):
        # The exact trap: a directory first registered by a per-part SHALLOW
        # COPY of answers. Without a lease opened before the copies, each copy
        # gets its own and everything it registers leaks.
        before = self._owned_temp_paths()
        answers = self._answers(separator_points=[2.0], keep_source_chapters=False)
        FFmWiz.build_ffmpeg_command(answers)
        # The outer build slices the subtitles per part, so it registers
        # directories of its own; the parts must add to the SAME lease.
        outer_created = self._owned_temp_paths() - before
        for segment in FFmWiz.separator_ranges([2.0], float(answers["format"]["duration"])):
            part = dict(answers)
            part["cut_keep_ranges"] = [segment]
            part.pop("separator_points", None)
            part.pop("output_path", None)
            FFmWiz.build_ffmpeg_command(part)
        self.assertTrue(self._owned_temp_paths() - before - outer_created,
                        "no part directory was created beyond the outer build's")
        FFmWiz.release_artifacts(answers)
        self.assertEqual(set(), self._owned_temp_paths() - before,
                         "a part's temporary directory outlived the outer job")

    def test_the_outer_dict_holds_the_lease_before_any_part_is_copied(self):
        # The guard itself, not a leak symptom. Every registrant today happens
        # to run before the per-part copies, so removing the outer build's
        # early `artifact_lease(answers)` leaks nothing yet -- and a leak test
        # alone would let that line be deleted. A job that registers nothing
        # must STILL leave the container on the outer dict, so whichever copy
        # is first to need it shares this one instead of making its own.
        answers = self._answers(separator_points=[2.0], keep_source_chapters=False,
                                subtitle_streams=[], subtitle_tracks=[])
        FFmWiz.build_ffmpeg_command(answers)
        self.addCleanup(FFmWiz.release_artifacts, answers)
        self.assertIn(
            FFmWiz.ARTIFACT_LEASE_KEY, answers,
            "the outer build must open the lease before any shallow copy")
        self.assertEqual([], list(FFmWiz.artifact_lease(answers)),
                         "this fixture was meant to register nothing itself")

    def test_a_retimed_subtitle_directory_is_owned_too(self):
        before = self._owned_temp_paths()
        answers = self._answers(video_speed_enabled=True, video_speed_factor=2.0,
                                audio_speed_from_video=True)
        FFmWiz.build_ffmpeg_command(answers)
        created = self._owned_temp_paths() - before
        self.assertTrue(any("retimed_subs" in path.name for path in created), created)
        FFmWiz.release_artifacts(answers)
        self.assertEqual(set(), self._owned_temp_paths() - before)

    def test_chapters_keep_input_one_and_subtitles_follow(self):
        # The chapter metadata file must stay at input index 1; the rebuilt
        # subtitle tracks are appended after it.
        answers = self._chaptered_answers(video_speed_enabled=True, video_speed_factor=2.0,
                                          audio_speed_from_video=True)
        cmd = FFmWiz.build_ffmpeg_command(answers)
        self.addCleanup(FFmWiz.release_artifacts, answers)
        self.assertEqual("1", cmd[cmd.index("-map_chapters") + 1])
        self.assertIn("2:s:0", cmd)
        result = _run(cmd)
        self.assertEqual(0, result.returncode, result.stderr[-1200:])
        cues = self._output_subtitles(Path(answers["output_path"]))[0]["cues"]
        self._assert_cue(cues[1], 1.250, 1.750, "HELLO")


if __name__ == "__main__":
    unittest.main()
