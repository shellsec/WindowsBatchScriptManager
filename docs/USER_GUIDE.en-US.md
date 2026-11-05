# User guide

[Project overview](../README.md)

## Use Cases

| Scenario | Description |
|----------|-------------|
| Personal script hub | Centralize backup/sync/local services; optional launch-on-app-start and keep-alive |
| Local dev | Start/stop many services at once; groups, work dir, and launch args |
| Scheduled maintenance | Daily/hourly start, restart, stop without Windows Task Scheduler |
| LAN remote control | Optional `WindowsBatchScriptManager_api_server.py` over HTTP/MCP (Python + auth); open `http://<LAN-IP>:8765/` for connect docs |
| Adopt running processes | Scan running scripts or match by path on refresh |
| HTTP task to own API | `/start` and `/stop` are POST; HTTP tasks can use GET/POST plus `X-API-Key` (GET-only cannot fire those endpoints) |
| Avoid retyping IP/key | **Load API Calls** or **Fill Local API** writes the LAN URL and header |
| Find API script / exe | **Open tool folder** opens the exe / `main.py` directory |
| Remote `/list` WinError 740 | GUI exe set to "Run as administrator" can break LAN `/list`; run the API with Python and turn off the EXE compatibility flag |
| API killed by Restart All | Putting the API script in the task list lets Restart All stop the API first; use standalone **Start API** instead |

## Platform Notes

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

## Features

- **Unified Management**: Add, edit, delete scripts; supports multiple script types:
  - **Batch**: `.bat`, `.cmd`
  - **Python**: `.py`
  - **Node.js**: `.js`, `.mjs`, `.cjs`, `.ts`, `.jsx`, `.tsx`
  - **PowerShell**: `.ps1`
  - **VBScript**: `.vbs`
  - **JScript**: `.js`, `.jse` (preferentially identified as Node.js)
  - **WSF**: `.wsf`
  - **Shell**: `.sh` (Git Bash/WSL)
  - **HTTP Request**: `http://` or `https://` URL (GET by default; POST and custom headers such as `X-API-Key` are supported; older tasks with no method/headers stay GET with no headers)
  - **Process Management**: Process name (e.g., `notepad.exe`), directly start/stop programs
  - **Direct Command**: Enter command line directly (e.g., `ping 8.8.8.8`, `ipconfig`, `python -c "print('hello')"`), supports pipes, redirection, etc.; supports scheduled tasks and log output
  - **System Shutdown / Reboot**: Task types with no script path; manual Start or scheduled Start runs graceful Windows shutdown/reboot
