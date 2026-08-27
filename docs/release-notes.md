# リリースノート

## V2.7.1: PySide6実操作UI Hotfix - 2026-08-28

- 主要操作ボタンのQt標準アイコンを、アプリ内で描画する線画アイコンへ置き換え、画面全体のアイコン表現を統一した
- 統計画面の勝利数・勝率推移グラフで、上部ラベル、日付ラベル、凡例が重ならないように描画領域を分けた
- 戦績管理などの表で、選択や操作後に行高が変わらないよう固定行高へ変更した
- 設定の戦績表示色テーブルに「変更」列を追加し、色選択ダイアログから既存UI設定へ保存できるようにした
- シーズン入力を、名前1行、種別・開始日・終了日1行に整理し、3入力の幅を揃えた
- シーズン一覧の種別を `ranked` / `event` ではなく「ランク戦」「イベント」「カスタム」で表示するようにした
- SQLite schema、設定schema、録画ファイル、queue、manifest、OAuth資格情報は変更しない

## V2.7.0: 視覚タイムラインMVP - 2026-08-27

- アプリ内レビュー画面に視覚タイムラインMVPを追加し、録画長に対する現在位置、選択位置、タイムラインイベント位置を確認できるようにした
- 視覚タイムラインのイベント選択と表形式タイムラインの選択を、既存の動画シークへ同期するようにした
- `duel_start`、手動マーカー、候補イベント、通常イベントを識別できる表示種別をReview ViewModelへ追加した
- 録画長不明、イベントなし、範囲外イベントでも保存データを変更せず扱えるようにした
- PySide6 smoke contractへ、視覚タイムラインWidget、表示種別、選択同期、fallback安全性の契約を追加した
- クリップ範囲エディタ、波形/サムネイル、実録画codec差異の網羅検証、DB schema、設定形式、長時間バックグラウンド録画、任意クリップ保存、OAuth、queue、manifestは変更しない
- Ruff、全609 pytest、全610 unittest、PySide6 GUI smoke、CLI/GUI/updater EXEビルド、3 EXE smokeに合格した
- GitHub Actions run `33088497995` でmain CIに合格し、run `33088520685` でWindows EXE release workflow、3 EXE smoke、公開asset検証、ダウンロード後EXE smokeに合格した
- GitHub Release `v2.7.0` として公開し、Issue #608-#611 とMilestone `V2.7.0` を完了として閉じた

## V2.6.0: 自動録画プリロールMVP - 2026-08-27

- 自動録画だけを対象に、明示的に有効化した場合だけ短いプリロール映像を録画へ含めるMVPを追加した
- `[detection]`設定へ `preroll_enabled`、`preroll_seconds`、`preroll_max_megabytes` を追加し、既存設定では既定無効で補完するようにした
- PySide6設定画面の録画設定②とCLI設定管理から、プリロールの有効化、秒数、保存上限を確認・変更できるようにした
- 自動監視中だけ `user_data/data/preroll/` 配下へ短い一時segmentを保持し、録画開始時に凍結したsegmentを本録画停止後に結合するようにした
- プリロール結合に失敗した場合も本録画だけを保存し、失敗理由を録画診断へ残すfallbackを追加した
- プリロールを含む録画では、開始候補 `duel_start` を録画ファイル先頭からの経過として保存するようにした
- 手動録画、長時間バックグラウンド録画、任意クリップ保存、SQLite schema、既存録画、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全607 pytest、全608 unittest、PySide6 GUI smoke、CLI/GUI/updater EXEビルド、3 EXE smokeに合格した
- GitHub Actions run `32986621376` でWindows EXE release workflow、Lint/Test、3 EXEスモーク、公開用artifact生成に合格し、GitHub Release `v2.6.0` として公開した
- `python scripts/verify_release_assets.py v2.6.0` と、公開Releaseから再ダウンロードした3 EXE smokeに合格した

## V2.5.0: PySide6全画面の実操作品質監査 - 2026-08-26

- PySide6通常GUIの主要画面について、表示操作を実処理、状態依存の無効化、明確な案内、非表示/削除へ分類する操作契約を追加した
- 録画画面の録画対象更新/保存を、実際の録画対象列挙と設定保存へ接続した
- 録画画面の環境診断保存と診断フォルダ表示を、既存の自動監視診断ZIP保存と `logs/visual-monitor` 表示へ接続した
- 録画画面と内部改善ページの「録画なし戦績追加」を、既存の戦績編集ダイアログと手動戦績作成へ接続した
- 内部MP4準備ページの操作は、MP4変換が通常機能ではなくYouTube投稿時の内部処理であることを明確に案内するようにした
- 設定のクリーンアンインストールを、操作状態確認、削除対象確認、確認語入力付きの既存cleanup worker起動へ接続した
- 設定タブの主要ボタンを共通ボタン生成経路へ寄せ、Qt標準アイコンの適用範囲を広げた
- レビュー画面のタイムライン種別、状態、由来を日本語表示にし、マーカー編集判定用の内部値はQt data roleへ保持するようにした
- レビュー画面の外部再生、マーカー操作、クリップ出力失敗時に、FFmpeg生ログだけではなく利用者向けの短い失敗理由を表示するようにした
- `docs/architecture/pyside-operational-quality-2.5.0.md` と `docs/validation/2.5.0.md` に操作棚卸しと検証結果を記録した
- SQLite schema、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全599テスト、PySide6 GUI smoke、CLI/GUI/updater EXEビルド、3 EXE smokeに合格した
- GitHub Actions run `32940361847` でWindows EXE release workflow、3 EXE smoke、公開asset検証、ダウンロード後EXE smokeに合格し、GitHub Release `v2.5.0` として公開した

## V2.4.4: レビュークリップ出力とRelease検証Hotfix - 2026-08-26

- レビュー画面の「選択位置をクリップ出力」で、FFmpegが一時出力ファイルの形式を判定できず失敗する問題を修正した
- クリップ出力の一時ファイル名を `.partial.mp4` 形式にし、FFmpegへ渡す出力パスが `.mp4` 末尾になるようにした
- Release asset検証スクリプトがGitHub Actionsの `GITHUB_TOKEN` / `GH_TOKEN` を使うようにし、無認証rate limitでRelease workflowが失敗しないようにした
- 最終出力ファイル名、元録画非破壊、原子的な一時出力からの置換は従来どおり維持した
- SQLite schema、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- GitHub Actions run `32870293905` でWindows EXE release workflow、3 EXEスモーク、公開asset検証、ダウンロード後スモークに合格し、GitHub Release `v2.4.4` として公開した

## V2.4.3: レビュークリップ出力Hotfix - 2026-08-26

- 注記: GitHub Release作成後、公開asset検証がGitHub APIの無認証rate limitで失敗したため、正式な完了版としてはV2.4.4で置き換えた
- レビュー画面の「選択位置をクリップ出力」で、FFmpegが一時出力ファイルの形式を判定できず失敗する問題を修正した
- クリップ出力の一時ファイル名を `.partial.mp4` 形式にし、FFmpegへ渡す出力パスが `.mp4` 末尾になるようにした
- 最終出力ファイル名、元録画非破壊、原子的な一時出力からの置換は従来どおり維持した
- 一時出力名の回帰テストを追加した
- SQLite schema、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない

## V2.4.2: PySide6実操作Hotfix - 2026-08-26

- 設定画面を `録画設定①` / `録画設定②` に再分配し、録画品質、音声、自動判定、保存先、録画診断・信頼性を読みやすく整理した
- UI言語設定を `auto`、`ja`、`en` の非編集プルダウンへ変更し、誤入力で保存に失敗しないようにした
- 戦績表示設定、デッキ名、タグのカラー列を枠線付きの大きめスウォッチにし、白や淡色でも見分けやすくした
- 録画画面の開催中シーズン表示を状態パネル化し、開催中かどうかとシーズン名を分けて読めるようにした
- 録画、戦績管理、設定、デッキ/タグ/シーズン編集などの主要操作ボタンへQt標準アイコンを付けた
- テンプレート画面の概要欄を広げ、画面下部の余白を編集領域として使えるようにした
- 戦績管理の編集ボタンから実際のPySide6戦績編集ダイアログを開き、勝敗、先後、コイン、対戦種別、デッキ、タグ、メモ、シーズンを保存できるようにした
- レビュー画面にマーカー編集ボタンを追加し、選択中のmarkerラベルを編集保存できるようにした
- SQLite schema、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全596 pytest、全597 unittest、PySide6 GUI smoke、CLI/GUI/updater EXEビルド、3 EXEスモークに合格した
- GitHub Actions run `32866385225` でWindows EXE release workflow、3 EXEスモーク、公開asset検証、ダウンロード後スモークに合格し、GitHub Release `v2.4.2` として公開した

## V2.4.1: PySide6 UI情報設計Hotfix - 2026-08-25

- テンプレート画面からMP4準備一覧、準備一覧更新、選択録画をMP4準備へ追加、バックグラウンド処理表示を外し、投稿テンプレート編集へ責務を絞った
- YouTube投稿導線では、動画形式の準備を投稿時の内部処理として扱う文言に変更した
- 左ナビから信頼性ページを外し、設定内の「録画診断・信頼性」タブへ統合した
- 録画画面の環境診断から「録画診断・信頼性」へ移動できる導線を追加した
- 録画画面の開催中シーズン表示と左下ステータスを短い状態名へ整理し、補足はツールチップで確認できるようにした
- 設定の「表示」タブを「戦績表示設定」へ変更し、戦績表示色の対象名を内部キーではなく日本語で表示するようにした
- デッキ名、タグ、戦績表示設定のカラー列を、色コードや「色」文字ではなくスウォッチ基準の表示へ統一した
- 戦績管理フィルターで、期間、シーズン、デッキ、タグ、コイン、登録元を一覧クエリへ反映するようにした
- 保存済み条件を選択した場合は既存の条件をクエリへ変換し、複数タグ条件も保存済み条件経由で復旧できるようにした
- デッキ名・タグなどの主要テーブルに安定した高さ契約を追加し、選択や更新で表が不自然に拡大しないようにした
- PySide6 smoke contractと回帰テストへ、信頼性統合、テンプレート非露出、戦績フィルター、カラー表示、表高さの契約を追加した
- SQLite schema、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全593テスト、PySide6 GUI smoke、CLI/GUI/updater EXEビルド、3 EXEスモークに合格した
- GitHub Actions run `32861778037` でWindows EXE release workflow、3 EXEスモーク、公開asset検証、ダウンロード後スモークに合格し、GitHub Release `v2.4.1` として公開した

## V2.4.0: 通常GUI内動画レビュー統合 - 2026-08-25

- PySide6通常GUIの戦績管理「再生」から、録画IDに対応するレビューウィンドウを開けるようにした
- レビューウィンドウで`.mp4`/`.mkv`の動画再生、再生/一時停止、シーク、現在位置表示を扱うようにした
- タイムライン一覧に経過、種別、状態、ラベル、由来を表示し、イベントクリックで動画位置へシークするようにした
- 現在位置マーカー追加と選択位置中心のクリップ出力を、既存の`RecorderApplicationService`経由で接続した
- Qt Multimediaの読み込み失敗、未対応形式、再生エラー時は外部プレイヤーへfallbackする契約を追加した
- PySide6 smoke contractと回帰テストへ、通常GUI内レビュー導線、対応拡張子、fallback、タイムライン列契約を追加した
- SQLite schema、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全591テスト、PySide6戦績管理GUI smoke、CLI/GUI/updater EXEビルド、3 EXEスモークに合格した
- GitHub Actions run `32848688489` でWindows EXE release workflow、3 EXEスモーク、公開asset検証、ダウンロード後スモークに合格し、GitHub Release `v2.4.0` として公開した

## V2.3.0: PySide6 UI実操作回復 - 2026-08-25

- 左下の固定「要確認」を、実診断結果に基づく状態表示へ変更した
- 録画画面の開催中シーズン表示を `active_season_summaries()` へ接続し、該当なしと取得失敗を区別できるようにした
- デッキ名、タグ、シーズン画面で、一覧選択から入力欄へ反映し、追加、保存、削除またはアーカイブを既存サービス層へ接続した
- テンプレート画面から接続、切断、更新、privateテストのボタンを外し、YouTube接続管理は設定画面へ集約した
- 信頼性画面の状態更新と初回導入確認をクリック後に結果表示する操作へ接続した
- YouTube投稿導線をバックグラウンド実行へ接続し、処理中、完了、失敗を画面に戻す状態表示と進捗表示を追加した
- ボタン、入力、プルダウン、日付入力の高さを揃え、日付入力はカレンダーポップアップ付きであることを維持した
- デッキ/タグのカラー列を、色コード文字ではなく色見本と利用者向けラベルで表示するようにした
- PySide6 smoke contractと回帰テストへ、固定状態表示、編集保存、テンプレート画面責務、信頼性ボタン、バックグラウンド処理、UI高さ契約を追加した
- SQLite schema、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全590テスト、PySide6 GUI smoke、スクリーンショット確認に合格した
- GitHub Actions run `32796425233` でWindows EXE release workflow、3 EXEスモーク、公開asset検証、ダウンロード後スモークに合格し、GitHub Release `v2.3.0` として公開した

## V2.2.3: 戦績管理ハブ実操作Hotfix - 2026-08-23

- PySide6戦績管理画面の主要ボタンを、実処理、確認ダイアログ、または明確な案内へ接続し、クリックしても無反応に見える状態を解消した
- 再生、編集、削除、YouTube投稿など行選択が必要な操作は、未選択時に無効化し、選択時に有効化するようにした
- 削除操作は危険操作として表示を分け、確認ダイアログで同意した場合だけ履歴削除へ進むようにした
- 戦績管理一覧の勝敗、先後、コイン、対戦種別、登録元を日本語表示へ変換し、内部コードが利用者向けセル値として露出しないようにした
- 戦績管理フィルターに開始日・終了日のカレンダーポップアップ付き日付入力と解除操作を追加した
- ツールバーの主要操作、補助操作、アイコン操作、危険操作をスタイルとツールチップで区別した
- GUI smoke contractと回帰テストへ、ボタン接続、選択状態、日本語表示、日付ピッカー欠落を検出する契約を追加した
- Release workflowで個別`.sha256`に加え、`SHA256SUMS-v2.2.3.txt`も公開するようにした
- SQLite schema、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全590テスト、PySide6戦績管理GUI smoke、CLI/GUI/updater EXEビルド、3 EXEスモークに合格した
- ローカル通常ビルドCLI EXE SHA-256: `435f6815264bb22159cb56ec3906ae686d42b22a23b4c2bff792181558f8460b`
- ローカル通常ビルドGUI EXE SHA-256: `19b7aa0ea7be8c7143781db8e2b85a7153e491ac4139d2a428a089b1a8311c2e`
- ローカル通常ビルドupdater EXE SHA-256: `417029b9783f54a03e20bd1cb0433acb13f2a6d0972230721824856deaf8a452`
- 公開Release CLI EXE SHA-256: `9f930f06e9addad2fcdbb71e9c45e7da331b3939da14fc653d83fad16afa1e63`
- 公開Release GUI EXE SHA-256: `a387d7ad108219444f80f0bf35b5cf7e6949160fbeeef71e182c026ffa2ccabb`
- 公開Release updater EXE SHA-256: `578d85fab369545e4f83f78cdf6b7bc7efc336fca406585114a1e80427c392e8`
- 追跡: [V2.2.3 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/79)、親Issue #572、子Issue #573 - #577

## V2.2.2: 設定画面・アプリ更新Hotfix - 2026-08-23

