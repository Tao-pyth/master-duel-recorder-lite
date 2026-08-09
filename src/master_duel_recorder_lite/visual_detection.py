from __future__ import annotations

from dataclasses import dataclass, replace
import struct
from typing import Protocol

from .frame_capture import FrameSample


DETECTOR_ID = "mdrl.roi-features"
DETECTOR_VERSION = "1"
MAX_NORMALIZED_WIDTH = 640
MAX_NORMALIZED_HEIGHT = 360


@dataclass(frozen=True)
class DetectionCandidate:
    event_type: str
    elapsed_ms: int
    confidence: float
    reason: str
    detector_id: str
    detector_version: str
    actor: str | None = None
    outcome: str | None = None

    def __post_init__(self) -> None:
        if self.event_type not in {"duel_start", "turn_change", "duel_result"}:
            raise ValueError(f"未対応の自動判定イベントです: {self.event_type}")
        if self.elapsed_ms < 0:
            raise ValueError("elapsed_msは0以上である必要があります")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidenceは0から1である必要があります")
        if not self.reason.strip() or not self.detector_id.strip() or not self.detector_version.strip():
            raise ValueError("判定理由と検出器情報は空にできません")


@dataclass(frozen=True)
class FrameCues:
    layout_valid: bool
    start_score: float = 0.0
    turn_score: float = 0.0
    result_score: float = 0.0
    actor: str = "unknown"
    outcome: str = "unknown"
    detail: str = ""
    start_animation_score: float = 0.0
    board_score: float = 0.0


class VisualCueExtractor(Protocol):
    def extract(self, frame: FrameSample) -> FrameCues: ...


class VisualEventDetector(Protocol):
    detector_id: str
    detector_version: str

    def detect(self, frame: FrameSample, elapsed_ms: int) -> tuple[DetectionCandidate, ...]: ...


@dataclass(frozen=True)
class NormalizedBmp:
    width: int
    height: int
    pixels: bytes

    def rgb(self, x: int, y: int) -> tuple[int, int, int]:
        offset = (y * self.width + x) * 3
        return self.pixels[offset], self.pixels[offset + 1], self.pixels[offset + 2]


@dataclass(frozen=True)
class RegionFeatures:
    mean_luma: float
    bright_ratio: float
    edge_density: float
    red_ratio: float
    blue_ratio: float


