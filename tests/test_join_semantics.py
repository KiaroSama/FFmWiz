"""What a joined re-encode promises vs. what it actually emits (D30/D31/D32).

The join builder used to anchor its whole audio topology to input 1: a silent
first input dropped every other input's audio without a word, a silent LATER
input made the build raise instead of synthesising silence (which the
standalone join path has always done), extra tracks that only later inputs
carry were unreachable, and the wizard asked about subtitles and "source
extras" that the emitted command discards.
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import FFmWiz
from join_test_helpers import audio_stream, make_item


def _answers(tmp, items, **extra):
    answers = {
        "ffmpeg": "ffmpeg",
        "ffprobe": "ffprobe",
        "video_encoders": ["libx264", "libx265"],
        "input_path": items[0]["path"],
        "output_location": Path(tmp),
        "output_ext": "mp4",
        "color_range_choice": "tv",
        "video_streams": items[0]["video_streams"],
        "audio_streams": items[0]["audio_streams"],
        "subtitle_streams": [],
        "data_streams": [],
        "attachment_streams": [],
        "subtitle_tracks": [],
        "video_codec": "H264",
        "use_gpu": False,
        "crop_enabled": False,
        "audio_codec": "aac",
        "audio_bitrate_kbps": 128,
        "resolution": "n",
        "fps": 30,
        "format": {"duration": "10.0"},
        "keep_source_metadata": False,
        "keep_source_chapters": False,
        "keep_source_subtitles": False,
        "keep_source_data_streams": False,
        "keep_source_extra_video_streams": False,
        "join_input_items": items[1:],
    }
    # The key exists only when the track question was actually answered. A
    # silent input 1 leaves it ABSENT; writing an empty list instead would say
    # "the user asked for no audio", which is now honoured literally (F04).
    if items[0]["audio_streams"]:
        answers["audio_tracks"] = list(range(len(items[0]["audio_streams"])))
    answers.update(extra)
    return answers


def _build(answers, items, tmp):
    """Build the join command, returning (filter_complex, full text, notes)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        cmd = FFmWiz.build_join_encode_command(answers, items, Path(tmp) / "out.mp4")
    text = " ".join(str(part) for part in cmd)
    graph = next(cmd[i + 1] for i, part in enumerate(cmd) if part == "-filter_complex")
    return graph, text, buffer.getvalue()


