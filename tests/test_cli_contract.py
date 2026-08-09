import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from master_duel_recorder_lite.__main__ import build_parser, configure_standard_streams


class CliContractTest(unittest.TestCase):
    def test_standard_stream_configuration_handles_japanese_on_cp1252(self) -> None:
        output = io.BytesIO()
        stream = io.TextIOWrapper(output, encoding="cp1252", errors="strict")

        with patch.object(sys, "stdout", stream):
            configure_standard_streams()
            print("日本語ヘルプ")
            stream.flush()

        self.assertEqual(stream.encoding, "utf-8")
        self.assertEqual(output.getvalue().decode("utf-8").splitlines(), ["日本語ヘルプ"])

    def test_root_help_lists_core_commands_and_safety_notice(self) -> None:
        help_text = build_parser().format_help()

        for command in ("config", "doctor", "status", "record", "watch", "history", "duel", "recovery", "prepare"):
            self.assertIn(command, help_text)
        self.assertIn("安全確認", help_text)

    def test_representative_argument_error_has_code_summary_and_action(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["history", "show"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("E_ARGUMENT", error.getvalue())
        self.assertIn("必須引数がありません", error.getvalue())
        self.assertIn("対処:", error.getvalue())

    def test_recovery_and_config_help_explain_destructive_controls(self) -> None:
        recovery_output = io.StringIO()
        with redirect_stdout(recovery_output), self.assertRaises(SystemExit):
            build_parser().parse_args(["recovery", "--help"])
        config_output = io.StringIO()
        with redirect_stdout(config_output), self.assertRaises(SystemExit):
            build_parser().parse_args(["config", "reset", "--help"])

        self.assertIn("元録画を上書きしません", recovery_output.getvalue())
        self.assertIn("--yes", config_output.getvalue())


if __name__ == "__main__":
    unittest.main()
