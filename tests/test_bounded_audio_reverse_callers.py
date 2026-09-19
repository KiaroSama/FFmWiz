"""Everything that reuses the bounded reverse instead of writing its own.

Two standalone modes reach it through their real dispatchers, and the join
carries every part of a multi-input job through it. The plan is covered by
`test_bounded_audio_reverse` and the executor by
`test_bounded_audio_reverse_run`; what is checked here is that these callers
route through that one plan rather than re-implementing it and drifting.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k bounded_audio_reverse_callers
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
from ffmwiz import modes_transform


from audio_reverse_fixtures import FFMPEG, FFPROBE, RATE, TONES, requires_ffmpeg, run_tool


@requires_ffmpeg
class ThePublicDispatchersUseIt(NoLeakedArtifacts, unittest.TestCase):
    """The two standalone modes, driven through their real dispatchers."""

    def setUp(self):
        super().setUp()
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_arevmode_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.source = self._tmp / "tones.flac"
        inputs, labels = [], []
        for index, freq in enumerate(TONES):
            inputs += ["-f", "lavfi", "-i",
                       f"sine=frequency={freq}:duration=1:sample_rate={RATE}"]
            labels.append(f"[{index}:a]")
        result = run_tool([FFMPEG, "-hide_banner", "-v", "error", "-y", *inputs,
                       "-filter_complex",
                       "".join(labels) + f"concat=n={len(TONES)}:v=0:a=1[a]",
                       "-map", "[a]", "-ac", "1", "-c:a", "flac", str(self.source)])
        if result.returncode != 0:
            self.skipTest("could not build the fixture")

    def _drive(self, dispatcher, steps):
        """Run the real mode impl with only the interactive steps stubbed."""
        calls = {"bounded": 0}
        real = modes_transform.run_bounded_audio_reverse

        def spy(*args, **kwargs):
            calls["bounded"] += 1
            return real(*args, **kwargs)

        with contextlib.ExitStack() as stack:
            for target, replacement in steps:
                stack.enter_context(mock.patch.object(
                    modes_transform, target, replacement))
            stack.enter_context(mock.patch.object(
                modes_transform, "run_bounded_audio_reverse", spy))
            stack.enter_context(mock.patch.object(
                modes_transform.wizard, "step_input_path",
                lambda a: None))
            stack.enter_context(mock.patch.object(
                modes_transform.wizard, "step_output_location",
                lambda a: None))
            stack.enter_context(mock.patch.object(
                FFmWiz.appio, "ask_yes_no", return_value=True))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            result = dispatcher({
                "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
                "input_path": self.source,
                "output_location": self._tmp,
                "audio_tool_output_ext": "flac",
            })
        return result, calls

    def test_the_standalone_speed_reverse_mode_routes_through_the_bounded_plan(self):
        probe = FFmWiz.ffprobe_full_json(FFPROBE, self.source)

        def track(answers):
            answers.update({
                "probe": probe, "format": probe.get("format", {}),
                "video_streams": [], "subtitle_streams": [],
                "audio_streams": [s for s in probe.get("streams", [])
                                  if s.get("codec_type") == "audio"],
                "audio_index": 0,
            })

        def options(answers):
            answers.update({"reverse_audio": True, "speed_factor": 1.0,
                            "_speed_reverse_noop": False})

        result, calls = self._drive(
            FFmWiz.run_audio_speed_reverse_mode,
            [("step_audio_track_for_tool", track),
             ("step_audio_speed_reverse_options", options)])
        self.assertIsNotNone(result)
        self.assertEqual(0, result[0])
        self.assertEqual(1, calls["bounded"],
                         "the mode must reach the bounded executor exactly once")

    def test_the_standalone_transform_mode_routes_through_the_bounded_plan(self):
        probe = FFmWiz.ffprobe_full_json(FFPROBE, self.source)

        def track(answers):
            answers.update({
                "probe": probe, "format": probe.get("format", {}),
                "video_streams": [], "subtitle_streams": [],
                "audio_streams": [s for s in probe.get("streams", [])
                                  if s.get("codec_type") == "audio"],
                "audio_index": 0,
            })

        def editor(answers):
            answers.update({"reverse_audio": True, "audio_speed_enabled": True,
                            "audio_speed_factor": 1.0,
                            "audio_cut_keep_ranges": [(0.0, 2.0)],
                            "_audio_transform_noop": False})

        result, calls = self._drive(
            FFmWiz.run_audio_transform_mode,
            [("step_audio_track_for_tool", track),
             ("step_audio_transform_editor", editor)])
        self.assertIsNotNone(result)
        self.assertEqual(0, result[0])
        self.assertEqual(1, calls["bounded"])


class TheJoinCarriesEveryPart(unittest.TestCase):
    """The concat DEMUXER silently drops FLAC parts; the filter does not.

    Every FLAC file carries its own STREAMINFO, and the demuxer applies the
    FIRST part's header to all of them. Parts recorded with a different
    blocksize then fail to decode --

        [flac @ ...] blocksize 1024 > 272
        [flac @ ...] decode_frame() failed

    -- and are dropped WITH EXIT CODE 0, under a container header still
    claiming the full length. Measured on ffmpeg 9.0.1, eight half-second parts
    holding 4.0000 s between them:

        -c copy (any flag combination)         0.4938s
        per-part `duration` in the list        0.4938s
        re-encode THROUGH the demuxer          0.4938s
        concat FILTER, one input per part      4.0000s

    0.4938 s at 44100 is 21776 samples, which is what CI reported for five
    commits while every stage guard called its own step healthy.

    This decodes the result instead of reading its duration, because the header
    is the thing that lied.
    """

    @staticmethod
    def _decoded_seconds(path, rate=RATE):
        run = subprocess.run(
            [FFMPEG, "-v", "error", "-i", str(path), "-map", "0:a:0",
             "-f", "s16le", "-ac", "1", "-ar", str(rate), "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
        return len(run.stdout) / 2 / float(rate)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_parts_with_different_blocksizes_all_survive_the_join(self):
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            parts = []
            # Different blocksizes on purpose: that is the divergence the
            # demuxer cannot represent, and a uniform set would prove nothing.
            for index, (freq, block) in enumerate(
                    ((440, 4096), (660, 1024), (880, 512), (1100, 256))):
                part = work / f"part{index}.mkv"
                run_tool([FFMPEG, "-v", "error", "-y", "-f", "lavfi",
                      "-i", f"sine=frequency={freq}:sample_rate={RATE}:duration=0.5",
                      "-c:a", "flac", "-blocksize", str(block), str(part)])
                parts.append(part)

            expected = sum(self._decoded_seconds(p) for p in parts)
            self.assertAlmostEqual(2.0, expected, delta=0.05,
                                   msg="the fixture parts are not half a second each")

            joined = work / "joined.mkv"
            cmd = FFmWiz.build_audio_concat_filter_command(FFMPEG, parts, joined)
            result = run_tool([str(part) for part in cmd])
            self.assertEqual(0, result.returncode, (result.stderr or "")[-400:])

            got = self._decoded_seconds(joined)
            self.assertAlmostEqual(
                expected, got, delta=0.05,
                msg=f"the join carried {got:.4f}s of the {expected:.4f}s it was "
                    "given; parts were dropped")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_the_builder_opens_each_part_as_its_own_input(self):
        # The shape is the fix: one `-i` per part. A single `-i` on a list file
        # is the demuxer, which is what dropped them.
        parts = [Path(f"p{i}.mkv") for i in range(4)]
        cmd = FFmWiz.build_audio_concat_filter_command("ffmpeg", parts, Path("out.mkv"))
        self.assertEqual(4, cmd.count("-i"), "each part needs its own input")
        self.assertIn("-filter_complex", cmd)
        self.assertNotIn("concat", [str(part) for part in cmd][:4],
                         "the demuxer must not be used for this")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_every_track_and_its_tags_are_carried(self):
        # A filter output has no source stream, so both of these are lost
        # unless the builder asks for them.
        parts = [Path(f"p{i}.mkv") for i in range(2)]
        cmd = [str(part) for part in
               FFmWiz.build_audio_concat_filter_command("ffmpeg", parts,
                                                        Path("out.mkv"), tracks=3)]
        graph = cmd[cmd.index("-filter_complex") + 1]
        for track in range(3):
            self.assertIn(f"[aout{track}]", graph, f"track {track} has no chain")
            self.assertIn(f"[0:a:{track}]", graph)
            self.assertIn(f"-map_metadata:s:a:{track}", cmd,
                          f"track {track} would lose its language and title")
