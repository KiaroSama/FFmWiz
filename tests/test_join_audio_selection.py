"""F04/F05/F06: what the joined audio ANSWER means, and when a join may copy.

Three defects, all of them a case of reading intent out of a value's shape
instead of its state. Measured on real 2 s clips before the fix:

* F04 -- two audible inputs with an explicit `audio_tracks=[]` still produced
  `concat=n=2:v=1:a=1`, mapped `[jafinal0]`, omitted `-an`, printed "input 1 has
  no audio" and muxed an AAC stream the user had said no to. An empty list and
  a missing key were both just falsy.
* F05 -- input 1 with one track and input 2 with two: the track prompt rejected
  `1` with "Invalid stream number(s): [1]. Allowed range: 0 to 0", because the
  question was sized from input 1 alone. With input 1 silent and input 2
  carrying three tracks only track 0 was offered and the builder announced the
  other two as "NOT in the joined output".
* F06 -- two copy-compatible H.264/AAC MKVs with `video_codec=copy`,
  `audio_codec=copy` and the complete selection `[0]`: the summary said "stream
  copy, no re-encode" while the command carried `-filter_complex ... libx265
  ... aac`, and the produced file was hevc. The gate accepted a selection only
  when it was literally `None` or `"all"`.

The real-encode classes below re-measure each of those with FFmpeg: stream
counts and codecs from ffprobe, and per-segment tone presence from a Goertzel
filter over decoded PCM (volumedetect's report goes to stderr at info level,
which `-v error` suppresses, so it is not usable here).
"""
import array
import contextlib
import io
import json
import math
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

from join_test_helpers import make_item

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")

# Measured on the fixtures below: a 1.2 s audible segment decodes to RMS ~2892
# with the expected tone holding a Goertzel share of ~0.50 and the other two
# candidates at 0.0; a synthesised-silence segment decodes to RMS 0.00 with
# every share 0.0. The thresholds sit far from both.
PROBE_SAMPLE_RATE = 16000
SILENT_RMS = 50.0
AUDIBLE_RMS = 500.0
TONE_SHARE = 0.1
OFF_TONE_SHARE = 0.01


def _answers(items, **extra):
    """Wizard answers for a join whose primary input is `items[0]`."""
    first = items[0]
    answers = {
        "ffmpeg": FFMPEG or "ffmpeg", "ffprobe": FFPROBE or "ffprobe",
        "input_path": first["path"], "output_location": Path("."),
        "video_streams": first["video_streams"], "audio_streams": first["audio_streams"],
        "subtitle_streams": first.get("subtitle_streams") or [],
        "data_streams": first.get("data_streams") or [],
        "attachment_streams": first.get("attachment_streams") or [],
        "streams": first["streams"], "format": first["format"],
        "output_ext": "mkv", "video_codec": "H264", "use_gpu": False,
        "audio_codec": "aac", "audio_bitrate_kbps": 128,
        "subtitle_tracks": [], "resolution": "n", "fps": 25,
        "video_bitrate_kbps": 400, "color_range_choice": "tv",
        # The synthetic streams carry no `index`, which the duplicate-audio
        # report needs; track SELECTION is what these tests measure.
        "detect_duplicate_audio": False,
        "join_input_items": items[1:],
    }
    answers.update(extra)
    return answers


def _graph_and_cmd(items, **kwargs):
    answers = _answers(items, **kwargs)
    cmd = [str(part) for part in FFmWiz.build_join_encode_command(
        answers, items, Path("out.mkv"))]
    graph = cmd[cmd.index("-filter_complex") + 1] if "-filter_complex" in cmd else ""
    return graph, cmd, answers


def _goertzel(samples, sample_rate, frequency):
    """Power of `frequency` in `samples`, normalised by the segment's energy.

    Small enough to keep the suite dependency-free, and it answers the exact
    question a per-track assertion needs: is THIS tone in THIS segment?
    """
    count = len(samples)
    if not count:
        return 0.0
    k = int(round(count * float(frequency) / sample_rate))
    omega = 2.0 * math.pi * k / count
    coeff = 2.0 * math.cos(omega)
    s1 = s2 = 0.0
    energy = 0.0
    for value in samples:
        s0 = value + coeff * s1 - s2
        s2, s1 = s1, s0
        energy += value * value
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return power / (energy * count) if energy > 0 else 0.0


