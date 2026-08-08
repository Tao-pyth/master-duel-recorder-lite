# 録画履歴

## 目的

V0.5.0では録画の開始から完了または失敗までをSQLiteへ保存し、後から結果と失敗理由を確認できるようにします。DBは `user_data/data/db/history.sqlite3` に限定し、録画ファイルそのものとは分離します。

## スキーマと状態

`schema_version` は単一の整数版を持ち、現在の版は2です。`recordings` は録画IDを主キーとし、出力相対パスにも一意制約を持ちます。版2では失敗コード、復旧方針、復旧状態、試行回数、利用者向け説明、内部診断を追加し、別ファイルの修復成果物を `recovery_artifacts` で追跡します。

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
