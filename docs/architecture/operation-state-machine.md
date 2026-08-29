# 録画・自動監視の操作状態機械

## 目的

V0.23.0以降は、録画状態、自動監視状態、GUIの操作可否を`OperationStateMachine`へ集約します。`_current`、監視スレッド、開始処理中フラグは実体の所有確認にだけ使い、GUIのボタン状態を個別フラグから推測しません。

## 状態

| 状態 | 意味 | 利用者が実行できる操作 |
|---|---|---|
| `idle` | 録画・監視とも停止 | 手動録画開始、自動監視開始、戦績更新、管理データ操作、終了 |
| `manual_starting` | 手動録画の診断・開始処理中 | なし |
| `manual_recording` | 手動録画中 | 録画停止、終了 |
| `watch_starting` | 自動監視の診断・開始処理中 | 自動監視停止、終了 |
| `watch_waiting` | 自動監視中・対戦待機 | 自動監視停止、終了 |
| `candidate_recording` | 対戦候補を仮録画中 | 自動監視停止、終了 |
| `automatic_recording` | 盤面確定済みの自動録画中 | 自動監視停止、終了 |
| `stopping` | 録画または監視の停止処理中 | なし |
| `failed` | 開始、録画、監視の失敗を表示中 | 再開始、戦績更新、管理データ操作、終了 |
| `closing` | 終了処理中 | なし |

## 遷移

```text
idle -> manual_starting -> manual_recording -> stopping -> idle
  |            |                  |               `-> failed
  |            `-> failed        `-> failed
  `-> watch_starting -> watch_waiting -> candidate_recording
              |              ^              |        |
              |              |              |        `-> automatic_recording
              |              |              `------------^          |
              |              `---------------------------------------'
              `-> stopping -> idle
              `-> failed
```

候補破棄、結果停止、次戦境界では`watch_waiting`へ戻ります。監視例外は`failed`を保持し、GUIが確認する前に`idle`へ上書きしません。同じ状態への遷移は表示メッセージ更新として許可し、定義外の遷移は例外にします。

## 操作制約

- 手動録画と自動監視は相互排他とする
- 自動監視中は候補録画前でも戦績の新規作成・更新を禁止する
- 手動録画中は戦績の新規作成・更新を禁止する
- 診断ZIP出力と参照操作は録画状態を変更しない
- Windows通知の失敗は録画、停止、監視の成否へ影響させない
- 非同期FFmpeg終了をポーリングした場合も状態機械を`idle`または`failed`へ同期する

実装: `src/master_duel_recorder_lite/operation_state.py`

追跡: [V0.23.0 Milestone](https://github.com/Tao-pyth/master-duel-recorder-lite/milestone/45)、Issue #290 - #292
