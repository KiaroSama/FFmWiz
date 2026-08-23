"""Regression: a command "ready to run manually" must still be runnable (R07).

Declining execution printed the command and then immediately deleted the inputs
it references. Confirmed on a copy-Join: the command named a generated
`.ffconcat` file, the file existed, `cleanup_join_concat_list()` removed it, and
the printed command was dead by the time the user could read it.

The contract chosen here is "preserve generated inputs until the user discards
them": ownership is handed back so no later cleanup can delete them either, and
the user is told exactly which files were kept, because they are now the only
one who can remove them.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

import FFmWiz


class PreserveHandsOwnershipBack(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_manual_"))
        self._notes = []
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = self._notes.append

    def tearDown(self):
        FFmWiz.appio.note = self._real_note
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _leased_file(self, answers, name):
        path = self._tmp / name
        path.write_text("generated", encoding="utf-8")
        return FFmWiz.artifact_lease(answers).register(path)

    def test_a_leased_input_survives_preserve(self):
        answers = {}
        kept = self._leased_file(answers, "joined.srt")
        FFmWiz.preserve_artifacts_for_manual_run(answers)
        self.assertTrue(kept.exists())

    def test_a_later_cleanup_cannot_delete_it_either(self):
        # Ownership is handed BACK, not merely skipped once -- the executor's
        # finally block runs on other paths too.
        answers = {}
        kept = self._leased_file(answers, "joined.srt")
        FFmWiz.preserve_artifacts_for_manual_run(answers)
        FFmWiz.cleanup_join_concat_list(answers)
        FFmWiz.release_artifacts(answers)
        self.assertTrue(kept.exists())

    def test_the_concat_list_is_preserved_too(self):
        concat = self._tmp / "list.ffconcat"
        concat.write_text("ffconcat version 1.0\n", encoding="utf-8")
        answers = {"_join_concat_list": str(concat)}
        FFmWiz.preserve_artifacts_for_manual_run(answers)
        FFmWiz.cleanup_join_concat_list(answers)
        self.assertTrue(concat.exists())

    def test_the_user_is_told_which_files_were_kept(self):
        answers = {}
        kept = self._leased_file(answers, "joined.srt")
        FFmWiz.preserve_artifacts_for_manual_run(answers)
        joined = " ".join(self._notes)
        self.assertIn(str(kept), joined)
        self.assertIn("KEPT", joined)

    def test_it_returns_only_paths_that_exist(self):
        answers = {}
        FFmWiz.artifact_lease(answers).register(self._tmp / "never_created.txt")
        self.assertEqual([], FFmWiz.preserve_artifacts_for_manual_run(answers))

    def test_nothing_to_preserve_is_silent(self):
        FFmWiz.preserve_artifacts_for_manual_run({})
        self.assertEqual([], self._notes)

    def test_running_normally_still_cleans_up(self):
        # Preserving must not become the default; the execute path still deletes.
        answers = {}
        temp = self._leased_file(answers, "scratch.srt")
        FFmWiz.cleanup_join_concat_list(answers)
        self.assertFalse(temp.exists())


class DeclinePathUsesIt(unittest.TestCase):
    def test_the_join_decline_path_preserves_instead_of_cleaning(self):
        source = (Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "modes_join.py").read_text(encoding="utf-8")
        # Anchor on the branch itself, not on the message: the phrase appears
        # more than once in the file and a naive split lands between them.
        block = source.split("if not start_now:")[1].split("return None")[0]
        self.assertIn("preserve_artifacts_for_manual_run", block)
        self.assertNotIn("cleanup_join_concat_list", block)


if __name__ == "__main__":
    unittest.main()
