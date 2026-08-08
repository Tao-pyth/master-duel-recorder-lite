from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FailureCode(str, Enum):
    APPLICATION_INTERRUPTED = "application_interrupted"
    STORAGE_FULL = "storage_full"
    OUTPUT_MISSING = "output_missing"
    OUTPUT_EMPTY = "output_empty"
    PROCESS_CRASH = "process_crash"
    OPERATION_TIMEOUT = "operation_timeout"
    OUTPUT_CORRUPT = "output_corrupt"
    REPAIR_FAILED = "repair_failed"
    UNKNOWN = "unknown"


class RecoveryPolicy(str, Enum):
    RETRYABLE = "retryable"
    MANUAL_REVIEW = "manual_review"
    UNRECOVERABLE = "unrecoverable"


@dataclass(frozen=True)
class FailureClassification:
    code: FailureCode
    policy: RecoveryPolicy
    user_message: str
    internal_diagnostic: str


def classify_recording_failure(
    *,
    error: str | None,
    returncode: int | None,
    output_exists: bool,
    output_size: int,
    interrupted: bool = False,
) -> FailureClassification:
    diagnostic = error.strip() if error and error.strip() else "原因を特定できません"
    normalized = diagnostic.casefold()
    if interrupted:
        return FailureClassification(
            FailureCode.APPLICATION_INTERRUPTED,
            RecoveryPolicy.MANUAL_REVIEW,
            "前回の録画が完了前に中断されました。元ファイルを検査してください。",
            diagnostic,
        )
    if any(
        marker in normalized
        for marker in ("no space", "disk full", "not enough space", "容量不足", "winerror 112")
    ):
        return FailureClassification(
            FailureCode.STORAGE_FULL,
            RecoveryPolicy.RETRYABLE,
            "保存先の空き容量を確保してから再試行してください。",
            diagnostic,
        )
    if not output_exists:
        return FailureClassification(
            FailureCode.OUTPUT_MISSING,
            RecoveryPolicy.UNRECOVERABLE,
            "録画ファイルが作成されていないため修復できません。",
            diagnostic,
        )
    if output_size <= 0:
        return FailureClassification(
            FailureCode.OUTPUT_EMPTY,
            RecoveryPolicy.UNRECOVERABLE,
            "録画ファイルが空のため修復できません。",
            diagnostic,
        )
    if "timeout" in normalized or "タイムアウト" in normalized:
        return FailureClassification(
            FailureCode.OPERATION_TIMEOUT,
            RecoveryPolicy.RETRYABLE,
            "処理がタイムアウトしました。環境を確認して再試行してください。",
            diagnostic,
        )
    if returncode not in {None, 0}:
        return FailureClassification(
            FailureCode.PROCESS_CRASH,
            RecoveryPolicy.MANUAL_REVIEW,
            "FFmpegが異常終了しました。元ファイルを検査してください。",
            diagnostic,
        )
    return FailureClassification(
        FailureCode.UNKNOWN,
        RecoveryPolicy.MANUAL_REVIEW,
        "録画を正常に確定できませんでした。元ファイルと診断を確認してください。",
        diagnostic,
    )
