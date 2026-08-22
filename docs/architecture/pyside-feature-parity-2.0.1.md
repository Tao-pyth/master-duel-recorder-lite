# V2.0.1 PySide6機能同等性ゲート

## 目的

V2.0.1では、V2.0.0で通常配布入口になったPySide6 GUIをいったん通常入口から外し、`master-duel-recorder-lite-gui.exe`を1.x相当のTkinter GUIへ戻す。目的は、既存DB、戦績、録画履歴、設定、バックアップなどを更新後も失わず、ユーザーが実運用できる操作面を復旧することである。

PySide6移行は中止しない。ただし、通常配布入口へ戻す前に、この文書の標準機能を満たすことを必須条件にする。

## 通常入口

- V2.0.1の通常配布GUI入口: `master_duel_recorder_lite.gui`
- PySide6レビュー入口: `master_duel_recorder_lite.pyside_review`
- PySide6シェル検証入口: `master_duel_recorder_lite.pyside_gui`

V2.0.1では、PySide6シェルは通常配布入口ではない。TkinterとQtのevent loopを同一プロセスで混在させない既存契約を維持する。

## 1.x標準機能一覧

次の一覧は、V1.5.2のTkinter UI baselineとV2.0.1の`STANDARD_GUI_FEATURES`を基準にした、PySide6全面移植で最低限担保する機能である。

| Key | 標準機能 | 主な確認widget | baseline |
| --- | --- | --- | --- |
| `recording_control` | 録画対象選択、録画開始・停止、自動監視、環境診断 | `target_selector`, `record_start`, `record_stop`, `watch_toggle`, `record_status`, `visual_status`, `visual_details_toggle`, `visual_diagnostics_folder`, `activity` | `docs/assets/tkinter-ui-baseline-1.5.2/01-record.png` |
| `manual_duel_entry` | 録画なし戦績の手動追加と簡易入力 | `manual_duel_add`, `history_add` | `docs/assets/tkinter-ui-baseline-1.5.2-popups/07-quick-duel-editor.png` |
| `history_management` | 戦績管理一覧、未完了処理、一括編集、再生、編集、削除、重複比較、更新 | `history_table`, `history_incomplete`, `history_bulk`, `history_play`, `history_duel`, `history_delete`, `history_duplicates`, `history_refresh` | `docs/assets/tkinter-ui-baseline-1.5.2/02-history.png` |
| `history_filter_columns` | 戦績管理のフィルター、表示列、YouTube投稿導線 | `history_columns`, `history_youtube` | `docs/assets/tkinter-ui-baseline-1.5.2-popups/04-history-filter.png` |
| `statistics` | 全体・条件付き・先後・デッキ・コイントス・シーズン統計 | `statistics_filters`, `statistics_chart`, `statistics_deck_table`, `statistics_order_table`, `statistics_coin_table`, `statistics_season_table`, `statistics_date_from_picker`, `statistics_date_to_picker` | `docs/assets/tkinter-ui-baseline-1.5.2/03-statistics.png` |
| `deck_catalog` | デッキ名、説明、カラー、用途、使用回数、デッキタグ管理 | `deck_catalog_table`, `catalog_table` | `docs/assets/tkinter-ui-baseline-1.5.2/04-decks.png` |
| `tag_catalog` | タグ名、説明、カラー、デッキ専用タグ管理 | `tag_catalog_table` | `docs/assets/tkinter-ui-baseline-1.5.2/05-tags.png` |
| `season_management` | シーズン追加・更新・削除/アーカイブ、期間、レポート | `season_table` | `docs/assets/tkinter-ui-baseline-1.5.2/06-seasons.png` |
| `youtube_template` | YouTube投稿テンプレートと投稿状態管理 | `youtube_template`, `youtube_status`, `youtube_connect`, `youtube_disconnect`, `youtube_refresh`, `youtube_test_upload` | `docs/assets/tkinter-ui-baseline-1.5.2/07-template.png` |
| `prepare_queue` | MP4準備、投稿前処理キュー、準備対象選択 | `prepare_table`, `prepare_recording` | `docs/assets/tkinter-ui-baseline-1.5.2/10-prepare-internal.png` |
| `reliability` | 事前チェック、導入状態、ホットキー、トレイ、後解析導線 | `reliability_status`, `improvement_status` | `docs/assets/tkinter-ui-baseline-1.5.2/08-reliability.png` |
| `settings_recording_audio` | FFmpeg、音声、録画品質、自動判定、通知、保存先設定 | `settings_form`, `ffmpeg_setup` | `docs/assets/tkinter-ui-baseline-1.5.2/09-settings.png` |
| `data_protection` | 管理データ、バックアップ、復元、整合性診断、クリーンアンインストール | `data_protection_status`, `data_backup_table`, `clean_uninstall` | `docs/assets/tkinter-ui-baseline-1.5.2-popups/13-clean-uninstall.png` |
| `csv_and_update` | 戦績CSV入出力、アプリ更新確認、更新適用 | `csv_status`, `app_update` | `docs/assets/tkinter-ui-baseline-1.5.2/09-settings.png` |
| `dialogs` | 日付選択、FFmpeg導入、投稿、フィルター、タイムライン、戦績入力、診断、レポート | `statistics_date_from_picker`, `ffmpeg_setup`, `history_youtube`, `history_duplicates`, `history_incomplete`, `history_bulk` | `docs/assets/tkinter-ui-baseline-1.5.2-popups/` |

## ゲート条件

PySide6 GUIを通常配布入口へ戻すには、少なくとも次を満たす。

- `STANDARD_GUI_FEATURES`の全keyがPySide6側で実操作または明示的な代替導線として成立している
- 空runtimeだけでなく、DB入りruntimeで戦績、録画履歴、デッキ、タグ、シーズン、統計、設定、データ保護を表示できる
- `scripts/smoke_windows_gui.ps1`相当の配布GUI smokeで、実体のないwidget名だけでは合格しない
- DB schema、設定形式、録画ファイル、queue、manifest、OAuth資格情報を削除・初期化・上書きしない
- PySide6へ移す場合も、TkinterとQtのevent loopを同一プロセスで混在させない

## V2.0.1での判断

V2.0.1時点では、PySide6シェルは上記の同等性を満たしていない。そのため通常配布GUIはTkinter入口へ戻し、PySide6はレビュー/検証入口として残す。
