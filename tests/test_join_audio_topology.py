"""Regression tests: joined audio must be planned across ALL inputs (D30).

The audio topology used to be anchored to input 1. That cost audio two ways:

  * input 1 silent, a later input with audio -> no audio graph was created at
    all and every later input's audio was silently lost;
  * input 1 with audio, a later input silent -> the builder refused the join
    outright, even though the standalone join path synthesises silence for
    exactly the same set of files.

Both are now planned per input: a missing track becomes an anullsrc segment of
that input's own duration, and tracks past input 1's count are reported as
unreachable rather than dropped without a word.

The permutations were verified with real FFmpeg encodes and per-segment
volumedetect; audible segments measured -24.1 dB and synthesised ones -91.0 dB
(the silence floor), landing exactly on the intended inputs. RealJoinAudio
below re-proves that; the rest assert the command shape without encoding.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

from join_test_helpers import make_item

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

SILENCE_FLOOR_DB = -60.0


def _answers(items, tracks=None, **extra):
    first = items[0]
    answers = {
        "ffmpeg": "ffmpeg", "ffprobe": "ffprobe",
        "input_path": first["path"], "output_location": Path("."),
        "video_streams": first["video_streams"], "audio_streams": first["audio_streams"],
        "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
        "streams": first["streams"], "format": first["format"],
        "output_ext": "mkv", "video_codec": "H264", "use_gpu": False,
        "audio_codec": "aac", "audio_bitrate_kbps": 128,
        "subtitle_tracks": [], "resolution": "n", "fps": 30,
        "video_bitrate_kbps": 400, "color_range_choice": "tv",
        "join_input_items": items[1:],
    }
    # The key exists only when the track question was actually answered, and a
    # silent input 1 leaves it ABSENT. An empty list is now the user's explicit
    # "no audio tracks" answer and is honoured literally (F04), so a fixture
    # must not use it to mean "never asked".
    if tracks is not None:
        answers["audio_tracks"] = tracks
    elif first["audio_streams"]:
        answers["audio_tracks"] = list(range(len(first["audio_streams"])))
    answers.update(extra)
    return answers


def _filtergraph(items, **kw):
    answers = _answers(items, **kw)
    cmd = [str(part) for part in FFmWiz.build_join_encode_command(
        answers, items, Path("out.mkv"))]
    return cmd[cmd.index("-filter_complex") + 1], cmd, answers


class Topology(unittest.TestCase):
    def test_a_silent_first_input_still_joins_later_audio(self):
        # The regression: input 1 silent meant NO audio graph at all.
        items = [make_item("a.mkv", 6.0, with_audio=False),
                 make_item("b.mkv", 4.0)]
        graph, cmd, _ = _filtergraph(items)
        self.assertIn("concat=n=2:v=1:a=1", graph,
                      "later inputs' audio must still reach the output")
        self.assertNotIn("-an", cmd)

    def test_a_silent_first_input_gets_silence_for_its_own_duration(self):
        items = [make_item("a.mkv", 6.0, with_audio=False),
                 make_item("b.mkv", 4.0)]
        graph, _cmd, _ = _filtergraph(items)
        self.assertIn("anullsrc", graph)
        self.assertIn("d=6.000000", graph,
                      "silence must span the silent input, not a placeholder length")

    def test_a_silent_later_input_is_filled_not_refused(self):
        # The other half of the regression: this used to raise instead of
        # synthesising silence the way the standalone join path always has.
        items = [make_item("a.mkv", 6.0), make_item("b.mkv", 4.0, with_audio=False)]
        graph, _cmd, _ = _filtergraph(items)
        self.assertIn("d=4.000000", graph)
        self.assertIn("concat=n=2:v=1:a=1", graph)

    def test_alternating_silence_fills_only_the_silent_input(self):
        items = [make_item("a.mkv", 4.0), make_item("b.mkv", 3.0, with_audio=False),
                 make_item("c.mkv", 4.0)]
        graph, _cmd, _ = _filtergraph(items)
        self.assertEqual(graph.count("anullsrc"), 1,
                         "only the silent input may be synthesised")
        self.assertIn("d=3.000000", graph)
        self.assertIn("concat=n=3:v=1:a=1", graph)

    def test_all_silent_inputs_produce_no_audio_stream(self):
        items = [make_item("a.mkv", 5.0, with_audio=False),
                 make_item("b.mkv", 4.0, with_audio=False)]
        graph, _cmd, _ = _filtergraph(items)
        self.assertIn("concat=n=2:v=1:a=0", graph)
        self.assertNotIn("anullsrc", graph,
                         "there is no track to fill when nothing has audio")

    def test_every_selected_track_gets_its_own_concat_output(self):
        items = [make_item("a.mkv", 5.0), make_item("b.mkv", 4.0)]
        for item in items:
            item["audio_streams"] = item["audio_streams"] * 2
        graph, _cmd, _ = _filtergraph(items, tracks=[0, 1])
        self.assertIn("concat=n=2:v=1:a=2", graph)
        self.assertIn("[jacat0]", graph)
        self.assertIn("[jacat1]", graph)

    def test_a_track_missing_from_one_input_is_silenced_per_track(self):
        # Input 1 has two tracks, input 2 has one: track 1 needs silence for
        # input 2's segment only.
        items = [make_item("a.mkv", 5.0), make_item("b.mkv", 4.0)]
        items[0]["audio_streams"] = items[0]["audio_streams"] * 2
        graph, _cmd, _ = _filtergraph(items, tracks=[0, 1])
        self.assertIn("concat=n=2:v=1:a=2", graph)
        self.assertEqual(graph.count("anullsrc"), 1)
        self.assertIn("d=4.000000", graph)

    def test_the_silence_source_matches_the_join_sample_rate(self):
        # A mismatched rate makes the concat filter resample or refuse.
        items = [make_item("a.mkv", 6.0), make_item("b.mkv", 4.0, with_audio=False)]
        answers = _answers(items)
        rate = FFmWiz.join_target_sample_rate(answers)
        graph, _cmd, _ = _filtergraph(items)
        self.assertIn(f"sample_rate={rate}", graph)


class Notes(unittest.TestCase):
    """What the user is told has to match what the command does."""

    def setUp(self):
        self._notes = []
        self._real = FFmWiz.appio.note
        FFmWiz.appio.note = self._notes.append

    def tearDown(self):
        FFmWiz.appio.note = self._real

    def test_recovering_from_a_silent_first_input_is_announced(self):
        items = [make_item("a.mkv", 6.0, with_audio=False), make_item("b.mkv", 4.0)]
        _filtergraph(items)
        joined = " ".join(self._notes).lower()
        self.assertIn("input 1 has no audio", joined)

    def test_synthesised_silence_is_announced(self):
        items = [make_item("a.mkv", 6.0), make_item("b.mkv", 4.0, with_audio=False)]
        _filtergraph(items)
        joined = " ".join(self._notes).lower()
        self.assertIn("silence is synthesised", joined)

    def test_tracks_beyond_the_question_are_reported_not_dropped_silently(self):
        # The track question only reaches input 1's count; anything past it is
        # genuinely absent from the output and the user must hear about it.
        items = [make_item("a.mkv", 5.0), make_item("b.mkv", 4.0)]
        items[1]["audio_streams"] = items[1]["audio_streams"] * 3
        _filtergraph(items, tracks=[0])
        joined = " ".join(self._notes).lower()
        self.assertIn("not in the joined output", joined)


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")
class RealJoinAudio(unittest.TestCase):
    """Prove silence really lands on the silent input, not just in the graph."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_joinaudio_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _clip(self, name, duration, with_audio):
        path = self._tmp / f"{name}.mkv"
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"color=c=red:s=160x120:d={duration}:r=25"]
        if with_audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
                    "-map", "0:v", "-map", "1:a", "-c:a", "aac"]
        else:
            cmd += ["-map", "0:v"]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-vf", "format=yuv420p", str(path)]
        subprocess.run(cmd, check=True, timeout=120)
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60).stdout)
        streams = probe["streams"]
        return {"path": path, "streams": streams, "format": probe["format"],
                "duration": float(duration),
                "video_streams": [s for s in streams if s["codec_type"] == "video"],
                "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
                "subtitle_streams": [], "attachment_streams": [], "data_streams": []}

    def _mean_db(self, path, start, length):
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
             "-i", str(path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120)
        for line in result.stderr.splitlines():
            if "mean_volume:" in line:
                return float(line.split("mean_volume:")[1].strip().split()[0])
        self.fail(f"volumedetect reported no mean_volume for {path}")

    def _join(self, specs):
        items = [self._clip(f"in{i}", d, a) for i, (d, a) in enumerate(specs)]
        answers = _answers(items)
        answers["output_location"] = self._tmp
        cmd = [str(part) for part in FFmWiz.build_join_encode_command(
            answers, items, self._tmp / "joined.mkv")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stderr[-400:])
        return Path(answers["output_path"]), items

    def _assert_segments(self, output, items, expected_audible):
        offset = 0.0
        for item, want_audio in zip(items, expected_audible):
            db = self._mean_db(output, offset + 0.4, max(0.6, item["duration"] - 0.8))
            if want_audio:
                self.assertGreater(db, SILENCE_FLOOR_DB,
                                   f"segment at {offset:g}s should be audible, measured {db} dB")
            else:
                self.assertLessEqual(db, SILENCE_FLOOR_DB,
                                     f"segment at {offset:g}s should be silent, measured {db} dB")
            offset += item["duration"]

    def test_silent_first_input_keeps_later_audio(self):
        output, items = self._join([(6, False), (4, True)])
        self._assert_segments(output, items, [False, True])

    def test_silent_later_input_becomes_silence_not_a_failure(self):
        output, items = self._join([(6, True), (4, False)])
        self._assert_segments(output, items, [True, False])

    def test_alternating_inputs_keep_audio_aligned_to_the_timeline(self):
        # The alignment case: a wrong silence length here shifts every later
        # segment's audio off its picture.
        output, items = self._join([(4, True), (3, False), (4, True)])
        self._assert_segments(output, items, [True, False, True])


if __name__ == "__main__":
    unittest.main()
