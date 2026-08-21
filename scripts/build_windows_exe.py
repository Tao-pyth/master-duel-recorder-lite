from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_NAME = "master-duel-recorder-lite.exe"
GUI_EXECUTABLE_NAME = "master-duel-recorder-lite-gui.exe"
VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
NATIVE_HELPER_PROJECT = Path("native/audio_loopback/mdrl_audio_loopback.vcxproj")
NATIVE_HELPER_OUTPUT = Path("native/audio_loopback/bin/mdrl-audio-loopback.exe")
APP_ICON = Path("assets/mdrl.ico")
YOUTUBE_OAUTH_CLIENT_ASSET = Path("assets/youtube-oauth-client.json")
YOUTUBE_OAUTH_CLIENT_ID_ENV = "MDRL_YOUTUBE_OAUTH_CLIENT_ID"


def read_project_version(project_root: Path = PROJECT_ROOT) -> str:
    with (project_root / "pyproject.toml").open("rb") as handle:
        value = tomllib.load(handle)["project"]["version"]
    if not isinstance(value, str) or VERSION_PATTERN.fullmatch(value) is None:
        raise ValueError(f"project.versionはX.Y.Z形式である必要があります: {value}")
    return value


def windows_version_tuple(version: str) -> tuple[int, int, int, int]:
    match = VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError(f"バージョンはX.Y.Z形式である必要があります: {version}")
    values = tuple(int(item) for item in match.groups())
    if any(item > 65_535 for item in values):
        raise ValueError("Windowsバージョンの各要素は65535以下である必要があります")
    return values[0], values[1], values[2], 0


def windows_version_resource(
    version: str,
    *,
    executable_name: str = EXECUTABLE_NAME,
    description: str = "Master Duel Recorder Lite",
) -> str:
    file_version = windows_version_tuple(version)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={file_version!r},
    prodvers={file_version!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'Tao-pyth'),
          StringStruct('FileDescription', '{description}'),
          StringStruct('FileVersion', '{version}.0'),
          StringStruct('InternalName', '{executable_name.removesuffix(".exe")}'),
          StringStruct('LegalCopyright', 'Copyright (c) Tao-pyth'),
          StringStruct('OriginalFilename', '{executable_name}'),
          StringStruct('ProductName', 'master-duel-recorder-lite'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def build_command(
    project_root: Path,
    version_file: Path,
    *,
    executable_name: str = EXECUTABLE_NAME,
    entrypoint: str = "mdrl_entry.py",
    windowed: bool = False,
    youtube_oauth_client_asset: Path | None = None,
) -> tuple[str, ...]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed" if windowed else "--console",
        "--noupx",
        "--name",
        executable_name.removesuffix(".exe"),
        "--paths",
        str(project_root / "src"),
        "--version-file",
        str(version_file),
        "--icon",
        str(project_root / APP_ICON),
        "--distpath",
        str(project_root / "dist"),
        "--workpath",
        str(project_root / "build" / "pyinstaller"),
        "--specpath",
        str(project_root / "build" / "spec"),
    ]
    helper = project_root / NATIVE_HELPER_OUTPUT
    if helper.is_file():
        command.extend(["--add-binary", f"{helper};native"])
    notice = project_root / "THIRD_PARTY_NOTICES.md"
    if notice.is_file():
        command.extend(["--add-data", f"{notice};."])
    icon = project_root / APP_ICON
    if icon.is_file():
        command.extend(["--add-data", f"{icon};assets"])
    if youtube_oauth_client_asset is not None:
        command.extend(["--add-data", f"{youtube_oauth_client_asset};assets"])
    command.append(str(project_root / "packaging" / entrypoint))
    return tuple(command)


