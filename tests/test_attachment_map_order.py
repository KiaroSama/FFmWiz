"""Regression: an attachment must be the LAST stream a command maps.

`-map` order is output-index order, and Matroska refuses a packet-bearing
stream that arrives at a higher output index than an attachment. The builder
mapped `0:t?` with the source maps, long before the filter-complex audio
outputs, so any job whose audio comes out of a filter graph produced:

    -map 0:v:0 -map 1:s:0 -map 0:t? ... -filter_complex [0:a:0]areverse...[aout0] -map [aout0]

and died at the muxer:

    [aost#0:2/copy] Error submitting a packet to the muxer: Invalid argument
    [aost#0:3/aac]  Error submitting a packet to the muxer: Invalid argument
    [out#0/matroska] Task finished with error code: -22 (Invalid argument)

Measured on one 2 s source, reversing with the audio following the picture:

    attachment  subtitle  audio from filter graph   result
    no          yes       yes                       ok
    yes         no        yes                       ok
    yes         yes       no  (audio mapped inline) ok
    yes         yes       yes                       FAIL, -22

The non-synchronised case survived only because its audio is mapped inline,
before the attachment -- so the combination, not the attachment, is the tell.

The Split path carried the same ordering against `-map 0:d:N` and was made
uniform with it, but that one cannot fail today: Matroska is the only container
FFmWiz maps an attachment into and it rejects a data stream outright, so the
two can never appear in one output. The Split case below therefore proves only
that each part still maps its attachment last.

These tests run the produced command and probe the produced file: the argv
assertion alone would not have caught it, because the argv was accepted by
FFmpeg's parser and rejected by the muxer.
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

from artifact_guard import NoLeakedArtifacts

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")


def _run(args, timeout=300):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, encoding="utf-8",
                          errors="replace", timeout=timeout)


@requires_ffmpeg
class AttachmentsAreMappedLast(NoLeakedArtifacts, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._class_tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_attachorder_"))
        font = cls._class_tmp / "sample.ttf"
        font.write_bytes(b"\x00\x01\x00\x00" + b"ffmwiz test attachment payload" * 8)
        subs = cls._class_tmp / "sample.srt"
        subs.write_text("1\n00:00:00,200 --> 00:00:01,200\nFIRST\n\n", encoding="utf-8")
        cls.source = cls._class_tmp / "rich.mkv"
        result = _run([FFMPEG, "-hide_banner", "-v", "error", "-y",
                       "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=2",
                       "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                       "-i", str(subs),
                       "-attach", str(font),
                       "-metadata:s:t:0", "mimetype=application/x-truetype-font",
                       "-map", "0:v", "-map", "1:a", "-map", "2:s",
                       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                       # No -shortest: with a mapped subtitle input FFmpeg 8.1.1 can
                       # deadlock. Every input states its own duration.
                       "-c:a", "aac", "-c:s", "srt", str(cls.source)])
        if result.returncode != 0:
            raise unittest.SkipTest("could not build the fixture: " + (result.stderr or "")[-300:])
        cls.probe = FFmWiz.ffprobe_full_json(FFPROBE, cls.source)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._class_tmp, ignore_errors=True)

    def setUp(self):
        super().setUp()
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_attachorder_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _answers(self, **extra):
        streams = self.probe.get("streams", [])
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.source,
            "probe": self.probe, "format": self.probe.get("format", {}),
            "video_streams": [s for s in streams if s.get("codec_type") == "video"],
            "audio_streams": [s for s in streams if s.get("codec_type") == "audio"],
            "subtitle_streams": [s for s in streams if s.get("codec_type") == "subtitle"],
            "attachment_streams": [s for s in streams if s.get("codec_type") == "attachment"],
            "data_streams": [],
            "audio_tracks": [0], "subtitle_tracks": [0],
            "video_codec": "H264", "audio_codec": "aac", "use_gpu": False,
            "keep_embedded_attachments": True,
            "keep_source_subtitles": True,
            "output_location": self._tmp, "output_ext": "mkv",
            "color_range_choice": "tv", "resolution": "n",
        }
        answers.update(extra)
        self.own(answers)
        FFmWiz.artifact_lease(answers)
        FFmWiz.begin_plan(answers)
        return answers

    def _build(self, answers):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(FFmWiz.appio, "note", lambda *a, **k: None):
            return [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]

    def _map_order(self, cmd):
        return [cmd[index + 1] for index, part in enumerate(cmd) if part == "-map"]

    def _kinds(self, path):
        probe = FFmWiz.ffprobe_full_json(FFPROBE, path)
        return [s.get("codec_type") for s in probe.get("streams", [])]

    def test_the_fixture_really_carries_all_four_stream_kinds(self):
        self.assertEqual(["video", "audio", "subtitle", "attachment"],
                         self._kinds(self.source))

    def test_a_reversed_audio_encode_with_an_attachment_and_a_subtitle_muxes(self):
        answers = self._answers(audio_speed_enabled=True, reverse_audio=True,
                                audio_speed_factor=1.0)
        cmd = self._build(answers)
        order = self._map_order(cmd)
        self.assertIn("0:t?", order, "the attachment must still be mapped")
        self.assertEqual("0:t?", order[-1],
                         f"the attachment must be the last map, got {order}")
        self.assertTrue(any(part.startswith("[aout") for part in order),
                        "this job's audio has to come from the filter graph")
        result = _run(cmd)
        self.assertEqual(0, result.returncode,
                         "the command FFmpeg accepted must also mux:\n"
                         + (result.stderr or "")[-800:])
        # Sorted: a filter-graph audio stream legitimately lands after the
        # subtitle it was mapped behind. What matters is that all four kinds
        # survive and the attachment is last.
        kinds = self._kinds(Path(answers["output_path"]))
        self.assertEqual(["attachment", "audio", "subtitle", "video"], sorted(kinds))
        self.assertEqual("attachment", kinds[-1])

    def test_an_inline_audio_encode_still_keeps_the_attachment_last(self):
        # The case that always worked: audio mapped inline, before the
        # attachment. It must not regress into mapping the attachment early.
        answers = self._answers()
        cmd = self._build(answers)
        self.assertEqual("0:t?", self._map_order(cmd)[-1])
        result = _run(cmd)
        self.assertEqual(0, result.returncode, (result.stderr or "")[-800:])
        kinds = self._kinds(Path(answers["output_path"]))
        self.assertEqual(["attachment", "audio", "subtitle", "video"], sorted(kinds))
        self.assertEqual("attachment", kinds[-1])

    def test_a_cut_encode_maps_the_attachment_after_the_filter_outputs(self):
        # Multiple cut ranges also route audio through filter_complex.
        answers = self._answers(cut_keep_ranges=[(0.0, 0.5), (1.0, 1.5)])
        cmd = self._build(answers)
        order = self._map_order(cmd)
        self.assertEqual("0:t?", order[-1], f"got {order}")
        result = _run(cmd)
        self.assertEqual(0, result.returncode, (result.stderr or "")[-800:])
        self.assertIn("attachment", self._kinds(Path(answers["output_path"])))

    def test_every_split_part_maps_its_attachment_after_the_packet_streams(self):
        answers = self._answers(separator_points=[1.0])
        cmd = self._build(answers)
        # One argv, several outputs: check each output's own map run rather
        # than only the last one.
        runs: list[list[str]] = []
        current: list[str] = []
        for index, part in enumerate(cmd):
            if part == "-map":
                current.append(cmd[index + 1])
            elif current and part.endswith(".mkv") and not part.startswith("-"):
                runs.append(current)
                current = []
        self.assertTrue(runs, "the Split path must emit at least one output")
        for run in runs:
            if "0:t?" in run:
                self.assertEqual("0:t?", run[-1], f"got {run}")
        result = _run(cmd)
        self.assertEqual(0, result.returncode, (result.stderr or "")[-800:])
        for part_path in answers["split_output_paths"]:
            self.assertIn("attachment", self._kinds(Path(part_path)))


if __name__ == "__main__":
    unittest.main()
