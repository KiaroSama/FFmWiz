"""Regression: an edited join must still carry its selected subtitles (F09).

`join_subtitle_plan()` refused assembly outright for cuts, a Split, a speed
change or reverse. The limitation was announced, but the user-selected tracks
were still removed -- and by then every piece needed to keep them existed:
`TimelineMap` for the transform, and a per-part slicer for Split. The two
features simply did not compose: an ordinary edited file could retime its
subtitles, a plain join could merge selected tracks, an edited join could do
neither.

Measured on two 2 s inputs whose cues are deliberately distinguishable --
input 1 says FIRST at 0.5-1.5, input 2 says SECOND at 0.5-1.5, so the merged
joined track reads FIRST 0.5-1.5 and SECOND 2.5-3.5:

    edit                     before          after
    2x speed                 no subtitles    FIRST 0.25-0.75, SECOND 1.25-1.75
    reverse                  no subtitles    SECOND 0.5-1.5,  FIRST 2.5-3.5
    cut keep 0-1 and 2-4     no subtitles    FIRST 0.5-1.0,   SECOND 1.5-2.5
    split at 2.0             no subtitles    Part01 FIRST,    Part02 SECOND 0.5-1.5

The cue times are checked numerically. A "a subtitle stream exists" assertion
would pass on a track that still carried the unedited joined timestamps, which
is the outcome that must never ship.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

import FFmWiz

from cue_clock import read_cues

from artifact_guard import NoLeakedArtifacts

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")
class EditedJoinsKeepTheirSubtitles(NoLeakedArtifacts, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_joinedit_"))
        cls.inputs = []
        for name, colour, text in (("a", "red", "FIRST"), ("b", "blue", "SECOND")):
            srt = cls._tmp / f"{name}.srt"
            srt.write_text(f"1\n00:00:00,500 --> 00:00:01,500\n{text}\n\n",
                           encoding="utf-8", newline="\n")
            path = cls._tmp / f"{name}.mkv"
            subprocess.run(
                [FFMPEG, "-v", "error", "-y",
                 "-f", "lavfi", "-i", f"color=c={colour}:size=320x240:rate=30:duration=2",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                 "-i", str(srt), "-map", "0:v", "-map", "1:a", "-map", "2:s",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-c:s", "srt", str(path)],
                check=True, capture_output=True, timeout=300)
            cls.inputs.append(path)

    def test_each_input_really_carries_two_seconds_of_picture(self):
        # Guard the fixture. It used to be muxed with `-shortest`, which clipped
        # the picture to the 1.500 s subtitle: every number in this file assumes
        # 2.000 s per input, and the joined output was really 3.009 s with the
        # colour changing at 1.500. The offsets below are only meaningful while
        # the picture is what the fixture claims.
        for path in self.inputs:
            info = self._probe(path)
            video = [s for s in info["streams"] if s["codec_type"] == "video"][0]
            self.assertAlmostEqual(
                2.0, FFmWiz.video_stream_span_seconds(video, info["format"]),
                delta=0.05, msg=f"{path.name} picture span")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _probe(self, path, *extra):
        return json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", *extra,
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120).stdout)

    def _item(self, path):
        info = self._probe(path)
        return {"path": path, "probe": info, "format": info["format"],
                "streams": info["streams"],
                "video_streams": [s for s in info["streams"] if s["codec_type"] == "video"],
                "audio_streams": [s for s in info["streams"] if s["codec_type"] == "audio"],
                "subtitle_streams": [s for s in info["streams"] if s["codec_type"] == "subtitle"],
                "data_streams": [], "duration": 2.0}

    def _cues(self, path):
        # Read on the PICTURE clock. A plain extraction hands back whatever the
        # demuxer rebased by the container start, which on a primed output is
        # every cue 23 ms late against times these tests write on the picture
        # (D16). See tests/cue_clock.py for the measurement.
        return read_cues(FFMPEG, FFPROBE, path)

    def _join(self, label, **extra):
        out = self._tmp / label
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        items = [self._item(path) for path in self.inputs]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.inputs[0],
            "probe": items[0]["probe"], "format": items[0]["format"],
            "output_location": out,
            "video_streams": items[0]["video_streams"],
            "audio_streams": items[0]["audio_streams"],
            "subtitle_streams": items[0]["subtitle_streams"],
            "output_ext": "mkv", "audio_tracks": [0],
            "color_range_choice": "tv",
            "keep_source_subtitles": True, "subtitle_tracks": [0],
            "video_encoder": "libx264", "crf": 28, "preset": "ultrafast",
            "audio_codec": "aac", "join_input_items": items[1:],
        })
        answers.update(extra)
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            cmd = [str(part) for part in FFmWiz.build_join_encode_command(
                answers, items, out / "j.mkv")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        self.assertEqual(0, result.returncode, result.stderr[-2000:])
        return sorted(out.glob("j*.mkv"))

    def test_a_plain_join_is_unchanged(self):
        # Guard the guard: the baseline all four edits are measured against.
        outputs = self._join("plain")
        self.assertEqual([(0.5, 1.5, "FIRST"), (2.5, 3.5, "SECOND")],
                         self._cues(outputs[0]))

    def test_speed_halves_the_joined_cue_times(self):
        outputs = self._join("speed", video_speed_enabled=True, video_speed_factor=2.0)
        self.assertEqual([(0.25, 0.75, "FIRST"), (1.25, 1.75, "SECOND")],
                         self._cues(outputs[0]))

    def test_reverse_mirrors_them_and_swaps_the_order(self):
        outputs = self._join("reverse", video_speed_enabled=True,
                             video_speed_factor=1.0, reverse_video=True)
        self.assertEqual([(0.5, 1.5, "SECOND"), (2.5, 3.5, "FIRST")],
                         self._cues(outputs[0]))

    def test_a_cut_collapses_the_removed_stretch(self):
        # Keep 0-1 and 2-4 of the 4 s joined timeline. FIRST loses its second
        # half; SECOND moves back by the missing second.
        outputs = self._join("cut", cut_keep_ranges=[(0.0, 1.0), (2.0, 4.0)])
        self.assertEqual([(0.5, 1.0, "FIRST"), (1.5, 2.5, "SECOND")],
                         self._cues(outputs[0]))

    def test_a_split_gives_each_part_its_own_rebased_track(self):
        parts = self._join("split", separator_points=[2.0])
        self.assertEqual(2, len(parts))
        self.assertEqual([(0.5, 1.5, "FIRST")], self._cues(parts[0]))
        self.assertEqual([(0.5, 1.5, "SECOND")], self._cues(parts[1]),
                         "Part 2 must be rebased to zero, not left at 2.5-3.5")

    def test_every_edit_still_produces_exactly_one_subtitle_stream(self):
        for label, extra in (
            ("s1", {"video_speed_enabled": True, "video_speed_factor": 2.0}),
            ("s2", {"reverse_video": True, "video_speed_enabled": True,
                    "video_speed_factor": 1.0}),
            ("s3", {"cut_keep_ranges": [(0.0, 1.0), (2.0, 4.0)]}),
        ):
            with self.subTest(edit=label):
                outputs = self._join(label, **extra)
                streams = [s for s in self._probe(outputs[0])["streams"]
                           if s["codec_type"] == "subtitle"]
                self.assertEqual(1, len(streams))

    def test_the_track_metadata_survives_the_edit(self):
        outputs = self._join("meta", video_speed_enabled=True, video_speed_factor=2.0)
        streams = [s for s in self._probe(outputs[0])["streams"]
                   if s["codec_type"] == "subtitle"]
        self.assertEqual(1, len(streams))
        self.assertEqual("subrip", streams[0].get("codec_name"))


if __name__ == "__main__":
    unittest.main()
