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
- 録画履歴から動画を再生し、保存場所へ到達する録画の閲覧
- 勝敗、先後、デッキ、対戦種別、タグ、メモを後編集できる対戦記録管理
- 対戦開始、ターン切り替え、勝敗を録画時刻へ関連付ける対戦タイムライン

## 現在の状態

現在のバージョンは `0.16.10`、「対戦記録入力と履歴削除の改善」です。GitHub Releaseでは、通常利用向けGUI EXEと自動化・詳細操作向けCLI EXEを配布します。対戦記録の選択肢は日本語で表示し、デッキ名とタグは選択または自由入力できます。保存したデッキ名・タグはサイドメニューの一覧で再編集でき、対戦種別・両者のデッキ・タグは次の新規記録へ引き継ぎます。録画履歴の削除では、確認後に録画ファイル、復旧成果物、対戦記録、タイムラインをまとめて削除します。自動監視・FFmpeg起動・GUI応答維持に関するV0.16.8までの動作も継続します。外部サービスへの直接アップロードとOAuthは実装していません。V1.0.0への更新はユーザーの明示指示を待ちます。

開発計画は [docs/roadmap.md](docs/roadmap.md)、バージョンごとの変更は [docs/release-notes.md](docs/release-notes.md) を参照してください。ロードマップ作業は実装前にGitHub Issueへ登録し、バージョンラベルとMilestoneへ接続します。

## 重要方針

- OBS PluginとOBS WebSocketには依存しない
- Pythonを中心に保守しやすい責務へ分離する
- 録画データ、設定、認証情報、履歴、キュー、ログを `user_data/` に分離して保護する
- ゲーム画像、テンプレート画像、配布できないゲーム素材をリポジトリに含めない
- 復旧安全性を優先し、破損状態のまま自動処理を進めない
- 直接アップロードとOAuthはV1.0.0の中核範囲に含めず、まず安全なアップロード準備までを提供する
- GUIとCLIを同じアプリケーションサービスへ接続し、録画・履歴・復旧の規則を共通化する

## Windows EXEの導入

