# 設定・運用CLI

## 目的と範囲

V0.8.0ではV1.0.0候補の操作面を`mdrl`拡張CLIへ統一し、V0.9.0では同じCLIをWindows EXEとして配布します。V1.1.0では`mdrl youtube`を追加し、YouTube OAuth接続、投稿素材生成、クリップ出力、MP4自動アップロード、投稿状態確認をCLIから操作します。既存のグローバル初期化オプションは互換用に残しますが、新しい操作はサブコマンドを使用します。

## コマンド体系

| コマンド | 責務 | 主な識別子 |
|---|---|---|
| `config init/show/get/set/reset` | 非シークレット設定の初期化、参照、変更 | 設定キー |
| `doctor` / `list-inputs` | FFmpeg、入力、保存先の検査 | なし |
| `status` | 全サブシステムの統合診断 | なし |
| `record` / `watch` | 手動録画、自動録画補助 | 録画ID |
| `history list/show/check` | 録画履歴とファイル整合性 | 録画ID |
| `prepare enqueue/list/show/run/cancel` | アップロード準備 | 録画ID、キューID |

録画IDは録画開始時に発行し、履歴とアップロード準備で共通利用します。キューIDは同じ録画に対する準備処理を識別します。

## 出力と終了コード

| 終了コード | 意味 |
|---|---|
| 0 | 正常完了。警告だけの`doctor`も含む |
| 2 | 引数、設定、録画環境を利用者が修正する必要がある |
| 3 | ファイル、DB、外部プロセス等の処理に失敗した |
| 4 | 不整合、未発見、準備失敗など確認が必要である |
| 130 | Ctrl+Cを正常完了として扱えない処理を中断した |

代表的な標準エラーは`[ERROR] E_CODE: 要約`と`対処: 次の操作`の2行です。詳細診断は`--verbose`指定時だけ追加表示します。通常結果は標準出力、エラーは標準エラーへ出します。

`status --json`はスキーマ版2として`overall`、`environment`、`runtime`、`recording`、`history`、`upload_queue`、`errors`を出力します。人向け表示と同じ収集結果を描画し、秘密情報と実行時データの絶対パスを含めません。一部の診断に失敗しても残りを収集し、全体を成功にしません。

## 設定の安全性

`config set`は許可済みキーだけを型変換し、`AppConfig`全体を検証してから保存します。不正値では現在設定を変更しません。保存は一時ファイルをfsyncして原子的に置換し、直前世代を`app.toml.previous`へ保持します。`config init`は既存設定を拒否し、`config reset`は`--yes`を必須とします。

## 長時間処理と再実行

`record`と`watch`はCtrl+Cで正常停止を試み、成功時は終了コード0です。`prepare run`は項目ごとに開始と完了状態を表示します。Ctrl+Cは終了コード130として扱い、processing状態と部分出力を削除しません。次回の`prepare`コマンドは中断項目をwaitingへ戻し、明示的に再実行できます。

## 互換方針

`--init-user-data`、`--write-default-config`、`--show-config`はV0.7以前との互換用です。`--write-default-config`も既存設定を上書きしません。新規手順は`config init`、`config show`を使用します。互換オプションを削除する場合は、Issue、移行手順、リリースノートを先に用意します。
