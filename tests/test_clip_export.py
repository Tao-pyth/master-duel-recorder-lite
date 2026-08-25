import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from master_duel_recorder_lite.clip_export import (
    ClipExportError,
    ClipExportService,
    resolve_clip_range,
)
from master_duel_recorder_lite.ffmpeg import CommandResult
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.upload_media import UploadMediaValidator


PROBE_JSON = json.dumps(
    {
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"format_name": "mov,mp4", "duration": "60.0"},
    }
)


class ClipExportTest(unittest.TestCase):
    def test_clip_range_is_clamped_to_recording_bounds(self) -> None:
        clip = resolve_clip_range(
            center_seconds=10.0,
            before_seconds=30.0,
            after_seconds=30.0,
            duration_seconds=45.0,
        )

        self.assertEqual(clip.start_seconds, 0.0)
        self.assertEqual(clip.duration_seconds, 40.0)

    def test_clip_range_clamps_end_to_duration(self) -> None:
        clip = resolve_clip_range(
            center_seconds=40.0,
            before_seconds=5.0,
            after_seconds=30.0,
            duration_seconds=45.0,
        )

        self.assertEqual(clip.start_seconds, 35.0)
        self.assertEqual(clip.duration_seconds, 10.0)

    def test_negative_values_are_rejected(self) -> None:
        with self.assertRaises(ClipExportError):
            resolve_clip_range(
                center_seconds=1.0,
                before_seconds=-1.0,
                after_seconds=1.0,
                duration_seconds=10.0,
            )

    def test_temporary_clip_output_keeps_mp4_suffix_for_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            ensure_runtime_dirs(paths)
            source = paths.recordings / "source.mkv"
            source.write_bytes(b"original")
            repository = SimpleNamespace(
                get=lambda _recording_id: SimpleNamespace(
                    state="completed",
                    output_path=Path("source.mkv"),
                    duration_seconds=60.0,
                )
            )
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
            output_arguments: list[Path] = []

            def runner(command: tuple[str, ...], _timeout: float) -> CommandResult:
                output = Path(command[-1])
                output_arguments.append(output)
                output.write_bytes(b"clip")
                return CommandResult(0, "", "")

            result = ClipExportService(
                paths=paths,
                repository=repository,
                ffmpeg_executable=root / "ffmpeg.exe",
                validator=validator,
                runner=runner,
            ).export_clip(recording_id="recording", center_seconds=12.345)

        self.assertEqual(output_arguments[0].suffix, ".mp4")
        self.assertIn(".partial", output_arguments[0].name)
        self.assertEqual(result.output_path.suffix, ".mp4")
        self.assertTrue(result.output_path.name.startswith("recording-0000012345-"))


if __name__ == "__main__":
    unittest.main()
