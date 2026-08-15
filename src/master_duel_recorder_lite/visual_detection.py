from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import struct
from typing import Protocol

from .frame_capture import FrameSample


DETECTOR_ID = "mdrl.master-duel-ui"
DETECTOR_VERSION = "3"
MAX_NORMALIZED_WIDTH = 640
MAX_NORMALIZED_HEIGHT = 360
TIMELINE_EVENT_TYPES = {"duel_start", "turn_change", "duel_result"}
CONTROL_EVENT_TYPES = {
    "duel_boundary",
    "duel_confirmed",
    "match_error",
    "replay_detected",
}


class MasterDuelState(str, Enum):
    IDLE = "idle"
    MATCHMAKING = "matchmaking"
    COIN_TOSS_CANDIDATE = "coin_toss_candidate"
    TURN_ORDER_CONFIRMED = "turn_order_confirmed"
    DUEL_ACTIVE = "duel_active"
    OVERLAY = "overlay"
    RESULT = "result"
    MATCH_ERROR = "match_error"
    REPLAY = "replay"


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
    play_order: str | None = None
    evidence: str | None = None

    def __post_init__(self) -> None:
        if self.event_type not in TIMELINE_EVENT_TYPES | CONTROL_EVENT_TYPES:
            raise ValueError(f"未対応の自動判定イベントです: {self.event_type}")
        if self.elapsed_ms < 0:
            raise ValueError("elapsed_msは0以上である必要があります")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidenceは0から1である必要があります")
        if not self.reason.strip() or not self.detector_id.strip() or not self.detector_version.strip():
            raise ValueError("判定理由と検出器情報は空にできません")
        if self.actor not in {None, "self", "opponent", "unknown"}:
            raise ValueError(f"未対応のactorです: {self.actor}")
        if self.outcome not in {None, "win", "loss", "draw", "unknown"}:
            raise ValueError(f"未対応のoutcomeです: {self.outcome}")
        if self.play_order not in {None, "first", "second", "unknown"}:
            raise ValueError(f"未対応のplay_orderです: {self.play_order}")


@dataclass(frozen=True)
class DisplayProfile:
    name: str
    minimum_ratio: float
    maximum_ratio: float

    def accepts(self, width: int, height: int) -> bool:
        ratio = width / height
        return self.minimum_ratio <= ratio <= self.maximum_ratio


STANDARD_PROFILE = DisplayProfile("standard-16:9-window", 1.60, 1.95)
ULTRAWIDE_PROFILE = DisplayProfile("ultrawide-fullscreen", 2.20, 2.50)
DISPLAY_PROFILES = (STANDARD_PROFILE, ULTRAWIDE_PROFILE)


def detect_display_profile(width: int, height: int) -> DisplayProfile | None:
    if width < 160 or height < 90:
        return None
    return next((profile for profile in DISPLAY_PROFILES if profile.accepts(width, height)), None)


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
    coin_toss_score: float = 0.0
    turn_order_score: float = 0.0
    play_order: str = "unknown"
    overlay_score: float = 0.0
    match_error_score: float = 0.0
    replay_score: float = 0.0
    loading_score: float = 0.0
    profile_name: str = "unknown"
    result_evidence: str | None = None


@dataclass(frozen=True)
class ConsensusAgreement:
    event_type: str
    evidence: str | None
    matched: int
    required: int
    window: int


@dataclass(frozen=True)
class FrameAnalysis:
    elapsed_ms: int
    state: MasterDuelState
    profile_name: str
    source_width: int
    source_height: int
    coin_score: float
    board_score: float
    turn_score: float
    turn_order_score: float
    result_score: float
    error_score: float
    replay_score: float
    overlay_score: float
    loading_score: float
    candidates: tuple[DetectionCandidate, ...]
    agreements: tuple[ConsensusAgreement, ...]


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
    profile_name: str = "unknown"

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
    dark_ratio: float = 0.0


@dataclass(frozen=True)
class WhiteSpan:
    ratio: float
    pixel_ratio: float
    center_x: float


