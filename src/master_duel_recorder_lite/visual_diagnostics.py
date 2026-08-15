from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
import zipfile
from uuid import uuid4

from .visual_detection import FrameAnalysis


MAX_SAMPLES = 900
MAX_SESSIONS = 10
MAX_TOTAL_BYTES = 2 * 1024 * 1024


class VisualDiagnosticSession:
    """Writes bounded numeric diagnostics without persisting captured images."""

    def __init__(
        self,
        logs_root: Path,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.directory = logs_root / "visual-monitor"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.monotonic = monotonic
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        started = self.clock().astimezone(timezone.utc)
        name = f"{started:%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.json"
        self.path = self.directory / name
        self.started_at = started
        self.samples: deque[dict[str, object]] = deque(maxlen=MAX_SAMPLES)
        self.transitions: deque[dict[str, object]] = deque(maxlen=100)
        self._last_sample_at: float | None = None
        self._first_analysis_at: float | None = None
        self._analyzed_frames = 0
        self._closed = False
        self._lock = threading.RLock()
        self._write()

    def record(
        self,
        analysis: FrameAnalysis,
        *,
        effective_fps: float = 0.0,
        restart_count: int = 0,
    ) -> bool:
        with self._lock:
            if self._closed:
                return False
            now = self.monotonic()
            if self._first_analysis_at is None:
                self._first_analysis_at = now
            self._analyzed_frames += 1
            if self._last_sample_at is not None and now - self._last_sample_at < 1.0:
                return False
            self._last_sample_at = now
            measured_fps = effective_fps
            if (
                measured_fps <= 0
                and self._first_analysis_at is not None
                and now > self._first_analysis_at
            ):
                measured_fps = (self._analyzed_frames - 1) / (
                    now - self._first_analysis_at
                )
            self.samples.append(
                {
                    "at": self.clock().astimezone(timezone.utc).isoformat(),
                    "elapsed_ms": analysis.elapsed_ms,
                    "state": analysis.state.value,
                    "profile": analysis.profile_name,
                    "width": analysis.source_width,
                    "height": analysis.source_height,
                    "effective_fps": round(max(0.0, measured_fps), 3),
                    "restart_count": max(0, restart_count),
                    "scores": {
                        "coin": round(analysis.coin_score, 4),
                        "board": round(analysis.board_score, 4),
                        "turn": round(analysis.turn_score, 4),
                        "turn_order": round(analysis.turn_order_score, 4),
                        "result": round(analysis.result_score, 4),
                        "error": round(analysis.error_score, 4),
                        "replay": round(analysis.replay_score, 4),
                        "overlay": round(analysis.overlay_score, 4),
                        "loading": round(analysis.loading_score, 4),
                    },
                    "candidates": [
                        {
                            "event": item.event_type,
                            "confidence": round(item.confidence, 4),
                            "outcome": item.outcome,
                            "play_order": item.play_order,
                            "evidence": item.evidence,
                        }
                        for item in analysis.candidates
                    ],
                    "agreements": [
                        {
                            "event": item.event_type,
                            "evidence": item.evidence,
                            "matched": item.matched,
                            "required": item.required,
                            "window": item.window,
                        }
                        for item in analysis.agreements
                    ],
                }
            )
            self._write()
            return True

    def transition(
        self,
        event: str,
        *,
        elapsed_ms: int | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        with self._lock:
            if self._closed:
                return
            transition: dict[str, object] = {
                "at": self.clock().astimezone(timezone.utc).isoformat(),
                "event": event,
                "elapsed_ms": elapsed_ms,
            }
            if details:
                transition["details"] = details
            self.transitions.append(transition)
            self._write()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._write(ended_at=self.clock().astimezone(timezone.utc))

    def export(self, destination: Path) -> Path:
        """Issue添付用に数値JSONだけをZIPへ格納します。"""
        target = destination.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        with self._lock:
            self._write()
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.write(self.path, arcname=self.path.name)
        os.replace(temporary, target)
        return target

    def _write(self, *, ended_at: datetime | None = None) -> None:
        document = {
            "schema_version": 1,
            "started_at": self.started_at.isoformat(),
            "ended_at": ended_at.isoformat() if ended_at is not None else None,
            "samples": list(self.samples),
            "transitions": list(self.transitions),
        }
        payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, self.path)
        self._rotate()

    def _rotate(self) -> None:
        files = sorted(
            self.directory.glob("*.json"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
        total = 0
        for index, path in enumerate(files):
            size = path.stat().st_size
            total += size
            if index >= MAX_SESSIONS or total > MAX_TOTAL_BYTES:
                path.unlink(missing_ok=True)
