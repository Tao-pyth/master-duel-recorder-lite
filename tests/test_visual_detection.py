import struct
import unittest
from collections.abc import Callable
from datetime import datetime, timezone

from master_duel_recorder_lite.frame_capture import FrameSample
from master_duel_recorder_lite.visual_detection import (
    _ultrawide_lower_loss_score,
    DetectionCandidate,
    DuelConfirmationDetector,
    DuelResultDetector,
    DuelStartDetector,
    FrameCues,
    MatchErrorDetector,
    MasterDuelVisualEventDetector,
    MasterDuelState,
    MasterDuelUiStateMachine,
    TemporalEventConsensus,
    TurnChangeDetector,
    WhiteSpan,
    detect_display_profile,
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
    play_order: str | None = None,
    evidence: str | None = None,
) -> DetectionCandidate:
    return DetectionCandidate(
        event_type=event_type,
        elapsed_ms=elapsed_ms,
        confidence=confidence,
        reason="synthetic",
        detector_id="test",
        detector_version="1",
        actor=actor,
        outcome=outcome,
        play_order=play_order,
        evidence=evidence,
    )


class BmpNormalizationTest(unittest.TestCase):
    def test_standard_window_is_downsampled_without_cropping(self) -> None:
        normalized = normalize_bmp(frame(bmp(640, 360, (10, 20, 30)), 640, 360))

        self.assertIsNotNone(normalized)
        self.assertEqual((normalized.width, normalized.height), (640, 360))  # type: ignore[union-attr]
        self.assertEqual(normalized.profile_name, "standard-16:9-window")  # type: ignore[union-attr]
        self.assertEqual(normalized.rgb(0, 0), (10, 20, 30))  # type: ignore[union-attr]

    def test_ultrawide_fullscreen_preserves_full_aspect_ratio(self) -> None:
        normalized = normalize_bmp(frame(bmp(688, 288, (10, 20, 30)), 688, 288))

        self.assertIsNotNone(normalized)
        self.assertEqual((normalized.width, normalized.height), (640, 268))  # type: ignore[union-attr]
        self.assertEqual(normalized.profile_name, "ultrawide-fullscreen")  # type: ignore[union-attr]

    def test_unknown_layout_and_invalid_bmp_are_rejected(self) -> None:
        self.assertIsNone(detect_display_profile(800, 600))
        self.assertIsNone(normalize_bmp(frame(b"not-bmp")))
        self.assertIsNone(normalize_bmp(frame(bmp(160, 120, (0, 0, 0)), 160, 120)))


