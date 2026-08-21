from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from . import __version__


ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]
ProcessStarter = Callable[..., subprocess.Popen[object]]


class UpdateRunnerError(RuntimeError):
    """専用updaterで更新を適用できない場合のエラーです。"""


@dataclass(frozen=True)
class UpdateRunnerConfig:
    current: Path
    candidate: Path
    backup: Path
    expected_sha256: str
    expected_version: str
    parent_pid: int | None = None
    smoke_timeout_seconds: float = 20.0
    restart: bool = True


def apply_staged_update(
    config: UpdateRunnerConfig,
    *,
    process_runner: ProcessRunner = subprocess.run,
    process_starter: ProcessStarter = subprocess.Popen,
) -> None:
    current = config.current.expanduser().resolve()
    candidate = config.candidate.expanduser().resolve()
    backup = config.backup.expanduser().resolve()
    if config.parent_pid is not None:
        _wait_for_process_exit(config.parent_pid)
    _verify_file(candidate, "更新候補EXE")
    if _sha256(candidate) != config.expected_sha256.lower():
        raise UpdateRunnerError("更新候補EXEのSHA-256が一致しません")
    if not current.is_file():
        raise UpdateRunnerError(f"現在のEXEが見つかりません: {current}")
    staged = current.with_name(f"{current.name}.staged")
    staged.unlink(missing_ok=True)
    backup.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(candidate, staged)
        if _sha256(staged) != config.expected_sha256.lower():
            raise UpdateRunnerError("staging後の更新EXEのSHA-256が一致しません")
        os.replace(current, backup)
        os.replace(staged, current)
        try:
            _smoke_updated_executable(
                current,
                expected_version=config.expected_version,
                timeout_seconds=config.smoke_timeout_seconds,
                process_runner=process_runner,
            )
        except Exception:
            _rollback(current=current, backup=backup)
            raise
        if config.restart:
            process_starter(
                [str(current)],
                cwd=str(current.parent),
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    finally:
        staged.unlink(missing_ok=True)


def _wait_for_process_exit(pid: int, *, timeout_seconds: float = 60.0) -> None:
    if pid <= 0:
        return
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return
        time.sleep(0.2)
    raise UpdateRunnerError("更新前に旧アプリの終了を確認できませんでした")


def _process_exists(pid: int) -> bool:
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return str(pid) in completed.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _smoke_updated_executable(
    executable: Path,
    *,
    expected_version: str,
    timeout_seconds: float,
    process_runner: ProcessRunner,
) -> None:
    with tempfile.TemporaryDirectory(prefix="mdrl-updater-smoke-") as tmp_dir:
        smoke_root = Path(tmp_dir)
        result_path = smoke_root / "result.json"
        local_app_data = smoke_root / "local-app-data"
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(local_app_data)
        completed = process_runner(
            [
                str(executable),
                "--smoke-test",
                "--smoke-output",
                str(result_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            env=environment,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[-1000:]
            message = f"更新後GUI EXEの起動検証に失敗しました: exit code {completed.returncode}"
            if detail:
                message = f"{message}: {detail}"
            raise UpdateRunnerError(message)
        if not result_path.is_file():
            raise UpdateRunnerError("更新後GUI EXEの起動検証結果が作成されませんでした")
        try:
            document = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UpdateRunnerError("更新後GUI EXEの起動検証結果を解析できません") from exc
        if not isinstance(document, dict) or document.get("version") != expected_version:
            raise UpdateRunnerError(
                f"更新後GUI EXEのバージョンが一致しません: {document}"
            )
        if local_app_data.joinpath("MasterDuelRecorderLite").exists():
            raise UpdateRunnerError("更新後GUI EXEの起動検証が実行時データを作成しました")


def _rollback(*, current: Path, backup: Path) -> None:
    if backup.is_file():
        current.unlink(missing_ok=True)
        os.replace(backup, current)


def _verify_file(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise UpdateRunnerError(f"{label}が見つかりません: {path}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Master Duel Recorder Lite updater")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--current", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--expected-version")
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument("--no-restart", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        print(f"mdrl-updater {__version__}")
        return 0
    required = {
        "current": args.current,
        "candidate": args.candidate,
        "backup": args.backup,
        "expected_sha256": args.expected_sha256,
        "expected_version": args.expected_version,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        print(f"[ERROR] 必須引数が不足しています: {', '.join(missing)}", file=sys.stderr)
        return 2
    try:
        apply_staged_update(
            UpdateRunnerConfig(
                current=args.current,
                candidate=args.candidate,
                backup=args.backup,
                expected_sha256=args.expected_sha256,
                expected_version=args.expected_version,
                parent_pid=args.parent_pid,
                restart=not args.no_restart,
            )
        )
    except (OSError, subprocess.SubprocessError, UpdateRunnerError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0
