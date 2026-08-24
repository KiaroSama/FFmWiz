"""Regression: a compatible join must remux, and must never guess (B10, B11).

`join_copy_plan()` answered one question where there are three. It treated a
partial audio/subtitle/data/attachment selection, and a metadata or chapter
drop, as proof that stream copy was impossible -- although a `-map` list and
`-map_metadata`/`-map_chapters` deliver all of them without decoding a packet.
Measured on two copy-compatible H.264/AAC clips with two audio tracks each,
`video_codec=copy`, `audio_codec=copy` and audio track 1 selected:

    join_copy_compatibility   (True, [])
    plan                      supported=False
                              "only audio track(s) [1] of 2 were selected"
    route                     concat FILTER (re-encode)
    output video codec        hevc            <-- sources were h264

The opposite failure lived in the same builder. With `audio_tracks=[]` on an
audio-only join the copy route was entered anyway and the explicit map list came
out EMPTY, so the command carried no `-map` at all and FFmpeg's automatic stream
selection put the audio back:

    maps in command           0
    ffmpeg return code        0
    output streams            one FLAC audio stream   <-- explicitly deselected

Tone presence, codec names and packet hashes settle these; a command substring
would not tell a genuine copy from an encode that happens to pick the same
codec.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import FFmWiz

from join_test_helpers import make_item
from test_join_audio_selection import _MediaCase, requires_ffmpeg, FFMPEG, FFPROBE


class _JoinRouterCase(_MediaCase):
    """Drives the PUBLIC router -- `step_start_now` -- not the builders."""

    def _answers(self, items, **extra):
        first = items[0]
        answers = {
            "ffmpeg": FFMPEG or "ffmpeg", "ffprobe": FFPROBE or "ffprobe",
            "input_path": first["path"], "output_location": self._tmp,
            "video_streams": first["video_streams"], "audio_streams": first["audio_streams"],
            "subtitle_streams": first.get("subtitle_streams") or [],
            "data_streams": first.get("data_streams") or [],
            "attachment_streams": first.get("attachment_streams") or [],
            "streams": first["streams"], "format": first["format"],
            "output_ext": first["path"].suffix.lstrip(".") or "mkv",
            "video_codec": "copy", "audio_codec": "copy", "use_gpu": False,
            "resolution": "n", "fps": None,
            # Every real-media fixture states the range explicitly: an FFmpeg
            # build that cannot report one raises ColorRangeUnresolvedError
            # rather than assuming a default (B16).
            "color_range_choice": "tv",
            "detect_duplicate_audio": False,
            "join_input_items": items[1:],
        }
        answers.update(extra)
        FFmWiz.artifact_lease(answers)
        self.addCleanup(FFmWiz.release_artifacts, answers)
        return answers

    def _route(self, answers):
        """Build through the wizard step and decline execution."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), \
                mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=False), \
                mock.patch.object(FFmWiz.appio, "note", lambda *a, **k: None):
            FFmWiz.step_start_now(answers)
        return [str(part) for part in answers["cmd"]]

    def _assert_copy_route(self, cmd):
        self.assertIn("concat", cmd, "a legal remux must use the concat demuxer")
        self.assertNotIn("-filter_complex", cmd, "a legal remux must not re-encode")

    def _execute(self, answers):
        self._run(answers["cmd"])
        return Path(answers["output_path"])

    def _video_packet_hash(self, path, selector="0:v:0"):
        result = self._run([FFMPEG, "-hide_banner", "-v", "error", "-i", str(path),
                            "-map", selector, "-c", "copy", "-f", "md5", "-"])
        return (result.stdout or "").strip()

    def _reference_concat_hash(self, items, selector="0:v"):
        """The same join, done by hand with a plain concat-demuxer copy.

        Written to a real file and hashed exactly like the output is: `-f md5`
        includes container timestamps, so hashing a demuxer stream directly and
        a muxed file would differ even for byte-identical packets.
        """
        listing = self._tmp / "reference.ffconcat"
        listing.write_text(
            "ffconcat version 1.0\n"
            + "".join(f"file '{item['path'].as_posix()}'\n" for item in items),
            encoding="utf-8", newline="\n")
        reference = self._tmp / "reference_join.mkv"
        self._run([FFMPEG, "-hide_banner", "-v", "error", "-y", "-f", "concat",
                   "-safe", "0", "-i", str(listing), "-map", selector,
                   "-c", "copy", str(reference)])
        return self._video_packet_hash(reference)

    def _chapters(self, path):
        result = self._run([FFPROBE, "-v", "error", "-show_chapters", "-of", "json", str(path)])
        return json.loads(result.stdout).get("chapters") or []

    def _subtitle_text(self, path, index=0):
        dump = self._tmp / f"dump{index}.srt"
        self._run([FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", str(path),
                   "-map", f"0:s:{index}", "-c:s", "srt", str(dump)])
        return dump.read_text(encoding="utf-8", errors="replace")