- **Start / Stop / Restart Task**: One-click operations on selected items; stopping will terminate the process tree (including child processes)
- **System shutdown/reboot**: GUI buttons or task types; remote API `GET/POST /shutdown`, `/reboot`
- **Standalone API panel**: Toolbar `API: Start/Stop/Restart API`, optional **Launch API when app starts**, and **Load API Calls** beside those buttons; default `WindowsBatchScriptManager_api_server.py`; whitelist keeps API alive during `/shutdown`, `/reboot`, `/restart-all`. Starting API writes call settings to `api_config.json` and does **not** auto-insert tasks; **Load API Calls** writes the Local API group (`GET /list`, `POST /start?id=1`, …; `id=1` is only an example). Starting API does **not** open a browser; visit `http://<LAN-IP>:8765/` for API/MCP docs.
- **Path & Working Directory**: Each record can set script path and working directory (relative paths resolve against config file directory if work dir is empty); saved to `WindowsBatchScriptManager_scripts.json`, automatically loaded on next launch
- **Status & PID**: Table displays running status and main process PID; refresh interval can be set via menu "Settings" → "Refresh interval" (2/4/8 sec), can "Pause Refresh" / "Resume Refresh"; items with invalid path show "Path invalid" in status column
- **Running Process Adoption**: Scripts already in the list but started externally (e.g., CMD, File Explorer), including .exe, will be automatically matched by path on refresh and shown as "Running", no need to click "Start" again; requires `psutil`
- **Port Detection**: Automatically displays listening ports of the process tree (depends on `psutil`, falls back to `netstat -ano`)
- **Page Title**: The "Title" column automatically fetches webpage titles of web services (asynchronous background fetching, non-blocking UI)
- **Click to Visit**: Toolbar "Visit" button or right-click "Visit :port", or ":port" buttons in popup, opens `http://localhost:port` in browser
- **Process View & Resource Monitoring**: Select a running item, click "Ports/Processes" to view main process and child processes' PID, name, path, CPU%, memory MB (requires `psutil`)
- **Scan Running**: Identifies **currently running** script processes in the system (parses script paths from command line), can "Add to Manager" or "Add All" to manage, or "End Selected"
- **Start All / Stop All**: One-click start or stop all items in the list
- **Stop All Batch Scripts Outside Manager**: Stops all running batch scripts in the system (including those in the list and outside); **Open tool folder** to its right opens the exe / `main.py` directory
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
- **Export/Import Config**: Toolbar "Export Config" / "Import Config" (merge or replace); saves auto-write `.bak`; Settings → "Restore config from last backup"
- **Scheduled Tasks**: Daily/hourly/minutely/weekly/monthly/every-N-hours/quarterly/yearly/once/weekday; toolbar **Schedules** overview shows next run times
- **Execution History**: Records start/stop, runtime, exit code; date filter + CSV export; statistics via "Execution History"
- **Start Failure Diagnosis**: Status bar shows concrete reasons; right-click "Diagnose start" / "Copy diagnosis"
- **System Tray (Windows)**: Optional "Minimize to tray on close" (ctypes, no Pillow); tray right-click: Show / Stop All / Quit
- **Exception Notifications**: Popup on abnormal exit; Webhook GET/POST with automatic retries; configure via "Notification Config"
- **Batch Edit**: Select multiple scripts, use "Batch Edit" button to modify group, interpreter, launch arguments, scheduled tasks, keep-alive, log output in bulk
- **HTTP Request Tasks**: Support adding `http://` or `https://` URL; GET by default, optional POST and custom headers (e.g. `X-API-Key`) for calling this tool's API; older tasks with no method/headers stay GET with no headers; automatically sends request on start and shows response status code; auto-disable keep-alive for HTTP request type; add/edit dialog has **Fill Local API**
- **Drag & Drop**: Drag script files into the window to add as new tasks; supports batch dropping; always adds new tasks without replacing existing ones
- **Process Management Tasks**: Support adding process name (e.g., `notepad.exe`) as process management task; can directly start/stop specified programs; process not existing does not affect adding
- **Direct Command Tasks**: Support entering command line directly (e.g., `ping 8.8.8.8`, `ipconfig /all`, `python -c "print('hello')"`), executed via `cmd.exe /c`, supports pipes, redirection and other Windows command-line features; supports keep-alive, scheduled tasks, and log output; auto-detection: commands with spaces and no file extension on the first word are automatically identified as command type
- **Add/Edit Dialog**: Unified form for name, path, working directory, group, interpreter (Python/Node.js/Shell), keep-alive, log output (Python/Node.js/PowerShell/Command), scheduled tasks; path and working directory can be browsed; automatically shows/hides relevant options based on script type; type dropdown supports Script / HTTP Request / Process Management / Direct Command; HTTP tasks can use **Fill Local API**
- **Main Panel Add (Manual) Button**: Toolbar "Add (Manual)" button opens add dialog for manual URL, process name, or command input without file picker
- **Right-Click Menu**: Open file/dir, visit port, copy info, group start/stop, view log, diagnose start, etc.
- **Row Reordering**: Alt+drag to reorder rows; Alt+↑/↓ keyboard shortcuts to move rows up/down; right-click "Move Up/Move Down"
- **Template System**: Save script config as template, add new scripts from template, manage existing templates
- **Auto Start on Boot**: Right-click menu to set the manager as Windows startup item
- **View Log**: Right-click "View Log" to view script log file (whether running or not), auto-displays last 100KB
- **Button Shortcut Tooltips**: Hover toolbar buttons to see keyboard shortcuts (e.g., Ctrl+N, F5, etc.)

## How to Run

1. Install dependencies (recommended, for port and process details):
   ```bash
   pip install -r requirements.txt
   ```
   Can run without it, port detection will use `netstat`, process details will be simplified.

2. Start the program:
   ```bash
   python main.py
   ```
   To stay out of the way on boot: **Settings → Minimize to taskbar on start**, or `python main.py --minimized`.
   Windows stay-resident: **Settings → Minimize to tray on close** (tray right-click: Show / Stop All / Quit).
   **macOS / Linux**: `python3 main.py` at the repository root; add-dialog soft-filters Windows-only extensions (`.bat/.cmd/.vbs/.exe`).

## macOS / Linux quick check

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

Optional API: `pip install fastapi uvicorn` then `python3 WindowsBatchScriptManager_api_server.py --host 127.0.0.1 --port 8765` and `curl -H "X-API-Key: change-me-in-production" "http://127.0.0.1:8765/list"`.

