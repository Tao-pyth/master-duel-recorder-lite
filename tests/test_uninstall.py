from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from master_duel_recorder_lite.__main__ import main
from master_duel_recorder_lite.runtime_paths import default_runtime_paths
from master_duel_recorder_lite.uninstall import (
    UninstallError,
    create_uninstall_plan,
    execute_cleanup,
    launch_cleanup_worker,
    run_cleanup_manifest,
    validate_runtime_root,
)


class UninstallTest(unittest.TestCase):
    def test_plan_inventories_all_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "user_data"
            paths = default_runtime_paths(user_data_dir=root)
            (paths.config / "app.toml").parent.mkdir(parents=True)
            (paths.config / "app.toml").write_bytes(b"config")
            (paths.recordings / "duel.mkv").parent.mkdir(parents=True)
            (paths.recordings / "duel.mkv").write_bytes(b"recording")
            (root / "tools" / "ffmpeg" / "ffmpeg.exe").parent.mkdir(parents=True)
            (root / "tools" / "ffmpeg" / "ffmpeg.exe").write_bytes(b"tool")

            plan = create_uninstall_plan(paths)

            self.assertEqual(plan.runtime_root, root.resolve())
            self.assertEqual(plan.file_count, 3)
            self.assertGreaterEqual(plan.directory_count, 6)
            self.assertEqual(plan.total_bytes, len(b"configrecordingtool"))

    def test_cleanup_removes_runtime_root_and_selected_portable_executable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "user_data"
            paths = default_runtime_paths(user_data_dir=root)
            (paths.data / "db" / "history.sqlite3").parent.mkdir(parents=True)
            (paths.data / "db" / "history.sqlite3").write_bytes(b"db")
            executable = base / "mdrl.exe"
            executable.write_bytes(b"exe")
            outside = base / "keep.txt"
            outside.write_text("keep", encoding="utf-8")
            plan = create_uninstall_plan(
                paths,
                remove_executable=True,
                executable=executable,
                frozen=True,
            )

            execute_cleanup(plan)

            self.assertFalse(root.exists())
            self.assertFalse(executable.exists())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_cleanup_removes_runtime_root_pointer_that_selects_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "selected-data"
            (root / "logs").mkdir(parents=True)
            pointer = base / "runtime-root.json"
            pointer.write_text(
                json.dumps({"runtime_root": str(root.resolve())}), encoding="utf-8"
            )
            paths = default_runtime_paths(user_data_dir=root)
            with (
                patch(
                    "master_duel_recorder_lite.data_location.runtime_root_pointer_path",
                    return_value=pointer,
                ),
            ):
                plan = create_uninstall_plan(paths)
                execute_cleanup(plan)

            self.assertFalse(root.exists())
            self.assertFalse(pointer.exists())

    def test_cleanup_does_not_follow_directory_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "user_data"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            protected = outside / "protected.txt"
            protected.write_text("keep", encoding="utf-8")
            try:
                (root / "linked").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("シンボリックリンクを作成できない環境です")

            execute_cleanup(
                create_uninstall_plan(default_runtime_paths(user_data_dir=root))
            )

            self.assertEqual(protected.read_text(encoding="utf-8"), "keep")

    def test_cleanup_removes_readonly_runtime_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "user_data"
            target = root / "config" / "app.toml"
            target.parent.mkdir(parents=True)
            target.write_text("readonly", encoding="utf-8")
            os.chmod(target, 0o444)

            execute_cleanup(
                create_uninstall_plan(default_runtime_paths(user_data_dir=root))
            )

            self.assertFalse(root.exists())

    def test_rejects_unknown_directory_and_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unknown = Path(temporary) / "ordinary-folder"
            unknown.mkdir()
            (unknown / "unrelated.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(UninstallError):
                validate_runtime_root(unknown)
        with self.assertRaises(UninstallError):
            validate_runtime_root(Path.home())

    def test_python_runtime_cannot_remove_shared_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = default_runtime_paths(user_data_dir=Path(temporary) / "user_data")
            with self.assertRaisesRegex(UninstallError, "共有Python"):
                create_uninstall_plan(paths, remove_executable=True, frozen=False)

    def test_worker_manifest_deletes_isolated_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "user_data"
            (root / "logs").mkdir(parents=True)
            (root / "logs" / "app.log").write_text("log", encoding="utf-8")
            result = base / "result.json"
            manifest = base / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "parent_pid": 0,
                        "runtime_root": str(root),
                        "executable": None,
                        "remove_executable": False,
                        "result_path": str(result),
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(run_cleanup_manifest(manifest), 0)
            self.assertFalse(root.exists())
            self.assertTrue(json.loads(result.read_text(encoding="utf-8"))["succeeded"])

    @patch("master_duel_recorder_lite.uninstall.subprocess.Popen")
    def test_launcher_uses_private_manifest(self, popen: object) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "user_data"
            root.mkdir()
            plan = create_uninstall_plan(default_runtime_paths(user_data_dir=root))
            result = launch_cleanup_worker(
                plan, module="master_duel_recorder_lite", parent_pid=123
            )
            command = popen.call_args.args[0]
            self.assertIn("--cleanup-manifest", command)
            manifest = Path(command[-1])
            document = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(document["parent_pid"], 123)
            self.assertEqual(Path(document["runtime_root"]), root.resolve())
            manifest.unlink()
            self.assertFalse(result.exists())

    @patch("master_duel_recorder_lite.__main__.launch_cleanup_worker")
    def test_cli_requires_exact_confirmation(self, launcher: object) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "user_data"
            root.mkdir()
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                self.assertEqual(main(["--user-data-dir", str(root), "uninstall"]), 4)
                launcher.assert_not_called()
                self.assertEqual(
                    main(
                        [
                            "--user-data-dir",
                            str(root),
                            "uninstall",
                            "--yes",
                            "--confirm",
                            "アンインストール",
                        ]
                    ),
                    0,
                )
            launcher.assert_called_once()


if __name__ == "__main__":
    unittest.main()
