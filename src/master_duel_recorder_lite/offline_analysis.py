from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .upload_media import MediaValidationStatus, UploadMediaValidator
from .visual_detection import DetectionCandidate


class OfflineAnalysisMode(str, Enum):
    PAST_VIDEO = "past_video"
    REPLAY = "replay"


class OfflineAnalysisError(RuntimeError):
    """既存動画の後解析を安全に開始できない場合のエラーです。"""


CandidateProvider = Callable[[Path, OfflineAnalysisMode], tuple[DetectionCandidate, ...]]


@dataclass(frozen=True)
class OfflineAnalysisReport:
    source_path: Path
    mode: OfflineAnalysisMode
    duration_seconds: float | None
    candidates: tuple[DetectionCandidate, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "source_name": self.source_path.name,
            "mode": self.mode.value,
            "duration_seconds": self.duration_seconds,
            "candidate_count": len(self.candidates),
            "candidates": [
                {
                    "event_type": candidate.event_type,
                    "elapsed_ms": candidate.elapsed_ms,
                    "confidence": candidate.confidence,
                    "reason": candidate.reason,
                }
                for candidate in self.candidates
            ],
            "warnings": list(self.warnings),
        }


class OfflineAnalysisService:
    def __init__(
        self,
        *,
        validator: UploadMediaValidator,
        candidate_provider: CandidateProvider | None = None,
    ) -> None:
        self.validator = validator
        self.candidate_provider = candidate_provider or _empty_candidates

    def analyze(
        self,
        source_path: Path,
        *,
        mode: OfflineAnalysisMode = OfflineAnalysisMode.PAST_VIDEO,
    ) -> OfflineAnalysisReport:
        source = source_path.expanduser().resolve()
        before = _file_fingerprint(source)
        validation = self.validator.validate(source)
        after = _file_fingerprint(source)
        if before != after:
            raise OfflineAnalysisError("後解析中に元動画が変更されました")
        if validation.status is MediaValidationStatus.INVALID:
            raise OfflineAnalysisError("動画を後解析できません: " + "、".join(validation.errors))
        candidates = self.candidate_provider(source, mode)
        if _file_fingerprint(source) != after:
            raise OfflineAnalysisError("候補抽出中に元動画が変更されました")
        return OfflineAnalysisReport(
            source,
            mode,
            validation.duration_seconds,
            candidates,
            validation.warnings,
        )


def _empty_candidates(
    _source_path: Path, _mode: OfflineAnalysisMode
) -> tuple[DetectionCandidate, ...]:
    return ()


def _file_fingerprint(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns
