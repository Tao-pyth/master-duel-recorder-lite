import struct
import unittest
from collections.abc import Callable
from datetime import datetime, timezone

from master_duel_recorder_lite.frame_capture import FrameSample
from master_duel_recorder_lite.visual_detection import (
    DetectionCandidate,
    BmpRoiCueExtractor,
    DuelResultDetector,
    DuelStartDetector,
    FrameCues,
    MasterDuelVisualEventDetector,
    TemporalEventConsensus,
    TurnChangeDetector,
    normalize_bmp,
)


class FakeExtractor:
    def __init__(self, cues: FrameCues) -> None:
        self.cues = cues

    def extract(self, _frame: FrameSample) -> FrameCues:
        return self.cues


def frame(data: bytes, width: int = 160, height: int = 90) -> FrameSample:
    return FrameSample(
        captured_at=datetime.now(timezone.utc),
        window_handle=1,
        window_title="Master Duel",
        width=width,
        height=height,
        pixel_format="bmp",
        data=data,
    )


def bmp(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    return bmp_pixels(width, height, lambda _x, _y: color)


def bmp_pixels(
    width: int,
    height: int,
    pixel: Callable[[int, int], tuple[int, int, int]],
) -> bytes:
    row_size = ((width * 24 + 31) // 32) * 4
    pixels = bytearray(row_size * height)
    for y in range(height):
        for x in range(width):
            red, green, blue = pixel(x, height - 1 - y)
            offset = y * row_size + x * 3
            pixels[offset : offset + 3] = bytes((blue, green, red))
    size = 54 + len(pixels)
    header = bytearray(54)
    header[:2] = b"BM"
    struct.pack_into("<I", header, 2, size)
    struct.pack_into("<I", header, 10, 54)
    struct.pack_into("<I", header, 14, 40)
    struct.pack_into("<ii", header, 18, width, height)
    struct.pack_into("<HH", header, 26, 1, 24)
    struct.pack_into("<I", header, 34, len(pixels))
    return bytes(header + pixels)


def candidate(
    event_type: str,
    elapsed_ms: int,
    confidence: float = 0.8,
    *,
    actor: str | None = None,
    outcome: str | None = None,
) -> DetectionCandidate:
    return DetectionCandidate(
        event_type,
        elapsed_ms,
        confidence,
        "synthetic",
        "test",
        "1",
        actor,
        outcome,
    )


class BmpNormalizationTest(unittest.TestCase):
    def test_letterboxed_bmp_is_cropped_to_sixteen_by_nine(self) -> None:
        normalized = normalize_bmp(frame(bmp(200, 160, (10, 20, 30)), 200, 160))

        self.assertIsNotNone(normalized)
        self.assertEqual((normalized.width, normalized.height), (200, 112))  # type: ignore[union-attr]
        self.assertEqual(normalized.rgb(0, 0), (10, 20, 30))  # type: ignore[union-attr]

    def test_invalid_or_too_small_bmp_is_rejected(self) -> None:
        self.assertIsNone(normalize_bmp(frame(b"not-bmp")))
        self.assertIsNone(normalize_bmp(frame(bmp(80, 45, (0, 0, 0)), 80, 45)))

    def test_large_frame_is_downsampled_to_bounded_sixteen_by_nine(self) -> None:
        normalized = normalize_bmp(
            frame(bmp(800, 600, (10, 20, 30)), width=800, height=600)
        )

        self.assertIsNotNone(normalized)
        self.assertEqual((normalized.width, normalized.height), (640, 360))  # type: ignore[union-attr]
        self.assertEqual(normalized.rgb(639, 359), (10, 20, 30))  # type: ignore[union-attr]


class VisualDetectorTest(unittest.TestCase):
    def test_roi_extractor_detects_synthetic_board_and_turn_regions(self) -> None:
        board_data = bmp_pixels(
            160, 90, lambda x, y: (230, 230, 230) if (x + y) % 2 else (20, 20, 20)
        )
        turn_data = bmp_pixels(
            160,
            90,
            lambda x, y: (
                (240, 240, 240)
                if 38 <= y < 53 and 18 <= x < 142
                else (20, 40, 220)
                if 70 <= y < 86 and 122 <= x < 156
                else (20, 20, 20)
            ),
        )
        extractor = BmpRoiCueExtractor()

        board = extractor.extract(frame(board_data))
        turn = extractor.extract(frame(turn_data))

        self.assertTrue(board.layout_valid)
        self.assertGreaterEqual(board.start_score, 0.70)
        self.assertGreaterEqual(turn.turn_score, 0.70)
        self.assertEqual(turn.actor, "self")

    def test_detector_maps_cues_to_three_supported_events(self) -> None:
        detector = MasterDuelVisualEventDetector(
            FakeExtractor(
                FrameCues(
                    True,
                    detail="synthetic animation",
                    start_animation_score=0.8,
                )
            )
        )

        self.assertEqual(detector.detect(frame(bmp(160, 90, (0, 0, 0))), 1000), ())
        detector.extractor = FakeExtractor(
            FrameCues(
                True,
                turn_score=0.75,
                result_score=0.9,
                actor="self",
                outcome="win",
                detail="synthetic board",
                board_score=0.8,
            )
        )
        detected = detector.detect(frame(bmp(160, 90, (0, 0, 0))), 1234)

        self.assertEqual([item.event_type for item in detected], [
            "duel_start", "turn_change", "duel_result"
        ])
        self.assertEqual(detected[1].actor, "self")
        self.assertEqual(detected[2].outcome, "win")

    def test_start_requires_animation_then_board_transition(self) -> None:
        detector = DuelStartDetector()

        board_only = detector.detect(FrameCues(True, board_score=0.9), 1000)
        animation = detector.detect(FrameCues(True, start_animation_score=0.8), 1500)
        started = detector.detect(FrameCues(True, board_score=0.9), 2000)

        self.assertIsNone(board_only)
        self.assertIsNone(animation)
        self.assertIsNotNone(started)

    def test_start_transition_expires_before_late_board(self) -> None:
        detector = DuelStartDetector(maximum_transition_ms=2000)

        detector.detect(FrameCues(True, start_animation_score=0.8), 1000)
        expired = detector.detect(FrameCues(True, board_score=0.9), 4000)

        self.assertIsNone(expired)

    def test_turn_detector_latches_persistent_animation(self) -> None:
        detector = TurnChangeDetector()
        cues = FrameCues(True, turn_score=0.9, actor="self")

        burst = [detector.detect(cues, index * 500) for index in range(6)]
        detector.detect(FrameCues(True, turn_score=0.0), 3500)
        next_turn = detector.detect(cues, 4000)

        self.assertEqual(sum(item is not None for item in burst), 3)
        self.assertIsNotNone(next_turn)

    def test_unknown_layout_generates_no_candidate(self) -> None:
        detector = MasterDuelVisualEventDetector(FakeExtractor(FrameCues(False)))
        self.assertEqual(detector.detect(frame(b"invalid"), 0), ())

    def test_result_detector_preserves_draw_outcome(self) -> None:
        detected = DuelResultDetector().detect(
            FrameCues(True, result_score=0.8, outcome="draw", detail="synthetic"),
            5000,
        )

        self.assertIsNotNone(detected)
        self.assertEqual(detected.outcome, "draw")  # type: ignore[union-attr]


class TemporalConsensusTest(unittest.TestCase):
    def test_low_confidence_is_discarded_and_first_observation_time_is_used(self) -> None:
        consensus = TemporalEventConsensus(minimum_confidence=0.70)

        self.assertEqual(consensus.process((candidate("duel_start", 100, 0.69),)), ())
        self.assertEqual(consensus.process((candidate("duel_start", 1000),)), ())
        emitted = consensus.process((candidate("duel_start", 1500, 0.9),))

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].elapsed_ms, 1000)
        self.assertAlmostEqual(emitted[0].confidence, 0.85)

    def test_state_order_uniqueness_and_turn_cooldown(self) -> None:
        consensus = TemporalEventConsensus(turn_cooldown_ms=5000)

        self.assertEqual(consensus.process((candidate("turn_change", 1000),)), ())
        consensus.process((candidate("duel_start", 2000),))
        self.assertEqual(len(consensus.process((candidate("duel_start", 2500),))), 1)
        self.assertEqual(consensus.process((candidate("duel_start", 3000),)), ())
        consensus.process((candidate("turn_change", 4000, actor="self"),))
        first_turn = consensus.process((candidate("turn_change", 4500, actor="self"),))
        self.assertEqual(len(first_turn), 1)
        self.assertEqual(consensus.process((candidate("turn_change", 5000, actor="self"),)), ())
        consensus.process((candidate("duel_result", 10000, outcome="unknown"),))
        result = consensus.process((candidate("duel_result", 10500, outcome="unknown"),))
        self.assertEqual(len(result), 1)
        self.assertEqual(consensus.process((candidate("turn_change", 12000),)), ())

    def test_missing_frame_breaks_consecutive_consensus(self) -> None:
        consensus = TemporalEventConsensus()
        consensus.process((candidate("duel_start", 1000),))
        consensus.process(())
        self.assertEqual(consensus.process((candidate("duel_start", 1500),)), ())


if __name__ == "__main__":
    unittest.main()