## Command Line (CLI)

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

## Remote API and MCP

`WindowsBatchScriptManager_api_server.py` exposes HTTP endpoints and MCP (same process, same port) that call the main CLI via subprocess, for remote start/stop, add, delete, export/import.

**Note:** Use the GUI **API: Start/Stop/Restart API** panel (optional **Launch API when app starts**). If listed as a task, `/shutdown`, `/reboot`, and `/restart-all` skip the API via whitelist; still prefer running API standalone. Starting the API does **not** open a browser and does **not** auto-insert tasks; it only writes call settings to `api_config.json`. Use **Load API Calls** beside the API buttons to write Local API HTTP tasks (with `X-API-Key`; `id=1` is only an example). The add/edit dialog also has **Fill Local API**. **Open tool folder** (next to "Stop All Batch Scripts Outside Manager") finds the API script / exe.

1. Install API dependencies:
   ```bash
   pip install fastapi "uvicorn[standard]"
   ```
2. Start the API server:
   ```bash
   python WindowsBatchScriptManager_api_server.py --port 8765
   ```
   - **Auth is enabled by default**; use `--no-auth` only for local debugging (never on public networks)
   - Allow access from other machines: `--host 0.0.0.0` (must change the default API key)
   - Custom API key: `--api-key your-secret` or env `API_KEY`
   - `/` is the connect-docs page by default (`--no-docs` restores JSON)
3. Requests must include header: `X-API-Key: <key>` (default `change-me-in-production`; change in production).
4. On the LAN, open `http://<current-IP>:8765/` for API tables, curl/Python/fetch samples, and one-click MCP JSON. After an IP change, open the new address — the page updates. Saved Cursor config does not auto-rewrite; copy the hostname variant or recopy from the page.

Optional **GET `/exec?cmd=...`** (disabled by default): uncomment `# ENABLE_GET_CMD_EXEC = True` in the API script. High risk — blocked with `--no-auth` or default key on `0.0.0.0`.

Optional **Web Dashboard** (`web_dashboard.py`): binds `127.0.0.1` by default; requires `X-API-Key` (or `/?key=`); not bundled into the GUI exe.

MCP (Cursor / Claude Desktop):

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

Tools: `list_scripts`, `script_status`, `start_script`, `stop_script`, `restart_script`, `start_all`, `stop_all` (no shutdown/reboot/exec).

| Method | Path | Description |
|--------|------|-------------|
| GET | / | API/MCP connect docs (default) |
| GET | /health | Health JSON |
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
| POST | /mcp | MCP Streamable HTTP |
| GET | /exec?cmd=... | Run cmd via `cmd.exe /c` (Windows; **off by default**) |

Example (from another machine):
```bash
curl -H "X-API-Key: change-me-in-production" "http://192.168.1.10:8765/list"
curl -X POST -H "X-API-Key: change-me-in-production" "http://192.168.1.10:8765/start?id=1"
```

Examples:

- **LAN `/list`**: another machine calls `GET /list` with `X-API-Key` (above). Auth is on by default; missing header → 401.
- **HTTP task POST start**: add HTTP, method POST, URL `http://<LAN-IP>:8765/start?id=1`, header `X-API-Key: ...`; or **Fill Local API** (empty path becomes `/list`; an existing `/start` becomes POST plus header).
- **Load then change id**: **Load API Calls** writes Local API tasks (`GET /list`, `GET /status?id=1`, `POST /start?id=1`, `POST /stop?id=1`, `POST /restart?id=1`, `POST /start-all`, `POST /stop-all`). Change `id=1` in the URL to a real task ID.
- **Find API script / exe**: **Open tool folder** — same directory as `WindowsBatchScriptManager_api_server.py` (and the packaged exe).
- **Header vs no header**: default `DISABLE_AUTH = False` requires `X-API-Key`. `--no-auth` skips the check for that process. Set `DISABLE_AUTH = True` in the script if you want **Load API Calls / Fill Local API** to omit the header. Do not disable auth on public networks.

CLI and API work on **Windows / macOS / Linux**. Pain point: GUI exe set to "Run as administrator" can make LAN `/list` fail with WinError 740 — run the API with Python and turn off that compatibility flag. The two-file deployment uses the EXE beside the API script. A source deployment uses `main.py` when it is present.

## Usage