Windows 10/11 x64では、[GitHub Releases](https://github.com/Tao-pyth/master-duel-recorder-lite/releases/latest)からGUI版またはCLI版と、対応するSHA-256ファイルをダウンロードします。Pythonのインストールは不要です。

- `master-duel-recorder-lite-gui.exe`（通常利用向け）
- `master-duel-recorder-lite-gui.exe.sha256`
- `master-duel-recorder-lite.exe`
- `master-duel-recorder-lite.exe.sha256`

同じフォルダでPowerShellを開き、公開ハッシュとダウンロードしたEXEを照合します。

```powershell
$expected = (Get-Content .\master-duel-recorder-lite.exe.sha256).Split()[0]
$actual = (Get-FileHash .\master-duel-recorder-lite.exe -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256が一致しません" }
```

通常は`master-duel-recorder-lite-gui.exe`をダブルクリックし、録画対象を選択して「環境を診断」を実行します。診断が成功したら「録画開始」で手動録画、「自動監視」でMaster Duelウィンドウの検出に応じた録画を開始します。CLIを使う場合は次の順で実行します。

```powershell
.\master-duel-recorder-lite.exe --version
.\master-duel-recorder-lite.exe config init
.\master-duel-recorder-lite.exe doctor
```

EXEにはPythonランタイムを含みますが、FFmpegは含みません。GUIでFFmpegが見つからない場合は「FFmpegのセットアップ」が自動表示されます。配布元、GPLv3ライセンス、ダウンロードURL、インストール先を確認し、「インストール」を選ぶと、FFmpeg公式サイトが案内するGyan FFmpeg Buildsのrelease essentials ZIPを取得します。公開SHA-256を照合し、FFmpeg 6.0以上を確認できた場合だけ設定へ保存します。キャンセルした場合は何も導入しません。手動導入してPATHへ追加するか、設定画面または`config set recorder.ffmpeg_path`で既存のFFmpegを指定することもできます。

EXEはコード署名されていないため、Windows SmartScreenが警告する場合があります。GitHub Releaseの公開元、SHA-256、必要に応じて`gh attestation verify master-duel-recorder-lite.exe --repo Tao-pyth/master-duel-recorder-lite`でbuild provenanceを確認してください。確認できないEXEは実行しないでください。

EXE実行時の既定データ保存先は`%LOCALAPPDATA%\MasterDuelRecorderLite`、GUIから導入するFFmpegの既定先はその配下の`tools\ffmpeg`です。EXEを置いたフォルダには作業フォルダを作りません。V0.16.0以前にEXEと同じフォルダで作成された`user_data/`は自動移動も削除もしません。既存データを継続利用する場合は、バックアップ後に新しい既定先へ内容を移すか、`MDRL_USER_DATA_DIR`または`--user-data-dir`で旧フォルダを明示してください。

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
python -m master_duel_recorder_lite targets
python -m master_duel_recorder_lite record --duration 10
python -m master_duel_recorder_lite watch --once
python -m master_duel_recorder_lite watch
python -m master_duel_recorder_lite history list
python -m master_duel_recorder_lite history play RECORDING_ID
python -m master_duel_recorder_lite history reveal RECORDING_ID
python -m master_duel_recorder_lite history check
python -m master_duel_recorder_lite timeline list RECORDING_ID
python -m master_duel_recorder_lite timeline add RECORDING_ID --elapsed-ms 3000 --type marker --label "重要局面"
python -m master_duel_recorder_lite recovery list
python -m master_duel_recorder_lite recovery detect
python -m master_duel_recorder_lite prepare list
python -m master_duel_recorder_lite prepare run
python -m unittest discover -s tests
```

Python実行では既定でカレントプロジェクト直下の`user_data/`、EXE実行では`%LOCALAPPDATA%\MasterDuelRecorderLite`を使用します。検証用または移行中の保存先は環境変数`MDRL_USER_DATA_DIR`または`--user-data-dir`で変更できます。

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

FFmpeg 6.0以上、または同等のlibavutil 58以上を含むnightly buildが必要です。GUIでは初回セットアップを利用できます。CLI利用時または手動で管理したい場合はFFmpegを導入した後、次のコマンドで録画能力、入力設定、保存先、空き容量を確認します。

```powershell
python -m master_duel_recorder_lite doctor
python -m master_duel_recorder_lite list-inputs
```

`doctor` は警告だけなら終了コード `0`、録画を開始できない問題があれば終了コード `2` を返します。詳細は [録画環境の初期化設計](docs/architecture/recording-environment.md) を参照してください。

## 最小録画

録画前に `doctor` を実行し、必要に応じて `app.toml` の `audio_input` を `list-inputs` が表示した識別子へ設定します。音声を設定しない場合は画面だけを録画します。GUIの録画対象欄、またはCLIの`targets`で、Master Duel、任意の可視ウィンドウ、モニター、デスクトップ全体から対象を明示的に選択できます。

```powershell
python -m master_duel_recorder_lite record --duration 10
python -m master_duel_recorder_lite record
```

時間を省略した場合はCtrl+Cで正常停止します。録画は `user_data/data/recordings/YYYY/MM/DD/` 配下へ保存します。既存ファイルは上書きしません。Master Duel対象では、プロセス名とタイトル条件に一致する可視・非最小化ウィンドウのうち面積が最大のものをPID・Windowsハンドルで固定し、対応するウィンドウタイトルをFFmpegへ渡します。モニター対象ではOSが返す座標とサイズを使います。任意ウィンドウやデスクトップ全体も明示選択できます。録画前に対象名を確認してください。詳細は [録画対象の選択設計](docs/architecture/capture-targets.md) と [最小録画設計](docs/architecture/recording.md) を参照してください。

## Master Duel向け録画補助

状態だけを確認する場合は `watch --once`、継続監視と自動録画を行う場合は `watch` を実行します。

```powershell
python -m master_duel_recorder_lite watch --once
python -m master_duel_recorder_lite watch
```

既定では`masterduel.exe`の可視・非最小化ウィンドウを固定し、最大2fpsで高信頼度の対戦盤面を3フレーム合意すると録画します。盤面表示から開始までの目安は約1.5秒です。開始演出は補助信号として利用しますが必須ではないため、対戦途中から監視しても開始できます。メニュー、デッキ編集、設定画面、結果画面、大きな演出の表示だけでは開始しません。録画開始前の画像はメモリだけで扱い、開始候補を録画タイムラインの0秒へ保存します。録画後はウィンドウ不在、最小化、対象変更を5回連続で確認すると停止します。Ctrl+Cは監視と実行中の録画を正常停止します。`record`と`watch`はOSロックにより同時録画を拒否します。詳細は[Master Duel向け録画補助設計](docs/architecture/detection.md)と[基本イベント自動判定設計](docs/architecture/visual-event-detection.md)を参照してください。

## 録画履歴

録画結果は `user_data/data/db/history.sqlite3` に保存されます。動画パスは録画保存先からの相対パスとして保持し、録画IDごとに同じ履歴を開始状態から完了または失敗へ更新します。

```powershell
python -m master_duel_recorder_lite history list
python -m master_duel_recorder_lite history list --state failed --limit 20
python -m master_duel_recorder_lite history show RECORDING_ID
python -m master_duel_recorder_lite history play RECORDING_ID
python -m master_duel_recorder_lite history reveal RECORDING_ID
python -m master_duel_recorder_lite history check
```

GUIでは録画履歴を選択し、「再生」でWindows既定プレイヤー、「保存場所を開く」で対象を選択したExplorerを起動します。「削除」は確認後に元録画、復旧成果物、関連する対戦記録とタイムラインを一括削除し、録画履歴と復旧一覧の両方から消去します。録画または自動監視の実行中は削除できません。`history play`と`history reveal`も同じ安全なパス検証を利用します。`history check`は履歴から欠損したファイル、履歴にない録画ファイル、サイズ不一致を報告するだけで、ファイルを変更しません。詳細は[録画履歴設計](docs/architecture/history.md)と[録画の閲覧設計](docs/architecture/recording-browsing.md)を参照してください。

## 対戦記録

録画履歴の「対戦記録」から、状態、勝敗、先後、対戦種別、自分デッキ、相手デッキ、複数タグ、メモを後から何度でも編集できます。状態・勝敗・先後・対戦種別は日本語表示ですが、SQLiteとCLIでは互換性のある英語コードを保持します。自分デッキと相手デッキは共通のデッキ名一覧から選択でき、一覧にない日本語名も直接入力できます。タグも候補から追加または自由入力でき、保存した新規名は次回以降の候補になります。

サイドメニューの「デッキ名・タグ」では、両方の候補を一つの一覧で追加・名称変更・削除できます。候補の変更は過去の対戦記録を書き換えません。新規対戦記録を開くと、最後に保存した対戦種別、自分デッキ、相手デッキ、タグを初期値として表示し、既存記録の再編集ではその記録固有の値を優先します。詳細は[対戦記録管理設計](docs/architecture/duel-records.md)を参照してください。

## 対戦タイムライン

GUIでは録画履歴を選択して「タイムライン」を開き、状態・種別での絞り込み、手動マーカー追加、自動判定候補の確認・却下を行います。CLIでは録画開始からの経過ミリ秒を指定します。

```powershell
python -m master_duel_recorder_lite timeline list RECORDING_ID --json
python -m master_duel_recorder_lite timeline add RECORDING_ID --elapsed-ms 1000 --type duel_start
python -m master_duel_recorder_lite timeline add RECORDING_ID --elapsed-ms 3000 --type marker --label "重要局面"
python -m master_duel_recorder_lite timeline confirm EVENT_ID
python -m master_duel_recorder_lite timeline reject EVENT_ID
```

確定済みの開始と結果は各1件に制限し、ターン切り替えは開始より後、結果より前だけに置けます。イベントは物理削除せず、却下も監査可能な状態として残します。詳細は[対戦タイムライン基盤設計](docs/architecture/duel-timeline.md)を参照してください。

自動監視から開始したMaster Duel録画では、言語に依存しないROI特徴判定を録画処理と別スレッドで実行します。GUIの録画画面に処理・破棄フレーム数と候補件数を表示し、タイムライン画面に信頼度と判定理由を表示します。「録画開始」またはCLIの`record`による手動録画では判定ワーカーを起動せず、ユーザー操作だけで開始・停止します。実画面での確認は[基本イベント自動判定 実画面チェックリスト](docs/visual-detection-checklist.md)を使用します。

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
