# V2.1.0 PySide6標準機能移植

## 目的

V2.1.0では、V2.0.1/V2.0.2で定義した15標準機能ゲートをPySide6 GUI側で満たし、通常配布GUI入口をPySide6へ戻す。目的は見た目の置換ではなく、現行機能をPySide6 UIで管理・表示できる状態へ戻すことである。

## 方針

- `RecorderApplicationService` をGUI境界として使い、GUIからDBや設定ファイルを直接書き換えない。
- smoke contractで、要求widget、標準機能、標準操作、録画後ワークフロー、データ保全表示を確認する。
- smokeモードではruntime dataを作らない。実データの読み込みは通常起動時に限定する。
- Tkinter互換モジュールは残すが、通常配布GUI入口は `master_duel_recorder_lite.pyside_gui` とする。

## 対象機能

- 録画対象、録画開始/停止、自動監視、視覚診断
- 戦績管理、手動戦績追加、未完了処理、一括編集、履歴フィルター、YouTube投稿導線
- 統計、デッキ名、タグ、シーズン
- YouTube連携状態、投稿テンプレート、MP4準備
- 信頼性、設定、データ保全、CSV/更新
- 日付、FFmpeg、投稿、重複、未完了、一括編集の主要ダイアログ入口

## 統計推移単位

PySide6統計画面では、推移単位を「勝利数・勝率推移」内の条件として扱う。既定値は日単位にする。これは、推移単位が他の集計表へ影響する条件に見える誤解を避けるためである。

## 通常入口判定

通常入口をPySide6へ戻す条件は次の通り。

- PySide6 smoke contractで要求widget 51個が揃う。
- `standard_feature_contract` が `True`。
- `standard_operation_contract` が `True`。
- `failed_standard_operation_checks` が空。
- 録画後ワークフローとデータ保全表示の契約が通る。

## 非対象

- SQLite schema変更
- 設定形式変更
- 録画ファイル、queue、manifest、OAuth資格情報の形式変更
- OBS Plugin / OBS WebSocket依存追加
- Tkinter互換モジュールの削除
