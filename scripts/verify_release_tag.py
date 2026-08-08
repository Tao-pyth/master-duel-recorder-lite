from __future__ import annotations

import ast
from pathlib import Path
import sys

if __package__:
    from scripts.build_windows_exe import PROJECT_ROOT, read_project_version
else:
    from build_windows_exe import PROJECT_ROOT, read_project_version


def read_package_version(project_root: Path = PROJECT_ROOT) -> str:
    source = (project_root / "src" / "master_duel_recorder_lite" / "__init__.py").read_text(
        encoding="utf-8-sig"
    )
    module = ast.parse(source)
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    raise ValueError("__version__を読み取れません")


def verify_release_tag(tag: str, project_root: Path = PROJECT_ROOT) -> str:
    if not tag.startswith("v"):
        raise ValueError(f"リリースタグはvX.Y.Z形式である必要があります: {tag}")
    tag_version = tag[1:]
    project_version = read_project_version(project_root)
    package_version = read_package_version(project_root)
    if tag_version != project_version or project_version != package_version:
        raise ValueError(
            "タグ、pyproject.toml、__version__が一致しません: "
            f"tag={tag_version}, project={project_version}, package={package_version}"
        )
    return tag_version


def verify_project_version(project_root: Path = PROJECT_ROOT) -> str:
    project_version = read_project_version(project_root)
    package_version = read_package_version(project_root)
    if project_version != package_version:
        raise ValueError(
            "pyproject.tomlと__version__が一致しません: "
            f"project={project_version}, package={package_version}"
        )
    return project_version


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) > 1:
        print("usage: python scripts/verify_release_tag.py [vX.Y.Z]", file=sys.stderr)
        return 2
    try:
        version = verify_release_tag(arguments[0]) if arguments else verify_project_version()
    except (OSError, TypeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
