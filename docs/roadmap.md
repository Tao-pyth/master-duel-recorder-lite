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

## V0.10.0: 録画対象の明示化

状態: 完了

- Windowsのモニターと可視ウィンドウを列挙する
- Master Duel、任意ウィンドウ、モニター、デスクトップを共通モデルで扱う
- 選択対象をFFmpeg gdigrab入力へ安全に変換する
- CLIと設定から録画対象を確認・保存できるようにする
- 自動検出したMaster Duelウィンドウを実際の録画入力へ接続する

追跡: [V0.10.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/11)、Issue #62 - #67

## V0.11.0: Windows GUI

状態: 完了

- CLIと共通のアプリケーションサービスを設ける
- 録画対象、診断、手動録画、自動監視を非同期GUIから操作する
- 履歴、復旧、アップロード準備、主要設定をGUIへ統合する
- 実行状態、エラー、終了処理を画面上で追跡できるようにする

追跡: [V0.11.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/12)、Issue #68 - #73

## V0.12.0: GUI配布品質

状態: 完了

- コンソールを表示しないone-file GUI EXEを追加する
- CLI版とGUI版を同じGitHub Releaseで配布する
- GUIの起動、主要部品、終了、バージョンを実EXEでスモーク検証する
- 両EXEへ個別のSHA-256とbuild provenanceを提供する

追跡: [V0.12.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/13)、Issue #74 - #77

完了条件: GitHub Releaseから両EXEを取得でき、CLI操作とGUI起動をPython不要で確認できること。

## V0.12.1: 対戦記録ロードマップ策定

状態: 完了

- V0.13.0からV0.16.0の機能境界と詳細仕様を定義する
- 後編集可能な対戦記録、タイムライン、自動判定のデータ保護規則を定義する
- 各中核機能を1ステップ単位のIssueへ分解する

追跡: [V0.12.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/18)、Issue #115 - #116

完了条件: 設計文書、ロードマップ、バージョン、Issue・Milestoneの接続が一致し、既存実行機能に回帰がないこと。

## V0.13.0: 録画の閲覧

状態: 完了

- 録画履歴からWindows既定プレイヤーで動画を再生する
- Explorerで録画ファイルを選択して保存場所を開く
- 録画保存領域外、欠損、空、未対応ファイルを拒否する
- GUIとCLIで同じ閲覧規則とエラー契約を利用する

追跡: [V0.13.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/14)、Issue #78 - #85

完了条件: GUIとCLIから有効な録画を開け、不正な参照では別ファイルを開かず、両EXEで入口を検証できること。

詳細: [録画の閲覧設計](architecture/recording-browsing.md)

## V0.13.1: 自動監視の安定化

状態: 完了

- 同一PID・HWNDの可視状態だけを連続確認として数える
- 録画中に別ウィンドウへ無条件で切り替えない
- FFmpeg終了コードをDWORD、符号付き32bit、16進数で診断する
- stderr末尾と録画開始時のPID・HWND・画面サイズを履歴へ保存する
- 出力サイズ停止を検出し、連続失敗を指数バックオフ後に遮断する

追跡: [V0.13.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/19)、Issue #117

完了条件: ウィンドウの一時変化で対象を誤切替せず、FFmpeg異常終了の原因情報を履歴から確認でき、連続再起動ループを防止できること。

詳細: [Master Duel向け録画補助](architecture/detection.md)

## V0.14.0: 対戦記録管理

状態: 完了

- 録画IDと対戦記録をSQLiteで1対1に関連付ける
- 勝敗、先後、自分・相手デッキ、対戦種別、タグ、メモを管理する
- draftとconfirmedの双方を後から何度でも再編集できる
- 変更履歴を保存し、自動判定が利用者編集を上書きしない
- 手動録画後は入力画面、自動録画後は非モーダルの未入力通知を提供する

追跡: [V0.14.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/15)、Issue #86 - #95

完了条件: 録画後または履歴から対戦記録を作成・再編集でき、競合・移行失敗時に既存データを維持できること。

詳細: [対戦記録管理設計](architecture/duel-records.md)

## V0.15.0: 対戦タイムライン基盤

状態: 完了

- 録画開始からの経過ミリ秒で対戦イベントを保存する
- 対戦開始、ターン切り替え、対戦結果、手動マーカーを共通モデルで扱う
- 候補、確定、却下を物理削除なしで管理する
- 確定イベントの一意性と時系列整合性を検証する
- GUIとCLIからタイムラインを確認・操作する

追跡: [V0.15.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/16)、Issue #96 - #103

完了条件: 手動イベントを録画IDへ安全に保存し、矛盾を確定せず、再起動後も安定順序で取得できること。

詳細: [対戦タイムライン基盤設計](architecture/duel-timeline.md)

## V0.16.0: 基本イベント自動判定

状態: 完了

- Master Duel録画中に低頻度フレームを録画処理と独立して解析する
- 対戦開始、ターン切り替え、勝敗・対戦終了だけを判定する
- 複数フレーム合意、クールダウン、状態遷移で重複と誤判定を抑える
- 信頼度0.70以上を候補として保存し、自動では確定しない
- 解析遅延・例外・無効化時も録画を継続する

追跡: [V0.16.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/17)、Issue #104 - #114

完了条件: 開始と結果を各最大1件、ターン切り替えを重複なく候補保存し、判定器の障害が録画結果へ影響しないこと。

詳細: [基本イベント自動判定設計](architecture/visual-event-detection.md)

## V0.16.1: 初回FFmpegセットアップと保存先修正

状態: 完了

- FFmpegが利用できないGUI初回起動時に、利用者の許可を求めるセットアップ画面を表示する
- 配布元、ライセンス、URL、導入先を表示し、公開SHA-256と実行可能性を検証してから設定へ反映する
- EXEの実行時データと管理対象FFmpegを`%LOCALAPPDATA%\MasterDuelRecorderLite`配下へ置く
- EXE隣接フォルダに作業データを作らないことを実配布スモークで検証する
- 旧EXE隣接`user_data/`を自動変更せず、明示指定による継続利用方法を文書化する

追跡: [V0.16.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/20)、Issue #118 - #120

完了条件: 初回利用者がGUIの確認画面からFFmpegを安全に導入でき、EXE配置フォルダに実行時データが作成されず、失敗時に既存データと設定が維持されること。

詳細: [FFmpeg初回セットアップ設計](architecture/ffmpeg-setup.md)

## V0.16.2: 対戦開始基準の自動録画

状態: 完了

- 自動監視中にMaster Duelウィンドウの開始演出から盤面への遷移を低頻度フレームで判定する
- ウィンドウ表示だけでは録画せず、同一PID・HWNDの複数フレーム合意後に一度だけ開始する
- 対象変更、最小化、取得失敗で開始合意を破棄し、画像を保存しない
- 自動監視の録画対象をMaster Duelへ固定し、従来の停止・復旧・バックオフを維持する
- 録画前の開始候補を録画タイムラインの0ミリ秒へ関連付ける

追跡: [V0.16.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/21)、Issue #121 - #123

完了条件: メニューやデッキ編集では録画せず、対戦開始合意後だけ録画し、判定失敗と対象変更が誤録画や既存データ変更を起こさないこと。

詳細: [Master Duel向け録画補助設計](architecture/detection.md)

## V0.16.3: FFmpeg 9能力判定修正

状態: 完了

- FFmpeg 9のdemuxer一覧に含まれるdeviceフラグ列を解析する
- 自動導入したFFmpeg 9.0の`gdigrab`対応を正しく判定する
- FFmpeg 6から8の従来形式との後方互換性を維持する

追跡: [V0.16.3 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/22)、Issue #124

完了条件: 実際に自動導入したFFmpeg 9.0を使った`doctor`で録画能力がOKとなり、従来形式と新形式の回帰テストが成功すること。

## V0.16.4: Windows FFmpeg起動安定化

状態: 完了

- WindowsのFFmpeg子プロセスを非表示で起動し、OSエラーダイアログを抑止する
- 一時的なDLL初期化失敗`0xc0000142`だけを限定再試行する
- 開始前診断の失敗項目と理由をGUIアクティビティへ表示する
- 自動導入済みFFmpeg 9.0で連続診断と実フレーム取得を検証する

追跡: [V0.16.4 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/23)、Issue #125 - #126

完了条件: 診断とフレーム取得が一時的なDLL初期化失敗から復旧でき、恒久エラーでは具体的な診断理由を確認できること。

## V0.16.5: 進行中盤面からの自動録画開始

状態: 完了

- 開始演出を必須条件から補助条件へ変更する
- 開始演出を見逃した場合と対戦途中から監視した場合も安定盤面から開始する
- 大きな演出、結果画面、低盤面スコアを盤面単独判定から除外する
- 提供画像と実ゲーム画面を3フレーム合意へ通して検証する

追跡: [V0.16.5 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/24)、Issue #127 - #128

完了条件: 開始演出を検出できなくても高信頼度盤面の3フレーム目で開始候補を確定し、非盤面1フレームでは開始しないこと。

## V0.16.6: 対戦開始待機表示の集約

状態: 完了

- 対戦開始判定の待機時間を秒単位で表示する
- 同じ待機メッセージをアクティビティの1行へ集約する
- 判定成功、状態変更、監視停止時に待機行を除去する
- 待機以外の操作履歴を維持する

追跡: [V0.16.6 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/25)、Issue #129

完了条件: 自動監視中の待機表示が`対戦開始を判定中です (4s)`形式で1行だけ更新され、再監視時に経過秒がリセットされること。

## V0.16.7: 録画用FFmpegのコンソール抑止

状態: 完了

- 長時間録画用FFmpegをWindowsで非表示起動する
- 重大エラーダイアログ抑止を録画経路にも適用する
- GUIとCLIの手動録画を画面イベント判定から分離する
- 手動録画から同一ウィンドウに対する判定用FFmpegの追加取得を排除する
- 手動録画開始時にMaster Duelウィンドウを再解決する
- 標準入力による正常停止と標準エラー収集を維持する
- Windows以外の起動動作を維持する

追跡: [V0.16.7 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/26)、Issue #130 - #131

完了条件: Windows GUIから手動録画を開始してもFFmpegのコンソールや判定用FFmpegが追加表示・起動されず、録画開始・正常停止・診断収集が機能すること。

## V0.16.8: 録画開始中のGUI応答維持

状態: 完了

- 録画開始の排他予約と時間のかかる準備処理を分離する
- バックグラウンド操作中のGUI同期ポーリングを停止する
- 二重開始、自動監視、停止、終了との競合を拒否する
- 開始失敗後の再試行を保証する

追跡: [V0.16.8 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/27)、Issue #132

完了条件: 録画開始処理が5秒以上かかってもGUIのTkメインスレッドがサービスロックを待たず、開始成功・失敗・再試行を安全に処理できること。

## V0.16.9: 対戦記録入力と履歴削除の改善

状態: 完了

- 状態、勝敗、先後、対戦種別を日本語表示する
- デッキ名・タグ辞書をSQLiteへ保存し、サイドメニューの一画面で編集する
- 自分デッキ、相手デッキ、タグを候補選択と日本語自由入力の両方に対応する
- 対戦種別、自分デッキ、相手デッキ、タグを次の新規記録へ引き継ぐ
- 録画履歴、録画ファイル、復旧成果物、対戦記録、タイムラインを一括削除する

追跡: [V0.16.9 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/28)、Issue #133 - #138

完了条件: 日本語で対戦記録を再利用可能な候補から入力でき、前回値が新規記録へ引き継がれ、履歴削除後に元録画と復旧対象を含む関連データが残らないこと。

## V0.16.10: Windows短縮パス環境のCI修正

状態: 完了

- Windows runnerの8.3短縮パスと正規化済みパスの表記差を吸収する
- 履歴削除結果が正規化済みパスを返す製品契約を維持する
- V0.16.9の全機能を変更せず再検証する

追跡: [V0.16.10 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/29)、Issue #139

完了条件: ローカルとGitHub ActionsのWindows環境で全テストが成功し、V0.16.10のWindows EXEスモークが成功すること。

## V0.17.0: 録画・対戦情報の統合管理

状態: 完了

- デッキ名とタグを独立画面で管理し、説明、タグカラー、安定ID、参照を保つアーカイブを追加する
- GUIでWindows音声入力を列挙・選択・テストし、AAC録音、同期補正、履歴状態へ接続する
- 日本語UIの16:9・ウルトラワイド実画面特徴からコイントス、盤面、先後、ターン、勝敗、エラー、リプレイを状態機械で判定する
- 候補録画を盤面確認で確定し、マッチエラー・リプレイ・確認タイムアウトでは破棄して監視へ戻す
- 提供動画14本を端末内だけで評価する台帳、ストリーミング評価器、数値レポートを追加する
- 録画履歴を勝敗・先後・対戦種別中心へ変更し、再生・編集・Explorer・削除を行内アイコンで提供する
- 更新、保存、追加、削除等の共通操作をアイコン中心にし、ツールチップとキーボード操作を併設する
- SQLiteを版6へ移行し、移行前バックアップと失敗時ロールバックを維持する

追跡: [V0.17.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/30)、Issue #140 - #180

完了条件: 4トラックの自動テスト、ローカル実動画評価、GUI/EXEスモーク、SQLite移行を検証し、既存録画・設定を失わずV0.17.0として配布できること。

## V0.17.1: Windows短縮パス環境の台帳テスト修正

状態: 完了

- GitHub ActionsのWindows runnerで一時フォルダの8.3短縮パスと正規化済みパスを同一動画として比較する
- V0.17.0の中核機能を変更せず、全テストとWindows Releaseを再検証する

追跡: [V0.17.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/31)、Issue #181

完了条件: Windows runnerで全テスト、CLI/GUI EXEビルド、スモーク、GitHub Release公開が成功すること。

## V0.17.2: 自動監視・ライブ判定修正

状態: 実装完了・実戦不合格・未公開

- Master Duelクライアント領域の物理座標とDPIを解決する
- 単一常駐FFmpegで最大2fpsの判定フレームを継続取得し、停止・移動・リサイズから復旧する
- オフラインとライブを`FrameAnalysis`へ統一し、画像を保存しない数値診断を追加する
- コイントス2/4、盤面3/5、結果2/4等のイベント別時間窓合意を適用する
- 候補録画、盤面確定、候補破棄、結果停止、次対戦待機を同一ストリームへ接続する
- GUIで手動録画対象と自動監視対象を分け、取得・判定・合意状態を表示する
- 提供動画14本を2fpsで再評価し、ローカルEXEで3戦、調整後10戦の実戦検証を行う

追跡: [V0.17.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/32)、Issue #182 - #191

検証結果: 提供動画14本の2fps評価は合格したが、実戦3戦の保存動画3/3がコイントス画面で静止したためリリース不可とした。修正と再検証はV0.17.3へ引き継ぐ。

## V0.17.3: フルスクリーン録画静止修正

状態: V0.18.0へ統合・単独未公開

- Master Duelの実録画を`title=`入力から物理座標の`gdigrab desktop`へ切り替える
- 自動判定の観測座標を実録画コマンドへ引き継ぐ
- 判定入力を1280pxから640pxへ軽量化し、最大2fpsの処理余力を確保する
- 自動監視の録画待機、候補録画、本録画をGUIの色付き表示帯で明確に区別する
- GUI共通ヘッダーへ戦績管理未完了件数と録画履歴への導線を表示する
- 修正後3戦と調整後10戦で、検出と保存動画を再検証する

追跡: [V0.17.3 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/33)、Issue #192 - #198

完了条件: 修正後3戦と調整後10戦で自動開始・盤面確定・結果停止・監視復帰が成功し、静止動画0件、Master Duel領域外の誤録画0件を確認する。座標録画では重なった別アプリも映る制約を公開する。完了前はIssueクローズ、`main` push、タグ、Releaseを行わない。

## V0.17.4: 対戦終了境界・GUI識別性修正

状態: V0.18.0へ統合・単独未公開

