from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid
import zipfile


CURRENT_SCHEMA_VERSION = 19
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


def _migrate_to_v15(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE youtube_uploads (
            upload_id TEXT PRIMARY KEY CHECK (length(trim(upload_id)) > 0),
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            prepare_queue_id TEXT,
            state TEXT NOT NULL CHECK (
                state IN ('waiting', 'preparing', 'uploading', 'completed', 'failed', 'cancelled')
            ),
            privacy TEXT NOT NULL CHECK (privacy IN ('private', 'unlisted', 'public')),
            title TEXT NOT NULL CHECK (length(trim(title)) > 0),
            description TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            video_id TEXT,
            watch_url TEXT,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX youtube_uploads_recording_idx "
        "ON youtube_uploads(recording_id, created_at DESC, upload_id DESC)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX youtube_uploads_completed_recording_idx "
        "ON youtube_uploads(recording_id) WHERE state = 'completed'"
    )
    connection.execute(
        "CREATE INDEX youtube_uploads_state_idx "
        "ON youtube_uploads(state, updated_at DESC, upload_id DESC)"
    )


def _migrate_to_v16(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE tag_templates (
            template_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE UNIQUE INDEX tag_templates_name_idx ON tag_templates(name)"
    )
    connection.execute(
        """
        CREATE TABLE practice_goals (
            goal_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            metric TEXT NOT NULL,
            target_value REAL NOT NULL,
            current_value REAL NOT NULL DEFAULT 0,
            own_deck TEXT,
            opponent_deck TEXT,
            season_name TEXT,
            status TEXT NOT NULL CHECK (status IN ('active', 'achieved', 'paused', 'archived')),
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX practice_goals_status_idx ON practice_goals(status, updated_at DESC)"
    )


def _migrate_to_v17(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE duel_catalog_entries ADD COLUMN deck_only INTEGER NOT NULL DEFAULT 0 "
        "CHECK (deck_only IN (0, 1))"
    )
    connection.execute(
        """
        CREATE TABLE deck_tag_links (
            deck_entry_id INTEGER NOT NULL REFERENCES duel_catalog_entries(entry_id) ON DELETE CASCADE,
            tag_entry_id INTEGER NOT NULL REFERENCES duel_catalog_entries(entry_id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (deck_entry_id, tag_entry_id),
            CHECK (deck_entry_id <> tag_entry_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX deck_tag_links_tag_idx ON deck_tag_links(tag_entry_id, deck_entry_id)"
    )


def _migrate_to_v18(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE card_targets (
            target_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            aliases_json TEXT NOT NULL,
            roles_json TEXT NOT NULL,
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            revision INTEGER NOT NULL CHECK (revision >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE card_analysis_jobs (
            job_id TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            generation INTEGER NOT NULL CHECK (generation >= 1),
            state TEXT NOT NULL CHECK (
                state IN ('queued', 'running', 'waiting', 'completed', 'failed', 'cancelled')
            ),
            fingerprint_json TEXT NOT NULL,
            target_snapshot_json TEXT NOT NULL,
            analyzer_version TEXT NOT NULL,
            sample_fps REAL NOT NULL CHECK (sample_fps >= 0.25 AND sample_fps <= 2),
            progress REAL NOT NULL CHECK (progress >= 0 AND progress <= 1),
            error TEXT NOT NULL DEFAULT '',
            result_metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (recording_id, generation)
        )
        """
    )
    connection.execute(
        "CREATE UNIQUE INDEX card_analysis_active_recording_idx "
        "ON card_analysis_jobs(recording_id) WHERE state IN ('queued', 'running', 'waiting')"
    )
    connection.execute(
        """
        CREATE TABLE card_observations (
            event_id TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            job_id TEXT REFERENCES card_analysis_jobs(job_id) ON DELETE RESTRICT,
            generation INTEGER NOT NULL CHECK (generation >= 0),
            target_id TEXT REFERENCES card_targets(target_id) ON DELETE RESTRICT,
            card_name TEXT NOT NULL,
            actor TEXT NOT NULL CHECK (actor IN ('self', 'opponent', 'unknown')),
            action TEXT NOT NULL CHECK (
                action IN ('activate', 'normal_summon', 'special_summon', 'unknown')
            ),
            observed_ms INTEGER NOT NULL CHECK (observed_ms >= 0),
            actual_ms INTEGER CHECK (actual_ms IS NULL OR actual_ms >= 0),
            time_kind TEXT NOT NULL CHECK (
                time_kind IN ('log_observed', 'detail_observed', 'actual_use')
            ),
            evidence_json TEXT NOT NULL,
            confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
            source_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('candidate', 'confirmed', 'rejected')),
            revision INTEGER NOT NULL CHECK (revision >= 1),
            manual_locked INTEGER NOT NULL CHECK (manual_locked IN (0, 1)),
            original_values_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (recording_id, source_key)
        )
        """
    )
    connection.execute(
        "CREATE INDEX card_observations_recording_idx "
        "ON card_observations(recording_id, observed_ms, event_id)"
    )
    connection.execute(
        """
        CREATE TABLE card_nonuse_confirmations (
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            target_id TEXT NOT NULL REFERENCES card_targets(target_id) ON DELETE RESTRICT,
            actor TEXT NOT NULL CHECK (actor IN ('self', 'opponent')),
            confirmed INTEGER NOT NULL CHECK (confirmed IN (0, 1)),
            revision INTEGER NOT NULL CHECK (revision >= 1),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (recording_id, target_id, actor)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE card_opener_confirmations (
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            actor TEXT NOT NULL CHECK (actor IN ('self', 'opponent')),
            event_id TEXT REFERENCES card_observations(event_id) ON DELETE RESTRICT,
            status TEXT NOT NULL CHECK (status IN ('confirmed', 'unknown')),
            revision INTEGER NOT NULL CHECK (revision >= 1),
            updated_at TEXT NOT NULL,
            CHECK ((status = 'confirmed' AND event_id IS NOT NULL)
                   OR (status = 'unknown' AND event_id IS NULL)),
            PRIMARY KEY (recording_id, actor)
        )
        """
    )


def _migrate_to_v19(connection: sqlite3.Connection) -> None:
    """動画の根拠を消しても、対戦の確認済み情報を失わない形へ移します。"""
    connection.execute("DROP INDEX youtube_uploads_completed_recording_idx")
    connection.execute(
        "CREATE INDEX youtube_uploads_completed_recording_idx ON youtube_uploads "
        "(recording_id, updated_at DESC, upload_id DESC) WHERE state='completed'"
    )
    _create_v19_tables(connection)
    previous_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        _migrate_v19_assets(connection)
        _migrate_v19_card_information(connection)
        connection.execute("DROP TABLE card_opener_confirmations")
        connection.execute("DROP TABLE card_nonuse_confirmations")
    finally:
        connection.row_factory = previous_factory


def _create_v19_tables(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE card_identities (
            card_id TEXT PRIMARY KEY CHECK (length(trim(card_id)) > 0),
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE card_target_identity_links (
            target_id TEXT PRIMARY KEY REFERENCES card_targets(target_id) ON DELETE CASCADE,
            card_id TEXT NOT NULL REFERENCES card_identities(card_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE duel_card_information (
            information_id TEXT PRIMARY KEY CHECK (length(trim(information_id)) > 0),
            duel_id TEXT NOT NULL REFERENCES duel_records(duel_id) ON DELETE RESTRICT,
            card_id TEXT NOT NULL REFERENCES card_identities(card_id) ON DELETE RESTRICT,
            actor TEXT NOT NULL CHECK (actor IN ('self', 'opponent', 'unknown')),
            state TEXT NOT NULL CHECK (state IN ('used', 'not_used', 'unknown')),
            source TEXT NOT NULL CHECK (source IN ('manual', 'review', 'import', 'migration')),
            revision INTEGER NOT NULL CHECK (revision >= 1),
            confirmed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (duel_id, card_id, actor)
        )
        """,
        """
        CREATE TABLE duel_card_information_changes (
            change_id INTEGER PRIMARY KEY AUTOINCREMENT,
            information_id TEXT NOT NULL
                REFERENCES duel_card_information(information_id) ON DELETE RESTRICT,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            source TEXT NOT NULL CHECK (source IN ('manual', 'review', 'import', 'migration')),
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            UNIQUE (information_id, revision)
        )
        """,
        """
        CREATE TABLE duel_card_openers (
            duel_id TEXT NOT NULL REFERENCES duel_records(duel_id) ON DELETE RESTRICT,
            actor TEXT NOT NULL CHECK (actor IN ('self', 'opponent')),
            card_id TEXT REFERENCES card_identities(card_id) ON DELETE RESTRICT,
            status TEXT NOT NULL CHECK (status IN ('confirmed', 'unknown')),
            source TEXT NOT NULL CHECK (source IN ('manual', 'review', 'import', 'migration')),
            revision INTEGER NOT NULL CHECK (revision >= 1),
            confirmed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK ((status = 'confirmed' AND card_id IS NOT NULL)
                   OR (status = 'unknown' AND card_id IS NULL)),
            PRIMARY KEY (duel_id, actor)
        )
        """,
        """
        CREATE TABLE duel_card_opener_changes (
            change_id INTEGER PRIMARY KEY AUTOINCREMENT,
            duel_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            source TEXT NOT NULL CHECK (source IN ('manual', 'review', 'import', 'migration')),
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            UNIQUE (duel_id, actor, revision),
            FOREIGN KEY (duel_id, actor)
                REFERENCES duel_card_openers(duel_id, actor) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE recording_video_assets (
            asset_id TEXT PRIMARY KEY CHECK (length(trim(asset_id)) > 0),
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            generation INTEGER NOT NULL CHECK (generation >= 1),
            is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
            path TEXT NOT NULL CHECK (length(trim(path)) > 0),
            size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
            mtime_ns INTEGER CHECK (mtime_ns IS NULL OR mtime_ns >= 0),
            sha256 TEXT NOT NULL DEFAULT '',
            time_basis TEXT NOT NULL DEFAULT 'recording_start'
                CHECK (length(trim(time_basis)) > 0),
            state TEXT NOT NULL DEFAULT 'unverified'
                CHECK (state IN ('unverified', 'present', 'deleted', 'unavailable')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (recording_id, generation),
            UNIQUE (asset_id, generation),
            UNIQUE (recording_id, asset_id, generation)
        )
        """,
        "CREATE UNIQUE INDEX recording_video_current_idx ON recording_video_assets(recording_id) "
        "WHERE is_current = 1",
        """
        CREATE TABLE recording_video_protection (
            recording_id TEXT PRIMARY KEY REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            protected INTEGER NOT NULL DEFAULT 0 CHECK (protected IN (0, 1)),
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE card_observation_links (
            event_id TEXT PRIMARY KEY REFERENCES card_observations(event_id) ON DELETE CASCADE,
            video_asset_id TEXT NOT NULL
                REFERENCES recording_video_assets(asset_id) ON DELETE RESTRICT,
            video_generation INTEGER NOT NULL CHECK (video_generation >= 1),
            time_basis TEXT NOT NULL,
            information_id TEXT
                REFERENCES duel_card_information(information_id) ON DELETE SET NULL,
            input_fingerprint_json TEXT NOT NULL DEFAULT '{}',
            verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
            FOREIGN KEY (video_asset_id, video_generation)
                REFERENCES recording_video_assets(asset_id, generation) ON DELETE RESTRICT
        )
        """,
        "CREATE INDEX card_observation_information_idx "
        "ON card_observation_links(information_id, event_id)",
        """
        CREATE TABLE card_analysis_job_sources (
            job_id TEXT PRIMARY KEY REFERENCES card_analysis_jobs(job_id) ON DELETE CASCADE,
            video_asset_id TEXT NOT NULL
                REFERENCES recording_video_assets(asset_id) ON DELETE RESTRICT,
            video_generation INTEGER NOT NULL CHECK (video_generation >= 1),
            time_basis TEXT NOT NULL,
            input_fingerprint_json TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
            FOREIGN KEY (video_asset_id, video_generation)
                REFERENCES recording_video_assets(asset_id, generation) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE duel_event_sources (
            event_id TEXT PRIMARY KEY REFERENCES duel_events(event_id) ON DELETE CASCADE,
            video_asset_id TEXT NOT NULL
                REFERENCES recording_video_assets(asset_id) ON DELETE RESTRICT,
            video_generation INTEGER NOT NULL CHECK (video_generation >= 1),
            time_basis TEXT NOT NULL,
            input_fingerprint_json TEXT NOT NULL DEFAULT '{}',
            verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
            FOREIGN KEY (video_asset_id, video_generation)
                REFERENCES recording_video_assets(asset_id, generation) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE video_asset_uses (
            use_id TEXT PRIMARY KEY CHECK (length(trim(use_id)) > 0),
            asset_id TEXT NOT NULL REFERENCES recording_video_assets(asset_id) ON DELETE RESTRICT,
            owner_id TEXT NOT NULL CHECK (length(trim(owner_id)) > 0),
            purpose TEXT NOT NULL CHECK (length(trim(purpose)) > 0),
            process_id INTEGER NOT NULL CHECK (process_id >= 0),
            acquired_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX video_asset_uses_asset_idx ON video_asset_uses(asset_id)",
        """
        CREATE TABLE video_deletion_history (
            deletion_id TEXT PRIMARY KEY CHECK (length(trim(deletion_id)) > 0),
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            asset_id TEXT NOT NULL REFERENCES recording_video_assets(asset_id) ON DELETE RESTRICT,
            generation INTEGER NOT NULL CHECK (generation >= 1),
            reason TEXT NOT NULL CHECK (
                reason IN ('capacity', 'retention', 'youtube_uploaded', 'manual', 'restored_deleted')
            ),
            deleted_at TEXT NOT NULL,
            original_path TEXT NOT NULL,
            size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
            sha256 TEXT NOT NULL DEFAULT '',
            cause_known INTEGER NOT NULL DEFAULT 1 CHECK (cause_known IN (0, 1)),
            upload_id TEXT REFERENCES youtube_uploads(upload_id) ON DELETE SET NULL,
            prepare_queue_id TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            CHECK (reason <> 'restored_deleted' OR cause_known = 0),
            UNIQUE (asset_id, generation),
            FOREIGN KEY (recording_id, asset_id, generation)
                REFERENCES recording_video_assets(recording_id, asset_id, generation) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE video_deletion_journal (
            intent_id TEXT PRIMARY KEY CHECK (length(trim(intent_id)) > 0),
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            asset_id TEXT NOT NULL REFERENCES recording_video_assets(asset_id) ON DELETE RESTRICT,
            generation INTEGER NOT NULL CHECK (generation >= 1),
            reason TEXT NOT NULL CHECK (
                reason IN ('capacity', 'retention', 'youtube_uploaded', 'manual')
            ),
            state TEXT NOT NULL CHECK (
                state IN ('intent', 'staged', 'unlinked', 'completed', 'cancelled', 'failed')
            ),
            original_path TEXT NOT NULL CHECK (length(trim(original_path)) > 0),
            staged_path TEXT NOT NULL CHECK (length(trim(staged_path)) > 0),
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            mtime_ns INTEGER NOT NULL CHECK (mtime_ns >= 0),
            sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
            backup_path TEXT NOT NULL CHECK (length(trim(backup_path)) > 0),
            upload_id TEXT REFERENCES youtube_uploads(upload_id) ON DELETE SET NULL,
            prepare_queue_id TEXT,
            requested_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (recording_id, asset_id, generation)
                REFERENCES recording_video_assets(recording_id, asset_id, generation) ON DELETE RESTRICT
        )
        """,
        "CREATE UNIQUE INDEX video_deletion_pending_asset_idx "
        "ON video_deletion_journal(asset_id, generation) "
        "WHERE state IN ('intent', 'staged', 'unlinked')",
        """
        CREATE TABLE upload_cleanup_requests (
            request_id TEXT PRIMARY KEY CHECK (length(trim(request_id)) > 0),
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            asset_id TEXT NOT NULL REFERENCES recording_video_assets(asset_id) ON DELETE RESTRICT,
            generation INTEGER NOT NULL CHECK (generation >= 1),
            upload_id TEXT NOT NULL REFERENCES youtube_uploads(upload_id) ON DELETE RESTRICT,
            prepare_queue_id TEXT,
            state TEXT NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'completed', 'cancelled')),
            provenance_json TEXT NOT NULL,
            last_skip_reason TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            last_attempt_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (upload_id, asset_id, generation),
            FOREIGN KEY (recording_id, asset_id, generation)
                REFERENCES recording_video_assets(recording_id, asset_id, generation) ON DELETE RESTRICT
        )
        """,
        "CREATE INDEX upload_cleanup_pending_idx ON upload_cleanup_requests(state, created_at)",
        """
        CREATE TABLE card_migration_review_items (
            review_id TEXT PRIMARY KEY CHECK (length(trim(review_id)) > 0),
            recording_id TEXT REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            duel_id TEXT REFERENCES duel_records(duel_id) ON DELETE RESTRICT,
            source_kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            data_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'needs_review'
                CHECK (status IN ('needs_review', 'resolved')),
            created_at TEXT NOT NULL,
            UNIQUE (source_kind, source_id, reason)
        )
        """,
    )
    # executescriptは既存transactionをcommitするため、移行内では使いません。
    for statement in statements:
        connection.execute(statement)


def _v19_id(kind: str, *parts: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, _migration_json(["mdrl-schema19", kind, *parts])).hex


def _migration_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _migration_object(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _migrate_v19_assets(connection: sqlite3.Connection) -> None:
    for recording in connection.execute("SELECT * FROM recordings ORDER BY recording_id").fetchall():
        identifier = _v19_id("asset", recording["recording_id"], "1")
        connection.execute(
            "INSERT INTO recording_video_assets "
            "(asset_id,recording_id,generation,is_current,path,size_bytes,state,created_at,updated_at) "
            "VALUES (?,?,1,1,?,?,'unverified',?,?)",
            (identifier, recording["recording_id"], recording["output_path"],
             recording["size_bytes"], recording["created_at"], recording["updated_at"]),
        )
        connection.execute(
            "INSERT INTO recording_video_protection "
            "(recording_id,protected,revision,created_at,updated_at) VALUES (?,0,1,?,?)",
            (recording["recording_id"], recording["created_at"], recording["updated_at"]),
        )
    connection.execute(
        "INSERT INTO card_analysis_job_sources "
        "(job_id,video_asset_id,video_generation,time_basis,input_fingerprint_json,verified) "
        "SELECT jobs.job_id,assets.asset_id,assets.generation,assets.time_basis,jobs.fingerprint_json,0 "
        "FROM card_analysis_jobs jobs JOIN recording_video_assets assets "
        "ON assets.recording_id=jobs.recording_id AND assets.is_current=1"
    )
    connection.execute(
        "INSERT INTO card_observation_links "
        "(event_id,video_asset_id,video_generation,time_basis,input_fingerprint_json,verified) "
        "SELECT observations.event_id,assets.asset_id,assets.generation,assets.time_basis,"
        "COALESCE(jobs.fingerprint_json,'{}'),0 "
        "FROM card_observations observations JOIN recording_video_assets assets "
        "ON assets.recording_id=observations.recording_id AND assets.is_current=1 "
        "LEFT JOIN card_analysis_jobs jobs ON jobs.job_id=observations.job_id"
    )
    connection.execute(
        "INSERT INTO duel_event_sources "
        "(event_id,video_asset_id,video_generation,time_basis,verified) "
        "SELECT events.event_id,assets.asset_id,assets.generation,assets.time_basis,0 "
        "FROM duel_events events JOIN recording_video_assets assets "
        "ON assets.recording_id=events.recording_id AND assets.is_current=1"
    )


def _migration_review(
    connection: sqlite3.Connection, *, recording_id: str, duel_id: str | None,
    source_kind: str, source_id: str, reason: str, data: dict[str, object], created_at: str,
) -> None:
    connection.execute(
        "INSERT INTO card_migration_review_items "
        "(review_id,recording_id,duel_id,source_kind,source_id,reason,data_json,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (_v19_id("review", source_kind, source_id, reason), recording_id, duel_id,
         source_kind, source_id, reason, _migration_json(data), created_at),
    )


def _migrate_v19_card_information(connection: sqlite3.Connection) -> None:
    targets = {
        row["target_id"]: row for row in connection.execute("SELECT * FROM card_targets").fetchall()
    }
    for identifier, target in targets.items():
        connection.execute(
            "INSERT INTO card_identities VALUES (?,?,?,?,?)",
            (identifier, target["name"], target["normalized_name"],
             target["created_at"], target["updated_at"]),
        )
        connection.execute("INSERT INTO card_target_identity_links VALUES (?,?)", (identifier, identifier))

    recordings = {
        row["recording_id"]: row for row in connection.execute("SELECT * FROM recordings").fetchall()
    }
    duels = {
        row["recording_id"]: row["duel_id"]
        for row in connection.execute("SELECT duel_id,recording_id FROM duel_records").fetchall()
        if row["recording_id"] is not None
    }
    observations = connection.execute("SELECT * FROM card_observations ORDER BY event_id").fetchall()
    nonuses = connection.execute("SELECT * FROM card_nonuse_confirmations").fetchall()
    openers = connection.execute("SELECT * FROM card_opener_confirmations").fetchall()
    meaningful_recordings = {
        row["recording_id"] for row in observations if _old_observation_used(row)
    } | {
        row["recording_id"] for row in nonuses if row["confirmed"] == 1
    } | {
        row["recording_id"] for row in openers if row["status"] == "confirmed"
    }
    for recording_id in sorted(meaningful_recordings - duels.keys()):
        recording = recordings[recording_id]
        duel_id = _v19_id("draft", recording_id)
        occurred_at = recording["started_at"] or recording["created_at"]
        connection.execute(
            "INSERT INTO duel_records "
            "(duel_id,recording_id,entry_origin,occurred_at,status,result,play_order,coin_face,"
            "own_deck,opponent_deck,duel_type,notes,revision,created_at,updated_at) "
            "VALUES (?,?,'recording',?,'draft','unknown','unknown','unknown','','','other','',1,?,?)",
            (duel_id, recording_id, occurred_at, recording["created_at"], recording["updated_at"]),
        )
        duels[recording_id] = duel_id

    grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
    for row in observations:
        if row["status"] != "confirmed":
            continue
        duel_id = duels.get(row["recording_id"])
        reason = None
        if not _old_observation_used(row):
            reason = "confirmed_observation_without_usage"
        elif row["target_id"] not in targets:
            reason = "unmapped_card"
        elif row["actor"] == "unknown":
            reason = "unknown_actor"
        if reason is not None:
            _migration_review(
                connection, recording_id=row["recording_id"], duel_id=duel_id,
                source_kind="observation", source_id=row["event_id"], reason=reason,
                data=dict(row), created_at=row["updated_at"],
            )
        if _old_observation_used(row) and row["target_id"] in targets and duel_id is not None:
            grouped.setdefault((duel_id, row["target_id"], row["actor"]), []).append(row)

    information_ids: dict[tuple[str, str, str], str] = {}
    for key, rows in grouped.items():
        duel_id, card_id, actor = key
        snapshots = [_old_semantic_snapshot(row) for row in rows]
        latest = max(rows, key=lambda row: (row["updated_at"], row["event_id"]))
        revision = max(row["revision"] for row in rows)
        information_id = _insert_migrated_information(
            connection, duel_id=duel_id, card_id=card_id, actor=actor, state="used",
            revision=revision, created_at=min(row["created_at"] for row in rows),
            updated_at=latest["updated_at"], legacy_snapshots=snapshots,
        )
        information_ids[key] = information_id
        for row in rows:
            connection.execute(
                "UPDATE card_observation_links SET information_id=? WHERE event_id=?",
                (information_id, row["event_id"]),
            )

    for row in nonuses:
        duel_id = duels.get(row["recording_id"])
        source_id = _migration_json([row["recording_id"], row["target_id"], row["actor"]])
        if duel_id is None:
            _migration_review(
                connection, recording_id=row["recording_id"], duel_id=None,
                source_kind="nonuse", source_id=source_id, reason="orphan_confirmation_history",
                data=dict(row), created_at=row["updated_at"],
            )
            continue
        key = (duel_id, row["target_id"], row["actor"])
        if key in information_ids:
            _migration_review(
                connection, recording_id=row["recording_id"], duel_id=duel_id,
                source_kind="nonuse", source_id=source_id,
                reason="usage_nonuse_conflict" if row["confirmed"] else "nonuse_clear_with_usage",
                data=dict(row), created_at=row["updated_at"],
            )
            continue
        information_ids[key] = _insert_migrated_information(
            connection, duel_id=duel_id, card_id=row["target_id"], actor=row["actor"],
            state="not_used" if row["confirmed"] else "unknown", revision=row["revision"],
            created_at=row["updated_at"], updated_at=row["updated_at"],
            legacy_snapshots=[dict(row)],
        )

    by_event = {row["event_id"]: row for row in observations}
    for row in openers:
        duel_id = duels.get(row["recording_id"])
        source_id = _migration_json([row["recording_id"], row["actor"]])
        if duel_id is None:
            _migration_review(
                connection, recording_id=row["recording_id"], duel_id=None,
                source_kind="opener", source_id=source_id, reason="orphan_confirmation_history",
                data=dict(row), created_at=row["updated_at"],
            )
            continue
        observation = by_event.get(row["event_id"])
        valid = row["status"] == "confirmed" and observation is not None and (
            _old_observation_used(observation) and observation["target_id"] in targets
            and observation["actor"] == row["actor"]
            and observation["recording_id"] == row["recording_id"]
        )
        if row["status"] == "confirmed" and not valid:
            _migration_review(
                connection, recording_id=row["recording_id"], duel_id=duel_id,
                source_kind="opener", source_id=source_id, reason="invalid_opener_reference",
                data={"opener": dict(row), "observation": dict(observation) if observation else None},
                created_at=row["updated_at"],
            )
        card_id = observation["target_id"] if valid else None
        status = "confirmed" if valid else "unknown"
        connection.execute(
            "INSERT INTO duel_card_openers "
            "(duel_id,actor,card_id,status,source,revision,confirmed_at,created_at,updated_at) "
            "VALUES (?,?,?,?,'migration',?,?,?,?)",
            (duel_id, row["actor"], card_id, status, row["revision"],
             row["updated_at"] if valid else None, row["updated_at"], row["updated_at"]),
        )
        connection.execute(
            "INSERT INTO duel_card_opener_changes "
            "(duel_id,actor,revision,source,before_json,after_json,changed_at) "
            "VALUES (?,?,?,'migration',?,?,?)",
            (duel_id, row["actor"], row["revision"], _migration_json(dict(row)),
             _migration_json({"duel_id": duel_id, "actor": row["actor"], "card_id": card_id,
                              "status": status}), row["updated_at"]),
        )


def _old_observation_used(row: sqlite3.Row) -> bool:
    return (row["status"] == "confirmed" and row["action"] != "unknown"
            and _migration_object(row["evidence_json"]).get("supports_use") is True)


def _old_semantic_snapshot(row: sqlite3.Row) -> dict[str, object]:
    # 対戦の確認履歴は動画時刻を存在条件にしません。旧revisionと人の訂正は残します。
    original = _migration_object(row["original_values_json"])
    keys = ("target_id", "card_name", "actor", "action", "status", "revision", "manual_locked")
    return {
        "current": {key: row[key] for key in keys},
        "original": {key: original[key] for key in keys if key in original},
        "original_supports_use": (
            original.get("evidence", {}).get("supports_use")
            if isinstance(original.get("evidence"), dict) else None
        ),
        "supports_use": _migration_object(row["evidence_json"]).get("supports_use"),
    }


def _insert_migrated_information(
    connection: sqlite3.Connection, *, duel_id: str, card_id: str, actor: str, state: str,
    revision: int, created_at: str, updated_at: str, legacy_snapshots: list[dict[str, object]],
) -> str:
    information_id = _v19_id("information", duel_id, card_id, actor)
    connection.execute(
        "INSERT INTO duel_card_information "
        "(information_id,duel_id,card_id,actor,state,source,revision,confirmed_at,created_at,updated_at) "
        "VALUES (?,?,?,?,?,'migration',?,?,?,?)",
        (information_id, duel_id, card_id, actor, state, revision,
         updated_at if state != "unknown" else None, created_at, updated_at),
    )
    connection.execute(
        "INSERT INTO duel_card_information_changes "
        "(information_id,revision,source,before_json,after_json,changed_at) "
        "VALUES (?,?,'migration',?,?,?)",
        (information_id, revision, _migration_json({"legacy": legacy_snapshots}),
         _migration_json({"information_id": information_id, "duel_id": duel_id,
                          "card_id": card_id, "actor": actor, "state": state}), updated_at),
    )
    return information_id


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
    15: _migrate_to_v15,
    16: _migrate_to_v16,
    17: _migrate_to_v17,
    18: _migrate_to_v18,
    19: _migrate_to_v19,
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
            if not backup_path.is_file() or backup_path.stat().st_size == 0:
                raise HistoryDatabaseError("移行前バックアップを保持できません")
            _validate_migration_backup(backup_path, current_version)

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
        "deck_only",
    }.issubset(catalog_columns):
        raise HistoryDatabaseError(
            "録画履歴DBに必須のduel_catalog_entriesスキーマがありません"
        )
    deck_tag_link_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(deck_tag_links)")
    }
    if not {"deck_entry_id", "tag_entry_id"}.issubset(deck_tag_link_columns):
        raise HistoryDatabaseError(
            "録画履歴DBに必須のdeck_tag_linksスキーマがありません"
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
    youtube_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(youtube_uploads)")
    }
    if not {
        "upload_id",
        "recording_id",
        "prepare_queue_id",
        "state",
        "privacy",
        "title",
        "description",
        "tags_json",
        "video_id",
        "watch_url",
        "attempts",
        "error",
        "created_at",
        "updated_at",
    }.issubset(youtube_columns):
        raise HistoryDatabaseError("録画履歴DBに必須のyoutube_uploadsスキーマがありません")
    tag_template_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(tag_templates)")
    }
    if not {"template_id", "name", "tags_json", "created_at", "updated_at"}.issubset(
        tag_template_columns
    ):
        raise HistoryDatabaseError("録画履歴DBに必須のtag_templatesスキーマがありません")
    goal_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(practice_goals)")
    }
    if not {
        "goal_id",
        "title",
        "metric",
        "target_value",
        "current_value",
        "own_deck",
        "opponent_deck",
        "season_name",
        "status",
        "notes",
        "created_at",
        "updated_at",
    }.issubset(goal_columns):
        raise HistoryDatabaseError("録画履歴DBに必須のpractice_goalsスキーマがありません")
    card_columns = {
        "card_targets": {"target_id", "name", "normalized_name", "aliases_json", "roles_json",
                         "enabled", "revision", "created_at", "updated_at"},
        "card_analysis_jobs": {"job_id", "recording_id", "generation", "state", "fingerprint_json",
                               "target_snapshot_json", "analyzer_version", "sample_fps", "progress",
                               "error", "result_metadata_json", "created_at", "updated_at"},
        "card_observations": {"event_id", "recording_id", "job_id", "generation", "target_id",
                              "card_name", "actor", "action", "observed_ms", "actual_ms", "time_kind",
                              "evidence_json", "confidence", "source_key", "status", "revision",
                              "manual_locked", "original_values_json", "created_at", "updated_at"},
        "card_identities": {"card_id", "name", "normalized_name", "created_at", "updated_at"},
        "card_target_identity_links": {"target_id", "card_id"},
        "duel_card_information": {"information_id", "duel_id", "card_id", "actor", "state", "source",
                                  "revision", "confirmed_at", "created_at", "updated_at"},
        "duel_card_information_changes": {"change_id", "information_id", "revision", "source",
                                          "before_json", "after_json", "changed_at"},
        "duel_card_openers": {"duel_id", "actor", "card_id", "status", "source", "revision",
                              "confirmed_at", "created_at", "updated_at"},
        "duel_card_opener_changes": {"change_id", "duel_id", "actor", "revision", "source",
                                     "before_json", "after_json", "changed_at"},
        "recording_video_assets": {"asset_id", "recording_id", "generation", "is_current", "path",
                                   "size_bytes", "mtime_ns", "sha256", "time_basis", "state",
                                   "created_at", "updated_at"},
        "recording_video_protection": {"recording_id", "protected", "revision", "created_at", "updated_at"},
        "card_observation_links": {"event_id", "video_asset_id", "video_generation", "time_basis",
                                   "information_id", "input_fingerprint_json", "verified"},
        "card_analysis_job_sources": {"job_id", "video_asset_id", "video_generation", "time_basis",
                                      "input_fingerprint_json", "verified"},
        "duel_event_sources": {"event_id", "video_asset_id", "video_generation", "time_basis",
                               "input_fingerprint_json", "verified"},
        "video_asset_uses": {"use_id", "asset_id", "owner_id", "purpose", "process_id", "acquired_at"},
        "video_deletion_history": {"deletion_id", "recording_id", "asset_id", "generation", "reason",
                                   "deleted_at", "original_path", "size_bytes", "sha256", "cause_known",
                                   "upload_id", "prepare_queue_id", "metadata_json"},
        "video_deletion_journal": {"intent_id", "recording_id", "asset_id", "generation", "reason", "state",
                                   "original_path", "staged_path", "size_bytes", "mtime_ns", "sha256",
                                   "backup_path", "upload_id", "prepare_queue_id", "requested_at", "updated_at",
                                   "error", "metadata_json"},
        "upload_cleanup_requests": {"request_id", "recording_id", "asset_id", "generation", "upload_id",
                                    "prepare_queue_id", "state", "provenance_json", "last_skip_reason", "attempts",
                                    "last_attempt_at", "created_at", "updated_at"},
        "card_migration_review_items": {"review_id", "recording_id", "duel_id", "source_kind", "source_id",
                                        "reason", "data_json", "status", "created_at"},
    }
    for table, required in card_columns.items():
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if not required.issubset(columns):
            raise HistoryDatabaseError(f"録画履歴DBに必須の{table}スキーマがありません")
    quick_check = connection.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or quick_check[0] != "ok":
        detail = quick_check[0] if quick_check else "結果なし"
        raise HistoryDatabaseError(f"録画履歴DBの整合性検査に失敗しました: {detail}")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise HistoryDatabaseError("録画履歴DBの外部キー整合性検査に失敗しました")


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


def _validate_migration_backup(path: Path, expected_version: int) -> None:
    """callbackの成功返却だけに依存せず、移行前の復元材料を検証します。"""
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                if (manifest.get("schema") != "mdrl-data-backup-v1"
                        or manifest.get("protected") is not True
                        or manifest.get("schema_version") != expected_version):
                    raise HistoryDatabaseError("移行前バックアップの保護状態・スキーマ版が不正です")
                allowed = {"manifest.json", "data/history.sqlite3", "config/app.toml"}
                if not set(archive.namelist()).issubset(allowed):
                    raise HistoryDatabaseError("移行前バックアップに未許可のファイルがあります")
                members = manifest.get("members")
                if not isinstance(members, dict) or "data/history.sqlite3" not in members:
                    raise HistoryDatabaseError("移行前バックアップに履歴DBがありません")
                for name, metadata in members.items():
                    if name not in allowed - {"manifest.json"} or not isinstance(metadata, dict):
                        raise HistoryDatabaseError("移行前バックアップのファイル定義が不正です")
                    payload = archive.read(name)
                    if (metadata.get("size") != len(payload)
                            or metadata.get("sha256") != hashlib.sha256(payload).hexdigest()):
                        raise HistoryDatabaseError("移行前バックアップのファイル検証に失敗しました")
                with tempfile.TemporaryDirectory(prefix="mdrl-migration-check-") as directory:
                    snapshot = Path(directory) / "history.sqlite3"
                    snapshot.write_bytes(archive.read("data/history.sqlite3"))
                    _validate_migration_snapshot(snapshot, expected_version)
        else:
            _validate_migration_snapshot(path, expected_version)
    except (OSError, sqlite3.Error, zipfile.BadZipFile, ValueError, KeyError, AttributeError) as exc:
        raise HistoryDatabaseError(f"移行前バックアップを検証できません: {exc}") from exc


def _validate_migration_snapshot(path: Path, expected_version: int) -> None:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=10.0)
    try:
        if _read_schema_version(connection) != expected_version:
            raise HistoryDatabaseError("移行前バックアップのスキーマ版が一致しません")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise HistoryDatabaseError("移行前バックアップの整合性検査に失敗しました")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise HistoryDatabaseError("移行前バックアップの外部キー検査に失敗しました")
    finally:
        connection.close()
