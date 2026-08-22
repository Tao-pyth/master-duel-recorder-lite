from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import string
import unicodedata

from .recording_history import RecordingHistoryEntry
from .upload_metadata import UploadMetadata, UploadMetadataError


DESCRIPTION_TEMPLATE_FILE = "youtube-description-template.txt"
YOUTUBE_POSTING_TEMPLATE_FILE = "youtube-posting-template.json"
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
DEFAULT_POSTING_TEMPLATE_TITLE = "{title}"
DEFAULT_POSTING_TEMPLATE_TAGS = ""
TEMPLATE_VARIABLE_DESCRIPTIONS = {
    "title": "自動生成した投稿タイトル",
    "recording_id": "録画ID",
    "recordingid": "録画ID",
    "started_at": "録画開始日時",
    "date": "録画日",
    "duration": "録画時間",
    "own_deck": "自分デッキ",
    "deckname": "自分デッキ",
    "opponent_deck": "相手デッキ",
    "opponentdeck": "相手デッキ",
    "result": "勝敗",
    "play_order": "先後",
    "coin_face": "コイントス",
    "duel_type": "対戦種別",
    "tags": "戦績タグ",
    "notes": "メモ",
}
ALLOWED_TEMPLATE_VARIABLES = set(TEMPLATE_VARIABLE_DESCRIPTIONS)
SECRET_LIKE_PATTERN = re.compile(
    r"(secret|token|api[_-]?key|password|client[_-]?secret|refresh)",
    re.IGNORECASE,
)


class DescriptionTemplateError(ValueError):
    """YouTube概要欄テンプレートを安全に展開できない場合のエラーです。"""


@dataclass(frozen=True)
class YouTubePostingTemplate:
    title: str = DEFAULT_POSTING_TEMPLATE_TITLE
    description: str = DEFAULT_DESCRIPTION_TEMPLATE
    tags: str = DEFAULT_POSTING_TEMPLATE_TAGS

    def normalized(self) -> YouTubePostingTemplate:
        title = _normalized_template(self.title, "title", allow_empty=False)
        description = _normalized_template(
            self.description, "description", allow_empty=True, allow_newlines=True
        )
        tags = _normalized_template(
            self.tags, "tags", allow_empty=True, allow_newlines=False
        )
        _validate_template_variables(title, field_label="タイトルテンプレート")
        _validate_template_variables(description, field_label="概要欄テンプレート")
        _validate_template_variables(tags, field_label="タグテンプレート")
        return YouTubePostingTemplate(title, description, tags)


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
        date_text = self.started_at.split("T", 1)[0] if self.started_at else "-"
        own_deck = self.own_deck or "-"
        opponent_deck = self.opponent_deck or "-"
        return {
            "title": self.title,
            "recording_id": self.recording_id,
            "recordingid": self.recording_id,
            "started_at": self.started_at,
            "date": date_text,
            "duration": self.duration,
            "own_deck": own_deck,
            "deckname": own_deck,
            "opponent_deck": opponent_deck,
            "opponentdeck": opponent_deck,
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


def load_youtube_posting_template(config_dir: Path) -> YouTubePostingTemplate:
    path = config_dir / YOUTUBE_POSTING_TEMPLATE_FILE
    if not path.exists():
        return YouTubePostingTemplate(description=load_description_template(config_dir))
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DescriptionTemplateError(f"YouTube投稿テンプレートを読めません: {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise DescriptionTemplateError("YouTube投稿テンプレートはobjectである必要があります")
    allowed = {"title", "description", "tags"}
    unknown = set(document) - allowed
    if unknown:
        raise DescriptionTemplateError(
            "許可されていないYouTube投稿テンプレート項目です: "
            + ", ".join(sorted(str(item) for item in unknown))
        )
    try:
        return YouTubePostingTemplate(
            title=document.get("title", DEFAULT_POSTING_TEMPLATE_TITLE),
            description=document.get("description", DEFAULT_DESCRIPTION_TEMPLATE),
            tags=document.get("tags", DEFAULT_POSTING_TEMPLATE_TAGS),
        ).normalized()
    except TypeError as exc:
        raise DescriptionTemplateError("YouTube投稿テンプレート項目は文字列である必要があります") from exc


def save_youtube_posting_template(
    config_dir: Path,
    template: YouTubePostingTemplate,
) -> YouTubePostingTemplate:
    selected = template.normalized()
    path = config_dir / YOUTUBE_POSTING_TEMPLATE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "title": selected.title,
                    "description": selected.description,
                    "tags": selected.tags,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise DescriptionTemplateError(f"YouTube投稿テンプレートを保存できません: {path}: {exc}") from exc
    return selected


def youtube_template_aliases() -> tuple[tuple[str, str], ...]:
    return tuple((f"{{{name}}}", description) for name, description in TEMPLATE_VARIABLE_DESCRIPTIONS.items())


def render_description_template(
    template: str,
    context: DescriptionTemplateContext,
) -> str:
    rendered = render_template_text(template, context, field_label="概要欄テンプレート")
    try:
        UploadMetadata(title=context.title, description=rendered)
    except UploadMetadataError as exc:
        raise DescriptionTemplateError(str(exc)) from exc
    return rendered


def render_youtube_posting_template(
    template: YouTubePostingTemplate,
    context: DescriptionTemplateContext,
    *,
    fallback_tags: tuple[str, ...] = (),
) -> UploadMetadata:
    selected = template.normalized()
    title = render_template_text(selected.title, context, field_label="タイトルテンプレート")
    description = render_template_text(
        selected.description, context, field_label="概要欄テンプレート"
    )
    if selected.tags.strip():
        rendered_tags = render_template_text(
            selected.tags, context, field_label="タグテンプレート"
        )
        tags = tuple(
            dict.fromkeys(
                tag.strip() for tag in rendered_tags.split(",") if tag.strip()
            )
        )
    else:
        tags = fallback_tags
    try:
        return UploadMetadata(title=title, description=description, tags=tags)
    except UploadMetadataError as exc:
        raise DescriptionTemplateError(str(exc)) from exc


def render_template_text(
    template: str,
    context: DescriptionTemplateContext,
    *,
    field_label: str,
) -> str:
    variables = context.variables()
    _validate_template_variables(template, field_label=field_label)
    try:
        return template.format_map(variables)
    except (KeyError, ValueError) as exc:
        raise DescriptionTemplateError(f"{field_label}を展開できません: {exc}") from exc


def _validate_template_variables(template: str, *, field_label: str) -> None:
    for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(template):
        if field_name is None:
            continue
        name = field_name.split(".", 1)[0].split("[", 1)[0]
        if SECRET_LIKE_PATTERN.search(name):
            raise DescriptionTemplateError(f"秘密情報に見える変数は使用できません: {name}")
        if name not in ALLOWED_TEMPLATE_VARIABLES:
            raise DescriptionTemplateError(f"未知の{field_label}変数です: {name}")


def _normalized_template(
    value: object,
    key: str,
    *,
    allow_empty: bool,
    allow_newlines: bool = False,
) -> str:
    if not isinstance(value, str):
        raise DescriptionTemplateError(f"{key} は文字列である必要があります")
    normalized = unicodedata.normalize("NFC", value).strip()
    if not allow_empty and not normalized:
        raise DescriptionTemplateError(f"{key} は空にできません")
    if not allow_newlines and any(character in normalized for character in "\r\n"):
        raise DescriptionTemplateError(f"{key} に改行は使用できません")
    return normalized
