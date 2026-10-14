from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from contextlib import closing
import sqlite3
from unittest.mock import patch

from master_duel_recorder_lite.data_reconciliation import DataReconciliationService
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.recording_history import (
    RecordingHistoryError,
    RecordingHistoryRepository,
)
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.duel_card_repository import DuelCardRepository
from master_duel_recorder_lite.duel_timeline import DuelTimelineRepository
from master_duel_recorder_lite.video_lifecycle import VideoLifecycleRepository
from master_duel_recorder_lite.recording_retention import fingerprint_file
from master_duel_recorder_lite.video_source_use import video_source_use


class DataReconciliationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.paths = default_runtime_paths(user_data_dir=Path(self.temporary.name))
        ensure_runtime_dirs(self.paths)
        self.history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        self.duels = DuelRecordRepository.from_runtime_paths(self.paths)
        self.service = DataReconciliationService(self.history)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _recording(
        self, identifier: str, payload: bytes, *, created_at: datetime | None = None
    ) -> Path:
        path = self.paths.recordings / f"{identifier}.mkv"
        path.write_bytes(payload)
        self.history.register_starting(
            recording_id=identifier,
            output_path=path,
            container="mkv",
            source="manual",
            created_at=created_at or datetime.now(timezone.utc),
        )
        return path

    def test_relink_requires_preview_and_updates_reference_only(self) -> None:
        original = self._recording("one", b"old")
        candidate = self.paths.recordings / "moved" / "one.mkv"
        candidate.parent.mkdir()
        candidate.write_bytes(b"new-video")

        preview = self.service.preview_relink("one", candidate)
        self.service.relink(preview)

        entry = self.history.get("one")
        assert entry is not None
        self.assertEqual(entry.output_path, Path("moved/one.mkv"))
        self.assertTrue(original.is_file())
        self.assertTrue(candidate.is_file())

    def test_relink_rejects_outside_used_and_modified_files(self) -> None:
        self._recording("one", b"one")
        used = self._recording("two", b"two")
        outside = Path(self.temporary.name).parent / "outside.mkv"
        outside.write_bytes(b"outside")
        self.addCleanup(outside.unlink, missing_ok=True)

        with self.assertRaisesRegex(RecordingHistoryError, "配下"):
            self.service.preview_relink("one", outside)
        with self.assertRaisesRegex(RecordingHistoryError, "使用中"):
            self.service.preview_relink("one", used)

        candidate = self.paths.recordings / "candidate.mkv"
        candidate.write_bytes(b"before")
        preview = self.service.preview_relink("one", candidate)
        candidate.write_bytes(b"after")
        with self.assertRaisesRegex(RecordingHistoryError, "変更"):
            self.service.relink(preview)

    def _completed_video_with_marker(self):
        source = self._recording("one", b"original-video")
        with closing(sqlite3.connect(self.history.database_path)) as connection, connection:
            connection.execute("UPDATE recordings SET state='completed',ended_at=created_at,size_bytes=? WHERE recording_id='one'", (source.stat().st_size,))
        marker = DuelTimelineRepository.from_runtime_paths(self.paths).add("one", elapsed_ms=100, event_type="marker", label="old marker")
        lifecycle = VideoLifecycleRepository.from_runtime_paths(self.paths)
        asset = lifecycle.get_current_asset("one")
        duel = self.duels.save("one", DuelRecordValues(status="confirmed", result="win"), expected_revision=0)
        cards = DuelCardRepository.from_runtime_paths(self.paths)
        identity = cards.save_identity(name="Meaning stays")
        cards.save_information(duel.duel_id, identity.card_id, "self", "used", is_opener=True)
        return source, lifecycle, asset, marker, cards, duel

    def test_replacement_changes_generation_and_keeps_confirmed_information(self) -> None:
        source, lifecycle, old, marker, cards, duel = self._completed_video_with_marker()
        before = cards.get_review(duel.duel_id)
        candidate = self.paths.recordings / "replacement.mkv"
        candidate.write_bytes(b"different-video")
        self.service.relink(self.service.preview_relink("one", candidate))
        current = lifecycle.get_current_asset("one")
        self.assertNotEqual(current.asset_id, old.asset_id)
        self.assertEqual(current.generation, old.generation + 1)
        self.assertEqual(cards.get_review(duel.duel_id), before)
        events = DuelTimelineRepository.from_runtime_paths(self.paths).list("one")
        self.assertEqual(events[0].event_id, marker.event_id)
        self.assertFalse(events[0].video_verified)
        self.assertTrue(source.exists())

    def test_identical_content_move_keeps_asset_and_marker(self) -> None:
        source, lifecycle, old, _marker, _cards, _duel = self._completed_video_with_marker()
        candidate = self.paths.recordings / "same-content.mkv"
        candidate.write_bytes(source.read_bytes())
        self.service.relink(self.service.preview_relink("one", candidate))
        current = lifecycle.get_current_asset("one")
        self.assertEqual((current.asset_id, current.generation), (old.asset_id, old.generation))
        self.assertEqual(current.path, "same-content.mkv")
        self.assertTrue(DuelTimelineRepository.from_runtime_paths(self.paths).list("one")[0].video_verified)

    def test_source_use_prevents_relink_before_any_metadata_change(self) -> None:
        self._completed_video_with_marker()
        candidate = self.paths.recordings / "replacement.mkv"
        candidate.write_bytes(b"different-video")
        preview = self.service.preview_relink("one", candidate)
        with video_source_use(self.paths, "one", purpose="fixture-reader"):
            with self.assertRaisesRegex(RecordingHistoryError, "利用中"):
                self.service.relink(preview)
        self.assertEqual(self.history.get("one").output_path, Path("one.mkv"))

    def test_late_file_change_rolls_back_recording_and_asset(self) -> None:
        source, lifecycle, old, _marker, cards, duel = self._completed_video_with_marker()
        candidate = self.paths.recordings / "replacement.mkv"
        candidate.write_bytes(b"different-video")
        preview = self.service.preview_relink("one", candidate)
        before = cards.get_review(duel.duel_id)
        calls = 0
        def changing(path):
            nonlocal calls
            calls += 1
            if calls == 3:
                candidate.write_bytes(b"late-change")
            return fingerprint_file(path)
        with patch("master_duel_recorder_lite.data_reconciliation.fingerprint_file", side_effect=changing):
            with self.assertRaisesRegex(RecordingHistoryError, "変更"):
                self.service.relink(preview)
        self.assertEqual(self.history.get("one").output_path, Path("one.mkv"))
        self.assertEqual(lifecycle.get_current_asset("one").asset_id, old.asset_id)
        self.assertEqual(cards.get_review(duel.duel_id), before)
        self.assertTrue(source.is_file())

    def test_duplicate_detection_finds_strong_manual_match_only(self) -> None:
        occurred = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
        common = DuelRecordValues(
            status="confirmed",
            result="win",
            play_order="first",
            own_deck="青眼",
            duel_type="ranked",
        )
        first = self.duels.create_manual(common, occurred_at=occurred)
        second = self.duels.create_manual(
            common, occurred_at=occurred + timedelta(seconds=20)
        )
        self.duels.create_manual(
            DuelRecordValues(
                status="confirmed",
                result="loss",
                play_order="second",
                own_deck="白き森",
                duel_type="event",
            ),
            occurred_at=occurred + timedelta(seconds=25),
        )

        candidates = self.service.duplicate_candidates()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            {candidates[0].left_duel_id, candidates[0].right_duel_id},
            {first.duel_id, second.duel_id},
        )
        self.assertGreaterEqual(candidates[0].score, 70)

    def test_different_recording_hashes_are_not_duplicate_without_strong_metadata(self) -> None:
        occurred = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
        self._recording("one", b"video-one", created_at=occurred)
        self._recording(
            "two", b"video-two", created_at=occurred + timedelta(seconds=10)
        )
        self.duels.save(
            "one",
            DuelRecordValues(status="confirmed", result="win"),
            expected_revision=0,
        )
        self.duels.save(
            "two",
            DuelRecordValues(status="confirmed", result="loss"),
            expected_revision=0,
        )

        self.assertEqual(self.service.duplicate_candidates(), ())


if __name__ == "__main__":
    unittest.main()
