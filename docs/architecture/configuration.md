# Configuration

## 目的

master-duel-recorder-lite は、設定が無い状態でも起動できるようにします。これは、初回起動時に設定ファイルがまだ存在しないことが自然だからです。

## 保存場所

非シークレット設定は次のファイルに保存します。

```text
user_data/config/app.toml
```

OAuthトークン、APIキー、クライアントシークレットなどは、このファイルに保存しません。秘密情報は将来 `user_data/config/secrets/` 配下に分離します。

設定を変更するときは`config set`を使用します。候補値だけでなく設定全体を検証し、同じフォルダの一時ファイルを同期してから原子的に置換します。既存設定は`app.toml.previous`へ1世代保持します。不正値、未知キー、秘密情報名、保存失敗では現在の`app.toml`を変更しません。

## 現在の設定

```toml
[recorder]
ffmpeg_path = "ffmpeg"
recording_format = "mkv"
screen_input = "desktop"
screen_input_format = "gdigrab"
audio_input = ""
audio_input_format = "dshow"
video_encoder = "libx264"
frame_rate = 30
capture_width = 0
capture_height = 0
video_bitrate_kbps = 6000
audio_bitrate_kbps = 192

[detection]
game_process_name = "masterduel.exe"
game_window_title_contains = ""
auto_start_recording = true
auto_stop_recording = true
start_confirmations = 3
stop_confirmations = 5
minimum_confidence = 0.5
poll_interval_seconds = 1.0
cooldown_seconds = 10.0
visual_events_enabled = true
visual_maximum_fps = 2.0
visual_language = "auto"
visual_minimum_confidence = 0.70

[upload]
privacy_status = "private"

[runtime]
auto_create_user_data = true
```

初心者向けに言うと、ここでは「録画に使うFFmpegの場所」「録画ファイル形式」「画面と音声の入力元」「映像エンコーダー」「ゲーム監視と自動録画の条件」「アップロード準備時の公開範囲」「必要なフォルダを自動作成するか」を扱います。

`audio_input` が空文字の場合は音声録画を無効として扱います。利用可能な音声入力名は `python -m master_duel_recorder_lite list-inputs` で確認し、表示された識別子をそのまま設定します。現在は画面入力方式をWindowsの `gdigrab`、音声入力方式を `dshow` に限定します。未知の入力方式、空の画面入力、不正なエンコーダー名は設定読込時に拒否します。

`capture_width` と `capture_height` が両方 `0` の場合は入力元の解像度を維持します。解像度を変更する場合は両方を指定し、幅320-7680、高さ240-4320の偶数にします。`frame_rate` は1-120、`video_bitrate_kbps` は500-100000、`audio_bitrate_kbps` は32-512の範囲です。既定値は30fps、映像6000kbps、音声192kbpsです。

`game_process_name` は監視するWindows実行ファイル名、`game_window_title_contains` は任意のタイトル絞り込みです。`start_confirmations` と `stop_confirmations` は連続確認回数、`minimum_confidence` は採用する信頼度、`poll_interval_seconds` は監視間隔、`cooldown_seconds` は停止後に再開を抑止する時間です。自動開始または自動停止は個別に無効化できます。

`visual_events_enabled`はMaster Duel録画中の基本イベント判定、`visual_maximum_fps`は0より大きく2以下の解析頻度、`visual_language`は`auto`・`ja`・`en`、`visual_minimum_confidence`は0.70以上の候補保存閾値です。画像はメモリ内で処理し保存しません。任意ウィンドウ、モニター、デスクトップ録画では自動判定を無効化し、録画は継続します。

`upload.privacy_status` はアップロード準備メタデータの既定公開範囲です。安全のため `private` が既定で、明示した場合だけ `unlisted` を使用できます。`public`、OAuthトークン、APIキー、クライアントシークレットは設定・メタデータ・マニフェストへ保存しません。

V0.1.xで作成した設定には新しい項目がありませんが、読込時に上記の既定値を補うため手動移行は不要です。

## 実行時データの上書き

通常はリポジトリ直下の `user_data/` を使います。開発や検証で場所を変えたい場合は、環境変数 `MDRL_USER_DATA_DIR` またはCLIの `--user-data-dir` を使います。

```powershell
$env:MDRL_USER_DATA_DIR = "D:\\RecorderData"
python -m master_duel_recorder_lite --show-config
```

## 起動コマンド

```powershell
python -m master_duel_recorder_lite config init
python -m master_duel_recorder_lite config show
python -m master_duel_recorder_lite config get recorder.frame_rate
python -m master_duel_recorder_lite config set recorder.frame_rate 60
python -m master_duel_recorder_lite config reset --yes
python -m master_duel_recorder_lite doctor
python -m master_duel_recorder_lite record --duration 10
python -m master_duel_recorder_lite watch --once
python -m master_duel_recorder_lite watch
```

`config init`は必要なフォルダと既定設定を作りますが、既存`app.toml`を上書きしません。`config reset --yes`だけが既存設定を既定値へ戻し、その直前内容を`app.toml.previous`へ保持します。V0.7以前の`--init-user-data`、`--write-default-config`、`--show-config`は互換用に残しますが、新しい操作では`config`コマンドを使用します。いずれも録画データやDBを削除しません。
