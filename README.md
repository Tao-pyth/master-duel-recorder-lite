# master-duel-recorder-lite

master-duel-recorder-liteは、OBSに依存せず、Yu-Gi-Oh! Master Duelの対戦録画、整理、復旧、共有準備を自動化する軽量なローカルツールです。録画や状態管理をPython側へ集約し、外部ツールとしてFFmpegを利用します。

## プロダクトコンセプト

対戦を記録したいユーザーが、録画ソフトの複雑な設定や録画開始・停止の操作に気を取られず、対戦後に必要な動画を見つけて安全に共有準備できることを目指します。録画ファイルと履歴をユーザーの管理下に置き、失敗時にも元データを失わないことを優先します。

## 中核機能

V1.0.0までに、次の中核機能を段階的に提供します。

- FFmpeg、録画入力、保存先を確認する録画環境の初期化
- 画面と音声を録画し、正常停止して再生可能なファイルを保存する最小録画
- Master Duelの実行状態と対戦状態に応じて開始・停止を補助する自動録画
- 録画結果、状態、ファイル、失敗理由をSQLiteで追跡する録画履歴
- 中断録画を検出し、元ファイルを保護しながら検査・修復する復旧
- 動画検証、remux、メタデータ、キュー、マニフェストを扱うアップロード準備
- 初期化からアップロード準備までを一貫して操作する設定・運用CLI

## 現在の状態

現在のバージョンは `0.9.0`、「Windows EXE配布」です。Pythonを別途導入せず、GitHub Releaseから取得した単一EXEで設定、診断、録画、履歴、復旧、アップロード準備を操作できます。現段階の自動検出は可視ウィンドウの存在判定です。外部サービスへの直接アップロード、OAuth、GUIは実装していません。V1.0.0への更新はユーザーの明示指示を待ちます。

開発計画は [docs/roadmap.md](docs/roadmap.md)、バージョンごとの変更は [docs/release-notes.md](docs/release-notes.md) を参照してください。ロードマップ作業は実装前にGitHub Issueへ登録し、バージョンラベルとMilestoneへ接続します。

## 重要方針

- OBS PluginとOBS WebSocketには依存しない
- Pythonを中心に保守しやすい責務へ分離する
- 録画データ、設定、認証情報、履歴、キュー、ログを `user_data/` に分離して保護する
- ゲーム画像、テンプレート画像、配布できないゲーム素材をリポジトリに含めない
- 復旧安全性を優先し、破損状態のまま自動処理を進めない
- 直接アップロードとOAuthはV1.0.0の中核範囲に含めず、まず安全なアップロード準備までを提供する
- V1.0.0の操作面は拡張CLIとし、GUIはV1.0.0以降の候補とする

## Windows EXEの導入