- 実ログから結果見逃しと次戦まで録画が継続する状態遷移を再現する
- 勝敗種別付きの高信頼度結果だけを単一フレームで確定する
- 盤面確定後の次戦コイントス合意を前戦の録画境界として停止する
- 録画履歴操作を識別可能な標準アイコン、ツールチップ、選択・無効状態へ変更する
- 復旧ページで修復可能・修復不可・理由を日本語表示し、元録画を保護する
- タグ一覧のカラー列へ保存済みの実色スウォッチを表示する

追跡: [V0.17.4 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/34)、Issue #199 - #206

完了条件: 実ログ再現、全単体テスト、Ruff、CLI/GUI EXEビルド、両スモーク、ローカル実戦での終了・監視復帰を確認する。実戦完了前は親Issue #199のクローズ、`main` push、タグ、Releaseを行わない。

## V0.18.0: 戦績統計・Material 3 GUI刷新

状態: 完了

- 確定済み・勝敗入力済みの正常完了録画から全体勝率を算出する
- 期間、デッキ、タグ安定ID、先後を単独または複合条件として適用する
- 日・週・月単位の勝利数と勝率推移を空期間も含めて表示する
- デッキ別・先後別に対戦数、勝敗、引分、勝率を比較する
- GUI全体へMaterial 3のカラーロール、情報階層、選択・ホバー・無効状態を適用する
- 1180x760と最小980x640で統計ページと主要ページの描画を確認する

追跡: [V0.18.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/35)、Issue #207 - #216

完了条件: 集計境界・複合条件・時系列の自動テスト、既存SQLiteの実データ集計、全単体テスト、Ruff、GUI描画、CLI/GUI EXEビルドと両スモークが成功すること。完了前は親Issue #207のクローズ、`main` push、タグ、Releaseを行わない。

## V0.18.1: 統計操作・先後内訳改善

状態: V0.19.0へ統合

- 開始日・終了日をカレンダーから選択できるようにする
- 条件適用後の先攻時・後攻時勝率を上部に表示する
- 「デッキ別」を「デッキ別全体」へ改める
- デッキごとの先攻時・後攻時・未設定を縦に比較する「デッキ先後別」を提供する

追跡: [V0.18.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/36)、Issue #217 - #224

## V0.19.0: シーズン管理

状態: 完了

- ランク、イベント、カスタムのシーズンCRUD、期間、説明、レポートメモ、アーカイブを提供する
- 対戦記録へ1シーズンを手動で割り当て、期間外保存は警告する
- シーズン別の勝率、先後、デッキ、日・週・月推移をライブ集計する
- 録画履歴をシーズン、自分デッキ、相手デッキ、複数タグでSQL絞り込みする
- デッキへカラー、相手専用、履歴・統計候補非表示フラグを追加する
- 旧復旧GUI、CLI、サービス、DB情報を安全なv7移行で撤去し、シーズン等をv8で追加する
- 左下の利用状態を高コントラストのアイコンと状態名で表示する

追跡: [V0.19.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/37)、Issue #225 - #238

完了条件: v6実データ相当からv8への移行・復元、シーズンCRUD・集計・絞り込み、デッキフラグ規則、全単体テスト、Ruff、CLI/GUI EXEビルド、両スモーク、Windows CIが成功し、`v0.19.0` Releaseを公開すること。

## V0.19.1: GUI一貫性・データ管理改善

状態: 完了

- 入力欄、プルダウン、文字ボタン、アイコンボタンの高さとMaterial配色を共通化する
- 録画履歴のフィルター操作をアイコン化し、音声列を廃止して自分デッキ名と実色ラインを表示する
- 統計条件行、デッキ・タグ管理、録画・設定画面のコントロール寸法を統一する
- シーズンを日本語種別と期間で一覧上部から直接登録し、対戦種別の重複入力を廃止する
- シーズンごとのライブ集計とレポートメモを独立したレポート画面へまとめる
- 履歴・デッキ・タグ・シーズンを1つの検証可能なJSONとして入出力する
- 履歴・デッキ・タグ・シーズンの個別初期化に、二段階確認、操作前SQLiteバックアップ、失敗時復元を適用する
- 履歴情報の初期化では動画ファイルを削除しない

追跡: [V0.19.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/38)、Issue #239 - #248

完了条件: GUI描画、JSON往復、対象別初期化、動画保護、失敗時非変更、全単体テスト、Ruff、CLI/GUI EXEビルド、両スモーク、Windows CIが成功し、`v0.19.1` Releaseを公開すること。

## V0.19.2: 統計カレンダーピッカー表示修正

状態: 完了

- 統計ページの開始日・終了日を、直接入力とカレンダー選択を併用できる専用コントロールへ変更する
- カレンダーアイコンを入力欄右端へ固定し、フィルター列が狭くなっても隠れないようにする
- 条件適用・クリアの固定幅を確保し、980x640以上のウィンドウで全操作を表示する
- GUIスモーク契約へ開始日・終了日のカレンダーピッカーを追加する

追跡: [V0.19.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/39)、Issue #249

完了条件: 1180x760と980x640で両ピッカーが表示され、カレンダーを開いてISO形式の日付を選択でき、全単体テスト、Ruff、CLI/GUI EXEビルド、両スモーク、Windows CIが成功し、`v0.19.2` Releaseを公開すること。

## V0.20.0: コイントス記録・統計

状態: 完了。V0.25.0で重複項目を整理

- コインの面を表・裏・未設定で管理する
- コインの表をコイントス勝利、裏を敗北として管理する
- コインの面、先後、最終勝敗を独立して保存し、相互推測しない
- 録画終了後と録画履歴からの後日編集に対応する
- 録画履歴へコイン情報を表示し、複合フィルターへ追加する
- 統計へコイン条件と表裏別の内訳を追加する
- DB v9で追加後、V0.25.0のDB v13で意味が重複する`coin_toss_outcome`列と索引を撤去する
- 旧管理データの重複項目は無視し、コイン表裏を保持する
- 自動画面判定は本バージョンへ含めず、手動修正可能なデータ基盤を先に確立する

追跡: [V0.20.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/40)、Issue #250 - #258

完了条件: v8からv9への安全な移行とv13での重複項目撤去、変更監査、履歴SQL複合検索、コイン表裏別統計、旧管理データ互換、GUI編集・表示、全単体テスト、Ruff、CLI/GUI EXEビルドと両スモークが成功すること。

## V0.21.1: 録画なし戦績の事後入力

状態: 完了

- 録画とは独立した`duel_id`を全対戦へ発行し、録画IDは任意の関連情報とする
- 録画あり・なしを同じ対戦記録、タグ、変更監査、統計で一元管理する
- 録画なし戦績へ対戦日時、勝敗、先後、コイントス、デッキ、種別、シーズン、タグ、メモを保存する
- DB v10へ移行し、既存録画戦績とV0.20以前の管理データJSONを保持する
- 録画履歴ページを戦績管理ページへ改め、手入力の追加・編集・削除と録画有無の識別を提供する
- 録画操作は録画付き戦績だけに表示し、手入力戦績には適用しない

実装済み: DB v10、Repository/API、`duel create` CLI、統計統合、旧JSON入出力互換、戦績管理GUI、手入力追加・編集・削除、録画有無フィルター。

追跡: [V0.21.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/41)、Issue #259 - #269

完了条件: v9からv10への安全な移行、録画あり・なしの一元表示とCRUD、統計・フィルター・JSON往復、全単体テスト、Ruff、CLI/GUI EXEビルドと両スモークが成功すること。

## V0.21.2: 初期画面・戦績更新排他

状態: 完了

- 初期画面は録画操作、録画状態、自動判定状態、アクティビティを中心とする現在構成を維持する
- 初期画面と戦績管理へ「戦績を追加」を配置する
- 現在期間内のシーズンを、ランク優先、同順位は終了期限が近い順で最大2件表示する
- シーズン名、勝率、対戦内訳を表示し、シーズンレポートへ遷移できるようにする
- 自動監視中は新規戦績入力を禁止し、既存編集画面は「自動監視中のため更新できません」を表示して読み取り専用にする
- 手動録画と録画開始・停止処理中も戦績の作成・更新・削除を禁止する
- 排他はGUIだけでなくアプリケーションサービスで強制する

