"""Regression: a hard memory cap needs the REAL size of a decoded pixel (D10).

`decoded_bytes_per_pixel()` used to read the layout out of the format NAME --
"444" means full chroma, a trailing "10le" means 10-bit -- and fell back to
1.5 B/px for anything it did not recognise. Both halves are wrong, and both
break the cap that is built on top of them.

Measured against FFmpeg 8.1.1 by decoding one 640x480 frame to rawvideo, i.e.
av_image_get_buffer_size(). 26 of the 205 convertible software formats came
back UNDER the real allocation:

    2.67x  vuya ayuv uyva vuyx 0rgb 0bgr v30xle          1.5 -> 4.0 B/px
    2.00x  nv24 nv42                                     1.5 -> 3.0 B/px
    2.00x  xyz12le xyz12be                               3.0 -> 6.0 B/px
    2.00x  yuv444p1{0,2}msb{le,be} gbrp1{0,2}msb{le,be}  3.0 -> 6.0 B/px
    1.33x  nv16                                          1.5 -> 2.0 B/px
    1.33x  rgb0 bgr0 x2rgb10le x2bgr10le                 3.0 -> 4.0 B/px
    1.07x  xv36le xv36be                                 7.5 -> 8.0 B/px

and the name reader also ran the other way: the "565" in `rgb565le` parsed as
a 565-bit component depth, so a 2 B/px format was billed at 213 B/px, which
collapses the reverse window to a single frame.

The replacement is a measured table plus a conservative fallback. The fallback
matters as much as the table: 1.5 B/px is the SMALLEST common layout, so using
it for an unrecognised name meant the cap held only for formats we had already
heard of.
"""
import shutil
import subprocess
import unittest

import FFmWiz

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

# Measured on FFmpeg 8.1.1 (see the module docstring for the command). These are
# the 13 formats the audit named, plus the four extra ones the same measurement
# turned up, plus the layouts that were already correct and must stay correct.
MEASURED_BYTES_PER_PIXEL = {
    # The audit's 13.
    "vuya": 4.0, "vuyx": 4.0, "nv42": 3.0, "nv24": 3.0, "0rgb": 4.0,
    "0bgr": 4.0, "xyz12le": 6.0, "xyz12be": 6.0, "nv16": 2.0,
    "x2rgb10le": 4.0, "x2rgb10be": 4.0, "x2bgr10le": 4.0, "x2bgr10be": 4.0,
    # Same measurement, same defect, not in the audit's list.
    "ayuv": 4.0, "uyva": 4.0, "v30xle": 4.0, "rgb0": 4.0, "bgr0": 4.0,
    "yuv444p10msble": 6.0, "gbrp12msble": 6.0, "xv36le": 8.0,
    # Already correct before the change; a conservative table must not lose
    # them, because every one of these makes the segment shorter than it needs.
    "yuv420p": 1.5, "nv12": 1.5, "yuv422p": 2.0, "yuv444p": 3.0,
    "yuv420p10le": 3.0, "yuv444p12le": 6.0, "rgb24": 3.0, "gray": 1.0,
    "yuva420p": 2.5, "yuyv422": 2.0, "p010le": 3.0, "rgba": 4.0,
    "rgb565le": 2.0, "bgr48le": 6.0, "rgba64le": 8.0,
}

# What the discarded name reader returned for the same formats. Kept so the
# assertions below prove the defect is gone rather than merely asserting the
# current numbers back at themselves.
NAME_READER_BYTES_PER_PIXEL = {
    "vuya": 1.5, "vuyx": 1.5, "nv42": 1.5, "nv24": 1.5, "0rgb": 1.5,
    "0bgr": 1.5, "xyz12le": 3.0, "xyz12be": 3.0, "nv16": 1.5,
    "x2rgb10le": 3.0, "x2rgb10be": 3.0, "x2bgr10le": 3.0, "x2bgr10be": 3.0,
    "ayuv": 1.5, "uyva": 1.5, "v30xle": 1.5, "rgb0": 3.0, "bgr0": 3.0,
    "yuv444p10msble": 3.0, "gbrp12msble": 3.0, "xv36le": 7.5,
}


def parse_pixel_formats(ffprobe: str) -> dict[str, dict]:
    """Every pixel format the installed FFmpeg reports, from its own mouth."""
    text = subprocess.run(
        [ffprobe, "-hide_banner", "-v", "error", "-show_pixel_formats"],
        capture_output=True, text=True, timeout=120).stdout
    formats: dict[str, dict] = {}
    current: dict | None = None
    for line in text.splitlines():
        line = line.strip()
        if line == "[PIXEL_FORMAT]":
            current = {"components": []}
        elif line == "[/PIXEL_FORMAT]" and current is not None:
            formats[current.get("name", "")] = current
            current = None
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            if key == "bit_depth":
                current["components"].append(int(value))
            else:
                current[key] = value
    formats.pop("", None)
    return formats


