from __future__ import annotations

from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.card_ocr import CardAnalysisError, CardOcrBackend, MODEL_HASHES, OcrLine, resolve_ocr_models
from master_duel_recorder_lite.card_video_analysis import (
    CardFrameAnalyzer, LogEntry, LogEventTracker, Target, analyze_video, extract_log_entries,
    match_target, parse_targets, source_fingerprint,
)
from master_duel_recorder_lite import card_analysis_worker
from scripts.build_windows_exe import build_command, UPDATER_EXECUTABLE_NAME


def line(text: str, top: int = 10, confidence: float = .8) -> OcrLine:
    return OcrLine(text, confidence, ((10., float(top)), (200., float(top)),
                                     (200., float(top + 20)), (10., float(top + 20))))


def entry(name: str = "alpha", action: str = "activate", actor: str = "self", target_id: str | None = "target-1") -> LogEntry:
    target = Target(target_id, name, (name,)) if target_id is not None else None
    return LogEntry(name, actor, action, target, .8, {"kind": "log_use", "supports_use": True, "row_center_y": 10})


class FakeImage:
    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self.shape = (height, width, 3)

    def __getitem__(self, key: tuple[slice, slice]) -> FakeImage:
        ys, xs = key
        return FakeImage(xs.stop - xs.start, ys.stop - ys.start)


class EmptyBackend:
    def recognize(self, image: object) -> list[OcrLine]:
        return []


CV = types.ModuleType("cv2")
for index, name in enumerate(("CAP_PROP_FPS", "CAP_PROP_FRAME_COUNT", "CAP_PROP_POS_MSEC", "CAP_PROP_POS_FRAMES")):
    setattr(CV, name, index)


class FakeCapture:
    def __init__(self, *, frames: int = 20, expected_frames: int = 20, fps: float = 10, broken_clock: bool = False) -> None:
        self.frames, self.expected_frames, self.fps, self.broken_clock = frames, expected_frames, fps, broken_clock
        self.index, self.released = 0, False

    def isOpened(self) -> bool:
        return True

    def get(self, key: int) -> float:
        if key == CV.CAP_PROP_FPS:
            return self.fps
        if key == CV.CAP_PROP_FRAME_COUNT:
            return self.expected_frames
        if key == CV.CAP_PROP_POS_FRAMES:
            return self.index
        return 0 if self.broken_clock else max(0, self.index - 1) * 1000 / self.fps

    def set(self, key: int, value: float) -> bool:
        if key == CV.CAP_PROP_POS_FRAMES:
            self.index = round(value)
        elif key == CV.CAP_PROP_POS_MSEC:
            self.index = round(value * self.fps / 1000)
        else:
            return False
        return True

    def grab(self) -> bool:
        return self.read()[0]

    def read(self) -> tuple[bool, FakeImage | None]:
        if self.index >= self.frames:
            return False, None
        self.index += 1
        return True, FakeImage()

    def release(self) -> None:
        self.released = True


class VariableCapture(FakeCapture):
    timestamps = (0, 100, 500, 900, 1200, 1600, 2100, 2500, 3100, 3800, 4400, 5000, 5500, 5900)

    def __init__(self, *, truncated: bool = False) -> None:
        super().__init__(frames=12 if truncated else len(self.timestamps), expected_frames=60)

    def get(self, key: int) -> float:
        if key == CV.CAP_PROP_POS_MSEC:
            return self.timestamps[min(max(0, self.index - 1), len(self.timestamps) - 1)]
        return super().get(key)


class CardOcrSafetyTest(unittest.TestCase):
    def test_missing_or_changed_model_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(CardAnalysisError) as caught:
                resolve_ocr_models(root)
            self.assertEqual(caught.exception.code, "model_missing")
            for name in MODEL_HASHES:
                (root / name).write_bytes(b"wrong model")
            with self.assertRaises(CardAnalysisError) as caught:
                resolve_ocr_models(root)
            self.assertEqual(caught.exception.code, "model_hash_mismatch")

    def test_frozen_assets_are_used_without_network_or_package_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "card_ocr_models").mkdir()
            (root / "card_ocr_models" / "model.onnx").write_bytes(b"model")
            hashes = {"model.onnx": hashlib.sha256(b"model").hexdigest()}
            with patch("master_duel_recorder_lite.card_ocr.MODEL_HASHES", hashes), patch("sys._MEIPASS", str(root), create=True):
                result = resolve_ocr_models()
            self.assertEqual(result["model.onnx"], root / "card_ocr_models" / "model.onnx")

    def test_invalid_ocr_result_is_not_an_empty_success(self) -> None:
        backend = object.__new__(CardOcrBackend)
        backend.engine = lambda *args, **kwargs: types.SimpleNamespace(txts=["name"], boxes=None, scores=[.8])
        with self.assertRaises(CardAnalysisError) as caught:
            backend.recognize(object())
        self.assertEqual(caught.exception.code, "ocr_inference_failed")


