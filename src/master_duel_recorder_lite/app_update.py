from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


RELEASE_API = "https://api.github.com/repos/Tao-pyth/master-duel-recorder-lite/releases/latest"
GUI_ASSET_NAME = "master-duel-recorder-lite-gui.exe"
UPDATER_ASSET_NAME = "master-duel-recorder-lite-updater.exe"
MAX_UPDATE_BYTES = 256 * 1024 * 1024
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class AppUpdateError(RuntimeError):
    """アプリ更新を安全に確認・取得できない場合のエラーです。"""


@dataclass(frozen=True)
class UpdateRelease:
    version: str
    name: str
    page_url: str
    executable_url: str
    checksum_url: str
    size_bytes: int


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    release: UpdateRelease | None

    @property
    def available(self) -> bool:
        return self.release is not None


OpenUrl = Callable[..., object]
ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


class AppUpdateService:
    def __init__(
        self,
        *,
        opener: OpenUrl = urlopen,
        process_runner: ProcessRunner = subprocess.run,
    ) -> None:
        self.opener = opener
        self.process_runner = process_runner

    def check(self, current_version: str) -> UpdateCheckResult:
        current = _version(current_version)
        document = self._read_json(RELEASE_API, 1024 * 1024)
        if bool(document.get("draft")) or bool(document.get("prerelease")):
            return UpdateCheckResult(current_version, None)
        tag = str(document.get("tag_name", ""))
        latest = _version(tag)
        if latest <= current:
            return UpdateCheckResult(current_version, None)
        assets = document.get("assets")
        if not isinstance(assets, list):
            raise AppUpdateError("Releaseの成果物一覧を確認できません")
        by_name = {
            str(asset.get("name")): asset
            for asset in assets
            if isinstance(asset, dict)
        }
        executable = by_name.get(GUI_ASSET_NAME)
        checksum = by_name.get(f"{GUI_ASSET_NAME}.sha256")
        updater = by_name.get(UPDATER_ASSET_NAME)
        updater_checksum = by_name.get(f"{UPDATER_ASSET_NAME}.sha256")
        if (
            not isinstance(executable, dict)
            or not isinstance(checksum, dict)
            or not isinstance(updater, dict)
            or not isinstance(updater_checksum, dict)
        ):
            raise AppUpdateError("GUI EXE、updater EXE、またはSHA-256成果物がReleaseにありません")
        size = executable.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_UPDATE_BYTES:
            raise AppUpdateError("更新EXEのサイズが安全範囲外です")
        updater_size = updater.get("size")
        if (
            isinstance(updater_size, bool)
            or not isinstance(updater_size, int)
            or not 0 < updater_size <= MAX_UPDATE_BYTES
        ):
            raise AppUpdateError("更新updater EXEのサイズが安全範囲外です")
        release = UpdateRelease(
            ".".join(str(value) for value in latest),
            str(document.get("name") or tag),
            str(document.get("html_url") or ""),
            _https_url(executable.get("browser_download_url")),
            _https_url(checksum.get("browser_download_url")),
            size,
        )
        return UpdateCheckResult(current_version, release)

    def download(self, release: UpdateRelease, destination: Path) -> Path:
        target = destination.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        checksum_text = self._read_bytes(release.checksum_url, 4096).decode(
            "ascii", errors="strict"
        )
        match = re.search(r"(?i)(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", checksum_text)
        if match is None:
            raise AppUpdateError("更新SHA-256を解析できません")
        payload = self._read_bytes(release.executable_url, MAX_UPDATE_BYTES)
        if len(payload) != release.size_bytes:
            raise AppUpdateError("更新EXEのサイズがRelease情報と一致しません")
        actual = hashlib.sha256(payload).hexdigest()
        if actual != match.group(0).lower():
            raise AppUpdateError("更新EXEのSHA-256が公開値と一致しません")
        temporary = target.with_suffix(f"{target.suffix}.part")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def download_and_verify(self, release: UpdateRelease, destination: Path) -> Path:
        target = self.download(release, destination)
        self.verify_gui_executable(target, expected_version=release.version)
        return target

    def verify_gui_executable(
        self,
        executable: Path,
        *,
        expected_version: str,
        timeout_seconds: float = 20.0,
    ) -> None:
        target = executable.expanduser().resolve()
        if not target.is_file() or target.stat().st_size <= 0:
            raise AppUpdateError("更新EXEの起動検証対象が見つかりません")
        with tempfile.TemporaryDirectory(prefix="mdrl-update-smoke-") as tmp_dir:
            smoke_root = Path(tmp_dir)
            result_path = smoke_root / "result.json"
            local_app_data = smoke_root / "local-app-data"
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = str(local_app_data)
            try:
                completed = self.process_runner(
                    [
                        str(target),
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
            except subprocess.TimeoutExpired as exc:
                raise AppUpdateError("更新EXEの起動検証がタイムアウトしました") from exc
            except OSError as exc:
                raise AppUpdateError(f"更新EXEを起動できません: {exc}") from exc
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip()[-1000:]
                message = f"更新EXEの起動検証に失敗しました: exit code {completed.returncode}"
                if detail:
                    message = f"{message}: {detail}"
                raise AppUpdateError(message)
            if not result_path.is_file():
                raise AppUpdateError("更新EXEの起動検証結果が作成されませんでした")
            try:
                document = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AppUpdateError("更新EXEの起動検証結果を解析できません") from exc
            if not isinstance(document, dict):
                raise AppUpdateError("更新EXEの起動検証結果の形式が不正です")
            if document.get("version") != expected_version:
                raise AppUpdateError(
                    f"更新EXEのバージョンが一致しません: {document.get('version')}"
                )
            runtime_data = document.get("runtime_data")
            if isinstance(runtime_data, str):
                expected_runtime = local_app_data / "MasterDuelRecorderLite"
                if Path(runtime_data).resolve() != expected_runtime.resolve():
                    raise AppUpdateError("更新EXEの既定保存先が検証環境から外れています")
            if local_app_data.joinpath("MasterDuelRecorderLite").exists():
                raise AppUpdateError("更新EXEの起動検証が実行時データを作成しました")

    def _read_json(self, url: str, maximum: int) -> dict[str, object]:
        try:
            document = json.loads(self._read_bytes(url, maximum).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppUpdateError("更新情報を解析できません") from exc
        if not isinstance(document, dict):
            raise AppUpdateError("更新情報の形式が不正です")
        return document

    def _read_bytes(self, url: str, maximum: int) -> bytes:
        request = Request(
            url,
            headers={
                "User-Agent": "master-duel-recorder-lite-update",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with self.opener(request, timeout=20) as response:  # type: ignore[attr-defined]
                data = response.read(maximum + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise AppUpdateError(f"更新情報を取得できません: {exc}") from exc
        if len(data) > maximum:
            raise AppUpdateError("更新データが安全上限を超えています")
        return data


def launch_update_after_exit(downloaded_executable: Path, *, expected_version: str) -> Path:
    if not bool(getattr(sys, "frozen", False)):
        raise AppUpdateError("アプリ更新の適用は配布EXEでのみ利用できます")
    current = Path(sys.executable).resolve()
    downloaded = downloaded_executable.expanduser().resolve()
    if not downloaded.is_file() or downloaded.stat().st_size <= 0:
        raise AppUpdateError("取得済み更新EXEが見つかりません")
    updater_source = _bundled_updater_executable()
    updater_target = downloaded.parent / UPDATER_ASSET_NAME
    if updater_source.resolve() != updater_target.resolve():
        shutil.copy2(updater_source, updater_target)
    digest = _file_sha256(downloaded)
    backup = current.with_suffix(f"{current.suffix}.previous")
    subprocess.Popen(
        (
            str(updater_target),
            "--parent-pid",
            str(os.getpid()),
            "--current",
            str(current),
            "--candidate",
            str(downloaded),
            "--backup",
            str(backup),
            "--expected-sha256",
            digest,
            "--expected-version",
            expected_version,
        ),
        cwd=str(current.parent),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )
    return updater_target


def _bundled_updater_executable() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    candidates = (
        base / UPDATER_ASSET_NAME,
        Path(sys.executable).resolve().with_name(UPDATER_ASSET_NAME),
    )
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    raise AppUpdateError("更新用updater EXEが配布物に含まれていません")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise AppUpdateError(f"更新バージョンの形式が不正です: {value}")
    return tuple(int(item) for item in match.groups())  # type: ignore[return-value]


def _https_url(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("https://"):
        raise AppUpdateError("更新成果物のURLがHTTPSではありません")
    return value
