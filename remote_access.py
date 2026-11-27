"""Windows OpenSSH enrollment UI."""

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import winreg


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"

def _powershell_path():
    return Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"


def _service_status():
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.OpenSCManagerW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    api.OpenSCManagerW.restype = wintypes.HANDLE
    api.OpenServiceW.argtypes = (wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD)
    api.OpenServiceW.restype = wintypes.HANDLE
    api.CloseServiceHandle.argtypes = (wintypes.HANDLE,)
    api.CloseServiceHandle.restype = wintypes.BOOL
    api.QueryServiceStatus.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    api.QueryServiceStatus.restype = wintypes.BOOL
    manager = api.OpenSCManagerW(None, None, 0x0001)  # SC_MANAGER_CONNECT
    if not manager:
        return "Unknown"
    try:
        service = api.OpenServiceW(manager, "sshd", 0x0004)  # SERVICE_QUERY_STATUS
        if not service:
            return "Missing" if ctypes.get_last_error() == 1060 else "Unknown"
        try:
            status = (wintypes.DWORD * 7)()  # SERVICE_STATUS
            if not api.QueryServiceStatus(service, status):
                return "Unknown"
            return {
                1: "Stopped", 2: "StartPending", 3: "StopPending", 4: "Running",
                5: "ContinuePending", 6: "PausePending", 7: "Paused",
            }.get(status[1], "Unknown")
        finally:
            api.CloseServiceHandle(service)
    finally:
        api.CloseServiceHandle(manager)