class CardExtractionTest(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy optional dependency is unavailable")
    def test_multilingual_action_variant_preserves_original_ocr_evidence(self) -> None:
        import numpy as np

        image = np.zeros((837, 300, 3), dtype=np.uint8)
        targets = parse_targets([{"target_id": "target-1", "name": "テストカード"}])
        rows = extract_log_entries([line("テストカード", 10), line("特殊召唤", 50)], image, targets, (0, 0, 300, 837))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].action, "special_summon")
        self.assertEqual(rows[0].target.target_id, "target-1")
        self.assertTrue(rows[0].evidence["supports_use"])
        self.assertEqual(rows[0].evidence["action_text"], "特殊召唤")

    def test_alias_normalization_and_ambiguous_matching(self) -> None:
        targets = parse_targets([{"target_id": "target-1", "name": "テストカード", "aliases": ["ＴＥＳＴ"], "enabled": True}])
        self.assertEqual(match_target(" TEST ", targets)[0].target_id, "target-1")
        self.assertIsNone(match_target("unknown", targets))
        self.assertIsNone(match_target("TEST", targets + (Target("target-2", "other", ("test",)),)))

    def test_empty_and_invalid_targets_fail(self) -> None:
        for values in ([], [{"id": True, "name": "name"}], [{"id": 1, "name": "name"}],
                       [{"id": "", "name": "name"}], [{"id": " " * 4, "name": "name"}],
                       [{"id": "x" * 101, "name": "name"}], [{"id": "target-1", "name": " "}],
                       [{"id": "target-1", "name": "name", "enabled": False}],
                       [{"id": "target-1", "name": "one"}, {"target_id": "target-1", "name": "two"}]):
            with self.subTest(values=values), self.assertRaises(CardAnalysisError):
                parse_targets(values)

    def test_detail_display_is_not_use_or_actual_time(self) -> None:
        class DetailBackend:
            calls = 0

            def recognize(self, image: object) -> list[OcrLine]:
                self.calls += 1
                return [line("テストカード")] if self.calls % 2 else []

        analyzer = CardFrameAnalyzer((Target("target-1", "テストカード", ("テストカード",)),), DetailBackend())
        analyzer.analyze(FakeImage(), 1000)
        analyzer.analyze(FakeImage(), 2000)
        self.assertEqual(len(analyzer.observations()), 1)
        observed = analyzer.observations()[0]
        self.assertFalse(observed["evidence"]["supports_use"])
        self.assertIsNone(observed["actual_ms"])
        self.assertEqual(observed["time_kind"], "detail_observed")
        self.assertEqual(observed["action"], "unknown")

    def test_continuous_and_redisplayed_logs_do_not_duplicate(self) -> None:
        tracker = LogEventTracker()
        rows = [entry(), entry("other", target_id=None)]
        for timestamp in (0, 500, 1000):
            tracker.observe(rows, timestamp)
        tracker.observe([], 1500)
        tracker.observe(rows, 2000)
        self.assertEqual(len(tracker.observations), 1)
        observed = next(iter(tracker.observations.values()))
        self.assertEqual(observed["observed_ms"], 0)
        self.assertIsNone(observed["actual_ms"])
        self.assertEqual(tracker.reappeared_sequences, 1)

    def test_identical_card_new_row_and_distinct_reuse_are_preserved(self) -> None:
        tracker = LogEventTracker()
        tracker.observe([entry()], 0)
        tracker.observe([entry(), entry()], 500)
        self.assertEqual(len(tracker.observations), 2)
        tracker.observe([entry("other", target_id=None)], 1000)
        tracker.observe([entry()], 1500)
        self.assertEqual(len(tracker.observations), 3)

    def test_distinct_action_is_not_collapsed_by_card_name(self) -> None:
        tracker = LogEventTracker()
        tracker.observe([entry(action="normal_summon")], 1000)
        tracker.observe([entry(action="activate")], 2000)
        self.assertEqual(len(tracker.observations), 2)

    def test_later_actor_evidence_updates_same_observation(self) -> None:
        tracker = LogEventTracker()
        tracker.observe([entry(actor="unknown")], 1000)
        tracker.observe([entry(actor="self")], 1500)
        self.assertEqual(len(tracker.observations), 1)
        self.assertEqual(next(iter(tracker.observations.values()))["actor"], "self")


class CardVideoSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "video.mp4"
        self.path.write_bytes(b"isolated video fixture")
        self.request = {"source_path": str(self.path), "expected_fingerprint": source_fingerprint(self.path),
                        "targets": [{"id": "target-1", "name": "テストカード"}], "sample_fps": 2}

    def test_sample_clock_full_length_and_no_detection_are_recorded(self) -> None:
        capture, updates = FakeCapture(), []
        original = self.path.read_bytes()
        with patch.dict("sys.modules", {"cv2": CV}):
            result = analyze_video(self.request, updates.append, backend=EmptyBackend(), capture_factory=lambda _: capture)
        self.assertTrue(capture.released)
        self.assertEqual(result["duration_ms"], 2000)
        self.assertEqual(result["frames_processed"], 4)
        self.assertEqual([row["observed_ms"] for row in updates if row.get("stage") == "analysis"], [0, 500, 1000, 1500])
        self.assertEqual(result["coverage"]["decoded_frames"], 20)
        self.assertEqual(result["coverage"]["no_log_evidence_frames"], 4)
        self.assertEqual(result["metrics"]["worker_pid"], os.getpid())
        if os.name == "nt":
            self.assertGreater(result["metrics"]["peak_rss_bytes"], 0)
        values = [row["progress"] for row in updates if row["type"] == "progress"]
        self.assertEqual(values, sorted(values))
        self.assertEqual([row["progress"] for row in updates if row.get("stage") == "analysis"], [0, .25, .5, .75])
        self.assertIn("do not establish non-use", result["coverage"]["interpretation"])
        self.assertEqual(self.path.read_bytes(), original)

    def test_clock_fallback_is_visible(self) -> None:
        capture = FakeCapture(broken_clock=True)
        with patch.dict("sys.modules", {"cv2": CV}):
            result = analyze_video(self.request, backend=EmptyBackend(), capture_factory=lambda _: capture)
        self.assertEqual(result["coverage"]["time_kind"], "frame_clock_fallback")
        self.assertTrue(result["warnings"])

    def test_variable_video_uses_terminal_pts_and_measures_actual_sampling_gap(self) -> None:
        updates = []
        with patch.dict("sys.modules", {"cv2": CV}):
            result = analyze_video(self.request, updates.append, backend=EmptyBackend(), capture_factory=lambda _: VariableCapture())
        coverage = result["coverage"]
        self.assertEqual((coverage["decoded_frames"], coverage["reported_frames"]), (14, 60))
        self.assertEqual(result["duration_ms"], 6000)
        self.assertTrue(coverage["reported_frame_count_estimated"])
        self.assertEqual(coverage["eof_validation"], "terminal_decoder_timestamp")
        self.assertEqual(coverage["nominal_sampling_interval_ms"], 500)
        self.assertEqual(coverage["maximum_sampling_gap_ms"], 700)
        values = [row["progress"] for row in updates if row["type"] == "progress"]
        self.assertEqual(values, sorted(values))
        self.assertAlmostEqual([row["progress"] for row in updates if row.get("stage") == "analysis"][-1], 5500 / 6000)
        with patch.dict("sys.modules", {"cv2": CV}), self.assertRaises(CardAnalysisError) as caught:
            analyze_video(self.request, backend=EmptyBackend(), capture_factory=lambda _: VariableCapture(truncated=True))
        self.assertEqual(caught.exception.code, "video_decode_incomplete")

    def test_variable_checkpoint_seek_uses_verified_pts_after_overshoot(self) -> None:
        saved, cancelled = [], [False]

        def save(document: dict[str, object]) -> None:
            if document["type"] == "checkpoint":
                saved.append(json.loads(json.dumps(document["checkpoint"])))
                cancelled[0] = True

        with patch.dict("sys.modules", {"cv2": CV}), self.assertRaises(CardAnalysisError):
            analyze_video(self.request, save, backend=EmptyBackend(), capture_factory=lambda _: VariableCapture(),
                          cancel_requested=lambda: cancelled[0])
        self.assertEqual(saved[0]["next_frame_index"], 12)
        self.assertEqual(saved[0]["last_timestamp"], 5000)
        updates = []
        with patch.dict("sys.modules", {"cv2": CV}):
            result = analyze_video({**self.request, "resume_state": saved[0]}, updates.append,
                                   backend=EmptyBackend(), capture_factory=lambda _: VariableCapture())
        self.assertEqual(result["coverage"]["resume_method"], "validated_decoder_pts_seek")
        self.assertEqual(result["coverage"]["decoded_frames"], 14)
        self.assertEqual(result["coverage"]["maximum_sampling_gap_ms"], 700)
        self.assertEqual([row["observed_ms"] for row in updates if row.get("stage") == "analysis"], [5500])
        values = [row["progress"] for row in updates if row["type"] == "progress"]
        self.assertEqual(values, sorted(values))

    def test_incomplete_decode_and_invalid_metadata_fail_and_release(self) -> None:
        for capture in (FakeCapture(frames=7), FakeCapture(fps=0)):
            with self.subTest(capture=capture), patch.dict("sys.modules", {"cv2": CV}), self.assertRaises(CardAnalysisError):
                analyze_video(self.request, backend=EmptyBackend(), capture_factory=lambda _: capture)
            self.assertTrue(capture.released)

    def test_input_fingerprint_mismatch_fails_before_decode(self) -> None:
        self.path.write_bytes(b"replaced")
        with self.assertRaises(CardAnalysisError) as caught:
            analyze_video(self.request, backend=EmptyBackend())
        self.assertEqual(caught.exception.code, "source_changed")

    def test_uncomputed_hash_is_verified_and_returned(self) -> None:
        self.request["expected_fingerprint"]["sha256"] = ""
        with patch.dict("sys.modules", {"cv2": CV}):
            result = analyze_video(self.request, backend=EmptyBackend(), capture_factory=lambda _: FakeCapture())
        self.assertEqual(result["source_fingerprint"]["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_changed_file_during_ocr_is_rejected(self) -> None:
        path = self.path

        class ChangingBackend:
            def recognize(self, image: object) -> list[OcrLine]:
                path.write_bytes(b"changed input")
                return []

        capture = FakeCapture()
        with patch.dict("sys.modules", {"cv2": CV}), self.assertRaises(CardAnalysisError) as caught:
            analyze_video(self.request, backend=ChangingBackend(), capture_factory=lambda _: capture)
        self.assertEqual(caught.exception.code, "source_changed")
        self.assertTrue(capture.released)

    def test_cancellation_is_checked_during_fingerprint(self) -> None:
        with self.assertRaises(CardAnalysisError) as caught:
            analyze_video(self.request, backend=EmptyBackend(), cancel_requested=lambda: True)
        self.assertEqual(caught.exception.code, "analysis_cancelled")

    def test_checkpoint_resumes_clock_and_full_coverage_without_reprocessing(self) -> None:
        checkpoints, cancelled = [], [False]

        def update(document: dict[str, object]) -> None:
            if document["type"] == "checkpoint":
                checkpoints.append(json.loads(json.dumps(document["checkpoint"])))
                cancelled[0] = True

        capture = FakeCapture(frames=60, expected_frames=60)
        with patch.dict("sys.modules", {"cv2": CV}), self.assertRaises(CardAnalysisError) as caught:
            analyze_video(self.request, update, backend=EmptyBackend(), capture_factory=lambda _: capture,
                          cancel_requested=lambda: cancelled[0])
        self.assertEqual(caught.exception.code, "analysis_cancelled")
        self.assertTrue(capture.released)
        self.assertEqual(checkpoints[0]["state"]["counts"]["sampled_frames"], 10)
        updates = []
        resumed = FakeCapture(frames=60, expected_frames=60)
        request = {**self.request, "resume_state": checkpoints[0]}
        with patch.dict("sys.modules", {"cv2": CV}):
            result = analyze_video(request, updates.append, backend=EmptyBackend(), capture_factory=lambda _: resumed)
        self.assertEqual(result["coverage"]["decoded_frames"], 60)
        self.assertEqual(result["frames_processed"], 12)
        self.assertEqual([row["observed_ms"] for row in updates if row.get("stage") == "analysis"], [5000, 5500])
        request["targets"] = [{"id": "target-1", "name": "変更された対象"}]
        with patch.dict("sys.modules", {"cv2": CV}), self.assertRaises(CardAnalysisError) as caught:
            analyze_video(request, backend=EmptyBackend(), capture_factory=lambda _: FakeCapture(frames=60, expected_frames=60))
        self.assertEqual(caught.exception.code, "invalid_checkpoint")

    def test_checkpoint_retains_log_dedup_state(self) -> None:
        analyzer = CardFrameAnalyzer((Target("target-1", "alpha", ("alpha",)),), EmptyBackend())
        analyzer.tracker.observe([entry()], 0)
        saved = json.loads(json.dumps(analyzer.export_state()))
        restored = CardFrameAnalyzer(analyzer.targets, EmptyBackend())
        restored.restore_state(saved)
        restored.tracker.observe([entry()], 1000)
        self.assertEqual(len(restored.observations()), 1)
        restored.tracker.observe([entry(), entry()], 1500)
        self.assertEqual(len(restored.observations()), 2)

    def test_resume_rejects_physical_frame_and_saved_clock_disagreement(self) -> None:
        saved, cancelled = [], [False]

        def save(document: dict[str, object]) -> None:
            if document["type"] == "checkpoint":
                saved.append(json.loads(json.dumps(document["checkpoint"])))
                cancelled[0] = True

        with patch.dict("sys.modules", {"cv2": CV}), self.assertRaises(CardAnalysisError):
            analyze_video(self.request, save, backend=EmptyBackend(),
                          capture_factory=lambda _: FakeCapture(frames=60, expected_frames=60, broken_clock=True),
                          cancel_requested=lambda: cancelled[0])
        saved[0]["last_timestamp"] -= 100
        with patch.dict("sys.modules", {"cv2": CV}), self.assertRaises(CardAnalysisError) as caught:
            analyze_video({**self.request, "resume_state": saved[0]}, backend=EmptyBackend(),
                          capture_factory=lambda _: FakeCapture(frames=60, expected_frames=60, broken_clock=True))
        self.assertEqual(caught.exception.code, "video_seek_failed")

    @unittest.skipUnless(importlib.util.find_spec("cv2"), "OpenCV optional dependency is unavailable")
    def test_actual_video_container_duration_and_sample_timestamps(self) -> None:
        import cv2
        import numpy as np

        path = Path(self.directory.name) / "actual.avi"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10., (640, 360))
        if not writer.isOpened():
            self.skipTest("MJPG encoder unavailable")
        try:
            for _ in range(20):
                writer.write(np.zeros((360, 640, 3), dtype=np.uint8))
        finally:
            writer.release()
        request = {**self.request, "source_path": str(path), "expected_fingerprint": source_fingerprint(path)}
        updates = []
        result = analyze_video(request, updates.append, backend=EmptyBackend())
        self.assertEqual(result["coverage"]["decoded_frames"], 20)
        self.assertAlmostEqual(result["duration_ms"], 2000, delta=2)
        self.assertEqual([row["observed_ms"] for row in updates if row.get("stage") == "analysis"], [0, 500, 1000, 1500])

    @unittest.skipUnless(importlib.util.find_spec("cv2"), "OpenCV optional dependency is unavailable")
    def test_actual_video_seek_resumes_after_checkpoint(self) -> None:
        import cv2
        import numpy as np

        path = Path(self.directory.name) / "resume.avi"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10., (640, 360))
        if not writer.isOpened():
            self.skipTest("MJPG encoder unavailable")
        try:
            for _ in range(60):
                writer.write(np.zeros((360, 640, 3), dtype=np.uint8))
        finally:
            writer.release()
        request = {**self.request, "source_path": str(path), "expected_fingerprint": source_fingerprint(path)}
        saved, cancelled = [], [False]

        def save_and_stop(document: dict[str, object]) -> None:
            if document["type"] == "checkpoint":
                saved.append(json.loads(json.dumps(document["checkpoint"])))
                cancelled[0] = True

        with self.assertRaises(CardAnalysisError) as caught:
            analyze_video(request, save_and_stop, backend=EmptyBackend(), cancel_requested=lambda: cancelled[0])
        self.assertEqual(caught.exception.code, "analysis_cancelled")
        updates = []
        result = analyze_video({**request, "resume_state": saved[0]}, updates.append, backend=EmptyBackend())
        self.assertEqual(result["coverage"]["decoded_frames"], 60)
        self.assertEqual(result["frames_processed"], 12)
        self.assertEqual(result["duration_ms"], 6000)
        self.assertEqual([row["observed_ms"] for row in updates if row.get("stage") == "analysis"], [5000, 5500])


class CardWorkerAndPackagingTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows native process memory counters")
    def test_native_peak_rss_tracks_current_process_memory_in_bytes(self) -> None:
        from master_duel_recorder_lite.card_video_analysis import _peak_rss_bytes

        baseline = _peak_rss_bytes()
        memory = bytearray(16 * 1024 * 1024)
        for index in range(0, len(memory), 4096):
            memory[index] = 1
        peak = _peak_rss_bytes()
        self.assertIsInstance(peak, int)
        self.assertGreaterEqual(peak, baseline)
        self.assertGreaterEqual(peak, len(memory))

    @unittest.skipUnless(os.name == "nt", "Windows launcher lifetime contract")
    def test_windows_venv_and_frozen_workers_obey_launchers_but_direct_python_uses_one_owner(self) -> None:
        from io import StringIO

        for frozen, parent_pid in ((False, 100), (False, 200), (True, 200)):
            with self.subTest(frozen=frozen, parent=parent_pid), tempfile.TemporaryDirectory() as directory:
                indirect = parent_pid != 100
                closed = []
                manager = types.SimpleNamespace(pid=100, cancelled=lambda: False, close=lambda: closed.append("manager"))
                outer = types.SimpleNamespace(pid=200, cancelled=lambda: True, close=lambda: closed.append("outer"))
                request = Path(directory) / "request.json"
                request.write_text(json.dumps({"owner_pid": 100}), encoding="utf-8")
                output = StringIO()

                def analysis(request: dict[str, object], *, progress_callback, cancel_requested):
                    if cancel_requested():
                        raise CardAnalysisError("analysis_cancelled", "bootloader exited")
                    return {"observations": []}

                with patch("sys.stdout", output), patch("sys.frozen", frozen, create=True), \
                        patch.object(card_analysis_worker, "lower_process_priority"), \
                        patch.object(card_analysis_worker.os, "getppid", return_value=parent_pid) as get_parent, \
                        patch.object(card_analysis_worker, "OwnerProcess", side_effect=[manager, outer]) as owners, \
                        patch.object(card_analysis_worker, "analyze_video", side_effect=analysis):
                    status = card_analysis_worker.main(["--request", str(request)])
                self.assertEqual(status, 1 if indirect else 0)
                self.assertEqual(owners.call_count, 2 if indirect else 1)
                self.assertEqual(get_parent.call_count, 1)
                self.assertEqual(closed, ["outer", "manager"] if indirect else ["manager"])
                self.assertEqual(json.loads(output.getvalue())["type"], "error" if indirect else "result")

    def test_terminated_owner_is_detected_without_an_extra_dependency(self) -> None:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **options)
        owner = None
        try:
            owner = card_analysis_worker.OwnerProcess(process.pid)
            self.assertFalse(owner.cancelled())
            process.terminate()
            process.wait(timeout=5)
            self.assertTrue(owner.cancelled())
        finally:
            if owner is not None:
                owner.close()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    def test_broken_protocol_pipe_exits_even_while_reporting_an_error(self) -> None:
        class ClosedPipe:
            def write(self, value: str) -> None:
                raise BrokenPipeError("parent pipe closed")

            def flush(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / "request.json"
            request.write_text(json.dumps({"source_path": "missing-video.mp4", "targets": []}), encoding="utf-8")
            with patch("sys.stdout", ClosedPipe()), patch.object(card_analysis_worker, "lower_process_priority"):
                self.assertEqual(card_analysis_worker.main(["--request", str(request)]), 1)

    def test_real_repository_snapshot_passes_worker_checkpoint_and_completion(self) -> None:
        from io import StringIO

        from master_duel_recorder_lite.card_analysis_repository import CardAnalysisRepository
        from master_duel_recorder_lite.card_video_analysis import ANALYZER_VERSION

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "video.mp4"
            source.write_bytes(b"isolated repository-to-worker fixture")
            repository = CardAnalysisRepository(root / "history.sqlite3")
            with closing(sqlite3.connect(repository.database_path)) as connection, connection:
                connection.execute(
                    "INSERT INTO recordings (recording_id,state,source,output_path,container,created_at,updated_at,duration_seconds) "
                    "VALUES ('test-video','completed','manual',?,'mp4','2026-10-14T00:00:00+00:00','2026-10-14T00:00:00+00:00',2)",
                    (str(source),),
                )
            repository.seed_presets()
            custom = repository.save_target(name="テストカード", aliases=("テスト別名",))
            fingerprint = {**source_fingerprint(source), "sha256": ""}
            job = repository.create_job("test-video", fingerprint=fingerprint, analyzer_version=ANALYZER_VERSION)
            repository.claim_job(job.job_id)
            snapshots = [target.to_dict() for target in job.target_snapshot]
            request = root / "request.json"
            request.write_text(json.dumps({"source_path": str(source), "expected_fingerprint": fingerprint,
                                           "targets": snapshots, "sample_fps": job.sample_fps}), encoding="utf-8")

            class SnapshotBackend:
                calls = 0

                def recognize(self, image: object) -> list[OcrLine]:
                    self.calls += 1
                    return [line(target["name"], 10 + 30 * index) for index, target in enumerate(snapshots)] \
                        if self.calls % 2 else []

            output = StringIO()
            with patch("sys.stdout", output), patch.object(card_analysis_worker, "lower_process_priority"), \
                    patch.dict("sys.modules", {"cv2": CV}), \
                    patch("master_duel_recorder_lite.card_video_analysis._open_video", return_value=FakeCapture()), \
                    patch("master_duel_recorder_lite.card_video_analysis.create_ocr_backend", return_value=SnapshotBackend()):
                self.assertEqual(card_analysis_worker.main(["--request", str(request)]), 0)
            documents = [json.loads(row) for row in output.getvalue().splitlines()]
            for document in documents:
                if document["type"] == "checkpoint":
                    repository.update_checkpoint(job.job_id, document["checkpoint"])
            result = next(document for document in documents if document["type"] == "result")
            completed = repository.complete_job(job.job_id, result["observations"],
                                                fingerprint=result["source_fingerprint"], result_metadata=result)
            observations = repository.list_observations("test-video")
            self.assertEqual(completed.state, "completed")
            self.assertNotIn("checkpoint", completed.result_metadata)
            self.assertEqual({row.target_id for row in observations},
                             {"preset-maxx-c", "preset-silvy", custom.target_id})
            self.assertTrue(all(isinstance(row.target_id, str) and row.values.actual_ms is None for row in observations))
            self.assertEqual(completed.fingerprint.sha256, source_fingerprint(source)["sha256"])

    def test_worker_error_is_ascii_json_and_nonzero(self) -> None:
        from io import StringIO

        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / "request.json"
            request.write_text(json.dumps({"source_path": "missing-video.mp4", "targets": []}), encoding="utf-8")
            output = StringIO()
            with patch("sys.stdout", output), patch.object(card_analysis_worker, "lower_process_priority"):
                status = card_analysis_worker.main(["--request", str(request)])
        self.assertEqual(status, 1)
        encoded = output.getvalue()
        self.assertTrue(encoded.isascii())
        self.assertEqual(json.loads(encoded)["type"], "error")

    def test_cli_and_gui_bundle_fixed_assets_updater_excludes_ocr(self) -> None:
        root = Path("isolated-project").resolve()
        data = ((root / "fixed.onnx", "card_ocr_models"), (root / "APACHE-2.0.txt", "licenses/card-ocr"))
        with patch("scripts.build_windows_exe.card_ocr_bundle_data", return_value=data):
            command = build_command(root, root / "version.txt", include_card_ocr=True)
        self.assertIn(f"{root / 'fixed.onnx'};card_ocr_models", command)
        self.assertIn("--recursive-copy-metadata", command)
        self.assertIn("rapidocr.inference_engine.onnxruntime", command)
        updater = build_command(root, root / "version.txt", executable_name=UPDATER_EXECUTABLE_NAME)
        self.assertNotIn("card_ocr_models", " ".join(updater))
        self.assertIn("--exclude-module", updater)


if __name__ == "__main__":
    unittest.main()
