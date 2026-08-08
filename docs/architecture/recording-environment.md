# 録画環境の初期化

## 目的

録画開始前にFFmpeg、録画能力、画面・音声入力、保存先、空き容量を検査し、環境依存の問題を録画失敗として持ち越さないようにします。V0.2.0はWindowsの画面・音声録画環境を対象にします。

## FFmpeg対応条件

- FFmpeg 6.0以上を最低対応とする
- nightly buildのようにセマンティックバージョンを表示しない場合は、libavutil 58以上を同等条件とする
- 既定構成では入力方式 `gdigrab`、映像エンコーダー `libx264`、出力コンテナ `matroska` が必要になる
- 音声入力を設定した場合は `dshow` も必要になる
- MP4設定では出力コンテナ `mp4` が必要になる

FFmpegは次の優先順位で探します。

1. `app.toml` の絶対パスまたは相対パス
2. `PATH` 上の設定コマンド名
3. Windowsの限定された既知配置先

候補はファイルの存在、実行権限、`-version` の終了コードと出力を確認します。明示パスが不正な場合に別のFFmpegへ黙って切り替えることはしません。

## Windows入力

画面入力はFFmpeg `gdigrab` の `desktop` を既定値として提供します。音声入力はFFmpeg `dshow` のデバイス列挙結果を解析します。表示名と識別子は日本語を含めて保持します。

```powershell
python -m master_duel_recorder_lite list-inputs
```

音声入力が見つからない場合と、dshowの実行自体に失敗した場合は区別して診断します。音声は任意であり、`audio_input = ""` の場合は警告を表示したうえで録画環境を利用可能と判定します。

## doctor

```powershell
python -m master_duel_recorder_lite doctor
```

診断項目は次の6つです。

| コード | 検査内容 |
|---|---|
| `config` | app.tomlの読込または既定値利用 |
| `ffmpeg` | 実行可能なFFmpegの探索 |
| `capabilities` | バージョン、入力方式、エンコーダー、コンテナ |
| `inputs` | 設定した画面・音声入力の存在 |
| `storage` | 録画保存先の作成と一時書込 |
| `disk-space` | 録画保存先に1GiB以上の空き容量があること |

各項目は `[OK]`、`[WARN]`、`[ERROR]` のいずれかで表示します。警告だけなら終了コード `0`、1つでもエラーがあれば終了コード `2` です。先行検査が失敗した場合も、後続項目を省略せず「確認できません」と表示します。

保存先検査では `auto_create_user_data = true` の場合に不足ディレクトリを作成し、録画保存先に一時ファイルを書いて直ちに削除します。既存の録画、DB、キュー、ログは変更しません。診断出力へ認証情報などの秘密情報を含めません。

## 設定例

```toml
[recorder]
ffmpeg_path = "ffmpeg"
recording_format = "mkv"
screen_input = "desktop"
screen_input_format = "gdigrab"
audio_input = "マイク (Yamaha AG06MK2)"
audio_input_format = "dshow"
video_encoder = "libx264"
```

設定ファイルがリポジトリ以外にある場合は、グローバルオプションをサブコマンドより前に指定します。

```powershell
python -m master_duel_recorder_lite --user-data-dir D:\RecorderData doctor
```
