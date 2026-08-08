import json
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.ffmpeg import CommandResult
from master_duel_recorder_lite.upload_media import (
    MediaValidationStatus,
    UploadMediaValidator,
    parse_upload_probe,
)


def probe_document(*, audio: bool = True, video: bool = True, duration: str = "5.0") -> str:
    streams = []
    if video:
        streams.append({"index": 0, "codec_type": "video", "codec_name": "h264"})
    if audio:
        streams.append({"index": 1, "codec_type": "audio", "codec_name": "aac"})
    return json.dumps(
        {"streams": streams, "format": {"format_name": "matroska,webm", "duration": duration}}
    )


class UploadMediaTest(unittest.TestCase):
    def test_probe_parser_distinguishes_valid_silent_and_missing_video(self) -> None:
        path = Path("video.mkv")

        valid = parse_upload_probe(path, probe_document())
        silent = parse_upload_probe(path, probe_document(audio=False))
        missing_video = parse_upload_probe(path, probe_document(video=False))

        self.assertIs(valid.status, MediaValidationStatus.VALID)
        self.assertIs(silent.status, MediaValidationStatus.WARNING)
        self.assertTrue(silent.eligible)
        self.assertIs(missing_video.status, MediaValidationStatus.INVALID)

    def test_empty_and_corrupt_files_are_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            empty = Path(tmp_dir) / "empty.mkv"
            empty.touch()
            corrupt = Path(tmp_dir) / "corrupt.mkv"
            corrupt.write_bytes(b"not media")
            validator = UploadMediaValidator(
                ffprobe_executable=Path(tmp_dir) / "ffprobe.exe",
                runner=lambda _command, _timeout: CommandResult(1, "", "invalid data"),
            )

            empty_result = validator.validate(empty)
            corrupt_result = validator.validate(corrupt)

        self.assertIs(empty_result.status, MediaValidationStatus.INVALID)
        self.assertIs(corrupt_result.status, MediaValidationStatus.INVALID)
        self.assertIn("invalid data", corrupt_result.errors[0])


if __name__ == "__main__":
    unittest.main()
