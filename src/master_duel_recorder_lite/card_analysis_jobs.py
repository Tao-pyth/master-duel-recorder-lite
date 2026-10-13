from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

from .card_analysis_models import CardAnalysisJob, CardInputFingerprint
from .card_analysis_repository import CardAnalysisError, CardAnalysisRepository
from .config import AppConfig
from .recording_lock import RecordingBusyError, RecordingLock, _lock_one_byte, _unlock_one_byte
from .runtime_paths import RuntimePaths


CARD_ANALYZER_VERSION = "rapidocr-ppocrv6-v1"


def worker_command(request_path: Path) -> tuple[str, ...]:
    if getattr(sys, "frozen", False):
        return (sys.executable, "--card-analysis-worker", "--request", str(request_path))
    return (sys.executable, "-u", "-m", "master_duel_recorder_lite.card_analysis_worker",
            "--request", str(request_path))


def recording_lock_busy(path: Path) -> bool:
    """録画ロックの内容を書き換えず、別プロセスの録画も検知する。"""
    if not path.exists():
        return False
    try:
        with path.open("r+b") as handle:
            handle.seek(0)
            _lock_one_byte(handle)
            _unlock_one_byte(handle)
        return False
    except OSError:
        return True


class CardAnalysisManager:
    """永続ジョブを1子プロセスで処理し、録画開始時には即時停止を要求する。"""

    def __init__(
        self,
        *,
        paths: RuntimePaths,
        repository: CardAnalysisRepository,
        settings: Callable[[], AppConfig],
        can_run: Callable[[], bool],
        command_factory: Callable[[Path], Sequence[str]] = worker_command,
        idle_timeout_seconds: float = 120.0,
    ) -> None:
        self.paths = paths
        self.repository = repository
        self._settings = settings
        self._can_run = can_run
        self._command_factory = command_factory
        self._idle_timeout = idle_timeout_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._interrupt = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._owner: RecordingLock | None = None
        self.last_error = ""

    def start(self) -> None:
        with self._lock:
            if self._stop.is_set():
                raise CardAnalysisError("カード分析管理は終了しています")
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, name="mdrl-card-analysis", daemon=True)
                self._thread.start()
        self.wake()

    def wake(self) -> None:
        self._wake.set()

    def interrupt(self) -> None:
        """モデルの完了を待たず、所有する解析プロセスだけを止める。"""
        self._interrupt.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        self.wake()

    def close(self) -> None:
        self._stop.set()
        self.interrupt()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=6.0)
            if thread.is_alive():
                raise CardAnalysisError("カード分析の終了を確認できません")

    @property
    def active_process_id(self) -> int | None:
        with self._lock:
            process = self._process
            return process.pid if process is not None and process.poll() is None else None

    def _allowed(self) -> bool:
        return (
            not self._stop.is_set()
            and self._settings().card_analysis_enabled
            and self._can_run()
            and not recording_lock_busy(self.paths.data / "recording.lock")
        )

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    if self._owner is None:
                        try:
                            self._owner = RecordingLock.acquire(
                                self.paths.data / "card-analysis-worker.lock", recording_id="card-analysis"
                            )
                        except RecordingBusyError:
                            self._wait()
                            continue
                        # OSロック取得後だけ復元し、生きている別workerのジョブを変えない。
                        self.repository.recover_running()
                    if not self._allowed():
                        self._wait()
                        continue
                    jobs = self.repository.list_jobs()
                    job = next((item for item in jobs if item.state in {"queued", "waiting"}), None)
                    if job is None:
                        self._wait()
                        continue
                    self._interrupt.clear()
                    job = self.repository.claim_job(job.job_id)
                    self._run_job(job)
                except Exception as exc:
                    self.last_error = str(exc)
                    self._wait()
        finally:
            if self._owner is not None:
                self._owner.release()
                self._owner = None

    def _wait(self) -> None:
        self._wake.wait(0.2)
        self._wake.clear()

    def _run_job(self, job: CardAnalysisJob) -> None:
        request = {
            "schema": "card-analysis-request-v1",
            "source_path": job.fingerprint.path,
            "expected_fingerprint": job.fingerprint.to_dict(),
            "targets": [item.to_dict() for item in job.target_snapshot],
            "sample_fps": job.sample_fps,
            "owner_pid": os.getpid(),
        }
        checkpoint = job.result_metadata.get("checkpoint")
        if checkpoint is not None:
            request["resume_state"] = checkpoint
        descriptor, filename = tempfile.mkstemp(prefix="mdrl-card-request-", suffix=".json")
        request_path = Path(filename)
        process: subprocess.Popen[str] | None = None
        readers: list[threading.Thread] = []
        messages: queue.Queue[dict[str, object]] = queue.Queue()
        stderr_tail: deque[str] = deque(maxlen=20)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(request, handle, ensure_ascii=True)
            if self._interrupt.is_set() or not self._allowed():
                self.repository.defer_job(job.job_id, "録画または設定OFFのため待機")
                return
            environment = os.environ.copy()
            if not getattr(sys, "frozen", False):
                source_root = str(Path(__file__).resolve().parents[1])
                environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
            process = subprocess.Popen(
                tuple(self._command_factory(request_path)), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", env=environment,
                creationflags=flags,
            )
            with self._lock:
                self._process = process
            assert process.stdout is not None and process.stderr is not None
            readers = [
                threading.Thread(target=self._read_messages, args=(process.stdout, messages), daemon=True),
                threading.Thread(target=self._read_stderr, args=(process.stderr, stderr_tail), daemon=True),
            ]
            for reader in readers:
                reader.start()
            result = None
            error = ""
            last_message = time.monotonic()
            last_progress = job.progress
            interrupted = False
            while True:
                if self._interrupt.is_set() or not self._allowed():
                    interrupted = True
                    self._terminate(process)
                try:
                    item = messages.get(timeout=0.05)
                except queue.Empty:
                    item = None
                if item is not None:
                    last_message = time.monotonic()
                    kind = item.get("type")
                    if kind == "progress":
                        progress = item.get("progress")
                        if isinstance(progress, bool) or not isinstance(progress, (int, float)) or not 0 <= progress <= 1:
                            raise CardAnalysisError("解析進捗の値が不正です")
                        if progress - last_progress >= 0.01:
                            self.repository.update_progress(job.job_id, progress)
                            last_progress = float(progress)
                    elif kind == "checkpoint":
                        checkpoint = item.get("checkpoint")
                        if not isinstance(checkpoint, dict):
                            raise CardAnalysisError("解析再開情報の形式が不正です")
                        self.repository.update_checkpoint(job.job_id, checkpoint)
                    elif kind == "result":
                        if result is not None:
                            raise CardAnalysisError("解析完了通知が重複しました")
                        result = item
                    elif kind == "error":
                        error = str(item.get("message", "カード分析に失敗しました"))
                    elif kind == "protocol_error":
                        raise CardAnalysisError(str(item.get("message")))
                    else:
                        raise CardAnalysisError("解析workerの通知形式が不正です")
                if process.poll() is not None and not readers[0].is_alive() and messages.empty():
                    break
                if time.monotonic() - last_message > self._idle_timeout:
                    raise CardAnalysisError("カード分析からの応答が途切れたため中止しました")
            if interrupted or self._interrupt.is_set() or not self._allowed():
                self.repository.defer_job(job.job_id, "録画・設定OFF・終了のため中断して待機")
                return
            if process.returncode != 0 or error or result is None:
                details = error or "\n".join(stderr_tail)[-2000:] or "解析workerが正常完了しませんでした"
                raise CardAnalysisError(details)
            if result.get("analyzer_version") != job.analyzer_version:
                raise CardAnalysisError("解析方式の版がジョブと一致しません")
            values = result.get("observations")
            fingerprint = result.get("source_fingerprint")
            if not isinstance(values, list) or not isinstance(fingerprint, dict):
                raise CardAnalysisError("解析結果の形式が不正です")
            final = CardInputFingerprint.from_dict(fingerprint)
            source = Path(final.path)
            stat = source.stat()
            if (stat.st_size, stat.st_mtime_ns) != (final.size_bytes, final.mtime_ns):
                raise CardAnalysisError("録画が変更されたため解析結果を保存できません")
            metadata = {key: result.get(key) for key in (
                "coverage", "duration_ms", "frames_processed", "warnings", "metrics",
            )}
            self.repository.complete_job(job.job_id, values, fingerprint=final, result_metadata=metadata)
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            current = self.repository.get_job(job.job_id)
            if current.state in {"queued", "running", "waiting"}:
                if self._interrupt.is_set() or self._stop.is_set():
                    self.repository.defer_job(job.job_id, "録画・設定OFF・終了のため中断して待機")
                else:
                    self.repository.fail_job(job.job_id, str(exc) or "カード分析に失敗しました")
        finally:
            if process is not None:
                self._terminate(process)
                for reader in readers:
                    reader.join(timeout=1.0)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
            with self._lock:
                self._process = None
            # 新規作成した一時requestだけを削除し、runtimeのキュー/ログは残す。
            if request_path.resolve().parent == Path(tempfile.gettempdir()).resolve():
                request_path.unlink(missing_ok=True)

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)

    @staticmethod
    def _read_messages(stream, messages: queue.Queue[dict[str, object]]) -> None:
        try:
            while line := stream.readline(8 * 1024 * 1024 + 1):
                if len(line) > 8 * 1024 * 1024:
                    raise ValueError("解析通知がサイズ上限を超えました")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("解析通知はJSON objectである必要があります")
                messages.put(value)
        except Exception as exc:
            messages.put({"type": "protocol_error", "message": str(exc)})

    @staticmethod
    def _read_stderr(stream, tail: deque[str]) -> None:
        for line in stream:
            tail.append(line.rstrip()[-500:])
