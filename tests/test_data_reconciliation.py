from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from master_duel_recorder_lite.data_reconciliation import DataReconciliationService
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.recording_history import (
    RecordingHistoryError,
    RecordingHistoryRepository,
)
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


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
