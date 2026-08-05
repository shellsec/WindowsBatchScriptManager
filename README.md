# WindowsBatchScriptManager

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-1.1.0-green.svg)](version.py)

## ☕ 请我喝可乐
开源不易，欢迎赞助支持：
👉 [爱发电](https://ifdian.net/a/shellsec)

[English](#english) | [中文](#中文)

> 一个轻量级的脚本管理器（**v1.1.0**），支持 **Windows / macOS / Linux**，统一管理批处理、Python、Node.js、PowerShell 等多种脚本类型，提供执行历史、异常通知、定时任务等高级功能；支持**命令行（CLI）**与**HTTP API**，便于自动化与远程调用。

**WindowsBatchScriptManager** 支持在 Windows / macOS / Linux 下运行；Windows 可打包为 exe，本机或另一台机器可通过 CLI 或 API 启停、添加脚本等。

---

<a name="中文"></a>
## 中文版

在 Windows 下集中管理多个批处理脚本（`.bat` / `.cmd`）及 Python、Node.js、PowerShell 等脚本，避免反复手动点开、查路径、起停与重启。**macOS / Linux** 下同样可用（`python3 main.py`），脚本类型以 Shell / Python / Node 等为主。

### 使用场景

| 场景 | 说明 |
|------|------|
| 个人电脑脚本收纳 | 备份、同步、本地服务等 bat/py 统一列表启停，可选「启动本工具时自动启动」、保活 |
| 开发联调 | 多服务一键全部启动/停止，分组筛选，工作目录 + 启动参数区分环境 |
| 定时运维 | 每天/每小时等定时启动、重启、停止，无需单独配 Windows 计划任务 |
| 内网远程控制 | 可选 `WindowsBatchScriptManager_api_server.py`，HTTP 启停任务（需 Python + 鉴权） |
| 接管已在跑的进程 | 扫描运行中脚本或按路径自动匹配为「运行中」 |

### 平台差异（简要）

| 能力 | Windows | macOS / Linux |
|------|---------|----------------|
| 运行方式 | `python main.py` 或打包 **exe** | 项目根目录 `python3 main.py` |
| 脚本类型 | bat/cmd、PowerShell、py、node 等 | 以 **Shell / py / node** 为主，无原生 bat |
| 配置目录 | 默认 exe/脚本**同目录** | 默认同目录；打包后见各平台 `platform_impl`（如 `~/.config/ScriptManager`） |
| 登录自启 | 注册表 Run | launchd（Mac）/ systemd user（Linux） |
| 系统关机/重启任务、工具栏关机按钮 | ✅ | ❌（仅 Windows） |
| 停止「软件外所有批处理」 | ✅ | ❌ |
| HTTP API | ✅（需 Python） | ✅ |
| 相对路径（未填工作目录） | 以配置文件目录为基准 | 同左（根目录 `main.py` 已统一） |

> 仓库内 `linuxBatchScriptManager/`、`macBatchScriptManager/` 主要为 **platform_impl** 与可单独发布的副本；日常跨平台使用请以**项目根目录** `main.py` 为准。

### 功能

- **统一管理**：添加、编辑、删除脚本；支持多种脚本类型：
  - **批处理**：`.bat`、`.cmd`
  - **Python**：`.py`
  - **Node.js**：`.js`、`.mjs`、`.cjs`、`.ts`、`.jsx`、`.tsx`
  - **PowerShell**：`.ps1`
  - **VBScript**：`.vbs`
  - **JScript**：`.js`、`.jse`（优先识别为 Node.js）
  - **WSF**：`.wsf`
  - **Shell**：`.sh`（Git Bash/WSL）
  - **HTTP GET**：`http://` 或 `https://` URL（自动发送 HTTP 请求）
  - **进程管理**：进程名（如 `notepad.exe`），可直接启停程序
  - **直接命令**：直接输入命令行执行（如 `ping 8.8.8.8`、`ipconfig`、`python -c "print('hello')"`），支持管道、重定向等；支持定时任务与日志输出
  - **系统关机 / 系统重启**：任务类型可选，无需脚本路径；手动「启动」或定时「启动」时执行（先停托管脚本，再正常关机/重启 Windows）
- **启动 / 停止 / 重启任务**：对选中项一键操作；停止时会结束进程树（含子进程）
- **系统关机 / 重启**：GUI 按钮或任务类型；远程 API `GET/POST /shutdown`、`/reboot`
- **API 服务独立管理**：工具栏 `API: 启动API / 停止API / 重启API`，默认 `WindowsBatchScriptManager_api_server.py`，可勾选「启动本工具时自动启动API」；**不必加入任务列表**（白名单保护，批量停/重启不会先挂 API）
- **路径与工作目录**：每条记录可设置脚本路径和工作目录（支持相对路径，未填工作目录时以配置文件目录为基准）；配置保存到 `WindowsBatchScriptManager_scripts.json`，下次打开自动加载
- **状态与 PID**：表格显示运行状态、主进程 PID；定时刷新间隔可在菜单「设置」→「刷新间隔」选择 2/4/8 秒，可「暂停刷新」/「恢复刷新」；路径不存在的项状态列显示「路径无效」
- **运行中接管**：列表中已有、但从外部（如 CMD、资源管理器）启动的脚本（含 .exe），会在刷新时按路径自动匹配并显示为「运行中」，无需再点「启动」；需 `psutil`
- **端口识别**：自动显示该进程树正在监听的端口（依赖 `psutil`，无则用 `netstat -ano` 降级）
- **页面标题**：表格「标题」列自动获取 Web 服务的页面标题（后台异步获取，不阻塞 UI）
- **点击访问**：工具栏「访问」或右键「访问 :端口」、弹窗内「:端口」按钮，在浏览器打开 `http://localhost:端口`
- **进程查看与资源监控**：选中运行中的项，点「端口/进程」可查看主进程及子进程的 PID、名称、路径、CPU%、内存 MB（需 `psutil`）
- **扫描运行中**：识别**系统当前正在运行**的脚本进程（从命令行解析脚本路径），可「加入管理器」或「全部加入」纳入管理，也可「结束选中」
- **全部启动 / 全部停止**：一键启动或停止列表中所有项
- **停止软件外所有批处理**：停止系统中所有正在运行的批处理脚本（包括列表中的和列表外的）
- **复制**：克隆选中项（名称加「(副本)」），可再编辑后用作变体；右键菜单「复制」子菜单可复制名称/路径/PID
- **指定解释器**：
  - **Python**：可填 `py -3.11`、解释器路径等，留空即用当前 Python
  - **Node.js**：可填 `node`、`node.exe` 或路径，留空即用 `node`
  - **Shell**：可填 `bash`、`sh` 或路径，留空即用 `bash`
- **崩溃自动重启（保活）**：编辑时勾选「崩溃后自动重启(保活)」，进程异常退出时每隔几秒自动重新启动（在编辑弹窗中设置，表格不显示）；**崩溃循环检测**：1分钟内重启超过5次自动停止保活，避免无限崩溃
- **运行时长与退出码**：进程退出时捕获取消码和运行约 X 秒，状态栏显示；**启动后很快退出**时约 1.5 秒后同样提示
- **记录输出到 logs/**：Python、Node.js、PowerShell 可勾选「启动时记录输出到 logs/」，stdout/stderr 写入 `logs/`；退出时若有保存则提示路径，**启动后很快退出**时可弹窗展示最近输出
- **分组/标签**：编辑时可设置「分组/标签」（如：项目A、监控、API等），表格显示分组列；表格上方可「筛选分组」快速过滤
- **关键词搜索与定位**：筛选行可输入「关键词」，点「定位」跳转到第一个匹配项（ID/名称/分组/路径/工作目录），点「下一个」继续查找下一项；支持回车触发定位
- **IP地址显示**：筛选行显示本地IP（点击复制）；第三行「停止软件外所有批处理」右侧显示外网IP，点击可复制；本地 IP 与外网 IP 均在主循环启动后异步加载，不阻塞启动；外网 IP 会先判断是否联网，未联网则直接显示「无法获取」；筛选行支持横向滚动，内容较多时可拖动查看
- **首页**：菜单栏「首页」在浏览器打开项目 GitHub 页面（https://github.com/shellsec/WindowsBatchScriptManager）
- **导出/导入配置**：工具栏「导出配置」备份到文件，「导入配置」支持合并或替换，便于迁移和备份
- **定时任务**：支持每天、每小时、每分钟、每周、每月、每N小时、每季度、每年、单次执行、工作日的定时启动/重启/停止
- **执行历史记录**：自动记录每个脚本的启动/停止时间、运行时长、退出码；查看历史统计（成功率、平均运行时长）；工具栏「执行历史」查看
- **异常通知**：脚本异常退出时自动弹窗提醒；支持 Webhook 通知（GET/POST），可配置通知条件（退出码非0、快速退出等）；工具栏「通知配置」设置
- **批量编辑**：选中多个脚本，工具栏「批量编辑」可批量修改分组、解释器、启动参数、定时任务、保活、日志输出等
- **拖放添加**：将脚本文件拖入窗口即可添加为新任务；支持批量拖入
- **HTTP 请求任务**：支持添加 `http://` 或 `https://` URL 作为 HTTP GET 请求任务，启动时自动发送请求并获取响应状态码；HTTP 请求类型自动禁用保活（curl 正常退出后无需重启）
- **进程管理任务**：支持添加进程名（如 `notepad.exe`）作为进程管理任务，可直接启停指定程序，进程不存在也不影响添加
- **直接命令任务**：支持直接输入命令行（如 `ping 8.8.8.8`、`ipconfig /all`、`python -c "print('hello')"`），通过 `cmd.exe /c` 执行，支持管道、重定向等 Windows 命令行特性；支持保活、定时任务、日志输出；自动识别：输入含空格且第一个词无文件扩展名的命令时自动切换为命令类型
- **添加/编辑弹窗**：统一表单填名称、路径、工作目录、分组、解释器（Python/Node.js/Shell）、保活、记录输出（Python/Node.js/PowerShell/命令）、定时任务；路径与工作目录可「浏览」选择；根据脚本类型自动显示/隐藏相应选项；类型下拉框可选择脚本/HTTP请求/进程管理/直接命令/系统关机/系统重启
- **主面板添加(手动)按钮**：工具栏「添加(手动)」按钮可快速打开添加弹窗（手动输入 URL、进程名或命令），无需选择本地文件
- **右键菜单**：支持打开文件、打开目录、访问端口、复制信息、分组启停、查看日志等快捷操作
- **行排序**：Alt+拖拽行可调整顺序；Alt+↑/↓键盘快捷键上下移动行；右键菜单「上移/下移」
- **模板系统**：保存脚本配置为模板，从模板快速添加新脚本，管理已有模板
- **开机自启**：右键菜单可将管理器设为 Windows 开机自启动
- **查看日志**：右键菜单查看脚本日志（无论是否运行中），自动显示最后100KB内容
- **按钮快捷键提示**：鼠标悬停工具栏按钮时显示对应快捷键（如 Ctrl+N、F5 等）

### 运行方式

1. 安装依赖（推荐，用于端口与进程详情）：
   ```bash
   pip install -r requirements.txt
   ```
   不装也可运行，端口识别会改用 `netstat`，进程详情会简化。

2. 启动程序：
   ```bash
   python main.py
   ```
   开机自启且不想挡桌面：**设置 → 启动后最小化到任务栏**；或 `python main.py --minimized`（也可写入开机自启命令）。关闭窗口仍会退出程序（无托盘）。
   **macOS / Linux**：在项目根目录执行 `python3 main.py`（不要进子目录里的独立副本，除非维护单独仓库）。

### macOS / Linux 快速验证

在仓库根目录执行（将 `test.sh` 换成你的脚本路径）：

```bash
pip install -r requirements.txt
python3 main.py --config-path
# 记下输出的 WindowsBatchScriptManager_scripts.json 所在目录

cat > test.sh <<'EOF'
#!/bin/bash
echo "hello from test.sh"
sleep 60
EOF
chmod +x test.sh

python3 main.py --add --path "./test.sh" --name "mac-linux-test" --script-type shell
python3 main.py --list --json
python3 main.py --start 1
python3 main.py --status 1 --json
python3 main.py --stop 1
```

可选：测试启动参数与环境变量（需任务支持对应解释器）：

```bash
python3 main.py --update 1 --args "--version" --env-vars "MY_TEST=1"
```

可选 API（另开终端）：

```bash
pip install fastapi uvicorn
python3 WindowsBatchScriptManager_api_server.py --host 127.0.0.1 --port 8765
curl "http://127.0.0.1:8765/list"
```

### 命令行（CLI）

不带参数运行为 GUI；带参数则为 CLI，执行后退出，便于脚本与自动化调用。**Windows 打包为 exe 后**同样支持：`WindowsBatchScriptManager.exe --list --json`。

| 参数 | 说明 |
|------|------|
| `--config-path` | 打印脚本列表配置路径（`WindowsBatchScriptManager_scripts.json`） |
| `--list` / `-l` [--json] | 列出所有脚本（id、name、path、running、pid） |
| `--status` [ID 或名称] [--json] | 查询一条或全部状态 |
| `--start` ID或名称 | 启动 |
| `--stop` ID或名称 | 停止 |
| `--restart` ID或名称 | 重启 |
| `--start-all` | 全部启动 |
| `--stop-all` | 全部停止 |
| `--restart-all` | 全部重启（先停后启） |
| `--shutdown` | 停止托管脚本后关机 Windows（可选 `--force`） |
| `--reboot` | 停止托管脚本后重启 Windows（可选 `--force`） |
| `--add` | 添加脚本（需 `--path`；可选 `--name`、`--work-dir`、`--group`、`--script-type`、`--interpreter`、`--args`、`--env-vars`、`--auto-restart`、`--log-output`、定时相关参数等） |
| `--update` ID或名称 | 更新一条（字段同 add；支持 `--no-auto-restart`、`--no-log-output`） |
| `--delete` ID或名称 | 删除一条 |
| `--export` [文件路径] | 导出配置（不写路径则输出到 stdout） |
| `--import` 文件路径 [--replace] | 导入配置（默认合并，--replace 为覆盖） |

ID 可为数字（如 `2`），名称支持子串匹配（如 `trae` 匹配「trae-changelog」）。输出机器可读时加 `--json`。

示例：
```bash
python main.py --list --json
python main.py --start 1
python main.py --add --path "C:\path\to\script.py" --name "我的脚本" --script-type python
```

### HTTP API（供另一台机器调用）

通过 `WindowsBatchScriptManager_api_server.py` 提供 HTTP 接口，内部通过 subprocess 调用 main 的 CLI，实现远程启停、添加、删除、导出/导入等。

**注意**：API 服务可在 GUI **「API: 启动API」** 独立管理，**不必加入任务列表**。若已加入，`/shutdown`、`/reboot`、`/restart-all` 会自动跳过 API（白名单），避免接口先挂；仍建议单独运行，不要用 `/restart-all` 管理 API 任务。

1. 安装 API 依赖：
   ```bash
   pip install fastapi "uvicorn[standard]"
   ```
2. 启动 API 服务：
   ```bash
   python WindowsBatchScriptManager_api_server.py --port 8765
   ```
   - 允许本机以外访问：`--host 0.0.0.0`
   - 自定义 API Key：`--api-key your-secret` 或环境变量 `API_KEY`
3. 请求时请求头需带：`X-API-Key: <密钥>`（默认 `change-me-in-production`，生产环境务必修改）。使用 `--no-auth` 时可不带 X-API-Key 直接 GET/POST。

可选 **GET /exec?cmd=...**（默认关闭）：在 API 脚本内去掉 `# ENABLE_GET_CMD_EXEC = True` 行首注释后启用；高危，仅内网且务必鉴权。

接口示例：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /list | 列表（同 CLI --list --json） |
| GET | /status?id=2 | 状态 |
| POST | /start?id=2 | 启动 |
| GET | /start?id=2 | 启动（支持 GET） |
| POST | /stop?id=2 | 停止 |
| GET | /stop?id=2 | 停止（支持 GET） |
| POST | /restart?id=2 | 重启 |
| GET | /restart?id=2 | 重启（支持 GET） |
| POST/GET | /update?id=2 | 更新（可选 name, path, work_dir, group, script_type, interpreter, **args**, **env_vars**, auto_restart, log_output, schedule_*、depends_on 等） |
| POST | /add?path=... | 添加（path 必填；可选 name, work_dir, group, script_type, interpreter, **args**, **env_vars**, schedule_* 等） |
| POST | /start-all | 全部启动 |
| GET | /start-all | 全部启动（支持 GET） |
| POST | /stop-all | 全部停止 |
| GET | /stop-all | 全部停止（支持 GET） |
| POST | /restart-all | 全部重启 |
| GET | /restart-all | 全部重启（支持 GET） |
| POST/GET | /shutdown | 停止托管脚本后关机（可选 `?force=true`） |
| POST/GET | /reboot | 停止托管脚本后重启系统（可选 `?force=true`） |
| GET | /config-path | 配置路径 |
| DELETE | /delete?id=2 | 删除 |
| GET | /export | 导出配置 |
| POST | /import?replace=false | 导入，body: `{"items": [...]}` |

示例（另一台机器）：
```bash
curl -H "X-API-Key: change-me-in-production" "http://192.168.1.10:8765/list"
curl -X POST -H "X-API-Key: change-me-in-production" "http://192.168.1.10:8765/start?id=1"
```

CLI 与 API 在 **Windows / macOS / Linux** 下均可使用；API 在无 exe 环境下会自动使用 `python(3) main.py` 调用。

### 使用说明

1. **添加**：选「添加」→ 选择脚本文件（支持批处理、Python、Node.js、PowerShell、VBScript、WSF、Shell 等）→ 在弹窗中填名称、工作目录、分组/标签、解释器（Python/Node.js/Shell 需填写）、是否保活、定时任务，可点「浏览」选路径与目录；也可通过工具栏「添加(手动)」按钮直接打开弹窗手动输入 URL（`http://` 或 `https://`）、进程名（如 `notepad.exe`）或直接命令（如 `ping 8.8.8.8`）
2. **编辑**：选中一行后点「编辑」或双击，在弹窗中修改
3. **复制**：选中一项或多项 → 「复制」，得到名称带「(副本)」的新项，可再编辑
4. **启动 / 全部启动**：选中项 → 「启动」；或第二行「全部启动」启动列表中所有未运行项
5. **停止 / 全部停止**：选中项 → 「停止」；或第二行「全部停止」结束列表中所有运行中项
6. **停止软件外所有批处理**：停止系统中所有正在运行的批处理脚本（包括列表中的和列表外的）
7. **重启**：先停止再启动
8. **端口/进程**：选中**正在运行**的项 → 「端口/进程」，弹窗显示监听端口、进程树及资源（CPU%、内存 MB）；下方有「:端口」按钮，点击在浏览器打开 `http://localhost:端口`
9. **访问**：选中运行中且含端口的项 → 「访问」打开首位端口；或右键选「访问 :端口」打开指定端口
10. **分组筛选**：表格上方「筛选分组」下拉框选择分组，只显示该分组的项；「清除筛选」恢复显示全部
11. **关键词搜索与定位**：在「关键词」输入框输入文字，点「定位」或按回车，列表中第一个匹配项（ID/名称/分组/路径/工作目录）会被选中并滚动到可见；点「下一个」可跳到下一个匹配项
12. **IP地址显示**：筛选行右侧显示本地IP，点击可复制；第三行「停止软件外所有批处理」右侧显示外网IP，点击可复制；外网IP启动时自动获取；当筛选行内容较多时，右侧会出现横向滚动条，可拖动查看所有内容
13. **导出/导入配置**：工具栏「导出配置」保存当前配置到 JSON 文件；「导入配置」可选择合并（保留现有+添加新）或替换（清空后全部导入）
14. **执行历史**：工具栏「执行历史」查看脚本的执行历史记录，包括启动/停止时间、运行时长、退出码、统计信息（成功率、平均运行时长）
15. **异常通知**：工具栏「通知配置」设置异常通知规则（退出码非0、快速退出阈值），可配置 Webhook URL（GET/POST）发送通知到外部系统
16. **批量编辑**：选中多个脚本，工具栏「批量编辑」可批量修改分组、解释器、定时任务启用/禁用、保活启用/禁用、日志输出启用/禁用
17. **暂停刷新 / 恢复刷新**：第二行「暂停刷新」停止定时刷新（保活、运行中接管一并暂停）；「刷新状态」仍可手动刷；再点「恢复刷新」恢复；菜单「设置」→「刷新间隔」可改为 2 秒 / 4 秒 / 8 秒
18. **扫描运行中**：点「扫描运行中」→ 弹窗列出**系统当前正在运行**的脚本进程（需 psutil）；可「加入管理器」（选中项）、「全部加入」（本页全部）、或「结束选中」
19. **定时任务**：编辑时启用定时任务，可选择每天、每小时、每分钟、每周、每月，支持启动/重启/停止三种行为
20. **右键菜单**：右键点击表格项，可快速打开文件、打开目录、访问端口、复制信息、分组启停、查看日志等
21. **行排序**：Alt+拖拽行调整顺序；Alt+↑/↓键盘上下移动行；右键菜单「上移/下移」
22. **模板系统**：右键菜单「保存为模板」保存当前配置，「从模板添加」快速创建新脚本，「管理模板」查看删除模板
23. **开机自启**：右键菜单可将管理器设为 Windows 开机自启动（创建启动文件夹快捷方式）
24. **查看日志**：右键菜单「查看日志」查看脚本日志文件，自动显示最后100KB内容
25. **首页**：菜单栏「首页」在浏览器打开项目 GitHub 页面

### 文件说明

| 文件/目录            | 说明                                   |
|----------------------|----------------------------------------|
| `version.py`         | 应用版本号（当前 **1.1.0**，GUI 与 API 共用） |
| `main.py`            | 主程序（GUI + CLI）                    |
| `WindowsBatchScriptManager_api_server.py` | HTTP API 服务（GUI 可独立启停；勿用 restart-all 管 API 任务） |
| `api_config.json`    | API 自动启动等配置（可选，GUI 勾选后生成） |
| `app_config.json`    | GUI 应用设置（如「启动后最小化到任务栏」，可选） |
| `WindowsBatchScriptManager_scripts.json` | 脚本任务列表（自动生成与保存） |
| `requirements.txt`   | 可选依赖 `psutil`；API 需另装 fastapi、uvicorn |
| `logs/`              | 脚本输出日志目录（自动创建）           |
| `linuxBatchScriptManager/` | Linux 平台实现（本仓库内；独立 Linux 项目见 **LinuxBatchScriptManager** 目录） |
| `macBatchScriptManager/`   | macOS 平台实现（本仓库内；独立 Mac 项目见 **MacBatchScriptManager** 目录） |

### 打包为 EXE（Windows）

推荐使用 `build_exe.spec` 打包（自动过滤 win32com runtime hook，避免在 Windows Server 等环境启动崩溃）：

```bash
pip install pyinstaller
pyinstaller build_exe.spec
```

也可双击 `build_exe.bat`。

如使用 SmartPacker 等图形化打包工具，需添加排除模块参数：
```
--exclude-module win32com --exclude-module pythoncom --exclude-module pywintypes --exclude-module win32api --exclude-module win32gui --exclude-module win32con
```

> **注意**：`psutil` 安装时会拉入 `pywin32`，PyInstaller 会自动检测并添加 win32com runtime hook，在部分 Windows Server 上会因 DLL 加载失败而崩溃。排除这些模块不影响功能（程序本身不使用 win32com）。

### 提示

- **脚本启动**：
  - **批处理**：在独立 CMD 窗口运行
  - **Python**：默认在**独立控制台窗口**运行，`print` 与报错会显示在该窗口
  - **Node.js**：在独立控制台窗口运行
  - **PowerShell**：通过 `powershell.exe -ExecutionPolicy Bypass -File` 运行
  - **VBScript/JScript/WSF**：通过 `cscript.exe //NoLogo` 运行
  - **Shell**：通过 `bash` 运行（需安装 Git Bash 或 WSL）
  - **HTTP GET 请求**：启动时自动发送 HTTP GET 请求到指定 URL（支持 http 和 https），状态栏显示响应状态码和响应时间；HTTP 请求类型自动禁用保活（curl 正常退出后无需重启）
  - **进程管理**：启动时通过进程名查找并启动程序（如 `notepad.exe`），停止时结束对应进程；进程不存在不影响添加任务
  - **直接命令**：通过 `cmd.exe /c` 执行输入的命令（如 `ping 8.8.8.8`、`ipconfig /all`），支持管道（`|`）和重定向（`>`）等；支持日志输出与定时任务
  - 若启动后很快退出，约 1.5 秒后状态栏会提示**运行时长、退出码**，便于排查
- **运行时长与退出码**：进程退出时（含「启动后很快退出」、定时刷新发现已停止）会捕获取消码和运行约 X 秒，并在状态栏显示；退出码 0 多为正常，非 0 多为报错
- **记录输出到 logs/**：编辑脚本时可勾选「启动时记录输出到 logs/」，stdout/stderr 会写入 `logs/名称_时间戳.log`，**启动后很快退出**时自动弹窗展示最近输出，或提示「输出已保存到 logs/xxx」
- **解释器设置**：
  - **Python**：留空=当前 `sys.executable`；可填 `py -3.11`、`py -3.10`、`D:\conda\envs\xx\python.exe` 等
  - **Node.js**：留空=`node`；可填 `node`、`node.exe` 或完整路径
  - **Shell**：留空=`bash`；可填 `bash`、`sh` 或完整路径（如 `C:\Program Files\Git\bin\bash.exe`）
- **保活**：勾选后，每约 4 秒检查一次；若进程已退出则自动重新启动（无法区分正常退出与崩溃）；**暂停刷新**时保活也会暂停；**崩溃循环检测**：1分钟内重启超过5次自动停止保活，避免无限崩溃
- **扫描运行中 · 全部加入**：会跳过「脚本路径已在管理器中」的项，避免重复；提示中会显示加入数、跳过数；支持识别所有脚本类型（批处理、Python、Node.js、PowerShell 等）
- **运行中接管**：若脚本已在列表中，且从管理器外启动（双击脚本、在 CMD 运行等），管理器在定时刷新或点「刷新状态」时会自动按路径接管，状态变为「运行中」并显示 PID；依赖 `psutil`；支持所有脚本类型
- **分组/标签**：编辑时填写「分组/标签」字段，可用于按项目、类型等分类；筛选时选择分组即可只显示该分组项
- **关键词搜索与定位**：在筛选行输入关键词后点「定位」或按回车，会在当前可见列表中按 ID、名称、分组、路径、工作目录匹配并选中、滚动到该项；「下一个」从当前选中项之后继续查找
- **IP地址显示**：
  - 筛选行右侧显示本地IP，点击可复制；第三行「停止软件外所有批处理」右侧显示外网IP，点击可复制
  - 本地 IP 与外网 IP 均在主循环启动后异步加载，不阻塞启动；外网 IP 会先判断是否联网，未联网则直接显示「无法获取」
  - 外网IP获取失败时显示「无法获取IP地址」
  - 筛选行内容较多时，右侧自动显示横向滚动条，可拖动查看所有内容
- **配置保存**：保存时先写临时文件再替换原文件，避免写入中断损坏配置；保存失败时状态栏会提示
- **启动后最小化**：菜单「设置」可勾选「启动后最小化到任务栏」（写入 `app_config.json`）；命令行 `python main.py --minimized` 仅当次生效。已启用「开机启动」时，勾选最小化会自动更新自启命令。无系统托盘，最小化后请从任务栏恢复；纯后台可改用 CLI/API
- **停止与状态**：对已停止的项点「停止」时，状态栏会提示「所选项已处于停止状态」；路径不存在的项在状态列显示「路径无效」
- **导出/导入配置**：导出会保存所有配置（不含运行时状态如 PID）；导入时「合并」会跳过已存在的路径，「替换」会清空现有后全部导入
- **定时任务**：
  - 支持每天、每小时、每分钟、每周、每月、每N小时、每季度、每年、单次执行、工作日共十种类型
  - 支持启动、重启、停止三种行为
  - 定时检查频率为每 10 秒一次
  - 对于停止行为，时间到达后持续有效直到执行完成
  - 单次执行类型执行后自动禁用
  - CLI 和 API 均支持完整的定时任务参数配置
- **执行历史记录**：
  - 自动记录每个脚本的每次执行（启动时间、停止时间、运行时长、退出码、状态）
  - 每个脚本最多保留最近 100 条历史记录
  - 历史记录保存在 `execution_history.json`
  - 支持查看统计信息（总计次数、成功次数、失败次数、平均运行时长）
- **异常通知**：
  - 可配置通知条件：退出码非0、快速退出（默认<5秒，可配置阈值）
  - 支持 Webhook 通知：GET 或 POST 请求，数据包含脚本名称、异常原因、退出码、运行时长、时间戳
  - 通知配置保存在 `notification_config.json`
- **批量编辑**：
  - 支持批量修改分组、解释器
  - 支持批量启用/禁用定时任务、保活、日志输出
  - 只修改勾选的选项，留空字段不修改
- 端口与进程详情、资源监控需要 `psutil`；在部分环境下 `psutil.net_connections()` 可能需管理员权限才能看到全部连接
- **性能优化**：端口查询结果缓存30秒，避免每4秒刷新时重复调用 `psutil.net_connections()`；表格采用增量更新，只更新值变化的行；分组筛选选项仅在行数变化时重建
- **配置热更新**：通过 CLI 或 API 修改配置后，GUI 自动检测 `WindowsBatchScriptManager_scripts.json` 变化并重新加载，无需重启管理器
- **拖放添加**：将脚本文件拖入窗口即可添加为新任务；支持批量拖入；拖放始终新增任务，不会替换已有任务

---

<a name="english"></a>
## English Version

**WindowsBatchScriptManager** is a lightweight script manager (**v1.1.0**) that runs on **Windows / macOS / Linux**. It centrally manages batch, Python, Node.js, PowerShell and other script types, with execution history, exception notifications, and scheduled tasks. It supports **CLI** and **HTTP API** for automation and remote control.

### Use Cases

| Scenario | Description |
|----------|-------------|
| Personal script hub | Centralize backup/sync/local services; optional launch-on-app-start and keep-alive |
| Local dev | Start/stop many services at once; groups, work dir, and launch args |
| Scheduled maintenance | Daily/hourly start, restart, stop without Windows Task Scheduler |
| LAN remote control | Optional `WindowsBatchScriptManager_api_server.py` over HTTP (Python + auth) |
| Adopt running processes | Scan running scripts or match by path on refresh |

### Platform Notes

| Feature | Windows | macOS / Linux |
|---------|---------|----------------|
| Run | `python main.py` or **exe** | `python3 main.py` at **repo root** |
| Script types | bat/cmd, PowerShell, py, node, … | Shell, Python, Node primarily |
| Config file | `WindowsBatchScriptManager_scripts.json` (default: next to exe/script) | Same filename; path may use XDG/App Support when frozen |
| Login auto-start | Registry Run | launchd (Mac) / systemd user (Linux) |
| Shutdown/reboot tasks & toolbar | Yes | No |
| Stop all batch outside manager | Yes | No |
| HTTP API | Yes (needs Python) | Yes |
| Relative paths (empty work dir) | Resolved against config directory | Same |

Subfolders `linuxBatchScriptManager/` / `macBatchScriptManager/` ship **platform_impl** (and optional standalone copies). Day-to-day cross-platform use: root `main.py` only.

### Features

- **Unified Management**: Add, edit, delete scripts; supports multiple script types:
  - **Batch**: `.bat`, `.cmd`
  - **Python**: `.py`
  - **Node.js**: `.js`, `.mjs`, `.cjs`, `.ts`, `.jsx`, `.tsx`
  - **PowerShell**: `.ps1`
  - **VBScript**: `.vbs`
  - **JScript**: `.js`, `.jse` (preferentially identified as Node.js)
  - **WSF**: `.wsf`
  - **Shell**: `.sh` (Git Bash/WSL)
  - **HTTP GET**: `http://` or `https://` URL (auto-sends HTTP request)
  - **Process Management**: Process name (e.g., `notepad.exe`), directly start/stop programs
  - **Direct Command**: Enter command line directly (e.g., `ping 8.8.8.8`, `ipconfig`, `python -c "print('hello')"`), supports pipes, redirection, etc.; supports scheduled tasks and log output
  - **System Shutdown / Reboot**: Task types with no script path; manual Start or scheduled Start runs graceful Windows shutdown/reboot
- **Start / Stop / Restart Task**: One-click operations on selected items; stopping will terminate the process tree (including child processes)
- **System shutdown/reboot**: GUI buttons or task types; remote API `GET/POST /shutdown`, `/reboot`
- **Standalone API panel**: Toolbar `API: Start/Stop/Restart API` for `WindowsBatchScriptManager_api_server.py`; optional auto-start with the manager; whitelist keeps API alive during `/shutdown`, `/reboot`, `/restart-all`
- **Path & Working Directory**: Each record can set script path and working directory (relative paths resolve against config file directory if work dir is empty); saved to `WindowsBatchScriptManager_scripts.json`, automatically loaded on next launch
- **Status & PID**: Table displays running status and main process PID; refresh interval can be set via menu "Settings" → "Refresh interval" (2/4/8 sec), can "Pause Refresh" / "Resume Refresh"; items with invalid path show "Path invalid" in status column
- **Running Process Adoption**: Scripts already in the list but started externally (e.g., CMD, File Explorer), including .exe, will be automatically matched by path on refresh and shown as "Running", no need to click "Start" again; requires `psutil`
- **Port Detection**: Automatically displays listening ports of the process tree (depends on `psutil`, falls back to `netstat -ano`)
- **Page Title**: The "Title" column automatically fetches webpage titles of web services (asynchronous background fetching, non-blocking UI)
- **Click to Visit**: Toolbar "Visit" button or right-click "Visit :port", or ":port" buttons in popup, opens `http://localhost:port` in browser
- **Process View & Resource Monitoring**: Select a running item, click "Ports/Processes" to view main process and child processes' PID, name, path, CPU%, memory MB (requires `psutil`)
- **Scan Running**: Identifies **currently running** script processes in the system (parses script paths from command line), can "Add to Manager" or "Add All" to manage, or "End Selected"
- **Start All / Stop All**: One-click start or stop all items in the list
- **Stop All Batch Scripts Outside Manager**: Stops all running batch scripts in the system (including those in the list and outside)
- **Duplicate**: Clone selected items (name appended with "(副本)"), can be edited for variants; right-click "Copy" submenu to copy name/path/PID
- **Specify Interpreter**:
  - **Python**: Can fill `py -3.11`, interpreter path, etc., leave empty to use current Python
  - **Node.js**: Can fill `node`, `node.exe` or path, leave empty to use `node`
  - **Shell**: Can fill `bash`, `sh` or path, leave empty to use `bash`
- **Auto Restart (Keep-Alive)**: Check "Auto Restart on Crash" when editing, automatically restarts every few seconds when process exits abnormally (set in edit dialog, not shown in table); **Crash Loop Detection**: auto-disables keep-alive after 5+ restarts per minute
- **Runtime & Exit Code**: Captures exit code and runtime (about X seconds) when process exits, displayed in status bar; same prompt about 1.5 seconds after **quick exit after start**
- **Log Output to logs/**: Python, Node.js, PowerShell can check "Log output to logs/ on start", stdout/stderr written to `logs/`; if saved, path is shown on exit, **quick exit after start** can popup to show recent output
- **Grouping/Tags**: Can set "Group/Tag" when editing (e.g., Project A, Monitor, API, etc.), table displays group column; can "Filter by Group" above table for quick filtering
- **Keyword Search & Locate**: Enter a keyword in the filter row, click "Locate" (or press Enter) to jump to the first matching row (ID/name/group/path/work dir); "Next" finds the next match
- **IP Address Display**: Filter row shows local IP (click to copy); third row shows public IP to the right of "Stop All Batch Scripts Outside Manager" (click to copy); local and public IP are both loaded asynchronously after main loop starts (non-blocking startup); public IP checks network reachability first and shows "Unable to get IP" when offline; filter row supports horizontal scrolling when content is too wide
- **Home**: Menu "Home" opens the project GitHub page (https://github.com/shellsec/WindowsBatchScriptManager) in browser
- **Export/Import Config**: Toolbar "Export Config" backs up to file, "Import Config" supports merge or replace, convenient for migration and backup
- **Scheduled Tasks**: Supports daily, hourly, minutely, weekly, monthly, every N hours, quarterly, yearly, once, weekday scheduled start/restart/stop
- **Execution History**: Automatically records start/stop times, runtime, exit codes for each script; view statistics (success rate, average runtime); access via "Execution History" button
- **Exception Notifications**: Automatic popup alerts on script exceptions; supports Webhook notifications (GET/POST), configurable conditions (non-zero exit code, quick exit); configure via "Notification Config" button
- **Batch Edit**: Select multiple scripts, use "Batch Edit" button to modify group, interpreter, launch arguments, scheduled tasks, keep-alive, log output in bulk
- **HTTP Request Tasks**: Support adding `http://` or `https://` URL as HTTP GET request tasks; automatically sends request on start and shows response status code; auto-disable keep-alive for HTTP request type (curl exits normally, no need to restart)
- **Drag & Drop**: Drag script files into the window to add as new tasks; supports batch dropping; always adds new tasks without replacing existing ones
- **Process Management Tasks**: Support adding process name (e.g., `notepad.exe`) as process management task; can directly start/stop specified programs; process not existing does not affect adding
- **Direct Command Tasks**: Support entering command line directly (e.g., `ping 8.8.8.8`, `ipconfig /all`, `python -c "print('hello')"`), executed via `cmd.exe /c`, supports pipes, redirection and other Windows command-line features; supports keep-alive, scheduled tasks, and log output; auto-detection: commands with spaces and no file extension on the first word are automatically identified as command type
- **Add/Edit Dialog**: Unified form for name, path, working directory, group, interpreter (Python/Node.js/Shell), keep-alive, log output (Python/Node.js/PowerShell/Command), scheduled tasks; path and working directory can be browsed; automatically shows/hides relevant options based on script type; type dropdown supports Script / HTTP Request / Process Management / Direct Command
- **Main Panel Add (Manual) Button**: Toolbar "Add (Manual)" button opens add dialog for manual URL, process name, or command input without file picker
- **Right-Click Menu**: Supports quick operations like open file, open directory, visit port, copy info, group start/stop, view log, etc.
- **Row Reordering**: Alt+drag to reorder rows; Alt+↑/↓ keyboard shortcuts to move rows up/down; right-click "Move Up/Move Down"
- **Template System**: Save script config as template, add new scripts from template, manage existing templates
- **Auto Start on Boot**: Right-click menu to set the manager as Windows startup item
- **View Log**: Right-click "View Log" to view script log file (whether running or not), auto-displays last 100KB
- **Button Shortcut Tooltips**: Hover toolbar buttons to see keyboard shortcuts (e.g., Ctrl+N, F5, etc.)

### How to Run

1. Install dependencies (recommended, for port and process details):
   ```bash
   pip install -r requirements.txt
   ```
   Can run without it, port detection will use `netstat`, process details will be simplified.

2. Start the program:
   ```bash
   python main.py
   ```
   To stay out of the way on boot: **Settings → Minimize to taskbar on start**, or `python main.py --minimized` (also appended to login auto-start when enabled). Closing the window still exits the app (no system tray).
   **macOS / Linux**: `python3 main.py` at the repository root.

### macOS / Linux quick check

```bash
pip install -r requirements.txt
python3 main.py --config-path
cat > test.sh <<'EOF'
#!/bin/bash
echo hello; sleep 60
EOF
chmod +x test.sh
python3 main.py --add --path "./test.sh" --name "test" --script-type shell
python3 main.py --start 1 && python3 main.py --status 1 --json && python3 main.py --stop 1
```

Optional API: `pip install fastapi uvicorn` then `python3 WindowsBatchScriptManager_api_server.py --host 127.0.0.1 --port 8765` and `curl http://127.0.0.1:8765/list`.

### Command Line (CLI)

Running without arguments starts the GUI; with arguments it runs in CLI mode and exits after the command (for scripts and automation). **When built as exe on Windows**, same support: `WindowsBatchScriptManager.exe --list --json`.

| Argument | Description |
|----------|-------------|
| `--config-path` | Print script list config path (`WindowsBatchScriptManager_scripts.json`) |
| `--list` / `-l` [--json] | List all scripts (id, name, path, running, pid) |
| `--status` [ID or name] [--json] | Status of one or all |
| `--start` ID or name | Start |
| `--stop` ID or name | Stop |
| `--restart` ID or name | Restart |
| `--start-all` | Start all |
| `--stop-all` | Stop all |
| `--restart-all` | Restart all (stop then start) |
| `--shutdown` | Stop managed scripts then shut down Windows (optional `--force`) |
| `--reboot` | Stop managed scripts then reboot Windows (optional `--force`) |
| `--add` | Add script (requires `--path`; optional `--name`, `--work-dir`, `--group`, `--script-type`, `--interpreter`, `--args`, `--env-vars`, `--auto-restart`, `--log-output`, schedule params, etc.) |
| `--update` ID or name | Update one (same fields as add; `--no-auto-restart`, `--no-log-output`) |
| `--delete` ID or name | Delete one |
| `--export` [file path] | Export config (omit path for stdout) |
| `--import` file path [--replace] | Import config (merge by default; --replace to overwrite) |

ID can be numeric (e.g. `2`); name supports substring match (e.g. `trae` matches "trae-changelog"). Use `--json` for machine-readable output.

Example:
```bash
python main.py --list --json
python main.py --start 1
python main.py --add --path "/path/to/script.py" --name "My Script" --script-type python
```

### HTTP API (for remote access)

`WindowsBatchScriptManager_api_server.py` exposes HTTP endpoints that call the main CLI via subprocess, for remote start/stop, add, delete, export/import.

**Note:** Use the GUI **API: Start API** panel to manage the API server separately. If listed as a task, `/shutdown`, `/reboot`, and `/restart-all` skip the API via whitelist; still prefer running API standalone and avoid `/restart-all` on API tasks.

1. Install API dependencies:
   ```bash
   pip install fastapi "uvicorn[standard]"
   ```
2. Start the API server:
   ```bash
   python WindowsBatchScriptManager_api_server.py --port 8765
   ```
   - Allow access from other machines: `--host 0.0.0.0`
   - Custom API key: `--api-key your-secret` or env `API_KEY`
3. Requests must include header: `X-API-Key: <key>` (default `change-me-in-production`; change in production). With `--no-auth`, no X-API-Key is required for GET/POST.

Optional **GET `/exec?cmd=...`** (disabled by default): uncomment `# ENABLE_GET_CMD_EXEC = True` in the API script (remove leading `#`), restart the server. High risk — use strong API key, never expose with `--no-auth` on untrusted networks.

| Method | Path | Description |
|--------|------|-------------|
| GET | /list | List scripts |
| GET | /status?id=2 | Status |
| POST/GET | /start?id=2 | Start |
| POST/GET | /stop?id=2 | Stop |
| POST/GET | /restart?id=2 | Restart |
| POST/GET | /update?id=2 | Update (optional name, path, work_dir, group, script_type, interpreter, **args**, **env_vars**, auto_restart, log_output, schedule_*, depends_on, …) |
| POST | /add?path=... | Add (path required; optional args, env_vars, schedule_*, …) |
| POST/GET | /start-all, /stop-all, /restart-all | Bulk control (GET supported) |
| POST/GET | /shutdown, /reboot | Windows only: stop managed scripts then power action (`?force=true`) |
| GET | /config-path | Config file path |
| DELETE | /delete?id=2 | Delete |
| GET | /export | Export JSON |
| POST | /import | Import body `{"items": [...]}` |
| GET | /exec?cmd=... | Run cmd via `cmd.exe /c` (Windows; **off by default**) |

Root `/` returns `{"status":"ok","version":"1.1.0"}` when HTML docs are disabled (`--docs` enables `/` docs page).

Example (from another machine):
```bash
curl -H "X-API-Key: change-me-in-production" "http://192.168.1.10:8765/list"
curl -X POST -H "X-API-Key: change-me-in-production" "http://192.168.1.10:8765/start?id=1"
```

CLI and API work on **Windows / macOS / Linux**; without an exe, the API uses `python(3) main.py` to run commands.

### Usage

1. **Add**: Select "Add" → Choose script file (supports batch, Python, Node.js, PowerShell, VBScript, WSF, Shell, etc.) → Fill in name, working directory, group/tag, interpreter (Python/Node.js/Shell need to fill), keep-alive, scheduled tasks in dialog, can click "Browse" to select path and directory; or use toolbar "Add (Manual)" button to open dialog for manual URL (`http://` or `https://`), process name (e.g., `notepad.exe`), or direct command (e.g., `ping 8.8.8.8`) input
2. **Edit**: Select a row then click "Edit" or double-click, modify in dialog
3. **Duplicate**: Select one or more items → "Duplicate", get new items with "(副本)" in name, can edit again
4. **Start / Start All**: Selected items → "Start"; or "Start All" button in second row to start all non-running items in list
5. **Stop / Stop All**: Selected items → "Stop"; or "Stop All" button in second row to stop all running items in list
6. **Stop All Batch Scripts Outside Manager**: Stops all running batch scripts in the system (including those in the list and outside)
7. **Restart**: Stop then start
8. **Ports/Processes**: Select a **running** item → "Ports/Processes", popup shows listening ports, process tree and resources (CPU%, memory MB); ":port" buttons below, click to open `http://localhost:port` in browser
9. **Visit**: Select a running item with ports → "Visit" opens the first port; or right-click "Visit :port" to open specified port
10. **Group Filter**: "Filter by Group" dropdown above table selects group, only shows items in that group; "Clear Filter" restores showing all
11. **Keyword Search & Locate**: Enter text in "Keyword" box, click "Locate" or press Enter to select and scroll to the first matching row (ID/name/group/path/work dir); "Next" jumps to the next match
12. **IP Address Display**: Filter row shows local IP (click to copy); third row shows public IP to the right of "Stop All Batch Scripts Outside Manager" (click to copy); public IP is automatically fetched on startup; when filter row content is too wide, a horizontal scrollbar appears for scrolling
13. **Export/Import Config**: Toolbar "Export Config" saves current config to JSON file; "Import Config" can choose merge (keep existing + add new) or replace (clear all then import)
14. **Execution History**: Toolbar "Execution History" to view script execution history, including start/stop times, runtime, exit codes, statistics (success rate, average runtime)
15. **Exception Notifications**: Toolbar "Notification Config" to set exception notification rules (non-zero exit code, quick exit threshold), can configure Webhook URL (GET/POST) to send notifications to external systems
16. **Batch Edit**: Select multiple scripts, use "Batch Edit" button to modify group, interpreter, enable/disable scheduled tasks, keep-alive, log output in bulk
17. **Pause Refresh / Resume Refresh**: Second row "Pause Refresh" stops auto-refresh (keep-alive, running process adoption also paused); "Refresh Status" can still manually refresh; click "Resume Refresh" to resume; menu "Settings" → "Refresh interval" can set 2 / 4 / 8 sec
18. **Scan Running**: Click "Scan Running" → Popup lists **currently running** script processes in system (requires psutil); can "Add to Manager" (selected items), "Add All" (all on page), or "End Selected"
19. **Scheduled Tasks**: Enable scheduled tasks when editing, can choose daily, hourly, minutely, weekly, monthly, supports start/restart/stop three behaviors
20. **Right-Click Menu**: Right-click table items for quick operations like open file, open directory, visit port, copy info, group start/stop, view log, etc.
21. **Row Reordering**: Alt+drag to reorder rows; Alt+↑/↓ keyboard shortcuts; right-click "Move Up/Move Down"
22. **Template System**: Right-click "Save as Template" to save config, "Add from Template" to quickly create, "Manage Templates" to view/delete
23. **Auto Start on Boot**: Right-click menu to add manager to Windows startup folder
24. **View Log**: Right-click "View Log" to view script log, auto-displays last 100KB
25. **Home**: Menu "Home" opens the project GitHub page in browser

### Files

| File/Directory      | Description                              |
|---------------------|------------------------------------------|
| `version.py`        | App version (**1.1.0**, shared by GUI & API) |
| `main.py`           | Main program (GUI + CLI)                 |
| `WindowsBatchScriptManager_api_server.py` | HTTP API server (GUI panel can start/stop; avoid restart-all on API task) |
| `api_config.json`   | API auto-start settings (optional)       |
| `app_config.json`   | GUI app settings (e.g. minimize on start, optional) |
| `WindowsBatchScriptManager_scripts.json` | Script task list (auto-generated and saved) |
| `requirements.txt`  | Optional dependency `psutil`; API also needs fastapi, uvicorn |
| `logs/`             | Script output log directory (auto-created) |
| `linuxBatchScriptManager/` | Linux platform impl (in this repo; standalone Linux project in **LinuxBatchScriptManager** directory) |
| `macBatchScriptManager/`   | macOS platform impl (in this repo; standalone Mac project in **MacBatchScriptManager** directory) |

### Building EXE (Windows)

Recommended: use `build_exe.spec` (automatically filters win32com runtime hooks to prevent startup crashes on Windows Server etc.):

```bash
pip install pyinstaller
pyinstaller build_exe.spec
```

Or double-click `build_exe.bat`.

When using SmartPacker or similar GUI tools, add exclude module arguments:
```
--exclude-module win32com --exclude-module pythoncom --exclude-module pywintypes --exclude-module win32api --exclude-module win32gui --exclude-module win32con
```

> **Note**: `psutil` installation pulls in `pywin32`, and PyInstaller auto-detects and adds win32com runtime hooks, which may crash on some Windows Server due to DLL load failure. Excluding these modules doesn't affect functionality (the program doesn't use win32com).

### Tips

- **Script Startup**:
  - **Batch**: Runs in independent CMD window
  - **Python**: By default runs in **independent console window**, `print` and errors displayed in that window
  - **Node.js**: Runs in independent console window
  - **PowerShell**: Runs via `powershell.exe -ExecutionPolicy Bypass -File`
  - **VBScript/JScript/WSF**: Runs via `cscript.exe //NoLogo`
  - **Shell**: Runs via `bash` (requires Git Bash or WSL installation)
  - **HTTP GET Request**: Auto-sends HTTP GET request to specified URL on start (supports http and https); status bar shows response status code and response time; auto-disable keep-alive for HTTP request type (curl exits normally, no need to restart)
  - **Process Management**: Finds and starts program by process name on start (e.g., `notepad.exe`), stops corresponding process on stop; process not existing does not affect adding task
  - **Direct Command**: Executes entered command via `cmd.exe /c` (e.g., `ping 8.8.8.8`, `ipconfig /all`), supports pipes (`|`) and redirection (`>`); supports log output and scheduled tasks
  - If exits quickly after start, status bar will show **runtime and exit code** after about 1.5 seconds for troubleshooting
- **Runtime & Exit Code**: When process exits (including "quick exit after start", auto-refresh detects stopped), captures exit code and runtime (about X seconds), displayed in status bar; exit code 0 is usually normal, non-zero is usually error
- **Log Output to logs/**: When editing scripts, can check "Log output to logs/ on start", stdout/stderr written to `logs/name_timestamp.log`, **quick exit after start** automatically pops up to show recent output, or prompts "Output saved to logs/xxx"
- **Interpreter Settings**:
  - **Python**: Empty = current `sys.executable`; can fill `py -3.11`, `py -3.10`, `D:\conda\envs\xx\python.exe`, etc.
  - **Node.js**: Empty = `node`; can fill `node`, `node.exe` or full path
  - **Shell**: Empty = `bash`; can fill `bash`, `sh` or full path (e.g., `C:\Program Files\Git\bin\bash.exe`)
- **Keep-Alive**: After checking, checks every about 4 seconds; if process exits, automatically restarts (cannot distinguish normal exit from crash); **Pause Refresh** also pauses keep-alive; **Crash Loop Detection**: auto-disables keep-alive after 5+ restarts per minute
- **Scan Running · Add All**: Skips items with "script path already in manager" to avoid duplicates; prompt shows added count and skipped count; supports identifying all script types (batch, Python, Node.js, PowerShell, etc.)
- **Running Process Adoption**: If script is already in list, and started outside manager (double-click script, run in CMD, etc.), manager will automatically adopt by path on auto-refresh or click "Refresh Status", status becomes "Running" and shows PID; depends on `psutil`; supports all script types
- **Grouping/Tags**: Fill "Group/Tag" field when editing, can be used for categorization by project, type, etc.; select group when filtering to only show items in that group
- **Keyword Search & Locate**: Enter keyword in filter row, click "Locate" or press Enter to match by ID, name, group, path, work dir and select/scroll to that row; "Next" continues from the current selection
- **IP Address Display**:
  - Filter row shows local IP (click to copy); third row shows public IP to the right of "Stop All Batch Scripts Outside Manager" (click to copy)
  - Local and public IP are both loaded asynchronously after main loop starts (non-blocking startup); public IP checks network reachability first and shows "Unable to get IP" when offline
  - Shows "Unable to get IP address" if public IP fetch fails
  - When filter row content is too wide, a horizontal scrollbar automatically appears on the right for scrolling
- **Config Save**: Saves to a temp file then replaces the original to avoid corruption on interrupt; status bar shows an error if save fails
- **Minimize on start**: **Settings** menu → **Minimize to taskbar on start** (saved in `app_config.json`); `python main.py --minimized` applies once per launch. If login auto-start is enabled, toggling minimize updates the startup command. No tray icon—restore from the taskbar; use CLI/API for headless operation
- **Stop & Status**: Clicking "Stop" on already-stopped items shows "Selected item(s) already stopped" in status bar; items with invalid path show "Path invalid" in status column
- **Export/Import Config**: Export saves all configurations (excluding runtime state like PID); when importing, "Merge" skips existing paths, "Replace" clears all then imports
- **Scheduled Tasks**:
  - Supports ten types: daily, hourly, minutely, weekly, monthly, every N hours, quarterly, yearly, once, weekday
  - Supports three behaviors: start, restart, stop
  - Check frequency is every 10 seconds
  - For stop behavior, remains effective after time arrives until execution completes
  - "Once" type auto-disables after execution
  - CLI and API both support full schedule parameter configuration
- **Execution History**:
  - Automatically records each script execution (start time, stop time, runtime, exit code, status)
  - Each script keeps up to 100 most recent records
  - History saved to `execution_history.json`
  - Supports viewing statistics (total count, success count, failure count, average runtime)
- **Exception Notifications**:
  - Configurable notification conditions: non-zero exit code, quick exit (default <5 seconds, configurable threshold)
  - Supports Webhook notifications: GET or POST requests, data includes script name, exception reason, exit code, runtime, timestamp
  - Notification config saved to `notification_config.json`
- **Batch Edit**:
  - Supports batch modification of group, interpreter
  - Supports batch enable/disable scheduled tasks, keep-alive, log output
  - Only modifies selected options, empty fields are not modified
- Port and process details, resource monitoring require `psutil`; in some environments `psutil.net_connections()` may require administrator privileges to see all connections
- **Performance Optimization**: Port query results cached for 30 seconds to avoid repeated `psutil.net_connections()` calls every 4 seconds; table uses incremental updates, only updating changed rows; group filter options only rebuild when row count changes
- **Config Hot Reload**: After modifying config via CLI or API, GUI automatically detects `WindowsBatchScriptManager_scripts.json` changes and reloads without restarting the manager
- **Drag & Drop**: Drag script files into window to add as new tasks; supports batch drop; always adds new tasks without replacing existing ones