- V1.x時期の設定画面構成を確認し、PySide6設定画面へ録画設定、YouTube連携、管理データ、CSV入出力、表示、アプリ更新の入口を復旧した
- 設定読み書きを既存の設定管理テーブルへ接続し、検出設定と録画設定のキー取り違えで保存・再読込が壊れる問題を防いだ
- アプリ更新タブで現在バージョン、確認中、最新、更新候補あり、確認失敗を区別し、更新候補がある場合だけダウンロードボタンを有効にした
- GUI smoke contractへ設定画面復旧契約と更新タブ状態契約を追加し、Windows GUI EXE smokeでも同じ契約を検査するようにした
- バックアップ復元は実行前確認を追加し、録画ファイル、queue、manifest、OAuth資格情報を変更しないことを明示した
- DB schema、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全テスト、PySide6 GUI smoke、UIスクリーンショット確認、CLI/GUI/updater EXEビルド、3 EXEスモーク、公開Release asset検証に合格した
- ローカル通常ビルドCLI EXE SHA-256: `9aea671e9f6d88142f7c1729e50ffc034436f7638c1802ba773d0e1293dc1795`
- ローカル通常ビルドGUI EXE SHA-256: `f85fb431449a98f4ce5944d53384f4083bed3c43f0fc5251f15dcfba2600bf11`
- ローカル通常ビルドupdater EXE SHA-256: `d78f838b5df66f44c926d60876edb5af8414408b9e066261efef120a188c1d0f`
- 追跡: [V2.2.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/78)、Issue #570

## V2.2.1: アプリ更新Release選択hotfix - 2026-08-23

- アプリ内更新がGitHubの`latest` 1件だけに依存せず、Release一覧から配布可能な最新安定版を選ぶようにした
- draft/prerelease、現在以下のバージョン、GUI EXE/updater EXE/SHA-256が不足するReleaseを更新候補から除外する
- 配布可能Releaseが存在しない場合は、例外ではなく「利用可能な更新なし」と扱う
- `v2.2.0` は配布資産なし通常Releaseとして公開されていたため、応急処置としてpre-releaseへ変更した
- DB schema、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全587テスト、CLI/GUI/updater EXEビルド、3 EXEスモーク、GUI smokeによるアプリ更新タブ・統計チャート・日付ピッカー・主要テーブル契約確認に合格した
- ローカル通常ビルドCLI EXE SHA-256: `848267dee6e46e9b132048106bbcc16d96f47c6474a4c170154d31d8f0ac2620`
- ローカル通常ビルドGUI EXE SHA-256: `7a079d1aeef711e8b3560abf84daf43d78c05132d805ab440f411f53a8908e3f`
- ローカル通常ビルドupdater EXE SHA-256: `4ca81cb5654db18a1411cd134142dbae9d34a1c6065da20ff911693a4c2adbb3`
- 追跡: [V2.2.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/77)、Issue #568

## V2.2.0: PySide6 UI読み取り品質改善 - 2026-08-23

- 統計画面の開始日・終了日をカレンダーポップアップ付きの日付入力にした
- 統計の勝利数・勝率推移を、勝利数の棒グラフと累積勝率の折れ線グラフとして表示するようにした
- 戦績管理、デッキ名、タグ、シーズン、統計内訳などの表に、列幅、横スクロール、行高、穏やかな選択色を設定した
- デッキ/タグのカラー列を、色コード文字ではなく色見本として確認できる表示へ改善した
- PySide6 smoke contractへ、カレンダー、統計チャート、表可読性、カラー表示のUI改善契約を追加した
- DB schema、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、重点テスト、PySide6 GUI smoke、全584テスト、スクリーンショット目視確認に合格した
- 追跡: [V2.2.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/76)、Issue #566

## V2.1.1: PySide6 rich UI回帰修正 - 2026-08-23

- PySide6 GUIが初期・簡略UIへ戻っていた回帰を修正し、`docs/assets/tkinter-ui-baseline-1.5.2-rich` の画面構成に近い情報量へ復旧した
- 録画画面に録画対象、録画状態、録画なし戦績追加、環境診断、アクティビティを同居させ、画像1のような簡略画面を完成扱いしない契約を追加した
- 戦績管理、統計、デッキ名、タグ、シーズン、テンプレート、信頼性、設定、内部MP4準備/改善ページの主要導線と表示密度を復旧した
- PySide6 smoke contractへrich baseline画像11件、rich UI section、内部ページの確認項目を追加した
- DB schema、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全587テスト、PySide6 GUI smoke、スクリーンショット目視確認に合格した
- 追跡: [V2.1.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/75)、Issue #564

## V2.1.0: PySide6標準機能移植 - 2026-08-23

- `master-duel-recorder-lite-gui.exe`の通常入口をPySide6 GUIへ戻した
- PySide6 smoke contractを15標準機能の実操作ゲートへ揃え、要求widget 51個と主要操作チェックを全通過するようにした
- 録画、戦績管理、履歴フィルター、手動戦績、統計、デッキ名、タグ、シーズン、YouTube、MP4準備、信頼性、設定、データ保全、CSV/更新、主要ダイアログの状態表示と入口をPySide6 UIへ追加した
- PySide6 GUIの実アプリ起動時に、既存サービス層から履歴、カタログ、シーズン、YouTubeテンプレート、MP4準備、バックアップ、統計を読み込むようにした
- 統計の推移単位をPySide6統計画面の「勝利数・勝率推移」内条件として配置し、既定値を日単位にした
- DB schema、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全586テスト、PySide6 GUI smoke、CLI/GUI/updater one-file EXEビルド、3 EXEスモークに合格した
- ローカル通常ビルドCLI EXE SHA-256: `51BF7E92E4C2D6E3E31C9156119F57E1563997BF3EC9F91389CB74B3A45AC201`
- ローカル通常ビルドGUI EXE SHA-256: `C931E56DDC18EA51B32E4107B0F41C0558E59AA09A15376ECA95F0B9FD59431E`
- ローカル通常ビルドupdater EXE SHA-256: `87FCB0E730D8F0F04435215D6F29ED9BFE9B04B1A19FF62F232C512A52583AAC`
- 追跡: [V2.1.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/74)、親Issue #556、子Issue #557 - #563

## V2.0.3: 戦績管理・統計・シーズンレポートの読み取り品質改善 - 2026-08-23

- 戦績管理一覧のデッキ名列で、デッキ色スウォッチと長い日本語デッキ名が重なりにくい余白へ調整した
- 統計の勝利数・勝率推移を、期間ごとの勝利数と、その区間までの累積勝率として表示するようにした
- シーズンレポートの推移表も、期間ごとの対戦・勝敗と累積勝率を分けて表示するようにした
- シーズンレポートの内訳に全体行を追加し、0件の未設定行と勝敗列に重複する最終勝敗軸を標準表示から外した
- GUI、HTML出力、統計・シーズンレポートのテスト期待値を同じ表示方針へ揃えた
- DBスキーマ、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全583テスト、隔離Python GUI smoke、CLI/GUI/updater one-file EXEビルド、3 EXEスモーク、V1候補検証に合格した
- ローカル通常ビルドCLI EXE SHA-256: `CE59A88D52B1679E24CC928F82AB7E4A70FC497179DF9660625E38DD7CA9AE40`
- ローカル通常ビルドGUI EXE SHA-256: `E7656B5FBF83B13708891313C3FD6BEE467386A27FE93FA3475AE4F34F35C1A1`
- ローカル通常ビルドupdater EXE SHA-256: `1E3A2006BD43434E825BF8045963C0558EAAFB767F8951D5D3306F75B7A74D2A`
- 追跡: [V2.0.3 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/73)、親Issue #550、子Issue #551 - #554

## V2.0.2: 15標準機能の実操作確認 - 2026-08-23

- GUI smokeに、15標準機能の実操作チェック結果、失敗した操作、標準操作契約を出力する契約を追加した
- 配布GUI smokeで、widget存在だけでなく録画後ワークフロー、データ保全表示、実操作契約を検査するようにした
- データ保全表示で、バックアップ/復元が管理DBと設定を対象にし、録画ファイル、queue、manifest、OAuth資格情報を変更しないことを明示した
- PySide6通常入口化ゲートを、widget存在確認から実操作確認、録画後ワークフロー、データ保全表示まで含む条件へ拡張した
- V1.4.x open Issueを#548で棚卸しし、V2.0.2のBackward Compatibilityを妨げる直接ブロッカーがないことを確認した
- DBスキーマ、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全583テスト、隔離Python GUI smoke、CLI/GUI/updater one-file EXEビルド、3 EXEスモーク、V1候補検証に合格した
- ローカル通常ビルドCLI EXE SHA-256: `0039925646F9185D5F482ACE198D80D68C69D8C19BDD660C8360FB7AFBFEAF40`
- ローカル通常ビルドGUI EXE SHA-256: `733577199DD48F95753C15803EDC0BC96E57D25109792CDD813344032E6D00C2`
- ローカル通常ビルドupdater EXE SHA-256: `9A86D37597EA202ECFB426C346FCFD2332BA6697C3788CF149E88450FD2E87BE`
- 追跡: [V2.0.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/72)、親Issue #542、子Issue #543 - #548

## V2.0.1: 通常配布GUI復旧 - 2026-08-22

- `master-duel-recorder-lite-gui.exe`の通常入口を`master_duel_recorder_lite.gui`へ戻し、1.x相当のTkinter操作面を復旧した
- PySide6シェル`master_duel_recorder_lite.pyside_gui`は通常入口から外し、検証入口として残した
- PySide6レビュー入口`master_duel_recorder_lite.pyside_review`は維持した
- GUI smokeに、通常入口、PySide6通常入口ではないこと、標準機能ゲート、参照中runtime data、SQLite DBパスを出力する契約を追加した
- 既存DB入りruntimeを`RecorderApplicationService`で再読込できる回帰テストを追加した
- PySide6全面移植で担保すべき1.x標準機能15項目を`docs/architecture/pyside-feature-parity-2.0.1.md`へ列挙した
- DBスキーマ、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、重点テスト、全580テスト、Python GUIスモーク、既存DB再読込スモーク、CLI/GUI/updater one-file EXEビルド、3 EXEスモーク、V1候補検証に合格した
- ローカル通常ビルドCLI EXE SHA-256: `D60E7D70B4C7E1E100D134EF5F4C897BDBF6E0EC290CEC2AB19E22372B7ABBC5`
- ローカル通常ビルドGUI EXE SHA-256: `41D5C7725D313EF2BF435872FDB06A9E654E4E47B763B49C6276DF3E35605F4A`
- ローカル通常ビルドupdater EXE SHA-256: `250843040A8D2696820FAB63A0281DAFE33D99D7A2069653C021C3EDC1120EBF`
- 追跡: [V2.0.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/71)、親Issue #536、子Issue #537 - #540

## V2.0.0: PySide6 GUI移行 - 2026-08-22

- `master-duel-recorder-lite-gui.exe`の通常入口をTkinterからPySide6へ切り替えた
- PySide6のメインウィンドウ、左ナビ、共通状態表示を追加した
- 録画、戦績管理、統計、デッキ名、タグ、シーズン、YouTube、信頼性、設定の主要ナビを追加した
- 録画開始、録画停止、自動監視切替、履歴更新をPySide6 GUIから既存サービス層へ接続した
- `prepare`と`improve`は独立ナビへ戻さず、YouTube、戦績管理、統計、設定へ統合する方針を維持した
- PySide6 GUIスモークJSONとスクリーンショット出力を追加した
- 旧Tkinter GUIは互換モジュール`master_duel_recorder_lite.gui`として残した
- DBスキーマ、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、PySide6契約テスト、全575テスト、PySide6 GUIスモーク、CLI/GUI/updater one-file EXEビルド、3 EXEスモーク、V1候補検証に合格した
- ローカル通常ビルドCLI EXE SHA-256: `5508BC994612BB4A62D849E710EA3925793DE5B38EBCEE6CA9AC6271ADC88F27`
- ローカル通常ビルドGUI EXE SHA-256: `A6FFA8577BAD116C92A2E79CCB8B182C759F31D4D5BB20D867B83DE50B2AED83`
- ローカル通常ビルドupdater EXE SHA-256: `C8D4F73DB6F6939EF7E035C0AD74562946FFB2AEF5AE1E3BC06F31F0C52BF270`
- 追跡: [V2.0.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/69)、親Issue #500、子Issue #501 - #522

## V1.6.0: 録画後ワークフロー情報設計 - 2026-08-22

- V2.0.0のPySide6全面移行へ入る前に、録画後の整理、振り返り、投稿準備を1つの作業導線として定義した
- 戦績管理を録画後ハブとして扱い、未入力、下書き、録画欠損、投稿済み、手動戦績などの状態優先度を整理した
- MP4準備内部ページは独立ページとして単純移植せず、YouTube投稿フロー、診断、CLIへ分解する方針にした
- 改善内部ページは独立ページとして単純移植せず、戦績管理、統計、設定へ吸収する方針にした
- 設定画面を通常設定、外部連携、データ保護、危険操作、診断へ分ける情報設計を記録した
- 戦績入力、未完了処理、詳細入力、一括編集、タイムライン、YouTube投稿、診断、クリーンアンインストールの導線をワークフロー単位で整理した
- V2.0.0のスクリーンショット回帰と操作スモークへ、画面単体だけでなく録画後フロー単位の検証要件を追加した
- Master Duel単体音声で録画した出力に音声ストリームがない場合、録画を削除せず履歴へ音声警告を残すようにした
- 音声設定GUIで、Master Duel単体音声ではDirectShow音声入力欄を使わないことを明示し、本当の音声なし設定と区別した
- DBスキーマ、設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、文書リンクテスト、全572テスト、Python GUIスモーク、CLI/GUI/updater one-file EXEビルド、3 EXEスモーク、V1候補検証に合格した
- ローカル通常ビルドCLI EXE SHA-256: `45FF1E09A4FB32FE683ED5635A28E249CEDB390BC0E34F0565BD2A5E0C6A9D73`
- ローカル通常ビルドGUI EXE SHA-256: `E2D1ED58FD8A77E49116CAD109A0FC4B88F994542841E3C8720C5BCD075FB2ED`
- ローカル通常ビルドupdater EXE SHA-256: `BDE119089FE95110F6FEC00FF860035FB98752CEAB7F6F34B238599B0CBC5F23`
- 追跡: [V1.6.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/70)、親Issue #523、子Issue #524 - #531

## V1.5.2: デッキ名の使用回数表示・利用頻度順・UI baseline保存 - 2026-08-22

- デッキ名カタログへ戦績上の使用回数を追加し、自分デッキ・相手デッキの出現回数を合算して表示できるようにした
- デッキ名一覧とGUIのデッキ候補を、使用回数降順、同数時は名前順で表示するようにした
- デッキ名管理画面へ「使用回数」列を追加した
- 戦績管理のデッキ名列の色表示を、色付きドットから枠線付き四角スウォッチへ変更した
- 明るいデッキ色には黒枠、暗いデッキ色には白枠を使い、選択行や背景に埋もれにくくした
- PySide6全面移行前のTkinter主要ページ、内部ページ、ポップアップ、OS標準ダイアログ導線、データ保護要件を文書とPNGで保存した
- DBスキーマはV17のまま変更しない。使用回数は既存`duel_records`からの派生値であり、保存列は追加しない
- 設定形式、録画ファイル、YouTube投稿履歴、OAuth資格情報、prepare queue、manifestは変更しない
- Ruff、全569テスト、Python GUIスモーク、CLI/GUI/updater one-file EXEビルド、3 EXEスモーク、V1候補検証、baseline画像36枚の非空白確認に合格した
- ローカル通常ビルドCLI EXE SHA-256: `3637A68ED03E8231FE286BD665A6CCE8CA1A6CC9FB5704032684A57B8F1F3B49`
- ローカル通常ビルドGUI EXE SHA-256: `DF806A16742E4D6F10C298D0DFE35AB0BBB817C030227D1668DD80DC89EF57A1`
- ローカル通常ビルドupdater EXE SHA-256: `30D8FF4A7B238CF8E170963A4310E4550F85C9B46CA047D6AF6C1C80F51E7C0D`
- 追跡: [V1.5.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/68)、親Issue #492、子Issue #493 - #496、#498 - #499

## V1.5.1: 戦績管理・YouTube投稿・入力体験の調整 - 2026-08-22

