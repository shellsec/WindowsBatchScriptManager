# -*- coding: utf-8 -*-
"""
macOS 平台实现：配置路径、开机自启（launchd）、脚本启动。
"""
import os
import sys
import subprocess
from pathlib import Path
from typing import Optional

STARTUP_LABEL = "com.scriptmanager.app"


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
    app = Path(app_path).resolve()
    if getattr(sys, "frozen", False) or ".app" in str(app).lower():
        return Path.home() / "Library" / "Application Support" / "ScriptManager"
    return app.parent


def get_startup_command(app_path: str) -> str:
    """返回用于登录自启的命令（供 main 显示用）。frozen 时为可执行路径，否则为 python + 脚本。"""
    app = os.path.abspath(app_path)
    if getattr(sys, "frozen", False):
        return app
    return f'"{sys.executable}" "{app}"'


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{STARTUP_LABEL}.plist"


def _program_arguments(app_path: str) -> list:
    """launchd ProgramArguments 数组。"""
    app = os.path.abspath(app_path)
    if getattr(sys, "frozen", False):
        return [app]
    return [sys.executable, app]


def is_startup_enabled(app_path: str) -> bool:
    """当前是否已启用登录自启。"""
    plist = _plist_path()
    if not plist.exists():
        return False
    try:
        import plistlib
        with open(plist, "rb") as f:
            data = plistlib.load(f)
        args = (data.get("ProgramArguments") or [])
        if not args:
            return False
        want_args = _program_arguments(app_path)
        if len(args) != len(want_args):
            return False
        return all(os.path.normcase(str(a)) == os.path.normcase(str(b)) for a, b in zip(args, want_args))
    except Exception:
        return False


def set_startup_enabled(enabled: bool, app_path: str) -> None:
    """启用或禁用登录自启（launchd 用户级 LaunchAgent）。"""
    plist = _plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    if enabled:
        # 若已加载则先 unload，再写 plist 后 load，确保使用最新配置
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        import plistlib
        plist_data = {
            "Label": STARTUP_LABEL,
            "ProgramArguments": _program_arguments(app_path),
            "RunAtLoad": True,
        }
        with open(plist, "wb") as f:
            plistlib.dump(plist_data, f)
        subprocess.run(["launchctl", "load", str(plist)], capture_output=True)
    else:
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        try:
            plist.unlink()
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
    在 macOS 上启动脚本。不新开终端窗口，在后台运行；若需新开终端可改为 open -a Terminal。
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
        # .sh 或无扩展名用 bash
        exe = (interpreter or "").strip() or "bash"
        args = [exe, abs_path]
    elif script_type == "powershell":
        # macOS 上若有 pwsh
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


# 供 main 判断「是否支持开机自启」：mac 支持
STARTUP_SUPPORTED = True
