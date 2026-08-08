import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from master_duel_recorder_lite.__main__ import main
from master_duel_recorder_lite.operational_status import OperationalStatus


class StatusCliTest(unittest.TestCase):
    def test_json_output_is_machine_readable_and_matches_exit_code(self) -> None:
        document = {
            "schema_version": 1,
            "overall": "warning",
            "environment": {"status": "ok", "checks": []},
            "runtime": {"status": "ok", "directories": {}},
            "recording": {"status": "ok", "state": "idle", "recording_id": None},
            "history": {"status": "ok", "total": 2, "consistency_issues": 0},
            "recovery": {"status": "warning", "pending": 1},
            "upload_queue": {"status": "ok", "total": 0},
            "errors": [],
        }
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch(
                    "master_duel_recorder_lite.__main__.collect_operational_status",
                    return_value=OperationalStatus(document, 4),
                ),
                redirect_stdout(output),
            ):
                code = main(["--user-data-dir", str(Path(tmp_dir) / "user_data"), "status", "--json"])

        self.assertEqual(code, 4)
        self.assertEqual(json.loads(output.getvalue()), document)


if __name__ == "__main__":
    unittest.main()
