"""Regression: `reset_sar` is probed, not assumed (D01).

`scale`/`scale_cuda` gained `reset_sar` in a 2025 commit first shipped in the
FFmpeg 7.2/8.0 line. Every release up to and including 7.1.x rejects it with
"Option 'reset_sar' not found" and the encode dies at filter init -- so every
scaled encode was broken for anyone on an older build.

The external brief proposed replacing it with a trailing `setsar=1`. That is
WRONG and the fallback here deliberately does not do it. Measured on a real
720x576 source with SAR 64:45 (DAR 16:9), scaling into a 1280x720 box:

    scale=...:reset_sar=1                 -> 1280x720  SAR 1:1  DAR 16:9   correct
    scale=...,setsar=1                    ->  900x720  SAR 1:1  DAR  5:4   SQUEEZED
    scale=iw*sar:ih,setsar=1,scale=...    -> 1280x720  SAR 1:1  DAR 16:9   correct

A trailing setsar only relabels the pixels as square without rescaling, so the
picture is distorted. The portable fallback normalises to square pixels FIRST.
"""
import ast
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

from ffmwiz.support import ext00c

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

BOX = "scale=1280:720:force_original_aspect_ratio=decrease"


class OptionProbe(unittest.TestCase):
    @unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
    def test_a_real_option_is_found(self):
        self.assertTrue(FFmWiz.filter_option_available(FFMPEG, "scale", "width"))

    @unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
    def test_an_invented_option_is_not(self):
        # A probe that returns True for everything is worse than no probe.
        self.assertFalse(
            FFmWiz.filter_option_available(FFMPEG, "scale", "not_a_real_option"))

    @unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
    def test_a_name_that_is_only_a_SUBSTRING_of_a_real_option_is_rejected(self):
        # `scale` has reset_sar but no bare `sar`. A substring match would
        # report `sar` as supported and the probe would silently start
        # approving options that do not exist.
        self.assertTrue(FFmWiz.filter_option_available(FFMPEG, "scale", "reset_sar"))
        self.assertFalse(FFmWiz.filter_option_available(FFMPEG, "scale", "sar"))

    @unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
    def test_an_unknown_filter_is_not_claimed_to_support_anything(self):
        self.assertFalse(
            FFmWiz.filter_option_available(FFMPEG, "no_such_filter", "width"))

    def test_a_missing_binary_answers_false_instead_of_raising(self):
        # The probe runs while building a command; it must never be the thing
        # that crashes the wizard.
        self.assertFalse(
            FFmWiz.filter_option_available("definitely-not-ffmpeg", "scale", "reset_sar"))

    @unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
    def test_the_answer_is_memoised(self):
        # One process launch per (binary, filter, option); the answer cannot
        # change while the binary does not.
        FFmWiz.filter_option_available(FFMPEG, "scale", "reset_sar")
        key = (str(FFMPEG), "scale", "reset_sar")
        self.assertIn(key, ext00c._FILTER_OPTION_CACHE)


