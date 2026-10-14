from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.history_database import connect_history_database
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_lock import RecordingLock
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import default_runtime_paths
from master_duel_recorder_lite.video_lifecycle import (
    VideoLifecycleError,
    VideoLifecycleRepository,
    _process_alive,
)


STAMP = datetime(2026, 10, 1, tzinfo=timezone.utc)
TEXT_STAMP = STAMP.isoformat()


class VideoLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "db" / "history.sqlite3"
        self.recordings = self.root / "recordings"
        self.recordings.mkdir()
        self.repository = VideoLifecycleRepository(database_path=self.database, recordings_root=self.recordings)
        self.history = RecordingHistoryRepository(database_path=self.database, recordings_root=self.recordings)
        self.add_recording("one")

    @contextmanager
    def connection(self):
        with closing(connect_history_database(self.database)) as connection, connection:
            yield connection

    def add_recording(self, identifier: str) -> None:
        output = self.recordings / f"{identifier}.mkv"
        output.write_bytes(f"test original {identifier}".encode())
        self.history.register_starting(
            recording_id=identifier, output_path=output, container="mkv", source="manual", created_at=STAMP,
        )
        self.history.finalize(identifier, RecordingResult(
            state=RecordingState.COMPLETED, output_path=output, returncode=0,
            started_at=STAMP, ended_at=STAMP + timedelta(seconds=5), size_bytes=output.stat().st_size,
            error=None, diagnostics=(),
        ))

    def verified(self, identifier: str = "one"):
        asset = self.repository.ensure_current_asset(identifier)
        path = self.recordings / asset.path
        return self.repository.set_verified_asset(
            asset.asset_id, size_bytes=path.stat().st_size, mtime_ns=path.stat().st_mtime_ns,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )

    def intent(self, identifier: str = "one", **changes):
        asset = self.repository.get_current_asset(identifier) or self.verified(identifier)
        if asset.state == "unverified":
            asset = self.verified(identifier)
        values = {
            "recording_id": identifier, "asset_id": asset.asset_id, "generation": asset.generation,
            "reason": "retention", "original_path": asset.path,
            "staged_path": f".deleting/{asset.asset_id}.mkv", "size_bytes": asset.size_bytes,
            "mtime_ns": asset.mtime_ns, "sha256": asset.sha256,
            "backup_path": str(self.root / "verified.mdrl-backup"), "metadata": {"policy": {"retention_days": 1}},
        }
        values.update(changes)
        return self.repository.create_intent(**values)

    def unlink_and_complete(self, intent):
        self.repository.update_intent(intent.intent_id, "staged")
        (self.recordings / intent.original_path).unlink()
        self.repository.update_intent(intent.intent_id, "unlinked")
        return self.repository.complete_deletion(intent.intent_id)

    def upload(self, identifier: str = "one", *, state: str = "completed", video_id: str | None = "video", watch_url: str | None = "https://www.youtube.com/watch?v=video") -> str:
        upload_id = f"upload-{identifier}"
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO youtube_uploads (upload_id,recording_id,prepare_queue_id,state,privacy,title,video_id,watch_url,created_at,updated_at) "
                "VALUES (?,?,'queue',?,'private','test',?,?,?,?)",
                (upload_id, identifier, state, video_id, watch_url, TEXT_STAMP, TEXT_STAMP),
            )
        return upload_id

    def request(self, identifier: str = "one", **changes):
        asset = self.repository.ensure_current_asset(identifier)
        values = {
            "recording_id": identifier, "asset_id": asset.asset_id, "generation": asset.generation,
            "upload_id": f"upload-{identifier}", "prepare_queue_id": "queue",
            "provenance": {"source": {"path": asset.path}, "export": {"sha256": "a" * 64}},
            "skip_reason": "source_busy",
        }
        values.update(changes)
        return self.repository.request_cleanup(**values)

    def job(self, job_id: str, asset, *, state: str = "queued", generation: int = 1) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO card_analysis_jobs (job_id,recording_id,generation,state,fingerprint_json,target_snapshot_json,analyzer_version,"
                "sample_fps,progress,result_metadata_json,created_at,updated_at) VALUES (?,?,?,?,'{}','[]','test',1,0,?,?,?)",
                (job_id, asset.recording_id, generation, state, json.dumps({"checkpoint": {"frame": 1}, "audit": "keep"}), TEXT_STAMP, TEXT_STAMP),
            )
            connection.execute(
                "INSERT INTO card_analysis_job_sources (job_id,video_asset_id,video_generation,time_basis,input_fingerprint_json,verified) "
                "VALUES (?,?,?,'recording_start','{}',1)", (job_id, asset.asset_id, asset.generation),
            )

    def test_constructor_and_reads_do_not_create_requests_assets_or_delete(self) -> None:
        before = (self.recordings / "one.mkv").read_bytes()
        again = VideoLifecycleRepository(database_path=self.database, recordings_root=self.recordings)
        self.assertIsNone(again.get_current_asset("one"))
        protection = again.get_protection("one")
        self.assertEqual((protection.protected, protection.revision), (False, 0))
        self.assertEqual(again.pending_intents(), ())
        self.assertEqual(again.pending_cleanup_requests(), ())
        self.assertEqual((self.recordings / "one.mkv").read_bytes(), before)
        with self.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM recording_video_assets").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM recording_video_protection").fetchone()[0], 0)

    def test_runtime_constructor_uses_protected_initialization(self) -> None:
        paths = default_runtime_paths(self.root / "runtime")
        with patch("master_duel_recorder_lite.data_protection.initialize_protected_history_database") as protected:
            repository = VideoLifecycleRepository.from_runtime_paths(paths)
        protected.assert_called_once_with(paths)
        self.assertEqual(repository.recordings_root, paths.recordings.resolve())
        self.assertEqual(repository.operation_lock_path, paths.data / "video-operations.lock")
        self.assertEqual(repository.pending_intents(), ())

    def test_lazy_asset_is_stable_and_unverified_without_reading_original(self) -> None:
        with patch.object(Path, "read_bytes", side_effect=AssertionError("must not read original")):
            first = self.repository.ensure_current_asset("one")
            second = self.repository.ensure_current_asset("one")
        self.assertEqual(first, second)
        self.assertEqual((first.generation, first.path, first.state, first.sha256), (1, "one.mkv", "unverified", ""))
        self.assertIsNone(first.mtime_ns)
        with self.assertRaises(FrozenInstanceError):
            first.state = "deleted"
        with self.connection() as connection:
            connection.execute("UPDATE recordings SET output_path='replacement.mkv' WHERE recording_id='one'")
        with self.assertRaises(VideoLifecycleError):
            self.repository.ensure_current_asset("one")

    def test_verified_identity_is_immutable_and_pending_deletion_blocks_updates(self) -> None:
        asset = self.verified()
        self.assertEqual(asset.state, "present")
        with self.assertRaises(VideoLifecycleError):
            self.repository.set_verified_asset(asset.asset_id, size_bytes=asset.size_bytes, mtime_ns=asset.mtime_ns, sha256="b" * 64)
        intent = self.intent()
        with self.assertRaises(VideoLifecycleError):
            self.repository.set_verified_asset(asset.asset_id, size_bytes=asset.size_bytes, mtime_ns=asset.mtime_ns, sha256=asset.sha256)
        self.repository.update_intent(intent.intent_id, "cancelled")
        self.assertEqual(self.repository.set_verified_asset(asset.asset_id, size_bytes=asset.size_bytes, mtime_ns=asset.mtime_ns, sha256=asset.sha256), asset)

    def test_protection_revision_and_manual_override_do_not_clear_flag(self) -> None:
        first = self.repository.set_protection("one", True, expected_revision=0)
        self.assertEqual(first.revision, 1)
        self.assertEqual(self.repository.set_protection("one", True, expected_revision=1), first)
        with self.assertRaises(VideoLifecycleError):
            self.repository.set_protection("one", False, expected_revision=0)
        for changes in ({}, {"reason": "capacity", "allow_protected": True}, {"reason": "manual"}):
            with self.subTest(changes=changes), self.assertRaises(VideoLifecycleError):
                self.intent(**changes)
        intent = self.intent(reason="manual", allow_protected=True)
        self.assertEqual(intent.reason, "manual")
        self.assertTrue(self.repository.get_protection("one").protected)

    def test_intent_rechecks_latest_protection_from_another_repository(self) -> None:
        self.verified()
        other = VideoLifecycleRepository(database_path=self.database, recordings_root=self.recordings)
        other.set_protection("one", True)
        with self.assertRaises(VideoLifecycleError):
            self.intent()
        self.assertEqual(self.repository.pending_intents(), ())
        self.assertTrue((self.recordings / "one.mkv").exists())

    def test_active_recording_running_job_and_use_block_intent(self) -> None:
        asset = self.verified()
        for state in ("starting", "recording"):
            with self.connection() as connection:
                connection.execute("UPDATE recordings SET state=? WHERE recording_id='one'", (state,))
            with self.subTest(state=state), self.assertRaises(VideoLifecycleError):
                self.intent()
        with self.connection() as connection:
            connection.execute("UPDATE recordings SET state='completed' WHERE recording_id='one'")
        self.job("running", asset, state="running")
        with self.assertRaises(VideoLifecycleError):
            self.intent()
        with self.connection() as connection:
            connection.execute("UPDATE card_analysis_jobs SET state='completed' WHERE job_id='running'")
        use = self.repository.register_use(asset_id=asset.asset_id, owner_id="review", purpose="playback")
        with self.assertRaises(VideoLifecycleError):
            self.intent()
        self.assertTrue(self.repository.release_use(use.use_id, owner_id="review"))
        self.assertEqual(self.intent().state, "intent")

    def test_intent_is_idempotent_and_unlinked_cannot_be_downgraded(self) -> None:
        intent = self.intent()
        self.assertEqual(self.intent().intent_id, intent.intent_id)
        with self.assertRaises(VideoLifecycleError):
            self.intent(reason="capacity")
        self.assertEqual(len(self.repository.pending_intents()), 1)
        with self.assertRaises(VideoLifecycleError):
            self.repository.complete_deletion(intent.intent_id)
        with self.assertRaises(VideoLifecycleError):
            self.repository.update_intent(intent.intent_id, "unlinked")
        self.repository.update_intent(intent.intent_id, "staged")
        (self.recordings / intent.original_path).unlink()
        self.repository.update_intent(intent.intent_id, "unlinked")
        with self.assertRaises(VideoLifecycleError):
            self.repository.update_intent(intent.intent_id, "failed")
        first = self.repository.complete_deletion(intent.intent_id)
        second = self.repository.complete_deletion(intent.intent_id)
        self.assertEqual(first, second)
        self.assertEqual(first.reason, "retention")
        self.assertEqual(self.repository.pending_intents(), ())
        with self.assertRaises(VideoLifecycleError):
            self.intent(reason="manual")

    def seed_evidence(self):
        current = self.verified()
        self.add_recording("other")
        other = self.verified("other")
        with self.connection() as connection:
            for identifier in ("one", "other"):
                connection.execute(
                    "INSERT INTO duel_records (duel_id,recording_id,entry_origin,occurred_at,status,result,play_order,coin_face,"
                    "duel_type,notes,revision,created_at,updated_at) VALUES (?,?,'recording',?,'confirmed','win','first','heads','ranked','keep notes',1,?,?)",
                    (f"duel-{identifier}", identifier, TEXT_STAMP, TEXT_STAMP, TEXT_STAMP),
                )
                connection.execute("INSERT INTO duel_record_tags (duel_id,tag,normalized_tag) VALUES (?,'keep tag','keep tag')", (f"duel-{identifier}",))
            connection.execute("INSERT INTO card_identities VALUES ('card','カード','card',?,?)", (TEXT_STAMP, TEXT_STAMP))
            for info, duel, actor, state in (("use", "duel-one", "self", "used"), ("nonuse", "duel-one", "opponent", "not_used"), ("other-use", "duel-other", "self", "used")):
                connection.execute("INSERT INTO duel_card_information VALUES (?,?,'card',?,?,'manual',1,?,?,?)", (info, duel, actor, state, TEXT_STAMP, TEXT_STAMP, TEXT_STAMP))
                connection.execute("INSERT INTO duel_card_information_changes (information_id,revision,source,before_json,after_json,changed_at) VALUES (?,1,'manual','{}','{}',?)", (info, TEXT_STAMP))
            connection.execute("INSERT INTO duel_card_openers VALUES ('duel-one','self','card','confirmed','manual',1,?,?,?)", (TEXT_STAMP, TEXT_STAMP, TEXT_STAMP))
            connection.execute("INSERT INTO duel_card_opener_changes (duel_id,actor,revision,source,before_json,after_json,changed_at) VALUES ('duel-one','self',1,'manual','{}','{}',?)", (TEXT_STAMP,))
            for event, recording, linked in (("current", "one", True), ("legacy", "one", False), ("other-event", "other", True)):
                connection.execute(
                    "INSERT INTO duel_events (event_id,recording_id,elapsed_ms,event_type,label,source,status,created_at,updated_at) "
                    "VALUES (?,?,1000,'marker','keep if other','manual','confirmed',?,?)", (event, recording, TEXT_STAMP, TEXT_STAMP),
                )
                if linked:
                    source = current if recording == "one" else other
                    connection.execute("INSERT INTO duel_event_sources (event_id,video_asset_id,video_generation,time_basis) VALUES (?,?,?,'recording_start')", (event, source.asset_id, source.generation))
            for event, recording, status in (("candidate", "one", "candidate"), ("confirmed-marker", "one", "confirmed"), ("other-observation", "other", "confirmed")):
                connection.execute(
                    "INSERT INTO card_observations (event_id,recording_id,generation,card_name,actor,action,observed_ms,time_kind,evidence_json,source_key,status,revision,manual_locked,original_values_json,created_at,updated_at) "
                    "VALUES (?,?,0,'カード','self','activate',1000,'log_observed','{}',?,?,1,0,'{}',?,?)",
                    (event, recording, event, status, TEXT_STAMP, TEXT_STAMP),
                )
                source = current if recording == "one" else other
                connection.execute(
                    "INSERT INTO card_observation_links (event_id,video_asset_id,video_generation,time_basis,information_id) VALUES (?,?,?,'recording_start',?)",
                    (event, source.asset_id, source.generation, "use" if recording == "one" else "other-use"),
                )
        self.upload()
        self.job("queued", current)
        self.job("terminal", current, state="completed", generation=2)
        self.job("other-job", other, state="completed")
        return current, other

    def test_completed_delete_purges_only_video_evidence_and_retains_canonical_data(self) -> None:
        current, other = self.seed_evidence()
        original_export = self.root / "exports" / "one.mp4"
        original_export.parent.mkdir()
        original_export.write_bytes(b"prepared MP4")
        protected = self.repository.set_protection("one", True)
        intent = self.intent(reason="manual", allow_protected=True)
        # intent/stagingの時点ではマーカーを消さないことを確認します。
        with self.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM card_observations WHERE recording_id='one'").fetchone()[0], 2)
        record = self.unlink_and_complete(intent)
        with self.connection() as connection:
            self.assertEqual(connection.execute("SELECT event_id FROM duel_events").fetchall()[0][0], "other-event")
            self.assertEqual(connection.execute("SELECT event_id FROM card_observations").fetchall()[0][0], "other-observation")
            self.assertEqual(connection.execute("SELECT count(*) FROM duel_card_information").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT count(*) FROM duel_card_information_changes").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT card_id FROM duel_card_openers").fetchone()[0], "card")
            self.assertEqual(connection.execute("SELECT count(*) FROM duel_card_opener_changes").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM duel_record_tags").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT notes FROM duel_records WHERE duel_id='duel-one'").fetchone()[0], "keep notes")
            self.assertEqual(connection.execute("SELECT watch_url FROM youtube_uploads").fetchone()[0], "https://www.youtube.com/watch?v=video")
            queued = connection.execute("SELECT * FROM card_analysis_jobs WHERE job_id='queued'").fetchone()
            terminal = connection.execute("SELECT * FROM card_analysis_jobs WHERE job_id='terminal'").fetchone()
            other_job = connection.execute("SELECT * FROM card_analysis_jobs WHERE job_id='other-job'").fetchone()
            self.assertEqual(queued["state"], "cancelled")
            self.assertNotIn("checkpoint", json.loads(queued["result_metadata_json"]))
            self.assertEqual(terminal["state"], "completed")
            self.assertEqual(json.loads(terminal["result_metadata_json"])["audit"], "keep")
            self.assertNotIn("checkpoint", json.loads(terminal["result_metadata_json"]))
            self.assertIn("checkpoint", json.loads(other_job["result_metadata_json"]))
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.repository.get_asset(current.asset_id).state, "deleted")
        self.assertEqual(self.repository.get_asset(other.asset_id).state, "present")
        self.assertEqual(self.repository.get_protection("one"), protected)
        self.assertEqual(record.reason, "manual")
        self.assertEqual(original_export.read_bytes(), b"prepared MP4")

    def test_complete_transaction_rolls_back_every_change_if_final_journal_commit_fails(self) -> None:
        self.seed_evidence()
        intent = self.intent()
        self.repository.update_intent(intent.intent_id, "staged")
        (self.recordings / intent.original_path).unlink()
        self.repository.update_intent(intent.intent_id, "unlinked")
        with self.connection() as connection:
            connection.execute("CREATE TRIGGER completion_fault BEFORE UPDATE OF state ON video_deletion_journal WHEN NEW.state='completed' BEGIN SELECT RAISE(ABORT,'injected completion failure'); END")
        with self.assertRaises(VideoLifecycleError):
            self.repository.complete_deletion(intent.intent_id)
        with self.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM video_deletion_history").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM card_observations WHERE recording_id='one'").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT state FROM card_analysis_jobs WHERE job_id='queued'").fetchone()[0], "queued")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            connection.execute("DROP TRIGGER completion_fault")
        self.assertEqual(self.repository.get_intent(intent.intent_id).state, "unlinked")
        self.assertEqual(self.repository.complete_deletion(intent.intent_id).reason, "retention")

    def test_pending_requests_require_persisted_success_and_matching_source(self) -> None:
        self.upload(state="uploading", video_id=None, watch_url=None)
        with self.assertRaises(VideoLifecycleError):
            self.request()
        with self.connection() as connection:
            connection.execute("UPDATE youtube_uploads SET state='completed',video_id='video',watch_url=' ' WHERE upload_id='upload-one'")
        with self.assertRaises(VideoLifecycleError):
            self.request()
        with self.connection() as connection:
            connection.execute("UPDATE youtube_uploads SET watch_url='https://www.youtube.com/watch?v=video' WHERE upload_id='upload-one'")
        for changes in ({"prepare_queue_id": "different"}, {"generation": 2}):
            with self.subTest(changes=changes), self.assertRaises(VideoLifecycleError):
                self.request(**changes)
        self.repository.set_protection("one", True)
        with self.assertRaises(VideoLifecycleError):
            self.request()
        self.repository.set_protection("one", False)
        first = self.request()
        second = self.request(skip_reason="backup_busy")
        self.assertEqual(first.request_id, second.request_id)
        self.assertEqual(second.last_skip_reason, "backup_busy")
        self.assertEqual(len(self.repository.pending_cleanup_requests()), 1)
        with self.assertRaises(VideoLifecycleError):
            self.request(provenance={"source": "different"})

    def test_cleanup_request_attempts_and_terminal_state_are_durable(self) -> None:
        self.upload()
        request = self.request()
        updated = self.repository.update_cleanup_request(request.request_id, skip_reason="still_busy", increment_attempts=True)
        self.assertEqual(updated.attempts, 1)
        self.assertIsNotNone(updated.last_attempt_at)
        again = VideoLifecycleRepository(database_path=self.database, recordings_root=self.recordings)
        self.assertEqual(again.pending_cleanup_requests()[0], updated)
        finished = again.update_cleanup_request(request.request_id, state="completed")
        self.assertEqual(finished.provenance, updated.provenance)
        self.assertEqual(again.pending_cleanup_requests(), ())
        self.assertEqual(again.request_cleanup(
            recording_id=request.recording_id, asset_id=request.asset_id, generation=request.generation,
            upload_id=request.upload_id, prepare_queue_id=request.prepare_queue_id, provenance=request.provenance,
            skip_reason="duplicate callback",
        ), finished)
        with self.assertRaises(VideoLifecycleError):
            again.update_cleanup_request(request.request_id, state="pending")
        self.assertEqual(again.update_cleanup_request(request.request_id, state="completed", increment_attempts=True), finished)

    def test_intent_and_retry_requests_are_not_both_created_for_same_operation(self) -> None:
        self.upload()
        intent = self.intent(reason="youtube_uploaded", upload_id="upload-one", prepare_queue_id="queue")
        with self.assertRaises(VideoLifecycleError):
            self.request()
        with self.assertRaises(VideoLifecycleError):
            self.repository.register_use(asset_id=intent.asset_id, owner_id="late-review", purpose="playback")

    def test_leases_expire_only_when_process_is_confirmed_dead(self) -> None:
        asset = self.verified()
        alive = self.repository.register_use(asset_id=asset.asset_id, owner_id="alive", purpose="playback", process_id=123)
        unknown = self.repository.register_use(asset_id=asset.asset_id, owner_id="unknown", purpose="export", process_id=0)
        dead = self.repository.register_use(asset_id=asset.asset_id, owner_id="dead", purpose="analysis", process_id=456)
        with self.connection() as connection:
            connection.execute("UPDATE video_asset_uses SET acquired_at='2000-01-01T00:00:00+00:00'")
        with patch("master_duel_recorder_lite.video_lifecycle._process_alive", side_effect=lambda pid: {123: True, 0: None, 456: False}[pid]):
            self.assertEqual(len(self.repository.list_uses(asset.asset_id)), 3)
            remaining = self.repository.list_uses(asset.asset_id, discard_dead=True)
        self.assertEqual({use.use_id for use in remaining}, {alive.use_id, unknown.use_id})
        self.assertFalse(self.repository.release_use(dead.use_id))
        with self.assertRaises(VideoLifecycleError):
            self.repository.release_use(alive.use_id, owner_id="other-owner")
        self.assertTrue(self.repository.release_use(alive.use_id))
        self.assertTrue(self.repository.release_use(unknown.use_id))
        self.assertTrue(_process_alive(os.getpid()))
        self.assertIsNone(_process_alive(0))

    def test_os_operation_lock_blocks_new_lease_and_allows_existing_lease_release(self) -> None:
        asset = self.verified()
        use = self.repository.register_use(asset_id=asset.asset_id, owner_id="review", purpose="playback")
        with RecordingLock.acquire(self.repository.operation_lock_path, recording_id="deletion"):
            with self.assertRaises(VideoLifecycleError):
                self.repository.register_use(asset_id=asset.asset_id, owner_id="export", purpose="prepare")
            self.assertTrue(self.repository.release_use(use.use_id))
        self.assertEqual(self.repository.list_uses(asset.asset_id), ())
        self.assertTrue((self.recordings / asset.path).exists())

    def test_intent_prunes_dead_use_but_preserves_unknown_use(self) -> None:
        asset = self.verified()
        unknown = self.repository.register_use(asset_id=asset.asset_id, owner_id="unknown", purpose="playback", process_id=456)
        with patch("master_duel_recorder_lite.video_lifecycle._process_alive", return_value=None):
            with self.assertRaises(VideoLifecycleError):
                self.intent()
        self.assertEqual(self.repository.list_uses(asset.asset_id)[0].use_id, unknown.use_id)
        self.assertEqual(self.repository.pending_intents(), ())
        with patch("master_duel_recorder_lite.video_lifecycle._process_alive", return_value=False):
            self.assertEqual(self.intent().state, "intent")

    def test_markers_from_another_asset_generation_are_retained(self) -> None:
        asset = self.verified()
        with self.connection() as connection:
            connection.execute("UPDATE recording_video_assets SET generation=2 WHERE asset_id=?", (asset.asset_id,))
            connection.execute(
                "INSERT INTO recording_video_assets (asset_id,recording_id,generation,is_current,path,state,created_at,updated_at) "
                "VALUES ('previous-asset','one',1,0,'previous.mkv','unverified',?,?)", (TEXT_STAMP, TEXT_STAMP),
            )
            for event, source_asset, generation in (("previous-event", "previous-asset", 1), ("current-event", asset.asset_id, 2)):
                connection.execute(
                    "INSERT INTO duel_events (event_id,recording_id,elapsed_ms,event_type,source,status,created_at,updated_at) "
                    "VALUES (?,'one',1000,'marker','manual','confirmed',?,?)", (event, TEXT_STAMP, TEXT_STAMP),
                )
                connection.execute("INSERT INTO duel_event_sources (event_id,video_asset_id,video_generation,time_basis) VALUES (?,?,?,'recording_start')", (event, source_asset, generation))
                connection.execute(
                    "INSERT INTO card_observations (event_id,recording_id,generation,card_name,actor,action,observed_ms,time_kind,evidence_json,source_key,status,revision,manual_locked,original_values_json,created_at,updated_at) "
                    "VALUES (?,'one',0,'カード','self','activate',1000,'log_observed','{}',?,'candidate',1,0,'{}',?,?)",
                    (event, event, TEXT_STAMP, TEXT_STAMP),
                )
                connection.execute("INSERT INTO card_observation_links (event_id,video_asset_id,video_generation,time_basis) VALUES (?,?,?,'recording_start')", (event, source_asset, generation))
        self.unlink_and_complete(self.intent())
        with self.connection() as connection:
            self.assertEqual([row[0] for row in connection.execute("SELECT event_id FROM duel_events")], ["previous-event"])
            self.assertEqual([row[0] for row in connection.execute("SELECT event_id FROM card_observations")], ["previous-event"])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_failed_recording_requires_known_terminal_time(self) -> None:
        self.verified()
        with self.connection() as connection:
            connection.execute("UPDATE recordings SET state='failed',ended_at=NULL WHERE recording_id='one'")
        with self.assertRaises(VideoLifecycleError):
            self.intent()
        with self.connection() as connection:
            connection.execute("UPDATE recordings SET ended_at='invalid' WHERE recording_id='one'")
        with self.assertRaises(VideoLifecycleError):
            self.intent()
        with self.connection() as connection:
            connection.execute("UPDATE recordings SET ended_at=? WHERE recording_id='one'", (TEXT_STAMP,))
        self.assertEqual(self.intent().state, "intent")

    def test_corrupt_persisted_time_or_provenance_is_rejected(self) -> None:
        self.upload()
        request = self.request()
        with self.connection() as connection:
            connection.execute("UPDATE upload_cleanup_requests SET created_at='2026-10-01T00:00:00' WHERE request_id=?", (request.request_id,))
        with self.assertRaises(VideoLifecycleError):
            self.repository.pending_cleanup_requests()
        with self.connection() as connection:
            connection.execute("UPDATE upload_cleanup_requests SET created_at=?,provenance_json=? WHERE request_id=?", (TEXT_STAMP, '{"source":1,"source":2}', request.request_id))
        with self.assertRaises(VideoLifecycleError):
            self.repository.get_cleanup_request(request.request_id)
        self.assertTrue((self.recordings / "one.mkv").exists())

    def test_terminal_request_duplicate_after_deletion_does_not_reopen_it(self) -> None:
        self.upload()
        request = self.request()
        self.unlink_and_complete(self.intent())
        terminal = self.repository.update_cleanup_request(request.request_id, state="completed")
        duplicate = self.request()
        self.assertEqual(duplicate, terminal)
        self.assertEqual(self.repository.pending_cleanup_requests(), ())

    def test_diagnostic_detects_changed_current_file_without_inventing_deletion(self) -> None:
        asset = self.verified()
        (self.recordings / asset.path).write_bytes(b"a changed video")
        state = self.repository.get_recording_video_state("one")
        self.assertEqual(state.state, "unverified")
        self.assertIsNone(state.reason)
        self.assertEqual(self.repository.get_asset(asset.asset_id).state, "present")

    def test_missing_video_has_no_invented_cause_and_unavailable_root_is_distinct(self) -> None:
        asset = self.repository.ensure_current_asset("one")
        (self.recordings / asset.path).unlink()
        missing = self.repository.get_recording_video_state("one")
        self.assertEqual(missing.state, "missing")
        self.assertIsNone(missing.reason)
        self.assertFalse(missing.cause_known)
        self.assertIsNone(self.repository.deletion_for_asset(asset.asset_id))
        self.recordings.rmdir()
        unavailable = self.repository.get_recording_video_state("one")
        self.assertEqual(unavailable.state, "unavailable")
        self.assertEqual(unavailable.message, "確認不可")
        self.assertIsNone(unavailable.reason)

    def test_deleted_diagnostic_uses_only_persisted_deletion_reason(self) -> None:
        intent = self.intent(reason="capacity")
        record = self.unlink_and_complete(intent)
        state = self.repository.get_recording_video_state("one")
        self.assertEqual((state.state, state.reason, state.cause_known), ("deleted", "capacity", True))
        self.assertEqual(state.deletion, record)
        self.assertEqual(state.message, "保存容量超過")
        self.assertFalse(state.pending_intent)

    def test_invalid_inputs_or_corrupt_metadata_do_not_start_or_complete_deletion(self) -> None:
        for changes in (
            {"generation": True}, {"size_bytes": -1}, {"mtime_ns": True}, {"sha256": "x" * 64},
            {"original_path": "../outside.mkv"}, {"staged_path": "C:/outside.mkv"},
            {"staged_path": "sub/video:stream"}, {"reason": "restored_deleted"},
            {"metadata": []}, {"metadata": {"nan": float("nan")}}, {"metadata": {1: "bad key"}},
        ):
            with self.subTest(changes=changes), self.assertRaises(VideoLifecycleError):
                self.intent(**changes)
        self.assertEqual(self.repository.pending_intents(), ())
        self.assertTrue((self.recordings / "one.mkv").exists())
        intent = self.intent()
        with self.connection() as connection:
            connection.execute("UPDATE video_deletion_journal SET metadata_json='[]' WHERE intent_id=?", (intent.intent_id,))
        with self.assertRaises(VideoLifecycleError):
            self.repository.get_intent(intent.intent_id)
        with self.connection() as connection:
            self.assertEqual(connection.execute("SELECT state FROM video_deletion_journal WHERE intent_id=?", (intent.intent_id,)).fetchone()[0], "intent")


if __name__ == "__main__":
    unittest.main()
