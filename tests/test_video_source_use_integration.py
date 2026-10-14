from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from unittest.mock import patch

from master_duel_recorder_lite.clip_export import ClipExportError
from master_duel_recorder_lite.config import AppConfig, save_app_config
from master_duel_recorder_lite.post_upload_cleanup import PostUploadCleanupHandler
from master_duel_recorder_lite.upload_manifest import UploadManifestError
from master_duel_recorder_lite.upload_queue import UploadQueueState
from master_duel_recorder_lite.youtube_client import YouTubeUploadResult
from master_duel_recorder_lite.youtube_uploads import YouTubeUploadRepository, YouTubeUploadState
from test_video_source_use import SourceUseFixture


class VideoSourceUseIntegrationTest(SourceUseFixture):
    def assert_use_blocks_delete(self):
        result = self.deletion.delete_video("recording")
        self.assertEqual((result.status, result.reason), ("skipped", "source_in_use"))
        self.assertTrue(self.source.exists())

    def test_preparation_keeps_lease_through_validator_ffmpeg_manifest_and_queue_commit(self) -> None:
        item = self.enqueue()
        phases = []
        probe, writer, transition = self.validator.runner, self.writer.write, self.queue.transition

        def guarded_probe(command, timeout):
            phases.append("probe")
            self.assert_use_blocks_delete()
            return probe(command, timeout)

        def guarded_runner(command, timeout):
            phases.append("ffmpeg")
            self.assert_use_blocks_delete()
            return self.runner(command, timeout)

        def guarded_manifest(**kwargs):
            phases.append("manifest")
            self.assert_use_blocks_delete()
            return writer(**kwargs)

        def guarded_transition(queue_id, state, **kwargs):
            if state is UploadQueueState.COMPLETED:
                phases.append("commit")
                self.assert_use_blocks_delete()
            return transition(queue_id, state, **kwargs)

        with patch.object(self.validator, "runner", guarded_probe), patch.object(self.writer, "write", guarded_manifest), patch.object(self.queue, "transition", guarded_transition):
            result = self.preparation(guarded_runner).process(item.queue_id)[0]
        self.assertTrue(result.succeeded)
        self.assertTrue({"probe", "ffmpeg", "manifest", "commit"}.issubset(phases))
        self.assert_no_uses()
        self.assertEqual(self.deletion.delete_video("recording").status, "deleted")
        self.assertTrue(result.export_path.is_file())

    def test_clip_keeps_lease_through_validator_ffmpeg_and_output_commit(self) -> None:
        probe = self.validator.runner

        def guarded_probe(command, timeout):
            self.assert_use_blocks_delete()
            return probe(command, timeout)

        def guarded_runner(command, timeout):
            self.assert_use_blocks_delete()
            return self.runner(command, timeout)

        with patch.object(self.validator, "runner", guarded_probe):
            result = self.clip(guarded_runner).export_clip(recording_id="recording", center_seconds=10)
        self.assertTrue(result.output_path.is_file())
        self.assert_no_uses()
        self.assertEqual(self.deletion.delete_video("recording").status, "deleted")
        self.assertTrue(result.output_path.is_file())

    def test_preparation_ffmpeg_exception_saves_failure_and_releases_lease(self) -> None:
        item = self.enqueue()

        def fail(_command, _timeout):
            self.assert_use_blocks_delete()
            raise OSError("injected ffmpeg failure")

        result = self.preparation(fail).process(item.queue_id)[0]
        self.assertEqual(result.state, UploadQueueState.FAILED)
        self.assertEqual(self.queue.get(item.queue_id).state, UploadQueueState.FAILED)
        self.assert_no_uses()
        self.assertEqual(self.deletion.delete_video("recording").status, "deleted")

    def test_manifest_exception_saves_failure_and_releases_lease(self) -> None:
        item = self.enqueue()
        with patch.object(self.writer, "write", side_effect=UploadManifestError("injected manifest failure")):
            result = self.preparation().process(item.queue_id)[0]
        self.assertEqual(result.state, UploadQueueState.FAILED)
        self.assert_no_uses()
        self.assertEqual(self.deletion.delete_video("recording").status, "deleted")

    def test_clip_ffmpeg_exception_releases_lease(self) -> None:
        def fail(_command, _timeout):
            self.assert_use_blocks_delete()
            raise OSError("injected ffmpeg failure")

        with self.assertRaises(ClipExportError):
            self.clip(fail).export_clip(recording_id="recording", center_seconds=10)
        self.assert_no_uses()
        self.assertEqual(self.deletion.delete_video("recording").status, "deleted")

    def test_pending_journal_refuses_preparation_before_validator_or_ffmpeg(self) -> None:
        item = self.enqueue()
        self.pending_intent()
        with patch.object(self.validator, "validate") as validator:
            result = self.preparation().process(item.queue_id)[0]
        self.assertEqual(result.state, UploadQueueState.FAILED)
        self.assertEqual(self.queue.get(item.queue_id).state, UploadQueueState.FAILED)
        validator.assert_not_called()
        self.assertEqual(self.commands, [])
        self.assert_no_uses()
        self.assertTrue(self.source.exists())

    def test_other_process_operation_lock_refusal_finishes_processing_queue_as_failed(self) -> None:
        item = self.enqueue()
        code = "from pathlib import Path\nimport sys\nfrom master_duel_recorder_lite.recording_lock import RecordingLock\nwith RecordingLock.acquire(Path(sys.argv[1]),recording_id='fixture'):\n print('ready',flush=True)\n sys.stdin.readline()\n"
        with self.child(code, self.paths.data / "video-operations.lock"):
            result = self.preparation().process(item.queue_id)[0]
        self.assertEqual(result.state, UploadQueueState.FAILED)
        self.assertEqual(self.queue.get(item.queue_id).state, UploadQueueState.FAILED)
        self.assertEqual(self.commands, [])
        self.assert_no_uses()
        self.assertTrue(self.source.exists())

    def test_source_changed_between_export_and_manifest_cannot_create_new_provenance(self) -> None:
        item = self.enqueue()
        writer = self.writer.write

        def changed_source(**kwargs):
            self.source.write_bytes(b"replaced after export")
            return writer(**kwargs)

        with patch.object(self.writer, "write", changed_source):
            result = self.preparation().process(item.queue_id)[0]
        self.assertEqual(result.state, UploadQueueState.FAILED)
        self.assertFalse((self.paths.queue / "manifests" / f"{item.queue_id}.json").exists())
        self.assert_no_uses()

    def cleanup_after_upload(self, prepared):
        uploads = YouTubeUploadRepository.from_runtime_paths(self.paths)
        created = uploads.create(recording_id="recording", metadata=prepared.metadata)
        saved = uploads.update(created, state=YouTubeUploadState.COMPLETED, prepare_queue_id=prepared.queue_id,
                               video_id="uploaded", watch_url="https://www.youtube.com/watch?v=uploaded")
        export = self.paths.root / prepared.export_path
        sent = YouTubeUploadResult("uploaded", "https://www.youtube.com/watch?v=uploaded", "private",
                                   media_path=export, media_size_bytes=export.stat().st_size,
                                   media_sha256=hashlib.sha256(export.read_bytes()).hexdigest())
        save_app_config(paths=self.paths, config=replace(AppConfig(), upload_delete_source_after_upload=True))
        outcome = PostUploadCleanupHandler(self.paths, deletion_service=self.deletion,
                                           lifecycle_repository=self.lifecycle, upload_repository=uploads, queue=self.queue)(saved, prepared, sent)
        self.assertEqual(uploads.get(saved.upload_id).state, YouTubeUploadState.COMPLETED)
        self.assertEqual(uploads.get(saved.upload_id).watch_url, sent.watch_url)
        return outcome

    def test_reused_old_mp4_without_manifest_does_not_gain_new_source_proof_or_delete_replacement(self) -> None:
        item = self.enqueue()
        old_export = self.paths.exports / "recording" / f"{item.queue_id}.mp4"
        old_export.parent.mkdir()
        old_export.write_bytes(b"old prepared export")
        replacement = b"replacement original with other contents"
        self.source.write_bytes(replacement)
        result = self.preparation().process(item.queue_id)[0]
        self.assertEqual(result.state, UploadQueueState.COMPLETED)
        self.assertIsNone(result.manifest_path)
        self.assertIn("出自記録がない", result.message)
        self.assertFalse((self.paths.queue / "manifests" / f"{item.queue_id}.json").exists())
        self.assertEqual(old_export.read_bytes(), b"old prepared export")
        prepared = self.queue.get(item.queue_id)
        self.assertIsNone(prepared.manifest_path)
        outcome = self.cleanup_after_upload(prepared)
        self.assertEqual((outcome.status, outcome.reason), ("skipped", "provenance_mismatch"))
        self.assertEqual(self.source.read_bytes(), replacement)
        self.assertEqual(self.lifecycle.pending_cleanup_requests(), ())
        self.assertEqual(self.lifecycle.pending_intents(), ())
        self.assert_no_uses()

    def test_reused_export_preserves_old_manifest_source_and_cleanup_keeps_replacement(self) -> None:
        item = self.enqueue()
        result = self.preparation().process(item.queue_id)[0]
        manifest_bytes = result.manifest_path.read_bytes()
        old_source_hash = json.loads(manifest_bytes)["source"]["sha256"]
        prepared = self.queue.get(item.queue_id)
        # 前回準備完了後の送信ではなく、準備確定直前の中断を人工fixtureで再現します。
        self.queue._save_items((replace(prepared, state=UploadQueueState.FAILED),))
        replacement = b"replacement original"
        self.source.write_bytes(replacement)
        reused = self.preparation().process(item.queue_id)[0]
        self.assertEqual(reused.state, UploadQueueState.COMPLETED)
        self.assertEqual(reused.manifest_path.read_bytes(), manifest_bytes)
        self.assertNotEqual(old_source_hash, hashlib.sha256(replacement).hexdigest())
        outcome = self.cleanup_after_upload(self.queue.get(item.queue_id))
        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(self.source.read_bytes(), replacement)
        self.assertEqual(self.lifecycle.pending_cleanup_requests(), ())

    def test_reused_export_changed_since_old_manifest_is_rejected(self) -> None:
        item = self.enqueue()
        result = self.preparation().process(item.queue_id)[0]
        manifest_bytes = result.manifest_path.read_bytes()
        prepared = self.queue.get(item.queue_id)
        self.queue._save_items((replace(prepared, state=UploadQueueState.FAILED),))
        result.export_path.write_bytes(b"changed MP4")
        retried = self.preparation().process(item.queue_id)[0]
        self.assertEqual(retried.state, UploadQueueState.FAILED)
        self.assertEqual(result.manifest_path.read_bytes(), manifest_bytes)
        self.assert_no_uses()
