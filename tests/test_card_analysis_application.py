from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from master_duel_recorder_lite.application import ApplicationEvent, RecorderApplicationService
from master_duel_recorder_lite.card_analysis_models import CardObservationValues
from master_duel_recorder_lite.card_analysis_repository import CardAnalysisError
from master_duel_recorder_lite.operation_state import OperationState
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState


class CardAnalysisApplicationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdrl-card-service-test-")
        self.addCleanup(self.temp.cleanup)
        self.service = RecorderApplicationService(user_data_dir=Path(self.temp.name))
        self.addCleanup(self.service.close)
        self.repository = self.service._get_card_repository()
        self.source = self.service.paths.recordings / "fixture.mkv"
        self.source.parent.mkdir(parents=True, exist_ok=True)
        self.source.write_bytes(b"isolated movie fixture")
        with closing(sqlite3.connect(self.repository.database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO recordings (recording_id,state,source,output_path,container,created_at,updated_at) "
                "VALUES ('one','completed','manual','fixture.mkv','mkv','2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00')"
            )
        self.manager = Mock()
        self.manager_patch = patch.object(self.service, "_ensure_card_manager", return_value=self.manager)
        self.manager_patch.start()
        self.addCleanup(self.manager_patch.stop)

    def enable(self):
        self.service.save_settings({"card_analysis.enabled": "true"})

    def test_default_off_does_not_create_job_and_explicit_request_explains_setting(self):
        self.service._enqueue_completed_card_analysis("one")
        self.assertEqual(self.service.get_card_analysis_jobs(), ())
        with self.assertRaisesRegex(CardAnalysisError, "ON"):
            self.service.request_card_analysis("one")

    def test_explicit_job_uses_snapshot_and_does_not_hash_on_calling_thread(self):
        self.enable()
        job = self.service.request_card_analysis("one")
        self.assertEqual(job.fingerprint.sha256, "")
        self.assertEqual(job.sample_fps, 2.0)
        target = self.service.list_card_targets()[0]
        self.service.save_card_target(target_id=target.target_id, name="変更後", enabled=False,
                                      expected_revision=target.revision)
        original = next(row for row in job.target_snapshot if row.target_id == target.target_id)
        self.assertEqual(original.name, target.name)
        self.assertEqual(self.source.read_bytes(), b"isolated movie fixture")

    def test_failed_or_active_recording_is_never_enqueued(self):
        self.enable()
        for state in ("failed", "starting", "recording"):
            with self.subTest(state=state):
                with closing(sqlite3.connect(self.repository.database_path)) as connection, connection:
                    connection.execute("UPDATE recordings SET state=? WHERE recording_id='one'", (state,))
                with self.assertRaisesRegex(CardAnalysisError, "正常完了"):
                    self.service.request_card_analysis("one")
        self.assertEqual(self.service.get_card_analysis_jobs(), ())

    def test_three_completion_routes_enqueue_completed_only(self):
        for failed in (False, True):
            with self.subTest(failed=failed), patch.object(self.service, "_enqueue_completed_card_analysis") as enqueue:
                state = RecordingState.FAILED if failed else RecordingState.COMPLETED
                now = datetime.now(timezone.utc)
                result = RecordingResult(state, self.source, 1 if failed else 0, now, now,
                                         self.source.stat().st_size, None, ())
                prepared = SimpleNamespace(target=SimpleNamespace(recording_id="one"),
                    stop=lambda: result, release=Mock(), visual_detection_status=self.service._visual_status,
                    poll=lambda: state)
                self.service._operation_state = type(self.service._operation_state)()
                self.service._operation_state.transition(OperationState.MANUAL_STARTING, "test")
                self.service._operation_state.transition(OperationState.MANUAL_RECORDING, "test")
                self.service._current = prepared
                self.service.stop_recording()
                self.assertEqual(enqueue.call_count, 0 if failed else 1)
                enqueue.reset_mock()
                self.service._operation_state = type(self.service._operation_state)()
                self.service._operation_state.transition(OperationState.MANUAL_STARTING, "test")
                self.service._operation_state.transition(OperationState.MANUAL_RECORDING, "test")
                self.service._current = prepared
                self.service._collect_manual_terminal_locked()
                self.assertEqual(enqueue.call_count, 0 if failed else 1)
                enqueue.reset_mock()
                self.service._emit(None, ApplicationEvent("stopped", "test", recording_id="one", state=state.value))
                self.assertEqual(enqueue.call_count, 0 if failed else 1)

    def test_analysis_error_does_not_change_successful_recording(self):
        self.enable()
        with patch.object(self.service, "request_card_analysis", side_effect=OSError("fixture failure")):
            self.service._enqueue_completed_card_analysis("one")
        self.assertEqual(self.service.get_history("one").state, "completed")
        self.assertEqual(self.service.card_analysis_error, "fixture failure")

    def test_explicit_human_confirmation_promotes_detail_with_known_action_only(self):
        self.enable()
        job = self.service.request_card_analysis("one")
        self.repository.claim_job(job.job_id)
        target = job.target_snapshot[0]
        self.repository.complete_job(job.job_id, [CardObservationValues(
            target.target_id, target.name, "unknown", "unknown", 1000,
            time_kind="detail_observed", evidence={"supports_use": False}, source_key="detail-one",
        ).to_dict()], fingerprint={**job.fingerprint.to_dict(), "sha256": "a" * 64})
        observation, = self.service.get_card_analysis_review("one").observations
        with self.assertRaisesRegex(CardAnalysisError, "使用者と行動"):
            self.service.confirm_card_observation(observation.event_id, expected_revision=observation.revision)
        confirmed = self.service.update_card_observation(observation.event_id, expected_revision=observation.revision,
            actor="self", action="activate", status="confirmed", is_opener=True, opener_expected_revision=0)
        self.assertTrue(confirmed.values.supports_use)
        self.assertTrue(confirmed.evidence["manually_confirmed_use"])
        self.assertEqual(confirmed.time_kind, "detail_observed")
        self.assertIsNone(confirmed.actual_ms)
        self.assertTrue(confirmed.is_opener)


if __name__ == "__main__":
    unittest.main()
