from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import LoadedAppConfig
from .preflight import CheckStatus, PreflightReport, run_preflight
from .recording_history import (
    HistoryQuery,
    RecordingHistoryError,
    RecordingHistoryRepository,
)
from .recording_state_store import RecordingStateStore, RecordingStateStoreError
from .runtime_paths import RuntimePaths
from .upload_queue import UploadQueueError, UploadQueueState, UploadQueueStore


STATUS_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class OperationalStatus:
    document: dict[str, object]
    exit_code: int


PreflightRunner = Callable[..., PreflightReport]


def collect_operational_status(
    *,
    paths: RuntimePaths,
    loaded: LoadedAppConfig,
    preflight_runner: PreflightRunner | None = None,
) -> OperationalStatus:
    errors: list[dict[str, str]] = []
    attention = False
    environment_error = False

    runner = preflight_runner or run_preflight
    try:
        report = runner(
            paths=paths, config=loaded.config, config_loaded=loaded.config_loaded
        )
        environment = _environment_document(report, paths, loaded)
        environment_error = environment["status"] == "error"
    except Exception as exc:
        environment_error = True
        environment = {"status": "error", "checks": []}
        errors.append(
            _error(
                "environment",
                "E_STATUS_ENVIRONMENT",
                _redact(str(exc), paths, loaded),
                "doctorを実行してください。",
            )
        )

    runtime = {
        "status": "ok" if paths.root.is_dir() else "warning",
        "directories": {
            "config": paths.config.is_dir(),
            "data": paths.data.is_dir(),
            "logs": paths.logs.is_dir(),
            "recordings": paths.recordings.is_dir(),
            "exports": paths.exports.is_dir(),
            "queue": paths.queue.is_dir(),
        },
    }

    try:
        loaded_state = RecordingStateStore(paths).load()
        recording = {
            "status": "ok",
            "state": loaded_state.value.state if loaded_state is not None else "idle",
            "recording_id": loaded_state.value.recording_id
            if loaded_state is not None
            else None,
            "used_previous": loaded_state.used_previous
            if loaded_state is not None
            else False,
        }
        if loaded_state is not None and loaded_state.used_previous:
            recording["status"] = "warning"
            attention = True
    except RecordingStateStoreError as exc:
        recording = {
            "status": "error",
            "state": "unknown",
            "recording_id": None,
            "used_previous": False,
        }
        errors.append(
            _error(
                "recording",
                "E_STATUS_RECORDING",
                _redact(str(exc), paths, loaded),
                "statusとhistory checkで状態を確認してください。",
            )
        )

    repository: RecordingHistoryRepository | None = None
    try:
        repository = RecordingHistoryRepository.from_runtime_paths(paths)
        entries = repository.query(HistoryQuery(limit=1000))
        consistency = repository.check_consistency()
        history = {
            "status": "warning" if consistency else "ok",
            "total": len(entries),
            "state_counts": dict(
                sorted(Counter(entry.state for entry in entries).items())
            ),
            "consistency_issues": len(consistency),
            "truncated": len(entries) == 1000,
        }
        attention = attention or bool(consistency)
    except RecordingHistoryError as exc:
        history = {
            "status": "error",
            "total": None,
            "state_counts": {},
            "consistency_issues": None,
            "truncated": False,
        }
        errors.append(
            _error(
                "history",
                "E_STATUS_HISTORY",
                _redact(str(exc), paths, loaded),
                "history checkを実行してください。",
            )
        )

    try:
        queue_items = UploadQueueStore(paths).list()
        queue_counts = Counter(item.state.value for item in queue_items)
        queue_attention = bool(queue_counts[UploadQueueState.FAILED.value])
        upload_queue = {
            "status": "warning" if queue_attention else "ok",
            "total": len(queue_items),
            "state_counts": dict(sorted(queue_counts.items())),
        }
        attention = attention or queue_attention
    except UploadQueueError as exc:
        upload_queue = {"status": "error", "total": None, "state_counts": {}}
        errors.append(
            _error(
                "upload_queue",
                "E_STATUS_QUEUE",
                _redact(str(exc), paths, loaded),
                "prepare listでキューを確認してください。",
            )
        )

    if errors or environment_error:
        overall = "error"
    elif (
        attention
        or environment["status"] == "warning"
        or runtime["status"] == "warning"
    ):
        overall = "warning"
    else:
        overall = "ok"

    document: dict[str, object] = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "overall": overall,
        "environment": environment,
        "runtime": runtime,
        "recording": recording,
        "history": history,
        "upload_queue": upload_queue,
        "errors": errors,
    }
    exit_code = 3 if errors else 2 if environment_error else 4 if attention else 0
    return OperationalStatus(document=document, exit_code=exit_code)


def _environment_document(
    report: PreflightReport,
    paths: RuntimePaths,
    loaded: LoadedAppConfig,
) -> dict[str, object]:
    if any(check.status is CheckStatus.ERROR for check in report.checks):
        status = "error"
    elif any(check.status is CheckStatus.WARNING for check in report.checks):
        status = "warning"
    else:
        status = "ok"
    return {
        "status": status,
        "checks": [
            {
                "code": check.code,
                "label": check.label,
                "status": check.status.value,
                "message": _redact(check.message, paths, loaded),
            }
            for check in report.checks
        ],
    }


def _redact(message: str, paths: RuntimePaths, loaded: LoadedAppConfig) -> str:
    redacted = message
    candidates = [paths.root, Path.home()]
    configured_ffmpeg = Path(loaded.config.ffmpeg_path)
    if configured_ffmpeg.is_absolute():
        candidates.append(configured_ffmpeg)
    for candidate in candidates:
        text = str(candidate)
        if text:
            redacted = redacted.replace(text, "<path>")
            redacted = redacted.replace(text.replace("\\", "/"), "<path>")
    return redacted


def _error(section: str, code: str, summary: str, action: str) -> dict[str, str]:
    return {"section": section, "code": code, "summary": summary, "action": action}
