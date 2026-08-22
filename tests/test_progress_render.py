"""Regression tests for the FFmpeg progress line's width behaviour.

The status line is clamped to a single terminal row. It used to be clamped by
chopping the tail and appending "...", and because ETA is the LAST field, ETA
was always the first thing destroyed on a narrow terminal -- the user saw
`... ` where the remaining time should be.

The renderer now drops the OPTIONAL fields by rank until the line fits, so
percent, time/total, elapsed and ETA survive every width.
"""
import io
import os
import time
import unittest
from unittest import mock

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


class NarrowTerminalWidth(unittest.TestCase):
    """USER-13-3: the one-row guarantee only holds if the reported width is the
    REAL width. A 60-column floor on a 40-column terminal makes the renderer
    emit 59 visible characters, which wraps to two rows -- and the carriage
    return + clear-line then clears only the last one, leaving orphans."""

    ERASE = "\r" + chr(27) + "[2K"

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    @staticmethod
    def _fake_size(columns):
        return mock.patch("shutil.get_terminal_size",
                          return_value=os.terminal_size((columns, 20)))

    def test_a_narrow_terminal_is_reported_honestly(self):
        with self._fake_size(40):
            self.assertEqual(runtime_render._progress_terminal_width(), 40)

    def test_a_wide_terminal_is_still_reported_as_is(self):
        with self._fake_size(160):
            self.assertEqual(runtime_render._progress_terminal_width(), 160)

    def test_written_line_fits_a_narrow_terminal(self):
        started = time.perf_counter() - 300.0
        rendered = runtime_render._render_progress_line(_state(), 3600.0, started)
        buffer = io.StringIO()
        with self._fake_size(40), \
                mock.patch.object(FFmWiz.runtime, "_stdout_supports_in_place_progress",
                                  return_value=True), \
                mock.patch.object(FFmWiz.runtime, "_enable_windows_vt_mode"), \
                mock.patch.object(FFmWiz.runtime.sys, "stdout", buffer):
            FFmWiz.runtime._write_progress_line(rendered)
        payload = buffer.getvalue().replace(self.ERASE, "")
        self.assertLessEqual(
            FFmWiz._visible_len(payload), 39,
            "the status line must fit one row of a 40-column terminal",
        )


class ColourSamplePreview(unittest.TestCase):
    """USER-13-4: the colour-sample screen advertises a '100-column preview'.
    It passed max_width=100 to a renderer that ignored the parameter, so the
    preview came out byte-identical to the full line printed above it."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_the_hundred_column_preview_is_actually_narrower(self):
        started = time.perf_counter() - 300.0
        full = runtime_render._render_progress_line(_state(), 3600.0, started)
        preview = runtime_render._render_progress_line(_state(), 3600.0, started, max_width=100)
        self.assertGreater(FFmWiz._visible_len(full), 100)
        self.assertNotEqual(preview, full, "the preview must not repeat the full line")
        self.assertLessEqual(FFmWiz._visible_len(preview), 100)




if __name__ == "__main__":
    unittest.main()
