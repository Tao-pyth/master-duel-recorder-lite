# master-duel-recorder-lite

master-duel-recorder-liteは、OBSに依存せず、Yu-Gi-Oh! Master Duelの対戦録画、整理、戦績分析、共有準備を自動化する軽量なローカルツールです。録画や状態管理をPython側へ集約し、外部ツールとしてFFmpegを利用します。

## プロダクトコンセプト

対戦を記録したいユーザーが、録画ソフトの複雑な設定や録画開始・停止の操作に気を取られず、対戦後に必要な動画を見つけて安全に共有準備できることを目指します。録画ファイルと履歴をユーザーの管理下に置き、失敗時にも元データを失わないことを優先します。

## 中核機能

V2.2.2では、次の中核機能を提供します。

- FFmpeg、録画入力、保存先を確認する録画環境の初期化
- 画面と音声を録画し、正常停止して再生可能なファイルを保存する最小録画
- Master Duelの実行状態と対戦状態に応じて開始・停止を補助する自動録画
- 録画結果、状態、ファイル、失敗理由をSQLiteで追跡する録画履歴
- ランク・イベント期間を独立して管理し、戦績とレポートメモをまとめるシーズン管理
- 動画検証、remux、メタデータ、キュー、マニフェストを扱うアップロード準備
- YouTube公式OAuth連携、MP4自動アップロード、履歴への動画URL保存
- タイムライン時刻からの投稿用クリップ出力と、OAuth未接続でも使える投稿素材生成
- 自動録画の事前チェック、初回導入状態、既存動画後解析、ホットキー・トレイ操作入口
- 戦績入力候補、タグテンプレート、目標、デッキ改善集計、ストレージ候補、移行パック、軽量レビュー入口
- 初期化からアップロード準備までを一貫して操作する設定・運用CLI
- 録画履歴から動画を再生し、保存場所へ到達する録画の閲覧
- 勝敗、先後、デッキ、対戦種別、タグ、メモを後編集できる対戦記録管理
- 対戦開始、ターン切り替え、勝敗を録画時刻へ関連付ける対戦タイムライン
- 全体・条件付き勝率と時期ごとの推移を確認する戦績統計
- Master Duelだけのゲーム音声を映像対象と独立して取得するプロセス単体音声
- スプレッドシートの既存戦績を安全に移行・再出力するCSV入出力
- GUI非依存のレビュー用ViewModel、PySide6レビュー入口、PySide6通常配布GUI

## 現在の状態

現在の正式版は `2.2.2`、「設定画面・アプリ更新Hotfix」です。`master-duel-recorder-lite-gui.exe`はPySide6 GUIを起動し、録画、戦績管理、統計、デッキ名、タグ、シーズン、YouTube、MP4準備、信頼性、設定、データ保全、CSV/更新、主要ダイアログの状態を管理・表示できます。既存DB、戦績、録画履歴、デッキ、タグ、シーズン、設定、バックアップ操作を更新後も通常GUIから扱えることを優先します。

V2.2.2では、V1.x時期の設定画面構成を確認し、録画設定、YouTube連携、管理データ、CSV入出力、表示、アプリ更新の入口をPySide6設定画面へ復旧しました。アプリ更新タブは現在バージョン、確認中、最新、更新候補あり、失敗を区別し、更新候補がある場合だけダウンロードボタンを有効にします。GUI smokeとWindows GUI EXE smokeにも設定画面の復旧契約と更新タブ状態契約を追加しました。DB schema、設定形式、録画、queue、manifest、OAuth資格情報は変更しません。

V2.2.0では、統計の日付入力をカレンダーポップアップ付きにし、勝利数・勝率推移を棒と線のグラフとして表示します。戦績管理、統計内訳、デッキ名、タグ、シーズンの表は列幅、横スクロール、行高、選択色を調整し、デッキ/タグのカラー列は色見本として確認できるようにしました。V2.2.0ではDB schema、設定形式、録画、queue、manifest、OAuth資格情報を変更しません。

V2.2.1では、アプリ内更新がGitHubの`latest` 1件だけに依存せず、Release一覧からGUI EXE、updater EXE、SHA-256が揃った最新安定版を選ぶようにしました。配布資産のないReleaseが存在しても更新確認を壊さず、配布可能なReleaseだけを更新候補にします。

## V2.2.0のPySide6 UI読み取り品質改善

- 統計画面の開始日・終了日は、直接入力とカレンダーポップアップの両方で指定できます
- 統計の「勝利数・勝率推移」は、期間ごとの勝利数を棒、累積勝率を線として表示します
- 戦績管理、デッキ名、タグ、シーズン、統計内訳の表は、列幅と横スクロールの意図を持たせ、長い日本語デッキ名や説明を読み取りやすくしました
- 表の選択色は強すぎない色へ調整し、選択中も文字を読めるようにしました
- デッキ/タグのカラー列は、色コードだけでなく実際の色見本として確認できます
- DB schema、設定形式、録画、queue、manifest、OAuth資格情報は変更しません

