# YouTube再連携とデッキ相性統計（V2.7.20）

## YouTube認証失敗の扱い

Googleのtoken endpointは、YouTube動画APIとは異なり `{"error":"invalid_grant","error_description":"..."}` という文字列のerrorを返す。refresh tokenが拒否された場合は入力修正で解決できないため、再認証が必要な失敗として分類する。一般のHTTP400は従来の入力失敗分類を維持する。

再認証が必要な失敗は自動再試行しない。投稿を成功扱いせず、日本語で「設定 → YouTube → 連携する」の操作を案内する。既存の準備済みMP4、失敗した投稿履歴、資格情報をエラー処理だけで削除しない。

PySide6の連携ボタンから既存の `ApplicationService.connect_youtube` をバックグラウンドで呼ぶ。認証中は連携の多重開始と切断等の競合を防ぎ、成功・失敗のどちらでも操作可能な状態へ戻す。ブラウザでの本人同意を完了してから、新しい資格情報を既存のOS資格情報ストアへ保存する。認証に失敗した場合は保存済み資格情報を保持する。

保存済み資格情報の存在確認は、現在その資格情報がGoogleで有効かどうかの検証とは区別して表示する。今回の修正では起動・状態表示に新しい外部通信を追加しない。

失効原因の細分類はログだけでは特定できない。GoogleのTesting公開状態等による制限は[公式OAuth仕様](https://developers.google.com/identity/protocols/oauth2#expiration)を参照する。

## 相手デッキ×自分デッキの集計

既存 `duel_records` の `opponent_deck` と `own_deck` を読む。DB列の追加・保存形式の変更・移行は不要。録画・対戦記録・カタログは統計処理から書き換えない。

統計の条件適用後の行を両デッキ名の組でまとめる。デッキ名は既存と同じNFKC・前後空白除去・大文字小文字非依存で比較し、表示名と集計値は分離する。名前中の記号による衝突を防ぐため、組を文字列連結したキーにしない。

対象は確定済み・勝敗入力済みの対戦で、正常完了録画または録画なしの手入力・取込を含む。非表示の自分デッキは除外し、相手デッキだけが非表示の対戦は含める。未設定は独立した組として扱う。

GUIの「デッキ相性」タブは相手と自分を別列にし、対戦数・勝利・敗北・引分・自分勝率を示す。勝率は `勝利数 / (勝利数 + 敗北数 + 引分数)`。期間と対戦種別の条件を反映し、全体カードは従来の無条件比較基準を保つ。0件は空状態と説明を表示する。

## 互換性と検証

Dashboardの新しい集計項目には既定値を持たせ、旧GUIの直接constructor利用を維持する。単体集計・実widget・OAuth合成応答・fake資格情報/投稿を検証し、本番DBや実Google投稿をテストに使用しない。

追跡: [#658](https://github.com/Tao-pyth/master-duel-recorder-lite/issues/658)、[#659](https://github.com/Tao-pyth/master-duel-recorder-lite/issues/659)、[Release Contract](../release-contracts/2.7.20.md)。
