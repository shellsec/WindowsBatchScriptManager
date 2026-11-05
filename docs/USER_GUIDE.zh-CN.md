# 使用手册

[返回项目首页](../README.zh-CN.md)

## 使用场景

| 场景 | 说明 |
|------|------|
| 个人电脑脚本收纳 | 备份、同步、本地服务等 bat/py 统一列表启停，可选「启动本工具时自动启动」、保活 |
| 开发联调 | 多服务一键全部启动/停止，分组筛选，工作目录 + 启动参数区分环境 |
| 定时运维 | 每天/每小时等定时启动、重启、停止，无需单独配 Windows 计划任务 |
| 内网远程控制 | 可选 `WindowsBatchScriptManager_api_server.py`，HTTP / MCP 启停任务（需 Python + 鉴权）；浏览器打开 `http://当前IP:8765/` 查看接入说明 |
| 接管已在跑的进程 | 扫描运行中脚本或按路径自动匹配为「运行中」 |
| HTTP 任务调本机 API | 本工具 `/start`、`/stop` 等是 POST；HTTP 任务可选 GET/POST 并填 `X-API-Key`，避免只会 GET 时点不了自己的接口 |
| 少抄 IP 和密钥 | 局域网换机器不必每次手填地址和 Key：点「一键加载API调用」，或添加 HTTP 时点「填入本机API」 |
| 找 API 脚本 / exe | 「打开工具目录」打开 exe / `main.py` 所在文件夹，可找到 `WindowsBatchScriptManager_api_server.py` |
| 远程 `/list` 失败（WinError 740） | GUI exe 勾了「以管理员身份运行」时，另一台机器调 `/list` 可能失败；用 Python 运行 API，取消 EXE 的管理员兼容性设置 |
| API 被全部重启杀掉 | 把 API 脚本混进任务列表后，「全部重启」会先停接口，远程全断；应走独立「启动API」，不必加入列表 |

## 平台差异（简要）

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

## 功能

- **统一管理**：添加、编辑、删除脚本；支持多种脚本类型：
  - **批处理**：`.bat`、`.cmd`
  - **Python**：`.py`
  - **Node.js**：`.js`、`.mjs`、`.cjs`、`.ts`、`.jsx`、`.tsx`
  - **PowerShell**：`.ps1`
  - **VBScript**：`.vbs`
  - **JScript**：`.js`、`.jse`（优先识别为 Node.js）
  - **WSF**：`.wsf`
  - **Shell**：`.sh`（Git Bash/WSL）
  - **HTTP 请求**：`http://` 或 `https://` URL（默认 GET；可改 POST，并可填自定义请求头如 `X-API-Key`；旧任务未填方法/请求头时仍按 GET、无请求头）
  - **进程管理**：进程名（如 `notepad.exe`），可直接启停程序
  - **直接命令**：直接输入命令行执行（如 `ping 8.8.8.8`、`ipconfig`、`python -c "print('hello')"`），支持管道、重定向等；支持定时任务与日志输出
  - **系统关机 / 系统重启**：任务类型可选，无需脚本路径；手动「启动」或定时「启动」时执行（先停托管脚本，再正常关机/重启 Windows）
