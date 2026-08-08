# 中核機能ロードマップ

このロードマップは、中核機能1つを原則として1マイナーバージョンへ対応させます。各作業は実装前にGitHub Issueへ登録し、該当するバージョンラベルとMilestoneへ接続します。

## V0.1.0: 初期スキャフォールド

状態: 完了

- Pythonパッケージと最小CLIを作成する
- `user_data/` の標準ディレクトリ構成を定義する
- 非シークレット設定 `user_data/config/app.toml` を読み書きする
- 実行時データ分離の設計文書を追加する

## V0.1.1: 計画再検討

状態: 完了

- プロダクトコンセプトとV1.0.0までの中核機能を定義する
- 品質優先の変更許可と実行時データの保護境界を定義する
- バージョン、リリースノート、Issue先行の運用原則を定義する
- バージョン値を `0.1.1` へ更新し、一致テストを追加する

追跡: [V0.1.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/1)、Issue #1 - #8

完了条件: 文書、バージョン値、テスト、GitHub Issueの接続が一致し、既存の設定機能に回帰がないこと。

## V0.2.0: 録画環境の初期化

状態: 完了

- FFmpeg実行ファイルを探索する
- FFmpegのバージョン、入力方式、エンコーダー、コンテナ能力を検証する
- Windowsの画面・音声入力候補を列挙する
- 選択した録画入力を設定へ保存し検証する
- 録画前preflightと診断CLIを提供する

追跡: [V0.2.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/2)、Issue #9 - #14

完了条件: 対応Windows環境で録画可否を1コマンドで判定し、不足項目と対処方法を表示できること。

## V0.3.0: 最小録画

状態: 完了

- 録画プロファイルとFFmpeg引数列を構築する
- FFmpeg子プロセスを開始し、状態を管理する
- 正常停止、タイムアウト、異常終了を扱う
- 一意なファイル名で `user_data/data/recordings/` に保存する
- CLIから指定秒数または手動停止で録画する

追跡: [V0.3.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/3)、Issue #15 - #21

完了条件: 画面と音声を短時間録画し、正常停止後に再生可能なファイルと明確な実行結果を得られること。

## V0.4.0: Master Duel向け録画補助

状態: 完了

- 対戦開始・終了検出の契約を録画処理から分離する
- Master Duelプロセスと対象ウィンドウを監視する
- 低頻度の検出用フレーム取得を抽象化する
- 安定した開始・終了判定から録画を自動制御する
- 手動上書き、クールダウン、誤検出抑制を提供する

追跡: [V0.4.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/4)、Issue #22 - #28

完了条件: 一時的な画面変化で誤作動せず、対戦シナリオに応じて1回だけ開始・停止し、手動操作で安全に上書きできること。

## V0.5.0: 録画履歴管理

状態: 完了

- バージョン付きSQLiteスキーマと移行入口を定義する
- 録画開始から完了・失敗までの状態を保存する
- 履歴の一覧、詳細、検索、絞り込みCLIを提供する
- DBと録画ファイルの不整合を削除せず検出する
- 移行前バックアップと失敗時ロールバックを検証する

追跡: [V0.5.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/5)、Issue #29 - #34

完了条件: 録画結果と失敗理由を履歴から再確認でき、ファイル欠損やスキーマ移行失敗でも既存データを失わないこと。

## V0.6.0: 失敗時の復旧

状態: 完了

- 録画失敗の分類と復旧状態を定義する
- 録画状態を原子的に保存する
- 起動時に中断録画を検出する
- 未確定ファイルを検査し、元ファイルを保持して修復する
- 復旧CLIと障害注入テストを提供する

追跡: [V0.6.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/6)、Issue #35 - #40

完了条件: クラッシュ、容量不足、強制終了後に中断状態を検出し、元データを上書きせず検査・復旧できること。

## V0.7.0: アップロード準備

状態: 完了

- タイトル、説明、タグ、公開範囲のメタデータを管理する
- 録画ファイルの形式、長さ、ストリーム、破損を検証する
- 元録画を保持してremux・エクスポートする
- 再起動可能な準備キューを管理する
- バージョン付きJSONマニフェストを出力する

追跡: [V0.7.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/7)、Issue #41 - #46

完了条件: 元録画を変更せず、検証済み動画、privateを既定とするメタデータ、追跡可能なマニフェストを生成できること。

## V0.8.0: 設定・運用CLI

状態: 完了

- V1.0.0までの操作面を拡張CLIとして確定する
- 設定の表示、変更、初期化を安全に行う
- 録画環境、実行状態、履歴、復旧、準備キューを診断する
- 初期化からアップロード準備までの操作体系を統一する
- 日本語エラー、終了コード、help、JSON出力を整備する

追跡: [V0.8.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/8)、Issue #47 - #52

完了条件: クリーンなWindows環境で初期化からアップロード準備までのE2E手順が完走し、V1.0.0判断に必要な結果を確認できること。

## V0.9.0: Windows EXE配布

状態: 完了

- Pythonランタイムを内包したWindows x64向けone-fileコンソールEXEを生成する
- EXE配置フォルダを既定の`user_data/`基準にする
- バージョン情報、SHA-256、build provenanceを配布する
- タグとコード内バージョンが一致する場合だけGitHub Releaseを作成する
- Python不要の導入、更新、FFmpeg要件、SmartScreen制約を文書化する

追跡: [V0.9.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/9)のIssue #53 - #59、および[V0.9.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/10)のIssue #60 - #61

完了条件: `v0.9.1`のGitHub ReleaseからEXEとSHA-256を取得でき、ダウンロードしたEXEが`0.9.1`を表示し、ハッシュが一致すること。

## V1.0.0: 中核機能完成

V0.2.0からV0.9.0までの中核機能が完了しても、自動ではV1.0.0へ更新しません。全完了条件を確認したうえで、ユーザーが明示的に「V1.0.0に変更せよ」と依頼した場合のみ更新します。
