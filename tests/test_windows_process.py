import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from master_duel_recorder_lite.windows_process import (
    SEM_FAILCRITICALERRORS,
    SEM_NOGPFAULTERRORBOX,
    WINDOWS_DLL_INIT_FAILED,
    configure_windows_process_errors,
    is_transient_windows_process_failure,
    run_with_windows_retry,
    subprocess_creation_flags,
)


class WindowsProcessTest(unittest.TestCase):
    def test_windows_creation_uses_no_window_flag(self) -> None:
        with patch(
            "master_duel_recorder_lite.windows_process.platform.system",
            return_value="Windows",
        ):
            self.assertEqual(
                subprocess_creation_flags(),
                getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )

    def test_non_windows_creation_uses_no_flags(self) -> None:
        with patch(
            "master_duel_recorder_lite.windows_process.platform.system",
            return_value="Linux",
        ):
            self.assertEqual(subprocess_creation_flags(), 0)

    def test_windows_error_mode_suppresses_child_error_dialogs(self) -> None:
        kernel32 = SimpleNamespace(
            GetErrorMode=Mock(return_value=0x8000),
            SetErrorMode=Mock(),
        )
        with (
            patch(
                "master_duel_recorder_lite.windows_process.platform.system",
                return_value="Windows",
            ),
            patch(
                "master_duel_recorder_lite.windows_process.ctypes.WinDLL",
                return_value=kernel32,
                create=True,
            ),
            patch(
                "master_duel_recorder_lite.windows_process._error_mode_configured",
                False,
            ),
        ):
            configure_windows_process_errors()

        kernel32.SetErrorMode.assert_called_once_with(
            0x8000 | SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX
        )

    def test_dll_initialization_failure_is_retried_once(self) -> None:
        failure = SimpleNamespace(returncode=WINDOWS_DLL_INIT_FAILED)
        success = SimpleNamespace(returncode=0)
        operation = Mock(side_effect=(failure, success))
        sleeper = Mock()

        with patch(
            "master_duel_recorder_lite.windows_process.platform.system",
            return_value="Windows",
        ):
            result = run_with_windows_retry(operation, sleeper=sleeper)

        self.assertIs(result, success)
        self.assertEqual(operation.call_count, 2)
        sleeper.assert_called_once_with(0.2)

    def test_signed_dll_initialization_failure_is_recognized(self) -> None:
        signed = WINDOWS_DLL_INIT_FAILED - 2**32
        with patch(
            "master_duel_recorder_lite.windows_process.platform.system",
            return_value="Windows",
        ):
            self.assertTrue(is_transient_windows_process_failure(signed))

    def test_regular_failure_is_not_retried(self) -> None:
        failure = SimpleNamespace(returncode=1)
        operation = Mock(return_value=failure)

        with patch(
            "master_duel_recorder_lite.windows_process.platform.system",
            return_value="Windows",
        ):
            result = run_with_windows_retry(operation, sleeper=Mock())

        self.assertIs(result, failure)
        operation.assert_called_once_with()

    def test_dll_initialization_failure_is_retried_only_once(self) -> None:
        failure = SimpleNamespace(returncode=WINDOWS_DLL_INIT_FAILED)
        operation = Mock(return_value=failure)

        with patch(
            "master_duel_recorder_lite.windows_process.platform.system",
            return_value="Windows",
        ):
            result = run_with_windows_retry(operation, sleeper=Mock())

        self.assertIs(result, failure)
        self.assertEqual(operation.call_count, 2)


if __name__ == "__main__":
    unittest.main()
