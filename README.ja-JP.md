# WindowsBatchScriptManager · 日本語

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja-JP.md) · [한국어](README.ko-KR.md) · [Deutsch](README.de-DE.md) · [Français](README.fr-FR.md) · [Español](README.es-ES.md) · [Português (Brasil)](README.pt-BR.md) · [Русский](README.ru-RU.md)

Windows でバッチ、Python、Node.js、PowerShell のタスクを管理できます。スケジュール、実行キュー、リモート API に対応しています。

[Windows 版をダウンロード](https://github.com/shellsec/WindowsBatchScriptManager/releases)
## クイックスタート

デスクトップ版は単体の EXE を起動します。リモート API を使うには、サーバーの同じフォルダーに EXE と API スクリプトを置き、Python の依存パッケージをインストールしてサービスを起動します。更新時は既存の設定 JSON を保持してください。

```powershell
python -m pip install fastapi "uvicorn[standard]"
python WindowsBatchScriptManager_api_server.py --host 0.0.0.0 --port 8765 --api-key "<API_KEY>"
```

言語メニューの先頭は「システムに従う」、2 番目は English です。言語は手動でも選べます。

## C ドライブのクリーンアップ

v1.3.4 では、ツールフォルダーを開くボタンの隣から C ドライブのクリーンアップを実行できます。処理はバックグラウンドで実行され、結果は下部のログに表示されます。C ドライブ上のユーザーおよびシステムの一時フォルダーで、最終更新から 7 日を超えた `.tmp` と `.temp` を削除します。C ドライブ上のユーザーのサムネイルキャッシュを削除し、**C ドライブのごみ箱を確認なしで完全に空にして**、DNS キャッシュを消去します。現在の権限で実行し、Windows Update キャッシュ、Prefetch、システムログは保持します。

![実行画面](docs/screenshots/ja-JP.jpg)

[API / MCP](docs/USER_GUIDE.en-US.md#remote-api-and-mcp) · [Apache-2.0](LICENSE) · [NOTICE](NOTICE)
