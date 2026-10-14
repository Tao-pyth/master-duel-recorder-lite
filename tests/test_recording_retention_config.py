import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from master_duel_recorder_lite.__main__ import main
from master_duel_recorder_lite.config import (
    GIB_BYTES,
    MAX_RECORDING_RETENTION_DAYS,
    MAX_RECORDING_STORAGE_GIB,
    SECONDS_PER_DAY,
    AppConfig,
    AppConfigError,
    load_app_config,
    save_app_config,
    validate_app_config,
)
from master_duel_recorder_lite.config_management import (
    ConfigValueError,
    config_values,
    updated_config_values,
)
from master_duel_recorder_lite.runtime_paths import default_runtime_paths


class RecordingRetentionConfigTest(unittest.TestCase):
    def test_missing_and_old_config_default_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir))
            config_path = paths.config / "app.toml"
            missing = load_app_config(user_data_dir=paths.root)
            self.assertFalse(config_path.exists())
            config_path.parent.mkdir(parents=True)
            original = b'[upload]\nprivacy_status = "unlisted"\n'
            config_path.write_bytes(original)

            old = load_app_config(user_data_dir=paths.root)

            self.assertEqual(config_path.read_bytes(), original)
            self.assertFalse(config_path.with_name("app.toml.previous").exists())
            for loaded in (missing, old):
                self.assertEqual(loaded.config.recording_storage_max_gib, 0)
                self.assertEqual(loaded.config.recording_retention_days, 0)
                self.assertFalse(loaded.config.upload_delete_source_after_upload)

    def test_settings_round_trip_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir))
            for capacity, days, upload in ((0, 0, False), (10, 0, False), (0, 30, True), (10, 30, True)):
                with self.subTest(capacity=capacity, days=days, upload=upload):
                    expected = AppConfig(
                        recording_storage_max_gib=capacity,
                        recording_retention_days=days,
                        upload_delete_source_after_upload=upload,
                    )
                    save_app_config(paths=paths, config=expected)
                    actual = load_app_config(user_data_dir=paths.root).config

                    self.assertEqual(actual, expected)
                    self.assertEqual(actual.recording_storage_max_bytes, capacity * 1_073_741_824)
                    self.assertEqual(actual.recording_retention_seconds, days * 86_400)

    def test_integer_conversion_bounds(self) -> None:
        config = AppConfig(
            recording_storage_max_gib=MAX_RECORDING_STORAGE_GIB,
            recording_retention_days=MAX_RECORDING_RETENTION_DAYS,
        )
        validate_app_config(config)

        self.assertEqual(GIB_BYTES, 1_073_741_824)
        self.assertEqual(SECONDS_PER_DAY, 86_400)
        self.assertLessEqual(config.recording_storage_max_bytes, 2**63 - 1)
        self.assertLessEqual(config.recording_retention_seconds, 2**63 - 1)
        self.assertGreater((MAX_RECORDING_STORAGE_GIB + 1) * GIB_BYTES, 2**63 - 1)
        self.assertGreater((MAX_RECORDING_RETENTION_DAYS + 1) * SECONDS_PER_DAY, 2**63 - 1)
        for attribute, value in (
            ("recording_storage_max_gib", MAX_RECORDING_STORAGE_GIB + 1),
            ("recording_retention_days", MAX_RECORDING_RETENTION_DAYS + 1),
        ):
            with self.subTest(attribute=attribute), self.assertRaises(ValueError):
                invalid = replace(config, **{attribute: value})
                validate_app_config(invalid)

    def test_invalid_file_values_fail_without_rewriting(self) -> None:
        invalid_documents = [
            f"[storage]\n{key} = {value}\n"
            for key in ("max_gib", "retention_days")
            for value in ("-1", "true", "1.5", '"1"', str(2**63))
        ] + [
            f"[upload]\ndelete_source_after_upload = {value}\n"
            for value in ("0", "1", '"true"', "2026-10-15")
        ] + ["storage = 10\n"]
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir))
            config_path = paths.config / "app.toml"
            config_path.parent.mkdir(parents=True)
            for document in invalid_documents:
                with self.subTest(document=document):
                    config_path.write_text(document, encoding="utf-8")
                    original = config_path.read_bytes()
                    with self.assertRaises(AppConfigError):
                        load_app_config(user_data_dir=paths.root)
                    self.assertEqual(config_path.read_bytes(), original)

    def test_invalid_in_memory_values_do_not_replace_saved_generation(self) -> None:
        invalid_values = [
            (attribute, value)
            for attribute in ("recording_storage_max_gib", "recording_retention_days")
            for value in (-1, True, False, 1.5, "1", None, 2**63)
        ] + [("upload_delete_source_after_upload", value) for value in (0, 1, "true", None)]
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir))
            config_path = save_app_config(paths=paths, config=AppConfig(recording_retention_days=30))
            original = config_path.read_bytes()
            for attribute, value in invalid_values:
                with self.subTest(attribute=attribute, value=value):
                    with self.assertRaises(ValueError):
                        save_app_config(paths=paths, config=replace(AppConfig(), **{attribute: value}))
                    self.assertEqual(config_path.read_bytes(), original)
                    self.assertFalse(config_path.with_name("app.toml.previous").exists())

    def test_batch_failure_preserves_config_and_saved_values(self) -> None:
        original = AppConfig(recording_storage_max_gib=10, recording_retention_days=30)
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir))
            config_path = save_app_config(paths=paths, config=original)
            original_bytes = config_path.read_bytes()
            for changes in (
                {"storage.max_gib": "20", "storage.retention_days": "-1"},
                {"storage.max_gib": "20", "upload.delete_source_after_upload": "yes"},
                {"storage.max_gib": "true", "storage.retention_days": "60"},
            ):
                with self.subTest(changes=changes), self.assertRaises(ConfigValueError):
                    updated_config_values(original, changes)

            self.assertEqual(config_path.read_bytes(), original_bytes)
            self.assertEqual(load_app_config(user_data_dir=paths.root).config, original)
        updated = updated_config_values(original, {
            "storage.max_gib": "20",
            "storage.retention_days": "60",
            "upload.delete_source_after_upload": "true",
        })
        self.assertEqual(config_values(updated)["storage.max_gib"], 20)
        self.assertEqual(updated.recording_retention_days, 60)
        self.assertTrue(updated.upload_delete_source_after_upload)
        self.assertFalse(original.upload_delete_source_after_upload)

    def test_failed_atomic_replace_keeps_original_and_previous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir))
            config_path = save_app_config(paths=paths, config=AppConfig(recording_storage_max_gib=10))
            original = config_path.read_bytes()
            from master_duel_recorder_lite import config as config_module

            original_replace = config_module.os.replace

            def fail_current(source: Path, destination: Path) -> None:
                if destination == config_path:
                    raise OSError("simulated atomic write failure")
                original_replace(source, destination)

            with patch.object(config_module.os, "replace", side_effect=fail_current):
                with self.assertRaises(AppConfigError):
                    save_app_config(paths=paths, config=AppConfig(recording_storage_max_gib=20))

            self.assertEqual(config_path.read_bytes(), original)
            self.assertEqual(config_path.with_name("app.toml.previous").read_bytes(), original)
            self.assertEqual(list(config_path.parent.glob("*.tmp")), [])

    def test_reloading_observes_latest_saved_flag_and_rejects_read_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir))
            saved = AppConfig(upload_delete_source_after_upload=True)
            save_app_config(paths=paths, config=saved)
            initial_snapshot = load_app_config(user_data_dir=paths.root).config
            save_app_config(paths=paths, config=replace(saved, upload_delete_source_after_upload=False))
            self.assertFalse(load_app_config(user_data_dir=paths.root).config.upload_delete_source_after_upload)
            self.assertTrue(initial_snapshot.upload_delete_source_after_upload)
            save_app_config(paths=paths, config=saved)
            self.assertTrue(load_app_config(user_data_dir=paths.root).config.upload_delete_source_after_upload)

            with patch.object(Path, "read_text", side_effect=OSError("simulated read failure")):
                with self.assertRaises(AppConfigError):
                    load_app_config(user_data_dir=paths.root)

    def test_cli_retention_set_get_show_and_invalid_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            prefix = ["--user-data-dir", str(root), "config"]
            expected = {
                "storage.max_gib": 20,
                "storage.retention_days": 60,
                "upload.delete_source_after_upload": True,
            }
            for key, value in expected.items():
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main([*prefix, "set", key, str(value).lower()]), 0)
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main([*prefix, "get", key, "--json"]), 0)
                self.assertEqual(json.loads(output.getvalue()), {"key": key, "value": value})
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([*prefix, "show", "--json"]), 0)
            values = json.loads(output.getvalue())["values"]
            for key, value in expected.items():
                self.assertEqual(values[key], value)

            config_path = root / "config" / "app.toml"
            original = config_path.read_bytes()
            for key, value in (
                ("storage.max_gib", "true"),
                ("storage.retention_days", "-1"),
                ("upload.delete_source_after_upload", "1"),
            ):
                with self.subTest(key=key), redirect_stderr(io.StringIO()):
                    self.assertEqual(main([*prefix, "set", key, value]), 2)
                self.assertEqual(config_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
