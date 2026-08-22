"""Track Manager: external-track mapping, metadata, and container compatibility.

Covers two defects that lived in build_track_manager_command:
- every prompted external stream is mapped and its title/language answers reach
  FFmpeg (previously only :a:0/:s:0 was mapped and no -metadata:s:* was emitted);
- external subtitles are transcoded when the source container cannot carry them
  (MP4 + .srt used to fail with "Could not find tag for codec subrip" and leave
  a 0-byte output).
"""
from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import FFmWiz

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(bool(FFMPEG and FFPROBE), "ffmpeg/ffprobe not available")


def sub_item(path, codecs, metadata=None):
    item = {
        "path": Path(path),
        "audio_streams": [],
        "subtitle_streams": [{"codec_type": "subtitle", "codec_name": c} for c in codecs],
    }
    if metadata is not None:
        item["subtitle_metadata"] = metadata
    return item


def probe(audio=0, subtitle=0):
    streams = [{"index": 0, "codec_type": "video", "codec_name": "h264"}]
    streams += [{"index": 1 + i, "codec_type": "audio", "codec_name": "aac"} for i in range(audio)]
    streams += [{"index": 1 + audio + i, "codec_type": "subtitle", "codec_name": "subrip"}
                for i in range(subtitle)]
    return {"streams": streams}


class KeptSourceCountTests(unittest.TestCase):
    def test_counts_survivors_not_raw_totals(self):
        streams = probe(audio=2, subtitle=1)["streams"]
        self.assertEqual(FFmWiz.track_manager_source_stream_counts(streams, []), (2, 1))
        self.assertEqual(FFmWiz.track_manager_source_stream_counts(streams, ["a:0"]), (1, 1))
        self.assertEqual(FFmWiz.track_manager_source_stream_counts(streams, ["s:0"]), (2, 0))

    def test_absolute_index_spec_is_resolved_by_position(self):
        streams = probe(audio=2, subtitle=1)["streams"]
        # Absolute stream #2 is the second audio stream.
        self.assertEqual(FFmWiz.track_manager_source_stream_counts(streams, ["2"]), (1, 1))

    def test_no_probe_falls_back_to_zero(self):
        self.assertEqual(FFmWiz.track_manager_source_stream_counts([], ["a:0"]), (0, 0))


