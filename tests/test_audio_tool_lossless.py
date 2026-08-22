"""Audio tools: lossless trims (USER-5-4) and surviving cover art (USER-5-5).

A single keep-range Audio Cut re-encoded a track the output container already
accepts, and every audio tool mapped `0:a:N -vn`, which throws away an existing
attached picture (an MP3 APIC, an m4a cover, a FLAC picture block).
"""
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import FFmWiz

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _answers(tmp, *, codec="mp3", ext="mp3", cover=False, **extra):
    stream = {"codec_type": "audio", "codec_name": codec, "channels": 2,
              "sample_rate": "48000", "bit_rate": "320000"}
    video_streams = []
    if cover:
        video_streams = [{"codec_type": "video", "codec_name": "mjpeg",
                          "disposition": {"attached_pic": 1}}]
    answers = {
        "ffmpeg": "ffmpeg",
        "input_path": Path(tmp) / f"src.{ext}",
        "output_location": Path(tmp),
        "audio_index": 0,
        "audio_streams": [stream],
        "video_streams": video_streams,
        "format": {"duration": "60"},
        "audio_keep_ranges": [(1.0, 4.0)],
    }
    answers.update(extra)
    return answers


def _text(cmd):
    return " ".join(str(part) for part in cmd)


class AudioCutStreamCopy(unittest.TestCase):
    """USER-5-4: offer a real stream copy when the container accepts the codec."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_copy_is_available_for_a_single_range_in_a_matching_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = _answers(tmp)
            answers["output_ext"] = "mp3"
            self.assertTrue(FFmWiz.audio_cut_stream_copy_available(answers))

    def test_copy_is_not_available_across_codecs_or_ranges_or_transforms(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcode = _answers(tmp)
            transcode["output_ext"] = "flac"
            self.assertFalse(FFmWiz.audio_cut_stream_copy_available(transcode))

            multi = _answers(tmp, audio_keep_ranges=[(1.0, 4.0), (6.0, 9.0)])
            multi["output_ext"] = "mp3"
            self.assertFalse(FFmWiz.audio_cut_stream_copy_available(multi))

            loud = _answers(tmp, loudnorm_enabled=True)
            loud["output_ext"] = "mp3"
            self.assertFalse(FFmWiz.audio_cut_stream_copy_available(loud))

            rated = _answers(tmp, audio_sample_rate=44100)
            rated["output_ext"] = "mp3"
            self.assertFalse(FFmWiz.audio_cut_stream_copy_available(rated))

    def test_chosen_copy_emits_c_a_copy_and_no_encoder_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = _answers(tmp, audio_cut_stream_copy=True)
            text = _text(FFmWiz.build_audio_cut_command(answers))
        self.assertIn("-c:a copy", text)
        self.assertIn("-avoid_negative_ts make_zero", text)
        self.assertNotIn("-b:a", text)
        self.assertNotIn("libmp3lame", text)

    def test_the_encode_path_is_untouched_when_copy_was_not_chosen(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = _text(FFmWiz.build_audio_cut_command(_answers(tmp)))
        self.assertIn("-c:a libmp3lame", text)
        self.assertIn("-b:a 320k", text)
        self.assertNotIn("-c:a copy", text)

    def test_an_unsafe_copy_request_is_ignored_by_the_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = _answers(tmp, audio_cut_stream_copy=True,
                               output_location=Path(tmp) / "out.flac")
            text = _text(FFmWiz.build_audio_cut_command(answers))
        self.assertIn("-c:a flac", text)
        self.assertNotIn("-c:a copy", text)

    def test_the_step_offers_the_copy_only_when_it_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = _answers(tmp)
            with contextlib.redirect_stdout(io.StringIO()), \
                 mock.patch.object(FFmWiz.appio, "ask_yes_no", side_effect=[True, True]) as ask:
                FFmWiz.step_audio_cut_start_now(answers)
            self.assertEqual(ask.call_count, 2, "the lossless question must be asked")
            self.assertTrue(answers["audio_cut_stream_copy"])
            self.assertIn("-c:a copy", _text(answers["cmd"]))

    def test_the_step_does_not_offer_a_copy_that_cannot_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = _answers(tmp, audio_keep_ranges=[(1.0, 4.0), (6.0, 9.0)])
            with contextlib.redirect_stdout(io.StringIO()), \
                 mock.patch.object(FFmWiz.appio, "ask_yes_no", side_effect=[True]) as ask:
                FFmWiz.step_audio_cut_start_now(answers)
            self.assertEqual(ask.call_count, 1, "only the start question belongs here")
            self.assertNotIn("-c:a copy", _text(answers["cmd"]))


class AudioToolCoverArt(unittest.TestCase):
    """USER-5-5: keep an existing attached picture where the container can."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_cut_keeps_the_picture_for_a_container_that_stores_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = _text(FFmWiz.build_audio_cut_command(_answers(tmp, cover=True)))
        self.assertIn("-map 0:v:0", text)
        self.assertIn("-c:v copy", text)
        self.assertIn("-disposition:v attached_pic", text)
        self.assertNotIn("-vn", text)

    def test_multi_range_cut_keeps_the_picture_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = _answers(tmp, cover=True, audio_keep_ranges=[(1.0, 4.0), (6.0, 9.0)])
            text = _text(FFmWiz.build_audio_cut_command(answers))
        self.assertIn("-map 0:v:0", text)
        self.assertIn("-disposition:v attached_pic", text)
        self.assertNotIn("-vn", text)

    def test_speed_reverse_and_transform_keep_the_picture(self):
        with tempfile.TemporaryDirectory() as tmp:
            speed = _answers(tmp, cover=True, speed_factor=1.5, reverse_audio=False)
            speed_text = _text(FFmWiz.build_audio_speed_reverse_command(speed))
            transform = _answers(tmp, cover=True, audio_cut_keep_ranges=[(0.0, 2.0)],
                                 audio_speed_enabled=True, audio_speed_factor=1.5)
            transform_text = _text(FFmWiz.build_audio_transform_command(transform))
        for text in (speed_text, transform_text):
            self.assertIn("-c:v copy", text)
            self.assertIn("-disposition:v attached_pic", text)
            self.assertNotIn("-vn", text)

    def test_containers_without_a_stream_form_still_drop_video(self):
        for ext in ("wav", "opus", "ogg"):
            with tempfile.TemporaryDirectory() as tmp:
                answers = _answers(tmp, cover=True,
                                   output_location=Path(tmp) / f"out.{ext}")
                text = _text(FFmWiz.build_audio_cut_command(answers))
            self.assertIn("-vn", text, ext)
            self.assertNotIn("-c:v copy", text, ext)

    def test_a_real_video_stream_is_not_treated_as_a_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = _answers(tmp, codec="aac", output_location=Path(tmp) / "out.m4a")
            answers["video_streams"] = [{"codec_type": "video", "codec_name": "h264",
                                         "disposition": {"attached_pic": 0}}]
            text = _text(FFmWiz.build_audio_cut_command(answers))
        self.assertIn("-vn", text)
        self.assertNotIn("-c:v copy", text)


