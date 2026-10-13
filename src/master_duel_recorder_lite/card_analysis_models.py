from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import unicodedata


CARD_ACTORS = {"self", "opponent", "unknown"}
CARD_ACTIONS = {"activate", "normal_summon", "special_summon", "unknown"}
CARD_TIME_KINDS = {"log_observed", "detail_observed", "actual_use"}
CARD_STATUSES = {"candidate", "confirmed", "rejected"}
CARD_JOB_STATES = {"queued", "running", "waiting", "completed", "failed", "cancelled"}


def normalize_card_name(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


@dataclass(frozen=True)
class CardTargetSnapshot:
    target_id: str
    name: str
    aliases: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"target_id": self.target_id, "id": self.target_id, "name": self.name,
                "aliases": list(self.aliases), "roles": list(self.roles)}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> CardTargetSnapshot:
        return cls(str(value.get("target_id", value.get("id", ""))), str(value["name"]),
                   tuple(value.get("aliases", ())), tuple(value.get("roles", ())))


@dataclass(frozen=True)
class CardTarget:
    target_id: str
    name: str
    aliases: tuple[str, ...]
    roles: tuple[str, ...]
    enabled: bool
    revision: int
    created_at: datetime
    updated_at: datetime

    @property
    def purpose(self) -> str:
        return "、".join(self.roles)

    def snapshot(self) -> CardTargetSnapshot:
        return CardTargetSnapshot(self.target_id, self.name, self.aliases, self.roles)

    def to_dict(self) -> dict[str, object]:
        return {**self.snapshot().to_dict(), "purpose": self.purpose, "enabled": self.enabled,
                "revision": self.revision, "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat()}


@dataclass(frozen=True)
class CardInputFingerprint:
    path: str
    size_bytes: int
    mtime_ns: int
    sha256: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> CardInputFingerprint:
        return cls(**value)


@dataclass(frozen=True)
class CardAnalysisJob:
    job_id: str
    recording_id: str
    generation: int
    state: str
    fingerprint: CardInputFingerprint
    target_snapshot: tuple[CardTargetSnapshot, ...]
    analyzer_version: str
    sample_fps: float
    progress: float
    error: str
    result_metadata: dict[str, object]
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {"job_id": self.job_id, "recording_id": self.recording_id,
                "generation": self.generation, "state": self.state,
                "fingerprint": self.fingerprint.to_dict(),
                "target_snapshot": [item.to_dict() for item in self.target_snapshot],
                "analyzer_version": self.analyzer_version, "sample_fps": self.sample_fps,
                "progress": self.progress, "error": self.error,
                "result_metadata": self.result_metadata,
                "created_at": self.created_at.isoformat(), "updated_at": self.updated_at.isoformat()}


@dataclass(frozen=True)
class CardObservationValues:
    target_id: str | None
    card_name: str
    actor: str
    action: str
    observed_ms: int
    actual_ms: int | None = None
    time_kind: str = "log_observed"
    evidence: dict[str, object] = field(default_factory=dict)
    source_key: str = ""
    confidence: float | None = None

    @property
    def supports_use(self) -> bool:
        return self.evidence.get("supports_use") is True and self.action != "unknown"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CardObservation:
    event_id: str
    recording_id: str
    job_id: str | None
    generation: int
    values: CardObservationValues
    status: str
    revision: int
    manual_locked: bool
    is_opener: bool
    created_at: datetime
    updated_at: datetime

    @property
    def observation_id(self) -> str:
        return self.event_id

    @property
    def target_id(self) -> str | None:
        return self.values.target_id

    @property
    def card_name(self) -> str:
        return self.values.card_name

    @property
    def actor(self) -> str:
        return self.values.actor

    @property
    def action(self) -> str:
        return self.values.action

    @property
    def observed_ms(self) -> int:
        return self.values.observed_ms

    @property
    def actual_ms(self) -> int | None:
        return self.values.actual_ms

    @property
    def time_kind(self) -> str:
        return self.values.time_kind

    @property
    def evidence(self) -> dict[str, object]:
        return self.values.evidence

    def to_dict(self) -> dict[str, object]:
        return {**self.values.to_dict(), "event_id": self.event_id,
                "observation_id": self.event_id, "recording_id": self.recording_id,
                "job_id": self.job_id, "generation": self.generation, "status": self.status,
                "revision": self.revision, "manual_locked": self.manual_locked,
                "is_opener": self.is_opener, "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat()}


@dataclass(frozen=True)
class CardNonuseConfirmation:
    recording_id: str
    target_id: str
    actor: str
    confirmed: bool
    revision: int
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "updated_at": self.updated_at.isoformat()}


@dataclass(frozen=True)
class CardOpenerConfirmation:
    recording_id: str
    actor: str
    event_id: str | None
    target_id: str | None
    status: str
    revision: int
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "updated_at": self.updated_at.isoformat()}


@dataclass(frozen=True)
class CardAnalysisReview:
    recording_id: str
    job: CardAnalysisJob | None
    successful_job: CardAnalysisJob | None
    observations: tuple[CardObservation, ...]
    nonuses: tuple[CardNonuseConfirmation, ...]
    openers: tuple[CardOpenerConfirmation, ...]

    def to_dict(self) -> dict[str, object]:
        return {"recording_id": self.recording_id,
                "job": self.job.to_dict() if self.job else None,
                "successful_job": self.successful_job.to_dict() if self.successful_job else None,
                "observations": [item.to_dict() for item in self.observations],
                "nonuses": [item.to_dict() for item in self.nonuses],
                "openers": [item.to_dict() for item in self.openers]}