- **启动 / 停止 / 重启任务**：对选中项一键操作；停止时会结束进程树（含子进程）
- **系统关机 / 重启**：GUI 按钮或任务类型；远程 API `GET/POST /shutdown`、`/reboot`
- **API 服务独立管理**：工具栏 `API: 启动API / 停止API / 重启API`，可勾选「启动本工具时自动启动API」，旁有「一键加载API调用」；默认 `WindowsBatchScriptManager_api_server.py`，**不必加入任务列表**（白名单保护，批量停/重启不会先挂 API）。启动 API 只把调用配置写入 `api_config.json`，**不会自动插入任务**；点「一键加载API调用」才会写入「本机API」分组（`GET /list`、`POST /start?id=1` 等，`id=1` 仅为示例）。启动后**不会自动打开浏览器**；局域网访问 `http://当前IP:8765/` 即可看到 API / MCP 接入说明、调用示例和一键复制。
- **路径与工作目录**：每条记录可设置脚本路径和工作目录（支持相对路径，未填工作目录时以配置文件目录为基准）；配置保存到 `WindowsBatchScriptManager_scripts.json`，下次打开自动加载
- **状态与 PID**：表格显示运行状态、主进程 PID；定时刷新间隔可在菜单「设置」→「刷新间隔」选择 2/4/8 秒，可「暂停刷新」/「恢复刷新」；路径不存在的项状态列显示「路径无效」
- **运行中接管**：列表中已有、但从外部（如 CMD、资源管理器）启动的脚本（含 .exe），会在刷新时按路径自动匹配并显示为「运行中」，无需再点「启动」；需 `psutil`
- **端口识别**：自动显示该进程树正在监听的端口（依赖 `psutil`，无则用 `netstat -ano` 降级）
- **页面标题**：表格「标题」列自动获取 Web 服务的页面标题（后台异步获取，不阻塞 UI）
- **点击访问**：工具栏「访问」或右键「访问 :端口」、弹窗内「:端口」按钮，在浏览器打开 `http://localhost:端口`
- **进程查看与资源监控**：选中运行中的项，点「端口/进程」可查看主进程及子进程的 PID、名称、路径、CPU%、内存 MB（需 `psutil`）
- **扫描运行中**：识别**系统当前正在运行**的脚本进程（从命令行解析脚本路径），可「加入管理器」或「全部加入」纳入管理，也可「结束选中」
- **全部启动 / 全部停止**：一键启动或停止列表中所有项
- **停止软件外所有批处理**：停止系统中所有正在运行的批处理脚本（包括列表中的和列表外的）；其右侧「打开工具目录」打开 exe / `main.py` 所在文件夹
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
- **导出/导入配置**：工具栏「导出配置」备份到文件，「导入配置」支持合并或替换；保存配置时自动写 `.bak`，菜单「设置」→「从上次备份恢复配置」可一键回滚
- **定时任务**：支持每天、每小时、每分钟、每周、每月、每N小时、每季度、每年、单次执行、工作日的定时启动/重启/停止；工具栏「定时总览」查看已启用任务与下次执行时间
- **执行历史记录**：自动记录每个脚本的启动/停止时间、运行时长、退出码；支持按日期筛选与导出 CSV；工具栏「执行历史」查看统计（成功率、平均运行时长）
- **启动失败诊断**：状态栏显示具体失败原因（路径不存在、未找到 Python 等）；右键「诊断启动问题 / 复制诊断信息」
- **系统托盘（Windows）**：设置可勾选「关闭窗口时最小化到托盘」（ctypes，无 Pillow）；托盘右键：显示主窗口 / 全部停止 / 退出
- **异常通知**：脚本异常退出时自动弹窗提醒；支持 Webhook 通知（GET/POST，失败自动重试）；可配置通知条件（退出码非0、快速退出等）；工具栏「通知配置」设置
- **批量编辑**：选中多个脚本，工具栏「批量编辑」可批量修改分组、解释器、启动参数、定时任务、保活、日志输出等
- **拖放添加**：将脚本文件拖入窗口即可添加为新任务；支持批量拖入
- **HTTP 请求任务**：支持添加 `http://` 或 `https://` URL；默认为 GET，可在添加/编辑时选择 POST 并填写请求头（如 `X-API-Key: ...`，便于调用本工具 API）；旧任务未填方法/请求头时仍按 GET、无请求头执行；启动时发送请求并获取响应状态码；HTTP 类型自动禁用保活（curl 正常退出后无需重启）；添加/编辑弹窗可点「填入本机API」
- **进程管理任务**：支持添加进程名（如 `notepad.exe`）作为进程管理任务，可直接启停指定程序，进程不存在也不影响添加
- **直接命令任务**：支持直接输入命令行（如 `ping 8.8.8.8`、`ipconfig /all`、`python -c "print('hello')"`），通过 `cmd.exe /c` 执行，支持管道、重定向等 Windows 命令行特性；支持保活、定时任务、日志输出；自动识别：输入含空格且第一个词无文件扩展名的命令时自动切换为命令类型
- **添加/编辑弹窗**：统一表单填名称、路径、工作目录、分组、解释器（Python/Node.js/Shell）、保活、记录输出（Python/Node.js/PowerShell/命令）、定时任务；路径与工作目录可「浏览」选择；根据脚本类型自动显示/隐藏相应选项；类型下拉框可选择脚本/HTTP请求/进程管理/直接命令/系统关机/系统重启；HTTP 时可点「填入本机API」
- **主面板添加(手动)按钮**：工具栏「添加(手动)」按钮可快速打开添加弹窗（手动输入 URL、进程名或命令），无需选择本地文件
- **右键菜单**：支持打开文件、打开目录、访问端口、复制信息、分组启停、查看日志、启动诊断等快捷操作
- **行排序**：Alt+拖拽行可调整顺序；Alt+↑/↓键盘快捷键上下移动行；右键菜单「上移/下移」
- **模板系统**：保存脚本配置为模板，从模板快速添加新脚本，管理已有模板
- **开机自启**：右键菜单可将管理器设为 Windows 开机自启动
- **查看日志**：右键菜单查看脚本日志（无论是否运行中），自动显示最后100KB内容
- **按钮快捷键提示**：鼠标悬停工具栏按钮时显示对应快捷键（如 Ctrl+N、F5 等）

