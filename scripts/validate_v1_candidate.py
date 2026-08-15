from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import time

from master_duel_recorder_lite.application import RecorderApplicationService


CONFIRMATION = "アンインストール"


def _run(executable: Path, user_data: Path, *arguments: str) -> str:
    completed = subprocess.run(
        [str(executable), "--user-data-dir", str(user_data), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"CLIが失敗しました ({completed.returncode}): {' '.join(arguments)}\n"
            f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
        )
    return completed.stdout.strip()


def validate(cli_executable: Path, expected_version: str) -> dict[str, object]:
    executable = cli_executable.expanduser().resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"CLI EXEが見つかりません: {executable}")

    with tempfile.TemporaryDirectory(prefix="mdrl-v1-candidate-") as temporary:
        root = Path(temporary)
        user_data = root / "runtime" / "MasterDuelRecorderLite"
        outside = root / "outside-sentinel.txt"
        outside.write_text("keep", encoding="utf-8")

        version = subprocess.run(
            [str(executable), "--version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        if version != f"mdrl {expected_version}":
            raise RuntimeError(f"配布CLIのバージョンが一致しません: {version}")

        _run(
            executable,
            user_data,
            "--init-user-data",
            "--write-default-config",
        )
        if not (user_data / "config" / "app.toml").is_file():
            raise RuntimeError("クリーン環境へ既定設定を作成できませんでした")

        created = json.loads(
            _run(
                executable,
                user_data,
                "duel",
                "create",
                "--occurred-at",
                datetime(2026, 8, 16, 12, tzinfo=timezone.utc).isoformat(),
                "--result",
                "win",
                "--play-order",
                "first",
                "--coin-face",
                "heads",
                "--own-deck",
                "V1候補デッキ",
                "--opponent-deck",
                "検証相手",
                "--duel-type",
                "ranked",
                "--tag",
                "V1候補",
                "--notes",
                "クリーン環境E2E",
                "--json",
            )
        )
        duel_id = str(created["duel_id"])
        shown = json.loads(
            _run(executable, user_data, "duel", "show", duel_id, "--json")
        )
        if shown["result"] != "win" or shown["coin_face"] != "heads":
            raise RuntimeError("配布CLIの戦績CRUD結果が一致しません")

        service = RecorderApplicationService(user_data_dir=user_data)
        csv_path = service.export_duel_csv(service.paths.exports / "v1-candidate.csv")
        csv_bytes = csv_path.read_bytes()
        if not csv_bytes.startswith(b"\xef\xbb\xbf") or b"\r\n" not in csv_bytes:
            raise RuntimeError("CSVがUTF-8 BOM・CRLF契約を満たしません")
        preview = service.preview_duel_csv(csv_path)
        if not preview.valid or len(preview.rows) != 1:
            raise RuntimeError("CSV往復プレビューが一致しません")
        imported = service.import_duel_csv(preview)
        if imported.updated_ids != (duel_id,) or not imported.backup_path.is_file():
            raise RuntimeError("CSV取込または取込前バックアップに失敗しました")
        sample = service.export_duel_csv_sample(
            service.paths.exports / "v1-candidate-sample.csv"
        )
        if not service.preview_duel_csv(sample).valid:
            raise RuntimeError("サンプルCSVを再読込できません")

        backup = service.create_data_backup("v1-candidate")
        if not backup.path.is_file() or not service.diagnose_data_integrity().healthy:
            raise RuntimeError("バックアップまたは整合性診断に失敗しました")

        _run(
            executable,
            user_data,
            "uninstall",
            "--yes",
            "--confirm",
            CONFIRMATION,
        )
        deadline = time.monotonic() + 10.0
        while user_data.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        if user_data.exists():
            raise RuntimeError("アンインストール後も実行時ルートが残っています")
        if outside.read_text(encoding="utf-8") != "keep":
            raise RuntimeError("アンインストールが境界外ファイルを変更しました")
        if not executable.is_file():
            raise RuntimeError("指定していない配布CLI EXEが削除されました")

        return {
            "version": expected_version,
            "clean_initialization": True,
            "duel_crud": True,
            "csv_round_trip": True,
            "csv_sample": True,
            "backup": True,
            "integrity": True,
            "clean_uninstall": True,
            "outside_boundary_preserved": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="V1候補を空のWindows実行時ルートで検証します。"
    )
    parser.add_argument("--cli-exe", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    result = validate(args.cli_exe, args.expected_version)
    document = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_out is not None:
        destination = args.json_out.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(document, encoding="utf-8")
        temporary.replace(destination)
    print(document, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