class _MediaCase(unittest.TestCase):
    """Real clips plus the probe/decode helpers the measurements need."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_joinsel_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, cmd, timeout=600):
        result = subprocess.run([str(part) for part in cmd], capture_output=True,
                                text=True, stdin=subprocess.DEVNULL, timeout=timeout)
        self.assertEqual(result.returncode, 0, result.stderr[-600:])
        return result

    def _clip(self, name, duration=2.0, tones=(440,), subtitles=(), attach=False):
        """A red 2 s clip with one audio track per entry in `tones`."""
        path = self._tmp / f"{name}.mkv"
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"color=c=red:s=160x120:d={duration}:r=25"]
        for hertz in tones:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency={hertz}:duration={duration}"]
        for index, body in enumerate(subtitles):
            srt = self._tmp / f"{name}_{index}.srt"
            srt.write_text(f"1\n00:00:00,100 --> 00:00:01,900\n{body}\n\n",
                           encoding="utf-8", newline="\n")
            cmd += ["-i", str(srt)]
        if attach:
            note = self._tmp / f"{name}_note.txt"
            note.write_text("attached", encoding="utf-8", newline="\n")
            cmd += ["-attach", str(note), "-metadata:s:t", "mimetype=text/plain"]
        cmd += ["-map", "0:v"]
        for index in range(len(tones)):
            cmd += ["-map", f"{index + 1}:a"]
        for index in range(len(subtitles)):
            cmd += ["-map", f"{len(tones) + index + 1}:s"]
        if tones:
            cmd += ["-c:a", "aac"]
        if subtitles:
            cmd += ["-c:s", "srt"]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-vf", "format=yuv420p", str(path)]
        self._run(cmd, timeout=180)
        return self._item(path, duration)

    def _item(self, path, duration):
        probe = json.loads(self._run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            timeout=60).stdout)
        streams = probe["streams"]

        def of(kind):
            return [s for s in streams if s.get("codec_type") == kind]

        return {"path": Path(path), "streams": streams, "format": probe["format"],
                "duration": float(duration),
                "video_streams": of("video"), "audio_streams": of("audio"),
                "subtitle_streams": of("subtitle"), "attachment_streams": of("attachment"),
                "data_streams": of("data")}

    def _stream_kinds(self, path):
        probe = json.loads(self._run(
            [FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(path)],
            timeout=60).stdout)
        return [(s.get("codec_type"), s.get("codec_name")) for s in probe["streams"]]

    def _segment(self, path, audio_index, start, length):
        """Decoded mono PCM for one output track over one joined segment."""
        raw = subprocess.run(
            [FFMPEG, "-hide_banner", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
             "-i", str(path), "-map", f"0:a:{audio_index}", "-ac", "1",
             "-ar", str(PROBE_SAMPLE_RATE), "-f", "s16le", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=180)
        self.assertEqual(raw.returncode, 0, raw.stderr[-400:])
        samples = array.array("h")
        samples.frombytes(raw.stdout[:len(raw.stdout) // 2 * 2])
        self.assertGreater(len(samples), PROBE_SAMPLE_RATE // 4,
                           f"decoded too few samples from {path.name} track {audio_index}")
        return samples

    @staticmethod
    def _rms(samples):
        return math.sqrt(sum(float(v) * v for v in samples) / len(samples))

    def _assert_tone(self, path, audio_index, start, length, frequency, others):
        samples = self._segment(path, audio_index, start, length)
        rms = self._rms(samples)
        self.assertGreater(rms, AUDIBLE_RMS,
                           f"track {audio_index} at {start:g}s should be audible, RMS {rms:.2f}")
        share = _goertzel(samples, PROBE_SAMPLE_RATE, frequency)
        self.assertGreater(share, TONE_SHARE,
                           f"{frequency} Hz should carry track {audio_index} at {start:g}s, "
                           f"share {share:.4f}")
        for other in others:
            other_share = _goertzel(samples, PROBE_SAMPLE_RATE, other)
            self.assertLess(other_share, OFF_TONE_SHARE,
                            f"track {audio_index} at {start:g}s carries the wrong input's "
                            f"{other} Hz tone, share {other_share:.4f}")

    def _assert_silent(self, path, audio_index, start, length):
        samples = self._segment(path, audio_index, start, length)
        rms = self._rms(samples)
        self.assertLess(rms, SILENT_RMS,
                        f"track {audio_index} at {start:g}s should be silence, RMS {rms:.2f}")


# ---------------------------------------------------------------- F04


class ExplicitNoAudioSelection(unittest.TestCase):
    """An empty answer is an answer. It used to be read as "never asked"."""

    def setUp(self):
        self._notes = []
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = self._notes.append

    def tearDown(self):
        FFmWiz.appio.note = self._real_note

    def _two_audible(self):
        return [make_item("a.mkv", 2.0), make_item("b.mkv", 2.0)]

    def test_an_explicit_empty_selection_leaves_the_join_video_only(self):
        graph, cmd, _ = _graph_and_cmd(self._two_audible(), audio_tracks=[])
        self.assertIn("concat=n=2:v=1:a=0", graph)
        self.assertIn("-an", cmd)
        self.assertNotIn("anullsrc", graph,
                         "there is nothing to fill when no track was asked for")
        self.assertEqual([], [cmd[i + 1] for i, part in enumerate(cmd)
                              if part == "-map" and "jafinal" in cmd[i + 1]])

    def test_an_explicit_empty_selection_is_not_recovered_from(self):
        _graph_and_cmd(self._two_audible(), audio_tracks=[])
        self.assertNotIn("input 1 has no audio", " ".join(self._notes).lower(),
                         "both inputs are audible; the recovery note was pure fiction")

    def test_a_missing_key_still_recovers_a_silent_first_input(self):
        # The other side of the same line: absent means never asked, and that
        # is the one state in which a later input's track may be borrowed.
        items = [make_item("a.mkv", 2.0, with_audio=False), make_item("b.mkv", 2.0)]
        answers = _answers(items)
        answers.pop("audio_tracks", None)
        graph = [str(p) for p in FFmWiz.build_join_encode_command(
            answers, items, Path("out.mkv"))]
        self.assertIn("concat=n=2:v=1:a=1",
                      graph[graph.index("-filter_complex") + 1])
        self.assertIn("input 1 has no audio", " ".join(self._notes).lower())

    def test_a_silent_first_input_does_not_override_an_explicit_empty_answer(self):
        # The hard case for the same line: input 1 IS silent, so the recovery
        # would fire on the old rule -- but the user answered the question, and
        # the answer was "no audio". Lending a track here would make the codec,
        # bitrate and LoudNorm gates (and the summary) describe a stream the
        # output does not carry.
        items = [make_item("a.mkv", 2.0, with_audio=False), make_item("b.mkv", 2.0)]
        answers = _answers(items, audio_tracks=[])
        self.assertEqual(([], []), FFmWiz.join_audio_recovery(answers))
        seen = {}
        FFmWiz.with_join_audio_view(
            lambda a: seen.update(tracks=FFmWiz.selected_audio_streams(a)))(answers)
        self.assertEqual([], seen["tracks"])
        self.assertEqual([], answers["audio_tracks"], "the answer must survive the lend")
        graph, cmd, _ = _graph_and_cmd(items, audio_tracks=[])
        self.assertIn("concat=n=2:v=1:a=0", graph)
        self.assertIn("-an", cmd)

    def test_the_selection_state_separates_none_from_unasked(self):
        items = self._two_audible()
        answers = _answers(items, audio_tracks=[])
        self.assertEqual(("none", []), FFmWiz.join_audio_selection(answers, items))
        answers.pop("audio_tracks")
        self.assertEqual(("unasked", []), FFmWiz.join_audio_selection(answers, items))


@requires_ffmpeg
class RealExplicitNoAudio(_MediaCase):
    def test_two_audible_inputs_with_no_selected_track_produce_no_audio(self):
        items = [self._clip("a", tones=(440,)), self._clip("b", tones=(880,))]
        answers = _answers(items, audio_tracks=[], output_location=self._tmp)
        cmd = FFmWiz.build_join_encode_command(answers, items, self._tmp / "joined.mkv")
        with contextlib.redirect_stdout(io.StringIO()):
            self._run(cmd)
        kinds = self._stream_kinds(Path(answers["output_path"]))
        self.assertEqual([("video", "h264")], kinds,
                         "the output must carry no audio stream at all")


# ---------------------------------------------------------------- F05


class LogicalTrackView(unittest.TestCase):
    """The joined view, the exact analogue of join_subtitle_streams_view."""

    def _mixed_counts(self):
        """Input 1 with one track, input 2 with two."""
        first = make_item("a.mkv", 2.0)
        second = make_item("b.mkv", 2.0)
        second["audio_streams"] = [dict(second["audio_streams"][0], sample_rate="48000"),
                                   dict(second["audio_streams"][0], sample_rate="22050")]
        return [first, second]

    def test_the_view_offers_every_track_any_input_carries(self):
        items = self._mixed_counts()
        self.assertEqual(2, len(FFmWiz.join_audio_streams_view(_answers(items))))
        self.assertEqual(2, FFmWiz.join_audio_track_count(items))

    def test_each_logical_track_is_described_by_the_first_input_that_has_it(self):
        items = self._mixed_counts()
        view = FFmWiz.join_audio_streams_view(_answers(items))
        self.assertIs(items[0]["audio_streams"][0], view[0])
        # Track 1 exists only on input 2, so its codec/rate must come from
        # there -- describing it with input 1's track 0 would report a stream
        # the output does not carry.
        self.assertIs(items[1]["audio_streams"][1], view[1])
        self.assertEqual("22050", view[1]["sample_rate"])

    def test_a_silent_first_input_no_longer_hides_the_later_track_list(self):
        items = [make_item("a.mkv", 2.0, with_audio=False), make_item("b.mkv", 2.0)]
        items[1]["audio_streams"] = items[1]["audio_streams"] * 3
        self.assertEqual(3, len(FFmWiz.join_audio_streams_view(_answers(items))))

    def test_a_later_only_index_is_a_valid_selection(self):
        items = self._mixed_counts()
        self.assertEqual(("indices", [1]),
                         FFmWiz.join_audio_selection(_answers(items, audio_tracks=[1]), items))


class TheTrackQuestionCoversTheWholeJoin(unittest.TestCase):
    """The wizard's own audio_tracks Step, captured from run_wizard."""

    class _Stop(Exception):
        pass

    def _steps(self):
        recorded = []
        real_step = FFmWiz.wizard.Step
        real_input = FFmWiz.wizard.step_input_path

        def recorder(name, applicable, run):
            step = real_step(name, applicable, run)
            recorded.append(step)
            return step

        def stop(_answers):
            raise TheTrackQuestionCoversTheWholeJoin._Stop()

        FFmWiz.wizard.Step = recorder
        FFmWiz.wizard.step_input_path = stop
        try:
            with self.assertRaises(TheTrackQuestionCoversTheWholeJoin._Stop):
                FFmWiz.run_wizard({})
        finally:
            FFmWiz.wizard.Step = real_step
            FFmWiz.wizard.step_input_path = real_input
        return {step.name: step for step in recorded}

    def _ask(self, step, answers, reply):
        replies = iter([reply])
        real_raw = FFmWiz.appio.ask_raw
        FFmWiz.appio.ask_raw = lambda *_a, **_kw: next(replies)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                step.run(answers)
        finally:
            FFmWiz.appio.ask_raw = real_raw

    def test_the_question_is_offered_when_only_a_later_input_is_audible(self):
        items = [make_item("a.mkv", 2.0, with_audio=False), make_item("b.mkv", 2.0)]
        answers = _answers(items)
        answers.pop("audio_tracks", None)
        self.assertTrue(self._steps()["audio_tracks"].applicable(answers))

    def test_the_question_stays_hidden_for_an_entirely_silent_join(self):
        items = [make_item("a.mkv", 2.0, with_audio=False),
                 make_item("b.mkv", 2.0, with_audio=False)]
        answers = _answers(items)
        answers.pop("audio_tracks", None)
        self.assertFalse(self._steps()["audio_tracks"].applicable(answers))

    def test_a_track_only_a_later_input_carries_can_be_selected(self):
        # Before the fix this reply was rejected: "Invalid stream number(s):
        # [1]. Allowed range: 0 to 0".
        items = [make_item("a.mkv", 2.0), make_item("b.mkv", 2.0)]
        items[1]["audio_streams"] = items[1]["audio_streams"] * 2
        answers = _answers(items)
        answers.pop("audio_tracks", None)
        self._ask(self._steps()["audio_tracks"], answers, "1")
        self.assertEqual([1], answers["audio_tracks"])
        self.assertEqual(items[0]["audio_streams"], answers["audio_streams"],
                         "input 1 must not keep claiming another input's streams")

    def test_all_three_tracks_of_a_later_input_can_be_selected(self):
        items = [make_item("a.mkv", 2.0, with_audio=False), make_item("b.mkv", 2.0)]
        items[1]["audio_streams"] = items[1]["audio_streams"] * 3
        answers = _answers(items)
        answers.pop("audio_tracks", None)
        self._ask(self._steps()["audio_tracks"], answers, "0,1,2")
        self.assertEqual([0, 1, 2], answers["audio_tracks"])
        self.assertEqual([], answers["audio_streams"])


