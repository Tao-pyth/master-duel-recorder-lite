from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .clip_export import ClipRange, resolve_clip_range
from .duel_records import DuelRecord
from .duel_timeline import DuelEvent
from .recording_history import RecordingHistoryEntry
from .recording_browsing import RecordingReference


class ReviewModelError(ValueError):
    """レビュー画面へ渡す操作値が不正な場合のエラーです。"""


@dataclass(frozen=True)
class ReviewRecordingSummary:
    recording_id: str
    state: str
    source: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None
    size_bytes: int | None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "recording_id": self.recording_id,
            "state": self.state,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_seconds": self.duration_seconds,
            "size_bytes": self.size_bytes,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ReviewVideoReference:
    recording_id: str
    path: Path
    suffix: str
    can_play_in_app: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "recording_id": self.recording_id,
            "path": str(self.path),
            "suffix": self.suffix,
            "can_play_in_app": self.can_play_in_app,
        }


@dataclass(frozen=True)
class ReviewTimelineEvent:
    event_id: str
    elapsed_ms: int
    elapsed_label: str
    event_type: str
    status: str
    label: str
    source: str
    confidence: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "elapsed_ms": self.elapsed_ms,
            "elapsed_label": self.elapsed_label,
            "event_type": self.event_type,
            "status": self.status,
            "label": self.label,
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ReviewVisualTimelineItem:
    event_id: str
    elapsed_ms: int
    ratio: float
    kind: str
    label: str
    tooltip: str
    in_range: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "elapsed_ms": self.elapsed_ms,
            "ratio": self.ratio,
            "kind": self.kind,
            "label": self.label,
            "tooltip": self.tooltip,
            "in_range": self.in_range,
        }


@dataclass(frozen=True)
class ReviewDuelSummary:
    duel_id: str | None
    status: str
    result: str
    play_order: str
    coin_face: str
    own_deck: str
    opponent_deck: str
    duel_type: str
    tags: tuple[str, ...]
    notes: str
    youtube_watch_url: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "duel_id": self.duel_id,
            "status": self.status,
            "result": self.result,
            "play_order": self.play_order,
            "coin_face": self.coin_face,
            "own_deck": self.own_deck,
            "opponent_deck": self.opponent_deck,
            "duel_type": self.duel_type,
            "tags": list(self.tags),
            "notes": self.notes,
            "youtube_watch_url": self.youtube_watch_url,
        }


@dataclass(frozen=True)
class ReviewClipCandidate:
    event_id: str | None
    center_seconds: float
    clip_range: ClipRange
    label: str

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "center_seconds": self.center_seconds,
            "start_seconds": self.clip_range.start_seconds,
            "duration_seconds": self.clip_range.duration_seconds,
            "label": self.label,
        }


@dataclass(frozen=True)
class ReviewViewModel:
    recording: ReviewRecordingSummary
    video: ReviewVideoReference
    duel: ReviewDuelSummary
    timeline: tuple[ReviewTimelineEvent, ...]
    visual_timeline: tuple[ReviewVisualTimelineItem, ...]
    clip_candidates: tuple[ReviewClipCandidate, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "recording": self.recording.to_dict(),
            "video": self.video.to_dict(),
            "duel": self.duel.to_dict(),
            "timeline": [event.to_dict() for event in self.timeline],
            "visual_timeline": [item.to_dict() for item in self.visual_timeline],
            "clip_candidates": [
                candidate.to_dict() for candidate in self.clip_candidates
            ],
        }


@dataclass(frozen=True)
class ReviewSeekRequest:
    recording_id: str
    elapsed_ms: int

    def __post_init__(self) -> None:
        _recording_id(self.recording_id)
        _nonnegative_int(self.elapsed_ms, "elapsed_ms")


@dataclass(frozen=True)
class ReviewMarkerRequest:
    recording_id: str
    elapsed_ms: int
    label: str

    def __post_init__(self) -> None:
        _recording_id(self.recording_id)
        _nonnegative_int(self.elapsed_ms, "elapsed_ms")
        if not self.label.strip():
            raise ReviewModelError("labelは空にできません")
        if len(self.label.strip()) > 200:
            raise ReviewModelError("labelは200文字以内である必要があります")


@dataclass(frozen=True)
class ReviewClipExportRequest:
    recording_id: str
    center_seconds: float
    before_seconds: float = 30.0
    after_seconds: float = 30.0

    def __post_init__(self) -> None:
        _recording_id(self.recording_id)
        for key, value in (
            ("center_seconds", self.center_seconds),
            ("before_seconds", self.before_seconds),
            ("after_seconds", self.after_seconds),
        ):
            if isinstance(value, bool) or value < 0:
                raise ReviewModelError(f"{key}は0以上の数値である必要があります")


