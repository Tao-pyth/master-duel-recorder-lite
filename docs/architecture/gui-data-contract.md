# GUI境界とレビュー基盤の保存契約

## 目的

V1.5.0では、Tkinter GUIを正式導線として維持したまま、将来のPySide6移行に備えてレビュー画面のデータ境界をGUI非依存にする。目的は見た目の刷新ではなく、SQLite DB、設定、録画ファイル、YouTube OAuth資格情報を壊さずに新しい画面を検証できる状態へ分離することである。

## 守るデータ

| 種類 | 既定場所 | V1.5.0の契約 |
| --- | --- | --- |
| SQLite履歴DB | `user_data/data/db/history.sqlite3` | 既存Repositoryと`RecorderApplicationService`だけが更新する。V1.5.0ではschema migrationを行わない |
| 非シークレット設定 | `user_data/config/app.toml` | PySide6レビューのための永続設定は追加しない。設定migrationも行わない |
| 録画ファイル | `user_data/data/recordings/` | レビュー画面は読み取りと外部プレイヤー起動だけを行う。削除、移動、上書きはしない |
| exports | `user_data/data/exports/` | クリップ出力は`ClipExportService`経由で新規ファイルとして作成し、元録画のサイズと更新時刻を検証する |
| queue / manifest | `user_data/data/queue/`, `user_data/data/exports/` | YouTube投稿準備の既存サービスだけが作成・更新する |
| logs | `user_data/logs/` | レビューViewModelにはOAuth token、client secret、認可コードを書かない |
| OAuth資格情報 | Windows資格情報ストア | GUI、CLI、PySide6レビューは直接保存しない。YouTube連携サービスだけが扱う |
| 移行パック | ユーザー指定先 | 録画ファイルとOAuth資格情報を含めない既存契約を維持する |

## 層ごとの責務

- GUI層: 画面表示、入力値の受け取り、ユーザー操作の発火だけを行う。
- Review ViewModel: 録画概要、動画参照、タイムライン、戦績概要、クリップ候補をGUI非依存のdataclassとして表現する。
- Application層: `RecorderApplicationService`がRepository、`RecordingBrowser`、`ClipExportService`、YouTube投稿履歴を束ねる。
- Repository層: SQLiteの読み書き、制約、schema互換を担当する。
- RuntimePaths: Tkinter、CLI、PySide6が同じ`user_data`を解決するための唯一の入口にする。
- DataProtection: 削除、再関連付け、保存先変更など、既存データへ影響する操作の事前バックアップを担当する。

## GUI層で直接行わない操作

- SQLite DBへの直接SQL実行
- 録画ファイルの削除、移動、上書き、修復
- OAuth token、refresh token、client secret、認可コードの保存やログ出力
- queue、manifest、移行パックの直接生成
- FFmpegを直接呼ぶクリップ出力
- TkinterとQtのevent loopを同一プロセスで混在させること

## 許可する経路

- 録画参照: `RecorderApplicationService.resolve_recording`
- 外部再生: `RecorderApplicationService.play_recording`
- 保存場所表示: `RecorderApplicationService.reveal_recording`
- タイムライン表示: `RecorderApplicationService.list_timeline`
- 現在位置マーカー: `RecorderApplicationService.add_review_marker`
- クリップ出力: `RecorderApplicationService.export_review_clip`
- YouTube URL参照: `YouTubeUploadRepository.completed_for_recording`をApplication層経由で利用する

## PySide6レビューの方針

PySide6レビューは`pyside_review.py`へ隔離し、PySide6 importは同モジュール内部の起動時だけ行う。未導入環境では`review status`が要確認状態を返し、`review launch --fallback-external`はWindows既定プレイヤーへ戻る。

Tkinter GUIからは別プロセスで`mdrl review launch RECORDING_ID --fallback-external`を起動する。これにより、TkinterとQtのevent loop競合を避け、PySide6起動失敗が既存GUIの状態や管理データへ影響しないようにする。

## DB / 設定migrationを行わない理由

V1.5.0で追加するデータはレビュー表示用の派生データであり、既存の録画履歴、対戦記録、タイムライン、YouTube投稿履歴から生成できる。新しい永続列や設定キーがなくても、録画再生、タイムライン選択、現在位置マーカー、クリップ出力導線を検証できるため、DB schema migrationとConfiguration Migrationは不要である。

## 互換条件

- 既存`user_data`のDB、設定、録画、exports、queue、logsを削除または初期化しない。
- `review show`は読み取り専用で、録画ファイルが存在する場合だけViewModelを生成する。
- `review launch`の失敗は外部プレイヤーfallbackまたはCLI終了コードで表現し、保存データを書き換えない。
- クリップ出力は新規ファイルだけを`exports`へ作成し、元録画を変更しない。
- YouTube OAuth資格情報はOS資格情報ストアに残し、ViewModel、DB、設定、queue、manifest、ログへ含めない。