## 运行方式

1. 安装依赖（推荐，用于端口与进程详情）：
   ```bash
   pip install -r requirements.txt
   ```
   不装也可运行，端口识别会改用 `netstat`，进程详情会简化。

2. 启动程序：
   ```bash
   python main.py
   ```
   开机自启且不想挡桌面：**设置 → 启动后最小化到任务栏**；或 `python main.py --minimized`（也可写入开机自启命令）。
   Windows 常驻：**设置 → 关闭窗口时最小化到托盘**（也可菜单「最小化到托盘」）；托盘右键可显示窗口 / 全部停止 / 退出。
   **macOS / Linux**：在项目根目录执行 `python3 main.py`（不要进子目录里的独立副本，除非维护单独仓库）；添加脚本时默认弱化 `.bat/.cmd/.vbs/.exe` 等 Windows 专用类型。

## macOS / Linux 快速验证

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
# 默认开启鉴权；请改 API_KEY 或传 --api-key
curl -H "X-API-Key: change-me-in-production" "http://127.0.0.1:8765/list"
```

## 命令行（CLI）

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

## 远程 API 与 MCP

通过 `WindowsBatchScriptManager_api_server.py` 提供 HTTP 接口和 MCP（同一进程、同一端口）。内部通过 subprocess 调用 main 的 CLI，实现远程启停、添加、删除、导出/导入等。

**注意**：API 服务可在 GUI **「API: 启动API / 停止API / 重启API」** 独立管理，并可勾选「启动本工具时自动启动API」，**不必加入任务列表**。若已加入，`/shutdown`、`/reboot`、`/restart-all` 会自动跳过 API（白名单），避免接口先挂；仍建议单独运行，不要用 `/restart-all` 管理 API 任务。GUI 启动 API **不会自动打开页面**，也**不会自动插入任务**，只把调用配置写入 `api_config.json`。需要任务列表时，在 API 按钮旁点 **「一键加载API调用」**（写入「本机API」分组，带 `X-API-Key`；`id=1` 只是示例，请按实际任务改 URL）。添加/编辑 HTTP 任务时可点 **「填入本机API」**。第三行「停止软件外所有批处理」右侧 **「打开工具目录」** 可找到 API 脚本 / exe。

1. 安装 API 依赖：
   ```bash
   pip install fastapi "uvicorn[standard]"
   ```
2. 启动 API 服务：
   ```bash
   python WindowsBatchScriptManager_api_server.py --port 8765
   ```
   - **默认开启鉴权**；仅本机调试可加 `--no-auth`（勿对公网开放）
   - 允许本机以外访问：`--host 0.0.0.0`（务必改掉默认 API Key）
   - 自定义 API Key：`--api-key your-secret` 或环境变量 `API_KEY`
   - 根路径 `/` 默认是接入说明页（`--no-docs` 可改回 JSON）
3. 请求时请求头需带：`X-API-Key: <密钥>`（默认 `change-me-in-production`，生产环境务必修改）。
4. 局域网浏览器打开 `http://<当前IP>:8765/`：页面展示当前 IP、计算机名、API 表、curl/Python/fetch 示例，以及 MCP 一键复制。IP 变了之后打开新地址，页面上的示例会跟着变；Cursor 里已保存的配置不会自己改，可复制「计算机名」配置，或回到页面重新复制。

