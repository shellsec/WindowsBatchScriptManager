# WindowsBatchScriptManager · 繁體中文

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja-JP.md) · [한국어](README.ko-KR.md) · [Deutsch](README.de-DE.md) · [Français](README.fr-FR.md) · [Español](README.es-ES.md) · [Português (Brasil)](README.pt-BR.md) · [Русский](README.ru-RU.md)

在 Windows 上管理批次檔、Python、Node.js 與 PowerShell 工作，支援排程、佇列及遠端 API。

[下載 Windows 版](https://github.com/shellsec/WindowsBatchScriptManager/releases)
## 快速開始

桌面使用時直接開啟單檔 EXE。部署遠端 API 時，將 EXE 與 API 腳本放在伺服器的同一個資料夾，安裝 Python 相依套件後啟動服務。升級時請保留原有設定 JSON。

```powershell
python -m pip install fastapi "uvicorn[standard]"
python WindowsBatchScriptManager_api_server.py --host 0.0.0.0 --port 8765 --api-key "<API_KEY>"
```

選單「語言」的第一項是「跟隨系統」，第二項是 English。可手動選擇其他語言。

## 一鍵清理 C 槽

v1.3.4 的「一鍵清理 C 槽」位於開啟工具資料夾按鈕右側。清理在背景執行，每一步的結果都會顯示在下方日誌。它會清除 C 槽使用者和系統暫存目錄中超過 7 天未修改的 `.tmp` 和 `.temp` 檔案、目前使用者在 C 槽的縮圖快取，**不經確認即永久清空 C 槽資源回收筒**，並重新整理 DNS 快取。清理使用目前的權限，並保留 Windows Update 快取、Prefetch 和系統日誌。

![執行畫面](docs/screenshots/zh-TW.jpg)

[API / MCP](docs/USER_GUIDE.en-US.md#remote-api-and-mcp) · [Apache-2.0](LICENSE) · [NOTICE](NOTICE)
