# 実行時データ方針

## 保存先

Python実行時はカレントプロジェクト直下の`user_data/`、PyInstaller EXE実行時は`%LOCALAPPDATA%\MasterDuelRecorderLite`を既定の保存先にします。環境変数またはCLIで明示した保存先は、この既定値より優先します。EXEの配置フォルダには作業フォルダを作成しません。

```text
user_data/
  config/
    app.toml
    app.toml.previous
  data/
    recording.lock
    video-operations.lock
    data-protection.lock
    recording-state.json
    recording-state.json.previous
    db/
      history.sqlite3
      history.vN.*.backup.sqlite3
    recordings/
      .mdrl-video-staging/
    backups/
    preroll/
    screenshots/
    exports/
    queue/
      upload-preparation.json
      upload-preparation.json.previous
      manifests/
  logs/
```

## Git に含めない理由

`user_data/` には、録画ファイル、SQLiteデータベース、OAuthトークン、アップロードキュー、ログが入る可能性があります。これらはユーザー固有の情報なので GitHub に上げてはいけません。

## 上書き方法

開発や検証で保存先を変えたい場合は、環境変数 `MDRL_USER_DATA_DIR` を使います。CLIから一時的に変える場合は `--user-data-dir` を使います。

V0.16.0以前のEXE隣接`user_data/`は自動移行しません。録画、DB、設定を保護するため、元データを残したまま、利用者がバックアップ後に新しい既定先へ移すか、旧フォルダを明示指定します。

## 失敗時の保持方針

アプリ起動時に中断された録画や処理中のキューを確認します。未完了録画は`failed`へ確定し、修復処理は行いません。録画ファイル、失敗コード、エラー、診断情報は履歴確認用に保持します。

`recording.lock` は同時録画を防ぐ排他制御と診断情報に使います。内容が残っていても削除して解決せず、OSロックを取得できるかで実行中かを判定します。

`history.sqlite3` は録画履歴・戦績・カード情報・動画管理を保持するDBで、V3.1.0の現在版はschema19です。通常入口の旧17/18からの移行は、DB・設定の検証済み保護バックアップを`data/backups/`へ作ってから単一transactionで実行します。単独DBの低水準移行は同じフォルダの版番号付きSQLiteバックアップを使います。失敗時は元DBを保持し、移行前保護バックアップを自動削除しません。[保存構造](retention-schema-3.1.0.md)を参照してください。

`recording-state.json` は直前の録画状態をチェックサム付きで保持します。書込みは同じフォルダの一時ファイルを同期してから原子的に置換し、直前の有効状態を `.previous` に1世代保持します。不完全な一時ファイルやチェックサム不一致は有効状態として読みません。

`app.toml`も検証後に原子的に置換し、直前の内容を`app.toml.previous`へ1世代保持します。初期化は既存設定を上書きせず、リセットには明示的な`--yes`が必要です。

V2.6.0のプリロール一時segmentは `user_data/data/preroll/` 配下に保存します。このフォルダは正式な録画保存先ではなく、短い上限付きバッファです。録画へ取り込み済みまたはfallback済みのsegmentは削除します。削除に失敗しても既存録画やDBは変更せず、次回の上限管理と診断で扱います。

V0.19.0のv7移行では、旧`recovery_artifacts`が参照する成果物だけを録画保存先内で検証して退避し、DB移行成功後に削除します。通常録画と同じパスは削除せず、移行失敗時は退避ファイルとDBを復元します。移行バックアップは自動削除しません。

アップロード準備済みMP4は `exports/{recording_id}/{queue_id}.mp4`、マニフェストは `queue/manifests/{queue_id}.json` に保存します。準備キューはチェックサム付きJSONを原子的に更新し、1世代前を `.previous` に保持します。remux失敗・キャンセル時の部分出力も相対パスをキューへ残し、自動削除しません。

## V3.1.0の元動画整理

容量・期限は初期0（無制限）、投稿後削除は初期falseです。通常GUI起動で移行・未完了削除回復・期限・容量・保存された投稿後再試行を順に行います。生成・参照・診断・設定保存・復元だけで新しい整理を開始しません。

対象は登録済み元動画だけです。容量はrecordings領域の実サイズを数え、保護・未登録・退避ファイルも含めます。exports、clip、preroll、未登録動画は自動削除しません。削除の退避先は同じrecordings領域の`.mdrl-video-staging/`とし、未完了journal・確定理由・再試行要求はDBへ保存します。退避ファイルの存在を容量解放とみなしません。

`video-operations.lock`、`recording.lock`と動画利用leaseで、録画・解析・レビュー・管理データ置換と削除を隔離します。`data-protection.lock`は削除処理中の事前バックアップを世代整理から守ります。利用中・確認不能・事前バックアップ保持不能では元動画を保持します。

元動画の削除成功時だけ動画上のマーカー・未確認候補を除去します。戦績・タグ・メモ・使用/明示非使用/不明・初動・完了投稿URLは残します。動画本体はバックアップに含まれません。JSON取込や復元で戻った動画根拠は内容・世代・時刻基準を確認するまで利用しません。[保存管理](recording-retention.md)、[管理JSON](managed-data.md)、[復元](retention-management-3.1.0.md)を参照してください。
