"""Regression: the audio-bitrate prompt must default to the SOURCE (USER-5-3).

The user's report was "cutting audio never matches the input, it is always 128".
Two separate places produced that:

  * the audio TOOLS re-encoded at DEFAULT_SPEED_AUDIO_BITRATE_KBPS regardless of
    the source -- fixed earlier by resolve_audio_tool_bitrate_kbps();
  * this one, the interactive prompt shared by the main wizard and the mode
    flows, which computed the source bitrate (including the highest across
    joined inputs) and then threw it away unless it happened to be BELOW 128:

        default_audio_bitrate = DEFAULT_AUDIO_BITRATE_KBPS
        if source and source < DEFAULT_AUDIO_BITRATE_KBPS:
            default_audio_bitrate = source

    so a 320 kbps track offered 128, and pressing Enter cost 60% of the bitrate.
    Worse, that explicit 128 then overrode the good tool-side resolver, which
    honours an explicit answer.

Driven through the real step function with only I/O stubbed, so the assertions
cover the actual prompt default rather than a reimplementation of it.
"""
import unittest

import FFmWiz

MAX = FFmWiz.AUDIO_TOOL_MAX_BITRATE_KBPS


def _stream(bitrate_kbps, codec="mp3", channels=2):
    stream = {"codec_type": "audio", "codec_name": codec,
              "channels": channels, "sample_rate": "44100"}
    if bitrate_kbps:
        stream["bit_rate"] = str(int(bitrate_kbps * 1000))
    return stream


class AudioBitrateDefault(unittest.TestCase):
    def setUp(self):
        self._ask = FFmWiz.appio.ask_raw
        self._packets = FFmWiz.services.get_packet_sizes
        self._colour = FFmWiz.appio.USE_COLOR
        FFmWiz.appio.USE_COLOR = False
        # Probing needs a real file; the bitrate comes from the stream metadata.
        FFmWiz.services.get_packet_sizes = lambda answers: {}
        self.prompts = []

    def tearDown(self):
        FFmWiz.appio.ask_raw = self._ask
        FFmWiz.services.get_packet_sizes = self._packets
        FFmWiz.appio.USE_COLOR = self._colour

    def _run(self, source_kbps, typed="", **extra):
        def fake_ask(prompt, *args, **kwargs):
            self.prompts.append(prompt)
            return typed

        FFmWiz.appio.ask_raw = fake_ask
        answers = {
            "audio_streams": [_stream(source_kbps)], "audio_tracks": [0],
            "format": {"duration": "180.0"}, "audio_codec": "aac",
            "output_ext": "mp4", "ffprobe": "ffprobe", "input_path": "song.mp3",
        }
        answers.update(extra)
        FFmWiz.step_audio_bitrate(answers)
        return answers

    def _shown_default(self):
        # question_prompt renders the default inside the trailing [brackets].
        prompt = self.prompts[-1]
        self.assertIn("[", prompt, prompt)
        return prompt.rsplit("[", 1)[1].split("]")[0]

    def test_a_320k_source_defaults_to_320_not_128(self):
        answers = self._run(320)
        self.assertEqual("320", self._shown_default())
        self.assertEqual(320, answers["audio_bitrate_kbps"])

    def test_pressing_enter_keeps_a_256k_source(self):
        self.assertEqual(256, self._run(256)["audio_bitrate_kbps"])

    def test_a_source_below_the_old_default_is_still_not_inflated(self):
        # The one thing the old code got right: never raise a 96 kbps track.
        answers = self._run(96)
        self.assertEqual("96", self._shown_default())
        self.assertEqual(96, answers["audio_bitrate_kbps"])

    def test_an_unknown_source_falls_back_to_the_constant(self):
        answers = self._run(None)
        self.assertEqual(str(FFmWiz.DEFAULT_AUDIO_BITRATE_KBPS),
                         self._shown_default())
        self.assertEqual(FFmWiz.DEFAULT_AUDIO_BITRATE_KBPS,
                         answers["audio_bitrate_kbps"])

    def test_a_lossless_source_is_clamped_to_the_ceiling(self):
        # CD PCM reports ~1411 kbps, which is not a meaningful target for a
        # lossy encoder.
        answers = self._run(1411)
        self.assertEqual(str(MAX), self._shown_default())
        self.assertEqual(MAX, answers["audio_bitrate_kbps"])

    def test_a_tiny_source_is_not_raised(self):
        # Clamping UP would inflate the file and, worse, make the prompt ask the
        # user to confirm exceeding the source -- its own default. Writing this
        # test is what caught that: the first version of the fix hung here.
        answers = self._run(8)
        self.assertEqual("8", self._shown_default())
        self.assertEqual(8, answers["audio_bitrate_kbps"])

    def test_an_explicit_answer_still_wins(self):
        self.assertEqual(192, self._run(320, typed="192")["audio_bitrate_kbps"])

    def test_n_keeps_the_source_and_flags_it(self):
        answers = self._run(320, typed="n")
        self.assertEqual(320, answers["audio_bitrate_kbps"])
        self.assertTrue(answers.get("audio_bitrate_keep"))

    def test_the_default_never_exceeds_the_source(self):
        # Defaulting to the source must not be able to inflate a file; the
        # shown default is always <= the detected source.
        for source in (64, 96, 128, 160, 192, 256, 320):
            with self.subTest(source=source):
                self.prompts.clear()
                self._run(source)
                self.assertLessEqual(int(self._shown_default()), source)


if __name__ == "__main__":
    unittest.main()