@requires_ffmpeg
class CompatibleSelectiveJoinsRemux(_JoinRouterCase):
    """B10 -- map decisions and muxer policy are not reasons to decode."""

    def test_a_partial_audio_selection_stream_copies(self):
        items = [self._clip("p1", tones=(440, 880)), self._clip("p2", tones=(440, 880))]
        answers = self._answers(items, audio_tracks=[1])
        plan = FFmWiz.join_copy_plan(answers, items)
        self.assertTrue(plan["can_remux_compatibly"])
        self.assertFalse(plan["maps_every_source_stream"])

        cmd = self._route(answers)
        self._assert_copy_route(cmd)
        output = self._execute(answers)
        kinds = self._stream_kinds(output)
        self.assertEqual([("video", "h264"), ("audio", "aac")], kinds,
                         "a legal remux must leave every codec exactly as it was")
        # The RIGHT track: 880 Hz was selected, 440 Hz was not, in both segments.
        for start in (0.4, 2.4):
            self._assert_tone(output, 0, start, 1.2, 880, [440])

    def test_a_partial_audio_selection_copies_the_packets(self):
        items = [self._clip("q1", tones=(440, 880)), self._clip("q2", tones=(440, 880))]
        answers = self._answers(items, audio_tracks=[1])
        self._route(answers)
        output = self._execute(answers)
        self.assertEqual(self._reference_concat_hash(items),
                         self._video_packet_hash(output),
                         "video packets must match a plain concat-demuxer copy")

    def test_a_partial_subtitle_selection_stream_copies(self):
        items = [self._clip("s1", subtitles=("TRACK-ZERO", "TRACK-ONE")),
                 self._clip("s2", subtitles=("TRACK-ZERO", "TRACK-ONE"))]
        answers = self._answers(items, audio_tracks="all", subtitle_tracks=[0])
        cmd = self._route(answers)
        self._assert_copy_route(cmd)
        output = self._execute(answers)
        self.assertEqual(1, sum(1 for kind, _ in self._stream_kinds(output)
                                if kind == "subtitle"))
        text = self._subtitle_text(output)
        self.assertIn("TRACK-ZERO", text)
        self.assertNotIn("TRACK-ONE", text)
        self.assertEqual(self._reference_concat_hash(items),
                         self._video_packet_hash(output))

    def test_dropping_chapters_is_muxer_policy_not_a_re_encode(self):
        items = [self._chaptered("c1"), self._chaptered("c2")]
        self.assertTrue(self._chapters(items[0]["path"]), "fixture must carry chapters")
        answers = self._answers(items, audio_tracks="all", keep_source_chapters=False)
        cmd = self._route(answers)
        self._assert_copy_route(cmd)
        self.assertIn("-map_chapters", cmd)
        self.assertEqual("-1", cmd[cmd.index("-map_chapters") + 1])
        output = self._execute(answers)
        self.assertEqual([], self._chapters(output), "the chapters were asked to go")
        self.assertEqual(self._reference_concat_hash(items),
                         self._video_packet_hash(output))

    def test_dropping_metadata_is_muxer_policy_not_a_re_encode(self):
        items = [self._clip("m1"), self._clip("m2")]
        answers = self._answers(items, audio_tracks="all", keep_source_metadata=False)
        cmd = self._route(answers)
        self._assert_copy_route(cmd)
        self.assertIn("-map_metadata", cmd)
        self.assertEqual("-1", cmd[cmd.index("-map_metadata") + 1])
        output = self._execute(answers)
        self.assertEqual([("video", "h264"), ("audio", "aac")], self._stream_kinds(output))
        self.assertEqual(self._reference_concat_hash(items),
                         self._video_packet_hash(output))

    def test_dropping_attachments_stream_copies(self):
        items = [self._clip("t1", attach=True), self._clip("t2", attach=True)]
        self.assertTrue(items[0]["attachment_streams"], "fixture must carry an attachment")
        answers = self._answers(items, audio_tracks="all", keep_embedded_attachments=False)
        cmd = self._route(answers)
        self._assert_copy_route(cmd)
        output = self._execute(answers)
        kinds = [kind for kind, _ in self._stream_kinds(output)]
        self.assertNotIn("attachment", kinds)
        self.assertEqual([("video", "h264"), ("audio", "aac")], self._stream_kinds(output))

    def test_an_unasked_audio_answer_still_maps_every_track(self):
        # The explicit map list is reached because subtitles are dropped. The
        # audio question was never asked, and "never asked" is not "none".
        items = [self._clip("u1", tones=(440, 880), subtitles=("ONE",)),
                 self._clip("u2", tones=(440, 880), subtitles=("ONE",))]
        answers = self._answers(items, keep_source_subtitles=False)
        answers.pop("audio_tracks", None)
        cmd = self._route(answers)
        self._assert_copy_route(cmd)
        output = self._execute(answers)
        kinds = self._stream_kinds(output)
        self.assertEqual([("video", "h264"), ("audio", "aac"), ("audio", "aac")], kinds,
                         "an unasked audio question must keep every track")
        self._assert_tone(output, 0, 0.4, 1.2, 440, [880])
        self._assert_tone(output, 1, 0.4, 1.2, 880, [440])

    def test_a_re_encoding_codec_still_re_encodes(self):
        items = [self._clip("e1"), self._clip("e2")]
        answers = self._answers(items, audio_tracks=[0], audio_codec="aac")
        plan = FFmWiz.join_copy_plan(answers, items)
        self.assertFalse(plan["can_remux_compatibly"])
        self.assertIn("the selected audio codec re-encodes", plan["reasons"])
        self.assertIn("-filter_complex", self._route(answers))

    def test_a_video_filter_still_re_encodes(self):
        items = [self._clip("f1"), self._clip("f2")]
        answers = self._answers(items, audio_tracks="all", crop_enabled=True,
                                crop_top=8, crop_left=8, crop_right=8, crop_bottom=8)
        plan = FFmWiz.join_copy_plan(answers, items)
        self.assertFalse(plan["can_remux_compatibly"])
        self.assertIn("-filter_complex", self._route(answers))

    def test_an_empty_audio_selection_on_a_video_join_stays_video_only(self):
        # Must not regress: an explicit empty answer is authoritative, and the
        # encode path states it in the command (concat a=0 plus -an).
        items = [self._clip("v1"), self._clip("v2")]
        answers = self._answers(items, audio_tracks=[])
        cmd = self._route(answers)
        self.assertIn("-filter_complex", cmd)
        self.assertIn("-an", cmd)
        self.assertIn("a=0", cmd[cmd.index("-filter_complex") + 1])
        output = self._execute(answers)
        self.assertEqual(["video"], [kind for kind, _ in self._stream_kinds(output)])

    # ---- fixture: the base clip plus real chapters ----
    def _chaptered(self, name):
        base = self._clip(name + "_src")
        meta = self._tmp / f"{name}.ffmeta"
        meta.write_text(
            ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\n"
            f"title=Opening of {name}\n", encoding="utf-8", newline="\n")
        path = self._tmp / f"{name}.mkv"
        self._run([FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", str(base["path"]),
                   "-i", str(meta), "-map", "0", "-map_chapters", "1", "-c", "copy", str(path)])
        return self._item(path, base["duration"])


