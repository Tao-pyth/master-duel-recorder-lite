from datetime import datetime, timezone
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.ffmpeg import discover_ffmpeg
from master_duel_recorder_lite.media_recovery import InspectionStatus, MediaRecoveryService
from master_duel_recorder_lite.recording_failure import classify_recording_failure
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository


@unittest.skipUnless(
    os.getenv("MDRL_RUN_FFMPEG_SMOKE") == "1",
    "MDRL_RUN_FFMPEG_SMOKE=1 のときだけ実FFmpegを使用します",
)
class RealRecoverySmokeTest(unittest.TestCase):
    def test_remux_preserves_original_and_produces_decodable_copy(self) -> None:
        discovery = discover_ffmpeg("ffmpeg")
        if not discovery.found or discovery.executable is None:
            self.skipTest("FFmpegが見つかりません")

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            recordings = root / "recordings"
            recordings.mkdir()
            original = recordings / "synthetic.mkv"
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
                    "-t",
                    "1",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(original),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )
            self.assertEqual(
                generated.returncode,
                0,
                generated.stderr.decode("utf-8", errors="replace"),
            )
            repository = RecordingHistoryRepository(
                database_path=root / "db" / "history.sqlite3",
                recordings_root=recordings,
            )
            now = datetime.now(timezone.utc)
            repository.register_starting(
                recording_id="recovery-smoke",
                output_path=original,
                container="mkv",
                source="manual",
                created_at=now,
            )
            repository.mark_interrupted(
                "recovery-smoke",
                classification=classify_recording_failure(
                    error="simulated application interruption",
                    returncode=None,
                    output_exists=True,
                    output_size=original.stat().st_size,
                    interrupted=True,
                ),
                ended_at=now,
                size_bytes=original.stat().st_size,
            )
            original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            service = MediaRecoveryService(
                repository=repository,
                ffmpeg_executable=discovery.executable,
            )

            inspection = service.inspect("recovery-smoke")
            repaired = service.repair("recovery-smoke")
            after_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            decoded = subprocess.run(
                [
                    str(discovery.executable),
                    "-v",
                    "error",
                    "-i",
                    str(repaired.output_path),
                    "-map",
                    "0:v:0",
                    "-f",
                    "null",
                    "-",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )

        self.assertIs(inspection.status, InspectionStatus.VALID)
        self.assertTrue(repaired.succeeded)
        self.assertEqual(original_hash, after_hash)
        self.assertEqual(decoded.returncode, 0, decoded.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