追跡: [V0.21.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/42)、Issue #270 - #275

完了条件: 手動戦績CRUD、録画あり・なし統合一覧、監視中読み取り専用、録画中サービス拒否、シーズン優先順位、全単体テスト、Ruff、CLI/GUI EXEビルドと両スモークが成功すること。

## V0.21.3: V0.22.0-V0.25.0実装計画策定

状態: 完了

- V0.22.0からV0.25.0までを中核機能単位で確定する
- 各バージョンを設計、サービス、GUI、異常系、検証、文書へ細分化する
- GitHub Milestone、`version:0.x.0`ラベル、親Issue、子Issueを作成する
- 詳細な依存順と完了条件を`docs/implementation-plan-v0.22-v0.25.md`へ記録する
- 本版では次期機能を実装せず、既存V0.21.2機能の挙動を変更しない

追跡: [V0.21.3 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/48)、Issue #328

完了条件: Issue #276 - #327が対応Milestone・ラベルへ接続され、ロードマップ、詳細計画、README、リリースノート、バージョン情報が一致し、Ruffと全単体テストが成功すること。

## V0.22.0: 戦績入力体験

状態: 完了

- 勝敗、先後、自分デッキを中心とする簡易入力と詳細入力への遷移を提供する
- 前回値、開催中シーズン、利用頻度から候補を提示し、暗黙には確定しない
- 未完了戦績を保存・後回し・前後移動で連続処理する
- 複数戦績のシーズン、デッキ、種別、タグをトランザクションで一括更新する
- 複合フィルターに名称を付けて保存・再利用する
- 録画・自動監視中の更新排他と既存のデッキ表示規則を維持する

実装順: 入力契約 #277、候補サービス #278 - #279、入力GUI #280 - #281、未完了処理 #282 - #283、一括編集 #284 - #285、保存済みフィルター #286 - #287、検証・Release #288。

追跡: [V0.22.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/44)、親Issue #276、子Issue #277 - #288

完了条件: 簡易入力、候補値、未完了連続処理、一括編集、保存済みフィルターが録画あり・なしの双方で動作し、Ruff、全テスト、GUI描画、両EXE、Windows CIが成功すること。

## V0.23.0: 自動監視信頼性

状態: 実装・検証完了、V0.25.0へ統合・単独未公開

- 録画・監視・候補・確定・停止・失敗を単一状態機械と操作許可表で管理する
- 開始、盤面、ターン、結果の検出契約と評価指標を分離する
- オフライン動画とライブ監視を同一解析パイプラインへ接続する
- 適合率、再現率、検出遅延をイベント・解像度・表示モード別に評価する
- 画像、タイトル、絶対パスを含まない診断レポートを提供する
- 利用者向け状態と開発者向けスコア表示を分離し、Windows通知を追加する
- 異常系と実戦連続試験の合格基準を明文化する

実装順: 状態契約 #290、状態機械 #291 - #292、解析契約 #293 - #294、評価・診断 #295 - #297、通知 #298、異常系・実戦検証 #299 - #301、実戦ゲート集計 #330。

追跡: [V0.23.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/45)、親Issue #289、子Issue #290 - #301

完了条件: 単一状態機械、イベント別評価、診断、通知が統合され、SHA-256固定済み超横長14本・標準16:9ウィンドウ4本のオフライン評価、保存済み本検証セッション12録画の全編2fps評価、Ruff、全テスト、両EXE、Windows CIが成功すること。16:9で未取得のコイントス・リプレイ・マッチエラー実動画は公開時の既知リスクとして明記する。

## V0.24.0: データ保全

状態: 実装・障害注入検証完了、V0.25.0へ統合・単独未公開

- SQLite Backup APIによる整合性確認付き原子的バックアップを提供する
- 更新、移行、取込、初期化、復元前に用途別バックアップを作成する
- 世代数と容量上限でバックアップを管理する
- 復元前にスキーマ、整合性、件数、差分をプレビューする
- 復元途中の失敗時は元DBへ戻し、録画ファイルへ変更を加えない
- DB、録画参照、管理データの読み取り専用診断を統合する
- 移動した録画の再関連付けと、重複戦績候補の比較・解決を提供する
- 設定画面へデータ保全状態と操作入口を集約する

実装順: 保全契約 #303、バックアップ #304 - #306、復元 #307 - #308、診断・再関連付け #309 - #310、重複検出 #311 - #312、GUI #313、障害注入・Release #314。

追跡: [V0.24.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/46)、親Issue #302、子Issue #303 - #314

完了条件: バックアップ、復元、診断、再関連付け、重複解決が失敗時に元データと録画を保持し、障害注入、Ruff、全テスト、両EXE、Windows CIが成功すること。

## V0.25.0: シーズンレポート

状態: 完了。V0.23.0・V0.24.0を統合して公開済み

- 現在シーズンと前シーズンまたは任意シーズンを比較する
- デッキ別・先後別クロス集計、コイン表裏・最終勝敗の内訳を提供する
- 日・週の勝率、対戦数、使用デッキ比率の推移を表示する
- 少数標本、未設定、非表示デッキ、分母を明確に表示する
- シーズン終了・アーカイブ時の振り返りフローを提供する
- 目標、良かった点、課題、次期方針を既存メモと共存させる
- 外部CDNや絶対パスを含まない印刷可能な単一HTMLへ出力する
- コイン表裏と意味が重複するコイントス勝敗項目をDBスキーマv13で完全撤去する

実装順: 集計契約 #316、比較・分析 #317 - #320、終了フロー #321、レポートGUI・メモ #322 - #323、HTML出力 #324 - #325、検証・Release #326 - #327。

追跡: [V0.25.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/47)、親Issue #315、子Issue #316 - #327、修正Issue #333 - #339、#366

完了条件: 統計ページと同じ母集団で比較・分析・推移・メモ・HTML出力を提供し、集計一致、描画、出力検証、Ruff、全テスト、両EXE、Windows CIが成功すること。

## V0.26.0: 単体音声・戦績CSV移行

状態: 完了

- Windows Process LoopbackでMaster Duelと子プロセスの音声だけを48kHz stereo PCMとして取得する
- 映像対象と音声対象を分離し、デスクトップ映像とMaster Duel単体音声を組み合わせられるようにする
- Master Duelのみ、PC全体、入力デバイス、音声なしの4モードを提供する
- 自動監視中に音声ヘルパーを事前待機し、同一プロセスを候補録画へ引き継ぐ
- 音声障害時に別音源へ無断で切り替えず、映像と履歴を保護して診断を残す
- ネイティブヘルパーと第三者ライセンス表示をone-file EXEへ同梱する
- 固定11列、UTF-8 BOM、CRLFの戦績CSVを出力・取込する
- 既存ID更新、未知ID再附番、未登録デッキ・タグ・シーズン作成をプレビュー後の単一トランザクションで実行する
- 新規取込戦績を登録元「取込」として手動戦績と同様に管理する
- 設定画面にCSV入出力タブとサンプルCSV出力を追加する

実装順: 音声契約 #341、PoC・ヘルパー #342 - #345、設定・GUI・事前待機 #346 - #349、配布・検証 #350 - #353、CSV契約・DB #355 - #356、出力・解析・適用 #357 - #362、GUI・検証 #363 - #365。

追跡: [V0.26.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/49)、親Issue #340・#354、子Issue #341 - #353・#355 - #365、修正Issue #368

完了条件: Master Duel以外の音を混入させない実機音声録画、手動・自動・デスクトップ映像の組み合わせ、30分A/V同期、CSV往復・ロールバック、Ruff、全テスト、両EXE、Windows CI、公開SHA-256が成功すること。

## V0.26.1: クリーンアンインストール

状態: 完了

