# 失敗時の復旧

## 目的

V0.6.0ではクラッシュ、容量不足、強制終了後に中断録画を検出し、元ファイルを変更せず検査・修復します。判断不能な失敗を成功扱いせず、利用者向け説明と内部診断を分離して履歴へ保存します。

## 失敗分類

| 原因コード | 復旧方針 |
|---|---|
| `application_interrupted` | 手動確認 |
| `storage_full` | 容量確保後に再試行 |
| `output_missing` | 復旧不能 |
| `output_empty` | 復旧不能 |
| `process_crash` | 手動確認 |
| `operation_timeout` | 再試行 |
| `output_corrupt` | 手動確認 |
| `repair_failed` | 手動確認 |
| `unknown` | 手動確認 |

復旧状態は `not_required`、`pending`、`inspecting`、`repairable`、`repaired`、`ignored`、`unrecoverable` です。検査・修復の試行回数、利用者向けメッセージ、内部診断を同じ録画履歴へ保存します。

## 原子的な録画状態

録画の `starting`、`recording`、`completed`、`failed` を `user_data/data/recording-state.json` へ保存します。JSONはスキーマ版、録画ID、PID、起点、録画相対パス、時刻、SHA-256チェックサムを持ちます。

一時ファイルへ書いて `fsync` した後に `os.replace` で原子的に置換します。直前の検証済み状態は `recording-state.json.previous` に1世代保持します。現在ファイルが途中書込みやチェックサム不一致なら前世代へフォールバックし、`.tmp` は読込対象にしません。

## 中断検出

`record`、`watch`、`history`、`recovery`の起動時に、履歴の `starting/recording`、OS録画ロック、状態ファイルのPIDを照合します。録画ロックが保持中、または一致するPIDが生存中なら他プロセスの録画として変更しません。ロックがなく対応プロセスもない場合だけ `application_interrupted` として `failed/pending` へ更新します。

状態ファイルが壊れていても、ロックがなく履歴に実行中状態が残る場合は内部診断へ破損理由を含めて中断扱いにします。正常完了済みの履歴は変更しません。

## 検査と修復

`inspect` はffprobeでストリームと長さを読み、元ファイルを書き換えません。欠損・空ファイルは復旧不能、ツール実行失敗・タイムアウトは再試行、コンテナ解析失敗は修復候補として記録します。

`repair` は次の条件で実行します。

- 元ファイルと異なるUUID付きパスへ出力する
- FFmpegへ `-map 0 -c copy -n` を渡し、シェルを使用しない
- 出力が非空でffprobe検証に成功した場合だけ `repaired` とする
- 処理前後で元ファイルのサイズと更新時刻が一致することを確認する
- 成功・失敗・タイムアウトの成果物をDBで追跡し、自動削除しない

## CLI

```powershell
python -m master_duel_recorder_lite recovery list
python -m master_duel_recorder_lite recovery detect
python -m master_duel_recorder_lite recovery inspect RECORDING_ID
python -m master_duel_recorder_lite recovery repair RECORDING_ID --dry-run
python -m master_duel_recorder_lite recovery repair RECORDING_ID
python -m master_duel_recorder_lite recovery ignore RECORDING_ID
```

`--dry-run` は予定する元パスと別出力パスを表示し、FFmpeg実行、ファイル作成、履歴更新を行いません。復旧不能、タイムアウト、修復失敗は非ゼロ終了コードを返します。`ignore` は履歴状態だけを変更し、ファイルを削除しません。
