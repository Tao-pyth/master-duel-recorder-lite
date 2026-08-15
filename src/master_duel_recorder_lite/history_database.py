from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import uuid


CURRENT_SCHEMA_VERSION = 14
HISTORY_DATABASE_NAME = "history.sqlite3"


class HistoryDatabaseError(RuntimeError):
    """録画履歴DBを安全に初期化または移行できない場合のエラーです。"""


@dataclass(frozen=True)
class HistoryDatabaseInfo:
    path: Path
    version: int
    backup_path: Path | None


Migration = Callable[[sqlite3.Connection], None]
MigrationBackupFactory = Callable[[int], Path]


@dataclass(frozen=True)
class _RetiredArtifact:
    original: Path
    staged: Path


def _migrate_to_v1(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE recordings (
            recording_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (
                state IN ('starting', 'recording', 'completed', 'failed')
            ),
            source TEXT NOT NULL CHECK (length(trim(source)) > 0),
            detection_reason TEXT,
            output_path TEXT NOT NULL UNIQUE CHECK (length(trim(output_path)) > 0),
            container TEXT NOT NULL CHECK (container IN ('mkv', 'mp4')),
            created_at TEXT NOT NULL,
            started_at TEXT,
            ended_at TEXT,
            duration_seconds REAL CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
            size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
            returncode INTEGER,
            error TEXT,
            diagnostics_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX recordings_started_at_idx ON recordings(started_at DESC, recording_id DESC)"
    )
    connection.execute(
        "CREATE INDEX recordings_state_started_at_idx "
        "ON recordings(state, started_at DESC, recording_id DESC)"
    )


def _migrate_to_v2(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE recordings ADD COLUMN failure_code TEXT")
    connection.execute("ALTER TABLE recordings ADD COLUMN recovery_policy TEXT")
    connection.execute(
        "ALTER TABLE recordings ADD COLUMN recovery_state TEXT NOT NULL DEFAULT 'not_required' "
        "CHECK (recovery_state IN "
        "('not_required', 'pending', 'inspecting', 'repairable', 'repaired', 'ignored', 'unrecoverable'))"
    )
    connection.execute(
        "ALTER TABLE recordings ADD COLUMN recovery_attempts INTEGER NOT NULL DEFAULT 0 "
        "CHECK (recovery_attempts >= 0)"
    )
    connection.execute("ALTER TABLE recordings ADD COLUMN recovery_message TEXT")
    connection.execute("ALTER TABLE recordings ADD COLUMN recovery_diagnostic TEXT")
    connection.execute(
        """
        CREATE TABLE recovery_artifacts (
            artifact_id TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            kind TEXT NOT NULL CHECK (kind IN ('recovered', 'partial')),
            status TEXT NOT NULL CHECK (status IN ('created', 'valid', 'failed')),
            output_path TEXT NOT NULL UNIQUE CHECK (length(trim(output_path)) > 0),
            size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
            diagnostic TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX recordings_recovery_state_idx "
        "ON recordings(recovery_state, updated_at DESC, recording_id DESC)"
    )
    connection.execute(
        "CREATE INDEX recovery_artifacts_recording_id_idx "
        "ON recovery_artifacts(recording_id, created_at DESC)"
    )
    connection.execute(
        """
        UPDATE recordings
        SET failure_code = 'legacy_failure',
            recovery_policy = 'manual_review',
            recovery_state = 'pending',
            recovery_message = '以前のバージョンで失敗した録画です。内容を確認してください。',
            recovery_diagnostic = COALESCE(error, 'legacy failed recording')
        WHERE state = 'failed'
        """
    )


def _migrate_to_v3(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE duel_records (
            recording_id TEXT PRIMARY KEY REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            status TEXT NOT NULL CHECK (status IN ('draft', 'confirmed')),
            result TEXT NOT NULL CHECK (result IN ('win', 'loss', 'draw', 'unknown')),
            play_order TEXT NOT NULL CHECK (play_order IN ('first', 'second', 'unknown')),
            own_deck TEXT NOT NULL DEFAULT '',
            opponent_deck TEXT NOT NULL DEFAULT '',
            duel_type TEXT NOT NULL CHECK (duel_type IN ('ranked', 'event', 'room', 'solo', 'other')),
            notes TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL CHECK (revision >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE duel_record_tags (
            recording_id TEXT NOT NULL REFERENCES duel_records(recording_id) ON DELETE RESTRICT,
            tag TEXT NOT NULL,
            normalized_tag TEXT NOT NULL,
            PRIMARY KEY (recording_id, normalized_tag)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE duel_record_changes (
            change_id INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_id TEXT NOT NULL REFERENCES duel_records(recording_id) ON DELETE RESTRICT,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            source TEXT NOT NULL CHECK (source IN ('user', 'system', 'detected')),
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            UNIQUE (recording_id, revision)
        )
        """
    )
    connection.execute(
        "CREATE INDEX duel_records_updated_at_idx ON duel_records(updated_at DESC, recording_id DESC)"
    )
    connection.execute(
        "CREATE INDEX duel_record_changes_recording_idx "
        "ON duel_record_changes(recording_id, revision DESC)"
    )


def _migrate_to_v4(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE duel_events (
            event_id TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            elapsed_ms INTEGER NOT NULL CHECK (elapsed_ms >= 0),
            event_type TEXT NOT NULL CHECK (
                event_type IN ('duel_start', 'turn_change', 'duel_result', 'marker')
            ),
            actor TEXT CHECK (actor IS NULL OR actor IN ('self', 'opponent', 'unknown')),
            outcome TEXT CHECK (outcome IS NULL OR outcome IN ('win', 'loss', 'draw', 'unknown')),
            label TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL CHECK (source IN ('manual', 'detected', 'system')),
            confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
            status TEXT NOT NULL CHECK (status IN ('candidate', 'confirmed', 'rejected')),
            detector_id TEXT,
            detector_version TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX duel_events_timeline_idx "
        "ON duel_events(recording_id, elapsed_ms, event_id)"
    )
    connection.execute(
        "CREATE INDEX duel_events_status_idx "
        "ON duel_events(recording_id, status, event_type, elapsed_ms)"
    )


def _migrate_to_v5(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE duel_catalog_entries (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK (kind IN ('deck', 'tag')),
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            normalized_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (kind, normalized_name)
        )
        """
    )
    connection.execute(
        "CREATE INDEX duel_catalog_kind_name_idx "
        "ON duel_catalog_entries(kind, normalized_name, entry_id)"
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO duel_catalog_entries (
            kind, name, normalized_name, created_at, updated_at
        )
        SELECT 'deck', name, lower(name), created_at, updated_at
        FROM (
            SELECT trim(own_deck) AS name, created_at, updated_at
            FROM duel_records WHERE length(trim(own_deck)) > 0
            UNION
            SELECT trim(opponent_deck) AS name, created_at, updated_at
            FROM duel_records WHERE length(trim(opponent_deck)) > 0
        )
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO duel_catalog_entries (
            kind, name, normalized_name, created_at, updated_at
        )
        SELECT 'tag', trim(tags.tag), lower(trim(tags.tag)), records.created_at, records.updated_at
        FROM duel_record_tags AS tags
        JOIN duel_records AS records ON records.recording_id = tags.recording_id
        WHERE length(trim(tags.tag)) > 0
        """
    )
    connection.execute(
        """
        CREATE TABLE duel_editor_preferences (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            duel_type TEXT NOT NULL CHECK (
                duel_type IN ('ranked', 'event', 'room', 'solo', 'other')
            ),
            own_deck TEXT NOT NULL DEFAULT '',
            opponent_deck TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )
        """
    )


def _migrate_to_v6(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE recordings ADD COLUMN audio_input TEXT")
    connection.execute(
        "ALTER TABLE recordings ADD COLUMN audio_state TEXT NOT NULL DEFAULT 'disabled' "
        "CHECK (audio_state IN ('disabled', 'configured', 'recorded', 'warning', 'failed'))"
    )
    connection.execute("ALTER TABLE recordings ADD COLUMN audio_warning TEXT")
    connection.execute(
        "ALTER TABLE duel_catalog_entries ADD COLUMN description TEXT NOT NULL DEFAULT ''"
    )
    connection.execute("ALTER TABLE duel_catalog_entries ADD COLUMN color TEXT")
    connection.execute(
        "ALTER TABLE duel_catalog_entries ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0 "
        "CHECK (is_archived IN (0, 1))"
    )
    connection.execute(
        """
        CREATE TABLE duel_record_tag_links (
            recording_id TEXT NOT NULL REFERENCES duel_records(recording_id) ON DELETE RESTRICT,
            tag_entry_id INTEGER NOT NULL REFERENCES duel_catalog_entries(entry_id) ON DELETE RESTRICT,
            PRIMARY KEY (recording_id, tag_entry_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX duel_record_tag_links_tag_idx "
        "ON duel_record_tag_links(tag_entry_id, recording_id)"
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO duel_record_tag_links(recording_id, tag_entry_id)
        SELECT tags.recording_id, catalog.entry_id
        FROM duel_record_tags AS tags
        JOIN duel_catalog_entries AS catalog
          ON catalog.kind = 'tag'
         AND catalog.normalized_name = tags.normalized_tag
        """
    )


def _migrate_to_v7(connection: sqlite3.Connection) -> None:
    """Remove the discontinued recovery subsystem while preserving failure diagnostics."""
    connection.execute("DROP INDEX IF EXISTS recovery_artifacts_recording_id_idx")
    connection.execute("DROP INDEX IF EXISTS recordings_recovery_state_idx")
    connection.execute("DROP TABLE recovery_artifacts")
    for column in (
        "recovery_policy",
        "recovery_state",
        "recovery_attempts",
        "recovery_message",
        "recovery_diagnostic",
    ):
        connection.execute(f"ALTER TABLE recordings DROP COLUMN {column}")


def _migrate_to_v8(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE duel_catalog_entries ADD COLUMN opponent_only INTEGER NOT NULL DEFAULT 0 "
        "CHECK (opponent_only IN (0, 1))"
    )
    connection.execute(
        "ALTER TABLE duel_catalog_entries ADD COLUMN hidden_from_history_statistics "
        "INTEGER NOT NULL DEFAULT 0 CHECK (hidden_from_history_statistics IN (0, 1))"
    )
    connection.execute(
        "UPDATE duel_catalog_entries SET color = '#4F6F8F' "
        "WHERE kind = 'deck' AND color IS NULL"
    )
    connection.execute(
        """
        CREATE TABLE seasons (
            season_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            normalized_name TEXT NOT NULL UNIQUE,
            season_type TEXT NOT NULL CHECK (season_type IN ('ranked', 'event', 'custom')),
            duel_type TEXT NOT NULL CHECK (
                duel_type IN ('ranked', 'event', 'room', 'solo', 'other')
            ),
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            report_notes TEXT NOT NULL DEFAULT '',
            is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (start_date <= end_date)
        )
        """
    )
    connection.execute(
        "CREATE INDEX seasons_dates_idx ON seasons(start_date, end_date, season_id)"
    )
    connection.execute(
        "ALTER TABLE duel_records ADD COLUMN season_id INTEGER REFERENCES seasons(season_id)"
    )
    connection.execute(
        "ALTER TABLE duel_records ADD COLUMN own_deck_id INTEGER "
        "REFERENCES duel_catalog_entries(entry_id)"
    )
    connection.execute(
        "ALTER TABLE duel_records ADD COLUMN opponent_deck_id INTEGER "
        "REFERENCES duel_catalog_entries(entry_id)"
    )
    connection.execute(
        """
        UPDATE duel_records
        SET own_deck_id = (
            SELECT entry_id FROM duel_catalog_entries
            WHERE kind = 'deck' AND normalized_name = lower(trim(duel_records.own_deck))
        )
        WHERE length(trim(own_deck)) > 0
        """
    )
    connection.execute(
        """
        UPDATE duel_records
        SET opponent_deck_id = (
            SELECT entry_id FROM duel_catalog_entries
            WHERE kind = 'deck' AND normalized_name = lower(trim(duel_records.opponent_deck))
        )
        WHERE length(trim(opponent_deck)) > 0
        """
    )
    connection.execute(
        "CREATE INDEX duel_records_season_idx ON duel_records(season_id)"
    )
    connection.execute(
        "CREATE INDEX duel_records_own_deck_idx ON duel_records(own_deck_id)"
    )
    connection.execute(
        "CREATE INDEX duel_records_opponent_deck_idx ON duel_records(opponent_deck_id)"
    )


def _migrate_to_v9(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE duel_records ADD COLUMN coin_face TEXT NOT NULL DEFAULT 'unknown' "
        "CHECK (coin_face IN ('heads', 'tails', 'unknown'))"
    )
    connection.execute(
        "CREATE INDEX duel_records_coin_face_idx ON duel_records(coin_face)"
    )


def _migrate_to_v10(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE duel_records_v10 (
            duel_id TEXT PRIMARY KEY CHECK (length(trim(duel_id)) > 0),
            recording_id TEXT UNIQUE REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            entry_origin TEXT NOT NULL CHECK (entry_origin IN ('recording', 'manual')),
            occurred_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('draft', 'confirmed')),
            result TEXT NOT NULL CHECK (result IN ('win', 'loss', 'draw', 'unknown')),
            play_order TEXT NOT NULL CHECK (play_order IN ('first', 'second', 'unknown')),
            coin_face TEXT NOT NULL CHECK (coin_face IN ('heads', 'tails', 'unknown')),
            own_deck TEXT NOT NULL DEFAULT '',
            opponent_deck TEXT NOT NULL DEFAULT '',
            duel_type TEXT NOT NULL CHECK (duel_type IN ('ranked', 'event', 'room', 'solo', 'other')),
            notes TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL CHECK (revision >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            season_id INTEGER REFERENCES seasons(season_id),
            own_deck_id INTEGER REFERENCES duel_catalog_entries(entry_id),
            opponent_deck_id INTEGER REFERENCES duel_catalog_entries(entry_id),
            CHECK (
                (entry_origin = 'recording' AND recording_id IS NOT NULL)
                OR (entry_origin = 'manual' AND recording_id IS NULL)
            )
        )
        """
    )
    connection.execute(
        """
        INSERT INTO duel_records_v10 (
            duel_id, recording_id, entry_origin, occurred_at, status, result,
            play_order, coin_face, own_deck, opponent_deck,
            duel_type, notes, revision, created_at, updated_at, season_id,
            own_deck_id, opponent_deck_id
        )
        SELECT
            duel.recording_id, duel.recording_id, 'recording',
            COALESCE(recording.started_at, recording.created_at), duel.status,
            duel.result, duel.play_order, duel.coin_face,
            duel.own_deck, duel.opponent_deck, duel.duel_type, duel.notes,
            duel.revision, duel.created_at, duel.updated_at, duel.season_id,
            duel.own_deck_id, duel.opponent_deck_id
        FROM duel_records AS duel
        JOIN recordings AS recording ON recording.recording_id = duel.recording_id
        """
    )
    connection.execute(
        """
        CREATE TABLE duel_record_tags_v10 (
            duel_id TEXT NOT NULL REFERENCES duel_records_v10(duel_id) ON DELETE RESTRICT,
            tag TEXT NOT NULL,
            normalized_tag TEXT NOT NULL,
            PRIMARY KEY (duel_id, normalized_tag)
        )
        """
    )
    connection.execute(
        "INSERT INTO duel_record_tags_v10(duel_id, tag, normalized_tag) "
        "SELECT recording_id, tag, normalized_tag FROM duel_record_tags"
    )
    connection.execute(
        """
        CREATE TABLE duel_record_changes_v10 (
            change_id INTEGER PRIMARY KEY AUTOINCREMENT,
            duel_id TEXT NOT NULL REFERENCES duel_records_v10(duel_id) ON DELETE RESTRICT,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            source TEXT NOT NULL CHECK (source IN ('user', 'system', 'detected')),
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            UNIQUE (duel_id, revision)
        )
        """
    )
    connection.execute(
        "INSERT INTO duel_record_changes_v10(change_id, duel_id, revision, source, before_json, after_json, changed_at) "
        "SELECT change_id, recording_id, revision, source, before_json, after_json, changed_at "
        "FROM duel_record_changes"
    )
    connection.execute(
        """
        CREATE TABLE duel_record_tag_links_v10 (
            duel_id TEXT NOT NULL REFERENCES duel_records_v10(duel_id) ON DELETE RESTRICT,
            tag_entry_id INTEGER NOT NULL REFERENCES duel_catalog_entries(entry_id) ON DELETE RESTRICT,
            PRIMARY KEY (duel_id, tag_entry_id)
        )
        """
    )
    connection.execute(
        "INSERT INTO duel_record_tag_links_v10(duel_id, tag_entry_id) "
        "SELECT recording_id, tag_entry_id FROM duel_record_tag_links"
    )
    connection.execute("DROP TABLE duel_record_tag_links")
    connection.execute("DROP TABLE duel_record_tags")
    connection.execute("DROP TABLE duel_record_changes")
    connection.execute("DROP TABLE duel_records")
    connection.execute("ALTER TABLE duel_records_v10 RENAME TO duel_records")
    connection.execute("ALTER TABLE duel_record_tags_v10 RENAME TO duel_record_tags")
    connection.execute("ALTER TABLE duel_record_changes_v10 RENAME TO duel_record_changes")
    connection.execute("ALTER TABLE duel_record_tag_links_v10 RENAME TO duel_record_tag_links")
    connection.execute(
        "CREATE INDEX duel_records_updated_at_idx ON duel_records(updated_at DESC, duel_id DESC)"
    )
    connection.execute("CREATE INDEX duel_records_recording_idx ON duel_records(recording_id)")
    connection.execute("CREATE INDEX duel_records_occurred_idx ON duel_records(occurred_at DESC, duel_id DESC)")
    connection.execute("CREATE INDEX duel_records_season_idx ON duel_records(season_id)")
    connection.execute("CREATE INDEX duel_records_own_deck_idx ON duel_records(own_deck_id)")
    connection.execute("CREATE INDEX duel_records_opponent_deck_idx ON duel_records(opponent_deck_id)")
    connection.execute("CREATE INDEX duel_records_coin_face_idx ON duel_records(coin_face)")
    connection.execute(
        "CREATE INDEX duel_record_changes_duel_idx ON duel_record_changes(duel_id, revision DESC)"
    )
    connection.execute(
        "CREATE INDEX duel_record_tag_links_tag_idx ON duel_record_tag_links(tag_entry_id, duel_id)"
    )


def _migrate_to_v11(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE saved_duel_filters (
            filter_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            criteria_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX saved_duel_filters_updated_idx "
        "ON saved_duel_filters(updated_at DESC, filter_id)"
    )


def _migrate_to_v12(connection: sqlite3.Connection) -> None:
    for column in (
        "report_goal",
        "report_highlights",
        "report_challenges",
        "report_next_plan",
    ):
        connection.execute(
            f"ALTER TABLE seasons ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
        )
    connection.execute(
        "ALTER TABLE seasons ADD COLUMN report_revision INTEGER NOT NULL DEFAULT 0 "
        "CHECK (report_revision >= 0)"
    )


def _migrate_to_v13(connection: sqlite3.Connection) -> None:
    legacy_column = "coin_toss_outcome"
    columns = {row[1] for row in connection.execute("PRAGMA table_info(duel_records)")}
    if legacy_column in columns:
        connection.execute("DROP INDEX IF EXISTS duel_records_coin_toss_outcome_idx")
        connection.execute(f"ALTER TABLE duel_records DROP COLUMN {legacy_column}")

    for row in connection.execute(
        "SELECT change_id, before_json, after_json FROM duel_record_changes"
    ).fetchall():
        documents: list[str] = []
        for raw in (row[1], row[2]):
            document = json.loads(raw)
            if isinstance(document, dict):
                document.pop(legacy_column, None)
            documents.append(
                json.dumps(
                    document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        connection.execute(
            "UPDATE duel_record_changes SET before_json = ?, after_json = ? WHERE change_id = ?",
            (documents[0], documents[1], row[0]),
        )

    for row in connection.execute(
        "SELECT filter_id, criteria_json FROM saved_duel_filters"
    ).fetchall():
        criteria = json.loads(row[1])
        if isinstance(criteria, dict) and legacy_column in criteria:
            criteria.pop(legacy_column)
            connection.execute(
                "UPDATE saved_duel_filters SET criteria_json = ? WHERE filter_id = ?",
                (
                    json.dumps(
                        criteria,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    row[0],
                ),
            )


def _migrate_to_v14(connection: sqlite3.Connection) -> None:
    """Add CSV-import provenance without weakening recording relationships."""
    connection.execute(
        """
        CREATE TABLE duel_records_v14 (
            duel_id TEXT PRIMARY KEY CHECK (length(trim(duel_id)) > 0),
            recording_id TEXT UNIQUE REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            entry_origin TEXT NOT NULL CHECK (entry_origin IN ('recording', 'manual', 'import')),
            occurred_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('draft', 'confirmed')),
            result TEXT NOT NULL CHECK (result IN ('win', 'loss', 'draw', 'unknown')),
            play_order TEXT NOT NULL CHECK (play_order IN ('first', 'second', 'unknown')),
            coin_face TEXT NOT NULL CHECK (coin_face IN ('heads', 'tails', 'unknown')),
            own_deck TEXT NOT NULL DEFAULT '',
            opponent_deck TEXT NOT NULL DEFAULT '',
            duel_type TEXT NOT NULL CHECK (duel_type IN ('ranked', 'event', 'room', 'solo', 'other')),
            notes TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL CHECK (revision >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            season_id INTEGER REFERENCES seasons(season_id),
            own_deck_id INTEGER REFERENCES duel_catalog_entries(entry_id),
            opponent_deck_id INTEGER REFERENCES duel_catalog_entries(entry_id),
            CHECK (
                (entry_origin = 'recording' AND recording_id IS NOT NULL)
                OR (entry_origin IN ('manual', 'import') AND recording_id IS NULL)
            )
        )
        """
    )
    connection.execute(
        """
        INSERT INTO duel_records_v14 (
            duel_id, recording_id, entry_origin, occurred_at, status, result,
            play_order, coin_face, own_deck, opponent_deck, duel_type, notes,
            revision, created_at, updated_at, season_id, own_deck_id, opponent_deck_id
        )
        SELECT duel_id, recording_id, entry_origin, occurred_at, status, result,
               play_order, coin_face, own_deck, opponent_deck, duel_type, notes,
               revision, created_at, updated_at, season_id, own_deck_id, opponent_deck_id
        FROM duel_records
        """
    )
    connection.execute(
        """
        CREATE TABLE duel_record_tags_v14 (
            duel_id TEXT NOT NULL REFERENCES duel_records_v14(duel_id) ON DELETE RESTRICT,
            tag TEXT NOT NULL,
            normalized_tag TEXT NOT NULL,
            PRIMARY KEY (duel_id, normalized_tag)
        )
        """
    )
    connection.execute(
        "INSERT INTO duel_record_tags_v14 SELECT duel_id, tag, normalized_tag FROM duel_record_tags"
    )
    connection.execute(
        """
        CREATE TABLE duel_record_changes_v14 (
            change_id INTEGER PRIMARY KEY AUTOINCREMENT,
            duel_id TEXT NOT NULL REFERENCES duel_records_v14(duel_id) ON DELETE RESTRICT,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            source TEXT NOT NULL CHECK (source IN ('user', 'system', 'detected', 'import')),
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            UNIQUE (duel_id, revision)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO duel_record_changes_v14 (
            change_id, duel_id, revision, source, before_json, after_json, changed_at
        )
        SELECT change_id, duel_id, revision, source, before_json, after_json, changed_at
        FROM duel_record_changes
        """
    )
    connection.execute(
        """
        CREATE TABLE duel_record_tag_links_v14 (
            duel_id TEXT NOT NULL REFERENCES duel_records_v14(duel_id) ON DELETE RESTRICT,
            tag_entry_id INTEGER NOT NULL REFERENCES duel_catalog_entries(entry_id) ON DELETE RESTRICT,
            PRIMARY KEY (duel_id, tag_entry_id)
        )
        """
    )
    connection.execute(
        "INSERT INTO duel_record_tag_links_v14 SELECT duel_id, tag_entry_id FROM duel_record_tag_links"
    )
    connection.execute("DROP TABLE duel_record_tag_links")
    connection.execute("DROP TABLE duel_record_tags")
    connection.execute("DROP TABLE duel_record_changes")
    connection.execute("DROP TABLE duel_records")
    connection.execute("ALTER TABLE duel_records_v14 RENAME TO duel_records")
    connection.execute("ALTER TABLE duel_record_tags_v14 RENAME TO duel_record_tags")
    connection.execute("ALTER TABLE duel_record_changes_v14 RENAME TO duel_record_changes")
    connection.execute("ALTER TABLE duel_record_tag_links_v14 RENAME TO duel_record_tag_links")
    connection.execute(
        "CREATE INDEX duel_records_updated_at_idx ON duel_records(updated_at DESC, duel_id DESC)"
    )
    connection.execute("CREATE INDEX duel_records_recording_idx ON duel_records(recording_id)")
    connection.execute(
        "CREATE INDEX duel_records_occurred_idx ON duel_records(occurred_at DESC, duel_id DESC)"
    )
    connection.execute("CREATE INDEX duel_records_season_idx ON duel_records(season_id)")
    connection.execute("CREATE INDEX duel_records_own_deck_idx ON duel_records(own_deck_id)")
    connection.execute(
        "CREATE INDEX duel_records_opponent_deck_idx ON duel_records(opponent_deck_id)"
    )
    connection.execute("CREATE INDEX duel_records_coin_face_idx ON duel_records(coin_face)")
    connection.execute(
        "CREATE INDEX duel_record_changes_duel_idx ON duel_record_changes(duel_id, revision DESC)"
    )
    connection.execute(
        "CREATE INDEX duel_record_tag_links_tag_idx ON duel_record_tag_links(tag_entry_id, duel_id)"
    )


_MIGRATIONS: dict[int, Migration] = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
    3: _migrate_to_v3,
    4: _migrate_to_v4,
    5: _migrate_to_v5,
    6: _migrate_to_v6,
    7: _migrate_to_v7,
    8: _migrate_to_v8,
    9: _migrate_to_v9,
    10: _migrate_to_v10,
    11: _migrate_to_v11,
    12: _migrate_to_v12,
    13: _migrate_to_v13,
    14: _migrate_to_v14,
}


def initialize_history_database(
    path: Path,
    *,
    recordings_root: Path | None = None,
    migrations: Mapping[int, Migration] | None = None,
    migration_backup_factory: MigrationBackupFactory | None = None,
) -> HistoryDatabaseInfo:
    database_path = path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    existed = database_path.exists() and database_path.stat().st_size > 0
    selected_migrations = dict(_MIGRATIONS if migrations is None else migrations)
    expected_versions = set(range(1, CURRENT_SCHEMA_VERSION + 1))
    if set(selected_migrations) != expected_versions:
        raise HistoryDatabaseError(
            "マイグレーション定義が現在のスキーマ版と一致しません"
        )

    connection: sqlite3.Connection | None = None
    backup_path: Path | None = None
    retired_artifacts: tuple[_RetiredArtifact, ...] = ()
    try:
        connection = sqlite3.connect(database_path, timeout=10.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        current_version = _read_schema_version(connection)
        if current_version > CURRENT_SCHEMA_VERSION:
            raise HistoryDatabaseError(
                f"履歴DBのスキーマ版{current_version}はこのアプリでは未対応です"
            )
        if current_version == CURRENT_SCHEMA_VERSION:
            _validate_current_schema(connection)
            return HistoryDatabaseInfo(database_path, current_version, None)

        if existed:
            backup_path = (
                migration_backup_factory(current_version)
                if migration_backup_factory is not None
                else _backup_database(connection, database_path, current_version)
            )

        if current_version >= 2 and current_version < 7 and recordings_root is not None:
            retired_artifacts = _stage_recovery_artifacts(
                connection,
                recordings_root.expanduser().resolve(),
            )

        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                version INTEGER NOT NULL CHECK (version >= 0)
            )
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(singleton, version) VALUES (1, ?)",
            (current_version,),
        )
        for version in range(current_version + 1, CURRENT_SCHEMA_VERSION + 1):
            selected_migrations[version](connection)
            connection.execute(
                "UPDATE schema_version SET version = ? WHERE singleton = 1",
                (version,),
            )
            connection.execute(f"PRAGMA user_version = {version}")
        _validate_current_schema(connection)
        connection.commit()
        _discard_staged_artifacts(retired_artifacts)
        return HistoryDatabaseInfo(database_path, CURRENT_SCHEMA_VERSION, backup_path)
    except HistoryDatabaseError:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        _restore_staged_artifacts(retired_artifacts)
        raise
    except (OSError, sqlite3.Error, KeyError, RuntimeError, ValueError) as exc:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        _restore_staged_artifacts(retired_artifacts)
        raise HistoryDatabaseError(
            f"録画履歴DBを初期化できません: {database_path}: {exc}"
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def _stage_recovery_artifacts(
    connection: sqlite3.Connection,
    recordings_root: Path,
) -> tuple[_RetiredArtifact, ...]:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'recovery_artifacts'"
    ).fetchone()
    if table is None:
        return ()
    recordings_root.mkdir(parents=True, exist_ok=True)
    root = recordings_root.resolve()
    normal_paths = {
        str(row[0])
        for row in connection.execute("SELECT output_path FROM recordings").fetchall()
    }
    stage_root = root / ".recovery-retirement" / uuid.uuid4().hex
    moved: list[_RetiredArtifact] = []
    try:
        for index, row in enumerate(
            connection.execute("SELECT output_path FROM recovery_artifacts").fetchall()
        ):
            stored = str(row[0])
            if stored in normal_paths:
                continue
            relative = Path(stored)
            if relative.is_absolute() or ".." in relative.parts:
                raise HistoryDatabaseError(
                    f"復旧成果物の保存先が録画保存先外です: {stored}"
                )
            original = (root / relative).resolve()
            if original != root and root not in original.parents:
                raise HistoryDatabaseError(
                    f"復旧成果物の保存先が録画保存先外です: {stored}"
                )
            if not original.exists():
                continue
            if not original.is_file():
                raise HistoryDatabaseError(f"復旧成果物がファイルではありません: {stored}")
            stage_root.mkdir(parents=True, exist_ok=True)
            staged = stage_root / f"{index:06d}-{original.name}"
            shutil.move(str(original), str(staged))
            moved.append(_RetiredArtifact(original, staged))
    except Exception:
        _restore_staged_artifacts(tuple(moved))
        raise
    return tuple(moved)


def _restore_staged_artifacts(artifacts: tuple[_RetiredArtifact, ...]) -> None:
    for artifact in reversed(artifacts):
        if not artifact.staged.exists():
            continue
        artifact.original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(artifact.staged), str(artifact.original))
    _remove_empty_stage_directories(artifacts)


def _discard_staged_artifacts(artifacts: tuple[_RetiredArtifact, ...]) -> None:
    for artifact in artifacts:
        artifact.staged.unlink(missing_ok=True)
    _remove_empty_stage_directories(artifacts)


def _remove_empty_stage_directories(artifacts: tuple[_RetiredArtifact, ...]) -> None:
    for artifact in artifacts:
        session = artifact.staged.parent
        retirement = session.parent
        if session.exists() and not any(session.iterdir()):
            session.rmdir()
        if retirement.exists() and not any(retirement.iterdir()):
            retirement.rmdir()


def connect_history_database(path: Path) -> sqlite3.Connection:
    info = initialize_history_database(path)
    try:
        connection = sqlite3.connect(info.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection
    except sqlite3.Error as exc:
        raise HistoryDatabaseError(
            f"録画履歴DBへ接続できません: {info.path}: {exc}"
        ) from exc


def _read_schema_version(connection: sqlite3.Connection) -> int:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if table is None:
        return 0
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(schema_version)")
    }
    if not {"singleton", "version"}.issubset(columns):
        raise HistoryDatabaseError("schema_versionテーブルの形式が不正です")
    row = connection.execute(
        "SELECT version FROM schema_version WHERE singleton = 1"
    ).fetchone()
    if (
        row is None
        or isinstance(row[0], bool)
        or not isinstance(row[0], int)
        or row[0] < 0
    ):
        raise HistoryDatabaseError("schema_versionの値が不正です")
    return row[0]


def _validate_current_schema(connection: sqlite3.Connection) -> None:
    required_columns = {
        "recording_id",
        "state",
        "source",
        "output_path",
        "container",
        "created_at",
        "diagnostics_json",
        "updated_at",
        "failure_code",
        "audio_input",
        "audio_state",
        "audio_warning",
    }
    columns = {row[1] for row in connection.execute("PRAGMA table_info(recordings)")}
    if not required_columns.issubset(columns):
        raise HistoryDatabaseError("録画履歴DBに必須のrecordingsスキーマがありません")
    duel_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(duel_records)")
    }
    if not {
        "duel_id",
        "recording_id",
        "entry_origin",
        "occurred_at",
        "status",
        "result",
        "revision",
        "updated_at",
        "season_id",
        "own_deck_id",
        "opponent_deck_id",
        "coin_face",
    }.issubset(duel_columns):
        raise HistoryDatabaseError("録画履歴DBに必須のduel_recordsスキーマがありません")
    if "coin_toss_outcome" in duel_columns:
        raise HistoryDatabaseError("録画履歴DBに廃止済みのコイントス勝敗列が残っています")
    filter_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(saved_duel_filters)")
    }
    if not {"filter_id", "name", "normalized_name", "criteria_json"}.issubset(
        filter_columns
    ):
        raise HistoryDatabaseError("録画履歴DBに必須の保存済みフィルタースキーマがありません")
    tag_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(duel_record_tags)")
    }
    if not {"duel_id", "tag", "normalized_tag"}.issubset(tag_columns):
        raise HistoryDatabaseError(
            "録画履歴DBに必須のduel_record_tagsスキーマがありません"
        )
    change_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(duel_record_changes)")
    }
    if not {"change_id", "duel_id", "revision", "after_json"}.issubset(
        change_columns
    ):
        raise HistoryDatabaseError(
            "録画履歴DBに必須のduel_record_changesスキーマがありません"
        )
    event_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(duel_events)")
    }
    if not {"event_id", "recording_id", "elapsed_ms", "event_type", "status"}.issubset(
        event_columns
    ):
        raise HistoryDatabaseError("録画履歴DBに必須のduel_eventsスキーマがありません")
    catalog_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(duel_catalog_entries)")
    }
    if not {
        "entry_id",
        "kind",
        "name",
        "normalized_name",
        "description",
        "color",
        "is_archived",
        "opponent_only",
        "hidden_from_history_statistics",
    }.issubset(catalog_columns):
        raise HistoryDatabaseError(
            "録画履歴DBに必須のduel_catalog_entriesスキーマがありません"
        )
    tag_link_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(duel_record_tag_links)")
    }
    if not {"duel_id", "tag_entry_id"}.issubset(tag_link_columns):
        raise HistoryDatabaseError(
            "録画履歴DBに必須のduel_record_tag_linksスキーマがありません"
        )
    season_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(seasons)")
    }
    if not {
        "season_id",
        "name",
        "season_type",
        "duel_type",
        "start_date",
        "end_date",
        "description",
        "report_notes",
        "report_goal",
        "report_highlights",
        "report_challenges",
        "report_next_plan",
        "report_revision",
        "is_archived",
    }.issubset(season_columns):
        raise HistoryDatabaseError("録画履歴DBに必須のseasonsスキーマがありません")
    preference_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(duel_editor_preferences)")
    }
    if not {
        "singleton",
        "duel_type",
        "own_deck",
        "opponent_deck",
        "tags_json",
    }.issubset(preference_columns):
        raise HistoryDatabaseError(
            "録画履歴DBに必須のduel_editor_preferencesスキーマがありません"
        )
    quick_check = connection.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or quick_check[0] != "ok":
        detail = quick_check[0] if quick_check else "結果なし"
        raise HistoryDatabaseError(f"録画履歴DBの整合性検査に失敗しました: {detail}")


def _backup_database(
    source: sqlite3.Connection,
    database_path: Path,
    current_version: int,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = database_path.with_name(
        f"{database_path.stem}.v{current_version}.{timestamp}.{uuid.uuid4().hex}.backup.sqlite3"
    )
    destination: sqlite3.Connection | None = None
    try:
        destination = sqlite3.connect(backup_path)
        source.backup(destination)
        destination.commit()
        return backup_path
    except (OSError, sqlite3.Error) as exc:
        raise HistoryDatabaseError(
            f"移行前バックアップを作成できません: {backup_path}: {exc}"
        ) from exc
    finally:
        if destination is not None:
            destination.close()
