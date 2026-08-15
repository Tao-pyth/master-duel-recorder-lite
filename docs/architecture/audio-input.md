# 音声入力

## 音声モード

V0.26.0は映像の録画対象と音声の取得対象を分離し、次の4モードを提供します。

| モード | 取得対象 | 入力方式 |
| --- | --- | --- |
| Master Duelのみ（推奨） | `masterduel.exe`と子プロセス | Windows Process Loopback、48kHz、stereo、s16le |
| PC全体 | 選択したシステム音声入力 | FFmpeg DirectShow |
| 入力デバイス | 選択したマイク等 | FFmpeg DirectShow |
| 音声なし | なし | `-an` |

Process LoopbackはWindows build 20348以上で利用できます。非対応環境、ゲーム未起動、ヘルパー障害では別の音源へ無断で切り替えず、具体的な警告を残して映像のみ録画します。PC全体または入力デバイスを使う場合は、GUIでDirectShow入力を明示選択します。マイクとゲーム音声の同時ミックスは対象外です。

## プロセス単体音声

ネイティブヘルパー`mdrl-audio-loopback.exe`は、Master Duelウィンドウから解決したPIDを`AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`へ渡し、対象プロセスツリーの音声だけをイベント駆動で取得します。PCMは録画ごとに生成するWindows名前付きパイプへ送り、FFmpegが映像とAAC音声を同じMKVまたはMP4へmuxします。`aresample=async=1:first_pts=0`で開始差と長時間ドリフトを補正します。

自動監視ではゲームPIDを確認した時点でヘルパーを事前起動し、FFmpegのパイプ接続を待機させます。候補録画は同じヘルパーを引き継ぎ、重複起動しません。録画終了後は次の待機用ヘルパーを作成し、ゲーム再起動やPID変更時は旧予約を停止して新PIDへ接続します。手動録画では開始直前にヘルパーを起動します。

## 障害と診断

ヘルパーは対象終了、初期化失敗、パイプ切断、取得失敗、正常停止を標準エラーのイベントと終了コードで区別します。開始失敗時は音声入力を持たないFFmpegコマンドへ切り替え、録画中のヘルパー終了ではFFmpeg映像を継続します。履歴には選択音源、音声警告、ヘルパー診断を保存します。無音はゲーム側の状態でも起こるため、録画自体の失敗にはしません。

## 配布とライセンス

ヘルパーはVisual C++静的ランタイムのx64 Releaseとしてビルドし、PyInstaller one-file EXEへ同梱します。一時展開はPyInstaller管理領域で行い、EXE配置先へ作業フォルダを作りません。実装はMicrosoft Application Loopback sampleを基にしており、配布物へ`THIRD_PARTY_NOTICES.md`を同梱します。

追跡: [V0.26.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/49)、Issue #340 - #353

実機検証には`scripts/validate_process_audio.py --pid PID --ffmpeg PATH --output PATH --duration 10`を使用し、製品と同じヘルパー、名前付きパイプ、48kHz stereo契約でWAVを生成してffprobe結果をJSON表示します。
