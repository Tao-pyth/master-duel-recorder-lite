from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

from . import __version__
from .ffmpeg import CommandRunner, FfmpegVersion, discover_ffmpeg, run_command
from .runtime_paths import local_application_data_root


FFMPEG_PROVIDER_PAGE = "https://www.gyan.dev/ffmpeg/builds/"
FFMPEG_DOWNLOAD_URL = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
)
FFMPEG_CHECKSUM_URL = f"{FFMPEG_DOWNLOAD_URL}.sha256"
FFMPEG_LICENSE = "GPLv3"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_CHECKSUM_BYTES = 4096
MAX_TOOL_BYTES = 300 * 1024 * 1024


class FfmpegSetupError(RuntimeError):
    """FFmpegを安全に導入できない場合のエラーです。"""


@dataclass(frozen=True)
class FfmpegInstallProgress:
    stage: str
    downloaded_bytes: int = 0
    total_bytes: int | None = None


@dataclass(frozen=True)
class FfmpegInstallResult:
    destination: Path
    executable: Path
    ffprobe_executable: Path
    version: FfmpegVersion
    archive_sha256: str
    provider_url: str = FFMPEG_PROVIDER_PAGE
    license_name: str = FFMPEG_LICENSE


ProgressCallback = Callable[[FfmpegInstallProgress], None]
DownloadFunction = Callable[[str, Path, int, ProgressCallback | None], None]