- タグ管理のデッキ専用タグ文言を「デッキ名登録でのみ使用」へ変更した
- 戦績管理の上部操作を1行へ整理し、主要操作を「未完了を処理」「一括編集」「手動追加」の順にした
- 戦績管理の絞り込み解除ボタンを、フィルター未適用時は無効表示にした
- 戦績管理のデッキ色表示を、デッキ名列の色付きドット表示へ変更した
- YouTube投稿テンプレート画面を追加し、タイトル、概要欄、タグの単一テンプレートと`{deckname}`などの変数展開に対応した
- YouTube投稿ダイアログに「Youtubeリンク」欄を追加し、投稿済み録画では入力欄を無効化して「リンクを開く」へ切り替えるようにした
- 録画診断ダイアログの閉じるボタンを判読しやすい表示へ変更した
- 対戦記録編集画面の録画レビュー導線を説明しやすい文言へ変え、起動直後に失敗した場合は利用者へ通知するようにした
- 簡易入力は勝敗、先後、コイントス、相手デッキを入力する形へ変更し、自分デッキとシーズンは候補値として保持するようにした
- 簡易入力と詳細入力の新規候補で、前回入力した対戦種別、自分デッキ、シーズンを引き継ぐようにした
- DBスキーマはV17のまま変更しない。既存`user_data`、録画、YouTube投稿履歴、OAuth資格情報は保持する
- Ruff、全565テスト、Python GUIスモーク、CLI/GUI/updater one-file EXEビルド、3 EXEスモーク、CLI EXEの`review status`に合格した
- ローカル通常ビルドCLI EXE SHA-256: `5613B30A73B2BC5661192D220F3100DD9D47875E6255B64186D155DB5890FC08`
- ローカル通常ビルドGUI EXE SHA-256: `A02A3D9D245F22D1AC5A1B41CE51F05CC85754E5388629E6E97808C4A735DDB7`
- ローカル通常ビルドupdater EXE SHA-256: `DAE0143DFD1EF706D099FCB09DB4404A9BF3F7EB7BF4B31BA40903452BF09EAD`
- 追跡: [V1.5.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/67)、親Issue #479、子Issue #480 - #491

## V1.5.0: GUI非依存データ契約とPySide6レビュー基盤 - 2026-08-22

- GUI境界と保存契約を`docs/architecture/gui-data-contract.md`へ追加し、DB、設定、録画、exports、queue、logs、OAuth資格情報の扱いを明文化した
- 録画概要、動画参照、タイムライン、戦績概要、クリップ候補を表すGUI非依存Review ViewModel/DTOを追加した
- `RecorderApplicationService`へレビュー用ViewModel生成、現在位置マーカー追加、レビュー用クリップ出力の入口を追加した
- `mdrl review show/status/launch`を追加し、PySide6がない環境でもViewModel確認と利用可否確認ができるようにした
- PySide6レビュー画面は`pyside_review.py`に隔離し、Tkinterからは別プロセスで起動してevent loop競合を避けるようにした
- PySide6起動失敗時は`--fallback-external`でWindows既定プレイヤーへ戻れるようにした
- PySide6は通常依存ではなく`review` extraへ配置し、配布ビルド用の`build` extraではQt Multimediaを含める
- DBスキーマと設定形式は変更しない。既存`user_data`、録画、YouTube OAuth資格情報、prepare queue、manifestは保持する
- Ruff、全561テスト、CLI/GUI/updater one-file EXEビルド、3 EXEスモーク、CLI EXEの`review status`に合格した
- ローカル通常ビルドCLI EXE SHA-256: `43CE4B831C0E79BE2A18A7F608D7AA114C3B6713893496FA769A5D6D34532AB2`
- ローカル通常ビルドGUI EXE SHA-256: `C6FF277595AD8AEECFE43A1E42605D1A214EA123CD7EC67FF49A2F7E7454259D`
- ローカル通常ビルドupdater EXE SHA-256: `18B024216BB8A4CA041404C0F7B851BAFB464607895FD446B1E4C743F5FD9E77`
- 追跡: [V1.5.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/60)、親Issue #434、子Issue #435 - #441

## V1.4.6: 戦績編集とデッキタグ管理の改善 - 2026-08-22

- 対戦記録編集画面の下部左側へ「保存場所を開く」「タイムラインを表示」「録画診断を表示」「欠損した録画ファイルを再関連付け」を集約した
- 編集画面の「対戦内容」見出し横に録画有無とYouTube連携状態を表示し、録画欠損も判別できるようにした
- 編集画面の下部にYouTubeリンク欄と「開く」ボタンを追加し、投稿済みURLをブラウザで確認できるようにした
- デッキ名へタグを付けるDBモデル、サービスAPI、デッキ管理GUIを追加した
- タグ管理に「デッキ名登録でのみ使用」を追加し、ONのタグは戦績入力時のタグ候補から除外するようにした
- DBスキーマをV17へ更新し、既存の戦績、録画、YouTubeアップロード履歴、既存タグを保持したまま移行する
- Ruff、全554テスト、CLI/GUI/updater one-file EXEビルド、3 EXEスモークに合格した
- ローカル通常ビルドCLI EXE SHA-256: `0F9CED99348EECDA12D34E9ACCF3B8E9CFC846619E31CC3796C6F8E276DAED02`
- ローカル通常ビルドGUI EXE SHA-256: `06A293A28C556DCE7578321C51485E14F4F072119449348CED23F92CE9B94CE1`
- ローカル通常ビルドupdater EXE SHA-256: `A676A73DF817996D5422D15A3312C7E247C80AAB30D1F575D3725DF8E037EA6B`
- ローカルrelease必須OAuth client検証は、手元環境に`MDRL_YOUTUBE_OAUTH_CLIENT_ID/MDRL_YOUTUBE_OAUTH_CLIENT_SECRET`または`assets/youtube-oauth-client.json`がないため未完了。正式ReleaseではGitHub ActionsのSecretsで検証する

追跡: [V1.4.6 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/66)、親Issue #471、子Issue #472 - #476

## V1.4.5: YouTube OAuth資格情報ストアhotfix - 2026-08-22

- 配布EXEからWindows資格情報ストアのYouTube OAuth資格情報を読み取れず、GUIの`接続確認`や投稿ダイアログで未連携扱いになる問題を修正した
- `WindowsCredentialStore`をWindows Credential APIの直接呼び出しへ変更し、PyInstaller EXEでの`win32cred`差異に依存しないようにした
- 資格情報なし、Windows API失敗、保存済みJSON不正を区別し、`read/delete`の失敗を成功扱いにしないようにした
- CLI EXEスモークへ、隔離したCredential targetでのYouTube OAuth資格情報読み取りと削除の検証を追加した
- DBスキーマ、設定形式、録画ファイル、prepare queueは変更しない。`refresh_token`、`access_token`、認可コードは配布物、設定、DB、queue、manifest、ログへ含めない
- Ruff、全550テスト、CLI/GUI/updater one-file EXEビルド、3 EXEスモークに合格した
- ローカルrelease相当CLI EXE SHA-256: `9D658C7E41A38318C0321B3EF591400C2F1644517CC7E728EFEFBAD02A556521`
- ローカルrelease相当GUI EXE SHA-256: `7E842975AF78B2D8D8691C2096D2EF1CF68469FE72521BAD4B69B4AFB6E9B953`
- ローカルrelease相当updater EXE SHA-256: `863A61D7152A51C35C911F8BA14A0C1C7E2AA2B2B40308CF863DCC34DD8DFD08`

追跡: [V1.4.5 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/65)、Issue #466

## V1.4.4: YouTube OAuth client_secret同梱hotfix - 2026-08-22

- GUIの通常YouTube連携でOAuth token交換時に`client_secret`を送信できるよう、配布EXEへ`client_id`と`client_secret`を同梱する方針へ変更した
- release workflowで`MDRL_YOUTUBE_OAUTH_CLIENT_SECRET`を受け取り、releaseビルドでは`client_id`と`client_secret`の両方を必須にした
- release toolingテストを更新し、配布assetに`client_secret`が含まれることと、`refresh_token`、`access_token`を拒否することを確認するようにした
- OAuth token交換テストを追加し、`client_secret`設定済みのOAuth clientではtoken requestへ`client_secret`を含めることを確認した
- DBスキーマ、設定形式、録画ファイル、prepare queueは変更しない。`refresh_token`、`access_token`、認可コードは配布物、設定、DB、queue、manifest、ログへ含めない
- Ruff、全545テスト、CLI/GUI/updater one-file EXEビルド、3 EXEスモークに合格した
- ローカルrelease相当CLI EXE SHA-256: `2BA3829638C42D7BF2785F7C7789E06AA0F3135E54D2291933263306C6A6DE8A`
- ローカルrelease相当GUI EXE SHA-256: `20539D31D4EC65104C1D9A7B5386EA3865187275AE6576F84C0CF67CBCC54522`
- ローカルrelease相当updater EXE SHA-256: `E454B48A734F9F83864CF5EDA4498808627D4E5C9E0D7A0214D9A2EF2BCB2272`

追跡: [V1.4.4 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/64)、Issue #468

## V1.4.3: アプリ内自動更新updater化 - 2026-08-22

- アプリ内更新を手動ダウンロードへ退避せず、GUIへ同梱した専用`master-duel-recorder-lite-updater.exe`で自動適用するようにした
- 旧GUI終了待機、候補SHA-256再確認、`.staged`コピー、`.previous`退避、置換後GUIスモーク、失敗時ロールバック、成功時再起動をupdaterへ分離した
- release workflowでCLI/GUI/updaterの3 EXEとSHA-256を公開し、公開済みassetを再ダウンロードして3 EXEをスモークするようにした
- release toolingテストの固定バージョン期待値を`pyproject.toml`と`__version__`から導出し、Fixリリース時の更新漏れを防ぐようにした
- V1.4.1/V1.4.2の旧更新コードでは、置換対象GUI EXEの起動前スモークだけでは更新適用時のPyInstaller展開失敗を防げないことを検証記録へ残す
- YouTube OAuth client_id同梱、OAuth 400診断、Windows CredentialBlob互換修正は維持した
- DBスキーマ、設定形式、録画ファイル、prepare queue、OAuth資格情報保存先は変更しない
- Ruff、全543テスト、Python GUIスモーク、CLI/GUI/updater one-file EXEビルド、3 EXEスモーク、updater置換スモークに合格した
- ローカルrelease相当CLI EXE SHA-256: `A024F4F6581EED64ED48B0B3DC6CC1AC133604AE518C4B7EF071C27D61C68394`
- ローカルrelease相当GUI EXE SHA-256: `F766FE003C7205DF78D4F184DE0D2521C794DE7D4412BD3413C6C074555AA30D`
- ローカルrelease相当updater EXE SHA-256: `0FEE2ABB045C1E0C06E9631CD096D44342F81784F0F1C71B8B725B4848C7B2B2`

追跡: [V1.4.3 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/63)、親Issue #459、#462、子Issue #460 - #461、#463 - #465

## V1.4.2: 更新EXE起動検証とOAuth診断hotfix - 2026-08-21

- アプリ更新でGUI EXEを取得した後、置換前に`--smoke-test`で起動検証し、Python DLL展開失敗などの起動不能EXEを適用しないようにした
- GitHub Release公開後に公開済みassetを再ダウンロードし、CLI/GUIスモークを公開物そのものへ実行するようにした
- YouTube OAuth token交換HTTP 400のGoogleエラー本文を読み取り、`invalid_grant`、`redirect_uri_mismatch`、`invalid_client`などの原因分類と次の確認事項を表示するようにした
- OAuth診断ではauthorization code、access token、refresh token、client secretなどのsecret相当値を伏せるようにした
- V1.4.2配布ビルドでは、更新されたYouTube OAuth `client_id`だけをGitHub Secretから同梱する
- DBスキーマ、設定形式、録画ファイル、prepare queue、OAuth資格情報保存先は変更しない
- Ruff、全535テスト、Python GUIスモーク、CLI/GUI one-file EXEビルド、両スモーク、更新取得後GUI EXE事前スモークに合格した
- ローカルrelease相当CLI EXE SHA-256: `926B4A94CC32955949624390B4143FE02AD0D82AB31771F9CAFD04A327BFB2C2`
- ローカルrelease相当GUI EXE SHA-256: `9F44FA27E6EEB3A735B952C23904EE0E6C5F95E6495997E6A7434317F0619322`

追跡: [V1.4.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/62)、親Issue #452、子Issue #453 - #457

## V1.4.1: YouTube一般配布導線hotfix - 2026-08-21

- 改善ページの状態更新で`list_history()`の引数不整合により例外が出る回帰を修正した
- YouTube OAuth client_idを配布EXEへ同梱できるようにし、releaseビルドでは未設定を検出するようにした
- OAuth client_id未設定時はGUIでYouTube連携開始を無効化し、配布ビルド側の前提不足として表示するようにした
- `MP4準備`をGUIの独立主要ナビから外し、録画履歴からのYouTube投稿フロー内で投稿前処理として扱うようにした
- `改善`を未成熟な主要ナビとして露出しないよう整理し、録画なし戦績追加など必要な操作は戦績管理側から維持した
- PKCE/loopback、OS資格情報ストア、prepare queue、manifest、DB V16、secret非保存契約は維持した
- 実YouTube private投稿E2Eは、正式更新で配布EXEを取得した後の外部検証として扱う
- Ruff、全531テスト、Python GUIスモーク、CLI/GUI one-file EXEビルド、両スモークに合格した
- release用OAuth client_id必須検証は、未設定時にビルド前失敗し、設定時に`client_id`のみを同梱して成功することを確認した
- ローカルrelease相当CLI EXE SHA-256: `C94D5EA25905965DE74DB196493D540ADEFA16AD718C84AED56C441C86BBF2FE`
- ローカルrelease相当GUI EXE SHA-256: `D19A00E92D30ED0E1F7A74F3A535E0D3AED1F05A4BE6A5F3796FB6C655A31663`

追跡: [V1.4.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/61)、親Issue #444、子Issue #445 - #450

## V1.4.0: YouTube一般配布導線 - 2026-08-21

- YouTube OAuthをPKCEと`127.0.0.1:<random port>` loopback callbackに対応させ、通常導線で認可コードのコピー&ペーストを不要にした
- 配布者管理OAuth Clientを使う通常導線と、開発者向け`--client-secrets` fallbackを分離した
- GUIの設定へ`YouTube`タブを追加し、連携状態、連携、切断、接続確認、最新録画でのprivateテスト投稿入口を追加した
- 戦績管理の録画履歴からYouTube投稿ダイアログを開き、タイトル、概要欄、タグ、公開範囲を確認して投稿できるようにした
- 既定公開範囲は`private`を維持し、`public`投稿では追加確認を行うようにした
- 401、403、quota/rate、5xx、通信断、不明応答の分類と、再認証、quota待ち、権限確認、再試行、手動確認の次アクション表示を追加した
- refresh token、access token、client secret相当の値を設定、DB、queue、manifest、ログ、移行パックへ保存しない契約を維持した
- DBスキーマはV16のまま変更しない。既存`youtube_uploads`を継続利用する
- 実YouTube private投稿E2Eは、配布者管理OAuth ClientとGoogleアカウント許可が必要な外部ゲートとして残る
- Ruff、全520テスト、Python GUIスモーク、CLI/GUI one-file EXEビルド、両スモークに合格した
- ローカルCLI EXE SHA-256: `C986332100AF2DC36D60BA88CF7F424910706A8EE79E7A75848A6AA878CB4547`
- ローカルGUI EXE SHA-256: `BFB1D21CEF8B0E8270847F628AA210F3EA0B04BC254A448C024EA5BE0C740034`

追跡: [V1.4.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/59)、親Issue #427、子Issue #428 - #433

## V1.3.0: 入力削減・デッキ改善・運用管理 - 2026-08-21

- 履歴DBをV16へ更新し、タグテンプレート用`tag_templates`と練習目標用`practice_goals`を追加した
- 最近値、頻出値、タグから戦績入力候補を生成するサービスと`mdrl improve suggest`を追加した
- タグテンプレートの作成・一覧と、練習目標の作成・一覧を`mdrl improve`へ追加した
- 自分デッキ別に相手デッキ、勝敗、先後、コインを集計するデッキ改善集計を追加した
- 失敗録画と戦績未入力録画を中心に、録画ストレージ整理候補を読み取り専用で提示するようにした
- 設定と履歴DBをSHA-256付きZIPへまとめる移行パック作成を追加し、録画ファイルとOAuth資格情報は含めない契約にした
- クリップ、マーカー、戦績編集へつなぐ軽量レビュー状態モデルを追加した
- GUIへ「改善」ページを追加し、入力候補、録画なし戦績追加、デッキ改善、ストレージ、移行、レビューの入口をまとめた
- Ruff、全514テスト、Python GUIスモーク、CLI/GUI one-file EXEビルド、両スモークに合格した
- ローカルCLI EXE SHA-256: `303A61DD0272F58C8761D7D20F874A2C6D55BA6E955EC2DCF8FFBF4C543B6150`
- ローカルGUI EXE SHA-256: `B11A2411A430443F605303A151673C428243DBB3777C9E5790B0C726BD5EF41A`

