# 跨平台脚本管理器 · 设计清单

在现有 **Windows Batch Script Manager** 基础上，做一版可运行于 Windows / macOS / Linux 的跨平台脚本管理器的功能与接口清单。

**当前实现**：已创建 `mac/` 与 `linux/` 目录，主程序 `main.py` 在 macOS/Linux 上会自动加载对应平台实现（配置路径、登录自启、脚本启动）。在项目根目录执行 `python main.py` 即可（Mac 或 Linux 上）。

---

## 一、功能列表（与现有对齐 + 平台差异）

| 序号 | 功能 | Windows 现状 | Mac/Linux 实现要点 |
|-----|------|--------------|--------------------|
| 1 | 脚本列表：添加/编辑/删除/复制 | ✅ WindowsBatchScriptManager_scripts.json | 同：JSON 配置，路径/工作目录/解释器按平台解析 |
| 2 | 多种类型：批处理/Python/Node/Shell 等 | ✅ .bat/.cmd/.py/.js/.ps1… | Mac/Linux：.sh、.py、.js、.command；无 .bat/.cmd，用 shell 替代 |
| 3 | 启动 / 停止 / 重启 | ✅ subprocess + CREATE_NEW_CONSOLE | Mac: `open -a Terminal` 或 subprocess；Linux: `xterm`/`gnome-terminal` 或 subprocess；停止统一用进程树 kill |
| 4 | 状态与 PID、定时刷新 | ✅ psutil / 4 秒轮询 | 同：psutil 跨平台；刷新间隔可配置 |
| 5 | 运行中接管（按路径匹配已有进程） | ✅ scan_running_script_processes | 同思路：枚举进程 + 命令行/可执行路径匹配（Mac/Linux 用 psutil 或 /proc） |
| 6 | 端口识别、标题、访问 | ✅ psutil + HTTP 取 title | 同：psutil 跨平台；localhost 访问逻辑一致 |
| 7 | 开机/登录自启 | ✅ 注册表 Run | Mac: launchd 或 登录项；Linux: systemd user 或 XDG autostart |
| 8 | 定时任务（每天/每时/每分/每周/每月） | ✅ 自研轮询 | 可保留自研，或 Mac 用 launchd、Linux 用 systemd timer 委托（复杂度高，先自研） |
| 9 | 保活（崩溃自动重启） | ✅ 定时检查 pid 再启动 | 同：定时检查进程是否存在，不存在则重新 start_script |
| 10 | 日志输出到 logs/ | ✅ 重定向 stdout/stderr | 同：subprocess 重定向到文件 |
| 11 | 分组、筛选、关键词搜索 | ✅ 纯 UI/数据 | 无平台差异 |
| 12 | 执行历史、异常通知、Webhook | ✅ JSON + 弹窗 | 无平台差异 |
| 13 | 导出/导入配置 | ✅ JSON | 无平台差异 |
| 14 | 本地 IP / 外网 IP | ✅ socket + API | 同：socket 与 HTTP 跨平台；联网判断逻辑可复用 |

---

## 二、各平台要用的接口/命令

### 1. 启动脚本时「是否新开终端」

| 平台 | 方式 | 说明 |
|------|------|------|
| Windows | `subprocess.CREATE_NEW_CONSOLE` 或 `cmd /c start` | 已有 |
| macOS | `open -a Terminal script.sh` 或 `subprocess` 不新开窗 | 需选：新开 Terminal.app 还是当前进程子进程 |
| Linux | `xterm -e "cmd"` / `gnome-terminal --` / `konsole -e` 或 `subprocess` | 需检测桌面环境或配置用哪种终端 |

### 2. 按类型执行脚本

| 类型 | Windows | macOS | Linux |
|------|---------|-------|-------|
| 批处理 | cmd /c 或 powershell | 无；用 shell 代替 | 无；用 bash/sh 代替 |
| Shell | bash（Git Bash） | /bin/bash、zsh | /bin/bash、sh |
| Python | python/py | python3 | python3 |
| Node | node | node | node |
| PowerShell | powershell -File | 可选 pwsh | 可选 pwsh（若安装） |