Windows 10/11 x64では、[GitHub Releases](https://github.com/Tao-pyth/master-duel-recorder-lite/releases/latest)から次の2ファイルをダウンロードします。Pythonのインストールは不要です。

- `master-duel-recorder-lite.exe`
- `master-duel-recorder-lite.exe.sha256`

同じフォルダでPowerShellを開き、公開ハッシュとダウンロードしたEXEを照合します。

```powershell
$expected = (Get-Content .\master-duel-recorder-lite.exe.sha256).Split()[0]
$actual = (Get-FileHash .\master-duel-recorder-lite.exe -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256が一致しません" }
```

一致を確認したら初期化と環境診断を実行します。

```powershell
.\master-duel-recorder-lite.exe --version
.\master-duel-recorder-lite.exe config init
.\master-duel-recorder-lite.exe doctor
```

EXEにはPythonランタイムを含みますが、FFmpegは含みません。録画にはFFmpeg 6.0以上を別途導入してPATHへ追加するか、`config set recorder.ffmpeg_path`で指定してください。

EXEはコード署名されていないため、Windows SmartScreenが警告する場合があります。GitHub Releaseの公開元、SHA-256、必要に応じて`gh attestation verify master-duel-recorder-lite.exe --repo Tao-pyth/master-duel-recorder-lite`でbuild provenanceを確認してください。確認できないEXEは実行しないでください。

EXE実行時の既定データ保存先は、EXEと同じフォルダの`user_data/`です。更新時はアプリを終了し、`user_data/`を残したままEXEだけを置き換えます。別フォルダへ移す場合はEXEと`user_data/`を一緒に移してください。

詳細は[Windows EXE配布設計](docs/architecture/windows-distribution.md)を参照してください。

## 開発者向けセットアップ

Python 3.11以上が必要です。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m master_duel_recorder_lite
python -m master_duel_recorder_lite --version
python -m master_duel_recorder_lite config init
python -m master_duel_recorder_lite config show
python -m master_duel_recorder_lite status
python -m master_duel_recorder_lite status --json
python -m master_duel_recorder_lite doctor
python -m master_duel_recorder_lite list-inputs
python -m master_duel_recorder_lite record --duration 10
python -m master_duel_recorder_lite watch --once
python -m master_duel_recorder_lite watch
python -m master_duel_recorder_lite history list
python -m master_duel_recorder_lite history check
python -m master_duel_recorder_lite recovery list
python -m master_duel_recorder_lite recovery detect
python -m master_duel_recorder_lite prepare list
python -m master_duel_recorder_lite prepare run
python -m unittest discover -s tests
```

Python実行では既定でカレントプロジェクト直下、EXE実行ではEXE配置フォルダ直下の `user_data/` を使用します。検証用の保存先は環境変数 `MDRL_USER_DATA_DIR` または `--user-data-dir` で変更できます。

## 設定・運用CLI

設定は検証後に原子的に更新し、直前の`app.toml`を`app.toml.previous`へ保持します。`config init`は既存設定を上書きせず、`config reset`は`--yes`を必須とします。表示・変更対象は非シークレット設定だけです。

```powershell
python -m master_duel_recorder_lite config init
python -m master_duel_recorder_lite config show
python -m master_duel_recorder_lite config show --json
python -m master_duel_recorder_lite config get recorder.frame_rate
python -m master_duel_recorder_lite config set recorder.frame_rate 60
python -m master_duel_recorder_lite config reset --yes
python -m master_duel_recorder_lite status
python -m master_duel_recorder_lite status --json
```

`status`は録画環境、実行状態、履歴整合性、復旧待ち、準備キューを一度に診断します。JSONはスキーマ版を持ち、秘密情報と実行時データの絶対パスを含めません。終了コードは`0`が成功、`2`が設定・引数・環境不備、`3`が処理失敗、`4`が要確認状態、`130`が正常完了として扱えない処理のCtrl+C中断です。詳細は[設定・運用CLI設計](docs/architecture/cli.md)と[V1候補E2Eチェックリスト](docs/e2e-checklist.md)を参照してください。

## 録画環境の確認

FFmpeg 6.0以上、または同等のlibavutil 58以上を含むnightly buildが必要です。FFmpegを導入した後、次のコマンドで録画能力、入力設定、保存先、空き容量を確認します。

```powershell
python -m master_duel_recorder_lite doctor
python -m master_duel_recorder_lite list-inputs
```

`doctor` は警告だけなら終了コード `0`、録画を開始できない問題があれば終了コード `2` を返します。詳細は [録画環境の初期化設計](docs/architecture/recording-environment.md) を参照してください。

## 最小録画

録画前に `doctor` を実行し、必要に応じて `app.toml` の `audio_input` を `list-inputs` が表示した識別子へ設定します。音声を設定しない場合は画面だけを録画します。

```powershell
python -m master_duel_recorder_lite record --duration 10
python -m master_duel_recorder_lite record
```

時間を省略した場合はCtrl+Cで正常停止します。録画は `user_data/data/recordings/YYYY/MM/DD/` 配下へ保存します。既存ファイルは上書きしません。現在の画面入力はデスクトップ全体です。通知、個人情報、秘密情報が映り込む可能性があるため、録画前に表示内容を確認してください。詳細は [最小録画設計](docs/architecture/recording.md) を参照してください。

## Master Duel向け録画補助

状態だけを確認する場合は `watch --once`、継続監視と自動録画を行う場合は `watch` を実行します。

```powershell
python -m master_duel_recorder_lite watch --once
python -m master_duel_recorder_lite watch
```

既定では `masterduel.exe` の可視ウィンドウを3回連続で確認すると開始し、ウィンドウ不在または最小化を5回連続で確認すると停止します。Ctrl+Cは監視と実行中の録画を正常停止します。`record` と `watch` はOSロックにより同時録画を拒否します。現在の検出はゲームウィンドウの存在判定であり、対戦中・メニュー・デッキ編集を区別しません。詳細は [Master Duel向け録画補助設計](docs/architecture/detection.md) を参照してください。

## 録画履歴

録画結果は `user_data/data/db/history.sqlite3` に保存されます。動画パスは録画保存先からの相対パスとして保持し、録画IDごとに同じ履歴を開始状態から完了または失敗へ更新します。

```powershell
python -m master_duel_recorder_lite history list
python -m master_duel_recorder_lite history list --state failed --limit 20
python -m master_duel_recorder_lite history show RECORDING_ID
python -m master_duel_recorder_lite history check
```

`history check` は履歴から欠損したファイル、履歴にない録画ファイル、サイズ不一致を報告するだけで、ファイルを変更しません。詳細は [録画履歴設計](docs/architecture/history.md) を参照してください。

## 失敗時の復旧

`record`、`watch`、`history`、`recovery`の起動時に、録画ロック、保存済みPID、履歴を照合して中断録画を検出します。他プロセスが録画中なら変更しません。

```powershell
python -m master_duel_recorder_lite recovery list
python -m master_duel_recorder_lite recovery detect
python -m master_duel_recorder_lite recovery inspect RECORDING_ID
python -m master_duel_recorder_lite recovery repair RECORDING_ID --dry-run
python -m master_duel_recorder_lite recovery repair RECORDING_ID
python -m master_duel_recorder_lite recovery ignore RECORDING_ID
```

検査は元ファイルを読み取るだけです。修復は同じフォルダへUUID付きの別ファイルを作り、ffprobe検証に成功した場合だけ`repaired`として記録します。元ファイルと失敗した部分成果物は自動削除しません。詳細は [失敗時の復旧設計](docs/architecture/recovery.md) を参照してください。

## アップロード準備

正常完了した録画を、タイトル、説明、タグ、公開範囲とともに準備キューへ追加します。公開範囲を省略すると`private`です。

```powershell
python -m master_duel_recorder_lite prepare enqueue RECORDING_ID --title "対戦記録" --tag "Master Duel"
python -m master_duel_recorder_lite prepare list
python -m master_duel_recorder_lite prepare show QUEUE_ID
python -m master_duel_recorder_lite prepare run
python -m master_duel_recorder_lite prepare cancel QUEUE_ID
```

`prepare run`は元録画をffprobeで検証し、`user_data/data/exports/`へ一時MP4を作成します。再検証に成功した場合だけ原子的に確定し、動画と元録画のSHA-256、相対パス、メタデータを持つJSONマニフェストを出力します。音声なしは警告として準備できますが、空・破損・映像なしは失敗です。詳細は [アップロード準備設計](docs/architecture/upload-preparation.md) を参照してください。

## ライセンス

MIT License

## 免責

このプロジェクトは非公式のファンメイドツールです。KONAMIおよびYu-Gi-Oh! Master Duelの公式プロジェクトではありません。Yu-Gi-Oh! Master Duelのゲーム素材は配布しません。