class JoinAudioTopology(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_a_silent_later_input_gets_silence_instead_of_a_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [
                make_item(Path(tmp) / "A.mov", duration=10.0),
                make_item(Path(tmp) / "B.mov", duration=12.0, with_audio=False),
                make_item(Path(tmp) / "C.mov", duration=8.0),
            ]
            graph, text, notes = _build(_answers(tmp, items), items, tmp)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=", graph)
        self.assertIn("[ja1_0]", graph)
        self.assertNotIn("[1:a:0]", graph)
        self.assertIn("[0:a:0]", graph)
        self.assertIn("[2:a:0]", graph)
        self.assertIn("concat=n=3:v=1:a=1", graph)
        self.assertIn("B.mov", notes)
        self.assertIn("[jafinal0]", text)

    def test_a_silent_first_input_no_longer_drops_every_other_inputs_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [
                make_item(Path(tmp) / "A.mov", duration=10.0, with_audio=False),
                make_item(Path(tmp) / "B.mov", duration=12.0),
                make_item(Path(tmp) / "C.mov", duration=8.0),
            ]
            graph, text, notes = _build(_answers(tmp, items), items, tmp)
        self.assertIn("concat=n=3:v=1:a=1", graph)
        self.assertIn("[1:a:0]", graph)
        self.assertIn("[2:a:0]", graph)
        self.assertIn("anullsrc", graph)
        self.assertIn("[ja0_0]", graph)
        self.assertNotIn("-an", text)
        # The recovered track IS in the output, so nothing may claim otherwise.
        self.assertNotIn("NOT in the joined output", notes)

    def test_tracks_only_a_later_input_carries_are_named_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [
                make_item(Path(tmp) / "A.mov", duration=10.0),
                make_item(Path(tmp) / "B.mov", duration=12.0),
                make_item(Path(tmp) / "C.mov", duration=8.0),
            ]
            items[2]["audio_streams"] = [audio_stream(), audio_stream(), audio_stream()]
            _graph, _text, notes = _build(_answers(tmp, items), items, tmp)
        self.assertIn("C.mov", notes)
        self.assertIn("[1, 2]", notes)

    def test_matching_inputs_produce_no_silence_and_no_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [
                make_item(Path(tmp) / "A.mov", duration=10.0),
                make_item(Path(tmp) / "B.mov", duration=12.0),
            ]
            graph, _text, notes = _build(_answers(tmp, items), items, tmp)
        self.assertNotIn("anullsrc", graph)
        self.assertNotIn("silence", notes.lower())


class JoinPromiseHonesty(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_selected_subtitles_are_reported_as_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [
                make_item(Path(tmp) / "A.mov", duration=10.0),
                make_item(Path(tmp) / "B.mov", duration=12.0),
            ]
            answers = _answers(
                tmp, items,
                subtitle_streams=[{"codec_type": "subtitle", "codec_name": "subrip"}],
                subtitle_tracks=[0],
                keep_source_subtitles=True,
            )
            _graph, text, notes = _build(answers, items, tmp)
        self.assertIn("-sn", text)
        self.assertIn("Subtitles: dropped", notes)

    def test_kept_extras_are_reported_per_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [
                make_item(Path(tmp) / "A.mov", duration=10.0),
                make_item(Path(tmp) / "B.mov", duration=12.0),
            ]
            answers = _answers(
                tmp, items,
                keep_source_metadata=True,
                keep_source_chapters=True,
                keep_embedded_attachments=True,
                attachment_streams=[{"codec_type": "attachment", "codec_name": "ttf"}],
            )
            _graph, text, notes = _build(answers, items, tmp)
        self.assertIn("-map_chapters -1", text)
        self.assertIn("Chapters: dropped", notes)
        self.assertIn("Metadata:", notes)
        self.assertIn("A.mov", notes)
        self.assertIn("Attachments", notes)

    def test_nothing_is_claimed_when_no_extras_were_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [
                make_item(Path(tmp) / "A.mov", duration=10.0),
                make_item(Path(tmp) / "B.mov", duration=12.0),
            ]
            _graph, _text, notes = _build(_answers(tmp, items), items, tmp)
        self.assertNotIn("Chapters:", notes)
        self.assertNotIn("Subtitles:", notes)


class JoinPictureFilters(unittest.TestCase):
    """A plain join -- no reverse, no split -- must still apply the picture
    edits the user asked for.

    `build_join_encode_command` normalises every input with its own chain
    (crop, fps, scale, pad, format) and then concatenates. It never called
    `build_cpu_video_filter`, and there was no second stage behind it to make
    up the difference, so on this path orientation, colour, denoise, sharpen
    and BOTH fades were built into no command at all: answered, summarised,
    and silently absent from the output.

    A staged job hid this. There, ownership hands `look` and `fade` to the
    reverse stage, which does call the single-input builder -- so the only
    symptom the staged tests could ever see was the dropped rotation. These
    tests cover the path where nothing else can compensate.
    """

    def _graph(self, **extra):
        with tempfile.TemporaryDirectory() as tmp:
            items = [make_item(Path(tmp) / "a.mkv"), make_item(Path(tmp) / "b.mkv")]
            graph, _text, _notes = _build(_answers(tmp, items, **extra), items, tmp)
        return graph

    def test_a_rotation_reaches_the_command(self):
        self.assertIn("transpose=1", self._graph(rotate_choice="90cw"))

    def test_both_flips_reach_the_command(self):
        graph = self._graph(flip_horizontal=True, flip_vertical=True)
        self.assertIn("hflip", graph)
        self.assertIn("vflip", graph)

    def test_denoise_reaches_the_command(self):
        self.assertIn("hqdn3d", self._graph(denoise_level="medium"))

    def test_a_colour_adjustment_reaches_the_command(self):
        self.assertIn("saturation=0", self._graph(adjust_grayscale=True))

    def test_both_fades_reach_the_command(self):
        graph = self._graph(fade_in_seconds=1.0, fade_out_seconds=1.0)
        self.assertIn("fade=t=in", graph)
        self.assertIn("fade=t=out", graph)

    def test_a_plain_join_adds_none_of_them_unasked(self):
        # The other direction, and the one that keeps the filters honest: a
        # join with no picture answers must build no picture stage at all.
        graph = self._graph()
        for unwanted in ("transpose=", "hflip", "vflip", "hqdn3d", "eq=", "fade=", "[jvpic]"):
            self.assertNotIn(unwanted, graph, unwanted)

    def test_the_rotation_lands_after_the_common_scale(self):
        # Order is the whole reason this is applied once, after the concat,
        # rather than per input. Every input is normalised INTO one landscape
        # canvas; rotating before that would fit a portrait frame into it and
        # pillarbox the picture instead of standing the output on its side.
        graph = self._graph(rotate_choice="90cw")
        self.assertLess(graph.index("concat=n=2"), graph.index("transpose=1"),
                        "the rotation must follow the concat, not precede it")

    def test_the_fade_out_is_timed_against_the_sped_up_output(self):
        # A fade-out measured from the SOURCE length lands past the end of a
        # faster output and never renders. Two 10s inputs at 2x are a 10s
        # output, so a 2s fade-out starts at 8s -- not at 18s.
        graph = self._graph(fade_out_seconds=2.0,
                            video_speed_enabled=True, video_speed_factor=2.0)
        self.assertIn("fade=t=out:st=8.000", graph)

    def test_the_fade_out_is_timed_against_the_whole_joined_timeline(self):
        # And without a speed change it is the SUM of the inputs, not the
        # first one: a fade timed from input 1 alone would land halfway.
        graph = self._graph(fade_out_seconds=2.0)
        self.assertIn("fade=t=out:st=18.000", graph)


if __name__ == "__main__":
    unittest.main()
