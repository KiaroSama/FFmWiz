"""Regression: a join is laid out on each input's PICTURE, not its container (B07).

Every join offset -- the cue shift for a merged subtitle track, the joined
`TimelineMap` a cut/speed/reverse is measured against, and the item durations
`join_items_from_answers()` hands the reverse pipeline -- was read out of
`format.duration`. That is the CONTAINER's length, and any stream outliving the
picture inflates it: a trailing subtitle cue, an audio pad, AAC priming. The
`concat` filter splices decoded frames, so none of that adds a single frame to
the joined video.

Measured on the fixtures below, input 1 a 2.000 s picture stretched to a 3.000 s
container by one subtitle cue and input 2 a plain 2.000 s clip:

    joined picture                     4.000 s          (concat, both ways)
    input 1's tail cue      1.500-3.000  ->  1.500-2.000   (clipped to its picture)
    input 2's cue           3.500-4.500  ->  2.500-3.500   (0.500 past the end -> on the flash)
    joined_timeline_map     6.000 s      ->  4.000 s

A second, independent source of the same error: Matroska writes a per-stream
`DURATION` tag that is an END timestamp, not a length. A 2.000 s picture remuxed
with `-output_ts_offset 1.0` tags `DURATION=00:00:03.000000000` alongside
`start_time=1.000`, so reading the tag as a length made that input a full second
too long everywhere `video_stream_span_seconds()` is used -- the joined offsets
here and the mirror axis `encode_timeline_map()` reverses around.

The cue numbers are compared against the WHITE FLASH each cue belongs to, taken
from the finished file, so a merged track that is internally consistent but
wrong against the picture cannot pass.
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

import FFmWiz
# `joined_timeline_map` is not on the facade; the facade import above has to
# come first, because this module back-imports `wizard`.
from ffmwiz import wizard_build_b

from artifact_guard import NoLeakedArtifacts

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")

PICTURE = 2.0        # every fixture's real picture length
TAIL = 3.0           # how far the trailing subtitle/audio stretches the container
FLASH = (0.5, 1.5)   # the white flash, on each input's own picture clock


def _run(args, timeout=600):
    return subprocess.run([str(part) for part in args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                          timeout=timeout)


class PictureSpanArithmetic(unittest.TestCase):
    """The two clock decisions, without touching ffmpeg."""

    def test_a_matroska_duration_tag_is_an_end_timestamp(self):
        # start 1.000 + 2.000 s of picture is tagged DURATION 3.000.
        stream = {"start_time": "1.000000", "tags": {"DURATION": "00:00:03.000000000"}}
        self.assertEqual(2.0, FFmWiz.video_stream_span_seconds(stream, {"duration": "3.0"}))

    def test_an_mp4_track_duration_is_already_a_length(self):
        # MP4/MOV report the track's own length beside a non-zero start.
        stream = {"start_time": "1.000000", "duration": "2.000000"}
        self.assertEqual(2.0, FFmWiz.video_stream_span_seconds(stream, {"duration": "2.0"}))

    def test_the_container_is_only_the_last_resort(self):
        stream = {"tags": {"DURATION": "00:00:02.000000000"}}
        self.assertEqual(2.0, FFmWiz.join_item_picture_span(
            {"video_streams": [stream], "format": {"duration": "3.0"}, "duration": 3.0}))

    def test_an_audio_only_item_keeps_its_own_duration(self):
        # No picture to measure, so the container IS the program length.
        self.assertEqual(3.0, FFmWiz.join_item_picture_span(
            {"video_streams": [], "format": {"duration": "3.0"}, "duration": 3.0}))

    def test_a_video_stream_hidden_in_streams_is_still_found(self):
        item = {"streams": [{"codec_type": "video",
                             "tags": {"DURATION": "00:00:02.000000000"}}],
                "format": {"duration": "3.0"}, "duration": 3.0}
        self.assertEqual(2.0, FFmWiz.join_item_picture_span(item))

    def test_audio_shorter_than_the_picture_does_not_shorten_it(self):
        # The policy, stated: the VIDEO decides the program length in both
        # directions. `concat` emits every decoded frame whatever the audio
        # does, and a short audio track is padded rather than truncating the
        # picture -- so only the video stream is measured here.
        item = {"video_streams": [{"tags": {"DURATION": "00:00:02.000000000"}}],
                "audio_streams": [{"duration": "1.000000"}],
                "format": {"duration": "2.0"}, "duration": 1.0}
        self.assertEqual(2.0, FFmWiz.join_item_picture_span(item))

    def test_an_item_that_knows_nothing_reports_nothing(self):
        self.assertEqual(0.0, FFmWiz.join_item_picture_span({}))

    def test_a_variable_frame_rate_tail_uses_the_exact_sources_first(self):
        # frames/fps is the third choice on purpose: it assumes a constant rate,
        # and a VFR tail makes that assumption wrong. A stream that reports its
        # own duration must not be measured by a frame count.
        stream = {"duration": "2.000000", "nb_frames": "40", "avg_frame_rate": "30/1"}
        self.assertEqual(2.0, FFmWiz.video_stream_span_seconds(stream, {"duration": "9.0"}))

    def test_a_frame_count_answers_when_nothing_else_does(self):
        stream = {"nb_frames": "60", "avg_frame_rate": "30/1"}
        self.assertEqual(2.0, FFmWiz.video_stream_span_seconds(stream, {"duration": "3.0"}))


@requires_ffmpeg
class JoinedTimelineFollowsThePicture(NoLeakedArtifacts, unittest.TestCase):
    """Real sources whose container and picture disagree, and by how much.

    `subtitle_tail`  -- 2.000 s picture, one cue running to 3.000 s.
    `audio_tail`     -- 2.000 s picture, 3.000 s of audio.
    `plain`          -- 2.000 s picture, 2.000 s audio, nothing hanging over.
    `offset`         -- 2.000 s picture remuxed to start at 1.000 s, so its
                        Matroska DURATION tag reads 3.000.

    Each picture is black with a white flash over its cue, so a merged cue can
    be compared with the frames it is supposed to sit on.
    """

    @classmethod
    def setUpClass(cls):
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_joinpic_"))
        picture = cls._root / "picture.mkv"
        cls._build(picture, [
            "-f", "lavfi", "-i",
            f"color=c=black:s=160x90:r=25:d={PICTURE},"
            f"drawbox=x=0:y=0:w=160:h=90:color=white:t=fill:"
            f"enable='between(t,{FLASH[0]},{FLASH[1] - 0.02})'",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "5"])
        short_audio = cls._root / "short.mka"
        cls._build(short_audio, ["-f", "lavfi", "-i", f"sine=frequency=440:duration={PICTURE}",
                                 "-c:a", "aac"])
        long_audio = cls._root / "long.mka"
        cls._build(long_audio, ["-f", "lavfi", "-i", f"sine=frequency=440:duration={TAIL}",
                                "-c:a", "aac"])
        on_picture = cls._srt(cls._root / "on_picture.srt", [(FLASH[0], FLASH[1], "CUE")])
        over_the_end = cls._srt(cls._root / "over_the_end.srt",
                                [(FLASH[0], FLASH[1], "CUE"), (1.5, TAIL, "TAIL")])

        cls.plain = cls._root / "plain.mkv"
        cls._build(cls.plain, ["-i", str(picture), "-i", str(short_audio), "-i", str(on_picture),
                               "-map", "0:v", "-map", "1:a", "-map", "2:s", "-c", "copy"])
        cls.subtitle_tail = cls._root / "subtitle_tail.mkv"
        cls._build(cls.subtitle_tail, ["-i", str(picture), "-i", str(short_audio),
                                       "-i", str(over_the_end),
                                       "-map", "0:v", "-map", "1:a", "-map", "2:s", "-c", "copy"])
        cls.audio_tail = cls._root / "audio_tail.mkv"
        cls._build(cls.audio_tail, ["-i", str(picture), "-i", str(long_audio),
                                    "-i", str(on_picture),
                                    "-map", "0:v", "-map", "1:a", "-map", "2:s", "-c", "copy"])
        cls.offset = cls._root / "offset.mkv"
        cls._build(cls.offset, ["-i", str(cls.plain), "-c", "copy",
                                "-output_ts_offset", "1.0", "-avoid_negative_ts", "disabled"])

        # Guard the guards: a fixture whose container did NOT outlive its
        # picture would let every assertion below pass for the wrong reason.
        for name, container in (("subtitle_tail", TAIL), ("audio_tail", TAIL),
                                ("plain", PICTURE), ("offset", PICTURE + 1.0)):
            measured = float(cls._probe(getattr(cls, name), "-show_format")["format"]["duration"])
            if abs(measured - container) > 0.08:
                raise unittest.SkipTest(
                    f"{name} fixture reports a {measured:.3f}s container on this ffmpeg, "
                    f"not the {container:.3f}s these measurements need")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    @classmethod
    def _srt(cls, path, cues):
        path.write_text("".join(
            f"{index}\n{FFmWiz.srt_timestamp(start)} --> {FFmWiz.srt_timestamp(end)}\n{body}\n\n"
            for index, (start, end, body) in enumerate(cues, start=1)),
            encoding="utf-8", newline="\n")
        return path

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

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="joinpic_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *args, **kwargs: None
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", self._real_note))

    # ---- fixtures as the wizard sees them -------------------------------
    def _item(self, path):
        """A join item built the way the wizard builds one: container duration."""
        probe = self._probe(path, "-show_format", "-show_streams")
        streams = probe["streams"]
        return {
            "path": path, "probe": probe, "format": probe["format"], "streams": streams,
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [s for s in streams if s["codec_type"] == "subtitle"],
            "attachment_streams": [], "data_streams": [],
            "duration": float(probe["format"]["duration"]),
        }

    def _answers(self, items, **extra):
        first = items[0]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": first["path"],
            "probe": first["probe"], "format": first["format"], "streams": first["streams"],
            "video_streams": first["video_streams"], "audio_streams": first["audio_streams"],
            "subtitle_streams": first["subtitle_streams"],
            "data_streams": [], "attachment_streams": [],
            "output_location": self._tmp, "output_ext": "mkv",
            "keep_source_subtitles": True, "subtitle_tracks": [0], "audio_tracks": [0],
            "video_codec": "H264", "video_encoder": "libx264", "crf": 28,
            "preset": "ultrafast", "use_gpu": False, "audio_codec": "aac",
            "audio_bitrate_kbps": 96, "resolution": "n",
            # Older FFmpeg builds refuse an unresolved source range, so every
            # real encode in this suite states it.
            "color_range_choice": "tv",
            "join_input_items": items[1:],
        })
        answers.update(extra)
        return answers

    def _merged_cues(self, items, **extra):
        answers = self._answers(items, **extra)
        built = FFmWiz.build_joined_subtitle_files(answers, items)
        self.assertEqual(1, len(built), built)
        return [(round(start, 3), round(end, 3), body) for start, end, body
                in FFmWiz.parse_srt(Path(built[0]["path"]).read_text(encoding="utf-8"))]

    def _encode(self, items, **extra):
        answers = self._answers(items, **extra)
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            cmd = FFmWiz.build_join_encode_command(answers, items, self._tmp / "joined.mkv")
        result = _run([str(part) for part in cmd])
        self.assertEqual(0, result.returncode, result.stderr[-1200:])
        return Path(answers["output_path"])

    # ---- measurement ----------------------------------------------------
    def _white_spans(self, path):
        """Where the flashes really are, from the output's own first frame."""
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "info", "-copyts",
             "-i", str(path), "-fps_mode", "passthrough",
             "-vf", "scale=1:1,format=gray,showinfo", "-f", "rawvideo", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=600)
        times = [float(value) for value in re.findall(
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
        if start is not None:
            spans.append((round(start - origin, 3), round(times[-1] - origin, 3)))
        return spans

    def _picture_seconds(self, path):
        packets = self._probe(path, "-select_streams", "v", "-show_packets")["packets"]
        last = packets[-1]
        return (float(last["pts_time"]) + float(last.get("duration_time") or 0)
                - float(packets[0]["pts_time"]))

    def _assert_close(self, expected, measured, delta=0.06, label=""):
        self.assertAlmostEqual(expected, measured, delta=delta, msg=f"{label}: {measured}")

    # ---- the defect -----------------------------------------------------
    def test_the_fixtures_really_separate_the_two_clocks(self):
        for name in ("subtitle_tail", "audio_tail"):
            item = self._item(getattr(self, name))
            self._assert_close(TAIL, item["duration"], 0.08, f"{name} container")
            self._assert_close(PICTURE, FFmWiz.join_item_picture_span(item), 0.02,
                               f"{name} picture")

    def test_a_subtitle_tail_no_longer_delays_the_next_input(self):
        # Was [(0.5, 1.0 tail-free), (1.5, 3.0), (3.5, 4.5)].
        cues = self._merged_cues([self._item(self.subtitle_tail), self._item(self.plain)])
        self.assertEqual(3, len(cues), cues)
        self._assert_close(FLASH[0], cues[0][0], 0.02, "input 1 cue start")
        # The tail cue is clipped back to the picture it belongs to.
        self.assertEqual("TAIL", cues[1][2])
        self._assert_close(PICTURE, cues[1][1], 0.02, "clipped tail cue end")
        # Input 2's cue is offset by input 1's PICTURE, not its container.
        self._assert_close(PICTURE + FLASH[0], cues[2][0], 0.02, "input 2 cue start")
        self._assert_close(PICTURE + FLASH[1], cues[2][1], 0.02, "input 2 cue end")

    def test_an_audio_tail_no_longer_delays_the_next_input(self):
        cues = self._merged_cues([self._item(self.audio_tail), self._item(self.plain)])
        self.assertEqual(2, len(cues), cues)
        self._assert_close(PICTURE + FLASH[0], cues[1][0], 0.02, "input 2 cue start")

    def test_a_non_zero_start_input_is_measured_from_its_own_first_frame(self):
        # `offset.mkv` tags DURATION 3.000 for a 2.000 s picture starting at
        # 1.000; reading the tag as a length made it a second too long.
        item = self._item(self.offset)
        self._assert_close(PICTURE, FFmWiz.join_item_picture_span(item), 0.02, "offset picture")
        cues = self._merged_cues([item, self._item(self.plain)])
        self._assert_close(PICTURE + FLASH[0], cues[1][0], 0.02, "input 2 cue start")

    def test_the_merged_cues_land_on_the_flashes_of_the_finished_join(self):
        items = [self._item(self.subtitle_tail), self._item(self.plain)]
        output = self._encode(items)
        self._assert_close(2 * PICTURE, self._picture_seconds(output), 0.08, "joined picture")
        flashes = self._white_spans(output)
        self.assertEqual(2, len(flashes), flashes)
        cues = [(round(start, 3), round(end, 3)) for start, end in
                [(float(packet["pts_time"]),
                  float(packet["pts_time"]) + float(packet.get("duration_time") or 0))
                 for packet in self._probe(output, "-select_streams", "s",
                                           "-show_packets")["packets"]]]
        # Three cues, two flashes: the TAIL cue has no flash of its own, and the
        # two that do have to sit on them.
        self.assertEqual(3, len(cues), cues)
        for cue, flash in zip((cues[0], cues[2]), flashes):
            self._assert_close(flash[0], cue[0], 0.08, "cue start vs flash")
            self._assert_close(flash[1], cue[1], 0.08, "cue end vs flash")
        # And nothing may hang past the last frame any more.
        self.assertLessEqual(max(end for _start, end in cues),
                             self._picture_seconds(output) + 0.06)

    def test_the_joined_timeline_map_measures_the_picture(self):
        items = [self._item(self.subtitle_tail), self._item(self.audio_tail)]
        answers = self._answers(items)
        timeline = wizard_build_b.joined_timeline_map(answers, items)
        # Was 6.000: two 3.000 s containers around two 2.000 s pictures.
        self._assert_close(2 * PICTURE, timeline.source_duration, 0.05, "source duration")
        self.assertTrue(timeline.is_identity)

    def test_a_cut_on_the_joined_timeline_uses_the_picture_length(self):
        items = [self._item(self.subtitle_tail), self._item(self.plain)]
        answers = self._answers(items, cut_keep_ranges=[(1.0, 3.0)])
        timeline = wizard_build_b.joined_timeline_map(answers, items)
        self.assertEqual([(1.0, 3.0)], timeline.keep_ranges)
        self._assert_close(2.0, timeline.output_duration, 0.02, "kept duration")

    def test_the_complete_item_list_carries_picture_durations(self):
        items = [self._item(self.subtitle_tail), self._item(self.plain)]
        answers = self._answers(items)
        rebuilt = FFmWiz.join_items_from_answers(answers)
        self.assertEqual(2, len(rebuilt))
        for item in rebuilt:
            self._assert_close(PICTURE, item["duration"], 0.02, "rebuilt item duration")
        # The caller's own items are left exactly as they were.
        self._assert_close(TAIL, items[0]["duration"], 0.08, "caller's item")

    def test_an_input_with_no_knowable_duration_is_refused_not_guessed(self):
        items = [self._item(self.plain), self._item(self.plain)]
        blind = dict(items[1])
        blind["video_streams"] = [{}]
        blind["streams"] = [{}]
        blind["format"] = {}
        blind["duration"] = 0.0
        plan = FFmWiz.join_subtitle_plan(self._answers(items), [items[0], blind])
        self.assertFalse(plan["supported"])
        self.assertIn("no known duration", plan["reason"])


if __name__ == "__main__":
    unittest.main()
