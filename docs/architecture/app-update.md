# アプリケーション更新

## 目的

V1.0.1では、利用者がGitHub Releaseを毎回手動で探さなくても正式版の更新有無を確認し、安全性を確認したGUI EXEへ置換できるようにします。V1.4.3では自己置換用PowerShellを廃止し、GUIに同梱した専用updater EXEで自動更新を維持します。更新確認は利便機能であり、録画や戦績管理の開始条件にはしません。

## 更新契約

- GitHubの`releases/latest`からdraftでもprereleaseでもないReleaseだけを対象にする
- `X.Y.Z`形式で現在版より新しい場合だけ通知する
- `master-duel-recorder-lite-gui.exe`、`master-duel-recorder-lite-updater.exe`、それぞれ同名の`.sha256`が揃わないReleaseを拒否する
- HTTPS以外のURL、256MiBを超えるEXE、Release記載サイズと異なるEXEを拒否する
- 取得したGUI EXEは置換前に`--smoke-test`で別プロセス起動し、期待バージョン、終了コード、スモーク結果、実行時データ非作成を検証する
- 適用時はGUIへ同梱された`master-duel-recorder-lite-updater.exe`を一時更新フォルダへコピーして起動し、終了したGUIプロセスの外側から置換する
- 自動確認は設定で無効化でき、ダウンロードと適用は利用者の明示操作を必須にする

## 適用と復元

取得したEXEは一時ファイルへ保存し、公開SHA-256との完全一致とGUIスモーク成功後だけ確定します。配布EXE以外では適用できません。適用時は専用updaterが親GUIプロセスの終了を待ち、候補EXEのSHA-256を再確認してから現在のGUI EXEを`.previous`へ退避します。候補EXEは同じフォルダの`.staged`へコピーしてから`os.replace`で入れ替え、置換後に改めて`--smoke-test`を実行します。更新版が起動検証に失敗した場合は旧EXEを戻し、成功した場合だけ更新後GUIを再起動します。

この方式は手動ダウンロードを通常導線にしません。手動ダウンロードは自動更新が失敗した場合の緊急回避策に限り、プロダクト導線としてはアプリ内の「更新確認」「ダウンロード」「終了して更新」を維持します。V1.4.1/V1.4.2の旧自己置換コードは、置換を始めるプロセス自身のPyInstaller展開状態に依存するため、起動前のGUI EXEスモークだけでは更新適用時のPython DLL展開失敗を防げません。V1.4.3以降の配布EXEから、置換責務を別EXEへ分離します。

更新機能はコード署名を代替しません。Releaseの公開元、SHA-256、GitHub artifact attestationを組み合わせて発行元と成果物を確認します。
