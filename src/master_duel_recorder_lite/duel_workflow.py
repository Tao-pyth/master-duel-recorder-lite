from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
import unicodedata
import uuid

from .duel_catalog import DuelCatalogEntry, DuelCatalogRepository
from .duel_records import DuelRecord, DuelRecordRepository, DuelRecordValues
from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .runtime_paths import RuntimePaths
from .seasons import Season, SeasonRepository


class DuelWorkflowError(RuntimeError):
    """日常的な戦績入力ワークフローを完了できない場合のエラーです。"""


@dataclass(frozen=True)
class DuelInputSuggestion:
    values: DuelRecordValues
    reasons: tuple[str, ...]
    decks: tuple[DuelCatalogEntry, ...]
    tags: tuple[DuelCatalogEntry, ...]
    active_seasons: tuple[Season, ...]


@dataclass(frozen=True)
class IncompleteDuel:
    identifier: str
    kind: str
    occurred_at: datetime
    record: DuelRecord | None


@dataclass(frozen=True)
class BulkDuelUpdate:
    season_id: int | None = None
    change_season: bool = False
    own_deck: str | None = None
    coin_face: str | None = None
    duel_type: str | None = None
    add_tags: tuple[str, ...] = ()
    remove_tags: tuple[str, ...] = ()

    def apply(self, values: DuelRecordValues) -> DuelRecordValues:
        tags = list(values.tags)
        removed = {_key(item) for item in self.remove_tags}
        tags = [item for item in tags if _key(item) not in removed]
        existing = {_key(item) for item in tags}
        for item in self.add_tags:
            normalized = unicodedata.normalize("NFC", item.strip())
            if normalized and _key(normalized) not in existing:
                tags.append(normalized)
                existing.add(_key(normalized))
        raw = {
            **values.__dict__,
            "tags": tuple(tags),
        }
        if self.change_season:
            raw["season_id"] = self.season_id
        if self.own_deck is not None:
            raw["own_deck"] = self.own_deck
        if self.coin_face is not None:
            raw["coin_face"] = self.coin_face
        if self.duel_type is not None:
            raw["duel_type"] = self.duel_type
        return DuelRecordValues(**raw).normalized()


@dataclass(frozen=True)
class DuelFilterCriteria:
    season_id: int | None = None
    own_deck_id: int | None = None
    opponent_deck_id: int | None = None
    tag_entry_ids: tuple[int, ...] = ()
    coin_face: str | None = None
    entry_origin: str | None = None

    def normalized(self) -> DuelFilterCriteria:
        if self.entry_origin not in {None, "recording", "manual", "import"}:
            raise DuelWorkflowError("登録元のフィルター値が不正です")
        for value in (self.season_id, self.own_deck_id, self.opponent_deck_id):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise DuelWorkflowError("フィルターIDは1以上の整数で指定してください")
        tag_ids = tuple(dict.fromkeys(self.tag_entry_ids))
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in tag_ids):
            raise DuelWorkflowError("タグIDは1以上の整数で指定してください")
        return DuelFilterCriteria(
            self.season_id,
            self.own_deck_id,
            self.opponent_deck_id,
            tag_ids,
            self.coin_face,
            self.entry_origin,
        )


@dataclass(frozen=True)
class SavedDuelFilter:
    filter_id: str
    name: str
    criteria: DuelFilterCriteria
    created_at: datetime
    updated_at: datetime


