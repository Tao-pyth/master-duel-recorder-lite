from contextlib import closing
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

from master_duel_recorder_lite.card_analysis_jobs import (
    CARD_ANALYZER_VERSION, CardAnalysisManager, recording_lock_busy,
)
from master_duel_recorder_lite.card_analysis_models import CardInputFingerprint
from master_duel_recorder_lite.card_analysis_repository import CardAnalysisRepository
from master_duel_recorder_lite.config import AppConfig
from master_duel_recorder_lite.recording_lock import RecordingLock
from master_duel_recorder_lite.runtime_paths import default_runtime_paths


WORKER = r'''
import hashlib, json, pathlib, sys, time
request = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
mode = sys.argv[2]
source = pathlib.Path(request["source_path"])
stat = source.stat()
fingerprint = {"path": str(source), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
               "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
def emit(item):
    print(json.dumps(item, ensure_ascii=True), flush=True)
if mode == "bad-json":
    print("broken protocol", flush=True)
    sys.exit(1)
if mode == "timeout":
    time.sleep(5)
    sys.exit(1)
if mode == "resume" and not request.get("resume_state"):
    checkpoint = {"schema": "card-analysis-checkpoint-v1", "analyzer_version": "rapidocr-ppocrv6-v1",
        "source_fingerprint": fingerprint,
        "targets_sha256": hashlib.sha256(json.dumps(request["targets"], sort_keys=True,
            ensure_ascii=True, allow_nan=False).encode()).hexdigest(),
        "sample_fps": request["sample_fps"], "source_fps": 30.0, "reported_frames": 300,
        "next_frame_index": 100, "next_sample_ms": 3500.0, "last_timestamp": 3000.0,
        "observations": [], "state": {}, "timestamp_fallback": False, "warnings": []}
    emit({"type": "progress", "progress": 0.3})
    emit({"type": "checkpoint", "checkpoint": checkpoint})
    time.sleep(60)
target = request["targets"][0]
observation = {"target_id": target["target_id"], "card_name": target["name"],
    "actor": "self", "action": "activate", "observed_ms": 4000, "actual_ms": None,
    "time_kind": "log_observed", "source_key": "stable-row", "confidence": 0.8,
    "evidence": {"supports_use": True, "resumed": bool(request.get("resume_state"))}}
if mode == "source-change":
    source.write_bytes(source.read_bytes() + b"changed")
emit({"type": "result", "analyzer_version": "rapidocr-ppocrv6-v1",
      "source_fingerprint": fingerprint, "observations": [observation],
      "coverage": {"complete": True}, "duration_ms": 10000, "frames_processed": 20, "warnings": []})
sys.exit(2 if mode == "nonzero" else 0)
'''


class CardAnalysisJobsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdrl-card-jobs-test-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = default_runtime_paths(user_data_dir=root / "runtime")
        self.repository = CardAnalysisRepository.from_runtime_paths(self.paths)
        self.target = self.repository.save_target(name="日本語カード")
        self.source = root / "video.mkv"
        self.source.write_bytes(b"isolated source")
        self.helper = root / "worker.py"
        self.helper.write_text(WORKER, encoding="utf-8")
        self.config = AppConfig(card_analysis_enabled=True)
        self.allowed = True
        self.add_recording("one")

    def add_recording(self, identifier):
        with closing(sqlite3.connect(self.repository.database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO recordings (recording_id,state,source,output_path,container,created_at,updated_at) "
                "VALUES (?,'completed','manual',?,'mkv','2026-10-01','2026-10-01')",
                (identifier, identifier + ".mkv"),
            )

    def job(self, identifier="one"):
        stat = self.source.stat()
        return self.repository.create_job(identifier, analyzer_version=CARD_ANALYZER_VERSION,
            fingerprint=CardInputFingerprint(str(self.source), stat.st_size, stat.st_mtime_ns))

    def manager(self, mode="success", timeout=5):
        manager = CardAnalysisManager(paths=self.paths, repository=self.repository,
            settings=lambda: self.config, can_run=lambda: self.allowed,
            command_factory=lambda path: (sys.executable, "-u", str(self.helper), str(path), mode),
            idle_timeout_seconds=timeout)
        self.addCleanup(manager.close)
        manager.start()
        return manager

    def wait(self, predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.025)
        self.fail("isolated worker did not reach the expected state")

    def test_success_records_full_hash_and_japanese_snapshot(self):
        job = self.job()
        manager = self.manager()
        self.wait(lambda: self.repository.get_job(job.job_id).state == "completed")
        completed = self.repository.get_job(job.job_id)
        self.assertEqual(completed.fingerprint.sha256, hashlib.sha256(self.source.read_bytes()).hexdigest())
        observation, = self.repository.list_observations("one")
        self.assertEqual(observation.card_name, "日本語カード")
        self.assertEqual(observation.status, "candidate")
        self.wait(lambda: manager.active_process_id is None)

    def test_interrupt_checkpoint_resume_keeps_results_uncommitted_until_success(self):
        job = self.job()
        manager = self.manager("resume")
        self.wait(lambda: bool(self.repository.get_job(job.job_id).result_metadata.get("checkpoint")))
        self.allowed = False
        started = time.monotonic()
        manager.interrupt()
        self.wait(lambda: self.repository.get_job(job.job_id).state == "waiting")
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(self.repository.list_observations("one"), ())
        self.assertGreaterEqual(self.repository.get_job(job.job_id).progress, 0.3)
        self.allowed = True
        manager.wake()
        self.wait(lambda: self.repository.get_job(job.job_id).state == "completed")
        observation, = self.repository.list_observations("one")
        self.assertTrue(observation.evidence["resumed"])
        self.assertNotIn("checkpoint", self.repository.get_job(job.job_id).result_metadata)

    def test_recording_os_lock_prevents_worker_and_content_is_unchanged(self):
        job = self.job()
        lock_path = self.paths.data / "recording.lock"
        lock = RecordingLock.acquire(lock_path, recording_id="isolated-capture")
        self.addCleanup(lock.release)
        lock._handle.seek(0)
        before = lock._handle.read()
        self.assertTrue(recording_lock_busy(lock_path))
        manager = self.manager()
        time.sleep(0.3)
        self.assertIsNone(manager.active_process_id)
        self.assertEqual(self.repository.get_job(job.job_id).state, "queued")
        lock.release()
        self.assertEqual(lock_path.read_bytes(), before)
        manager.wake()
        self.wait(lambda: self.repository.get_job(job.job_id).state == "completed")

    def test_off_interrupts_worker_without_committing_partial_observations(self):
        job = self.job()
        manager = self.manager("resume")
        self.wait(lambda: manager.active_process_id is not None)
        self.config = replace(self.config, card_analysis_enabled=False)
        manager.interrupt()
        self.wait(lambda: self.repository.get_job(job.job_id).state == "waiting")
        self.wait(lambda: manager.active_process_id is None)
        self.assertEqual(self.repository.list_observations("one"), ())

    def test_second_manager_does_not_recover_a_live_owned_job(self):
        job = self.job()
        first = self.manager("resume")
        self.wait(lambda: bool(self.repository.get_job(job.job_id).result_metadata.get("checkpoint")))
        second = self.manager()
        time.sleep(0.3)
        self.assertEqual(self.repository.get_job(job.job_id).state, "running")
        self.assertIsNotNone(first.active_process_id)
        self.assertIsNone(second.active_process_id)
        second.close()

    def test_nonzero_exit_and_protocol_errors_are_not_success(self):
        for mode in ("nonzero", "bad-json"):
            with self.subTest(mode=mode):
                job = self.job()
                manager = self.manager(mode)
                self.wait(lambda: self.repository.get_job(job.job_id).state == "failed")
                self.assertEqual(self.repository.list_observations("one"), ())
                manager.close()

    def test_source_change_rejects_result(self):
        job = self.job()
        self.manager("source-change")
        self.wait(lambda: self.repository.get_job(job.job_id).state == "failed")
        self.assertIn("変更", self.repository.get_job(job.job_id).error)
        self.assertEqual(self.repository.list_observations("one"), ())

    def test_unresponsive_child_is_reaped_and_failed(self):
        job = self.job()
        manager = self.manager("timeout", timeout=0.15)
        self.wait(lambda: self.repository.get_job(job.job_id).state == "failed")
        self.wait(lambda: manager.active_process_id is None)
        self.assertIn("応答", self.repository.get_job(job.job_id).error)


if __name__ == "__main__":
    unittest.main()