- 現在の実行時ルートにある設定、DB、録画、ログ、キュー、バックアップ、エクスポート、導入済みFFmpegを一括削除する
- 削除対象のパス、ファイル数、フォルダ数、合計サイズを実行前に表示する
- GUIの確認語と最終確認、CLIの二重確認を必須にする
- 録画、自動監視、開始・停止、他の管理処理中は実行を拒否する
- ドライブ、ホーム、LocalAppDataそのものと不明な任意ルートを拒否し、リンク先へ越境しない
- Windows one-file版では終了後クリーナーを使い、任意選択した起動EXEも削除する

実装順: 安全境界 #370、終了後クリーナー・CLI #371、設定GUI #372、検証・Release #373。

追跡: [V0.26.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/50)、親Issue #369、子Issue #370 - #373

完了条件: 隔離環境で全使用領域と任意EXEを削除し、境界外ファイルとリンク先を保持し、Ruff、全テスト、CLI/GUI EXEビルド、両スモーク、Windows CI、公開SHA-256が成功すること。

## V0.26.2: V1移行準備

状態: 完了

- 仕様変更後に残ったIssue #250を現行のコイン表裏仕様とテスト証拠へ接続して完了する
- V0.20.0 Milestoneと公開済みバージョンのIssue・Milestone状態を整合する
- ロードマップ、E2Eチェックリスト、公開済み検証記録の古い状態表記を修正する
- V1.0.0の対象をV0.2.0からV0.26.2までの全中核機能と修正として明文化する
- 空の実行時ルートで初期化、戦績CRUD、CSV往復、バックアップ、クリーンアンインストール境界を確認する
- Ruff、全テスト、実FFmpeg試験、CLI/GUI EXEビルド、両スモーク、Windows CI、公開SHA-256を再確認する
- 対応解像度、実動画コーパス、Process Loopback要件、未署名EXE、YouTube連携対象外を既知制約として維持する

追跡: [V0.26.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/51)、親Issue #374、子Issue #375 - #377

完了条件: GitHub上の未完了Issue・MilestoneがV0.26.2の作業だけとなり、文書・コード・タグ・Releaseが一致し、クリーン環境E2Eと全品質ゲートが成功すること。V1.0.0への更新は含めない。

## V1.0.0: 中核機能完成

状態: 完了

- V0.2.0からV0.26.2までの計画済み中核機能と修正を正式版として確定する
- V0.26.2でIssue・Milestone・文書・検証証拠を整理し、最終クリーン環境E2Eを完了する
- 2026-08-16のユーザーによる明示的なV1.0.0変更指示をバージョンとREADMEへ反映する
- 正式版のCLI/GUI配布物へ全テスト、実FFmpeg試験、両スモーク、クリーン環境E2Eを再適用する
- main、`v1.0.0`タグ、GitHub Release、provenance、公開SHA-256を一致させる
- YouTube連携・直接アップロードは独立した将来計画とする

追跡: [V1.0.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/52)、親Issue #378、子Issue #379 - #380

完了条件: コード、README、ロードマップ、main、`v1.0.0`タグ、GitHub Release、CI、公開SHA-256が一致し、V0.26.2で確定した既知制約を維持したまま全品質ゲートが成功すること。

## V1.0.1: 正式版初回の不具合修正と操作改善

状態: 完了

- 手動戦績削除時の外部キー制約エラーを修正する
- FFmpegの既存実行ファイルと導入先をGUIから選択可能にする
- 実行時データの保存先をバックアップ・検査・失敗時非切替付きで変更可能にする
- MP4準備対象を利用者が識別できる録画情報から選択可能にする
- 統計へシーズン別勝率を追加する
- 戦績管理の任意列表示と値別背景色設定を追加する
- 録画なし戦績の追加操作を明確に表記する
- GitHub正式Releaseの安全な更新確認・取得・適用を追加する
- 専用アプリアイコンをGUI、タスクバー、配布EXEへ適用する

追跡: [V1.0.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/53)、親Issue #381、子Issue #382 - #392

完了条件: Ruff、全単体テスト、CLI/GUI EXEビルド、両スモーク、GUI目視確認、Windows CI、公開SHA-256に合格し、コード、文書、main、`v1.0.1`タグ、GitHub Releaseを一致させること。

## V1.0.2: 戦績入力フローと一括編集の操作改善

状態: 完了

- 未完了戦績の連続処理へ簡易入力を統合する
- 詳細入力の保存・キャンセル後に未完了戦績の連続処理へ復帰する
- 一括編集へコイントスの表裏と未設定を追加する
- 戦績管理一覧のダブルクリック動作を録画再生または戦績編集から選べるようにする
- 戦績管理一覧の表示列へ相手デッキを初期非表示の任意列として追加する
- README、設計文書、リリースノート、バージョン情報を`1.0.2`へ整合する

追跡: [V1.0.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/54)、親Issue #393、子Issue #394 - #398

完了条件: Ruff、全単体テスト、配布ビルド、GUIスモーク、Release Contract確認に合格し、コード、文書、`v1.0.2`タグ、GitHub Releaseを一致させること。

## V1.0.3: GUI操作性改善

状態: 完了

- カレンダーピッカーのヘッダーを7列グリッドへ整理し、前月、年月、今月へ、翌月を1:3:2:1で配置する
- 年月タイトルを火・水・木列へまたがる大きめの表示にする
- 月曜から日曜までの曜日ラベルと日付セルを等幅で揃え、土曜日列だけ歪まないようにする
- 戦績の簡易入力、未完了処理、詳細編集、一括編集で固定択一項目をボタン選択へ変更する
- 一括編集の「変更しない」と「未設定」を視覚的にも保存値としても分離する
- 更新時変更ログ表示方針をリリース確認文書へ残す

追跡: [V1.0.3 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/55)、親Issue #409、子Issue #399 - #400

完了条件: カレンダーと固定択一UIのGUIスモーク、Ruff、全単体テスト、CLI/GUI EXEビルド、両スモーク、Release Contract確認に合格し、コード、文書、`v1.0.3`タグ、GitHub Releaseを一致させること。

## V1.1.0: YouTube公式連携 + MP4自動アップロード

状態: 完了

- YouTube投稿向け公開範囲と概要欄テンプレートを定義する
- 履歴DBにYouTubeアップロード状態と動画URLを保存する
- OAuth 2.0をOS資格情報ストアで管理し、秘密情報を設定、manifest、queue、ログへ保存しない
- 既存prepare結果を再利用し、MP4準備からアップロード待ちまでを自動化する
- YouTube Data APIのresumable upload、再試行、通信断・5xx・rate/quota・401/403分類を実装する
- `mdrl youtube` CLIと履歴URL表示を追加する
- タイムラインマーカーから投稿用クリップを元録画非破壊で出力する
- OAuth未接続でも投稿素材、サムネ候補、投稿チェックリストを生成できるようにする

追跡: [V1.1.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/56)、親Issue #401、子Issue #402 - #408、#410 - #411

完了条件: MP4準備からYouTube投稿、履歴URL表示、クリップ出力、投稿素材生成、秘密情報非保存、DB移行、Ruff、全単体テスト、fake client結合テスト、CLI/GUI EXEビルド、両スモーク、手動E2E、GitHub Releaseが成功すること。

## V1.2.0: 自動録画の信頼性と後解析

状態: 完了

- 対戦前の30秒自動録画事前チェックと利用者向け判定理由を提供する
- 初回導入ウィザードへFFmpeg確認、保存先確認、テスト録画、再生確認を統合する
- 既存動画とゲーム内リプレイ録画の後解析入口を追加する
- グローバルホットキーとタスクトレイから録画操作と状態確認を行えるようにする
- 数値診断と利用者向け判定を分離したまま文書と検証を更新する

追跡: [V1.2.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/57)、親Issue #412、子Issue #413 - #417

完了条件: 30秒チェック、初回ウィザード、後解析、ホットキー、トレイ、設定後方互換、必要なDB判断、Ruff、全単体テスト、GUIスモーク、CLI/GUI EXEビルド、Windows CI、GitHub Releaseが成功すること。

## V1.3.0: 入力削減・デッキ改善・運用管理

状態: 完了

