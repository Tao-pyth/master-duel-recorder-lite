from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from master_duel_recorder_lite.card_analysis_models import CardInputFingerprint, CardObservationValues
from master_duel_recorder_lite.card_analysis_repository import (
    CardAnalysisConflictError, CardAnalysisError, CardAnalysisRepository,
)


def add_recording(database: Path, identifier: str = "video", *, state: str = "completed") -> None:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO recordings (recording_id,state,source,output_path,container,created_at,updated_at,duration_seconds) "
            "VALUES (?,?,'manual',?,'mkv','2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00',60)",
            (identifier, state, identifier + ".mkv"),
        )


class CardAnalysisRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "history.sqlite3"
        self.repository = CardAnalysisRepository(self.database)
        add_recording(self.database)
        self.target = self.repository.save_target(name="増殖するG", aliases=("増G",), roles=("手札誘発",))
        self.other = self.repository.save_target(name="白き森のシルヴィ", roles=("初動",))
        self.fingerprint = CardInputFingerprint("video.mkv", 100, 12345, "a" * 64)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def values(self, key: str = "row-1", *, target: str | None = None, actor: str = "opponent",
               action: str = "activate", time: int = 1000, supports: bool = True) -> CardObservationValues:
        return CardObservationValues(target or self.target.target_id, self.target.name, actor, action, time,
                                     evidence={"kind": "log_use", "supports_use": supports, "text": "original"},
                                     source_key=key)

    def job(self, *, fingerprint: CardInputFingerprint | None = None):
        job = self.repository.create_job("video", fingerprint=fingerprint or self.fingerprint,
                                         analyzer_version="rapidocr-ppocrv6-v1")
        self.repository.claim_job(job.job_id)
        return job

    def finish(self, *values: CardObservationValues):
        job = self.job()
        self.repository.complete_job(job.job_id, values)
        return self.repository.list_observations("video")

    def test_targets_preserve_id_and_snapshot_after_edit_and_disable(self) -> None:
        job = self.job()
        changed = self.repository.save_target(target_id=self.target.target_id, name="改名済み", aliases=("別名",),
                                               roles=("任意用途",), enabled=False, expected_revision=1)
        self.assertEqual(changed.target_id, self.target.target_id)
        self.assertEqual(changed.purpose, "任意用途")
        self.assertNotIn(changed, self.repository.list_targets(include_disabled=False))
        old = self.repository.get_job(job.job_id)
        snapshot = next(item for item in old.target_snapshot if item.target_id == self.target.target_id)
        self.assertEqual((snapshot.name, snapshot.aliases), ("増殖するG", ("増G",)))
        self.assertEqual(old.sample_fps, 2)

    def test_target_validation_rejects_empty_normalized_duplicates_alias_collisions(self) -> None:
        for kwargs in ({"name": "  "}, {"name": "増殖するＧ"}, {"name": "新名", "aliases": ("増G",)},
                       {"name": "新名", "aliases": ("新 名",)}, {"name": "新名", "enabled": "yes"},
                       {"name": "新名", "purpose": False}, {"name": "新名", "target_id": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.repository.save_target(**kwargs)
        with self.assertRaises(CardAnalysisConflictError):
            self.repository.save_target(name="改名", target_id=self.target.target_id, expected_revision=0)

    def test_presets_are_idempotent_and_do_not_reenable_changed_targets(self) -> None:
        self.repository.seed_presets()
        presets = {row.target_id: row for row in self.repository.list_targets()}
        # 利用者登録済みの同名対象は重複投入しません。
        self.assertEqual(len(presets), 2)
        separate = CardAnalysisRepository(Path(self.temporary.name) / "presets.sqlite3")
        initial = separate.seed_presets()
        one = initial[0]
        separate.save_target(target_id=one.target_id, name="変更した名称", enabled=False)
        seeded = separate.seed_presets()
        self.assertEqual(len(seeded), 2)
        self.assertFalse(next(row for row in seeded if row.target_id == one.target_id).enabled)

    def test_only_completed_recordings_and_enabled_targets_can_create_jobs(self) -> None:
        add_recording(self.database, "recording-now", state="recording")
        with self.assertRaises(CardAnalysisError):
            self.repository.create_job("recording-now", fingerprint=self.fingerprint, analyzer_version="test")
        for target in self.repository.list_targets():
            self.repository.save_target(target_id=target.target_id, name=target.name, enabled=False)
        with self.assertRaisesRegex(CardAnalysisError, "対象"):
            self.repository.create_job("video", fingerprint=self.fingerprint, analyzer_version="test")

    def test_duplicate_jobs_and_multiple_running_are_rejected_and_recovered(self) -> None:
        first = self.job()
        with self.assertRaises(CardAnalysisConflictError):
            self.repository.create_job("video", fingerprint=self.fingerprint, analyzer_version="test")
        add_recording(self.database, "another")
        second = self.repository.create_job("another", fingerprint=self.fingerprint, analyzer_version="test")
        with self.assertRaises(CardAnalysisConflictError):
            self.repository.claim_job(second.job_id)
        self.repository.update_progress(first.job_id, 0.6)
        self.repository.update_progress(first.job_id, 0.3)
        self.assertEqual(self.repository.get_job(first.job_id).progress, 0.6)
        self.assertEqual(self.repository.recover_running(), 1)
        self.assertEqual(self.repository.get_job(first.job_id).state, "waiting")
        self.repository.claim_job(first.job_id)
        self.assertEqual(self.repository.get_job(first.job_id).progress, 0)

    def test_job_fps_uses_same_bounds_as_settings_and_backend(self) -> None:
        for value in (0, 0.24, 2.01, 5, float("nan"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.repository.create_job("video", fingerprint=self.fingerprint, analyzer_version="test", sample_fps=value)
        for value in (0.25, 2.0):
            job = self.repository.create_job("video", fingerprint=self.fingerprint, analyzer_version="test", sample_fps=value)
            self.assertEqual(job.sample_fps, value)
            self.repository.cancel_job(job.job_id)

    def test_queued_empty_hash_is_filled_only_after_matching_full_hash(self) -> None:
        job = self.job(fingerprint=replace(self.fingerprint, sha256=""))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.repository.complete_job(job.job_id, ())
        with self.assertRaisesRegex(CardAnalysisError, "fingerprint"):
            self.repository.complete_job(job.job_id, (), fingerprint=replace(self.fingerprint, size_bytes=101))
        completed = self.repository.complete_job(job.job_id, (), fingerprint=self.fingerprint,
                                                 result_metadata={"coverage": "sampled", "frames_processed": 30})
        self.assertEqual(completed.fingerprint.sha256, "a" * 64)
        self.assertEqual(completed.result_metadata["frames_processed"], 30)
        self.assertEqual(completed.state, "completed")

    def test_known_hash_change_cannot_replace_successful_results(self) -> None:
        original = self.finish(self.values())[0]
        next_job = self.job()
        with self.assertRaises(CardAnalysisError):
            self.repository.complete_job(next_job.job_id, (), fingerprint=replace(self.fingerprint, sha256="b" * 64))
        self.repository.fail_job(next_job.job_id, "source_changed")
        self.assertEqual(self.repository.get_review("video").observations, (original,))
        self.assertEqual(self.repository.get_review("video").job.state, "failed")
        self.assertEqual(self.repository.get_review("video").successful_job.generation, 1)

    def test_failed_and_deferred_reanalysis_keep_last_successful_candidates(self) -> None:
        original = self.finish(self.values())[0]
        failure = self.job()
        self.repository.fail_job(failure.job_id, "OCR failed")
        interrupted = self.job()
        self.repository.defer_job(interrupted.job_id, "録画を優先")
        self.assertEqual(self.repository.list_observations("video"), (original,))
        self.repository.claim_job(interrupted.job_id)
        self.repository.complete_job(interrupted.job_id, (self.values("new", time=2000),))
        rows = self.repository.list_observations("video")
        self.assertEqual([row.values.source_key for row in rows], ["new"])

    def test_successful_reanalysis_preserves_all_human_decisions_and_corrections(self) -> None:
        rows = self.finish(*(self.values(str(index), time=1000 * index) for index in (1, 2, 3, 4)))
        confirmed = self.repository.update_observation(rows[0].event_id, expected_revision=1, status="confirmed",
                                                       target_id=self.other.target_id, card_name=self.other.name,
                                                       actor="self", action="special_summon", actual_ms=750, is_opener=True)
        rejected = self.repository.reject_observation(rows[1].event_id, expected_revision=1)
        edited = self.repository.update_observation(rows[2].event_id, expected_revision=1, card_name="人の訂正")
        new_job = self.job()
        self.repository.complete_job(new_job.job_id, tuple(self.values(str(index), time=1000 * index) for index in (1, 2, 3, 5)))
        current = {row.event_id: row for row in self.repository.list_observations("video")}
        self.assertEqual(current[confirmed.event_id], confirmed)
        self.assertEqual(current[rejected.event_id], rejected)
        self.assertEqual(current[edited.event_id], edited)
        self.assertNotIn(rows[3].event_id, current)
        self.assertEqual(len(current), 4)
        self.assertTrue(current[confirmed.event_id].is_opener)

    def test_completion_validation_is_atomic_and_does_not_delete_previous_candidates(self) -> None:
        original = self.finish(self.values())[0]
        job = self.job()
        with self.assertRaises(ValueError):
            self.repository.complete_job(job.job_id, (self.values("new"), self.values("out-of-range", time=61000)))
        self.assertEqual(self.repository.get_observation(original.event_id), original)
        self.assertEqual(self.repository.get_job(job.job_id).state, "running")
        with self.assertRaises(ValueError):
            self.repository.complete_job(job.job_id, (self.values("duplicate"), self.values("duplicate")))

    def test_target_added_after_job_is_not_allowed_in_automatic_results(self) -> None:
        job = self.job()
        later = self.repository.save_target(name="新規対象")
        with self.assertRaisesRegex(ValueError, "snapshot"):
            self.repository.complete_job(job.job_id, (self.values(target=later.target_id),))

    def test_revision_conflict_cannot_overwrite_human_edit(self) -> None:
        row = self.finish(self.values())[0]
        edited = self.repository.update_observation(row.event_id, expected_revision=1, card_name="訂正名")
        with self.assertRaises(CardAnalysisConflictError):
            self.repository.reject_observation(row.event_id, expected_revision=1)
        self.assertEqual(self.repository.get_observation(row.event_id), edited)

    def test_nonuse_and_confirmed_use_cannot_contradict(self) -> None:
        row = self.finish(self.values())[0]
        nonuse = self.repository.set_nonuse("video", self.target.target_id, "opponent")
        with self.assertRaises(CardAnalysisConflictError):
            self.repository.confirm_observation(row.event_id, expected_revision=1)
        with self.assertRaises(CardAnalysisConflictError):
            self.repository.set_nonuse("video", self.target.target_id, "opponent", False, expected_revision=0)
        self.repository.set_nonuse("video", self.target.target_id, "opponent", False, expected_revision=nonuse.revision)
        self.repository.confirm_observation(row.event_id, expected_revision=1)
        with self.assertRaises(CardAnalysisConflictError):
            self.repository.set_nonuse("video", self.target.target_id, "opponent", True)

    def test_marker_rejection_or_actor_correction_preserves_canonical_opener(self) -> None:
        row = self.finish(self.values())[0]
        with self.assertRaises(ValueError):
            self.repository.set_opener("video", "opponent", event_id=row.event_id)
        confirmed = self.repository.confirm_observation(row.event_id, expected_revision=1)
        opener = self.repository.set_opener("video", "opponent", event_id=row.event_id)
        with self.assertRaises(CardAnalysisConflictError):
            self.repository.set_opener("video", "opponent", status="unknown", expected_revision=opener.revision - 1)
        corrected = self.repository.update_observation(confirmed.event_id, expected_revision=confirmed.revision, actor="self")
        self.assertFalse(corrected.is_opener)
        self.assertEqual(self.repository.get_review("video").openers[0].status, "confirmed")
        self.assertEqual(self.repository.get_review("video").openers[0].actor, "opponent")
        self.repository.reject_observation(row.event_id, expected_revision=corrected.revision)
        self.assertEqual(self.repository.get_review("video").openers[0].status, "confirmed")
        self.assertEqual(self.repository.get_review("video").information[0].actor, "opponent")
        self.assertEqual(self.repository.get_review("video").information[0].state, "used")

    def test_detail_display_does_not_support_use_until_human_supplies_evidence(self) -> None:
        row = self.finish(self.values(supports=False, action="unknown"))[0]
        confirmed = self.repository.confirm_observation(row.event_id, expected_revision=1)
        with self.assertRaises(ValueError):
            self.repository.set_opener("video", "opponent", event_id=row.event_id)
        edited = self.repository.update_observation(confirmed.event_id, expected_revision=confirmed.revision,
                                                    action="activate", evidence={"supports_use": True, "manually_confirmed_use": True},
                                                    status="confirmed", is_opener=True)
        self.assertTrue(edited.is_opener)
        self.assertTrue(edited.values.supports_use)
        json.dumps(self.repository.get_review("video").to_dict(), ensure_ascii=False)

    def test_opener_revision_prevents_a_stale_editor_from_overwriting_another_choice(self) -> None:
        first, second = self.finish(self.values("first", actor="self"), self.values("second", actor="self", target=self.other.target_id, time=2000))
        first = self.repository.confirm_observation(first.event_id, expected_revision=1)
        second = self.repository.confirm_observation(second.event_id, expected_revision=1)
        initial = self.repository.set_opener("video", "self", event_id=first.event_id)
        changed = self.repository.set_opener("video", "self", event_id=second.event_id,
                                              expected_revision=initial.revision)
        with self.assertRaises(CardAnalysisConflictError):
            self.repository.update_observation(first.event_id, expected_revision=first.revision,
                                               card_name="must not be saved", is_opener=True,
                                               opener_expected_revision=initial.revision)
        with self.assertRaises(CardAnalysisConflictError):
            self.repository.update_observation(first.event_id, expected_revision=first.revision, is_opener=True)
        self.assertEqual(self.repository.get_observation(first.event_id).card_name, self.target.name)
        self.assertEqual(self.repository.get_review("video").openers[0].revision, changed.revision)
        self.assertEqual(self.repository.get_review("video").openers[0].event_id, second.event_id)

    def checkpoint(self, job, *, frame_index: int = 300):
        return {
            "schema": "card-analysis-checkpoint-v1", "analyzer_version": job.analyzer_version,
            "source_fingerprint": self.fingerprint.to_dict(),
            "targets_sha256": hashlib.sha256(json.dumps(
                [target.to_dict() for target in job.target_snapshot], sort_keys=True, ensure_ascii=True, allow_nan=False,
            ).encode()).hexdigest(),
            "sample_fps": job.sample_fps, "source_fps": 30.0, "reported_frames": 1800,
            "next_frame_index": frame_index, "next_sample_ms": 10000.0,
            "last_timestamp": 9966.67, "observations": [self.values("staged-only").to_dict()],
            "state": {"display_series": {"last_log": "sample"}}, "timestamp_fallback": False,
            "warnings": ["限定した対象の解析です"],
        }

    def test_checkpoint_persists_resume_state_hash_progress_without_publishing_candidates(self) -> None:
        original = self.finish(self.values())[0]
        job = self.job(fingerprint=replace(self.fingerprint, sha256=""))
        checkpoint = self.checkpoint(job)
        saved = self.repository.update_checkpoint(job.job_id, checkpoint, progress=0.4)
        self.assertEqual(saved.fingerprint.sha256, self.fingerprint.sha256)
        self.assertEqual(saved.result_metadata["checkpoint"], checkpoint)
        self.assertEqual(self.repository.list_observations("video"), (original,))
        checkpoint["state"]["display_series"]["last_log"] = "caller mutation"
        self.assertEqual(self.repository.get_job(job.job_id).result_metadata["checkpoint"]["state"]["display_series"]["last_log"], "sample")
        self.repository.defer_job(job.job_id, "録画開始")
        claimed = self.repository.claim_job(job.job_id)
        self.assertEqual(claimed.progress, 0.4)
        self.assertEqual(self.repository.recover_running(), 1)
        recovered = self.repository.get_job(job.job_id)
        self.assertEqual(recovered.progress, 0.4)
        self.assertEqual(recovered.result_metadata["checkpoint"]["next_frame_index"], 300)
        self.repository.claim_job(job.job_id)
        self.repository.complete_job(job.job_id, (self.values("finished"),), result_metadata={"frames_processed": 120, "checkpoint": checkpoint})
        finished = self.repository.get_job(job.job_id)
        self.assertNotIn("checkpoint", finished.result_metadata)
        self.assertEqual(finished.result_metadata["frames_processed"], 120)
        self.assertEqual([row.values.source_key for row in self.repository.list_observations("video")], ["finished"])

    def test_checkpoint_rejects_source_snapshot_version_fps_and_state_mismatches_atomically(self) -> None:
        job = self.job(fingerprint=replace(self.fingerprint, sha256=""))
        valid = self.checkpoint(job)
        invalids = [
            {**valid, "schema": "unknown"}, {**valid, "analyzer_version": "another"},
            {**valid, "sample_fps": 1}, {**valid, "targets_sha256": "b" * 64},
            {**valid, "source_fingerprint": replace(self.fingerprint, size_bytes=101).to_dict()},
            {**valid, "source_fingerprint": replace(self.fingerprint, sha256="").to_dict()},
            {**valid, "next_frame_index": -1}, {**valid, "state": []},
            {**valid, "source_fps": 0}, {**valid, "warnings": [123]},
        ]
        for checkpoint in invalids:
            with self.subTest(checkpoint=checkpoint), self.assertRaises((CardAnalysisError, ValueError)):
                self.repository.update_checkpoint(job.job_id, checkpoint, progress=0.5)
            current = self.repository.get_job(job.job_id)
            self.assertEqual(current.fingerprint.sha256, "")
            self.assertEqual(current.result_metadata, {})
            self.assertEqual(current.progress, 0)
        self.repository.update_checkpoint(job.job_id, valid, progress=0.5)
        with self.assertRaises(CardAnalysisConflictError):
            self.repository.update_checkpoint(job.job_id, self.checkpoint(job, frame_index=1), progress=0.1)
        with self.assertRaises(CardAnalysisError):
            self.repository.update_checkpoint(job.job_id, {**valid, "source_fingerprint": replace(self.fingerprint, sha256="b" * 64).to_dict()})
        self.repository.defer_job(job.job_id)
        with self.assertRaises(CardAnalysisConflictError):
            self.repository.update_checkpoint(job.job_id, valid)


if __name__ == "__main__":
    unittest.main()
