from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from master_duel_recorder_lite.card_analysis_models import CardInputFingerprint, CardObservationValues
from master_duel_recorder_lite.card_analysis_repository import CardAnalysisConflictError, CardAnalysisRepository
from master_duel_recorder_lite.card_analysis_statistics import CardAnalysisStatisticsRepository
from master_duel_recorder_lite.duel_card_repository import DuelCardRepository
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.history_database import connect_history_database
from test_card_analysis_repository import add_recording


class DuelCardRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "history.sqlite3"
        self.cards = DuelCardRepository(self.database)
        self.analysis = CardAnalysisRepository(self.database)
        self.records = DuelRecordRepository(self.database)
        self.statistics = CardAnalysisStatisticsRepository(self.database)
        self.target = self.analysis.save_target(name="確認カード")
        self.identity = next(item for item in self.cards.list_identities() if item.card_id == self.target.card_id)
        self.duel = self.records.create_manual(DuelRecordValues(status="confirmed", result="win"), occurred_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.fingerprint = CardInputFingerprint("video.mkv", 100, 12345, "a" * 64)

    def video(self):
        add_recording(self.database)
        self.records.save("video", DuelRecordValues(status="confirmed", result="loss"), expected_revision=0)
        return self.records.get("video")

    def complete(self, *, key="marker", supports=True, fingerprint=None):
        job = self.analysis.create_job("video", fingerprint=fingerprint or self.fingerprint, analyzer_version="test")
        self.analysis.claim_job(job.job_id)
        value = CardObservationValues(self.target.target_id, self.target.name, "self", "activate", 1000,
                                      evidence={"supports_use": supports}, source_key=key)
        self.analysis.complete_job(job.job_id, (value,))
        return next(item for item in self.analysis.list_observations("video") if item.values.source_key == key)

    def test_manual_duel_supports_use_nonuse_unknown_and_opener_without_video(self):
        initial = self.cards.get_review(self.duel.duel_id)
        self.assertEqual(initial.information, ())
        used = self.cards.save_information(self.duel.duel_id, self.identity.card_id, "self", "used",
                                           expected_revision=0, is_opener=True, opener_expected_revision=0)
        review = self.cards.get_review(self.duel.duel_id)
        self.assertEqual(review.information, (used,))
        self.assertEqual(review.openers[0].card_id, used.card_id)
        self.assertEqual(review.openers[0].status, "confirmed")
        json.dumps(review.to_dict(), ensure_ascii=False)
        with closing(connect_history_database(self.database)) as connection:
            for table in ("recordings", "card_observations", "card_analysis_jobs"):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        result = self.statistics.dashboard(target_id=self.identity.card_id)
        self.assertEqual((result.used.matches, result.used.wins, result.unknown_opener_count), (1, 1, 0))
        nonuse = self.cards.save_information(self.duel.duel_id, self.identity.card_id, "self", "not_used",
                                            expected_revision=used.revision, opener_expected_revision=review.openers[0].revision)
        self.assertEqual(self.cards.get_review(self.duel.duel_id).openers[0].status, "unknown")
        unknown = self.cards.save_information(self.duel.duel_id, self.identity.card_id, "self", "unknown", expected_revision=nonuse.revision)
        self.assertIsNone(unknown.confirmed_at)
        self.assertEqual((self.statistics.dashboard(target_id=used.card_id).not_used.matches,
                          self.statistics.dashboard(target_id=used.card_id).unknown.matches), (0, 1))

    def test_conflicting_information_or_opener_rolls_back_both_and_changes(self):
        used = self.cards.save_information(self.duel.duel_id, self.identity.card_id, "self", "used", expected_revision=0,
                                           is_opener=True, opener_expected_revision=0)
        original = self.cards.get_review(self.duel.duel_id)
        with self.assertRaises(CardAnalysisConflictError):
            self.cards.save_information(self.duel.duel_id, used.card_id, "self", "not_used", expected_revision=used.revision,
                                        opener_expected_revision=0)
        with self.assertRaises(CardAnalysisConflictError):
            self.cards.save_information(self.duel.duel_id, used.card_id, "self", "unknown", expected_revision=0)
        self.assertEqual(self.cards.get_review(self.duel.duel_id), original)
        with closing(connect_history_database(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM duel_card_information_changes").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM duel_card_opener_changes").fetchone()[0], 1)

    def test_card_identity_is_shared_without_becoming_analysis_target(self):
        identity = self.cards.save_identity(name=" 独自カード ")
        same = self.cards.save_identity(name="独 自 カード")
        self.assertEqual(identity, same)
        self.cards.save_information(self.duel.duel_id, identity.card_id, "opponent", "not_used", expected_revision=0)
        self.assertIsNone(self.analysis.get_target(identity.card_id))
        self.assertEqual(self.statistics.dashboard(target_id=identity.card_id, actor="opponent").not_used.matches, 1)
        linked = self.analysis.save_target(name=identity.name)
        self.assertEqual(linked.card_id, identity.card_id)
        self.assertNotEqual(linked.target_id, identity.card_id)
        self.assertEqual(self.statistics.dashboard(target_id=linked.target_id, actor="opponent").not_used.matches, 1)

    def test_marker_deletion_and_target_disabling_do_not_change_confirmed_statistics(self):
        record = self.video()
        marker = self.complete()
        self.analysis.review_observation(marker.event_id, expected_revision=1, status="confirmed", is_opener=True,
                                          information_expected_revision=0, opener_expected_revision=0)
        before = self.statistics.dashboard(target_id=self.target.target_id)
        canonical = self.cards.get_review(record.duel_id)
        with closing(connect_history_database(self.database)) as connection, connection:
            connection.execute("DELETE FROM card_observations WHERE recording_id='video'")
            connection.execute("DELETE FROM card_analysis_jobs WHERE recording_id='video'")
        self.analysis.save_target(target_id=self.target.target_id, name=self.target.name, enabled=False)
        self.assertEqual(self.cards.get_review(record.duel_id), canonical)
        self.assertEqual(self.statistics.dashboard(target_id=self.target.target_id), before)
        self.assertIsNone(self.analysis.get_review("video").openers[0].event_id)

    def test_multiple_markers_link_to_one_fact_and_opener_semantics(self):
        record = self.video()
        first = self.complete(key="first")
        self.analysis.confirm_observation(first.event_id, expected_revision=1)
        second = self.complete(key="second")
        self.analysis.confirm_observation(second.event_id, expected_revision=1)
        review = self.cards.get_review(record.duel_id)
        self.assertEqual(len(review.information), 1)
        with closing(connect_history_database(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM card_observation_links WHERE information_id=?", (review.information[0].information_id,)).fetchone()[0], 2)
        self.assertEqual(self.statistics.dashboard(target_id=self.target.target_id).used.matches, 1)

    def test_observation_confirmation_checks_fact_revision_in_same_transaction(self):
        record = self.video()
        marker = self.complete()
        information = self.cards.save_information(record.duel_id, self.identity.card_id, "self", "unknown", expected_revision=0)
        updated = self.cards.save_information(record.duel_id, self.identity.card_id, "self", "unknown", expected_revision=information.revision)
        with self.assertRaises(CardAnalysisConflictError):
            self.analysis.review_observation(marker.event_id, expected_revision=1, status="confirmed",
                                              information_expected_revision=information.revision)
        self.assertEqual(self.analysis.get_observation(marker.event_id).revision, 1)
        self.assertEqual(self.analysis.get_observation(marker.event_id).status, "candidate")
        self.assertEqual(self.cards.get_review(record.duel_id).information, (updated,))

    def test_source_generation_change_keeps_fact_and_refuses_old_marker(self):
        record = self.video()
        marker = self.complete()
        confirmed = self.analysis.confirm_observation(marker.event_id, expected_revision=1)
        canonical = self.cards.get_review(record.duel_id)
        replacement = CardInputFingerprint("replacement.mkv", 101, 99999, "b" * 64)
        with closing(connect_history_database(self.database)) as connection, connection:
            old = connection.execute("SELECT * FROM recording_video_assets WHERE recording_id='video' AND is_current=1").fetchone()
            connection.execute("UPDATE recording_video_assets SET is_current=0 WHERE asset_id=?", (old["asset_id"],))
            connection.execute("INSERT INTO recording_video_assets (asset_id,recording_id,generation,path,size_bytes,mtime_ns,sha256,state,created_at,updated_at) "
                               "VALUES ('replacement','video',2,?,?,?,?,'present',?,?)",
                               (replacement.path, replacement.size_bytes, replacement.mtime_ns, replacement.sha256, old["created_at"], old["updated_at"]))
        self.assertFalse(self.analysis.get_observation(marker.event_id).video_verified)
        with self.assertRaises(CardAnalysisConflictError):
            self.analysis.review_observation(marker.event_id, expected_revision=confirmed.revision, status="confirmed", expected_generation=marker.generation)
        fresh = self.complete(fingerprint=replacement)
        self.assertNotEqual(fresh.event_id, marker.event_id)
        self.assertEqual(fresh.status, "candidate")
        self.assertEqual(self.cards.get_review(record.duel_id), canonical)
        self.assertEqual(self.statistics.dashboard(target_id=self.target.target_id).used.matches, 1)

    def test_late_completion_and_stale_generation_cannot_recreate_or_confirm_markers(self):
        self.video()
        original = self.complete()
        job = self.analysis.create_job("video", fingerprint=self.fingerprint, analyzer_version="test")
        self.analysis.claim_job(job.job_id)
        with closing(connect_history_database(self.database)) as connection, connection:
            connection.execute("UPDATE recording_video_assets SET state='deleted' WHERE recording_id='video'")
            connection.execute("DELETE FROM card_observations WHERE recording_id='video'")
        with self.assertRaises(CardAnalysisConflictError):
            self.analysis.complete_job(job.job_id, (original.values,))
        self.assertEqual(self.analysis.list_observations("video"), ())
        self.assertEqual(self.analysis.get_job(job.job_id).state, "running")

    def test_old_source_without_fingerprint_cannot_be_used_even_when_path_exists(self):
        self.video()
        marker = self.complete()
        with closing(connect_history_database(self.database)) as connection, connection:
            connection.execute("UPDATE card_observation_links SET verified=0,input_fingerprint_json='{}' WHERE event_id=?", (marker.event_id,))
        self.assertFalse(self.analysis.get_observation(marker.event_id).video_verified)
        with self.assertRaises(CardAnalysisConflictError):
            self.analysis.confirm_observation(marker.event_id, expected_revision=1)
        self.assertEqual(self.statistics.dashboard(target_id=self.target.target_id, include_candidates=True).candidate_used.matches, 0)

    def test_marker_source_requires_matching_verified_content(self):
        self.video()
        marker = self.complete()
        with closing(connect_history_database(self.database)) as connection, connection:
            connection.execute("UPDATE card_observation_links SET verified=0 WHERE event_id=?", (marker.event_id,))
            connection.execute("UPDATE recording_video_assets SET state='unverified',sha256='' WHERE recording_id='video'")
        self.assertFalse(self.analysis.get_observation(marker.event_id).video_verified)
        self.assertEqual(self.analysis.verify_video_sources("video", self.fingerprint), 1)
        self.assertTrue(self.analysis.get_observation(marker.event_id).video_verified)
        self.assertEqual(self.analysis.verify_video_sources("video", self.fingerprint), 0)

    def test_reused_event_id_cannot_accept_a_stale_analysis_generation(self):
        self.video()
        original = self.complete()
        current = self.complete()
        self.assertEqual(original.event_id, current.event_id)
        self.assertNotEqual(original.generation, current.generation)
        with self.assertRaises(CardAnalysisConflictError):
            self.analysis.review_observation(current.event_id, expected_revision=original.revision,
                                              expected_generation=original.generation, status="confirmed")
        self.assertEqual(self.analysis.get_observation(current.event_id).status, "candidate")

    def test_absolute_input_fingerprint_never_changes_managed_relative_asset_path(self):
        self.video()
        fingerprint = CardInputFingerprint(str(Path(self.temp.name) / "recordings" / "video.mkv"), 100, 12345, "a" * 64)
        self.complete(fingerprint=fingerprint)
        self.analysis.verify_video_sources("video", fingerprint)
        with closing(connect_history_database(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT path FROM recording_video_assets WHERE recording_id='video' AND is_current=1").fetchone()[0], "video.mkv")
            source = connection.execute("SELECT input_fingerprint_json FROM card_analysis_job_sources").fetchone()[0]
            self.assertEqual(json.loads(source)["path"], fingerprint.path)


if __name__ == "__main__":
    unittest.main()
