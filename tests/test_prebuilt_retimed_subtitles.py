"""Regression: a staged plan can hand the builder its retimed subtitles.

`build_ffmpeg_command()` obtained retimed subtitle tracks by EXTRACTING them
from `answers["input_path"]`. That is right while the input is the user's file
and wrong the moment a staged plan DESCRIBES a command whose input has not been
written yet: extraction from `joined_forward.mkv`, or from `reversed_whole.mkv`
for the split stage, found nothing, so the exported plan emitted `-sn` where the
automatic run mapped the retimed track. Measured, planned against executed
reverse segment:

    only in PLAN : ['-sn']
    only in RUN  : ['-c:s', '-disposition:s:0', '-metadata:s:s',
                    '1:s:0', 'copy', 'retimed00.srt']

The planner CAN build those tracks -- the source files exist at plan time -- so
the builder now accepts them through `answers["_prebuilt_retimed_subtitles"]`
and only extracts when nothing was supplied. Same list of dicts, so nothing
downstream changes.

The fixture reproduces the shape of the failure rather than describing it: the
input is a subtitle-free intermediate, exactly like the one a staged plan names,
while `subtitle_streams` still describes the source the plan started from.
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
class APlannerCanSupplyTheRetimedTracks(NoLeakedArtifacts, unittest.TestCase):
    def setUp(self):
        super().setUp()
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_prebuiltsubs_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        subs = self._tmp / "source.srt"
        subs.write_text("1\n00:00:00,500 --> 00:00:01,500\nFIRST\n\n"
                        "2\n00:00:02,500 --> 00:00:03,500\nSECOND\n\n",
                        encoding="utf-8")
        self.subtitled = self._tmp / "subtitled.mkv"
        result = _run([FFMPEG, "-hide_banner", "-v", "error", "-y",
                       "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=4",
                       "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                       "-i", str(subs),
                       "-map", "0:v", "-map", "1:a", "-map", "2:s",
                       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                       # No -shortest: with a mapped subtitle input FFmpeg 8.1.1 never
                       # terminates (measured: killed at 25 s, 0.2 s of CPU).
                       # Every input already states its own duration.
                       "-c:a", "aac", "-c:s", "srt", str(self.subtitled)])
        if result.returncode != 0:
            self.skipTest("could not build the fixture: " + (result.stderr or "")[-300:])
        # The intermediate a staged plan would NAME: same picture and sound, no
        # subtitle stream, because the stage that writes it has not run.
        self.intermediate = self._tmp / "joined_forward.mkv"
        result = _run([FFMPEG, "-hide_banner", "-v", "error", "-y",
                       "-i", str(self.subtitled), "-map", "0:v", "-map", "0:a",
                       "-c", "copy", str(self.intermediate)])
        self.assertEqual(0, result.returncode, result.stderr)
        self.source_probe = FFmWiz.ffprobe_full_json(FFPROBE, self.subtitled)

    def _answers(self, **extra):
        streams = self.source_probe.get("streams", [])
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
            # The stage's own input: no subtitle track to extract from.
            "input_path": self.intermediate,
            "probe": self.source_probe, "format": self.source_probe.get("format", {}),
            "video_streams": [s for s in streams if s.get("codec_type") == "video"],
            "audio_streams": [s for s in streams if s.get("codec_type") == "audio"],
            "subtitle_streams": [s for s in streams if s.get("codec_type") == "subtitle"],
            "data_streams": [], "attachment_streams": [],
            "audio_tracks": [0], "subtitle_tracks": [0],
            "keep_source_subtitles": True,
            "video_codec": "H264", "audio_codec": "aac", "use_gpu": False,
            "output_location": self._tmp, "output_ext": "mkv",
            "color_range_choice": "tv", "resolution": "n",
            # A speed change is what forces the retiming in the first place.
            "video_speed_enabled": True, "video_speed_factor": 2.0,
            "audio_speed_from_video": True,
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

    def _retimed_track(self):
        """What a planner builds from the SOURCE, which it really does have."""
        track = self._tmp / "retimed00.srt"
        track.write_text("1\n00:00:01,250 --> 00:00:01,750\nSECOND\n\n"
                         "2\n00:00:00,250 --> 00:00:00,750\nFIRST\n\n",
                         encoding="utf-8")
        return [{"path": track, "source_index": 0, "language": "eng",
                 "title": "", "default": True, "forced": False}]

    def test_without_the_hook_the_stage_cannot_find_a_subtitle_to_retime(self):
        # The reproduction: extraction from an input that has no subtitle track.
        answers = self._answers()
        cmd = self._build(answers)
        self.assertIn("-sn", cmd,
                      "extraction from the intermediate must really come up empty")

    def test_a_supplied_track_is_added_as_an_input_and_reaches_the_output(self):
        supplied = self._retimed_track()
        answers = self._answers(_prebuilt_retimed_subtitles=supplied)
        cmd = self._build(answers)
        self.assertNotIn("-sn", cmd)
        self.assertIn(str(supplied[0]["path"]), cmd,
                      "the supplied track must be an input of the command")
        result = _run(cmd)
        self.assertEqual(0, result.returncode, (result.stderr or "")[-800:])
        probe = FFmWiz.ffprobe_full_json(FFPROBE, Path(answers["output_path"]))
        kinds = [s.get("codec_type") for s in probe.get("streams", [])]
        self.assertIn("subtitle", kinds,
                      "the supplied retimed track must reach the produced file")

    def test_an_empty_supplied_list_is_honoured_rather_than_ignored(self):
        # A planner that knows there is nothing to retime says so with [], and
        # that must not fall back to extracting.
        answers = self._answers(_prebuilt_retimed_subtitles=[])
        cmd = self._build(answers)
        self.assertIn("-sn", cmd)

    def test_extraction_still_happens_when_nothing_was_supplied(self):
        # The ordinary interactive path, where input_path IS the user's file.
        answers = self._answers(input_path=self.subtitled)
        cmd = self._build(answers)
        self.assertNotIn("-sn", cmd,
                         "a real subtitled input must still be retimed by extraction")
        result = _run(cmd)
        self.assertEqual(0, result.returncode, (result.stderr or "")[-800:])
        probe = FFmWiz.ffprobe_full_json(FFPROBE, Path(answers["output_path"]))
        self.assertIn("subtitle",
                      [s.get("codec_type") for s in probe.get("streams", [])])


if __name__ == "__main__":
    unittest.main()
