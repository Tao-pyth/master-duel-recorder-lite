"""ゲーム素材を使わず、配布CLI/GUIの汎用OCRを実推論で確認する。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def generated_video(path: Path) -> None:
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    font_path = next((fonts / name for name in ("meiryo.ttc", "YuGothM.ttc", "msgothic.ttc")
                      if (fonts / name).is_file()), None)
    if font_path is None:
        raise RuntimeError("日本語推論試験に必要なWindowsフォントがありません")
    image = Image.new("RGB", (1920, 1080), "black")
    draw = ImageDraw.Draw(image)
    draw.rectangle((15, 137, 303, 202), fill="white")
    draw.text((25, 141), "増殖するG", fill="black", font=ImageFont.truetype(str(font_path), 38))
    frame = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 2.0, (1920, 1080))
    if not writer.isOpened():
        raise RuntimeError("隔離した推論試験動画を生成できません")
    try:
        for _ in range(4):
            writer.write(frame)
    finally:
        writer.release()


def run(command: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                            env=environment, timeout=180,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(f"OCR配布試験が失敗しました（exit={result.returncode}）: {result.stderr[-1500:]}")
    return result


def verify(executable: Path, *, gui: bool, root: Path, source: Path) -> dict[str, object]:
    # Windows TEMPの8.3短名もworkerと同じ実パスへ正規化する。
    source = source.resolve()
    environment = os.environ.copy()
    environment.update({"MDRL_USER_DATA_DIR": str(root / "unused-runtime"),
                        "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
                        "ALL_PROXY": "http://127.0.0.1:9", "NO_PROXY": "",
                        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    diagnose = ([str(executable), "--card-analysis-diagnose"] if gui else
                [str(executable), "card-analysis", "diagnose", "--json"])
    diagnostics = json.loads(run(diagnose, environment).stdout)
    if diagnostics.get("settings", {}).get("network_required") is not False:
        raise RuntimeError("同梱モデルのオフライン診断が不正です")
    stat = source.stat()
    fingerprint = {"path": str(source), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                   "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    request = {"schema": "card-analysis-request-v1", "source_path": str(source),
               "expected_fingerprint": fingerprint, "owner_pid": os.getpid(), "sample_fps": 2.0,
               "targets": [{"target_id": "smoke-target", "id": "smoke-target", "name": "増殖するG",
                            "aliases": [], "roles": []}]}
    request_path = root / ("gui-request.json" if gui else "cli-request.json")
    request_path.write_text(json.dumps(request, ensure_ascii=True), encoding="utf-8")
    process = run([str(executable), "--card-analysis-worker", "--request", str(request_path)], environment)
    messages = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    results = [message for message in messages if message.get("type") == "result"]
    if len(results) != 1 or any(message.get("type") == "error" for message in messages):
        raise RuntimeError("実推論workerが正常完了していません")
    result = results[0]
    if result.get("source_fingerprint") != fingerprint or result.get("analyzer_version") != "rapidocr-ppocrv6-v1":
        raise RuntimeError("実推論の入力または方式が一致しません: " + json.dumps({
            "expected_fingerprint": fingerprint, "actual_fingerprint": result.get("source_fingerprint"),
            "actual_analyzer_version": result.get("analyzer_version")}, ensure_ascii=True))
    observations = result.get("observations", [])
    if not any(row.get("target_id") == "smoke-target" and row.get("card_name") == "増殖するG"
               and row.get("time_kind") == "detail_observed" and row.get("evidence", {}).get("supports_use") is False
               for row in observations):
        raise RuntimeError("生成した日本語の認識結果がありません")
    if (root / "unused-runtime").exists():
        raise RuntimeError("DB不要のOCR試験がruntimeへ書き込みました")
    return {"executable": str(executable), "gui": gui, "diagnostics": diagnostics,
            "generated_text_recognized": True, "observations": observations,
            "metrics": result.get("metrics"), "coverage": result.get("coverage"),
            "network_proxy_disabled": True, "runtime_unchanged": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--gui", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="mdrl-bundled-ocr-smoke-") as directory:
        root = Path(directory)
        source = root / "generic-japanese.avi"
        generated_video(source)
        report = {"schema": "card-ocr-packaged-smoke-v1", "game_assets_used": False,
                  "results": [verify(arguments.cli.resolve(), gui=False, root=root, source=source),
                              verify(arguments.gui.resolve(), gui=True, root=root, source=source)]}
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("CLI/GUI bundled Japanese OCR: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
