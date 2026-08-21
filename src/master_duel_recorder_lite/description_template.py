from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import string

from .recording_history import RecordingHistoryEntry
from .upload_metadata import UploadMetadata, UploadMetadataError


DESCRIPTION_TEMPLATE_FILE = "youtube-description-template.txt"
DEFAULT_DESCRIPTION_TEMPLATE = """{title}

録画ID: {recording_id}
日時: {started_at}
自分デッキ: {own_deck}
相手デッキ: {opponent_deck}
勝敗: {result}
先後: {play_order}
対戦種別: {duel_type}
タグ: {tags}
"""
ALLOWED_TEMPLATE_VARIABLES = {
    "title",
    "recording_id",
    "started_at",
    "duration",
    "own_deck",
    "opponent_deck",
    "result",
    "play_order",
    "coin_face",
    "duel_type",
    "tags",
    "notes",
}
SECRET_LIKE_PATTERN = re.compile(
    r"(secret|token|api[_-]?key|password|client[_-]?secret|refresh)",
    re.IGNORECASE,
)


class DescriptionTemplateError(ValueError):
    """YouTube概要欄テンプレートを安全に展開できない場合のエラーです。"""


@dataclass(frozen=True)
class DescriptionTemplateContext:
    title: str
    recording_id: str
    started_at: str
    duration: str
    own_deck: str = ""
    opponent_deck: str = ""
    result: str = "unknown"
    play_order: str = "unknown"
    coin_face: str = "unknown"
    duel_type: str = "other"
    tags: tuple[str, ...] = ()
    notes: str = ""

    @classmethod
    def from_history(
        cls,
        *,
        title: str,
        history: RecordingHistoryEntry,
        duel_record: object | None = None,
    ) -> DescriptionTemplateContext:
        values = getattr(duel_record, "values", None)
        return cls(
            title=title,
            recording_id=history.recording_id,
            started_at=(history.started_at or history.created_at).astimezone().isoformat(),
            duration=(
                f"{history.duration_seconds:.1f}s"
                if history.duration_seconds is not None
                else "-"
            ),
            own_deck=getattr(values, "own_deck", "") if values is not None else "",
            opponent_deck=getattr(values, "opponent_deck", "") if values is not None else "",
            result=getattr(values, "result", "unknown") if values is not None else "unknown",
            play_order=(
                getattr(values, "play_order", "unknown") if values is not None else "unknown"
            ),
            coin_face=(
                getattr(values, "coin_face", "unknown") if values is not None else "unknown"
            ),
            duel_type=getattr(values, "duel_type", "other") if values is not None else "other",
            tags=tuple(getattr(values, "tags", ())) if values is not None else (),
            notes=getattr(values, "notes", "") if values is not None else "",
        )

    def variables(self) -> dict[str, str]:
        return {
            "title": self.title,
            "recording_id": self.recording_id,
            "started_at": self.started_at,
            "duration": self.duration,
            "own_deck": self.own_deck or "-",
            "opponent_deck": self.opponent_deck or "-",
            "result": self.result,
            "play_order": self.play_order,
            "coin_face": self.coin_face,
            "duel_type": self.duel_type,
            "tags": ", ".join(self.tags) or "-",
            "notes": self.notes,
        }


def load_description_template(config_dir: Path) -> str:
    path = config_dir / DESCRIPTION_TEMPLATE_FILE
    if not path.exists():
        return DEFAULT_DESCRIPTION_TEMPLATE
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DescriptionTemplateError(f"概要欄テンプレートを読めません: {path}: {exc}") from exc


def render_description_template(
    template: str,
    context: DescriptionTemplateContext,
) -> str:
    variables = context.variables()
    for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(template):
        if field_name is None:
            continue
        name = field_name.split(".", 1)[0].split("[", 1)[0]
        if name not in ALLOWED_TEMPLATE_VARIABLES:
            raise DescriptionTemplateError(f"未知の概要欄テンプレート変数です: {name}")
        if SECRET_LIKE_PATTERN.search(name):
            raise DescriptionTemplateError(f"秘密情報に見える変数は使用できません: {name}")
    try:
        rendered = template.format_map(variables)
    except (KeyError, ValueError) as exc:
        raise DescriptionTemplateError(f"概要欄テンプレートを展開できません: {exc}") from exc
    try:
        UploadMetadata(title=context.title, description=rendered)
    except UploadMetadataError as exc:
        raise DescriptionTemplateError(str(exc)) from exc
    return rendered
