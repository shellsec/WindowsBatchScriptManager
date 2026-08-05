# 各平台脚本运行状态与可选优化

## 当前状态：脚本能否正常运行

### Windows
- **所有类型**：.bat / .cmd / .py / .js / .ps1 / .vbs / .jscript / .wsf / .sh / .exe 均由 `start_script` 正确分支，可正常启动、停止、保活、定时。
- **运行中接管 / 自动识别**：`scan_running_script_processes`、`scan_running_exe_processes` 针对 Windows 命令行解析，工作正常。

### macOS / Linux
- **可正常运行的脚本类型**：
  - **Python**：`python3` 或指定解释器（含 `py -3.11` 等多参数）
  - **Node.js**：`node` 或指定解释器
  - **Shell**：`bash` 或指定解释器（.sh、无扩展名等）
  - **批处理 / unknown**：按 shell 用 bash 执行（.bat/.cmd 在 Mac/Linux 下用 bash 跑，简单内容可能能跑）
  - **PowerShell**：有 `pwsh` 时用 `pwsh -File`，否则回退到 bash
  - **可执行文件**：直接执行路径（需有执行权限）
- **会失败或不适用的类型**（预期行为）：
  - **VBScript / JScript / WSF**：无 cscript/wscript，当前用 bash 执行，一般会失败，属 Windows 专用类型。
- **运行中接管 / 自动识别**：依赖 `_extract_script_path` 解析 cmdline；对常见形式（如 `python3 /path/to/script.py`、`node /path/to/app.js`、`bash /path/to/script.sh`）能解析出路径，列表内项可被接管；极端或复杂命令行可能解析不到，属可优化点。

**结论**：在各自平台下，**常用脚本（Python / Node / Shell / 批处理映射到 bash）可以正常添加、启动、停止、保活、定时**；VBS/JScript/WSF 仅在 Windows 下可用。

---

## 已完成的 Mac/Linux 优化

| 优化项 | 说明 |
|--------|------|
| 打开文件 / 打开目录 | 非 Windows 上使用 `xdg-open` / `open`，不再使用仅 Windows 的 `os.startfile`。 |
| Mac/Linux 添加脚本时文件类型 | 添加/编辑时在非 Windows 上优先展示 Shell / Python / Node / PowerShell，不展示批处理/VBS/WSF/exe 快捷项，减少误选；“全部脚本”仍可选任意文件。 |
| Node 解释器带参数 | Mac/Linux 侧 Node 解释器支持带参数（如 `node --experimental-modules`），与 Python 一样按空格拆成多参数再启动。 |

## 建议优化（非必须）

| 优化项 | 说明 | 优先级 |
|--------|------|--------|
| 运行中扫描在 Mac/Linux 上增强 | 在 `_extract_script_path` 或 `scan_running_script_processes` 中为 darwin/linux 增加对 `python3`、`node`、`bash` 等 argv 的解析，提高“运行中接管”识别率 | 低 |
| 启动失败时提示 | `start_script` 返回 None 时（如解释器未找到、无执行权限），在状态栏或弹窗提示“启动失败：未找到 python3”等 | 中 |

---

## 功能模块与平台对应

| 功能 | Windows | macOS | Linux |
|------|---------|-------|-------|
| 启动 / 停止 / 重启 | ✅ | ✅ | ✅ |
| 保活（崩溃自动重启） | ✅ | ✅ | ✅ |
| 定时任务 | ✅ | ✅ | ✅ |
| 日志输出到 logs/ | ✅ | ✅ | ✅ |
| 运行中接管（按路径匹配） | ✅ | ✅ 常见 cmdline | ✅ 常见 cmdline |
| 自动识别（扫描运行中） | ✅ | ✅ 部分 | ✅ 部分 |
| 开机/登录自启 | ✅ 注册表 | ✅ launchd | ✅ systemd user |
| 端口/进程/访问 | ✅ psutil | ✅ psutil | ✅ psutil |
| 识别 exe 加入 | ✅ | ⚪ 无 .exe | ⚪ 可选：二进制路径 |

当前脚本在各平台**可以按上述范围正常使用**；若要进一步优化体验，可按上表优先级逐步做。
