"""Regression: a joined audio track is judged by ITS OWN carriers (B12).

`with_join_audio_view()` lends one representative stream dict per LOGICAL
joined audio track, but it lent nothing else. `packet_sizes`,
`audio_volume_stats` and `audio_duplicate_report` all belong to the PRIMARY
input and are keyed by ABSOLUTE stream index, so a track borrowed from a later
input was measured against whatever input 1 happens to hold at that index.

Reproduced with a primary whose audio is absolute index 1 and whose absolute
index 2 is a 2-byte subtitle, joined to an input carrying a real German track
at absolute index 2:

    view                       [1, 2]        ->  [0, 1]   (logical positions)
    size of the German track   2 bytes       ->  46,964
    bitrate of it              1 kbps        ->  124
    empty_tracks               [1]           ->  []
    auto_select(..., "de")     [0]           ->  [0, 1]

The German track was dropped from the joined output as "empty" while being the
only reason the user added that input. The program-level rules are asserted one
by one below, and the finished join is decoded and read with a Goertzel filter,
because `volumedetect` writes its report to stderr at info level and FFmWiz runs
with `-v error`.
"""
import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")

CLIP = 1.5           # seconds per input
TONE_ENERGY = 20.0   # measured: a real tone reads far above this, silence ~0


def _run(args, timeout=600):
    return subprocess.run([str(part) for part in args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                          timeout=timeout)


def _goertzel(samples, rate, frequency):
    """Energy at one frequency. Enough to tell 440 Hz, 880 Hz and silence apart."""
    count = len(samples)
    if not count:
        return 0.0
    bin_index = int(0.5 + count * frequency / rate)
    omega = 2 * math.pi * bin_index / count
    coefficient = 2 * math.cos(omega)
    first = second = 0.0
    for value in samples:
        current = value + coefficient * first - second
        second, first = first, current
    return math.sqrt(max(0.0, first * first + second * second
                         - coefficient * first * second)) / count


@requires_ffmpeg
class JoinedAudioKnowsItsCarriers(NoLeakedArtifacts, unittest.TestCase):
    """Real inputs whose logical track 1 lives in different files.

    `primary`      -- video, 440 Hz audio at absolute 1, a 2-byte subtitle at
                      absolute 2. That subtitle is what the borrowed track used
                      to be measured against.
    `later`        -- video, 440 Hz (eng) at absolute 1, 880 Hz (deu) at
                      absolute 2. The colliding index is the whole point.
    `silent_first` -- video, 440 Hz at absolute 1, digital silence at absolute 2.
    `twin`         -- video, 440 Hz at absolute 1 and the identical 440 Hz again
                      at absolute 2.
    `wide`         -- video, 440 Hz mono at absolute 1, 880 Hz STEREO at 2.
                      lavfi's `sine` is mono, so widening one track is what makes
                      the layouts differ at all.
    """

    @classmethod
    def setUpClass(cls):
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_joinaud_"))
        subtitle = cls._root / "tiny.srt"
        subtitle.write_text("1\n00:00:00,100 --> 00:00:00,600\nhi\n\n",
                            encoding="utf-8", newline="\n")

        cls.primary = cls._clip("primary", "red", [("sine=frequency=440", "eng", None)],
                                subtitle=subtitle)
        cls.later = cls._clip("later", "blue", [("sine=frequency=440", "eng", None),
                                                ("sine=frequency=880", "deu", None)])
        cls.silent_first = cls._clip("silent_first", "green",
                                     [("sine=frequency=440", "eng", None),
                                      ("anullsrc=channel_layout=stereo:sample_rate=44100",
                                       "deu", None)])
        cls.twin = cls._clip("twin", "white", [("sine=frequency=440", "eng", None),
                                               ("sine=frequency=440", "eng", None)])
        cls.wide = cls._clip("wide", "black", [("sine=frequency=440", "eng", None),
                                               ("sine=frequency=880", "deu", 2)])

        # Guard the guard: the collision these measurements need has to exist.
        primary_streams = cls._probe(cls.primary, "-show_streams")["streams"]
        later_streams = cls._probe(cls.later, "-show_streams")["streams"]
        collision = (primary_streams[2]["codec_type"] == "subtitle"
                     and later_streams[2]["codec_type"] == "audio")
        if not collision:
            raise unittest.SkipTest(
                "this ffmpeg did not lay the fixtures out with a subtitle and an audio "
                "stream sharing absolute index 2, so the collision is not reproduced")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    @classmethod
    def _clip(cls, name, colour, tracks, subtitle=None):
        path = cls._root / f"{name}.mkv"
        args = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"color=c={colour}:s=160x90:r=25:d={CLIP}"]
        for source, _language, _channels in tracks:
            args += ["-f", "lavfi", "-i", f"{source}:duration={CLIP}"
                     if source.startswith("sine") else source]
        if subtitle is not None:
            args += ["-i", str(subtitle)]
        args += ["-map", "0:v"]
        for position, _track in enumerate(tracks, start=1):
            args += ["-map", f"{position}:a"]
        if subtitle is not None:
            args += ["-map", f"{len(tracks) + 1}:s", "-c:s", "srt"]
        args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "128k", "-t", str(CLIP)]
        for position, (_source, language, channels) in enumerate(tracks):
            args += [f"-metadata:s:a:{position}", f"language={language}"]
            if channels:
                args += [f"-ac:a:{position}", str(channels)]
        result = _run([*args, str(path)])
        if result.returncode != 0:
            raise unittest.SkipTest(f"could not build {name}: {result.stderr[-400:]}")
        return path

    @classmethod
    def _probe(cls, path, *args):
        return json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                *args, str(path)]).stdout or "{}")

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="joinaud_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *args, **kwargs: None
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", self._real_note))

    # ---- answers --------------------------------------------------------
    def _item(self, path):
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

    def _answers(self, paths, **extra):
        items = [self._item(path) for path in paths]
        first = items[0]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": first["path"],
            "probe": first["probe"], "format": first["format"], "streams": first["streams"],
            "video_streams": first["video_streams"], "audio_streams": first["audio_streams"],
            "subtitle_streams": first["subtitle_streams"],
            "data_streams": [], "attachment_streams": [],
            "output_location": self._tmp, "output_ext": "mkv",
            "video_codec": "H264", "video_encoder": "libx264", "crf": 28,
            "preset": "ultrafast", "use_gpu": False, "audio_codec": "aac",
            "audio_bitrate_kbps": 96, "resolution": "n", "subtitle_tracks": [],
            "keep_source_subtitles": False, "detect_duplicate_audio": True,
            # Older FFmpeg builds refuse an unresolved source range.
            "color_range_choice": "tv",
            "join_input_items": items[1:],
        })
        answers.update(extra)
        return answers, items

    def _inside_the_view(self, answers, probe):
        """Run `probe(answers)` with the joined view lent, as a wizard step is."""
        captured = {}

        def step(lent):
            captured.update(probe(lent))

        FFmWiz.with_join_audio_view(step)(answers)
        return captured

    # ---- the defect -----------------------------------------------------
    def test_a_later_only_track_is_no_longer_auto_dropped_as_empty(self):
        answers, _items = self._answers([self.primary, self.later])
        seen = self._inside_the_view(answers, lambda lent: {
            "languages": [(s.get("tags") or {}).get("language") for s in lent["audio_streams"]],
            "selected": FFmWiz.auto_select_audio_tracks(lent, "de"),
            "report": FFmWiz.detect_duplicate_audio(lent),
            "sizes": [FFmWiz.stream_size_bytes(
                stream, lent.get("format"), FFmWiz.services.get_packet_sizes(lent))[0]
                for stream in lent["audio_streams"]],
            "bitrates": [FFmWiz.stream_bitrate_kbps(
                stream, lent.get("format"), FFmWiz.services.get_packet_sizes(lent))
                for stream in lent["audio_streams"]],
        })
        self.assertEqual(["eng", "deu"], seen["languages"])
        # Was [0]: the German track counted as empty.
        self.assertEqual([0, 1], seen["selected"])
        self.assertEqual(set(), set(seen["report"]["empty_tracks"]))
        # Was 2 bytes / 1 kbps, read off the primary's subtitle at absolute 2.
        true_size = FFmWiz.services.probe_packet_sizes(FFPROBE, self.later)[2]
        self.assertEqual(true_size, seen["sizes"][1])
        self.assertGreater(seen["bitrates"][1], 100)

    def test_the_two_inputs_really_collide_on_absolute_index_two(self):
        # Guard the guard. Without the collision the assertion above proves
        # nothing about provenance.
        primary_sizes = FFmWiz.services.probe_packet_sizes(FFPROBE, self.primary)
        later_sizes = FFmWiz.services.probe_packet_sizes(FFPROBE, self.later)
        self.assertLess(primary_sizes[2], 1000, "primary's absolute 2 must be the tiny subtitle")
        self.assertGreater(later_sizes[2], 10000, "later's absolute 2 must be real audio")

    def test_each_logical_track_reports_its_own_carrier_size(self):
        answers, _items = self._answers([self.primary, self.later])
        model = FFmWiz.joined_audio_track_model(answers)
        self.assertEqual([0, 1], [stream["index"] for stream in model["view"]])
        self.assertEqual(FFmWiz.services.probe_packet_sizes(FFPROBE, self.primary)[1],
                         model["packet_sizes"][0])
        self.assertEqual(FFmWiz.services.probe_packet_sizes(FFPROBE, self.later)[2],
                         model["packet_sizes"][1])

    def test_a_track_whose_first_carrier_is_silent_is_not_empty(self):
        # silent_first's track 1 is digital silence; later's track 1 is 880 Hz.
        answers, _items = self._answers([self.silent_first, self.later])
        model = FFmWiz.joined_audio_track_model(answers)
        self.assertEqual(2, len(model["carriers"][1]), "both inputs carry track 1")
        self.assertNotIn(1, model["report"]["empty_tracks"])
        seen = self._inside_the_view(answers, lambda lent: {
            "selected": FFmWiz.auto_select_audio_tracks(lent, "de")})
        self.assertIn(1, seen["selected"])

    def test_a_track_every_carrier_reports_empty_stays_empty(self):
        answers, _items = self._answers([self.silent_first, self.silent_first])
        model = FFmWiz.joined_audio_track_model(answers)
        self.assertIn(1, model["report"]["empty_tracks"])
        seen = self._inside_the_view(answers, lambda lent: {
            "selected": FFmWiz.auto_select_audio_tracks(lent, "e")})
        self.assertEqual([0], seen["selected"])

    def test_a_duplicate_in_only_one_input_is_not_dropped(self):
        # `twin` carries the same tone twice; `later` carries 440 and 880. The
        # pair is redundant in input 1 and NOT in input 2, so dropping it would
        # lose input 2's German track.
        answers, _items = self._answers([self.twin, self.later])
        model = FFmWiz.joined_audio_track_model(answers)
        self.assertEqual([], model["report"]["confirmed_pairs"])
        seen = self._inside_the_view(answers, lambda lent: {
            "selected": FFmWiz.auto_select_audio_tracks(lent, "d")})
        self.assertEqual([0, 1], seen["selected"])

    def test_a_duplicate_in_every_input_is_dropped(self):
        answers, _items = self._answers([self.twin, self.twin])
        model = FFmWiz.joined_audio_track_model(answers)
        self.assertEqual([(0, 1)], model["report"]["confirmed_pairs"])
        seen = self._inside_the_view(answers, lambda lent: {
            "selected": FFmWiz.auto_select_audio_tracks(lent, "d")})
        self.assertEqual([0], seen["selected"])

    def test_differing_channel_layouts_stay_one_logical_track(self):
        # `wide`'s track 1 is 2-channel 880 Hz, `later`'s is 1-channel 880 Hz.
        answers, items = self._answers([self.wide, self.later])
        model = FFmWiz.joined_audio_track_model(answers)
        self.assertEqual(2, len(model["view"]))
        self.assertEqual(2, len(model["carriers"][1]), "both inputs carry track 1")
        # The representative is the FIRST carrier, described as it really is.
        self.assertEqual(2, int(model["view"][1]["channels"]))
        self.assertEqual(1, int(model["carriers"][1][1][2]["channels"]))
        self.assertEqual([], model["report"]["confirmed_pairs"],
                         "a mono and a stereo track are not the same stream")
        # The join still has to give concat one layout, and it takes the widest.
        self.assertEqual("stereo", FFmWiz.join_target_channel_layout(items))

    def test_the_language_of_a_borrowed_track_survives_selection(self):
        answers, _items = self._answers([self.primary, self.later])
        seen = self._inside_the_view(answers, lambda lent: {
            "titles": [FFmWiz.stream_title(stream, index)
                       for index, stream in enumerate(lent["audio_streams"])]})
        self.assertIn("lang=", seen["titles"][1])
        self.assertNotIn("lang=English", seen["titles"][1])

    def test_the_primary_caches_are_handed_straight_back(self):
        answers, _items = self._answers([self.primary, self.later])
        primary_sizes = FFmWiz.services.get_packet_sizes(answers)
        primary_report = FFmWiz.detect_duplicate_audio(answers)
        primary_streams = answers["audio_streams"]
        self._inside_the_view(answers, lambda lent: {})
        self.assertIs(primary_sizes, answers["packet_sizes"])
        self.assertIs(primary_report, answers["audio_duplicate_report"])
        self.assertIs(primary_streams, answers["audio_streams"])

    def test_what_is_left_behind_is_the_primary_file_and_only_it(self):
        # The model computes the primary's own caches on the way past, which is
        # the point -- they are the primary's, keyed by ABSOLUTE index, and the
        # rest of the wizard reads them there. What must never survive the lend
        # is the joined view's logical numbering.
        answers, _items = self._answers([self.primary, self.later])
        for key in ("packet_sizes", "audio_volume_stats", "audio_duplicate_report"):
            answers.pop(key, None)
        self._inside_the_view(answers, lambda lent: {})
        self.assertEqual(FFmWiz.services.probe_packet_sizes(FFPROBE, self.primary),
                         answers["packet_sizes"])
        self.assertEqual(1, len(answers["audio_streams"]))

    def test_a_plain_single_input_job_is_left_completely_alone(self):
        answers, _items = self._answers([self.primary])
        answers.pop("join_input_items", None)
        answers["packet_sizes"] = {"sentinel": 1}
        seen = self._inside_the_view(answers, lambda lent: {
            "streams": lent["audio_streams"], "sizes": lent["packet_sizes"]})
        self.assertIs(answers["audio_streams"], seen["streams"])
        self.assertEqual({"sentinel": 1}, seen["sizes"])

    # ---- the finished media --------------------------------------------
    def _track_energy(self, path, track, start, frequency, seconds=0.6):
        wav = self._tmp / f"probe_{track}_{start:.1f}_{frequency}.wav"
        _run([FFMPEG, "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(path),
              "-t", str(seconds), "-map", f"0:a:{track}", "-ac", "1", "-ar", "16000",
              "-f", "wav", str(wav)])
        if not wav.exists() or wav.stat().st_size < 200:
            return 0.0
        with wave.open(str(wav)) as handle:
            raw = handle.readframes(handle.getnframes())
            rate = handle.getframerate()
        samples = struct.unpack(f"<{len(raw) // 2}h", raw)
        return _goertzel(samples, rate, frequency)

    def test_the_selected_later_only_track_reaches_the_finished_file(self):
        answers, items = self._answers([self.primary, self.later])
        seen = self._inside_the_view(answers, lambda lent: {
            "selected": FFmWiz.auto_select_audio_tracks(lent, "de")})
        answers["audio_tracks"] = seen["selected"]
        self.assertEqual([0, 1], answers["audio_tracks"])
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            cmd = FFmWiz.build_join_encode_command(answers, items, self._tmp / "joined.mkv")
        result = _run([str(part) for part in cmd])
        self.assertEqual(0, result.returncode, result.stderr[-1200:])
        output = Path(answers["output_path"])
        audio = [s for s in self._probe(output, "-show_streams")["streams"]
                 if s["codec_type"] == "audio"]
        self.assertEqual(2, len(audio), "both selected tracks must be muxed")
        # Track 0 is 440 Hz throughout: both inputs carry it.
        self.assertGreater(self._track_energy(output, 0, 0.4, 440), TONE_ENERGY)
        self.assertGreater(self._track_energy(output, 0, CLIP + 0.4, 440), TONE_ENERGY)
        # Track 1 only exists in input 2, so it is silence then 880 Hz -- and
        # the 880 Hz half is exactly what used to be dropped.
        self.assertLess(self._track_energy(output, 1, 0.4, 880), TONE_ENERGY)
        self.assertGreater(self._track_energy(output, 1, CLIP + 0.4, 880), TONE_ENERGY)
        self.assertLess(self._track_energy(output, 1, CLIP + 0.4, 440), TONE_ENERGY)


if __name__ == "__main__":
    unittest.main()