追跡: [V1.3.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/58)、親Issue #418、子Issue #419 - #426

## V1.2.0: 自動録画の信頼性と後解析 - 2026-08-21

- 自動録画の事前チェック結果モデルを追加し、preflight、Master Duelウィンドウ、画面判定サンプルから利用者向けの成功、警告、失敗理由を生成できるようにした
- `mdrl reliability check`を追加し、録画前に「利用できます / 失敗しそうです / 理由」を確認できる入口を用意した
- 初回導入ウィザードの状態モデルを追加し、FFmpeg、保存先、録画対象、音声、事前チェック、テスト録画、再生確認を一連の導入ステップとして扱えるようにした
- 既存動画とリプレイ録画の後解析入口を追加し、ffprobe検証と元動画のサイズ・更新時刻確認により読み取り専用契約を守るようにした
- ホットキー操作許可表とディスパッチャーを追加し、録画中、監視中、停止中、処理中の許可操作を明確化した
- GUIへ「信頼性」ページを追加し、30秒事前チェック、初回導入、ホットキー・トレイ状態を確認できるようにした
- `[interaction]`設定を追加し、事前チェック秒数、導入完了状態、ショートカット、トレイ設定を非シークレット設定として既定値補完するようにした
- DBスキーマ、録画ファイル、YouTube資格情報、prepare queue、manifestの形式は変更しない
- Ruff、全511テスト、Python GUIスモーク、CLI/GUI one-file EXEビルド、両スモークに合格した
- ローカルCLI EXE SHA-256: `AB4F090563F384B4540D5A323927EC4C302745C7B9B8FEC6BEEC91EC2547ECE4`
- ローカルGUI EXE SHA-256: `C1D4574160416C2AC19857D90143BD74CBA359146DF057A08128F4588F56A694`

追跡: [V1.2.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/57)、親Issue #412、子Issue #413 - #417

## V1.1.0: YouTube公式連携 + MP4自動アップロード - 2026-08-21

- `upload.privacy_status`とアップロードメタデータで`public`を明示指定できるようにし、既定は引き続き`private`にした
- 概要欄テンプレートを追加し、許可済み変数だけを展開して未知項目や秘密情報に見える項目を拒否するようにした
- 履歴DBスキーマをV15へ更新し、`youtube_uploads`で投稿状態、試行回数、失敗理由、動画ID、視聴URLを録画IDへ紐付けるようにした
- YouTube OAuth接続、接続状態確認、切断を`mdrl youtube`へ追加し、資格情報をOS資格情報ストアだけへ保存するようにした
- 既存のcompleted prepare結果を再利用し、なければMP4準備を実行してからYouTube Data APIのresumable uploadへ進む投稿サービスを追加した
- fake clientを使う結合テストで成功、再試行、OAuth未接続時の失敗保存を検証した
- `mdrl youtube upload RECORDING_ID --title TITLE`、`upload run/list/show`、`history list/show`のYouTube URL表示を追加した
- タイムライン時刻から前後秒数を指定して投稿用MP4クリップを`user_data/data/exports/`へ非破壊で出力するCLIとサービスを追加した
- OAuth未接続でもタイトル、概要欄、タグ、投稿チェックリスト、素材出力先を生成できる投稿素材サービスを追加した
- Ruff、全500テスト、Python GUIスモーク、CLI/GUI one-file EXEビルド、両スモークに合格した
- ローカルCLI EXE SHA-256: `330D3BAE34DCB8E706F49CC474CCF11078C0759B51BA0F4A6484A848A3776DBD`
- ローカルGUI EXE SHA-256: `D05CAF0F416A8D7ED487E3A1F2A80D5CF07B1A5F27856BAF0037CDD7B0AEA842`

追跡: [V1.1.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/56)、親Issue #401、子Issue #402 - #408、#410 - #411

## V1.0.3: GUI操作性改善 - 2026-08-21

- カレンダーピッカーのヘッダーを7列グリッドへ整理し、「前月」「年月」「今月へ」「翌月」を1:3:2:1で配置した
- 年月タイトルを大きくし、火・水・木列にまたがる表示へ変更した
- 月曜から日曜までの曜日ラベルと日付セルを等幅で揃え、土曜日列だけ横位置や余白が歪まないようにした
- 簡易入力と未完了戦績処理の勝敗、先後をボタン選択へ変更した
- 詳細編集の状態、勝敗、先後、コインの面、対戦種別をボタン選択へ変更した
- 一括編集のコイントスと対戦種別をボタン選択へ変更し、「変更しない」と「未設定」を明確に分離した
- DBスキーマ、CSV形式、CLI契約、設定、`ui-preferences.json`、録画ファイル、実行時データ形式は変更しない
- 更新時変更ログは通常の更新情報表示をrequired、強制モーダルをnot-requiredとして判断した
- V1.1.0、V1.2.0、V1.3.0のロードマップと追跡先をローカル文書へ追加した
- Ruff、全478テスト、Python GUIスモーク、CLI/GUI one-file EXEビルド、両スモークに合格した
- ローカルCLI EXE SHA-256: `BDA6C86E575CAEF886F43A85256A339C6E07D98ACF6D871E13F1B82B5454D183`
- ローカルGUI EXE SHA-256: `50688E4473C4F4AF9E52DD46A6296F2DF76BE3CE172722175CEE552206DA582A`

追跡: [V1.0.3 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/55)、親Issue #409、子Issue #399 - #400

## V1.1.0 - V1.3.0 計画追跡 - 2026-08-21

- V1.1.0はYouTube公式連携、MP4自動アップロード、クリップ出力、投稿素材生成を対象とする
- V1.2.0は30秒事前チェック、初回導入ウィザード、既存動画・リプレイ後解析、ホットキー・トレイを対象とする
- V1.3.0は入力候補強化、録画なしミニ入力、デッキ改善ビュー、目標、ストレージ管理、移行パック、軽量レビューを対象とする
- 各バージョンのIssue、Milestone、Release Contractは`docs/roadmap.md`へ反映した

## V1.0.2: 戦績入力フローと一括編集の操作改善 - 2026-08-21

- 未完了戦績の連続処理画面へ勝敗、先後、自分デッキ、シーズンの簡易入力を統合し、保存後に残件を再取得して次の対象へ進むようにした
- 連続処理から詳細入力を開いた場合も、保存またはキャンセル後に未完了戦績の処理へ戻るようにした
- 一括編集へコインの表、裏、未設定を追加し、「変更しない」と明示的な未設定を区別した
- 戦績管理一覧のダブルクリック動作を、設定の「表示」から録画再生または戦績編集へ切り替えられるようにした。既定値は従来互換の録画再生
- 戦績管理一覧の任意列へ相手デッキを追加し、初期非表示のまま列メニューから表示できるようにした
- 旧`ui-preferences.json`では新しい表示列とダブルクリック設定を既定値で補完し、DBスキーマ変更なしで既存データを保持する
- Ruff、全474テスト、CLI/GUI one-file EXEビルド、両スモークに合格した
- ローカルCLI EXE SHA-256: `CC735DDCB38134179057D9966D8D699A8EDB6AA88C7C1375F11B4C028DA5A247`
- ローカルGUI EXE SHA-256: `7CBF3A13E36AC76BFFB2F477D3774E5089DFEAEB4FBFA000FC93752CD624116B`

追跡: [V1.0.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/54)、親Issue #393、子Issue #394 - #398

## V1.0.1: 正式版初回の不具合修正と操作改善 - 2026-08-16

- タグ付き手動戦績の削除順序を修正し、`FOREIGN KEY constraint failed`を解消した
- FFmpegの空フォルダ導入、既存`ffmpeg.exe`選択、全実行時データの安全な保存先変更を追加した
- MP4準備を録画ID入力から、開始日時・デッキ・勝敗・ファイル名による候補選択へ変更した
- 統計へシーズン別集計を追加した
- 戦績管理の任意列表示と、勝敗・先後・コイン・登録元の背景色設定を追加した
- 録画ページの手動登録を「戦績を追加（録画なし）」へ明確化した
- GitHub正式Releaseの更新確認、SHA-256検証、明示適用、異常終了時復元を追加した
- 専用アイコンを制作し、GUI、タスクバー、CLI/GUI EXEへ適用した
- DBスキーマを変更せず、保存先変更時も旧データを自動削除しない契約を維持した
- Ruff、全474テスト、実FFmpeg試験2件、ネイティブ音声ヘルパー、CLI/GUI one-file EXEビルド、両スモーク、GUI目視確認に合格した
- ローカルCLI EXE SHA-256: `12D76281A034A2FF20AE9D17DF2EF622BBC3C9B3E8CBD78F509CBBA51864B50B`
- ローカルGUI EXE SHA-256: `EA2D95C74FA0D581E690D08C257A251777CFFA27D80946479D876C5AA3E89E1B`

追跡: [V1.0.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/53)、Issue #381 - #392

## V1.0.0: 中核機能完成 - 2026-08-16

- V0.2.0からV0.26.2までに実装・検証した録画、閲覧、戦績、タイムライン、統計、シーズン、データ保全、単体音声、CSV移行、アップロード準備、クリーンアンインストールを正式版として確定した
- V0.26.2で全Issue・Milestone・ロードマップ・検証記録を整合し、空の実行時ルートによる最終E2Eを完了した
- 2026-08-16に受領したユーザーの明示的なV1.0.0変更指示に基づき、コード、README、Release tooling、ロードマップを1.0.0へ更新した
- 日本語UIと対応解像度、Process Loopback要件、未署名EXE、YouTube直接アップロード対象外の既知制約を維持した
- Ruff、全463テスト、実FFmpeg試験2件、CLI/GUI one-file EXEビルド、両スモーク、正式版番号でのクリーン環境E2Eを通過した
- ローカルCLI EXE SHA-256: `410A7F9BB75FD66A0ECCDB14DAECC7492AC55DF10344DA3AF0EEBE7E63AAA616`
- ローカルGUI EXE SHA-256: `498EF1572C6DDB6204B1384BD0794FAB4470BA89BA18FC168F202B1E284510D7`

追跡: [V1.0.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/52)、Issue #378 - #380

## V0.26.2: V1移行準備 - 2026-08-16

- 仕様変更後に残っていたIssue #250へ、V0.25.0で`coin_toss_outcome`を撤去した理由とDB・互換テストの証拠を記録して完了し、V0.20.0 Milestoneを閉じた
- ロードマップのV0.20.0、V0.21.2、V0.21.3、V0.22.0、V0.25.0を現行仕様と公開状態へ整合した
- V0.25.0・V0.26.0検証記録の公開状態と、V0.26.0公式ReleaseのSHA-256を追記した
- V1.0.0判断範囲をV0.2.0からV0.26.2までへ更新し、ユーザーの明示指示まで0.xを維持する契約を再確認した
- 空の実行時ルートを使う最終クリーン環境E2Eを追加し、初期化、戦績CRUD、CSV往復、バックアップ、整合性診断、アンインストール境界に合格した
- Ruff、全463テスト、実FFmpeg試験2件、CLI/GUI one-file EXEビルド、両スモークを通過した
- ローカルCLI EXE SHA-256: `F6CE32FF44E8EBCB2821287898D482A9264D3F71260CE1A70B8DE5CE050BF0BA`
- ローカルGUI EXE SHA-256: `33D031FB7B9FC03B69167939F25E627044A31F644BF6C9F532A823DBB5FBA560`

追跡: [V0.26.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/51)、Issue #374 - #377

## V0.26.1: クリーンアンインストール - 2026-08-16

- 設定、SQLite DB、戦績、録画、ログ、キュー、バックアップ、エクスポート、導入済みFFmpegを含む現在の実行時ルートを一括削除する機能を追加した
- 設定画面の管理データタブへ、削除対象パス、件数、容量、不可逆警告、確認語、最終確認を持つアンインストール画面を追加した
- `uninstall --yes --confirm アンインストール` CLIと、配布EXE自身も削除する`--remove-executable`を追加した
- 録画・自動監視・他処理中の実行を拒否し、ドライブ、ホーム、LocalAppData、不明な任意ルートの削除を拒否するようにした
- シンボリックリンクやジャンクションの参照先をたどらず、アプリの保存領域外へ削除を広げないようにした
- Windows one-file版は一時コピーした終了後クリーナーで使用領域と元EXEを削除し、クリーナー自身を次回再起動時の削除対象へ登録するようにした
- Ruff、全463テスト、CLI/GUI one-file EXEビルド、両スモーク、隔離した配布CLIによる実データ領域・起動EXE削除E2Eを通過した
- ローカルCLI EXE SHA-256: `39E24BE3F0E10F003C7A748D9DF0E89337057AC51AA0C2CC5738CBEC740BB674`
- ローカルGUI EXE SHA-256: `0B420F4095B969869EAAE47712A00EC35FF040C1ED1A2BFB790A42005FEF2D8C`

追跡: [V0.26.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/50)、Issue #369 - #373

## V0.26.0: 単体音声・戦績CSV移行 - 2026-08-16

- Windows Process Loopbackを使い、Master DuelのPIDと子プロセスだけを対象とするx64ネイティブ音声ヘルパーを追加した
- Master Duelのみ、PC全体、入力デバイス、音声なしの4音声モードを設定・診断・GUI・録画プロファイルへ追加した
- 48kHz stereo s16leを録画単位の名前付きパイプでFFmpegへ渡し、AAC、ゲイン、同期補正とともに映像へmuxするようにした
- 自動監視待機中に音声ヘルパーを事前起動し、候補録画へ同一プロセスを引き継ぐようにした
- 音声開始失敗・実行中終了・パイプ切断で別音源へ切り替えず、警告と診断を残して映像録画を継続するようにした
- Visual C++静的ランタイムのヘルパー、Microsoft由来の第三者ライセンス表示をPyInstaller one-file配布へ同梱した
- DBスキーマv14で戦績登録元`import`と変更元`import`を追加し、GUI表示「取込」として管理できるようにした
- `ID,開始日時,自分デッキ名,相手デッキ名,勝敗,先後,コイン,対戦種別,シーズン,タグ,メモ`の固定11列CSVを追加した
- UTF-8 BOM、CRLF、引用符、改行、式文字列保護を含むCSV出力とサンプル出力を実装した
- 既存ID更新、未知・空IDの再附番、未登録デッキ・タグ・シーズン作成を事前プレビュー、検証済みバックアップ、単一トランザクションで実装した
- 設定画面へ独立した「CSV入出力」タブを追加し、出力、取込、サンプル保存、件数確認、行別エラー表示を提供した
- Ruff、全453テスト、ネイティブx64ビルド・probe、CLI/GUI one-file EXEビルドと両隔離スモークを通過した
- Windows Media Player PIDを使うProcess Loopback実機PoCで5秒・48kHz・2chの有音取得を確認した
- 無音PCM補完、Process入力キュー、停止順序、libx264 ultrafastを追加し、30分録画で映像1800.866秒、音声1800.429秒、累積ドリフトなしを確認した
- 奇数幅のデスクトップ入力を最大1px補完し、libx264が扱える偶数寸法で保存するようにした
- ローカルCLI EXE SHA-256: `EF76D5C475DB76F4558BCEFBF4DECE22DEF1840A3B1329060B2B4B521B43215D`
- ローカルGUI EXE SHA-256: `EBA78002CC38BE470B55574B744C5E3CD5DC1301CBCC494E26C26551A8FBF97E`

追跡: [V0.26.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/49)、Issue #340 - #365、#368

## V0.25.0: シーズンレポート - 2026-08-16

