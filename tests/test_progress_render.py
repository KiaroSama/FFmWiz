"""Regression tests for the FFmpeg progress line's width behaviour.

The status line is clamped to a single terminal row. It used to be clamped by
chopping the tail and appending "...", and because ETA is the LAST field, ETA
was always the first thing destroyed on a narrow terminal -- the user saw
`... ` where the remaining time should be.

The renderer now drops the OPTIONAL fields by rank until the line fits, so
percent, time/total, elapsed and ETA survive every width.
"""
import time
import unittest

import FFmWiz
from ffmwiz import runtime_render


def _state():
    return {
        "_ffmwiz_current_s": "1830.0",
        "fps": "142.7",
        "stream_0_0_q": "23.0",
        "speed": "6.12x",
        "total_size": "734003200",
        "bitrate": "3204.5kbits/s",
    }


class ProgressLineWidth(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._started = time.perf_counter() - 300.0

    def _render(self, width):
        return runtime_render._render_progress_line(_state(), 3600.0, self._started, max_width=width)

    # ---------------- the fields that must never be dropped ----------------

    def test_eta_survives_every_width(self):
        for width in (200, 120, 100, 88, 76, 64, 52, 40, 20):
            line = self._render(width)
            self.assertIn("ETA", line, f"ETA disappeared at width {width}")
            self.assertIn("%", line, f"percent disappeared at width {width}")
            self.assertIn("time 00:30:30", line, f"position disappeared at width {width}")

    def test_elapsed_is_given_up_before_eta(self):
        # On a terminal too narrow for both, the time REMAINING is worth more
        # than the time already spent.
        line = self._render(52)
        self.assertIn("ETA", line)
        self.assertNotIn("elapsed", line)

    def test_renderer_never_appends_an_ellipsis(self):
        # Dropping whole fields replaces tail-chopping; the renderer itself must
        # never emit the "..." marker that used to eat the ETA.
        for width in (200, 100, 60, 30):
            self.assertNotIn("...", self._render(width))

    # ---------------- drop order ----------------

    def test_wide_terminal_shows_every_field(self):
        line = self._render(200)
        for field in ("fps ", "q ", "speed ", "size ", "bitrate ", "elapsed ", "ETA"):
            self.assertIn(field, line)

    def test_least_useful_fields_are_dropped_first(self):
        # q is the first to go, then fps, then bitrate, then size, then speed.
        order = ["q ", "fps ", "bitrate ", "size ", "speed "]
        dropped_at = {}
        for field in order:
            for width in range(200, 40, -2):
                if field not in self._render(width):
                    dropped_at[field] = width
                    break
            else:
                self.fail(f"{field!r} was never dropped, even at width 40")
        widths = [dropped_at[field] for field in order]
        self.assertEqual(
            widths, sorted(widths, reverse=True),
            f"fields must drop in rank order, got {dropped_at}",
        )

    def test_line_fits_the_budget_while_droppable_fields_remain(self):
        for width in (120, 100, 88, 76):
            line = self._render(width)
            self.assertLessEqual(
                FFmWiz._visible_len(line), width,
                f"line still exceeds {width} columns while optional fields remain",
            )

    def test_eta_survives_the_full_render_plus_clamp_path(self):
        # The real user-visible bug: _write_progress_line clamps the rendered
        # line with _truncate_ansi_visible(rendered, width - 1), which appends
        # "..." and destroyed the trailing ETA. Reproduce that exact pipeline.
        # 60 is the floor _progress_terminal_width() enforces.
        for width in (120, 100, 88, 76, 64, 60):
            rendered = self._render(width - 1)
            clamped = FFmWiz._truncate_ansi_visible(rendered, width - 1)
            self.assertIn("ETA", clamped, f"ETA lost to the one-row clamp at width {width}")
            self.assertNotIn("...", clamped, f"line was tail-chopped at width {width}")

    def test_no_budget_means_the_complete_line(self):
        # Callers that want the full line (tests, the colour preview) must keep
        # getting every field; only the console display path asks for a budget.
        line = runtime_render._render_progress_line(_state(), 3600.0, self._started)
        for field in ("fps ", "q ", "speed ", "size ", "bitrate ", "elapsed ", "ETA"):
            self.assertIn(field, line)


if __name__ == "__main__":
    unittest.main()