class VisualDetectorTest(unittest.TestCase):
    def test_ultrawide_lower_loss_score_rejects_dense_attack_effect(self) -> None:
        actual_loss = WhiteSpan(ratio=0.38, pixel_ratio=0.088, center_x=0.484)
        dense_attack = WhiteSpan(ratio=0.41, pixel_ratio=0.153, center_x=0.503)
        fading_attack = WhiteSpan(ratio=0.41, pixel_ratio=0.121, center_x=0.503)

        self.assertGreaterEqual(_ultrawide_lower_loss_score(actual_loss), 0.95)
        self.assertEqual(_ultrawide_lower_loss_score(dense_attack), 0.0)
        self.assertLess(_ultrawide_lower_loss_score(fading_attack), 0.70)

    def test_result_near_board_requires_four_of_five_frames(self) -> None:
        consensus = TemporalEventConsensus(assume_started=True)
        for elapsed_ms in (1000, 1500, 2000):
            consensus.process(
                (candidate("duel_confirmed", elapsed_ms, play_order="second"),)
            )

        for elapsed_ms in (20_000, 20_500):
            self.assertEqual(
                consensus.process(
                    (
                        candidate(
                            "duel_result",
                            elapsed_ms,
                            1.0,
                            outcome="loss",
                            evidence="result-near-board",
                        ),
                    )
                ),
                (),
            )
        self.assertEqual(consensus.process(()), ())
        for elapsed_ms in (30_000, 30_500, 31_000):
            self.assertEqual(
                consensus.process(
                    (
                        candidate(
                            "duel_result",
                            elapsed_ms,
                            1.0,
                            outcome="loss",
                            evidence="result-near-board",
                        ),
                    )
                ),
                (),
            )
        emitted = consensus.process(
            (
                candidate(
                    "duel_result",
                    31_500,
                    1.0,
                    outcome="loss",
                    evidence="result-near-board",
                ),
            )
        )

        self.assertEqual([item.event_type for item in emitted], ["duel_result"])

    def test_loss_requires_four_of_five_frames_even_when_clean_and_high_confidence(self) -> None:
        consensus = TemporalEventConsensus(assume_started=True)
        for elapsed_ms in (1000, 1500, 2000):
            consensus.process(
                (candidate("duel_confirmed", elapsed_ms, play_order="second"),)
            )

        for elapsed_ms in (20_000, 20_500, 21_000):
            self.assertEqual(
                consensus.process(
                    (
                        candidate(
                            "duel_result",
                            elapsed_ms,
                            0.99,
                            outcome="loss",
                            evidence="result-clean",
                        ),
                    )
                ),
                (),
            )
        emitted = consensus.process(
            (
                candidate(
                    "duel_result",
                    21_500,
                    0.99,
                    outcome="loss",
                    evidence="result-clean",
                ),
            )
        )

        self.assertEqual([item.event_type for item in emitted], ["duel_result"])

    def test_high_confidence_ultrawide_lower_loss_can_emit_once(self) -> None:
        consensus = TemporalEventConsensus(assume_started=True)
        for elapsed_ms in (1000, 1500, 2000):
            consensus.process(
                (candidate("duel_confirmed", elapsed_ms, play_order="second"),)
            )

        emitted = consensus.process(
            (
                candidate(
                    "duel_result",
                    20_000,
                    1.0,
                    outcome="loss",
                    evidence="ultrawide-lower-loss",
                ),
            )
        )

        self.assertEqual([item.event_type for item in emitted], ["duel_result"])

    def test_short_loss_shaped_attack_effect_does_not_end_duel(self) -> None:
        consensus = TemporalEventConsensus(assume_started=True)
        for elapsed_ms in (1000, 1500, 2000):
            consensus.process(
                (candidate("duel_confirmed", elapsed_ms, play_order="second"),)
            )

        self.assertEqual(
            consensus.process(
                (
                    candidate(
                        "duel_result",
                        20_000,
                        0.7661,
                        outcome="loss",
                        evidence="result-clean",
                    ),
                )
            ),
            (),
        )
        self.assertEqual(consensus.process(()), ())
        self.assertEqual(
            consensus.process(
                (
                    candidate(
                        "duel_result",
                        21_000,
                        0.775,
                        outcome="loss",
                        evidence="result-clean",
                    ),
                )
            ),
            (),
        )
        self.assertEqual(consensus.process(()), ())
        self.assertEqual(consensus.process(()), ())

        self.assertFalse(consensus._resulted)

    def test_state_machine_tracks_overlay_recovery_error_and_replay(self) -> None:
        machine = MasterDuelUiStateMachine()

        self.assertEqual(machine.observe(FrameCues(True)), MasterDuelState.MATCHMAKING)
        self.assertEqual(
            machine.observe(FrameCues(True, coin_toss_score=0.9, turn_order_score=0.8)),
            MasterDuelState.COIN_TOSS_CANDIDATE,
        )
        self.assertEqual(
            machine.observe(FrameCues(True, board_score=0.8)),
            MasterDuelState.TURN_ORDER_CONFIRMED,
        )
        self.assertEqual(
            machine.observe(FrameCues(True, board_score=0.8)), MasterDuelState.DUEL_ACTIVE
        )
        self.assertEqual(
            machine.observe(FrameCues(True, overlay_score=0.8)), MasterDuelState.OVERLAY
        )
        self.assertEqual(
            machine.observe(FrameCues(True, board_score=0.8)), MasterDuelState.DUEL_ACTIVE
        )

        self.assertEqual(
            MasterDuelUiStateMachine().observe(FrameCues(True, match_error_score=0.8)),
            MasterDuelState.MATCH_ERROR,
        )
        self.assertEqual(
            MasterDuelUiStateMachine().observe(FrameCues(True, replay_score=0.8)),
            MasterDuelState.REPLAY,
        )

    def test_coin_toss_creates_start_candidate(self) -> None:
        detected = DuelStartDetector().detect(
            FrameCues(True, coin_toss_score=0.9, turn_order_score=0.8, detail="coin"),
            20_000,
        )

        self.assertIsNotNone(detected)
        self.assertEqual(detected.event_type, "duel_start")  # type: ignore[union-attr]
        self.assertGreaterEqual(detected.confidence, 0.9)  # type: ignore[union-attr]

    def test_start_rejects_home_screen_coin_like_colors(self) -> None:
        detected = DuelStartDetector().detect(
            FrameCues(True, coin_toss_score=0.78, turn_order_score=0.46),
            0,
        )

        self.assertIsNone(detected)

    def test_start_rejects_connecting_screen(self) -> None:
        detected = DuelStartDetector().detect(
            FrameCues(
                True,
                coin_toss_score=0.9,
                turn_order_score=0.8,
                loading_score=0.9,
            ),
            0,
        )

        self.assertIsNone(detected)

    def test_board_confirmation_preserves_play_order(self) -> None:
        detected = DuelConfirmationDetector().detect(
            FrameCues(
                True,
                board_score=0.8,
                actor="opponent",
                play_order="second",
                detail="board",
            ),
            43_000,
        )

        self.assertIsNotNone(detected)
        self.assertEqual(detected.event_type, "duel_confirmed")  # type: ignore[union-attr]
        self.assertEqual(detected.play_order, "second")  # type: ignore[union-attr]

    def test_start_rejects_replay_error_and_overlay_frames(self) -> None:
        detector = DuelStartDetector()

        self.assertIsNone(
            detector.detect(FrameCues(True, coin_toss_score=0.9, replay_score=0.8), 0)
        )
        self.assertIsNone(
            detector.detect(FrameCues(True, coin_toss_score=0.9, match_error_score=0.8), 0)
        )
        self.assertIsNone(
            detector.detect(FrameCues(True, coin_toss_score=0.9, result_score=0.8), 0)
        )
        self.assertIsNone(
            detector.detect(FrameCues(True, board_score=0.9, overlay_score=0.7), 0)
        )

    def test_short_turn_banner_is_carried_for_consensus(self) -> None:
        detector = TurnChangeDetector()

        first = detector.detect(FrameCues(True, turn_score=0.4, actor="opponent"), 0)
        carried = detector.detect(FrameCues(True, turn_score=0.0), 500)
        finished = detector.detect(FrameCues(True, turn_score=0.0), 1000)

        self.assertIsNotNone(first)
        self.assertIsNotNone(carried)
        self.assertEqual(carried.actor, "opponent")  # type: ignore[union-attr]
        self.assertIsNone(finished)

    def test_result_detector_preserves_outcome(self) -> None:
        detected = DuelResultDetector().detect(
            FrameCues(True, result_score=0.8, outcome="loss", detail="LOSE"), 5000
        )

        self.assertIsNotNone(detected)
        self.assertEqual(detected.outcome, "loss")  # type: ignore[union-attr]

    def test_error_detector_uses_event_specific_threshold(self) -> None:
        self.assertIsNone(
            MatchErrorDetector().detect(
                FrameCues(True, match_error_score=0.6, detail="loading transition"),
                4500,
            )
        )
        detected = MatchErrorDetector().detect(
            FrameCues(True, match_error_score=0.75, detail="error dialog"), 5000
        )

        self.assertIsNotNone(detected)
        self.assertEqual(detected.confidence, 0.75)  # type: ignore[union-attr]

    def test_master_detector_rejects_unknown_layout(self) -> None:
        detector = MasterDuelVisualEventDetector(FakeExtractor(FrameCues(False)))
        self.assertEqual(detector.detect(frame(b"invalid"), 0), ())


