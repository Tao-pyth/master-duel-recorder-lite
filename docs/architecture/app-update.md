# アプリケーション更新

## 目的

V1.0.1では、利用者がGitHub Releaseを毎回手動で探さなくても正式版の更新有無を確認し、安全性を確認したGUI EXEへ置換できるようにします。更新確認は利便機能であり、録画や戦績管理の開始条件にはしません。

## 更新契約

- GitHubの`releases/latest`からdraftでもprereleaseでもないReleaseだけを対象にする
- `X.Y.Z`形式で現在版より新しい場合だけ通知する
- `master-duel-recorder-lite-gui.exe`と同名の`.sha256`が揃わないReleaseを拒否する
- HTTPS以外のURL、256MiBを超えるEXE、Release記載サイズと異なるEXEを拒否する
- 自動確認は設定で無効化でき、ダウンロードと適用は利用者の明示操作を必須にする

## 適用と復元

取得したEXEは一時ファイルへ保存し、公開SHA-256との完全一致後だけ確定します。配布EXE以外では適用できません。適用時は終了後PowerShellを非表示で起動し、現EXEを`.previous`へ退避してから置換します。更新版が起動直後に異常終了した場合は旧EXEを戻して再起動します。

更新機能はコード署名を代替しません。Releaseの公開元、SHA-256、GitHub artifact attestationを組み合わせて発行元と成果物を確認します。
