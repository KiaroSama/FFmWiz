"""Split from the command-generation suite (see command_gen_base.py)."""
import contextlib
import io
import os
import tempfile
from pathlib import Path
from unittest import mock
import FFmWiz
import cache_test_utils
from command_gen_base import CommandGenBase, _home_module


class CommandGenerationCoreTests3(CommandGenBase):
    def test_strict_resolve_ok_after_batch_choice(self):
        """After a batch policy, strict mode reports a batch user assumption."""
        settings = self._folder_settings(policy="pc")
        job = self._folder_job(color_range=None)
        FFmWiz.apply_folder_batch_color_range(job, settings, {"path": Path("a.mkv")})
        resolved, source = FFmWiz.resolve_color_range(job, allow_compatibility_fallback=False)
        self.assertEqual(resolved, "pc")
        self.assertEqual(source, "batch user assumption")

    def test_strict_resolve_ok_when_detected(self):
        """A detected source range satisfies the strict guard."""
        job = self._folder_job(color_range="tv")
        self.assertEqual(
            FFmWiz.resolve_color_range(job, allow_compatibility_fallback=False),
            ("tv", "detected"),
        )

    def test_entry_guard_raises_on_unresolved_reencode(self):
        """The production entry guard rejects an unresolved re-encode workflow."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._unknown_range_encode(tmp)
            with self.assertRaises(FFmWiz.ColorRangeUnresolvedError):
                FFmWiz.ensure_color_range_resolved(answers)

    def test_entry_guard_passes_after_choice(self):
        """The entry guard is a no-op once a choice is recorded."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._unknown_range_encode(tmp)
            answers["color_range_choice"] = "unspecified"
            FFmWiz.ensure_color_range_resolved(answers)  # must not raise

    def test_entry_guard_noop_for_stream_copy(self):
        """Stream-copy output never triggers the color-range guard."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._unknown_range_encode(tmp)
            answers["video_codec"] = "copy"
            answers["crop_enabled"] = False
            FFmWiz.ensure_color_range_resolved(answers)  # must not raise

    # ===================================================================
    # Pixel-format no-op (no destructive warning)
    # ===================================================================

    def test_pixfmt_420_to_nv12_no_destructive_warning(self):
        """yuv420p -> nv12 is a relabel: no bit-depth/chroma warning."""
        info = FFmWiz.compare_pixel_formats("yuv420p", "nv12")
        self.assertEqual(info["warnings"], [])
        self.assertEqual(info["bit_depth_conversion"], "no")
        self.assertEqual(info["chroma_conversion"], "no")

    def test_back_nav_preserves_do_not_force_default(self):
        """Re-entering the menu with a stored 'unspecified' choice defaults to 2."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)
            answers["color_range_choice"] = "unspecified"
            captured = {}

            def fake_ask(prompt):
                captured["prompt"] = prompt
                return ""  # Enter keeps the default.

            with mock.patch.object(FFmWiz.appio, "ask_raw", side_effect=fake_ask), \
                    contextlib.redirect_stdout(io.StringIO()):
                FFmWiz.step_color_range(answers)
            self.assertIn("[2]", captured["prompt"])
            self.assertEqual(answers["color_range_choice"], "unspecified")

    def test_environment_fingerprint_stable(self):
        with mock.patch.object(FFmWiz.services, "capability_environment_identity",
                               return_value=self._fake_identity()):
            _, k1 = FFmWiz.services.capability_environment_key("ffmpeg", "ffprobe", "libx265")
            _, k2 = FFmWiz.services.capability_environment_key("ffmpeg", "ffprobe", "libx265")
        self.assertEqual(k1, k2)

    def test_environment_fingerprint_path_invalidates(self):
        def ident(ffmpeg, ffprobe, *, include_gpu):
            return self._fake_identity(ffmpeg_path=ffmpeg)
        with mock.patch.object(FFmWiz.services, "capability_environment_identity", side_effect=ident):
            _, k1 = FFmWiz.services.capability_environment_key("C:/a/ffmpeg.exe", "ffprobe", "libx265")
            _, k2 = FFmWiz.services.capability_environment_key("C:/b/ffmpeg.exe", "ffprobe", "libx265")
        self.assertNotEqual(k1, k2)

    def test_environment_fingerprint_build_invalidates(self):
        def ident(ffmpeg, ffprobe, *, include_gpu):
            return self._fake_identity(ffmpeg_build_hash="v1" if "a" in ffmpeg else "v2")
        with mock.patch.object(FFmWiz.services, "capability_environment_identity", side_effect=ident):
            _, k1 = FFmWiz.services.capability_environment_key("a", "ffprobe", "libx265")
            _, k2 = FFmWiz.services.capability_environment_key("b", "ffprobe", "libx265")
        self.assertNotEqual(k1, k2)

    def test_cpu_entry_does_not_include_gpu(self):
        captured = {}
        def ident(ffmpeg, ffprobe, *, include_gpu):
            captured["include_gpu"] = include_gpu
            return self._fake_identity()
        with mock.patch.object(FFmWiz.services, "capability_environment_identity", side_effect=ident):
            FFmWiz.services.capability_environment_key("ffmpeg", "ffprobe", "libx265")
        self.assertFalse(captured["include_gpu"])

    def test_lazy_probe_runs_once_and_caches(self):
        verified = {"status": "verified", "expected_final_range": "tv",
                    "probe_method": "real encode + ffprobe", "encoder": "libx265",
                    "container_family": "mkv", "sample_command_hash": "h",
                    "ffprobe_result": "tv", "verified_at_utc": "t", "error": None}
        with mock.patch.object(FFmWiz.services, "capability_environment_identity",
                               return_value=self._fake_identity()), \
                mock.patch.object(FFmWiz.services, "probe_color_range_capability",
                                  return_value=verified) as probe:
            a = self._cap_answers()
            r1 = FFmWiz.services.resolve_capability(a)
            r2 = FFmWiz.services.resolve_capability(a)  # session memo -> no second probe
            FFmWiz.services._CAPABILITY_SESSION_MEMO.clear()
            r3 = FFmWiz.services.resolve_capability(a)  # file cache -> still no probe
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(r1["capability_source"], "fresh probe")
        self.assertEqual(r2["capability_source"], "fresh probe")  # memoized copy
        self.assertEqual(r3["capability_source"], "verified cache")
        self.assertEqual(r3["expected_final_range"], "tv")

    def test_entry_from_other_environment_not_reused(self):
        # Seed cache under a different env key.
        cache = {"schema_version": 1, "environments": {"OTHER": {"capabilities": {
            "color_range_do_not_force": {"libx265|mkv": {"status": "verified",
                                                          "expected_final_range": "pc"}}}}}}
        FFmWiz.save_capability_cache(cache)
        verified = {"status": "verified", "expected_final_range": "tv",
                    "probe_method": "m", "encoder": "libx265", "container_family": "mkv",
                    "sample_command_hash": "h", "ffprobe_result": "tv",
                    "verified_at_utc": "t", "error": None}
        with mock.patch.object(FFmWiz.services, "capability_environment_identity",
                               return_value=self._fake_identity()), \
                mock.patch.object(FFmWiz.services, "probe_color_range_capability",
                                  return_value=verified) as probe:
            r = FFmWiz.services.resolve_capability(self._cap_answers())
        self.assertEqual(probe.call_count, 1)  # other env not reused
        self.assertEqual(r["expected_final_range"], "tv")

    def test_probe_failure_does_not_abort(self):
        failed = {"status": "probe_failed", "expected_final_range": None, "probe_method": "m",
                  "encoder": "libx265", "container_family": "mkv", "sample_command_hash": "h",
                  "ffprobe_result": None, "verified_at_utc": "t", "error": "boom"}
        with mock.patch.object(FFmWiz.services, "capability_environment_identity",
                               return_value=self._fake_identity()), \
                mock.patch.object(FFmWiz.services, "probe_color_range_capability", return_value=failed):
            r = FFmWiz.services.resolve_capability(self._cap_answers())
        self.assertEqual(r["status"], "probe_failed")
        self.assertIsNone(r["expected_final_range"])

    def test_corrupted_cache_does_not_crash(self):
        Path(self._cache_dir, FFmWiz.CAPABILITY_CACHE_FILENAME).write_text("{not json", encoding="utf-8")
        data = FFmWiz.load_capability_cache()
        self.assertEqual(data["environments"], {})

    def test_atomic_cache_write_roundtrip(self):
        data = {"schema_version": 1, "environments": {"E": {"capabilities": {}}}}
        self.assertTrue(FFmWiz.save_capability_cache(data))
        self.assertTrue(Path(self._cache_dir, FFmWiz.CAPABILITY_CACHE_FILENAME).exists())
        self.assertEqual(FFmWiz.load_capability_cache()["environments"], {"E": {"capabilities": {}}})

    def test_view_cache_output_accurate(self):
        cache = {"schema_version": 1, "environments": {"ENVKEY123456": {
            "ffmpeg_identity": {"version": "ffmpeg 8.1.1", "build_hash": "x", "path": "p"},
            "hardware_identity": {"gpu": "n/a", "driver": "n/a"},
            "capabilities": {"color_range_do_not_force": {
                "libx265|mkv": {"status": "verified", "expected_final_range": "tv",
                                "verified_at_utc": "t"}}}}}}
        FFmWiz.save_capability_cache(cache)
        buf = io.StringIO()
        with mock.patch.object(FFmWiz.services, "capability_environment_key", return_value=({}, "ENVKEY123456")), \
                contextlib.redirect_stdout(buf):
            FFmWiz._capability_cache_view("ffmpeg", "ffprobe")
        out = buf.getvalue()
        self.assertIn("libx265|mkv", out)
        self.assertIn("status=verified", out)
        self.assertIn("expected_final_range=tv", out)

    def test_stale_mismatch_invalidates_entry(self):
        cache = {"schema_version": 1, "environments": {"K": {"capabilities": {
            "color_range_do_not_force": {"libx265|mkv": {"status": "verified",
                                                         "expected_final_range": "tv"}}}}}}
        FFmWiz.save_capability_cache(cache)
        with mock.patch.object(FFmWiz.services, "capability_environment_key", return_value=({}, "K")):
            FFmWiz.invalidate_capability_entry(self._cap_answers())
        data = FFmWiz.load_capability_cache()
        self.assertNotIn("libx265|mkv",
                         data["environments"]["K"]["capabilities"]["color_range_do_not_force"])

    # ===================================================================
    # SAR/DAR provenance separation (raw ffprobe vs resolved)
    # ===================================================================

    def test_raw_fields_unchanged_after_resolution(self):
        """Raw ffprobe SAR/DAR are preserved exactly; resolved values are separate."""
        stream = {"width": 720, "height": 576, "sample_aspect_ratio": "16:15"}
        answers = {"video_streams": [stream]}
        info = FFmWiz.sar_dar_info(answers)
        # Raw SAR present (16:15 -> ~1.0667), raw DAR absent.
        self.assertAlmostEqual(info["raw_ffprobe_sar"], 16 / 15, places=4)
        self.assertIsNone(info["raw_ffprobe_dar"])
        # Resolved values are stored separately and do not overwrite the stream.
        self.assertIsNotNone(info["resolved_dar"])
        self.assertEqual(stream.get("sample_aspect_ratio"), "16:15")
        self.assertNotIn("display_aspect_ratio", stream)

    def test_resolver_is_pure_and_idempotent(self):
        """resolve_video_geometry never mutates input and is idempotent."""
        g1 = FFmWiz.resolve_video_geometry(2160, 3840, None, 9 / 16)
        g2 = FFmWiz.resolve_video_geometry(2160, 3840, None, 9 / 16)
        self.assertEqual(g1, g2)
        # Feeding the resolved DAR back as raw must NOT change Case-B provenance
        # to a detected SAR-derived case; it is a different (legitimate) input,
        # but the resolver never consumes its own dict.
        self.assertEqual(g1["raw_ffprobe_dar"], 9 / 16)
        self.assertEqual(g1["raw_ffprobe_sar"], None)

    def test_fixture_both_unknown_provenance(self):
        """Fixture 2: no raw SAR/DAR -> fallback SAR 1:1, calculated DAR."""
        info = FFmWiz.sar_dar_info({"video_streams": [{"width": 2160, "height": 3840}]})
        self.assertEqual(info["sar_text"], "1:1")
        self.assertEqual(info["sar_source"], "fallback assumption")
        self.assertEqual(info["dar_text"], "9:16")
        self.assertEqual(info["dar_source"], "calculated from coded resolution and fallback SAR")
        self.assertEqual(info["pixel_shape"], "square (assumed)")
        self.assertTrue(info["fallback_used"])

    def test_both_fixtures_same_geometry_different_provenance(self):
        """The two 2160x3840 fixtures match numerically but differ in provenance."""
        detected = FFmWiz.sar_dar_info({"video_streams": [{"width": 2160, "height": 3840,
                                                          "display_aspect_ratio": "9:16"}]})
        fallback = FFmWiz.sar_dar_info({"video_streams": [{"width": 2160, "height": 3840}]})
        self.assertAlmostEqual(detected["effective_dar_decimal"],
                               fallback["effective_dar_decimal"], places=6)
        self.assertNotEqual(detected["sar_source"], fallback["sar_source"])
        self.assertNotEqual(detected["dar_source"], fallback["dar_source"])
        self.assertFalse(detected["fallback_used"])
        self.assertTrue(fallback["fallback_used"])

    def test_no_report_labels_fallback_as_detected(self):
        """A both-unknown result never labels SAR/DAR as detected by ffprobe."""
        info = FFmWiz.sar_dar_info({"video_streams": [{"width": 2160, "height": 3840}]})
        self.assertNotIn("detected by ffprobe", info["sar_source"])
        self.assertNotIn("detected by ffprobe", info["dar_source"])

    def test_provenance_independent_per_stream(self):
        """Resolving one stream does not contaminate another (Folder Encode)."""
        s1 = {"width": 2160, "height": 3840, "display_aspect_ratio": "9:16"}
        s2 = {"width": 2160, "height": 3840}
        i1 = FFmWiz.sar_dar_info({"video_streams": [s1]})
        i2 = FFmWiz.sar_dar_info({"video_streams": [s2]})
        self.assertEqual(i1["dar_source"], "detected by ffprobe")
        self.assertTrue(i2["fallback_used"])
        # Original raw streams unchanged.
        self.assertEqual(s1.get("display_aspect_ratio"), "9:16")
        self.assertNotIn("display_aspect_ratio", s2)

    def test_workflow_crop_does_not_overwrite_raw_geometry(self):
        """Crop keeps normalization and never writes post-crop DAR into raw fields."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["color_range_choice"] = "tv"
            stream = {"codec_type": "video", "codec_name": "h264",
                      "width": 720, "height": 576, "sample_aspect_ratio": "16:15"}
            answers["video_streams"] = [stream]
            answers["resolution"] = "n"
            answers["crop_enabled"] = True
            answers["crop_top"] = 2
            answers["crop_bottom"] = 2
            answers["crop_left"] = 4
            answers["crop_right"] = 4
            text = self.command_text(answers)
            self.assertIn("crop=", text)
            self.assertNotIn("pad=", text)  # no black compatibility padding
            # Raw source SAR is not overwritten by any post-crop geometry.
            self._assert_raw_immutable(stream, "16:15", None)
            info = FFmWiz.sar_dar_info(answers)
            self.assertEqual(info["raw_ffprobe_sar"], 16 / 15)
            self.assertEqual(info["sar_source"], "detected by ffprobe")

    # ===================================================================
    # Cleanup-safety: owned temp cache only; protected paths refused
    # ===================================================================

    def test_cleanup_isolated_temp_cache_created_with_marker(self):
        run_id = "run-" + os.urandom(4).hex()
        path = cache_test_utils.create_owned_temp_cache_dir(run_id)
        try:
            p = Path(path)
            self.assertTrue(p.is_dir())
            self.assertEqual(Path(tempfile.gettempdir()).resolve(), p.resolve().parent)
            marker = p / cache_test_utils.TEST_CACHE_OWNER_MARKER
            self.assertTrue(marker.is_file())
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), run_id)
        finally:
            cache_test_utils.safe_remove_owned_temp_dir(path, run_id, tempfile.gettempdir())

    def test_cleanup_owned_delete_and_idempotent(self):
        run_id = "run-" + os.urandom(4).hex()
        path = cache_test_utils.create_owned_temp_cache_dir(run_id)
        self.assertTrue(cache_test_utils.safe_remove_owned_temp_dir(path, run_id, tempfile.gettempdir()))
        self.assertFalse(Path(path).exists())
        # Idempotent second call.
        self.assertFalse(cache_test_utils.safe_remove_owned_temp_dir(path, run_id, tempfile.gettempdir()))

    def test_cleanup_marker_mismatch_blocks(self):
        run_id = "run-" + os.urandom(4).hex()
        path = cache_test_utils.create_owned_temp_cache_dir(run_id)
        try:
            with self.assertRaises(RuntimeError):
                cache_test_utils.safe_remove_owned_temp_dir(path, "WRONG-ID", tempfile.gettempdir())
            self.assertTrue(Path(path).exists())
        finally:
            cache_test_utils.safe_remove_owned_temp_dir(path, run_id, tempfile.gettempdir())

    def test_cleanup_missing_marker_blocks(self):
        run_id = "run-" + os.urandom(4).hex()
        path = cache_test_utils.create_owned_temp_cache_dir(run_id)
        try:
            (Path(path) / cache_test_utils.TEST_CACHE_OWNER_MARKER).unlink()
            with self.assertRaises(RuntimeError):
                cache_test_utils.safe_remove_owned_temp_dir(path, run_id, tempfile.gettempdir())
            self.assertTrue(Path(path).exists())
        finally:
            import shutil as _sh
            _sh.rmtree(path, ignore_errors=True)

    def test_cleanup_refuses_protected_paths(self):
        root = tempfile.gettempdir()
        project_root = Path(FFmWiz.__file__).resolve().parent
        protected_candidates = [
            project_root,                                  # project root
            project_root / FFmWiz.CAPABILITY_CACHE_DIRNAME,  # project .cache
            Path.home(),                                   # home
            Path(project_root.anchor),                     # filesystem root
            Path(tempfile.gettempdir()),                   # temp root itself
        ]
        for candidate in protected_candidates:
            with self.assertRaises(RuntimeError):
                cache_test_utils.safe_remove_owned_temp_dir(candidate, "any", root)

    def test_cleanup_refuses_path_outside_temp_root(self):
        project_root = Path(FFmWiz.__file__).resolve().parent
        with self.assertRaises(RuntimeError):
            cache_test_utils.safe_remove_owned_temp_dir(project_root / "some_sub", "any", tempfile.gettempdir())

    def test_cleanup_refuses_symlink_to_protected(self):
        run_id = "run-" + os.urandom(4).hex()
        link_parent = cache_test_utils.create_owned_temp_cache_dir(run_id)
        link = Path(link_parent) / "link_to_cache"
        target = Path(FFmWiz.__file__).resolve().parent / FFmWiz.CAPABILITY_CACHE_DIRNAME
        try:
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation not permitted on this system")
            # Resolves to project .cache -> protected -> refused.
            with self.assertRaises(RuntimeError):
                cache_test_utils.safe_remove_owned_temp_dir(link, run_id, tempfile.gettempdir())
        finally:
            cache_test_utils.safe_remove_owned_temp_dir(link_parent, run_id, tempfile.gettempdir())

    def test_corrupted_cache_recovery_keeps_unrelated_files(self):
        Path(self._cache_dir, FFmWiz.CAPABILITY_CACHE_FILENAME).write_text("{bad", encoding="utf-8")
        unrelated = Path(self._cache_dir, "keep_me.txt")
        unrelated.write_text("data", encoding="utf-8")
        FFmWiz.load_capability_cache()  # moves corrupt aside, does not delete others
        self.assertTrue(unrelated.exists())

    def test_teardown_uses_isolated_cache_not_real(self):
        """The active cache path resolves under the owned temp dir, not project."""
        active = FFmWiz.capability_cache_path().resolve()
        self.assertEqual(active.parent, Path(self._cache_dir).resolve())
        project_cache = Path(FFmWiz.__file__).resolve().parent / FFmWiz.CAPABILITY_CACHE_DIRNAME
        self.assertNotEqual(active.parent, project_cache)

    def test_production_has_no_test_only_cache_helpers(self):
        """Test-only cleanup infrastructure must not live in production FFmWiz.py."""
        for symbol in ("create_owned_temp_cache_dir", "safe_remove_owned_temp_dir",
                       "TEST_CACHE_OWNER_MARKER", "protected_cleanup_paths"):
            self.assertFalse(hasattr(FFmWiz, symbol),
                             "FFmWiz unexpectedly exposes test-only symbol %s" % symbol)
        src = Path(FFmWiz.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".ffmwiz_test_cache_owner", src)
        self.assertNotIn("create_owned_temp_cache_dir", src)
        self.assertNotIn("safe_remove_owned_temp_dir", src)
        self.assertNotIn("ffmwiz_test_cache_", src)
        # The test utility module provides them instead.
        self.assertTrue(hasattr(cache_test_utils, "create_owned_temp_cache_dir"))
        self.assertTrue(hasattr(cache_test_utils, "safe_remove_owned_temp_dir"))

    def test_clear_removes_recovery_created_corrupt_backup(self):
        """The clear action removes the exact corrupt-backup the recovery makes."""
        cap = FFmWiz.capability_cache_path()
        cap.parent.mkdir(parents=True, exist_ok=True)
        cap.write_text("{not valid json", encoding="utf-8")
        # Real recovery path renames the bad file to ffmpeg_capabilities.corrupt.
        FFmWiz.load_capability_cache()
        corrupt = cap.with_suffix(".corrupt")
        self.assertEqual(corrupt.name, "ffmpeg_capabilities.corrupt")
        self.assertTrue(corrupt.exists())
        # Recreate a valid primary file as well.
        FFmWiz.save_capability_cache({"schema_version": 1, "environments": {}})
        self.assertTrue(cap.exists())
        with mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()):
            FFmWiz._capability_cache_clear()
        self.assertFalse(cap.exists())
        self.assertFalse(corrupt.exists())

    def test_clear_keeps_similar_but_unrelated_filenames(self):
        """Files that merely resemble cache artifacts must survive the clear."""
        cap = FFmWiz.capability_cache_path()
        cap.parent.mkdir(parents=True, exist_ok=True)
        FFmWiz.save_capability_cache({"schema_version": 1, "environments": {}})
        decoys = [
            Path(self._cache_dir, "ffmpeg_capabilities.json.bak"),
            Path(self._cache_dir, "my_ffmpeg_capabilities.json"),
            Path(self._cache_dir, "capabilities.corrupt"),
            Path(self._cache_dir, "ffmpeg_capabilities.corrupt.old"),
            Path(self._cache_dir, "notes.txt"),
        ]
        for d in decoys:
            d.write_text("keep", encoding="utf-8")
        with mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()):
            FFmWiz._capability_cache_clear()
        self.assertFalse(cap.exists())
        for d in decoys:
            self.assertTrue(d.exists(), "decoy unexpectedly removed: %s" % d.name)

    def test_clear_missing_files_is_idempotent_noop(self):
        """Clearing when no cache files exist is a safe no-op (no prompt, no error)."""
        cap = FFmWiz.capability_cache_path()
        self.assertFalse(cap.exists())
        with mock.patch.object(FFmWiz.appio, "ask_yes_no",
                               side_effect=AssertionError("should not prompt")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            FFmWiz._capability_cache_clear()
            FFmWiz._capability_cache_clear()  # idempotent
        self.assertIn("already empty", out.getvalue())
        self.assertTrue(Path(self._cache_dir).is_dir())

    def test_clear_partial_failure_does_not_claim_full_success(self):
        """If one owned artifact cannot be deleted, unrelated files survive and
        the message does not falsely claim a full clear."""
        cap = FFmWiz.capability_cache_path()
        cap.parent.mkdir(parents=True, exist_ok=True)
        FFmWiz.save_capability_cache({"schema_version": 1, "environments": {}})
        # Make the corrupt-backup name an undeletable directory (unlink fails).
        corrupt_dir = cap.with_suffix(".corrupt")
        corrupt_dir.mkdir()
        (corrupt_dir / "blocker.txt").write_text("x", encoding="utf-8")
        unrelated = Path(self._cache_dir, "survivor.json")
        unrelated.write_text("{}", encoding="utf-8")
        buf = io.StringIO()
        with mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
                contextlib.redirect_stdout(buf):
            FFmWiz._capability_cache_clear()
        text = buf.getvalue()
        self.assertFalse(cap.exists())               # primary removed
        self.assertTrue(corrupt_dir.is_dir())         # failed artifact remains
        self.assertTrue(unrelated.exists())           # unrelated survives
        self.assertIn("partially cleared", text)
        self.assertNotIn("Capability cache cleared (", text)

    # ===================================================================
    # Real-cache read-only snapshot safety
    # ===================================================================

    def test_snapshot_is_read_only_for_existing_file(self):
        """Snapshotting an existing cache file does not modify it."""
        with tempfile.TemporaryDirectory(prefix="ffmwiz_snap_") as tmp:
            f = Path(tmp, "ffmpeg_capabilities.json")
            f.write_text('{"schema_version":1,"environments":{}}', encoding="utf-8")
            before = (f.stat().st_size, f.stat().st_mtime_ns, f.read_bytes())
            snap = cache_test_utils.snapshot_runtime_cache_state(tmp)
            after = (f.stat().st_size, f.stat().st_mtime_ns, f.read_bytes())
            self.assertEqual(before, after)  # unchanged
            self.assertTrue(snap["ffmpeg_capabilities.json"]["exists"])
            self.assertIsNotNone(snap["ffmpeg_capabilities.json"]["sha256"])
            self.assertFalse(snap["ffmpeg_capabilities.corrupt"]["exists"])

    def test_snapshot_does_not_create_absent_files(self):
        """Snapshotting an empty cache dir creates nothing."""
        with tempfile.TemporaryDirectory(prefix="ffmwiz_snap_") as tmp:
            snap = cache_test_utils.snapshot_runtime_cache_state(tmp)
            for name in cache_test_utils.CAPABILITY_CACHE_OWNED_FILENAMES:
                self.assertFalse(snap[name]["exists"])
                self.assertIsNone(snap[name]["sha256"])
                self.assertFalse(Path(tmp, name).exists())  # not created

    def test_snapshot_does_not_create_runtime_cache_dir(self):
        """The default runtime snapshot never creates the real .cache directory."""
        runtime_dir = cache_test_utils.runtime_cache_dir()
        existed_before = runtime_dir.exists()
        cache_test_utils.snapshot_runtime_cache_state()  # default = real dir
        self.assertEqual(runtime_dir.exists(), existed_before)

    # ===================================================================
    # Isolated cache environment save/restore
    # ===================================================================

    def test_isolated_cache_env_restores_previous_value(self):
        os.environ["FFMWIZ_CACHE_DIR"] = "SENTINEL_PREV_VALUE"
        try:
            with cache_test_utils.isolated_cache_env() as (path, _run):
                self.assertEqual(os.environ["FFMWIZ_CACHE_DIR"], path)
            self.assertEqual(os.environ["FFMWIZ_CACHE_DIR"], "SENTINEL_PREV_VALUE")
        finally:
            os.environ.pop("FFMWIZ_CACHE_DIR", None)

    def test_isolated_cache_env_restores_on_exception(self):
        os.environ.pop("FFMWIZ_CACHE_DIR", None)
        with self.assertRaises(ValueError):
            with cache_test_utils.isolated_cache_env():
                raise ValueError("boom")
        # Absent before -> absent after, even though the body raised.
        self.assertNotIn("FFMWIZ_CACHE_DIR", os.environ)

    def test_writable_tests_use_isolated_cache_dir(self):
        active = FFmWiz.capability_cache_path().resolve()
        self.assertEqual(active.parent, Path(self._cache_dir).resolve())
        self.assertEqual(os.environ.get("FFMWIZ_CACHE_DIR"), self._cache_dir)

    # ===================================================================
    # Validation sentinel safety
    # ===================================================================

    def test_sentinel_exclusive_creation_and_unique_name(self):
        info = cache_test_utils.create_validation_sentinel(self._cache_dir, self._cache_run_id)
        try:
            self.assertIn(self._cache_run_id, info["name"])
            self.assertTrue(info["name"].startswith(".ffmwiz_validation_sentinel_"))
            self.assertNotIn(info["name"], cache_test_utils.CAPABILITY_CACHE_OWNED_FILENAMES)
            self.assertEqual(Path(info["path"]).read_text(encoding="utf-8"), info["token"])
        finally:
            cache_test_utils.remove_validation_sentinel(info, self._cache_dir)

    def test_sentinel_existing_path_blocks_overwrite(self):
        info = cache_test_utils.create_validation_sentinel(self._cache_dir, self._cache_run_id)
        try:
            # Re-creating with the same run id targets the same path -> refused.
            with self.assertRaises(RuntimeError):
                cache_test_utils.create_validation_sentinel(self._cache_dir, self._cache_run_id)
        finally:
            cache_test_utils.remove_validation_sentinel(info, self._cache_dir)

    def test_sentinel_token_mismatch_blocks_deletion(self):
        info = cache_test_utils.create_validation_sentinel(self._cache_dir, self._cache_run_id)
        try:
            tampered = dict(info)
            tampered["token"] = "WRONG-TOKEN"
            with self.assertRaises(RuntimeError):
                cache_test_utils.remove_validation_sentinel(tampered, self._cache_dir)
            self.assertTrue(Path(info["path"]).exists())
        finally:
            cache_test_utils.remove_validation_sentinel(info, self._cache_dir)

    def test_sentinel_never_uses_production_cache_filename(self):
        info = cache_test_utils.create_validation_sentinel(self._cache_dir, self._cache_run_id)
        try:
            self.assertNotIn("ffmpeg_capabilities", info["name"])
        finally:
            cache_test_utils.remove_validation_sentinel(info, self._cache_dir)

    def test_sentinel_only_exact_owned_removed(self):
        info = cache_test_utils.create_validation_sentinel(self._cache_dir, self._cache_run_id)
        decoy = Path(self._cache_dir, ".ffmwiz_validation_sentinel_OTHER")
        decoy.write_text("other", encoding="utf-8")
        self.assertTrue(cache_test_utils.remove_validation_sentinel(info, self._cache_dir))
        self.assertFalse(Path(info["path"]).exists())
        self.assertTrue(decoy.exists())  # unrelated sentinel-like file survives

    def test_mock_protected_cache_dir_not_recursively_deleted(self):
        """A mock runtime .cache dir (no ownership marker) cannot be rmtree'd."""
        with tempfile.TemporaryDirectory(prefix="ffmwiz_mockproj_") as tmp:
            mock_cache = Path(tmp, "mock_project", ".cache")
            mock_cache.mkdir(parents=True)
            keep = mock_cache / "ffmpeg_capabilities.json"
            keep.write_text("{}", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                cache_test_utils.safe_remove_owned_temp_dir(
                    mock_cache, "any-id", tempfile.gettempdir())
            self.assertTrue(keep.exists())  # nothing deleted

    # ===================================================================
    # Professional logging (UTC, structured, redaction, shutdown)
    # ===================================================================

    def test_redact_secrets_masks_credentials_only(self):
        r = FFmWiz.redact_secrets
        self.assertNotIn("SECRET123", r("api_key=SECRET123"))
        self.assertIn("[REDACTED]", r("api_key=SECRET123"))
        self.assertNotIn("hunter2", r("password: hunter2"))
        self.assertNotIn("tok_abc", r("access_token=tok_abc"))
        self.assertNotIn("jwtpart", r("Authorization: Bearer jwtpart.more"))
        self.assertEqual(r("://u:p4ss@host/x"), "://u:[REDACTED]@host/x")
        # Ordinary FFmpeg arguments must not be touched.
        self.assertEqual(r("crf=23 preset=medium scale=1280:720"),
                         "crf=23 preset=medium scale=1280:720")
        self.assertEqual(r(""), "")
        self.assertEqual(r(None), "")

    def test_logging_file_is_utc_structured_and_redacted(self):
        prev = (FFmWiz.appio._LOGGER, FFmWiz.appio._LOG_PATH, FFmWiz.appio._SHUTDOWN_LOGGED,
                FFmWiz.appio._EXECUTION_ID, FFmWiz.appio._SESSION_START_MONOTONIC)
        FFmWiz.appio._LOGGER = None
        FFmWiz.appio._LOG_PATH = None
        FFmWiz.appio._SHUTDOWN_LOGGED = False
        try:
            with tempfile.TemporaryDirectory(prefix="ffmwiz_logtest_") as tmp, \
                    mock.patch.object(FFmWiz.appio, "_logs_dir", return_value=Path(tmp)), \
                    mock.patch.object(FFmWiz.appio, "_logging_enabled_from_config", return_value=True), \
                    mock.patch.object(FFmWiz.appio, "_log_retention_days_from_config", return_value=0):
                path = FFmWiz.setup_logging()
                self.assertIsNotNone(path)
                self.assertTrue(path.name.startswith("ffmwiz_"))
                self.assertTrue(path.name.endswith("_UTC.log"))
                FFmWiz.log_info("hello world", component="UnitTest")
                FFmWiz.log_warn("careful now", component="UnitTest")
                FFmWiz.log_info("login api_key=TOPSECRETXYZ done", component="Net")
                FFmWiz.shutdown_logging(exit_code=0)
                text = path.read_text(encoding="utf-8")
            import re as _re
            self.assertRegex(
                text,
                r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\] \[INFO\] \[UnitTest\] hello world",
            )
            self.assertIn("[WARNING] [UnitTest] careful now", text)
            self.assertNotIn("TOPSECRETXYZ", text)
            self.assertIn("api_key=[REDACTED]", text)
            self.assertNotRegex(text, r"\d{2}:\d{2}:\d{2}[.,]\d")  # no milliseconds
            self.assertIn("[Shutdown]", text)
            self.assertIn("total duration=", text)
        finally:
            # Restore module logging state so other tests are unaffected.
            if FFmWiz.appio._LOGGER is not None:
                for h in list(FFmWiz.appio._LOGGER.handlers):
                    try:
                        h.close()
                    except Exception:
                        pass
                    FFmWiz.appio._LOGGER.removeHandler(h)
            (FFmWiz.appio._LOGGER, FFmWiz.appio._LOG_PATH, FFmWiz.appio._SHUTDOWN_LOGGED,
             FFmWiz.appio._EXECUTION_ID, FFmWiz.appio._SESSION_START_MONOTONIC) = prev


if __name__ == "__main__":
    unittest.main()