- 統計ページと同じ確定戦績・期間・非表示デッキ除外契約を共有するシーズンレポートサービスを追加した
- 同種別の直前開始シーズンを既定比較候補とし、任意シーズン・比較なし・期間重複・母数0を扱えるようにした
- 安定デッキIDとカラーを使う全体・先攻・後攻・未設定クロス集計を追加した
- コイン表裏、先後、最終勝敗を独立軸で集計し、未知値を分離した
- コインの表がコイントス勝利、裏が敗北を表すため、重複していた「コイントス勝敗」をGUI、CLI、検索、統計、監査JSON、管理データJSONから削除し、DBスキーマv13で既存列と索引を撤去した
- 対戦記録から開いたタイムラインへモーダル操作権を移し、閉じた後は対戦記録へ戻すようにした
- 対戦記録フォームを縦スクロール対応にし、タグ説明の不要な予約領域を除去してメモと保存操作へ常に到達できるようにした
- 戦績管理ツールバーを2段へ分け、狭い画面でも履歴更新と整合性確認を表示するようにした
- カレンダーの前月・翌月を日付セル幅へ揃え、土曜列上部へ当月に戻る「今月へ」を追加した。当月表示中は無効化する
- 自動録画中の経過時間を、候補録画を開始したFFmpegセッションの開始時刻を起点として500msごとに更新するようにした
- 自動監視を録画中に停止した場合は、結果検出停止と区別できる診断遷移を数値ログへ記録するようにした
- 実戦集計CLIは録画中の監視停止を利用者による中断として別集計し、検出失敗や連続成功試験の対象へ混入させないようにした
- 待機中フレーム取得後の固定待機を廃止し、取得・解析時間を差し引いた残り時間だけ待つことで最大2fpsを阻害しないようにした
- 数値診断の実効fpsを保存件数ではなく全解析フレーム数から算出し、1秒間引きと900件上限で値が人工的に低下しないようにした
- シーズン全期間の空区間を含む日・週推移と使用デッキ比率を追加した
- 1戦以上10戦未満の全体・内訳へ少数標本の注意を表示するようにした
- 既存メモを保持し、目標、良かった点、課題、次期方針をrevision競合検出付きで保存するDBスキーマv12へ更新した
- 参照中シーズンの暗黙アーカイブを廃止し、レポート保存・確認・事前バックアップを経る明示フローへ変更した
- 外部画像・CDN・JavaScript・秘密情報・絶対パスを追加しない印刷可能な単一HTML出力を実装した
- HTMLのエスケープ、上書き確認、Windows予約名拒否、fsync後の原子的保存、出力後に開くGUIを追加した
- 未参照シーズンの削除前にも検証済みバックアップを作成するようにした
- V0.23.0の結果判定回帰修正と実戦集計CLIを含め、Ruff、単体・統合テスト436件（skip 2件）、CLI/GUI EXEビルドと両EXEのV0.25.0隔離スモークを通過した
- 最新候補GUIの初期3戦で、勝利した第3戦を決着前の攻撃演出で敗北と誤確定する早期停止を検出し、敗北を4/5フレーム合意へ変更した
- 一律4/5合意で2fps時に1フレームだけ現れる正規LOSEを見逃す回帰を修正し、高特異度のウルトラワイド下段LOSE形状0.95以上だけを1フレーム確定可能にした
- 補足有効11戦の2fps全区間再評価で開始11/11、盤面11/11、結果11/11、各Precision/Recall 1.000、負例3本で厳格イベント誤検出0を確認した
- 復元困難な旧提供動画への依存を廃止し、有効11戦・負例3本の超横長コーパスと、対戦2本・負例2本の1920x1080ウィンドウコーパスをサイズとSHA-256で固定した
- 標準16:9で4秒間表示された正規LOSEを見逃す不具合を修正し、中央白文字形状と敗北4/5合意で攻撃演出から分離した
- 実戦集計CLIへ結果停止後の盤面継続検査を追加し、旧集計の3/3合格を2/3合格・第3戦不合格へ訂正した
- 2セッションの実戦ログで、結果を見逃した録画が次対戦境界まで継続し、次録画が再検出まで約8秒欠ける問題を確認した
- 次対戦境界の時間窓合意が成立した同じ監視ループで前録画を停止し、候補を次録画へ即時引き継ぐようにした。境界スコア、合意、引継ぎ時間は数値診断の遷移へ保存する
- fix340 GUIによる初期3戦は開始・盤面確定・結果停止3/3で合格した。直前の補足2戦も2/2で合格し、5録画すべてFFmpeg終了コード0と有効な終了映像を確認した
- fix340の本検証1セッションでは診断上12戦連続成功となったが、2戦目のブラック・マジシャン・ガール召喚カットインをウルトラワイド版LOSEと誤認し、決着前に停止していた
- 単一フレームのウルトラワイド版LOSEを、盤面性0.30未満かつカード演出オーバーレイ0.50以下へ限定し、検出器をversion 4へ更新した
- オフラインmanifestへ候補録画開始済みを表す`assume_started`を追加し、保存済み1セッション12録画を先頭から最後まで2fpsで再生した。正規結果11/11、召喚カットイン負例0/1、Precision/Recall 1.000を確認した
- V0.23.0の自動監視信頼性とV0.24.0のデータ保全を本Releaseへ統合し、両版の単独タグ・Releaseは作成しない
- ローカルCLI EXE SHA-256: `D43F7DB0DE2ADC205AEC3B9AA6D0BD0B167901B4B30007E51B9427018321CC2A`
- ローカルGUI EXE SHA-256: `8B54FBA3459986F109F938F6AD3F4CCD3E6A1505700D9FC2691B808B30BC16A5`

追跡: [V0.25.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/47)、Issue #315 - #327、#333 - #339、#366

## V0.24.0: データ保全 - V0.25.0へ統合・単独未公開

- SQLite Backup API、SQLite整合性、member SHA-256を持つ原子的`.mdrl-backup`を追加した
- DB移行、管理データ取込・初期化、復元、録画再関連付け、戦績削除前のバックアップを接続した
- 通常世代を最大20世代・合計256MiBで管理し、移行・復元前の保護世代を維持するようにした
- 復元候補を別パスで検証し、管理データ件数をプレビューしてから切り替え、途中失敗時はDBと設定を戻すようにした
- 設定、DB、外部キー、録画欠損・サイズ不一致・未登録録画を変更せず分類する統合診断を追加した
- 保存先外・空・別履歴使用中・確認後変更を拒否する録画ファイル再関連付けを追加した
- 日時、勝敗、先後、デッキ、対戦種別、録画ハッシュから重複候補を提示し、タグ・メモ・関連録画を比較できるGUIを追加した
- 設定画面へDB状態、最終バックアップ、総容量、世代一覧、手動作成、診断、復元を集約した
- 権限、容量、安全バックアップ、改ざん、将来スキーマ、復元後検証失敗を注入し、元DBと録画を維持するテストを追加した
- Ruff、単体テスト399件（skip 2件）、CLI/GUI EXEビルド、両EXEのV0.24.0隔離スモークを通過した
- ローカルCLI EXE SHA-256: `6C1C9357A0729EB3B1C7778B9394F3845C5EA809A6FBCBAE2A8A872CFCCF66CD`
- ローカルGUI EXE SHA-256: `CF72A4ADA816964503BD646F8B008E45947F357C1134636101BEBB1452EC7070`
- 障害注入とローカル検証を完了し、V0.25.0累積Releaseへ統合する

追跡: [V0.24.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/46)、Issue #302 - #314

## V0.23.0: 自動監視信頼性 - V0.25.0へ統合・単独未公開

- 手動録画、自動監視、候補録画、確定録画、停止、失敗を単一の操作状態機械へ統合した
- GUIの録画・監視・戦績更新ボタンを状態機械の操作許可表へ接続した
- 開始、盤面、ターン、結果をイベント別のスコア・閾値・時間窓契約へ分離した
- 盤面上の攻撃演出を結果と誤確定しない4/5フレーム合意と、ウルトラワイドで短く表示される敗北結果の専用判定を追加した。2026-08-15の実戦で別の攻撃演出による早期敗北判定が再現したため、敗北は信頼度・盤面表示にかかわらず4/5フレーム必須へ強化した
- 実行時録画の有効11戦を2fpsで再評価し、開始・盤面・結果を各11/11、区間外誤検出0件で確認した
- 破損録画2本と非対戦録画1本で開始・盤面・結果の誤検出0件を確認し、非対戦録画のマッチエラー1件は正しく分離した
- 数値診断から候補開始・盤面確定・結果停止・停止後の監視復帰を戦単位で集計し、試験開始時刻以降の最新3戦・10戦連続を判定するCLIを追加した。結果停止後も盤面が続く早期停止疑いを不合格にする検査を追加した
- オフライン評価とライブ監視が同じ`FrameAnalysis`解析契約を使用することを契約テストで固定した
- 表示プロファイル・イベント別の適合率、再現率、平均絶対遅延をJSONへ原子的に出力できるようにした
- 画像、ウィンドウタイトル、動画パスを含まない数値診断ZIPをGUIから保存できるようにした
- 利用者向けの監視状態と折りたたみ可能な判定詳細を分離した
- 候補開始、盤面確定、停止、失敗のWindows通知と通知無効設定を追加した
- 最新の結果誤判定修正を含め、Ruff、単体・統合テスト423件（skip 2件）を通過した
- CLI/GUI EXEビルドと両EXEのV0.23.0隔離スモークを通過した
- ローカルCLI EXE SHA-256: `BBC09FA307E20762E99FDC8A9AF303BAF4D1355CAFAE2BC700BE2522D408E87B`
- ローカルGUI EXE SHA-256: `2530D4D4F1BB5BDBFAC68862B113159E457578CBD221535DC89233D1851F1614`
- 超横長14本と1920x1080ウィンドウ4本を固定コーパスとして2fps評価に合格した。保存済み本検証セッション12録画の全編再生で正規結果11/11、既知偽陽性0/1を確認し、V0.25.0累積Releaseへ統合する

追跡: [V0.23.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/45)、Issue #289 - #301、#329 - #332

## V0.22.0: 戦績入力体験 - 2026-08-13

- 録画終了後と初期画面から、勝敗・先後・自分デッキ・シーズンを短時間で登録できる簡易入力を追加した
- 直近の確認済み戦績、前回値、開催中シーズン、デッキ・タグ使用頻度から根拠付き候補を算出するサービスを追加した
- 未入力、録画付きdraft、手動draftを日時順に処理する未完了キューを追加した
- 戦績一覧を複数選択に対応させ、シーズン・自分デッキ・対戦種別・タグを一括更新できるようにした
- 複合フィルターの保存、呼出し、上書き、削除と管理データJSON互換を追加した
- DBスキーマをv11へ更新し、`saved_duel_filters`を追加した
- Ruff、単体テスト368件（skip 2件）、CLI/GUI EXEビルド、両EXEのV0.22.0スモークを通過した
- ローカルCLI EXE SHA-256: `18D4743A4E2691BB74B3DDD224E69E5C277724D7DC0B9DCE1AFD3792D364A2E0`
- ローカルGUI EXE SHA-256: `BE660131B33591917B49FF901F36975E013C7DF8D5DCDC45B3B8600351D53979`

追跡: [V0.22.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/44)、Issue #276 - #288

## V0.21.3: V0.22.0-V0.25.0実装計画策定 - 2026-08-13

- V0.22.0を「戦績入力体験」とし、簡易入力、候補値、未完了連続処理、一括編集、保存済みフィルターを計画した
- V0.23.0を「自動監視信頼性」とし、単一状態機械、イベント別評価、診断レポート、Windows通知、実戦E2Eを計画した
- V0.24.0を「データ保全」とし、原子的バックアップ、検証付き復元、統合診断、録画再関連付け、重複検出を計画した
- V0.25.0を「シーズンレポート」とし、期間比較、デッキ・先後・コイントス分析、推移、振り返り、HTML出力を計画した
- 4 Milestone、4バージョンラベル、4親Issue、48子Issueを作成し、Issue #276 - #327へ細分化した
- `docs/implementation-plan-v0.22-v0.25.md`を追加し、実装順、重要な設計判断、バージョン間依存、完了条件を記録した
- 本版ではV0.22.0以降の機能実装を行わず、V0.21.2の既存挙動を維持する
- Ruff、単体テスト362件（skip 2件）、CLI/GUI EXEビルド、両EXEのV0.21.3スモークを通過した
- CLI EXE SHA-256: `BCCE431F8C7390C2D7D8A0E50F56A14914BAD0B5DDB53EF10151842EC5B1BE40`
- GUI EXE SHA-256: `F42F62F9047D321E6E5638A6585290CBFD0795E3E0DC198EEDB2F5F7E7F144B6`

追跡: [V0.21.3 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/48)、Issue #328、[V0.22.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/44)から[V0.25.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/47)、Issue #276 - #327

## V0.21.2: 初期画面・戦績更新排他 - 2026-08-13

- 初期画面の録画操作、状態、ログを維持し、録画なしの「戦績を追加」を追加した
- 開催中シーズンをランク優先、終了期限順で最大2件表示し、勝率と対戦内訳からレポートへ遷移できるようにした
- 自動監視中は新規入力を無効化し、既存戦績編集は「自動監視中のため更新できません」を表示する読み取り専用画面とした
- 録画・自動監視中の作成、更新、削除をアプリケーションサービスでも拒否するようにした
- 戦績管理一覧へ録画あり・なしを統合し、手動戦績では録画固有操作だけを無効化した
- 手動戦績の対戦日時、内容、タグ、メモの追加・後日編集・削除をGUIへ接続した
- Ruff、単体テスト362件（2件スキップ）、CLI/GUI EXEビルド、両EXEスモーク、ProductVersion 0.21.2を確認した
- ローカルビルドSHA-256はCLI `10F7C349EA3B2710FEFA4A5F8A968C92DA3C7BCAE99632D300D658AD9E84A6CD`、GUI `40A78768597DF14C508325D16D71AF99E67271C79AC457D513A4EA3B8C0906BD`

追跡: [V0.21.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/42)、Issue #270 - #275

## V0.21.1: 録画なし戦績の事後入力 - 2026-08-13

- DBをv10へ更新し、対戦固有の`duel_id`、任意の`recording_id`、入力元、対戦日時を追加した
- 既存の録画付き戦績を保持したまま、録画なし戦績を作成・更新できるRepositoryとアプリケーションAPIを追加した
- `duel create` CLIを追加し、録画ファイルや録画履歴を作らずに確定済み戦績を登録できるようにした
- 録画なし戦績も全体・条件付き統計へ含め、録画付き戦績は正常完了した録画だけを集計する規則を維持した
- タグ、変更監査、録画履歴削除、デッキ・タグ名変更を`duel_id`参照へ移行した
- V0.19/V0.20形式の管理データJSONをv10形式へ補完して取り込む互換処理を追加した
- 録画履歴を戦績管理へ改め、録画あり・なしの一元表示、手入力追加・編集・削除、登録元フィルターを実装した

追跡: [V0.21.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/41)、Issue #259 - #269

## V0.20.0: コイントス記録・統計 - 2026-08-13

- コインの面（表・裏・未設定）とプレイヤー視点のコイントス勝敗（勝ち・負け・未設定）を追加した
- 両項目を先攻・後攻と独立してDB v9へ保存し、対戦記録の変更監査へ含めた
- 録画終了後・後日編集、録画履歴の表示と複合フィルター、CLIへ接続した
- 統計へコイントス条件と「コイントス別」内訳を追加した
- V0.19.x以前の管理データJSONは新項目を`unknown`で補完して取り込む
- 既存記録の未設定は戦績管理未完了件数へ含めず、自動画面判定は今後の別機能とした
- Ruff、単体テスト354件（2件スキップ）、CLI/GUI EXEビルド、両EXEスモークを確認した

追跡: [V0.20.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/40)、Issue #250 - #258

## V0.19.2: 統計カレンダーピッカー表示修正 - 2026-08-13

- V0.19.1で統計フィルターを等幅化した際、開始日・終了日の入力欄に隣接するカレンダーボタンが表示領域外へ押し出される回帰を修正した
- 日付入力欄の右端へカレンダーアイコンを固定し、直接入力とカレンダー選択を併用できる専用コントロールへ変更した
- 条件適用・クリアの幅を確保し、980x640でも日付、各種条件、実行操作が同じ行に収まるようにした
- GUIスモーク契約へ開始日・終了日のカレンダーピッカーを追加した
- 1180x760と980x640で両ピッカーの表示を確認し、980x640でカレンダーからISO形式の日付を選択できることを確認した
- Ruff、単体テスト349件（2件スキップ）、CLI/GUI EXEビルド、両EXEスモーク、ProductVersion 0.19.2を確認した
- ローカルビルドSHA-256はCLI `6B4F651692CC8D116951C7F3CBDB77C1BE8CB3FFF644BF404CE9701C3D9127BD`、GUI `3C9FC0ADBD260E7786D4A43FBAB0AF64CF9072E08758A9E9B1CE302F589F2BCD`。GitHub Actions公開成果物はRelease添付の`.sha256`を正とする
- V1.0.0には更新していない

