# master-duel-recorder-lite

master-duel-recorder-lite は、OBS に依存しない Yu-Gi-Oh! Master Duel 向け録画支援ツールを目指す実験的な Python プロジェクトです。

このリポジトリでは、録画・検出・履歴保存・アップロード準備を Python 側に集約し、外部ツールとして FFmpeg などを利用する構成を前提にします。初心者向けに言うと、OBS の中にプラグインを入れる方式ではなく、このアプリ自身が録画フローを管理する方式です。

## 方針

- OBS Plugin と OBS WebSocket には依存しない
- Python を中心に保守しやすい構成にする
- ユーザーの録画データ、設定、認証情報、キュー状態を `user_data/` に分離する
- ゲーム画像やテンプレート画像など、配布できない素材はリポジトリに含めない
- 復旧安全性を優先し、壊れた状態で自動処理を進めない

## 現在の状態

このリポジトリは初期設計段階です。最初の実装は、録画そのものではなく、設定・保存先・設計ドキュメントの土台作りから始めます。これは、録画処理は環境依存が強く、先に保存先や責務分離を決めておかないと後から安全に変更しにくいためです。

## 開発メモ

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m master_duel_recorder_lite
```

## ライセンス

MIT License

## 免責

このプロジェクトは非公式のファンメイドツールです。KONAMI および Yu-Gi-Oh! Master Duel の公式プロジェクトではありません。Yu-Gi-Oh! Master Duel のゲーム素材は配布しません。
