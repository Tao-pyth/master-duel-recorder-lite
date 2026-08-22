# PySide6 GUI移行設計

## 目的

V2.0.0では、通常配布GUI入口をTkinterからPySide6へ切り替える。目的は、既存の録画、履歴、戦績、統計、YouTube、設定の責務をサービス層に残したまま、GUIの表示と操作入口をQtへ移すことである。

旧Tkinter GUIは`master_duel_recorder_lite.gui`として残す。これは互換確認と緊急退避のためであり、`master-duel-recorder-lite-gui.exe`の通常入口は`master_duel_recorder_lite.pyside_gui`とする。

## 対象

- PySide6 `QMainWindow`、左ナビ、共通ヘッダー、状態表示
- 録画、戦績管理、統計、デッキ名、タグ、シーズン、YouTube、信頼性、設定の主要ナビ
- YouTubeページ内のMP4準備表示
- 録画開始、録画停止、自動監視切替、履歴更新のサービス接続
- PySide6 GUIスモークJSONとスクリーンショット出力
- Windows GUI EXE入口のPySide6切替

## 対象外

- DB schema変更
- 設定形式変更
- 録画ファイル、queue、manifest、OAuth資格情報の削除、初期化、形式変更
- OBS Plugin / OBS WebSocket 依存の追加
- Tkinter互換モジュールの即時削除

## ナビゲーション

主要ナビは次の9項目とする。

| キー | 表示 | 主な責務 |
| --- | --- | --- |
| record | 録画 | 手動録画、自動監視、録画対象、視覚診断 |
| history | 戦績管理 | 録画後ハブ、履歴更新、再生、編集、削除、重複比較 |
| statistics | 統計 | 日付、条件、デッキ別、先後別集計 |
| decks | デッキ名 | 使用回数、色、分類、候補 |
| tags | タグ | タグ候補、デッキ専用タグ |
| seasons | シーズン | ランク、イベント、期間 |
| youtube | YouTube | 投稿テンプレート、MP4準備、投稿前確認 |
| reliability | 信頼性 | 事前チェック、導入、後解析、ホットキー |
| settings | 設定 | 通常設定、外部連携、データ保護、危険操作、診断 |

`prepare`と`improve`は独立ナビとして復活させない。V1.6.0の録画後ワークフロー情報設計に従い、`prepare`はYouTubeページ内の投稿前処理、`improve`は戦績管理、統計、設定へ吸収する。

## データ保護

PySide6 GUIは既存の`RecorderApplicationService`を呼び出す。GUI移行のためにDB schemaや設定形式を変えない。クリーンアンインストール、保存先変更、バックアップ、復元、OAuth切断、public投稿などの危険操作は、影響範囲とキャンセル時無変更を維持する。

## 検証

- `python -m master_duel_recorder_lite.pyside_gui --smoke-test`
- `scripts/smoke_windows_gui.ps1`で配布GUI EXEがPySide6スモーク契約を返すこと
- `docs/assets/pyside6-ui-2.0.0/01-shell-smoke.png`で主要ナビと録画ページが非空白、日本語表示正常であること
- 本番`user_data`ではなく、スモーク用runtimeで起動すること
- 既存Tkinter GUIは互換モジュールとしてimport可能であること