- 相手デッキ、前回値、最近値、タグテンプレートを使った入力候補を強化する
- 録画なし戦績のミニ入力モードを追加する
- 自分デッキ別に対面、先後、コイン、シーズンを分析するデッキ改善ビューを追加する
- ユーザー定義タグテンプレート、目標管理、練習メニューを追加する
- 古い録画、失敗録画、出力済み、未入力、重要タグを使うストレージ管理候補を提示する
- 設定、DB、デッキ辞書、タグ、シーズンの安全な移行パックを実装する
- クリップ再生、マーカー、戦績編集をまとめる軽量レビュー画面を追加する

追跡: [V1.3.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/58)、親Issue #418、子Issue #419 - #426

完了条件: 入力候補、ミニ入力、改善ビュー、目標、ストレージ候補、移行パック、軽量レビュー、DB移行、録画ファイル保護、Ruff、全単体テスト、GUIスモーク、CLI/GUI EXEビルド、Windows CI、GitHub Releaseが成功すること。

## V1.4.0: YouTube一般配布導線

状態: 外部検証待ち

- YouTube OAuthを配布者管理OAuth Client、PKCE、`127.0.0.1:<random port>` loopback callbackへ再設計する
- `client_secret.json`取得と認可コード再実行を一般ユーザー導線から外し、既定ブラウザでの認証完了をGUIから扱う
- YouTube連携状態、連携、切断、接続確認、privateテスト投稿をGUIで操作できるようにする
- 録画履歴から投稿前確認を経てYouTube private投稿し、完了時に`youtube_uploads.video_id`と`watch_url`を保存して履歴へ表示する
- 401、403、quota/rate、5xx、通信断、不明応答を利用者向け状態へ分類し、再認証、再試行、quota待ち、手動確認の次アクションを表示する
- refresh token、access token、client secret相当の値を設定、DB、queue、manifest、ログ、移行パックへ保存しない契約を維持する
- 検証用YouTubeチャンネルでprivate実投稿E2Eを実施し、GUI連携、MP4準備、投稿完了、履歴URL表示、重複投稿防止、切断後の失敗表示を記録する
- README、YouTube連携設計、検証記録、リリースノート、バージョン情報、配布物検証を`1.4.0`へ整合する

追跡: [V1.4.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/59)、親Issue #427、子Issue #428 - #433

完了条件: GUIだけでYouTube連携とprivate投稿が完了し、秘密情報非保存、既存CLI fallback互換、実YouTube private投稿E2E、Ruff、全単体テスト、GUIスモーク、CLI/GUI EXEビルド、両EXEスモーク、Release Contract確認、GitHub Releaseが成功すること。

## V1.4.1: YouTube一般配布導線hotfix

状態: 外部検証待ち

- 改善ページの履歴取得回帰を修正する
- 配布EXEへYouTube OAuth client_idを安全に供給し、未設定releaseビルドを検出する
- OAuth client_id未設定時のGUI導線を、例外ダイアログではなく状態表示と操作可否で扱う
- MP4準備をYouTube投稿導線へ統合し、独立した主要ナビとして露出しない
- 改善ページの主要ナビ露出を整理し、必要な操作は戦績管理などへ移す
- V1.4.1のバージョン、リリースノート、検証記録、配布物を整合する

追跡: [V1.4.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/61)、親Issue #444、子Issue #445 - #450

完了条件: GUIで改善ページ回帰が再発せず、YouTube client_id設定済みビルドで連携開始へ進め、未設定ビルドでは明確に無効化され、MP4準備がYouTube投稿フロー内で確認でき、Ruff、全単体テスト、GUIスモーク、CLI/GUI EXEビルド、両EXEスモーク、実YouTube private投稿E2Eまたは外部ゲート記録が完了すること。

## V1.4.2: 更新EXE起動検証とOAuth診断hotfix

状態: 外部検証待ち

- アプリ更新で取得したGUI EXEを置換前に起動検証し、Python DLL展開失敗などの起動不能assetを適用しない
- GitHub Release公開後に公開済みassetを再ダウンロードし、CLI/GUIスモークを公開物へ直接実行する
- YouTube OAuth token交換HTTP 400の本文を読み取り、secretやtokenを伏せたうえで原因分類と次の確認事項を表示する
- V1.4.2配布ビルドへ新しいYouTube OAuth `client_id`だけをGitHub Secretから同梱する
- README、設計文書、リリースノート、検証記録、バージョン情報、配布物検証を`1.4.2`へ整合する

追跡: [V1.4.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/62)、親Issue #452、子Issue #453 - #457

完了条件: 更新対象GUI EXEの事前スモークで起動不能assetを拒否でき、公開済みRelease assetの再スモークがCIで成功し、OAuth 400時に秘匿済み診断と対処を表示し、Ruff、全単体テスト、GUIスモーク、CLI/GUI EXEビルド、両EXEスモーク、Release Contract確認、GitHub Release、更新確認からのダウンロード検証が成功すること。

## V1.4.3: アプリ内自動更新updater化

状態: 完了

- release toolingの固定バージョン期待値をプロジェクト版から導出し、Fixリリース時のテスト更新漏れを防ぐ
- GUIの自己置換用PowerShellを廃止し、GUIへ同梱した専用updater EXEから現在GUI EXEを置換する
- 専用updaterで親GUI終了待機、候補SHA-256再確認、`.staged`コピー、`.previous`退避、置換後スモーク、失敗時ロールバック、成功時再起動を行う
- release workflowでCLI/GUI/updaterの3 EXEとSHA-256を公開し、公開済みassetを再ダウンロードして3 EXEをスモークする
- V1.4.1/V1.4.2の旧更新コードでは起動前スモークだけでPyInstaller展開失敗を防げない理由を検証記録へ残す
- V1.4.2で入れたYouTube OAuth client_id同梱、OAuth 400診断、Windows CredentialBlob互換修正を維持する
- README、設計文書、リリースノート、検証記録、バージョン情報、配布物検証を`1.4.3`へ整合する

追跡: [V1.4.3 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/63)、親Issue #459、#462、子Issue #460 - #461、#463 - #465

完了条件: アプリ内更新が手動ダウンロードなしで専用updaterから適用でき、失敗時に旧GUIへロールバックでき、release toolingの固定期待値が消え、Ruff、全単体テスト、Python GUIスモーク、CLI/GUI/updater EXEビルド、3 EXEスモーク、updater置換スモーク、Release Contract確認、GitHub Release、更新確認からのダウンロード検証が成功すること。

## V1.4.4: YouTube OAuth client_secret同梱hotfix

状態: 完了

- 配布EXEへYouTube OAuth `client_id`と`client_secret`を同梱する
- token交換で`client_secret`を送信し、配布GUIの通常連携を復旧する
- OAuth token、refresh token、認可コードを配布物、設定、DB、queue、manifest、ログへ保存しない契約を維持する

追跡: [V1.4.4 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/64)、Issue #468

## V1.4.5: YouTube OAuth資格情報ストアhotfix

状態: 完了

- PyInstaller EXEでWindows資格情報ストアの読み取り・削除が失敗する回帰を修正する
- YouTube接続確認、投稿ダイアログ、CLI EXEスモークで保存済み資格情報を確認する
- DBスキーマ、設定形式、録画ファイル、prepare queueは変更しない

追跡: [V1.4.5 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/65)、Issue #466

## V1.4.6: 戦績編集とデッキタグ管理の改善

状態: 完了

- 対戦記録編集画面へ録画詳細操作、録画有無、YouTube連携状態、YouTube URL欄を整理する
- デッキ名にタグを登録できるようにし、デッキ専用タグを戦績入力候補から除外する
- DBスキーマをV17へ更新し、既存戦績、録画、YouTube投稿履歴、タグを保持する

追跡: [V1.4.6 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/66)、親Issue #471、子Issue #472 - #476

## V1.5.0: GUI非依存データ契約とPySide6レビュー基盤

状態: 完了