可选 **GET /exec?cmd=...**（默认关闭）：在 API 脚本内去掉 `# ENABLE_GET_CMD_EXEC = True` 行首注释后启用；高危，禁止与 `--no-auth` 或默认 Key + `0.0.0.0` 组合使用。

可选 **Web Dashboard**（`web_dashboard.py`）：默认绑定 `127.0.0.1`，需 `X-API-Key`（或 `/?key=`）；与 GUI exe 分离，不打进 exe。

**MCP 一键接入**（Cursor / Claude Desktop，默认用当前局域网 IP）：

```json
{
  "mcpServers": {
    "script-manager": {
      "url": "http://192.168.1.10:8765/mcp",
      "headers": { "X-API-Key": "change-me-in-production" }
    }
  }
}
```

工具：`list_scripts`、`script_status`、`start_script`、`stop_script`、`restart_script`、`start_all`、`stop_all`（不含关机 / 重启 / exec）。

MCP 探测示例：
```bash
curl -X POST -H "Content-Type: application/json" -H "X-API-Key: change-me-in-production" ^
  "http://192.168.1.10:8765/mcp" ^
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}"
```

接口示例：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | / | API / MCP 接入说明页（默认） |
| GET | /health | 健康检查 JSON |
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
| POST | /mcp | MCP Streamable HTTP |

示例（另一台机器）：
```bash
curl -H "X-API-Key: change-me-in-production" "http://192.168.1.10:8765/list"
curl -X POST -H "X-API-Key: change-me-in-production" "http://192.168.1.10:8765/start?id=1"
```

案例：

- **局域网拉列表**：另一台机器带 `X-API-Key` 访问 `GET /list`（见上）。默认鉴权开启，不带头会 401。
- **HTTP 任务 POST 启动**：添加 HTTP，方法选 POST，URL 填 `http://本机IP:8765/start?id=1`，请求头一行 `X-API-Key: change-me-in-production`；或点「填入本机API」（空路径会填 `/list`，已有 `/start` 会改成 POST 并补请求头）。
- **一键加载后再改 id**：点「一键加载API调用」，「本机API」分组会写入 `GET /list`、`GET /status?id=1`、`POST /start?id=1`、`POST /stop?id=1`、`POST /restart?id=1`、`POST /start-all`、`POST /stop-all`。把 URL 里的 `id=1` 改成实际任务 ID 再点「启动」。
- **找 API 脚本 / exe**：点「打开工具目录」，同目录有 `WindowsBatchScriptManager_api_server.py`（以及打包后的 exe）。
- **要带头还是不要头**：脚本默认 `DISABLE_AUTH = False`，请求必须带 `X-API-Key`。本机调试可加 `--no-auth`（本次进程不校验请求头）。把脚本里 `DISABLE_AUTH` 改成 `True` 后，「一键加载API调用 / 填入本机API」才不写请求头。勿对公网关鉴权。

CLI 与 API 在 **Windows / macOS / Linux** 下均可使用。常见痛点：GUI exe 勾了「以管理员身份运行」时，局域网调 `/list` 可能 WinError 740；请用 Python 运行 API 脚本，并取消 EXE 的管理员兼容性设置。双文件部署时 API 调用同目录的 EXE。源码部署时，如同目录存在 `main.py`，API 会优先使用源码。

## 使用说明