@requires_ffmpeg
class RealLaterOnlyTracks(_MediaCase):
    TONES = (440, 880, 1320)

    def _join(self, items, **kwargs):
        answers = _answers(items, output_location=self._tmp, **kwargs)
        cmd = FFmWiz.build_join_encode_command(answers, items, self._tmp / "joined.mkv")
        with contextlib.redirect_stdout(io.StringIO()):
            self._run(cmd)
        return Path(answers["output_path"])

    def test_a_later_only_track_reaches_the_output_and_stays_aligned(self):
        # Input 1 has one track (440 Hz); input 2 has two (880 / 1320 Hz).
        # Logical track 1 exists only on input 2: its segment must carry
        # 1320 Hz and input 1's segment must be silent for that track.
        first = self._clip("one", tones=(440,))
        second = self._clip("two", tones=(880, 1320))
        output = self._join([first, second], audio_tracks=[1])
        self.assertEqual(1, sum(1 for kind, _ in self._stream_kinds(output)
                                if kind == "audio"))
        self._assert_silent(output, 0, 0.4, 1.2)
        self._assert_tone(output, 0, 2.4, 1.2, 1320, [440, 880])

    def test_a_silent_first_input_can_keep_all_three_later_tracks(self):
        first = self._clip("silent", tones=())
        second = self._clip("three", tones=self.TONES)
        output = self._join([first, second], audio_tracks=[0, 1, 2])
        self.assertEqual(3, sum(1 for kind, _ in self._stream_kinds(output)
                                if kind == "audio"))
        for index, tone in enumerate(self.TONES):
            self._assert_silent(output, index, 0.4, 1.2)
            self._assert_tone(output, index, 2.4, 1.2, tone,
                              [other for other in self.TONES if other != tone])


