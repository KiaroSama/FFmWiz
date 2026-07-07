"""Tests for the Stream Cleanup Remux subsystem (ffmwiz.muxcleanup).

Covers stream-selection/remux-decision logic, output path resolution, the
audio/subtitle skip logic, and a real end-to-end ffmpeg remux through
``processing.process_files`` (skipped automatically when ffmpeg/ffprobe are
not on PATH).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable so ``import ffmwiz...`` works regardless of
# the current working directory of the test runner.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ffmwiz.muxcleanup.constants import (
    AUDIO_ALL,
    AUDIO_BY_INDEX,
    AUDIO_BY_LANGUAGE,
    AUDIO_NONE,
    SUBTITLE_ALL,
    SUBTITLE_NONE,
)
from ffmwiz.muxcleanup.models import MediaFile, SelectionRules, StreamInfo
from ffmwiz.muxcleanup.muxlogic import (
    build_ffmpeg_command,
    remux_needed_reasons,
    selected_audio_streams,
    selected_subtitle_streams,
)
from ffmwiz.muxcleanup.output import (
    make_output_path,
    resolve_output_root,
    sanitize_filename_part,
    selection_suffix,
    unique_path,
)
from ffmwiz.muxcleanup.reporting import max_stream_count_for, stream_languages_for
from ffmwiz.muxcleanup import media as mux_media
from ffmwiz.muxcleanup import output as mux_output
from ffmwiz.muxcleanup import processing as mux_processing

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _rules(**overrides) -> SelectionRules:
    base = dict(
        audio_mode=AUDIO_ALL,
        audio_languages=[],
        audio_titles=[],
        audio_indexes=[],
        subtitle_mode=SUBTITLE_ALL,
        subtitle_languages=[],
        subtitle_titles=[],
        subtitle_indexes=[],
        keep_attachments=True,
        keep_metadata=True,
        keep_chapters=True,
        overwrite=False,
        copy_non_video_files=True,
    )
    base.update(overrides)
    return SelectionRules(**base)


def _media() -> MediaFile:
    return MediaFile(path=Path("in.mkv"), streams=[
        StreamInfo(index=0, codec_type="video"),
        StreamInfo(index=1, codec_type="audio", language="jpn", disposition_default=1),
        StreamInfo(index=2, codec_type="audio", language="eng"),
        StreamInfo(index=3, codec_type="subtitle", language="eng", disposition_default=1),
    ])


class MuxLogicTests(unittest.TestCase):
    def test_selected_audio_by_language(self):
        rules = _rules(audio_mode=AUDIO_BY_LANGUAGE, audio_languages=["jpn"])
        self.assertEqual([s.index for s in selected_audio_streams(_media(), rules)], [1])

    def test_selected_audio_by_index(self):
        rules = _rules(audio_mode=AUDIO_BY_INDEX, audio_indexes=[2])
        self.assertEqual([s.index for s in selected_audio_streams(_media(), rules)], [2])

    def test_selected_audio_none(self):
        self.assertEqual(selected_audio_streams(_media(), _rules(audio_mode=AUDIO_NONE)), [])

    def test_selected_subtitle_none(self):
        self.assertEqual(selected_subtitle_streams(_media(), _rules(subtitle_mode=SUBTITLE_NONE)), [])

    def test_remux_not_needed_when_keeping_everything(self):
        media = _media()
        rules = _rules()
        reasons = remux_needed_reasons(
            media, rules,
            selected_audio_streams(media, rules),
            selected_subtitle_streams(media, rules),
        )
        self.assertEqual(reasons, [])

    def test_remux_needed_when_dropping_stream(self):
        media = _media()
        rules = _rules(audio_mode=AUDIO_BY_LANGUAGE, audio_languages=["jpn"])
        reasons = remux_needed_reasons(
            media, rules,
            selected_audio_streams(media, rules),
            selected_subtitle_streams(media, rules),
        )
        self.assertIn("audio stream selection changes", reasons)

    def test_build_ffmpeg_command_maps_selected_streams(self):
        media = _media()
        rules = _rules(audio_mode=AUDIO_BY_LANGUAGE, audio_languages=["jpn"], overwrite=True)
        cmd, audio_keep, subs_keep = build_ffmpeg_command(
            Path("in.mkv"), Path("out.mkv"), media, rules
        )
        self.assertEqual([s.index for s in audio_keep], [1])
        self.assertIn("-map", cmd)
        self.assertIn("0:1", cmd)
        self.assertNotIn("0:2", cmd)
        self.assertIn("-y", cmd)
        self.assertIn("copy", cmd)


class OutputPathTests(unittest.TestCase):
    def test_sanitize_replaces_invalid_chars(self):
        self.assertEqual(sanitize_filename_part('a<b>c:d"e'), "a-b-c-d-e")

    def test_sanitize_falls_back_when_no_alnum(self):
        self.assertEqual(sanitize_filename_part("***", fallback="X"), "X")

    def test_selection_suffix_language_mode(self):
        rules = _rules(audio_mode=AUDIO_BY_LANGUAGE, audio_languages=["jpn"])
        self.assertEqual(selection_suffix(rules), "[JA Audio + All Subs]")

    def test_selection_suffix_no_subs(self):
        self.assertEqual(selection_suffix(_rules(subtitle_mode=SUBTITLE_NONE)), "[All Audio + No Subs]")

    def test_resolve_output_root_folder_creates_named_subfolder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_root = root / "Series"
            input_root.mkdir()
            output_base = root / "Out"
            result = resolve_output_root(input_root, output_base, _rules())
            self.assertEqual(result.parent, output_base)
            self.assertIn("Series", result.name)
            self.assertIn("All Audio", result.name)

    def test_resolve_output_root_single_file_is_output_base(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_file = root / "episode.mkv"
            input_file.write_bytes(b"0")
            output_base = root / "Out"
            self.assertEqual(resolve_output_root(input_file, output_base, _rules()), output_base)

    def test_make_output_path_avoids_self_collision(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_file = root / "clip.mkv"
            input_file.write_bytes(b"0")
            result = make_output_path(input_file, root, input_file, _rules())
            self.assertNotEqual(result, input_file)

    def test_unique_path_adds_numeric_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            existing = Path(td) / "out.mkv"
            existing.write_bytes(b"0")
            self.assertEqual(unique_path(existing).name, "out (2).mkv")

    def test_unique_path_returns_same_if_free(self):
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td) / "free.mkv"
            self.assertEqual(unique_path(candidate), candidate)


class SelectionSkipLogicTests(unittest.TestCase):
    @staticmethod
    def _audio(index, language, title=""):
        return StreamInfo(index=index, codec_type="audio", language=language, title=title)

    @staticmethod
    def _video(index=0):
        return StreamInfo(index=index, codec_type="video")

    def test_multi_track_single_language_not_skippable(self):
        media = MediaFile(path=Path("a.mkv"), streams=[
            self._video(0), self._audio(1, "jpn"), self._audio(2, "jpn", title="Commentary"),
        ])
        self.assertEqual(max_stream_count_for([media], "audio"), 2)
        self.assertEqual(stream_languages_for([media], "audio"), ["jpn"])

    def test_single_track_is_skippable(self):
        media = MediaFile(path=Path("b.mkv"), streams=[self._video(0), self._audio(1, "jpn")])
        self.assertLessEqual(max_stream_count_for([media], "audio"), 1)

    def test_mixed_files_one_multi_track_not_skippable(self):
        single = MediaFile(path=Path("b.mkv"), streams=[self._video(0), self._audio(1, "jpn")])
        multi = MediaFile(path=Path("a.mkv"), streams=[
            self._video(0), self._audio(1, "jpn"), self._audio(2, "jpn", title="Commentary"),
        ])
        self.assertEqual(max_stream_count_for([single, multi], "audio"), 2)

    def test_no_audio_streams_is_skippable(self):
        media = MediaFile(path=Path("c.mkv"), streams=[self._video(0)])
        self.assertEqual(max_stream_count_for([media], "audio"), 0)


@unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg/ffprobe not found in PATH")
class ProcessingEndToEndTests(unittest.TestCase):
    """Real ffmpeg-backed remux through processing.process_files."""

    def _make_mkv(self, dst: Path, srt_path: Path) -> None:
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=128x72:rate=5:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
            "-i", str(srt_path),
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
            "-c:s", "srt",
            "-metadata:s:a:0", "language=jpn",
            "-metadata:s:a:1", "language=eng",
            "-metadata:s:s:0", "language=eng",
            "-shortest", str(dst),
        ]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])

    def test_remux_keeps_only_selected_audio_language_and_subs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            indir = root / "Series"
            indir.mkdir()
            srt = root / "sub.srt"
            srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
            self._make_mkv(indir / "E01.mkv", srt)
            self._make_mkv(indir / "E02.mkv", srt)
            (indir / "readme.txt").write_text("not a video", encoding="utf-8")

            media_files = mux_media.scan_files(sorted(indir.glob("*.mkv")))
            self.assertEqual(len(media_files), 2)

            rules = _rules(audio_mode=AUDIO_BY_LANGUAGE, audio_languages=["jpn"], overwrite=True)
            out_root = mux_output.resolve_output_root(indir, root / "OutA", rules)
            mux_processing.process_files(media_files, indir, out_root, rules)

            outputs = sorted(out_root.rglob("*.mkv"))
            self.assertEqual(len(outputs), 2)
            for f in outputs:
                probed = mux_media.probe_file(f)
                self.assertEqual([s.language for s in probed.audio_streams], ["jpn"])
                self.assertEqual(len(probed.subtitle_streams), 1)
            self.assertTrue(list(out_root.rglob("readme.txt")), "non-video file was not copied")
            mux_processing.verify_output(out_root, rules)


if __name__ == "__main__":
    unittest.main()