### 3. 开机/登录自启

| 平台 | 方式 | 配置位置/命令 |
|------|------|----------------|
| Windows | 注册表 HKCU\…\Run | 已有 |
| macOS | launchd 用户级 | ~/Library/LaunchAgents/xxx.plist；launchctl load |
| macOS 备选 | 登录项 | 用 AppleScript/System Events 添加登录项 |
| Linux | systemd user | ~/.config/systemd/user/xxx.service；systemctl --user enable |
| Linux 备选 | XDG 自启动 | ~/.config/autostart/xxx.desktop |

### 4. 进程枚举与路径匹配（运行中接管）

| 平台 | 方式 |
|------|------|
| 全平台 | psutil.process_iter()；p.exe()、p.cmdline()、p.cwd() |
| Linux 补充 | /proc/<pid>/exe、/proc/<pid>/cmdline 可做回退 |

### 5. 杀进程树

| 平台 | 方式 |
|------|------|
| 全平台 | psutil：先取 children，递归 kill；最后 kill 父进程 |

### 6. 配置/数据路径

| 平台 | 建议 |
|------|------|
| Windows | 当前：程序同目录 WindowsBatchScriptManager_scripts.json | 可保留 |
| macOS | ~/Library/Application Support/WindowsBatchScriptManager/ 或 程序同目录 |
| Linux | ~/.config/WindowsBatchScriptManager/ 或 $XDG_CONFIG_HOME | 或程序同目录 |

---

## 三、实现顺序建议

1. **阶段 1：跨平台跑通**
   - 用条件判断 `sys.platform == "win32"` / `"darwin"` / `"linux"` 分支。
   - 统一：配置读写、列表 UI、启动/停止/重启（先不新开终端，仅 subprocess）。
   - 启动脚本：按类型选解释器（python3、node、bash），不依赖 .bat/.cmd。

2. **阶段 2：平台特性补全**
   - Windows：保持现有注册表、CREATE_NEW_CONSOLE、.bat/.ps1。
   - macOS：launchd 自启；可选 `open -a Terminal` 新开终端。
   - Linux：systemd user 或 autostart 自启；可选 xterm/gnome-terminal 新开终端。

3. **阶段 3：体验对齐**
   - 运行中接管、端口、定时任务、保活、历史、通知、IP 等与现有逻辑对齐。
   - 配置路径按平台选应用数据目录，并做迁移（从“同目录”到“标准目录”可选）。

---

## 四、技术栈建议

| 项目 | 建议 | 说明 |
|------|------|------|
| GUI | 保持 Tkinter 或 迁到 PySide6 | Tk 已跨平台；PySide6 更现代、与 Shogun 一致 |
| 进程 | psutil | 已用，跨平台 |
| 配置 | JSON | 已用 |
| 打包 | PyInstaller（Windows）；py2app（Mac）；PyInstaller 或 AppImage（Linux） | 各平台分别打 |

---

## 五、文件/目录结构建议（跨平台版）

```
WindowsBatchScriptManager/   # 或改名为 ScriptManager
├── main.py                  # 入口，按平台分支
├── core/
│   ├── config.py            # 配置路径 + 读写（按平台）
│   ├── runner.py            # start_script / stop_script（按平台）
│   ├── process_utils.py     # 进程枚举、杀树、端口（psutil）
│   └── schedule.py          # 定时任务轮询
├── gui/
│   ├── main_window.py       # 主窗口、表格、工具栏
│   └── dialogs.py           # 编辑、历史、通知等弹窗
├── platform/
│   ├── win32.py             # 注册表、CREATE_NEW_CONSOLE、.bat
│   ├── darwin.py            # launchd、open -a Terminal
│   └── linux.py             # systemd/autostart、xterm/gnome-terminal
├── config.json
└── CROSS_PLATFORM_DESIGN.md # 本清单
```

---

本清单可直接作为「跨平台版脚本管理器」的需求与实现对照表使用；若只做原型，可先完成「阶段 1」再按需补「阶段 2/3」。