class ChainSelection(unittest.TestCase):
    def _chain(self, supported):
        real = ext00c.filter_option_available
        ext00c.filter_option_available = lambda *a, **k: supported
        try:
            return ext00c.square_pixel_scale_chain("ffmpeg", BOX)
        finally:
            ext00c.filter_option_available = real

    def test_a_modern_build_uses_the_inline_option(self):
        chain = self._chain(True)
        self.assertEqual(f"{BOX}:reset_sar=1", chain)

    def test_an_old_build_normalises_to_square_pixels_first(self):
        chain = self._chain(False)
        self.assertTrue(chain.startswith("scale=iw*sar:ih,setsar=1,"), chain)
        self.assertIn(BOX, chain)

    def test_the_fallback_never_emits_the_unsupported_option(self):
        self.assertNotIn("reset_sar", self._chain(False))

    def test_the_fallback_is_not_a_bare_trailing_setsar(self):
        # The shape the brief proposed; it squeezes an anamorphic source.
        chain = self._chain(False)
        self.assertNotEqual(f"{BOX},setsar=1", chain)


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")
class BothFormsProduceTheSameGeometry(unittest.TestCase):
    """The fallback has to be equivalent, not merely accepted."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_sar_"))
        cls._src = cls._tmp / "anamorphic.mkv"
        # 720x576 stored, displayed 16:9 -> SAR 64:45. The only case where the
        # two forms can differ.
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=720x576:rate=25:duration=1",
             "-c:v", "libx264", "-preset", "ultrafast", "-vf", "setsar=64/45",
             str(cls._src)], check=True, timeout=300)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _encode(self, name, video_filter):
        out = self._tmp / f"{name}.mkv"
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(self._src),
             "-vf", video_filter, "-c:v", "libx264", "-preset", "ultrafast",
             "-frames:v", "1", str(out)],
            capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr.strip()[-300:])
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,sample_aspect_ratio,display_aspect_ratio",
             "-of", "json", str(out)],
            capture_output=True, text=True, timeout=60).stdout)["streams"][0]
        return (probe["width"], probe["height"],
                probe.get("sample_aspect_ratio"), probe.get("display_aspect_ratio"))

    def test_the_source_really_is_anamorphic(self):
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=sample_aspect_ratio", "-of", "json", str(self._src)],
            capture_output=True, text=True, timeout=60).stdout)["streams"][0]
        self.assertEqual("64:45", probe.get("sample_aspect_ratio"),
                         "the fixture is not anamorphic; the test proves nothing")

    def test_the_fallback_matches_the_inline_option(self):
        inline = self._encode("inline", f"{BOX}:reset_sar=1")
        fallback = self._encode("fallback", f"scale=iw*sar:ih,setsar=1,{BOX}")
        self.assertEqual(inline, fallback,
                         "the portable fallback must produce the same geometry")
        self.assertEqual((1280, 720, "1:1", "16:9"), inline)

    def test_a_bare_trailing_setsar_would_have_been_wrong(self):
        # Pinning the defect the brief proposed, so nobody "simplifies" the
        # fallback back into it.
        squeezed = self._encode("squeezed", f"{BOX},setsar=1")
        self.assertEqual((900, 720, "1:1", "5:4"), squeezed)
        self.assertNotEqual(self._encode("inline2", f"{BOX}:reset_sar=1"), squeezed)


class EveryScaleSiteIsGuarded(unittest.TestCase):
    """No builder may emit the option without asking first."""

    SOURCES = ("ffmwiz/wizard_build.py", "ffmwiz/wizard_build_b.py",
               "ffmwiz/modes_join.py", "ffmwiz/support/ext02.py")

    # A flat line scan cannot tell a guarded emit from a bare one, so walk the
    # AST: any function that puts reset_sar into a filter string must itself
    # either call the probe or delegate to the shared chain builder.
    GUARDS = {"filter_option_available", "square_pixel_scale_chain"}

    @staticmethod
    def _calls_in(node):
        return {child.func.id for child in ast.walk(node)
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)}

    def test_every_reset_sar_emit_is_behind_the_probe(self):
        root = Path(FFmWiz.__file__).resolve().parent
        checked = 0
        for name in self.SOURCES:
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            for func in [n for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                emits = [n for n in ast.walk(func)
                         if isinstance(n, ast.Constant) and isinstance(n.value, str)
                         and "reset_sar" in n.value]
                if not emits:
                    continue
                checked += 1
                with self.subTest(f"{name}::{func.name}"):
                    self.assertTrue(
                        self._calls_in(func) & self.GUARDS,
                        f"{name}::{func.name} emits reset_sar without calling "
                        f"the probe or the shared chain builder")
        self.assertGreater(checked, 0, "no emit site found -- the scan is broken")


if __name__ == "__main__":
    unittest.main()