V2.0.2では、1.x相当の15標準機能を「画面部品が存在する」だけでなく「ユーザーが実操作として到達できる」状態で確認しました。GUI smokeと回帰テストの実操作チェックを強化し、その結果を録画後ワークフロー、データ保全表示、PySide6通常入口化ゲートへ反映しました。V1.4.x open Issue棚卸しでも、V2.0.2のBackward Compatibilityを妨げる直接ブロッカーは確認していません。

CSV移行は適用前バックアップと単一DBトランザクションで既存データを保護します。単体音声はWindows build 20348以上が対象で、非対応・ゲーム未起動・ヘルパー障害時には別音源へ無断で切り替えず、警告を残して映像のみ継続します。YouTube OAuth資格情報はOS資格情報ストアだけに保存し、設定、manifest、queue、標準出力、ログへ保存しません。

Master Duel単体音声ではDirectShow音声入力欄を使いません。設定画面では「DirectShow入力は未使用」と表示し、録画完了後に出力ファイルへ音声ストリームがない場合は、録画を削除せず履歴の音声警告として確認できます。

## V2.0.1の通常配布GUI復旧

- `master-duel-recorder-lite-gui.exe`は1.x相当のTkinter GUIを起動します
- PySide6レビュー入口`master_duel_recorder_lite.pyside_review`とPySide6シェル検証入口`master_duel_recorder_lite.pyside_gui`は残します
- GUI smokeでは1.x標準機能15項目のwidget充足を確認します
- 更新後に既存データが見えない場合に備え、GUI smokeは参照中のruntime dataとSQLite DBパスを出力します
- DB schema、設定形式、録画ファイル、queue、manifest、OAuth資格情報は変更しません
- PySide6を通常配布入口へ戻す条件は[PySide6機能同等性ゲート](docs/architecture/pyside-feature-parity-2.0.1.md)を参照してください

## V2.0.2の15標準機能実操作確認

