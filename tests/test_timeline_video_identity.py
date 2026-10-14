from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from unittest.mock import patch

from master_duel_recorder_lite.duel_timeline import DuelTimelineError, DuelTimelineRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from test_video_source_use import SourceUseFixture


class TimelineVideoIdentityTest(SourceUseFixture):
    def setUp(self) -> None:
        super().setUp()
        self.timeline = DuelTimelineRepository.from_runtime_paths(self.paths)

    def marker(self, **changes):
        values = {"elapsed_ms": 1000, "event_type": "marker", "label": "fixture"}
        values.update(changes)
        return self.timeline.add("recording", **values)

    def begin_recording(self):
        with self.connection() as connection:
            connection.execute("UPDATE recordings SET state='recording',ended_at=NULL,size_bytes=NULL,duration_seconds=NULL WHERE recording_id='recording'")

    def finish_recording(self):
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.history.finalize("recording", RecordingResult(
            RecordingState.COMPLETED, self.source, 0, start, start + timedelta(seconds=60), self.source.stat().st_size, None, (),
        ))

    def detected(self):
        return self.timeline.add("recording", elapsed_ms=1000, event_type="duel_start", source="detected",
                                 status="candidate", confidence=0.9, detector_id="fixture", detector_version="1")

    def test_new_completed_marker_has_immutable_source_and_actual_hash_proof(self) -> None:
        event = self.marker()
        asset = self.lifecycle.get_current_asset("recording")
        self.assertEqual((event.video_asset_id, event.video_generation, event.time_basis), (asset.asset_id, 1, "recording_start"))
        self.assertTrue(event.video_verified)
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM duel_event_sources WHERE event_id=?", (event.event_id,)).fetchone()
            proof = json.loads(row["input_fingerprint_json"])
            self.assertEqual(proof["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
            self.assertEqual(proof["schema"], "mdrl-timeline-video-v1")
            self.assertEqual(row["verified"], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assert_no_uses()

    def test_active_detection_captures_session_inode_without_hashing_and_verifies_after_finish(self) -> None:
        self.begin_recording()
        with patch("master_duel_recorder_lite.recording_retention.fingerprint_file", side_effect=AssertionError("must not hash active recording")):
            event = self.detected()
        self.assertFalse(event.video_verified)
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM duel_event_sources WHERE event_id=?", (event.event_id,)).fetchone()
            proof = json.loads(row["input_fingerprint_json"])
            self.assertEqual(row["verified"], 0)
            self.assertEqual(proof["sha256"], "")
            self.assertEqual(proof["inode"], self.source.stat().st_ino)
            self.assertTrue(proof["session_id"].startswith("recording:"))
        self.finish_recording()
        verified = self.timeline.verify_video_sources("recording")[0]
        self.assertTrue(verified.video_verified)
        self.assertEqual(verified.event_id, event.event_id)
        self.assertEqual(self.timeline.confirm(event.event_id).status, "confirmed")
        self.assert_no_uses()

    def test_recording_start_before_file_creation_stays_unverified_without_content_evidence(self) -> None:
        self.source.unlink()
        self.begin_recording()
        event = self.detected()
        self.assertFalse(event.video_verified)
        self.source.write_bytes(b"later-created file")
        self.finish_recording()
        with patch("master_duel_recorder_lite.recording_retention.fingerprint_file", side_effect=AssertionError("no inode or SHA proof")):
            self.assertFalse(self.timeline.verify_video_sources("recording")[0].video_verified)
            with self.assertRaisesRegex(DuelTimelineError, "一致"):
                self.timeline.confirm(event.event_id)
        self.assertEqual(self.timeline.get(event.event_id).status, "candidate")

    def test_legacy_empty_proof_does_not_upgrade_from_recording_id_or_same_path(self) -> None:
        event = self.marker(status="candidate")
        with self.connection() as connection:
            connection.execute("UPDATE duel_event_sources SET input_fingerprint_json='{}',verified=0 WHERE event_id=?", (event.event_id,))
        with patch("master_duel_recorder_lite.recording_retention.fingerprint_file", side_effect=AssertionError("must not invent legacy proof")):
            self.assertFalse(self.timeline.verify_video_sources("recording")[0].video_verified)
            with self.assertRaisesRegex(DuelTimelineError, "一致"):
                self.timeline.confirm(event.event_id)

    def test_content_hash_can_verify_after_copy_timestamp_changed(self) -> None:
        event = self.marker()
        asset = self.lifecycle.get_current_asset("recording")
        stat = self.source.stat()
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        with self.connection() as connection:
            connection.execute("UPDATE recording_video_assets SET state='unverified' WHERE asset_id=?", (asset.asset_id,))
            connection.execute("UPDATE duel_event_sources SET verified=0 WHERE event_id=?", (event.event_id,))
        self.assertFalse(self.timeline.get(event.event_id).video_verified)
        restored = self.timeline.verify_video_sources("recording")[0]
        self.assertTrue(restored.video_verified)
        current = self.lifecycle.get_current_asset("recording")
        self.assertEqual((current.asset_id, current.generation), (asset.asset_id, asset.generation))
        self.assertEqual(current.mtime_ns, self.source.stat().st_mtime_ns)

    def test_replacement_file_does_not_become_the_same_asset_from_old_marker_proof(self) -> None:
        event = self.marker()
        asset = self.lifecycle.get_current_asset("recording")
        self.source.write_bytes(b"different replacement contents")
        with self.connection() as connection:
            connection.execute("UPDATE recording_video_assets SET state='unverified',sha256='' WHERE asset_id=?", (asset.asset_id,))
            connection.execute("UPDATE duel_event_sources SET verified=0 WHERE event_id=?", (event.event_id,))
        self.assertFalse(self.timeline.verify_video_sources("recording")[0].video_verified)
        current = self.lifecycle.get_current_asset("recording")
        self.assertEqual((current.state, current.sha256), ("unverified", ""))
        with self.assertRaisesRegex(DuelTimelineError, "既存マーカー"):
            self.marker(label="new marker")
        self.assertEqual(len(self.timeline.list("recording")), 1)
        self.assert_no_uses()

    def test_pending_deletion_blocks_add_confirm_reject_and_label_edit(self) -> None:
        event = self.marker(status="candidate")
        self.pending_intent()
        for operation in (
            lambda: self.marker(), lambda: self.timeline.confirm(event.event_id),
            lambda: self.timeline.reject(event.event_id), lambda: self.timeline.update_marker_label(event.event_id, "changed"),
        ):
            with self.subTest(operation=operation), self.assertRaises(DuelTimelineError):
                operation()
        self.assertEqual(self.timeline.get(event.event_id).label, "fixture")
        self.assertEqual(self.timeline.get(event.event_id).status, "candidate")
        self.assertTrue(self.source.exists())
        self.assert_no_uses()

    def test_old_generation_marker_is_not_rebound_or_used_as_current_start(self) -> None:
        event = self.timeline.add("recording", elapsed_ms=1000, event_type="duel_start")
        asset = self.lifecycle.get_current_asset("recording")
        with self.connection() as connection:
            connection.execute("UPDATE recording_video_assets SET is_current=0 WHERE asset_id=?", (asset.asset_id,))
            connection.execute(
                "INSERT INTO recording_video_assets (asset_id,recording_id,generation,is_current,path,state,created_at,updated_at) "
                "VALUES ('new-asset','recording',2,1,'source.mkv','unverified',?,?)", (event.created_at.isoformat(), event.updated_at.isoformat()),
            )
        self.assertFalse(self.timeline.verify_video_sources("recording")[0].video_verified)
        with self.assertRaisesRegex(DuelTimelineError, "対戦開始"):
            self.timeline.add("recording", elapsed_ms=2000, event_type="turn_change", actor="self")
        current_start = self.timeline.add("recording", elapsed_ms=1000, event_type="duel_start")
        self.assertTrue(current_start.video_verified)
        self.assertEqual(current_start.video_generation, 2)
        self.assertEqual(self.timeline.get(event.event_id).video_generation, 1)
        self.assertFalse(self.timeline.get(event.event_id).video_verified)

    def test_marker_hashing_holds_lease_and_file_change_before_commit_rolls_back_marker(self) -> None:
        from master_duel_recorder_lite.recording_retention import fingerprint_file

        def guarded_hash(path):
            result = self.deletion.delete_video("recording")
            self.assertEqual((result.status, result.reason), ("skipped", "source_in_use"))
            return fingerprint_file(path)

        with patch("master_duel_recorder_lite.recording_retention.fingerprint_file", guarded_hash):
            event = self.marker()
        self.assertTrue(event.video_verified)
        guard = self.timeline._guard_asset

        def change_after_check(connection, asset):
            guard(connection, asset)
            self.source.write_bytes(b"changed before DB commit")

        with patch.object(self.timeline, "_guard_asset", change_after_check):
            with self.assertRaisesRegex(DuelTimelineError, "変更"):
                self.marker(label="must roll back")
        self.assertEqual(len(self.timeline.list("recording")), 1)
        self.assertFalse(self.timeline.get(event.event_id).video_verified)
        self.assert_no_uses()

    def test_constructor_without_recordings_root_is_read_only_and_not_verified(self) -> None:
        event = self.marker()
        readonly = DuelTimelineRepository(self.history.database_path)
        self.assertFalse(readonly.get(event.event_id).video_verified)
        with self.assertRaisesRegex(DuelTimelineError, "保存先"):
            readonly.verify_video_sources("recording")
        with self.assertRaisesRegex(DuelTimelineError, "保存先"):
            readonly.update_marker_label(event.event_id, "changed")
        self.assertTrue(self.timeline.get(event.event_id).video_verified)


if __name__ == "__main__":
    import unittest

    unittest.main()
