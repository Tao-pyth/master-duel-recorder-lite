# Tkinter UI Baseline for PySide6 Refresh

## 目的

V1.5.2では、将来のGUI刷新でTkinterからPySide6へ移行する前に、現行Tkinter UIの画面、呼び出し導線、要件、改善候補を保存する。

この文書は実装指示ではなく、移行前の現状記録である。改善点はこの場で修正せず、後続の設計・Issue化候補として残す。

## 保存条件

- 記録日: 2026-08-22
- 現行アプリバージョン: 1.5.1
- 対象計画バージョン: 1.5.2
- 対象GUI: `src/master_duel_recorder_lite/gui.py` のTkinter GUI
- 補助GUI: `src/master_duel_recorder_lite/pyside_review.py` の隔離PySide6レビュー入口
- 画像取得方法: GUI smoke用のダミーデータ、および本番SQLiteコピーに表示確認用サンプルを追加したデータで各ページを表示し、実ウィンドウをPNG保存した
- 実行時データ: 画像取得時は `build/ui-baseline-runtime/` と `build/ui-baseline-runtime-rich/` を使い、利用者の `user_data/` は変更しない
- 本番SQLite確認: `user_data/data/db/history.sqlite3` はコピー元として確認したが、記録時点では `recordings=0`、`duel_records=0`、`duel_catalog_entries=0`、`seasons=0` だったため、データ入り表示はコピー先にサンプルを追加して撮影した

## 画像保存先

| 種別 | 画面 | 画像 |
| --- | --- | --- |
| 主要ナビ | 録画 | [01-record.png](../assets/tkinter-ui-baseline-1.5.2/01-record.png) |
| 主要ナビ | 戦績管理 | [02-history.png](../assets/tkinter-ui-baseline-1.5.2/02-history.png) |
| 主要ナビ | 統計 | [03-statistics.png](../assets/tkinter-ui-baseline-1.5.2/03-statistics.png) |
| 主要ナビ | デッキ名 | [04-decks.png](../assets/tkinter-ui-baseline-1.5.2/04-decks.png) |
| 主要ナビ | タグ | [05-tags.png](../assets/tkinter-ui-baseline-1.5.2/05-tags.png) |
| 主要ナビ | シーズン | [06-seasons.png](../assets/tkinter-ui-baseline-1.5.2/06-seasons.png) |
| 主要ナビ | テンプレート | [07-template.png](../assets/tkinter-ui-baseline-1.5.2/07-template.png) |
| 主要ナビ | 信頼性 | [08-reliability.png](../assets/tkinter-ui-baseline-1.5.2/08-reliability.png) |
| 主要ナビ | 設定 | [09-settings.png](../assets/tkinter-ui-baseline-1.5.2/09-settings.png) |
| 内部ページ | MP4準備 | [10-prepare-internal.png](../assets/tkinter-ui-baseline-1.5.2/10-prepare-internal.png) |
| 内部ページ | 改善 | [11-improve-internal.png](../assets/tkinter-ui-baseline-1.5.2/11-improve-internal.png) |

## データ入り画像保存先

本番SQLiteを検証用へコピーしたうえで、コピー先にサンプルの録画履歴、手動戦績、デッキ、タグ、シーズン、タイムライン、振り返りを追加して撮影した。元の `user_data/`、録画、SQLite DB、OAuth資格情報は変更していない。

