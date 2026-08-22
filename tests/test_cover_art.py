"""Tests for cover art / thumbnail attachment across every container.

Each container stores a cover differently and fails differently when you get it
wrong, so every expectation here was checked by really muxing the file and
reading it back with ffprobe:

  * mov ACCEPTS the MP4 recipe, exits 0, logs the mapping -- and writes no
    picture at all. It must be refused, not attempted.
  * opus/ogg reject a mapped image stream outright; the cover has to be a base64
    METADATA_BLOCK_PICTURE tag.
  * mkv's unindexed `-metadata:s:t mimetype=...` retags EVERY attachment, so an
    existing subtitle font gets relabelled as a JPEG.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


class CoverArtPolicy(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_coverpolicy_"))
        self._cover = self._tmp / "cover.jpg"
        self._cover.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_supported_containers_have_a_method(self):
        for ext in ("mp4", "m4a", "m4v", "mkv", "mka", "mp3", "flac", "opus", "ogg"):
            self.assertIsNotNone(FFmWiz.cover_art_method(ext), f".{ext} should be supported")

    def test_mov_is_refused_because_it_silently_drops_the_picture(self):
        reason = FFmWiz.cover_art_rejection_reason("mov", self._cover)
        self.assertIsNotNone(reason)
        self.assertIn("mov", reason.lower())

    def test_containers_without_a_mechanism_are_refused(self):
        for ext in ("wav", "webm", "avi", "ts"):
            with self.subTest(container=ext):
                self.assertIsNotNone(FFmWiz.cover_art_rejection_reason(ext, self._cover))

    def test_only_jpeg_and_png_are_accepted(self):
        bad = self._tmp / "cover.bmp"
        bad.write_bytes(b"BM")
        self.assertIsNotNone(FFmWiz.cover_art_rejection_reason("mp4", bad))
        self.assertIsNone(FFmWiz.cover_art_rejection_reason("mp4", self._cover))

    def test_missing_cover_file_is_refused(self):
        self.assertIsNotNone(
            FFmWiz.cover_art_rejection_reason("mp4", self._tmp / "nope.jpg"))

    def test_attached_pic_index_follows_the_output_video_count(self):
        # Audio-only output -> the picture is video stream 0; a real video file
        # already has stream 0, so the picture is stream 1.
        audio_args = FFmWiz.cover_art_output_args("m4a", self._cover, mapped_video_streams=0)
        self.assertIn("-disposition:v:0", audio_args)
        video_args = FFmWiz.cover_art_output_args("mp4", self._cover, mapped_video_streams=1)
        self.assertIn("-disposition:v:1", video_args)

    def test_matroska_tags_the_new_attachment_by_index(self):
        # The unindexed form relabels an existing font as a JPEG.
        args = FFmWiz.cover_art_output_args("mkv", self._cover, existing_attachments=1)
        self.assertIn("-metadata:s:t:1", args)
        self.assertNotIn("-metadata:s:t", args)

    def test_matroska_always_supplies_a_mimetype(self):
        # Without it: "Attachment stream N has no mimetype tag" and no output.
        args = FFmWiz.cover_art_output_args("mkv", self._cover)
        self.assertTrue(any(str(a).startswith("mimetype=") for a in args))

    def test_mp3_pins_id3v2_version_3(self):
        args = FFmWiz.cover_art_output_args("mp3", self._cover)
        self.assertIn("-id3v2_version", args)
        self.assertEqual(args[args.index("-id3v2_version") + 1], "3")

    def test_front_cover_comment_is_always_set_for_tagged_formats(self):
        # Without the comment the APIC/FLAC picture type is "Other", not front cover.
        for ext in ("mp3", "flac"):
            with self.subTest(container=ext):
                args = FFmWiz.cover_art_output_args(ext, self._cover)
                self.assertTrue(any(FFmWiz.COVER_ART_COMMENT in str(a) for a in args))

    def test_opus_uses_a_tag_and_never_maps_a_stream(self):
        real_cover = self._tmp / "real.png"
        real_cover.write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + b"IHDR" + b"\x00\x00\x02X\x00\x00\x02X" + b"\x00" * 40)
        self.assertEqual(FFmWiz.cover_art_input_args("opus", real_cover), [])
        args = FFmWiz.cover_art_output_args("opus", real_cover)
        self.assertNotIn("-map", args)
        self.assertTrue(any(str(a).startswith("METADATA_BLOCK_PICTURE=") for a in args))

    def test_metadata_block_picture_reads_png_dimensions(self):
        import base64
        import struct
        png = self._tmp / "d.png"
        png.write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + b"IHDR" + struct.pack(">II", 600, 400) + b"\x00" * 40)
        block = base64.b64decode(FFmWiz.metadata_block_picture_value(png))
        mime_len = struct.unpack(">I", block[4:8])[0]
        offset = 8 + mime_len
        offset += 4 + struct.unpack(">I", block[offset:offset + 4])[0]  # description
        width, height = struct.unpack(">II", block[offset:offset + 8])
        self.assertEqual((width, height), (600, 400))


@unittest.skipIf(not FFMPEG or not FFPROBE, "ffmpeg/ffprobe not on PATH")
class CoverArtRealMux(unittest.TestCase):
    """The only assertion that matters: is the picture actually in the file?"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_coverreal_"))
        cls._cover = cls._tmp / "cover.jpg"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=magenta:s=600x600:d=1", "-frames:v", "1", str(cls._cover)],
                       check=True, timeout=120)
        for name, codec in (("movie.mp4", ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"]),
                            ("movie.mkv", ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"])):
            subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=2",
                            "-f", "lavfi", "-i", "sine=duration=2", *codec, str(cls._tmp / name)],
                           check=True, timeout=180)
        for name, codec in (("song.m4a", ["-c:a", "aac"]), ("song.mp3", ["-c:a", "libmp3lame"]),
                            ("song.flac", ["-c:a", "flac"]), ("song.opus", ["-c:a", "libopus"]),
                            ("song.ogg", ["-c:a", "libvorbis"])):
            subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "sine=duration=2", *codec, str(cls._tmp / name)],
                           check=True, timeout=180)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _attached_picture(self, path):
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60).stdout
        for stream in json.loads(out).get("streams", []):
            if stream.get("disposition", {}).get("attached_pic") == 1:
                return stream
        return None

    def test_cover_lands_in_every_supported_container(self):
        cases = [("movie.mp4", "out.mp4", 1), ("song.m4a", "out.m4a", 0),
                 ("movie.mkv", "out.mkv", 0), ("song.mp3", "out.mp3", 0),
                 ("song.flac", "out.flac", 0), ("song.opus", "out.opus", 0),
                 ("song.ogg", "out.ogg", 0)]
        for source, target, video_count in cases:
            with self.subTest(container=target):
                destination = self._tmp / target
                cmd = FFmWiz.build_cover_art_command(
                    FFMPEG, self._tmp / source, self._cover, destination,
                    mapped_video_streams=video_count)
                result = subprocess.run([str(p) for p in cmd], capture_output=True,
                                        text=True, timeout=300)
                self.assertEqual(result.returncode, 0,
                                 result.stderr.strip().splitlines()[-1] if result.stderr else "")
                self.assertIsNotNone(self._attached_picture(destination),
                                     f"{target} has no attached picture")

    def test_original_audio_is_still_stream_copied(self):
        destination = self._tmp / "copy_check.m4a"
        cmd = FFmWiz.build_cover_art_command(
            FFMPEG, self._tmp / "song.m4a", self._cover, destination)
        subprocess.run([str(p) for p in cmd], check=True, timeout=300,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=codec_name", "-of", "json", str(destination)],
            capture_output=True, text=True, timeout=60).stdout)
        self.assertEqual(out["streams"][0]["codec_name"], "aac")


if __name__ == "__main__":
    unittest.main()
