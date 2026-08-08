import unittest

from master_duel_recorder_lite.recording_failure import (
    FailureCode,
    RecoveryPolicy,
    classify_recording_failure,
)


class RecordingFailureTest(unittest.TestCase):
    def test_each_failure_maps_to_one_recovery_policy(self) -> None:
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
                RecoveryPolicy.MANUAL_REVIEW,
            ),
            (
                dict(error="No space left", returncode=1, output_exists=True, output_size=10),
                FailureCode.STORAGE_FULL,
                RecoveryPolicy.RETRYABLE,
            ),
            (
                dict(error="failed", returncode=1, output_exists=False, output_size=0),
                FailureCode.OUTPUT_MISSING,
                RecoveryPolicy.UNRECOVERABLE,
            ),
            (
                dict(error="failed", returncode=1, output_exists=True, output_size=0),
                FailureCode.OUTPUT_EMPTY,
                RecoveryPolicy.UNRECOVERABLE,
            ),
            (
                dict(error="operation timeout", returncode=1, output_exists=True, output_size=10),
                FailureCode.OPERATION_TIMEOUT,
                RecoveryPolicy.RETRYABLE,
            ),
            (
                dict(error="encoder crash", returncode=7, output_exists=True, output_size=10),
                FailureCode.PROCESS_CRASH,
                RecoveryPolicy.MANUAL_REVIEW,
            ),
        )
        for arguments, code, policy in cases:
            with self.subTest(code=code):
                classification = classify_recording_failure(**arguments)
                self.assertIs(classification.code, code)
                self.assertIs(classification.policy, policy)

    def test_unknown_failure_is_not_treated_as_success(self) -> None:
        classification = classify_recording_failure(
            error="secret internal detail",
            returncode=None,
            output_exists=True,
            output_size=10,
        )

        self.assertIs(classification.code, FailureCode.UNKNOWN)
        self.assertIs(classification.policy, RecoveryPolicy.MANUAL_REVIEW)
        self.assertNotIn("secret", classification.user_message)
        self.assertIn("secret", classification.internal_diagnostic)


if __name__ == "__main__":
    unittest.main()
