"""Shared fixtures for the compositing suites.

`test_composite_inputs` proves the INPUT INDICES in the argv, `test_composite_media`
proves the result in pixels and tones. Both need the same tone frequencies and the
same ffmpeg availability gate, so they live here rather than being duplicated -- a
second copy of MAIN_TONE that drifted would make the two suites disagree silently.

Deliberately not named `test*.py`: run_suite discovers by that glob, and this file
holds no tests.
"""
import shutil
import subprocess
import unittest

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

MAIN_TONE = 440
BED_TONE = 880
CONTROL_TONE = 1500


def _run(args, timeout=300):
    return subprocess.run([str(part) for part in args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace", timeout=timeout)
