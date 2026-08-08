import json
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.ffmpeg import CommandResult
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.runtime_paths import RuntimePaths
from master_duel_recorder_lite.upload_export import UploadExporter, UploadExportStatus
from master_duel_recorder_lite.upload_media import UploadMediaValidator


PROBE_JSON = json.dumps(
    {
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"format_name": "mov,mp4", "duration": "5.0"},
    }
)


class UploadExporterTest(unittest.TestCase):
    def make_context(self, root: Path) -> tuple[RuntimePaths, Path, UploadMediaValidator]:
        paths = default_runtime_paths(user_data_dir=root / "user_data")
        ensure_runtime_dirs(paths)
        source = paths.recordings / "source.mkv"
        source.write_bytes(b"original")
        validator = UploadMediaValidator(
            ffprobe_executable=root / "ffprobe.exe",
            runner=lambda command, _timeout: CommandResult(
                0,
                PROBE_JSON
                if Path(command[-1]).suffix.lower() == ".mp4"
                else PROBE_JSON.replace("mov,mp4", "matroska,webm"),
                "",
            ),
        )
        return paths, source, validator

    def test_success_is_atomically_finalized_without_changing_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths, source, validator = self.make_context(root)
            before = source.read_bytes()

            def runner(command: tuple[str, ...], _timeout: float) -> CommandResult:
                Path(command[-1]).write_bytes(b"export")
                return CommandResult(0, "", "")

            exporter = UploadExporter(
                paths=paths,
                ffmpeg_executable=root / "ffmpeg.exe",
                validator=validator,
                runner=runner,
            )

            result = exporter.export(recording_id="recording", queue_id="queue", source_path=source)
            preserved = source.read_bytes()

        self.assertIs(result.status, UploadExportStatus.COMPLETED)
        assert result.output_path is not None
        self.assertEqual(result.output_path.name, "queue.mp4")
        self.assertEqual(before, preserved)

    def test_failure_and_cancellation_never_create_final_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths, source, validator = self.make_context(root)

            def failing(command: tuple[str, ...], _timeout: float) -> CommandResult:
                Path(command[-1]).write_bytes(b"partial")
                return CommandResult(1, "", "failed")

            failed = UploadExporter(
                paths=paths,
                ffmpeg_executable=root / "ffmpeg.exe",
                validator=validator,
                runner=failing,
            ).export(recording_id="recording", queue_id="failed", source_path=source)
            cancelled = UploadExporter(
                paths=paths,
                ffmpeg_executable=root / "ffmpeg.exe",
                validator=validator,
                runner=failing,
            ).export(
                recording_id="recording",
                queue_id="cancelled",
                source_path=source,
                cancel_requested=lambda: True,
            )

        self.assertIs(failed.status, UploadExportStatus.FAILED)
        self.assertIs(cancelled.status, UploadExportStatus.CANCELLED)
        self.assertIsNone(failed.output_path)
        self.assertIsNone(cancelled.output_path)


if __name__ == "__main__":
    unittest.main()
