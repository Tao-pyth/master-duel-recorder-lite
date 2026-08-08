from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from .detection import DetectionSignal, DuelObservation
from .game_window import GameWindowMonitor, GameWindowStatus


Clock = Callable[[], datetime]


class MasterDuelWindowDetector:
    """ゲームウィンドウの存在を候補信号へ変換する第一段階の検出器です。"""

    def __init__(self, monitor: GameWindowMonitor, *, clock: Clock | None = None) -> None:
        self.monitor = monitor
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def observe(self) -> DuelObservation:
        game = self.monitor.observe()
        if game.status is GameWindowStatus.VISIBLE:
            return DuelObservation(DetectionSignal.PRESENT, 0.7, game.message, self.clock())
        if game.status in {
            GameWindowStatus.NOT_RUNNING,
            GameWindowStatus.RUNNING_NO_WINDOW,
            GameWindowStatus.MINIMIZED,
        }:
            return DuelObservation(DetectionSignal.ABSENT, 0.8, game.message, self.clock())
        return DuelObservation(DetectionSignal.UNKNOWN, 0.0, game.message, self.clock())