# ---------------------------------------------------------------- F06


class CopyPlan(unittest.TestCase):
    """Input compatibility and "the selection stream-copies" are two questions."""

    def _copyable(self, **extra):
        items = [make_item("a.mkv", 2.0), make_item("b.mkv", 2.0)]
        settings = {"video_codec": "copy", "audio_codec": "copy",
                    "audio_tracks": [0], "fps": None}
        settings.update(extra)
        return _answers(items, **settings), items

    def test_a_complete_one_track_selection_is_a_copy_plan(self):
        # `[0]` on a one-track input IS everything; the old gate accepted only
        # `None` or the string "all" and re-encoded this.
        answers, items = self._copyable()
        self.assertTrue(FFmWiz.join_copy_plan(answers, items)["supported"])

    def test_an_empty_audio_selection_is_not_a_copy_plan(self):
        answers, items = self._copyable(audio_tracks=[])
        plan = FFmWiz.join_copy_plan(answers, items)
        self.assertFalse(plan["supported"])
        self.assertFalse(plan["maps_everything"])

    def test_a_partial_audio_selection_is_not_a_copy_plan(self):
        answers, items = self._copyable(audio_tracks=[0])
        for item in items:
            item["audio_streams"] = item["audio_streams"] * 2
        plan = FFmWiz.join_copy_plan(answers, items)
        self.assertFalse(plan["supported"])
        self.assertFalse(plan["maps_everything"])

    def test_dropping_subtitles_data_or_attachments_blocks_the_blanket_map(self):
        for key, stream_key, stream in (
                ("keep_source_subtitles", "subtitle_streams",
                 {"codec_type": "subtitle", "codec_name": "subrip"}),
                ("keep_source_data_streams", "data_streams", {"codec_type": "data"}),
                ("keep_embedded_attachments", "attachment_streams", {"codec_type": "attachment"})):
            with self.subTest(key=key):
                answers, items = self._copyable(**{key: False})
                for item in items:
                    item[stream_key] = [dict(stream)]
                    item["streams"] = item["streams"] + [dict(stream)]
                self.assertFalse(FFmWiz.join_copy_plan(answers, items)["maps_everything"])

    def test_a_keep_everything_plan_still_uses_the_blanket_map(self):
        answers, items = self._copyable()
        cmd = [str(part) for part in FFmWiz.build_join_copy_command(
            answers, items, Path(tempfile.gettempdir()) / "joined.mkv")]
        self.assertIn("-map", cmd)
        self.assertEqual("0", cmd[cmd.index("-map") + 1])

    def test_the_summary_separates_input_compatibility_from_the_join_mode(self):
        answers, items = self._copyable(audio_tracks=[])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            FFmWiz.print_join_summary(items, True, [], FFmWiz.join_copy_plan(answers, items))
        text = re.sub(r"\x1b\[[0-9;]*m", "", buffer.getvalue())
        self.assertIn("inputs: stream-copy compatible", text)
        self.assertIn("join mode: re-encode required", text,
                      "compatible inputs must not promise a copy the plan cannot deliver")


