from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import unicodedata


MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 5000
MAX_TAGS = 30
MAX_TAG_LENGTH = 100


class UploadMetadataError(ValueError):
    """アップロード準備メタデータが安全な制約を満たさない場合のエラーです。"""


class UploadPrivacy(str, Enum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


@dataclass(frozen=True)
class UploadMetadata:
    title: str
    description: str = ""
    tags: tuple[str, ...] = ()
    privacy: UploadPrivacy = UploadPrivacy.PRIVATE

    def __post_init__(self) -> None:
        title = _normalized_text(self.title, "title", allow_newlines=False)
        description = _normalized_text(
            self.description,
            "description",
            allow_empty=True,
            allow_newlines=True,
        )
        if len(title) > MAX_TITLE_LENGTH:
            raise UploadMetadataError(f"title は{MAX_TITLE_LENGTH}文字以内である必要があります")
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise UploadMetadataError(
                f"description は{MAX_DESCRIPTION_LENGTH}文字以内である必要があります"
            )
        if not isinstance(self.tags, tuple):
            raise UploadMetadataError("tags は文字列tupleである必要があります")
        if len(self.tags) > MAX_TAGS:
            raise UploadMetadataError(f"tags は{MAX_TAGS}件以内である必要があります")
        normalized_tags: list[str] = []
        seen: set[str] = set()
        for tag in self.tags:
            normalized = _normalized_text(tag, "tag", allow_newlines=False)
            if len(normalized) > MAX_TAG_LENGTH:
                raise UploadMetadataError(f"tag は{MAX_TAG_LENGTH}文字以内である必要があります")
            key = normalized.casefold()
            if key in seen:
                raise UploadMetadataError(f"tag が重複しています: {normalized}")
            seen.add(key)
            normalized_tags.append(normalized)
        if not isinstance(self.privacy, UploadPrivacy):
            raise UploadMetadataError("privacy はprivate、unlisted、public のいずれかである必要があります")
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "tags", tuple(normalized_tags))

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "privacy": self.privacy.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> UploadMetadata:
        if not isinstance(value, dict):
            raise UploadMetadataError("metadata はobjectである必要があります")
        allowed = {"title", "description", "tags", "privacy"}
        unknown = set(value) - allowed
        if unknown:
            raise UploadMetadataError(
                "許可されていないmetadata項目です: " + ", ".join(sorted(str(key) for key in unknown))
            )
        tags = value.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise UploadMetadataError("tags は文字列配列である必要があります")
        title = value.get("title")
        description = value.get("description", "")
        if not isinstance(title, str):
            raise UploadMetadataError("title は文字列である必要があります")
        if not isinstance(description, str):
            raise UploadMetadataError("description は文字列である必要があります")
        try:
            privacy = UploadPrivacy(value.get("privacy", UploadPrivacy.PRIVATE.value))
        except ValueError as exc:
            raise UploadMetadataError("privacy はprivate、unlisted、public のいずれかである必要があります") from exc
        return cls(
            title=title,
            description=description,
            tags=tuple(tags),
            privacy=privacy,
        )


def _normalized_text(
    value: object,
    key: str,
    *,
    allow_empty: bool = False,
    allow_newlines: bool,
) -> str:
    if not isinstance(value, str):
        raise UploadMetadataError(f"{key} は文字列である必要があります")
    normalized = unicodedata.normalize("NFC", value).strip()
    if not allow_empty and not normalized:
        raise UploadMetadataError(f"{key} は空にできません")
    for character in normalized:
        category = unicodedata.category(character)
        if category == "Cc" and not (allow_newlines and character in {"\n", "\r", "\t"}):
            raise UploadMetadataError(f"{key} に制御文字は使用できません")
    if not allow_newlines and any(character in normalized for character in "\r\n"):
        raise UploadMetadataError(f"{key} に改行は使用できません")
    return normalized
