"""人工source/MP4と隔離DBだけで投稿後削除の許可条件を検証します。"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from master_duel_recorder_lite.config import AppConfig, LoadedAppConfig, save_app_config
from master_duel_recorder_lite.post_upload_cleanup import PostUploadCleanupHandler
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.upload_metadata import UploadMetadata
from master_duel_recorder_lite.upload_queue import UploadQueueState, UploadQueueStore
from master_duel_recorder_lite.youtube_client import FakeYouTubeClient, YouTubeUploadResult
from master_duel_recorder_lite.youtube_oauth import MemoryCredentialStore, YouTubeCredentials
from master_duel_recorder_lite.youtube_service import YouTubeUploadService
from master_duel_recorder_lite.youtube_uploads import YouTubeUploadRepository, YouTubeUploadState


class PostUploadCleanupTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = default_runtime_paths(user_data_dir=Path(self.temporary.name))
        ensure_runtime_dirs(self.paths)
        self.source = self.paths.recordings / "original.mkv"
        self.source.write_bytes(b"original-video-data")
        self.export = self.paths.exports / "prepared.mp4"
        self.export.write_bytes(b"encoded-mp4-data")
        self.metadata = UploadMetadata("人工検証")
        self.history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.history.register_starting(
            recording_id="rec", output_path=self.source, container="mkv", source="manual", created_at=stamp,
        )
        self.history.mark_recording("rec", started_at=stamp)
        self.history.finalize("rec", RecordingResult(
            state=RecordingState.COMPLETED, output_path=self.source, returncode=0,
            started_at=stamp, ended_at=stamp, size_bytes=self.source.stat().st_size,
            error=None, diagnostics=(),
        ))
        self.queue = UploadQueueStore(self.paths)
        item = self.queue.enqueue(recording_id="rec", metadata=self.metadata)
        self.queue.transition(item.queue_id, UploadQueueState.PROCESSING)
        self.manifest = self.paths.queue / "manifests" / f"{item.queue_id}.json"
        self.manifest.parent.mkdir()
        self.document = {
            "schema_version": 1, "generated_at": stamp.isoformat(),
            "queue_id": item.queue_id, "recording_id": "rec",
            "source": self.file_document(self.source),
            "export": {**self.file_document(self.export), "container": "mp4",
                       "duration_seconds": 1, "stream_types": ["video"]},
            "metadata": self.metadata.to_dict(),
            "validation": {"source_status": "valid", "export_status": "valid",
                           "source_warnings": [], "export_warnings": []},
        }
        self.write_manifest()
        self.prepared = self.queue.transition(
            item.queue_id, UploadQueueState.COMPLETED,
            export_path=self.export.relative_to(self.paths.root),
            manifest_path=self.manifest.relative_to(self.paths.root),
        )
        self.uploads = YouTubeUploadRepository.from_runtime_paths(self.paths)
        created = self.uploads.create(recording_id="rec", metadata=self.metadata)
        self.upload = self.uploads.update(
            created, state=YouTubeUploadState.COMPLETED, prepare_queue_id=item.queue_id,
            video_id="uploaded", watch_url="https://youtu.be/uploaded", error="privacy warning",
        )
        self.sent_result = YouTubeUploadResult(
            "uploaded", "https://youtu.be/uploaded", "private", warning="privacy warning",
            media_path=self.export.resolve(), media_size_bytes=self.export.stat().st_size,
            media_sha256=hashlib.sha256(self.export.read_bytes()).hexdigest(),
        )
        self.asset = SimpleNamespace(asset_id="asset", recording_id="rec", generation=1, path="original.mkv")
        self.lifecycle = Mock()
        self.lifecycle.ensure_current_asset.return_value = self.asset
        self.lifecycle.get_current_asset.return_value = self.asset
        self.lifecycle.get_protection.return_value = SimpleNamespace(protected=False)
        self.lifecycle.deletion_for_asset.return_value = None
        self.lifecycle.request_cleanup.return_value = SimpleNamespace(request_id="request")
        self.deletion = Mock()
        self.deletion.delete_video.return_value = self.deletion_result("deleted")
        self.save_setting(True)
        self.handler = PostUploadCleanupHandler(
            self.paths, deletion_service=self.deletion, lifecycle_repository=self.lifecycle,
            upload_repository=self.uploads, queue=self.queue,
        )

    def file_document(self, path):
        return {"path": path.relative_to(self.paths.root).as_posix(),
                "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.document), encoding="utf-8")

    def save_setting(self, value):
        save_app_config(paths=self.paths, config=replace(AppConfig(), upload_delete_source_after_upload=value))

    def deletion_result(self, status, *, retryable=False, journal_started=False):
        return SimpleNamespace(status=status, reason="in_use" if retryable else "youtube_uploaded",
                               message="使用中" if retryable else "削除しました",
                               retryable=retryable, journal_started=journal_started)

    def run_cleanup(self, *, result=None):
        return self.handler(self.upload, self.prepared, result or self.sent_result)

    def retry_request(self, provenance):
        request = SimpleNamespace(request_id="request", state="pending", upload_id=self.upload.upload_id,
                                  recording_id="rec", asset_id="asset", generation=1,
                                  prepare_queue_id=self.prepared.queue_id, provenance=provenance)
        self.lifecycle.get_cleanup_request.return_value = request
        return request

    def test_valid_provenance_uses_source_export_and_queue_relative_paths(self):
        outcome = self.run_cleanup()
        self.assertEqual(outcome.status, "deleted")
        arguments = self.deletion.delete_video.call_args.kwargs
        self.assertEqual(arguments["expected_asset_id"], "asset")
        self.assertEqual(arguments["expected_generation"], 1)
        provenance = arguments["provenance"]
        self.assertEqual(provenance["source"]["path"], "original.mkv")
        self.assertEqual(provenance["export"]["path"], "prepared.mp4")
        self.assertEqual(provenance["manifest"]["path"], f"manifests/{self.prepared.queue_id}.json")
        self.assertEqual(provenance["export"]["sha256"], self.sent_result.media_sha256)
        self.assertEqual(provenance["video_id"], "uploaded")
        self.lifecycle.request_cleanup.assert_not_called()

    def test_latest_saved_off_and_missing_config_keep_original(self):
        for mode in ("off", "missing"):
            with self.subTest(mode=mode):
                self.save_setting(False)
                if mode == "missing":
                    (self.paths.config / "app.toml").unlink()
                outcome = self.run_cleanup()
                self.assertEqual(outcome.status, "disabled")
                self.deletion.delete_video.assert_not_called()
                self.lifecycle.request_cleanup.assert_not_called()
                self.assertTrue(self.source.exists())

    def test_config_invalid_or_not_saved_never_permits_delete(self):
        loaders = (
            Mock(side_effect=RuntimeError("config unreadable")),
            lambda: LoadedAppConfig(replace(AppConfig(), upload_delete_source_after_upload=True),
                                    self.paths.config / "app.toml", False),
            lambda: SimpleNamespace(config_loaded=True, config=SimpleNamespace(upload_delete_source_after_upload="true")),
        )
        for loader in loaders:
            with self.subTest(loader=loader):
                self.handler.config_loader = loader
                self.run_cleanup()
                self.deletion.delete_video.assert_not_called()
                self.lifecycle.request_cleanup.assert_not_called()

    def test_protected_video_has_no_new_retry_request(self):
        self.lifecycle.get_protection.return_value = SimpleNamespace(protected=True)
        outcome = self.run_cleanup()
        self.assertEqual(outcome.reason, "protected")
        self.deletion.delete_video.assert_not_called()
        self.lifecycle.request_cleanup.assert_not_called()

    def test_unknown_sent_bytes_preserve_source_but_keep_completed_url(self):
        outcome = self.run_cleanup(result=YouTubeUploadResult("uploaded", "https://youtu.be/uploaded", "private"))
        self.assertEqual(outcome.reason, "sent_media_unverified")
        self.deletion.delete_video.assert_not_called()
        self.assertEqual(self.uploads.get(self.upload.upload_id).state, YouTubeUploadState.COMPLETED)
        self.assertEqual(self.uploads.get(self.upload.upload_id).error, "privacy warning")

    def test_old_export_source_replacement_or_manifest_mismatch_never_deletes(self):
        original_source, original_export = self.source.read_bytes(), self.export.read_bytes()
        cases = ("source", "export", "source_path", "queue_id", "export_sha", "missing_manifest")
        for case in cases:
            with self.subTest(case=case):
                self.source.write_bytes(original_source)
                self.export.write_bytes(original_export)
                document = json.loads(json.dumps(self.document))
                if case == "source":
                    self.source.write_bytes(b"replacement-original")
                elif case == "export":
                    self.export.write_bytes(b"replacement-export")
                elif case == "source_path":
                    document["source"]["path"] = "data/recordings/another.mkv"
                elif case == "queue_id":
                    document["queue_id"] = "other-queue"
                elif case == "export_sha":
                    document["export"]["sha256"] = "0" * 64
                self.manifest.write_text(json.dumps(document), encoding="utf-8")
                if case == "missing_manifest":
                    self.manifest.unlink()
                outcome = self.run_cleanup()
                self.assertIn(outcome.status, {"skipped", "failed"})
                self.deletion.delete_video.assert_not_called()
                self.lifecycle.request_cleanup.assert_not_called()

    def test_in_use_preintent_creates_one_retry_with_verified_provenance(self):
        self.deletion.delete_video.return_value = self.deletion_result("skipped", retryable=True)
        outcome = self.run_cleanup()
        self.assertEqual(outcome.status, "retry_pending")
        self.assertEqual(outcome.retry_request_id, "request")
        arguments = self.lifecycle.request_cleanup.call_args.kwargs
        self.assertEqual(arguments["upload_id"], self.upload.upload_id)
        self.assertEqual(arguments["provenance"]["export"]["sha256"], self.sent_result.media_sha256)
        self.assertEqual(arguments["skip_reason"], "in_use")

    def test_retry_record_failure_is_not_reported_as_retry_pending(self):
        self.deletion.delete_video.return_value = self.deletion_result("skipped", retryable=True)
        self.lifecycle.request_cleanup.side_effect = RuntimeError("retry DB failure")
        outcome = self.run_cleanup()
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.reason, "retry_record_failed")
        self.assertIsNone(outcome.retry_request_id)
        self.assertEqual(self.uploads.get(self.upload.upload_id).state, YouTubeUploadState.COMPLETED)

    def test_started_journal_failure_does_not_create_preintent_request(self):
        self.deletion.delete_video.return_value = self.deletion_result("failed", retryable=True, journal_started=True)
        self.assertEqual(self.run_cleanup().status, "failed")
        self.lifecycle.request_cleanup.assert_not_called()

    def test_retry_with_started_journal_hands_off_and_cancels_the_preintent_request(self):
        self.run_cleanup()
        provenance = self.deletion.delete_video.call_args.kwargs["provenance"]
        request = self.retry_request(provenance)
        self.deletion.delete_video.return_value = self.deletion_result("failed", journal_started=True)
        self.assertEqual(self.handler.retry_request(request).status, "failed")
        self.lifecycle.update_cleanup_request.assert_any_call(
            "request", state="cancelled", skip_reason="youtube_uploaded: 削除journalの回復へ引き継ぎました",
        )
        self.lifecycle.request_cleanup.assert_not_called()

    def test_existing_deleted_reason_is_kept_without_overwriting_or_retry(self):
        self.lifecycle.deletion_for_asset.return_value = SimpleNamespace(reason="retention_expired")
        outcome = self.run_cleanup()
        self.assertEqual((outcome.status, outcome.reason), ("already_deleted", "retention_expired"))
        self.deletion.delete_video.assert_not_called()
        self.lifecycle.request_cleanup.assert_not_called()

    def test_retry_only_saved_request_uses_latest_config_and_protection(self):
        self.run_cleanup()
        provenance = self.deletion.delete_video.call_args.kwargs["provenance"]
        request = self.retry_request(provenance)
        self.deletion.reset_mock()
        self.save_setting(False)
        self.assertEqual(self.handler.retry_request(request).reason, "setting_off")
        self.deletion.delete_video.assert_not_called()
        self.save_setting(True)
        self.lifecycle.get_protection.return_value = SimpleNamespace(protected=True)
        self.assertEqual(self.handler.retry_request(request).reason, "protected")
        self.deletion.delete_video.assert_not_called()
        self.lifecycle.get_protection.return_value = SimpleNamespace(protected=False)
        self.assertEqual(self.handler.retry_request(request).status, "deleted")
        self.lifecycle.update_cleanup_request.assert_any_call("request", state="completed")

    def test_retry_source_generation_or_manifest_change_cancels_without_deletion(self):
        self.run_cleanup()
        provenance = self.deletion.delete_video.call_args.kwargs["provenance"]
        request = self.retry_request(provenance)
        self.deletion.reset_mock()
        self.asset.generation = 2
        self.assertEqual(self.handler.retry_request(request).reason, "asset_changed")
        self.deletion.delete_video.assert_not_called()
        self.asset.generation = 1
        # 内容が同じJSONでも、保存されたsidecar SHAから変更されたら保持します。
        self.manifest.write_text(json.dumps(self.document, indent=2), encoding="utf-8")
        self.assertEqual(self.handler.retry_request(request).reason, "provenance_mismatch")
        self.deletion.delete_video.assert_not_called()
        self.lifecycle.update_cleanup_request.assert_any_call("request", state="cancelled", skip_reason="provenance_mismatch")

    def test_setting_changes_during_upload_are_read_after_saved_success(self):
        credentials = MemoryCredentialStore()
        credentials.write(YouTubeCredentials("fake-client", "fake-secret", "fake-refresh"))
        owner = self

        class SentClient(FakeYouTubeClient):
            def upload_video(self, **arguments):
                path = arguments["media_path"]
                data = path.read_bytes()
                result = super().upload_video(**arguments)
                owner.save_setting(owner.next_setting)
                return replace(result, media_path=path.resolve(), media_size_bytes=len(data),
                               media_sha256=hashlib.sha256(data).hexdigest())

        service = YouTubeUploadService(
            paths=self.paths, upload_repository=self.uploads, queue=self.queue,
            credential_store=credentials, youtube_client=SentClient(), post_upload_cleanup=self.handler,
        )
        for start, finish in ((True, False), (False, True)):
            with self.subTest(start=start, finish=finish):
                # 各ケースを完了投稿なしの状態から独立して実行します。
                for completed in self.uploads.list(state=YouTubeUploadState.COMPLETED):
                    self.uploads.update(completed, state=YouTubeUploadState.CANCELLED)
                self.save_setting(start)
                self.next_setting = finish
                self.deletion.reset_mock()
                outcome = service.upload_recording(recording_id="rec", metadata=self.metadata, force_new_upload=True)
                self.assertEqual(outcome.upload.state, YouTubeUploadState.COMPLETED)
                self.assertEqual(outcome.cleanup.status, "deleted" if finish else "disabled")
                self.assertEqual(self.deletion.delete_video.called, finish)

    def test_completed_row_or_prepare_reference_mismatch_does_not_delete(self):
        for changed in (replace(self.upload, state=YouTubeUploadState.UPLOADING),
                        replace(self.upload, prepare_queue_id="other")):
            with self.subTest(changed=changed):
                with patch.object(self.uploads, "get", return_value=changed):
                    self.assertEqual(self.run_cleanup().reason, "completion_not_verified")
                self.deletion.delete_video.assert_not_called()

    def test_completed_or_cancelled_request_is_not_replayed_from_stale_object(self):
        self.run_cleanup()
        provenance = self.deletion.delete_video.call_args.kwargs["provenance"]
        request = self.retry_request(provenance)
        self.deletion.reset_mock()
        self.lifecycle.get_cleanup_request.return_value = SimpleNamespace(state="cancelled")
        self.assertEqual(self.handler.retry_request(request).reason, "request_not_pending")
        self.deletion.delete_video.assert_not_called()

    def real_handler(self, *, fault_hook=None):
        from master_duel_recorder_lite.recording_retention import RecordingDeletionService
        from master_duel_recorder_lite.video_lifecycle import VideoLifecycleRepository
        lifecycle = VideoLifecycleRepository.from_runtime_paths(self.paths)
        deletion = RecordingDeletionService(self.paths, repository=lifecycle, fault_hook=fault_hook)
        return PostUploadCleanupHandler(
            self.paths, lifecycle_repository=lifecycle, deletion_service=deletion,
            upload_repository=self.uploads, queue=self.queue,
        )

    def test_real_delete_keeps_upload_and_export_and_stores_cause(self):
        handler = self.real_handler()
        outcome = handler(self.upload, self.prepared, self.sent_result)
        self.assertEqual(outcome.status, "deleted", outcome.message)
        self.assertFalse(self.source.exists())
        self.assertTrue(self.export.exists())
        self.assertTrue(self.manifest.exists())
        self.assertIsNotNone(self.history.get("rec"))
        saved = self.uploads.get(self.upload.upload_id)
        self.assertEqual(saved.state, YouTubeUploadState.COMPLETED)
        self.assertEqual(saved.watch_url, self.upload.watch_url)
        self.assertEqual(saved.error, "privacy warning")
        asset = handler.lifecycle.get_current_asset("rec")
        reason = handler.lifecycle.deletion_for_asset(asset.asset_id)
        self.assertEqual(reason.reason, "youtube_uploaded")
        self.assertEqual(reason.upload_id, self.upload.upload_id)
        self.assertEqual(handler(self.upload, self.prepared, self.sent_result).status, "already_deleted")
        self.assertEqual(handler.lifecycle.pending_cleanup_requests(), ())

    def test_real_use_lease_creates_idempotent_retry_then_releases_for_delete(self):
        handler = self.real_handler()
        asset = handler.lifecycle.ensure_current_asset("rec")
        use = handler.lifecycle.register_use(asset_id=asset.asset_id, owner_id="test", purpose="review")
        first = handler(self.upload, self.prepared, self.sent_result)
        second = handler(self.upload, self.prepared, self.sent_result)
        self.assertEqual(first.status, "retry_pending", first.message)
        self.assertEqual(first.retry_request_id, second.retry_request_id)
        requests = handler.lifecycle.pending_cleanup_requests()
        self.assertEqual(len(requests), 1)
        self.assertTrue(self.source.exists())
        handler.lifecycle.release_use(use.use_id, owner_id="test")
        self.save_setting(False)
        self.assertEqual(handler.retry_request(requests[0]).reason, "setting_off")
        self.assertTrue(self.source.exists())
        self.assertEqual(len(handler.lifecycle.pending_cleanup_requests()), 1)
        self.save_setting(True)
        retried = handler.retry_request(requests[0])
        self.assertEqual(retried.status, "deleted", retried.message)
        self.assertFalse(self.source.exists())
        self.assertEqual(handler.lifecycle.pending_cleanup_requests(), ())
        self.assertEqual(handler.retry_request(requests[0]).reason, "request_not_pending")

    def test_real_backup_failure_keeps_source_and_records_only_explicit_retry(self):
        from master_duel_recorder_lite.data_protection import DataProtectionError
        handler = self.real_handler()
        with patch.object(handler.deletion.protection, "retained_backup", side_effect=DataProtectionError("disk full")):
            outcome = handler(self.upload, self.prepared, self.sent_result)
        self.assertEqual(outcome.status, "retry_pending", outcome.message)
        self.assertTrue(self.source.exists())
        self.assertEqual(handler.lifecycle.pending_intents(), ())
        self.assertEqual(len(handler.lifecycle.pending_cleanup_requests()), 1)
        self.assertEqual(self.uploads.get(self.upload.upload_id).state, YouTubeUploadState.COMPLETED)

    def test_real_unlink_completion_fault_recovers_upload_cause_without_request(self):
        def fault(phase, _intent_id):
            if phase == "after_unlink":
                raise RuntimeError("after-unlink-db-failure")

        handler = self.real_handler(fault_hook=fault)
        outcome = handler(self.upload, self.prepared, self.sent_result)
        self.assertEqual(outcome.status, "failed", outcome.message)
        self.assertFalse(self.source.exists())
        self.assertEqual(handler.lifecycle.pending_cleanup_requests(), ())
        self.assertEqual(len(handler.lifecycle.pending_intents()), 1)
        self.assertEqual(self.uploads.get(self.upload.upload_id).state, YouTubeUploadState.COMPLETED)
        handler.deletion._fault_hook = lambda *_arguments: None
        recovered = handler.deletion.recover_pending()
        self.assertEqual(recovered[0].status, "deleted", recovered[0].message)
        asset = handler.lifecycle.get_current_asset("rec")
        self.assertEqual(handler.lifecycle.deletion_for_asset(asset.asset_id).reason, "youtube_uploaded")

    def test_real_setting_change_before_unlink_restores_video_without_new_retry(self):
        def fault(phase, _intent_id):
            if phase == "before_unlink":
                self.save_setting(False)

        handler = self.real_handler(fault_hook=fault)
        outcome = handler(self.upload, self.prepared, self.sent_result)
        self.assertEqual(outcome.status, "failed", outcome.message)
        self.assertTrue(self.source.exists())
        self.assertEqual(self.source.read_bytes(), b"original-video-data")
        self.assertEqual(handler.lifecycle.pending_cleanup_requests(), ())
        self.assertEqual(self.uploads.get(self.upload.upload_id).state, YouTubeUploadState.COMPLETED)


if __name__ == "__main__":
    unittest.main()
