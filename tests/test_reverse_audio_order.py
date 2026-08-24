"""Regression: a video-only reverse must not reorder the audio (B02).

`run_segmented_reverse_main_encode()` encodes each source chunk WITH its audio
and then concatenates `reversed(segment_paths)`. Reversing the files reverses
the order of their audio blocks too -- even when the user asked for video
reverse only and explicitly declined synchronized or reversed audio.

Measured on a 4 s fixture carrying 440 Hz for 0-2 s and 880 Hz for 2-4 s, with
1-second chunks, `reverse_video=True`, `audio_speed_from_video=False` and
`reverse_audio=False`:

    before   audio 880 Hz at 0.4 s, 440 Hz at 3.2 s   (chunk-reordered)
    after    audio 440 Hz at 0.4 s, 880 Hz at 3.2 s

Not one command contained `areverse`, so nothing in the argv gave it away: the
reordering came purely from the concat order. A test that only proves an audio
stream survived would pass on the broken output, which is why these read the
actual spectrum.

The repair concatenates the two timelines separately -- video from the reversed
order, audio from the source order -- and muxes them. Both passes are stream
copies, so the fix costs no extra encode. When the audio DOES follow the reverse
the single reversed concat is still correct and is kept.
"""
import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts
from ffmwiz import encoding
from ffmwiz.support import L00_split

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _goertzel(samples, rate, freq):
    """Energy at one frequency; enough to tell two pure tones apart."""
    count = len(samples)
    bin_index = int(0.5 + count * freq / rate)
    omega = 2 * math.pi * bin_index / count
    coeff = 2 * math.cos(omega)
    first = second = 0.0
    for value in samples:
        current = value + coeff * first - second
        second, first = first, current
    return math.sqrt(max(0.0, first * first + second * second
                         - coeff * first * second)) / count


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")
class TheAudioKeepsItsOwnOrder(NoLeakedArtifacts, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_revaudio_"))
        cls.source = cls._tmp / "src.mkv"
        # Video red then blue; audio 440 Hz then 880 Hz. Both halves are
        # identifiable, so an ordering mistake in either stream is visible.
        subprocess.run(
            [FFMPEG, "-v", "error", "-y",
             "-f", "lavfi", "-i", "color=c=red:size=160x120:rate=30:duration=2",
             "-f", "lavfi", "-i", "color=c=blue:size=160x120:rate=30:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
             "-filter_complex",
             "[0:v][1:v]concat=n=2:v=1[v];[2:a][3:a]concat=n=2:v=0:a=1[a]",
             "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", str(cls.source)],
            check=True, capture_output=True, timeout=300)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _probe(self, path):
        return json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120).stdout)

    def _tone_at(self, path, start, seconds=0.5):
        wav = path.with_suffix(f".{start:.1f}.wav")
        if wav.exists():
            wav.unlink()
        subprocess.run(
            [FFMPEG, "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(path),
             "-t", str(seconds), "-map", "0:a:0", "-ac", "1", "-ar", "16000",
             "-f", "wav", str(wav)], capture_output=True, timeout=120)
        if not wav.exists() or wav.stat().st_size < 200:
            return "silent"
        with wave.open(str(wav)) as handle:
            raw = handle.readframes(handle.getnframes())
            rate = handle.getframerate()
        samples = struct.unpack(f"<{len(raw) // 2}h", raw)
        energies = {freq: _goertzel(samples, rate, freq) for freq in (440, 880)}
        loudest = max(energies, key=energies.get)
        return loudest if energies[loudest] > 20 else "silent"

    def _colour_at(self, path, at):
        raw = subprocess.run(
            [FFMPEG, "-v", "error", "-ss", f"{at:.3f}", "-i", str(path),
             "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=120).stdout
        if len(raw) < 3:
            return "missing"
        red, _green, blue = raw[0], raw[1], raw[2]
        if red > blue + 40:
            return "red"
        if blue > red + 40:
            return "blue"
        return f"other({red},{blue})"

    def _reverse(self, label, chunk=1.0, **extra):
        out = self._tmp / label
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        info = self._probe(self.source)
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.source,
            "probe": info, "format": info["format"], "output_location": out,
            "video_streams": [s for s in info["streams"] if s["codec_type"] == "video"],
            "audio_streams": [s for s in info["streams"] if s["codec_type"] == "audio"],
            "subtitle_streams": [], "output_ext": "mkv", "audio_tracks": [0],
            "color_range_choice": "tv",
            "video_encoder": "libx264", "crf": 28, "preset": "ultrafast",
            "audio_codec": "aac",
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True,
        })
        answers.update(extra)
        real_split = L00_split.split_ranges_for_reverse_segments
        commands = []
        real_runner = encoding.run_ffmpeg_with_progress

        def small(ranges, duration, seconds=None):
            return real_split(ranges, duration, chunk)

        def spy(cmd, **kwargs):
            commands.append([str(part) for part in cmd])
            return real_runner(cmd, **kwargs)

        encoding.split_ranges_for_reverse_segments = small
        encoding.run_ffmpeg_with_progress = spy
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                answers["cmd"] = [str(p) for p in FFmWiz.build_ffmpeg_command(answers)]
                code, _elapsed = encoding.run_segmented_reverse_main_encode(answers)
        finally:
            encoding.split_ranges_for_reverse_segments = real_split
            encoding.run_ffmpeg_with_progress = real_runner
        self.assertEqual(0, code, noise.getvalue()[-1500:])
        return Path(answers["output_path"]), commands

    def test_the_fixture_really_carries_two_distinct_tones(self):
        # Guard the guard: with one tone throughout, every ordering assertion
        # below would pass regardless.
        self.assertEqual(440, self._tone_at(self.source, 0.5))
        self.assertEqual(880, self._tone_at(self.source, 2.5))

    def test_a_video_only_reverse_leaves_the_audio_in_source_order(self):
        output, _commands = self._reverse(
            "videoonly", audio_speed_from_video=False, reverse_audio=False)
        self.assertEqual(440, self._tone_at(output, 0.4),
                         "the audio was chunk-reordered by the reversed concat")
        self.assertEqual(880, self._tone_at(output, 3.2))

    def test_the_picture_is_still_reversed_in_that_case(self):
        output, _commands = self._reverse(
            "videoonly2", audio_speed_from_video=False, reverse_audio=False)
        self.assertEqual("blue", self._colour_at(output, 0.4))
        self.assertEqual("red", self._colour_at(output, 3.2))

    def test_no_command_reverses_audio_the_user_declined(self):
        _output, commands = self._reverse(
            "declined", audio_speed_from_video=False, reverse_audio=False)
        # Look inside the FILTER arguments only. Scanning whole argv lists for
        # the substring is a trap: an output directory called "noareverse"
        # matches it, which is exactly how this assertion first failed.
        filter_flags = {"-af", "-filter:a", "-filter_complex", "-lavfi"}
        offenders = []
        for cmd in commands:
            for index, part in enumerate(cmd[:-1]):
                if part in filter_flags and "areverse" in cmd[index + 1]:
                    offenders.append(cmd[index + 1])
        self.assertEqual([], offenders,
                         "areverse appeared although the user declined it")

    def test_that_filter_scan_can_actually_find_areverse(self):
        # Guard the guard: the scan above must not pass by looking in the
        # wrong place. The synchronized branch really does emit it.
        _output, commands = self._reverse("declinedguard", audio_speed_from_video=True)
        filter_flags = {"-af", "-filter:a", "-filter_complex", "-lavfi"}
        found = [cmd[index + 1] for cmd in commands
                 for index, part in enumerate(cmd[:-1])
                 if part in filter_flags and "areverse" in cmd[index + 1]]
        self.assertTrue(found, "the scan found no areverse where one must exist")

    def test_synchronized_audio_still_follows_the_reverse(self):
        # The other half of the contract: this branch was already right and
        # must stay right.
        output, commands = self._reverse("synced", audio_speed_from_video=True)
        self.assertEqual(880, self._tone_at(output, 0.4),
                         "synchronized audio must be reversed with the picture")
        self.assertEqual(440, self._tone_at(output, 3.2))
        self.assertTrue(any("areverse" in part for cmd in commands for part in cmd))

    def test_the_output_still_carries_both_streams(self):
        output, _commands = self._reverse(
            "streams", audio_speed_from_video=False, reverse_audio=False)
        kinds = [s["codec_type"] for s in self._probe(output)["streams"]]
        self.assertIn("video", kinds)
        self.assertIn("audio", kinds)

    def test_the_two_streams_still_span_the_same_time(self):
        output, _commands = self._reverse(
            "spans", audio_speed_from_video=False, reverse_audio=False)
        duration = float(self._probe(output)["format"]["duration"])
        self.assertAlmostEqual(duration, 4.0, delta=0.4,
                               msg=f"expected about 4 s of output, got {duration:.3f}s")


if __name__ == "__main__":
    unittest.main()
