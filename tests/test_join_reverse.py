"""Regression: Join + Reverse must include every input (R01).

`FFmWiz.py` routed any non-Split reverse job to `run_segmented_reverse_main_encode()`,
which rebuilds every segment through the SINGLE-input `build_ffmpeg_command()` path
and takes its duration from `answers["format"]` -- input 1's. A joined job therefore
reversed input 1 alone and discarded the rest.

Reproduced before the fix with two 2-second clips (red/440 Hz then blue/880 Hz):

    planned command      : 2 inputs, concat=n=2          (correct)
    executed result      : 2.123 s, every sampled frame red
    expected             : ~4 s, blue (input 2) then red (input 1)

The join command itself was already right -- executing it directly gave 4.040 s with
blue at 0.3 s/1.5 s and red at 2.5 s/3.7 s. So the repair is to stop the dispatcher
hijacking a join, which is the same guard `build_separator_job_specs` already applies.
"""
import array
import json
import math
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

import FFmWiz

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _reverse_answers(**extra):
    answers = {
        "video_streams": [{"codec_type": "video", "codec_name": "h264",
                           "width": 320, "height": 240,
                           "avg_frame_rate": "25/1", "r_frame_rate": "25/1"}],
        "audio_streams": [{"codec_type": "audio", "codec_name": "aac", "channels": 2}],
        "video_speed_enabled": True, "video_speed_factor": 1.0,
        "reverse_video": True, "output_ext": "mkv",
    }
    answers.update(extra)
    return answers


