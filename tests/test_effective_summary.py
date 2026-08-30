"""Regression: the settings summary must describe the COMMAND (F07).

`effective_settings` recorded what a job really resolved to, but almost nothing
read it back -- four writers against three readers. A join whose inputs are not
copy-compatible records `video_codec=libx265` and `audio_codec=aac` in the
effective map while leaving the requested `copy` in `answers`, because the join
builder never mutates the requested keys the way the single-input builder does.

Measured on two joined inputs of different resolutions, both requested as
`copy`:

    command                  -c:v libx265   -c:a aac
    summary before           video codec: libx265
                             audio codec: copy                  <- wrong
                             estimated output size: N/A (video stream copy...)
                             colour-range policy: stream copy (preserved...)
                             encoder / pixel format / path: absent
    summary after            audio codec: aac, encoder libx265, and the size
                             line stops blaming a stream copy that is not
                             happening

The repair is at the resolver rather than at each print site:
`resolve_video_encoder()` reads the resolved codec, so the encoder, profile,
pixel-format, filter-path, colour-range and size-estimate consumers all agree
without each having to remember.
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

import FFmWiz
from ffmwiz import wizard_composite, wizard_quick, wizard_raw

from artifact_guard import NoLeakedArtifacts
from command_gen_base import CommandGenBase

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TheResolverReadsTheResolvedCodec(unittest.TestCase):
    """No ffmpeg needed: the resolver is the single point every consumer uses."""

    def test_a_recorded_substitution_wins_over_the_request(self):
        answers = {"video_codec": "copy"}
        FFmWiz.effective_settings(answers)["video_codec"] = "libx265"
        self.assertEqual("libx265", FFmWiz.resolve_video_encoder(answers)[0])

    def test_the_request_still_applies_before_anything_is_resolved(self):
        self.assertEqual("copy", FFmWiz.resolve_video_encoder({"video_codec": "copy"})[0])
        self.assertEqual("libx265", FFmWiz.resolve_video_encoder({"video_codec": "H265"})[0])

    def test_resolving_twice_is_stable(self):
        # The builder records the ENCODER name under the codec key, so a second
        # resolution must not try to alias it again into something else.
        answers = {"video_codec": "copy"}
        FFmWiz.effective_settings(answers)["video_codec"] = "libx265"
        first = FFmWiz.resolve_video_encoder(answers)[0]
        self.assertEqual(first, FFmWiz.resolve_video_encoder(answers)[0])

    def test_a_copy_that_was_never_substituted_stays_copy(self):
        answers = {"video_codec": "copy"}
        FFmWiz.effective_settings(answers)["audio_codec"] = "aac"
        self.assertEqual("copy", FFmWiz.resolve_video_encoder(answers)[0])


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")
class TheSummaryAgreesWithTheCommand(NoLeakedArtifacts, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_effsum_"))
        cls.inputs = []
        # Different resolutions: not copy-compatible, so the join must
        # re-encode however the user asked for copy.
        for name, size, colour in (("a", "320x240", "red"), ("b", "640x480", "blue")):
            path = cls._tmp / f"{name}.mkv"
            subprocess.run(
                [FFMPEG, "-v", "error", "-y",
                 "-f", "lavfi", "-i", f"color=c={colour}:size={size}:rate=30:duration=2",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-shortest", str(path)],
                check=True, capture_output=True, timeout=300)
            cls.inputs.append(path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _item(self, path):
        info = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120).stdout)
        return {"path": path, "probe": info, "format": info["format"],
                "streams": info["streams"],
                "video_streams": [s for s in info["streams"] if s["codec_type"] == "video"],
                "audio_streams": [s for s in info["streams"] if s["codec_type"] == "audio"],
                "data_streams": [], "duration": 2.0}

    def _join_summary(self):
        out = self._tmp / "out"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        items = [self._item(path) for path in self.inputs]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
            "input_path": self.inputs[0],
            "probe": items[0]["probe"], "format": items[0]["format"],
            "output_location": out,
            "video_streams": items[0]["video_streams"],
            "audio_streams": items[0]["audio_streams"],
            "subtitle_streams": [], "output_ext": "mkv",
            "video_codec": "copy", "audio_codec": "copy", "audio_tracks": [0],
            "resolution": "n", "color_range_choice": "tv",
            "join_input_items": items[1:],
        })
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            cmd = [str(part) for part in FFmWiz.build_join_encode_command(
                answers, items, out / "joined.mkv")]
        answers["cmd"] = cmd
        answers["output_path"] = out / "joined.mkv"
        printed = StringIO()
        with redirect_stdout(printed), redirect_stderr(printed):
            FFmWiz.print_summary(answers, cmd)
        return answers, cmd, _plain(printed.getvalue())

    def _line(self, summary, label):
        for line in summary.splitlines():
            stripped = line.strip()
            if stripped.startswith(label + ":"):
                return stripped.split(":", 1)[1].strip()
        return None

    def test_the_join_really_is_forced_to_re_encode(self):
        # Guard the guard: if the inputs became copy-compatible this fixture
        # would stop exercising the defect and everything below would pass
        # for the wrong reason.
        _answers, cmd, _summary = self._join_summary()
        self.assertEqual("libx265", cmd[cmd.index("-c:v") + 1])
        self.assertEqual("aac", cmd[cmd.index("-c:a") + 1])

    def test_the_audio_codec_line_matches_the_command(self):
        _answers, cmd, summary = self._join_summary()
        self.assertEqual(cmd[cmd.index("-c:a") + 1], self._line(summary, "audio codec"))

    def test_the_video_codec_line_matches_the_command(self):
        _answers, cmd, summary = self._join_summary()
        self.assertEqual(cmd[cmd.index("-c:v") + 1], self._line(summary, "video codec"))

    def test_the_encoder_and_path_lines_appear_at_all(self):
        # They are printed only when the resolver says this is not a copy;
        # reading the requested value hid the whole block.
        _answers, _cmd, summary = self._join_summary()
        self.assertEqual("libx265", self._line(summary, "encoder"))
        self.assertIsNotNone(self._line(summary, "pixel format"))
        self.assertIsNotNone(self._line(summary, "path"))

    def test_the_size_estimate_does_not_blame_a_stream_copy(self):
        _answers, _cmd, summary = self._join_summary()
        estimate = self._line(summary, "estimated output size") or ""
        self.assertNotIn("stream copy", estimate)

    def test_the_colour_range_policy_does_not_claim_a_stream_copy(self):
        _answers, _cmd, summary = self._join_summary()
        policy = self._line(summary, "color-range policy") or ""
        self.assertNotIn("stream copy", policy)


class TheFiveNewFeaturesShowInTheSummary(CommandGenBase):
    """Picture filters, quick output, compositing, volume and raw ffmpeg
    options each change the job as much as anything already in the summary,
    and none of them showed up there. `print_summary` now guards one row per
    feature on the ANSWER key, never on the describer's own text -- every
    describe_* returns the string "none" for a job that never touched it, and
    "none" is still a non-empty, truthy string.
    """

    def _summary_for(self, tmp, **features):
        # The command is built BEFORE the features are added: `quick_output`
        # reroutes build_ffmpeg_command to an entirely different job shape
        # (build_quick_output_stages), and none of these five rows read `cmd`
        # at all, so a plain encode command is all print_summary needs here.
        answers = self.base_answers(tmp)
        cmd = self.command_for(answers)
        answers.update(features)
        buf = StringIO()
        with redirect_stdout(buf):
            FFmWiz.print_summary(answers, cmd)
        return answers, _plain(buf.getvalue())

    def test_every_new_feature_appears_when_asked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, summary = self._summary_for(
                tmp,
                rotate_choice="90cw",
                quick_output="boomerang",
                composite_mode="overlay",
                audio_volume=2.0,
                raw_ffmpeg_args=["-tune", "film"],
            )
        checks = (
            ("picture filters", FFmWiz.describe_look(answers)),
            ("quick output", wizard_quick.describe_quick(answers)),
            ("composite", wizard_composite.describe_composite(answers)),
            ("volume", wizard_raw.describe_raw({"audio_volume": answers["audio_volume"]})),
            ("raw options", wizard_raw.describe_raw({"raw_ffmpeg_args": answers["raw_ffmpeg_args"]})),
        )
        for label, expected in checks:
            with self.subTest(label=label):
                self.assertNotEqual("none", expected)
                self.assertIn(label, summary)
                self.assertIn(expected, summary)

    def test_a_plain_encode_shows_none_of_them(self):
        # The trap this plan warned about: every describe_* returns the
        # string "none" for an empty job, and "none" is still truthy. A guard
        # on the describer's OUTPUT would print all five rows right here.
        with tempfile.TemporaryDirectory() as tmp:
            _answers, summary = self._summary_for(tmp)
        for label in ("picture filters", "quick output", "composite", "volume", "raw options"):
            with self.subTest(label=label):
                self.assertNotIn(label, summary)

    def test_the_raw_options_row_shows_the_actual_options(self):
        raw_args = ["-metadata", "title=my film", "-tune", "film"]
        with tempfile.TemporaryDirectory() as tmp:
            _answers, summary = self._summary_for(tmp, raw_ffmpeg_args=raw_args)
        self.assertIn("raw options", summary)
        self.assertIn(" ".join(raw_args), summary)

    def test_a_volume_of_one_is_not_shown(self):
        # 1.0 is reachable, not just "unset": parse_volume accepts an
        # explicit 1.0/100%, and step_audio_volume stores whatever it parsed.
        # Showing "no change" back to the user would be noise.
        with tempfile.TemporaryDirectory() as tmp:
            _answers, summary = self._summary_for(tmp, audio_volume=1.0)
        self.assertNotIn("volume", summary)


if __name__ == "__main__":
    unittest.main()