1. **Add**: Select "Add" → Choose script file (supports batch, Python, Node.js, PowerShell, VBScript, WSF, Shell, etc.) → Fill in name, working directory, group/tag, interpreter (Python/Node.js/Shell need to fill), keep-alive, scheduled tasks in dialog, can click "Browse" to select path and directory; or use toolbar "Add (Manual)" button to open dialog for manual URL (`http://` or `https://`), process name (e.g., `notepad.exe`), or direct command (e.g., `ping 8.8.8.8`) input
2. **Edit**: Select a row then click "Edit" or double-click, modify in dialog
3. **Duplicate**: Select one or more items → "Duplicate", get new items with "(副本)" in name, can edit again
4. **Start / Start All**: Selected items → "Start"; or "Start All" button in second row to start all non-running items in list
5. **Stop / Stop All**: Selected items → "Stop"; or "Stop All" button in second row to stop all running items in list
6. **Stop All Batch Scripts Outside Manager**: Stops all running batch scripts in the system (including those in the list and outside); **Open tool folder** to its right opens the exe / `main.py` directory (API script / packaged exe)
7. **Restart**: Stop then start
8. **Ports/Processes**: Select a **running** item → "Ports/Processes", popup shows listening ports, process tree and resources (CPU%, memory MB); ":port" buttons below, click to open `http://localhost:port` in browser
9. **Visit**: Select a running item with ports → "Visit" opens the first port; or right-click "Visit :port" to open specified port
10. **Group Filter**: "Filter by Group" dropdown above table selects group, only shows items in that group; "Clear Filter" restores showing all
11. **Keyword Search & Locate**: Enter text in "Keyword" box, click "Locate" or press Enter to select and scroll to the first matching row (ID/name/group/path/work dir); "Next" jumps to the next match
12. **IP Address Display**: Filter row shows local IP (click to copy); third row shows public IP to the right of "Stop All Batch Scripts Outside Manager" (click to copy); public IP is automatically fetched on startup; when filter row content is too wide, a horizontal scrollbar appears for scrolling
13. **Export/Import Config**: Export/import JSON; Settings → restore from `.bak`
14. **Execution History**: View stats; date filter + CSV export
15. **Schedule Overview**: Toolbar "Schedules" lists enabled jobs and next run times
16. **Exception Notifications**: Notification rules + Webhook (with retries)
17. **Batch Edit**: Bulk change group, interpreter, schedule/keep-alive/log flags
18. **Pause Refresh / Resume Refresh**: Pause auto-refresh; Settings → refresh interval 2/4/8s
19. **Scan Running**: List running script processes (psutil); add or end selected
20. **Scheduled Tasks**: Enable in edit dialog (daily/hourly/… start/restart/stop)
21. **Right-Click Menu**: Open file/dir, ports, copy, group ops, logs, diagnose start
22. **Row Reordering**: Alt+drag / Alt+↑↓ / Move Up-Down
23. **Template System**: Save / apply / manage templates
24. **Auto Start / Tray**: Boot startup; Windows tray minimize on close
25. **View Log**: Last 100KB of script log
26. **Home**: Opens GitHub project page
27. **Start API**: Toolbar **Start API** (optional **Launch API when app starts**). Writes `api_config.json` only — does **not** insert tasks or open a browser; open `http://<LAN-IP>:8765/` for docs
28. **Load API Calls**: Writes the Local API group (`GET /list`, `POST /start?id=1`, …). `id=1` is an example — edit the URL to a real task ID
29. **Fill Local API**: In the add/edit HTTP dialog, fills LAN URL, method (`/start` `/stop` `/restart` `/start-all` `/stop-all` → POST), and `X-API-Key`; omits the header only when the script has `DISABLE_AUTH = True`

## Files

| File/Directory      | Description                              |
|---------------------|------------------------------------------|
| `version.py`        | App version (**1.3.0**, shared by GUI & API) |
| `main.py`           | Main program (GUI + CLI)                 |
| `WindowsBatchScriptManager_api_server.py` | HTTP API + MCP server (GUI panel can start/stop; avoid restart-all on API task; open `/` for connect docs) |
| `api_config.json`   | API auto-start and call settings (written on API start; does not auto-insert tasks; optional) |
| `app_config.json`   | GUI app settings (e.g. minimize on start, optional) |
| `WindowsBatchScriptManager_scripts.json` | Script task list (auto-generated and saved) |
| `requirements.txt`  | Optional dependency `psutil`; API also needs fastapi, uvicorn |
| `logs/`             | Script output log directory (auto-created) |
| `linuxBatchScriptManager/` | Linux platform impl (in this repo; standalone Linux project in **LinuxBatchScriptManager** directory) |
| `macBatchScriptManager/`   | macOS platform impl (in this repo; standalone Mac project in **MacBatchScriptManager** directory) |

