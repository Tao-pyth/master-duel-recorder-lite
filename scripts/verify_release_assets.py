from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


REPOSITORY = "Tao-pyth/master-duel-recorder-lite"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/tags"
EXECUTABLES = (
    "master-duel-recorder-lite.exe",
    "master-duel-recorder-lite-gui.exe",
    "master-duel-recorder-lite-updater.exe",
)
SHA256_PATTERN = re.compile(r"(?i)(?<![0-9a-f])([0-9a-f]{64})(?![0-9a-f])")


class ReleaseAssetVerificationError(RuntimeError):
    """GitHub Releaseの配布資産が公開ハッシュと一致しない場合のエラーです。"""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    digest: str
    download_url: str


def verify_release_assets(tag: str) -> list[str]:
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ReleaseAssetVerificationError(
            f"リリースタグはvX.Y.Z形式である必要があります: {tag}"
        )
    document = _read_json(f"{RELEASE_API}/{tag}")
    assets = _assets_by_name(document)
    verified: list[str] = []
    for executable_name in EXECUTABLES:
        executable = _required_asset(assets, executable_name)
        checksum = _required_asset(assets, f"{executable_name}.sha256")
        expected = _asset_sha256(executable)
        published = _published_sha256(checksum)
        if published != expected:
            raise ReleaseAssetVerificationError(
                f"{checksum.name}が{executable.name}のGitHub digestと一致しません: "
                f"published={published}, digest={expected}"
            )
        verified.append(f"{executable.name}: {expected}")
    return verified


def _assets_by_name(document: object) -> dict[str, ReleaseAsset]:
    if not isinstance(document, dict):
        raise ReleaseAssetVerificationError("Release情報の形式が不正です")
    raw_assets = document.get("assets")
    if not isinstance(raw_assets, list):
        raise ReleaseAssetVerificationError("Releaseの成果物一覧を確認できません")
    assets: dict[str, ReleaseAsset] = {}
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        digest = item.get("digest")
        download_url = item.get("browser_download_url")
        if isinstance(name, str) and isinstance(digest, str) and isinstance(download_url, str):
            assets[name] = ReleaseAsset(name, digest, download_url)
    return assets


def _required_asset(assets: dict[str, ReleaseAsset], name: str) -> ReleaseAsset:
    try:
        return assets[name]
    except KeyError as exc:
        raise ReleaseAssetVerificationError(f"Release資産が見つかりません: {name}") from exc


def _asset_sha256(asset: ReleaseAsset) -> str:
    prefix = "sha256:"
    if not asset.digest.startswith(prefix):
        raise ReleaseAssetVerificationError(f"{asset.name}のdigest形式が不正です")
    value = asset.digest.removeprefix(prefix).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ReleaseAssetVerificationError(f"{asset.name}のdigest形式が不正です")
    return value


def _published_sha256(asset: ReleaseAsset) -> str:
    text = _read_bytes(_cache_busted_url(asset.download_url, asset.name), 4096).decode(
        "ascii", errors="strict"
    )
    match = SHA256_PATTERN.search(text)
    if match is None:
        raise ReleaseAssetVerificationError(f"{asset.name}からSHA-256を読み取れません")
    return match.group(1).lower()


def _cache_busted_url(url: str, asset_name: str) -> str:
    parts = urlsplit(url)
    query = urlencode({"mdrl_release_check": asset_name})
    if parts.query:
        query = f"{parts.query}&{query}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _read_json(url: str) -> object:
    try:
        return json.loads(_read_bytes(url, 1024 * 1024).decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ReleaseAssetVerificationError("Release情報を解析できません") from exc


def _read_bytes(url: str, maximum: int) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "master-duel-recorder-lite-release-check",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - 固定GitHub URLのみを検証対象にする
            data = response.read(maximum + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ReleaseAssetVerificationError(f"Release情報を取得できません: {exc}") from exc
    if len(data) > maximum:
        raise ReleaseAssetVerificationError("Release情報が安全上限を超えています")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="GitHub ReleaseのEXE digestと公開SHA-256を照合します。"
    )
    parser.add_argument("tag", help="検証するリリースタグ。例: v1.4.0")
    args = parser.parse_args(argv)
    try:
        for line in verify_release_assets(args.tag):
            print(line)
    except (OSError, TypeError, UnicodeError, ReleaseAssetVerificationError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