| 種別 | 画面 | 画像 |
| --- | --- | --- |
| 主要ナビ | 録画 | [01-record-rich.png](../assets/tkinter-ui-baseline-1.5.2-rich/01-record-rich.png) |
| 主要ナビ | 戦績管理 | [02-history-rich.png](../assets/tkinter-ui-baseline-1.5.2-rich/02-history-rich.png) |
| 主要ナビ | 統計 | [03-statistics-rich.png](../assets/tkinter-ui-baseline-1.5.2-rich/03-statistics-rich.png) |
| 主要ナビ | デッキ名 | [04-decks-rich.png](../assets/tkinter-ui-baseline-1.5.2-rich/04-decks-rich.png) |
| 主要ナビ | タグ | [05-tags-rich.png](../assets/tkinter-ui-baseline-1.5.2-rich/05-tags-rich.png) |
| 主要ナビ | シーズン | [06-seasons-rich.png](../assets/tkinter-ui-baseline-1.5.2-rich/06-seasons-rich.png) |
| 主要ナビ | テンプレート | [07-template-rich.png](../assets/tkinter-ui-baseline-1.5.2-rich/07-template-rich.png) |
| 主要ナビ | 信頼性 | [08-reliability-rich.png](../assets/tkinter-ui-baseline-1.5.2-rich/08-reliability-rich.png) |
| 主要ナビ | 設定 | [09-settings-rich.png](../assets/tkinter-ui-baseline-1.5.2-rich/09-settings-rich.png) |
| 内部ページ | MP4準備 | [10-prepare-internal-rich.png](../assets/tkinter-ui-baseline-1.5.2-rich/10-prepare-internal-rich.png) |
| 内部ページ | 改善 | [11-improve-internal-rich.png](../assets/tkinter-ui-baseline-1.5.2-rich/11-improve-internal-rich.png) |

## ポップアップ画像保存先

アプリがTkinterで構成する独自ポップアップは、実ウィンドウとして個別に撮影した。Windows標準の `messagebox`、`filedialog`、`colorchooser`、`simpledialog` はOSが構成するため、画像ではなく後続の「ダイアログとファイル選択導線」表で呼び出し元と要件を固定する。

| 種別 | ポップアップ | 画像 |
| --- | --- | --- |
| 補助表示 | ツールチップ | [00-tooltip.png](../assets/tkinter-ui-baseline-1.5.2-popups/00-tooltip.png) |
| 入力 | 日付を選択 | [01-calendar-picker.png](../assets/tkinter-ui-baseline-1.5.2-popups/01-calendar-picker.png) |
| 初回導入 | FFmpegのセットアップ | [02-ffmpeg-setup.png](../assets/tkinter-ui-baseline-1.5.2-popups/02-ffmpeg-setup.png) |
| 投稿 | YouTubeへ投稿 | [03-youtube-upload.png](../assets/tkinter-ui-baseline-1.5.2-popups/03-youtube-upload.png) |
| 絞り込み | 戦績管理フィルター | [04-history-filter.png](../assets/tkinter-ui-baseline-1.5.2-popups/04-history-filter.png) |
| データ確認 | 重複戦績候補を比較 | [05-duplicate-candidates.png](../assets/tkinter-ui-baseline-1.5.2-popups/05-duplicate-candidates.png) |
| 対戦記録 | 対戦タイムライン | [06-timeline.png](../assets/tkinter-ui-baseline-1.5.2-popups/06-timeline.png) |
| 戦績入力 | 戦績を簡易入力 | [07-quick-duel-editor.png](../assets/tkinter-ui-baseline-1.5.2-popups/07-quick-duel-editor.png) |
| 戦績入力 | 未完了戦績を連続処理 | [08-incomplete-duel-queue.png](../assets/tkinter-ui-baseline-1.5.2-popups/08-incomplete-duel-queue.png) |
| 戦績入力 | 戦績を一括編集 | [09-bulk-duel-editor.png](../assets/tkinter-ui-baseline-1.5.2-popups/09-bulk-duel-editor.png) |
| 戦績入力 | 対戦記録 | [10-duel-editor.png](../assets/tkinter-ui-baseline-1.5.2-popups/10-duel-editor.png) |
| 診断 | 録画診断 | [11-recording-diagnostic.png](../assets/tkinter-ui-baseline-1.5.2-popups/11-recording-diagnostic.png) |
| 分析 | シーズンレポート | [12-season-report.png](../assets/tkinter-ui-baseline-1.5.2-popups/12-season-report.png) |
| 危険操作 | クリーンアンインストール | [13-clean-uninstall.png](../assets/tkinter-ui-baseline-1.5.2-popups/13-clean-uninstall.png) |