## Building EXE (Windows)

Recommended: use `build_exe.spec` (automatically filters win32com runtime hooks to prevent startup crashes on Windows Server etc.):

```bash
python -m pip install -r requirements-build.txt
python -m PyInstaller build_exe.spec
```

Run `build_exe.bat onefile` to build the standalone EXE used in the two-file release. Running `build_exe.bat` by itself builds the folder edition. Each build goes into a new `dist/release-<timestamp>/` directory.

When using SmartPacker or similar GUI tools, add exclude module arguments:
```
--exclude-module win32com --exclude-module pythoncom --exclude-module pywintypes --exclude-module win32api --exclude-module win32gui --exclude-module win32con
```

> **Note**: `psutil` installation pulls in `pywin32`, and PyInstaller auto-detects and adds win32com runtime hooks, which may crash on some Windows Server due to DLL load failure. Excluding these modules doesn't affect functionality (the program doesn't use win32com).

## Tips

- **Script Startup**:
  - **Batch**: Runs in independent CMD window
  - **Python**: By default runs in **independent console window**, `print` and errors displayed in that window
  - **Node.js**: Runs in independent console window
  - **PowerShell**: Runs via `powershell.exe -ExecutionPolicy Bypass -File`
  - **VBScript/JScript/WSF**: Runs via `cscript.exe //NoLogo`
  - **Shell**: Runs via `bash` (requires Git Bash or WSL installation)
  - **HTTP Request**: Sends GET (default) or POST to the specified URL on start, with optional custom headers; older tasks with no method/headers stay GET with no headers; status bar shows response status code and response time; auto-disable keep-alive for HTTP request type (curl exits normally, no need to restart)
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
- **Config Save**: Atomic write + automatic `.bak`; restore from Settings if needed
- **Minimize / Tray**: Settings → minimize to taskbar and/or tray on close (`app_config.json`); tray via ctypes (no Pillow); CLI/API for headless use
- **Stop & Status**: Already-stopped items show a status hint; invalid paths show "Path invalid"; start failures show a concrete reason + diagnose dialog
- **Export/Import Config**: Export omits runtime PID; merge skips existing paths; replace clears then imports
- **Scheduled Tasks**:
  - Ten schedule types; start/restart/stop actions; 10s check interval
  - "Once" auto-disables after run; toolbar **Schedules** overview for next runs
  - Full schedule params via CLI/API
- **Execution History**:
  - Up to 100 records per script in `execution_history.json`
  - Statistics, date filter, CSV export
- **Exception Notifications**:
  - Conditions: non-zero exit / quick exit; Webhook GET/POST with up to 2 retries
  - Config in `notification_config.json`
- **Batch Edit**:
  - Supports batch modification of group, interpreter
  - Supports batch enable/disable scheduled tasks, keep-alive, log output
  - Only modifies selected options, empty fields are not modified
- Port and process details, resource monitoring require `psutil`; in some environments `psutil.net_connections()` may require administrator privileges to see all connections
- **Performance Optimization**: Port query results cached for 30 seconds to avoid repeated `psutil.net_connections()` calls every 4 seconds; table uses incremental updates, only updating changed rows; group filter options only rebuild when row count changes
- **Config Hot Reload**: After modifying config via CLI or API, GUI automatically detects `WindowsBatchScriptManager_scripts.json` changes and reloads without restarting the manager
- **Drag & Drop**: Drag script files into window to add as new tasks; supports batch drop; always adds new tasks without replacing existing ones
- **HTTP / Local API**:
  - Method GET or POST; one header per line (e.g. `X-API-Key: change-me-in-production`); older tasks stay GET with no headers
  - **Fill Local API** picks the method from the URL: `/list` and `/status` → GET; `/start`, `/stop`, `/restart`, `/start-all`, `/stop-all` → POST
  - **Load API Calls** writes the seven Local API tasks above (same URL+method updates in place). **Start API** itself does not insert those tasks
  - Requests need the header by default. `--no-auth` only skips the check for that process. Set `DISABLE_AUTH = True` in the script if **Load / Fill** should omit the header. **Open tool folder** finds the API script / exe
- **Pain points**:
  - GET-only HTTP tasks cannot fire this tool's POST `/start` endpoints
  - Retyping LAN IP and `X-API-Key` on every machine
  - GUI exe set to "Run as administrator" can make LAN `/list` fail with WinError 740
  - Putting the API script in the task list lets Restart All kill the API first
