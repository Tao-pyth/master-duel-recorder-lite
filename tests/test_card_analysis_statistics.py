from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from master_duel_recorder_lite.card_analysis_models import CardInputFingerprint, CardObservationValues
from master_duel_recorder_lite.card_analysis_repository import CardAnalysisRepository
from master_duel_recorder_lite.card_analysis_statistics import CardAnalysisStatisticsRepository
from master_duel_recorder_lite.duel_catalog import DuelCatalogRepository
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.duel_statistics import StatisticsFilter


class CardAnalysisStatisticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "history.sqlite3"
        self.repository = CardAnalysisRepository(self.database)
        self.records = DuelRecordRepository(self.database)
        self.statistics = CardAnalysisStatisticsRepository(self.database)
        self.target = self.repository.save_target(name="比較対象")
        self.opener = self.repository.save_target(name="別の初動")
        for identifier, result in (("used", "win"), ("candidate", "loss"), ("detail", "win"),
                                   ("zero", "win"), ("failed", "loss"), ("unanalyzed", "draw"), ("nonuse", "win")):
            self.record(identifier, result=result)
        observations = self.complete("used", [self.value("same-card-1"), self.value("same-card-2")])
        for observation in observations:
            self.repository.confirm_observation(observation.event_id, expected_revision=1)
        self.complete("candidate", [self.value("candidate")])
        detail = self.complete("detail", [self.value("detail", supports=False)])[0]
        self.repository.confirm_observation(detail.event_id, expected_revision=1)
        self.complete("zero", [])
        job = self.repository.create_job("failed", fingerprint=self.fingerprint, analyzer_version="test")
        self.repository.fail_job(job.job_id, "OCR failure")
        self.repository.set_nonuse("nonuse", self.target.target_id, "self")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def fingerprint(self):
        return CardInputFingerprint("isolated-video.mkv", 100, 123, "a" * 64)

    def record(self, identifier: str, *, result: str = "win", status: str = "confirmed",
               recording_state: str = "completed", deck: str = "", duel_type: str = "ranked", day: int = 1) -> None:
        timestamp = datetime(2026, 10, day, tzinfo=timezone.utc)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "INSERT INTO recordings (recording_id,state,source,output_path,container,created_at,updated_at,duration_seconds) "
                "VALUES (?,?,'manual',?,'mkv',?,?,60)",
                (identifier, recording_state, identifier + ".mkv", timestamp.isoformat(), timestamp.isoformat()),
            )
        self.records.save(identifier, DuelRecordValues(status=status, result=result, own_deck=deck, duel_type=duel_type),
                          expected_revision=0)

    def value(self, key: str, *, supports: bool = True, actor: str = "self", target: str | None = None):
        return CardObservationValues(target or self.target.target_id, "比較対象", actor, "activate", 1000,
                                     source_key=key, evidence={"supports_use": supports})

    def complete(self, recording: str, values: list):
        job = self.repository.create_job(recording, fingerprint=self.fingerprint, analyzer_version="test")
        self.repository.claim_job(job.job_id)
        self.repository.complete_job(job.job_id, values)
        return self.repository.list_observations(recording)

    def test_duplicate_events_are_one_duel_and_unknown_is_not_nonuse(self) -> None:
        result = self.statistics.dashboard(target_id=self.target.target_id)
        self.assertEqual(result.total.matches, 7)
        self.assertEqual((result.used.matches, result.used.wins), (1, 1))
        self.assertEqual(result.not_used.matches, 1)
        self.assertEqual(result.unknown.matches, 5)
        self.assertEqual(result.candidate_used.matches, 1)
        self.assertEqual(result.unknown_opener_count, 7)
        self.assertEqual(result.by_opener, ())

    def test_candidate_reference_changes_only_explicit_mode_and_keeps_unknown_denominator(self) -> None:
        confirmed = self.statistics.dashboard(target_id=self.target.target_id)
        reference = self.statistics.dashboard(target_id=self.target.target_id, include_candidates=True)
        self.assertEqual(reference.total, confirmed.total)
        self.assertEqual(reference.confirmed_used, confirmed.confirmed_used)
        self.assertEqual((reference.used.matches, reference.used.wins, reference.used.losses), (2, 1, 1))
        self.assertEqual(reference.not_used.matches, 1)
        self.assertEqual(reference.unknown.matches, 4)
        self.assertIn("参考", reference.to_dict()["mode_label"])

    def test_actor_unknown_detail_and_rejected_candidates_do_not_become_use(self) -> None:
        self.record("unknown-actor")
        unknown = self.complete("unknown-actor", [self.value("actor-unknown", actor="unknown")])[0]
        self.repository.confirm_observation(unknown.event_id, expected_revision=1)
        self.record("rejected")
        rejected = self.complete("rejected", [self.value("reject")])[0]
        self.repository.reject_observation(rejected.event_id, expected_revision=1)
        result = self.statistics.dashboard(target_id=self.target.target_id, include_candidates=True)
        self.assertEqual(result.used.matches, 2)
        self.assertEqual(result.unknown.matches, 6)
        opponent = self.statistics.dashboard(target_id=self.target.target_id, actor="opponent")
        self.assertEqual((opponent.used.matches, opponent.not_used.matches, opponent.unknown.matches), (0, 0, 9))

    def test_initial_card_requires_confirmation_and_filters_comparison_by_duel(self) -> None:
        used = self.repository.list_observations("used")[0]
        self.repository.set_opener("used", "self", event_id=used.event_id)
        nonuse_opener = self.complete("nonuse", [self.value("initial-other", target=self.opener.target_id)])[0]
        confirmed = self.repository.confirm_observation(nonuse_opener.event_id, expected_revision=1)
        self.repository.set_opener("nonuse", "self", event_id=confirmed.event_id)
        all_cards = self.statistics.dashboard(target_id=self.target.target_id)
        self.assertEqual(len(all_cards.by_opener), 2)
        self.assertEqual(all_cards.unknown_opener_count, 5)
        conditional = self.statistics.dashboard(target_id=self.target.target_id, opener_target_id=self.opener.target_id)
        self.assertEqual(conditional.base_total.matches, 7)
        self.assertEqual((conditional.total.matches, conditional.used.matches, conditional.not_used.matches,
                          conditional.unknown.matches), (1, 0, 1, 0))
        self.assertEqual(conditional.by_opener[0].label, "別の初動")
        self.assertEqual(conditional.by_opener[0].metric.wins, 1)

    def test_existing_period_type_hidden_completion_and_confirmation_filters_remain(self) -> None:
        self.record("draft", status="draft")
        self.record("recording-now", recording_state="recording")
        self.record("unknown-result", result="unknown")
        self.record("hidden", deck="非表示")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE duel_catalog_entries SET hidden_from_history_statistics=1 WHERE name='非表示'")
        self.record("later", day=2, duel_type="event")
        result = self.statistics.dashboard(StatisticsFilter(date_from=date(2026, 10, 2), duel_type="event"),
                                            target_id=self.target.target_id)
        self.assertEqual((result.total.matches, result.unknown.matches), (1, 1))
        ranked = self.statistics.dashboard(StatisticsFilter(duel_type="ranked"), target_id=self.target.target_id)
        self.assertEqual(ranked.total.matches, 7)
        self.assertEqual(ranked.used.matches, 1)

    def test_manual_record_without_video_remains_unknown(self) -> None:
        self.records.create_manual(DuelRecordValues(status="confirmed", result="loss"),
                                   occurred_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
        result = self.statistics.dashboard(target_id=self.target.target_id)
        self.assertEqual((result.total.matches, result.unknown.matches), (8, 6))

    def test_target_outside_old_snapshot_is_unknown_and_disabled_target_history_is_retained(self) -> None:
        later = self.repository.save_target(name="後から追加したカード")
        result = self.statistics.dashboard(target_id=later.target_id)
        self.assertEqual((result.total.matches, result.used.matches, result.not_used.matches, result.unknown.matches), (7, 0, 0, 7))
        self.repository.save_target(target_id=self.target.target_id, name=self.target.name, enabled=False)
        result = self.statistics.dashboard(target_id=self.target.target_id)
        self.assertEqual(result.used.matches, 1)

    def test_nonuse_human_decision_overrides_unconfirmed_candidate_in_reference_mode(self) -> None:
        self.repository.set_nonuse("candidate", self.target.target_id, "self")
        result = self.statistics.dashboard(target_id=self.target.target_id, include_candidates=True)
        self.assertEqual((result.used.matches, result.not_used.matches, result.candidate_used.matches), (1, 2, 0))

    def test_statistics_rows_keep_existing_constructor_defaults(self) -> None:
        from master_duel_recorder_lite.duel_statistics import StatisticsRow

        row = StatisticsRow(datetime.now(timezone.utc), "win", "first", "heads", "", None, None)
        self.assertEqual(row.duel_id, "")
        self.assertIsNone(row.recording_id)
        # 既存カタログの通常操作も新schemaで維持します。
        self.assertIsNotNone(DuelCatalogRepository(self.database).add_deck("通常デッキ"))


if __name__ == "__main__":
    unittest.main()
