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

## V1.0.0: 中核機能完成

V0.2.0からV0.17.1までの中核機能と修正が完了しても、自動ではV1.0.0へ更新しません。全完了条件を確認したうえで、ユーザーが明示的に「V1.0.0に変更せよ」と依頼した場合のみ更新します。対戦ログの編集・エクスポートとYouTube連携・アップロードはV0.17.1以降の候補として別途計画します。
