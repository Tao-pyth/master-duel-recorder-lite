import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.update_runner import (
    UpdateRunnerConfig,
    UpdateRunnerError,
    apply_staged_update,
)


class UpdateRunnerTest(unittest.TestCase):
    def test_apply_staged_update_replaces_after_smoke_and_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            current = root / "app.exe"
            candidate = root / "candidate.exe"
            backup = root / "app.exe.previous"
            current.write_bytes(b"old")
            candidate.write_bytes(b"new")
            digest = hashlib.sha256(b"new").hexdigest()
            starts: list[list[str]] = []

            def runner(args, **_kwargs):
                output = Path(args[args.index("--smoke-output") + 1])
                output.write_text(json.dumps({"version": "1.4.3"}), encoding="utf-8")
                return subprocess.CompletedProcess(args, 0, "", "")

            def starter(args, **_kwargs):
                starts.append(list(args))
                return None  # type: ignore[return-value]

            apply_staged_update(
                UpdateRunnerConfig(
                    current=current,
                    candidate=candidate,
                    backup=backup,
                    expected_sha256=digest,
                    expected_version="1.4.3",
                ),
                process_runner=runner,
                process_starter=starter,
            )

            self.assertEqual(current.read_bytes(), b"new")
            self.assertEqual(backup.read_bytes(), b"old")
            self.assertEqual(starts, [[str(current.resolve())]])

    def test_apply_staged_update_rolls_back_when_smoke_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            current = root / "app.exe"
            candidate = root / "candidate.exe"
            backup = root / "app.exe.previous"
            current.write_bytes(b"old")
            candidate.write_bytes(b"new")
            digest = hashlib.sha256(b"new").hexdigest()

            def runner(args, **_kwargs):
                return subprocess.CompletedProcess(args, 1, "", "failed")

            with self.assertRaisesRegex(UpdateRunnerError, "起動検証"):
                apply_staged_update(
                    UpdateRunnerConfig(
                        current=current,
                        candidate=candidate,
                        backup=backup,
                        expected_sha256=digest,
                        expected_version="1.4.3",
                        restart=False,
                    ),
                    process_runner=runner,
                )

            self.assertEqual(current.read_bytes(), b"old")
            self.assertFalse(backup.exists())

    def test_apply_staged_update_rejects_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            current = root / "app.exe"
            candidate = root / "candidate.exe"
            backup = root / "app.exe.previous"
            current.write_bytes(b"old")
            candidate.write_bytes(b"new")

            with self.assertRaisesRegex(UpdateRunnerError, "SHA-256"):
                apply_staged_update(
                    UpdateRunnerConfig(
                        current=current,
                        candidate=candidate,
                        backup=backup,
                        expected_sha256="0" * 64,
                        expected_version="1.4.3",
                    )
                )

            self.assertEqual(current.read_bytes(), b"old")