- GUI境界と保存契約を文書化する
- 録画概要、動画参照、タイムライン、戦績概要、クリップ候補をGUI非依存ViewModel/DTOで表す
- 保存契約、ViewModel、PySide6未導入fallback、Tkinter別プロセス起動導線をテストする
- PySide6レビュー画面を隔離導入し、動画再生、タイムライン選択、現在位置マーカー、クリップ出力導線を検証する
- DBスキーマと設定形式は変更せず、既存`user_data`とOAuth資格情報非保存契約を維持する

追跡: [V1.5.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/60)、親Issue #434、子Issue #435 - #441

完了条件: Review ViewModel、PySide6隔離入口、Tkinter併存導線、保存契約テスト、Ruff、全単体テスト、Python GUIスモーク、CLI/GUI/updater EXEビルド、3 EXEスモーク、Release Contract確認、GitHub Releaseが成功すること。

## V1.5.1: 戦績管理・YouTube投稿・入力体験の調整

状態: 完了

- タグ管理、戦績管理、YouTube投稿、録画診断、録画レビュー、簡易入力の迷いやすい操作を調整する
- YouTube投稿テンプレートを追加し、投稿済み録画のリンク確認導線を整理する
- 簡易入力で勝敗、先後、コイントス、相手デッキを素早く入力できるようにする
- DBスキーマV17と既存録画、戦績、YouTube投稿履歴、OAuth資格情報を維持する

追跡: [V1.5.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/67)、親Issue #479、子Issue #480 - #491

完了条件: Ruff、全単体テスト、Python GUIスモーク、CLI/GUI/updater EXEビルド、3 EXEスモーク、Release Contract確認、GitHub Releaseが成功すること。

## V1.5.2: デッキ名の使用回数表示・利用頻度順・色表示

状態: 完了

- デッキ名ごとの使用回数を、自分デッキと相手デッキの出現回数合計として派生集計する
- デッキ名管理画面へ使用回数列を追加し、使用回数降順で表示する
- 戦績管理、統計、簡易入力、未完了処理、詳細入力、一括編集のデッキ候補順を使用回数順で揃える
- 戦績管理のデッキ色表示を、小さい枠線付き四角スウォッチへ変更する
- PySide6刷新前のTkinter UI baseline画像、ポップアップ導線、データ保護要件を保存する
- DBスキーマと設定形式は変更せず、既存`user_data`、録画、SQLite DB、OAuth資格情報を保持する

追跡: [V1.5.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/68)、親Issue #492、子Issue #493 - #496、#498 - #499

完了条件: デッキ使用回数、候補順、枠線付きスウォッチ、Tkinter UI baseline保存、Ruff、全単体テスト、Python GUIスモーク、CLI/GUI/updater EXEビルド、3 EXEスモーク、Release Contract確認、GitHub Releaseが成功すること。

## V1.6.0: 録画後ワークフロー情報設計

状態: 完了

- V1.5.2のTkinter UI baselineを入力に、録画後の整理、振り返り、投稿準備の導線を定義する
- 戦績管理を録画後ハブとして扱い、未完了、録画欠損、投稿済み、手動戦績の状態優先度を整理する
- MP4準備と改善の内部ページを、V2.0.0で残す、統合する、削除する判断基準へ分解する
- 設定画面の通常設定、外部連携、データ保護、危険操作、診断の境界を整理する
- 戦績入力、タイムライン、YouTube投稿、レビュー、診断のダイアログ導線をワークフロー単位で整理する
- V2.0.0のスクリーンショット回帰と操作スモークに、録画後ワークフロー単位の検証要件を追加する
- Master Duel単体音声の録画後診断で、出力ファイルの音声ストリーム有無を履歴警告として確認できるようにする
- 音声設定GUIで、Master Duel単体音声のDirectShow入力欄が未使用であることを明示する
- DBスキーマ、設定形式、録画ファイル、queue、manifest、OAuth資格情報は変更しない

追跡: [V1.6.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/70)、親Issue #523、子Issue #524 - #531

完了条件: 録画後ワークフロー情報設計、V2.0.0 Issueへの反映事項、単体音声の録画後警告、音声設定GUIの誤読防止、README、リリースノート、検証記録、バージョン情報、Ruff、全単体テスト、GUIスモーク、CLI/GUI/updater EXEビルド、3 EXEスモーク、GitHub Releaseが成功すること。

## V2.0.0: Tkinter GUIからPySide6 GUIへの全面移行

状態: 完了

- 正式配布後初のメジャーバージョン更新として、Tkinterで実装されているGUIをPySide6で全面再構築する
- V1.5.2のTkinter UI baseline画像・要件保存（Issue #499）を前提資料とし、現行画面・ポップアップ・OS標準ダイアログ導線の欠落を防ぐ
- アプリシェル、共通UI部品、起動入口、サービス接続を先に整備し、ページごとの移行Issueが同じ設計規約で進められるようにする
- 主要ナビの録画、戦績管理、統計、デッキ名、タグ、シーズン、YouTubeテンプレート、信頼性、設定を1ページ単位で追跡する
- 通常ナビから外れているMP4準備と改善の内部ページは、PySide6上で残す、統合する、削除するのいずれかをIssue単位で判断する
- `master-duel-recorder-lite-gui.exe`の通常入口をPySide6 GUIへ切り替え、旧Tkinter GUIは互換モジュールとして残す
- PySide6 GUIスモークJSONとスクリーンショットを保存し、配布GUIがPySide6であることを検証する
- 録画開始/停止、自動監視切替、履歴更新はPySide6 GUIから既存サービス層へ接続する
- DBスキーマ、設定形式、録画ファイル、queue、manifest、OAuth資格情報は変更しない

追跡: [V2.0.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/69)、親Issue #500、子Issue #501 - #522

完了条件: PySide6 GUI入口、主要ナビ、共通状態表示、録画・履歴のサービス接続、prepare/improve統合方針、互換Tkinter入口、スクリーンショット回帰、データ保護確認、README、リリースノート、検証記録、バージョン情報、Ruff、全単体テスト、PySide6 GUIスモーク、CLI/GUI/updater EXEビルド、3 EXEスモーク、GitHub Releaseが成功すること。

## V2.0.1: 通常配布GUI復旧

状態: 完了

- V2.0.0更新後に既存DB表示が失われたように見える問題を復旧する
- `master-duel-recorder-lite-gui.exe`の通常入口を1.x相当のTkinter GUIへ戻す
- PySide6全面移植で必要な1.x標準機能を列挙し、通常入口化の同等性ゲートにする
- 既存`user_data`、SQLite DB、録画、queue、manifest、OAuth資格情報を変更しない

追跡: [V2.0.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/71)、親Issue #536、子Issue #537 - #540

完了条件: 既存DB入りruntimeの再読込、通常GUIの標準機能ゲート、README、リリースノート、検証記録、バージョン情報、Ruff、全単体テスト、Python GUIスモーク、CLI/GUI/updater EXEビルド、3 EXEスモーク、GitHub Releaseが成功すること。

## V2.0.2: 15標準機能の実操作確認と録画後導線改善

状態: 完了

- V2.0.1で復旧した1.x相当の通常配布GUIについて、15標準機能をwidget存在ではなくユーザー操作レベルで確認する
- GUI smokeと回帰テストを拡張し、実体のないwidget名だけでは合格できない検証条件にする
- 戦績管理を録画後ハブとして、未完了処理、再生、編集、タイムライン、診断、YouTube投稿へ迷わず進める導線にする
- データ保全、復元、整合性診断の状態表示と失敗時の次アクションを分かりやすくする
- PySide6通常入口化ゲートを、DB入りruntime、録画後ワークフロー、データ保全、安全な失敗表示まで含む実操作確認へ拡張する
- PySide6を通常配布入口へ戻すこと、SQLite schema変更、設定形式変更、録画・queue・manifest・OAuth資格情報の削除や初期化は対象外とする
- V1.4.0 - V1.4.2の古いopen Issueは#548でV2.0.2完了前に棚卸しし、実装済み未close、外部検証待ち、未解決のどれかへ分類する

追跡: [V2.0.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/72)、親Issue #542、子Issue #543 - #548

推奨順: #543 実操作チェック、#544 録画後ワークフロー導線、#545 データ保全表示、#546 PySide6通常入口化ゲート、#548 V1.4.x open Issue棚卸し、#547 検証・文書・リリース整合。

