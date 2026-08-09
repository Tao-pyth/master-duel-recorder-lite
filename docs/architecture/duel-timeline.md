# 対戦タイムライン基盤

## 目的と範囲

V0.15.0では録画開始からの経過時間に対戦イベントを関連付け、手動入力と将来の自動判定を同じ契約で保存します。自動画像判定とエクスポートはこの版には含めません。

## データモデル

履歴DBをスキーマ版4へ移行し、`duel_events`を追加します。

```text
- event_id: UUID
- recording_id: recordingsへの外部キー
- elapsed_ms: 録画開始からの経過ミリ秒
- event_type: duel_start / turn_change / duel_result / marker
- actor: self / opponent / unknown / null
- outcome: win / loss / draw / unknown / null
- label
- source: manual / detected / system
- confidence: 0.0から1.0、手入力はnull
- status: candidate / confirmed / rejected
- detector_id
- detector_version
- created_at
- updated_at
```

`turn_change`だけが`actor`を使用し、`duel_result`だけが`outcome`を使用します。手動マーカーは名称を必須とします。自動判定候補は検出器ID、版、信頼度を必須とし、手入力と区別します。

イベントは物理削除せず`rejected`へ変更します。標準の並び順は`elapsed_ms`、`event_id`の昇順です。同一時刻でもイベントIDにより再起動前後で順序が変わりません。

## 整合性規則

- 経過時間は0以上で、確定済み録画の長さを超えない
- 確定済み`duel_start`と`duel_result`は1録画につき各1件まで
- 確定済みターン切り替えは開始以降、結果以前に置く
- 候補同士の矛盾は保持できるが、矛盾したまま確定できない
- 対戦記録がなくてもイベントを保持でき、後から同じ録画IDで関連付く

録画時間が未確定の実行中マーカーは現在の録画経過時間以下だけを許可し、録画確定後に再検証します。時刻は浮動小数ではなく整数ミリ秒で保存します。

## GUIとCLI

GUIの対戦記録詳細にタイムラインを表示し、時刻、種別、手番、勝敗、出所、信頼度、状態を確認できるようにします。状態フィルターと手動マーカー追加を提供します。この版ではイベント値の高度な編集とエクスポートは対象外です。

```powershell
mdrl timeline list RECORDING_ID
mdrl timeline add RECORDING_ID --elapsed-ms 204000 --type marker --label "重要局面"
mdrl timeline confirm EVENT_ID
mdrl timeline reject EVENT_ID
```

JSONはスキーマ版を持ち、録画IDとイベントだけを含め、絶対パスや画像を含めません。

## 完了条件

- 手動イベントを録画IDへ保存し、再起動後も安定順序で取得できる
- 候補、確定、却下を物理削除なしで管理できる
- 矛盾するイベントを確定できない
- スキーマ版3から安全に移行し、既存対戦記録を維持できる
- GUI、CLI、DB移行、実EXEの入口を検証する

追跡: [V0.15.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/16)、Issue #96 - #103
