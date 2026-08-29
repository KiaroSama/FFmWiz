"""Regression: a video-only reverse keeps every stream it was given (D03, D04).

`run_segmented_reverse_main_encode()` separates the reversed picture from the
forward-order audio correctly, and then muxed the two back together with

    -map 0:v -map 1:a

Once ANY explicit map is supplied FFmpeg stops selecting anything else, so
every other stream type was discarded in the last command of the pipeline --
after the earlier stages had carried it faithfully, and after the user had been
told the subtitles were retimed:

    Subtitles: 1 text track(s) were retimed onto the processed timeline
    SOURCE topology  ['video', 'audio', 'subtitle', 'attachment']
    OUTPUT topology  ['video', 'audio']

`bounded_reverse_plan()` carried a second hand-written copy of the same map
list, so the exported manual plan lost them too.

A quieter half came out while tracing it. The concat DEMUXER does not carry
dispositions, and only this path relies on it:

    SOURCE                       default=1 forced=1
    reverse_encode_seg_0001.mkv  default=1 forced=1
    video_reversed.mkv           default=0 forced=0
    FINAL                        default=0 forced=0

An ordinary re-encode and a 2x speed change both preserved them, which is why
nothing else caught it. Both halves are now one policy object shared by the
executor and the exported plan.
"""
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts
from cue_clock import read_cues
from ffmwiz import encoding
from ffmwiz import reverse_pipeline

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

SECONDS = 2.0
CUE = (0.5, 1.5, "CUE")


