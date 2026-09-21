# MacBatchScriptManager

macOS 平台实现（`platform_impl`）。日常请在上一级仓库根目录运行跨平台 `main.py`。

## 说明

- **配置目录**：未打包时使用程序同目录；打包为 .app 或可执行文件时使用 `~/Library/Application Support/ScriptManager/`。
- **登录自启**：通过 launchd 用户级 LaunchAgent（`~/Library/LaunchAgents/com.scriptmanager.app.plist`）实现。
- **脚本启动**：使用 `python3` / `node` / `bash` 等解释器，在后台 subprocess 运行（不新开 Terminal 窗口）。

## 依赖

在仓库根目录安装：

```bash
pip install -r requirements.txt
```

## 运行

**推荐**：在上一级 **WindowsBatchScriptManager** 仓库根目录执行（自动加载本目录的 `platform_impl`）：

```bash
python3 main.py
```

本目录 `main.py` 只是薄启动器，同样转到根目录 `main.py`，避免功能落后。根目录 GUI 已包含：HTTP 任务 GET/POST 与自定义请求头（如 `X-API-Key`）、工具栏「一键加载API调用」「打开工具目录」、API 独立启停等。细节见主仓库 [README](../README.md)。

- **GUI**：无参数启动即打开图形界面。
- **CLI**：带参数时走命令行，例如 `python3 main.py --list --json`、`python3 main.py --start-all`。

跨平台自测步骤见主仓库 [README](../README.md) 中 **「macOS / Linux 快速验证」**。

## 与主仓库关系

本目录提供 macOS `platform_impl`，并可单独发布；主程序始终是仓库根目录的 `main.py`。