def _configure(request):
    root = Path(__file__).resolve().parent
    installers = list((root / "openssh-installer").glob("*.msi"))
    request["installer"] = str(installers[0]) if len(installers) == 1 else ""
    with tempfile.TemporaryDirectory(prefix="wbsm-ssh-") as directory:
        request_file = Path(directory) / "request.json"
        result_file = Path(directory) / "result.json"
        request_file.write_text(json.dumps(request), encoding="utf-8")
        command = (
            f"& {_literal(root / 'remote_access.ps1')} "
            f"-RequestPath {_literal(request_file)} -ResultPath {_literal(result_file)}"
        )
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        launcher = (
            "$ErrorActionPreference = 'Stop'; "
            "$p = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') "
            f"-ArgumentList '-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded}' "
            "-Verb RunAs -Wait -PassThru; exit $p.ExitCode"
        )
        process = subprocess.run(
            [str(_powershell_path()), "-NoProfile", "-NonInteractive", "-Command", launcher],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result_file.exists():
            return json.loads(result_file.read_text(encoding="utf-8-sig"))
        return {"error": f"Administrator setup did not return a result ({process.returncode})."}


def starts_with_wbsm():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\sshd") as key:
            return winreg.QueryValueEx(key, "WbsmStartWithApp")[0] == 1
    except OSError:
        return None


def _load_connection_info():
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WindowsBatchScriptManager\RemoteAccess",
            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            info = json.loads(winreg.QueryValueEx(key, "ConnectionInfo")[0])
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(info, dict) or info.get("enabled") is not True:
        return info if isinstance(info, dict) else {}
    info.pop("password", None)
    if not info.get("account") or not info.get("host"):
        return {}
    try:
        addresses = list(dict.fromkeys(
            item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
            if not item[4][0].startswith(("127.", "169.254."))
        ))
        if addresses:
            info["addresses"] = addresses
    except OSError:
        pass
    return info


def _connection_handoff(connection):
    hosts = connection.get("addresses") or [connection["host"]]
    ports = connection.get("ports") or [22]
    account = connection["account"]
    return {
        **connection,
        "sshCommands": [f"ssh -p {port} -l {account} {host}" for host in hosts for port in ports],
        "sftpCommands": [f"sftp -P {port} {account}@{host}" for host in hosts for port in ports],
        "aiInstructions": (
            f"请连接 Windows 主机 {connection['host']}，使用账号 {account}。"
            "从 sshCommands 选择开发机可达的地址，先核对 hostKeyFingerprints 中的主机密钥指纹，"
            "再用 SSH 执行只读 PowerShell 命令（例如 hostname），并用 sftpCommands 验证文件连接。"
            "优先使用开发机已授权的 SSH 密钥。如需密码，请让操作者在本地终端提示中输入。"
            "验证通过后继续我交代的任务，并报告执行结果。"
        ),
    }


def show_remote_access(parent):
    dialog = tk.Toplevel(parent)
    dialog.title("远程运维 / Remote Access")
    dialog.transient(parent)
    dialog.columnconfigure(0, weight=1)
    output = tk.Text(dialog, height=11, width=74, wrap="word", state="disabled")
    output.grid(row=6, column=0, sticky="nsew", padx=8, pady=8)
    dialog.rowconfigure(6, weight=1)
    results = queue.Queue()
    connection = {}
    current_password = None
    start_with_app = tk.BooleanVar(master=dialog, value=bool(starts_with_wbsm()))
    saved_startup = start_with_app.get()
    current_service_status = "Unknown"
    service_status = tk.StringVar(master=dialog, value="正在查看服务 / Checking service")

    def display_service_status(status):
        nonlocal current_service_status
        current_service_status = status
        labels = {
            "Running": "运行中 / Running",
            "Stopped": "已停止 / Stopped",
            "Paused": "已暂停 / Paused",
            "Missing": "未安装 / Not installed",
        }
        service_status.set("OpenSSH: " + labels.get(status, status or "状态未知 / Unknown"))
        for button, allowed in (
            (start_service, status == "Stopped"),
            (stop_service, status in ("Running", "Paused")),
            (restart_service, status in ("Running", "Paused")),
        ):
            button.state(["!disabled"] if allowed else ["disabled"])

    def copy_text(value):
        if value:
            dialog.clipboard_clear()
            dialog.clipboard_append(value)

    def copy_connection():
        copy_text(json.dumps(_connection_handoff(connection), ensure_ascii=False, indent=2))

    def copy_ssh_command():
        copy_text(_connection_handoff(connection)["sshCommands"][0])

    def show_result(message):
        output.configure(state="normal")
        output.delete("1.0", "end")
        output.insert("1.0", message)
        output.configure(state="disabled")

    def connection_summary(info):
        handoff = _connection_handoff(info)
        prompt = handoff.pop("aiInstructions")
        return f"给 Codex / Claude Code 的提示词\n{prompt}\n\n" + json.dumps(
            handoff, ensure_ascii=False, indent=2,
        )

    def poll():
        nonlocal current_password, saved_startup
        if results.empty():
            dialog.after(150, poll)
            return
        action, result, status = results.get_nowait()
        display_service_status(status)
        if action == "status":
            connection.clear()
            if result.get("enabled") is True:
                connection.update(result)
                show_result(connection_summary(result))
            elif result.get("enabled") is False:
                current_password = None
                show_result("远程账号已禁用 / Remote account disabled.")
            else:
                show_result("尚无保存的接入信息，请点击启用 / No saved connection information. Click Enable.")
        elif action.startswith("service-"):
            if action == "service-startup":
                if "startWithApp" in result:
                    saved_startup = result["startWithApp"]
                start_with_app.set(saved_startup)
            if result.get("error"):
                messagebox.showerror(dialog.title(), result["error"], parent=dialog)
        elif action != "status":
            password = result.pop("password", None)
            if result.get("enabled") is True:
                connection.clear()
                connection.update(result)
                if action == "enable":
                    saved_startup = start_with_app.get()
                if password:
                    current_password = password
            elif result.get("enabled") is False:
                connection.clear()
                current_password = None
            elif action == "password":
                current_password = None
            if action == "uninstall-ssh" and result.get("enabled") is False:
                details = (
                    "重启 Windows 后完成移除 / Restart Windows to complete removal."
                    if result.get("restartRequired") else "已移除完成 / Removal complete."
                )
            elif result.get("enabled") is True:
                details = connection_summary(result)
            else:
                details = json.dumps(result, ensure_ascii=False, indent=2)
            if password:
                details += f"\n\n新密码 / New password: {password}"
            show_result(details)
        copy_button.state(["!disabled"] if connection else ["disabled"])
        copy_ssh.state(["!disabled"] if connection else ["disabled"])
        copy_account.state(["!disabled"] if connection.get("account") else ["disabled"])
        copy_password.state(["!disabled"] if current_password else ["disabled"])
        enable.state(["!disabled"])
        disable.state(["!disabled"])
        change_account.state(["!disabled"])
        change_password.state(["!disabled"])
        import_key.state(["!disabled"])
        refresh_service.state(["!disabled"])
        uninstall_ssh.state(["!disabled"])
        startup.state(["!disabled"])
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

    def start(action, **options):
        request = {"action": action, **options}
        if action == "enable":
            request["startWithApp"] = start_with_app.get()
        for control in (
            enable, disable, change_account, change_password, import_key,
            copy_button, copy_account, copy_password, copy_ssh, startup,
            start_service, stop_service, restart_service, refresh_service,
            uninstall_ssh,
        ):
            control.state(["disabled"])
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)
        if action == "status" or action.startswith("service-"):
            service_status.set("OpenSSH: 正在处理 / Working")
        else:
            show_result("等待管理员授权 / Waiting for administrator approval.")

        def run():
            try:
                result = _load_connection_info() if action == "status" else _configure(request)
            except (OSError, ValueError) as exc:
                result = {"error": str(exc)}
            results.put((
                action, result, result.get("serviceStatus") or _service_status(),
            ))

        threading.Thread(target=run, daemon=True).start()
        dialog.after(150, poll)

    def control_service(action):
        if action in ("service-stop", "service-restart") and not messagebox.askokcancel(
            dialog.title(),
            "此操作可能中断 SSH/SFTP 连接。继续？\n"
            "This may interrupt SSH/SFTP connections. Continue?",
            parent=dialog,
        ):
            return
        start(action)

    def change_startup():
        if current_service_status == "Missing":
            return
        start("service-startup", startWithApp=start_with_app.get())

    def confirm_removal():
        if messagebox.askyesno(
            "确认移除 / Confirm removal",
            "一键移除远程运维？将移除接入并卸载 OpenSSH，SSH/SFTP 会断开。业务文件保留。\n"
            "Remove remote access and uninstall OpenSSH? SSH/SFTP will disconnect. Your files are kept.",
            default="no", parent=dialog,
        ):
            start("uninstall-ssh")

    def select_public_key():
        editor = tk.Toplevel(dialog)
        editor.title("导入公钥 / Import Public Key")
        editor.transient(dialog)
        editor.grab_set()
        ttk.Label(editor, text="粘贴开发机 SSH 公钥，或选择 .pub 文件 / Paste a public key or select a .pub file").pack(
            anchor="w", padx=10, pady=(10, 4),
        )
        entry = tk.Text(editor, width=68, height=4, wrap="char")
        entry.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        actions = ttk.Frame(editor)
        actions.pack(fill="x", padx=10, pady=(0, 10))

        def submit(value):
            key = value.strip()
            if not key or len(key) > 8192 or len(key.splitlines()) != 1:
                messagebox.showerror(
                    editor.title(), "请输入一行 OpenSSH 公钥 / Enter a single OpenSSH public key.", parent=editor,
                )
                return
            editor.destroy()
            start("key", publicKey=key)

        def browse():
            filename = filedialog.askopenfilename(
                parent=editor, title=editor.title(), filetypes=(("OpenSSH public key", "*.pub"),),
            )
            if filename:
                try:
                    with open(filename, encoding="utf-8-sig") as source:
                        value = source.read(8193)
                except (OSError, ValueError) as exc:
                    messagebox.showerror(editor.title(), str(exc), parent=editor)
                    return
                submit(value)

        ttk.Button(actions, text="导入 / Import", command=lambda: submit(entry.get("1.0", "end-1c"))).pack(side="left")
        ttk.Button(actions, text="选择 .pub 文件 / Select .pub File", command=browse).pack(side="left", padx=6)
        editor.bind("<Escape>", lambda event: editor.destroy())
        entry.focus_set()

    def edit_credential(action, title, field):
        editor = tk.Toplevel(dialog)
        editor.title(title)
        editor.transient(dialog)
        editor.grab_set()
        ttk.Label(editor, text=title).pack(anchor="w", padx=10, pady=(10, 2))
        entry = ttk.Entry(editor, width=34, show="*" if action == "password" else "")
        entry.pack(fill="x", padx=10, pady=(0, 8))
        actions = ttk.Frame(editor)
        actions.pack(fill="x", padx=10, pady=(0, 10))

        def submit(random):
            value = entry.get()
            if not random and not value:
                entry.focus_set()
                return
            editor.destroy()
            start(action, **({} if random else {field: value}))

        ttk.Button(actions, text="保存 / Save", command=lambda: submit(False)).pack(side="left")
        ttk.Button(actions, text="随机生成 / Generate", command=lambda: submit(True)).pack(side="left", padx=6)
        entry.bind("<Return>", lambda event: submit(False))
        editor.bind("<Escape>", lambda event: editor.destroy())
        entry.focus_set()

    buttons = ttk.Frame(dialog)
    buttons.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))
    enable = ttk.Button(buttons, text="启用 / Enable", command=lambda: start("enable"))
    disable = ttk.Button(buttons, text="禁用账号 / Disable Account", command=lambda: start("disable"))
    enable.pack(side="left", padx=4)
    change_account = ttk.Button(
        buttons, text="修改账号 / Account",
        command=lambda: edit_credential("rename", "修改账号 / Change account", "name"),
    )
    change_account.pack(side="left", padx=4)
    change_password = ttk.Button(
        buttons, text="修改密码 / Password",
        command=lambda: edit_credential("password", "修改密码 / Change password", "password"),
    )
    change_password.pack(side="left", padx=4)
    disable.pack(side="left", padx=4)
    copy_bar = ttk.Frame(dialog)
    copy_bar.grid(row=3, column=0, sticky="ew", padx=8, pady=(6, 0))
    copy_account = ttk.Button(
        copy_bar, text="复制账号 / Copy Account", command=lambda: copy_text(connection.get("account")),
    )
    copy_password = ttk.Button(
        copy_bar, text="复制密码 / Copy Password", command=lambda: copy_text(current_password),
    )
    copy_ssh = ttk.Button(copy_bar, text="复制 SSH 命令 / Copy SSH", command=copy_ssh_command)
    copy_button = ttk.Button(copy_bar, text="复制 AI 接入信息 / Copy AI Connection", command=copy_connection)
    for button in (copy_account, copy_password, copy_ssh, copy_button):
        button.state(["disabled"])
        button.pack(side="left", padx=4)
    import_key = ttk.Button(copy_bar, text="导入公钥 / Import Key", command=select_public_key)
    import_key.pack(side="left", padx=4)
    startup = ttk.Checkbutton(
        dialog, text="随 WBSM 启动 / Start with WBSM",
        variable=start_with_app, command=change_startup,
    )
    startup.grid(row=1, column=0, sticky="w", padx=8, pady=(6, 0))

    service_bar = ttk.Frame(dialog)
    service_bar.grid(row=2, column=0, sticky="ew", padx=8, pady=(6, 0))
    ttk.Label(service_bar, textvariable=service_status).pack(side="left", padx=(0, 8))
    start_service = ttk.Button(service_bar, text="启动服务 / Start", command=lambda: control_service("service-start"))
    stop_service = ttk.Button(service_bar, text="停止服务 / Stop", command=lambda: control_service("service-stop"))
    restart_service = ttk.Button(service_bar, text="重启服务 / Restart", command=lambda: control_service("service-restart"))
    refresh_service = ttk.Button(service_bar, text="刷新 / Refresh", command=lambda: start("status"))
    for button in (start_service, stop_service, restart_service, refresh_service):
        button.pack(side="left", padx=4)
    help_text = ttk.Label(
        dialog,
        text=(
            "关闭窗口后 SSH/SFTP 继续运行。「随 WBSM 启动」在打开程序时启动服务。\n"
            "禁用账号会阻止该账号的新登录。停止服务会停止整台机器的 SSH/SFTP 接入。\n"
            "Closing this window leaves SSH/SFTP running. Start with WBSM starts it when you open the app.\n"
            "Disable Account blocks new logins for that account. Stop stops SSH/SFTP for everyone."
        ),
        wraplength=590,
        justify="left",
    )
    help_text.grid(row=4, column=0, sticky="w", padx=8, pady=(8, 0))
    cleanup_bar = ttk.Frame(dialog)
    cleanup_bar.grid(row=5, column=0, sticky="ew", padx=8, pady=(6, 0))
    uninstall_ssh = ttk.Button(
        cleanup_bar, text="一键移除远程运维 / Remove Remote Access", command=confirm_removal,
    )
    uninstall_ssh.pack(side="left", padx=4)
    start("status")
