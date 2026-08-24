"""Guard: a real-media fixture must state its colour policy (B16).

`resolve_color_range_for_encode()` raises `ColorRangeUnresolvedError` when the
source range is unknown and the fixture made no choice. Whether it is unknown
depends on the FFmpeg build: a synthetic `lavfi` source reports `color_range=tv`
on 8.1 and nothing on 6.1, the project's declared floor. So a fixture that omits
the decision passes on a modern machine and errors on a supported one.

That is not hypothetical. An external audit running 6.1.1 got 31 setup errors
before any of the intended media assertions executed:

    bounded reverse pipeline   10
    edited Join subtitles       9
    processed Split timeline    7
    reverse segment windows     5

Re-measuring here found seven MORE modules with the same latent gap (46 further
errors), none of which the audit had reached. The fix is one explicit line per
fixture -- `"color_range_choice": "tv"` -- never a production compatibility
fallback, which would let a real user's unknown-range source through silently.

This guard is deliberately narrow. It applies to a module only when it BOTH
generates a real `lavfi` source AND feeds it to a strict builder; a module that
hand-builds stream dictionaries with an explicit `color_range` is deterministic
on every FFmpeg version and is not covered.
"""
import re
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

STRICT_BUILDER = re.compile(r"build_ffmpeg_command\s*\(|build_join_encode_command\s*\(")
REAL_SOURCE = re.compile(r"lavfi")
DECISION = "color_range_choice"

# Modules that meet the shape but provably never reach the strict resolution.
# Keep each entry justified; an unexplained entry is a hole in the guard.
EXEMPT = {
    # Stream Cleanup is a `-c copy` remux: no video is encoded, so no colour
    # range has to be resolved. Measured as 0 colour-range errors with the
    # resolver forced to report nothing.
    "test_mux_cleanup": "stream-copy remux, never encodes video",
}


def _modules_at_risk():
    for path in sorted(TESTS_DIR.glob("test*.py")):
        text = path.read_text(encoding="utf-8")
        if STRICT_BUILDER.search(text) and REAL_SOURCE.search(text):
            yield path.stem, text


class EveryRealMediaFixtureStatesItsColourPolicy(unittest.TestCase):
    def test_no_module_omits_the_decision(self):
        missing = [name for name, text in _modules_at_risk()
                   if DECISION not in text and name not in EXEMPT]
        self.assertEqual(
            [], missing,
            "these fixtures build a real lavfi source through a strict builder "
            "without an explicit colour decision, so they pass on FFmpeg 8.1 and "
            f'error on 6.1. Add \'"{DECISION}": "tv"\' to the answers dict: '
            + ", ".join(missing))

    def test_the_guard_actually_matches_something(self):
        # Guard the guard: if the builder or lavfi patterns stopped matching,
        # the assertion above would pass over an empty set forever.
        found = list(_modules_at_risk())
        self.assertGreater(len(found), 15,
                           f"expected many real-media modules, found {len(found)}")

    def test_every_exemption_carries_a_reason(self):
        for name, reason in EXEMPT.items():
            self.assertTrue(reason.strip(),
                            f"{name} is exempt without a stated reason")
            self.assertTrue((TESTS_DIR / f"{name}.py").exists(),
                            f"{name} is exempt but no longer exists; drop the entry")

    def test_an_exempt_module_still_has_to_meet_the_shape(self):
        # An exemption for a module the guard would not have flagged anyway is
        # dead weight that hides a future regression.
        at_risk = {name for name, _text in _modules_at_risk()}
        for name in EXEMPT:
            self.assertIn(name, at_risk,
                          f"{name} no longer matches the guard; remove the exemption")


if __name__ == "__main__":
    unittest.main()
