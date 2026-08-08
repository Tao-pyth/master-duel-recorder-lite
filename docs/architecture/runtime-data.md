# 実行時データ方針

## 保存先

実行時データはリポジトリ直下の `user_data/` を既定の保存先にします。将来的にはユーザー設定で別の保存先を選べるようにします。

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

## 復旧方針

アプリ起動時に中断された録画や処理中のキューを確認します。安全に再開できないものは自動処理せず、手動確認が必要な状態へ移します。初心者向けに言うと、迷ったら勝手に進めず、データを残して止まる設計にします。

`recording.lock` は同時録画を防ぐ排他制御と診断情報に使います。内容が残っていても削除して解決せず、OSロックを取得できるかで実行中かを判定します。

`history.sqlite3` は録画履歴DBです。旧スキーマを移行する前には同じフォルダへ版番号付きバックアップを作り、移行失敗時は元DBのトランザクションをロールバックします。バックアップは自動削除しません。

`recording-state.json` は直前の録画状態をチェックサム付きで保持します。書込みは同じフォルダの一時ファイルを同期してから原子的に置換し、直前の有効状態を `.previous` に1世代保持します。不完全な一時ファイルやチェックサム不一致は有効状態として読みません。

`app.toml`も検証後に原子的に置換し、直前の内容を`app.toml.previous`へ1世代保持します。初期化は既存設定を上書きせず、リセットには明示的な`--yes`が必要です。

修復動画は元録画と同じ日付フォルダへ `.recovered.UUID` を含む別名で保存します。元録画、修復失敗時の部分成果物、移行バックアップは自動削除しません。

アップロード準備済みMP4は `exports/{recording_id}/{queue_id}.mp4`、マニフェストは `queue/manifests/{queue_id}.json` に保存します。準備キューはチェックサム付きJSONを原子的に更新し、1世代前を `.previous` に保持します。remux失敗・キャンセル時の部分出力も相対パスをキューへ残し、自動削除しません。
