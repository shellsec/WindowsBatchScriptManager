# WindowsBatchScriptManager

Manage batch, Python, Node.js, and PowerShell tasks from a Windows desktop app. Schedule jobs, queue runs, and control tasks remotely through HTTP or MCP.

**Give Codex or Claude Code access to a Windows target.** Enable Remote Access in WBSM and copy the connection details so your AI coding agent can run PowerShell over SSH and transfer files over SFTP.

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/shellsec/WindowsBatchScriptManager)](https://github.com/shellsec/WindowsBatchScriptManager/releases/latest)

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja-JP.md) · [한국어](README.ko-KR.md) · [Deutsch](README.de-DE.md) · [Français](README.fr-FR.md) · [Español](README.es-ES.md) · [Português (Brasil)](README.pt-BR.md) · [Русский](README.ru-RU.md)

[Download for Windows](https://github.com/shellsec/WindowsBatchScriptManager/releases) · [User guide](docs/USER_GUIDE.en-US.md) · [AI remote access](#ai-remote-access) · [API and MCP](docs/USER_GUIDE.en-US.md#remote-api-and-mcp)

![English desktop app showing tasks and an HTTP response](docs/screenshots/en-US.jpg?v=1.3.7)

## Quick start

1. Open [GitHub Releases](https://github.com/shellsec/WindowsBatchScriptManager/releases) and extract the Windows ZIP.
2. Run `WindowsBatchScriptManager.exe`. The desktop EXE includes its Python runtime.
3. Select **Add**, choose a script, then select **Start**. Python, Node.js, and other tasks use their configured interpreters.

When upgrading, keep your existing `WindowsBatchScriptManager_scripts.json` and other configuration files.

Starting with v1.3.7, use **Check for Updates** beside **Task tools** to check GitHub manually. After confirmation, the Windows single-file EXE downloads and verifies the ZIP, replaces itself, and restarts. The update restart skips tasks configured to launch at startup. Update the standalone API script separately when needed.

## What you can do

- Start, stop, and restart tasks in groups. View ports, logs, and execution history.
- Create tasks from templates, import a folder, or choose a Python virtual environment, Conda environment, or Node.js version.
- Set schedules, limit concurrent runs, and trigger follow-up tasks based on success or failure.
- Run HTTP tasks and inspect the status code and response body in the details pane.
- Enable SSH and SFTP on Windows, copy AI connection details, and sign in with a password or an imported SSH public key.
- Switch among ten interface languages. The first option follows the system language.

Schedules, queues, and follow-up tasks run while the desktop app is open. [Task management guide](docs/TASK_MANAGEMENT.md)

## C: drive cleanup
Starting with v1.3.9, **Clean up C: drive** removes recognized `.tmp` / `.temp` files without an age limit, thumbnail caches, supported browser and shader caches, inactive task logs, and crash dumps. Results appear in the lower log pane. Protected application/task paths and running browser caches are skipped; the Recycle Bin is kept.

**Disk cleanup preview** lists recognized cache locations on local fixed drives for the current user, rather than scanning arbitrary folders. Items are unchecked by default. Optional entries include Prefetch, individual system restore points, and disabling hibernation. Review the deletion impact before confirming. Right-click an entry to show its file/folder or copy its path; these actions are unavailable for restore points. Hibernation requires administrator permissions and also disables Fast Startup. If Windows leaves `hiberfil.sys` behind, the app reports the failure without counting the space as reclaimed; it does not forcibly delete the file.

To disable hibernation manually, right-click the hibernation item in **Disk cleanup preview** and select **Copy commands to disable hibernation**. Search for **Windows PowerShell** in Start, right-click it, select **Run as administrator**, then choose **Yes** at the prompt. In **Administrator: Windows PowerShell**, paste the three lines at the `PS>` prompt and press Enter.

```powershell
powercfg.exe /hibernate off
cmd.exe /d /c 'del /f /q /a "%SystemDrive%\hiberfil.sys"'
Get-Item -Force -LiteralPath "$env:SystemDrive\hiberfil.sys"
```

The first line disables hibernation and Fast Startup. The second tries to forcibly delete any remaining hibernation file. The third checks for the file. A path-not-found message means the file was removed.

## AI remote access

| Target OS | How to connect |
| --- | --- |
| **Windows** | Open **Remote Access → Enable** in WBSM and approve the administrator prompt. WBSM configures OpenSSH Server and a dedicated standard account. The offline build includes the installer. Select **Copy AI Connection** and share the details with Codex or Claude Code on your development machine. |
| **Linux / macOS** | Use the SSH/SFTP service already enabled on the target. The default port is **22**. No WBSM installation or platform-specific setup is needed. If the service uses a custom port, connect on that port. |

![Windows Remote Access window showing OpenSSH running and the Copy AI Connection button, with an example address.](docs/screenshots/remote-access-windows.png)

- **Deploy an app.** Update a Python script or EXE on your development machine, upload it with SFTP, then start it over SSH and check its health endpoint.
- **Troubleshoot or edit files.** Inspect processes, download readable logs, update scripts in writable directories, or check whether Python, Git, and Docker are available to the remote account.
- **Connect again.** Import your development machine's SSH public key on the target to sign in without typing a password. Windows account permissions determine which files and commands the agent can access.

Remote Access uses standard SSH and SFTP clients. The HTTP API and MCP below remain available for listing, starting, and stopping tasks configured in WBSM. [Windows remote access setup and verification](docs/WINDOWS_REMOTE_ACCESS.md)

## Remote API and MCP

On the server, keep `WindowsBatchScriptManager.exe` and `WindowsBatchScriptManager_api_server.py` in the same directory. The API server needs Python and the packages below. Choose a private API key before making the service reachable from another machine.

```powershell
python -m pip install fastapi "uvicorn[standard]"
python WindowsBatchScriptManager_api_server.py --host 0.0.0.0 --port 8765 --api-key "<your-key>"
```

Use the `X-API-Key` header for HTTP requests. The MCP endpoint is `http://<server>:8765/mcp`. For example, a desktop HTTP task can send `POST /start?id=10` to start task 10 on the server. [API and MCP reference](docs/USER_GUIDE.en-US.md#remote-api-and-mcp)

## Documentation

- [CLI and automation](docs/USER_GUIDE.en-US.md#command-line-cli)
- [Run from source on Windows, macOS, or Linux](docs/USER_GUIDE.en-US.md#how-to-run)
- [Build the Windows EXE](PYINSTALLER.md)
- [Contributing](CONTRIBUTING.md) · [Apache-2.0 license](LICENSE) · [Notices](NOTICE)

## Support the project

[Sponsor on Afdian](https://ifdian.net/a/shellsec) · [aiv123.com](https://aiv123.com/) · [ofox.ai](https://ofox.io/x/aiv123) · [TOKEN2ROI (AI Top-Ups)](https://token2roi.com/)
