# Windows EXE配布

## 目的と対象

V0.12.0ではPythonを導入していない利用者向けに、Windows 10/11 x64で動作するone-fileのCLI版とGUI版をGitHub Releaseから配布します。CLI版は既存の全コマンド、引数、終了コードを維持し、GUI版は中核操作を画面から提供します。

## 同梱範囲

EXEはCPythonランタイム、標準ライブラリ、`master_duel_recorder_lite`パッケージをPyInstallerで単一ファイルへまとめます。FFmpegとffprobeは同梱しません。理由は配布サイズ、ライセンス表示、脆弱性修正、利用者ごとのエンコーダー要件をPythonアプリと分離するためです。

ビルド依存はPyInstaller 6.21.0とpyinstaller-hooks-contrib 2026.6へ固定します。Windows実行ファイルはWindows上でのみ生成し、UPXは使用しません。EXEには次のWindowsリソースを埋め込みます。

- FileVersion: `0.16.5.0`
- ProductVersion: `0.16.5`
- ProductName: `master-duel-recorder-lite`
- OriginalFilename: `master-duel-recorder-lite.exe`

GUI版のOriginalFilenameは`master-duel-recorder-lite-gui.exe`で、コンソールウィンドウを表示しません。

## 実行時データ

凍結EXEでは`%LOCALAPPDATA%\MasterDuelRecorderLite`を既定の実行時データルートにします。EXEの配置フォルダとPyInstaller one-fileの一時展開先には実行時データを作成しません。通常のPython実行は従来どおりカレントディレクトリ直下の`user_data/`を基準にします。

保存先の優先順位は次のとおりです。

1. `--user-data-dir`
2. `MDRL_USER_DATA_DIR`
3. EXE実行時は`%LOCALAPPDATA%\MasterDuelRecorderLite`、Python実行時はproject root直下の`user_data/`

V0.16.0以前のEXE隣接`user_data/`は自動で移動、削除、上書きしません。継続利用時は利用者がバックアップ後に新しい既定先へ移すか、明示的な保存先指定で参照します。

## ビルドと検証

```powershell
python -m pip install -e ".[build,dev]"
python -W error::ResourceWarning -m unittest discover -s tests
python scripts/build_windows_exe.py
.\scripts\smoke_windows_exe.ps1 -ExePath .\dist\master-duel-recorder-lite.exe -ExpectedVersion 0.16.5
.\scripts\smoke_windows_gui.ps1 -ExePath .\dist\master-duel-recorder-lite-gui.exe -ExpectedVersion 0.16.5
```

CLIスモークは`--version`、`--help`、`config show --json`を検証します。GUIスモークは実ウィンドウの寸法、主要操作部品、バージョン、正常終了を検証します。両EXEを一時フォルダへコピーして起動し、EXE隣接`user_data/`を作成せず、既定パスが分離した`LOCALAPPDATA`配下になることを確認します。読取操作だけではその既定パスも作成しません。

## GitHub Release

`vX.Y.Z`タグをpushするとWindows runnerで次を順に実行します。

1. タグ、`pyproject.toml`、`__version__`の一致検証
2. Ruffと全単体テスト
3. one-file EXEビルドと実EXEスモーク
4. SHA-256ファイル生成
5. GitHub artifact attestationによるbuild provenance作成
6. EXEとSHA-256をGitHub Releaseへ公開

いずれかが失敗した場合はReleaseを作成しません。Actionsはタグ名ではなく検証済みcommit SHAへ固定します。

## FFmpeg子プロセス

WindowsではFFmpegを`CREATE_NO_WINDOW`で起動し、親プロセスのエラーモードへ重大エラーとWindows Error Reportingのダイアログ抑止を設定します。終了コード`0xc0000142`は一時的なDLL初期化失敗として200ミリ秒後に1回だけ再試行します。通常のFFmpegエラーは再試行せず、その終了コードと標準エラーを呼び出し元へ返します。フレーム取得の既定タイムアウトは1回5秒なので、再試行時の最大待機は約10.2秒です。

## セキュリティと既知制約

V0.9.0のEXEはコード署名していません。このためWindows SmartScreenの評価が蓄積されるまで警告される場合があります。利用者はGitHub Releaseの公開元とSHA-256を照合し、必要に応じて`gh attestation verify`でbuild provenanceを検証します。

SHA-256は転送後の破損・改変確認に使いますが、単独では発行者を証明しません。Releaseページとattestationを組み合わせて確認します。将来コード署名証明書を導入する場合は、秘密鍵をリポジトリやReleaseへ保存せず、GitHubの保護された署名基盤を利用します。