def resolve_youtube_oauth_client_asset(
    project_root: Path,
    build_root: Path,
    *,
    require: bool = False,
) -> Path | None:
    configured = project_root / YOUTUBE_OAUTH_CLIENT_ASSET
    if configured.is_file():
        _validate_youtube_oauth_client_asset(configured)
        return configured
    client_id = os.environ.get(YOUTUBE_OAUTH_CLIENT_ID_ENV, "").strip()
    if client_id:
        build_root.mkdir(parents=True, exist_ok=True)
        generated = build_root / YOUTUBE_OAUTH_CLIENT_ASSET.name
        generated.write_text(
            json.dumps(
                {
                    "installed": {
                        "client_id": client_id,
                        "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        _validate_youtube_oauth_client_asset(generated)
        return generated
    if require:
        raise RuntimeError(
            "YouTube OAuth client_idが未設定です。"
            f"{YOUTUBE_OAUTH_CLIENT_ASSET}または{YOUTUBE_OAUTH_CLIENT_ID_ENV}を設定してください。"
        )
    return None


def _validate_youtube_oauth_client_asset(path: Path) -> None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"YouTube OAuth client設定を読めません: {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise RuntimeError("YouTube OAuth client設定のルートはobjectである必要があります")
    client = document.get("installed") or document.get("web")
    if not isinstance(client, dict):
        raise RuntimeError("YouTube OAuth client設定にはinstalledまたはweb objectが必要です")
    client_id = client.get("client_id")
    if not isinstance(client_id, str) or not client_id.strip():
        raise RuntimeError("YouTube OAuth client_idは空でない文字列である必要があります")
    forbidden = {"client_secret", "refresh_token", "access_token"}
    leaked = sorted(key for key in forbidden if str(client.get(key, "")).strip())
    if leaked:
        raise RuntimeError(
            "配布用YouTube OAuth client設定にsecret相当値を含めないでください: "
            + ", ".join(leaked)
        )


def build_native_audio_helper(project_root: Path = PROJECT_ROOT) -> Path:
    if sys.platform != "win32":
        raise RuntimeError("音声ヘルパーはWindows上でビルドする必要があります")
    project = project_root / NATIVE_HELPER_PROJECT
    if not project.is_file():
        raise RuntimeError(f"音声ヘルパーのプロジェクトがありません: {project}")
    msbuild = _find_msbuild()
    completed = subprocess.run(
        [
            str(msbuild),
            str(project),
            "/m",
            "/p:Configuration=Release",
            "/p:Platform=x64",
            "/v:minimal",
        ],
        cwd=project_root,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"音声ヘルパーのビルドに失敗しました: exit code {completed.returncode}"
        )
    output = project_root / NATIVE_HELPER_OUTPUT
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError(f"音声ヘルパーが生成されませんでした: {output}")
    probe = subprocess.run(
        [str(output), "--probe"],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if probe.returncode != 0 or "event=probe_ok" not in probe.stderr:
        raise RuntimeError(f"音声ヘルパーのスモークテストに失敗しました: {probe.stderr}")
    return output


def _find_msbuild() -> Path:
    configured = os.environ.get("MSBUILD_EXE_PATH")
    if configured and Path(configured).is_file():
        return Path(configured)
    candidates = (
        Path(os.environ.get("ProgramFiles(x86)", ""))
        / "Microsoft Visual Studio/Installer/vswhere.exe",
        Path("C:/BuildTools2022/MSBuild/Current/Bin/MSBuild.exe"),
    )
    vswhere = candidates[0]
    if vswhere.is_file():
        found = subprocess.run(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-find",
                "MSBuild\\**\\Bin\\MSBuild.exe",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in found.stdout.splitlines():
            path = Path(line.strip())
            if path.is_file():
                return path
    if candidates[1].is_file():
        return candidates[1]
    raise RuntimeError("MSBuildとC++ Build Toolsが見つかりません")


def build_windows_executable(
    project_root: Path = PROJECT_ROOT,
    *,
    youtube_oauth_client_asset: Path | None = None,
) -> Path:
    if sys.platform != "win32":
        raise RuntimeError("Windows EXEはWindows上でビルドする必要があります")
    version = read_project_version(project_root)
    build_root = project_root / "build"
    build_root.mkdir(parents=True, exist_ok=True)
    version_file = build_root / "windows-version-info.txt"
    version_file.write_text(windows_version_resource(version), encoding="utf-8")
    command = build_command(
        project_root,
        version_file,
        youtube_oauth_client_asset=youtube_oauth_client_asset,
    )
    completed = subprocess.run(command, cwd=project_root, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"PyInstallerビルドに失敗しました: exit code {completed.returncode}")
    executable = project_root / "dist" / EXECUTABLE_NAME
    if not executable.is_file() or executable.stat().st_size <= 0:
        raise RuntimeError(f"EXEが生成されませんでした: {executable}")
    return executable


def build_windows_executables(
    project_root: Path = PROJECT_ROOT,
    *,
    require_youtube_oauth_client: bool = False,
) -> tuple[Path, Path]:
    version = read_project_version(project_root)
    build_root = project_root / "build"
    youtube_oauth_client_asset = resolve_youtube_oauth_client_asset(
        project_root,
        build_root,
        require=require_youtube_oauth_client,
    )
    build_native_audio_helper(project_root)
    cli_executable = build_windows_executable(
        project_root,
        youtube_oauth_client_asset=youtube_oauth_client_asset,
    )
    gui_version_file = build_root / "windows-gui-version-info.txt"
    gui_version_file.write_text(
        windows_version_resource(
            version,
            executable_name=GUI_EXECUTABLE_NAME,
            description="Master Duel Recorder Lite GUI",
        ),
        encoding="utf-8",
    )
    command = build_command(
        project_root,
        gui_version_file,
        executable_name=GUI_EXECUTABLE_NAME,
        entrypoint="mdrl_gui_entry.py",
        windowed=True,
        youtube_oauth_client_asset=youtube_oauth_client_asset,
    )
    completed = subprocess.run(command, cwd=project_root, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"GUI版PyInstallerビルドに失敗しました: exit code {completed.returncode}")
    gui_executable = project_root / "dist" / GUI_EXECUTABLE_NAME
    if not gui_executable.is_file() or gui_executable.stat().st_size <= 0:
        raise RuntimeError(f"GUI EXEが生成されませんでした: {gui_executable}")
    return cli_executable, gui_executable


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows向けone-file EXEを生成します。")
    parser.add_argument(
        "--require-youtube-oauth-client",
        action="store_true",
        help="YouTube OAuth client_id未設定のまま配布EXEを作ることを拒否します。",
    )
    args = parser.parse_args()
    try:
        executables = build_windows_executables(
            require_youtube_oauth_client=args.require_youtube_oauth_client
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    for executable in executables:
        print(executable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
