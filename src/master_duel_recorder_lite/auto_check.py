from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .game_window import GameWindowObservation, GameWindowStatus
from .preflight import CheckStatus, PreflightReport
from .visual_detection import FrameAnalysis, MasterDuelState


class AutoCheckStatus(str, Enum):
    READY = "ready"
    WARNING = "warning"
    FAILED = "failed"


@dataclass(frozen=True)
class AutoCheckResult:
    status: AutoCheckStatus
    headline: str
    reasons: tuple[str, ...]
    duration_seconds: int
    sampled_frames: int

    @property
    def succeeded(self) -> bool:
        return self.status is not AutoCheckStatus.FAILED

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": self.status.value,
            "headline": self.headline,
            "reasons": list(self.reasons),
            "duration_seconds": self.duration_seconds,
            "sampled_frames": self.sampled_frames,
        }


def evaluate_auto_check(
    *,
    preflight: PreflightReport,
    game_window: GameWindowObservation | None = None,
    analyses: tuple[FrameAnalysis, ...] = (),
    duration_seconds: int = 30,
    minimum_confidence: float = 0.70,
) -> AutoCheckResult:
    if duration_seconds < 5:
        raise ValueError("duration_secondsは5秒以上である必要があります")
    if not 0 <= minimum_confidence <= 1:
        raise ValueError("minimum_confidenceは0から1である必要があります")

    errors = [
        f"{check.label}: {check.message}"
        for check in preflight.checks
        if check.status is CheckStatus.ERROR
    ]
    if errors:
        return AutoCheckResult(
            AutoCheckStatus.FAILED,
            "録画前に直す必要があります",
            tuple(errors),
            duration_seconds,
            len(analyses),
        )

    reasons: list[str] = [
        f"{check.label}: {check.message}"
        for check in preflight.checks
        if check.status is CheckStatus.WARNING
    ]
    if game_window is not None and game_window.status is not GameWindowStatus.READY:
        return AutoCheckResult(
            AutoCheckStatus.FAILED,
            "Master Duelの画面を確認できません",
            (game_window.message,),
            duration_seconds,
            len(analyses),
        )

    if not analyses:
        if reasons:
            headline = "録画環境は使えますが画面安定性は未確認です"
            status = AutoCheckStatus.WARNING
        else:
            headline = "録画環境は利用できます"
            status = AutoCheckStatus.READY
        return AutoCheckResult(
            status,
            headline,
            tuple(reasons or ("30秒の画面サンプルは未取得です",)),
            duration_seconds,
            0,
        )

    unsupported = sum(1 for item in analyses if item.profile_name == "unknown")
    replay = sum(1 for item in analyses if item.state is MasterDuelState.REPLAY)
    errors_seen = sum(1 for item in analyses if item.error_score >= minimum_confidence)
    active = sum(
        1
        for item in analyses
        if max(
            item.coin_score,
            item.board_score,
            item.turn_score,
            item.turn_order_score,
            item.result_score,
            item.replay_score,
        )
        >= minimum_confidence
    )

    if errors_seen:
        reasons.append("ゲーム側のエラー画面候補が検出されています")
        return AutoCheckResult(
            AutoCheckStatus.FAILED,
            "自動録画が失敗しそうです",
            tuple(reasons),
            duration_seconds,
            len(analyses),
        )
    if unsupported > len(analyses) / 2:
        reasons.append("対応外の表示比率または画面構成が多く検出されています")
        return AutoCheckResult(
            AutoCheckStatus.FAILED,
            "自動判定に必要な画面特徴が足りません",
            tuple(reasons),
            duration_seconds,
            len(analyses),
        )
    if replay:
        reasons.append("リプレイ画面候補です。ライブ録画開始ではなく後解析向けです")
        return AutoCheckResult(
            AutoCheckStatus.WARNING,
            "リプレイとしては解析できます",
            tuple(reasons),
            duration_seconds,
            len(analyses),
        )
    if active >= max(1, len(analyses) // 3):
        reasons.append("盤面、ターン、コイントス、結果のいずれかを安定して検出できます")
        return AutoCheckResult(
            AutoCheckStatus.READY,
            "自動録画を開始できる見込みです",
            tuple(reasons),
            duration_seconds,
            len(analyses),
        )
    reasons.append("対戦画面らしい特徴が少ないため、画面を確認してください")
    return AutoCheckResult(
        AutoCheckStatus.WARNING,
        "自動録画の判定が不安定です",
        tuple(reasons),
        duration_seconds,
        len(analyses),
    )