class DispatcherRefusesToSegmentAJoin(unittest.TestCase):
    """The routing decision itself, independent of any encode."""

    def test_a_single_input_reverse_still_uses_the_segmented_executor(self):
        # The bounded executor exists for a reason; do not lose it.
        self.assertTrue(FFmWiz.reverse_video_needs_segmented_main_encode(
            _reverse_answers()))

    def test_a_joined_reverse_does_not(self):
        self.assertFalse(FFmWiz.reverse_video_needs_segmented_main_encode(
            _reverse_answers(join_input_items=[{"path": "b.mkv"}])))

    def test_an_empty_join_list_is_not_treated_as_a_join(self):
        self.assertTrue(FFmWiz.reverse_video_needs_segmented_main_encode(
            _reverse_answers(join_input_items=[])))

    def test_the_guard_lives_with_the_predicate_not_at_a_call_site(self):
        # Both callers -- FFmWiz.py's dispatcher and encoding.py's per-job loop --
        # must be covered, so the check belongs in the shared predicate.
        source = (Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "support" / "L02.py").read_text(encoding="utf-8")
        body = source.split("def reverse_video_needs_segmented_main_encode")[1]
        body = body.split("\ndef ")[0]
        self.assertIn("join_input_items", body)


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")
class RealJoinReverseKeepsEveryInput(unittest.TestCase):
    """Drive the public execution dispatcher, not a helper."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_joinrev_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _clip(self, name, colour, freq, duration=2):
        path = self._tmp / f"{name}.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"color=c={colour}:s=320x240:d={duration}:r=25",
             "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}",
             "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
             "-vf", "format=yuv420p", "-c:a", "aac", str(path)],
            check=True, timeout=300)
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json",
             str(path)], capture_output=True, text=True, timeout=60).stdout)
        streams = probe["streams"]
        return {"path": path, "streams": streams, "format": probe["format"],
                "duration": float(duration),
                "video_streams": [s for s in streams if s["codec_type"] == "video"],
                "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
                "subtitle_streams": [], "attachment_streams": [], "data_streams": []}

    def _colour_at(self, path, when):
        """'red' / 'blue' for the frame at `when`, by averaging it to one pixel."""
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-v", "error", "-ss", f"{when:.3f}",
             "-i", str(path), "-frames:v", "1", "-vf", "scale=1:1",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=120)
        raw = result.stdout
        self.assertGreaterEqual(len(raw), 3, f"no frame decoded at {when}s")
        red, green, blue = raw[0], raw[1], raw[2]
        if red > green + 40 and red > blue + 40:
            return "red"
        if blue > red + 40 and blue > green + 40:
            return "blue"
        return f"({red},{green},{blue})"

    @staticmethod
    def _goertzel(samples, rate, target_hz):
        """Energy at one frequency. Standard single-bin Goertzel."""
        count = len(samples)
        if count == 0:
            return 0.0
        bin_index = int(0.5 + (count * target_hz) / rate)
        omega = (2.0 * math.pi * bin_index) / count
        coeff = 2.0 * math.cos(omega)
        prev = prev2 = 0.0
        for sample in samples:
            current = sample + coeff * prev - prev2
            prev2, prev = prev, current
        return (prev2 * prev2 + prev * prev - coeff * prev * prev2) / count

    def _decode_audio(self, path):
        """(samples, rate) for the whole track, decoded once.

        Deliberately NOT a windowed ffmpeg measurement: `-ss` before `-i`,
        `-ss` after `-i` and `atrim` all gave inconsistent readings on this
        output and nearly produced a false defect report. Decoding once and
        analysing the raw samples has no such ambiguity.
        """
        wav = path.with_suffix(".probe.wav")
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
             "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le",
             str(wav)], check=True, timeout=300)
        with wave.open(str(wav), "rb") as handle:
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
        wav.unlink(missing_ok=True)
        samples = array.array("h")
        samples.frombytes(raw)
        return samples, rate

    def _dominant_tone(self, path, start, end, samples=None, rate=None):
        """'440' or '880' for the window, whichever sine carries more energy."""
        if samples is None:
            samples, rate = self._decode_audio(path)
        window = samples[max(0, int(start * rate)):min(len(samples), int(end * rate))]
        energies = {tone: self._goertzel(window, rate, tone) for tone in (440, 880)}
        return str(max(energies, key=energies.get))

    def _run_join_reverse(self):
        first = self._clip("red", "red", 440)
        second = self._clip("blue", "blue", 880)
        items = [first, second]
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
            "input_path": first["path"], "output_location": self._tmp,
            "streams": first["streams"], "format": first["format"],
            "video_streams": first["video_streams"],
            "audio_streams": first["audio_streams"],
            "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
            "output_ext": "mkv", "video_codec": "H264", "use_gpu": False,
            "audio_codec": "aac", "audio_bitrate_kbps": 128,
            "audio_tracks": [0], "subtitle_tracks": [],
            "resolution": "n", "fps": 25, "video_bitrate_kbps": 500,
            "color_range_choice": "tv", "join_input_items": items[1:],
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True, "audio_speed_from_video": True,
        }
        cmd = [str(part) for part in FFmWiz.build_join_encode_command(
            answers, items, self._tmp / "joined.mkv")]
        answers["cmd"] = cmd

        # The public dispatcher: this is what decides between the segmented
        # executor and the ordinary path, and it is where R01 went wrong.
        self.assertFalse(
            FFmWiz.reverse_video_needs_segmented_main_encode(answers),
            "a join must not be routed into the single-input segmented executor")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        self.assertEqual(result.returncode, 0, result.stderr.strip()[-400:])
        return Path(answers["output_path"]), items

    def test_the_output_holds_both_inputs(self):
        output, items = self._run_join_reverse()
        duration = float(json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_format", "-of", "json", str(output)],
            capture_output=True, text=True, timeout=60).stdout)["format"]["duration"])
        expected = sum(item["duration"] for item in items)
        self.assertAlmostEqual(expected, duration, delta=0.5,
                               msg=f"expected ~{expected}s, got {duration}s -- an input "
                                   "is missing")

    def test_the_visual_order_is_reversed(self):
        # 0.6 s, not 0.3: the claim here is about ORDER, and the first ~0.35 s
        # of the file belongs to the encoder rather than to either input.
        # Measured on FFmpeg 7.1.1 -- an x264 encode carrying this job's
        # `-b:v 500k -maxrate 1000k -bufsize 2000k` opens with about nine grey
        # frames averaging (133,130,134). It is not the join and not the
        # reverse: the same lead-in appears with `reverse` removed from the
        # graph, and disappears the moment the bitrate cap is dropped. FFmpeg
        # 6.1.1 and 9.0.1 do not do it. Sampling inside that window made an
        # ordering test hostage to rate-control start-up on one build.
        output, _items = self._run_join_reverse()
        self.assertEqual("blue", self._colour_at(output, 0.6),
                         "input 2 must come first in a reversed join")
        self.assertEqual("blue", self._colour_at(output, 1.5))
        self.assertEqual("red", self._colour_at(output, 2.5))
        self.assertEqual("red", self._colour_at(output, 3.7))

    def test_the_audio_order_is_reversed_too(self):
        # 880 Hz (input 2) must lead, 440 Hz (input 1) must follow.
        output, _items = self._run_join_reverse()
        samples, rate = self._decode_audio(output)
        self.assertEqual("880", self._dominant_tone(output, 0.2, 1.8, samples, rate))
        self.assertEqual("440", self._dominant_tone(output, 2.2, 3.8, samples, rate))

    def test_the_tone_detector_itself_is_sound(self):
        # A measurement method this test depends on has to be validated against
        # known input, or a bad detector reads as a product defect. An earlier
        # ffmpeg-windowed version of this check did exactly that.
        first = self._clip("red", "red", 440)
        second = self._clip("blue", "blue", 880)
        self.assertEqual("440", self._dominant_tone(first["path"], 0.2, 1.8))
        self.assertEqual("880", self._dominant_tone(second["path"], 0.2, 1.8))


if __name__ == "__main__":
    unittest.main()