class DuelWorkflowService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        connect_history_database(self.database_path).close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> DuelWorkflowService:
        from .data_protection import initialize_protected_history_database

        initialize_protected_history_database(paths)
        return cls(paths.db / HISTORY_DATABASE_NAME)

    def input_suggestion(self, *, occurred_on: date | None = None) -> DuelInputSuggestion:
        target = occurred_on or datetime.now().astimezone().date()
        records = DuelRecordRepository(self.database_path).list(limit=1000)
        catalog = DuelCatalogRepository(self.database_path)
        decks = tuple(
            item
            for item in catalog.list_decks()
            if not item.hidden_from_history_statistics and not item.opponent_only
        )
        tags = tuple(item for item in catalog.list_tags() if not item.is_archived)
        deck_rank = self._usage_rank("deck")
        tag_rank = self._usage_rank("tag")
        decks = tuple(sorted(decks, key=lambda item: self._rank_key(item, deck_rank)))
        tags = tuple(sorted(tags, key=lambda item: self._rank_key(item, tag_rank)))
        latest_input = records[0] if records else None
        latest = next((item for item in records if item.values.status == "confirmed"), None)
        preferences = catalog.preferences().to_record_values()
        values = latest.values if latest is not None else preferences
        previous_values = (
            preferences
            if preferences != DuelRecordValues()
            else latest_input.values
            if latest_input is not None
            else None
        )
        if previous_values is not None:
            values = DuelRecordValues(
                **{
                    **values.__dict__,
                    "duel_type": previous_values.duel_type,
                    "own_deck": previous_values.own_deck,
                }
            )
        if latest_input is not None:
            latest_values = latest_input.values
            values = DuelRecordValues(
                **{
                    **values.__dict__,
                    "season_id": latest_values.season_id,
                }
            )
        values = DuelRecordValues(**{**values.__dict__, "status": "confirmed"})
        reasons: list[str] = []
        if latest_input is not None:
            reasons.append("前回入力した対戦種別・自分デッキ・シーズンを候補にしました")
            if latest is not None and latest.duel_id != latest_input.duel_id:
                reasons.append("勝敗・先後などは直近の確認済み戦績から候補にしました")
            elif latest is not None:
                reasons.append("直近の確認済み戦績から入力候補を引き継ぎました")
        elif preferences != DuelRecordValues():
            reasons.append("前回入力したデッキ・タグ・対戦種別を候補にしました")
        active = tuple(
            item
            for item in SeasonRepository(self.database_path).list()
            if item.contains(target)
        )
        active = tuple(sorted(active, key=lambda item: (item.end_date, item.name.casefold())))
        if values.season_id is None and len(active) == 1:
            values = DuelRecordValues(**{**values.__dict__, "season_id": active[0].season_id})
            reasons.append(f"開催中シーズン「{active[0].name}」を候補にしました")
        return DuelInputSuggestion(values, tuple(reasons), decks, tags, active)

    def list_incomplete(self) -> tuple[IncompleteDuel, ...]:
        repository = DuelRecordRepository(self.database_path)
        records = repository.list(limit=1000)
        by_recording = {item.recording_id: item for item in records if item.recording_id}
        items: list[IncompleteDuel] = []
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT recording_id, COALESCE(started_at, created_at) AS occurred_at "
                "FROM recordings WHERE state = 'completed' ORDER BY occurred_at, recording_id"
            ).fetchall()
        for row in rows:
            record = by_recording.get(row["recording_id"])
            if record is None or record.values.status != "confirmed":
                items.append(
                    IncompleteDuel(
                        str(row["recording_id"]),
                        "missing" if record is None else "draft",
                        datetime.fromisoformat(row["occurred_at"]),
                        record,
                    )
                )
        items.extend(
            IncompleteDuel(item.duel_id, "draft", item.occurred_at, item)
            for item in records
            if item.entry_origin in {"manual", "import"}
            and item.values.status != "confirmed"
        )
        return tuple(sorted(items, key=lambda item: (item.occurred_at, item.identifier)))

    def bulk_update(self, duel_ids: tuple[str, ...], update: BulkDuelUpdate) -> tuple[DuelRecord, ...]:
        identifiers = tuple(dict.fromkeys(item.strip() for item in duel_ids if item.strip()))
        if not identifiers:
            raise DuelWorkflowError("一括更新する戦績を選択してください")
        repository = DuelRecordRepository(self.database_path)
        current = []
        for identifier in identifiers:
            record = repository.get(identifier)
            if record is None or record.duel_id != identifier:
                raise DuelWorkflowError(f"対戦記録が見つかりません: {identifier}")
            current.append(record)
        snapshots = [(item, update.apply(item.values)) for item in current]
        saved: list[DuelRecord] = []
        try:
            for record, values in snapshots:
                saved.append(
                    repository.update(
                        record.duel_id,
                        values,
                        expected_revision=record.revision,
                        source="user",
                    )
                )
        except Exception as exc:
            for original, _values in reversed(snapshots[: len(saved)]):
                changed = repository.get(original.duel_id)
                if changed is not None:
                    repository.update(
                        original.duel_id,
                        original.values,
                        expected_revision=changed.revision,
                        source="system",
                    )
            raise DuelWorkflowError(f"一括更新をロールバックしました: {exc}") from exc
        return tuple(saved)

    def list_filters(self) -> tuple[SavedDuelFilter, ...]:
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT * FROM saved_duel_filters ORDER BY updated_at DESC, name"
            ).fetchall()
        return tuple(self._filter(row) for row in rows)

    def save_filter(
        self,
        name: str,
        criteria: DuelFilterCriteria,
        *,
        filter_id: str | None = None,
    ) -> SavedDuelFilter:
        display = unicodedata.normalize("NFC", name.strip())
        if not display or len(display) > 80:
            raise DuelWorkflowError("フィルター名は1から80文字で入力してください")
        selected = criteria.normalized()
        identifier = filter_id or uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(asdict(selected), ensure_ascii=False, sort_keys=True)
        try:
            with closing(connect_history_database(self.database_path)) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO saved_duel_filters(
                        filter_id, name, normalized_name, criteria_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(filter_id) DO UPDATE SET
                        name = excluded.name,
                        normalized_name = excluded.normalized_name,
                        criteria_json = excluded.criteria_json,
                        updated_at = excluded.updated_at
                    """,
                    (identifier, display, display.casefold(), payload, now, now),
                )
                row = connection.execute(
                    "SELECT * FROM saved_duel_filters WHERE filter_id = ?", (identifier,)
                ).fetchone()
            assert row is not None
            return self._filter(row)
        except sqlite3.IntegrityError as exc:
            raise DuelWorkflowError("同じフィルター名が既にあります") from exc

    def delete_filter(self, filter_id: str) -> SavedDuelFilter:
        with closing(connect_history_database(self.database_path)) as connection, connection:
            row = connection.execute(
                "SELECT * FROM saved_duel_filters WHERE filter_id = ?", (filter_id,)
            ).fetchone()
            if row is None:
                raise DuelWorkflowError("保存済みフィルターが見つかりません")
            connection.execute("DELETE FROM saved_duel_filters WHERE filter_id = ?", (filter_id,))
        return self._filter(row)

    def _usage_rank(self, kind: str) -> dict[int, tuple[int, str]]:
        if kind == "deck":
            sql = """
                SELECT catalog.entry_id, COUNT(duel.duel_id) AS uses,
                       COALESCE(MAX(duel.occurred_at), '') AS latest
                FROM duel_catalog_entries AS catalog
                LEFT JOIN duel_records AS duel ON duel.own_deck_id = catalog.entry_id
                WHERE catalog.kind = 'deck' GROUP BY catalog.entry_id
            """
        else:
            sql = """
                SELECT catalog.entry_id, COUNT(link.duel_id) AS uses,
                       COALESCE(MAX(duel.occurred_at), '') AS latest
                FROM duel_catalog_entries AS catalog
                LEFT JOIN duel_record_tag_links AS link ON link.tag_entry_id = catalog.entry_id
                LEFT JOIN duel_records AS duel ON duel.duel_id = link.duel_id
                WHERE catalog.kind = 'tag' GROUP BY catalog.entry_id
            """
        with closing(connect_history_database(self.database_path)) as connection:
            return {
                int(row["entry_id"]): (int(row["uses"]), str(row["latest"]))
                for row in connection.execute(sql)
            }

    @staticmethod
    def _rank_key(item: DuelCatalogEntry, ranks: dict[int, tuple[int, str]]) -> tuple[object, ...]:
        uses, latest = ranks.get(item.entry_id, (0, ""))
        return (-uses, "".join(chr(0x10FFFF - ord(char)) for char in latest), item.name.casefold())

    @staticmethod
    def _filter(row: sqlite3.Row) -> SavedDuelFilter:
        try:
            raw = json.loads(row["criteria_json"])
            raw["tag_entry_ids"] = tuple(raw.get("tag_entry_ids", ()))
            criteria = DuelFilterCriteria(**raw).normalized()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DuelWorkflowError("保存済みフィルターが破損しています") from exc
        return SavedDuelFilter(
            str(row["filter_id"]),
            str(row["name"]),
            criteria,
            datetime.fromisoformat(row["created_at"]),
            datetime.fromisoformat(row["updated_at"]),
        )


def _key(value: str) -> str:
    return unicodedata.normalize("NFC", value.strip()).casefold()
