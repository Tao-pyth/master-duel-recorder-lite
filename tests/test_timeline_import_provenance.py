"""持込・復元された録画中proofは、inodeだけで内容一致へ昇格しません。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from unittest.mock import patch

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.data_management import ManagedDataService
from master_duel_recorder_lite.data_protection import DataProtectionService
from master_duel_recorder_lite.duel_timeline import DuelTimelineRepository
from master_duel_recorder_lite.portable_data import get_portable_export
from master_duel_recorder_lite.recording_retention import fingerprint_file
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from test_video_source_use import SourceUseFixture


class TimelineImportProvenanceTest(SourceUseFixture):
    def setUp(self):
        super().setUp()
        self.timeline = DuelTimelineRepository.from_runtime_paths(self.paths)
        self.service = RecorderApplicationService(user_data_dir=self.paths.root)
        self.addCleanup(self.service.close)

    def marker(self):
        return self.timeline.add("recording", elapsed_ms=1000, event_type="marker", label="fixture")

    def completed_capture(self):
        with self.connection() as connection:
            connection.execute("UPDATE recordings SET state='recording',ended_at=NULL,size_bytes=NULL,duration_seconds=NULL WHERE recording_id='recording'")
        event = self.timeline.add("recording", elapsed_ms=1000, event_type="duel_start", source="detected",
                                  status="candidate", confidence=.9, detector_id="fixture", detector_version="1")
        started = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.history.finalize("recording", RecordingResult(
            RecordingState.COMPLETED, self.source, 0, started, started + timedelta(seconds=60),
            self.source.stat().st_size, None, (),
        ))
        return event

    def proof(self, event_id):
        with self.connection() as connection:
            row = connection.execute("SELECT input_fingerprint_json FROM duel_event_sources WHERE event_id=?", (event_id,)).fetchone()
        return json.loads(row[0])

    def change_bytes_preserving_inode_and_size(self):
        before = self.source.stat()
        original = self.source.read_bytes()
        self.source.write_bytes(b"x" * len(original))
        self.assertEqual((self.source.stat().st_ino, self.source.stat().st_size), (before.st_ino, before.st_size))
        self.assertNotEqual(self.source.read_bytes(), original)

    def assert_weak_proof_unusable_without_hashing(self, event_id, origin):
        self.assertEqual(self.proof(event_id)["verification_origin"], origin)
        self.assertFalse(self.timeline.get(event_id).video_verified)
        with patch("master_duel_recorder_lite.recording_retention.fingerprint_file", side_effect=AssertionError("foreign inode is not content proof")):
            self.assertEqual(self.service.verify_recording_video_sources("recording"), 0)
            self.assertFalse(self.timeline.verify_video_sources("recording")[0].video_verified)
            model = self.service.get_review_view_model("recording")
        self.assertFalse(model.timeline[0].video_verified)
        self.assertEqual(model.visual_timeline, ())
        self.assertEqual(model.clip_candidates, ())
        self.assert_no_uses()

    def test_export_import_overwrites_forged_local_origin_and_rejects_changed_same_inode_capture(self):
        event = self.completed_capture()
        payload = get_portable_export(self.history.database_path, self.paths.recordings)
        source = payload["tables"]["duel_event_sources"][0]
        proof = json.loads(source["input_fingerprint_json"])
        proof["verification_origin"] = "local_capture"
        source["input_fingerprint_json"] = json.dumps(proof)
        source["verified"] = 1
        path = self.root / "portable.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        ManagedDataService.from_runtime_paths(self.paths).import_from(path)
        self.change_bytes_preserving_inode_and_size()
        self.assert_weak_proof_unusable_without_hashing(event.event_id, "portable_import")

    def test_restoration_forces_foreign_origin_and_rejects_changed_same_inode_capture(self):
        event = self.completed_capture()
        protection = DataProtectionService(self.paths)
        backup = protection.create_backup("provenance-fixture").path
        self.change_bytes_preserving_inode_and_size()
        protection.restore(backup)
        self.assert_weak_proof_unusable_without_hashing(event.event_id, "backup_restore")

    def test_imported_capture_with_unchanged_file_still_needs_original_sha(self):
        event = self.completed_capture()
        payload = get_portable_export(self.history.database_path, self.paths.recordings)
        path = self.root / "portable.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        ManagedDataService.from_runtime_paths(self.paths).import_from(path)
        self.assert_weak_proof_unusable_without_hashing(event.event_id, "portable_import")

    def copy_same_content(self):
        before = self.source.stat()
        replacement = self.source.with_suffix(".copy")
        replacement.write_bytes(self.source.read_bytes())
        os.replace(replacement, self.source)
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns + 5_000_000_000))

    def test_known_sha_import_allows_normal_copy_after_background_content_verification(self):
        event = self.marker()
        payload = get_portable_export(self.history.database_path, self.paths.recordings)
        path = self.root / "portable.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        ManagedDataService.from_runtime_paths(self.paths).import_from(path)
        self.copy_same_content()
        with patch("master_duel_recorder_lite.recording_retention.fingerprint_file", wraps=fingerprint_file) as hashed:
            self.assertEqual(self.service.verify_recording_video_sources("recording"), 1)
            self.assertTrue(self.timeline.get(event.event_id).video_verified)
            self.assertEqual(len(self.service.get_review_view_model("recording").clip_candidates), 1)
            self.assertEqual(hashed.call_count, 1)

    def test_known_sha_restore_allows_normal_copy_after_background_content_verification(self):
        event = self.marker()
        protection = DataProtectionService(self.paths)
        backup = protection.create_backup("provenance-fixture").path
        self.copy_same_content()
        protection.restore(backup)
        with patch("master_duel_recorder_lite.recording_retention.fingerprint_file", wraps=fingerprint_file) as hashed:
            self.assertEqual(self.service.verify_recording_video_sources("recording"), 1)
            self.assertTrue(self.timeline.get(event.event_id).video_verified)
            self.assertEqual(len(self.service.get_review_view_model("recording").clip_candidates), 1)
            self.assertEqual(hashed.call_count, 1)
