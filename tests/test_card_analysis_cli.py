"""通常CLIの新しい名前空間と、配布workerの起動経路を隔離して確認する。"""
from __future__ import annotations

from contextlib import nullcontext, redirect_stderr, redirect_stdout
from dataclasses import replace
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from master_duel_recorder_lite import __main__ as cli
from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.config import AppConfig, save_app_config
from test_gui_refresh_and_review_lifecycle import register_video


class CardAnalysisCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.service = RecorderApplicationService(user_data_dir=self.root)
        self.addCleanup(self.service.close)

    def run_cli(self, arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(["--user-data-dir", str(self.root), "card-analysis", *arguments])
        return code, output.getvalue(), errors.getvalue()

    def test_targets_save_alias_edit_disable_and_conflict_are_persistent(self):
        code, output, error = self.run_cli(["targets", "save", "--name", "CLI対象", "--alias", "別表記", "--purpose", "妨害", "--json"])
        self.assertEqual((code, error), (0, ""))
        target = json.loads(output)
        self.assertEqual(target["aliases"], ["別表記"])
        code, _, error = self.run_cli(["targets", "disable", target["target_id"], "--expected-revision", str(target["revision"]), "--json"])
        self.assertEqual((code, error), (0, ""))
        code, output, _ = self.run_cli(["targets", "list", "--json"])
        self.assertEqual(code, 0)
        saved = next(item for item in json.loads(output) if item["target_id"] == target["target_id"])
        self.assertFalse(saved["enabled"])
        code, _, error = self.run_cli(["targets", "disable", target["target_id"], "--expected-revision", str(target["revision"])])
        self.assertEqual(code, cli.EXIT_OPERATION)
        self.assertIn("競合", error)
        self.assertEqual(next(item for item in self.service.list_card_targets() if item.target_id == target["target_id"]).revision, saved["revision"])

    def test_zero_results_are_unanalysed_and_off_cannot_enqueue(self):
        register_video(self.service, "cli-recording")
        code, output, _ = self.run_cli(["results", "cli-recording", "--json"])
        self.assertEqual(code, 0)
        review = json.loads(output)
        self.assertIsNone(review["job"])
        self.assertEqual(review["nonuses"], [])
        code, _, error = self.run_cli(["analyse", "cli-recording", "--timeout", ".01"])
        self.assertEqual(code, cli.EXIT_OPERATION)
        self.assertIn("ON", error)
        code, output, _ = self.run_cli(["jobs", "--recording-id", "cli-recording", "--json"])
        self.assertEqual((code, json.loads(output)), (0, []))

    def test_pending_timeout_keeps_job_and_closes_service(self):
        register_video(self.service, "cli-recording")
        save_app_config(paths=self.service.paths, config=replace(AppConfig(), card_analysis_enabled=True))
        created = []

        def create_service(**kwargs):
            service = RecorderApplicationService(**kwargs)
            service.close = Mock(wraps=service.close)
            created.append(service)
            return service

        with patch.object(RecorderApplicationService, "_ensure_card_manager", return_value=SimpleNamespace(wake=Mock())), \
                patch.object(cli, "RecorderApplicationService", side_effect=create_service):
            code, output, error = self.run_cli(["analyse", "cli-recording", "--timeout", ".01", "--json"])
        self.assertEqual(code, cli.EXIT_ATTENTION)
        self.assertIn("E_CARD_TIMEOUT", error)
        job = json.loads(output)
        self.assertEqual(job["state"], "queued")
        self.assertEqual(self.service.get_card_analysis_job(job["job_id"]).state, "queued")
        created[0].close.assert_called_once()

    def test_diagnose_is_database_independent_and_classifies_model_error(self):
        from master_duel_recorder_lite.card_ocr import CardAnalysisError
        with patch.object(cli, "RecorderApplicationService", side_effect=AssertionError("diagnose must not open runtime data")), \
                patch("master_duel_recorder_lite.card_ocr.diagnose_ocr_models", return_value={"available": True, "models": []}):
            code, output, error = self.run_cli(["diagnose", "--json"])
        self.assertEqual((code, error), (0, ""))
        self.assertTrue(json.loads(output)["available"])
        with patch("master_duel_recorder_lite.card_ocr.diagnose_ocr_models", side_effect=CardAnalysisError("model_missing", "モデルなし")):
            code, _, error = self.run_cli(["diagnose", "--json"])
        self.assertEqual(code, cli.EXIT_OPERATION)
        self.assertIn("E_CARD_MODEL_MISSING", error)

    def test_worker_route_bypasses_public_cli_parser(self):
        with patch.object(cli, "build_parser", side_effect=AssertionError("worker must bypass CLI parser")), \
                patch("master_duel_recorder_lite.card_analysis_worker.main", return_value=7) as worker:
            self.assertEqual(cli.main(["--card-analysis-worker", "--request", "isolated.json"]), 7)
        worker.assert_called_once_with(["--request", "isolated.json"])

    def test_gui_entry_routes_worker_and_diagnose_before_gui_import(self):
        path = Path(__file__).resolve().parents[1] / "packaging" / "mdrl_gui_entry.py"
        spec = importlib.util.spec_from_file_location("isolated_gui_entry", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.dict("sys.modules", {"master_duel_recorder_lite.pyside_gui": None}), \
                patch("master_duel_recorder_lite.card_analysis_worker.main", return_value=6) as worker:
            self.assertEqual(module.main(["--card-analysis-worker", "--request", "isolated.json"]), 6)
        worker.assert_called_once_with(["--request", "isolated.json"])
        with patch.dict("sys.modules", {"master_duel_recorder_lite.pyside_gui": None}), \
                patch("master_duel_recorder_lite.card_analysis_worker.worker_standard_streams", return_value=nullcontext()), \
                patch.object(cli, "main", return_value=0) as diagnose:
            self.assertEqual(module.main(["--card-analysis-diagnose"]), 0)
        diagnose.assert_called_once_with(["card-analysis", "diagnose", "--json"])


if __name__ == "__main__":
    unittest.main()
