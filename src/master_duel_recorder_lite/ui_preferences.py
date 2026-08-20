from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import uuid


HISTORY_DEFAULT_VISIBLE_COLUMNS = ("coin_face", "duel_type", "duration", "size")
HISTORY_SELECTABLE_COLUMNS = (
    "coin_face",
    "duel_type",
    "duration",
    "size",
    "opponent_deck",
)
HISTORY_OPTIONAL_COLUMNS = HISTORY_SELECTABLE_COLUMNS
HISTORY_DOUBLE_CLICK_ACTIONS = {"play", "edit"}
COLOR_KEYS = (
    "result.win",
    "result.loss",
    "result.draw",
    "result.unknown",
    "play_order.first",
    "play_order.second",
    "play_order.unknown",
    "coin_face.heads",
    "coin_face.tails",
    "coin_face.unknown",
    "entry_origin.recording",
    "entry_origin.manual",
    "entry_origin.import",
)
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True)
class UiPreferences:
    history_visible_columns: tuple[str, ...] = HISTORY_DEFAULT_VISIBLE_COLUMNS
    history_cell_colors: dict[str, str] = field(
        default_factory=lambda: {key: "#FFFFFF" for key in COLOR_KEYS}
    )
    automatic_update_check: bool = True
    history_double_click_action: str = "play"

    def normalized(self) -> UiPreferences:
        columns = tuple(
            name for name in HISTORY_SELECTABLE_COLUMNS if name in self.history_visible_columns
        )
        colors = {
            key: value.upper() if HEX_COLOR.fullmatch(value) else "#FFFFFF"
            for key, value in self.history_cell_colors.items()
            if key in COLOR_KEYS
        }
        for key in COLOR_KEYS:
            colors.setdefault(key, "#FFFFFF")
        action = (
            self.history_double_click_action
            if self.history_double_click_action in HISTORY_DOUBLE_CLICK_ACTIONS
            else "play"
        )
        return UiPreferences(columns, colors, bool(self.automatic_update_check), action)


def preferences_path(config_directory: Path) -> Path:
    return config_directory.expanduser().resolve() / "ui-preferences.json"


def load_ui_preferences(config_directory: Path) -> UiPreferences:
    path = preferences_path(config_directory)
    if not path.is_file():
        return UiPreferences()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            return UiPreferences()
        raw_columns = document.get("history_visible_columns", HISTORY_DEFAULT_VISIBLE_COLUMNS)
        raw_colors = document.get("history_cell_colors", {})
        return UiPreferences(
            tuple(str(item) for item in raw_columns)
            if isinstance(raw_columns, list)
            else HISTORY_DEFAULT_VISIBLE_COLUMNS,
            {str(key): str(value) for key, value in raw_colors.items()}
            if isinstance(raw_colors, dict)
            else {},
            bool(document.get("automatic_update_check", True)),
            str(document.get("history_double_click_action", "play")),
        ).normalized()
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return UiPreferences()


def save_ui_preferences(config_directory: Path, preferences: UiPreferences) -> Path:
    selected = preferences.normalized()
    path = preferences_path(config_directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    data = json.dumps(
        {
            "schema_version": 1,
            "history_visible_columns": list(selected.history_visible_columns),
            "history_cell_colors": selected.history_cell_colors,
            "automatic_update_check": selected.automatic_update_check,
            "history_double_click_action": selected.history_double_click_action,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
