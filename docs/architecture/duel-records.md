# 対戦記録管理

## 目的と範囲

録画の有無とは独立した対戦記録を作成し、勝敗、先攻・後攻、コイントス、デッキ、対戦種別、タグ、メモを後から何度でも編集できるようにします。録画付き対戦は録画履歴を任意に関連付け、録画なし対戦も同じ戦績・統計基盤で扱います。

## データモデル

履歴DBの現在版は10です。版3で対戦記録を、版8でシーズンとデッキ安定IDを、版9でコイントス情報を、版10で録画から独立した対戦IDと対戦日時を追加します。

```text
duel_records
- duel_id: 対戦固有の主キー
- recording_id: recordingsへの任意・一意な外部キー
- entry_origin: recording / manual
- occurred_at: 統計に使用する対戦日時
- status: draft / confirmed
- result: win / loss / draw / unknown
- play_order: first / second / unknown
- coin_face: heads / tails / unknown
- own_deck
- opponent_deck
- duel_type: ranked / event / room / solo / other
- notes
- revision
- created_at
- updated_at

duel_record_tags
- duel_id
- tag
- normalized_tag

duel_record_changes
- change_id
- duel_id
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
- description
- color: tagだけに設定する#RRGGBB
- archived_at
- created_at
- updated_at

duel_record_catalog_links
- recording_id
- entry_id
- kind: own_deck / opponent_deck / tag

duel_editor_preferences
- singleton: 常に1
- duel_type
- own_deck
- opponent_deck
- tags_json
- updated_at
```

`coin_face`はコイントスの結果を表・裏・未設定で保存します。日本語版Master Duelでは表がコイントス勝利、裏が敗北を表すため、別の勝敗項目は保持しません。`play_order`はカード効果などでコイントス結果と一致しない場合があるため独立して保存します。未設定は有効な任意値であり、戦績管理未完了件数には影響しません。

`duel_id`は録画の有無にかかわらず発行します。`recording_id`は録画付き対戦だけが保持し、手入力対戦ではNULLです。録画付き対戦の`occurred_at`は録画開始日時、手入力対戦では利用者が指定した日時とし、後者だけを変更可能にします。タグはUnicode NFCと大文字小文字を無視した値で重複を拒否します。監査JSONは対戦記録で許可した項目だけを含み、絶対パスや秘密情報を保存しません。

## 状態と後編集

`draft`は未入力または入力途中、`confirmed`は利用者が一度確認した状態です。`confirmed`は編集禁止を意味しません。どちらの状態も再編集でき、更新は全項目の検証後に単一トランザクションで確定します。

更新には`revision`を使った競合検出を行います。別画面や別プロセスで先に更新されていた場合は上書きせず、再読込を求めます。変更履歴の保存に失敗した場合は対戦記録だけを更新しません。将来の自動判定は利用者が編集した値を黙って上書きしません。

## 録画終了後の導線

手動録画が正常完了した場合は、履歴確定後に録画IDを引き継いだ対戦記録入力画面を表示します。画面を閉じても録画は保持し、必要に応じて空または入力途中の`draft`を履歴から再編集できます。

自動監視中は次の対戦検出を妨げないため、録画終了時にモーダルを表示しません。対戦記録を`draft`として作成し、未入力件数と編集導線だけを通知します。失敗録画には対戦記録を自動作成しません。

GUI共通ヘッダーの戦績管理未完了件数は、`recordings.state = completed`かつ、対戦記録が存在しない、または`duel_records.status <> confirmed`の件数です。画面の履歴表示上限とは独立したSQLite集計とし、録画完了、対戦記録保存、履歴削除、履歴更新後に再集計します。録画失敗、録画中、候補破棄は含めません。

## GUIとCLI

GUIの録画履歴詳細に対戦記録の表示・編集画面を常設します。勝敗、先後、コインの面、自分・相手デッキ、対戦種別、タグ、メモ、状態、最終更新を表示します。選択項目は日本語表示名と英語内部値を双方向変換します。履歴と統計ではコインの面を他条件と組み合わせて絞り込めます。

自分デッキと相手デッキは同じ`deck`辞書を入力可能な選択欄として利用します。タグは`tag`辞書から複数追加でき、候補にない日本語名も入力できます。対戦記録の保存後、新しいデッキ名とタグを辞書へ追加し、対戦種別、自分デッキ、相手デッキ、タグを`duel_editor_preferences`へ保存します。未作成の記録だけがこの前回値を初期値に使い、既存記録は自身の値を優先します。

サイドメニューの「デッキ名」と「タグ」は独立した管理画面です。どちらにも説明を設定でき、タグには`#RRGGBB`カラーを指定できます。タグ一覧のカラー列はコードと実色スウォッチを併記し、対戦記録編集でもタグ名と色見本を表示します。

対戦記録は表示用文字列に加えて`duel_record_catalog_links`でカタログの安定IDへ関連付けます。名前変更は同じIDの表示名を更新するため、将来のタグ別集計で同一項目として扱えます。参照中の項目を削除した場合はアーカイブして過去記録の関連を残し、未使用項目だけを恒久削除します。版5から6への移行では既存名称をカタログへ取り込み、対戦記録との関連を補完します。

CLIは`duel create`、`duel show`、`duel set`、`duel confirm`、`duel history`を提供し、JSON出力では絶対パスと秘密情報を除外します。`duel create`は録画履歴を作成せず、手入力由来の確定済み対戦を登録します。

## 完了条件

- 録画IDと対戦記録が1対1で関連付く
- `draft`と`confirmed`を何度でも再編集できる
- 競合・検証・DB障害時に既存内容を維持する
- 手動録画後は入力画面、自動録画後は非モーダル通知を提供する
- 全画面の共通ヘッダーで戦績管理未完了件数を確認し、録画履歴へ移動できる
- 日本語表示と英語内部値を往復して既存データを再保存できる
- コインの面と先後を独立保存し、後から編集できる
- 表裏別の戦績を集計できる
- デッキ名・タグ辞書と前回入力がアプリ再起動後も維持される
- スキーマ版12から13への移行前バックアップ、重複列の撤去、失敗時ロールバックを検証する
- スキーマ版9から10への移行で既存録画戦績を保持する
- 録画なし戦績が録画付き戦績と同じ統計・変更監査へ反映される

追跡: [V0.14.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/15)、Issue #86 - #95、[V0.17.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/30)、Issue #140 - #152、[V0.20.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/40)、Issue #250 - #258、[V0.21.1 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/41)、Issue #259 - #269