def normalize_bmp(frame: FrameSample) -> NormalizedBmp | None:
    data = frame.data
    if len(data) < 54 or data[:2] != b"BM":
        return None
    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    dib_size = struct.unpack_from("<I", data, 14)[0]
    width, signed_height = struct.unpack_from("<ii", data, 18)
    planes, bits_per_pixel = struct.unpack_from("<HH", data, 26)
    compression = struct.unpack_from("<I", data, 30)[0]
    if (
        dib_size < 40
        or width < 160
        or abs(signed_height) < 90
        or planes != 1
        or bits_per_pixel not in {24, 32}
        or compression != 0
    ):
        return None
    height = abs(signed_height)
    bytes_per_pixel = bits_per_pixel // 8
    stride = ((width * bits_per_pixel + 31) // 32) * 4
    if pixel_offset + stride * height > len(data):
        return None

    source_ratio = width / height
    target_ratio = 16 / 9
    if source_ratio >= target_ratio:
        crop_height = height
        crop_width = round(height * target_ratio)
        crop_x = (width - crop_width) // 2
        crop_y = 0
    else:
        crop_width = width
        crop_height = round(width / target_ratio)
        crop_x = 0
        crop_y = (height - crop_height) // 2
    if crop_width < 160 or crop_height < 90:
        return None

    scale = min(
        1.0,
        MAX_NORMALIZED_WIDTH / crop_width,
        MAX_NORMALIZED_HEIGHT / crop_height,
    )
    target_width = max(1, round(crop_width * scale))
    target_height = max(1, round(crop_height * scale))
    pixels = bytearray(target_width * target_height * 3)
    top_down = signed_height < 0
    target = 0
    for target_y in range(target_height):
        y = crop_y + min(crop_height - 1, target_y * crop_height // target_height)
        source_y = y if top_down else height - 1 - y
        row = pixel_offset + source_y * stride
        for target_x in range(target_width):
            x = crop_x + min(crop_width - 1, target_x * crop_width // target_width)
            source = row + x * bytes_per_pixel
            blue, green, red = data[source : source + 3]
            pixels[target : target + 3] = bytes((red, green, blue))
            target += 3
    return NormalizedBmp(target_width, target_height, bytes(pixels))


class BmpRoiCueExtractor:
    """言語やゲーム画像テンプレートに依存しない軽量なROI特徴抽出器です。"""

    def extract(self, frame: FrameSample) -> FrameCues:
        image = normalize_bmp(frame)
        if image is None:
            return FrameCues(False, detail="16:9表示領域を確定できません")
        board = _region_features(image, 0.05, 0.08, 0.95, 0.92)
        center = _region_features(image, 0.25, 0.22, 0.75, 0.78)
        banner = _region_features(image, 0.12, 0.42, 0.88, 0.58)
        upper = _region_features(image, 0.76, 0.05, 0.97, 0.22)
        lower = _region_features(image, 0.76, 0.78, 0.97, 0.95)

        board_score = _scaled(board.edge_density, 0.02, 0.05)
        center_contrast = min(1.0, abs(center.mean_luma - board.mean_luma) / 90)
        banner_score = max(
            _scaled(banner.bright_ratio, 0.16, 0.58),
            _scaled(max(banner.red_ratio, banner.blue_ratio), 0.12, 0.48),
        )
        result_panel = (
            0.55 * _scaled(center.bright_ratio, 0.22, 0.70)
            + 0.45 * center_contrast
        )
        indicator = max(upper.red_ratio, upper.blue_ratio, lower.red_ratio, lower.blue_ratio)
        start_score = min(1.0, 0.70 * board_score + 0.30 * center_contrast)
        turn_score = min(1.0, 0.70 * banner_score + 0.30 * _scaled(indicator, 0.08, 0.45))
        result_score = min(1.0, result_panel)

        actor = "unknown"
        if lower.blue_ratio + lower.red_ratio > upper.blue_ratio + upper.red_ratio + 0.08:
            actor = "self"
        elif upper.blue_ratio + upper.red_ratio > lower.blue_ratio + lower.red_ratio + 0.08:
            actor = "opponent"
        outcome = "unknown"
        if (
            center.blue_ratio >= 0.15
            and center.red_ratio >= 0.15
            and abs(center.blue_ratio - center.red_ratio) <= 0.10
        ):
            outcome = "draw"
        elif center.blue_ratio > center.red_ratio + 0.15:
            outcome = "win"
        elif center.red_ratio > center.blue_ratio + 0.15:
            outcome = "loss"

        return FrameCues(
            True,
            start_score=start_score,
            turn_score=turn_score,
            result_score=result_score,
            actor=actor,
            outcome=outcome,
            detail=(
                f"board={board_score:.2f}, banner={banner_score:.2f}, "
                f"result={result_panel:.2f}"
            ),
            start_animation_score=max(center_contrast, banner_score),
            board_score=board_score,
        )


class MasterDuelVisualEventDetector:
    detector_id = DETECTOR_ID
    detector_version = DETECTOR_VERSION

    def __init__(
        self,
        extractor: VisualCueExtractor | None = None,
        detectors: tuple["CueEventDetector", ...] | None = None,
    ) -> None:
        self.extractor = extractor or BmpRoiCueExtractor()
        self.detectors = detectors or (
            DuelStartDetector(),
            TurnChangeDetector(),
            DuelResultDetector(),
        )

    def detect(self, frame: FrameSample, elapsed_ms: int) -> tuple[DetectionCandidate, ...]:
        cues = self.extractor.extract(frame)
        if not cues.layout_valid:
            return ()
        return tuple(
            candidate
            for detector in self.detectors
            if (candidate := detector.detect(cues, elapsed_ms)) is not None
        )


class CueEventDetector(Protocol):
    def detect(self, cues: FrameCues, elapsed_ms: int) -> DetectionCandidate | None: ...


class DuelStartDetector:
    def __init__(
        self,
        *,
        maximum_transition_ms: int = 5000,
        board_only_minimum_score: float = 0.85,
        board_only_maximum_overlay_score: float = 0.35,
    ) -> None:
        if maximum_transition_ms < 1:
            raise ValueError("対戦開始遷移の最大時間は正数である必要があります")
        if not 0.60 <= board_only_minimum_score <= 1.0:
            raise ValueError("盤面単独判定の盤面スコアは0.60から1.0である必要があります")
        if not 0.0 <= board_only_maximum_overlay_score < 0.60:
            raise ValueError("盤面単独判定の演出スコアは0.0以上0.60未満である必要があります")
        self.maximum_transition_ms = maximum_transition_ms
        self.board_only_minimum_score = board_only_minimum_score
        self.board_only_maximum_overlay_score = board_only_maximum_overlay_score
        self._animation_elapsed_ms: int | None = None

    def detect(self, cues: FrameCues, elapsed_ms: int) -> DetectionCandidate | None:
        animation_score = cues.start_animation_score
        board_score = cues.board_score
        if animation_score >= 0.60:
            self._animation_elapsed_ms = elapsed_ms
            return None
        if self._animation_elapsed_ms is not None:
            if elapsed_ms - self._animation_elapsed_ms > self.maximum_transition_ms:
                self._animation_elapsed_ms = None
            elif board_score >= 0.60:
                return _cue_candidate(
                    "duel_start",
                    elapsed_ms,
                    min(1.0, (board_score + 0.70) / 2),
                    replace(cues, detail=f"開始演出後の盤面, {cues.detail}"),
                )

        if (
            board_score < self.board_only_minimum_score
            or animation_score > self.board_only_maximum_overlay_score
            or cues.result_score > self.board_only_maximum_overlay_score
        ):
            return None
        return _cue_candidate(
            "duel_start",
            elapsed_ms,
            min(1.0, (board_score + 0.75) / 2),
            replace(cues, detail=f"安定した対戦盤面, {cues.detail}"),
        )


class TurnChangeDetector:
    def __init__(self) -> None:
        self._active_frames = 0

    def detect(self, cues: FrameCues, elapsed_ms: int) -> DetectionCandidate | None:
        if cues.turn_score < 0.5:
            self._active_frames = 0
            return None
        self._active_frames += 1
        if self._active_frames > 3:
            return None
        return _cue_candidate(
            "turn_change", elapsed_ms, cues.turn_score, cues, actor=cues.actor
        )


class DuelResultDetector:
    def detect(self, cues: FrameCues, elapsed_ms: int) -> DetectionCandidate | None:
        return _cue_candidate(
            "duel_result", elapsed_ms, cues.result_score, cues, outcome=cues.outcome
        )


def _cue_candidate(
    event_type: str,
    elapsed_ms: int,
    confidence: float,
    cues: FrameCues,
    *,
    actor: str | None = None,
    outcome: str | None = None,
) -> DetectionCandidate | None:
    if confidence < 0.5:
        return None
    return DetectionCandidate(
        event_type=event_type,
        elapsed_ms=elapsed_ms,
        confidence=confidence,
        reason=f"ROI特徴が{event_type}と一致しました ({cues.detail})",
        detector_id=DETECTOR_ID,
        detector_version=DETECTOR_VERSION,
        actor=actor,
        outcome=outcome,
    )


@dataclass
class _ConsensusTrack:
    count: int
    first_elapsed_ms: int
    last_elapsed_ms: int
    confidence_total: float
    latest: DetectionCandidate


class TemporalEventConsensus:
    def __init__(
        self,
        *,
        minimum_confidence: float = 0.70,
        confirmations: int = 2,
        maximum_gap_ms: int = 2500,
        turn_cooldown_ms: int = 8000,
    ) -> None:
        if not 0.70 <= minimum_confidence <= 1:
            raise ValueError("自動判定の信頼度閾値は0.70から1.0である必要があります")
        if confirmations < 2:
            raise ValueError("自動判定は2フレーム以上の合意が必要です")
        if maximum_gap_ms < 1 or turn_cooldown_ms < 1:
            raise ValueError("時間合意とクールダウンは正数である必要があります")
        self.minimum_confidence = minimum_confidence
        self.confirmations = confirmations
        self.maximum_gap_ms = maximum_gap_ms
        self.turn_cooldown_ms = turn_cooldown_ms
        self._tracks: dict[tuple[str, str | None, str | None], _ConsensusTrack] = {}
        self._started = False
        self._resulted = False
        self._last_turn_elapsed_ms: int | None = None

    def process(
        self, candidates: tuple[DetectionCandidate, ...]
    ) -> tuple[DetectionCandidate, ...]:
        viable = [item for item in candidates if item.confidence >= self.minimum_confidence]
        visible_keys = {_candidate_key(item) for item in viable}
        for key in tuple(self._tracks):
            if key not in visible_keys:
                del self._tracks[key]

        emitted: list[DetectionCandidate] = []
        for candidate in sorted(viable, key=lambda item: _event_priority(item.event_type)):
            if not self._state_allows(candidate):
                continue
            key = _candidate_key(candidate)
            track = self._tracks.get(key)
            if track is None or candidate.elapsed_ms - track.last_elapsed_ms > self.maximum_gap_ms:
                track = _ConsensusTrack(1, candidate.elapsed_ms, candidate.elapsed_ms, candidate.confidence, candidate)
            else:
                track.count += 1
                track.last_elapsed_ms = candidate.elapsed_ms
                track.confidence_total += candidate.confidence
                track.latest = candidate
            self._tracks[key] = track
            if track.count < self.confirmations:
                continue
            accepted = replace(
                track.latest,
                elapsed_ms=track.first_elapsed_ms,
                confidence=min(1.0, track.confidence_total / track.count),
                reason=f"{track.count}フレーム合意: {track.latest.reason}",
            )
            emitted.append(accepted)
            del self._tracks[key]
            self._mark_emitted(accepted)
        return tuple(emitted)

    def _state_allows(self, candidate: DetectionCandidate) -> bool:
        if candidate.event_type == "duel_start":
            return not self._started and not self._resulted
        if candidate.event_type == "turn_change":
            if not self._started or self._resulted:
                return False
            return (
                self._last_turn_elapsed_ms is None
                or candidate.elapsed_ms - self._last_turn_elapsed_ms >= self.turn_cooldown_ms
            )
        return self._started and not self._resulted

    def _mark_emitted(self, candidate: DetectionCandidate) -> None:
        if candidate.event_type == "duel_start":
            self._started = True
        elif candidate.event_type == "turn_change":
            self._last_turn_elapsed_ms = candidate.elapsed_ms
        elif candidate.event_type == "duel_result":
            self._resulted = True


class VisualDetectionPipeline:
    def __init__(
        self,
        detector: VisualEventDetector | None = None,
        consensus: TemporalEventConsensus | None = None,
    ) -> None:
        self.detector = detector or MasterDuelVisualEventDetector()
        self.consensus = consensus or TemporalEventConsensus()

    def analyze(self, frame: FrameSample, elapsed_ms: int) -> tuple[DetectionCandidate, ...]:
        return self.consensus.process(self.detector.detect(frame, elapsed_ms))


def _candidate_key(candidate: DetectionCandidate) -> tuple[str, str | None, str | None]:
    return candidate.event_type, candidate.actor, candidate.outcome


def _event_priority(event_type: str) -> int:
    return {"duel_start": 0, "turn_change": 1, "duel_result": 2}[event_type]


def _scaled(value: float, minimum: float, maximum: float) -> float:
    if value <= minimum:
        return 0.0
    if value >= maximum:
        return 1.0
    return (value - minimum) / (maximum - minimum)


def _region_features(
    image: NormalizedBmp,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> RegionFeatures:
    x0 = max(0, min(image.width - 1, round(image.width * left)))
    y0 = max(0, min(image.height - 1, round(image.height * top)))
    x1 = max(x0 + 1, min(image.width, round(image.width * right)))
    y1 = max(y0 + 1, min(image.height, round(image.height * bottom)))
    step_x = max(1, (x1 - x0) // 80)
    step_y = max(1, (y1 - y0) // 45)
    lumas: list[tuple[int, int, float]] = []
    bright = red = blue = 0
    for y in range(y0, y1, step_y):
        for x in range(x0, x1, step_x):
            r, g, b = image.rgb(x, y)
            luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
            lumas.append((x, y, luma))
            bright += luma >= 190
            red += r >= 100 and r > b * 1.25 and r > g * 1.15
            blue += b >= 100 and b > r * 1.25 and b > g * 1.05
    if not lumas:
        return RegionFeatures(0, 0, 0, 0, 0)
    by_position = {(x, y): value for x, y, value in lumas}
    edges = comparisons = 0
    for x, y, value in lumas:
        for neighbor in ((x + step_x, y), (x, y + step_y)):
            if neighbor in by_position:
                comparisons += 1
                edges += abs(value - by_position[neighbor]) >= 32
    count = len(lumas)
    return RegionFeatures(
        mean_luma=sum(value for _, _, value in lumas) / count,
        bright_ratio=bright / count,
        edge_density=edges / comparisons if comparisons else 0,
        red_ratio=red / count,
        blue_ratio=blue / count,
    )
