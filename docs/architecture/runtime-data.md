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
    recording-state.json
    recording-state.json.previous
    db/
      history.sqlite3
      history.vN.*.backup.sqlite3
    recordings/
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

`history.sqlite3` は録画履歴DBです。旧スキーマを移行する前には同じフォルダへ版番号付きバックアップを作り、移行失敗時は元DBのトランザクションをロールバックします。バックアップは自動削除しません。

`recording-state.json` は直前の録画状態をチェックサム付きで保持します。書込みは同じフォルダの一時ファイルを同期してから原子的に置換し、直前の有効状態を `.previous` に1世代保持します。不完全な一時ファイルやチェックサム不一致は有効状態として読みません。

`app.toml`も検証後に原子的に置換し、直前の内容を`app.toml.previous`へ1世代保持します。初期化は既存設定を上書きせず、リセットには明示的な`--yes`が必要です。

V2.6.0のプリロール一時segmentは `user_data/data/preroll/` 配下に保存します。このフォルダは正式な録画保存先ではなく、短い上限付きバッファです。録画へ取り込み済みまたはfallback済みのsegmentは削除します。削除に失敗しても既存録画やDBは変更せず、次回の上限管理と診断で扱います。

V0.19.0のv7移行では、旧`recovery_artifacts`が参照する成果物だけを録画保存先内で検証して退避し、DB移行成功後に削除します。通常録画と同じパスは削除せず、移行失敗時は退避ファイルとDBを復元します。移行バックアップは自動削除しません。

アップロード準備済みMP4は `exports/{recording_id}/{queue_id}.mp4`、マニフェストは `queue/manifests/{queue_id}.json` に保存します。準備キューはチェックサム付きJSONを原子的に更新し、1世代前を `.previous` に保持します。remux失敗・キャンセル時の部分出力も相対パスをキューへ残し、自動削除しません。