完了条件: 15標準機能の実操作確認、録画後導線、データ保全表示、PySide6同等性ゲート、README、設計文書、リリースノート、検証記録、バージョン情報、Ruff、全単体テスト、GUI smoke、CLI/GUI/updater EXEビルド、3 EXEスモーク、Release Contract確認が一致し、既存`user_data`、SQLite、録画、queue、manifest、OAuth資格情報を保持すること。

## V2.0.3: 戦績管理・統計・シーズンレポートの読み取り品質改善

状態: 完了

- 戦績管理一覧のデッキ名列で、デッキ色スウォッチとデッキ名が重ならず、長い日本語デッキ名でも読み取れるようにする
- デッキ名列の表示は、列幅、文字余白、スウォッチ位置、DPI差、横スクロール、再描画タイミングを確認対象にする
- 統計の「勝利数・勝率推移」は、棒を期間ごとの勝利数、線を期間開始点からの累積勝率として扱う
- 期間フィルター時の累積開始点はフィルター開始日とし、0戦日は期間勝利数0、累積勝率は直前までの値を維持し、引分は分母に含める
- シーズンレポートでは、勝敗列と重複する`最終勝敗`軸を標準表示から外す
- デッキ・先後・コイントスの内訳では、全体行を比較基準として追加し、0件の未設定行は標準表示から外す
- 未設定値を推測しないデータ方針は維持し、未設定の実データがある場合だけ標準表示へ残す
- GUIとHTML出力、既存テストの期待値を同じ表示方針へ揃える
- SQLite schema、設定形式、録画ファイル、queue、manifest、OAuth資格情報は変更しない

追跡: [V2.0.3 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/73)、親Issue #550、子Issue #551 - #554

実施順: #551 戦績管理のデッキ名可読性改善、#552 統計の日別勝利数・累積勝率、#553 シーズンレポート・統計内訳の表示整理、#554 検証・文書・リリース整合。

完了条件: 実データ入り戦績管理でデッキ色とデッキ名が重ならず、統計推移が日別勝利数と累積勝率として表示され、シーズンレポートの重複軸・未設定行・全体行の表示方針がGUI/HTML/テストで一致し、Ruff、全単体テスト、GUI smoke、配布EXEビルド、検証記録が一致すること。

## V2.1.0: PySide6標準機能移植

状態: 完了

- PySide6通常入口化を単発の入口切替ではなく、1.x相当の15標準機能をQt上で実操作できる状態へ移植する中核GUIリリースとして独立させた
- V2.0.1/V2.0.2で定義したPySide6機能同等性ゲートを前提に、要求widget 51個と主要操作チェックをPySide6 smoke contractへ揃えた
- 移植対象は録画、戦績管理、履歴フィルター/表示列/YouTube導線、手動戦績、統計、デッキ名、タグ、シーズン、YouTube、MP4準備、信頼性、設定、データ保全、CSV/更新、主要ダイアログとした
- 通常入口をPySide6へ戻し、配布GUI smokeはPySide6入口、標準機能ゲート、録画後ワークフロー、データ保全表示を確認する
- 完了条件は、現行機能をPySide6 UIで管理・表示でき、15標準機能の実操作ゲートに合格することとした
- 統計の推移単位は、PySide6統計画面では「勝利数・勝率推移」内の条件として扱い、既定値を日単位にした
- V2.0.3の表示契約をPySide6統計・戦績表示へ引き継いだ
- SQLite schema、設定形式、録画ファイル、queue、manifest、OAuth資格情報は変更しない
- TkinterとQtのevent loopを同一プロセスで混在させない

追跡: [V2.1.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/74)、親Issue #556、子Issue #557 - #563、[Release Contract](release-contracts/2.1.0.md)。

推奨順: #557 PySide6シェルと標準機能契約、#558 履歴/戦績管理ハブ、#559 統計/シーズン/デッキ/タグ、#560 YouTube/MP4準備、#561 設定/データ保全/CSV更新、#562 主要ダイアログ、#563 DB入りruntimeと配布EXE smoke、通常入口切替判定。

完了条件: PySide6 GUIが15標準機能の実操作ゲートを満たし、既存戦績、録画履歴、デッキ、タグ、シーズン、統計、YouTube投稿状態、設定、データ保全を扱え、既存`user_data`、SQLite、録画、queue、manifest、OAuth資格情報を保持し、Ruff、全単体テスト、PySide6 GUI smoke、設計文書、検証記録が一致すること。配布EXEビルドと3 EXEスモークはローカル環境では未実行のため、Release作成前の追加確認対象とする。

## V2.2.0: PySide6 UI読み取り品質改善

状態: 完了

- 統計画面の開始日・終了日をカレンダーポップアップ付きの日付入力にする
- 統計の勝利数・勝率推移を、勝利数の棒グラフと累積勝率の折れ線グラフとして表示する
- 戦績管理、デッキ名、タグ、シーズン、統計内訳などの表に、列幅、横スクロール、行高、穏やかな選択色を設定する
- デッキ/タグのカラー列を、色コード文字だけでなく色見本として確認できる表示へ改善する
- PySide6 smoke contractへ、カレンダー、統計チャート、表可読性、カラー表示のUI改善契約を追加する
- SQLite schema、設定形式、録画ファイル、queue、manifest、OAuth資格情報は変更しない

追跡: [V2.2.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/76)、Issue #566、[Release Contract](release-contracts/2.2.0.md)。

完了条件: 統計画面の日付入力がカレンダーポップアップ付きで、統計推移が棒と線のグラフとして表示され、表の列幅・横スクロール・選択色・カラー表示が読み取りやすく、Ruff、重点テスト、PySide6 GUI smoke、スクリーンショット確認、検証記録が一致すること。

## V2.2.1: アプリ更新Release選択hotfix

状態: 完了

- 配布資産なし通常Releaseでアプリ内更新が壊れる問題を修正する
- 更新確認をGitHubの`latest` 1件依存から、Release一覧の配布可能安定版選択へ変更する
- GUI EXE、updater EXE、各SHA-256が揃ったReleaseだけを更新候補にする
- 配布可能Releaseが存在しない場合は、例外ではなく「利用可能な更新なし」と扱う
- V2.2.1通常ReleaseにはCLI/GUI/updater EXE、各SHA-256、SHA256SUMSを揃える
- SQLite schema、設定形式、録画ファイル、queue、manifest、OAuth資格情報は変更しない

追跡: [V2.2.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/77)、Issue #568、[Release Contract](release-contracts/2.2.1.md)。

完了条件: 資産なしReleaseをスキップして配布可能な最新安定版を選べ、Ruff、重点テスト、EXEビルド、3 EXEスモーク、公開Release asset検証、Issue/Milestone/Release closureが完了すること。

## V2.2.2: 設定画面・アプリ更新Hotfix

状態: 完了

- V1.x時期の設定画面構成を確認し、PySide6設定画面へ録画設定、YouTube連携、管理データ、CSV入出力、表示、アプリ更新の入口を復旧する
- 設定読み書きを既存の設定管理テーブルへ接続し、録画設定と検出設定のキー取り違えを防ぐ
- アプリ更新タブで、現在バージョン、確認中、最新、更新候補あり、確認失敗を区別する
- 更新候補がある場合だけダウンロードして更新ボタンを有効化し、候補なしでは押せない状態にする
- GUI smokeとWindows GUI EXE smokeへ設定画面復旧契約と更新タブ状態契約を追加する
- SQLite schema、設定形式、録画ファイル、queue、manifest、OAuth資格情報は変更しない

追跡: [V2.2.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/78)、Issue #570、[Release Contract](release-contracts/2.2.2.md)。

完了条件: V1.x相当の設定カテゴリと操作入口がPySide6設定画面で確認でき、アプリ更新タブが候補あり/なし/失敗を誤表示せず、Ruff、全テスト、UI screenshot smoke、EXEビルド、3 EXEスモーク、公開Release asset検証、Issue/Milestone/Release closureが完了すること。
