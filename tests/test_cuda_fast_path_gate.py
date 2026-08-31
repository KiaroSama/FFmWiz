"""The CUDA fast path must decline what its single filter cannot carry.

`build_cuda_video_filter` emits exactly one filter -- `scale_cuda`. So every
picture edit that is NOT a scale has to keep the job on the complex-graph path.
It did not: with `use_gpu` and an NVENC encoder, a rotate, a colour adjustment,
denoise/sharpen/blur and a fade all returned True from
`can_use_cuda_fast_path` and the command came out carrying
`scale_cuda=format=nv12:passthrough=0:reset_sar=1` and nothing else. The user's
edit was gone, with no warning and no error. A fade was worse than gone: the
matching `afade` was still emitted, so the sound faded over a picture that did
not.

An fps change is NOT in that set. It is applied as the `-r:v` output option,
which needs no filter and works on CUDA frames -- pinned below so the gate is
not widened onto it again.

The two adjacent gates disagreed, which is the sharpest way to see it:
`video_filters_required` said the picture chain was needed and
`can_use_cuda_fast_path` said the GPU shortcut was fine, for the same answers.

What this does NOT assert is `not video_filters_required(...)`. That covers crop
and resize too, and the CUDA path really does handle those (decoder crop plus
`scale_cuda`) -- gating on it would switch the GPU path off for the jobs it
exists to serve. The contract is narrower and stated as such: whatever the one
emitted filter cannot express must send the job elsewhere.
"""
import unittest

import FFmWiz
from ffmwiz.core.constants_look import BLUR_FILTERS, DENOISE_FILTERS, SHARPEN_FILTERS
from ffmwiz.support import L02


def _answers(**extra):
    base = {
        "use_gpu": True, "video_codec": "hevc_nvenc", "output_ext": "mp4",
        "video_streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                           "r_frame_rate": "24/1", "avg_frame_rate": "24/1"}],
        "audio_streams": [], "subtitle_streams": [],
        "color_range_choice": "tv", "input_path": "in.mkv",
    }
    base.update(extra)
    return base


class TheFastPathDeclinesWhatItCannotExpress(unittest.TestCase):
    def test_an_untouched_job_still_takes_the_gpu_path(self):
        # The control. If this ever fails the gate has been over-tightened and
        # the GPU shortcut is dead for the jobs it is meant to serve.
        self.assertTrue(L02.can_use_cuda_fast_path(_answers(), "hevc_nvenc"))

    def test_scaling_still_takes_the_gpu_path(self):
        # scale_cuda IS the filter it emits, so a resize must not be pushed off.
        self.assertTrue(
            L02.can_use_cuda_fast_path(_answers(resize_width=1280), "hevc_nvenc"),
            "resize is exactly what scale_cuda is for; the gate must not reject it")

    def test_each_unexpressable_edit_is_declined(self):
        cases = {
            "rotate": {"rotate_choice": "90cw"},
            "flip": {"flip_horizontal": True},
            "grayscale": {"adjust_grayscale": True},
            "denoise": {"denoise_level": next(iter(DENOISE_FILTERS))},
            "sharpen": {"sharpen_level": next(iter(SHARPEN_FILTERS))},
            "blur": {"blur_level": next(iter(BLUR_FILTERS))},
            "fade in": {"fade_in_seconds": 2.0},
            "fade out": {"fade_out_seconds": 2.0},
        }
        for label, extra in cases.items():
            with self.subTest(edit=label):
                answers = _answers(**extra)
                self.assertFalse(
                    L02.can_use_cuda_fast_path(answers, "hevc_nvenc"),
                    f"{label} cannot be expressed by scale_cuda, so the fast path "
                    "must decline it -- taking it drops the edit silently")

    def test_the_two_adjacent_gates_never_disagree(self):
        # Whenever the picture chain is required for something other than a
        # scale, the shortcut that would skip that chain must be off. Stated as
        # a relationship so a future edit added to one gate is caught by the other.
        for label, extra in (("rotate", {"rotate_choice": "180"}),
                             ("colour", {"adjust_grayscale": True}),
                             ("fade", {"fade_out_seconds": 1.5})):
            with self.subTest(edit=label):
                answers = _answers(**extra)
                self.assertTrue(FFmWiz.video_filters_required(answers))
                self.assertFalse(L02.can_use_cuda_fast_path(answers, "hevc_nvenc"))


    def test_a_frame_rate_still_takes_the_gpu_path(self):
        # The gate was briefly widened onto `fps` on the reading that the fast
        # path dropped it. It does not: the rate is set with `-r:v`, an output
        # option that needs no filter. Rejecting it here would have sent every
        # GPU job that picks a frame rate down the slow path for nothing.
        self.assertTrue(
            L02.can_use_cuda_fast_path(_answers(fps=24), "hevc_nvenc"),
            "a frame rate is expressed with -r:v, not a filter")


if __name__ == "__main__":
    unittest.main()
