# -*- coding: utf-8 -*-
"""
Linux 平台实现：配置路径、开机自启（systemd user）、脚本启动。
"""
import os
import sys
import subprocess
from pathlib import Path
from typing import Optional

SERVICE_NAME = "scriptmanager.service"
_raw_xdg = os.environ.get("XDG_CONFIG_HOME")
if _raw_xdg:
    XDG_CONFIG_HOME = Path(_raw_xdg).expanduser()
else:
    XDG_CONFIG_HOME = Path.home() / ".config"
SYSTEMD_USER_DIR = XDG_CONFIG_HOME / "systemd" / "user"
CONFIG_BASE_DIR = XDG_CONFIG_HOME / "ScriptManager"


def _proc_env_from_vars(env_vars: Optional[str]):
    if not env_vars or not str(env_vars).strip():
        return None
    proc_env = os.environ.copy()
    for line in str(env_vars).strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            proc_env[k.strip()] = v.strip()
    return proc_env


def get_config_dir(app_path: str) -> Path:
    """返回配置目录（WindowsBatchScriptManager_scripts.json、history 等所在目录）。"""
    if getattr(sys, "frozen", False):
        return CONFIG_BASE_DIR
    return Path(app_path).resolve().parent


def get_startup_command(app_path: str) -> str:
    """返回用于登录自启的命令（供 main 显示用）。frozen 时为可执行路径，否则为 python + 脚本。"""
    app = os.path.abspath(app_path)
    if getattr(sys, "frozen", False):
        return app
    return f'"{sys.executable}" "{app}"'


def _service_file() -> Path:
    return SYSTEMD_USER_DIR / SERVICE_NAME


def _exec_start_command(app_path: str) -> str:
    """systemd ExecStart 完整命令；路径含空格时加引号。"""
    app = os.path.abspath(app_path)
    if getattr(sys, "frozen", False):
        return app
    if " " in app:
        return f'{sys.executable} "{app}"'
    return f"{sys.executable} {app}"


def is_startup_enabled(app_path: str) -> bool:
    """当前是否已启用登录自启（systemd user）。"""
    sf = _service_file()
    if not sf.exists():
        return False
    try:
        text = sf.read_text(encoding="utf-8")
        if "ExecStart" not in text:
            return False
        app = os.path.abspath(app_path)
        # 路径或可执行路径在文件内即视为已配置（避免引号/空格差异）
        if app not in text and os.path.basename(app) not in text:
            return False
        if not getattr(sys, "frozen", False) and sys.executable not in text:
            return False
        return True
    except Exception:
        return False


def set_startup_enabled(enabled: bool, app_path: str) -> None:
    """启用或禁用登录自启（systemd --user）。"""
    sf = _service_file()
    if enabled:
        SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
        cmd = _exec_start_command(app_path)
        content = f"""[Unit]
Description=Script Manager
After=graphical-session.target

[Service]
Type=simple
ExecStart={cmd}
Restart=on-failure

[Install]
WantedBy=default.target
"""
        sf.write_text(content, encoding="utf-8")
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
        )
        subprocess.run(
            ["systemctl", "--user", "enable", SERVICE_NAME],
            capture_output=True,
        )
    else:
        subprocess.run(
            ["systemctl", "--user", "disable", SERVICE_NAME],
            capture_output=True,
        )
        try:
            sf.unlink()
        except FileNotFoundError:
            pass


def start_script(
    path: str,
    work_dir: Optional[str],
    script_type: str,
    interpreter: Optional[str],
    log_path: Optional[str],
    app_path: str,
    get_script_type_fn,
    extra_args: Optional[str] = None,
    env_vars: Optional[str] = None,
) -> Optional[subprocess.Popen]:
    """
    在 Linux 上启动脚本。后台 subprocess 运行；不新开终端窗口。
    """
    path = (path or "").strip()
    if not path:
        return None
    if work_dir and not os.path.isabs(path):
        path = os.path.normpath(os.path.join(work_dir, path))
    if not os.path.isfile(path):
        return None
    cwd = (work_dir or os.path.dirname(path)) if work_dir else os.path.dirname(path)
    if not os.path.isdir(cwd):
        cwd = None
    abs_path = os.path.abspath(path)
    if os.path.normcase(abs_path) == os.path.normcase(os.path.abspath(app_path)):
        return None

    script_type = script_type or (get_script_type_fn(path) if get_script_type_fn else "shell")
    use_log = bool(log_path)

    if script_type == "python":
        exe = (interpreter or "").strip() or "python3"
        if exe and (" " in exe or exe.startswith("py ") or exe.startswith("python ")):
            parts = exe.split()
            args = parts + [abs_path]
        else:
            args = [exe, abs_path]
    elif script_type == "node":
        exe = (interpreter or "").strip() or "node"
        if exe and (" " in exe or exe.startswith("node ")):
            parts = exe.split()
            args = parts + [abs_path]
        else:
            args = [exe, abs_path]
    elif script_type in ("shell", "batch", "unknown"):
        exe = (interpreter or "").strip() or "bash"
        args = [exe, abs_path]
    elif script_type == "powershell":
        args = ["pwsh", "-File", abs_path] if _which("pwsh") else ["bash", abs_path]
    elif script_type == "exe":
        args = [abs_path]
    else:
        args = ["bash", abs_path]

    if extra_args and extra_args.strip():
        args.extend(extra_args.strip().split())
    proc_env = _proc_env_from_vars(env_vars)

    if use_log:
        try:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            lf = open(log_path, "w", encoding="utf-8", errors="replace")
            p = subprocess.Popen(
                args,
                cwd=cwd or None,
                stdout=lf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=proc_env,
            )
            lf.close()
            return p
        except Exception:
            return None
    try:
        p = subprocess.Popen(
            args,
            cwd=cwd or None,
            stdin=subprocess.DEVNULL,
            env=proc_env,
        )
        return p
    except Exception:
        return None


def _which(cmd: str) -> Optional[str]:
    import shutil
    return shutil.which(cmd)


# 供 main 判断「是否支持开机自启」：Linux 支持 systemd user
STARTUP_SUPPORTED = True
