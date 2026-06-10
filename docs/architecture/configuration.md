# Configuration

## 目的

master-duel-recorder-lite は、設定が無い状態でも起動できるようにします。これは、初回起動時に設定ファイルがまだ存在しないことが自然だからです。

## 保存場所

非シークレット設定は次のファイルに保存します。

```text
user_data/config/app.toml
```

OAuthトークン、APIキー、クライアントシークレットなどは、このファイルに保存しません。秘密情報は将来 `user_data/config/secrets/` 配下に分離します。

## 現在の設定

```toml
[recorder]
ffmpeg_path = "ffmpeg"
recording_format = "mkv"

[upload]
privacy_status = "private"

[runtime]
auto_create_user_data = true
```

初心者向けに言うと、ここでは「録画に使うFFmpegの場所」「録画ファイル形式」「YouTubeアップロード時の公開範囲」「必要なフォルダを自動作成するか」を扱います。

## 実行時データの上書き

通常はリポジトリ直下の `user_data/` を使います。開発や検証で場所を変えたい場合は、環境変数 `MDRL_USER_DATA_DIR` またはCLIの `--user-data-dir` を使います。

```powershell
$env:MDRL_USER_DATA_DIR = "D:\\RecorderData"
python -m master_duel_recorder_lite --show-config
```

## 起動コマンド

```powershell
python -m master_duel_recorder_lite --init-user-data --write-default-config --show-config
```

このコマンドは必要なフォルダを作り、既定設定を書き込み、読み込まれた設定を表示します。既存の録画データやDBは削除しません。
