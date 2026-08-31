"""A folder item must carry the same media facts a single file does.

`FOLDER_MEDIA_METADATA_KEYS` is doing two jobs at once: it is the filter
`scan_folder_media_files` stores each probed item through, AND the key set
`copy_media_metadata` swaps in (and, for a key the source lacks, CLEARS) when a
per-file job is prepared. So a key missing from it is missing twice over -- the
fact never reaches the item, and whatever the settings answers already held is
never cleared out of the per-file job.

`data_streams` was missing while its five sibling stream lists were all listed.
Same file, same answers: the single-file path emitted `-map 0:d:0` and the
folder path emitted nothing, so `keep_source_data_streams` was honoured for one
file and silently ignored for a folder of them.

The first test reads `load_input_metadata` itself rather than repeating a list,
so a sixth stream list added there is covered the day it is added.
"""
from __future__ import annotations

import ast
import contextlib
import inspect
import io
import unittest

import FFmWiz
from ffmwiz.support import ext04

DATA_STREAM = {"index": 3, "codec_type": "data", "codec_name": "bin_data"}


def _keys_load_input_metadata_writes() -> set[str]:
    """The `answers["..."] = ...` keys `load_input_metadata` assigns."""
    tree = ast.parse(inspect.getsource(ext04.load_input_metadata))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "answers"
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)):
                keys.add(target.slice.value)
    return keys


class TheKeyListCoversWhatOneProbeWrites(unittest.TestCase):

    def test_every_media_fact_the_probe_writes_is_carried(self):
        written = _keys_load_input_metadata_writes()
        self.assertIn("data_streams", written, "the probe stopped writing this")
        missing = written - set(FFmWiz.FOLDER_MEDIA_METADATA_KEYS)
        self.assertEqual(
            set(), missing,
            "load_input_metadata writes these, but a folder item cannot carry "
            f"them: {sorted(missing)}")


class TheFolderPathMapsWhatTheSingleFilePathMaps(unittest.TestCase):

    def _probe_answers(self, **extra):
        answers = {
            "input_path": "a.mkv", "probe": {}, "format": {},
            "video_streams": [{"codec_type": "video", "codec_name": "h264"}],
            "audio_streams": [], "subtitle_streams": [],
            "attachment_streams": [], "data_streams": [DATA_STREAM],
        }
        answers.update(extra)
        return answers

    def _stored_item_answers(self, probe_answers):
        # Exactly what scan_folder_media_files stores per item.
        return {key: probe_answers.get(key)
                for key in FFmWiz.FOLDER_MEDIA_METADATA_KEYS
                if key in probe_answers}

    def _data_maps(self, answers):
        cmd: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            FFmWiz.append_source_data_maps(cmd, answers)
        return cmd

    def test_a_folder_item_still_maps_its_data_streams(self):
        probe_answers = self._probe_answers()
        settings = {"keep_source_data_streams": True}
        job = dict(settings)
        FFmWiz.copy_media_metadata(job, self._stored_item_answers(probe_answers))
        self.assertEqual(self._data_maps(dict(settings, **probe_answers)),
                         self._data_maps(job),
                         "the folder path dropped a data stream the single-file "
                         "path maps")
        self.assertEqual(["-map", "0:d:0"], self._data_maps(job))

    def test_a_previous_files_data_streams_are_cleared(self):
        # The other half: a key absent from the list is never popped either, so
        # a stale value leaks into every later per-file job.
        item = self._stored_item_answers(self._probe_answers(data_streams=[]))
        job = {"keep_source_data_streams": True, "data_streams": [DATA_STREAM]}
        FFmWiz.copy_media_metadata(job, item)
        self.assertEqual([], job.get("data_streams") or [])
        self.assertEqual([], self._data_maps(job))


if __name__ == "__main__":
    unittest.main()
