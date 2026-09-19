"""Fixtures the three bounded-reverse suites share.

Not a test module -- named so the runner (which collects `test*.py`) does not
load it as a suite. It holds only what more than one of those suites needs: the
tool paths and their skip, the fixture's sample rate and marker tones, and the
three probes that read a produced file back.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import unittest

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

RATE = 44100
TONES = (300, 600, 1200, 2400)


def run_tool(args, timeout=300):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, encoding="utf-8",
                          errors="replace", timeout=timeout)


def goertzel(values, rate, freq):
    """Energy at one frequency; enough to tell the four marker tones apart."""
    count = len(values)
    if count == 0:
        return 0.0
    bin_index = int(0.5 + count * freq / rate)
    omega = 2 * math.pi * bin_index / count
    coeff = 2 * math.cos(omega)
    first = second = 0.0
    for value in values:
        current = value + coeff * first - second
        second, first = first, current
    return math.sqrt(max(0.0, first * first + second * second
                         - coeff * first * second)) / count


def probe_seconds(path):
    """The container duration, or None when ffprobe cannot say."""
    out = run_tool([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=nk=1:nw=1", str(path)])
    try:
        return float((out.stdout or "").strip())
    except (TypeError, ValueError):
        return None