1. **添加**：选「添加」→ 选择脚本文件（支持批处理、Python、Node.js、PowerShell、VBScript、WSF、Shell 等）→ 在弹窗中填名称、工作目录、分组/标签、解释器（Python/Node.js/Shell 需填写）、是否保活、定时任务，可点「浏览」选路径与目录；也可通过工具栏「添加(手动)」按钮直接打开弹窗手动输入 URL（`http://` 或 `https://`）、进程名（如 `notepad.exe`）或直接命令（如 `ping 8.8.8.8`）
2. **编辑**：选中一行后点「编辑」或双击，在弹窗中修改
3. **复制**：选中一项或多项 → 「复制」，得到名称带「(副本)」的新项，可再编辑
4. **启动 / 全部启动**：选中项 → 「启动」；或第二行「全部启动」启动列表中所有未运行项
5. **停止 / 全部停止**：选中项 → 「停止」；或第二行「全部停止」结束列表中所有运行中项
6. **停止软件外所有批处理**：停止系统中所有正在运行的批处理脚本（包括列表中的和列表外的）；其右侧「打开工具目录」打开 exe / `main.py` 所在文件夹，可找到 `WindowsBatchScriptManager_api_server.py` 与打包后的 exe
7. **重启**：先停止再启动
8. **端口/进程**：选中**正在运行**的项 → 「端口/进程」，弹窗显示监听端口、进程树及资源（CPU%、内存 MB）；下方有「:端口」按钮，点击在浏览器打开 `http://localhost:端口`
9. **访问**：选中运行中且含端口的项 → 「访问」打开首位端口；或右键选「访问 :端口」打开指定端口
10. **分组筛选**：表格上方「筛选分组」下拉框选择分组，只显示该分组的项；「清除筛选」恢复显示全部
11. **关键词搜索与定位**：在「关键词」输入框输入文字，点「定位」或按回车，列表中第一个匹配项（ID/名称/分组/路径/工作目录）会被选中并滚动到可见；点「下一个」可跳到下一个匹配项
12. **IP地址显示**：筛选行右侧显示本地IP，点击可复制；第三行「停止软件外所有批处理」右侧显示外网IP，点击可复制；外网IP启动时自动获取；当筛选行内容较多时，右侧会出现横向滚动条，可拖动查看所有内容
13. **导出/导入配置**：工具栏「导出配置」保存当前配置到 JSON 文件；「导入配置」可选择合并（保留现有+添加新）或替换（清空后全部导入）；菜单「设置」→「从上次备份恢复配置」可从 `.bak` 回滚
14. **执行历史**：工具栏「执行历史」查看记录与统计；可按日期筛选并导出 CSV
15. **定时总览**：工具栏「定时总览」查看已启用定时任务及下次执行时间
16. **异常通知**：工具栏「通知配置」设置异常通知规则（退出码非0、快速退出阈值），可配置 Webhook URL（GET/POST，失败重试）
17. **批量编辑**：选中多个脚本，工具栏「批量编辑」可批量修改分组、解释器、定时任务启用/禁用、保活启用/禁用、日志输出启用/禁用
18. **暂停刷新 / 恢复刷新**：第二行「暂停刷新」停止定时刷新（保活、运行中接管一并暂停）；「刷新状态」仍可手动刷；再点「恢复刷新」恢复；菜单「设置」→「刷新间隔」可改为 2 秒 / 4 秒 / 8 秒
19. **扫描运行中**：点「扫描运行中」→ 弹窗列出**系统当前正在运行**的脚本进程（需 psutil）；可「加入管理器」（选中项）、「全部加入」（本页全部）、或「结束选中」
20. **定时任务**：编辑时启用定时任务，可选择每天、每小时、每分钟、每周、每月等，支持启动/重启/停止三种行为
21. **右键菜单**：右键点击表格项，可快速打开文件、打开目录、访问端口、复制信息、分组启停、查看日志、诊断启动问题等
22. **行排序**：Alt+拖拽行调整顺序；Alt+↑/↓键盘上下移动行；右键菜单「上移/下移」
23. **模板系统**：右键菜单「保存为模板」保存当前配置，「从模板添加」快速创建新脚本，「管理模板」查看删除模板
24. **开机自启 / 托盘**：右键可将管理器设为开机自启动；Windows 下可勾选关闭到托盘
25. **查看日志**：右键菜单「查看日志」查看脚本日志文件，自动显示最后100KB内容
26. **首页**：菜单栏「首页」在浏览器打开项目 GitHub 页面
27. **启动 API**：工具栏「启动API」（可勾选「启动本工具时自动启动API」）。只把调用配置写入 `api_config.json`，**不会**往任务列表插入项，也**不会**自动打开浏览器；局域网打开 `http://当前IP:8765/` 看接入说明
28. **一键加载API调用**：API 按钮旁点「一键加载API调用」，写入「本机API」分组（`GET /list`、`GET /status?id=1`、`POST /start?id=1`、`POST /stop?id=1`、`POST /restart?id=1`、`POST /start-all`、`POST /stop-all`）。`id=1` 只是示例，编辑对应任务把 URL 改成实际 ID
29. **填入本机API**：添加/编辑 HTTP 任务时点「填入本机API」，按当前路径补本机地址、方法（`/start` `/stop` `/restart` `/start-all` `/stop-all` 为 POST，其余多为 GET）和 `X-API-Key`；仅当脚本里 `DISABLE_AUTH = True` 时不写请求头（`--no-auth` 只影响本次 API 进程，不改按钮是否填头）