## 画面一覧

### 共通シェル

- 左サイドバーに `録画`、`戦績管理`、`統計`、`デッキ名`、`タグ`、`シーズン`、`テンプレート`、`信頼性`、`設定` を表示する。
- ヘッダーにページタイトル、未完了戦績件数、処理中表示を置く。
- 左下に接続・準備状態を表示する。
- 画面サイズは初期 `1180x760`、最小 `980x640`。

### 録画

- 録画対象選択、対象更新、選択保存を提供する。
- 録画状態、経過時間、録画ID、保存先、自動判定状態、音声状態を表示する。
- `録画開始`、`停止`、`自動監視開始`、`戦績を追加（録画なし）` を提供する。
- 環境診断、数値診断フォルダ表示、数値診断ZIP保存、アクティビティ履歴を表示する。
- PySide6移行後も、録画中・監視中・候補録画中・失敗状態は色だけでなく文言で判別できる必要がある。

### 戦績管理

- 未完了処理、一括編集、手動追加、再生、対戦記録編集、削除、重複候補比較、更新、整合性確認、列選択、YouTube投稿、フィルター、フィルター解除を提供する。
- 一覧列は `開始日時`、`デッキ名`、`勝敗`、`先後`、`コイン`、`対戦種別`、`時間`、`サイズ`、`相手デッキ`、`登録元`。
- 行選択時に再生・編集・削除・YouTube投稿の可否を更新する。
- ダブルクリック動作は表示設定で `録画再生` または `戦績編集` に切り替わる。
- PySide6移行後も、録画あり・録画なし・取込データを同じ一覧で扱うこと。

### 統計

- 全体勝率、条件適用後、先後別勝率を上部に表示する。
- 期間、シーズン、デッキ、タグ、先後、推移単位、コイン面で絞り込む。
- タブは `勝利数・勝率推移`、`デッキ別全体`、`デッキ先後別`、`コイントス別`、`シーズン別`。
- PySide6移行後も、少数標本や未設定条件が分かる表示を維持すること。

### デッキ名

- 名前、説明、カラー、相手デッキのみ、履歴・統計の選択肢で非表示、デッキタグを編集する。
- 一覧列はカラー、名前、説明、用途。
- 追加、保存、削除、一覧更新を提供する。
- V1.5.2既存Issueでは使用回数列と利用頻度順が予定されているため、PySide6側でも列追加を前提に設計する。

### タグ

- 名前、説明、カラー、デッキ名登録でのみ使用を編集する。
- 一覧列はカラー、名前、説明、用途。
- 追加、保存、削除、一覧更新を提供する。
- PySide6移行後も、通常タグとデッキ専用タグの用途差を明示すること。

### シーズン

- 名前、種別、開始日、終了日、説明を編集する。
- 一覧列はシーズン、種別、期間、状態。
- 追加、保存、削除またはアーカイブ、レポート表示、一覧更新を提供する。
- 日付入力はカレンダーポップアップと手入力を併用する。

### テンプレート

- YouTube投稿テンプレートとして、タイトル、概要欄、タグを編集する。
- 使用できる変数一覧を表示し、テンプレートを保存する。
- OAuth資格情報、token、client secret、認可コードは表示・保存しない。

### 信頼性

- 30秒事前チェック、初回導入ウィザード、ホットキーとトレイの状態を表示する。
- 状態更新を提供する。
- PySide6移行後も、診断結果は利用者向け文言にし、内部パスや秘密情報を出さない。

### 設定

- タブは `録画設定`、`YouTube`、`管理データ`、`CSV入出力`、`表示`、`アプリ更新`。
- 録画設定ではFFmpeg、音声入力、録画品質、自動判定、Windows通知、データ保存先を扱う。
- YouTubeでは連携状態、連携、切断、接続確認、最新録画でprivateテスト投稿を扱う。
- 管理データでは管理データ入出力、初期化、データ保全、バックアップ、復元、整合性診断、クリーンアンインストールを扱う。
- CSV入出力では戦績CSVの書き出し、取り込み、サンプル保存を扱う。
- 表示では戦績管理セル色とダブルクリック動作を扱う。
- アプリ更新では起動後確認、更新確認、ダウンロードして更新を扱う。
- PySide6移行後も、危険操作は通常操作と視覚的に分離し、確認語や確認ダイアログを維持すること。

