from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import os

from .recording_failure import classify_recording_failure
from .recording_history import (
    HistoryQuery,
    RecordingHistoryEntry,
    RecordingHistoryError,
    RecordingHistoryRepository,
)
from .recording_lock import RecordingBusyError, RecordingLock
from .recording_state_store import (
    LoadedRecordingState,
    RecordingStateStore,
    RecordingStateStoreError,
)
from .runtime_paths import RuntimePaths


class RecoveryError(RuntimeError):
    """中断録画を安全に検出または更新できない場合のエラーです。"""


class InterruptedDetectionKind(str, Enum):
    ACTIVE = "active"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class InterruptedDetection:
    recording_id: str
    kind: InterruptedDetectionKind
    message: str


ProcessChecker = Callable[[int], bool]


class RecoveryManager:
    def __init__(
        self,
        *,
        paths: RuntimePaths,
        repository: RecordingHistoryRepository | None = None,
        state_store: RecordingStateStore | None = None,
        process_checker: ProcessChecker | None = None,
    ) -> None:
        self.paths = paths
        self.repository = repository or RecordingHistoryRepository.from_runtime_paths(paths)
        self.state_store = state_store or RecordingStateStore(paths)
        self.process_checker = process_checker or is_process_running

    def detect_interrupted(self) -> tuple[InterruptedDetection, ...]:
        active_entries = self._active_entries()
        if not active_entries:
            return ()

        loaded: LoadedRecordingState | None = None
        state_diagnostic: str | None = None
        try:
            loaded = self.state_store.load()
        except RecordingStateStoreError as exc:
            state_diagnostic = str(exc)

        lock: RecordingLock | None = None
        try:
            lock = RecordingLock.acquire(
                self.paths.data / "recording.lock",
                recording_id="recovery-scan",
            )
        except RecordingBusyError:
            return tuple(
                InterruptedDetection(
                    entry.recording_id,
                    InterruptedDetectionKind.ACTIVE,
                    "別プロセスが録画ロックを保持しているため変更しません",
                )
                for entry in active_entries
            )
        except OSError as exc:
            raise RecoveryError(f"録画ロックを確認できません: {exc}") from exc

        detections: list[InterruptedDetection] = []
        try:
            for entry in active_entries:
                if self._matching_process_is_alive(entry, loaded):
                    detections.append(
                        InterruptedDetection(
                            entry.recording_id,
                            InterruptedDetectionKind.ACTIVE,
                            "状態ファイルのプロセスが実行中のため変更しません",
                        )
                    )
                    continue
                output_path = (self.paths.recordings / entry.output_path).resolve()
                output_exists = output_path.is_file()
                size_bytes = output_path.stat().st_size if output_exists else 0
                diagnostic_parts = ["録画ロックなし、対応プロセスなし"]
                if state_diagnostic:
                    diagnostic_parts.append(f"状態ファイル: {state_diagnostic}")
                classification = classify_recording_failure(
                    error="; ".join(diagnostic_parts),
                    returncode=None,
                    output_exists=output_exists,
                    output_size=size_bytes,
                    interrupted=True,
                )
                updated = self.repository.mark_interrupted(
                    entry.recording_id,
                    classification=classification,
                    ended_at=datetime.now(timezone.utc),
                    size_bytes=size_bytes,
                )
                if loaded is not None and loaded.value.recording_id == entry.recording_id:
                    try:
                        self.state_store.save(
                            recording_id=entry.recording_id,
                            state="failed",
                            source=entry.source,
                            output_path=output_path,
                            started_at=entry.started_at,
                            pid=loaded.value.pid,
                        )
                    except RecordingStateStoreError as exc:
                        self.repository.set_recovery_state(
                            entry.recording_id,
                            state=updated.recovery_state,
                            message=updated.recovery_message or classification.user_message,
                            diagnostic=(
                                f"{updated.recovery_diagnostic or classification.internal_diagnostic}; "
                                f"終端状態を保存できません: {exc}"
                            ),
                        )
                detections.append(
                    InterruptedDetection(
                        entry.recording_id,
                        InterruptedDetectionKind.INTERRUPTED,
                        classification.user_message,
                    )
                )
        except (OSError, RecordingHistoryError) as exc:
            raise RecoveryError(f"中断録画を履歴へ記録できません: {exc}") from exc
        finally:
            if lock is not None:
                lock.release()
        return tuple(detections)

    def _active_entries(self) -> tuple[RecordingHistoryEntry, ...]:
        try:
            entries = self.repository.query(HistoryQuery(state="starting", limit=1000))
            entries += self.repository.query(HistoryQuery(state="recording", limit=1000))
            return tuple(sorted(entries, key=lambda item: item.recording_id))
        except RecordingHistoryError as exc:
            raise RecoveryError(f"録画履歴を確認できません: {exc}") from exc

    def _matching_process_is_alive(
        self,
        entry: RecordingHistoryEntry,
        loaded: LoadedRecordingState | None,
    ) -> bool:
        if loaded is None or loaded.value.recording_id != entry.recording_id:
            return False
        if loaded.value.state not in {"starting", "recording"}:
            return False
        try:
            return self.process_checker(loaded.value.pid)
        except OSError:
            return True


def is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