class ExternalStreamMappingTests(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_every_prompted_subtitle_stream_is_mapped(self):
        item = sub_item("subs.mkv", ["subrip", "subrip"])
        cmd = FFmWiz.build_track_manager_command(
            "ffmpeg", Path("in.mkv"), [], [item], Path("out.mkv"))
        self.assertIn("1:s:0", cmd)
        self.assertIn("1:s:1", cmd)

    def test_metadata_answers_reach_ffmpeg_with_output_indexes(self):
        item = sub_item("sub.srt", ["subrip"], [{"language": "eng", "title": "English"}])
        answers = {"probe": probe(audio=2, subtitle=1)}
        cmd = FFmWiz.build_track_manager_command(
            "ffmpeg", Path("in.mkv"), ["a:0"], [item], Path("out.mkv"), answers)
        # The source subtitle survives, so the added one is output subtitle #1.
        self.assertIn("-metadata:s:s:1", cmd)
        self.assertIn("language=eng", cmd)
        self.assertIn("title=English", cmd)
        self.assertNotIn("-metadata:s:s:0", cmd)

    def test_audio_metadata_indexes_skip_removed_source_tracks(self):
        item = {"path": Path("x.aac"), "subtitle_streams": [],
                "audio_streams": [{"codec_type": "audio", "codec_name": "aac"}],
                "audio_metadata": [{"language": "fra"}]}
        answers = {"probe": probe(audio=2)}
        cmd = FFmWiz.build_track_manager_command(
            "ffmpeg", Path("in.mkv"), ["a:1"], [item], Path("out.mkv"), answers)
        self.assertIn("-metadata:s:a:1", cmd)
        self.assertIn("language=fra", cmd)

    def test_metadata_is_dropped_when_the_user_asked_for_a_clean_output(self):
        item = sub_item("sub.srt", ["subrip"], [{"language": "eng"}])
        answers = {"probe": probe(), "track_manager_keep_metadata": False}
        cmd = FFmWiz.build_track_manager_command(
            "ffmpeg", Path("in.mkv"), [], [item], Path("out.mkv"), answers)
        self.assertNotIn("language=eng", cmd)


class SubtitleContainerCompatibilityTests(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_srt_into_mp4_is_transcoded_after_the_copy(self):
        item = sub_item("sub.srt", ["subrip"])
        cmd = FFmWiz.build_track_manager_command(
            "ffmpeg", Path("in.mp4"), [], [item], Path("out.mp4"))
        self.assertIn("-c:s:0", cmd)
        self.assertEqual(cmd[cmd.index("-c:s:0") + 1], "mov_text")
        # Per-stream codec must come after the global "-c copy" to win.
        self.assertGreater(cmd.index("-c:s:0"), cmd.index("-c"))

    def test_srt_into_mkv_stays_a_stream_copy(self):
        item = sub_item("sub.srt", ["subrip"])
        cmd = FFmWiz.build_track_manager_command(
            "ffmpeg", Path("in.mkv"), [], [item], Path("out.mkv"))
        self.assertFalse([a for a in cmd if str(a).startswith("-c:s")])

    def test_mov_text_into_mkv_is_transcoded_to_srt(self):
        item = sub_item("sub.mp4", ["mov_text"])
        cmd = FFmWiz.build_track_manager_command(
            "ffmpeg", Path("in.mkv"), [], [item], Path("out.mkv"))
        self.assertEqual(cmd[cmd.index("-c:s:0") + 1], "srt")

    def test_impossible_combination_is_reported_not_built(self):
        self.assertEqual(
            FFmWiz.track_manager_subtitle_container_problems(".mkv", [sub_item("s.srt", ["subrip"])]), [])
        self.assertEqual(
            FFmWiz.track_manager_subtitle_container_problems(".mp4", [sub_item("s.srt", ["subrip"])]), [])
        problems = FFmWiz.track_manager_subtitle_container_problems(
            ".mp4", [sub_item("s.sup", ["hdmv_pgs_subtitle"])])
        self.assertTrue(problems)
        self.assertIn("hdmv_pgs_subtitle", problems[0])

    def test_single_file_confirm_aborts_on_an_impossible_subtitle(self):
        answers = {"ffmpeg": "ffmpeg", "input_path": Path("in.mp4"),
                   "probe": {"streams": [{"index": 0, "codec_type": "video"}]},
                   "format": {"duration": "10"}}
        item = sub_item("s.sup", ["hdmv_pgs_subtitle"])
        buf = io.StringIO()
        with mock.patch.object(FFmWiz.trackmanager, "ask_track_manager_source", lambda a: None), \
             mock.patch.object(FFmWiz.trackmanager, "print_source_info", lambda a: None), \
             mock.patch.object(FFmWiz.trackmanager, "print_track_list", lambda a: None), \
             mock.patch.object(FFmWiz.trackmanager, "ask_track_remove_specs", lambda a, c: []), \
             mock.patch.object(FFmWiz.trackmanager, "_track_manager_collect_externals",
                               lambda a: [item]), \
             mock.patch.object(FFmWiz.trackmanager, "_track_manager_ask_loudnorm", lambda a: None), \
             mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
             mock.patch.object(FFmWiz.trackmanager, "run_ffmpeg_with_progress",
                               side_effect=AssertionError("must not start ffmpeg")):
            with contextlib.redirect_stdout(buf):
                result = FFmWiz._run_track_manager_single(answers)
        self.assertIsNone(result)
        self.assertIn("hdmv_pgs_subtitle", buf.getvalue())


@requires_ffmpeg
class PracticalExternalSubtitleMuxTests(unittest.TestCase):
    """Real ffmpeg: the generated argv must actually mux, not die at header write."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_tm_container_"))
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def _run(self, cmd):
        return subprocess.run([str(a) for a in cmd], capture_output=True, text=True, timeout=180)

    def _source(self, name):
        path = self._tmp / name
        self.assertEqual(self._run([
            FFMPEG, "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc=320x240:rate=10:duration=1",
            "-f", "lavfi", "-i", "sine=440:duration=1", "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest", str(path)]).returncode, 0)
        return path

    def _srt(self):
        path = self._tmp / "sub.srt"
        path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n\n", encoding="utf-8")
        return path

    def _mux(self, source_name, ext_path, ext_codec):
        src = self._source(source_name)
        out = self._tmp / f"out_{source_name}"
        item = sub_item(ext_path, [ext_codec])
        cmd = FFmWiz.build_track_manager_command(FFMPEG, src, [], [item], out)
        result = self._run(cmd)
        self.assertEqual(result.returncode, 0, (result.stderr or "")[-800:])
        self.assertTrue(out.exists() and out.stat().st_size > 0)
        return out

    def _subtitle_codecs(self, path):
        result = self._run([FFPROBE, "-v", "error", "-select_streams", "s",
                            "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)])
        return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]

    def test_mp4_plus_srt(self):
        out = self._mux("src.mp4", self._srt(), "subrip")
        self.assertEqual(self._subtitle_codecs(out), ["mov_text"])

    def test_mkv_plus_srt(self):
        out = self._mux("src.mkv", self._srt(), "subrip")
        self.assertEqual(self._subtitle_codecs(out), ["subrip"])

    def test_mp4_plus_mov_text(self):
        # An external MP4 whose subtitle is already mov_text must stay a copy.
        carrier = self._tmp / "carrier.mp4"
        self.assertEqual(self._run([
            FFMPEG, "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc=160x120:rate=10:duration=1",
            "-i", str(self._srt()), "-c:v", "libx264", "-preset", "ultrafast",
            "-c:s", "mov_text", str(carrier)]).returncode, 0)
        out = self._mux("src2.mp4", carrier, "mov_text")
        self.assertEqual(self._subtitle_codecs(out), ["mov_text"])


if __name__ == "__main__":
    unittest.main()
