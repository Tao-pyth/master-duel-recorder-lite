from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WizardStep(str, Enum):
    FFMPEG = "ffmpeg"
    STORAGE = "storage"
    TARGET = "target"
    AUDIO = "audio"
    READINESS = "readiness"
    TEST_RECORDING = "test_recording"
    PLAYBACK = "playback"


class WizardStepStatus(str, Enum):
    WAITING = "waiting"
    COMPLETED = "completed"
    WARNING = "warning"
    FAILED = "failed"


@dataclass(frozen=True)
class WizardStepResult:
    step: WizardStep
    status: WizardStepStatus
    message: str


@dataclass(frozen=True)
class SetupWizardState:
    steps: tuple[WizardStepResult, ...]

    @property
    def completed(self) -> bool:
        return all(step.status is WizardStepStatus.COMPLETED for step in self.steps)

    @property
    def blocked(self) -> bool:
        return any(step.status is WizardStepStatus.FAILED for step in self.steps)

    @property
    def next_step(self) -> WizardStep | None:
        for result in self.steps:
            if result.status is WizardStepStatus.WAITING:
                return result.step
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "completed": self.completed,
            "blocked": self.blocked,
            "next_step": self.next_step.value if self.next_step else None,
            "steps": [
                {
                    "step": item.step.value,
                    "status": item.status.value,
                    "message": item.message,
                }
                for item in self.steps
            ],
        }


DEFAULT_WIZARD_STEPS = (
    WizardStep.FFMPEG,
    WizardStep.STORAGE,
    WizardStep.TARGET,
    WizardStep.AUDIO,
    WizardStep.READINESS,
    WizardStep.TEST_RECORDING,
    WizardStep.PLAYBACK,
)


def initial_wizard_state(*, completed: bool = False) -> SetupWizardState:
    status = WizardStepStatus.COMPLETED if completed else WizardStepStatus.WAITING
    message = "完了済み" if completed else "未確認"
    return SetupWizardState(
        tuple(WizardStepResult(step, status, message) for step in DEFAULT_WIZARD_STEPS)
    )


def update_wizard_step(
    state: SetupWizardState,
    *,
    step: WizardStep,
    status: WizardStepStatus,
    message: str,
) -> SetupWizardState:
    if not message.strip():
        raise ValueError("messageは空にできません")
    return SetupWizardState(
        tuple(
            WizardStepResult(item.step, status, message.strip())
            if item.step is step
            else item
            for item in state.steps
        )
    )
