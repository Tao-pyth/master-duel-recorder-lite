# V1候補E2Eチェックリスト

## 前提

- Windows 10または11
- Python開発検証ではPython 3.11以上。利用者のEXE実行にはPython不要
- FFmpeg 6.0以上、またはlibavutil 58以上を含むbuild
- 実ユーザーデータと分離した空の`MDRL_USER_DATA_DIR`

## 手順

1. `python -m pip install -e ".[build,dev]"`が成功し、`python -m master_duel_recorder_lite --version`が`0.16.7`を表示する。
2. `config init`が`app.toml`を作成し、2回目は終了コード4で既存設定を保持する。
3. `config set`、`config get`、`config show --json`で値とJSONを確認し、不正値で元設定が変わらないことを確認する。
4. `doctor`と`list-inputs`で画面入力、任意の音声入力、エンコーダー、保存先を確認する。
5. `status`と`status --json`で同じサブシステム状態を確認し、JSONに実行時データの絶対パスや秘密情報がないことを確認する。
6. `record --duration 3`で手動録画し、判定が`disabled`、処理・破棄が0、出力が非空であることを確認して、表示された録画IDを`history show`へ渡す。
7. `history check`で録画ファイルとの整合性を確認する。
8. 中断状態がある場合は`recovery list`、`inspect`、`repair --dry-run`の順で確認し、元録画が不変であることを確認する。
9. 完了済み録画IDを`prepare enqueue`へ渡し、`prepare run`でMP4とマニフェストを生成する。
10. `prepare show`でcompleted、privateまたは明示したunlisted、相対出力パスを確認する。
11. 録画IDへ対戦記録と手動タイムラインイベントを追加し、GUIとCLIから再取得・候補遷移できることを確認する。
12. [基本イベント自動判定 実画面チェックリスト](visual-detection-checklist.md)で候補、重複、誤判定、録画非干渉を確認する。
13. 自動監視中にメニュー表示だけでは録画せず、開始演出を見逃した場合と対戦途中から開始した場合も安定盤面3フレームの合意後に録画し、開始候補が0ミリ秒へ保存されることを確認する。
14. 録画、復旧、準備の実FFmpegスモークと全単体テストを実行する。
15. FFmpegを利用できない一時環境でGUIセットアップが表示され、キャンセル時は通信・ファイル作成なし、許可時はSHA-256照合後にLocalAppData配下へ導入されることを確認する。
16. `python scripts/build_windows_exe.py`、CLI・GUIのスモークが成功し、両方のPEバージョンが`0.16.7`、EXE隣接`user_data/`なし、既定先がLocalAppData配下であることを確認する。
17. `v0.16.7`のGitHub ReleaseからCLI・GUI EXEとSHA-256を再取得し、ハッシュ一致、CLIの`--version`、GUI起動を確認する。

## 自動検証との対応

- `tests/test_workflow_e2e.py`: 初期化、設定変更、共通録画ID、履歴、準備キュー、キャンセル、元録画保持
- `tests/test_recording_smoke.py`: 合成映像・音声の録画、正常停止、再デコード
- `tests/test_recovery_smoke.py`: 元録画を保持した別ファイル修復、再デコード
- `tests/test_upload_smoke.py`: 元録画を保持したMP4準備、マニフェスト、再デコード
- `tests/test_prepare_cli.py`: Ctrl+C後のprocessing保持と次回waiting復帰
- `tests/test_visual_detection.py`: 16:9正規化、合成BMP特徴、3イベント、時間合意、状態遷移
- `tests/test_visual_worker.py`: 最大2fps、単一処理、遅延破棄、例外分離、正常終了
- `tests/test_recorder.py`: Master Duel録画ライフサイクルとcandidate保存、判定失敗時の録画継続

## V1.0.0判断

V0.16.7までの中核機能と検証証拠が揃っても、自動でV1.0.0へ更新しません。既知制約を確認し、ユーザーが明示的に「V1.0.0に変更せよ」と依頼するまで`0.x`を維持します。