@unittest.skipIf(not FFMPEG or not FFPROBE, "ffmpeg/ffprobe not on PATH")
class AudioToolCoverRoundTrip(unittest.TestCase):
    """The complaint was about the FILE: cut an MP3 that really carries an APIC."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_audio_cover_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, args):
        subprocess.run([str(part) for part in args], check=True, timeout=180,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_cutting_an_mp3_with_a_cover_keeps_the_cover(self):
        bare = self._tmp / "bare.mp3"
        cover = self._tmp / "cover.jpg"
        src = self._tmp / "src.mp3"
        self._run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                   "-i", "sine=frequency=440:duration=8", "-c:a", "libmp3lame",
                   "-b:a", "192k", bare])
        self._run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                   "-i", "color=c=red:s=120x120:d=1", "-frames:v", "1", cover])
        self._run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", bare,
                   "-i", cover, "-map", "0:a", "-map", "1:v", "-c", "copy",
                   "-id3v2_version", "3", "-disposition:v", "attached_pic", src])

        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(src)],
            capture_output=True, text=True, timeout=60).stdout)
        streams = probe["streams"]
        answers = {
            "ffmpeg": FFMPEG, "input_path": src, "output_location": self._tmp,
            "audio_index": 0,
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "format": probe["format"], "audio_keep_ranges": [(1.0, 5.0)],
            "audio_cut_stream_copy": True,
        }
        self._run(FFmWiz.build_audio_cut_command(answers))

        out = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-of", "json",
             str(answers["output_path"])],
            capture_output=True, text=True, timeout=60).stdout)["streams"]
        kinds = {stream["codec_type"]: stream for stream in out}
        self.assertIn("video", kinds, "the cover art must survive the cut")
        self.assertEqual(kinds["video"]["codec_name"], "mjpeg")
        self.assertEqual(kinds["video"]["disposition"]["attached_pic"], 1)
        self.assertEqual(kinds["audio"]["codec_name"], "mp3")
        self.assertAlmostEqual(float(kinds["audio"]["duration"]), 4.0, delta=0.2)


if __name__ == "__main__":
    unittest.main()
