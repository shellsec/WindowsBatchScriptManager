# LinuxBatchScriptManager

Linux 独立版脚本管理器，功能与 [WindowsBatchScriptManager](https://github.com/shellsec/WindowsBatchScriptManager) 一致，仅包含 Linux 平台实现，可单独克隆、在 Linux 上运行。

## 说明

- **配置目录**：未打包时使用程序同目录；打包或 frozen 时使用 `$XDG_CONFIG_HOME/ScriptManager/`（默认 `~/.config/ScriptManager/`）。
- **登录自启**：通过 systemd 用户级服务（`~/.config/systemd/user/scriptmanager.service`）实现，需 `systemctl --user` 可用。
- **脚本启动**：使用 `python3` / `node` / `bash` 等解释器，在后台 subprocess 运行（不新开终端窗口）。

## 依赖

```bash
pip install -r requirements.txt
```

- 若使用登录自启，需 systemd 用户会话（多数桌面环境均支持）。
- 脚本执行依赖系统已安装对应解释器（python3、node、bash 等）。

## 运行

**推荐**：在上一级 **WindowsBatchScriptManager** 仓库根目录执行 `python3 main.py`（自动加载本目录的 `platform_impl`）。

本目录独立运行：

```bash
python3 main.py
```

- **GUI**：无参数启动即打开图形界面。
- **CLI**：带参数时走命令行，例如 `python3 main.py --list --json`、`python3 main.py --start-all`。
- 若在非 Linux 下运行会提示「仅支持 Linux」并退出；若未加载到 `linuxBatchScriptManager` 会提示在项目根目录运行。

跨平台自测步骤见主仓库 [README](../README.md) 中 **「macOS / Linux 快速验证」**。

## 与主仓库关系

本目录为**独立项目**，可单独发布为独立仓库；主仓库 `WindowsBatchScriptManager` 中的 `linuxBatchScriptManager/` 为同一实现，供跨平台版 `main.py` 在 Linux 上加载。