## 文件说明

| 文件/目录            | 说明                                   |
|----------------------|----------------------------------------|
| `version.py`         | 应用版本号（当前 **1.3.0**，GUI 与 API 共用） |
| `main.py`            | 主程序（GUI + CLI）                    |
| `WindowsBatchScriptManager_api_server.py` | HTTP API + MCP 服务（GUI 可独立启停；勿用 restart-all 管 API 任务；浏览器访问 `/` 看接入说明） |
| `api_config.json`    | API 自动启动与调用配置（启动 API 时写入 call，不自动插入任务；可选） |
| `app_config.json`    | GUI 应用设置（如「启动后最小化到任务栏」，可选） |
| `WindowsBatchScriptManager_scripts.json` | 脚本任务列表（自动生成与保存） |
| `requirements.txt`   | 可选依赖 `psutil`；API 需另装 fastapi、uvicorn |
| `logs/`              | 脚本输出日志目录（自动创建）           |
| `linuxBatchScriptManager/` | Linux 平台实现（本仓库内；独立 Linux 项目见 **LinuxBatchScriptManager** 目录） |
| `macBatchScriptManager/`   | macOS 平台实现（本仓库内；独立 Mac 项目见 **MacBatchScriptManager** 目录） |

## 打包为 EXE（Windows）

推荐使用 `build_exe.spec` 打包（自动过滤 win32com runtime hook，避免在 Windows Server 等环境启动崩溃）：

```bash
python -m pip install -r requirements-build.txt
python -m PyInstaller build_exe.spec
```

运行 `build_exe.bat onefile` 可生成双文件发布包所用的单文件 EXE。直接运行 `build_exe.bat` 则生成目录版。构建输出位于 `dist/release-时间戳/`。

如使用 SmartPacker 等图形化打包工具，需添加排除模块参数：
```
--exclude-module win32com --exclude-module pythoncom --exclude-module pywintypes --exclude-module win32api --exclude-module win32gui --exclude-module win32con
```

> **注意**：`psutil` 安装时会拉入 `pywin32`，PyInstaller 会自动检测并添加 win32com runtime hook，在部分 Windows Server 上会因 DLL 加载失败而崩溃。排除这些模块不影响功能（程序本身不使用 win32com）。

## 提示