- 15標準機能の実操作チェックをGUI smokeへ追加しました
- 戦績管理から未完了処理、再生、編集、タイムライン、診断、YouTube投稿へ進む導線をsmoke契約で確認します
- データ保全表示では、バックアップと復元が管理DBと設定を対象にし、録画ファイル、queue、manifest、OAuth資格情報を変更しないことを明示します
- PySide6通常入口化ゲートを、widget存在確認ではなく実操作確認へ拡張しました
- V1.4.x open Issue棚卸しは[#548](https://github.com/Tao-pyth/master-duel-recorder-lite/issues/548)としてV2.0.2完了前に確認します
- PySide6通常入口化、DB schema変更、設定形式変更、録画・queue・manifest・OAuth資格情報の削除や初期化は対象外です

## V2.0.3の読み取り品質改善

- 戦績管理のデッキ名列で、色スウォッチと長い日本語デッキ名が重なりにくい余白へ調整しました
- 統計の勝利数・勝率推移は、棒を期間ごとの勝利数、線を累積勝率として表示します
- シーズンレポートの推移表でも、期間ごとの対戦・勝敗と累積勝率を分けて確認できます
- シーズンレポートの内訳には全体行を追加し、0件の未設定行と最終勝敗の重複内訳は標準表示から外します
- DB schema、設定形式、録画、queue、manifest、OAuth資格情報は変更しません

## V2.0.0のPySide6 GUI

- `master-duel-recorder-lite-gui.exe`はV2.0.0でPySide6 GUIを起動していましたが、V2.0.1で通常入口から外しました
- 主要ナビは録画、戦績管理、統計、デッキ名、タグ、シーズン、YouTube、信頼性、設定です
- `prepare`と`improve`は独立ナビへ戻さず、YouTube投稿、戦績管理、統計、設定の導線へ統合します
- 旧Tkinter GUIは互換モジュール`master_duel_recorder_lite.gui`として残していましたが、V2.0.1では通常配布入口へ戻します
- V2.0.0ではDB schema、設定形式、録画ファイル、queue、manifest、OAuth資格情報を変更しません
- 詳細は[PySide6 GUI移行設計](docs/architecture/pyside-gui-2.0.0.md)を参照してください

## V1.6.0の録画後ワークフロー情報設計

- 戦績管理を録画後の整理、振り返り、投稿準備へ進むハブとして定義しました
- MP4準備と改善の内部ページは、V2.0.0で独立ページとして単純移植せず、YouTube投稿、戦績管理、統計、設定へ分解する方針にしました
- 設定画面を通常設定、外部連携、データ保護、危険操作、診断の領域へ分ける前提を整理しました
- 戦績入力、未完了処理、タイムライン、レビュー、YouTube投稿、診断のダイアログ導線をワークフロー単位で定義しました
- V2.0.0のスクリーンショット回帰では、空データとデータ入りの両方に加え、録画後フロー単位の操作スモークを確認する方針にしました
- 詳細は[録画後ワークフロー情報設計](docs/architecture/post-recording-workflow-1.6.0.md)を参照してください

## V1.5.2のデッキ名表示とUI baseline

- デッキ名管理に「使用回数」列を追加し、自分デッキ・相手デッキの合算利用回数を表示します
- デッキ名一覧と入力候補は、使用回数が多い順、同数時は名前順で表示します
- 戦績管理のデッキ名列では、デッキ色を小さい枠線付き四角スウォッチとして表示します
- 明るいデッキ色には黒枠、暗いデッキ色には白枠を使い、背景に埋もれにくくします
- PySide6全面移行前のTkinter主要画面、内部ページ、ポップアップ導線を画像と要件で保存しました
- DB schema、設定形式、録画ファイル、YouTube OAuth資格情報、prepare queue、manifestは変更しません

## V1.5.1の操作調整

- タグ管理では、デッキ分類用タグを「デッキ名登録でのみ使用」と表示します
- 戦績管理の上部操作は1行に整理し、「未完了を処理」「一括編集」「手動追加」の順で表示します
- フィルター未適用時は絞り込み解除ボタンを無効表示にします
- デッキ名の色は、デッキ名列先頭の色付きドットで表示します
- テンプレート画面で、YouTube投稿のタイトル、概要欄、タグを1件だけ保存できます
- テンプレートでは`{deckname}`などの変数を使い、戦績管理の情報を投稿時に反映できます
- YouTube投稿済み録画では投稿ダイアログに「Youtubeリンク」を表示し、投稿欄を無効化してリンクを開けます
- 簡易入力では勝敗、先後、コイントス、相手デッキを入力し、自分デッキとシーズンは前回値候補として保持します

## V1.5.0のレビュー基盤

- 対戦記録編集画面から、同じ録画IDのPySide6レビュー画面を別プロセスで起動できます
- PySide6が利用できない場合は、既存のWindows既定プレイヤー再生へ戻れます
- `mdrl review show RECORDING_ID --json`で、録画概要、動画参照、タイムライン、戦績概要、YouTube URL、クリップ候補をGUI非依存データとして確認できます
- `mdrl review status`で、現在の環境でPySide6レビュー画面を起動できるか確認できます
- レビュー画面のマーカー追加とクリップ出力は既存サービス層を通し、DB schema、設定形式、録画ファイル、OAuth資格情報の保存契約は変更しません

## V1.4.6の戦績編集とデッキタグ管理

- 戦績管理画面上部にあった録画詳細確認用の操作を、対戦記録編集画面下部へ集約しました
- 対戦記録編集画面で、録画有無、録画ファイル欠損、YouTube連携有無を確認できます
- YouTube投稿済みの記録では、編集画面下部にYouTubeリンクが表示され、「開く」からブラウザで確認できます
- デッキ名にタグを付けられるようにし、デッキ管理側で分類を保存できます
- タグ管理に「デッキ名登録でのみ使用」を追加し、このタグは戦績入力時のタグ候補には表示しません
- 既存の戦績、録画、YouTube投稿履歴はDB移行後も保持します

## V1.4.5のYouTube OAuth資格情報ストアhotfix

- 配布EXEからWindows資格情報ストアのYouTube OAuth資格情報を確実に読み取り、GUIの`接続確認`と投稿ダイアログで未連携扱いにならないようにしました
- 資格情報の読み取り、保存、削除はWindows Credential APIを直接呼び、PyInstaller EXEでの`win32cred`差異に依存しないようにしました
- 資格情報が存在しない状態と、Windows API失敗や保存済みJSON不正を区別して表示します
- `youtube disconnect`は削除失敗を成功扱いにせず、配布EXEスモークでは隔離targetで読み取りと削除を検証します
- `refresh_token`、`access_token`、認可コードは引き続き配布物、設定、DB、queue、manifest、ログへ含めません

## V1.4.4のYouTube OAuth hotfix

- 配布EXEへYouTube OAuth `client_id`と`client_secret`を同梱し、GUIの通常連携でtoken交換に`client_secret`を送信します
- ユーザーに`client_secret.json`選択やGoogle Cloud Console操作を要求しない通常導線を維持します
- releaseビルドでは`MDRL_YOUTUBE_OAUTH_CLIENT_ID`と`MDRL_YOUTUBE_OAUTH_CLIENT_SECRET`の両方を必須にします
- `refresh_token`、`access_token`、認可コードは引き続き配布物、設定、DB、queue、manifest、ログへ含めません
- `client_secret`は配布EXE内assetとOS資格情報ストアに限定し、エラー診断では伏せます

## V1.4.3のアプリ内自動更新hotfix

- アプリ内更新は手動ダウンロードへ退避せず、GUIへ同梱した専用`master-duel-recorder-lite-updater.exe`で自動適用します
- updaterは旧GUI終了待機、候補SHA-256再確認、`.previous`退避、置換後GUIスモーク、失敗時ロールバック、成功時再起動を行います
- GitHub ReleaseではCLI/GUI/updaterの3 EXEとSHA-256を公開し、公開済みassetの再ダウンロード後に3 EXEをスモークします
- release toolingのテストはプロジェクト版から期待値を導出し、Fixリリースごとの固定値更新漏れを防ぎます
- YouTube OAuth client_id同梱、OAuth 400診断、Windows CredentialBlob互換修正は維持します

## V1.4.2の更新・OAuth hotfix

- アプリ更新でGUI EXEを取得した後、置換前に`--smoke-test`で起動検証し、Python DLL展開失敗などの起動不能EXEを適用しません
- GitHub Releaseの公開後に、公開済みassetを再ダウンロードしてCLI/GUIスモークを実行します
- YouTube OAuth token交換のHTTP 400はGoogleのエラー本文を読み取り、secretやtokenを伏せた診断と次の確認事項を表示します
- V1.4.2の配布ビルドでは、更新されたYouTube OAuth `client_id`だけをGitHub Secretから同梱します。V1.4.4で`client_secret`も同梱する方針へ修正しました
- DBスキーマ、設定形式、OAuth資格情報保存先、録画ファイル、prepare queueは変更しません

## V1.4.1のYouTube導線hotfix

- 配布EXEへYouTube OAuth client_idを同梱できるようにし、releaseビルドでは未設定を検出します
- OAuth client_id未設定時はGUIで連携開始ボタンを無効化し、このビルドでは連携を開始できない理由を表示します
- `MP4準備`は独立した主要ナビではなく、録画履歴からのYouTube投稿フロー内で投稿前処理として扱います
- `改善`は未成熟な主要ナビとして露出せず、録画なし戦績追加など必要な操作は戦績管理側から使います
- 改善状態更新で`list_history()` positional argument例外が出る回帰を修正します

## V1.4.0のYouTube一般配布導線

- GUIの`設定`に`YouTube`タブを追加し、連携状態、連携、切断、接続確認、最新録画でのprivateテスト投稿を操作できます
- `mdrl youtube connect`は配布者管理OAuth Clientが設定されている場合、既定ブラウザと`127.0.0.1:<random port>`のloopback callbackで認証コードを自動受信します
- 開発者向けfallbackとして`--client-secrets`と`--authorization-code`は残しますが、通常ユーザー導線では`client_secret.json`を要求しません
- 録画履歴で録画を選択し、タイトル、概要欄、タグ、公開範囲を確認してYouTubeへ投稿できます。既定は`private`で、`public`は追加確認を行います
- 401、403、quota/rate、5xx、通信断、不明応答を分類し、再認証、quota待ち、権限確認、再試行、手動確認の次アクションを表示します
- 配布前には配布者管理のGoogle OAuth Client、OAuth同意画面、プライバシーポリシー、必要に応じたGoogle OAuth verificationが必要です

## V1.2.0の信頼性機能

- `mdrl reliability check`で録画環境、Master Duelウィンドウ、利用者向け理由をまとめて確認できます
- `mdrl reliability wizard`でFFmpeg、保存先、録画対象、音声、事前チェック、テスト録画、再生確認の導入状態を確認できます
- `mdrl reliability analyze PATH --mode replay`で既存動画やリプレイ録画を読み取り専用で後解析できます
- `mdrl reliability hotkeys`で録画開始/停止、マーカー追加、自動監視切替のショートカットとトレイ設定を確認できます
- GUIには「信頼性」ページを追加し、30秒事前チェック、初回導入、ホットキー・トレイの状態入口をまとめました
- V1.2.0の設定追加は`[interaction]`の非シークレット項目だけで、既存設定は既定値補完により手動移行不要です

## V1.3.0の改善支援

- `mdrl improve suggest`で最近値、頻出値、タグから戦績入力候補を表示します
- `mdrl improve tag-template create --name NAME --tag TAG`でタグテンプレートを作成できます
- `mdrl improve goals create --title TITLE --metric METRIC --target 10`で練習目標を作成できます
- `mdrl improve deck-view`で自分デッキ別に対面、先後、コインを集計します
- `mdrl improve storage`で失敗録画や戦績未入力録画などの整理候補を表示します。自動削除はしません
- `mdrl improve migration-export PATH`で設定と管理DBの移行パックを作成します。録画ファイルとOAuth資格情報は含めません
- V1.3.0ではGUIに「改善」ページを追加しました。V1.4.1以降、未成熟な主要ナビとしては露出せず、必要な操作は戦績管理などの文脈へ整理します
- 履歴DBはV16へ移行し、`tag_templates`と`practice_goals`を追加します。移行前バックアップと失敗時ロールバックの既存契約を維持します

## V1.1.0のYouTube連携

- `mdrl youtube connect`でYouTube Data APIの`youtube.upload`スコープを接続し、資格情報をOS資格情報ストアへ保存します。V1.4.0以降の通常導線は既定ブラウザとloopback callbackで認証を完了します
- `mdrl youtube upload RECORDING_ID --title TITLE`で完了済み録画をMP4準備し、既存prepare結果があれば再利用してYouTubeへ投稿します
- 投稿状態、試行回数、失敗理由、動画ID、視聴URLを履歴DBの`youtube_uploads`へ保存します
- `mdrl history list`と`mdrl history show`では完了済みYouTube投稿URLを録画IDと一緒に表示します
- `mdrl youtube clip RECORDING_ID --elapsed-ms 30000`で元録画を壊さず`user_data/data/exports/`へ投稿用クリップを出力します
- `mdrl youtube materials RECORDING_ID`でタイトル、概要欄、タグ、投稿チェックリスト、素材出力先をOAuth未接続でも生成できます
- 既定公開範囲は`private`で、明示した場合だけ`unlisted`または`public`を使えます

## V1.0.3の操作改善

- 設定の「録画設定」で既存の`ffmpeg.exe`を直接選択でき、導入先が空フォルダの場合もセットアップできます
- 「保存先を変更」は処理停止中だけ実行でき、移動前バックアップ、SQLite検査、全データ複製、実行時ルート切替を行います。成功後も旧保存先は自動削除しません
- MP4準備は録画IDではなく、開始日時・デッキ・勝敗・ファイル名を含む候補から選択します
- 戦績管理の列メニューでコイン、対戦種別、時間、サイズ、相手デッキを表示・非表示にできます。相手デッキは初期非表示です
- 設定の「表示」で勝敗、先後、コイン、登録元ごとの背景色を指定できます。既定値は白です
- 設定の「表示」で戦績管理一覧のダブルクリック動作を「録画再生」または「戦績編集」へ切り替えられます。既定値は従来互換の録画再生です
- 「未完了を処理」は連続処理画面内で勝敗、先後、自分デッキ、シーズンを入力でき、保存後も次の未完了戦績へ進みます
- 統計などの日付カレンダーは、前月、年月、今月へ、翌月を7列グリッドの1:3:2:1で配置し、曜日と日付セルを等幅で表示します
- 簡易入力、未完了戦績処理、詳細編集、一括編集では、勝敗、先後、状態、コイン、対戦種別などの固定択一項目をプルダウンではなくボタンで選択できます
- 一括編集ではシーズン、自分デッキ、対戦種別、タグに加えて、コインの表・裏・未設定をまとめて更新できます
- 設定の「アプリ更新」はGitHubの正式Releaseだけを確認し、明示操作でGUI EXEを取得・SHA-256検証してから次回起動用に置換します

## クリーンアンインストール

GUIでは`設定`の`管理データ`タブにある「アンインストール」から、現在使用中のデータ保存先を確認して完全削除できます。録画と自動監視を停止し、必要な戦績や録画を別の場所へ書き出した後、確認語`アンインストール`を入力してください。配布GUIでは「この起動EXEも終了後に削除する」を選べます。

CLIでは対象を表示するだけなら`mdrl uninstall`、実行する場合は`mdrl uninstall --yes --confirm アンインストール`を使用します。配布CLI自身も削除する場合は`--remove-executable`を追加します。既定EXEの削除対象は`%LOCALAPPDATA%\MasterDuelRecorderLite`全体です。外部に手動導入したFFmpeg、別フォルダへ移動した動画、旧版で別の場所に残っている`user_data`は自動削除しません。Windows one-file版は一時クリーナーを次回再起動時に削除するため、Windows再起動まで一時ファイルが1件残る場合があります。

開発計画は [docs/roadmap.md](docs/roadmap.md)、バージョンごとの変更は [docs/release-notes.md](docs/release-notes.md)、リリース確認は [docs/release-checklist.md](docs/release-checklist.md) を参照してください。ロードマップ作業は実装前にGitHub Issueへ登録し、バージョンラベルとMilestoneへ接続します。

## 重要方針

- OBS PluginとOBS WebSocketには依存しない
- Pythonを中心に保守しやすい責務へ分離する
- 録画データ、設定、認証情報、履歴、キュー、ログを `user_data/` に分離して保護する
- ゲーム画像、テンプレート画像、配布できないゲーム素材をリポジトリに含めない
- 未完了録画は自動修復せず`failed`へ確定し、録画ファイルと失敗診断を保持する
- YouTube OAuth資格情報はOS資格情報ストアだけに保存し、設定、manifest、queue、ログへ混入させない
- GUIとCLIを同じアプリケーションサービスへ接続し、録画・履歴・戦績の規則を共通化する

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

通常は`master-duel-recorder-lite-gui.exe`をダブルクリックし、録画対象と必要な録音元を選択して診断を実行します。録画アイコンは即時に手動録画し、自動監視はMaster Duel日本語UIで対戦開始を確認してから録画します。CLIを使う場合は次の順で実行します。

```powershell
.\master-duel-recorder-lite.exe --version
.\master-duel-recorder-lite.exe config init
.\master-duel-recorder-lite.exe doctor
```

EXEにはPythonランタイムを含みますが、FFmpegは含みません。GUIでFFmpegが見つからない場合は「FFmpegのセットアップ」が自動表示されます。配布元、GPLv3ライセンス、ダウンロードURL、インストール先を確認し、「インストール」を選ぶと、FFmpeg公式サイトが案内するGyan FFmpeg Buildsのrelease essentials ZIPを取得します。公開SHA-256を照合し、FFmpeg 6.0以上を確認できた場合だけ設定へ保存します。キャンセルした場合は何も導入しません。手動導入してPATHへ追加するか、設定画面または`config set recorder.ffmpeg_path`で既存のFFmpegを指定することもできます。

EXEはコード署名されていないため、Windows SmartScreenが警告する場合があります。GitHub Releaseの公開元、SHA-256、必要に応じて`gh attestation verify master-duel-recorder-lite.exe --repo Tao-pyth/master-duel-recorder-lite`でbuild provenanceを確認してください。確認できないEXEは実行しないでください。

EXE実行時の既定データ保存先は`%LOCALAPPDATA%\MasterDuelRecorderLite`、GUIから導入するFFmpegの既定先はその配下の`tools\ffmpeg`です。設定画面の「保存先を変更」で別フォルダへ安全に切り替えられます。EXEを置いたフォルダには作業フォルダを作りません。V0.16.0以前にEXEと同じフォルダで作成された`user_data/`は自動移動も削除もしません。既存データを継続利用する場合は、バックアップ後に新しい既定先へ内容を移すか、`MDRL_USER_DATA_DIR`または`--user-data-dir`で旧フォルダを明示してください。

詳細は[Windows EXE配布設計](docs/architecture/windows-distribution.md)と[アプリケーション更新設計](docs/architecture/app-update.md)を参照してください。

## 開発者向けセットアップ

Python 3.11以上が必要です。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pip install -e ".[review]"
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
python -m master_duel_recorder_lite review show RECORDING_ID --json
python -m master_duel_recorder_lite review status
python -m master_duel_recorder_lite review launch RECORDING_ID --fallback-external
python -m master_duel_recorder_lite prepare list
python -m master_duel_recorder_lite prepare run
python -m master_duel_recorder_lite reliability check
python -m master_duel_recorder_lite reliability wizard
python -m master_duel_recorder_lite reliability hotkeys
python -m master_duel_recorder_lite improve suggest
python -m master_duel_recorder_lite improve deck-view
python -m master_duel_recorder_lite improve storage
python -m master_duel_recorder_lite youtube account
python -m master_duel_recorder_lite youtube materials RECORDING_ID
python -m master_duel_recorder_lite youtube upload RECORDING_ID --title "対戦記録"
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

`status`は録画環境、実行状態、履歴整合性、準備キューを一度に診断します。JSONはスキーマ版2を持ち、秘密情報と実行時データの絶対パスを含めません。終了コードは`0`が成功、`2`が設定・引数・環境不備、`3`が処理失敗、`4`が要確認状態、`130`が正常完了として扱えない処理のCtrl+C中断です。詳細は[設定・運用CLI設計](docs/architecture/cli.md)と[V1候補E2Eチェックリスト](docs/e2e-checklist.md)を参照してください。

## 録画環境の確認

FFmpeg 6.0以上、または同等のlibavutil 58以上を含むnightly buildが必要です。GUIでは初回セットアップを利用できます。CLI利用時または手動で管理したい場合はFFmpegを導入した後、次のコマンドで録画能力、入力設定、保存先、空き容量を確認します。

```powershell
python -m master_duel_recorder_lite doctor
python -m master_duel_recorder_lite list-inputs
```

`doctor` は警告だけなら終了コード `0`、録画を開始できない問題があれば終了コード `2` を返します。詳細は [録画環境の初期化設計](docs/architecture/recording-environment.md) を参照してください。

## 最小録画

録画前に `doctor` を実行します。GUIの音声入力では「Master Duelのみ（推奨）」「PC全体」「入力デバイス」「音声なし」を選択できます。推奨モードはゲームPIDと子プロセスの音声だけを取得するため、映像にデスクトップやモニターを選んでもブラウザや通知音を含めません。PC全体と入力デバイスはWindowsの`dshow`入力を1つ選択します。同時ミックスは対象外です。詳細は[音声入力設計](docs/architecture/audio-input.md)を参照してください。

```powershell
python -m master_duel_recorder_lite record --duration 10
python -m master_duel_recorder_lite record
```

時間を省略した場合はCtrl+Cで正常停止します。録画は `user_data/data/recordings/YYYY/MM/DD/` 配下へ保存します。既存ファイルは上書きしません。Master Duel対象では、プロセス名とタイトル条件に一致する可視・非最小化ウィンドウのうち面積が最大のものをPID・Windowsハンドルで固定し、DPI補正したクライアント領域を`gdigrab desktop`へ渡します。`title=`入力は3440x1440フルスクリーンで静止フレームとなる実機障害が確認されたため、Master Duel録画には使いません。モニター対象ではOSが返す座標とサイズを使います。任意ウィンドウやデスクトップ全体も明示選択できます。録画前に対象名を確認してください。詳細は [録画対象の選択設計](docs/architecture/capture-targets.md) と [最小録画設計](docs/architecture/recording.md) を参照してください。

## Master Duel向け録画補助

状態だけを確認する場合は `watch --once`、継続監視と自動録画を行う場合は `watch` を実行します。

```powershell
python -m master_duel_recorder_lite watch --once
python -m master_duel_recorder_lite watch
```

既定では`masterduel.exe`の可視・非最小化ウィンドウを特定し、DPI補正したクライアント領域の画面座標を`gdigrab desktop`から単一の常駐FFmpegで640px幅・最大2fps取得します。手動録画対象にモニターや別ウィンドウを選んでいても、自動監視と自動録画はMaster Duel領域を使用します。座標取得のためフォーカスを失っても監視は継続しますが、別ウィンドウがMaster Duelの上へ重なると判定対象と保存動画に映ります。自動録画中は他ウィンドウをMaster Duel上へ重ねないでください。

コイントスが直近4フレーム中2件で合意すると候補録画を開始し、見逃した場合は安定盤面が直近5フレーム中3件で合意した時点から候補録画を開始します。盤面確定前のマッチエラー、リプレイ、45秒タイムアウトでは候補録画と仮履歴を破棄します。結果は直近4フレーム中2件で合意し、3秒の後余白後に停止して勝敗・先後を保存します。単発の欠落や一時オーバーレイだけでは合意状態を全消去しません。

GUIは録画状態を色付きの表示帯で示します。青の「自動監視中 | 録画待機」は動画を保存していない待機状態、黄の「自動監視中 | 録画中（対戦確認中）」は盤面確定前の候補録画、赤の「自動監視中 | 録画中（対戦記録中）」は盤面確定後の本録画です。色だけでなく文言でも録画の有無を判別できます。

GUIは取得元、解像度、表示プロファイル、実効fps、状態、`coin/board/turn/result/error/replay/overlay/loading`スコア、合意数、ストリーム再起動数を表示します。敗北結果は攻撃・召喚演出との混同を避けるため、信頼度や盤面表示にかかわらず直近5フレーム中4件の合意で確定します。数値診断は`user_data/logs/visual-monitor/`へ1秒1件、1セッション900件、最新10セッション、合計2MiBを上限として原子的に保存し、候補イベントの勝敗・先後・根拠も記録します。診断には画像、BMP、ウィンドウタイトル、動画パスを含めません。対応表示は日本語UIの1920x1080ウィンドウと3440x1440フルスクリーンで、ボーダーレスは対象外です。`record`による手動録画は画面判定から独立しています。詳細は[Master Duel向け録画補助設計](docs/architecture/detection.md)と[基本イベント自動判定設計](docs/architecture/visual-event-detection.md)を参照してください。

開発時の実戦連続試験は、試験開始前にPowerShellで`$since = (Get-Date).ToString("o")`を実行し、対戦後に`python scripts/validate_live_monitoring.py --since $since --required-consecutive 3`または`--required-consecutive 10`で集計します。候補開始、盤面確定、結果停止、停止後の監視復帰を1戦単位で判定します。結果停止後から次戦候補開始まで、最大120秒の範囲で盤面スコア0.35以上が3件あれば早期停止疑いとして不合格にし、旧バージョンの診断は試験期間へ含めません。

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

GUI上部には「戦績管理 未完了 N件」を常時表示します。正常完了した録画のうち、対戦記録がない録画と、対戦記録が`confirmed`ではない録画を未完了として数えます。0件は緑、1件以上は注意色で表示し、表示を押すと録画履歴を開きます。録画失敗・録画中・候補破棄は件数に含めません。

GUIの一覧には開始日時、デッキ名とカラー、勝敗、先後、コインの表裏、対戦種別、時間、サイズ、相手デッキ、登録元を表示し、録画IDは診断画面だけに表示します。相手デッキは列メニューから任意表示でき、初期状態では非表示です。「フィルター」からシーズン、自分デッキ、相手デッキを単一選択、タグを複数選択でき、条件間はAND、タグ内はORです。「クリア」で即時解除します。行を選択すると上部の再生、対戦記録編集、Explorer、削除アイコンが有効になります。ダブルクリックは設定により録画再生または戦績編集へ切り替えられ、録画なし・編集不可の行では既存ボタンと同じ可否判定に従います。削除は確認後に元録画、対戦記録、タグ関連、タイムラインを一括削除します。録画または自動監視の実行中は削除できません。`history check`は不整合を報告するだけでファイルを変更しません。詳細は[録画履歴設計](docs/architecture/history.md)と[録画の閲覧設計](docs/architecture/recording-browsing.md)を参照してください。

## データ保全

設定の「管理データ」では、SQLite履歴DBと設定を検証済み`.mdrl-backup`として作成し、作成日時、作成契機、DB版、サイズ、保護状態を確認できます。録画ファイルはバックアップへ含めません。通常世代は最大20世代・合計256MiBで管理し、DB移行と復元直前の保護世代は通常ローテーションから除外します。

復元前にバックアップのSHA-256、SQLite整合性、スキーマ版と管理データ件数を別パスで検証し、現在値からの件数差を確認後だけ切り替えます。途中失敗時は元DBと設定へ戻し、録画には触れません。統合診断は設定、DB、録画参照、未登録録画を読み取り専用で確認します。欠損録画は履歴から録画保存先配下のmkv/mp4へ再関連付けでき、重複戦績候補は比較後に両方保持、個別編集、片方削除を選べます。自動修復・自動統合は行いません。詳細は[データ保全設計](docs/architecture/data-protection.md)を参照してください。

## 対戦記録

録画履歴の「対戦記録」から、状態、勝敗、先後、対戦種別、自分デッキ、相手デッキ、複数タグ、メモを後から何度でも編集できます。状態・勝敗・先後・対戦種別は日本語表示ですが、SQLiteとCLIでは互換性のある英語コードを保持します。自分デッキと相手デッキは共通のデッキ名一覧から選択でき、一覧にない日本語名も直接入力できます。タグも候補から追加または自由入力でき、保存した新規名は次回以降の候補になります。

サイドメニューの「デッキ名」と「タグ」は個別画面です。どちらにも説明とカラーを設定でき、一覧のカラー列には色コードと実際の色見本を表示します。デッキの「相手デッキのみで使用」は自分側の新規候補から除外し、「履歴・統計の選択肢で非表示」は両者の新規候補と絞り込み候補から除外します。後者を自分デッキとして使った戦績は全体勝率を含む統計対象外ですが、相手デッキだけに使った戦績は集計します。過去記録と現在値は保持します。参照中の項目は安定IDを保ったまま名前変更し、削除時は過去記録を壊さないようアーカイブします。詳細は[対戦記録管理設計](docs/architecture/duel-records.md)を参照してください。

## 戦績統計

サイドメニューの「統計」では、確定済みで勝敗が入力された正常完了録画だけを集計します。全体勝率、条件適用後勝率、条件適用後の先攻時・後攻時勝率を上部に表示します。期間はカレンダーまたは直接入力で指定でき、ローカル日付の両端を含み、シーズン、デッキ、タグ、先攻・後攻と同時に絞り込めます。タグは名称ではなく安定IDで関連を追跡します。

勝率は`勝利数 / (勝利数 + 敗北数 + 引分数)`です。引分は対戦数へ含め、未確定、勝敗未設定、録画失敗、録画中の履歴は統計へ含めません。推移タブは日・週・月単位の勝利数を棒、勝率を線で表示し、「デッキ別全体」と「デッキ先後別」では対戦数、勝敗、引分、勝率を比較できます。詳細は[戦績統計設計](docs/architecture/duel-statistics.md)を参照してください。

## 戦績CSV移行

設定画面の「CSV入出力」タブでは、固定11列の戦績CSV出力、取込、サンプル出力を実行できます。列順は`ID,開始日時,自分デッキ名,相手デッキ名,勝敗,先後,コイン,対戦種別,シーズン,タグ,メモ`です。既存IDは録画関連と登録元を維持して更新し、空または未知IDは新しいIDを発行して「取込」戦績として登録します。未登録のデッキ、タグ、シーズンはプレビューへ追加予定を表示し、確認後だけ作成します。

CSVはUTF-8 BOMとCRLFを使用し、カンマ、引用符、メモ改行を標準CSV規則で保持します。不正行が1件でもあれば一切適用せず、取込直前に検証済みバックアップを作成して全変更を単一トランザクションで確定します。詳細は[戦績CSV設計](docs/architecture/duel-csv.md)を参照してください。

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

自動監視から開始したMaster Duel録画では、日本語UI固有の相対ROI特徴と状態機械を録画処理と別スレッドで実行します。開始、ターン、勝敗はタイムライン候補として保存し、利用者が確認・却下できます。リプレイはライブ録画開始の対象外ですが、オフライン解析ではタイムライン抽出対象です。「録画開始」またはCLIの`record`による手動録画では判定ワーカーを起動しません。実画面での確認は[基本イベント自動判定 実画面チェックリスト](docs/visual-detection-checklist.md)を使用します。

## シーズン管理

サイドメニューの「シーズン」で、ランク、イベント、カスタムのシーズン名、対象対戦種別、開始日・終了日、説明、レポートメモを管理します。期間重複は許可し、対戦記録1件へ最大1シーズンを手動で割り当てます。録画日が期間外でも警告確認後に保存できます。参照中のシーズンは削除せずアーカイブします。

シーズンを選択すると、同種別の直前シーズンまたは任意シーズンとの対戦数・勝率差、デッキ別の全体・先攻・後攻・未設定、コインの表裏・勝敗、日・週推移、使用デッキ比率を最新データから集計します。シーズンIDと開始・終了日の双方が一致する確定戦績が母集団で、10戦未満には少数標本の注意を表示します。比較対象なし・母数0の勝率差は算出不可とし、未知値を推測しません。

振り返りには既存の自由メモを保持したまま、目標、良かった点、課題、次期方針を保存できます。競合更新は上書きせず、参照中シーズンはレポート確認と保存後の明示操作だけでアーカイブします。統計値は保存せず常にライブ集計します。外部CDN・画像・JavaScript・絶対パスを含まない印刷可能な単一HTMLへ、上書き確認と原子的保存付きで出力できます。詳細は[シーズン管理設計](docs/architecture/seasons.md)と[シーズンレポート設計](docs/architecture/season-reports.md)を参照してください。

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
