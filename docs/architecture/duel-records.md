# 対戦記録管理

## 目的と範囲

V0.14.0では録画履歴1件に対して対戦記録を0件または1件関連付け、勝敗、先攻・後攻、デッキ、対戦種別、タグ、メモを後から何度でも編集できるようにします。動画ファイルと録画履歴のライフサイクル情報は変更せず、利用者が入力する対戦情報を別テーブルへ保存します。

## データモデル

履歴DBの現在版は5です。版3で対戦記録を、版5でデッキ名・タグ辞書と前回入力を追加します。

```text
duel_records
- recording_id: recordingsへの主キー兼外部キー
- status: draft / confirmed
- result: win / loss / draw / unknown
- play_order: first / second / unknown
- own_deck
- opponent_deck
- duel_type: ranked / event / room / solo / other
- notes
- revision
- created_at
- updated_at

duel_record_tags
- recording_id
- tag
- normalized_tag

duel_record_changes
- change_id
- recording_id
- revision
- source: user / system / detected
- before_json
- after_json
- changed_at

duel_catalog_entries
- entry_id
- kind: deck / tag
- name
- normalized_name
- created_at
- updated_at

duel_editor_preferences
- singleton: 常に1
- duel_type
- own_deck
- opponent_deck
- tags_json
- updated_at
```

`recording_id`の削除連鎖は使用せず、既存録画履歴の削除を前提にしません。タグはUnicode NFCと大文字小文字を無視した値で重複を拒否します。入力長、制御文字、タグ件数には上限を設けます。監査JSONは対戦記録で許可した項目だけを含み、絶対パスや秘密情報を保存しません。

## 状態と後編集

`draft`は未入力または入力途中、`confirmed`は利用者が一度確認した状態です。`confirmed`は編集禁止を意味しません。どちらの状態も再編集でき、更新は全項目の検証後に単一トランザクションで確定します。

更新には`revision`を使った競合検出を行います。別画面や別プロセスで先に更新されていた場合は上書きせず、再読込を求めます。変更履歴の保存に失敗した場合は対戦記録だけを更新しません。将来の自動判定は利用者が編集した値を黙って上書きしません。

## 録画終了後の導線

手動録画が正常完了した場合は、履歴確定後に録画IDを引き継いだ対戦記録入力画面を表示します。画面を閉じても録画は保持し、必要に応じて空または入力途中の`draft`を履歴から再編集できます。

自動監視中は次の対戦検出を妨げないため、録画終了時にモーダルを表示しません。対戦記録を`draft`として作成し、未入力件数と編集導線だけを通知します。失敗録画には自動作成せず、復旧後に利用者が履歴から追加できます。

## GUIとCLI

GUIの録画履歴詳細に対戦記録の表示・編集画面を常設します。勝敗、先後、自分・相手デッキ、対戦種別、タグ、メモ、状態、最終更新を表示します。状態、勝敗、先後、対戦種別は日本語表示名と既存の英語内部値を双方向変換します。

自分デッキと相手デッキは同じ`deck`辞書を入力可能な選択欄として利用します。タグは`tag`辞書から複数追加でき、候補にない日本語名も入力できます。対戦記録の保存後、新しいデッキ名とタグを辞書へ追加し、対戦種別、自分デッキ、相手デッキ、タグを`duel_editor_preferences`へ保存します。未作成の記録だけがこの前回値を初期値に使い、既存記録は自身の値を優先します。

サイドメニューの「デッキ名・タグ」は両辞書を一つの一覧で追加・名称変更・削除します。辞書項目は候補のため、名称変更や削除で過去の対戦記録を変更しません。

CLIは`duel show`、`duel set`、`duel confirm`、`duel history`を提供し、JSON出力では絶対パスと秘密情報を除外します。

## 完了条件

- 録画IDと対戦記録が1対1で関連付く
- `draft`と`confirmed`を何度でも再編集できる
- 競合・検証・DB障害時に既存内容を維持する
- 手動録画後は入力画面、自動録画後は非モーダル通知を提供する
- 日本語表示と英語内部値を往復して既存データを再保存できる
- デッキ名・タグ辞書と前回入力がアプリ再起動後も維持される
- スキーマ版2から5までの移行前バックアップと失敗時ロールバックを検証する

追跡: [V0.14.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/15)、Issue #86 - #95