### MP4準備

- 現在は主要ナビから外れているが、`show_page("prepare")` で呼び出し可能な内部ページとして残っている。
- 対象録画、タイトル、キュー追加、待機中実行、準備キュー一覧を提供する。
- V1.4.1以降はYouTube投稿ダイアログ内の投稿前処理として扱う方針のため、PySide6移行時は独立ページとして復活させるか、内部ページを削除するかを設計判断する必要がある。

### 改善

- 現在は主要ナビから外れているが、`show_page("improve")` で呼び出し可能な内部ページとして残っている。
- 入力削減と運用管理の状態更新、録画なし戦績追加を提供する。
- V1.4.1以降は未成熟な主要ナビとして露出しない方針のため、PySide6移行時は戦績管理や統計へ機能を吸収するかを判断する必要がある。

### PySide6レビュー

- `pyside_review.py` に隔離され、Tkinter GUIとは別プロセスで起動する。
- 表示内容は録画ID、動画名、動画プレイヤー、再生、停止、外部で開く、現在位置にマーカー、選択位置をクリップ出力、タイムライン、YouTubeを開く。
- PySide6未導入時は既存GUIを固めず、警告または外部プレイヤーfallbackで扱う。
- 全GUI刷新時も、TkinterとQtのevent loopを同一プロセスで混在させない既存契約を確認すること。

## ダイアログとファイル選択導線

| 導線 | 種別 | 主な目的 | 移行時の要件 |
| --- | --- | --- | --- |
| ツールチップ | `Toplevel` | アイコンボタンの意味を補足 | アイコンだけの操作には同等の説明を持たせる |
| 日付を選択 | `Toplevel` | シーズン・統計の日付選択 | 7列カレンダー、今月へ、前月、翌月を維持 |
| カラー選択 | `colorchooser` | デッキ、タグ、履歴セル色 | 色だけでなく文字情報も維持 |
| FFmpegのセットアップ | `Toplevel` | 初回FFmpeg導入 | ダウンロード前確認、導入先選択、進捗、失敗表示を維持 |
| YouTubeへ投稿 | `Toplevel` | タイトル、概要欄、タグ、公開範囲、投稿済みURL | public投稿は追加確認し、投稿済みは再投稿しない |
| 戦績管理フィルター | `Toplevel` | シーズン、デッキ、タグ、コイン、登録元、保存済み条件 | 複数タグ選択、保存済み条件、解除導線を維持 |
| 重複戦績候補を比較 | `Toplevel` | 候補比較、個別編集、片方削除、両方保持 | 候補提示だけではデータ変更しない |
| 対戦タイムライン | `Toplevel` | 候補確認、却下、手動マーカー追加 | candidate/confirmed/rejectedの状態遷移を維持 |
| 戦績を簡易入力 | `Toplevel` | 勝敗、先後、コイントス、相手デッキを高速入力 | 詳細入力への遷移と後回し保存を維持 |
| 未完了戦績を連続処理 | `Toplevel` | 未入力・下書き戦績の連続処理 | 前へ、後回し、下書き、詳細入力を維持 |
| 戦績を一括編集 | `Toplevel` | 複数戦績の一括変更 | 適用前確認とタグ追加・削除を維持 |
| 対戦記録 | `Toplevel` | 詳細な対戦記録編集 | 録画状態、YouTubeリンク、タグ、シーズン期間外確認を維持 |
| 録画診断 | `Toplevel` | 失敗分類、音声状態、FFmpeg診断表示 | 長文診断をコピー・確認しやすくする |
| シーズンレポート | `Toplevel` | 概要、デッキ・先後、コイントス、推移、振り返り | HTML出力、保存、アーカイブ、比較を維持 |
| クリーンアンインストール | `Toplevel` | 実行時データ削除 | 確認語、録画・監視停止条件、削除範囲表示を維持 |
| ファイル保存 | `filedialog` | 診断ZIP、CSV、管理データ、HTML、サンプル保存 | 上書き確認とキャンセル時無変更を維持 |
| ファイル選択 | `filedialog` | 既存FFmpeg、CSV、管理データ、バックアップ、録画再関連付け | 対象種別の検証と適用前確認を維持 |
| フォルダ選択 | `filedialog` | FFmpeg導入先、データ保存先変更 | 保存先変更はバックアップと検査を維持 |
| 確認・警告 | `messagebox` / `simpledialog` | 削除、初期化、切断、更新、終了、上書き | 危険操作では理由と影響範囲を明示 |

