from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.__main__ import main
from master_duel_recorder_lite.ffmpeg import discover_ffmpeg
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.upload_manifest import validate_upload_manifest
from master_duel_recorder_lite.upload_queue import UploadQueueState, UploadQueueStore


@unittest.skipUnless(
    os.getenv("MDRL_RUN_FFMPEG_SMOKE") == "1",
    "MDRL_RUN_FFMPEG_SMOKE=1 のときだけ実FFmpegを使用します",
)
class RealUploadPreparationSmokeTest(unittest.TestCase):
    def test_full_offline_preparation_preserves_source_and_decodes_export(self) -> None:
        discovery = discover_ffmpeg("ffmpeg")
        if not discovery.found or discovery.executable is None:
            self.skipTest("FFmpegが見つかりません")

        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            source = paths.recordings / "synthetic.mkv"
            generated = subprocess.run(
                [
                    str(discovery.executable),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=320x240:rate=10",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=1000:sample_rate=48000",
                    "-t",
                    "1",
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(source),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )
            self.assertEqual(
                generated.returncode,
                0,
                generated.stderr.decode("utf-8", errors="replace"),
            )
            now = datetime.now(timezone.utc)
            repository = RecordingHistoryRepository.from_runtime_paths(paths)
            repository.register_starting(
                recording_id="upload-smoke",
                output_path=source,
                container="mkv",
                source="manual",
                created_at=now,
            )
            repository.mark_recording("upload-smoke", started_at=now)
            repository.finalize(
                "upload-smoke",
                RecordingResult(
                    RecordingState.COMPLETED,
                    source,
                    0,
                    now,
                    now,
                    source.stat().st_size,
                    None,
                    (),
                ),
            )
            queue = UploadQueueStore(paths)
            before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            with redirect_stdout(io.StringIO()):
                enqueue_code = main(
                    [
                        "--user-data-dir",
                        str(paths.root),
                        "prepare",
                        "enqueue",
                        "upload-smoke",
                        "--title",
                        "Synthetic duel",
                    ]
                )
            item = queue.list()[0]
            with redirect_stdout(io.StringIO()):
                run_code = main(
                    ["--user-data-dir", str(paths.root), "prepare", "run", item.queue_id]
                )
            after_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            stored = queue.get(item.queue_id)
            assert stored is not None
            assert stored.export_path is not None and stored.manifest_path is not None
            export_path = paths.root / stored.export_path
            manifest_path = paths.root / stored.manifest_path
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            decoded = subprocess.run(
                [
                    str(discovery.executable),
                    "-v",
                    "error",
                    "-i",
                    str(export_path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0",
                    "-f",
                    "null",
                    "-",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )

        validate_upload_manifest(manifest)
        self.assertEqual((enqueue_code, run_code), (0, 0))
        self.assertIs(stored.state, UploadQueueState.COMPLETED)
        self.assertEqual(before_hash, after_hash)
        self.assertEqual(manifest["metadata"]["privacy"], "private")
        self.assertEqual(decoded.returncode, 0, decoded.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
