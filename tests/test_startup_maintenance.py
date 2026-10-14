"""起動回復の順序、段階間の排他、保存要求だけの再試行を検証します。"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from master_duel_recorder_lite.config import AppConfig, save_app_config
from master_duel_recorder_lite.post_upload_cleanup import UploadCleanupOutcome
from master_duel_recorder_lite.recording_lock import RecordingBusyError
from master_duel_recorder_lite.recording_retention import RetentionResult, VideoDeletionResult
from master_duel_recorder_lite.runtime_paths import default_runtime_paths
from master_duel_recorder_lite.startup_maintenance import run_startup_maintenance


class StartupMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = default_runtime_paths(user_data_dir=Path(self.temporary.name))
        self.events = []
        self.lock_held = False
        self.repository = Mock()
        self.repository.pending_intents.return_value = ()
        self.repository.pending_cleanup_requests.return_value = ()

        @contextmanager
        def operation_lock():
            self.events.append("lock")
            self.lock_held = True
            try:
                yield
            finally:
                self.lock_held = False
                self.events.append("unlock")

        def recover():
            self.assertTrue(self.lock_held)
            self.events.append("recover")
            return (VideoDeletionResult("deleted", "rec", "capacity", "回復済み"),)

        def retention():
            self.assertTrue(self.lock_held)
            self.events.append("retention")
            return RetentionResult((), 10, 0, True, "容量・期限は無制限")

        self.service = SimpleNamespace(
            paths=self.paths, repository=self.repository, operation_lock=operation_lock,
            recover_pending=Mock(side_effect=recover), run_retention=Mock(side_effect=retention),
        )
        self.initializer = self.enterContext(patch(
            "master_duel_recorder_lite.startup_maintenance.initialize_protected_history_database",
            side_effect=lambda _paths: self.events.append("initialize"),
        ))
        self.handler = Mock()
        self.handler.retry_request.return_value = UploadCleanupOutcome("deleted", "youtube_uploaded", "削除済み")

    def run_maintenance(self, *, progress=None):
        return run_startup_maintenance(
            self.paths, deletion_service=self.service, cleanup_handler=self.handler, progress=progress,
        )

    def test_initialization_recovery_retention_saved_retries_share_one_outer_lock(self):
        requests = (SimpleNamespace(request_id="first"), SimpleNamespace(request_id="second"))
        self.repository.pending_cleanup_requests.return_value = requests

        def retry(request):
            self.assertTrue(self.lock_held)
            self.events.append(request.request_id)
            return UploadCleanupOutcome("deleted", "youtube_uploaded", "削除済み")

        self.handler.retry_request.side_effect = retry
        stages = []
        result = self.run_maintenance(progress=stages.append)
        self.assertTrue(result.operation_allowed)
        self.assertEqual(self.events, ["initialize", "lock", "recover", "retention", "first", "second", "unlock"])
        self.assertEqual(len(result.recovery), 1)
        self.assertEqual(len(result.upload_retries), 2)
        self.assertIn("削除済み2件", result.message)
        self.assertEqual(len(stages), 5)

    def test_remaining_recovery_journal_blocks_retention_retry_and_workers(self):
        self.repository.pending_intents.return_value = (object(),)
        result = self.run_maintenance()
        self.assertFalse(result.operation_allowed)
        self.service.run_retention.assert_not_called()
        self.repository.pending_cleanup_requests.assert_not_called()
        self.handler.retry_request.assert_not_called()
        self.assertIn("回復が未完了", result.message)

    def test_new_retention_journal_blocks_subsequent_retries(self):
        def interrupted():
            self.repository.pending_intents.return_value = (object(),)
            return RetentionResult((VideoDeletionResult("failed", "rec", "interrupted", "停止"),), 10, 0, True, "停止")

        self.service.run_retention.side_effect = interrupted
        result = self.run_maintenance()
        self.assertFalse(result.operation_allowed)
        self.handler.retry_request.assert_not_called()
        self.repository.pending_cleanup_requests.assert_not_called()

    def test_retry_failure_is_terminal_and_each_saved_request_is_attempted_once(self):
        requests = (SimpleNamespace(request_id="first"), SimpleNamespace(request_id="second"))
        self.repository.pending_cleanup_requests.return_value = requests
        self.handler.retry_request.side_effect = [RuntimeError("retry failure"), UploadCleanupOutcome("skipped", "protected", "保護中")]
        result = self.run_maintenance()
        self.assertTrue(result.operation_allowed)
        self.assertEqual([item.status for item in result.upload_retries], ["failed", "skipped"])
        self.assertEqual(self.handler.retry_request.call_count, 2)
        self.repository.pending_cleanup_requests.assert_called_once()

    def test_new_retry_journal_stops_further_requests_and_blocks_workers(self):
        self.repository.pending_cleanup_requests.return_value = (object(), object())

        def interrupted(_request):
            self.repository.pending_intents.return_value = (object(),)
            return UploadCleanupOutcome("failed", "interrupted", "回復待ち")

        self.handler.retry_request.side_effect = interrupted
        result = self.run_maintenance()
        self.assertFalse(result.operation_allowed)
        self.assertEqual(self.handler.retry_request.call_count, 1)
        self.assertEqual(len(result.upload_retries), 1)

    def test_no_saved_request_does_not_create_a_cleanup_handler_or_scan_completed(self):
        with patch("master_duel_recorder_lite.startup_maintenance.PostUploadCleanupHandler") as constructor:
            result = run_startup_maintenance(self.paths, deletion_service=self.service)
        self.assertTrue(result.operation_allowed)
        constructor.assert_not_called()
        self.repository.pending_cleanup_requests.assert_called_once()

    def test_default_handler_receives_same_service_and_repository(self):
        self.repository.pending_cleanup_requests.return_value = (object(),)
        with patch("master_duel_recorder_lite.startup_maintenance.PostUploadCleanupHandler", return_value=self.handler) as constructor:
            self.assertTrue(run_startup_maintenance(self.paths, deletion_service=self.service).operation_allowed)
        constructor.assert_called_once_with(self.paths, deletion_service=self.service, lifecycle_repository=self.repository)

    def test_configuration_or_backup_failure_keeps_normal_operations_available(self):
        self.service.run_retention.side_effect = RuntimeError("backup not available")
        result = self.run_maintenance()
        self.assertTrue(result.operation_allowed)
        self.assertIsNone(result.retention)
        self.assertIn("backup not available", result.message)

    def test_migration_failure_does_not_unlock_recording_operations(self):
        self.initializer.side_effect = RuntimeError("migration backup failed")
        result = self.run_maintenance()
        self.assertFalse(result.operation_allowed)
        self.service.recover_pending.assert_not_called()
        self.assertIn("migration backup failed", result.message)

    def test_failed_journal_inspection_does_not_claim_safe_completion(self):
        self.repository.pending_intents.side_effect = RuntimeError("DB unavailable")
        result = self.run_maintenance()
        self.assertFalse(result.operation_allowed)
        self.service.run_retention.assert_not_called()

    def test_busy_external_operation_skips_cleanup_and_keeps_pending_gate(self):
        @contextmanager
        def busy():
            raise RecordingBusyError("busy")
            yield

        self.service.operation_lock = busy
        self.assertTrue(self.run_maintenance().operation_allowed)
        self.service.recover_pending.assert_not_called()
        self.repository.pending_intents.return_value = (object(),)
        self.assertFalse(self.run_maintenance().operation_allowed)

    def test_progress_receiver_failure_does_not_interrupt_safe_maintenance(self):
        result = self.run_maintenance(progress=Mock(side_effect=RuntimeError("UI closed")))
        self.assertTrue(result.operation_allowed)
        self.assertIn("進捗表示を更新できません", result.message)


class RealStartupMaintenanceTest(unittest.TestCase):
    def fixture(self):
        import test_post_upload_cleanup as fixtures

        fixture = fixtures.PostUploadCleanupTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def pending_request(self, fixture, handler):
        asset = handler.lifecycle.ensure_current_asset("rec")
        use = handler.lifecycle.register_use(asset_id=asset.asset_id, owner_id="startup-fixture", purpose="review")
        outcome = handler(fixture.upload, fixture.prepared, fixture.sent_result)
        self.assertEqual(outcome.status, "retry_pending", outcome.message)
        handler.lifecycle.release_use(use.use_id, owner_id="startup-fixture")
        return handler.lifecycle.pending_cleanup_requests()[0]

    def test_service_construction_does_not_run_retention_and_old_completed_is_not_backfilled(self):
        fixture = self.fixture()
        save_app_config(paths=fixture.paths, config=replace(AppConfig(), recording_retention_days=1))
        handler = fixture.real_handler()
        self.assertTrue(fixture.source.exists())
        self.assertEqual(handler.lifecycle.pending_intents(), ())
        self.assertEqual(handler.lifecycle.pending_cleanup_requests(), ())
        # 実行要求のない過去投稿は、ONでも起動時の投稿後削除へ追加しません。
        fixture.save_setting(True)
        result = run_startup_maintenance(fixture.paths, deletion_service=handler.deletion, cleanup_handler=handler)
        self.assertTrue(result.operation_allowed, result.message)
        self.assertEqual(result.upload_retries, ())
        self.assertTrue(fixture.source.exists())

    def test_persisted_retry_deletes_after_initialization_with_reentrant_same_service(self):
        fixture = self.fixture()
        handler = fixture.real_handler()
        request = self.pending_request(fixture, handler)
        result = run_startup_maintenance(fixture.paths, deletion_service=handler.deletion, cleanup_handler=handler)
        self.assertTrue(result.operation_allowed, result.message)
        self.assertEqual(result.upload_retries[0].status, "deleted", result.message)
        self.assertFalse(fixture.source.exists())
        self.assertEqual(handler.lifecycle.get_cleanup_request(request.request_id).state, "completed")
        self.assertTrue(fixture.export.exists())

    def test_latest_off_and_protected_request_are_kept_for_later_startup(self):
        fixture = self.fixture()
        handler = fixture.real_handler()
        request = self.pending_request(fixture, handler)
        fixture.save_setting(False)
        result = run_startup_maintenance(fixture.paths, deletion_service=handler.deletion, cleanup_handler=handler)
        self.assertTrue(result.operation_allowed)
        self.assertEqual(result.upload_retries[0].reason, "setting_off")
        fixture.save_setting(True)
        handler.lifecycle.set_protection("rec", True)
        result = run_startup_maintenance(fixture.paths, deletion_service=handler.deletion, cleanup_handler=handler)
        self.assertTrue(result.operation_allowed)
        self.assertEqual(result.upload_retries[0].reason, "protected")
        self.assertTrue(fixture.source.exists())
        self.assertEqual(handler.lifecycle.get_cleanup_request(request.request_id).state, "pending")

    def test_expiry_precedes_retry_and_prior_deletion_cause_is_not_replaced(self):
        fixture = self.fixture()
        handler = fixture.real_handler()
        self.pending_request(fixture, handler)
        save_app_config(paths=fixture.paths, config=replace(AppConfig(), recording_retention_days=1, upload_delete_source_after_upload=True))
        handler.deletion._now = lambda: datetime(2026, 1, 3, tzinfo=timezone.utc)
        result = run_startup_maintenance(fixture.paths, deletion_service=handler.deletion, cleanup_handler=handler)
        self.assertTrue(result.operation_allowed, result.message)
        self.assertEqual(result.retention.deletions[0].status, "deleted", result.message)
        self.assertEqual(result.upload_retries[0].status, "already_deleted")
        self.assertEqual(result.upload_retries[0].reason, "retention")
        asset = handler.lifecycle.get_current_asset("rec")
        self.assertEqual(handler.lifecycle.deletion_for_asset(asset.asset_id).reason, "retention")

    def test_retry_crash_blocks_workers_until_next_startup_recovers_it(self):
        fixture = self.fixture()
        handler = fixture.real_handler()
        self.pending_request(fixture, handler)

        def fault(phase, _intent_id):
            if phase == "after_unlink":
                raise RuntimeError("completion DB failure")

        handler.deletion._fault_hook = fault
        first = run_startup_maintenance(fixture.paths, deletion_service=handler.deletion, cleanup_handler=handler)
        self.assertFalse(first.operation_allowed, first.message)
        self.assertEqual(first.upload_retries[0].status, "failed")
        self.assertFalse(fixture.source.exists())
        self.assertEqual(len(handler.lifecycle.pending_intents()), 1)
        handler.deletion._fault_hook = lambda *_arguments: None
        second = run_startup_maintenance(fixture.paths, deletion_service=handler.deletion, cleanup_handler=handler)
        self.assertTrue(second.operation_allowed, second.message)
        self.assertEqual(second.recovery[0].status, "deleted")
        self.assertEqual(second.upload_retries, ())


if __name__ == "__main__":
    unittest.main()
