from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.config import AppConfig, GIB_BYTES
from master_duel_recorder_lite.data_protection import DataProtectionService
from master_duel_recorder_lite.duel_card_repository import DuelCardRepository
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.duel_timeline import DuelTimelineRepository
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_retention import RecordingDeletionService, STAGING_DIRECTORY
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.startup_maintenance import run_startup_maintenance
from master_duel_recorder_lite.video_lifecycle import VideoLifecycleRepository
from master_duel_recorder_lite.video_file_usage import FileUseStatus


NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


class RecordingRetentionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = default_runtime_paths(user_data_dir=Path(self.temporary.name))
        ensure_runtime_dirs(self.paths)
        self.history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        self.lifecycle = VideoLifecycleRepository.from_runtime_paths(self.paths)
        self.settings = AppConfig(recording_retention_days=1)
        self.service = RecordingDeletionService(
            self.paths, repository=self.lifecycle,
            config_loader=lambda: self.settings, now=lambda: NOW,
        )

    def recording(self, identifier: str, *, stamp: datetime | None = None, content: bytes = b"video") -> Path:
        stamp = stamp or NOW - timedelta(days=2)
        path = self.paths.recordings / f"{identifier}.mkv"
        path.write_bytes(content)
        self.history.register_starting(
            recording_id=identifier, output_path=path, container="mkv", source="manual", created_at=stamp,
        )
        with closing(sqlite3.connect(self.lifecycle.database_path)) as connection, connection:
            connection.execute(
                "UPDATE recordings SET state='completed',started_at=?,ended_at=?,size_bytes=?,duration_seconds=10 WHERE recording_id=?",
                (stamp.isoformat(), (stamp + timedelta(seconds=10)).isoformat(), len(content), identifier),
            )
        return path

    def seed_meaning_and_marker(self, identifier: str) -> tuple[str, str]:
        duel = DuelRecordRepository.from_runtime_paths(self.paths).save(
            identifier, DuelRecordValues(status="confirmed", result="win"), expected_revision=0,
        )
        cards = DuelCardRepository.from_runtime_paths(self.paths)
        card = cards.save_identity(name="Test card")
        cards.save_information(duel.duel_id, card.card_id, "self", "used", is_opener=True)
        marker = DuelTimelineRepository.from_runtime_paths(self.paths).add(
            identifier, elapsed_ms=100, event_type="marker", label="before deletion",
        )
        return duel.duel_id, marker.event_id

    def assert_information_and_marker_retained(self, identifier, duel_id, marker_id, before):
        self.assertEqual(DuelCardRepository.from_runtime_paths(self.paths).get_review(duel_id), before)
        self.assertIsNotNone(DuelTimelineRepository.from_runtime_paths(self.paths).get(marker_id))
        asset = self.lifecycle.get_current_asset(identifier)
        self.assertIsNone(self.lifecycle.deletion_for_asset(asset.asset_id))
        self.assertNotEqual(asset.state, "deleted")

    def assert_pending_recovery_keeps_operations_blocked(self):
        other = self.recording("other-safe-video")
        self.service._fault_hook = lambda _phase, _identifier: None
        result = run_startup_maintenance(self.paths, deletion_service=self.service)
        self.assertFalse(result.operation_allowed)
        self.assertIsNone(result.retention)
        self.assertEqual(result.upload_retries, ())
        self.assertTrue(self.lifecycle.pending_intents())
        self.assertTrue(other.exists())
        self.assertEqual(self.service.delete_video("other-safe-video").reason, "recovery_pending")

    def test_video_only_delete_retains_duel_facts_and_opener_removes_marker(self) -> None:
        path = self.recording("one")
        duel_id, marker_id = self.seed_meaning_and_marker("one")
        export = self.paths.exports / "prepared.mp4"
        export.write_bytes(b"prepared")
        before = DuelCardRepository.from_runtime_paths(self.paths).get_review(duel_id)
        result = self.service.delete_video("one", reason="manual")
        self.assertEqual(result.status, "deleted", result.message)
        self.assertFalse(path.exists())
        self.assertTrue(export.is_file())
        self.assertIsNotNone(self.history.get("one"))
        self.assertEqual(DuelCardRepository.from_runtime_paths(self.paths).get_review(duel_id), before)
        self.assertNotIn(marker_id, {event.event_id for event in DuelTimelineRepository.from_runtime_paths(self.paths).list("one")})
        previous = self.service.delete_video("one", reason="retention")
        self.assertEqual((previous.status, previous.reason), ("already_deleted", "manual"))

    def test_creation_and_default_unlimited_checks_do_not_delete(self) -> None:
        path = self.recording("one")
        service = RecordingDeletionService(self.paths)
        self.assertTrue(path.is_file())
        result = service.run_retention(now=NOW)
        self.assertEqual(result.deletions, ())
        self.assertTrue(path.is_file())

    @unittest.skipUnless(sys.platform == "win32", "Windowsの外部利用確認")
    def test_external_reader_keeps_video_and_does_not_start_journal(self) -> None:
        path = self.recording("external")
        with path.open("rb"):
            result = self.service.delete_video("external")
        self.assertEqual(result.reason, "source_in_use", result.message)
        self.assertTrue(result.retryable)
        self.assertFalse(result.journal_started)
        self.assertTrue(path.exists())
        self.assertEqual(self.lifecycle.pending_intents(), ())

    def test_unknown_external_use_keeps_video_before_intent(self) -> None:
        path = self.recording("unknown-use")
        with patch("master_duel_recorder_lite.recording_retention.query_file_use",
                   return_value=FileUseStatus(False, False, (), "利用を確認できません")):
            result = self.service.delete_video("unknown-use")
        self.assertEqual(result.reason, "file_use_unknown")
        self.assertTrue(result.retryable)
        self.assertFalse(result.journal_started)
        self.assertTrue(path.exists())

    def test_external_use_begins_after_staging_and_keeps_markers(self) -> None:
        path = self.recording("late-use")
        _duel_id, marker_id = self.seed_meaning_and_marker("late-use")
        def status(target: Path) -> FileUseStatus:
            busy = target.suffix == ".pending"
            return FileUseStatus(busy, True, (999,) if busy else (), "利用中" if busy else "利用なし")
        with patch("master_duel_recorder_lite.recording_retention.query_file_use", side_effect=status):
            result = self.service.delete_video("late-use")
        self.assertEqual(result.status, "failed")
        self.assertTrue(path.exists())
        self.assertIn(marker_id, {item.event_id for item in DuelTimelineRepository.from_runtime_paths(self.paths).list("late-use")})
        self.assertEqual(self.lifecycle.pending_intents(), ())

    def test_expiry_is_strict_and_falls_back_to_created_at(self) -> None:
        boundary = self.recording("boundary", stamp=NOW - timedelta(days=1))
        expired = self.recording("expired", stamp=NOW - timedelta(days=1, microseconds=1))
        with closing(sqlite3.connect(self.lifecycle.database_path)) as connection, connection:
            connection.execute("UPDATE recordings SET started_at=NULL WHERE recording_id='expired'")
        result = self.service.run_retention(now=NOW)
        self.assertTrue(boundary.exists())
        self.assertFalse(expired.exists(), result.message)
        self.assertEqual([item.recording_id for item in result.deletions if item.status == "deleted"], ["expired"])

    def test_capacity_counts_unregistered_bytes_and_stops_at_limit(self) -> None:
        self.settings = AppConfig(recording_storage_max_gib=1)
        old = self.recording("old", stamp=NOW - timedelta(days=3), content=b"ab")
        new = self.recording("new", stamp=NOW - timedelta(days=2), content=b"cd")
        unregistered = self.paths.recordings / "unregistered.mp4"
        with unregistered.open("wb") as handle:
            handle.truncate(GIB_BYTES - 3)
        result = self.service.run_retention(now=NOW)
        self.assertFalse(old.exists(), result.message)
        self.assertTrue(new.exists())
        self.assertTrue(unregistered.exists())
        self.assertEqual(result.total_bytes, GIB_BYTES - 1)
        self.assertTrue(result.capacity_met)

    def test_equal_utc_dates_delete_recording_id_first_despite_insertion_and_mtime_order(self) -> None:
        self.settings = AppConfig(recording_storage_max_gib=1)
        stamp = NOW - timedelta(days=2)
        times = {"z": stamp.astimezone(timezone(timedelta(hours=9))),
                 "m": stamp.astimezone(timezone(timedelta(hours=-5))), "a": stamp}
        paths = {name: self.recording(name, stamp=times[name], content=b"ab") for name in ("z", "m", "a")}
        for index, name in enumerate(("z", "m", "a")):
            os.utime(paths[name], ns=(1_000_000_000 + index * 1_000_000_000,) * 2)
        filler = self.paths.recordings / "unregistered.mp4"
        with filler.open("wb") as handle:
            handle.truncate(GIB_BYTES - 5)
        result = self.service.run_retention(now=NOW)
        self.assertEqual([item.recording_id for item in result.deletions if item.status == "deleted"], ["a"])
        self.assertFalse(paths["a"].exists())
        self.assertTrue(paths["m"].exists())
        self.assertTrue(paths["z"].exists())
        self.assertTrue(filler.exists())
        self.assertEqual(result.total_bytes, GIB_BYTES - 1)
        self.assertTrue(result.capacity_met)

    def test_expiry_old_order_uses_utc_baseline_not_reversed_file_mtime(self) -> None:
        old = self.recording("old", stamp=NOW - timedelta(days=4))
        new = self.recording("new", stamp=NOW - timedelta(days=2))
        os.utime(old, ns=(3_000_000_000,) * 2)
        os.utime(new, ns=(1_000_000_000,) * 2)
        result = self.service.run_retention(now=NOW)
        self.assertEqual([item.recording_id for item in result.deletions if item.status == "deleted"], ["old", "new"])

    def test_invalid_or_timezone_missing_dates_keep_video_markers_and_canonical_information(self) -> None:
        for identifier, raw in (("invalid", "not-a-date"), ("naive", "2026-10-01T00:00:00")):
            with self.subTest(raw=raw):
                path = self.recording(identifier)
                duel_id, marker_id = self.seed_meaning_and_marker(identifier)
                before = DuelCardRepository.from_runtime_paths(self.paths).get_review(duel_id)
                with closing(sqlite3.connect(self.lifecycle.database_path)) as connection, connection:
                    connection.execute("UPDATE recordings SET started_at=? WHERE recording_id=?", (raw, identifier))
                result = self.service.run_retention(now=NOW)
                item = next(item for item in result.deletions if item.recording_id == identifier)
                self.assertEqual((item.status, item.reason), ("skipped", "unknown_date"))
                self.assertTrue(path.exists())
                self.assert_information_and_marker_retained(identifier, duel_id, marker_id, before)
                self.assertEqual(self.lifecycle.pending_intents(), ())

    def test_real_outward_file_symlink_is_kept_with_its_target_and_metadata(self) -> None:
        path = self.recording("symlink")
        duel_id, marker_id = self.seed_meaning_and_marker("symlink")
        before = DuelCardRepository.from_runtime_paths(self.paths).get_review(duel_id)
        outside = Path(self.temporary.name) / "external-source.mkv"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"実ファイルsymlinkを作成できません（Windows権限・filesystem条件）: {exc}")
        self.addCleanup(path.unlink, missing_ok=True)
        self.assertTrue(path.is_symlink())
        result = self.service.run_retention(now=NOW)
        self.assertEqual(result.deletions[0].reason, "unsafe_path")
        self.assertTrue(path.is_symlink())
        self.assertEqual(outside.read_bytes(), b"video")
        self.assert_information_and_marker_retained("symlink", duel_id, marker_id, before)
        self.assertEqual(self.lifecycle.pending_intents(), ())

    @unittest.skipUnless(sys.platform == "win32", "実Windows junctionを使う境界試験")
    def test_real_outward_directory_junction_is_kept_without_deleting_external_video(self) -> None:
        path = self.recording("junction-video")
        directory = self.paths.recordings / "junction"
        directory.mkdir()
        nested = directory / path.name
        path.rename(nested)
        with closing(sqlite3.connect(self.lifecycle.database_path)) as connection, connection:
            connection.execute("UPDATE recordings SET output_path=? WHERE recording_id='junction-video'", (f"junction/{path.name}",))
        duel_id, marker_id = self.seed_meaning_and_marker("junction-video")
        before = DuelCardRepository.from_runtime_paths(self.paths).get_review(duel_id)
        outside = Path(self.temporary.name) / "external-directory"
        outside.mkdir()
        target = outside / path.name
        target.write_bytes(nested.read_bytes())
        nested.unlink()
        directory.rmdir()
        # 作成する二つのabsolute pathを、fixture所有範囲で先に確認する。
        self.assertTrue(directory.absolute().is_relative_to(self.paths.recordings.absolute()))
        self.assertTrue(outside.resolve().is_relative_to(Path(self.temporary.name).resolve()))
        created = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(directory), str(outside)],
                                 capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if directory.is_junction():
            self.addCleanup(directory.rmdir)
        if created.returncode or not directory.is_junction():
            self.skipTest(f"実junctionを作成できません: returncode={created.returncode}, {created.stderr!r}")
        result = self.service.run_retention(now=NOW)
        self.assertEqual(result.deletions[0].reason, "unsafe_path")
        self.assertTrue(directory.is_junction())
        self.assertEqual(target.read_bytes(), b"video")
        self.assert_information_and_marker_retained("junction-video", duel_id, marker_id, before)
        self.assertEqual(self.lifecycle.pending_intents(), ())

    def test_real_hardlink_keeps_both_names_and_video_metadata(self) -> None:
        path = self.recording("hardlink")
        duel_id, marker_id = self.seed_meaning_and_marker("hardlink")
        before = DuelCardRepository.from_runtime_paths(self.paths).get_review(duel_id)
        other_name = Path(self.temporary.name) / "other-name.mkv"
        try:
            os.link(path, other_name)
        except OSError as exc:
            self.skipTest(f"同volumeの実hardlinkを作成できません: {exc}")
        self.assertGreater(path.stat().st_nlink, 1)
        self.assertEqual(path.stat().st_ino, other_name.stat().st_ino)
        result = self.service.run_retention(now=NOW)
        self.assertEqual(result.deletions[0].reason, "shared_file")
        self.assertEqual(path.read_bytes(), b"video")
        self.assertEqual(other_name.read_bytes(), b"video")
        self.assert_information_and_marker_retained("hardlink", duel_id, marker_id, before)
        self.assertEqual(self.lifecycle.pending_intents(), ())

    def test_capacity_reports_unmet_limit_without_deleting_protected_or_unregistered(self) -> None:
        self.settings = AppConfig(recording_storage_max_gib=1)
        path = self.recording("one")
        self.lifecycle.set_protection("one", True)
        with (self.paths.recordings / "unregistered.mp4").open("wb") as handle:
            handle.truncate(GIB_BYTES)
        result = self.service.run_retention(now=NOW)
        self.assertTrue(path.exists())
        self.assertFalse(result.capacity_met)
        self.assertIn("保護", result.message)

    def test_full_scan_reaches_recording_outside_gui_thousand_row_limit(self) -> None:
        oldest = self.recording("oldest")
        with closing(sqlite3.connect(self.lifecycle.database_path)) as connection, connection:
            connection.executemany(
                "INSERT INTO recordings(recording_id,state,source,output_path,container,created_at,started_at,updated_at) "
                "VALUES (?,'completed','manual',?,'mkv',?,?,?)",
                [(f"new-{i:04}", f"new-{i:04}.mkv", NOW.isoformat(), NOW.isoformat(), NOW.isoformat()) for i in range(1000)],
            )
        result = self.service.run_retention(now=NOW)
        self.assertFalse(oldest.exists(), result.message)
        self.assertEqual(sum(item.status == "deleted" for item in result.deletions), 1)

    def test_protection_requires_manual_permission_and_permission_does_not_clear_flag(self) -> None:
        path = self.recording("one")
        self.lifecycle.set_protection("one", True)
        self.assertEqual(self.service.delete_video("one", reason="retention").reason, "protected")
        self.assertTrue(path.exists())
        result = self.service.delete_video("one", allow_protected=True)
        self.assertEqual(result.status, "deleted", result.message)
        self.assertTrue(self.lifecycle.get_protection("one").protected)

    def test_backup_budget_failure_causes_zero_deletions(self) -> None:
        path = self.recording("one")
        protection = DataProtectionService(self.paths, max_generations=2)
        for i in range(2):
            protection.create_backup(f"migration-{i}", protected=True)
        service = RecordingDeletionService(self.paths, protection=protection, config_loader=lambda: self.settings, now=lambda: NOW)
        result = service.run_retention(now=NOW)
        self.assertTrue(path.exists())
        self.assertEqual(self.lifecycle.pending_intents(), ())
        self.assertEqual(result.deletions, ())
        self.assertIn("保持", result.message)

    def test_unlink_failure_restores_source_and_preserves_markers(self) -> None:
        path = self.recording("one")
        duel_id, marker_id = self.seed_meaning_and_marker("one")
        original_unlink = Path.unlink
        def fail_staged_unlink(target: Path, *args, **kwargs):
            if target.suffix == ".pending":
                raise PermissionError("locked fixture")
            return original_unlink(target, *args, **kwargs)
        with patch.object(Path, "unlink", fail_staged_unlink):
            result = self.service.delete_video("one")
        self.assertEqual(result.status, "failed")
        self.assertTrue(path.exists())
        self.assertIn(marker_id, {item.event_id for item in DuelTimelineRepository.from_runtime_paths(self.paths).list("one")})
        self.assertTrue(DuelCardRepository.from_runtime_paths(self.paths).get_review(duel_id).information)

    def test_latest_off_immediately_before_unlink_restores_staging(self) -> None:
        path = self.recording("one")
        def turn_off(phase: str, _identifier: str) -> None:
            if phase == "before_unlink":
                self.settings = AppConfig()
        self.service._fault_hook = turn_off
        result = self.service.delete_video("one", reason="retention")
        self.assertNotEqual(result.status, "deleted")
        self.assertTrue(path.exists())
        self.assertEqual(self.lifecycle.pending_intents(), ())

    def test_crash_boundaries_recover_idempotently_and_preserve_canonical_facts(self) -> None:
        for phase in ("after_intent", "after_rename", "before_unlink", "after_unlink", "before_complete"):
            with self.subTest(phase=phase):
                identifier = phase.replace("_", "-")
                path = self.recording(identifier)
                duel_id, _marker_id = self.seed_meaning_and_marker(identifier)
                def crash(current: str, _identifier: str, target: str = phase) -> None:
                    if current == target:
                        raise SystemExit("simulated process stop")
                self.service._fault_hook = crash
                with self.assertRaises(SystemExit):
                    self.service.delete_video(identifier, reason="retention")
                self.service._fault_hook = lambda _phase, _identifier: None
                results = self.service.recover_pending()
                self.assertTrue(results)
                self.assertFalse(any(item.status == "failed" for item in results), results)
                self.assertEqual(self.service.recover_pending(), ())
                if phase == "after_intent":
                    self.assertTrue(path.exists())
                else:
                    self.assertFalse(path.exists())
                self.assertTrue(DuelCardRepository.from_runtime_paths(self.paths).get_review(duel_id).information)

    def test_crash_after_unlink_recovers_original_reason_even_when_settings_turn_off(self) -> None:
        path = self.recording("one")
        def crash(phase: str, _identifier: str) -> None:
            if phase == "after_unlink":
                raise SystemExit("stop")
        self.service._fault_hook = crash
        with self.assertRaises(SystemExit):
            self.service.delete_video("one", reason="retention")
        self.settings = AppConfig()
        self.service._fault_hook = lambda _phase, _identifier: None
        result = self.service.recover_pending()
        self.assertFalse(path.exists())
        self.assertEqual(result[0].reason, "retention")

    def test_recovery_before_unlink_with_off_restores_original(self) -> None:
        path = self.recording("one")
        def crash(phase: str, _identifier: str) -> None:
            if phase == "before_unlink":
                raise SystemExit("stop")
        self.service._fault_hook = crash
        with self.assertRaises(SystemExit):
            self.service.delete_video("one", reason="retention")
        self.settings = AppConfig()
        self.service._fault_hook = lambda _phase, _identifier: None
        result = self.service.recover_pending()
        self.assertEqual(result[0].status, "skipped")
        self.assertTrue(path.is_file())
        self.assertEqual(list((self.paths.recordings / STAGING_DIRECTORY).glob("*.pending")), [])

    def test_changed_original_after_persisted_intent_is_kept_and_recovery_gate_stays_closed(self) -> None:
        path = self.recording("intent-replaced")
        duel_id, marker_id = self.seed_meaning_and_marker("intent-replaced")
        before = DuelCardRepository.from_runtime_paths(self.paths).get_review(duel_id)
        def crash(phase, _identifier):
            if phase == "after_intent":
                raise SystemExit("fixture stop after persisted intent")
        self.service._fault_hook = crash
        with self.assertRaises(SystemExit):
            self.service.delete_video("intent-replaced", reason="retention")
        path.write_bytes(b"different original")
        result = self.service.recover_pending()[0]
        self.assertEqual(result.status, "failed")
        self.assertEqual(path.read_bytes(), b"different original")
        self.assertEqual(self.lifecycle.pending_intents()[0].state, "intent")
        self.assert_information_and_marker_retained("intent-replaced", duel_id, marker_id, before)
        self.assert_pending_recovery_keeps_operations_blocked()

    def test_new_original_after_staging_preserves_both_files_and_blocks_recovery(self) -> None:
        path = self.recording("staging-replaced")
        duel_id, marker_id = self.seed_meaning_and_marker("staging-replaced")
        before = DuelCardRepository.from_runtime_paths(self.paths).get_review(duel_id)
        def crash(phase, _identifier):
            if phase == "before_unlink":
                raise SystemExit("fixture stop after staging")
        self.service._fault_hook = crash
        with self.assertRaises(SystemExit):
            self.service.delete_video("staging-replaced", reason="retention")
        intent = self.lifecycle.pending_intents()[0]
        self.assertEqual(intent.state, "staged")
        staged = self.paths.recordings / intent.staged_path
        path.write_bytes(b"new original")
        result = self.service.recover_pending()[0]
        self.assertEqual(result.status, "failed")
        self.assertEqual(path.read_bytes(), b"new original")
        self.assertEqual(staged.read_bytes(), b"video")
        self.assert_information_and_marker_retained("staging-replaced", duel_id, marker_id, before)
        self.assert_pending_recovery_keeps_operations_blocked()

    def test_changed_staged_bytes_are_not_unlinked_or_restored_as_original(self) -> None:
        path = self.recording("staged-changed")
        duel_id, marker_id = self.seed_meaning_and_marker("staged-changed")
        before = DuelCardRepository.from_runtime_paths(self.paths).get_review(duel_id)
        def crash(phase, _identifier):
            if phase == "before_unlink":
                raise SystemExit("fixture stop after staging")
        self.service._fault_hook = crash
        with self.assertRaises(SystemExit):
            self.service.delete_video("staged-changed", reason="retention")
        intent = self.lifecycle.pending_intents()[0]
        self.assertEqual(intent.state, "staged")
        staged = self.paths.recordings / intent.staged_path
        staged.write_bytes(b"changed staged bytes")
        result = self.service.recover_pending()[0]
        self.assertEqual(result.status, "failed")
        self.assertFalse(path.exists())
        self.assertEqual(staged.read_bytes(), b"changed staged bytes")
        self.assert_information_and_marker_retained("staged-changed", duel_id, marker_id, before)
        self.assert_pending_recovery_keeps_operations_blocked()

    def test_rename_failure_keeps_original_marker_facts_and_cancels_only_its_intent(self) -> None:
        path = self.recording("rename-failed")
        duel_id, marker_id = self.seed_meaning_and_marker("rename-failed")
        before = DuelCardRepository.from_runtime_paths(self.paths).get_review(duel_id)
        rename = os.rename
        def fail(source, destination, *args, **kwargs):
            if Path(source) == path and Path(destination).suffix == ".pending":
                raise PermissionError("fixture rename failure")
            return rename(source, destination, *args, **kwargs)
        with patch("master_duel_recorder_lite.recording_retention.os.rename", side_effect=fail):
            result = self.service.delete_video("rename-failed", reason="retention")
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.journal_started)
        self.assertEqual(path.read_bytes(), b"video")
        self.assertEqual(self.lifecycle.get_intent(result.intent_id).state, "cancelled")
        self.assertEqual(self.lifecycle.pending_intents(), ())
        self.assertEqual(list((self.paths.recordings / STAGING_DIRECTORY).glob("*.pending")), [])
        self.assert_information_and_marker_retained("rename-failed", duel_id, marker_id, before)
        self.settings = AppConfig()
        startup = run_startup_maintenance(self.paths, deletion_service=self.service)
        self.assertTrue(startup.operation_allowed)
        self.assertEqual(startup.recovery, ())
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
