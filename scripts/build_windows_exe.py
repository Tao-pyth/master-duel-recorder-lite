from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_NAME = "master-duel-recorder-lite.exe"
GUI_EXECUTABLE_NAME = "master-duel-recorder-lite-gui.exe"
VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


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
) -> tuple[str, ...]:
    return (
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
        "--distpath",
        str(project_root / "dist"),
        "--workpath",
        str(project_root / "build" / "pyinstaller"),
        "--specpath",
        str(project_root / "build" / "spec"),
        str(project_root / "packaging" / entrypoint),
    )


def build_windows_executable(project_root: Path = PROJECT_ROOT) -> Path:
    if sys.platform != "win32":
        raise RuntimeError("Windows EXEはWindows上でビルドする必要があります")
    version = read_project_version(project_root)
    build_root = project_root / "build"
    build_root.mkdir(parents=True, exist_ok=True)
    version_file = build_root / "windows-version-info.txt"
    version_file.write_text(windows_version_resource(version), encoding="utf-8")
    command = build_command(project_root, version_file)
    completed = subprocess.run(command, cwd=project_root, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"PyInstallerビルドに失敗しました: exit code {completed.returncode}")
    executable = project_root / "dist" / EXECUTABLE_NAME
    if not executable.is_file() or executable.stat().st_size <= 0:
        raise RuntimeError(f"EXEが生成されませんでした: {executable}")
    return executable


def build_windows_executables(project_root: Path = PROJECT_ROOT) -> tuple[Path, Path]:
    cli_executable = build_windows_executable(project_root)
    version = read_project_version(project_root)
    build_root = project_root / "build"
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
    parser.parse_args()
    try:
        executables = build_windows_executables()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    for executable in executables:
        print(executable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