## データ保護要件

- SQLite DB、録画ファイル、設定、queue、manifest、OAuth資格情報をGUI層から直接操作しない。
- 既存の `RecorderApplicationService`、Repository、DataProtection、RuntimePaths 経由の責務分離を維持する。
- DB schema、設定形式、OAuth資格情報、録画ファイルの保存先を変更する場合は、別途バックアップ、移行、ロールバック、検証Issueを先に作る。
- 画像保存やPySide6移行検証では、実利用者の `user_data/` を初期化・上書きしない。

## PySide6移行時の最低要件

- 主要ナビ9画面と内部ページ2画面の扱いを明示し、削除する場合も理由を文書化する。
- Tkinterで提供している全Toplevel導線に対し、PySide6側で画面、モーダル、別プロセス、または廃止のいずれかを決める。
- 録画中、自動監視中、データ保全中、更新中、アンインストール準備中の操作可否をサービス層の状態と一致させる。
- 色表示は情報の補助にとどめ、勝敗、先後、録画状態、警告、危険操作は文字でも判別できるようにする。
- YouTube OAuth資格情報、client secret、refresh token、認可コードを画面、設定、DB、ログへ出さない。
- GUI smokeで、起動、主要部品、バージョン、正常終了、既定保存先分離を検証する。

## 課題候補

| ID | 課題 | 理由 | 優先度 |
| --- | --- | --- | --- |
| UI-BASE-001 | 主要ナビに存在しない `prepare` / `improve` の扱いを決める | 画面は構築されるが通常導線から外れており、PySide6移行時に残す・統合する・削除する判断が必要 | P1 |
| UI-BASE-002 | 設定画面を情報領域ごとに再編する | 録画、YouTube、管理データ、CSV、表示、更新が同一Notebookに集約され、危険操作と通常設定の密度が高い | P1 |
| UI-BASE-003 | 戦績管理ツールバーのアイコン操作を整理する | 操作数が多く、アイコンだけでは新規利用者が意味を把握しづらい。ツールチップ依存を下げたい | P1 |
| UI-BASE-004 | ダイアログ過多をワークフロー単位で再設計する | 戦績入力、未完了処理、詳細編集、一括編集、タイムライン、YouTube投稿が別ダイアログで分散している | P2 |
| UI-BASE-005 | 録画診断とデータ保全の長文表示をコピー・保存しやすくする | 現行はText表示中心で、調査時の共有や再確認に一手間かかる | P2 |
| UI-BASE-006 | デッキ色・タグ色の表示をスウォッチ基準へ統一する | Treeview上の色表現はTkinter制約が強く、明るい色の視認性に課題がある | P1 |
| UI-BASE-007 | 危険操作の確認表示を共通化する | 削除、初期化、復元、アンインストール、更新、公開投稿で確認表現が分散している | P2 |
| UI-BASE-008 | PySide6レビューを全体刷新時の共通レビュー部品へ拡張する | 現在は隔離PoCであり、録画履歴、対戦記録、タイムラインとの画面統合は未設計 | P2 |

これらの課題候補は、現行Tkinter UIの保存時点で見つかった改善余地である。V1.5.2では修正せず、PySide6刷新計画または後続Version Planで個別Issueへ分解する。