class TheNamedUnderestimatesAreGone(unittest.TestCase):
    """Explicit cases for every format the audit measured, plus the extras."""

    def test_no_format_is_billed_below_its_real_allocation(self):
        for name, real in MEASURED_BYTES_PER_PIXEL.items():
            with self.subTest(pix_fmt=name):
                self.assertGreaterEqual(
                    FFmWiz.decoded_bytes_per_pixel(name), real,
                    f"{name} really allocates {real} B/px")

    def test_each_underestimate_actually_moved(self):
        # Guard the guard: an estimate that merely happens to sit above the old
        # value proves nothing unless the old value was genuinely below the real
        # allocation, so assert both ends of the gap the audit measured.
        for name, old in NAME_READER_BYTES_PER_PIXEL.items():
            real = MEASURED_BYTES_PER_PIXEL[name]
            with self.subTest(pix_fmt=name):
                self.assertLess(old, real, "the old reader must have been low")
                self.assertGreater(FFmWiz.decoded_bytes_per_pixel(name), old)

    def test_the_common_layouts_are_still_exact(self):
        # Over-estimating is safe for the cap and expensive for the user: every
        # extra byte per pixel shortens the segment and adds a concat seam.
        for name in ("yuv420p", "nv12", "yuv422p", "yuv444p", "yuv420p10le",
                     "yuv444p12le", "rgb24", "gray", "yuva420p", "yuyv422",
                     "p010le", "rgba"):
            with self.subTest(pix_fmt=name):
                self.assertEqual(MEASURED_BYTES_PER_PIXEL[name],
                                 FFmWiz.decoded_bytes_per_pixel(name))

    def test_a_packed_size_is_not_read_as_a_component_depth(self):
        # `rgb565le` is 2 B/px. The name reader saw "565le" and charged 213.
        self.assertLessEqual(FFmWiz.decoded_bytes_per_pixel("rgb565le"), 3.0)
        self.assertLessEqual(FFmWiz.decoded_bytes_per_pixel("bgr555le"), 3.0)
        self.assertLessEqual(FFmWiz.decoded_bytes_per_pixel("rgb444le"), 3.0)


class AnUnknownFormatIsNotTheSmallestOne(unittest.TestCase):
    def test_an_unrecognised_name_has_no_measured_size(self):
        self.assertIsNone(FFmWiz.pixel_format_bytes_per_pixel("something_new"))
        self.assertIsNone(FFmWiz.pixel_format_bytes_per_pixel(None))
        self.assertIsNone(FFmWiz.pixel_format_bytes_per_pixel(""))

    def test_the_fallback_is_conservative_not_convenient(self):
        # 1.5 B/px was the smallest common layout. An unknown 16 B/px source
        # sized at 1.5 overruns the cap by more than ten times.
        for unknown in ("something_new", None, "cuda"):
            with self.subTest(pix_fmt=unknown):
                self.assertEqual(FFmWiz.UNKNOWN_PIXEL_FORMAT_BYTES,
                                 FFmWiz.decoded_bytes_per_pixel(unknown))
        self.assertGreaterEqual(FFmWiz.UNKNOWN_PIXEL_FORMAT_BYTES,
                                max(FFmWiz.BYTES_PER_PIXEL_BY_FORMAT.values()),
                                "the fallback must cover the widest layout the "
                                "table knows about")

    def test_hardware_surfaces_are_not_given_a_software_size(self):
        for name in ("cuda", "d3d11", "vaapi", "qsv", "vulkan"):
            with self.subTest(pix_fmt=name):
                self.assertIn(name, FFmWiz.HARDWARE_PIXEL_FORMATS)
                self.assertIsNone(FFmWiz.pixel_format_bytes_per_pixel(name))

    def test_a_surface_this_build_dropped_is_still_classified(self):
        """The set spans the SUPPORTED builds, not the one that is installed (D14).

        FFmpeg carried `xvmc` as a hardware surface until 7.0 removed it, so
        `ffprobe -show_pixel_formats` reports it on 6.x and not on 8.x. The live
        sweep below can only ever see the local build: on the audit's FFmpeg
        6.1.1 it failed with `MISSING_HARDWARE_FORMATS ['xvmc']`, and on this
        machine's 8.1.1 it cannot see the format at all and would pass whether
        or not the classification is right. Keeping the name in the maintained
        set is what makes the two builds agree.
        """
        self.assertIn("xvmc", FFmWiz.HARDWARE_PIXEL_FORMATS)
        self.assertIsNone(FFmWiz.pixel_format_bytes_per_pixel("xvmc"),
                          "a hardware surface has no software byte size")
        self.assertNotIn("xvmc", FFmWiz.BYTES_PER_PIXEL_BY_FORMAT)