追跡: [V0.19.2 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/39)、Issue #249

## V0.19.1: GUI一貫性・データ管理改善 - 2026-08-12

- 録画、統計、デッキ、タグ、シーズン、設定の入力欄・プルダウン・ボタン高を共通化し、レトロ調のフォーム背景をMaterial配色へ統一した
- 録画履歴のフィルターとクリアをアイコン操作に変更し、保存場所と整合性確認のアイコンをフォルダ・レポートへ改めた
- 録画履歴から音声列を廃止し、開始日時の右に自分デッキの実色ラインとデッキ名を追加した
- シーズンを一覧上部の日本語入力フォームから追加・更新できるようにし、対戦種別の重複入力を種別からの自動決定へ置換した
- シーズン種別を縦のカラー指標で表示し、ライブ集計とレポートメモを専用画面へまとめた
- 履歴・デッキ・タグ・シーズンの管理データを検証付きJSONとして一括入出力する機能を設定画面へ追加した
- 履歴・デッキ・タグ・シーズンの個別初期化へ、確認ダイアログ、確認文字列、操作前SQLiteバックアップ、失敗時復元を追加した。履歴初期化は動画ファイルを削除しない
- Ruff、単体テスト349件（2件スキップ）、CLI/GUI EXEビルド、両EXEスモーク、ProductVersion 0.19.1を確認した
- ローカルビルドSHA-256はCLI `79E28D4FC33ECD67BB1C4108CBB7A48635D7C5FD8424D9BC373D8D756EEF9B6F`、GUI `8E830DBB7506BF099B03E7D2FE7B589ECBF8D57F8BC959DEA65F6E9E8964F5AC`。GitHub Actions公開成果物はRelease添付の`.sha256`を正とする
- V1.0.0には更新していない

追跡: [V0.19.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/38)、Issue #239 - #248

## V0.19.0: シーズン管理 - 2026-08-12

- ランク、イベント、カスタムのシーズン管理、説明、レポートメモ、アーカイブを追加した
- 対戦記録へシーズンを手動割り当てし、期間外保存時の確認とシーズン別ライブ集計を追加した
- 録画履歴へシーズン・自分デッキ・相手デッキ・複数タグのSQLフィルターと即時クリアを追加した
- デッキへカラー、相手デッキのみ、履歴・統計候補の非表示フラグと安定ID参照を追加した
- V0.18.1で計画したカレンダー入力、先攻・後攻勝率、デッキ別全体、デッキ先後別を統合した
- 復旧GUI、CLI、サービス、修復モジュール、専用テストを撤去し、`status` JSONをスキーマ2へ更新した
- DB v7で復旧情報を安全に撤去し、v8でシーズンとデッキ参照を追加した。移行前バックアップ、成果物退避、失敗時復元を検証した
- 左下の利用状態を、緑・アンバー・赤のアイコンと状態名を併記する表示へ変更した
- Ruff、単体テスト346件（2件スキップ）、CLI/GUI EXEビルド、両EXEスモーク、ProductVersion 0.19.0を確認した
- ローカルビルドSHA-256はCLI `6B69D5E8294421BFA668FFB73A9D3BBDEC0415FB53FEE905D48F5C5FE8C76E8E`、GUI `5B2C3780B9833DDCA268BEC146F8470F3C675146F397E8342D106EFBAF970665`。GitHub Actions公開成果物はRelease添付の`.sha256`を正とする

追跡: [V0.19.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/37)、Issue #225 - #238。V0.18.1のIssue #217 - #224を本リリースへ統合した。

バージョンは `メジャー.マイナー.Fix` で管理します。`main` への通常pushではFixを1つ増やし、中核機能を完了するpushでは次のマイナーバージョンの `.0` を設定します。すべてのバージョン変更をこの文書へ新しい順で記録します。

## V0.18.0: 戦績統計・Material 3 GUI刷新 - 2026-08-12

### 新機能

- 全体勝率を統計ページ最上部へ固定表示し、条件適用後の勝率と対戦数・勝敗・引分を並列表示した
- 期間、デッキ、タグ安定ID、先後を任意に組み合わせる読み取り専用のSQLite集計サービスを追加した
- 日・週・月単位の勝利数・勝率推移を空期間も含めて返し、棒と線のチャートで表示した
- デッキ別・先後別の対戦数、勝敗、引分、勝率をタブで比較できるようにした

### GUI刷新

- Material 3の色ロールを基準に、背景、サーフェス、主要操作、選択、境界、文字色を再定義した
- 明るいナビゲーションと選択インジケータ、タイトル・本文・補助文・数値のタイポグラフィ階層を全画面へ適用した
- ボタン、表、タブの通常・ホバー・押下・選択・無効状態を一貫した表示へ変更した
- 1180x760と最小980x640の統計ページで、フィルター、数値、タブ、チャートの非重複を確認した

### 集計契約

- `recordings.state='completed'`、`duel_records.status='confirmed'`、勝敗が`win/loss/draw`の対戦だけを分母へ含める
- 勝率を`勝利数 / (勝利数 + 敗北数 + 引分数)`とし、引分を対戦数へ含める
- 期間は端点を含むローカル日付、タグは名称変更に影響されない`duel_record_tag_links.tag_entry_id`で絞り込む
- SQLiteスキーマと既存録画・設定・対戦記録を変更しない

### 公開条件

- 集計単体テストを含む353件、Ruff、実SQLite集計、8ページの両サイズ描画、CLI/GUI EXEビルド、両スモークを完了した
- ProductVersion 0.18.0、FileVersion 0.18.0.0、GUI SHA-256 `0E3E2420603F99088E3644A127DEF6A77CF105AAEAFBAEB7E6AFA247F0FA204D`を確認した
- V0.17.2からV0.17.4までの未公開修正を本リリースへ統合し、各版の単独タグとReleaseは作成しない

追跡: [V0.18.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/35)、Issue #207 - #216

## V0.17.4: 対戦終了境界・GUI識別性修正 - V0.18.0へ統合・単独未公開

### 修正

- V0.17.3実ログ`20260811T121356Z-7d94925c.json`と録画`36eb44954e6a4d649c5c5533fceef652`を解析し、前戦の結果を取り逃した後、録画開始から約572秒で次戦コイントスを検出しても録画中の状態機械が開始候補を棄却し、942.2秒まで録画が継続した原因を特定した
- 盤面確定から15秒以上経過後、コイントスが直近4フレーム中2件成立した場合を`duel_boundary`として扱い、1秒後に前戦の録画を停止して次戦監視へ戻すフォールバックを追加した
- 勝敗種別を持つ信頼度0.95以上の結果だけを単一フレームで確定し、実ログの0.8171の先後表示類似フレームは従来どおり複数フレーム合意を要求する
- 録画履歴の小さな行内文字記号を廃止し、選択行へ作用する標準形状の大型アイコン、ツールチップ、無効状態、既存キーボード操作へ変更した
- 復旧状態と失敗分類を日本語化し、修復可能件数・修復不可件数・理由を表示して、修復可能な非空ファイルだけ修復操作を有効にした
- タグ一覧のカラー列へ色コードと実色スウォッチを表示し、編集ボタンにも選択色と可読性を保つ文字色を反映した

### 復旧範囲

- 復旧は中断・破損した非空の録画コンテナを別ファイルへstream copyし、ffprobeで検証する機能である
- 現在の実データにある4件はすべて`output_empty`の0バイト録画であり、映像データが存在しないため修復不可である
- 正常完了動画の誤った開始・終了境界は復旧機能の対象外とし、自動判定の修正で再発を防ぐ

### 公開条件

- 実ログ再現テスト、全単体テスト、Ruff、CLI/GUIビルド、両スモークを完了する
- ローカルV0.17.4 EXEで、結果停止と結果取り逃し時の次戦境界停止を実戦確認する
- 実装Issue #200 - #206は自動検証後に完了した。単独の`v0.17.4`タグとGitHub Releaseは作成せず、修正と検証記録をV0.18.0へ統合する

### データ影響

- SQLiteスキーマ、既存録画、設定、数値診断を変更・削除しない
- 復旧は従来どおり元録画を上書きせず、別ファイルへ出力する

追跡: [V0.17.4 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/34)、Issue #199 - #206

## V0.17.3: フルスクリーン録画静止修正 - V0.18.0へ統合・単独未公開

### 修正

- V0.17.2の初期実戦3戦で録画本体の`title=Master Duel`が3440x1440フルスクリーンで静止フレームとなることを確認し、Master Duelの実録画を物理座標の`gdigrab desktop`へ切り替えた
- 自動録画で判定時に観測したクライアント領域の`offset_x`、`offset_y`、`video_size`を実録画コマンドへ引き継ぐ
- 判定用入力を1280pxからオフライン評価と同じ640pxへ統一し、実機解析速度約8.07fpsを確認した
- Master Duel録画が`title=`入力へ戻らないこと、負座標を含む物理座標が保持されることをテストで固定した
- GUIへ高コントラストの録画状態表示帯を追加し、自動監視の録画待機、盤面確定前の候補録画、盤面確定後の本録画を文言と色で区別した
- 自動監視イベントと手動録画状態が同じ表示を上書きしないよう、録画状態の更新経路を共通化した
- GUI共通ヘッダーへ戦績管理未完了件数を追加し、正常完了録画のうち対戦記録がない、または未確認の件数をSQLite全件集計で表示した
- 未完了件数表示から録画履歴へ移動でき、録画完了、対戦記録保存、履歴削除後に件数を自動更新するようにした

### 公開条件

- 修正後のローカルV0.17.3 EXEで初期3戦を再実施し、開始・盤面・結果・監視復帰と保存動画の連続性を確認する
- 調整後10戦連続で自動開始、盤面確定、結果停止、次対戦への監視復帰、静止動画0件を確認する
- 上記完了前はIssue #192 - #198を閉じず、`main`へのpush、`v0.17.3`タグ、GitHub Releaseを行わない

### データ影響

- SQLiteスキーマ、既存録画、設定、数値診断を変更・削除しない
- 座標録画のため、Master Duelの上に重なった別ウィンドウも保存動画に映る制約がある

追跡: [V0.17.3 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/33)、Issue #192 - #198

## V0.17.2: 自動監視・ライブ判定修正 - 実戦不合格・未公開

### 修正

- V0.17.0・V0.17.1でオフライン動画の部分評価だけを根拠にライブ自動監視を完了扱いとし、実環境で候補0のまま録画開始しない問題を見逃した検証不足を明記する
- フレームごとに`title=masterduel`のFFmpegを起動する方式を廃止し、物理ピクセルへ補正したMaster Duelクライアント領域を単一の常駐FFmpegで最大2fps取得する
- ウィンドウ移動・リサイズ・モニター変更と3秒のフレーム停止を検出し、段階的待機でストリームを復旧する
- コイントス2/4、盤面3/5、結果2/4を中心とするイベント別時間窓合意へ変更し、単発欠落や一時オーバーレイで合意を全消去しない
- 候補録画開始前と録画中の判定に同一ストリームを使い、手動録画を画面判定から独立させたまま維持する
- GUIへ取得元、解像度、表示プロファイル、実効fps、状態、各スコア、合意数、再起動数を表示する
- 画像、ウィンドウタイトル、動画パスを含まない上限付き数値診断を`user_data/logs/visual-monitor/`へ追加する
- CONNECTING・VS・高速移動演出・関連カード画面を開始、エラー、結果から分離するイベント別特徴を追加する
- 提供動画14本の全区間2fps評価で、開始11/11、盤面11/11、結果11/11、エラー2/2、注釈済みターン4/4を確認した

### 公開条件

- 提供動画14本の2fps評価は完了した。1本はユーザー提供メモに対して最大1秒差、残り13本は広い許容区間での評価である
- 初期3戦は自動開始3/3・盤面確定3/3・結果停止2/3だったが、保存動画3/3が同一コイントスフレームで静止していたため全面不合格とする
- V0.17.2の`main` push、タグ、GitHub Releaseは行わず、静止録画の修正と再検証をV0.17.3へ引き継ぐ

### データ影響

- SQLiteスキーマと既存録画は変更しない
- 数値診断は1秒1件、1セッション900件、最新10セッション、合計2MiBを上限とし、既存ログや録画を削除しない

## V0.17.1: Windows短縮パス環境の台帳テスト修正 - 2026-08-11

### 修正

- GitHub ActionsのWindows runnerで、一時フォルダの8.3短縮パスと正規化済みパスを同一動画として比較するように画面判定台帳テストを修正した
- V0.17.0のデッキ・タグ管理、音声入力、固有UI判定、録画履歴GUIの製品動作は変更しない

### データ影響

- SQLite、録画、設定、ローカル動画台帳への追加変更はない

## V0.17.0: 録画・対戦情報の統合管理 - 2026-08-11

### デッキ・タグ管理

- デッキ名とタグを個別のGUI画面とサービスAPIへ分離した
- 両項目へ説明、タグへ`#RRGGBB`カラーと色見本を追加した
- 対戦記録との関連を安定IDで保持し、参照中の削除はアーカイブ、未使用項目は恒久削除とした
- 版5の既存名称とタグを版6へ移行し、将来のタグ集計で同一項目を追跡できるようにした

### 音声入力

- GUIでFFmpegの音声入力をシステム系・マイク系等の種別付きで列挙し、選択・保存・再読込できるようにした
- 入力を短時間開いて有音・無音・利用不可を区別する事前テストを追加した
- 選択した1入力をAAC、既定48kHz・2chで録音し、ゲイン設定と非同期リサンプルによる同期補正を追加した
- 録音元、音声付き完了、音声失敗理由を録画履歴とGUIへ表示した
- 同時ミックスは対象外とし、システム音またはマイクのどちらか1つを選ぶ仕様を明記した

### Master Duel固有UI判定

- 日本語UIの1920x1080ウィンドウと3440x1440フルスクリーン向け相対ROIプロファイルを追加した
- コイントス、先攻・後攻、盤面、一時オーバーレイ、ターン、VICTORY・LOSE・サレンダー、マッチエラー、リプレイの状態機械を追加した
- コイントスで候補録画を開始し、盤面で確定、エラー・リプレイ・45秒タイムアウトでは候補を破棄して監視へ戻すようにした
- 結果検出後に3秒の後余白を録画し、勝敗・先後を対戦記録へ保存するようにした
- リプレイをライブ自動録画から除外し、オフライン解析ではターン・結果を抽出できるようにした
- ローカル台帳、FFmpegストリーミング評価器、適合率・再現率・時刻誤差のMarkdownレポートを追加した
- 判定画像はメモリだけで扱い、DB、ログ、EXE、リポジトリへ保存しない

### GUIと録画履歴

- 録画履歴を開始日時、勝敗、先後、対戦種別、時間、サイズ、音声状態中心の表示へ変更し、録画IDを診断画面へ移した
- 各行へ再生、対戦記録編集、Explorer、削除アイコンを追加した
- アイコンへツールチップ、フォーカス、キーボード操作を追加し、削除時は対象と関連データを確認するようにした
- 更新、追加、保存、削除など意味が明確なGUI操作へ共通アイコンボタンを適用した

### データと互換性

- 履歴SQLiteをスキーマ版6へ移行し、カタログ説明・色・アーカイブ、対戦記録リンク、録音状態を追加した
- 移行前SQLiteバックアップ、単一トランザクション移行、失敗時ロールバックを維持する
- 手動録画は画面イベント判定から独立し、既存の録画、復旧、準備キュー、CLI契約を維持する

## V0.16.10: Windows短縮パス環境のCI修正 - 2026-08-10

### 修正

- GitHub ActionsのWindows runnerで、一時フォルダの8.3短縮パスと正規化済みパスを同一ファイルとして比較するように履歴削除テストを修正した
- 製品コードが録画保存先配下の正規化済み絶対パスを返す契約は変更しない
- V0.16.9の対戦記録入力、辞書、前回値引継ぎ、履歴削除機能をそのまま含む