def default_ffmpeg_install_directory(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    environment = dict(os.environ if environ is None else environ)
    return local_application_data_root(environ=environment, home=home) / "tools" / "ffmpeg"


class FfmpegInstaller:
    def __init__(
        self,
        *,
        download: DownloadFunction | None = None,
        runner: CommandRunner = run_command,
        platform_name: str | None = None,
    ) -> None:
        self.download = download or download_file
        self.runner = runner
        self.platform_name = platform.system() if platform_name is None else platform_name

    def install(
        self,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
    ) -> FfmpegInstallResult:
        if self.platform_name != "Windows":
            raise FfmpegSetupError("FFmpegの自動導入はWindowsでのみ利用できます")
        target = destination.expanduser().resolve()
        _validate_destination(target)
        _notify(progress, "チェックサムを取得しています")

        with tempfile.TemporaryDirectory(prefix="mdrl-ffmpeg-download-") as temporary:
            temporary_root = Path(temporary)
            checksum_path = temporary_root / "ffmpeg.zip.sha256"
            archive_path = temporary_root / "ffmpeg.zip"
            self.download(
                FFMPEG_CHECKSUM_URL,
                checksum_path,
                MAX_CHECKSUM_BYTES,
                progress,
            )
            expected_hash = _parse_checksum(checksum_path.read_text(encoding="ascii"))
            _notify(progress, "FFmpegをダウンロードしています")
            self.download(
                FFMPEG_DOWNLOAD_URL,
                archive_path,
                MAX_ARCHIVE_BYTES,
                progress,
            )
            actual_hash = _sha256(archive_path)
            if actual_hash != expected_hash:
                raise FfmpegSetupError(
                    "FFmpegアーカイブのSHA-256が配布元の値と一致しません"
                )

            _notify(progress, "アーカイブを検証して展開しています")
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(
                tempfile.mkdtemp(prefix=".mdrl-ffmpeg-install-", dir=target.parent)
            ).resolve()
            try:
                if staging.parent != target.parent or target in staging.parents:
                    raise FfmpegSetupError(
                        "FFmpegの一時展開先とインストール先の関係が不正です"
                    )
                _extract_required_tools(archive_path, staging)
                executable = staging / "bin" / "ffmpeg.exe"
                discovery = discover_ffmpeg(
                    str(executable),
                    runner=self.runner,
                    path_lookup=lambda _command: None,
                    environ={},
                    platform_name="Windows",
                )
                if (
                    not discovery.found
                    or discovery.version is None
                    or not discovery.version.is_supported
                ):
                    raise FfmpegSetupError(
                        "導入したFFmpegが必要なバージョン6.0以上ではありません"
                    )
                ffprobe = staging / "bin" / "ffprobe.exe"
                try:
                    ffprobe_result = self.runner([str(ffprobe), "-version"], 15)
                except Exception as exc:
                    raise FfmpegSetupError(
                        f"導入したffprobeを実行できません: {exc}"
                    ) from exc
                if ffprobe_result.returncode != 0:
                    detail = ffprobe_result.stderr.strip() or "終了コードが0ではありません"
                    raise FfmpegSetupError(f"導入したffprobeを実行できません: {detail}")
                _write_installation_record(staging, actual_hash, discovery.version)
                if target.exists():
                    target.rmdir()
                try:
                    os.replace(staging, target)
                except OSError as exc:
                    raise FfmpegSetupError(
                        "FFmpegをインストール先へ配置できません。"
                        f"書き込み権限と、他のアプリが使用していないことを確認してください: {target}: {exc}"
                    ) from exc
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise

        _notify(progress, "FFmpegの導入が完了しました")
        return FfmpegInstallResult(
            destination=target,
            executable=target / "bin" / "ffmpeg.exe",
            ffprobe_executable=target / "bin" / "ffprobe.exe",
            version=discovery.version,
            archive_sha256=actual_hash,
        )


def download_file(
    url: str,
    destination: Path,
    maximum_bytes: int,
    progress: ProgressCallback | None = None,
) -> None:
    request = Request(
        url,
        headers={"User-Agent": f"master-duel-recorder-lite/{__version__}"},
    )
    downloaded = 0
    try:
        with urlopen(request, timeout=30) as response:
            raw_length = response.headers.get("Content-Length")
            total = int(raw_length) if raw_length and raw_length.isdigit() else None
            if total is not None and total > maximum_bytes:
                raise FfmpegSetupError("ダウンロードサイズが安全上限を超えています")
            with destination.open("xb") as output:
                while chunk := response.read(1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > maximum_bytes:
                        raise FfmpegSetupError("ダウンロードサイズが安全上限を超えています")
                    output.write(chunk)
                    if progress is not None:
                        progress(
                            FfmpegInstallProgress(
                                "ダウンロード中",
                                downloaded,
                                total,
                            )
                        )
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        destination.unlink(missing_ok=True)
        raise FfmpegSetupError(f"FFmpegをダウンロードできません: {exc}") from exc
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _validate_destination(destination: Path) -> None:
    if destination == Path(destination.anchor):
        raise FfmpegSetupError("ドライブ直下はインストール先に指定できません")
    if destination.exists():
        if not destination.is_dir():
            raise FfmpegSetupError("インストール先はフォルダである必要があります")
        if any(destination.iterdir()):
            raise FfmpegSetupError(
                "インストール先が空ではありません。空の専用フォルダを指定してください"
            )


def _parse_checksum(value: str) -> str:
    match = re.search(r"(?i)(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", value)
    if match is None:
        raise FfmpegSetupError("配布元のSHA-256ファイルを解析できません")
    return match.group(0).lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_required_tools(archive_path: Path, staging: Path) -> None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            ffmpeg_info = _find_tool(archive, "ffmpeg.exe")
            ffprobe_info = _find_tool(archive, "ffprobe.exe")
            ffmpeg_root = PurePosixPath(ffmpeg_info.filename).parent.parent
            ffprobe_root = PurePosixPath(ffprobe_info.filename).parent.parent
            if ffmpeg_root != ffprobe_root:
                raise FfmpegSetupError("FFmpegとffprobeのアーカイブ構成が一致しません")
            for info, name in (
                (ffmpeg_info, "ffmpeg.exe"),
                (ffprobe_info, "ffprobe.exe"),
            ):
                if info.file_size <= 0 or info.file_size > MAX_TOOL_BYTES:
                    raise FfmpegSetupError(f"{name}のサイズが不正です")
                target = staging / "bin" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                target.chmod(0o755)
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise FfmpegSetupError(f"FFmpegアーカイブを展開できません: {exc}") from exc


def _find_tool(archive: zipfile.ZipFile, name: str) -> zipfile.ZipInfo:
    matches = []
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts:
            raise FfmpegSetupError("アーカイブに不正なパスが含まれています")
        if not info.is_dir() and path.name.casefold() == name.casefold() and path.parent.name == "bin":
            matches.append(info)
    if len(matches) != 1:
        raise FfmpegSetupError(f"アーカイブ内の{name}を一意に特定できません")
    return matches[0]


def _write_installation_record(
    staging: Path,
    archive_sha256: str,
    version: FfmpegVersion,
) -> None:
    installed_at = datetime.now(timezone.utc).isoformat()
    (staging / "INSTALLATION.txt").write_text(
        "\n".join(
            (
                "Master Duel Recorder Lite managed FFmpeg installation",
                f"Installed at: {installed_at}",
                f"Version: {version.display}",
                f"Source: {FFMPEG_DOWNLOAD_URL}",
                f"Provider: {FFMPEG_PROVIDER_PAGE}",
                f"Archive SHA-256: {archive_sha256}",
                f"License: {FFMPEG_LICENSE}",
                "FFmpeg source and license information: https://ffmpeg.org/",
                "",
            )
        ),
        encoding="utf-8",
    )


def _notify(progress: ProgressCallback | None, stage: str) -> None:
    if progress is not None:
        progress(FfmpegInstallProgress(stage))
