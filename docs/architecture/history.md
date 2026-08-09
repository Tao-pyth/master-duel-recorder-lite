# 録画履歴

## 目的

V0.5.0では録画の開始から完了または失敗までをSQLiteへ保存し、後から結果と失敗理由を確認できるようにします。DBは `user_data/data/db/history.sqlite3` に限定し、録画ファイルそのものとは分離します。

## スキーマと状態

`schema_version` は単一の整数版を持ち、現在の版は5です。`recordings` は録画IDを主キーとし、出力相対パスにも一意制約を持ちます。版2では復旧情報と`recovery_artifacts`、版3では対戦記録、版4では対戦タイムライン、版5ではデッキ名・タグ辞書と前回入力を追加します。

```text
starting -> recording -> completed
                      -> failed
starting ----------------> failed
```

各履歴は手動・自動などの起点、検出理由、コンテナ、作成・開始・終了時刻、長さ、サイズ、終了コード、エラー、上限付きFFmpeg診断を保持します。時刻はUTCへ正規化したタイムゾーン付きISO 8601です。動画パスは `recordings/` からの相対パスだけを許可し、保存先外部や別録画の結果で確定することを拒否します。

## 録画ライフサイクル

FFmpeg開始前に `starting` を登録します。履歴登録に失敗した場合はFFmpegを開始しません。FFmpeg開始後は `recording` へ更新し、正常停止、開始失敗、異常終了を同じ録画IDの `completed` または `failed` へ確定します。最終更新を再実行しても行は増えません。

開始後の履歴更新に失敗した場合は録画を停止し、エラーとして扱います。録画ロックはすべての終了経路で解放します。DB障害を無視して録画だけを継続する動作は行いません。

## 移行とバックアップ

DBを開くたびにスキーマ版、必須テーブル、SQLiteのquick checkを確認します。アプリより新しいスキーマは変更せず拒否します。旧版DBを移行する場合はSQLiteバックアップAPIで次のファイルを先に作成します。

```text
user_data/data/db/history.vN.UTC_TIMESTAMP.UUID.backup.sqlite3
```

マイグレーションは単一の `BEGIN IMMEDIATE` トランザクションで実行します。途中で失敗した場合は元DBをロールバックし、移行前バックアップも保持します。

## CLI

```powershell
python -m master_duel_recorder_lite history list
python -m master_duel_recorder_lite history list --state failed --since 2026-08-01T00:00:00+09:00 --until 2026-09-01T00:00:00+09:00 --limit 20 --offset 0
python -m master_duel_recorder_lite history show RECORDING_ID
python -m master_duel_recorder_lite history check
```

一覧は開始時刻、未開始なら作成時刻の降順と録画IDの降順で安定して並びます。SQL条件はすべてパラメータとして渡します。存在しないIDと不整合検出は終了コード4、DB操作失敗は3です。

`history check` は次を区別して表示します。

| 種別 | 意味 |
|---|---|
| `MISSING` | DBが参照する録画ファイルがない |
| `UNTRACKED` | MKVまたはMP4がDBへ登録されていない |
| `SIZE_MISMATCH` | DBの確定サイズと実ファイルサイズが異なる |
| `INVALID_REFERENCE` | DB内の相対パスが安全な録画保存先を指さない |

診断は読み取り専用です。録画、DB行、バックアップを削除・修正しません。

## 履歴削除

GUIの録画履歴で削除を確認すると、元録画と`recovery_artifacts`が参照する復旧成果物を録画保存先内の退避領域へ移動します。その後、`duel_record_tags`、`duel_record_changes`、`duel_events`、`duel_records`、`recovery_artifacts`、`recordings`を一つのSQLiteトランザクションで削除し、成功後に退避ファイルを消去します。DB削除に失敗した場合はトランザクションを戻し、退避ファイルを元の場所へ復元します。

存在しない録画ファイルは欠損として記録しつつDB行を削除できます。絶対パス、`..`を含むパス、録画保存先外へ解決されるパスは拒否します。録画または自動監視の実行中も削除を拒否します。録画行を削除するため、同じ録画IDは録画履歴一覧と中断録画の復旧一覧の両方から消えます。この操作はユーザー確認後の恒久削除で、元に戻せません。
