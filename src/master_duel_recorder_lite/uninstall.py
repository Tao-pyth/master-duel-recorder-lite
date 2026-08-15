from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from .runtime_paths import APPLICATION_DIRECTORY_NAME, RuntimePaths


CONFIRMATION_TEXT = "アンインストール"
KNOWN_RUNTIME_CHILDREN = frozenset({"config", "data", "logs", "tools", "backups"})


class UninstallError(RuntimeError):
    """安全にアンインストールできない場合のエラーです。"""


@dataclass(frozen=True)
class UninstallPlan:
    runtime_root: Path
    executable: Path | None
    remove_executable: bool
    file_count: int
    directory_count: int
    total_bytes: int


@dataclass(frozen=True)
class CleanupManifest:
    parent_pid: int
    runtime_root: str
    executable: str | None
    remove_executable: bool
    result_path: str


def _forbidden_roots() -> set[Path]:
    candidates = {Path.home().resolve()}
    for name in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        value = os.environ.get(name)
        if value:
            candidates.add(Path(value).expanduser().resolve())
    for anchor in {Path.cwd().anchor, Path.home().anchor}:
        if anchor:
            candidates.add(Path(anchor).resolve())
    return candidates


def validate_runtime_root(root: Path) -> Path:
    resolved = root.expanduser().resolve()
    if resolved in _forbidden_roots() or len(resolved.parts) < 3:
        raise UninstallError(f"危険な保存領域は削除できません: {resolved}")
    if not resolved.exists():
        return resolved
    if not resolved.is_dir():
        raise UninstallError(f"保存領域がフォルダではありません: {resolved}")
    known = {child.name.casefold() for child in resolved.iterdir()}
    named_default = resolved.name.casefold() in {
        APPLICATION_DIRECTORY_NAME.casefold(),
        "user_data",
    }
    if not named_default and not (known & KNOWN_RUNTIME_CHILDREN):
        raise UninstallError(
            f"アプリの保存領域と確認できないため削除しません: {resolved}"
        )
    return resolved


def _inventory(root: Path) -> tuple[int, int, int]:
    if not root.exists():
        return 0, 0, 0
    files = 0
    directories = 1
    total_bytes = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = tuple(os.scandir(directory))
        except OSError as exc:
            raise UninstallError(
                f"削除対象を確認できません: {directory}: {exc}"
            ) from exc
        for entry in entries:
            try:
                if entry.is_symlink():
                    files += 1
                    continue
                if entry.is_dir(follow_symlinks=False):
                    directories += 1
                    pending.append(Path(entry.path))
                    continue
                files += 1
                total_bytes += entry.stat(follow_symlinks=False).st_size
            except OSError as exc:
                raise UninstallError(
                    f"削除対象を確認できません: {entry.path}: {exc}"
                ) from exc
    return files, directories, total_bytes


def create_uninstall_plan(
    paths: RuntimePaths,
    *,
    remove_executable: bool = False,
    executable: Path | None = None,
    frozen: bool | None = None,
) -> UninstallPlan:
    root = validate_runtime_root(paths.root)
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    selected_executable: Path | None = None
    if remove_executable:
        if not is_frozen:
            raise UninstallError("Python実行では共有Python本体を削除できません")
        selected_executable = (
            (executable or Path(sys.executable)).expanduser().resolve()
        )
        if not selected_executable.is_file():
            raise UninstallError(f"起動EXEが見つかりません: {selected_executable}")
    files, directories, total_bytes = _inventory(root)
    if selected_executable is not None and not selected_executable.is_relative_to(root):
        files += 1
        total_bytes += selected_executable.stat().st_size
    return UninstallPlan(
        runtime_root=root,
        executable=selected_executable,
        remove_executable=remove_executable,
        file_count=files,
        directory_count=directories,
        total_bytes=total_bytes,
    )