def _run(args, timeout=300):
    return subprocess.run([str(part) for part in args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace", timeout=timeout)


class RichSource(NoLeakedArtifacts, unittest.TestCase):
    """One Matroska source carrying every stream type this path can lose.

    Video, TWO audio tracks with distinct languages and titles, a subtitle
    marked default+forced, and a font attachment.
    """

    @classmethod
    def setUpClass(cls):
        if not (FFMPEG and FFPROBE):
            raise unittest.SkipTest("ffmpeg/ffprobe not on PATH")
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_topology_"))
        srt = cls._root / "cue.srt"
        srt.write_text(
            f"1\n{FFmWiz.srt_timestamp(CUE[0])} --> {FFmWiz.srt_timestamp(CUE[1])}\n"
            f"{CUE[2]}\n\n", encoding="utf-8", newline="\n")
        cls.font = cls._root / "embedded.ttf"
        cls.font.write_bytes(b"\x00\x01\x00\x00" + bytes(range(256)) * 2)
        cls.source = cls._root / "rich.mkv"
        result = _run([
            FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=red:s=160x120:r=30:d={SECONDS}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={SECONDS}",
            "-f", "lavfi", "-i", f"sine=frequency=880:duration={SECONDS}",
            "-i", srt,
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-c:s", "srt",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:0", "title=English",
            "-metadata:s:a:1", "language=jpn", "-metadata:s:a:1", "title=Japanese",
            "-metadata:s:s:0", "language=spa", "-metadata:s:s:0", "title=Spanish",
            "-disposition:s:0", "default+forced",
            "-attach", cls.font, "-metadata:s:t:0",
            "mimetype=application/x-truetype-font", cls.source])
        if result.returncode != 0:
            raise unittest.SkipTest(f"could not build the source: {result.stderr[-400:]}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="topology_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._notes = []
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = self._notes.append
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", self._real_note))

    # ---- probing --------------------------------------------------------
    def _probe(self, path, *args):
        return json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                *args, path]).stdout or "{}")

    def _topology(self, path):
        return [s["codec_type"] for s in
                self._probe(path, "-show_streams").get("streams", [])]

    def _described(self, path):
        """(kind, language, title, default, forced) per stream, in order."""
        described = []
        for stream in self._probe(path, "-show_streams").get("streams", []):
            tags = {k.lower(): v for k, v in (stream.get("tags") or {}).items()}
            disposition = stream.get("disposition") or {}
            described.append((stream["codec_type"],
                              tags.get("language", ""), tags.get("title", ""),
                              int(disposition.get("default") or 0),
                              int(disposition.get("forced") or 0)))
        return described

    def _attachment_hash(self, path):
        extracted = self._tmp / "dumped.ttf"
        if extracted.exists():
            extracted.unlink()
        _run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
              "-dump_attachment:t:0", extracted, "-i", path, "-f", "null", "-"])
        if not extracted.exists():
            return None
        digest = hashlib.sha256(extracted.read_bytes()).hexdigest()
        extracted.unlink()
        return digest

    # ---- driving the public executor -------------------------------------
    def _answers(self, out, **extra):
        info = self._probe(self.source, "-show_format", "-show_streams")
        streams = info["streams"]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.source,
            "probe": info, "format": info["format"], "streams": streams,
            "output_location": out, "output_ext": "mkv",
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [s for s in streams if s["codec_type"] == "subtitle"],
            "attachment_streams": [s for s in streams if s["codec_type"] == "attachment"],
            "data_streams": [],
            "audio_tracks": [0, 1], "subtitle_tracks": [0],
            "keep_source_subtitles": True, "keep_embedded_attachments": True,
            "color_range_choice": "tv", "video_encoder": "libx264", "crf": 28,
            "preset": "ultrafast", "audio_codec": "aac",
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True,
        })
        answers.update(extra)
        # Open the lease HERE, on the outer dict, before anything takes a
        # `dict(answers)` copy. A copy made first gets its own container and
        # the scratch directories it leases are never released -- the trap this
        # repository has hit more than once.
        FFmWiz.artifact_lease(answers)
        return answers

    def _execute(self, label, **extra):
        """Through the PUBLIC dispatcher, not the segmented executor directly."""
        out = self._tmp / label
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        answers = self._answers(out, **extra)
        answers["output_path"] = out / f"{label}.mkv"
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            answers["cmd"] = [str(part) for part in
                              FFmWiz.build_ffmpeg_command(dict(answers))]
            code, _elapsed = encoding.execute_encode_plan(
                answers, answers["cmd"], total_duration=SECONDS, label=label)
        self.assertEqual(0, code, noise.getvalue()[-1500:])
        return Path(answers["output_path"]), answers

    # ---- D03 -------------------------------------------------------------
    def test_the_fixture_really_carries_everything(self):
        # Guard the guard: a source missing a stream type would let every
        # assertion below pass without proving anything.
        self.assertEqual(["video", "audio", "audio", "subtitle", "attachment"],
                         self._topology(self.source))

    def test_a_video_only_reverse_keeps_every_stream(self):
        output, _answers = self._execute("videoonly")
        self.assertEqual(self._topology(self.source), self._topology(output))

    def test_it_keeps_languages_titles_and_dispositions(self):
        output, _answers = self._execute("described")
        self.assertEqual(self._described(self.source), self._described(output))

    def test_the_default_and_forced_subtitle_survives_the_concat(self):
        # Named separately because the concat demuxer drops exactly this and
        # the topology assertion above would still pass without it.
        output, _answers = self._execute("dispositions")
        subtitle = [row for row in self._described(output) if row[0] == "subtitle"]
        self.assertEqual([("subtitle", "spa", "Spanish", 1, 1)], subtitle)

    def test_the_cues_still_land_on_the_reversed_picture(self):
        # Content, not just presence. Reversing a 2 s timeline mirrors a
        # 0.5-1.5 cue onto 0.5-1.5, so the check that matters is that the cue
        # is readable and inside the picture at all.
        output, _answers = self._execute("cues")
        cues = read_cues(FFMPEG, FFPROBE, output)
        self.assertEqual(1, len(cues), cues)
        start, end, body = cues[0]
        self.assertEqual(CUE[2], body)
        self.assertGreaterEqual(start, -0.05)
        self.assertLessEqual(end, SECONDS + 0.05)

    def test_a_synchronised_reverse_keeps_them_too(self):
        # The control: when the audio DOES follow the reverse the executor
        # takes the other branch, which always used `-map 0`. It must not
        # regress while the two-input branch is being repaired.
        #
        # The attachment is deliberately dropped from THIS case only. A
        # synchronised reverse of a source with an attachment AND a subtitle
        # fails outright today, for an unrelated reason in `wizard_build.py`:
        # the attachment map is emitted before the filter-complex audio output,
        # so Matroska refuses a packet stream that follows an attachment --
        # `Error submitting a packet to the muxer: Invalid argument`, -22.
        # Isolated to attachment + subtitle + filter-supplied audio; track
        # count is irrelevant. Including it here would tie this regression to
        # that repair instead of testing the branch it is about.
        expected = [kind for kind in self._topology(self.source)
                    if kind != "attachment"]
        output, _answers = self._execute(
            "synchronised", audio_speed_from_video=True, reverse_audio=True,
            keep_embedded_attachments=False)
        # Counts, not order. This branch puts the retimed subtitle at index 1
        # -- ['video', 'subtitle', 'audio', 'audio'] against the source's
        # ['video', 'audio', 'audio', 'subtitle'] -- because its stream order
        # comes from the segment builder, not from the mux policy repaired
        # here. The branch this defect is about DOES preserve exact order, and
        # `test_a_video_only_reverse_keeps_every_stream` asserts that.
        self.assertEqual(sorted(expected), sorted(self._topology(output)),
                         "the synchronised branch lost a stream")

    # ---- D04 -------------------------------------------------------------
    def test_the_attachment_arrives_byte_for_byte(self):
        # Stream copy is the contract for an attachment, so compare payloads
        # rather than trusting the stream count.
        output, _answers = self._execute("attachment")
        self.assertEqual(hashlib.sha256(self.font.read_bytes()).hexdigest(),
                         self._attachment_hash(output))

    def test_a_container_that_cannot_hold_it_says_so(self):
        maps, _dispositions, warnings = FFmWiz.reverse_mux_stream_policy(
            self._answers(self._tmp, output_ext="mp4"))
        self.assertNotIn("0:t?", maps)
        self.assertTrue(warnings, "an attachment was dropped with no warning")
        self.assertIn("attachment", " ".join(warnings).lower())

    def test_matroska_claims_the_attachment(self):
        maps, _dispositions, warnings = FFmWiz.reverse_mux_stream_policy(
            self._answers(self._tmp, output_ext="mkv"))
        self.assertIn("0:t?", maps)
        self.assertEqual([], warnings)

    def test_a_source_with_no_attachment_warns_about_nothing(self):
        answers = self._answers(self._tmp, output_ext="mp4")
        answers["attachment_streams"] = []
        _maps, _dispositions, warnings = FFmWiz.reverse_mux_stream_policy(answers)
        self.assertEqual([], warnings)

    def test_data_streams_are_claimed(self):
        maps, _dispositions, _warnings = FFmWiz.reverse_mux_stream_policy(
            self._answers(self._tmp))
        self.assertIn("0:d?", maps)

    # ---- the exported plan must state the same policy --------------------
    def test_the_exported_plan_uses_the_same_maps(self):
        answers = self._answers(self._tmp, separator_points=[1.0])
        answers["output_path"] = self._tmp / "planned.mkv"
        workspace = self._tmp / "plan_workspace"
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            stages = reverse_pipeline.bounded_reverse_plan(answers, workspace)
        muxes = [cmd for label, cmd in stages
                 if label.startswith("Concatenating reversed")]
        self.assertEqual(1, len(muxes), [label for label, _cmd in stages])
        expected_maps, expected_dispositions, _warnings = \
            FFmWiz.reverse_mux_stream_policy(answers)
        for token in expected_maps + expected_dispositions:
            self.assertIn(token, muxes[0],
                          f"the exported mux is missing {token!r}")


if __name__ == "__main__":
    unittest.main()