### データ影響

- SQLite、録画ファイル、設定、GUI動作への追加変更はない

## V0.16.9: 対戦記録入力と履歴削除の改善 - 2026-08-10

### 追加・変更

- 対戦記録の状態、勝敗、先後、対戦種別を日本語の選択肢で表示するようにした
- 自分デッキと相手デッキを共通候補から選択でき、一覧にない日本語名も入力できるようにした
- 複数タグを候補から追加または自由入力できるようにした
- デッキ名とタグを一つのサイドメニュー画面で追加・名称変更・削除できるようにした
- 対戦種別、自分デッキ、相手デッキ、タグを次の新規対戦記録へ引き継ぐようにした
- 録画履歴から元録画、復旧成果物、対戦記録、変更履歴、タグ、タイムラインを一括削除できるようにした

### データと安全性

- 履歴SQLiteをスキーマ版5へ移行し、デッキ名・タグ辞書と前回入力を追加した
- 移行時に既存対戦記録のデッキ名とタグを辞書へ取り込み、移行前DBバックアップを維持する
- GUIの日本語表示は既存の英語内部値へ変換し、SQLiteとCLIの互換性を維持する
- 履歴削除では対象ファイルを録画保存先内へ退避してから関連DB行を1トランザクションで削除し、DB失敗時は退避ファイルを復元する
- 録画または自動監視の実行中と、録画保存先外を参照する履歴の削除を拒否する

## V0.16.8: 録画開始中のGUI応答維持 - 2026-08-09

### 修正

- 手動録画開始中に環境診断やFFmpeg準備をサービスロック内で実行しないようにした
- サービスロックを開始予約と開始結果の公開に限定し、GUI状態取得のロック待ちを解消した
- GUIはバックグラウンド操作中の同期ポーリングを停止し、Tkメインスレッドの描画を継続するようにした
- 録画開始中の二重開始、自動監視開始、停止、アプリ終了を具体的なエラーまたは案内で拒否するようにした
- 開始失敗時も予約状態を必ず解放し、再試行できるようにした

### 互換性とデータ影響

- 録画形式、判定条件、FFmpeg設定、既存録画、SQLiteデータを変更しない
- 録画開始ボタンの非同期操作契約とGUI応答性だけを変更する

### GUI応答検証

- 録画開始処理を6秒間待機させる実Tk GUI試験を行い、100ミリ秒間隔のハートビートが60回継続することを確認した
- 5.5秒時点で開始処理が継続中でもWindowsの`IsHungAppWindow`がfalseであることを確認した

## V0.16.7: 録画用FFmpegのコンソール抑止 - 2026-08-09

### 修正

- Windowsで実際の録画を担当する長時間FFmpegへ`CREATE_NO_WINDOW`を適用した
- 録画開始前にWindowsの重大エラーダイアログ抑止設定を適用するようにした
- 診断・開始判定・録画でWindows子プロセスの非表示起動方針を統一した
- FFmpegの標準入力による正常停止と標準エラー収集は従来どおり維持した
- GUIとCLIの手動録画では画面イベント判定ワーカーを起動せず、ユーザー操作による録画を自動判定から分離した
- 手動録画から同じMaster Duelウィンドウに対する判定用FFmpegの追加取得を排除した
- 手動録画開始時にMaster Duelウィンドウを再取得し、一覧更新時の古い利用可否やハンドルを使用しないようにした

### 互換性とデータ影響

- Windows以外ではcreation flagsを0として従来のプロセス起動を維持する
- 録画形式、判定条件、設定、既存録画、SQLiteデータを変更しない
- Windows SmartScreenおよびUACの確認画面はOSのセキュリティ機能であり、この変更の抑止対象外とする

### 実画面検証

- 実Master Duelウィンドウをソース版で3秒手動録画し、判定`disabled`、43,809 bytes、終了コード0を確認した
- パッケージ版CLIで同じウィンドウを3秒手動録画し、判定処理0・破棄0、43,543 bytes、終了コード0を確認した
- 録画中判定を有効にした比較試験も成功したため、過去の空ファイル原因を二重取得だけに断定せず、手動録画の責務分離として修正した

## V0.16.6: 対戦開始待機表示の集約 - 2026-08-09

### 修正

- 自動監視中の`対戦開始を判定中です`へ待機開始からの経過秒を表示するようにした
- 同じ待機状態をアクティビティへ追加し続けず、経過秒付きの1行を更新するようにした
- 対戦開始の確定、自動判定状態の変更、監視停止時に古い待機行を除去するようにした
- 録画開始、停止、エラーなど待機以外のアクティビティ履歴は従来どおり保持する

### 互換性とデータ影響

- 対戦開始の判定条件、FFmpeg、設定、既存録画、SQLiteデータを変更しない
- 表示上の経過秒は監視対象のMaster Duelウィンドウを捕捉した時点から計測し、対象変更または再監視でリセットする

## V0.16.5: 進行中盤面からの自動録画開始 - 2026-08-09

### 修正

- 自動録画開始で開始演出の検出を必須条件から補助条件へ変更した
- 開始演出を見逃した場合や対戦途中から監視した場合も、高信頼度の対戦盤面を3フレーム合意すると録画するようにした
- 盤面単独判定では大きな演出と結果画面を除外し、Master Duelウィンドウの表示だけでは開始しない条件を維持した
- 開始判定理由へ「開始演出後の盤面」または「安定した対戦盤面」を記録するようにした

### 実画面検証

- 提供された対戦画面と起動中のMaster Duel実画面で`board=1.00`を確認した
- 両画面とも1・2フレーム目は未確定、3フレーム目で`duel_start`が確定することを確認した
- 既定2fpsでは盤面表示から録画開始までの目安を約1.5秒とする

### 互換性とデータ影響

- FFmpeg、設定、既存録画、SQLiteデータを変更しない
- 自動録画は判定合意後に開始するため、対戦開始直前の映像を含むプリロールは引き続き未実装とする

## V0.16.4: Windows FFmpeg起動安定化 - 2026-08-09

### 修正

- WindowsのFFmpeg子プロセスを非表示で起動し、重大エラーとWindows Error Reportingのダイアログを抑止した
- Windows終了コード`0xc0000142`のDLL初期化失敗に限り、200ミリ秒後に1回だけ再試行するようにした
- 能力検査と録画前フレーム取得の両方へ同じ起動方針を適用した
- 自動監視の開始前診断が失敗した場合に、失敗した項目名と理由をアクティビティへ表示するようにした

### 互換性とデータ影響

- FFmpeg本体、設定、既存録画、SQLiteデータを変更しない
- 通常のFFmpegエラーは再試行せず、従来どおり失敗として扱う
- フレーム取得で再試行が発生した場合の最大待機時間は既定で約10.2秒となる

## V0.16.3: FFmpeg 9能力判定修正 - 2026-08-09

### 修正

- FFmpeg 9でdemuxer一覧へ追加されたdeviceフラグ列を入力方式名と誤認しないようにした
- `D d gdigrab`形式から`gdigrab`を抽出し、自動導入したFFmpeg 9.0を録画対応として正しく判定するようにした
- FFmpeg 6から8で使われる従来の`D  gdigrab`形式との互換性を維持した

### 互換性とデータ影響

- FFmpeg本体、設定、既存録画、SQLiteデータを変更しない
- V0.16.2で自動導入済みのFFmpeg 9.0は再インストールせず、そのまま利用できる

## V0.16.2: 対戦開始基準の自動録画 - 2026-08-09

### 変更

- 自動監視の録画開始条件をMaster Duelウィンドウの存在から対戦開始の視覚判定へ変更した
- 録画前に最大2fpsでBMPをメモリ取得し、開始演出から5秒以内の盤面遷移を複数フレームで合意する監視ゲートを追加した
- 対象PID・HWNDの変更、最小化、ウィンドウ消失、フレーム取得失敗時に途中の開始合意を破棄するようにした
- 自動監視の録画対象をMaster Duelウィンドウへ固定し、設定中の別録画モードに切り替わらないようにした
- 録画開始前に検出した`duel_start`を録画経過0ミリ秒の未確認候補として保存するようにした
- 録画開始後は事前取得を停止し、従来の対象固定、5回連続停止確認、クールダウン、FFmpeg再試行を維持した
- GUIの自動判定状態へ待機中、取得失敗、対戦開始確認を表示するようにした

### 互換性とデータ影響

- 既存録画、SQLiteスキーマ、対戦記録、タイムラインを移行・削除・上書きしない
- 対戦前フレームをファイル、DB、ログへ保存しない
- `visual_events_enabled=false`では自動開始できないことを明示し、手動録画は引き続き利用可能とする
- 自動録画の開始は視覚合意分だけ対戦開始後に遅れるため、開始直前の映像を含むプリロールは今後の課題とする

## V0.16.1: 初回FFmpegセットアップと保存先修正 - 2026-08-09

### 変更

- FFmpegが利用できないGUI初回起動時にセットアップ画面を表示するようにした
- 配布元、GPLv3、ダウンロードURL、インストール先を示し、明示許可後だけGyan FFmpeg Buildsのrelease essentials ZIPを取得するようにした
- 公開SHA-256照合、安全なZIP展開、FFmpeg 6.0以上とffprobeの起動確認後だけ設定を保存する導入トランザクションを追加した
- 管理対象FFmpegを`%LOCALAPPDATA%\MasterDuelRecorderLite\tools\ffmpeg`から探索するようにした
- FFmpeg探索の起動待ちを5秒から15秒へ延長し、初回展開やセキュリティ検査による誤判定を抑えた
- EXE実行時の既定データ保存先をEXE隣接`user_data/`から`%LOCALAPPDATA%\MasterDuelRecorderLite`へ変更した
- CLI・GUI実EXEスモークへ、EXE隣接フォルダが作成されないこととLocalAppData分離を追加した

### 互換性とデータ影響

- V0.16.0以前のEXE隣接`user_data/`は自動で移動、削除、上書きしない
- `MDRL_USER_DATA_DIR`、`--user-data-dir`、明示的なproject rootは既定値より優先する
- FFmpeg導入は空または未作成の専用フォルダだけを使用し、失敗時は設定と既存データを変更しない
- CLIでは自動ダウンロードを行わず、既存の手動導入とパス指定を維持する

## V0.16.0: 基本イベント自動判定 - 2026-08-09

### 変更

- Master Duelウィンドウ録画中に最大2fpsでBMPフレームをメモリ内解析する専用ワーカーを追加した
- FFmpeg `gdigrab`が受理しない`hwnd=`入力を`title=`入力へ修正し、PID・HWNDは監視対象の固定情報として維持した
- 16:9表示領域を正規化し、輝度、色優勢、エッジ密度のROI特徴を抽出するオフラインバックエンドを追加した
- 対戦開始、ターン切り替え、勝敗・対戦終了の個別検出器を追加した
- 2フレーム以上の合意、0.70以上の閾値、ターンクールダウン、開始・ターン・結果の状態遷移を追加した
- 自動判定結果を検出器ID・版・信頼度・理由付きの`candidate`として保存し、自動確定しないようにした
- 解析遅延時はフレームを蓄積せず破棄し、解析例外時もFFmpeg録画を継続するようにした
- GUIへ判定状態、処理・破棄数、候補件数、信頼度、理由、確認・却下導線を追加した
- 有効化、最大fps、UI言語、候補閾値の設定とpreflight診断を追加した
- バックエンド選定ADR、合成BMP・偽時系列テスト、画像を保存しない実画面チェックリストを追加した

### 互換性とデータ影響

- 履歴DBはV0.15.0のスキーマ版4を維持し、既存録画、対戦記録、タイムラインを変更しない
- 取得フレーム、ゲーム画像、テンプレート画像をファイル、DB、ログへ保存しない
- 任意ウィンドウ、モニター、デスクトップ録画と設定で無効化した環境では自動判定だけを停止する
- ゲームUI変更で検出精度が変化し得るため、候補は利用者確認なしで確定しない

## V0.15.0: 対戦タイムライン基盤 - 2026-08-09

### 変更

- 履歴DBをスキーマ版4へ移行し、録画ID、経過ミリ秒、種別、入力元、信頼度、状態を持つ対戦イベントを追加した
- 対戦開始、ターン切り替え、対戦結果、手動マーカーを共通モデルで管理するようにした
- 候補、確定、却下を物理削除なしで遷移させ、検出器ID・版を含む自動判定候補の追跡契約を追加した
- 確定済み開始・結果の一意性、開始・ターン・結果の順序、録画時間内の時刻を検証するようにした
- CLIへ`timeline list/add/confirm/reject`、状態・種別フィルター、スキーマ版付きJSON出力を追加した
- GUI録画履歴へタイムライン画面を追加し、一覧、フィルター、手動マーカー追加、候補の確認・却下を提供した

### 互換性とデータ影響

- 版3 DBは移行前にSQLiteバックアップを作成し、既存録画履歴、対戦記録、動画を変更しない
- イベント更新は状態遷移だけを行い、候補や却下済みイベントを自動削除しない
- タイムラインJSONとDBには画像、絶対パス、ゲーム素材を保存しない

## V0.14.0: 対戦記録管理 - 2026-08-09

### 変更

- 履歴DBをスキーマ版3へ移行し、対戦記録、タグ、変更監査を録画IDへ関連付けた
- 勝敗、先後、自分・相手デッキ、対戦種別、タグ、メモ、draft・confirmedを追加した
- Unicode NFC、入力長、制御文字、タグ件数・重複を検証するようにした
- revisionによる競合検出と、記録・タグ・監査の単一トランザクション更新を追加した
- confirmedを含む対戦記録を録画履歴から何度でも再編集できるGUIを追加した
- 手動録画後の入力画面と、自動録画後の非モーダル未入力通知を追加した
- CLIへ`duel show`、`duel set`、`duel confirm`、`duel history`と安全なJSON出力を追加した
- 自動判定起点の既存対戦記録上書きを拒否する契約を追加した

### 互換性とデータ影響

- 版2 DBは移行前にSQLiteバックアップを作成し、既存録画履歴と動画を変更しない
- 対戦記録の保存失敗時は記録、タグ、監査の全変更をロールバックする
- `user_data/`の録画ファイルを削除、移動、上書きしない

## V0.13.1: 自動監視の安定化 - 2026-08-09

### 変更

- 自動録画の開始確認を同一PID・HWNDの連続観測に限定し、録画中の対象を固定した
- FFmpegのWindows終了コードをDWORD値、符号付き32bit値、16進値で表示するようにした
- 録画開始時のPID・HWND・画面サイズとFFmpeg stderr末尾を録画履歴へ保存するようにした
- 録画中に出力サイズが30秒間増加しない場合を異常として検出するようにした
- 自動録画の連続失敗へ10秒からの指数バックオフと3回の上限を追加した
- GUI録画履歴へ終了コード、失敗分類、検出理由、FFmpeg stderrを確認する診断画面を追加した
- Issue #117とV0.13.1マイルストーンへ実装を接続した

### 互換性とデータ影響

- 履歴DBの既存列を利用するためスキーマ変更はない
- 手動録画の開始・停止操作と既存録画ファイルは変更しない
- 自動監視は引き続きゲームウィンドウの存在判定であり、対戦画面判定はV0.16.0で提供する

## V0.13.0: 録画の閲覧 - 2026-08-09

### 変更

- 録画IDから履歴を取得し、録画保存領域内の非空MKV・MP4だけを解決する閲覧サービスを追加した
- Windows既定プレイヤーでの再生と、対象を選択したExplorer表示を追加した
- CLIへ`history play RECORDING_ID`と`history reveal RECORDING_ID`を追加した
- GUI録画履歴へ再生・保存場所表示・ダブルクリック再生・選択維持を追加した
- 欠損、空、保存領域外、未対応形式、Windows起動失敗を区別するようにした
- パス検証、Windows起動引数、CLI終了コード、Application委譲、GUI部品のテストを追加した

### 互換性とデータ影響

- 既存CLIコマンドとGUI画面を維持し、録画履歴へ閲覧操作だけを追加する
- 閲覧操作は録画、履歴DB、設定を変更しない
- `user_data/`のファイルを移動、コピー、削除、上書きしない

