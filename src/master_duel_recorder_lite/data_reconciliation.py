from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from .duel_records import DuelRecord, DuelRecordRepository
from .history_database import connect_history_database
from .recording_history import RecordingHistoryError, RecordingHistoryRepository


@dataclass(frozen=True)
class RelinkPreview:
    recording_id: str
    previous_path: Path
    candidate_path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class DuplicateCandidate:
    left_duel_id: str
    right_duel_id: str
    score: int
    reasons: tuple[str, ...]
    time_delta_seconds: float


class DataReconciliationService:
    def __init__(self, history: RecordingHistoryRepository) -> None:
        self.history = history
        self.duels = DuelRecordRepository(history.database_path)

    def preview_relink(self, recording_id: str, candidate: Path) -> RelinkPreview:
        entry = self.history.get(recording_id)
        if entry is None:
            raise RecordingHistoryError(f"録画履歴が見つかりません: {recording_id}")
        path = candidate.expanduser().resolve()
        try:
            relative = path.relative_to(self.history.recordings_root)
        except ValueError as exc:
            raise RecordingHistoryError("再関連付け先は録画保存先の配下である必要があります") from exc
        if not path.is_file() or path.suffix.casefold() not in {".mkv", ".mp4"}:
            raise RecordingHistoryError("再関連付け先は既存のmkvまたはmp4である必要があります")
        size = path.stat().st_size
        if size <= 0:
            raise RecordingHistoryError("空の録画ファイルは再関連付けできません")
        with closing(connect_history_database(self.history.database_path)) as connection:
            used = connection.execute(
                "SELECT recording_id FROM recordings WHERE output_path = ? AND recording_id <> ?",
                (relative.as_posix(), recording_id),
            ).fetchone()
        if used is not None:
            raise RecordingHistoryError(f"別の録画履歴が使用中です: {used['recording_id']}")
        return RelinkPreview(
            recording_id,
            entry.output_path,
            relative,
            size,
            _file_hash(path),
        )

    def relink(self, preview: RelinkPreview) -> None:
        verified = self.preview_relink(
            preview.recording_id, self.history.recordings_root / preview.candidate_path
        )
        if verified.size_bytes != preview.size_bytes or verified.sha256 != preview.sha256:
            raise RecordingHistoryError("確認後に録画ファイルが変更されました")
        with closing(connect_history_database(self.history.database_path)) as connection, connection:
            cursor = connection.execute(
                "UPDATE recordings SET output_path = ?, size_bytes = ?, updated_at = ? "
                "WHERE recording_id = ? AND output_path = ?",
                (
                    preview.candidate_path.as_posix(),
                    preview.size_bytes,
                    datetime.now(timezone.utc).isoformat(),
                    preview.recording_id,
                    preview.previous_path.as_posix(),
                ),
            )
            if cursor.rowcount != 1:
                raise RecordingHistoryError("録画履歴が変更されたため再関連付けを中止しました")

    def duplicate_candidates(
        self, *, maximum_seconds: int = 120
    ) -> tuple[DuplicateCandidate, ...]:
        if maximum_seconds < 10 or maximum_seconds > 600:
            raise ValueError("重複検出時間は10秒から600秒で指定してください")
        records = sorted(self.duels.list(limit=1000), key=lambda item: item.occurred_at)
        results: list[DuplicateCandidate] = []
        for index, left in enumerate(records):
            for right in records[index + 1 :]:
                delta = (right.occurred_at - left.occurred_at).total_seconds()
                if delta > maximum_seconds:
                    break
                candidate = self._compare(left, right, delta)
                if candidate is not None:
                    results.append(candidate)
        return tuple(sorted(results, key=lambda item: (-item.score, item.time_delta_seconds)))

    def _compare(
        self, left: DuelRecord, right: DuelRecord, delta: float
    ) -> DuplicateCandidate | None:
        score = 35 if delta <= 30 else 20
        reasons = [f"開始時刻差{delta:.1f}秒"]
        for label, left_value, right_value, points in (
            ("勝敗", left.values.result, right.values.result, 15),
            ("先後", left.values.play_order, right.values.play_order, 10),
            ("自分デッキ", left.values.own_deck, right.values.own_deck, 15),
            ("対戦種別", left.values.duel_type, right.values.duel_type, 5),
        ):
            if left_value and left_value != "unknown" and left_value == right_value:
                score += points
                reasons.append(f"{label}一致")
        if left.recording_id and right.recording_id:
            left_entry = self.history.get(left.recording_id)
            right_entry = self.history.get(right.recording_id)
            if left_entry and right_entry:
                left_path = self.history.recordings_root / left_entry.output_path
                right_path = self.history.recordings_root / right_entry.output_path
                if left_path.is_file() and right_path.is_file():
                    if left_path.stat().st_size == right_path.stat().st_size and _file_hash(left_path) == _file_hash(right_path):
                        score += 50
                        reasons.append("録画ハッシュ一致")
                    elif score < 75:
                        return None
        elif score < 70:
            return None
        if score < 70:
            return None
        return DuplicateCandidate(left.duel_id, right.duel_id, score, tuple(reasons), delta)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
