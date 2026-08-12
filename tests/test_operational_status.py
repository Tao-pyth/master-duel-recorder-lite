import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.config import AppConfig, LoadedAppConfig
from master_duel_recorder_lite.operational_status import collect_operational_status
from master_duel_recorder_lite.preflight import (
    CheckStatus,
    PreflightCheck,
    PreflightReport,
)
from master_duel_recorder_lite.runtime_paths import (
    default_runtime_paths,
    ensure_runtime_dirs,
)


def successful_preflight(**_kwargs: object) -> PreflightReport:
    return PreflightReport(
        (
            PreflightCheck(
                "config", "設定", CheckStatus.OK, "app.tomlを読み込みました"
            ),
            PreflightCheck(
                "ffmpeg", "FFmpeg", CheckStatus.OK, "C:/Users/private/ffmpeg.exeを検出"
            ),
        )
    )


class OperationalStatusTest(unittest.TestCase):
    def test_empty_runtime_reports_all_subsystems_without_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            loaded = LoadedAppConfig(AppConfig(), paths.config / "app.toml", True)

            status = collect_operational_status(
                paths=paths,
                loaded=loaded,
                preflight_runner=successful_preflight,
            )

        self.assertEqual(status.exit_code, 0)
        self.assertEqual(status.document["overall"], "ok")
        self.assertEqual(status.document["recording"]["state"], "idle")
        self.assertEqual(status.document["history"]["total"], 0)
        self.assertNotIn("recovery", status.document)
        self.assertEqual(status.document["upload_queue"]["total"], 0)
        self.assertNotIn(tmp_dir, str(status.document))

    def test_queue_corruption_is_error_and_other_sections_remain_available(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            (paths.queue / "upload-preparation.json").write_text(
                "not json", encoding="utf-8"
            )
            loaded = LoadedAppConfig(AppConfig(), paths.config / "app.toml", True)

            status = collect_operational_status(
                paths=paths,
                loaded=loaded,
                preflight_runner=successful_preflight,
            )

        self.assertEqual(status.exit_code, 3)
        self.assertEqual(status.document["overall"], "error")
        self.assertEqual(status.document["history"]["total"], 0)
        self.assertEqual(status.document["upload_queue"]["status"], "error")
        self.assertEqual(status.document["errors"][0]["code"], "E_STATUS_QUEUE")

    def test_environment_failure_is_not_reported_as_success(self) -> None:
        failed_report = PreflightReport(
            (PreflightCheck("ffmpeg", "FFmpeg", CheckStatus.ERROR, "見つかりません"),)
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            loaded = LoadedAppConfig(AppConfig(), paths.config / "app.toml", False)

            status = collect_operational_status(
                paths=paths,
                loaded=loaded,
                preflight_runner=lambda **_kwargs: failed_report,
            )

        self.assertEqual(status.exit_code, 2)
        self.assertEqual(status.document["overall"], "error")


if __name__ == "__main__":
    unittest.main()