@requires_ffmpeg
class RealCopyPlan(_MediaCase):
    def _wizard_answers(self, items, **extra):
        settings = {"output_location": self._tmp, "video_codec": "copy",
                    "audio_codec": "copy", "audio_tracks": [0], "fps": None}
        settings.update(extra)
        return _answers(items, **settings)

    def _start_now(self, answers):
        real_note, real_yes = FFmWiz.appio.note, FFmWiz.appio.ask_yes_no
        FFmWiz.appio.note = lambda *_a, **_kw: None
        FFmWiz.appio.ask_yes_no = lambda *_a, **_kw: False
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                FFmWiz.step_start_now(answers)
        finally:
            FFmWiz.appio.note, FFmWiz.appio.ask_yes_no = real_note, real_yes
        return [str(part) for part in answers["cmd"]]

    def test_a_complete_selection_stream_copies_instead_of_re_encoding(self):
        items = [self._clip("c1"), self._clip("c2")]
        answers = self._wizard_answers(items)
        cmd = self._start_now(answers)
        self.assertNotIn("-filter_complex", cmd, "a complete selection must not re-encode")
        self.assertIn("concat", cmd)
        self._run(cmd)
        self.assertEqual(self._stream_kinds(items[0]["path"]),
                         self._stream_kinds(Path(answers["output_path"])),
                         "stream copy must leave every codec exactly as it was")

    def test_an_empty_audio_selection_stays_video_only(self):
        items = [self._clip("c1"), self._clip("c2")]
        answers = self._wizard_answers(items, audio_tracks=[])
        cmd = self._start_now(answers)
        self.assertIn("-filter_complex", cmd,
                      "a video-only request cannot be served by a blanket concat copy")
        self._run(cmd)
        # A join cannot stream-copy video through the concat FILTER, so the
        # codec changes here; what must hold is that no audio stream exists.
        self.assertEqual(["video"], [kind for kind, _ in
                                     self._stream_kinds(Path(answers["output_path"]))])

    def test_a_partial_subtitle_selection_cannot_use_the_blanket_map(self):
        items = [self._clip("s1", subtitles=("TRACK-ZERO", "TRACK-ONE")),
                 self._clip("s2", subtitles=("TRACK-ZERO", "TRACK-ONE"))]
        answers = self._wizard_answers(items, subtitle_tracks=[0])
        cmd = FFmWiz.build_join_copy_command(answers, items, self._tmp / "joined.mkv")
        self.assertNotIn("0", [str(cmd[i + 1]) for i, part in enumerate(cmd) if part == "-map"],
                         "`-map 0` would copy the subtitle track that was not selected")
        self._run(cmd)
        output = Path(answers["output_path"])
        self.assertEqual(1, sum(1 for kind, _ in self._stream_kinds(output)
                                if kind == "subtitle"))
        dump = self._tmp / "kept.srt"
        self._run([FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", str(output),
                   "-map", "0:s:0", "-c:s", "srt", str(dump)], timeout=180)
        text = dump.read_text(encoding="utf-8", errors="replace")
        self.assertIn("TRACK-ZERO", text)
        self.assertNotIn("TRACK-ONE", text, "the unselected track must be absent")

    def test_dropping_attachments_cannot_be_undone_by_the_blanket_map(self):
        items = [self._clip("t1", attach=True), self._clip("t2", attach=True)]
        self.assertTrue(items[0]["attachment_streams"], "fixture must carry an attachment")
        answers = self._wizard_answers(items, keep_embedded_attachments=False)
        cmd = FFmWiz.build_join_copy_command(answers, items, self._tmp / "joined.mkv")
        self._run(cmd)
        kinds = [kind for kind, _ in self._stream_kinds(Path(answers["output_path"]))]
        self.assertNotIn("attachment", kinds)

    def test_dropping_data_streams_cannot_be_undone_by_the_blanket_map(self):
        # A MOV timecode track is a real data stream, so the request is made
        # against a file that genuinely has one. The OUTPUT stream count cannot
        # settle this case: the MOV muxer re-creates a tmcd track from the
        # video stream's timecode metadata whatever is mapped, and `-map 0` on
        # such an input fails outright ("Cannot map stream #0:2 - unsupported
        # type"). What is verifiable, and what the defect was, is that the
        # blanket map is not chosen and the dropped stream is never mapped.
        items = []
        for name in ("d1", "d2"):
            source = self._clip(name)
            path = self._tmp / f"{name}.mov"
            self._run([FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", str(source["path"]),
                       "-c", "copy", "-timecode", "00:00:00:00", str(path)], timeout=180)
            items.append(self._item(path, source["duration"]))
        self.assertTrue(items[0]["data_streams"], "fixture must carry a data stream")
        answers = self._wizard_answers(items, keep_source_data_streams=False, output_ext="mov")
        self.assertFalse(FFmWiz.join_copy_plan(answers, items)["maps_everything"])
        cmd = [str(part) for part in FFmWiz.build_join_copy_command(
            answers, items, self._tmp / "joined.mov")]
        mapped = [cmd[i + 1] for i, part in enumerate(cmd) if part == "-map"]
        self.assertNotIn("0", mapped)
        self.assertEqual([], [target for target in mapped if target.startswith("0:d")])
        self._run(cmd)


if __name__ == "__main__":
    unittest.main()
