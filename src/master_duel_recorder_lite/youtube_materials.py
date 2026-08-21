from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .description_template import (
    DescriptionTemplateContext,
    load_description_template,
    render_description_template,
)
from .duel_records import duel_choice_label
from .recording_history import RecordingHistoryEntry
from .runtime_paths import RuntimePaths
from .upload_metadata import UploadMetadata


class YouTubeMaterialError(RuntimeError):
    """YouTube投稿素材を生成できない場合のエラーです。"""


@dataclass(frozen=True)
class YouTubePostingMaterials:
    recording_id: str
    title: str
    description: str
    tags: tuple[str, ...]
    checklist: tuple[str, ...]
    output_directory: Path

    def metadata(self) -> UploadMetadata:
        return UploadMetadata(
            title=self.title,
            description=self.description,
            tags=self.tags,
        )


class YouTubeMaterialService:
    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths

    def generate(
        self,
        *,
        history: RecordingHistoryEntry,
        duel_record: object | None = None,
        title: str | None = None,
    ) -> YouTubePostingMaterials:
        values = getattr(duel_record, "values", None)
        own_deck = getattr(values, "own_deck", "") if values is not None else ""
        opponent_deck = getattr(values, "opponent_deck", "") if values is not None else ""
        result = getattr(values, "result", "unknown") if values is not None else "unknown"
        generated_title = title or _title(history, own_deck, opponent_deck, result)
        context = DescriptionTemplateContext.from_history(
            title=generated_title,
            history=history,
            duel_record=duel_record,
        )
        description = render_description_template(
            load_description_template(self.paths.config),
            context,
        )
        tags = _tags(values)
        checklist = (
            "タイトルが内容と一致している",
            "公開範囲を確認した",
            "KONAMIおよびYu-Gi-Oh! Master Duelの公式動画ではないことを理解している",
            "個人情報や通知音など不要な内容が含まれていない",
            "説明文とタグを確認した",
        )
        output_directory = self.paths.exports / history.recording_id
        return YouTubePostingMaterials(
            recording_id=history.recording_id,
            title=generated_title,
            description=description,
            tags=tags,
            checklist=checklist,
            output_directory=output_directory,
        )


def _title(
    history: RecordingHistoryEntry,
    own_deck: str,
    opponent_deck: str,
    result: str,
) -> str:
    date_text = (history.started_at or history.created_at).astimezone().strftime("%Y-%m-%d")
    parts = ["Master Duel", date_text]
    if own_deck:
        parts.append(own_deck)
    if opponent_deck:
        parts.append(f"vs {opponent_deck}")
    if result and result != "unknown":
        parts.append(duel_choice_label("result", result))
    return " ".join(parts)


def _tags(values: object | None) -> tuple[str, ...]:
    base = ["Master Duel", "遊戯王マスターデュエル"]
    if values is None:
        return tuple(base)
    for candidate in (
        getattr(values, "own_deck", ""),
        getattr(values, "opponent_deck", ""),
        duel_choice_label("duel_type", getattr(values, "duel_type", "other")),
    ):
        if candidate and candidate not in base:
            base.append(candidate)
    for tag in getattr(values, "tags", ()):
        if tag and tag not in base:
            base.append(tag)
    return tuple(base[:30])
