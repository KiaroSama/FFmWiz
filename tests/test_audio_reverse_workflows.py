"""Regression: reversing the audio must not change anything else (D01-D04).

`execute_encode_plan` routed every audio-only reverse into
`run_bounded_audio_reverse`, which was written for the standalone AUDIO TOOLS.
Their source is a track plus an optional cover image, so the helper decides
"does this carry a picture" with the COVER-ART policy and clears
`video_streams` when the answer is no. Applied to an encode job that owns a
real moving picture, three different kinds of silent loss followed -- all three
returned exit code 0:

    D01  video + audio in, audio-only out
         ORIGINAL_COMMAND_CONTAINED_VIDEO true
         OUTPUT_STREAM_TYPES ['audio']          OUTPUT_DURATION 3.998

    D02  a two-input Join produced only the first input
         EXPECTED_DURATION 4.0   ACTUAL_DURATION 2.0
         ACTUAL_STREAMS ['audio']

    D03  a Split wrote one unsplit audio-only part and never made the second
         EXPECTED ['splitrev_Part01.mkv', 'splitrev_Part02.mkv']
         EXISTS   [true, false]                 PART01_DURATION 3.998

    D04  a two-track selection reversed track 0 and dropped the rest
         EXPECTED_AUDIO_STREAMS 2   ACTUAL_AUDIO_STREAMS 1

The repair gives the encode path its own staged plan: join forward when there
is a join, reverse the selected audio into a lossless scratch, mux it back onto
the source's own picture through the SAME stream policy the video reverse mux
uses, then rebuild the ORIGINAL job on that file with the reversal already
spent. Cuts, speed, subtitles, chapters and Split then run untouched.

Every test here drives the PUBLIC executor. Command text is not the contract:
these assert stream topology, duration, the colour of the picture and the
frequency of the audio.
"""
import json
import math
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts
from ffmwiz import encoding
from ffmwiz import runtime
from ffmwiz.support import ext04c

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

SECONDS = 4.0
# One tone per second, so the ORDER of the finished audio is readable.
TONES = (440, 880, 1320, 1760)


