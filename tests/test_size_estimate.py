"""Regression: the encode size estimate read the wrong speed key.

`estimated_encode_duration_seconds` scales the source duration by the chosen
speed so `print_encode_size_estimate` can quote an output size. It read only
`speed_factor` -- the STANDALONE Video Speed tool's key. The encode wizard's
speed step writes `video_speed_factor` (`wizard_flow_b`), so every encode job
was estimated at its full source length:

    video_speed_factor 2.0, 600 s source -> 600.0   (the output runs 300 s)
    video_speed_factor 0.5, 600 s source -> 600.0   (the output runs 1200 s)

The audio-bitrate question that prints the estimate is step `audio_bitrate`,
which `wizard_flow.py` orders AFTER `video_speed_reverse`, so the factor is
known by the time the number is shown: this was reading past a value it had.
The fix reuses the same two helpers the command builder uses
(`video_speed_transform_enabled` + `encode_video_speed_factor`), so the
estimate and the encode cannot answer the speed question differently.
"""
import unittest

from ffmwiz import services

SOURCE_SECONDS = 600.0


def _answers(**extra):
    base = {
        "format": {"duration": f"{SOURCE_SECONDS}"},
        "video_streams": [{"duration": f"{SOURCE_SECONDS}"}],
        "audio_streams": [],
    }
    base.update(extra)
    return base


class TheEstimateUsesTheSpeedTheEncodeWillApply(unittest.TestCase):

    def test_no_speed_change_is_the_source_length(self):
        self.assertEqual(SOURCE_SECONDS,
                         services.estimated_encode_duration_seconds(_answers()))

    def test_the_encode_wizards_speed_key_is_honoured(self):
        self.assertEqual(
            SOURCE_SECONDS / 2,
            services.estimated_encode_duration_seconds(
                _answers(video_speed_enabled=True, video_speed_factor=2.0)))

    def test_slowing_down_lengthens_the_estimate(self):
        self.assertEqual(
            SOURCE_SECONDS * 2,
            services.estimated_encode_duration_seconds(
                _answers(video_speed_enabled=True, video_speed_factor=0.5)))

    def test_the_standalone_tools_key_still_works(self):
        # Guard the guard: the speed tool writes `speed_factor` and nothing
        # else, so the fix must not trade one key for the other.
        self.assertEqual(
            SOURCE_SECONDS / 2,
            services.estimated_encode_duration_seconds(_answers(speed_factor=2.0)))

    def test_a_declined_speed_step_ignores_a_stale_factor(self):
        # `video_speed_enabled` is the gate the filter builder reads, so a
        # factor left behind by a step the user then declined must not scale.
        self.assertEqual(
            SOURCE_SECONDS,
            services.estimated_encode_duration_seconds(
                _answers(video_speed_enabled=False, video_speed_factor=2.0)))

    def test_an_unusable_factor_falls_back_instead_of_raising(self):
        # An estimate is printed mid-wizard; it must never be the thing that
        # ends the session over a value the builder would reject later.
        for bad in ("not a number", 0, 99.0, None):
            with self.subTest(video_speed_factor=bad):
                self.assertEqual(
                    SOURCE_SECONDS,
                    services.estimated_encode_duration_seconds(
                        _answers(video_speed_enabled=True, video_speed_factor=bad)))

    def test_cuts_still_shorten_the_estimate(self):
        # Guard the guard: the kept-range branch runs before the speed one and
        # must keep doing so -- 100 s kept, then halved by a 2x speed.
        self.assertEqual(
            50.0,
            services.estimated_encode_duration_seconds(
                _answers(cut_keep_ranges=[(0.0, 100.0)],
                         video_speed_enabled=True, video_speed_factor=2.0)))


if __name__ == "__main__":
    unittest.main()