def normalize_bmp(frame: FrameSample) -> NormalizedBmp | None:
    """BMPを全画面の縦横比を保ったまま縮小します。

    V0.16までの中央16:9クロップは、3440x1440でLPやリプレイ操作UIを
    捨てていました。V0.17では表示方式を判定し、全画面の相対座標を使います。
    """

    data = frame.data
    if len(data) < 54 or data[:2] != b"BM":
        return None
    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    dib_size = struct.unpack_from("<I", data, 14)[0]
    width, signed_height = struct.unpack_from("<ii", data, 18)
    planes, bits_per_pixel = struct.unpack_from("<HH", data, 26)
    compression = struct.unpack_from("<I", data, 30)[0]
    height = abs(signed_height)
    profile = detect_display_profile(width, height)
    if (
        dib_size < 40
        or profile is None
        or planes != 1
        or bits_per_pixel not in {24, 32}
        or compression != 0
    ):
        return None
    bytes_per_pixel = bits_per_pixel // 8
    stride = ((width * bits_per_pixel + 31) // 32) * 4
    if pixel_offset + stride * height > len(data):
        return None

    scale = min(1.0, MAX_NORMALIZED_WIDTH / width, MAX_NORMALIZED_HEIGHT / height)
    target_width = max(1, round(width * scale))
    target_height = max(1, round(height * scale))
    pixels = bytearray(target_width * target_height * 3)
    top_down = signed_height < 0
    if target_width == width and target_height == height:
        source_view = memoryview(data)
        target_view = memoryview(pixels)
        for target_y in range(height):
            source_y = target_y if top_down else height - 1 - target_y
            source_start = pixel_offset + source_y * stride
            source_row = source_view[source_start : source_start + width * bytes_per_pixel]
            target_start = target_y * width * 3
            target_row = target_view[target_start : target_start + width * 3]
            target_row[0::3] = source_row[2::bytes_per_pixel]
            target_row[1::3] = source_row[1::bytes_per_pixel]
            target_row[2::3] = source_row[0::bytes_per_pixel]
        return NormalizedBmp(width, height, bytes(pixels), profile.name)
    target = 0
    for target_y in range(target_height):
        y = min(height - 1, target_y * height // target_height)
        source_y = y if top_down else height - 1 - y
        row = pixel_offset + source_y * stride
        for target_x in range(target_width):
            x = min(width - 1, target_x * width // target_width)
            source = row + x * bytes_per_pixel
            blue, green, red = data[source : source + 3]
            pixels[target : target + 3] = bytes((red, green, blue))
            target += 3
    return NormalizedBmp(target_width, target_height, bytes(pixels), profile.name)


class MasterDuelUiCueExtractor:
    """日本語版Master Duelの固定UIアンカーを数値特徴へ変換します。

    原本画像やテンプレート画像は保持しません。LP枠、ターン円、中央演出、
    リプレイ操作、エラーダイアログの相対ROIから輝度・色・輪郭だけを算出します。
    """

    def extract(self, frame: FrameSample) -> FrameCues:
        image = normalize_bmp(frame)
        if image is None:
            return FrameCues(False, detail="対応する16:9またはウルトラワイド表示ではありません")

        left_half = _region_features(image, 0.03, 0.10, 0.43, 0.88)
        right_half = _region_features(image, 0.57, 0.10, 0.97, 0.88)
        coin_center = _region_features(image, 0.34, 0.08, 0.66, 0.82)
        center_notice = _region_features(image, 0.28, 0.52, 0.72, 0.73)
        top_lp = _region_features(image, 0.78, 0.00, 1.00, 0.14)
        bottom_lp = _region_features(image, 0.00, 0.84, 0.23, 1.00)
        phase = _region_features(image, 0.66, 0.33, 0.77, 0.58)
        board = _region_features(image, 0.18, 0.08, 0.82, 0.88)
        central_banner = _region_features(image, 0.12, 0.34, 0.88, 0.63)
        result_span = _white_span(image, 0.12, 0.34, 0.88, 0.61)
        lower_result_span = _white_span(image, 0.20, 0.48, 0.80, 0.72)
        replay_controls = _region_features(image, 0.69, 0.88, 0.84, 1.00)
        replay_span = _white_span(image, 0.69, 0.88, 0.84, 1.00)
        loading_span = _white_span(image, 0.72, 0.78, 1.00, 1.00)
        loading_region = _region_features(image, 0.72, 0.78, 1.00, 1.00)
        error_header = _region_features(image, 0.42, 0.30, 0.58, 0.43)
        error_panel = _region_features(image, 0.24, 0.27, 0.76, 0.73)

        coin_toss_score = min(
            1.0,
            0.50 * _scaled(coin_center.blue_ratio, 0.18, 0.30)
            + 0.30 * _scaled(coin_center.edge_density, 0.16, 0.23)
            + 0.20 * _inverse_scaled(top_lp.bright_ratio, 0.06, 0.015),
        )

        lp_score = min(
            _scaled(top_lp.bright_ratio, 0.018, 0.045),
            _scaled(bottom_lp.bright_ratio, 0.018, 0.040),
        )
        phase_score = 0.55 * _scaled(phase.edge_density, 0.06, 0.22) + 0.45 * _scaled(
            max(phase.red_ratio, phase.blue_ratio), 0.015, 0.15
        )
        board_score = min(
            1.0,
            0.62 * lp_score
            + 0.20 * phase_score
            + 0.18 * _scaled(board.edge_density, 0.07, 0.20),
        )

        actor = "unknown"
        if board_score >= 0.50:
            actor = "opponent" if phase.red_ratio > phase.blue_ratio * 1.30 + 0.015 else "self"
        play_order = "unknown"
        if board_score >= 0.50 and actor != "unknown":
            play_order = "first" if actor == "self" else "second"

        notice_span = _white_span(image, 0.28, 0.52, 0.72, 0.73)
        turn_order_score = min(
            coin_toss_score,
            _scaled(center_notice.dark_ratio, 0.52, 0.58),
            0.55 * _scaled(center_notice.dark_ratio, 0.25, 0.75)
            + 0.45 * _scaled(notice_span.pixel_ratio, 0.005, 0.045),
        )

        loss_shape = _scaled(result_span.pixel_ratio, 0.035, 0.10)
        win_shape = _scaled(result_span.pixel_ratio, 0.13, 0.18)
        loss_width = _scaled(result_span.ratio, 0.20, 0.28) * _inverse_scaled(
            result_span.ratio, 0.40, 0.34
        )
        win_width = _scaled(result_span.ratio, 0.62, 0.70) * _inverse_scaled(
            result_span.ratio, 0.90, 0.82
        )
        result_score = min(
            1.0,
            max(
                loss_shape * (0.25 + 0.75 * loss_width),
                win_shape * (0.25 + 0.75 * win_width),
            ),
        )
        flash_victory = (
            _scaled(result_span.ratio, 0.90, 0.98)
            * _scaled(result_span.pixel_ratio, 0.25, 0.45)
            * _inverse_scaled(abs(result_span.center_x - 0.5), 0.18, 0.08)
        )
        result_score = max(result_score, flash_victory)
        lower_loss_score = (
            _ultrawide_lower_loss_score(lower_result_span)
            if image.profile_name == ULTRAWIDE_PROFILE.name
            else 0.0
        )
        result_score = max(result_score, lower_loss_score)
        if board_score >= 0.35:
            result_score = 0.0
        outcome = "unknown"
        result_evidence: str | None = None
        if lower_loss_score >= 0.95 and result_score >= 0.95:
            outcome = "loss"
            result_evidence = "ultrawide-lower-loss"
        elif result_score >= 0.5:
            outcome = "win" if result_span.ratio >= 0.60 else "loss"

        turn_shape = _scaled(result_span.ratio, 0.38, 0.46) * _inverse_scaled(
            result_span.ratio, 0.60, 0.54
        )
        turn_score = min(
            1.0,
            board_score
            * (0.60 * turn_shape + 0.40 * _scaled(central_banner.blue_ratio, 0.025, 0.15)),
        )
        if result_score >= 0.55:
            turn_score = 0.0

        replay_controls_score = min(
            1.0,
            0.40 * _scaled(replay_controls.bright_ratio, 0.015, 0.055)
            + 0.35 * _scaled(replay_controls.edge_density, 0.08, 0.16)
            + 0.25 * _scaled(replay_span.ratio, 0.30, 0.50),
        )
        replay_list_score = min(
            1.0,
            0.50 * _scaled(left_half.red_ratio, 0.075, 0.14)
            + 0.35 * _scaled(left_half.edge_density, 0.18, 0.28)
            + 0.15 * _scaled(right_half.dark_ratio, 0.78, 0.92),
        )
        replay_score = max(replay_controls_score, replay_list_score)
        if image.profile_name == ULTRAWIDE_PROFILE.name:
            replay_score *= 0.95

        loading_score = min(
            _scaled(loading_span.ratio, 0.20, 0.35),
            _scaled(loading_region.dark_ratio, 0.85, 0.93),
        )

        dialog_geometry = min(
            _inverse_scaled(error_header.edge_density, 0.18, 0.10),
            _inverse_scaled(error_panel.edge_density, 0.19, 0.10),
        )
        match_error_score = min(
            1.0,
            0.40 * _scaled(error_header.red_ratio, 0.018, 0.050)
            + 0.35 * dialog_geometry
            + 0.25 * _scaled(error_panel.dark_ratio, 0.62, 0.82),
        )
        match_error_score *= _inverse_scaled(error_panel.bright_ratio, 0.030, 0.015)
        if board_score >= 0.50:
            match_error_score *= 0.25

        overlay_score = 0.0
        if 0.18 <= board_score < 0.70:
            overlay_score = min(1.0, 0.55 * lp_score + 0.45 * _scaled(board.edge_density, 0.06, 0.18))

        detail = (
            f"profile={image.profile_name}, coin={coin_toss_score:.2f}, "
            f"board={board_score:.2f}, turn={turn_score:.2f}, "
            f"result={result_score:.2f}, error={match_error_score:.2f}, "
            f"replay={replay_score:.2f}, loading={loading_score:.2f}"
        )
        return FrameCues(
            True,
            start_score=max(coin_toss_score, board_score),
            turn_score=turn_score,
            result_score=result_score,
            actor=actor,
            outcome=outcome,
            detail=detail,
            start_animation_score=coin_toss_score,
            board_score=board_score,
            coin_toss_score=coin_toss_score,
            turn_order_score=turn_order_score,
            play_order=play_order,
            overlay_score=overlay_score,
            match_error_score=match_error_score,
            replay_score=replay_score,
            loading_score=loading_score,
            profile_name=image.profile_name,
            result_evidence=result_evidence,
        )


class BmpRoiCueExtractor(MasterDuelUiCueExtractor):
    """V0.16の公開名を維持する互換エイリアスです。実装は固有UI判定です。"""


class CueEventDetector(Protocol):
    def detect(self, cues: FrameCues, elapsed_ms: int) -> DetectionCandidate | None: ...


class DuelStartDetector:
    def __init__(
        self,
        *,
        maximum_transition_ms: int = 45_000,
        board_only_minimum_score: float = 0.70,
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
        self._coin_elapsed_ms: int | None = None

    def detect(self, cues: FrameCues, elapsed_ms: int) -> DetectionCandidate | None:
        if (
            cues.replay_score >= 0.65
            or cues.match_error_score >= 0.70
            or cues.result_score >= 0.55
            or cues.loading_score >= 0.65
        ):
            return None
        coin_score = max(cues.coin_toss_score, cues.start_animation_score)
        if coin_score >= 0.62 and cues.turn_order_score >= 0.50:
            self._coin_elapsed_ms = elapsed_ms
            return _cue_candidate(
                "duel_start",
                elapsed_ms,
                min(1.0, 0.25 + coin_score * 0.75),
                replace(cues, detail=f"Master Duelコイントス, {cues.detail}"),
                evidence="coin",
            )
        if self._coin_elapsed_ms is not None and elapsed_ms - self._coin_elapsed_ms > self.maximum_transition_ms:
            self._coin_elapsed_ms = None
        if (
            cues.board_score < self.board_only_minimum_score
            or cues.overlay_score > self.board_only_maximum_overlay_score
            or cues.result_score > self.board_only_maximum_overlay_score
        ):
            return None
        return _cue_candidate(
            "duel_start",
            elapsed_ms,
            min(1.0, (cues.board_score + 0.75) / 2),
            replace(cues, detail=f"LP・ターンUIを持つ対戦盤面, {cues.detail}"),
            evidence="board",
        )


class DuelConfirmationDetector:
    def detect(self, cues: FrameCues, elapsed_ms: int) -> DetectionCandidate | None:
        # 先後メッセージは文字形だけでは選択肢表示と確定表示を区別しにくいため、
        # V0.17では最初のLP・ターンUI成立を確定条件にします。
        if cues.board_score < 0.50 or cues.play_order not in {"first", "second"}:
            return None
        confidence = min(1.0, 0.40 + 0.60 * cues.board_score)
        return _cue_candidate(
            "duel_confirmed",
            elapsed_ms,
            confidence,
            replace(cues, detail=f"先後表示または対戦盤面を確認, {cues.detail}"),
            actor=cues.actor,
            play_order=cues.play_order,
            evidence="board",
        )


class TurnChangeDetector:
    def __init__(self) -> None:
        self._active_frames = 0
        self._active_actor: str | None = None
        self._last_score = 0.0

    def detect(self, cues: FrameCues, elapsed_ms: int) -> DetectionCandidate | None:
        actor = cues.actor if cues.actor in {"self", "opponent"} else None
        if cues.turn_score >= 0.36 and actor is not None:
            if actor != self._active_actor:
                self._active_frames = 0
            self._active_actor = actor
            self._last_score = cues.turn_score
            self._active_frames += 1
        elif self._active_frames == 1 and self._active_actor is not None:
            # TURN CHANGE is shorter than the 2fps sampling interval in some recordings.
            # Carry one frame so the temporal consensus still requires two observations.
            actor = self._active_actor
            self._active_frames += 1
        else:
            self._active_frames = 0
            self._active_actor = None
            self._last_score = 0.0
            return None
        if self._active_frames > 3:
            return None
        confidence = min(1.0, 0.50 + 0.55 * self._last_score)
        return _cue_candidate("turn_change", elapsed_ms, confidence, cues, actor=actor)


class DuelResultDetector:
    def detect(self, cues: FrameCues, elapsed_ms: int) -> DetectionCandidate | None:
        return _cue_candidate(
            "duel_result",
            elapsed_ms,
            cues.result_score,
            cues,
            outcome=cues.outcome,
            evidence=(
                cues.result_evidence
                or ("result-near-board" if cues.board_score >= 0.30 else "result-clean")
            ),
        )


class MatchErrorDetector:
    def detect(self, cues: FrameCues, elapsed_ms: int) -> DetectionCandidate | None:
        if cues.match_error_score < 0.70:
            return None
        return _cue_candidate("match_error", elapsed_ms, cues.match_error_score, cues)


class ReplayDetector:
    def detect(self, cues: FrameCues, elapsed_ms: int) -> DetectionCandidate | None:
        if cues.replay_score < 0.80:
            return None
        return _cue_candidate("replay_detected", elapsed_ms, cues.replay_score, cues)


class MasterDuelUiStateMachine:
    """Tracks screen state independently from event consensus and recording state."""

    def __init__(self) -> None:
        self.state = MasterDuelState.IDLE

    def observe(self, cues: FrameCues) -> MasterDuelState:
        if not cues.layout_valid:
            return self.state
        if cues.replay_score >= 0.80:
            self.state = MasterDuelState.REPLAY
        elif cues.match_error_score >= 0.70 and self.state not in {
            MasterDuelState.DUEL_ACTIVE,
            MasterDuelState.OVERLAY,
            MasterDuelState.RESULT,
        }:
            self.state = MasterDuelState.MATCH_ERROR
        elif cues.result_score >= 0.55 and self.state in {
            MasterDuelState.TURN_ORDER_CONFIRMED,
            MasterDuelState.DUEL_ACTIVE,
            MasterDuelState.OVERLAY,
        }:
            self.state = MasterDuelState.RESULT
        elif cues.loading_score >= 0.65:
            self.state = MasterDuelState.MATCHMAKING
        elif (
            max(cues.coin_toss_score, cues.start_animation_score) >= 0.62
            and cues.turn_order_score >= 0.50
        ):
            self.state = MasterDuelState.COIN_TOSS_CANDIDATE
        elif cues.board_score >= 0.50:
            if self.state in {
                MasterDuelState.COIN_TOSS_CANDIDATE,
                MasterDuelState.MATCHMAKING,
                MasterDuelState.IDLE,
            }:
                self.state = MasterDuelState.TURN_ORDER_CONFIRMED
            else:
                self.state = MasterDuelState.DUEL_ACTIVE
        elif cues.overlay_score >= 0.35 and self.state in {
            MasterDuelState.TURN_ORDER_CONFIRMED,
            MasterDuelState.DUEL_ACTIVE,
            MasterDuelState.OVERLAY,
        }:
            self.state = MasterDuelState.OVERLAY
        elif self.state is MasterDuelState.IDLE:
            self.state = MasterDuelState.MATCHMAKING
        return self.state


class MasterDuelVisualEventDetector:
    detector_id = DETECTOR_ID
    detector_version = DETECTOR_VERSION

    def __init__(
        self,
        extractor: VisualCueExtractor | None = None,
        detectors: tuple[CueEventDetector, ...] | None = None,
    ) -> None:
        self.extractor = extractor or MasterDuelUiCueExtractor()
        self.state_machine = MasterDuelUiStateMachine()
        self.detectors = detectors or (
            ReplayDetector(),
            MatchErrorDetector(),
            DuelStartDetector(),
            DuelConfirmationDetector(),
            TurnChangeDetector(),
            DuelResultDetector(),
        )

    def detect(self, frame: FrameSample, elapsed_ms: int) -> tuple[DetectionCandidate, ...]:
        return self.analyze_cues(frame, elapsed_ms)[2]

    def analyze_cues(
        self,
        frame: FrameSample,
        elapsed_ms: int,
    ) -> tuple[FrameCues, MasterDuelState, tuple[DetectionCandidate, ...]]:
        cues = self.extractor.extract(frame)
        if not cues.layout_valid:
            return cues, self.state_machine.state, ()
        state = self.state_machine.observe(cues)
        candidates = tuple(
            candidate
            for detector in self.detectors
            if (candidate := detector.detect(cues, elapsed_ms)) is not None
        )
        return cues, state, candidates


def _cue_candidate(
    event_type: str,
    elapsed_ms: int,
    confidence: float,
    cues: FrameCues,
    *,
    actor: str | None = None,
    outcome: str | None = None,
    play_order: str | None = None,
    evidence: str | None = None,
) -> DetectionCandidate | None:
    if confidence < 0.5:
        return None
    return DetectionCandidate(
        event_type=event_type,
        elapsed_ms=elapsed_ms,
        confidence=confidence,
        reason=f"Master Duel固有UIが{event_type}と一致しました ({cues.detail})",
        detector_id=DETECTOR_ID,
        detector_version=DETECTOR_VERSION,
        actor=actor,
        outcome=outcome,
        play_order=play_order,
        evidence=evidence,
    )


@dataclass
class _ConsensusTrack:
    samples: list[DetectionCandidate | None]
    last_elapsed_ms: int
    required: int
    window: int


class TemporalEventConsensus:
    _SINGLE_FRAME_RESULT_CONFIDENCE = 0.95
    _NEXT_DUEL_BOUNDARY_DELAY_MS = 15_000

    def __init__(
        self,
        *,
        minimum_confidence: float = 0.70,
        confirmations: int = 2,
        maximum_gap_ms: int = 2500,
        turn_cooldown_ms: int = 8000,
        assume_started: bool = False,
        source: str = "live",
    ) -> None:
        if not 0.70 <= minimum_confidence <= 1:
            raise ValueError("自動判定の信頼度閾値は0.70から1.0である必要があります")
        if confirmations < 2:
            raise ValueError("自動判定は2フレーム以上の合意が必要です")
        if maximum_gap_ms < 1 or turn_cooldown_ms < 1:
            raise ValueError("時間合意とクールダウンは正数である必要があります")
        if source not in {"live", "replay"}:
            raise ValueError("sourceはliveまたはreplayである必要があります")
        self.minimum_confidence = minimum_confidence
        self.confirmations = confirmations
        self.maximum_gap_ms = maximum_gap_ms
        self.turn_cooldown_ms = turn_cooldown_ms
        self.source = source
        self._tracks: dict[tuple[str, str | None, str | None, str | None], _ConsensusTrack] = {}
        self._started = assume_started or source == "replay"
        self._confirmed = source == "replay"
        self._resulted = False
        self._errored = False
        self._replay = source == "replay"
        self._last_turn_elapsed_ms: int | None = None
        self._last_turn_actor: str | None = None
        self._confirmed_elapsed_ms: int | None = None
        self.state = (
            MasterDuelState.REPLAY
            if source == "replay"
            else MasterDuelState.COIN_TOSS_CANDIDATE
            if assume_started
            else MasterDuelState.IDLE
        )

    def process(self, candidates: tuple[DetectionCandidate, ...]) -> tuple[DetectionCandidate, ...]:
        viable = [
            normalized
            for item in candidates
            if item.confidence >= self.minimum_confidence
            for normalized in (self._normalize_candidate(item),)
        ]
        visible = {_candidate_key(item): item for item in viable}
        for key, track in tuple(self._tracks.items()):
            if key in visible:
                continue
            track.samples.append(None)
            del track.samples[:-track.window]

        emitted: list[DetectionCandidate] = []
        for candidate in sorted(viable, key=lambda item: _event_priority(item.event_type)):
            if not self._state_allows(candidate):
                continue
            key = _candidate_key(candidate)
            track = self._tracks.get(key)
            if track is None or candidate.elapsed_ms - track.last_elapsed_ms > self.maximum_gap_ms:
                required, window = self._policy_for(candidate)
                track = _ConsensusTrack([candidate], candidate.elapsed_ms, required, window)
            else:
                track.last_elapsed_ms = candidate.elapsed_ms
                track.samples.append(candidate)
                del track.samples[:-track.window]
            self._tracks[key] = track
            matches = [item for item in track.samples if item is not None]
            if len(matches) < track.required:
                continue
            latest = matches[-1]
            accepted = replace(
                latest,
                elapsed_ms=matches[0].elapsed_ms,
                confidence=min(1.0, sum(item.confidence for item in matches) / len(matches)),
                reason=f"直近{track.window}フレーム中{len(matches)}件合意: {latest.reason}",
            )
            emitted.append(accepted)
            del self._tracks[key]
            self._mark_emitted(accepted)
            if accepted.event_type == "duel_confirmed":
                initial_turn = replace(
                    accepted,
                    event_type="turn_change",
                    reason=f"盤面成立時のTurn 1: {accepted.reason}",
                )
                emitted.append(initial_turn)
                self._mark_emitted(initial_turn)
        return tuple(emitted)

    @property
    def agreements(self) -> tuple[ConsensusAgreement, ...]:
        return tuple(
            ConsensusAgreement(
                event_type=key[0],
                evidence=key[3],
                matched=sum(item is not None for item in track.samples),
                required=track.required,
                window=track.window,
            )
            for key, track in sorted(self._tracks.items(), key=lambda item: repr(item[0]))
        )

    def _policy_for(self, candidate: DetectionCandidate) -> tuple[int, int]:
        # Attack and summon effects can briefly form a centered white shape that
        # resembles LOSE. A real loss screen persists; require the stricter
        # window regardless of confidence or board visibility.
        if (
            candidate.event_type == "duel_result"
            and candidate.outcome == "loss"
            and candidate.evidence == "ultrawide-lower-loss"
            and candidate.confidence >= self._SINGLE_FRAME_RESULT_CONFIDENCE
        ):
            return 1, 1
        if candidate.event_type == "duel_result" and candidate.outcome == "loss":
            return 4, 5
        if (
            candidate.event_type == "duel_result"
            and candidate.evidence == "result-near-board"
        ):
            return 4, 5
        if (
            candidate.event_type == "duel_result"
            and candidate.confidence >= self._SINGLE_FRAME_RESULT_CONFIDENCE
            and candidate.outcome in {"win", "loss", "draw"}
        ):
            return 1, 1
        if candidate.event_type == "duel_boundary":
            return 2, 4
        if candidate.event_type == "duel_start" and candidate.evidence == "coin":
            return 2, 4
        if candidate.event_type in {"duel_confirmed"} or (
            candidate.event_type == "duel_start" and candidate.evidence == "board"
        ):
            return 3, 5
        if candidate.event_type == "match_error":
            return 3, 5
        if candidate.event_type in {"duel_result", "replay_detected", "turn_change"}:
            return 2, 4
        return self.confirmations, max(self.confirmations, 4)

    def _state_allows(self, candidate: DetectionCandidate) -> bool:
        if candidate.event_type == "replay_detected":
            return not self._confirmed and not self._resulted and not self._replay
        if candidate.event_type == "match_error":
            return (
                not self._confirmed
                and not self._resulted
                and not self._replay
                and not self._errored
            )
        if candidate.event_type == "duel_start":
            return not self._started and not self._resulted and not self._replay and not self._errored
        if candidate.event_type == "duel_boundary":
            return self._confirmed and not self._resulted and not self._replay and not self._errored
        if candidate.event_type == "duel_confirmed":
            return (
                self._started
                and not self._confirmed
                and not self._resulted
                and not self._replay
                and not self._errored
            )
        if candidate.event_type == "turn_change":
            if not self._confirmed or self._resulted:
                return False
            if self._last_turn_actor is not None and candidate.actor == self._last_turn_actor:
                return False
            return self._last_turn_elapsed_ms is None or candidate.elapsed_ms - self._last_turn_elapsed_ms >= self.turn_cooldown_ms
        return self._confirmed and not self._resulted

    def _normalize_candidate(self, candidate: DetectionCandidate) -> DetectionCandidate:
        if (
            candidate.event_type == "duel_start"
            and candidate.evidence == "coin"
            and self._confirmed
            and self._confirmed_elapsed_ms is not None
            and candidate.elapsed_ms - self._confirmed_elapsed_ms
            >= self._NEXT_DUEL_BOUNDARY_DELAY_MS
        ):
            return replace(
                candidate,
                event_type="duel_boundary",
                outcome=None,
                evidence="next_duel",
                reason=(
                    "盤面確定後に次のコイントスを検出したため、"
                    "前の対戦結果を取り逃した録画境界と判定しました"
                ),
            )
        return candidate

    def _mark_emitted(self, candidate: DetectionCandidate) -> None:
        if candidate.event_type == "replay_detected":
            self._replay = True
            self.state = MasterDuelState.REPLAY
        elif candidate.event_type == "duel_start":
            self._started = True
            self.state = MasterDuelState.COIN_TOSS_CANDIDATE
        elif candidate.event_type == "duel_confirmed":
            self._confirmed = True
            self._confirmed_elapsed_ms = candidate.elapsed_ms
            self.state = MasterDuelState.DUEL_ACTIVE
        elif candidate.event_type == "turn_change":
            self._last_turn_elapsed_ms = candidate.elapsed_ms
            self._last_turn_actor = candidate.actor
        elif candidate.event_type == "duel_result":
            self._resulted = True
            self.state = MasterDuelState.RESULT
        elif candidate.event_type == "duel_boundary":
            self._resulted = True
            self.state = MasterDuelState.RESULT
        elif candidate.event_type == "match_error":
            self._errored = True
            self.state = MasterDuelState.MATCH_ERROR


class VisualDetectionPipeline:
    def __init__(
        self,
        detector: VisualEventDetector | None = None,
        consensus: TemporalEventConsensus | None = None,
    ) -> None:
        self.detector = detector or MasterDuelVisualEventDetector()
        self.consensus = consensus or TemporalEventConsensus()

    def analyze(self, frame: FrameSample, elapsed_ms: int) -> tuple[DetectionCandidate, ...]:
        return self.analyze_frame(frame, elapsed_ms).candidates

    def analyze_frame(self, frame: FrameSample, elapsed_ms: int) -> FrameAnalysis:
        if isinstance(self.detector, MasterDuelVisualEventDetector):
            cues, detected_state, raw_candidates = self.detector.analyze_cues(frame, elapsed_ms)
        else:
            cues = FrameCues(False)
            detected_state = self.consensus.state
            raw_candidates = self.detector.detect(frame, elapsed_ms)
        candidates = self.consensus.process(raw_candidates)
        state = self.consensus.state if candidates or self.consensus.state is not MasterDuelState.IDLE else detected_state
        return FrameAnalysis(
            elapsed_ms=elapsed_ms,
            state=state,
            profile_name=cues.profile_name,
            source_width=frame.width,
            source_height=frame.height,
            coin_score=cues.coin_toss_score,
            board_score=cues.board_score,
            turn_score=cues.turn_score,
            turn_order_score=cues.turn_order_score,
            result_score=cues.result_score,
            error_score=cues.match_error_score,
            replay_score=cues.replay_score,
            overlay_score=cues.overlay_score,
            loading_score=cues.loading_score,
            candidates=candidates,
            agreements=self.consensus.agreements,
        )


def _candidate_key(
    candidate: DetectionCandidate,
) -> tuple[str, str | None, str | None, str | None]:
    return candidate.event_type, candidate.actor, candidate.outcome, candidate.evidence


def _event_priority(event_type: str) -> int:
    return {
        "replay_detected": 0,
        "match_error": 1,
        "duel_start": 2,
        "duel_confirmed": 3,
        "duel_boundary": 4,
        "turn_change": 5,
        "duel_result": 6,
    }[event_type]


def _scaled(value: float, minimum: float, maximum: float) -> float:
    if value <= minimum:
        return 0.0
    if value >= maximum:
        return 1.0
    return (value - minimum) / (maximum - minimum)


def _inverse_scaled(value: float, maximum: float, minimum: float) -> float:
    return 1.0 - _scaled(value, minimum, maximum)


def _region_bounds(
    image: NormalizedBmp,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> tuple[int, int, int, int]:
    x0 = max(0, min(image.width - 1, round(image.width * left)))
    y0 = max(0, min(image.height - 1, round(image.height * top)))
    x1 = max(x0 + 1, min(image.width, round(image.width * right)))
    y1 = max(y0 + 1, min(image.height, round(image.height * bottom)))
    return x0, y0, x1, y1


def _region_features(
    image: NormalizedBmp,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> RegionFeatures:
    x0, y0, x1, y1 = _region_bounds(image, left, top, right, bottom)
    step_x = max(1, (x1 - x0) // 80)
    step_y = max(1, (y1 - y0) // 45)
    lumas: list[tuple[int, int, float]] = []
    bright = red = blue = dark = 0
    for y in range(y0, y1, step_y):
        for x in range(x0, x1, step_x):
            r, g, b = image.rgb(x, y)
            luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
            lumas.append((x, y, luma))
            bright += luma >= 190
            dark += luma <= 45
            red += r >= 100 and r > b * 1.25 and r > g * 1.15
            blue += b >= 100 and b > r * 1.25 and b > g * 1.05
    if not lumas:
        return RegionFeatures(0, 0, 0, 0, 0, 0)
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
        dark_ratio=dark / count,
    )


def _white_span(
    image: NormalizedBmp,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> WhiteSpan:
    x0, y0, x1, y1 = _region_bounds(image, left, top, right, bottom)
    step_x = max(1, (x1 - x0) // 160)
    step_y = max(1, (y1 - y0) // 60)
    columns: dict[int, int] = {}
    total = white = 0
    for y in range(y0, y1, step_y):
        for x in range(x0, x1, step_x):
            total += 1
            r, g, b = image.rgb(x, y)
            if min(r, g, b) >= 165 and max(r, g, b) - min(r, g, b) <= 65:
                white += 1
                columns[x] = columns.get(x, 0) + 1
    minimum_column_pixels = max(2, (y1 - y0) // step_y // 14)
    occupied = [x for x, count in columns.items() if count >= minimum_column_pixels]
    if not occupied or total == 0:
        return WhiteSpan(0.0, 0.0, 0.5)
    roi_width = max(1, x1 - x0)
    return WhiteSpan(
        ratio=(max(occupied) - min(occupied) + step_x) / roi_width,
        pixel_ratio=white / total,
        center_x=((min(occupied) + max(occupied)) / 2 - x0) / roi_width,
    )


def _ultrawide_lower_loss_score(span: WhiteSpan) -> float:
    return min(
        _scaled(span.pixel_ratio, 0.055, 0.085),
        _inverse_scaled(span.pixel_ratio, 0.130, 0.115),
        _scaled(span.ratio, 0.28, 0.36),
        _inverse_scaled(span.ratio, 0.55, 0.48),
        _inverse_scaled(abs(span.center_x - 0.5), 0.10, 0.05),
    )