@unittest.skipUnless(FFPROBE, "ffprobe required")
class EverySoftwareFormatThisFFmpegReports(unittest.TestCase):
    """The mandatory sweep: no format the build supports may be under-billed."""

    @classmethod
    def setUpClass(cls):
        cls.formats = parse_pixel_formats(FFPROBE)

    def test_the_sweep_actually_found_formats(self):
        self.assertGreater(len(self.formats), 100,
                           "parsing ffprobe -show_pixel_formats returned almost "
                           "nothing; the sweep below would prove nothing")

    def test_no_software_format_is_below_its_minimum_packed_size(self):
        for name, descriptor in self.formats.items():
            if descriptor.get("FLAGS:hwaccel") == "1":
                continue
            packed = descriptor.get("bits_per_pixel", "N/A")
            if packed == "N/A":
                continue
            with self.subTest(pix_fmt=name):
                self.assertGreaterEqual(
                    FFmWiz.decoded_bytes_per_pixel(name), int(packed) / 8.0,
                    f"{name} packs {packed} bits/pixel")

    def test_every_software_format_has_a_measured_entry(self):
        # A format present in the build but absent from the table falls back to
        # the conservative worst case, which is safe but wastes most of the
        # window. Surfacing it here is how the table gets regenerated after an
        # FFmpeg upgrade.
        missing = sorted(
            name for name, descriptor in self.formats.items()
            if descriptor.get("FLAGS:hwaccel") != "1"
            and name not in FFmWiz.BYTES_PER_PIXEL_BY_FORMAT)
        self.assertEqual([], missing,
                         "regenerate BYTES_PER_PIXEL_BY_FORMAT for this build")

    def test_every_hardware_surface_is_listed_as_one(self):
        missing = sorted(
            name for name, descriptor in self.formats.items()
            if descriptor.get("FLAGS:hwaccel") == "1"
            and name not in FFmWiz.HARDWARE_PIXEL_FORMATS)
        self.assertEqual([], missing)

    def test_the_planar_word_padding_the_cli_omits_is_still_charged(self):
        # ffprobe reports BITS_PER_PIXEL, which is the packed minimum, not the
        # allocation: 15 for yuv420p10le, which really takes 24 because FFmpeg
        # stores >8-bit components in whole 16-bit words. A table built only
        # from that number would under-bill every high-depth planar source.
        descriptor = self.formats.get("yuv420p10le")
        self.assertIsNotNone(descriptor)
        self.assertEqual("15", descriptor["bits_per_pixel"])
        self.assertEqual(3.0, FFmWiz.decoded_bytes_per_pixel("yuv420p10le"))


@unittest.skipUnless(FFMPEG, "ffmpeg required")
class TheTableMatchesWhatFFmpegActuallyAllocates(unittest.TestCase):
    """Ground truth, not a descriptor: decode one frame and count the bytes.

    Restricted to the formats the audit named plus the common layouts, because
    sweeping all ~205 convertible formats costs a minute of ffmpeg startups for
    a table that only changes when FFmpeg does.
    """

    WIDTH, HEIGHT = 640, 480

    def _real_bytes_per_pixel(self, name):
        raw = subprocess.run(
            [FFMPEG, "-hide_banner", "-v", "error",
             "-f", "lavfi", "-i", f"nullsrc=s={self.WIDTH}x{self.HEIGHT}",
             "-vf", f"format={name}", "-frames:v", "1", "-pix_fmt", name,
             "-f", "rawvideo", "-"],
            capture_output=True, timeout=120).stdout
        return len(raw) / float(self.WIDTH * self.HEIGHT) if raw else None

    def test_the_recorded_measurements_still_hold(self):
        checked = 0
        for name, expected in MEASURED_BYTES_PER_PIXEL.items():
            real = self._real_bytes_per_pixel(name)
            if real is None:
                continue  # this build cannot convert to it; the sweep covers it
            checked += 1
            with self.subTest(pix_fmt=name):
                self.assertAlmostEqual(
                    expected, real, places=4,
                    msg=f"{name} now allocates {real} B/px, not {expected}")
                self.assertGreaterEqual(FFmWiz.decoded_bytes_per_pixel(name), real)
        self.assertGreater(checked, 20, "almost nothing was measurable; the "
                                        "assertions above proved little")


if __name__ == "__main__":
    unittest.main()
