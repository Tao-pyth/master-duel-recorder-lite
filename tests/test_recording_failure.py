import unittest

from master_duel_recorder_lite.recording_failure import (
    FailureCode,
    classify_recording_failure,
)


class RecordingFailureTest(unittest.TestCase):
    def test_each_failure_maps_to_one_failure_code(self) -> None:
        cases = (
            (
                dict(
                    error="process disappeared",
                    returncode=None,
                    output_exists=True,
                    output_size=10,
                    interrupted=True,
                ),
                FailureCode.APPLICATION_INTERRUPTED,
            ),
            (
                dict(
                    error="No space left",
                    returncode=1,
                    output_exists=True,
                    output_size=10,
                ),
                FailureCode.STORAGE_FULL,
            ),
            (
                dict(error="failed", returncode=1, output_exists=False, output_size=0),
                FailureCode.OUTPUT_MISSING,
            ),
            (
                dict(error="failed", returncode=1, output_exists=True, output_size=0),
                FailureCode.OUTPUT_EMPTY,
            ),
            (
                dict(
                    error="operation timeout",
                    returncode=1,
                    output_exists=True,
                    output_size=10,
                ),
                FailureCode.OPERATION_TIMEOUT,
            ),
            (
                dict(
                    error="encoder crash",
                    returncode=7,
                    output_exists=True,
                    output_size=10,
                ),
                FailureCode.PROCESS_CRASH,
            ),
        )
        for arguments, code in cases:
            with self.subTest(code=code):
                classification = classify_recording_failure(**arguments)
                self.assertIs(classification.code, code)

    def test_unknown_failure_is_not_treated_as_success(self) -> None:
        classification = classify_recording_failure(
            error="secret internal detail",
            returncode=None,
            output_exists=True,
            output_size=10,
        )

        self.assertIs(classification.code, FailureCode.UNKNOWN)
        self.assertNotIn("secret", classification.user_message)
        self.assertIn("secret", classification.internal_diagnostic)


if __name__ == "__main__":
    unittest.main()