def build_review_view_model(
    *,
    history: RecordingHistoryEntry,
    reference: RecordingReference,
    duel_record: DuelRecord | None,
    timeline: tuple[DuelEvent, ...],
    youtube_watch_url: str | None,
) -> ReviewViewModel:
    duration = history.duration_seconds
    review_timeline = tuple(_timeline_event(event) for event in timeline)
    return ReviewViewModel(
        recording=ReviewRecordingSummary(
            recording_id=history.recording_id,
            state=history.state,
            source=history.source,
            created_at=history.created_at,
            started_at=history.started_at,
            ended_at=history.ended_at,
            duration_seconds=duration,
            size_bytes=history.size_bytes,
            warnings=reference.warnings,
        ),
        video=ReviewVideoReference(
            recording_id=reference.recording_id,
            path=reference.path,
            suffix=reference.path.suffix.lower(),
            can_play_in_app=reference.path.suffix.lower() in {".mp4", ".mkv"},
        ),
        duel=_duel_summary(duel_record, youtube_watch_url),
        timeline=review_timeline,
        visual_timeline=build_visual_timeline_items(
            review_timeline,
            duration_seconds=duration,
        ),
        clip_candidates=tuple(
            _clip_candidate(event, duration_seconds=duration) for event in timeline
        ),
    )


def build_visual_timeline_items(
    timeline: tuple[ReviewTimelineEvent, ...],
    *,
    duration_seconds: float | None,
) -> tuple[ReviewVisualTimelineItem, ...]:
    duration_ms = int(duration_seconds * 1000) if duration_seconds and duration_seconds > 0 else 0
    return tuple(
        _visual_timeline_item(event, duration_ms=duration_ms)
        for event in timeline
    )


def _duel_summary(
    duel_record: DuelRecord | None, youtube_watch_url: str | None
) -> ReviewDuelSummary:
    if duel_record is None:
        return ReviewDuelSummary(
            duel_id=None,
            status="unknown",
            result="unknown",
            play_order="unknown",
            coin_face="unknown",
            own_deck="",
            opponent_deck="",
            duel_type="other",
            tags=(),
            notes="",
            youtube_watch_url=youtube_watch_url,
        )
    values = duel_record.values
    return ReviewDuelSummary(
        duel_id=duel_record.duel_id,
        status=values.status,
        result=values.result,
        play_order=values.play_order,
        coin_face=values.coin_face,
        own_deck=values.own_deck,
        opponent_deck=values.opponent_deck,
        duel_type=values.duel_type,
        tags=values.tags,
        notes=values.notes,
        youtube_watch_url=youtube_watch_url,
    )


def _timeline_event(event: DuelEvent) -> ReviewTimelineEvent:
    label = event.label or event.outcome or event.actor or event.event_type
    return ReviewTimelineEvent(
        event_id=event.event_id,
        elapsed_ms=event.elapsed_ms,
        elapsed_label=_elapsed_label(event.elapsed_ms),
        event_type=event.event_type,
        status=event.status,
        label=label,
        source=event.source,
        confidence=event.confidence,
    )


def _clip_candidate(
    event: DuelEvent, *, duration_seconds: float | None
) -> ReviewClipCandidate:
    center = round(event.elapsed_ms / 1000, 3)
    return ReviewClipCandidate(
        event_id=event.event_id,
        center_seconds=center,
        clip_range=resolve_clip_range(
            center_seconds=center,
            duration_seconds=duration_seconds,
        ),
        label=event.label or event.event_type,
    )


def _visual_timeline_item(
    event: ReviewTimelineEvent,
    *,
    duration_ms: int,
) -> ReviewVisualTimelineItem:
    in_range = duration_ms > 0 and 0 <= event.elapsed_ms <= duration_ms
    ratio = _timeline_ratio(event.elapsed_ms, duration_ms=duration_ms)
    kind = _visual_timeline_kind(event)
    return ReviewVisualTimelineItem(
        event_id=event.event_id,
        elapsed_ms=max(0, event.elapsed_ms),
        ratio=ratio,
        kind=kind,
        label=event.label,
        tooltip=f"{event.elapsed_label} / {event.label} / {kind}",
        in_range=in_range,
    )


def _timeline_ratio(elapsed_ms: int, *, duration_ms: int) -> float:
    if duration_ms <= 0:
        return 0.0
    return round(min(1.0, max(0.0, elapsed_ms / duration_ms)), 6)


def _visual_timeline_kind(event: ReviewTimelineEvent) -> str:
    if event.event_type == "duel_start":
        return "duel_start"
    if event.event_type == "marker" and event.source == "manual":
        return "manual_marker"
    if event.status == "candidate":
        return "clip_candidate"
    return "timeline_event"


def _elapsed_label(elapsed_ms: int) -> str:
    total_seconds = max(0, elapsed_ms) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}.{elapsed_ms % 1000:03d}"


def _recording_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ReviewModelError("recording_idは空にできません")


def _nonnegative_int(value: int, key: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReviewModelError(f"{key}は0以上の整数である必要があります")
