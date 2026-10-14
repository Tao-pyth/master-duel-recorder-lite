from __future__ import annotations

from pathlib import Path
import random
import tempfile
import unittest

from master_duel_recorder_lite.data_protection import DataProtectionError, DataProtectionService
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


class RetainedBackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = default_runtime_paths(user_data_dir=Path(self.temporary.name))
        ensure_runtime_dirs(self.paths)
        RecordingHistoryRepository.from_runtime_paths(self.paths)
        self.service = DataProtectionService(self.paths, max_generations=2)

    def test_full_protected_budget_rejects_new_normal_backup_without_removing_old(self) -> None:
        protected = [self.service.create_backup(f"migration-{i}", protected=True) for i in range(2)]
        with self.assertRaisesRegex(DataProtectionError, "保持"):
            self.service.create_backup("pre-video-delete")
        self.assertEqual({item.path for item in self.service.list_backups()}, {item.path for item in protected})
        self.assertTrue(all(item.path.is_file() for item in protected))

    def test_backup_larger_than_budget_is_never_returned_as_success(self) -> None:
        service = DataProtectionService(self.paths, max_bytes=1024 * 1024)
        # 圧縮で小さくならない隔離fixtureを使い、容量不足を再現します。
        (self.paths.config / "app.toml").write_bytes(random.Random(0).randbytes(2 * 1024 * 1024))
        with self.assertRaisesRegex(DataProtectionError, "容量"):
            service.create_backup("pre-video-delete")
        self.assertEqual(service.list_backups(), ())

    def test_retained_backup_blocks_other_creations_and_rotation_until_operation_ends(self) -> None:
        other = DataProtectionService(self.paths, max_generations=2)
        with self.service.retained_backup("pre-video-delete") as backup:
            with self.assertRaisesRegex(DataProtectionError, "実行中"):
                other.create_backup("concurrent")
            with self.assertRaisesRegex(DataProtectionError, "実行中"):
                other.rotate()
            self.assertEqual(other.inspect_backup(backup.path).sha256, backup.sha256)
        other.create_backup("later-1")
        other.create_backup("later-2")
        self.assertFalse(backup.path.exists())
        self.assertEqual(len(other.list_backups()), 2)

    def test_exception_releases_retention_lock_without_deleting_backup(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "fixture"):
            with self.service.retained_backup("pre-video-delete") as backup:
                raise RuntimeError("fixture failure")
        self.assertTrue(backup.path.is_file())
        self.service.rotate()
        self.service.create_backup("next")

    def test_protected_migration_backups_remain_available_above_normal_budget(self) -> None:
        for index in range(3):
            self.service.create_backup(f"migration-{index}", protected=True)
        self.service.rotate()
        self.assertEqual(len(self.service.list_backups()), 3)


if __name__ == "__main__":
    unittest.main()
