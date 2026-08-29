# PySide6通常GUI内レビュー統合 V2.4.0 / 視覚タイムラインMVP V2.7.0

## 目的

V2.4.0では、戦績管理で選択した録画を通常GUIの外へ出さずに確認し、動画位置と対戦タイムラインを同じ画面で扱えるようにする。既存の録画参照、タイムライン、クリップ出力、外部再生のサービス境界を再利用し、DB schemaや設定形式は変更しない。

## 対象範囲

- 戦績管理の`history_play`からレビューウィンドウを開く。
- `.mp4`と`.mkv`をQt Multimediaでアプリ内再生する。
- 再生/一時停止、シーク、現在位置表示を提供する。
- タイムライン一覧に経過、種別、状態、説明を表示する。由来は内部管理情報として通常列へ出さない。
- タイムラインイベント選択で動画位置を`elapsed_ms`へ移動する。
- V2.7.0以降、視覚タイムラインバーで現在位置、選択位置、タイムラインイベント位置を表示する。
- 視覚タイムラインのイベント選択と表形式タイムラインの選択を同期し、既存の動画シークへ接続する。
- 現在位置を`RecorderApplicationService.add_review_marker`でマーカー化する。
- 選択イベントまたは現在位置を中心に`RecorderApplicationService.export_review_clip`でクリップを出力する。
- V2.7.4以降、下段を「マーカー編集」「戦績入力」タブへ分け、動画を確認しながら戦績を保存できるようにする。
- V2.7.4以降、候補イベントの確定/却下、手動マーカーの種別/説明編集、クリップ出力範囲説明、出力先フォルダを開く操作を提供する。
- Qt Multimedia不可、未対応形式、再生エラー時は`RecorderApplicationService.play_recording`で外部プレイヤーへfallbackする。

## 対象外

- DB schema migration
- 設定migration
- 録画処理、検出処理、queue、manifest、OAuth資格情報の変更
- 高度なタイムライン編集、波形、サムネイル、プレビュー付きクリップ範囲編集
- 対戦タグとマーカー種別の完全統合
- Tkinter GUI側の同一プロセス埋め込み

## 実装境界

通常GUIは`pyside_gui.py`の`history_play`操作で録画IDを解決し、`pyside_review.create_review_window()`を呼び出す。レビューウィンドウは親GUIと同じQt event loopで表示し、`app.exec()`を再実行しない。親GUIはウィンドウ参照を保持し、閉じた後に参照を破棄する。

レビュー画面は`RecorderApplicationService.get_review_view_model()`から録画概要、動画参照、戦績概要、タイムライン、視覚タイムライン用の派生表示データを受け取る。GUIはSQLiteへ直接SQLを発行せず、マーカー追加とクリップ出力もApplication Service経由に限定する。V2.7.0の視覚タイムラインはViewModel上の`visual_timeline`を描画するだけで、DBや録画ファイルを直接変更しない。

## 失敗時の扱い

アプリ内再生ができない場合は処理成功扱いにせず、理由をGUI操作ログまたは警告に残す。代替として外部プレイヤーを起動できた場合も、fallbackとして扱い、DB、設定、録画ファイルは変更しない。クリップ出力は既存の`ClipExportService`とFFmpeg検出に従い、出力は新規ファイルだけに限定する。

## 検証方針

- `smoke_contract`でレビュー導線、レビューウィジェット、対応拡張子、fallback、タイムライン列を検出可能にする。
- V2.7.0以降、`smoke_contract`で視覚タイムラインWidget、表示種別、同期契約、fallback安全性を検出可能にする。
- `review_timeline_display_row`でタイムライン列の欠落を単体テストする。
- `build_visual_timeline_items`で録画長に対する割合、種別分類、範囲外イベント、録画長不明を単体テストする。
- PySide6 GUI smokeで戦績管理画面が崩れず、レビュー導線契約がJSONへ出力されることを確認する。
- 全体テストとRuffで既存機能への回帰を確認する。