- **脚本启动**：
  - **批处理**：在独立 CMD 窗口运行
  - **Python**：默认在**独立控制台窗口**运行，`print` 与报错会显示在该窗口
  - **Node.js**：在独立控制台窗口运行
  - **PowerShell**：通过 `powershell.exe -ExecutionPolicy Bypass -File` 运行
  - **VBScript/JScript/WSF**：通过 `cscript.exe //NoLogo` 运行
  - **Shell**：通过 `bash` 运行（需安装 Git Bash 或 WSL）
  - **HTTP 请求**：启动时按所选方法（默认 GET，可选 POST）发送请求到指定 URL，可带自定义请求头；旧任务未填方法/请求头时仍按 GET、无请求头；状态栏显示响应状态码和响应时间；HTTP 类型自动禁用保活（curl 正常退出后无需重启）
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
- **配置保存**：保存时先写临时文件再替换原文件，并自动备份上一版为 `.bak`；保存失败时状态栏会提示；可用「从上次备份恢复配置」回滚
- **启动后最小化 / 托盘**：菜单「设置」可勾选「启动后最小化到任务栏」或「关闭窗口时最小化到托盘」（写入 `app_config.json`）；命令行 `python main.py --minimized` 仅当次生效。托盘为 ctypes 实现（不引入 Pillow）；纯后台可改用 CLI/API
- **停止与状态**：对已停止的项点「停止」时，状态栏会提示「所选项已处于停止状态」；路径不存在的项在状态列显示「路径无效」；启动失败时状态栏给出具体原因，右键可查看完整诊断
- **导出/导入配置**：导出会保存所有配置（不含运行时状态如 PID）；导入时「合并」会跳过已存在的路径，「替换」会清空现有后全部导入
- **定时任务**：
  - 支持每天、每小时、每分钟、每周、每月、每N小时、每季度、每年、单次执行、工作日共十种类型
  - 支持启动、重启、停止三种行为
  - 定时检查频率为每 10 秒一次
  - 对于停止行为，时间到达后持续有效直到执行完成
  - 单次执行类型执行后自动禁用
  - 工具栏「定时总览」可查看下次执行时间
  - CLI 和 API 均支持完整的定时任务参数配置
- **执行历史记录**：
  - 自动记录每个脚本的每次执行（启动时间、停止时间、运行时长、退出码、状态）
  - 每个脚本最多保留最近 100 条历史记录
  - 历史记录保存在 `execution_history.json`
  - 支持查看统计信息、按日期筛选、导出 CSV
- **异常通知**：
  - 可配置通知条件：退出码非0、快速退出（默认<5秒，可配置阈值）
  - 支持 Webhook 通知：GET 或 POST 请求，失败时最多重试 2 次并退避
  - 通知配置保存在 `notification_config.json`
- **批量编辑**：
  - 支持批量修改分组、解释器
  - 支持批量启用/禁用定时任务、保活、日志输出
  - 只修改勾选的选项，留空字段不修改
- 端口与进程详情、资源监控需要 `psutil`；在部分环境下 `psutil.net_connections()` 可能需管理员权限才能看到全部连接
- **性能优化**：端口查询结果缓存30秒，避免每4秒刷新时重复调用 `psutil.net_connections()`；表格采用增量更新，只更新值变化的行；分组筛选选项仅在行数变化时重建
- **配置热更新**：通过 CLI 或 API 修改配置后，GUI 自动检测 `WindowsBatchScriptManager_scripts.json` 变化并重新加载，无需重启管理器
- **拖放添加**：将脚本文件拖入窗口即可添加为新任务；支持批量拖入；拖放始终新增任务，不会替换已有任务
- **HTTP / 本机 API**：
  - 方法可选 GET / POST，请求头每行一个（如 `X-API-Key: change-me-in-production`）；旧任务未填则仍按 GET、无请求头
  - 「填入本机API」按 URL 选方法：`/list`、`/status` 为 GET，`/start`、`/stop`、`/restart`、`/start-all`、`/stop-all` 为 POST
  - 「一键加载API调用」写入「本机API」分组上述 7 条，相同 URL+方法会更新而不会重复添加；**启动 API 本身不会插入这些任务**
  - 默认请求要带头；`--no-auth` 只让本次进程不校验头。脚本里 `DISABLE_AUTH = True` 时，「一键加载 / 填入本机API」才不写请求头。「打开工具目录」可找到 API 脚本 / exe
- **常见痛点**：
  - HTTP 任务只会 GET 时，本工具 `/start` 等 POST 接口没法当任务一键点
  - 局域网换机器每次手抄 IP 和 `X-API-Key`
  - GUI exe 勾了「以管理员身份运行」时，另一台机器调 `/list` 可能 WinError 740
  - 把 API 脚本混进任务列表后，「全部重启」会先杀掉接口，远程全断
