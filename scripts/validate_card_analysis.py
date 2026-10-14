"""実動画を読み取り、隔離DBでカード分析と録画優先を検証する。

動画・inventory・保存先は引数で指定する。元動画を変更せず、生成動画と
DBは一意な一時領域へ作る。結果と原入力の前後SHAをEvidenceへ保存する。
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback

from master_duel_recorder_lite.card_analysis_jobs import CARD_ANALYZER_VERSION, CardAnalysisManager
from master_duel_recorder_lite.card_analysis_repository import CardAnalysisRepository
from master_duel_recorder_lite.card_ocr import diagnose_ocr_models
from master_duel_recorder_lite.config import AppConfig
from master_duel_recorder_lite.history_database import HISTORY_DATABASE_NAME
from master_duel_recorder_lite.recording_lock import RecordingLock
from master_duel_recorder_lite.recording_session import RecordingSession, RecordingState, TERMINAL_STATES
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, allow_nan=False, indent=2)
        output.write("\n")


def fingerprint(path: Path, *, include_hash: bool = True) -> dict[str, object]:
    before = path.stat()
    digest = ""
    if include_hash:
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"読取中に原入力が変わりました: {path}")
    return {"path": str(path.resolve()), "size_bytes": after.st_size,
            "mtime_ns": after.st_mtime_ns, "sha256": digest}


def register_fixture(repository: CardAnalysisRepository, identifier: str, path: Path, duration: float) -> None:
    # 実運用履歴を開かず、このDB内の正常完了fixtureだけを登録する。
    stamp = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(repository.database_path)) as connection, connection:
        connection.execute(
            "INSERT INTO recordings (recording_id,state,source,output_path,container,created_at,updated_at,"
            "duration_seconds,size_bytes) VALUES (?,'completed','manual',?,'mkv',?,?,?,?)",
            (identifier, path.name, stamp, stamp, duration, path.stat().st_size),
        )


class Measurement:
    def __init__(self, manager: CardAnalysisManager) -> None:
        import psutil

        self.psutil = psutil
        self.manager = manager
        self.owner = psutil.Process()
        self.stop_event = threading.Event()
        self.children: dict[int, dict[str, object]] = {}
        self.children_lock = threading.Lock()
        self.owner_rss = 0
        self.wall_start = time.perf_counter()
        self.owner_cpu_start = sum(self.owner.cpu_times()[:2])
        self.thread = threading.Thread(target=self.sample, name="card-validation-metrics", daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def sample(self) -> None:
        while not self.stop_event.is_set():
            self.owner_rss = max(self.owner_rss, self.owner.memory_info().rss)
            pid = self.manager.active_process_id
            if pid is not None:
                try:
                    launcher = self.psutil.Process(pid)
                    processes = [launcher, *launcher.children(recursive=True)]
                    for process in processes:
                        if "master_duel_recorder_lite.card_analysis_worker" not in process.cmdline():
                            continue
                        with self.children_lock:
                            self.children.setdefault(process.pid, {
                                "pid": process.pid, "created": process.create_time(),
                                "role": "launcher_or_direct" if process.pid == pid else "worker_descendant",
                                "cpu_seconds_sampled": 0.0, "peak_rss_sampled_bytes": 0,
                                "peak_rss_os_bytes": 0, "maximum_os_threads": 0,
                                "priority": int(process.nice()),
                            })
                except (self.psutil.NoSuchProcess, self.psutil.AccessDenied):
                    pass
            # 起動用python.exe終了後も、確認済みの子PIDを同じ生成時刻で追跡する。
            with self.children_lock:
                tracked = list(self.children.values())
            for entry in tracked:
                try:
                    process = self.psutil.Process(entry["pid"])
                    if process.create_time() != entry["created"]:
                        continue
                    info = process.memory_info()
                    entry["cpu_seconds_sampled"] = max(entry["cpu_seconds_sampled"], sum(process.cpu_times()[:2]))
                    entry["peak_rss_sampled_bytes"] = max(entry["peak_rss_sampled_bytes"], info.rss)
                    entry["peak_rss_os_bytes"] = max(entry["peak_rss_os_bytes"], getattr(info, "peak_wset", 0))
                    entry["maximum_os_threads"] = max(entry["maximum_os_threads"], process.num_threads())
                except (self.psutil.NoSuchProcess, self.psutil.AccessDenied):
                    pass
            self.stop_event.wait(0.05)

    def live_worker_pids(self) -> list[int]:
        with self.children_lock:
            tracked = list(self.children.values())
        live = []
        for entry in tracked:
            try:
                process = self.psutil.Process(entry["pid"])
                if process.is_running() and process.create_time() == entry["created"]:
                    live.append(process.pid)
            except (self.psutil.NoSuchProcess, self.psutil.AccessDenied):
                pass
        return live

    def __exit__(self, *_args):
        self.stop_event.set()
        self.thread.join(timeout=2)
        self.wall_seconds = time.perf_counter() - self.wall_start
        self.owner_cpu_seconds = sum(self.owner.cpu_times()[:2]) - self.owner_cpu_start

    def result(self) -> dict[str, object]:
        wall = self.wall_seconds
        owner_cpu = self.owner_cpu_seconds
        child_cpu = sum(row["cpu_seconds_sampled"] for row in self.children.values())
        return {
            "wall_seconds": wall, "owner_cpu_seconds": owner_cpu,
            "worker_cpu_seconds_sampled": child_cpu, "total_cpu_seconds_sampled": owner_cpu + child_cpu,
            "logical_cpu_count": self.psutil.cpu_count(),
            "whole_host_average_cpu_percent_sampled": (owner_cpu + child_cpu) / wall / self.psutil.cpu_count() * 100,
            "owner_peak_rss_sampled_bytes": self.owner_rss,
            "worker_peak_rss_sampled_bytes": max((row["peak_rss_sampled_bytes"] for row in self.children.values()), default=0),
            "worker_peak_rss_os_bytes": max((row["peak_rss_os_bytes"] for row in self.children.values()), default=0),
            "worker_processes": list(self.children.values()), "sample_interval_seconds": 0.05,
            "resource_scope": "manager launcher and verified worker descendants; PID creation time checked",
            "limitations": "owned processes only; sampled CPU can miss final process time; OS thread count includes helper threads",
        }


def wait_for(repository, manager, job_id, predicate, *, timeout: float, label: str):
    deadline, next_report = time.monotonic() + timeout, 0.0
    while time.monotonic() < deadline:
        job = repository.get_job(job_id)
        if predicate(job):
            return job
        if job.state in {"failed", "cancelled", "completed"}:
            raise RuntimeError(f"{label}: 期待状態になる前に{job.state}へ遷移しました: {job.error} / manager={manager.last_error}")
        if time.monotonic() >= next_report:
            checkpoint = job.result_metadata.get("checkpoint", {})
            print(json.dumps({"stage": label, "state": job.state, "progress": job.progress,
                              "checkpoint_frame": checkpoint.get("next_frame_index"), "pid": manager.active_process_id}), flush=True)
            next_report = time.monotonic() + 5
        time.sleep(0.1)
    raise TimeoutError(f"{label}: {timeout}秒以内に完了しませんでした")


def observation_summary(review) -> dict[str, object]:
    rows = review.observations
    return {
        "total": len(rows), "candidate": sum(row.status == "candidate" for row in rows),
        "supported_use_candidates": sum(row.status == "candidate" and row.values.supports_use for row in rows),
        "detail_display_only": sum(row.values.time_kind == "detail_observed" for row in rows),
        "unknown_actor": sum(row.actor == "unknown" for row in rows),
        "unknown_action": sum(row.action == "unknown" for row in rows),
        "actual_time_unconfirmed": sum(row.actual_ms is None for row in rows),
        "unique_source_keys": len({row.values.source_key for row in rows}),
        "by_card": {name: sum(row.card_name == name for row in rows) for name in sorted({row.card_name for row in rows})},
        "by_action": {action: sum(row.action == action for row in rows) for action in sorted({row.action for row in rows})},
    }


def priority_test(repository, manager, paths, output, timeout: float, measurement: Measurement) -> dict[str, object]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("録画優先試験にはFFmpeg/ffprobeが必要です")
    source = paths.recordings / "generated-analysis-source.mkv"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-n", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30", "-t", "60", "-c:v", "libx264", "-threads", "1",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source)], check=True, timeout=60,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    register_fixture(repository, "priority-source", source, 60)
    job = repository.create_job("priority-source", fingerprint=fingerprint(source, include_hash=False),
                                analyzer_version=CARD_ANALYZER_VERSION, sample_fps=2)
    manager.wake()
    ready = wait_for(repository, manager, job.job_id, lambda item: bool(item.result_metadata.get("checkpoint")),
                     timeout=timeout, label="priority-checkpoint")
    if ready.state != "running":
        raise RuntimeError("録画開始前に試験用解析が完了し、中断を検証できませんでした")
    checkpoint_before = ready.result_metadata["checkpoint"]
    record_output = paths.recordings / "generated-priority-recording.mkv"
    command = (ffmpeg, "-hide_banner", "-loglevel", "warning", "-n", "-re", "-f", "lavfi", "-i",
               "testsrc2=size=1280x720:rate=30", "-t", "3", "-c:v", "libx264", "-threads", "1",
               "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(record_output))
    samples, session = [], RecordingSession(command=command, output_path=record_output)
    with RecordingLock.acquire(paths.data / "recording.lock", recording_id="generated-recording"):
        lock_acquired_unix_time = time.time()
        started = time.perf_counter()
        manager.interrupt()
        interrupt_seconds = time.perf_counter() - started
        started = time.perf_counter()
        session.start()
        start_seconds = time.perf_counter() - started
        recording_started_unix_time = time.time()
        deadline = time.monotonic() + 20
        while session.poll() not in TERMINAL_STATES and time.monotonic() < deadline:
            samples.append({"elapsed_seconds": time.perf_counter() - started,
                            "unix_time": time.time(), "worker_pid": manager.active_process_id,
                            "worker_tree_pids": measurement.live_worker_pids(),
                            "job_state": repository.get_job(job.job_id).state})
            time.sleep(0.05)
        if session.state not in TERMINAL_STATES:
            session.stop()
            raise TimeoutError("生成録画が自然終了しませんでした")
        waiting = wait_for(repository, manager, job.job_id, lambda item: item.state == "waiting",
                           timeout=15, label="priority-waiting")
        for _ in range(5):
            samples.append({"elapsed_seconds": time.perf_counter() - started, "unix_time": time.time(),
                            "worker_pid": manager.active_process_id, "worker_tree_pids": measurement.live_worker_pids(),
                            "job_state": repository.get_job(job.job_id).state})
            time.sleep(0.05)
        if session.result is None or not session.result.succeeded or session.state is not RecordingState.COMPLETED:
            raise RuntimeError(f"生成録画が正常完了しませんでした: {session.result}")
        if any((sample["worker_pid"] is not None or sample["worker_tree_pids"])
               and sample["elapsed_seconds"] >= 0.5 for sample in samples):
            raise RuntimeError("録画開始後0.5秒を超えて解析workerが残っています")
        if waiting.job_id != job.job_id or waiting.result_metadata["checkpoint"]["next_frame_index"] < checkpoint_before["next_frame_index"]:
            raise RuntimeError("中断時に同じジョブのcheckpointが失われました")
    write_json(output / "priority-recording-samples.json", samples)
    manager.wake()
    completed = wait_for(repository, manager, job.job_id, lambda item: item.state == "completed", timeout=timeout, label="priority-resume")
    probe = subprocess.run([ffprobe, "-v", "error", "-count_frames", "-show_entries",
                            "stream=codec_type,width,height,r_frame_rate,nb_read_frames:format=duration,size", "-of", "json",
                            str(record_output)], capture_output=True, text=True, encoding="utf-8", check=True, timeout=30)
    decode = subprocess.run([ffmpeg, "-v", "error", "-i", str(record_output), "-f", "null", "-"],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    probed = json.loads(probe.stdout)
    if decode.returncode != 0 or float(probed["format"]["duration"]) < 2.5:
        raise RuntimeError("生成録画の再デコード・録画長検証に失敗しました")
    coverage = completed.result_metadata["coverage"]
    if coverage["decoded_frames"] != 1800 or completed.result_metadata["frames_processed"] != 120:
        raise RuntimeError("checkpoint再開後に生成動画の全範囲が揃いませんでした")
    result = {
        "passed": True, "job_id": job.job_id, "generation": completed.generation,
        "interrupt_call_seconds": interrupt_seconds, "recording_start_call_seconds": start_seconds,
        "lock_acquired_unix_time": lock_acquired_unix_time,
        "recording_started_unix_time": recording_started_unix_time,
        "worker_tree_stop_observed_seconds": max((sample["elapsed_seconds"] for sample in samples
                                                 if sample["worker_tree_pids"]), default=0),
        "checkpoint_before_frame": checkpoint_before["next_frame_index"],
        "checkpoint_preserved_frame": waiting.result_metadata["checkpoint"]["next_frame_index"],
        "waiting_progress": waiting.progress, "recording_samples": len(samples), "active_workers_during_recording": 0,
        "recording_output": str(record_output), "recording_bytes": session.result.size_bytes,
        "recording_probe": probed, "recording_decode_returncode": decode.returncode,
        "completed_job": completed.to_dict(),
        "limitations": "generated lavfi recording; no real game FPS or capture-device performance measurement",
    }
    write_json(output / "priority-test.json", result)
    return result


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--worker-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=1800)
    priority_mode = parser.add_mutually_exclusive_group()
    priority_mode.add_argument("--priority-only", action="store_true")
    priority_mode.add_argument("--skip-priority", action="store_true")
    parser.add_argument("--video-id", action="append", default=[])
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    videos = [] if args.priority_only else inventory["videos"]
    if args.video_id:
        videos = [video for video in videos if video["id"] in args.video_id]
        if {video["id"] for video in videos} != set(args.video_id):
            parser.error("--video-id がinventoryと一致しません")
    if args.skip_priority and not videos:
        parser.error("--skip-priority には１本以上の入力動画が必要です")
    output = args.output_dir.resolve() / ("pipeline-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    output.mkdir(parents=True, exist_ok=False)
    paths = default_runtime_paths(user_data_dir=Path(tempfile.mkdtemp(prefix="mdrl-v3-card-validation-")))
    ensure_runtime_dirs(paths)
    repository = CardAnalysisRepository(paths.db / HISTORY_DATABASE_NAME)
    repository.seed_presets()
    for name in args.target:
        repository.save_target(name=name)
    settings = AppConfig(card_analysis_enabled=True, card_analysis_fps=2)
    requests = []

    def command(request_path):
        document = json.loads(request_path.read_text(encoding="utf-8"))
        requests.append(document)
        write_json(output / f"worker-request-{len(requests):03}.json", document)
        return (str(args.worker_python.resolve()), "-u", "-m", "master_duel_recorder_lite.card_analysis_worker", "--request", str(request_path))

    manager = CardAnalysisManager(paths=paths, repository=repository, settings=lambda: settings,
                                  can_run=lambda: True, command_factory=command)
    summary = {"schema": "card-analysis-act-validation-v1", "runtime_dir": str(paths.root), "output_dir": str(output),
               "settings": {"fps": 2, "worker_count": 1}, "model_diagnostics": diagnose_ocr_models(),
               "targets": [target.to_dict() for target in repository.list_targets()], "videos": [],
               "input_protection": [], "status": "active",
               "limitations": "three selected recordings; AI visual references; no full event precision/recall, recall guarantee, human-confirmed truth or real game FPS"}
    try:
        for video in videos:
            actual = fingerprint(Path(video["path"]))
            if (actual["size_bytes"], actual["mtime_ns"], actual["sha256"]) != (video["bytes"], video["mtime_ns"], video["sha256"]):
                raise RuntimeError(f"原録画が調査manifestと一致しません: {video['id']}")
            summary["input_protection"].append({"id": video["id"], "before": actual})
        write_json(output / "input-protection-before.json", summary["input_protection"])
        manager.start()
        for video in videos:
            path = Path(video["path"])
            register_fixture(repository, video["id"], path, video["duration_seconds"])
            failure = None
            with Measurement(manager) as measurement:
                job = repository.create_job(video["id"], fingerprint=fingerprint(path, include_hash=False),
                                            analyzer_version=CARD_ANALYZER_VERSION, sample_fps=2)
                manager.wake()
                try:
                    finished = wait_for(repository, manager, job.job_id, lambda item: item.state == "completed",
                                        timeout=args.timeout, label=video["id"])
                except Exception as exc:
                    failure = exc
                    finished = repository.get_job(job.job_id)
            review = repository.get_review(video["id"])
            item = {"id": video["id"], "job": finished.to_dict(), "measurement": measurement.result(),
                    "observation_summary": observation_summary(review),
                    "expected_maximum_samples": math.ceil(video["duration_seconds"] * 2)}
            write_json(output / f"{video['id']}-result.json", item)
            write_json(output / f"{video['id']}-observations.json", [row.to_dict() for row in review.observations])
            summary["videos"].append(item)
            if failure is not None:
                raise failure
            print(json.dumps({"finished": video["id"], "observations": len(review.observations),
                              "wall_seconds": item["measurement"]["wall_seconds"]}), flush=True)
        if not args.skip_priority:
            priority_measurement = Measurement(manager)
            try:
                with priority_measurement:
                    summary["recording_priority"] = priority_test(
                        repository, manager, paths, output, args.timeout, priority_measurement)
            finally:
                summary["recording_priority_measurement"] = priority_measurement.result()
            resumed = [request for request in requests if "resume_state" in request]
            if not resumed:
                raise RuntimeError("同じcheckpointをrequestへ渡した再開が確認できません")
            summary["resume_requests"] = [{"next_frame_index": item["resume_state"]["next_frame_index"],
                                           "source_path": item["source_path"]} for item in resumed]
        summary["status"] = "completed"
    except Exception as exc:
        summary["status"] = "failed"
        summary["error"] = f"{type(exc).__name__}: {exc}"
        summary["traceback"] = traceback.format_exc()
        print(summary["error"], flush=True)
    finally:
        try:
            manager.close()
        except Exception as exc:
            summary["status"] = "failed"
            summary["close_error"] = f"{type(exc).__name__}: {exc}"
        for row in summary["input_protection"]:
            try:
                after = fingerprint(Path(row["before"]["path"]))
                row["after"] = after
                row["unchanged"] = after == row["before"]
            except OSError as exc:
                row["unchanged"] = False
                row["error"] = f"{type(exc).__name__}: {exc}"
            if not row["unchanged"]:
                summary["status"] = "failed"
                summary["error"] = "原録画の前後fingerprintが一致しません"
        write_json(output / "validation-summary.json", summary)
    print(json.dumps({"status": summary["status"], "summary_path": str(output / "validation-summary.json")}, ensure_ascii=False), flush=True)
    return 0 if summary["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
