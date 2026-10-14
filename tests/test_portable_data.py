from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.card_analysis_models import CardInputFingerprint, CardObservationValues, normalize_card_name
from master_duel_recorder_lite.card_analysis_repository import CardAnalysisConflictError, CardAnalysisRepository
from master_duel_recorder_lite.duel_card_repository import DuelCardRepository
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.history_database import connect_history_database, initialize_history_database
from master_duel_recorder_lite.portable_data import (
    FORBIDDEN_TABLES, LEGACY_SCHEMA, LEGACY_TABLES, PORTABLE_SCHEMA, PortableDataError,
    PortableImportPlan, _insert_portable_rows, get_portable_export, materialize_portable_plan, remap_portable_card_ids,
    validate_portable_document,
)
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.video_lifecycle import VideoLifecycleRepository


STAMP = "2026-10-01T00:00:00+00:00"


class PortableDataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = default_runtime_paths(user_data_dir=Path(self.temp.name) / "source")
        ensure_runtime_dirs(self.paths)
        self.database = self.paths.db / "history.sqlite3"
        self.history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        self.analysis = CardAnalysisRepository.from_runtime_paths(self.paths)
        self.cards = DuelCardRepository.from_runtime_paths(self.paths)
        self.duels = DuelRecordRepository.from_runtime_paths(self.paths)
        self.lifecycle = VideoLifecycleRepository.from_runtime_paths(self.paths)
        self.video = self.paths.recordings / "duel.mkv"
        self.video.write_bytes(b"portable fixture video")
        self._recording("video", self.video)
        self.duels.save("video", DuelRecordValues(status="confirmed", result="win"), expected_revision=0)
        self.duel_id = self.duels.get("video").duel_id
        self.target = self.analysis.save_target(name="使用カード")
        self.absent = self.analysis.save_target(name="未使用カード")
        self.fingerprint = CardInputFingerprint(
            str(self.video), self.video.stat().st_size, self.video.stat().st_mtime_ns,
            hashlib.sha256(self.video.read_bytes()).hexdigest(),
        )
        job = self.analysis.create_job("video", fingerprint=self.fingerprint, analyzer_version="fixture")
        self.analysis.claim_job(job.job_id)
        values = CardObservationValues(self.target.target_id, self.target.name, "self", "activate", 1000,
                                       evidence={"supports_use": True}, source_key="fixture")
        self.analysis.complete_job(job.job_id, (values,))
        self.marker = self.analysis.list_observations("video")[0]
        self.analysis.review_observation(self.marker.event_id, expected_revision=1, status="confirmed", is_opener=True,
                                          information_expected_revision=0, opener_expected_revision=0)
        self.analysis.set_nonuse("video", self.absent.target_id, "self")
        self.asset = self.lifecycle.get_current_asset("video")
        self.lifecycle.set_protection("video", True, expected_revision=0)
        self.manual = self.duels.create_manual(DuelRecordValues(status="confirmed", result="loss"),
                                              occurred_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.cards.save_information(self.manual.duel_id, self.target.card_id, "self", "used", expected_revision=0,
                                    is_opener=True, opener_expected_revision=0)
        deleted = self.paths.recordings / "deleted.mkv"
        self._recording("deleted", deleted)
        old_asset = self.lifecycle.ensure_current_asset("deleted")
        with closing(connect_history_database(self.database)) as connection, connection:
            connection.execute(
                "INSERT INTO duel_events "
                "(event_id,recording_id,elapsed_ms,event_type,label,source,status,created_at,updated_at) "
                "VALUES ('general','video',1500,'marker','一般マーカー','manual','confirmed',?,?)", (STAMP, STAMP),
            )
            connection.execute(
                "INSERT INTO duel_event_sources VALUES ('general',?,1,'recording_start',?,1)",
                (self.asset.asset_id, json.dumps(self.fingerprint.to_dict())),
            )
            for upload_id, recording_id, video_id in (("uploaded", "video", "portable1"), ("deleted-upload", "deleted", "portable2")):
                connection.execute(
                    "INSERT INTO youtube_uploads "
                    "(upload_id,recording_id,prepare_queue_id,state,privacy,title,video_id,watch_url,created_at,updated_at) "
                    "VALUES (?,?,?,'completed','private','投稿済み',?,?,?,?)",
                    (upload_id, recording_id, f"queue-{upload_id}", video_id,
                     f"https://www.youtube.com/watch?v={video_id}", STAMP, STAMP),
                )
            connection.execute("UPDATE recording_video_assets SET state='deleted' WHERE asset_id=?", (old_asset.asset_id,))
            connection.execute(
                "INSERT INTO video_deletion_history "
                "(deletion_id,recording_id,asset_id,generation,reason,deleted_at,original_path,size_bytes,sha256,"
                "cause_known,upload_id,prepare_queue_id,metadata_json) "
                "VALUES ('deletion','deleted',?,1,'youtube_uploaded',?,'deleted.mkv',10,?,1,'deleted-upload','queue-deleted','{}')",
                (old_asset.asset_id, STAMP, "a" * 64),
            )

    def _recording(self, identifier: str, path: Path) -> None:
        self.history.register_starting(recording_id=identifier, output_path=path, container="mkv", source="manual",
                                       created_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
        with closing(connect_history_database(self.database)) as connection, connection:
            connection.execute("UPDATE recordings SET state='completed',duration_seconds=60,size_bytes=? WHERE recording_id=?",
                               (path.stat().st_size if path.exists() else 10, identifier))

    def payload(self) -> dict[str, object]:
        return get_portable_export(self.database, self.paths.recordings)

    def destination(self, name: str = "target") -> Path:
        return Path(self.temp.name) / name / "db" / "history.sqlite3"

    def test_round_trip_preserves_semantics_urls_markers_reason_time_and_protection(self) -> None:
        payload = self.payload()
        self.assertEqual(payload["schema"], PORTABLE_SCHEMA)
        plan = validate_portable_document(payload)
        target = self.destination()
        root = target.parent.parent / "recordings"
        materialize_portable_plan(plan, target, root)
        round_trip = get_portable_export(target, root)
        self.assertEqual(round_trip["tables"], payload["tables"])
        self.assertEqual(len(plan.tables["duel_card_information"]), 3)
        self.assertEqual(len(plan.tables["duel_card_openers"]), 2)
        self.assertEqual(len(plan.tables["youtube_uploads"]), 2)
        self.assertEqual(plan.tables["video_deletion_history"][0]["reason"], "youtube_uploaded")
        self.assertEqual(plan.tables["video_deletion_history"][0]["deleted_at"], STAMP)
        self.assertTrue(plan.tables["recording_video_protection"][0]["protected"])
        with closing(connect_history_database(target)) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("SELECT count(*) FROM video_deletion_journal").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM upload_cleanup_requests").fetchone()[0], 0)
        self.assertFalse(root.exists())
        self.assertEqual(self.video.read_bytes(), b"portable fixture video")

    def test_imported_markers_remain_unverified_even_with_identical_local_video(self) -> None:
        target = self.destination()
        root = target.parent.parent / "recordings"
        root.mkdir(parents=True)
        (root / "duel.mkv").write_bytes(self.video.read_bytes())
        plan = validate_portable_document(self.payload())
        materialize_portable_plan(plan, target, root)
        with closing(connect_history_database(target)) as connection:
            self.assertEqual(connection.execute("SELECT verified FROM card_observation_links").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT verified FROM duel_event_sources").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT state FROM recording_video_assets WHERE asset_id=?", (self.asset.asset_id,)).fetchone()[0], "unverified")
        analysis = CardAnalysisRepository(target)
        with self.assertRaises(CardAnalysisConflictError):
            analysis.review_observation(self.marker.event_id, expected_revision=2, status="confirmed")
        self.assertEqual(DuelCardRepository(target).get_review(self.duel_id).openers[0].status, "confirmed")

    def test_absent_or_different_video_cannot_change_semantic_information_or_authorize_markers(self) -> None:
        for different in (False, True):
            with self.subTest(different=different):
                target = self.destination(f"different-{different}")
                root = target.parent.parent / "recordings"
                if different:
                    root.mkdir(parents=True)
                    (root / "duel.mkv").write_bytes(b"another video")
                materialize_portable_plan(validate_portable_document(self.payload()), target, root)
                review = DuelCardRepository(target).get_review(self.duel_id)
                self.assertEqual({row.state for row in review.information}, {"used", "not_used"})
                with self.assertRaises(CardAnalysisConflictError):
                    CardAnalysisRepository(target).review_observation(self.marker.event_id, expected_revision=2, status="confirmed")

    def test_video_free_manual_battle_round_trips_without_any_video_requirement(self) -> None:
        payload = self.payload()
        tables = payload["tables"]
        tables["recordings"] = []
        for table in ("recording_video_assets", "recording_video_protection", "duel_events", "duel_event_sources",
                      "card_observations", "card_observation_links", "youtube_uploads", "video_deletion_history"):
            tables[table] = []
        tables["duel_records"] = [row for row in tables["duel_records"] if row["duel_id"] == self.manual.duel_id]
        for table in ("duel_card_information", "duel_card_openers", "duel_card_opener_changes", "duel_record_changes"):
            tables[table] = [row for row in tables[table] if row["duel_id"] == self.manual.duel_id]
        ids = {row["information_id"] for row in tables["duel_card_information"]}
        tables["duel_card_information_changes"] = [row for row in tables["duel_card_information_changes"] if row["information_id"] in ids]
        materialize_portable_plan(validate_portable_document(payload), self.destination(), Path(self.temp.name) / "absent-videos")
        self.assertEqual(DuelCardRepository(self.destination()).get_review(self.manual.duel_id).information[0].state, "used")

    def test_legacy_json_defaults_have_no_inferred_cards_posts_or_deletion_reasons(self) -> None:
        modern = self.payload()
        payload = {"schema": LEGACY_SCHEMA, "tables": {table: modern["tables"][table] for table in LEGACY_TABLES}}
        payload["tables"].pop("saved_duel_filters")
        for row in payload["tables"]["duel_records"]:
            row.pop("coin_face")
            row["coin_toss_outcome"] = "win"
        plan = validate_portable_document(payload)
        self.assertEqual(plan.source_schema, LEGACY_SCHEMA)
        for table in ("duel_card_information", "duel_card_openers", "youtube_uploads", "video_deletion_history"):
            self.assertEqual(plan.tables[table], [])
        self.assertTrue(all(row["coin_face"] == "unknown" for row in plan.tables["duel_records"]))
        self.assertTrue(all(row["state"] == "unverified" for row in plan.tables["recording_video_assets"]))
        self.assertTrue(all(row["protected"] == 0 for row in plan.tables["recording_video_protection"]))
        materialize_portable_plan(plan, self.destination(), Path(self.temp.name) / "recordings")

    def test_legacy_recording_identity_and_legacy_related_rows_are_normalized(self) -> None:
        modern = self.payload()
        tables = {table: modern["tables"][table] for table in LEGACY_TABLES}
        old = next(row for row in tables["duel_records"] if row["recording_id"] == "video")
        tables["duel_records"] = [old]
        identifier = old.pop("duel_id")
        old.pop("entry_origin")
        old.pop("occurred_at")
        tables["duel_record_changes"] = [row for row in tables["duel_record_changes"] if row["duel_id"] == identifier]
        for row in tables["duel_record_changes"]:
            row.pop("duel_id")
            row["recording_id"] = "video"
        plan = validate_portable_document({"schema": LEGACY_SCHEMA, "tables": tables})
        self.assertEqual(plan.tables["duel_records"][0]["duel_id"], "video")
        self.assertEqual(plan.tables["duel_record_changes"][0]["duel_id"], "video")

    def test_forbidden_tables_and_active_upload_states_are_rejected(self) -> None:
        for table in FORBIDDEN_TABLES:
            with self.subTest(table=table):
                payload = self.payload()
                payload["tables"][table] = []
                with self.assertRaises(PortableDataError):
                    validate_portable_document(payload)
        for state in ("waiting", "preparing", "uploading", "failed", "cancelled"):
            with self.subTest(state=state):
                payload = self.payload()
                payload["tables"]["youtube_uploads"][0]["state"] = state
                with self.assertRaises(PortableDataError):
                    validate_portable_document(payload)

    def test_nested_credentials_checkpoint_and_job_references_are_rejected(self) -> None:
        for key in ("refresh_token", "access_token", "client_secret", "checkpoint"):
            with self.subTest(key=key):
                payload = self.payload()
                payload["tables"]["card_observations"][0]["evidence_json"] = json.dumps({"nested": {key: "forbidden"}})
                with self.assertRaises(PortableDataError):
                    validate_portable_document(payload)
        payload = self.payload()
        payload["tables"]["card_observations"][0]["job_id"] = "old-job"
        with self.assertRaises(PortableDataError):
            validate_portable_document(payload)

    def test_broken_references_duplicates_and_type_mismatches_are_rejected(self) -> None:
        for problem in ("card", "duel", "asset", "duplicate", "revision", "time"):
            with self.subTest(problem=problem):
                payload = self.payload()
                tables = payload["tables"]
                if problem == "card":
                    tables["duel_card_information"][0]["card_id"] = "missing-card"
                elif problem == "duel":
                    tables["duel_card_information"][0]["duel_id"] = "missing-duel"
                elif problem == "asset":
                    tables["card_observation_links"][0]["video_asset_id"] = "missing-asset"
                elif problem == "duplicate":
                    tables["duel_card_information"].append(dict(tables["duel_card_information"][0]))
                elif problem == "revision":
                    tables["duel_card_information"][0]["revision"] = 1.5
                else:
                    tables["duel_card_information"][0]["created_at"] = "2026-10-01T00:00:00"
                with self.assertRaises(PortableDataError):
                    validate_portable_document(payload)

    def test_conflicting_opener_wrong_recording_generation_or_time_basis_are_rejected(self) -> None:
        for problem in ("opener", "recording", "generation", "time_basis"):
            with self.subTest(problem=problem):
                payload = self.payload()
                tables = payload["tables"]
                if problem == "opener":
                    selected = next(row for row in tables["duel_card_information"] if row["state"] == "used")
                    selected["state"] = "not_used"
                else:
                    source = tables["card_observation_links"][0]
                    if problem == "recording":
                        source["video_asset_id"] = next(row["asset_id"] for row in tables["recording_video_assets"] if row["recording_id"] == "deleted")
                    elif problem == "generation":
                        source["video_generation"] = 2
                    else:
                        source["time_basis"] = "trimmed_start"
                with self.assertRaises(PortableDataError):
                    validate_portable_document(payload)

    def test_absolute_or_escaping_paths_and_mismatched_post_url_are_rejected(self) -> None:
        for invalid in ("../escape.mkv", "C:/outside.mkv", "/outside.mkv", "safe/name:stream", "\\\\server\\outside.mkv"):
            with self.subTest(path=invalid):
                payload = self.payload()
                payload["tables"]["recording_video_assets"][0]["path"] = invalid
                with self.assertRaises(PortableDataError):
                    validate_portable_document(payload)
        payload = self.payload()
        payload["tables"]["youtube_uploads"][0]["watch_url"] = "https://www.youtube.com/watch?v=another-id"
        with self.assertRaises(PortableDataError):
            validate_portable_document(payload)

    def test_absolute_input_fingerprint_becomes_recordings_relative_without_video_reads(self) -> None:
        with patch.object(Path, "read_bytes", side_effect=AssertionError("video should not be read")), \
                patch.object(Path, "unlink", side_effect=AssertionError("video should not be removed")):
            payload = self.payload()
            validate_portable_document(payload)
        source = payload["tables"]["card_observation_links"][0]
        self.assertEqual(json.loads(source["input_fingerprint_json"])["path"], "duel.mkv")
        self.assertEqual(json.loads(source["input_fingerprint_json"])["sha256"], self.fingerprint.sha256)

    def test_general_marker_keeps_timeline_identity_and_capture_proof_without_authorizing_playback(self) -> None:
        for capture in (False, True):
            with self.subTest(capture=capture):
                payload = self.payload()
                source = payload["tables"]["duel_event_sources"][0]
                fingerprint = json.loads(source["input_fingerprint_json"])
                fingerprint.update(schema="mdrl-timeline-capture-v1" if capture else "mdrl-timeline-video-v1",
                                   recording_id="video", asset_id=self.asset.asset_id, generation=1,
                                   time_basis="recording_start")
                if capture:
                    fingerprint.update(session_id="session", device=0, inode=0, sha256="")
                source["input_fingerprint_json"] = json.dumps(fingerprint)
                plan = validate_portable_document(payload)
                restored = plan.tables["duel_event_sources"][0]
                if capture:
                    fingerprint["verification_origin"] = "portable_import"
                self.assertEqual(json.loads(restored["input_fingerprint_json"]), fingerprint)
                self.assertEqual(restored["verified"], 0)

    def test_shared_card_id_collision_remaps_all_references_without_overwriting_existing_card(self) -> None:
        plan = validate_portable_document(self.payload())
        existing = [{"card_id": self.target.card_id, "name": "別のカード", "normalized_name": normalize_card_name("別のカード")}]
        remapped = remap_portable_card_ids(plan, existing)
        new_id = remapped.id_maps["card_id"][self.target.card_id]
        self.assertNotEqual(new_id, self.target.card_id)
        self.assertEqual(remap_portable_card_ids(plan, existing).id_maps, remapped.id_maps)
        self.assertTrue(any(row["card_id"] == new_id for row in remapped.tables["duel_card_information"]))
        self.assertTrue(all(row["card_id"] == new_id for row in remapped.tables["duel_card_openers"]))
        change = next(row for row in remapped.tables["duel_card_information_changes"] if json.loads(row["after_json"]).get("card_id") == new_id)
        self.assertEqual(json.loads(change["after_json"])["card_id"], new_id)
        self.assertEqual(existing[0]["name"], "別のカード")
        materialize_portable_plan(remapped, self.destination(), Path(self.temp.name) / "missing-videos")

    def test_equal_card_identity_uses_existing_id_and_target_id_collision_is_remapped(self) -> None:
        plan = validate_portable_document(self.payload())
        existing_card = {"card_id": "existing-card", "name": self.target.name,
                         "normalized_name": normalize_card_name(self.target.name)}
        existing_target = {"target_id": self.target.target_id, "name": "別の設定",
                           "normalized_name": normalize_card_name("別の設定")}
        remapped = remap_portable_card_ids(plan, [existing_card], [existing_target])
        self.assertEqual(remapped.id_maps["card_id"][self.target.card_id], "existing-card")
        self.assertNotEqual(remapped.id_maps["target_id"][self.target.target_id], self.target.target_id)
        marker = remapped.tables["card_observations"][0]
        self.assertEqual(marker["target_id"], remapped.id_maps["target_id"][self.target.target_id])
        materialize_portable_plan(remapped, self.destination(), Path(self.temp.name) / "missing-videos")

    def test_existing_destination_is_refused_before_database_bytes_are_modified(self) -> None:
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with self.assertRaisesRegex(PortableDataError, "隔離DB"):
            materialize_portable_plan(validate_portable_document(self.payload()), self.database, self.paths.recordings)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)

    def test_invalid_mutated_plan_is_rejected_before_creating_destination_database(self) -> None:
        plan = validate_portable_document(self.payload())
        plan.tables["duel_card_information"][0]["card_id"] = "missing"
        with self.assertRaises(PortableDataError):
            materialize_portable_plan(plan, self.destination(), Path(self.temp.name) / "recordings")
        self.assertFalse(self.destination().exists())

    def test_materialization_failure_rolls_back_every_imported_row(self) -> None:
        plan = validate_portable_document(self.payload())

        def fail_destination_insert(connection, tables):
            if connection.execute("PRAGMA database_list").fetchone()[2] == "":
                return _insert_portable_rows(connection, tables)
            row = tables["card_identities"][0]
            connection.execute("INSERT INTO card_identities VALUES (?,?,?,?,?)", tuple(row[key] for key in (
                "card_id", "name", "normalized_name", "created_at", "updated_at",
            )))
            raise sqlite3.OperationalError("injected materialization failure")

        with patch("master_duel_recorder_lite.portable_data._insert_portable_rows", side_effect=fail_destination_insert):
            with self.assertRaisesRegex(PortableDataError, "materialization failure"):
                materialize_portable_plan(plan, self.destination(), Path(self.temp.name) / "recordings")
        with closing(connect_history_database(self.destination())) as connection:
            for table in plan.tables:
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_original_recording_outcome_is_kept_without_restarting_recording(self) -> None:
        payload = self.payload()
        payload["tables"]["recordings"][0]["state"] = "recording"
        plan = validate_portable_document(payload)
        self.assertEqual(plan.tables["recordings"][0]["state"], "recording")
        materialize_portable_plan(plan, self.destination(), Path(self.temp.name) / "recordings")
        with closing(connect_history_database(self.destination())) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM card_analysis_jobs").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM video_asset_uses").fetchone()[0], 0)

    def test_synthetic_execution_rows_are_omitted_and_never_recreated_from_completed_information(self) -> None:
        with closing(connect_history_database(self.database)) as connection, connection:
            connection.execute(
                "INSERT INTO youtube_uploads (upload_id,recording_id,state,privacy,title,created_at,updated_at) "
                "VALUES ('waiting','video','waiting','private','not portable',?,?)", (STAMP, STAMP),
            )
            connection.execute(
                "INSERT INTO upload_cleanup_requests "
                "(request_id,recording_id,asset_id,generation,upload_id,state,provenance_json,created_at,updated_at) "
                "VALUES ('retry','video',?,1,'uploaded','pending','{}',?,?)", (self.asset.asset_id, STAMP, STAMP),
            )
        payload = self.payload()
        self.assertTrue(FORBIDDEN_TABLES.isdisjoint(payload["tables"]))
        self.assertTrue(all(row["state"] == "completed" and row["prepare_queue_id"] is None for row in payload["tables"]["youtube_uploads"]))
        self.assertTrue(all(row["job_id"] is None for row in payload["tables"]["card_observations"]))
        materialize_portable_plan(validate_portable_document(payload), self.destination(), Path(self.temp.name) / "videos")
        with closing(connect_history_database(self.destination())) as connection:
            for table in ("card_analysis_jobs", "card_analysis_job_sources", "video_deletion_journal", "upload_cleanup_requests", "video_asset_uses"):
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_empty_current_schema_can_be_materialized_and_unknown_preexisting_schema_is_refused(self) -> None:
        target = self.destination()
        initialize_history_database(target)
        plan = validate_portable_document(self.payload())
        returned = materialize_portable_plan(plan, target, Path(self.temp.name) / "recordings")
        self.assertEqual(returned.row_count, plan.row_count)
        strange = self.destination("strange")
        strange.parent.mkdir(parents=True)
        with closing(sqlite3.connect(strange)) as connection, connection:
            connection.execute("CREATE TABLE other_data (value TEXT)")
        with self.assertRaises(PortableDataError):
            materialize_portable_plan(PortableImportPlan(PORTABLE_SCHEMA, plan.tables), strange, Path(self.temp.name) / "recordings")


if __name__ == "__main__":
    unittest.main()