def _run(args, timeout=600):
    return subprocess.run([str(part) for part in args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _diagnosis(noise, command=None):
    """The lines worth reading out of a captured ffmpeg run.

    A progress bar redraws thousands of times, so a tail slice is spinner and
    nothing else. Keep the lines that carry a diagnosis, plus the command that
    produced them -- without that, a CI failure says only that something went
    wrong somewhere.
    """
    interesting = []
    for line in (noise or "").splitlines():
        text = line.strip()
        if not text or text.startswith(("100.0%", "  ")) or "•" in text:
            continue
        if any(mark in text.lower() for mark in (
                "error", "invalid", "failed", "cannot", "unable", "no such",
                "not permitted", "nothing was written", "conversion failed",
                "unsupported", "deprecated pixel", "@ 0x", "] ")):
            interesting.append(text)
    report = chr(10).join(interesting[-25:]) or (noise or "")[-400:]
    if command:
        report = "command: " + " ".join(str(part) for part in command) + chr(10) + report
    return report


class AudioReverseKeepsTheWorkflow(NoLeakedArtifacts, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not (FFMPEG and FFPROBE):
            raise unittest.SkipTest("ffmpeg/ffprobe not on PATH")
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_arevwf_"))
        # A 4 s source whose picture changes colour every second and whose audio
        # changes tone every second: both timelines are readable at a glance.
        colours = ("red", "green", "blue", "white")
        video = "".join(
            f"color=c={c}:s=160x120:r=30:d=1[v{i}];" for i, c in enumerate(colours))
        video += "".join(f"[v{i}]" for i in range(len(colours)))
        video += f"concat=n={len(colours)}:v=1:a=0[v];"
        audio = "".join(
            f"sine=frequency={t}:duration=1[a{i}];" for i, t in enumerate(TONES))
        audio += "".join(f"[a{i}]" for i in range(len(TONES)))
        audio += f"concat=n={len(TONES)}:v=0:a=1[a]"
        cls.source = cls._root / "movie.mkv"
        result = _run([
            FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-filter_complex", video + audio, "-map", "[v]", "-map", "[a]",
            # `-color_range tv` is not decoration. Without it, whether the file
            # carries a range tag depends on the build: FFmpeg 9.0.1 writes one,
            # 6.1.1 does not. An untagged source makes the folder-encode case
            # below demand a BATCH colour-range policy -- correctly, since the
            # builder refuses to guess a range -- so the test would be asserting
            # about colour-range policy on old builds and about video
            # preservation on new ones. Tagging the fixture keeps it about the
            # one thing its name claims.
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-color_range", "tv",
            "-c:a", "flac", cls.source])
        if result.returncode != 0:
            raise unittest.SkipTest(f"could not build the source: {result.stderr[-400:]}")

        # Two short single-colour, single-tone inputs for the Join case.
        cls.join_inputs = []
        for name, colour, tone in (("first", "red", 440), ("second", "blue", 880)):
            path = cls._root / f"{name}.mkv"
            _run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                  "-f", "lavfi", "-i", f"color=c={colour}:s=160x120:r=30:d=2",
                  "-f", "lavfi", "-i", f"sine=frequency={tone}:duration=2",
                  "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                  "-c:a", "flac", path])
            cls.join_inputs.append(path)

        # Two audio tracks with distinct tones and distinct metadata.
        cls.two_track = cls._root / "twotrack.mkv"
        _run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
              "-f", "lavfi", "-i", f"color=c=red:s=160x120:r=30:d={SECONDS}",
              "-f", "lavfi", "-i", f"sine=frequency=440:duration={SECONDS}",
              "-f", "lavfi", "-i", f"sine=frequency=1760:duration={SECONDS}",
              "-map", "0:v", "-map", "1:a", "-map", "2:a",
              "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
              "-c:a", "flac",
              "-metadata:s:a:0", "language=eng", "-metadata:s:a:0", "title=English",
              "-metadata:s:a:1", "language=jpn", "-metadata:s:a:1", "title=Japanese",
              cls.two_track])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="arevwf_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", self._real_note))

    # ---- measuring the produced media ------------------------------------
    def _probe(self, path, *args):
        return json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                *args, path]).stdout or "{}")

    def _topology(self, path):
        return [s["codec_type"] for s in
                self._probe(path, "-show_streams").get("streams", [])]

    def _duration(self, path):
        return float(self._probe(path, "-show_format")["format"]["duration"])

    def _frame_count(self, path):
        return len(self._probe(path, "-select_streams", "v", "-show_packets")["packets"])

    def _colour_at(self, path, at):
        raw = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
             "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
             "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300).stdout
        if len(raw) < 3:
            return "missing"
        red, green, blue = raw[0], raw[1], raw[2]
        if red > 200 and green > 200 and blue > 200:
            return "white"
        brightest = max(red, green, blue)
        if brightest < 60:
            return "dark"
        return {red: "red", green: "green", blue: "blue"}[brightest]

    def _tone_at(self, path, at, stream=0):
        """The dominant tone near `at`, by Goertzel over decoded PCM."""
        raw = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
             "-ss", f"{at:.3f}", "-i", str(path), "-t", "0.30",
             "-map", f"0:a:{stream}", "-f", "s16le", "-acodec", "pcm_s16le",
             "-ar", "8000", "-ac", "1", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300).stdout
        samples = [int.from_bytes(raw[i:i + 2], "little", signed=True)
                   for i in range(0, len(raw) - 1, 2)]
        if len(samples) < 256:
            return None
        best, best_power = None, 0.0
        for tone in TONES:
            omega = 2 * math.pi * tone / 8000
            coeff = 2 * math.cos(omega)
            s1 = s2 = 0.0
            for sample in samples:
                s0 = sample + coeff * s1 - s2
                s2, s1 = s1, s0
            power = s1 * s1 + s2 * s2 - coeff * s1 * s2
            if power > best_power:
                best, best_power = tone, power
        return best

    # ---- driving the public executor --------------------------------------
    def _answers(self, out, source, **extra):
        info = self._probe(source, "-show_format", "-show_streams")
        streams = info["streams"]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": source,
            "probe": info, "format": info["format"], "streams": streams,
            "output_location": out, "output_ext": "mkv",
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [], "attachment_streams": [], "data_streams": [],
            "audio_tracks": [0], "color_range_choice": "tv",
            "video_encoder": "libx264", "crf": 28, "preset": "ultrafast",
            "audio_codec": "flac",
            "audio_speed_enabled": True, "audio_speed_factor": 1.0,
            "reverse_audio": True,
        })
        answers.update(extra)
        FFmWiz.artifact_lease(answers)
        return answers

    def _execute(self, answers, seconds=SECONDS, force_staging=True):
        """Through `execute_encode_plan`, with the budget pinned small.

        A four-second fixture fits any real budget in one pass, so the staged
        path would never run. One second per chunk makes it stage.
        """
        real = ext04c.audio_reverse_segment_seconds
        if force_staging:
            ext04c.audio_reverse_segment_seconds = lambda _a, _i=None: 1.0
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                answers["cmd"] = [str(part) for part
                                  in FFmWiz.build_ffmpeg_command(answers)]
                promised = [Path(p).name for p in
                            (answers.get("split_output_paths") or [])]
                code, _elapsed = encoding.execute_encode_plan(
                    answers, answers["cmd"], total_duration=seconds, label="test")
        finally:
            ext04c.audio_reverse_segment_seconds = real
        self.assertEqual(0, code, _diagnosis(noise.getvalue(), answers.get("cmd")))
        return Path(answers["output_path"]), promised

    # ---- D01 --------------------------------------------------------------
    def test_the_fixture_really_has_a_picture_and_a_readable_timeline(self):
        # Guard the guard: an audio-only or single-colour source would let every
        # assertion below pass without proving anything.
        self.assertEqual(["video", "audio"], self._topology(self.source))
        self.assertEqual(["red", "green", "blue", "white"],
                         [self._colour_at(self.source, at)
                          for at in (0.5, 1.5, 2.5, 3.5)])
        self.assertEqual(list(TONES), [self._tone_at(self.source, at)
                                       for at in (0.35, 1.35, 2.35, 3.35)])

    def test_a_video_and_audio_job_keeps_its_video(self):
        out = self._tmp / "d01"
        out.mkdir()
        produced, _promised = self._execute(self._answers(out, self.source))
        self.assertEqual(["video", "audio"], self._topology(produced))
        self.assertAlmostEqual(SECONDS, self._duration(produced), delta=0.05)

    def test_the_picture_is_untouched_while_the_audio_reverses(self):
        out = self._tmp / "d01content"
        out.mkdir()
        produced, _promised = self._execute(self._answers(out, self.source))
        self.assertEqual(["red", "green", "blue", "white"],
                         [self._colour_at(produced, at)
                          for at in (0.5, 1.5, 2.5, 3.5)],
                         "the picture must stay in its own order")
        self.assertEqual(list(reversed(TONES)),
                         [self._tone_at(produced, at)
                          for at in (0.35, 1.35, 2.35, 3.35)],
                         "the audio must be reversed")

    def test_the_frame_count_matches_a_run_without_the_reversal(self):
        out = self._tmp / "d01frames"
        out.mkdir()
        produced, _promised = self._execute(self._answers(out, self.source))
        reference_dir = self._tmp / "d01reference"
        reference_dir.mkdir()
        reference = self._answers(reference_dir, self.source, reverse_audio=False)
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(reference)]
            code, _elapsed = encoding.execute_encode_plan(
                reference, cmd, total_duration=SECONDS, label="reference")
        self.assertEqual(0, code, noise.getvalue()[-1200:])
        self.assertEqual(self._frame_count(Path(reference["output_path"])),
                         self._frame_count(produced))

    def test_a_folder_encode_item_keeps_its_video_too(self):
        # The same plan through a DIFFERENT public dispatcher: Folder Encode
        # prepares its own per-item answers and used to reach the same helper.
        from ffmwiz import modes
        out = self._tmp / "d01folder"
        out.mkdir()
        answers = self._answers(out, self.source, folder_output_location=out)
        # `prepare_folder_job_answers` copies the item's own media facts out of
        # `item["answers"]`, which is how the folder scan hands them over.
        item = {"path": self.source, "answers": dict(answers)}
        real = ext04c.audio_reverse_segment_seconds
        ext04c.audio_reverse_segment_seconds = lambda _a, _i=None: 1.0
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                job = modes.prepare_folder_job_answers(answers, item)
                job["cmd"] = [str(part) for part in FFmWiz.build_ffmpeg_command(job)]
                code, _elapsed = encoding.execute_encode_plan(
                    job, job["cmd"], total_duration=SECONDS, label="folder")
        finally:
            ext04c.audio_reverse_segment_seconds = real
        self.assertEqual(0, code, _diagnosis(noise.getvalue()))
        self.assertEqual(["video", "audio"],
                         self._topology(Path(job["output_path"])))

    # ---- D02 --------------------------------------------------------------
    def _join_item(self, path):
        info = self._probe(path, "-show_format", "-show_streams")
        streams = info["streams"]
        return {"path": path, "probe": info, "format": info["format"],
                "streams": streams,
                "video_streams": [s for s in streams if s["codec_type"] == "video"],
                "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
                "subtitle_streams": [], "data_streams": [],
                "duration": float(info["format"]["duration"])}

    def test_a_join_reverses_the_whole_joined_timeline(self):
        out = self._tmp / "d02"
        out.mkdir()
        items = [self._join_item(path) for path in self.join_inputs]
        answers = self._answers(out, self.join_inputs[0],
                                join_input_items=items[1:])
        produced, _promised = self._execute(answers)
        self.assertEqual(["video", "audio"], self._topology(produced))
        self.assertAlmostEqual(4.0, self._duration(produced), delta=0.1,
                               msg="the later Join input was dropped")
        self.assertEqual(["red", "blue"],
                         [self._colour_at(produced, at) for at in (0.5, 2.5)],
                         "the joined picture must stay in forward order")
        self.assertEqual([880, 440],
                         [self._tone_at(produced, at) for at in (0.35, 2.35)],
                         "the whole joined audio must be reversed, not input 1")

    # ---- D03 --------------------------------------------------------------
    def test_a_split_still_produces_every_part(self):
        out = self._tmp / "d03"
        out.mkdir()
        answers = self._answers(out, self.source, separator_points=[2.0])
        _produced, promised = self._execute(answers)
        parts = sorted(out.glob("*Part*.mkv"))
        self.assertEqual(2, len(parts), [p.name for p in parts])
        self.assertEqual(promised, [p.name for p in parts],
                         "the parts must carry the names the summary showed")
        for part in parts:
            self.assertEqual(["video", "audio"], self._topology(part), part.name)
            self.assertAlmostEqual(2.0, self._duration(part), delta=0.1, msg=part.name)

    def test_each_split_part_holds_its_slice_of_the_reversed_audio(self):
        out = self._tmp / "d03content"
        out.mkdir()
        answers = self._answers(out, self.source, separator_points=[2.0])
        self._execute(answers)
        first, second = sorted(out.glob("*Part*.mkv"))
        # Fully reversed the tones run 1760, 1320, 880, 440; part 1 takes the
        # first two seconds of that and part 2 the last two.
        self.assertEqual([1760, 1320],
                         [self._tone_at(first, at) for at in (0.35, 1.35)])
        self.assertEqual([880, 440],
                         [self._tone_at(second, at) for at in (0.35, 1.35)])
        self.assertEqual(["red", "green"],
                         [self._colour_at(first, at) for at in (0.5, 1.5)])
        self.assertEqual(["blue", "white"],
                         [self._colour_at(second, at) for at in (0.5, 1.5)])

    # ---- D04 --------------------------------------------------------------
    def test_every_selected_audio_track_survives_and_is_reversed(self):
        out = self._tmp / "d04"
        out.mkdir()
        answers = self._answers(out, self.two_track, audio_tracks=[0, 1])
        produced, _promised = self._execute(answers)
        self.assertEqual(["video", "audio", "audio"], self._topology(produced))
        # Each track carries ONE tone for its whole length, so reversing it
        # cannot be observed by order -- what matters is that both survived and
        # kept their own content.
        self.assertEqual(440, self._tone_at(produced, 1.0, stream=0))
        self.assertEqual(1760, self._tone_at(produced, 1.0, stream=1))

    def test_the_selected_tracks_keep_their_metadata(self):
        out = self._tmp / "d04meta"
        out.mkdir()
        answers = self._answers(out, self.two_track, audio_tracks=[0, 1])
        produced, _promised = self._execute(answers)
        described = [
            ({k.lower(): v for k, v in (s.get("tags") or {}).items()}.get("language"),
             {k.lower(): v for k, v in (s.get("tags") or {}).items()}.get("title"))
            for s in self._probe(produced, "-show_streams")["streams"]
            if s["codec_type"] == "audio"]
        self.assertEqual([("eng", "English"), ("jpn", "Japanese")], described)

    # ---- Plan 011 ----------------------------------------------------------
    # The final rebuild used to inherit every key of `answers` unfiltered, so a
    # crop/fps/resize the forward join had already applied to
    # `joined_forward.mkv` was applied a SECOND time to the finished file.
    # Stage 1 (the join) has always owned the geometry correctly; these cover
    # stage 4 (the final rebuild), which now must own it only when no join
    # stage ran to spend it first.
    def _video(self, path):
        return self._probe(path, "-select_streams", "v:0", "-show_streams")["streams"][0]

    def _execute_capturing(self, answers, seconds=SECONDS):
        """Like `_execute`, but also returns every command the pipeline issued."""
        real_segment_seconds = ext04c.audio_reverse_segment_seconds
        ext04c.audio_reverse_segment_seconds = lambda _a, _i=None: 1.0
        commands = []
        real_runner = runtime.run_ffmpeg_with_progress

        def spy(cmd, **kwargs):
            commands.append([str(part) for part in cmd])
            return real_runner(cmd, **kwargs)

        runtime.run_ffmpeg_with_progress = spy
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                answers["cmd"] = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
                code, _elapsed = encoding.execute_encode_plan(
                    answers, answers["cmd"], total_duration=seconds, label="test")
        finally:
            runtime.run_ffmpeg_with_progress = real_runner
            ext04c.audio_reverse_segment_seconds = real_segment_seconds
        self.assertEqual(0, code, _diagnosis(noise.getvalue()))
        return Path(answers["output_path"]), commands

    def _stages_with(self, commands, needle):
        """Which produced files were made by a command containing `needle`."""
        return [cmd[-1] for cmd in commands if any(needle in str(part) for part in cmd)]

    def test_a_joined_audio_reverse_crops_once(self):
        out = self._tmp / "geomcrop"
        out.mkdir()
        items = [self._join_item(path) for path in self.join_inputs]
        answers = self._answers(out, self.join_inputs[0], join_input_items=items[1:],
                                crop_enabled=True, crop_left=10, crop_right=10,
                                crop_top=0, crop_bottom=0)
        _produced, commands = self._execute_capturing(answers)
        cropped = self._stages_with(commands, "crop=")
        self.assertEqual(1, len(cropped),
                         f"crop appears in {len(cropped)} command(s): {cropped}")

    def test_the_finished_file_has_the_asked_for_size(self):
        # The strongest evidence: an argv assertion is satisfied by a fix that
        # only MOVES the duplicate crop rather than removing it, but a crop
        # applied twice to a 160x120 source comes out 120x120, not 140x120.
        out = self._tmp / "geomsize"
        out.mkdir()
        items = [self._join_item(path) for path in self.join_inputs]
        answers = self._answers(out, self.join_inputs[0], join_input_items=items[1:],
                                crop_enabled=True, crop_left=10, crop_right=10,
                                crop_top=0, crop_bottom=0)
        produced, _commands = self._execute_capturing(answers)
        stream = self._video(produced)
        self.assertEqual((140, 120), (int(stream["width"]), int(stream["height"])),
                         "a crop applied twice would come out 120x120, not 140x120")

    def test_the_frame_rate_is_not_applied_twice(self):
        # fps down-conversion is idempotent on a second pass (15fps -> 15fps
        # changes nothing observable), so the command text is the only signal
        # that would catch a repeat application here.
        out = self._tmp / "geomfps"
        out.mkdir()
        items = [self._join_item(path) for path in self.join_inputs]
        answers = self._answers(out, self.join_inputs[0], join_input_items=items[1:], fps=15)
        _produced, commands = self._execute_capturing(answers)
        rated = self._stages_with(commands, "fps=15")
        self.assertEqual(1, len(rated),
                         f"fps=15 appears in {len(rated)} command(s): {rated}")

    def test_a_job_with_no_geometry_is_unchanged(self):
        # Guard the guard: without a requested crop, nothing should crop, and
        # the picture size must survive untouched. (The join always
        # normalises its inputs through its own `scale=`/`fps=`, requested or
        # not -- concat needs matching inputs -- so those two are not part of
        # this assertion; only an unrequested CROP would be this bug.)
        out = self._tmp / "geomnone"
        out.mkdir()
        items = [self._join_item(path) for path in self.join_inputs]
        answers = self._answers(out, self.join_inputs[0], join_input_items=items[1:])
        produced, commands = self._execute_capturing(answers)
        stream = self._video(produced)
        self.assertEqual((160, 120), (int(stream["width"]), int(stream["height"])))
        self.assertFalse(self._stages_with(commands, "crop="))

    def test_an_unjoined_audio_reverse_still_crops(self):
        # The no-join case: no forward stage ever runs to spend the geometry,
        # so the final rebuild must own it -- dropping it here would be a
        # worse bug than applying it twice.
        out = self._tmp / "geomsolo"
        out.mkdir()
        answers = self._answers(out, self.source, crop_enabled=True, crop_left=10,
                                crop_right=10, crop_top=0, crop_bottom=0)
        produced, commands = self._execute_capturing(answers)
        stream = self._video(produced)
        self.assertEqual((140, 120), (int(stream["width"]), int(stream["height"])),
                         "a job with no join stage must still get its crop somewhere")
        cropped = self._stages_with(commands, "crop=")
        self.assertEqual(1, len(cropped),
                         f"crop appears in {len(cropped)} command(s): {cropped}")
        self.assertEqual(list(reversed(TONES)),
                         [self._tone_at(produced, at) for at in (0.35, 1.35, 2.35, 3.35)],
                         "the crop fix must not have broken the reversal itself")


if __name__ == "__main__":
    unittest.main()