def _unlink_tree(root: Path) -> None:
    if not root.exists() and not root.is_symlink():
        return
    if root.is_symlink():
        root.unlink()
        return

    def remove_readonly(function: object, path: str, _error: object) -> None:
        os.chmod(path, stat.S_IWRITE)
        function(path)

    last_error: OSError | None = None
    for attempt in range(3):
        try:
            shutil.rmtree(root, onerror=remove_readonly)
            return
        except OSError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
    assert last_error is not None
    raise last_error


def execute_cleanup(plan: UninstallPlan) -> None:
    root = validate_runtime_root(plan.runtime_root)
    _unlink_tree(root)
    if plan.remove_executable and plan.executable is not None:
        executable = plan.executable.expanduser().resolve()
        if executable.exists() and not executable.is_relative_to(root):
            executable.unlink()


def wait_for_process_exit(pid: int, *, timeout_seconds: float = 120.0) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        wait_object_0 = 0x00000000
        wait_timeout = 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return
        try:
            wait_result = ctypes.windll.kernel32.WaitForSingleObject(
                handle, max(1, int(timeout_seconds * 1000))
            )
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        if wait_result == wait_object_0:
            return
        if wait_result == wait_timeout:
            raise UninstallError("アプリの終了を確認できなかったため削除を中止しました")
        raise UninstallError("アプリの終了待機に失敗したため削除を中止しました")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)
    raise UninstallError("アプリの終了を確認できなかったため削除を中止しました")


def _schedule_self_delete(path: Path) -> None:
    if os.name != "nt":
        return
    import ctypes

    move_file_delay_until_reboot = 0x4
    ctypes.windll.kernel32.MoveFileExW(str(path), None, move_file_delay_until_reboot)


def run_cleanup_manifest(path: Path) -> int:
    manifest_path = path.expanduser().resolve()
    document: dict[str, object] = {}
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = CleanupManifest(**document)
        manifest_path.unlink(missing_ok=True)
        wait_for_process_exit(manifest.parent_pid)
        plan = UninstallPlan(
            runtime_root=Path(manifest.runtime_root),
            executable=Path(manifest.executable) if manifest.executable else None,
            remove_executable=manifest.remove_executable,
            file_count=0,
            directory_count=0,
            total_bytes=0,
        )
        execute_cleanup(plan)
        Path(manifest.result_path).write_text(
            json.dumps({"succeeded": True}, ensure_ascii=False), encoding="utf-8"
        )
        if bool(getattr(sys, "frozen", False)):
            _schedule_self_delete(Path(sys.executable).resolve())
        return 0
    except Exception as exc:
        try:
            result_path = Path(str(document["result_path"]))
            result_path.write_text(
                json.dumps({"succeeded": False, "error": str(exc)}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass
        return 3


def launch_cleanup_worker(
    plan: UninstallPlan,
    *,
    module: str,
    parent_pid: int | None = None,
) -> Path:
    temp_root = Path(tempfile.gettempdir())
    token = uuid4().hex
    manifest_path = temp_root / f"mdrl-uninstall-{token}.json"
    result_path = temp_root / f"mdrl-uninstall-{token}-result.json"
    manifest = CleanupManifest(
        parent_pid=parent_pid or os.getpid(),
        runtime_root=str(plan.runtime_root),
        executable=str(plan.executable) if plan.executable else None,
        remove_executable=plan.remove_executable,
        result_path=str(result_path),
    )
    manifest_path.write_text(
        json.dumps(asdict(manifest), ensure_ascii=False), encoding="utf-8"
    )
    if bool(getattr(sys, "frozen", False)):
        helper = temp_root / f"mdrl-uninstall-{token}.exe"
        shutil.copy2(Path(sys.executable), helper)
        command = (str(helper), "--cleanup-manifest", str(manifest_path))
    else:
        command = (
            sys.executable,
            "-m",
            module,
            "--cleanup-manifest",
            str(manifest_path),
        )
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        command,
        close_fds=True,
        creationflags=creationflags,
    )
    return result_path