## V0.12.1: 対戦記録ロードマップ策定 - 2026-08-09

### 変更

- V0.13.0「録画の閲覧」、V0.14.0「対戦記録管理」、V0.15.0「対戦タイムライン基盤」、V0.16.0「基本イベント自動判定」をロードマップへ追加した
- 各中核機能の責務、データモデル、GUI・CLI、移行、安全性、完了条件を4設計文書へ記録した
- 対戦記録はdraft・confirmedのどちらも後編集可能とし、変更履歴と競合検出を必須にした
- 自動判定は対戦開始、ターン切り替え、勝敗・対戦終了に限定し、候補だけを保存して録画を阻害しない方針を定めた
- Milestone V0.13.0からV0.16.0、バージョンラベル、Issue #78から#114をGitHubへ登録した
- 計画改定自体をMilestone V0.12.1とIssue #115から#116へ接続した

### 互換性とデータ影響

- このFix版は計画と設計文書の更新であり、録画・GUI・CLIの実行機能は変更しない
- `user_data/`、設定、録画、履歴、復旧結果、アップロード準備データを変更しない
- 履歴DBのスキーマ移行はV0.14.0以降の各実装版でバックアップ・ロールバック付きで行う

## V0.12.0: GUI配布品質 - 2026-08-08

### 変更

- Python不要で起動できるone-fileウィンドウ版EXEを追加した
- CLI版とGUI版を同じタグからビルドし、個別のSHA-256とbuild provenance付きで配布するようにした
- 実GUIの表示寸法、主要操作部品、バージョン、正常終了を検証するEXEスモークを追加した

### 互換性とデータ影響

- CLI版EXEの名前と操作体系は維持する
- GUIとCLIは同じ`user_data/`を使用し、更新時に既存データを削除・初期化しない

## V0.11.0: Windows GUI - 2026-08-08

### 変更

- TkinterによるWindows GUIを追加し、録画、履歴、復旧、準備、設定を一画面から操作できるようにした
- 時間のかかる診断・録画・復旧処理をバックグラウンドで実行し、画面応答を維持するようにした
- CLIと共通のアプリケーションサービスを追加し、終了時に監視と録画を正常停止するようにした

## V0.10.0: 録画対象の明示化 - 2026-08-08

### 変更

- Master Duel、任意の可視ウィンドウ、モニター、デスクトップ全体を録画対象として列挙・選択できるようにした
- ウィンドウハンドルまたはモニター座標をFFmpeg gdigrab入力へ変換するようにした
- 自動監視で検出したMaster Duelウィンドウハンドルを録画入力へ接続した
- `targets` CLIと録画対象設定を追加した

### データ影響

- 設定へ`capture_mode`と`capture_target_id`を追加する。既存設定ではMaster Duelウィンドウを既定対象として補完する
- 録画、履歴、復旧成果物を削除・上書きしない

## V0.9.1: Windows EXE出力互換性修正 - 2026-08-08

### 修正

- GitHub Actionsの英語Windows環境で、日本語ヘルプ出力が`cp1252`へ変換できずEXEが異常終了する問題を修正した
- CLI開始時に標準出力と標準エラーをUTF-8へ統一し、変換不能文字でも異常終了しないようにした
- `cp1252`相当のストリームからUTF-8へ切り替えて日本語を出力する回帰テストを追加した

### データ影響

- `user_data/`、設定、録画、履歴、復旧結果、アップロード準備データへの変更はない

## V0.9.0: Windows EXE配布 - 2026-08-08

### 変更

- PyInstaller 6.21.0とhooks contrib 2026.6を固定し、Windows x64向けone-fileコンソールEXEのビルドスクリプトを追加した
- EXEへFileVersion `0.9.0.0`、ProductVersion `0.9.0`、製品名、著作権情報を埋め込むようにした
- PyInstaller凍結時はEXE配置フォルダ、Python実行時はカレントディレクトリを既定のプロジェクト基準にした
- `--version`、`--help`、読取専用の設定JSONを検証するEXEスモークを追加した
- `main`のWindows CIと、タグpush時のテスト、ビルド、スモーク、SHA-256、build provenance、GitHub Release公開を追加した
- GitHub Actionsを検証済みリリースのcommit SHAへ固定した
- タグ、`pyproject.toml`、`__version__`の一致検証を追加し、不一致時はReleaseを中止するようにした
- Python不要のダウンロード、ハッシュ確認、初回起動、更新手順をREADMEへ追加した

### 互換性とデータ影響

- Pythonモジュールと既存CLIの引数・終了コードは変更しない
- EXE実行時だけ既定の`user_data/`をEXEと同じフォルダへ置く
- `MDRL_USER_DATA_DIR`と`--user-data-dir`による明示指定を引き続き優先する
- EXE更新時も`user_data/`を削除・上書きしない
- FFmpegはライセンス、サイズ、独立更新のためEXEへ同梱しない
- EXEはコード署名されていないため、SmartScreen警告とSHA-256・build provenance確認手順を明記する

## V0.8.0: 設定・運用CLI - 2026-08-08

### 変更

- `config init/show/get/set/reset`を追加し、設定モデル全体の検証を経由する個別設定操作を提供した
- 設定保存を一時ファイル同期後の原子的置換へ変更し、直前の`app.toml`を`.previous`へ1世代保持するようにした
- `config init`と互換用`--write-default-config`が既存設定を上書きしないようにした
- 録画環境、実行状態、履歴整合性、復旧待ち、準備キューを個別に収集する`status`を追加した
- `status --json`と`config show/get --json`を追加し、スキーマ版を持つ機械可読出力から秘密情報と絶対パスを除外した
- CLI終了コードを成功0、設定・引数・環境不備2、処理失敗3、要確認4、Ctrl+C中断130として定義した
- 代表的な失敗をエラーコード、短い要約、対処候補の形式へ統一し、引数エラーを日本語化した
- 各helpへ例、安全上の注意、再実行性を追加し、内部診断は`--verbose`指定時だけ表示するようにした
- アップロード準備の項目別開始・完了表示を追加し、Ctrl+Cで処理中状態と部分出力を保持して次回起動時に待機状態へ復帰するようにした
- 復旧修復のCtrl+Cを終了コード130として扱い、元録画と部分成果物を保持するようにした
- 設定初期化、録画IDを共有する履歴・準備キュー、キャンセルを通す隔離E2Eテストを追加した

### 互換性とデータ影響

- V0.7以前の`--init-user-data`、`--write-default-config`、`--show-config`は互換用に残す
- `--write-default-config`は安全性のため既存`app.toml`を上書きせず、終了コード4を返す
- 設定変更時に`user_data/config/app.toml.previous`を新規作成する場合があるが、自動削除しない
- `status`は診断に必要な未作成フォルダと履歴DBを初期化する場合があるが、既存データを削除・上書きしない
- GUI、直接アップロード、OAuthは対象外で、V1.0.0へは更新しない

## V0.7.0: アップロード準備 - 2026-08-08

### 変更

- タイトル、説明、重複なしタグ、privateまたはunlistedを扱う制約付きメタデータを追加した
- 未知フィールド、public、認証情報名をメタデータとマニフェストのスキーマで拒否するようにした
- ffprobeでコンテナ、長さ、映像・音声ストリームを検証し、正常、音声なし警告、空・破損・映像なし失敗を区別した
- 元録画を保持してMP4へstream copyし、一時出力の再検証後だけ原子的に確定するエクスポートを追加した
- waiting、processing、completed、failed、cancelledを持つチェックサム付き原子キューと1世代保持を追加した
- 再起動時にprocessing項目をwaitingへ戻し、同一録画の未完了・完了重複を拒否するようにした
- 相対パス、元・出力SHA-256、サイズ、生成時刻、録画ID、検証結果、メタデータを持つJSONマニフェスト版1を追加した
- `prepare enqueue/list/show/run/cancel` CLIを追加した
- 外部サービスなしの統合テストと、合成MKVからMP4への実FFmpeg準備スモークを追加した

### 互換性とデータ影響

- 元録画は上書きせず、確定出力を `user_data/data/exports/{recording_id}/{queue_id}.mp4` へ作成する
- 準備キューと1世代前を `user_data/data/queue/` に保存し、マニフェストを同配下の `manifests/` に作成する
- remux失敗・キャンセル時の部分出力は確定名へ移さず、キューへ相対パスを記録して保持する
- 直接アップロード、OAuth、アクセストークン保存は実装範囲外である

## V0.6.0: 失敗時の復旧 - 2026-08-08

### 変更

- 録画失敗を中断、容量不足、出力欠損・空、FFmpeg異常終了、タイムアウト、未知原因へ分類し、再試行・手動確認・復旧不能の方針へ対応させた
- 履歴DBをスキーマ版2へ更新し、復旧状態、試行回数、利用者向け説明、内部診断、修復成果物を追跡できるようにした
- チェックサム、fsync、同一ディレクトリの原子的置換、1世代保持による録画状態ファイルを追加した
- 起動時に録画ロック、保存済みPID、履歴を照合し、他プロセスを変更せず中断録画だけを失敗履歴へ確定する処理を追加した
- ffprobeによる読み取り専用検査と、FFmpeg stream copyによるUUID付き別ファイル修復を追加した
- `recovery list/detect/inspect/repair/ignore` と修復の `--dry-run` を追加した
- 容量不足、子プロセス異常、アプリ中断、書込中断、検査・修復失敗、タイムアウトの障害注入テストを追加した
- 合成動画の実FFmpeg修復で元ファイルのSHA-256不変と修復動画の再デコードを検証した

### 互換性とデータ影響

- スキーマ版1のDBは移行前バックアップ作成後に版2へ移行し、既存の失敗履歴を手動確認対象として保持する
- `user_data/data/recording-state.json` と1世代前の `.previous` を新規作成する
- 修復は元録画を上書きせず、`*.recovered.UUID.mkv`またはMP4を別ファイルとして作成する
- 検査、修復、無視操作は元録画を削除せず、失敗した部分成果物も履歴へ記録して保持する

## V0.5.0: 録画履歴管理 - 2026-08-08

### 変更

- 録画ID、状態、起点、検出理由、相対ファイルパス、時刻、長さ、サイズ、終了コード、診断を保持するSQLiteスキーマを追加した
- 単一行のスキーマ版管理、再実行可能な初期化、未対応の新しいスキーマ拒否、必須スキーマとDB整合性の検査を追加した
- 旧スキーマ移行前のSQLiteバックアップと、移行失敗時のトランザクションロールバックを追加した
- 手動録画と自動録画の開始、完了、失敗を同一録画IDへ冪等に保存する処理を追加した
- 状態、ISO 8601期間、件数上限、offsetを組み合わせる `history list` と、診断を含む `history show` を追加した
- 欠損、未登録、サイズ不一致、不正参照を読み取り専用で報告する `history check` を追加した
- 正常・異常ライフサイクル、検索順序、移行バックアップ、障害注入、不整合診断のテストを追加した

### 互換性とデータ影響

- 初回の録画準備またはhistoryコマンドで `user_data/data/db/history.sqlite3` を新規作成する
- 動画パスは `recordings/` からの相対パスだけを保存し、保存先外部の参照を拒否する
- 旧DBの移行前バックアップは `history.vN.*.backup.sqlite3` として残し、自動削除しない
- 履歴診断は録画ファイル、DBレコード、バックアップを削除・修正しない

## V0.4.0: Master Duel向け録画補助 - 2026-08-08

### 変更

- 対戦候補の存在、不在、不明と信頼度を録画処理から分離する検出契約を追加した
- Windows APIでMaster Duelプロセスと対象ウィンドウを監視し、非表示・最小化・エラーを区別する機能を追加した
- FFmpegで対象ウィンドウの単一BMPフレームをメモリへ取得する非永続インターフェースを追加した
- 連続確認、信頼度閾値、停止後クールダウン、自動開始・停止の個別設定を追加した
- 検出結果から録画セッションを1回だけ開始・停止する自動録画コントローラーを追加した
- 同時録画を拒否するOSレベルの録画ロックと、手動開始・停止の制御APIを追加した
- 現在状態だけを表示する `watch --once` と、継続監視する `watch` コマンドを追加した
- 一時的な最小化、ノイズ、起動・停止失敗、FFmpeg異常終了、手動上書きのシナリオテストを追加した

### 互換性とデータ影響

- V0.3.0以前のapp.tomlには検出設定の既定値を補うため手動移行は不要
- `user_data/data/recording.lock` を新規作成するが、既存の録画や実行時データは削除・上書きしない
- フレーム取得インターフェースは取得データをメモリだけに保持し、標準の監視処理では使用しない
- 現在の自動判定は可視ウィンドウの存在に基づき、対戦画面とメニュー画面を区別しない

## V0.3.0: 最小録画 - 2026-08-08

### 変更

- フレームレート、解像度、映像・音声ビットレートを持つ録画プロファイルを追加した
- Windowsの画面・音声入力から安全なFFmpeg引数配列を構築する機能を追加した
- UTC日時とUUIDで衝突を防ぐ日付別録画保存先を追加した
- 録画開始、実行中、停止中、完了、失敗を追跡する録画セッションを追加した
- FFmpeg stderrを上限付きで読み続け、失敗診断へ残す機能を追加した
- stdinの `q` による正常停止、停止タイムアウト時の強制終了、非空出力検証を追加した
- 指定秒数またはCtrl+Cで停止する `record` コマンドを追加した
- 偽FFmpeg統合テストと、合成映像・音声を使う任意の実FFmpegスモークテストを追加した

### 互換性とデータ影響

- V0.2.0以前のapp.tomlには録画品質の既定値を補うため手動移行は不要
- 録画は `user_data/data/recordings/YYYY/MM/DD/` 配下へ新規作成し、既存ファイルを上書きしない
- 異常終了時の部分ファイルは自動削除せず保持する
- 録画履歴DBと中断ファイル修復はまだ未実装である

## V0.2.0: 録画環境の初期化 - 2026-08-08

### 変更

- 設定値、PATH、Windowsの既知配置先から実行可能なFFmpegを探索する機能を追加した
- FFmpeg 6.0またはlibavutil 58以上のバージョン判定を追加し、nightly buildにも対応した
- 入力方式、映像エンコーダー、出力コンテナの能力検査を追加した
- Windowsのデスクトップとdshow音声入力を列挙する機能を追加した
- 画面入力、音声入力、映像エンコーダーの設定項目と検証を追加した
- 設定、FFmpeg、録画能力、入力、保存先、空き容量を検査する `doctor` コマンドを追加した
- 入力候補を表示する `list-inputs` コマンドを追加した

### 互換性とデータ影響

- V0.1.xのapp.tomlは変更せず読み込め、不足する録画設定には安全な既定値を補う
- 音声入力は既定で無効のため、利用する場合は `list-inputs` の識別子を設定する必要がある
- `doctor` は自動作成が有効な場合に不足する `user_data/` ディレクトリを作成する
- 既存の録画、DB、キュー、ログは削除または上書きしない

## V0.1.1: 計画再検討 - 2026-08-08

### 変更

- プロダクトコンセプトとV1.0.0までの中核機能をREADMEへ明記した
- 品質を優先するため、リポジトリ管理下のコード、テスト、文書の削除・再設計を許可した
- `user_data/`、秘密情報、録画ファイル、SQLite DB、キュー、ログを破壊的変更の対象外として明記した
- 中核機能単位のV0.2.0からV0.8.0までのロードマップを定義した
- Issue先行、バージョンラベル、Milestoneによる追跡原則を追加した
- バージョンを `0.1.0` から `0.1.1` へ更新した
- `pyproject.toml` のUTF-8 BOMを除去し、標準TOMLパーサーとpipで読み込めるようにした
- `pyproject.toml` とパッケージの `__version__` の一致テストを追加した

### 互換性とデータ影響

- 既存CLIと設定ファイル形式に変更はない
- `user_data/` の移行、削除、上書きは行わない
- 録画機能はまだ未実装である

## V0.1.0: 初期スキャフォールド

### 変更

- Pythonパッケージと最小CLIを作成した
- `user_data/` の標準ディレクトリ構成を追加した
- 非シークレット設定 `user_data/config/app.toml` の読み書きを追加した

### 互換性とデータ影響

- 初回バージョンのため、以前のバージョンからの移行はない