class MissingLogicalTracksAreNotRemuxed(unittest.TestCase):
    """The concat demuxer cannot synthesise what an input does not carry."""

    def _answers(self, items, **extra):
        answers = {"video_codec": "copy", "audio_codec": "copy", "fps": None}
        answers.update(extra)
        return answers

    def test_a_track_only_a_later_input_carries_cannot_be_stream_copied(self):
        items = [make_item("a.mkv", 2.0), make_item("b.mkv", 2.0)]
        items[1]["audio_streams"] = items[1]["audio_streams"] * 2
        items[1]["streams"] = items[1]["video_streams"] + items[1]["audio_streams"]
        plan = FFmWiz.join_copy_plan(self._answers(items, audio_tracks=[1]), items)
        self.assertFalse(plan["can_remux_compatibly"])
        self.assertIn("audio track(s) [1] are missing from some input and must be rebuilt",
                      plan["reasons"])

    def test_a_track_every_input_carries_is_still_a_copy_plan(self):
        items = [make_item("a.mkv", 2.0), make_item("b.mkv", 2.0)]
        for item in items:
            item["audio_streams"] = item["audio_streams"] * 2
            item["streams"] = item["video_streams"] + item["audio_streams"]
        plan = FFmWiz.join_copy_plan(self._answers(items, audio_tracks=[1]), items)
        self.assertTrue(plan["can_remux_compatibly"])
        self.assertFalse(plan["maps_every_source_stream"])


