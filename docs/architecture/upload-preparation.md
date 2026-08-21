# アップロード準備

## 目的と範囲

V0.7.0では録画を外部アップローダーへ渡せる状態まで、ローカルで検証・エクスポート・記述しました。V1.1.0ではこの準備結果を再利用し、YouTube公式OAuth連携とMP4自動アップロードを追加しました。V1.4.0では一般配布向けに、GUIからYouTube連携とprivate投稿を完了できる導線へ拡張します。準備キュー、投稿状態、OAuth資格情報は責務を分け、秘密情報を設定、manifest、queue、標準出力、ログへ保存しません。

## メタデータ

| 項目 | 制約 |
|---|---|
| title | 必須、100文字以内、改行・制御文字なし |
| description | 5000文字以内 |
| tags | 30件以内、各100文字以内、大文字小文字を無視して重複不可 |
| privacy | `private`、`unlisted`、`public`、既定は`private` |

UnicodeはNFCへ正規化します。許可した4項目以外は拒否するため、APIキー、OAuth情報、クライアントシークレットをモデルやマニフェストへ混入できません。`public`はV1.1.0以降のYouTube投稿で明示指定された場合だけ使用します。

## メディア検証

ffprobeで拡張子とコンテナ、動画長、ストリームを検証します。MKVはmatroska/webm、MP4はmov/mp4コンテナとして一致する必要があります。

| 結果 | 条件 | 準備可否 |
|---|---|---|
| valid | 正の長さと映像・音声がある | 可 |
| warning | 正の長さと映像があり、音声がない | 可 |
| invalid | 欠損、空、破損、映像なし、長さ不正、コンテナ不一致 | 不可 |

検証結果と警告・エラーはキューへ保存します。invalidを完了状態にしません。

## エクスポート

FFmpegへ `-map 0 -c copy -movflags +faststart -n` を渡し、再エンコードせずMP4へremuxします。出力は次の順で確定します。

1. `exports/{recording_id}/` のUUID付き `.partial.mp4` へ出力する
2. ffprobeで一時出力を再検証する
3. 元録画のサイズと更新時刻が変わっていないことを確認する
4. `exports/{recording_id}/{queue_id}.mp4` へ原子的に置換する

最終出力が既にある場合は上書きせず、再検証に成功した同一キュー出力だけを再利用します。失敗・キャンセル時は部分出力を確定せず、相対パスをキューへ記録します。

## 準備キュー

状態は `waiting -> processing -> completed/failed/cancelled` です。failedは明示再実行でwaitingへ戻せます。同一録画のwaiting、processing、completed重複は拒否します。

キューは `upload-preparation.json` としてチェックサム付きで保存し、一時ファイルをfsync後に原子的置換します。直前の検証済み世代を `.previous` に保持します。起動時にprocessingだった項目は中断としてwaitingへ戻します。複数項目のうち1件が失敗しても他項目を処理します。

## マニフェスト

スキーマ版1のJSONには次だけを含めます。

- 生成時刻、キューID、元録画ID
- user_dataからの元録画・エクスポート相対パス
- 両ファイルのサイズとSHA-256
- エクスポートのコンテナ、長さ、ストリーム
- 許可済みメタデータと検証結果

絶対パス、`..`、未知フィールド、認証情報は拒否します。既存マニフェストは同じキュー・録画・出力ハッシュの場合だけ再利用し、異なる内容で上書きしません。

## GUI

GUIでは録画IDの手入力を要求せず、完了済みで実ファイルが存在する録画を、開始日時・自分デッキ・勝敗・ファイル名を含む表示名から選択します。戦績管理で選択中の録画からMP4準備ページを開くこともできます。V1.4.0以降は同じ戦績管理からYouTube投稿ダイアログも開けます。内部のキュー契約は録画IDを維持します。

## YouTube投稿

YouTube投稿は`youtube_uploads`テーブルで管理します。状態は`waiting -> preparing -> uploading -> completed/failed/cancelled`です。完了済み録画に対して既存のcompleted prepareがあれば再利用し、なければ`UploadPreparationService`でMP4準備を行ってから投稿します。同一録画のcompleted投稿は既定で重複作成せず、明示した再投稿だけを許可します。

OAuth資格情報はWindows資格情報ストアへ保存します。アプリ設定、準備キュー、manifest、投稿状態DB、標準出力、ログにはclient secret、refresh token、access tokenを保存しません。V1.4.0の一般導線では、配布者管理OAuth Clientのclient_idを使い、PKCEと`127.0.0.1:<random port>/callback`のloopback callbackで認証コードを自動受信します。認証画面はアプリ内WebViewではなく既定ブラウザで開きます。開発者向けfallbackとして`--client-secrets`と`--authorization-code`は残しますが、通常ユーザーにはGoogle Cloud Console操作を要求しません。

YouTube Data APIはresumable uploadを使い、通信断、5xx、rate/quota、401/403、不明応答を分類します。通信断と5xxは指数バックオフで再試行し、401は再認証、quota/rateはquota待ち、403は権限またはOAuth審査確認、不明応答はYouTube Studioでの手動確認を促します。完了時はvideo_idとwatch_urlを履歴表示へ渡します。

## CLI

```powershell
python -m master_duel_recorder_lite prepare enqueue RECORDING_ID --title "対戦記録" --description "説明" --tag "Master Duel" --privacy private
python -m master_duel_recorder_lite prepare list
python -m master_duel_recorder_lite prepare show QUEUE_ID
python -m master_duel_recorder_lite prepare run
python -m master_duel_recorder_lite prepare run QUEUE_ID
python -m master_duel_recorder_lite prepare cancel QUEUE_ID
python -m master_duel_recorder_lite youtube connect
python -m master_duel_recorder_lite youtube connect --client-secrets client_secret.json
python -m master_duel_recorder_lite youtube account
python -m master_duel_recorder_lite youtube materials RECORDING_ID
python -m master_duel_recorder_lite youtube clip RECORDING_ID --elapsed-ms 30000
python -m master_duel_recorder_lite youtube upload RECORDING_ID --title "対戦記録" --privacy private
python -m master_duel_recorder_lite youtube upload run
python -m master_duel_recorder_lite youtube upload list
python -m master_duel_recorder_lite youtube upload show UPLOAD_ID
```

`prepare run`は待機項目を個別に処理し、1件でも失敗・キャンセルなら終了コード4を返します。キュー・設定・ファイル操作自体の失敗は3です。
