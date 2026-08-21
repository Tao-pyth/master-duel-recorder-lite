import unittest

from master_duel_recorder_lite.auto_check import AutoCheckStatus, evaluate_auto_check
from master_duel_recorder_lite.preflight import CheckStatus, PreflightCheck, PreflightReport


class AutoCheckTest(unittest.TestCase):
    def test_preflight_error_becomes_failed_user_reason(self) -> None:
        report = PreflightReport(
            (PreflightCheck("ffmpeg", "FFmpeg", CheckStatus.ERROR, "見つかりません"),)
        )

        result = evaluate_auto_check(preflight=report, duration_seconds=30)

        self.assertEqual(result.status, AutoCheckStatus.FAILED)
        self.assertIn("FFmpeg", result.reasons[0])

    def test_warning_without_frames_keeps_environment_usable(self) -> None:
        report = PreflightReport(
            (PreflightCheck("audio", "音声", CheckStatus.WARNING, "無効です"),)
        )

        result = evaluate_auto_check(preflight=report, duration_seconds=30)

        self.assertEqual(result.status, AutoCheckStatus.WARNING)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.sampled_frames, 0)

    def test_clean_preflight_is_ready_even_before_sampling(self) -> None:
        report = PreflightReport(
            (PreflightCheck("all", "環境", CheckStatus.OK, "利用できます"),)
        )

        result = evaluate_auto_check(preflight=report, duration_seconds=30)

        self.assertEqual(result.status, AutoCheckStatus.READY)
        self.assertTrue(result.to_dict()["headline"])


if __name__ == "__main__":
    unittest.main()
