import tempfile
import threading
import time
import unittest
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from master_duel_recorder_lite.application import (
    ApplicationOperationError,
    DuelManagementQuery,
    RecorderApplicationService,
    _automatic_capture_input,
    _remaining_poll_delay,
)
from master_duel_recorder_lite.auto_recording import (
    AutoRecordingEvent,
    AutoRecordingEventAction,
)
from master_duel_recorder_lite.capture_targets import CaptureInput, CaptureMode, CaptureTarget
from master_duel_recorder_lite.config import AppConfig
from master_duel_recorder_lite.detection import DetectionSignal, DuelObservation
from master_duel_recorder_lite.duel_records import DuelRecordValues
from master_duel_recorder_lite.ffmpeg import FfmpegVersion
from master_duel_recorder_lite.ffmpeg_setup import FfmpegInstallResult
from master_duel_recorder_lite.operation_state import OperationState
from master_duel_recorder_lite.preflight import CheckStatus, PreflightCheck, PreflightReport
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.visual_detection import DetectionCandidate
from master_duel_recorder_lite.visual_worker import VisualDetectionStatus


class RecorderApplicationServiceTest(unittest.TestCase):
    def test_watch_poll_delay_subtracts_capture_and_analysis_time(self) -> None:
        self.assertAlmostEqual(_remaining_poll_delay(0.5, 10.0, 10.2), 0.3)
        self.assertEqual(_remaining_poll_delay(0.5, 10.0, 10.6), 0.0)
        self.assertEqual(_remaining_poll_delay(0.5, 10.2, 10.0), 0.5)

    def test_automatic_recording_snapshot_uses_ffmpeg_session_start(self) -> None:
        service = RecorderApplicationService(user_data_dir=Path("user_data"))
        started_at = datetime.now(timezone.utc)
        prepared = SimpleNamespace(
            target=SimpleNamespace(
                recording_id="automatic-id",
                path=Path("recordings/automatic.mkv"),
            ),
            session=SimpleNamespace(
                state=RecordingState.RECORDING,
                started_at=started_at,
                result=None,
            ),
        )

        service._publish_automatic_snapshot(prepared)
        first = service.recording_snapshot()
        time.sleep(0.02)
        second = service.recording_snapshot()

        self.assertTrue(first.active)
        self.assertEqual(first.recording_id, "automatic-id")
        self.assertEqual(first.started_at, started_at)
        self.assertGreater(second.elapsed_seconds, first.elapsed_seconds)

        service._clear_automatic_snapshot()
        self.assertFalse(service.recording_snapshot().active)

    def test_stopping_finished_watch_preserves_failed_state(self) -> None:
        service = RecorderApplicationService(user_data_dir=Path("user_data"))
        service._operation_state.transition(OperationState.FAILED, "監視に失敗しました")
        service._watch_thread = SimpleNamespace(is_alive=lambda: False)

        service.stop_watch()

        self.assertIs(service.operation_snapshot().state, OperationState.FAILED)
        self.assertIsNone(service._watch_thread)

    def test_notification_failure_does_not_escape_service(self) -> None:
        service = RecorderApplicationService(user_data_dir=Path("user_data"))
        service._notifications.notify = Mock(side_effect=OSError("notification failed"))

        with patch.object(
            service,
            "load_config",
            return_value=SimpleNamespace(config=AppConfig()),
        ):
            service._notify("started", "録画開始", "recording:started")

    def test_visual_diagnostic_export_contains_latest_json_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            source = service.paths.logs / "visual-monitor"
            source.mkdir(parents=True)
            for index in range(12):
                (source / f"{index:02d}.json").write_text(
                    '{"schema_version":1}', encoding="utf-8"
                )
            (source / "capture.bmp").write_bytes(b"BM")

            target = service.export_visual_diagnostics(Path(tmp_dir) / "diagnostic.zip")
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()

        self.assertEqual(len(names), 10)
        self.assertTrue(all(name.endswith(".json") for name in names))
        self.assertNotIn("capture.bmp", names)

    def test_next_duel_boundary_stops_recording_after_one_second(self) -> None:
        service = RecorderApplicationService(user_data_dir=Path("user_data"))
        event = AutoRecordingEvent(
            AutoRecordingEventAction.STOPPED,
            "stopped",
            None,
            None,
            recording_id="recording",
        )
        prepared = SimpleNamespace(
            visual_abort_reason=None,
            session=SimpleNamespace(started_at=datetime.now(timezone.utc)),
            duel_confirmed=True,
            result_detected_monotonic=None,
            boundary_detected_monotonic=100.0,
            target=SimpleNamespace(recording_id="recording"),
        )
        controller = SimpleNamespace(
            current=prepared,
            manual_stop=Mock(return_value=event),
        )
        start_monitor = Mock()

        with patch("master_duel_recorder_lite.application.time.monotonic", return_value=101.1):
            stopped = service._apply_automatic_visual_lifecycle(
                controller,
                start_monitor,
                None,
            )

        self.assertIsNotNone(stopped)
        self.assertIn("次の対戦開始", stopped.message)
        controller.manual_stop.assert_called_once_with()
        start_monitor.reset.assert_called_once_with()

    def test_automatic_recording_uses_observed_desktop_region(self) -> None:
        observation = DuelObservation(
            DetectionSignal.PRESENT,
            0.9,
            "コイントスを検出",
            datetime.now(timezone.utc),
            capture_window_handle=42,
            capture_process_id=100,
            capture_window_title="Master Duel",
            capture_left=-3440,
            capture_top=0,
            capture_width=3440,
            capture_height=1440,
        )

        capture_input = _automatic_capture_input(observation)

        self.assertEqual(capture_input.input_name, "desktop")
        self.assertEqual(
            capture_input.options,
            (
                "-draw_mouse",
                "0",
                "-offset_x",
                "-3440",
                "-offset_y",
                "0",
                "-video_size",
                "3440x1440",
            ),
        )
        self.assertNotIn("title=", capture_input.input_name)

    def test_history_views_join_duel_fields_without_exposing_id_as_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            history = RecordingHistoryRepository.from_runtime_paths(service.paths)
            history.register_starting(
                recording_id="internal-id",
                output_path=service.paths.recordings / "duel.mkv",
                container="mkv",
                source="manual",
            )
            service.save_duel_record(
                "internal-id",
                DuelRecordValues(result="win", play_order="second", duel_type="ranked"),
                expected_revision=0,
            )

            view = service.list_history_views()[0]

        self.assertEqual(view.recording_id, "internal-id")
        self.assertEqual((view.result, view.play_order, view.duel_type), ("win", "second", "ranked"))

    def test_manual_duel_is_listed_with_recording_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            manual = service.create_manual_duel_record(
                DuelRecordValues(status="confirmed", result="win", own_deck="青眼"),
                occurred_at=datetime(2026, 8, 13, 12, tzinfo=timezone.utc),
            )

            views = service.list_history_views(
                query=DuelManagementQuery(entry_origin="manual")
            )

        self.assertEqual(len(views), 1)
        self.assertEqual(views[0].row_id, manual.duel_id)
        self.assertIsNone(views[0].entry)
        self.assertIsNone(views[0].recording_id)
        self.assertEqual(views[0].own_deck, "青眼")

    def test_manual_duel_write_is_rejected_during_watch(self) -> None:
        service = RecorderApplicationService()
        service._watch_thread = SimpleNamespace(is_alive=lambda: True)

        self.assertEqual(
            service.duel_write_block_reason(), "自動監視中のため更新できません"
        )
        with self.assertRaisesRegex(ApplicationOperationError, "自動監視中"):
            service.create_manual_duel_record(
                DuelRecordValues(status="confirmed"),
                occurred_at=datetime.now(timezone.utc),
            )

    def test_active_seasons_prioritize_rank_then_nearest_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            common = {"duel_type": "other", "start_date": date(2026, 8, 1)}
            service.add_season(
                name="イベントA", season_type="event", end_date=date(2026, 8, 14), **common
            )
            service.add_season(
                name="ランク", season_type="ranked", end_date=date(2026, 8, 31), **common
            )
            service.add_season(
                name="カスタム", season_type="custom", end_date=date(2026, 8, 15), **common
            )

            summaries = service.active_season_summaries(
                today=date(2026, 8, 13), limit=2
            )

        self.assertEqual(
            [item.season.name for item in summaries], ["ランク", "イベントA"]
        )

    def test_season_report_update_export_and_archive_use_application_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            season = service.add_season(
                name="レポート対象",
                season_type="custom",
                duel_type="other",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 31),
            )
            service.create_manual_duel_record(
                DuelRecordValues(
                    status="confirmed", result="win", season_id=season.season_id
                ),
                occurred_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
            )
            updated = service.update_season_report(
                season.season_id,
                report_notes="従来メモ",
                report_goal="目標",
                report_highlights="良かった点",
                report_challenges="課題",
                report_next_plan="次期方針",
                expected_revision=0,
            )
            report = service.get_season_report(
                season.season_id, use_default_comparison=False
            )
            output = service.export_season_report(
                report, service.paths.exports / "report.html"
            )
            archived = service.archive_season_report(season.season_id)

            self.assertEqual(updated.report_revision, 1)
            self.assertTrue(output.is_file())
            self.assertTrue(archived.is_archived)
            self.assertEqual(
                service.list_data_backups()[0].reason, "pre-season-archive"
            )

    def test_deleting_unreferenced_season_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            season = service.add_season(
                name="削除対象",
                season_type="custom",
                duel_type="other",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 31),
            )

            deleted = service.delete_season(season.season_id)

            self.assertEqual(deleted.season_id, season.season_id)
            self.assertEqual(
                service.list_data_backups()[0].reason, "pre-season-delete"
            )

    def test_manual_duel_can_be_deleted_without_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            created = service.create_manual_duel_record(
                DuelRecordValues(status="confirmed"),
                occurred_at=datetime.now(timezone.utc),
            )

            deleted = service.delete_duel_record(created.duel_id)
            backups = service.list_data_backups()

        self.assertEqual(deleted.duel_id, created.duel_id)
        self.assertEqual(service.list_history_views(), ())
        self.assertEqual(backups[0].reason, "pre-duel-delete")

    def test_new_duel_editor_inherits_last_values_but_existing_record_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            service = RecorderApplicationService(user_data_dir=root)
            history = RecordingHistoryRepository.from_runtime_paths(service.paths)
            for recording_id in ("first", "next", "existing"):
                history.register_starting(
                    recording_id=recording_id,
                    output_path=service.paths.recordings / f"{recording_id}.mkv",
                    container="mkv",
                    source="manual",
                )
            previous = DuelRecordValues(
                duel_type="ranked",
                own_deck="青眼",
                opponent_deck="烙印",
                tags=("大会",),
            )
            service.save_duel_record(
                "existing",
                DuelRecordValues(
                    duel_type="room",
                    own_deck="閃刀姫",
                    opponent_deck="ラビュリンス",
                    tags=("友人戦",),
                ),
                expected_revision=0,
            )
            service.save_duel_record("first", previous, expected_revision=0)

            new_data = service.get_duel_editor_data("next")
            existing_data = service.get_duel_editor_data("existing")

        self.assertIsNone(new_data.record)
        self.assertEqual(new_data.values.duel_type, "ranked")
        self.assertEqual(new_data.values.own_deck, "青眼")
        self.assertEqual(new_data.values.opponent_deck, "烙印")
        self.assertEqual(new_data.values.tags, ("大会",))
        self.assertIsNotNone(existing_data.record)
        self.assertEqual(existing_data.values.own_deck, "閃刀姫")
        self.assertEqual(
            {entry.name for entry in new_data.decks},
            {"青眼", "烙印", "閃刀姫", "ラビュリンス"},
        )

    def test_history_delete_is_rejected_while_manual_start_is_reserved(self) -> None:
        service = RecorderApplicationService()
        service._manual_starting = True

        with self.assertRaisesRegex(ApplicationOperationError, "実行中"):
            service.delete_history("recording")

    def test_manual_start_reservation_is_released_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(
                user_data_dir=Path(tmp_dir) / "user_data"
            )
            target = CaptureTarget(
                CaptureMode.DESKTOP, "desktop", "デスクトップ全体"
            )
            failed_report = PreflightReport(
                (
                    PreflightCheck(
                        "capture",
                        "録画入力",
                        CheckStatus.ERROR,
                        "利用できません",
                    ),
                )
            )
            passed_report = PreflightReport(
                (PreflightCheck("all", "環境", CheckStatus.OK, "利用可能"),)
            )
            with (
                patch(
                    "master_duel_recorder_lite.application.run_preflight",
                    side_effect=(failed_report, passed_report),
                ),
                patch(
                    "master_duel_recorder_lite.application.prepare_recording",
                    side_effect=RuntimeError("retry reached preparation"),
                ),
            ):
                with self.assertRaises(ApplicationOperationError):
                    service.start_recording(target)
                with self.assertRaisesRegex(RuntimeError, "retry reached preparation"):
                    service.start_recording(target)

    def test_slow_manual_start_does_not_hold_service_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            output = root / "data" / "recordings" / "recording.mkv"
            now = datetime.now(timezone.utc)
            result = RecordingResult(
                RecordingState.COMPLETED,
                output,
                0,
                now,
                now,
                100,
                None,
                (),
            )
            session = SimpleNamespace(
                state=RecordingState.RECORDING,
                started_at=now,
                result=None,
            )
            prepared = SimpleNamespace(
                target=SimpleNamespace(recording_id="recording-id", path=output),
                session=session,
                start=lambda **_kwargs: RecordingState.RECORDING,
                poll=lambda: session.state,
                stop=lambda: result,
                release=lambda: None,
                visual_detection_status=VisualDetectionStatus(
                    "disabled", "disabled", 0, 0, 0
                ),
            )
            report = PreflightReport(
                (PreflightCheck("all", "環境", CheckStatus.OK, "利用可能"),)
            )
            entered_preflight = threading.Event()
            continue_preflight = threading.Event()
            failures: list[BaseException] = []

            def delayed_preflight(**_kwargs: object) -> PreflightReport:
                entered_preflight.set()
                if not continue_preflight.wait(2):
                    raise TimeoutError("test did not release preflight")
                return report

            service = RecorderApplicationService(user_data_dir=root)
            target = CaptureTarget(CaptureMode.DESKTOP, "desktop", "デスクトップ全体")

            def start() -> None:
                try:
                    service.start_recording(target)
                except BaseException as exc:
                    failures.append(exc)

            with (
                patch(
                    "master_duel_recorder_lite.application.run_preflight",
                    side_effect=delayed_preflight,
                ),
                patch(
                    "master_duel_recorder_lite.application.prepare_recording",
                    return_value=prepared,
                ),
            ):
                thread = threading.Thread(target=start)
                thread.start()
                self.assertTrue(entered_preflight.wait(1))

                before = time.monotonic()
                snapshot = service.recording_snapshot()
                elapsed = time.monotonic() - before

                self.assertFalse(snapshot.active)
                self.assertLess(elapsed, 0.25)
                with self.assertRaisesRegex(ApplicationOperationError, "開始処理"):
                    service.start_recording(target)
                with self.assertRaisesRegex(ApplicationOperationError, "開始処理中"):
                    service.start_watch()
                with self.assertRaisesRegex(ApplicationOperationError, "開始処理中"):
                    service.close()

                continue_preflight.set()
                thread.join(2)

            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue(service.recording_snapshot().active)
            service.stop_recording()

    def test_select_capture_target_persists_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            target = CaptureTarget(
                CaptureMode.WINDOW,
                "window:42",
                "ウィンドウ: Master Duel",
                window_handle=42,
                window_title="Master Duel",
            )

            saved = service.select_capture_target(target)
            loaded = service.load_config().config

        self.assertEqual(saved.capture_mode, "window")
        self.assertEqual(saved.capture_target_id, "window:42")
        self.assertEqual(loaded, saved)

    def test_unavailable_target_is_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            target = CaptureTarget(
                CaptureMode.MASTER_DUEL,
                "master_duel",
                "Master Duelウィンドウ",
                available=False,
            )

            with self.assertRaises(ApplicationOperationError):
                service.select_capture_target(target)

        self.assertFalse((service.paths.config / "app.toml").exists())

    def test_manual_recording_starts_and_stops_through_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            output = root / "data" / "recordings" / "recording.mkv"
            now = datetime.now(timezone.utc)
            result = RecordingResult(
                RecordingState.COMPLETED,
                output,
                0,
                now,
                now,
                100,
                None,
                (),
            )
            released: list[bool] = []
            session = SimpleNamespace(
                state=RecordingState.RECORDING,
                started_at=now,
                result=None,
            )
            prepared = SimpleNamespace(
                target=SimpleNamespace(recording_id="recording-id", path=output),
                session=session,
                start=lambda **_kwargs: RecordingState.RECORDING,
                poll=lambda: session.state,
                stop=lambda: result,
                release=lambda: released.append(True),
                visual_detection_status=VisualDetectionStatus(
                    "disabled", "disabled", 0, 0, 0
                ),
            )
            report = PreflightReport((PreflightCheck("all", "環境", CheckStatus.OK, "利用可能"),))
            service = RecorderApplicationService(user_data_dir=root)
            target = CaptureTarget(CaptureMode.DESKTOP, "desktop", "デスクトップ全体")
            with (
                patch("master_duel_recorder_lite.application.run_preflight", return_value=report),
                patch(
                    "master_duel_recorder_lite.application.prepare_recording",
                    return_value=prepared,
                ) as prepare,
            ):
                started = service.start_recording(target)
                stopped = service.stop_recording()

        self.assertTrue(started.active)
        self.assertEqual(started.recording_id, "recording-id")
        self.assertFalse(stopped.active)
        self.assertIs(stopped.state, RecordingState.COMPLETED)
        self.assertEqual(released, [True])
        self.assertFalse(prepare.call_args.kwargs["enable_visual_detection"])

    def test_manual_master_duel_target_is_refreshed_when_recording_starts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            output = root / "data" / "recordings" / "recording.mkv"
            now = datetime.now(timezone.utc)
            result = RecordingResult(
                RecordingState.COMPLETED,
                output,
                0,
                now,
                now,
                100,
                None,
                (),
            )
            session = SimpleNamespace(
                state=RecordingState.RECORDING,
                started_at=now,
                result=None,
            )
            prepared = SimpleNamespace(
                target=SimpleNamespace(recording_id="recording-id", path=output),
                session=session,
                start=lambda **_kwargs: RecordingState.RECORDING,
                poll=lambda: session.state,
                stop=lambda: result,
                release=lambda: None,
                visual_detection_status=VisualDetectionStatus(
                    "disabled", "disabled", 0, 0, 0
                ),
            )
            report = PreflightReport(
                (PreflightCheck("all", "環境", CheckStatus.OK, "利用可能"),)
            )
            stale_target = CaptureTarget(
                CaptureMode.MASTER_DUEL,
                "master_duel",
                "Master Duelウィンドウ",
                available=False,
            )
            fresh_input = CaptureInput(
                "gdigrab",
                "title=masterduel",
                window_handle=123,
                window_title="masterduel",
            )
            service = RecorderApplicationService(user_data_dir=root)
            with (
                patch(
                    "master_duel_recorder_lite.application.run_preflight",
                    return_value=report,
                ),
                patch(
                    "master_duel_recorder_lite.application.resolve_configured_capture",
                    return_value=fresh_input,
                ) as resolve,
                patch(
                    "master_duel_recorder_lite.application.prepare_recording",
                    return_value=prepared,
                ) as prepare,
            ):
                service.start_recording(stale_target)
                service.stop_recording()

        resolve.assert_called_once()
        self.assertIs(prepare.call_args.kwargs["capture_input"], fresh_input)
        self.assertFalse(prepare.call_args.kwargs["enable_visual_detection"])

    def test_recording_browsing_delegates_to_shared_service(self) -> None:
        reference = object()
        browser = SimpleNamespace(
            resolve=lambda recording_id: ("resolve", recording_id),
            play=lambda recording_id: ("play", recording_id, reference),
            reveal=lambda recording_id: ("reveal", recording_id),
        )
        service = RecorderApplicationService(recording_browser=browser)  # type: ignore[arg-type]

        self.assertEqual(service.resolve_recording("id"), ("resolve", "id"))
        self.assertEqual(service.play_recording("id"), ("play", "id", reference))
        self.assertEqual(service.reveal_recording("id"), ("reveal", "id"))

    def test_ffmpeg_installation_path_is_saved_to_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            destination = root / "tools" / "ffmpeg"
            executable = destination / "bin" / "ffmpeg.exe"
            ffprobe = destination / "bin" / "ffprobe.exe"
            result = FfmpegInstallResult(
                destination,
                executable,
                ffprobe,
                FfmpegVersion("8.1.2", (8, 1, 2), 60),
                "a" * 64,
            )
            installer = SimpleNamespace(install=lambda _destination, progress=None: result)
            service = RecorderApplicationService(
                user_data_dir=root / "user_data",
                ffmpeg_installer=installer,  # type: ignore[arg-type]
            )

            installed = service.install_ffmpeg(destination)
            configured = service.load_config().config.ffmpeg_path

        self.assertEqual(installed, result)
        self.assertEqual(configured, str(executable))

    def test_automatic_start_candidate_is_saved_at_recording_origin(self) -> None:
        service = RecorderApplicationService(user_data_dir=Path("user_data"))
        candidate = DetectionCandidate(
            "duel_start",
            2500,
            0.9,
            "3フレーム合意",
            "detector",
            "1",
        )
        repository = SimpleNamespace(add=lambda *args, **kwargs: (args, kwargs))

        with patch(
            "master_duel_recorder_lite.application.DuelTimelineRepository.from_runtime_paths",
            return_value=repository,
        ) as factory:
            with patch.object(repository, "add", wraps=repository.add) as add:
                service._save_automatic_start_candidate("recording", candidate, None)

        factory.assert_called_once_with(service.paths)
        add.assert_called_once()
        _, keyword = add.call_args
        self.assertEqual(keyword["elapsed_ms"], 0)
        self.assertEqual(keyword["event_type"], "duel_start")
        self.assertEqual(keyword["status"], "candidate")
        self.assertEqual(keyword["confidence"], 0.9)

    def test_watch_reports_error_when_visual_detection_is_disabled(self) -> None:
        service = RecorderApplicationService(user_data_dir=Path("user_data"))
        config = AppConfig(visual_detection_enabled=False)
        events = []

        with patch.object(
            service,
            "load_config",
            return_value=SimpleNamespace(config=config, config_loaded=True),
        ):
            service._watch_loop(events.append)

        self.assertEqual(events[0].kind, "error")
        self.assertIn("画面イベント判定", events[0].message)
        self.assertEqual(events[-1].state, "stopped")

    def test_watch_reports_failed_preflight_details(self) -> None:
        service = RecorderApplicationService(user_data_dir=Path("user_data"))
        report = PreflightReport(
            (
                PreflightCheck("ffmpeg", "FFmpeg", CheckStatus.OK, "9.0.0"),
                PreflightCheck(
                    "capabilities",
                    "録画能力",
                    CheckStatus.ERROR,
                    "FFmpeg能力検査が終了コード3221225794で失敗しました",
                ),
                PreflightCheck(
                    "inputs",
                    "録画入力",
                    CheckStatus.ERROR,
                    "FFmpeg能力を確認できないため入力列挙を中止しました",
                ),
            )
        )
        events = []

        with (
            patch.object(
                service,
                "load_config",
                return_value=SimpleNamespace(config=AppConfig(), config_loaded=True),
            ),
            patch("master_duel_recorder_lite.application.run_preflight", return_value=report),
        ):
            service._watch_loop(events.append)

        self.assertEqual(events[0].kind, "error")
        self.assertIn("録画能力:", events[0].message)
        self.assertIn("終了コード3221225794", events[0].message)
        self.assertIn("録画入力:", events[0].message)

    def test_watch_stop_during_start_observation_does_not_begin_recording(self) -> None:
        service = RecorderApplicationService(user_data_dir=Path("user_data"))
        config = AppConfig()
        report = PreflightReport((PreflightCheck("all", "環境", CheckStatus.OK, "利用可能"),))
        process = Mock()
        controller = SimpleNamespace(current=None, process=process)

        def observe_and_stop():
            service._watch_stop.set()
            return SimpleNamespace()

        start_monitor = SimpleNamespace(
            observe=observe_and_stop,
            status=VisualDetectionStatus("waiting", "waiting", 0, 0, 0),
            start_candidate=None,
        )
        with (
            patch.object(
                service,
                "load_config",
                return_value=SimpleNamespace(config=config, config_loaded=True),
            ),
            patch("master_duel_recorder_lite.application.run_preflight", return_value=report),
            patch(
                "master_duel_recorder_lite.application.discover_ffmpeg",
                return_value=SimpleNamespace(found=True, executable=Path("ffmpeg.exe").resolve()),
            ),
            patch("master_duel_recorder_lite.application.GameWindowMonitor"),
            patch("master_duel_recorder_lite.application.MasterDuelWindowDetector"),
            patch("master_duel_recorder_lite.application.PersistentFfmpegRegionFrameCapture"),
            patch("master_duel_recorder_lite.application.VisualDiagnosticSession"),
            patch(
                "master_duel_recorder_lite.application.MasterDuelStartMonitor",
                return_value=start_monitor,
            ),
            patch(
                "master_duel_recorder_lite.application.AutoRecordingController",
                return_value=controller,
            ),
        ):
            service._watch_stop.clear()
            service._watch_loop(None)

        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
