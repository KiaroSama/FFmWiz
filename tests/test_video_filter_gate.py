"""`video_filters_required` must cover everything `build_cpu_video_filter` emits.

The gate decides whether a `-c:v copy` answer has to be overridden. It used to
ask only about crop, fps, resize, cuts, split points and speed, while the chain
behind it also emits the orientation (rotate/flip), the look (colour, denoise,
sharpen/blur) and the fades. A gate narrower than its own body drops the
request silently: the command came out `-c:v copy` with no `-vf` at all, so a
rotation the user asked for simply never happened. Same defect the audio side
had before `audio_transform_enabled` was widened.

The look cases are generated from the real filter tables rather than listed, so
a new level or a new rotation is covered the moment it is added.
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import FFmWiz


def _look_answer_cases() -> list[tuple[str, dict]]:
    """One answers-fragment per thing the orientation/look/fade builders emit."""
    cases: list[tuple[str, dict]] = []
    for name in FFmWiz.ROTATE_FILTERS:
        cases.append((f"rotate_choice={name}", {"rotate_choice": name}))
    cases.append(("flip_horizontal", {"flip_horizontal": True}))
    cases.append(("flip_vertical", {"flip_vertical": True}))
    cases.append(("adjust_grayscale", {"adjust_grayscale": True}))
    for key, table in (("denoise_level", FFmWiz.DENOISE_FILTERS),
                       ("sharpen_level", FFmWiz.SHARPEN_FILTERS),
                       ("blur_level", FFmWiz.BLUR_FILTERS)):
        for level in table:
            cases.append((f"{key}={level}", {key: level}))
    for key, (low, high, neutral) in FFmWiz.ADJUST_RANGES.items():
        # A value that is genuinely not the neutral one, inside the valid range.
        value = (neutral + high) / 2.0
        if value == neutral:
            value = (neutral + low) / 2.0
        cases.append((f"{key}={value:g}", {key: value}))
    cases.append(("fade_in_seconds", {"fade_in_seconds": 1.0}))
    cases.append(("fade_out_seconds", {"fade_out_seconds": 1.0}))
    return cases


class TheGateCoversEveryFilterTheChainEmits(unittest.TestCase):

    def _answers(self, **extra):
        answers = {
            "video_streams": [{"codec_type": "video", "codec_name": "h264",
                               "width": 640, "height": 480,
                               "avg_frame_rate": "30/1", "pix_fmt": "yuv420p",
                               "color_range": "tv"}],
            "audio_streams": [], "subtitle_streams": [],
            "attachment_streams": [], "data_streams": [],
            "format": {"duration": "10.0"},
            "video_codec": "copy", "audio_codec": "copy",
            "audio_tracks": [], "subtitle_tracks": [], "use_gpu": False,
            "output_ext": "mkv",
        }
        answers.update(extra)
        return answers

    def test_every_look_and_fade_answer_opens_the_gate(self):
        for label, extra in _look_answer_cases():
            with self.subTest(answer=label):
                self.assertTrue(
                    FFmWiz.video_filters_required(self._answers(**extra)),
                    f"{label} reaches the filter chain but not the gate",
                )

    def test_an_untouched_job_still_needs_no_filters(self):
        # The gate must not open on its own: a plain remux still stream-copies.
        self.assertFalse(FFmWiz.video_filters_required(self._answers()))

    def test_a_neutral_adjustment_does_not_open_the_gate(self):
        for key, (_low, _high, neutral) in FFmWiz.ADJUST_RANGES.items():
            with self.subTest(answer=key):
                self.assertFalse(
                    FFmWiz.video_filters_required(self._answers(**{key: neutral})),
                    f"{key} at its neutral value emits nothing, so it must not "
                    "cost the user a stream copy",
                )

    def test_a_copy_answer_still_carries_the_filter_into_the_command(self):
        # The symptom, not the gate: `-c:v copy` plus a look answer used to
        # produce a command with no filter argument at all.
        with tempfile.TemporaryDirectory() as out:
            for label, extra in _look_answer_cases():
                with self.subTest(answer=label):
                    answers = self._answers(
                        ffmpeg="ffmpeg", ffprobe="ffprobe",
                        input_path=Path("in.mkv"), output_location=Path(out),
                        **extra)
                    expected = (FFmWiz.build_orientation_filters(answers)
                                + FFmWiz.build_look_filters(answers)
                                + FFmWiz.build_fade_filters(answers, 10.0))
                    self.assertTrue(expected, f"{label} reached no builder")
                    with contextlib.redirect_stdout(io.StringIO()):
                        cmd = [str(part) for part in
                               FFmWiz.build_ffmpeg_command(answers)]
                    self.assertNotIn("copy", cmd[cmd.index("-c:v") + 1:cmd.index("-c:v") + 2]
                                     if "-c:v" in cmd else [],
                                     f"{label} left the video on stream copy")
                    joined = " ".join(cmd)
                    for part in expected:
                        self.assertIn(part, joined,
                                      f"{label} was dropped from the command")


if __name__ == "__main__":
    unittest.main()
