"""Regression: joined subtitle selection and later-only topology (R04).

`join_subtitle_plan()` read `subtitle_tracks` as a yes/no flag and then took
`text_streams[0]` from every input. Measured with two text tracks per input
(relative 0 = English, relative 1 = Spanish):

    requested subtitle_tracks=[1]  ->  English was merged
    requested subtitle_tracks="all" -> ONE merged track, still English
    input 1 without subtitles       -> the whole step disappeared

The repair builds the selectable topology from ALL joined inputs, honours the
requested relative indices, and emits one merged track per selected logical
track. These tests extract the OUTPUT tracks and check cue bodies and times
numerically, so a plan that quietly substituted track 0 cannot pass.
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
import cache_test_utils

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

CLIP_SECONDS = 2.0


def _run(args, timeout=300):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, encoding="utf-8",
                          errors="replace", timeout=timeout)


def _stream(codec="subrip", language="eng", title="English"):
    return {"codec_type": "subtitle", "codec_name": codec,
            "tags": {"language": language, "title": title},
            "disposition": {"default": 0}}


def _item(streams, duration=10.0, key="subtitle_streams"):
    return {key: list(streams), "duration": duration, "path": Path("clip.mkv")}


class Topology(unittest.TestCase):
    """Plan-level selection, without touching ffmpeg."""

    def test_the_track_count_comes_from_every_input(self):
        items = [_item([]), _item([_stream(), _stream(language="spa")])]
        self.assertEqual(2, FFmWiz.join_subtitle_track_count(items))

    def test_the_primary_item_exposes_subtitles_through_streams(self):
        # The primary join input is assembled with no `subtitle_streams` key.
        primary = {"streams": [{"codec_type": "video"}, _stream()], "duration": 5.0}
        self.assertEqual(1, len(FFmWiz.item_subtitle_streams(primary)))

    def test_a_requested_index_is_honoured(self):
        items = [_item([_stream(), _stream(language="spa")])] * 2
        self.assertEqual([1], FFmWiz.selected_join_subtitle_tracks(
            {"subtitle_tracks": [1]}, items))

    def test_all_means_every_logical_track(self):
        items = [_item([_stream(), _stream(language="spa")])] * 2
        self.assertEqual([0, 1], FFmWiz.selected_join_subtitle_tracks(
            {"subtitle_tracks": "all"}, items))

    def test_an_index_no_input_has_is_ignored(self):
        items = [_item([_stream()])] * 2
        self.assertEqual([], FFmWiz.selected_join_subtitle_tracks(
            {"subtitle_tracks": [3]}, items))

    def test_an_explicit_empty_selection_still_means_none(self):
        items = [_item([_stream()])] * 2
        self.assertEqual([], FFmWiz.selected_join_subtitle_tracks(
            {"subtitle_tracks": []}, items))

    def test_a_never_asked_question_keeps_the_later_inputs_tracks(self):
        # The wizard only asks when INPUT 1 has subtitles, so an absent key on a
        # keep-subtitles job must not throw away input 2's track.
        items = [_item([]), _item([_stream()])]
        self.assertEqual([0], FFmWiz.selected_join_subtitle_tracks(
            {"keep_source_subtitles": True}, items))

    def test_keep_subtitles_off_still_means_none(self):
        items = [_item([]), _item([_stream()])]
        self.assertEqual([], FFmWiz.selected_join_subtitle_tracks(
            {"keep_source_subtitles": False}, items))

    def test_the_plan_builds_one_track_per_selected_index(self):
        items = [_item([_stream(), _stream(language="spa")]) for _ in range(2)]
        plan = FFmWiz.join_subtitle_plan({"subtitle_tracks": "all"}, items)
        self.assertTrue(plan["supported"], plan["reason"])
        self.assertEqual([0, 1], [track["index"] for track in plan["tracks"]])

    def test_the_plan_carries_each_tracks_own_metadata(self):
        items = [_item([_stream(), _stream(language="spa", title="Spanish")])
                 for _ in range(2)]
        plan = FFmWiz.join_subtitle_plan({"subtitle_tracks": [1]}, items)
        self.assertEqual("spa", plan["tracks"][0]["language"])
        self.assertEqual("Spanish", plan["tracks"][0]["title"])

    def test_an_input_missing_the_track_contributes_an_empty_segment(self):
        items = [_item([_stream(), _stream(language="spa")]), _item([_stream()])]
        plan = FFmWiz.join_subtitle_plan({"subtitle_tracks": [1]}, items)
        streams = [stream for _item_, stream, _duration in plan["tracks"][0]["segments"]]
        self.assertIsNotNone(streams[0])
        self.assertIsNone(streams[1])

    def test_a_bitmap_track_is_reported_as_dropped_not_substituted(self):
        items = [_item([_stream(), _stream(codec="hdmv_pgs_subtitle", language="spa")])
                 for _ in range(2)]
        plan = FFmWiz.join_subtitle_plan({"subtitle_tracks": "all"}, items)
        self.assertEqual([0], [track["index"] for track in plan["tracks"]])
        self.assertEqual([1], plan["dropped_tracks"])

    def test_selecting_only_a_bitmap_track_is_refused_with_a_reason(self):
        items = [_item([_stream(codec="hdmv_pgs_subtitle")]) for _ in range(2)]
        plan = FFmWiz.join_subtitle_plan({"subtitle_tracks": [0]}, items)
        self.assertFalse(plan["supported"])
        self.assertIn("bitmap", plan["reason"])


@requires_ffmpeg
class JoinedSubtitleTracksInRealOutput(unittest.TestCase):
    """Join for real, extract the output tracks, check bodies and cue times."""

    @classmethod
    def setUpClass(cls):
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_jointracks_"))
        # Two text tracks per clip: relative 0 English, relative 1 Spanish.
        cls._two_track = {
            "a": cls._build(cls._root, "a2.mkv", [("eng", "English", "ENG-A"),
                                                  ("spa", "Spanish", "SPA-A")]),
            "b": cls._build(cls._root, "b2.mkv", [("eng", "English", "ENG-B"),
                                                  ("spa", "Spanish", "SPA-B")]),
        }
        cls._one_track = cls._build(cls._root, "b1.mkv", [("eng", "English", "ENG-B")])
        cls._no_track = cls._build(cls._root, "none.mkv", [])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    @classmethod
    def _build(cls, root, name, tracks):
        args = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=10:duration={CLIP_SECONDS}",
                "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_SECONDS}"]
        for position, (_language, _title, body) in enumerate(tracks):
            srt = root / f"{name}.{position}.srt"
            srt.write_text(f"1\n00:00:00,500 --> 00:00:01,500\n{body}\n\n", encoding="utf-8")
            args += ["-i", str(srt)]
        args += ["-map", "0:v", "-map", "1:a"]
        for position in range(len(tracks)):
            args += ["-map", f"{position + 2}:s"]
        args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"]
        for position, (language, title, _body) in enumerate(tracks):
            args += [f"-c:s:{position}", "srt",
                     f"-metadata:s:s:{position}", f"language={language}",
                     f"-metadata:s:s:{position}", f"title={title}"]
        path = root / name
        args.append(str(path))
        result = _run(args)
        if result.returncode != 0:
            raise unittest.SkipTest("could not build the synthetic clip: "
                                    + result.stderr[-400:])
        return path

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._notes = []
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = self._notes.append
        self._prev_cache = os.environ.get("FFMWIZ_CACHE_DIR")
        self._cache_run_id = uuid.uuid4().hex
        self._cache_dir = cache_test_utils.create_owned_temp_cache_dir(self._cache_run_id)
        os.environ["FFMWIZ_CACHE_DIR"] = self._cache_dir
        FFmWiz.services._CAPABILITY_SESSION_MEMO.clear()
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_jointracks_case_"))

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

    def _media_item(self, path):
        probe = self._probe(path)
        streams = probe["streams"]
        return {"path": path, "probe": probe, "format": probe["format"],
                "streams": streams,
                "video_streams": [s for s in streams if s["codec_type"] == "video"],
                "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
                "subtitle_streams": [s for s in streams if s["codec_type"] == "subtitle"],
                "attachment_streams": [], "data_streams": [],
                "duration": float(probe["format"]["duration"])}

    def _join(self, paths, drop_primary_subtitle_key=False, **extra):
        items = [self._media_item(path) for path in paths]
        first = items[0]
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": first["path"],
            "output_location": self._tmp, "streams": first["streams"],
            "format": first["format"], "video_streams": first["video_streams"],
            "audio_streams": first["audio_streams"],
            "subtitle_streams": first["subtitle_streams"],
            "data_streams": [], "attachment_streams": [],
            "output_ext": "mkv", "video_codec": "H264", "use_gpu": False,
            "audio_codec": "aac", "audio_bitrate_kbps": 96, "audio_tracks": [0],
            "resolution": "n", "fps": 10, "video_bitrate_kbps": 300,
            "color_range_choice": "tv", "join_input_items": items[1:],
            "keep_source_subtitles": True,
        }
        answers.update(extra)
        if drop_primary_subtitle_key:
            # step_start_now builds the primary item without this key while the
            # answers dict still carries input 1's probed streams.
            items[0].pop("subtitle_streams", None)
        cmd = FFmWiz.build_join_encode_command(answers, items, self._tmp / "joined.mkv")
        self.addCleanup(FFmWiz.cleanup_join_concat_list, answers)
        result = _run(cmd)
        self.assertEqual(0, result.returncode,
                         f"ffmpeg failed:\n{result.stderr[-1500:]}")
        return answers, Path(answers["output_path"])

    def _tracks(self, path):
        probe = self._probe(path)
        subtitles = [s for s in probe["streams"] if s["codec_type"] == "subtitle"]
        result = []
        for position, stream in enumerate(subtitles):
            destination = self._tmp / f"out{position}.srt"
            if destination.exists():
                destination.unlink()
            _run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                  "-i", str(path), "-map", f"0:s:{position}", "-c:s", "srt",
                  str(destination)])
            text = destination.read_text(encoding="utf-8", errors="replace") if destination.exists() else ""
            tags = {key.lower(): value for key, value in (stream.get("tags") or {}).items()}
            result.append({"language": tags.get("language"), "title": tags.get("title"),
                           "default": (stream.get("disposition") or {}).get("default"),
                           "cues": FFmWiz.parse_srt(text)})
        return result

    def _bodies(self, track):
        return [cue[2] for cue in track["cues"]]

    # ---- the mandatory cases -------------------------------------------
    def test_selecting_relative_track_one_muxes_spanish_not_english(self):
        _answers, output = self._join(
            [self._two_track["a"], self._two_track["b"]], subtitle_tracks=[1])
        tracks = self._tracks(output)
        self.assertEqual(1, len(tracks))
        self.assertEqual(["SPA-A", "SPA-B"], self._bodies(tracks[0]))
        self.assertEqual("spa", tracks[0]["language"])
        self.assertEqual("Spanish", tracks[0]["title"])

    def test_the_second_inputs_cues_are_shifted_onto_the_joined_clock(self):
        _answers, output = self._join(
            [self._two_track["a"], self._two_track["b"]], subtitle_tracks=[1])
        cues = self._tracks(output)[0]["cues"]
        self.assertAlmostEqual(0.5, cues[0][0], delta=0.06)
        self.assertAlmostEqual(1.5, cues[0][1], delta=0.06)
        self.assertAlmostEqual(CLIP_SECONDS + 0.5, cues[1][0], delta=0.15)
        self.assertAlmostEqual(CLIP_SECONDS + 1.5, cues[1][1], delta=0.15)

    def test_all_produces_both_logical_tracks(self):
        _answers, output = self._join(
            [self._two_track["a"], self._two_track["b"]], subtitle_tracks="all")
        tracks = self._tracks(output)
        self.assertEqual(2, len(tracks))
        self.assertEqual(["ENG-A", "ENG-B"], self._bodies(tracks[0]))
        self.assertEqual(["SPA-A", "SPA-B"], self._bodies(tracks[1]))
        self.assertEqual(["eng", "spa"], [track["language"] for track in tracks])

    def test_a_first_input_without_subtitles_does_not_hide_the_later_ones(self):
        # `subtitle_tracks` is absent because the wizard never asks when input 1
        # has no subtitle stream.
        _answers, output = self._join([self._no_track, self._two_track["b"]])
        tracks = self._tracks(output)
        self.assertEqual(2, len(tracks))
        self.assertEqual(["ENG-B"], self._bodies(tracks[0]))
        self.assertEqual(["SPA-B"], self._bodies(tracks[1]))
        # Input 1 contributed no cues but must still advance the clock.
        self.assertAlmostEqual(CLIP_SECONDS + 0.5, tracks[0]["cues"][0][0], delta=0.15)

    def test_an_input_missing_one_selected_track_leaves_a_gap_not_a_substitute(self):
        _answers, output = self._join(
            [self._two_track["a"], self._one_track], subtitle_tracks="all")
        tracks = self._tracks(output)
        self.assertEqual(2, len(tracks))
        self.assertEqual(["ENG-A", "ENG-B"], self._bodies(tracks[0]))
        # Input 2 has no Spanish track; its segment stays empty rather than
        # borrowing that input's English one.
        self.assertEqual(["SPA-A"], self._bodies(tracks[1]))

    def test_the_primary_item_shape_the_wizard_really_builds_keeps_its_tracks(self):
        # step_start_now assembles the PRIMARY join item without a
        # `subtitle_streams` key -- its subtitles are only inside `streams`. Read
        # through the key alone, input 1 looked subtitle-free and every one of
        # its cues was lost.
        _answers, output = self._join(
            [self._two_track["a"], self._two_track["b"]],
            drop_primary_subtitle_key=True, subtitle_tracks="all")
        tracks = self._tracks(output)
        self.assertEqual(2, len(tracks))
        self.assertEqual(["ENG-A", "ENG-B"], self._bodies(tracks[0]))
        self.assertEqual(["SPA-A", "SPA-B"], self._bodies(tracks[1]))

    def test_a_mixed_text_and_bitmap_selection_states_what_it_dropped(self):
        items = [self._media_item(path)
                 for path in (self._two_track["a"], self._two_track["b"])]
        # The installed FFmpeg cannot encode text subtitles into a bitmap codec,
        # so the bitmap track is declared on the probed stream -- the field the
        # plan actually reads.
        for item in items:
            item["subtitle_streams"][1] = {**item["subtitle_streams"][1],
                                           "codec_name": "hdmv_pgs_subtitle"}
        plan = FFmWiz.join_subtitle_plan({"subtitle_tracks": "all"}, items)
        self.assertEqual([0], [track["index"] for track in plan["tracks"]])
        self.assertEqual([1], plan["dropped_tracks"])
        notes = " ".join(FFmWiz.join_extras_outcome_notes(
            {"subtitle_tracks": "all", "keep_source_subtitles": True}, items)).lower()
        self.assertIn("merged text track 0", notes)
        self.assertIn("track 1 is dropped", notes)
        self.assertIn("bitmap", notes)


if __name__ == "__main__":
    unittest.main()