class TemporalConsensusTest(unittest.TestCase):
    def test_coin_requires_two_matches_in_four_frame_window(self) -> None:
        consensus = TemporalEventConsensus()

        consensus.process((candidate("duel_start", 0, evidence="coin"),))
        consensus.process(())
        emitted = consensus.process((candidate("duel_start", 1000, evidence="coin"),))

        self.assertEqual(len(emitted), 1)
        self.assertIn("直近4フレーム中2件", emitted[0].reason)

    def test_board_fallback_requires_three_matches_in_five_frame_window(self) -> None:
        consensus = TemporalEventConsensus()

        consensus.process((candidate("duel_start", 0, evidence="board"),))
        consensus.process(())
        consensus.process((candidate("duel_start", 1000, evidence="board"),))
        self.assertEqual(consensus.process(()), ())
        emitted = consensus.process((candidate("duel_start", 2000, evidence="board"),))

        self.assertEqual(len(emitted), 1)
        self.assertIn("直近5フレーム中3件", emitted[0].reason)

    def test_low_confidence_is_discarded_and_first_observation_time_is_used(self) -> None:
        consensus = TemporalEventConsensus(minimum_confidence=0.70)

        self.assertEqual(consensus.process((candidate("duel_start", 100, 0.69),)), ())
        self.assertEqual(consensus.process((candidate("duel_start", 1000),)), ())
        emitted = consensus.process((candidate("duel_start", 1500, 0.9),))

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].elapsed_ms, 1000)
        self.assertAlmostEqual(emitted[0].confidence, 0.85)

    def test_live_state_requires_start_and_confirmation(self) -> None:
        consensus = TemporalEventConsensus(turn_cooldown_ms=5000)

        self.assertEqual(consensus.process((candidate("turn_change", 1000, actor="self"),)), ())
        consensus.process((candidate("duel_start", 2000),))
        self.assertEqual(len(consensus.process((candidate("duel_start", 2500),))), 1)
        consensus.process((candidate("duel_confirmed", 4000, actor="self", play_order="first"),))
        self.assertEqual(consensus.process(
            (candidate("duel_confirmed", 4500, actor="self", play_order="first"),)
        ), ())
        confirmed = consensus.process(
            (candidate("duel_confirmed", 5000, actor="self", play_order="first"),)
        )

        self.assertEqual([item.event_type for item in confirmed], ["duel_confirmed", "turn_change"])
        self.assertEqual(confirmed[1].play_order, "first")

    def test_turns_must_alternate_and_result_is_terminal(self) -> None:
        consensus = TemporalEventConsensus(turn_cooldown_ms=5000, assume_started=True)
        consensus.process((candidate("duel_confirmed", 1000, actor="self", play_order="first"),))
        consensus.process((candidate("duel_confirmed", 1500, actor="self", play_order="first"),))
        consensus.process((candidate("duel_confirmed", 1800, actor="self", play_order="first"),))
        consensus.process((candidate("duel_confirmed", 2000, actor="self", play_order="first"),))

        self.assertEqual(consensus.process((candidate("turn_change", 7000, actor="self"),)), ())
        consensus.process((candidate("turn_change", 8000, actor="opponent"),))
        changed = consensus.process((candidate("turn_change", 8500, actor="opponent"),))
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].actor, "opponent")

        consensus.process((candidate("duel_result", 10_000, outcome="win"),))
        result = consensus.process((candidate("duel_result", 10_500, outcome="win"),))
        self.assertEqual(len(result), 1)
        self.assertEqual(consensus.process((candidate("turn_change", 16_000, actor="self"),)), ())

    def test_high_confidence_result_can_finish_on_one_sample_after_confirmation(self) -> None:
        consensus = TemporalEventConsensus(assume_started=True)
        for elapsed_ms in (1000, 1500, 2000):
            consensus.process(
                (candidate("duel_confirmed", elapsed_ms, play_order="first"),)
            )

        emitted = consensus.process(
            (candidate("duel_result", 20_000, 0.95, outcome="win"),)
        )

        self.assertEqual([item.event_type for item in emitted], ["duel_result"])
        self.assertEqual(emitted[0].outcome, "win")

    def test_ambiguous_single_result_sample_still_requires_temporal_agreement(self) -> None:
        consensus = TemporalEventConsensus(assume_started=True)
        for elapsed_ms in (1000, 1500, 2000):
            consensus.process(
                (candidate("duel_confirmed", elapsed_ms, play_order="second"),)
            )

        self.assertEqual(
            consensus.process((candidate("duel_result", 20_000, 0.8171, outcome="win"),)),
            (),
        )

    def test_next_coin_toss_closes_a_confirmed_duel_as_fallback_boundary(self) -> None:
        consensus = TemporalEventConsensus(assume_started=True)
        for elapsed_ms in (1000, 1500, 2000):
            consensus.process(
                (candidate("duel_confirmed", elapsed_ms, play_order="first"),)
            )

        consensus.process((candidate("duel_start", 20_000, evidence="coin"),))
        emitted = consensus.process(
            (candidate("duel_start", 21_000, evidence="coin"),)
        )

        self.assertEqual([item.event_type for item in emitted], ["duel_boundary"])
        self.assertEqual(emitted[0].evidence, "next_duel")
        self.assertIn("録画境界", emitted[0].reason)

    def test_v0173_log_sequence_stops_at_next_coin_before_ambiguous_result(self) -> None:
        consensus = TemporalEventConsensus(assume_started=True)
        for elapsed_ms in (15_000, 15_500, 16_000):
            consensus.process(
                (candidate("duel_confirmed", elapsed_ms, play_order="second"),)
            )

        consensus.process(
            (candidate("duel_start", 571_970, 0.89, evidence="coin"),)
        )
        boundary = consensus.process(
            (candidate("duel_start", 573_866, 0.94, evidence="coin"),)
        )
        ambiguous_result = consensus.process(
            (candidate("duel_result", 594_354, 0.8171, outcome="win"),)
        )

        self.assertEqual([item.event_type for item in boundary], ["duel_boundary"])
        self.assertEqual(ambiguous_result, ())

    def test_initial_coin_toss_cannot_be_reused_as_next_duel_boundary(self) -> None:
        consensus = TemporalEventConsensus(assume_started=True)
        for elapsed_ms in (1000, 1500, 2000):
            consensus.process(
                (candidate("duel_confirmed", elapsed_ms, play_order="first"),)
            )

        for elapsed_ms in (3000, 3500, 4000):
            self.assertEqual(
                consensus.process(
                    (candidate("duel_start", elapsed_ms, evidence="coin"),)
                ),
                (),
            )

    def test_replay_like_controls_during_a_live_duel_are_ignored(self) -> None:
        consensus = TemporalEventConsensus(assume_started=True)
        consensus.process((candidate("duel_confirmed", 1000, actor="self", play_order="first"),))
        consensus.process((candidate("duel_confirmed", 1500, actor="self", play_order="first"),))
        consensus.process((candidate("duel_confirmed", 1800, actor="self", play_order="first"),))

        consensus.process((candidate("replay_detected", 2000),))
        self.assertEqual(consensus.process((candidate("replay_detected", 2500),)), ())

    def test_confirmed_replay_discards_unconfirmed_candidate(self) -> None:
        consensus = TemporalEventConsensus()
        consensus.process((candidate("duel_start", 1000),))
        consensus.process((candidate("duel_start", 1500),))

        consensus.process((candidate("replay_detected", 2000),))
        emitted = consensus.process((candidate("replay_detected", 2500),))

        self.assertEqual(emitted[0].event_type, "replay_detected")

    def test_transient_error_cues_after_duel_start_do_not_discard_candidate(self) -> None:
        consensus = TemporalEventConsensus()
        consensus.process((candidate("duel_start", 1000),))
        self.assertEqual(
            consensus.process((candidate("duel_start", 1500),))[0].event_type,
            "duel_start",
        )

        for elapsed_ms in (2000, 2500):
            self.assertEqual(
                consensus.process((candidate("match_error", elapsed_ms),)),
                (),
            )

        consensus.process((candidate("duel_confirmed", 3500),))
        consensus.process((candidate("duel_confirmed", 4000),))
        confirmed = consensus.process((candidate("duel_confirmed", 4500),))
        self.assertEqual(confirmed[0].event_type, "duel_confirmed")

    def test_confirmed_match_error_discards_unconfirmed_candidate(self) -> None:
        consensus = TemporalEventConsensus()
        consensus.process((candidate("duel_start", 1000),))
        consensus.process((candidate("duel_start", 1500),))
        consensus.process((candidate("match_error", 2000),))
        consensus.process((candidate("match_error", 2500),))

        emitted = consensus.process((candidate("match_error", 3000),))

        self.assertEqual(emitted[0].event_type, "match_error")

    def test_error_and_replay_are_control_events_not_duel_events(self) -> None:
        error_consensus = TemporalEventConsensus()
        error_consensus.process((candidate("match_error", 1000),))
        self.assertEqual(error_consensus.process((candidate("match_error", 1500),)), ())
        self.assertEqual(
            error_consensus.process((candidate("match_error", 2000),))[0].event_type,
            "match_error",
        )

        replay_consensus = TemporalEventConsensus()
        replay_consensus.process((candidate("replay_detected", 1000),))
        self.assertEqual(
            replay_consensus.process((candidate("replay_detected", 1500),))[0].event_type,
            "replay_detected",
        )

    def test_single_missing_frame_does_not_erase_window_consensus(self) -> None:
        consensus = TemporalEventConsensus()
        consensus.process((candidate("duel_start", 1000),))
        consensus.process(())
        emitted = consensus.process((candidate("duel_start", 1500),))

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].elapsed_ms, 1000)


if __name__ == "__main__":
    unittest.main()
