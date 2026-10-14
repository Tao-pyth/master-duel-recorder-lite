from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.clip_export import ClipExportService
from master_duel_recorder_lite.ffmpeg import CommandResult
from master_duel_recorder_lite.history_database import connect_history_database
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_retention import RecordingDeletionService
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.upload_export import UploadExporter
from master_duel_recorder_lite.upload_manifest import UploadManifestWriter
from master_duel_recorder_lite.upload_media import UploadMediaValidator
from master_duel_recorder_lite.upload_metadata import UploadMetadata
from master_duel_recorder_lite.upload_preparation import UploadPreparationService
from master_duel_recorder_lite.upload_queue import UploadQueueStore
from master_duel_recorder_lite.video_lifecycle import VideoLifecycleRepository
from master_duel_recorder_lite.video_source_use import VideoSourceUseError, video_source_use


PROBE_DOCUMENT = {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
                  "format": {"format_name": "matroska,webm", "duration": "60.0"}}


class SourceUseFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = default_runtime_paths(user_data_dir=self.root / "runtime")
        ensure_runtime_dirs(self.paths)
        self.history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        self.source = self.paths.recordings / "source.mkv"
        self.source.write_bytes(b"fixture original")
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.history.register_starting(recording_id="recording", output_path=self.source, container="mkv", source="manual", created_at=now)
        self.history.finalize("recording", RecordingResult(
            RecordingState.COMPLETED, self.source, 0, now, now + timedelta(seconds=60), self.source.stat().st_size, None, (),
        ))
        self.lifecycle = VideoLifecycleRepository.from_runtime_paths(self.paths)
        self.deletion = RecordingDeletionService(self.paths, repository=self.lifecycle)
        self.queue = UploadQueueStore(self.paths)
        self.writer = UploadManifestWriter(self.paths)
        self.validator = UploadMediaValidator(ffprobe_executable=self.root / "ffprobe.exe", runner=self.probe)
        self.commands = []

    @contextmanager
    def connection(self):
        with closing(connect_history_database(self.history.database_path)) as connection, connection:
            yield connection

    def probe(self, command, _timeout):
        document = json.loads(json.dumps(PROBE_DOCUMENT))
        if Path(command[-1]).suffix == ".mp4":
            document["format"]["format_name"] = "mov,mp4"
        return CommandResult(0, json.dumps(document), "")

    def runner(self, command, _timeout):
        self.commands.append(command)
        Path(command[-1]).write_bytes(b"fixture export")
        return CommandResult(0, "", "")

    def preparation(self, runner=None):
        return UploadPreparationService(
            paths=self.paths, repository=self.history, queue=self.queue,
            exporter=UploadExporter(paths=self.paths, ffmpeg_executable=self.root / "ffmpeg.exe", validator=self.validator, runner=runner or self.runner),
            manifest_writer=self.writer,
        )

    def clip(self, runner=None):
        return ClipExportService(paths=self.paths, repository=self.history, ffmpeg_executable=self.root / "ffmpeg.exe",
                                 validator=self.validator, runner=runner or self.runner)

    def enqueue(self):
        return self.preparation().enqueue(recording_id="recording", metadata=UploadMetadata("fixture"))

    def assert_no_uses(self):
        asset = self.lifecycle.get_current_asset("recording")
        if asset is not None:
            self.assertEqual(self.lifecycle.list_uses(asset.asset_id), ())

    def pending_intent(self):
        asset = self.lifecycle.ensure_current_asset("recording")
        stat = self.source.stat()
        asset = self.lifecycle.set_verified_asset(asset.asset_id, size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns,
                                                  sha256=hashlib.sha256(self.source.read_bytes()).hexdigest())
        return self.lifecycle.create_intent(
            recording_id="recording", asset_id=asset.asset_id, generation=asset.generation, reason="manual",
            original_path=asset.path, staged_path=".mdrl-video-staging/" + "a" * 32 + ".pending",
            size_bytes=asset.size_bytes, mtime_ns=asset.mtime_ns, sha256=asset.sha256, backup_path=str(self.root / "fixture-backup"),
        )

    @contextmanager
    def child(self, code, argument):
        process = subprocess.Popen([sys.executable, "-c", code, str(argument)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            yield process
        finally:
            try:
                _output, error = process.communicate("finish\n", timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                _output, error = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, error)


class VideoSourceUseTest(SourceUseFixture):
    def test_lease_blocks_deletion_and_release_allows_deletion(self) -> None:
        with video_source_use(self.paths, "recording", purpose="test_playback") as use:
            self.assertEqual(use.path, self.source)
            self.assertEqual(use.lease.asset_id, use.asset.asset_id)
            result = self.deletion.delete_video("recording")
            self.assertEqual((result.status, result.reason), ("skipped", "source_in_use"))
            self.assertTrue(self.source.exists())
        self.assert_no_uses()
        self.assertEqual(self.deletion.delete_video("recording").status, "deleted")

    def test_consumer_exception_releases_lease(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "consumer failure"):
            with video_source_use(self.paths, "recording", purpose="test"):
                raise RuntimeError("consumer failure")
        self.assert_no_uses()
        self.assertEqual(self.deletion.delete_video("recording").status, "deleted")

    def test_pending_intent_refuses_new_source_use(self) -> None:
        self.pending_intent()
        with self.assertRaises(VideoSourceUseError):
            with video_source_use(self.paths, "recording", purpose="test"):
                self.fail("must not open source")
        self.assert_no_uses()
        self.assertTrue(self.source.exists())

    def test_deleted_or_unavailable_asset_refuses_source_use(self) -> None:
        asset = self.lifecycle.ensure_current_asset("recording")
        for state in ("deleted", "unavailable"):
            with self.connection() as connection:
                connection.execute("UPDATE recording_video_assets SET state=? WHERE asset_id=?", (state, asset.asset_id))
            with self.subTest(state=state), self.assertRaises(VideoSourceUseError):
                with video_source_use(self.paths, "recording", purpose="test"):
                    self.fail("must not open source")
            self.assert_no_uses()

    def test_source_disappears_or_generation_changes_before_open_releases_lease(self) -> None:
        asset = self.lifecycle.ensure_current_asset("recording")
        with patch("master_duel_recorder_lite.video_source_use.VideoLifecycleRepository.get_current_asset", return_value=replace(asset, generation=2)):
            with self.assertRaises(VideoSourceUseError):
                with video_source_use(self.paths, "recording", purpose="test"):
                    self.fail("must not open changed source")
        self.assert_no_uses()
        self.source.unlink()
        with self.assertRaises(VideoSourceUseError):
            with video_source_use(self.paths, "recording", purpose="test"):
                self.fail("must not open missing source")
        self.assert_no_uses()

    def test_another_process_holding_operation_lock_refuses_source_use(self) -> None:
        code = "from pathlib import Path\nimport sys\nfrom master_duel_recorder_lite.recording_lock import RecordingLock\nwith RecordingLock.acquire(Path(sys.argv[1]), recording_id='fixture'):\n print('ready', flush=True)\n sys.stdin.readline()\n"
        with self.child(code, self.paths.data / "video-operations.lock"):
            with self.assertRaises(VideoSourceUseError):
                with video_source_use(self.paths, "recording", purpose="test"):
                    self.fail("must not race deletion")
        self.assert_no_uses()
        self.assertTrue(self.source.exists())

    def test_live_reader_in_another_process_blocks_deletion(self) -> None:
        code = "from pathlib import Path\nimport sys\nfrom master_duel_recorder_lite.runtime_paths import default_runtime_paths\nfrom master_duel_recorder_lite.video_source_use import video_source_use\npaths=default_runtime_paths(user_data_dir=Path(sys.argv[1]))\nwith video_source_use(paths,'recording',purpose='fixture'):\n print('ready', flush=True)\n sys.stdin.readline()\n"
        with self.child(code, self.paths.root):
            result = self.deletion.delete_video("recording")
            self.assertEqual((result.status, result.reason), ("skipped", "source_in_use"))
            self.assertTrue(self.source.exists())
        self.assert_no_uses()
        self.assertEqual(self.deletion.delete_video("recording").status, "deleted")


if __name__ == "__main__":
    unittest.main()
