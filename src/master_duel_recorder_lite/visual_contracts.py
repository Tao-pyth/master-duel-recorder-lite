from __future__ import annotations

from dataclasses import dataclass

from .visual_detection import FrameAnalysis


@dataclass(frozen=True)
class VisualEventContract:
    event_type: str
    score_field: str
    threshold: float
    required: int
    window: int

    def score(self, analysis: FrameAnalysis) -> float:
        return float(getattr(analysis, self.score_field))

    def evaluate(self, analysis: FrameAnalysis) -> VisualEventEvidence:
        score = self.score(analysis)
        agreement = next(
            (item for item in analysis.agreements if item.event_type == self.event_type),
            None,
        )
        matched = agreement.matched if agreement is not None else 0
        return VisualEventEvidence(
            self.event_type,
            score,
            score >= self.threshold,
            matched,
            agreement.required if agreement is not None else self.required,
            agreement.window if agreement is not None else self.window,
            analysis.profile_name,
            analysis.elapsed_ms,
        )


@dataclass(frozen=True)
class VisualEventEvidence:
    event_type: str
    score: float
    threshold_met: bool
    matched: int
    required: int
    window: int
    profile_name: str
    elapsed_ms: int


EVENT_CONTRACTS = {
    "duel_start": VisualEventContract("duel_start", "coin_score", 0.70, 2, 4),
    "duel_confirmed": VisualEventContract("duel_confirmed", "board_score", 0.70, 3, 5),
    "turn_change": VisualEventContract("turn_change", "turn_score", 0.70, 2, 4),
    "duel_result": VisualEventContract("duel_result", "result_score", 0.70, 2, 4),
}


def evaluate_frame_contracts(
    analysis: FrameAnalysis,
) -> tuple[VisualEventEvidence, ...]:
    return tuple(contract.evaluate(analysis) for contract in EVENT_CONTRACTS.values())
