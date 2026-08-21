import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.offline_analysis import (
    OfflineAnalysisMode,
    OfflineAnalysisService,
)
from master_duel_recorder_lite.upload_media import (
    MediaValidationStatus,
    UploadMediaValidation,
)


class FakeValidator:
    def __init__(self, validation: UploadMediaValidation) -> None:
        self.validation = validation

    def validate(self, _path: Path) -> UploadMediaValidation:
        return self.validation


class OfflineAnalysisTest(unittest.TestCase):
    def test_analyze_returns_read_only_summary_without_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "replay.mp4"
            path.write_bytes(b"video")
            validation = UploadMediaValidation(
                MediaValidationStatus.WARNING,
                path,
                "mp4",
                12.5,
                ("video",),
                ("音声ストリームがありません",),
                (),
            )
            service = OfflineAnalysisService(validator=FakeValidator(validation))  # type: ignore[arg-type]

            report = service.analyze(path, mode=OfflineAnalysisMode.REPLAY)

        document = report.to_dict()
        self.assertEqual(document["source_name"], "replay.mp4")
        self.assertEqual(document["mode"], "replay")
        self.assertEqual(document["candidate_count"], 0)
        self.assertNotIn(str(path.parent), str(document))


if __name__ == "__main__":
    unittest.main()