@requires_ffmpeg
class AudioOnlyJoinsRefuseAnEmptySelection(_JoinRouterCase):
    """B11 -- never hand an explicit selection to automatic stream selection."""

    def _audio_clip(self, name, frequency):
        path = self._tmp / f"{name}.mka"
        self._run([FFMPEG, "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
                   "-i", f"sine=frequency={frequency}:duration=1.0", "-c:a", "flac", str(path)])
        return self._item(path, 1.0)

    def test_an_empty_selection_is_refused_before_ffmpeg_runs(self):
        items = [self._audio_clip("a1", 440), self._audio_clip("a2", 880)]
        answers = self._answers(items, audio_tracks=[], output_ext="mka")
        before = sorted(p.name for p in self._tmp.iterdir())
        with mock.patch.object(subprocess, "Popen") as popen, \
                mock.patch.object(subprocess, "run") as run_:
            with self.assertRaises(ValueError) as caught:
                self._route(answers)
            self.assertFalse(popen.called, "FFmpeg must not be launched")
            self.assertFalse(run_.called, "FFmpeg must not be launched")
        self.assertIn("No output streams are selected", str(caught.exception))
        self.assertNotIn("cmd", answers)
        output = answers.get("output_path")
        self.assertFalse(output and Path(output).exists(), "no output file may be written")
        self.assertEqual(before, sorted(p.name for p in self._tmp.iterdir()),
                         "not even the concat list may be written")

    def test_a_complete_selection_still_stream_copies(self):
        # The guard must refuse only an EMPTY plan, not audio-only joins.
        items = [self._audio_clip("b1", 440), self._audio_clip("b2", 880)]
        answers = self._answers(items, audio_tracks=[0], output_ext="mka")
        cmd = self._route(answers)
        self._assert_copy_route(cmd)
        output = self._execute(answers)
        self.assertEqual([("audio", "flac")], self._stream_kinds(output))
        self._assert_tone(output, 0, 0.1, 0.8, 440, [880])
        self._assert_tone(output, 0, 1.1, 0.8, 880, [440])

    def test_an_unasked_audio_only_join_keeps_its_track(self):
        # A silent first input is impossible here (an audio-only join has audio
        # by definition); what must hold is that "never asked" keeps the track.
        items = [self._audio_clip("c1", 440), self._audio_clip("c2", 880)]
        answers = self._answers(items, output_ext="mka")
        answers.pop("audio_tracks", None)
        self._route(answers)
        output = self._execute(answers)
        self.assertEqual([("audio", "flac")], self._stream_kinds(output))


if __name__ == "__main__":
    unittest.main()
