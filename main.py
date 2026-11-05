# -*- coding: utf-8 -*-
"""
Windows Batch Script Manager - Windows 批处理脚本管理器
统一管理 Windows 下的批处理脚本（.bat/.cmd）及 Python、Node.js、PowerShell 等脚本，支持启动、关闭、重启、端口与进程查看、定时任务等。
"""

import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import tkinter as tk
import calendar
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import ttk, messagebox, filedialog
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import urllib.parse
from html.parser import HTMLParser

from version import __version__
from task_features import FEATURE_FIELDS, migrate_items, parse_codes, link_ids, validate_links, environment_vars, merge_task_items
from task_feature_ui import TaskFeatureMixin
from ui_language import (LANGUAGE_OPTIONS, detect_system_language, load_ui_catalogs,
                         normalize_language_preference, translate_inline)


class ToolTip:
    """简单的悬浮提示，鼠标悬停显示文本。"""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event=None):
        if self.tip_window:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 2
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(tw, text=self.text, background="#ffffe0", relief=tk.SOLID,
                       borderwidth=1, font=("Microsoft YaHei UI", 9), padx=4, pady=2)
        lbl.pack()

    def _hide(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None

# 尝试导入 psutil，用于端口与进程信息；无则降级为 netstat/tasklist
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# 托盘：Windows 用 ctypes+Shell_NotifyIcon（无 Pillow）；非 Windows 不启用
HAS_TRAY = sys.platform == "win32"

# start_script 最近一次失败原因（供状态栏展示）
_LAST_START_ERROR = ""


def get_last_start_error() -> str:
    return _LAST_START_ERROR


def _set_start_error(msg: str = "") -> None:
    global _LAST_START_ERROR
    _LAST_START_ERROR = (msg or "").strip()


def diagnose_start_item(it: dict) -> str:
    """生成启动失败诊断文本（无新依赖）。"""
    import shutil
    lines = []
    path = (it.get("path") or "").strip()
    name = it.get("name") or ""
    stype = it.get("script_type") or (get_script_type(path) if path else "") or ""
    wd = effective_work_dir(it) if it else None
    lines.append("name: %s" % name)
    lines.append("type: %s" % stype)
    lines.append("path: %s" % (path or "(empty)"))
    if get_last_start_error():
        lines.append("last_error: %s" % get_last_start_error())
    if stype in ("http_get", "process", "command") or is_system_power_task(it):
        lines.append("note: special task type (no local script file)")
        return "\n".join(lines)
    resolved = path
    if wd and path and not os.path.isabs(path):
        resolved = os.path.normpath(os.path.join(wd, path))
    if not path:
        lines.append("issue: empty path")
    elif not os.path.isfile(resolved):
        lines.append("issue: file not found")
        lines.append("resolved: %s" % resolved)
    else:
        lines.append("resolved: %s" % os.path.abspath(resolved))
        if sys.platform != "win32" and not os.access(resolved, os.R_OK):
            lines.append("issue: no read permission")
    if wd:
        lines.append("work_dir: %s%s" % (wd, "" if os.path.isdir(wd) else " (MISSING)"))
    else:
        lines.append("work_dir: (default)")
    interp = (it.get("interpreter") or it.get("python_exe") or "").strip()
    if stype == "python":
        if interp:
            first = interp.split()[0]
            found = shutil.which(first) if not os.path.isfile(first) else first
            lines.append("interpreter: %s -> %s" % (interp, found or "NOT FOUND"))
        elif getattr(sys, "frozen", False):
            py = shutil.which("python") or shutil.which("python3") or shutil.which("py")
            lines.append("interpreter: (auto) -> %s" % (py or "NOT FOUND"))
        else:
            lines.append("interpreter: %s" % sys.executable)
    elif stype == "node":
        exe = interp or "node"
        lines.append("interpreter: %s -> %s" % (exe, shutil.which(exe.split()[0]) or (exe if os.path.isfile(exe) else "NOT FOUND")))
    elif stype == "shell":
        exe = interp or "bash"
        lines.append("interpreter: %s -> %s" % (exe, shutil.which(exe.split()[0]) or (exe if os.path.isfile(exe) else "NOT FOUND")))
    return "\n".join(lines)

# 可选：Windows 下 windnd 作为拖放后备（pip install windnd）；exe 用 ctypes 原生 WM_DROPFILES，不依赖该库
try:
    if sys.platform == "win32":
        import windnd
        HAS_WINDND = True
    else:
        HAS_WINDND = False
except Exception:
    HAS_WINDND = False

WM_DROPFILES = 0x0233
GWL_WNDPROC = -4


def _win_toplevel_hwnd(widget) -> int:
    """Tk 控件对应的顶层 HWND（托盘图标挂在这层）。"""
    import ctypes
    hwnd = int(widget.winfo_id())
    user32 = ctypes.windll.user32
    parent = user32.GetParent(hwnd)
    while parent:
        hwnd = parent
        parent = user32.GetParent(hwnd)
    return hwnd


def _win_user32_wndproc_api():
    """64 位安全的窗口过程 API。不设 argtypes 时指针会被截成 32 位，窗口随即闪退。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    HWND = wintypes.HWND
    UINT = wintypes.UINT
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        LRESULT = ctypes.c_int64
        WPARAM = ctypes.c_uint64
        LPARAM = ctypes.c_int64
        set_long = user32.SetWindowLongPtrW
        get_long = user32.GetWindowLongPtrW
    else:
        LRESULT = ctypes.c_long
        WPARAM = ctypes.c_uint
        LPARAM = ctypes.c_long
        set_long = user32.SetWindowLongW
        get_long = user32.GetWindowLongW
    set_long.argtypes = [HWND, ctypes.c_int, ctypes.c_void_p]
    set_long.restype = ctypes.c_void_p
    get_long.argtypes = [HWND, ctypes.c_int]
    get_long.restype = ctypes.c_void_p
    call = user32.CallWindowProcW
    call.argtypes = [ctypes.c_void_p, HWND, UINT, WPARAM, LPARAM]
    call.restype = LRESULT
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)
    return WNDPROC, set_long, get_long, call


def _win_hdrop_paths(hdrop) -> list:
    """从 HDROP 取出文件路径列表。"""
    import ctypes
    from ctypes import wintypes
    shell32 = ctypes.windll.shell32
    dq = shell32.DragQueryFileW
    dq.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_wchar_p, wintypes.UINT]
    dq.restype = wintypes.UINT
    shell32.DragFinish.argtypes = [ctypes.c_void_p]
    hd = ctypes.c_void_p(int(hdrop) if hdrop else 0)
    count = dq(hd, 0xFFFFFFFF, None, 0)
    out = []
    buf = ctypes.create_unicode_buffer(32768)
    for i in range(int(count or 0)):
        n = dq(hd, i, buf, len(buf))
        if n:
            out.append(buf.value)
    shell32.DragFinish(hd)
    return out

# 获取实际程序路径（支持 PyInstaller 打包）
def get_app_path():
    """获取应用程序的实际路径（.py 或 .exe）。"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包的 exe
        return os.path.abspath(sys.executable)
    else:
        # 开发环境中的 .py 文件
        return os.path.abspath(__file__)


# 跨平台：Mac/Linux 使用 macBatchScriptManager/、linuxBatchScriptManager/ 下的 platform_impl
_PLATFORM = None
if sys.platform == "darwin":
    try:
        from macBatchScriptManager import platform_impl as _PLATFORM  # noqa: F401
    except ImportError:
        pass
elif sys.platform == "linux":
    try:
        from linuxBatchScriptManager import platform_impl as _PLATFORM  # noqa: F401
    except ImportError:
        pass

# 脚本列表配置文件名（避免与通用的 config.json 与其它工具冲突）
SCRIPT_LIST_CONFIG_FILENAME = "WindowsBatchScriptManager_scripts.json"

if _PLATFORM:
    _config_dir = Path(_PLATFORM.get_config_dir(get_app_path()))
    _config_dir.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH = _config_dir / SCRIPT_LIST_CONFIG_FILENAME
    HISTORY_PATH = _config_dir / "execution_history.json"
    NOTIFICATION_CONFIG_PATH = _config_dir / "notification_config.json"
    LANGUAGE_CONFIG_PATH = _config_dir / "language_config.json"
    API_CONFIG_PATH = _config_dir / "api_config.json"
    APP_CONFIG_PATH = _config_dir / "app_config.json"
else:
    CONFIG_PATH = Path(get_app_path()).parent / SCRIPT_LIST_CONFIG_FILENAME
    HISTORY_PATH = Path(get_app_path()).parent / "execution_history.json"
    NOTIFICATION_CONFIG_PATH = Path(get_app_path()).parent / "notification_config.json"
    LANGUAGE_CONFIG_PATH = Path(get_app_path()).parent / "language_config.json"
    API_CONFIG_PATH = Path(get_app_path()).parent / "api_config.json"
    APP_CONFIG_PATH = Path(get_app_path()).parent / "app_config.json"

STARTUP_REG_NAME = "WindowsBatchScriptManager"


def _get_startup_command() -> str:
    """返回要写入「开机/登录自启」的命令。"""
    app = get_app_path()
    if _PLATFORM:
        cmd = _PLATFORM.get_startup_command(app)
    elif getattr(sys, "frozen", False):
        cmd = app
    else:
        cmd = f'"{sys.executable}" "{app}"'
    if load_app_config().get("start_minimized"):
        cmd = cmd.rstrip() + " --minimized"
    return cmd


def _is_startup_enabled() -> bool:
    """当前是否已设置开机/登录自启。"""
    if _PLATFORM:
        return _PLATFORM.is_startup_enabled(get_app_path())
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        )
        try:
            val, _ = winreg.QueryValueEx(key, STARTUP_REG_NAME)
            # 路径大小写可能不同，用 normcase 统一比较
            want = _get_startup_command().strip()
            got = (val or "").strip()
            return os.path.normcase(got) == os.path.normcase(want)
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


def _set_startup_enabled(enabled: bool) -> None:
    """设置或取消开机/登录自启。"""
    if _PLATFORM:
        _PLATFORM.set_startup_enabled(enabled, get_app_path())
        return
    if sys.platform != "win32":
        return
    try:
        import winreg
        path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        if enabled:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                path,
                0,
                winreg.KEY_SET_VALUE,
            )
            try:
                winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ, _get_startup_command())
            finally:
                winreg.CloseKey(key)
        else:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                path,
                0,
                winreg.KEY_SET_VALUE,
            )
            try:
                winreg.DeleteValue(key, STARTUP_REG_NAME)
            except (FileNotFoundError, OSError):
                pass
            finally:
                winreg.CloseKey(key)
    except Exception:
        pass

# 语言字典
LANGUAGES = {
    "zh-CN": {
        # 主窗口
        "window_title": "Windows Batch Script Manager - Windows 批处理脚本管理器",
        "btn_add": "添加",
        "btn_edit": "编辑",
        "btn_delete": "删除",
        "btn_duplicate": "复制",
        "btn_select_all": "全选",
        "btn_select_none": "取消全选",
        "btn_start": "启动",
        "btn_stop": "停止",
        "btn_restart": "重启任务",
        "btn_start_all": "全部启动",
        "btn_stop_all": "全部停止",
        "btn_start_stopped": "其他停止都启动",
        "btn_stop_running": "其他启动都停止",
        "btn_scan_running": "自动识别",
        "btn_scan_and_add": "识别并加入",
        "btn_scan_exe_add": "识别exe加入",
        "btn_refresh": "刷新状态",
        "btn_pause_refresh": "暂停刷新",
        "btn_resume_refresh": "恢复刷新",
        "btn_ports": "端口/进程",
        "btn_visit": "访问",
        "btn_export": "导出配置",
        "btn_import": "导入配置",
        "btn_history": "执行历史",
        "btn_batch_edit": "批量编辑",
        "btn_clear_invalid": "清除无效项",
        "btn_notification": "通知配置",
        "btn_stop_all_batch": "停止软件外所有批处理",
        "btn_open_tool_folder": "打开工具目录",
        "msg_clear_invalid_confirm": "发现 %d 个路径失效或无效的项（脚本不存在或路径为空），是否一键删除？",
        "msg_no_invalid": "未发现路径失效或无效的项。",
        "status_cleared_invalid": "已清除 %d 个无效项",
        "status_drop_added": "已通过拖放添加 %d 个文件",
        "label_drop_zone": "拖拽处",
        "hint_drop_zone": "将脚本拖入本窗口任意处即可添加",
        "label_batch_ops": "批量:",
        "label_filter_group": "筛选分组:",
        "btn_clear_filter": "清除筛选",
        "label_search_keyword": "关键词:",
        "btn_locate": "定位",
        "btn_next_match": "下一个",
        "hint_search_keyword": "匹配名称、路径、工作目录、分组；空格分隔多个词；Esc 清空",
        "msg_search_no_match": "未找到包含「%s」的项",
        "msg_search_located": "已定位到第 %d 项",
        "msg_search_filtered": "匹配 %d / %d",
        "label_local_ip": "本地IP:",
        "label_public_ip": "外网IP:",
        "hint_click_to_copy": "(点击复制)",
        "hint_loading": "(获取中...)",
        "msg_ip_copied": "已复制IP地址: %s",
        "msg_ip_unavailable": "无法获取IP地址",
        "label_selected": "选中项:",
        "status_ready": "就绪",
        # 菜单
        "menu_file": "文件",
        "menu_home": "首页",
        "menu_language": "语言",
        "menu_startup": "开机启动",
        "menu_startup_enable": "启用",
        "menu_startup_disable": "禁用",
        "menu_language_zh": "中文",
        "menu_language_en": "English",
        "menu_help": "帮助",
        "menu_about": "关于",
        "msg_about": "Windows Batch Script Manager\n版本: %s",
        "tray_show": "显示主窗口",
        "tray_quit": "退出",
        # 消息
        "msg_select_first": "请先选中一项",
        "msg_select_items": "请先选中一项或多项",
        "msg_delete_confirm": "确认删除",
        "msg_delete_warning": "以下项正在运行，删除后进程将继续运行，仅从管理器中移除：",
        "msg_continue": "是否继续？",
        # 列标题
        "col_id": "ID",
        "col_name": "名称",
        "col_group": "分组",
        "col_path": "脚本路径",
        "col_work_dir": "工作目录",
        "col_type": "类型",
        "col_status": "状态",
        "col_pid": "PID",
        "col_port": "端口",
        "col_title": "标题",
        "col_schedule": "定时",
        "col_launch_on": "自动启动",
        "col_auto_restart": "崩溃重启",
        "cell_yes": "是",
        "cell_no": "否",
        # 详情和状态
        "detail_label": "选中项：路径与工作目录",
        "status_ready_msg": "就绪 | 「自动识别」或「识别并加入」可识别系统运行中的脚本；双击编辑；Ctrl+A 全选",
        # 右键菜单
        "menu_start": "启动",
        "menu_stop": "停止",
        "menu_restart": "重启任务",
        "menu_edit": "编辑",
        "menu_delete": "删除",
        "menu_duplicate": "复制",
        "menu_open_file": "打开文件",
        "menu_open_dir_work": "打开目录（工作目录）",
        "menu_open_dir_script": "打开目录（脚本目录）",
        "menu_select_all": "全选",
        "menu_deselect_all": "取消全选",
        "menu_ports": "端口/进程",
        "menu_visit": "访问 :%s",
        # 对话框标题
        "dialog_scan_running": "自动识别：系统当前运行中的脚本进程",
        "dialog_scan_exe": "识别exe：运行中的可执行文件",
        "msg_scan_exe_no_processes_detail": "当前未识别到运行中的 .exe 进程",
        "dialog_add_edit": "添加脚本",
        "dialog_edit": "编辑脚本",
        "dialog_history": "执行历史",
        "dialog_batch_edit": "批量编辑",
        "dialog_notification": "异常通知配置",
        "dialog_ports": "端口/进程",
        # 对话框按钮和标签
        "btn_refresh_dialog": "刷新",
        "btn_add_all": "全部加入",
        "btn_add_selected": "加入选中",
        "btn_kill_selected": "结束选中",
        "btn_ok": "确定",
        "btn_cancel": "取消",
        "btn_test": "测试",
        "btn_clear_history": "清除历史",
        "btn_export_csv": "导出 CSV",
        "status_history_exported": "历史已导出: %s",
        "menu_close_to_tray": "关闭窗口时最小化到托盘",
        "msg_close_choose": "是否退出程序？\n\n是 = 退出\n否 = 最小化到托盘\n取消 = 继续运行",
        "msg_win_only_script": "当前系统非 Windows，.bat/.cmd/.vbs 可能无法运行，仍要添加吗？",
        "menu_restore_bak": "从上次备份恢复配置",
        "msg_restore_bak_confirm": "用 .bak 覆盖当前脚本列表？当前配置会先再备份一次。",
        "msg_restore_bak_ok": "已从备份恢复",
        "msg_restore_bak_missing": "未找到配置备份文件（.bak）",
        "btn_schedule_overview": "定时总览",
        "dialog_schedule_overview": "定时任务总览",
        "col_next_run": "下次执行",
        "col_schedule_type": "类型",
        "label_date_from": "从:",
        "label_date_to": "到:",
        "hint_date_filter": "YYYY-MM-DD，留空=不限",
        "menu_diagnose": "诊断启动问题",
        "menu_copy_diagnose": "复制诊断信息",
        "tray_stop_all": "全部停止",
        "label_scan_result": "识别结果：",
        "label_script_path": "脚本路径:",
        "label_name": "名称:",
        "label_group": "分组:",
        "label_work_dir": "工作目录:",
        "label_interpreter": "解释器:",
        "label_task_type": "任务类型:",
        "label_request_url": "请求 URL:",
        "label_http_method": "请求方法:",
        "label_http_headers": "请求头:",
        "label_process_name": "进程名称:",
        "label_command": "命令:",
        "hint_request_url": "输入 http:// 或 https:// 地址",
        "hint_http_headers": "(每行 Header: Value，如 X-API-Key: change-me-in-production)",
        "btn_fill_local_api": "填入本机API",
        "hint_process_name": "输入进程名，如 explorer.exe",
        "hint_command": "直接输入命令，如 ping 8.8.8.8、ipconfig、python -c \"print('hello')\"",
        "label_auto_restart": "保活:",
        "label_log_output": "日志输出:",
        "label_schedule": "定时任务:",
        "tab_basic": "基本",
        "tab_schedule": "定时",
        "label_schedule_time": "时间:",
        "btn_time_0": "0点",
        "btn_time_3": "3点",
        "btn_time_6": "6点",
        "btn_time_9": "9点",
        "btn_time_12": "12点",
        "btn_time_15": "15点",
        "btn_time_18": "18点",
        "btn_time_21": "21点",
        "btn_time_now1": "+1分",
        "label_schedule_action": "动作:",
        "label_schedule_enabled": "启用:",
        "label_schedule_valid_from": "生效日期:",
        "label_schedule_valid_until": "失效日期:",
        "hint_schedule_valid": "(留空=不限制，格式: YYYY-MM-DD)",
        "hint_expired": "已过期",
        "label_schedule_max_runs": "最大执行次数:",
        "hint_schedule_max_runs": "(0=不限制)",
        "label_schedule_pre_delay": "执行前延迟(秒):",
        "hint_schedule_pre_delay": "(0=不延迟)",
        "label_env_vars": "环境变量:",
        "hint_env_vars": "(每行 KEY=VALUE，如 PORT=8080)",
        "label_startup_timeout": "启动超时(秒):",
        "hint_startup_timeout": "(0=不检测；脚本启动后超过此时间未退出则视为成功)",
        "label_basic_settings": "基本设置",
        "label_webhook_settings": "Webhook 通知（可选）",
        "label_webhook_url": "Webhook URL:",
        "label_webhook_method": "请求方法:",
        "label_webhook_enabled": "启用 Webhook 通知",
        "label_webhook_send_data": "发送数据（script_name, reason 等）",
        "label_quick_exit_threshold": "快速退出阈值（秒）:",
        "label_enable_notification": "启用异常通知",
        "label_notify_nonzero": "退出码非0时通知",
        "label_notify_quick": "快速退出时通知",
        "label_schedule_debug_log": "定时任务执行日志",
        "label_select_script": "选择脚本:",
        "label_batch_group": "分组:",
        "label_batch_interpreter": "解释器:",
        "label_batch_args": "启动参数:",
        "label_batch_schedule": "定时任务:",
        "label_batch_schedule_detail": "定时内容（可选，启用时应用）:",
        "hint_batch_schedule_detail": "类型/时间/星期/动作 留空或「不修改」则不改该项",
        "label_batch_keepalive": "保活:",
        "label_batch_launch_on_start": "启动时自动启动:",
        "label_batch_log": "日志输出:",
        "hint_batch_edit": "提示：留空的字段不会修改，只修改勾选的选项",
        "hint_same_as_script": "(同脚本目录)",
        "hint_no_change": "不修改",
        "hint_enable": "启用",
        "hint_disable": "禁用",
        "hint_group_empty": "(留空=无分组；如：项目A、监控、API等)",
        "hint_python_interpreter": "(留空=当前；可填 py -3.11 或解释器路径)",
        "hint_node_interpreter": "(留空=node；可填 node、node.exe 或路径)",
        "hint_shell_interpreter": "(留空=bash；可填 bash、sh 或路径)",
        "hint_time_format": "(格式: HH:MM，如 09:00)",
        "hint_interval_minutes": "分钟 (仅用于「每分钟」)",
        "btn_browse": "浏览",
        "btn_add_manual": "添加(手动)",
        "btn_auto_detect": "自动识别",
        "default_name": "未命名",
        "menu_copy_path": "复制路径",
        "menu_copy_pid": "复制PID",
        "menu_copy_name": "复制名称",
        "menu_start_group": "启动此分组",
        "menu_stop_group": "停止此分组",
        "menu_auto_start_on_boot": "开机自启",
        "menu_remove_auto_start": "取消开机自启",
        "msg_auto_start_enabled": "已启用开机自启",
        "msg_auto_start_disabled": "已取消开机自启",
        "msg_auto_start_failed": "设置开机自启失败: %s",
        "menu_view_log": "查看日志",
        "menu_log_not_found": "未找到日志文件",
        "title_log_viewer": "日志查看器 - %s",
        "menu_batch_edit": "批量编辑",
        "menu_save_template": "保存为模板",
        "menu_apply_template": "从模板添加",
        "menu_manage_templates": "管理模板",
        "msg_template_saved": "模板「%s」已保存",
        "msg_template_deleted": "模板「%s」已删除",
        "title_template_manager": "模板管理",
        "label_template_name": "模板名称:",
        "menu_move_up": "上移 (Alt+↑)",
        "menu_move_down": "下移 (Alt+↓)",
        "status_crash_loop": "频繁崩溃",
        "msg_crash_loop_notify": "任务「%s」频繁崩溃（%d次/分钟），已自动停止保活",
        "menu_tail_output": "实时输出",
        "title_live_output": "实时输出 - %s",
        "btn_close": "关闭",
        "btn_clear": "清空",
        "label_interpreter_python": "Python 解释器:",
        "label_interpreter_node": "Node.js 解释器:",
        "label_interpreter_shell": "Shell 解释器:",
        "label_schedule_type": "类型:",
        "label_schedule_interval": "间隔:",
        "label_schedule_weekday": "星期:",
        "label_type_prefix": "类型: ",
        "check_log_output": "启动时记录输出到 logs/（便于排查退出原因）",
        "check_auto_restart": "崩溃后自动重启(保活)",
        "check_schedule_enabled": "启用定时任务",
        "schedule_action_start": "启动",
        "schedule_action_restart": "重启任务",
        "schedule_action_stop": "停止",
        "task_type_system_shutdown": "系统关机",
        "task_type_system_reboot": "系统重启",
        "hint_system_power_task": "（无需路径；手动「启动」或定时「启动」时执行）",
        "schedule_action_shutdown": "关机",
        "schedule_action_reboot": "重启系统",
        "schedule_type_daily": "每天",
        "schedule_type_hourly": "每小时",
        "schedule_type_minutely": "每分钟",
        "schedule_type_weekly": "每周",
        "schedule_type_monthly": "每月",
        "schedule_type_every_hours": "每N小时执行",
        "schedule_type_quarterly": "每季度",
        "schedule_type_halfyearly": "每半年",
        "schedule_type_yearly": "每年",
        "schedule_type_once": "单次执行",
        "schedule_type_weekend": "每周末(6-7)",
        "schedule_type_weekday": "工作日(1-5)",
        "label_schedule_once_date": "执行日期:",
        "hint_schedule_once_date": "(格式: YYYY-MM-DD，留空=今天)",
        "hint_schedule_once_auto": "执行后自动禁用该定时任务",
        "label_schedule_month": "月份:",
        "label_schedule_quarter": "季度:",
        "schedule_quarter_q1": "第一季度(1月)",
        "schedule_quarter_q2": "第二季度(4月)",
        "schedule_quarter_q3": "第三季度(7月)",
        "schedule_quarter_q4": "第四季度(10月)",
        "label_schedule_interval_minutes": "间隔(分钟):",
        "label_schedule_interval_hours": "间隔(小时):",
        "hint_interval_hours_repeat": "循环执行",
        "label_schedule_day": "几号:",
        "schedule_day_last": "月末",
        "weekday_mon": "周一",
        "weekday_tue": "周二",
        "weekday_wed": "周三",
        "weekday_thu": "周四",
        "weekday_fri": "周五",
        "weekday_sat": "周六",
        "weekday_sun": "周日",
        "dialog_select_script": "选择脚本",
        "dialog_select_work_dir": "选择工作目录",
        "dialog_export_config": "导出配置",
        "dialog_import_config": "导入配置",
        "label_batch_selected": "已选中 %d 项，可批量修改以下属性：",
        "label_batch_edit_title": "批量修改",
        "col_start_time": "启动时间",
        "col_stop_time": "停止时间",
        "col_duration": "运行时长",
        "col_exit_code": "退出码",
        "col_status": "状态",
        "label_open_in_browser": "在浏览器打开:",
        "stat_total": "总计: %d 次",
        "stat_success": "成功: %d 次",
        "stat_failed": "失败: %d 次",
        "stat_avg_duration": "平均时长: %.1f 秒",
        "msg_scan_need_psutil_detail": "需要安装 psutil 才能扫描 (pip install psutil)",
        "msg_scan_no_processes_detail": "当前系统未识别到正在运行的 .bat / .cmd / .py 进程（可从任务栏、资源管理器打开的窗口）",
        "msg_scan_added_from": "已从「自动识别」加入 %d 项",
        "msg_scan_added": "已加入 %d 项",
        "msg_scan_skipped_detail": "，跳过 %d 项（路径已存在）",
        "msg_scan_prefix": "自动识别 · ",
        "msg_opened_in_editor": "已在编辑器中打开: %s",
        "msg_opened_file": "已打开文件: %s",
        "msg_opened_dir": "已打开目录: %s",
        "msg_opened_url": "已打开 %s",
        "msg_opened_url_ports": "已打开 %s（共 %d 个端口，可在右键菜单访问其他）",
        "msg_scan_confirm_title": "自动识别",
        "msg_stopped_count_detail": "已停止 %d 个：%s",
        "msg_stopped_failed_detail": "\n\n以下 %d 个停止失败：\n%s",
        # 消息框
        "msg_info": "提示",
        "msg_error": "错误",
        "msg_success": "成功",
        "msg_failed": "失败",
        "msg_confirm": "确认",
        "msg_confirm_delete": "确认删除",
        "msg_complete": "完成",
        "msg_select_first": "请先选中一项",
        "msg_select_items": "请先选中一项或多项",
        "msg_select_script": "请先选择一个脚本",
        "msg_select_table_rows": "请先勾选表格中要加入的行（有类型、路径、PID 的批处理或 Python 行）",
        "msg_no_processes": "当前没有识别到可加入的进程，请先点「刷新」再试。",
        "msg_no_items_added": "未加入任何项。请勾选表格中「类型」为批处理或 Python 的行（排除顶部的提示行），再点「加入选中」。",
        "msg_no_processes_refresh": "当前没有可加入的进程，请先点「刷新」再试。",
        "msg_added_scripts": "已将 %d 个运行中的脚本加入管理器。%s",
        "msg_skipped_paths": "\n跳过 %d 个已存在的路径。",
        "msg_enter_script_path": "请输入或选择脚本路径",
        "msg_script_not_exists": "脚本文件不存在: %s",
        "msg_clear_history_confirm": "确定要清除「%s」的所有历史记录吗？",
        "msg_enter_webhook_url": "请先输入 Webhook URL",
        "msg_webhook_test_success": "Webhook 测试成功！",
        "msg_webhook_test_failed": "Webhook 测试失败：\n%s",
        "msg_delete_confirm": "%s\n\n是否继续？",
        "msg_script_running": "「%s」已在运行",
        "msg_started": "已启动: %s (PID=%d)",
        "msg_start_failed": "启动失败: %s",
        "msg_stopped": "已停止: %s",
        "msg_start_all": "全部启动: 已启动 %d 项",
        "msg_stop_all": "全部停止: 已停止 %d 项",
        "msg_stopped_external": "已停止 %d 个脚本，%d 个失败",
        "msg_stopped_external_names": "已停止 %d 个脚本：%s",
        "msg_copied": "已复制 %d 项",
        "msg_file_not_exists": "文件不存在: %s",
        "msg_cannot_open_file": "无法打开文件：\n%s",
        "msg_dir_not_exists": "目录不存在: %s",
        "msg_cannot_open_dir": "无法打开目录：\n%s",
        "msg_select_one": "请先选中一项。",
        "msg_not_running_visit": "「%s」未在运行，无法访问端口。",
        "msg_not_running_ports": "「%s」未在运行，无法查看端口与进程。",
        "msg_no_port_detected": "「%s」未检测到监听端口。\n\n「访问」仅对会监听端口的服务（如 Web、API）有效；若为定时任务、爬虫等不开放端口的脚本，可忽略。\n\n请选中表格「端口」列有数字的项（如 app :5001）再点「访问」。",
        "label_listening_ports": "监听端口: ",
        "label_no_ports": "无",
        "label_process_info": "【%s】 PID=%d",
        "label_cpu": "CPU",
        "label_memory": "内存",
        "label_mb": "MB",
        "label_process_detail": "  PID %d: %s - %s",
        "label_process_detail_no_info": "  PID %d: (需 psutil 查看详情)",
        "msg_export_success": "配置已保存到：\n%s",
        "msg_export_failed": "导出配置时出错：\n%s",
        "msg_import_failed_format": "配置文件格式错误：应为数组",
        "msg_import_empty": "配置文件为空",
        "msg_import_complete": "%s\n跳过项：路径已在管理器中",
        "msg_import_json_error": "配置文件 JSON 格式错误：\n%s",
        "msg_import_error": "导入配置时出错：\n%s",
        "msg_export_to": "配置已导出到: %s",
        "msg_import_way": "导入方式",
        "msg_import_found": "发现 %d 条配置。\n\n",
        "msg_import_merge": "「是」= 合并（保留现有 + 添加新的）\n",
        "msg_import_replace": "「否」= 替换（清空现有 + 导入新的）\n",
        "msg_import_cancel": "「取消」= 取消导入",
        "msg_import_complete_title": "导入完成",
        "msg_imported": "已导入 %d 项",
        "msg_import_skipped_detail": "，跳过 %d 项（路径已存在）",
        "status_import_prefix": "导入配置 · ",
        "status_running": "运行中",
        "status_stopped": "已停止",
        "status_path_invalid": "路径无效",
        "status_already_stopped": "所选项已处于停止状态",
        "status_config_save_failed": "配置保存失败，请检查目录权限或磁盘空间",
        "status_added": "已添加: %s",
        "status_updated": "已更新: %s",
        "status_deleted": "已删除选中项",
        "status_script_running": "「%s」已在运行",
        "status_refreshed": "已刷新",
        "status_refreshed_adopted": "已刷新，已接管 %d 个运行中脚本",
        "status_auto_restarted": "已自动重启: %s",
        "status_schedule_triggered_stopped": "定时任务触发: %s (已停止)",
        "status_schedule_triggered": "定时任务触发: %s (%s)",
        "menu_schedule_pause": "暂停所有定时",
        "menu_schedule_resume": "恢复所有定时",
        "status_schedule_paused": "已暂停所有定时任务",
        "status_schedule_resumed": "已恢复所有定时任务",
        "schedule_next_run": "下次约 %s",
        "schedule_next_tomorrow": "明天",
        "status_schedule_max_reached": "定时任务已达最大执行次数: %s",
        "status_schedule_out_of_valid": "定时任务不在有效期内: %s",
        "status_schedule_path_invalid": "定时任务跳过: %s (脚本路径无效)",
        "status_startup_timeout": "「%s」启动超时（%d秒未退出，视为启动成功）",
        "btn_collapse_all": "全部折叠",
        "btn_expand_all": "全部展开",
        "label_group_collapse": "分组折叠",
        "hint_multi_time": "(多个时间用逗号分隔，如 09:00,15:00,21:00)",
        "menu_global_hotkeys": "快捷键说明",
        "msg_hotkeys_info": "全局快捷键：\nCtrl+N = 添加脚本\nCtrl+E = 编辑脚本\nCtrl+D = 删除脚本\nCtrl+R = 刷新状态\nCtrl+S = 启动选中\nCtrl+Q = 停止选中\nCtrl+A = 全选\nF5 = 刷新状态\nF6 = 全部启动\nF7 = 全部停止",
        "status_refresh_resumed": "已恢复定时刷新（%d 秒）",
        "menu_settings": "设置",
        "menu_refresh_interval": "刷新间隔",
        "refresh_interval_2s": "2 秒",
        "refresh_interval_4s": "4 秒",
        "refresh_interval_8s": "8 秒",
        "status_refresh_interval_set": "刷新间隔已设为 %d 秒",
        "menu_start_minimized": "启动后最小化到任务栏",
        "menu_mark_running_launch_on_start": "将当前运行中设为启动时自动启动",
        "dialog_mark_running_launch_on_start": "将当前运行中设为启动时自动启动",
        "label_mark_running_list": "当前运行中的任务（%d 项）：",
        "check_mark_running_clear_stopped": "同时取消未运行任务的「启动时自动启动」",
        "hint_mark_running": "确定后写入配置。下次打开本工具会自动启动这些任务；若要开机就跑，请再打开菜单「开机启动」。",
        "msg_mark_running_none": "当前没有运行中的任务。请先启动要随本工具自动跑的任务，再使用本功能。",
        "status_mark_running_done": "已为 %d 个运行中任务勾选启动时自动启动（新勾选 %d）",
        "status_mark_running_cleared": "，已取消未运行 %d 项",
        "check_launch_on_app_start": "启动本工具时自动启动",
        "status_refresh_paused": "已暂停定时刷新；保活、运行中接管会停止，可点「刷新状态」或「恢复刷新」",
        "status_history_cleared": "已清除历史记录",
        "status_notification_saved": "通知配置已保存",
        "msg_notification_save_failed": "通知配置保存失败，请检查目录权限或路径。",
        "status_batch_modified": "已批量修改 %d 项",
        "status_selected_all": "已全选 %d 项",
        "status_deselected_all": "已取消全选",
        "msg_stop_confirm_detail": "发现 %d 个正在运行的脚本（包括列表中的和列表外的）：\n%s\n\n是否全部停止？",
        "msg_stop_confirm_more": "\n  ... 还有 %d 个",
        "msg_delete_running_detail": "以下 %d 项正在运行，将从管理器中移除（进程将继续运行）：\n%s",
        "msg_delete_running_more": "\n  ... 还有 %d 项",
        "filetype_all": "全部",
        "msg_need_psutil": "此功能需要安装 psutil (pip install psutil)",
        "msg_no_running_scripts": "系统中没有正在运行的脚本（批处理、Python、Node.js、PowerShell等）",
        "msg_confirm_stop": "确认停止",
        "msg_partial_failed": "部分失败",
        "msg_stopped_count": "已停止 %d 个：%s\n\n以下 %d 个停止失败：\n%s",
        "msg_scan_need_psutil": "自动识别需要安装 psutil (pip install psutil)。",
        "msg_scan_no_processes": "当前未识别到运行中的批处理/Python 进程。",
        "msg_scan_confirm": "发现 %d 个运行中的批处理/Python 进程，是否全部加入管理器？",
        "msg_script_abnormal_exit": "脚本异常退出",
        "msg_abnormal_exit": "「%s」异常退出\n%s",
        "msg_exit_code": "退出码: %s",
        "msg_exit_code_detail": "退出码 %s",
        "msg_duration_only": "运行时长仅 %.1f 秒",
        "msg_duration_only_with_comma": "，运行时长仅 %.1f 秒",
        "msg_quick_exit_detail": "「%s」运行约 %.1f 秒后退出，退出码 %s",
        "msg_quick_exit_start": "「%s」启动后约 %.1f 秒退出，退出码 %s",
        "msg_quick_exit_with_log": "「%s」运行约 %.1f 秒后退出，退出码 %s。\n\n最近输出：\n%s",
        "msg_quick_exit_title": "启动后很快退出",
        "msg_log_saved": "；输出已保存到 %s",
        "msg_suggest_cmd": "；建议在 CMD 中直接运行该脚本排查",
        "msg_duration": "运行时长: %.1f 秒",
        # ---- 依赖链 ----
        "label_depends_on": "依赖项(ID):",
        "hint_depends_on": "(逗号分隔的ID，如 1,3；依赖项启动后才启动)",
        "label_delay_after_dep": "依赖延迟(秒):",
        "hint_delay_after_dep": "(依赖项启动后等待秒数，0=不等)",
        "col_depends": "依赖",
        "status_dep_waiting": "等待依赖",
        "status_dep_not_running": "依赖未运行",
        "msg_dep_circular": "检测到循环依赖，无法启动: %s",
        "msg_dep_not_running": "依赖项 %s(%s) 未运行，无法启动「%s」",
        "msg_dep_starting": "正在等待依赖项启动...",
        # ---- 启动参数 ----
        "label_args": "启动参数:",
        "hint_args": "(如 --port 8080 --debug；留空则无)",
        "col_args": "参数",
        # ---- 日志实时查看 ----
        "btn_tail_log": "实时日志",
        "dialog_tail_log": "实时日志 - %s",
        "btn_tail_stop": "停止",
        "btn_tail_clear": "清空",
        # ---- 重启工具 ----
        "btn_restart_self": "重启工具",
        "msg_restart_self_confirm": "确定要重启管理器吗？\n运行中的脚本将继续运行，重启后自动恢复管理。",
        # ---- 系统关机/重启 ----
        "btn_shutdown": "关闭系统",
        "btn_reboot": "重启系统",
        "msg_shutdown_confirm": "确定要关闭 Windows 吗？\n将先停止所有托管脚本，再正常关机。",
        "msg_reboot_confirm": "确定要重启 Windows 吗？\n将先停止所有托管脚本，再正常重启。",
        "status_schedule_triggered_power": "定时任务触发: %s → %s",
        "status_power_action_failed": "系统操作失败: %s",
        # ---- API 服务（独立管理） ----
        "label_api_ops": "API:",
        "btn_start_api": "启动API",
        "btn_stop_api": "停止API",
        "btn_restart_api": "重启API",
        "status_api_running": "运行中 PID=%s",
        "status_api_stopped": "未运行",
        "status_api_missing": "脚本不存在",
        "msg_api_not_found": "未找到 API 脚本:\n%s",
        "msg_api_already_running": "API 已在运行 (PID=%s)",
        "msg_api_started": "API 已启动 PID=%s",
        "msg_api_stopped": "API 已停止",
        "msg_api_restarted": "API 已重启 PID=%s",
        "msg_api_start_failed": "API 启动失败",
        "msg_api_stop_failed": "API 停止失败",
        "check_launch_api_on_start": "启动本工具时自动启动API",
        "btn_load_api_calls": "一键加载API调用",
        "btn_check_api_env": "检测运行环境",
        "group_local_api": "本机API",
        "status_api_call_hint": "API: %s  一键加载可写入HTTP调用任务",
        "msg_api_calls_loaded": "已添加 %d 条，更新 %d 条。\n\nid=1 只是示例，请按实际任务 ID 修改 URL。",
        "api_task_list": "列表",
        "api_task_status": "状态(id=1)",
        "api_task_start": "启动(id=1)",
        "api_task_stop": "停止(id=1)",
        "api_task_restart": "重启(id=1)",
        "api_task_start_all": "全部启动",
        "api_task_stop_all": "全部停止",
        # ---- 系统托盘 ----
        "tray_show": "显示主窗口",
        "tray_quit": "退出",
        "menu_minimize_to_tray": "最小化到托盘",
        # ---- 插件系统 ----
        "menu_plugins": "插件",
        "menu_plugins_dir": "打开插件目录",
        "msg_plugins_loaded": "已加载 %d 个插件",
        "msg_plugins_none": "未发现插件（在 plugins/ 目录放入 .py 文件）",
    },
    "en-US": {
        # Main window
        "window_title": "Windows Batch Script Manager",
        "btn_add": "Add",
        "btn_edit": "Edit",
        "btn_delete": "Delete",
        "btn_duplicate": "Duplicate",
        "btn_select_all": "Select All",
        "btn_select_none": "Deselect All",
        "btn_start": "Start",
        "btn_stop": "Stop",
        "btn_restart": "Restart Task",
        "btn_start_all": "Start All",
        "btn_stop_all": "Stop All",
        "btn_start_stopped": "Start All Stopped",
        "btn_stop_running": "Stop All Running",
        "btn_scan_running": "Scan Running",
        "btn_scan_and_add": "Scan & Add",
        "btn_scan_exe_add": "Scan EXE & Add",
        "btn_refresh": "Refresh",
        "btn_pause_refresh": "Pause Refresh",
        "btn_resume_refresh": "Resume Refresh",
        "btn_ports": "Ports/Processes",
        "btn_visit": "Visit",
        "btn_export": "Export Config",
        "btn_import": "Import Config",
        "btn_history": "History",
        "btn_batch_edit": "Batch Edit",
        "btn_clear_invalid": "Clear Invalid",
        "btn_notification": "Notifications",
        "btn_stop_all_batch": "Stop All Batch",
        "btn_open_tool_folder": "Open tool folder",
        "msg_clear_invalid_confirm": "Found %d items with missing or invalid script path (file not found or path empty). Delete them?",
        "msg_no_invalid": "No invalid items found.",
        "status_cleared_invalid": "Cleared %d invalid items",
        "status_drop_added": "Added %d files via drag and drop",
        "label_drop_zone": "Drop zone",
        "hint_drop_zone": "Drag script files into this window to add",
        "label_batch_ops": "Batch:",
        "label_filter_group": "Filter Group:",
        "btn_clear_filter": "Clear Filter",
        "label_search_keyword": "Keyword:",
        "btn_locate": "Locate",
        "btn_next_match": "Next",
        "hint_search_keyword": "Matches name, path, work dir, group; space-separated words; Esc to clear",
        "msg_search_no_match": "No item containing '%s'",
        "msg_search_located": "Located at item %d",
        "msg_search_filtered": "Matched %d / %d",
        "label_local_ip": "Local IP:",
        "label_public_ip": "Public IP:",
        "hint_click_to_copy": "(Click to copy)",
        "hint_loading": "(Loading...)",
        "msg_ip_copied": "IP address copied: %s",
        "msg_ip_unavailable": "Unable to get IP address",
        "label_selected": "Selected:",
        "status_ready": "Ready",
        # Menu
        "menu_file": "File",
        "menu_home": "Home",
        "menu_language": "Language",
        "menu_startup": "Start with Windows",
        "menu_startup_enable": "Enable",
        "menu_startup_disable": "Disable",
        "menu_language_zh": "中文",
        "menu_language_en": "English",
        "menu_help": "Help",
        "menu_about": "About",
        "msg_about": "Windows Batch Script Manager\nVersion: %s",
        "tray_show": "Show window",
        "tray_quit": "Quit",
        # Messages
        "msg_select_first": "Please select an item first",
        "msg_select_items": "Please select one or more items",
        "msg_delete_confirm": "Confirm Delete",
        "msg_delete_warning": "The following items are running. After deletion, processes will continue running, only removed from manager:",
        "msg_continue": "Continue?",
        # Column headers
        "col_id": "ID",
        "col_name": "Name",
        "col_group": "Group",
        "col_path": "Script Path",
        "col_work_dir": "Work Dir",
        "col_type": "Type",
        "col_status": "Status",
        "col_pid": "PID",
        "col_port": "Port",
        "col_title": "Title",
        "col_schedule": "Schedule",
        "col_launch_on": "Auto Start",
        "col_auto_restart": "Crash Restart",
        "cell_yes": "Y",
        "cell_no": "N",
        # Detail and status
        "detail_label": "Selected: Path & Work Directory",
        "status_ready_msg": "Ready | Use 'Scan Running' or 'Scan & Add' to identify running scripts; Double-click to edit; Ctrl+A to select all",
        # Right-click menu
        "menu_start": "Start",
        "menu_stop": "Stop",
        "menu_restart": "Restart Task",
        "menu_edit": "Edit",
        "menu_delete": "Delete",
        "menu_duplicate": "Duplicate",
        "menu_open_file": "Open File",
        "menu_open_dir_work": "Open Directory (Work Dir)",
        "menu_open_dir_script": "Open Directory (Script Dir)",
        "menu_select_all": "Select All",
        "menu_deselect_all": "Deselect All",
        "menu_ports": "Ports/Processes",
        "menu_visit": "Visit :%s",
        # Dialog titles
        "dialog_scan_running": "Scan Running: System Script Processes",
        "dialog_scan_exe": "Scan EXE: Running Executables",
        "msg_scan_exe_no_processes_detail": "No running .exe processes detected",
        "dialog_add_edit": "Add Script",
        "dialog_edit": "Edit Script",
        "dialog_history": "Execution History",
        "dialog_batch_edit": "Batch Edit",
        "dialog_notification": "Exception Notification Config",
        "dialog_ports": "Ports/Processes",
        # Dialog buttons and labels
        "btn_refresh_dialog": "Refresh",
        "btn_add_all": "Add All",
        "btn_add_selected": "Add Selected",
        "btn_kill_selected": "Kill Selected",
        "btn_ok": "OK",
        "btn_cancel": "Cancel",
        "btn_test": "Test",
        "btn_clear_history": "Clear History",
        "btn_export_csv": "Export CSV",
        "status_history_exported": "History exported: %s",
        "menu_close_to_tray": "Minimize to tray on close",
        "msg_close_choose": "Quit the application?\n\nYes = Quit\nNo = Minimize to tray\nCancel = Keep running",
        "msg_win_only_script": "This is not Windows; .bat/.cmd/.vbs may not run. Add anyway?",
        "menu_restore_bak": "Restore config from last backup",
        "msg_restore_bak_confirm": "Overwrite current script list with .bak? Current config will be backed up first.",
        "msg_restore_bak_ok": "Restored from backup",
        "msg_restore_bak_missing": "No config backup (.bak) found",
        "btn_schedule_overview": "Schedules",
        "dialog_schedule_overview": "Schedule Overview",
        "col_next_run": "Next run",
        "col_schedule_type": "Type",
        "label_date_from": "From:",
        "label_date_to": "To:",
        "hint_date_filter": "YYYY-MM-DD, empty=no limit",
        "menu_diagnose": "Diagnose start",
        "menu_copy_diagnose": "Copy diagnosis",
        "tray_stop_all": "Stop All",
        "label_scan_result": "Scan Result:",
        "label_script_path": "Script Path:",
        "label_name": "Name:",
        "label_group": "Group:",
        "label_work_dir": "Work Directory:",
        "label_interpreter": "Interpreter:",
        "label_task_type": "Task Type:",
        "label_request_url": "Request URL:",
        "label_http_method": "HTTP Method:",
        "label_http_headers": "Headers:",
        "label_process_name": "Process Name:",
        "label_command": "Command:",
        "hint_request_url": "Enter http:// or https:// URL",
        "hint_http_headers": "(one Header: Value per line, e.g. X-API-Key: change-me-in-production)",
        "btn_fill_local_api": "Fill Local API",
        "hint_process_name": "Enter process name, e.g. explorer.exe",
        "hint_command": "Enter command directly, e.g. ping 8.8.8.8, ipconfig, python -c \"print('hello')\"",
        "label_auto_restart": "Keep Alive:",
        "label_log_output": "Log Output:",
        "label_schedule": "Schedule:",
        "tab_basic": "Basic",
        "tab_schedule": "Schedule",
        "label_schedule_time": "Time:",
        "btn_time_0": "0:00",
        "btn_time_3": "3am",
        "btn_time_6": "6am",
        "btn_time_9": "9am",
        "btn_time_12": "12pm",
        "btn_time_15": "3pm",
        "btn_time_18": "6pm",
        "btn_time_21": "9pm",
        "btn_time_now1": "+1m",
        "label_schedule_action": "Action:",
        "label_schedule_enabled": "Enabled:",
        "label_schedule_valid_from": "Valid From:",
        "label_schedule_valid_until": "Valid Until:",
        "hint_schedule_valid": "(Empty=no limit, Format: YYYY-MM-DD)",
        "hint_expired": "Expired",
        "label_schedule_max_runs": "Max Runs:",
        "hint_schedule_max_runs": "(0=unlimited)",
        "label_schedule_pre_delay": "Pre-delay (sec):",
        "hint_schedule_pre_delay": "(0=no delay)",
        "label_env_vars": "Environment Vars:",
        "hint_env_vars": "(One per line KEY=VALUE, e.g. PORT=8080)",
        "label_startup_timeout": "Startup Timeout (sec):",
        "hint_startup_timeout": "(0=disabled; treat as successful if script runs beyond this time)",
        "label_basic_settings": "Basic Settings",
        "label_webhook_settings": "Webhook Notification (Optional)",
        "label_webhook_url": "Webhook URL:",
        "label_webhook_method": "Request Method:",
        "label_webhook_enabled": "Enable Webhook Notification",
        "label_webhook_send_data": "Send data (script_name, reason, etc.)",
        "label_quick_exit_threshold": "Quick Exit Threshold (seconds):",
        "label_enable_notification": "Enable Exception Notification",
        "label_notify_nonzero": "Notify on Non-Zero Exit Code",
        "label_notify_quick": "Notify on Quick Exit",
        "label_schedule_debug_log": "Schedule Execution Log",
        "label_select_script": "Select Script:",
        "label_batch_group": "Group:",
        "label_batch_interpreter": "Interpreter:",
        "label_batch_args": "Arguments:",
        "label_batch_schedule": "Schedule:",
        "label_batch_schedule_detail": "Schedule detail (optional, when enabling):",
        "hint_batch_schedule_detail": "Leave type/time/weekday/action empty or 'No Change' to keep",
        "label_batch_keepalive": "Keep Alive:",
        "label_batch_launch_on_start": "Launch when app starts:",
        "label_batch_log": "Log Output:",
        "hint_batch_edit": "Hint: Empty fields won't be modified, only checked options will be changed",
        "hint_same_as_script": "(same as script dir)",
        "hint_no_change": "No Change",
        "hint_enable": "Enable",
        "hint_disable": "Disable",
        "hint_group_empty": "(empty = no group; e.g., ProjectA, Monitor, API)",
        "hint_python_interpreter": "(empty = current; can fill py -3.11 or interpreter path)",
        "hint_node_interpreter": "(empty = node; can fill node, node.exe or path)",
        "hint_shell_interpreter": "(empty = bash; can fill bash, sh or path)",
        "hint_time_format": "(Format: HH:MM, e.g. 09:00)",
        "hint_interval_minutes": "minutes (only for 'Minutely')",
        "btn_browse": "Browse",
        "btn_add_manual": "Add (Manual)",
        "btn_auto_detect": "Auto Detect",
        "default_name": "Unnamed",
        "menu_copy_path": "Copy Path",
        "menu_copy_pid": "Copy PID",
        "menu_copy_name": "Copy Name",
        "menu_start_group": "Start This Group",
        "menu_stop_group": "Stop This Group",
        "menu_auto_start_on_boot": "Auto Start on Boot",
        "menu_remove_auto_start": "Remove Auto Start",
        "msg_auto_start_enabled": "Auto start on boot enabled",
        "msg_auto_start_disabled": "Auto start on boot disabled",
        "msg_auto_start_failed": "Failed to set auto start: %s",
        "menu_view_log": "View Log",
        "menu_log_not_found": "Log file not found",
        "title_log_viewer": "Log Viewer - %s",
        "menu_batch_edit": "Batch Edit",
        "menu_save_template": "Save as Template",
        "menu_apply_template": "Add from Template",
        "menu_manage_templates": "Manage Templates",
        "msg_template_saved": "Template '%s' saved",
        "msg_template_deleted": "Template '%s' deleted",
        "title_template_manager": "Template Manager",
        "label_template_name": "Template Name:",
        "menu_move_up": "Move Up (Alt+↑)",
        "menu_move_down": "Move Down (Alt+↓)",
        "status_crash_loop": "Crash Loop",
        "msg_crash_loop_notify": "Task '%s' crashing frequently (%d times/min), auto-restart disabled",
        "menu_tail_output": "Live Output",
        "title_live_output": "Live Output - %s",
        "btn_close": "Close",
        "btn_clear": "Clear",
        "label_interpreter_python": "Python Interpreter:",
        "label_interpreter_node": "Node.js Interpreter:",
        "label_interpreter_shell": "Shell Interpreter:",
        "label_schedule_type": "Type:",
        "label_schedule_interval": "Interval:",
        "label_schedule_weekday": "Weekday:",
        "label_type_prefix": "Type: ",
        "check_log_output": "Log output to logs/ on start (for troubleshooting)",
        "check_auto_restart": "Auto restart on crash (Keep Alive)",
        "check_launch_on_app_start": "Launch when app starts",
        "check_schedule_enabled": "Enable Schedule",
        "schedule_action_start": "Start",
        "schedule_action_restart": "Restart Task",
        "schedule_action_stop": "Stop",
        "task_type_system_shutdown": "System Shutdown",
        "task_type_system_reboot": "System Reboot",
        "hint_system_power_task": "(No path; run via Start or scheduled Start)",
        "schedule_action_shutdown": "Shutdown",
        "schedule_action_reboot": "Reboot System",
        "schedule_type_daily": "Daily",
        "schedule_type_hourly": "Hourly",
        "schedule_type_minutely": "Minutely",
        "schedule_type_weekly": "Weekly",
        "schedule_type_monthly": "Monthly",
        "schedule_type_every_hours": "Every N hours",
        "schedule_type_quarterly": "Quarterly",
        "schedule_type_halfyearly": "Half-Yearly",
        "schedule_type_yearly": "Yearly",
        "schedule_type_once": "Once",
        "schedule_type_weekend": "Weekend(6-7)",
        "schedule_type_weekday": "Weekday(1-5)",
        "label_schedule_once_date": "Date:",
        "hint_schedule_once_date": "(Format: YYYY-MM-DD, empty=today)",
        "hint_schedule_once_auto": "Auto-disable after execution",
        "label_schedule_month": "Month:",
        "label_schedule_quarter": "Quarter:",
        "schedule_quarter_q1": "Q1 (Jan)",
        "schedule_quarter_q2": "Q2 (Apr)",
        "schedule_quarter_q3": "Q3 (Jul)",
        "schedule_quarter_q4": "Q4 (Oct)",
        "label_schedule_interval_minutes": "Interval (minutes):",
        "label_schedule_interval_hours": "Interval (hours):",
        "hint_interval_hours_repeat": "Repeat",
        "label_schedule_day": "Day:",
        "schedule_day_last": "Last day",
        "weekday_mon": "Monday",
        "weekday_tue": "Tuesday",
        "weekday_wed": "Wednesday",
        "weekday_thu": "Thursday",
        "weekday_fri": "Friday",
        "weekday_sat": "Saturday",
        "weekday_sun": "Sunday",
        "dialog_select_script": "Select Script",
        "dialog_select_work_dir": "Select Work Directory",
        "dialog_export_config": "Export Config",
        "dialog_import_config": "Import Config",
        "label_batch_selected": "Selected %d items, can batch modify the following attributes:",
        "label_batch_edit_title": "Batch Edit",
        "col_start_time": "Start Time",
        "col_stop_time": "Stop Time",
        "col_duration": "Duration",
        "col_exit_code": "Exit Code",
        "col_status": "Status",
        "label_open_in_browser": "Open in browser:",
        "stat_total": "Total: %d times",
        "stat_success": "Success: %d times",
        "stat_failed": "Failed: %d times",
        "stat_avg_duration": "Avg Duration: %.1f seconds",
        "msg_scan_need_psutil_detail": "Need to install psutil to scan (pip install psutil)",
        "msg_scan_no_processes_detail": "No running .bat / .cmd / .py processes detected in the system (can check from taskbar, file explorer windows)",
        "msg_scan_added_from": "Added %d items from 'Scan Running'",
        "msg_scan_added": "Added %d items",
        "msg_scan_skipped_detail": ", skipped %d items (path already exists)",
        "msg_scan_prefix": "Scan Running · ",
        "msg_opened_in_editor": "Opened in editor: %s",
        "msg_opened_file": "Opened file: %s",
        "msg_opened_dir": "Opened directory: %s",
        "msg_opened_url": "Opened %s",
        "msg_opened_url_ports": "Opened %s (%d ports total, can access others from right-click menu)",
        "msg_scan_confirm_title": "Scan Running",
        "msg_stopped_count_detail": "Stopped %d: %s",
        "msg_stopped_failed_detail": "\n\nFailed to stop %d:\n%s",
        # Message boxes
        "msg_info": "Info",
        "msg_error": "Error",
        "msg_success": "Success",
        "msg_failed": "Failed",
        "msg_confirm": "Confirm",
        "msg_confirm_delete": "Confirm Delete",
        "msg_complete": "Complete",
        "msg_select_first": "Please select an item first",
        "msg_select_items": "Please select one or more items",
        "msg_select_script": "Please select a script",
        "msg_select_table_rows": "Please check rows in the table to add (batch or Python rows with type, path, PID)",
        "msg_no_processes": "No processes found to add. Please click 'Refresh' and try again.",
        "msg_no_items_added": "No items added. Please check rows with type 'batch' or 'Python' (excluding header row), then click 'Add Selected'.",
        "msg_no_processes_refresh": "No processes to add. Please click 'Refresh' and try again.",
        "msg_added_scripts": "Added %d running scripts to manager. %s",
        "msg_skipped_paths": "\nSkipped %d existing paths.",
        "msg_enter_script_path": "Please enter or select a script path",
        "msg_script_not_exists": "Script file does not exist: %s",
        "msg_clear_history_confirm": "Are you sure you want to clear all history for '%s'?",
        "msg_enter_webhook_url": "Please enter Webhook URL first",
        "msg_webhook_test_success": "Webhook test successful!",
        "msg_webhook_test_failed": "Webhook test failed:\n%s",
        "msg_delete_confirm": "%s\n\nContinue?",
        "msg_script_running": "'%s' is already running",
        "msg_started": "Started: %s (PID=%d)",
        "msg_start_failed": "Start failed: %s",
        "msg_stopped": "Stopped: %s",
        "msg_start_all": "Start All: Started %d items",
        "msg_stop_all": "Stop All: Stopped %d items",
        "msg_stopped_external": "Stopped %d scripts, %d failed",
        "msg_stopped_external_names": "Stopped %d scripts: %s",
        "msg_copied": "Copied %d items",
        "msg_file_not_exists": "File does not exist: %s",
        "msg_cannot_open_file": "Cannot open file:\n%s",
        "msg_dir_not_exists": "Directory does not exist: %s",
        "msg_cannot_open_dir": "Cannot open directory:\n%s",
        "msg_select_one": "Please select one item.",
        "msg_not_running_visit": "'%s' is not running, cannot access port.",
        "msg_not_running_ports": "'%s' is not running, cannot view ports and processes.",
        "msg_no_port_detected": "'%s' has no listening port detected.\n\n'Visit' only works for services that listen on ports (e.g., Web, API); scripts like scheduled tasks or crawlers that don't open ports can be ignored.\n\nPlease select an item with a number in the 'Port' column (e.g., app :5001) and click 'Visit'.",
        "label_listening_ports": "Listening Ports: ",
        "label_no_ports": "None",
        "label_process_info": "[%s] PID=%d",
        "label_cpu": "CPU",
        "label_memory": "Memory",
        "label_mb": "MB",
        "label_process_detail": "  PID %d: %s - %s",
        "label_process_detail_no_info": "  PID %d: (need psutil to view details)",
        "msg_export_success": "Config saved to:\n%s",
        "msg_export_failed": "Error exporting config:\n%s",
        "msg_import_failed_format": "Config file format error: should be an array",
        "msg_import_empty": "Config file is empty",
        "msg_import_complete": "%s\nSkipped items: paths already in manager",
        "msg_import_json_error": "Config file JSON format error:\n%s",
        "msg_import_error": "Error importing config:\n%s",
        "msg_need_psutil": "This feature requires psutil (pip install psutil)",
        "msg_no_running_scripts": "No running scripts in system (batch, Python, Node.js, PowerShell, etc.)",
        "msg_confirm_stop": "Confirm Stop",
        "msg_partial_failed": "Partial Failure",
        "msg_stopped_count": "Stopped %d: %s\n\nFailed to stop %d:\n%s",
        "msg_scan_need_psutil": "Scan requires psutil (pip install psutil).",
        "msg_scan_no_processes": "No running batch/Python processes found.",
        "msg_scan_confirm": "Found %d running batch/Python processes. Add all to manager?",
        "msg_script_abnormal_exit": "Script Abnormal Exit",
        "msg_abnormal_exit": "'%s' exited abnormally\n%s",
        "msg_exit_code": "Exit Code: %s",
        "msg_exit_code_detail": "Exit code %s",
        "msg_duration_only": "Duration only %.1f seconds",
        "msg_duration_only_with_comma": ", duration only %.1f seconds",
        "msg_quick_exit_detail": "'%s' exited after about %.1f seconds, exit code %s",
        "msg_quick_exit_start": "'%s' exited after about %.1f seconds from start, exit code %s",
        "msg_quick_exit_with_log": "'%s' exited after about %.1f seconds, exit code %s.\n\nRecent output:\n%s",
        "msg_quick_exit_title": "Exited Quickly After Start",
        "msg_log_saved": "; output saved to %s",
        "msg_suggest_cmd": "; suggest running the script directly in CMD to troubleshoot",
        "msg_duration": "Duration: %.1f seconds",
        "msg_export_to": "Config exported to: %s",
        "msg_import_way": "Import Method",
        "msg_import_found": "Found %d config items.\n\n",
        "msg_import_merge": "'Yes' = Merge (keep existing + add new)\n",
        "msg_import_replace": "'No' = Replace (clear existing + import new)\n",
        "msg_import_cancel": "'Cancel' = Cancel import",
        "msg_import_complete_title": "Import Complete",
        "msg_imported": "Imported %d items",
        "msg_import_skipped_detail": ", skipped %d items (path already exists)",
        "status_import_prefix": "Import Config · ",
        "status_running": "Running",
        "status_stopped": "Stopped",
        "status_path_invalid": "Path invalid",
        "status_already_stopped": "Selected item(s) already stopped",
        "status_config_save_failed": "Config save failed; check permissions or disk space",
        "status_added": "Added: %s",
        "status_updated": "Updated: %s",
        "status_deleted": "Deleted selected items",
        "status_script_running": "'%s' is already running",
        "status_refreshed": "Refreshed",
        "status_refreshed_adopted": "Refreshed, adopted %d running scripts",
        "status_auto_restarted": "Auto restarted: %s",
        "status_schedule_triggered_stopped": "Schedule triggered: %s (stopped)",
        "status_schedule_triggered": "Schedule triggered: %s (%s)",
        "menu_schedule_pause": "Pause All Schedules",
        "menu_schedule_resume": "Resume All Schedules",
        "status_schedule_paused": "All schedules paused",
        "status_schedule_resumed": "All schedules resumed",
        "schedule_next_run": "Next ~%s",
        "schedule_next_tomorrow": "Tomorrow",
        "status_schedule_max_reached": "Schedule max runs reached: %s",
        "status_schedule_out_of_valid": "Schedule out of valid period: %s",
        "status_schedule_path_invalid": "Schedule skipped: %s (script path invalid)",
        "status_startup_timeout": "'%s' startup timeout (%d sec, treated as successful)",
        "btn_collapse_all": "Collapse All",
        "btn_expand_all": "Expand All",
        "label_group_collapse": "Group Collapse",
        "hint_multi_time": "(Multiple times separated by commas, e.g. 09:00,15:00,21:00)",
        "menu_global_hotkeys": "Hotkey Help",
        "msg_hotkeys_info": "Keyboard Shortcuts:\nCtrl+N = Add script\nCtrl+E = Edit script\nCtrl+D = Delete script\nCtrl+R = Refresh status\nCtrl+S = Start selected\nCtrl+Q = Stop selected\nCtrl+A = Select all\nF5 = Refresh status\nF6 = Start all\nF7 = Stop all",
        "status_refresh_resumed": "Resumed refresh (%d sec)",
        "status_refresh_interval_set": "Refresh interval set to %d sec",
        "menu_settings": "Settings",
        "menu_refresh_interval": "Refresh interval",
        "refresh_interval_2s": "2 sec",
        "refresh_interval_4s": "4 sec",
        "refresh_interval_8s": "8 sec",
        "menu_start_minimized": "Minimize to taskbar on start",
        "menu_mark_running_launch_on_start": "Mark running tasks to auto-start with app",
        "dialog_mark_running_launch_on_start": "Mark running tasks to auto-start",
        "label_mark_running_list": "Currently running tasks (%d):",
        "check_mark_running_clear_stopped": "Also turn off auto-start for tasks that are not running",
        "hint_mark_running": "Saves to config. These tasks start the next time this app launches. To run them at login, also enable the Startup menu.",
        "msg_mark_running_none": "No tasks are running. Start the tasks you want on app launch, then use this again.",
        "status_mark_running_done": "Set auto-start on %d running task(s) (%d newly checked)",
        "status_mark_running_cleared": ", cleared auto-start on %d stopped task(s)",
        "status_refresh_paused": "Refresh paused; keep-alive and running adoption stopped, click 'Refresh' or 'Resume Refresh'",
        "status_history_cleared": "History cleared",
        "status_notification_saved": "Notification config saved",
        "msg_notification_save_failed": "Failed to save notification config. Check directory permissions or path.",
        "status_batch_modified": "Batch modified %d items",
        "status_selected_all": "Selected %d items",
        "status_deselected_all": "Deselected all",
        "msg_stop_confirm_detail": "Found %d running scripts (in list and outside):\n%s\n\nStop all?",
        "msg_stop_confirm_more": "\n  ... and %d more",
        "msg_delete_running_detail": "The following %d items are running, will be removed from manager (processes will continue):\n%s",
        "msg_delete_running_more": "\n  ... and %d more",
        "filetype_all": "All",
        # ---- Dependency Chain ----
        "label_depends_on": "Depends On (IDs):",
        "hint_depends_on": "(comma-separated IDs, e.g. 1,3; starts after deps)",
        "label_delay_after_dep": "Delay After Dep (s):",
        "hint_delay_after_dep": "(seconds to wait after deps start, 0=no wait)",
        "col_depends": "Deps",
        "status_dep_waiting": "Waiting for deps",
        "status_dep_not_running": "Deps not running",
        "msg_dep_circular": "Circular dependency detected, cannot start: %s",
        "msg_dep_not_running": "Dependency %s(%s) not running, cannot start '%s'",
        "msg_dep_starting": "Waiting for dependencies to start...",
        # ---- Launch Arguments ----
        "label_args": "Arguments:",
        "hint_args": "(e.g. --port 8080 --debug; empty = none)",
        "col_args": "Args",
        # ---- Log Tail ----
        "btn_tail_log": "Tail Log",
        "dialog_tail_log": "Live Log - %s",
        "btn_tail_stop": "Stop",
        "btn_tail_clear": "Clear",
        # ---- Restart Tool ----
        "btn_restart_self": "Restart Tool",
        "msg_restart_self_confirm": "Restart the manager?\nRunning scripts will continue, and will be re-adopted after restart.",
        # ---- System Shutdown/Reboot ----
        "btn_shutdown": "Shutdown System",
        "btn_reboot": "Reboot System",
        "msg_shutdown_confirm": "Shut down Windows?\nAll managed scripts will be stopped first, then normal shutdown.",
        "msg_reboot_confirm": "Reboot Windows?\nAll managed scripts will be stopped first, then normal reboot.",
        "status_schedule_triggered_power": "Schedule triggered: %s → %s",
        "status_power_action_failed": "System action failed: %s",
        # ---- API Server (standalone) ----
        "label_api_ops": "API:",
        "btn_start_api": "Start API",
        "btn_stop_api": "Stop API",
        "btn_restart_api": "Restart API",
        "status_api_running": "Running PID=%s",
        "status_api_stopped": "Stopped",
        "status_api_missing": "Script missing",
        "msg_api_not_found": "API script not found:\n%s",
        "msg_api_already_running": "API already running (PID=%s)",
        "msg_api_started": "API started PID=%s",
        "msg_api_stopped": "API stopped",
        "msg_api_restarted": "API restarted PID=%s",
        "msg_api_start_failed": "Failed to start API",
        "msg_api_stop_failed": "Failed to stop API",
        "check_launch_api_on_start": "Launch API when app starts",
        "btn_load_api_calls": "Load API Calls",
        "btn_check_api_env": "Check Runtime",
        "group_local_api": "Local API",
        "status_api_call_hint": "API: %s  Use Load API Calls to add HTTP tasks",
        "msg_api_calls_loaded": "Added %d, updated %d.\n\nid=1 is only an example; change the URL to a real task ID.",
        "api_task_list": "List",
        "api_task_status": "Status (id=1)",
        "api_task_start": "Start (id=1)",
        "api_task_stop": "Stop (id=1)",
        "api_task_restart": "Restart (id=1)",
        "api_task_start_all": "Start All",
        "api_task_stop_all": "Stop All",
        # ---- System Tray ----
        "tray_show": "Show Window",
        "tray_quit": "Quit",
        "menu_minimize_to_tray": "Minimize to Tray",
        # ---- Plugin System ----
        "menu_plugins": "Plugins",
        "menu_plugins_dir": "Open Plugins Dir",
        "msg_plugins_loaded": "Loaded %d plugin(s)",
        "msg_plugins_none": "No plugins found (place .py files in plugins/ directory)",
    }
}

LANGUAGES = load_ui_catalogs(LANGUAGES)


def load_language() -> str:
    """Resolve a saved preference, or follow the operating system language."""
    try:
        config = json.loads(LANGUAGE_CONFIG_PATH.read_text(encoding="utf-8"))
        preference = normalize_language_preference(config.get("language"))
        if preference in LANGUAGES:
            return preference
    except (OSError, ValueError, AttributeError):
        pass
    return detect_system_language()


def save_language(language: str) -> None:
    """保存语言偏好。"""
    language = normalize_language_preference(language)
    try:
        with open(LANGUAGE_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"language": language}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# 脚本类型定义（.exe 仅用于添加时选择，扫描运行中不识别 exe 避免列表过多）
SCRIPT_TYPES = {
    "python": [".py"],
    "node": [".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx"],
    "powershell": [".ps1"],
    "vbs": [".vbs"],
    "jscript": [".js", ".jse"],  # 注意：.js 可能是 Node 或 JScript，优先按 Node 处理
    "batch": [".bat", ".cmd"],
    "shell": [".sh"],
    "wsf": [".wsf"],
    "exe": [".exe"],
}

# 获取文件扩展名对应的脚本类型
def get_script_type(path: str) -> str:
    """根据文件路径返回脚本类型：python, node, powershell, vbs, jscript, batch, shell, wsf, http_get, process, command, unknown"""
    if not path:
        return "unknown"
    if path.startswith("http://") or path.startswith("https://"):
        return "http_get"
    # 进程名（如 explorer.exe）识别为 process 类型
    if is_process_name_str(path):
        return "process"
    ext = os.path.splitext(path)[1].lower()
    for stype, exts in SCRIPT_TYPES.items():
        if ext in exts:
            # .js 优先判断为 Node.js（更常见），除非明确是 JScript
            if ext == ".js" and stype == "jscript":
                continue
            return stype
    # 直接命令识别：包含空格且第一个词不像文件路径
    # 例如 "ping 8.8.8.8"、"ipconfig /all"、"python -c \"print(1)\""
    # 也包括 "python.exe -m pip install --upgrade pip" 等 可执行程序+参数 的形式
    _CMD_EXTS = {".exe", ".bat", ".cmd", ".com"}
    if " " in path:
        first_word = path.split()[0]
        first_ext = os.path.splitext(first_word)[1].lower()
        has_path_sep = os.path.sep in first_word or "/" in first_word
        # 第一个词没有路径分隔符，且满足以下之一：
        # 1) 无扩展名（如 ping、ipconfig、python）
        # 2) 扩展名是可执行类型（如 python.exe、cmd.bat）
        if not has_path_sep and (not first_ext or first_ext in _CMD_EXTS):
            return "command"
    return "unknown"


# ---------------------------------------------------------------------------
# 插件系统
# ---------------------------------------------------------------------------

PLUGIN_DIR = Path(get_app_path()).parent / "plugins"
_loaded_plugins = None

def _get_plugins():
    global _loaded_plugins
    if _loaded_plugins is not None:
        return _loaded_plugins
    _loaded_plugins = []
    if PLUGIN_DIR.is_dir():
        import importlib.util
        for f in sorted(PLUGIN_DIR.glob("*.py")):
            if f.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(f.stem, str(f))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    _loaded_plugins.append(mod)
            except Exception as e:
                print(f"[Plugin] {f.name}: {e}", file=sys.stderr)
    return _loaded_plugins

def fire_plugin_hook(name: str, **kw):
    for mod in _get_plugins():
        fn = getattr(mod, name, None)
        if callable(fn):
            try: fn(**kw)
            except Exception as e: print(f"[Plugin] {mod.__name__}.{name}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 依赖链：拓扑排序 + 循环检测
# ---------------------------------------------------------------------------

def _parse_dep_ids(dep_str):
    """解析逗号分隔的依赖 ID 字符串，返回 int 列表。"""
    return [int(s.strip()) for s in (dep_str or "").split(",") if s.strip().isdigit()]

def _check_circular_deps(items: list, start_id: int) -> Optional[str]:
    """DFS 检测循环依赖，返回循环路径或 None。"""
    id_map = {it.get("id"): it for it in items}
    visited, path = set(), []
    def dfs(cid):
        if cid in path:
            return " → ".join(str(x) for x in path[path.index(cid):] + [cid])
        if cid in visited:
            return None
        visited.add(cid); path.append(cid)
        for d in _parse_dep_ids((id_map.get(cid) or {}).get("depends_on")):
            r = dfs(d)
            if r: return r
        path.pop(); return None
    return dfs(start_id)

def _get_dep_order(items: list) -> list:
    """拓扑排序返回启动顺序。"""
    indeg = {it["id"]: 0 for it in items}; graph = {it["id"]: [] for it in items}
    for it in items:
        for d in _parse_dep_ids(it.get("depends_on")):
            if d in graph: graph[d].append(it["id"]); indeg[it["id"]] += 1
    q = [k for k, v in indeg.items() if v == 0]; result = []
    while q:
        n = q.pop(0); result.append(n)
        for c in graph.get(n, []):
            indeg[c] -= 1
            if indeg[c] == 0: q.append(c)
    return result

def _are_deps_running(it: dict, items: list) -> bool:
    """检查依赖项是否全部运行中。"""
    id_map = {x.get("id"): x for x in items}
    return all(id_map.get(d) and id_map[d].get("pid") and is_process_running(id_map[d]["pid"]) for d in _parse_dep_ids(it.get("depends_on")))


# ---------------------------------------------------------------------------
# 配置与数据
# ---------------------------------------------------------------------------

def get_config_dir() -> str:
    """脚本列表配置文件所在目录。"""
    return str(CONFIG_PATH.resolve().parent)


def effective_work_dir(it: dict):
    """相对路径且未填 work_dir 时，以配置文件目录为基准（与 CLI 一致）。"""
    w = (it.get("work_dir") or "").strip()
    if w:
        return w
    p = (it.get("path") or "").strip()
    if p and not os.path.isabs(p):
        return get_config_dir()
    return None


def resolve_script_file_path(it: dict) -> str:
    """解析脚本在磁盘上的路径（存在性检查、run_map 等）。"""
    path = (it.get("path") or "").strip()
    if not path:
        return ""
    work = effective_work_dir(it)
    if work and not os.path.isabs(path):
        path = os.path.normpath(os.path.join(work, path))
    return os.path.normpath(os.path.abspath(path))


def config_item_to_save(it: dict) -> dict:
    """单条任务写入配置的字段（GUI / CLI 共用，避免漏字段）。"""
    stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
    if not stype or stype == "unknown":
        if it.get("is_python"):
            stype = "python"
        else:
            stype = get_script_type(it.get("path", "")) or "batch"
    interpreter = it.get("interpreter") or it.get("python_exe") or ""
    return {
        "id": it.get("id"),
        **{key: it[key] for key in FEATURE_FIELDS if key in it},
        "name": it.get("name", ""),
        "path": it.get("path", ""),
        "work_dir": it.get("work_dir") or "",
        "group": it.get("group", ""),
        "script_type": stype,
        "interpreter": interpreter,
        "is_python": bool(it.get("is_python", False)) or (stype == "python"),
        "python_exe": interpreter if stype == "python" else "",
        "auto_restart": it.get("auto_restart", False),
        "launch_on_app_start": it.get("launch_on_app_start", False),
        "log_output": it.get("log_output", False),
        "schedule_enabled": it.get("schedule_enabled", False),
        "schedule_action": it.get("schedule_action", "start"),
        "schedule_type": it.get("schedule_type", "daily"),
        "schedule_time": it.get("schedule_time", "09:00"),
        "schedule_interval": it.get("schedule_interval", 5),
        "schedule_weekday": it.get("schedule_weekday", 0),
        "schedule_day": it.get("schedule_day", 1),
        "schedule_quarter": it.get("schedule_quarter", "q1"),
        "schedule_month": it.get("schedule_month", 1),
        "schedule_after_at": it.get("schedule_after_at"),
        "depends_on": it.get("depends_on", ""),
        "delay_after_dep": it.get("delay_after_dep", 0),
        "args": it.get("args", ""),
        "env_vars": it.get("env_vars", ""),
        "http_method": it.get("http_method") or "GET",
        "http_headers": it.get("http_headers") or "",
        "startup_timeout": it.get("startup_timeout", 0),
        "schedule_valid_from": it.get("schedule_valid_from", ""),
        "schedule_valid_until": it.get("schedule_valid_until", ""),
        "schedule_max_runs": it.get("schedule_max_runs", 0),
        "schedule_pre_delay": it.get("schedule_pre_delay", 0),
        "schedule_once_date": it.get("schedule_once_date", ""),
    }


def next_item_id(items: list) -> int:
    """分配新任务 ID：max(已有 id)+1，避免删除后 len+1 与旧 id 冲突。"""
    max_id = 0
    try:
        state = json.loads(CONFIG_PATH.with_name(CONFIG_PATH.name + '.ids.json').read_text(encoding='utf-8'))
        max_id = max(0, int(state.get('max_id', 0)))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    for it in items:
        try:
            max_id = max(max_id, int(it.get("id", 0)))
        except (TypeError, ValueError):
            pass
    return max_id + 1


def _bool_from_config(v) -> bool:
    """从 config 读出布尔：支持 bool、字符串 'true'/'false'、0/1，避免只启动一个任务等异常。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return bool(v)


def load_config() -> list:
    if not CONFIG_PATH.exists():
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        save_config([])
        return []
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
                raise ValueError("Task configuration must be a list of objects")
            return migrate_items(data)
    except Exception as e:
        try:
            import logging
            logging.exception("load_config failed: %s", e)
        except Exception:
            pass
        return []


def save_config(items: list) -> bool:
    """保存脚本列表到 SCRIPT_LIST_CONFIG_FILENAME。先写临时文件再替换，避免写入中断损坏配置。成功返回 True，失败返回 False。"""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    # Preserve the allocation high-water mark even when the largest task is deleted.
    previous = []
    try:
        previous = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        if not isinstance(previous, list): previous = []
    except (OSError, ValueError):
        pass
    high_water = next_item_id(items + [i for i in previous if isinstance(i, dict)]) - 1
    if not _atomic_write_json(CONFIG_PATH.with_name(CONFIG_PATH.name + '.ids.json'), {'max_id': high_water}):
        return False
    tmp_path = CONFIG_PATH.parent / (CONFIG_PATH.name + ".tmp")
    bak_path = CONFIG_PATH.parent / (CONFIG_PATH.name + ".bak")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        if CONFIG_PATH.exists():
            try:
                import shutil
                shutil.copy2(CONFIG_PATH, bak_path)
            except Exception:
                pass
        os.replace(tmp_path, CONFIG_PATH)
        return True
    except Exception as e:
        try:
            import logging
            logging.exception("save_config failed: %s", e)
        except Exception:
            pass
        return False
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


def load_history() -> dict:
    """加载执行历史记录。"""
    if HISTORY_PATH.exists():
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 转换格式：确保 item_id 是 int
                result = {}
                for k, v in data.items():
                    try:
                        result[int(k)] = v
                    except (ValueError, TypeError):
                        pass
                return result
        except Exception:
            pass
    return {}


def _atomic_write_json(path: Path, data) -> bool:
    """先写临时文件再替换，避免写入中断损坏 JSON。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    tmp_path = path.parent / (path.name + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return True
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        return False


def save_history(history: dict) -> None:
    """保存执行历史记录。"""
    _atomic_write_json(HISTORY_PATH, history)


def load_notification_config() -> dict:
    """加载通知配置。"""
    if NOTIFICATION_CONFIG_PATH.exists():
        try:
            with open(NOTIFICATION_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_notification_config(config: dict) -> bool:
    """保存通知配置。成功返回 True，失败返回 False。"""
    return _atomic_write_json(NOTIFICATION_CONFIG_PATH, config)


def load_api_config() -> dict:
    """加载 API 服务配置。"""
    if API_CONFIG_PATH.exists():
        try:
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_api_config(config: dict) -> bool:
    """保存 API 服务配置。"""
    return _atomic_write_json(API_CONFIG_PATH, config)


def load_app_config() -> dict:
    """加载 GUI 应用设置（如启动后最小化）。"""
    if APP_CONFIG_PATH.exists():
        try:
            with open(APP_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_app_config(config: dict) -> bool:
    """保存 GUI 应用设置。"""
    return _atomic_write_json(APP_CONFIG_PATH, config)


# ---------------------------------------------------------------------------
# 进程与端口（兼容无 psutil）
# ---------------------------------------------------------------------------

def get_process_tree_pids(pid: int) -> set:
    """获取以 pid 为根的进程树（含自身）的 PID 集合。"""
    if not HAS_PSUTIL:
        return {pid}
    try:
        p = psutil.Process(pid)
        out = {pid}
        for c in p.children(recursive=True):
            try:
                out.add(c.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return out
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {pid}


def get_ports_for_pids(pids: set) -> list:
    """获取这些 PID 监听的端口列表。有 psutil 用 psutil，否则用 netstat -ano。"""
    if HAS_PSUTIL:
        try:
            conns = psutil.net_connections(kind="inet")
            ports = []
            for c in conns:
                if c.pid in pids and c.status == "LISTEN" and c.laddr:
                    ports.append(c.laddr.port)
            return sorted(set(ports))
        except (psutil.AccessDenied, OSError):
            pass

    # 降级：netstat -ano，再根据 PID 过滤
    try:
        r = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=10, creationflags=0x08000000
        )
        if r.returncode != 0:
            return []
        pids_str = {str(x) for x in pids}
        ports = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].lower() in ("tcp", "tcpv6") and ("LISTENING" in line or "监听" in line):
                # 最后一列是 PID
                for i in range(len(parts) - 1, -1, -1):
                    if parts[i] in pids_str:
                        # 找端口：格式如 0.0.0.0:8080 或 [::]:8080
                        for j, s in enumerate(parts):
                            if ":" in s:
                                try:
                                    port = int(s.split(":")[-1])
                                    if 1 <= port <= 65535:
                                        ports.append(port)
                                except ValueError:
                                    pass
                        break
        return sorted(set(ports))
    except Exception:
        return []


def get_process_info(pid: int) -> Optional[dict]:
    """返回 {name, exe, cmdline} 或 None。"""
    if not HAS_PSUTIL:
        return None
    try:
        p = psutil.Process(pid)
        return {"name": p.name(), "exe": p.exe() or "", "cmdline": " ".join(p.cmdline() or [])[:200]}
    except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
        return None


class TitleParser(HTMLParser):
    """解析 HTML 提取 <title> 标签内容。"""
    def __init__(self):
        super().__init__()
        self.title = None
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title = (self.title or "") + data


def get_page_title(port: int, timeout: float = 0.8) -> Optional[str]:
    """尝试获取 http://localhost:端口 的页面标题，超时或失败返回 None。使用较短超时避免卡顿。"""
    try:
        url = f"http://localhost:{port}"
        req = Request(url, headers={"User-Agent": "BatchManager/1.0"})
        with urlopen(req, timeout=timeout) as f:
            content = f.read(4096).decode("utf-8", errors="ignore")
            parser = TitleParser()
            parser.feed(content)
            title = parser.title
            if title:
                title = title.strip()[:35]
                return title if title else None
    except (URLError, HTTPError, OSError, ValueError, UnicodeDecodeError):
        pass
    return None


def _is_network_likely_reachable(timeout: float = 1.0) -> bool:
    """快速判断是否可能联网（用于避免无网时长时间尝试）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.connect(('8.8.8.8', 80))
            return True
        finally:
            s.close()
    except Exception:
        return False


def get_local_ip(timeout: float = 2.0) -> str:
    """获取本地IP地址。timeout 为连接超时（秒），避免无网时长时间阻塞。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip
    except Exception:
        try:
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
            if ip == '127.0.0.1':
                local_ip = socket.gethostbyname_ex(hostname)[2]
                local_ip = [x for x in local_ip if not x.startswith('127.')]
                if local_ip:
                    return local_ip[0]
            return ip
        except Exception:
            return "无法获取"


def get_public_ip() -> str:
    """获取外网IP地址（通过API）。先快速判断是否联网，未联网则直接返回「无法获取」避免多次超时。"""
    if not _is_network_likely_reachable(timeout=1.0):
        return "无法获取"
    api_urls = [
        "http://api.ipify.org",
        "http://icanhazip.com",
        "http://ifconfig.me/ip",
        "http://ipinfo.io/ip",
    ]
    for url in api_urls:
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=3) as response:
                ip = response.read().decode('utf-8').strip()
                if ip and re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
                    return ip
        except Exception:
            continue
    return "无法获取"


def is_process_running(pid: int) -> bool:
    if HAS_PSUTIL:
        try:
            return psutil.Process(pid).is_running()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_process(pid: int, force: bool = True) -> bool:
    if HAS_PSUTIL:
        try:
            p = psutil.Process(pid)
            p.terminate()
            if force:
                try:
                    p.wait(timeout=3)
                except psutil.TimeoutExpired:
                    p.kill()
                    try:
                        p.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        pass
            # 验证进程是否真的终止
            return not p.is_running()
        except psutil.NoSuchProcess:
            return True  # 进程已不存在，视为成功
        except psutil.AccessDenied:
            return False
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
        return not is_process_running(pid)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 进程名管理：支持按进程名（如 explorer.exe）进行启动/停止/重启
# ---------------------------------------------------------------------------
import glob as _glob_mod

def is_process_name_str(path: str) -> bool:
    """判断路径是否为纯进程名（如 explorer.exe），而非文件路径。
    
    规则：
    - 以 .exe / .com / .msi 结尾
    - 不包含路径分隔符（/ 或 \）
    - 不是已存在的文件路径
    """
    p = (path or "").strip()
    if not p:
        return False
    base = os.path.basename(p)
    # 必须是纯文件名（不含目录部分）
    if base != p:
        return False
    # 必须是可执行文件扩展名
    ext = os.path.splitext(p)[1].lower()
    if ext not in (".exe", ".com", ".msi", ".scr", ".cpl"):
        return False
    # 不是已存在的文件（如果是存在的文件，按文件处理）
    return not os.path.isfile(p)


def start_process_by_name(name: str, work_dir: Optional[str] = None) -> Optional[subprocess.Popen]:
    """按进程名启动程序。返回 Popen 对象或 None。"""
    name = name.strip()
    if not name:
        return None
    try:
        cwd = work_dir if work_dir and os.path.isdir(work_dir) else None
        return subprocess.Popen([name], cwd=cwd, shell=False,
                                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0)
    except Exception as e:
        print(f"[Process] 启动进程失败: {name}, 错误: {e}")
        return None


def stop_process_by_name(name: str) -> int:
    """按进程名终止所有匹配的进程。返回终止的进程数。"""
    name = (name or "").strip().lower()
    if not name:
        return 0
    killed = 0
    if HAS_PSUTIL:
        try:
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    pname = ((p.info.get("name") or "")).lower()
                    if pname == name or pname == os.path.basename(name):
                        p.terminate()
                        try:
                            p.wait(timeout=3)
                        except psutil.TimeoutExpired:
                            p.kill()
                        killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass
    else:
        # 回退到 taskkill
        try:
            result = subprocess.run(["taskkill", "/F", "/IM", name],
                                    capture_output=True, timeout=10)
            if result.returncode == 0:
                # 解析输出中的成功数量
                out = (result.stdout.decode("gbk", errors="replace") + 
                       result.stderr.decode("gbk", errors="replace"))
                import re
                matches = re.findall(r'成功:\s*(\d+)', out)
                if matches:
                    killed = sum(int(m) for m in matches)
        except Exception as e:
            print(f"[Process] taskkill 失败: {e}")
    return killed


def get_running_pids_by_name(name: str) -> list:
    """查找匹配进程名的 PID 列表。"""
    name = (name or "").strip().lower()
    if not name:
        return []
    pids = []
    target_base = os.path.basename(name).lower()
    if HAS_PSUTIL:
        try:
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    pname = ((p.info.get("name") or "")).lower()
                    if pname == name or pname == target_base:
                        pids.append(p.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass
    return pids


def system_power_action(action: str, force: bool = False) -> bool:
    """Windows 关机/重启：正常流程，/t 0 无倒计时；force=True 时加 /f 强制结束程序。"""
    action = (action or "").strip().lower()
    if action not in ("shutdown", "reboot"):
        return False
    if os.name != "nt":
        print("[Power] Only supported on Windows", file=sys.stderr)
        return False
    flag = "/s" if action == "shutdown" else "/r"
    cmd = ["shutdown", flag, "/t", "0"]
    if force:
        cmd.append("/f")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            if err:
                print(f"[Power] {err}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"[Power] {e}", file=sys.stderr)
        return False


def is_system_power_schedule_action(action: str) -> bool:
    return (action or "").strip().lower() in ("shutdown", "reboot")


SYSTEM_POWER_SCRIPT_TYPES = frozenset({"system_shutdown", "system_reboot"})
SYSTEM_SHUTDOWN_PATH = "(system:shutdown)"
SYSTEM_REBOOT_PATH = "(system:reboot)"


def is_system_power_task(it) -> bool:
    """是否为系统关机/重启类任务（任务类型，非普通脚本）。"""
    if not isinstance(it, dict):
        return False
    st = (it.get("script_type") or "").strip().lower()
    if st in SYSTEM_POWER_SCRIPT_TYPES:
        return True
    # 兼容旧配置：定时动作曾为 shutdown/reboot
    sa = (it.get("schedule_action") or "").strip().lower()
    return sa in ("shutdown", "reboot")


def item_is_currently_running(it: dict) -> bool:
    """列表项是否有仍在运行的 PID（关机/重启类任务不算）。"""
    if not isinstance(it, dict) or is_system_power_task(it):
        return False
    pid = it.get("pid")
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    return is_process_running(pid)


def apply_mark_running_as_launch_on_start(
    items: list,
    running_ids=None,
    clear_stopped: bool = False,
) -> dict:
    """把指定（或当前运行中）的任务勾上 launch_on_app_start（可选取消未运行项）。原地修改 items。"""
    if running_ids is None:
        running_id_set = {it.get("id") for it in items if item_is_currently_running(it)}
    else:
        running_id_set = set(running_ids)
    n_running = 0
    n_enabled = 0
    n_cleared = 0
    for it in items:
        if not isinstance(it, dict) or is_system_power_task(it):
            continue
        if it.get("id") in running_id_set:
            n_running += 1
            if not it.get("launch_on_app_start"):
                it["launch_on_app_start"] = True
                n_enabled += 1
        elif clear_stopped and it.get("launch_on_app_start"):
            it["launch_on_app_start"] = False
            n_cleared += 1
    return {
        "running": n_running,
        "enabled": n_enabled,
        "cleared": n_cleared,
    }


def get_system_power_action_for_task(it) -> str:
    st = (it.get("script_type") or "").strip().lower()
    if st == "system_shutdown":
        return "shutdown"
    if st == "system_reboot":
        return "reboot"
    sa = (it.get("schedule_action") or "").strip().lower()
    if sa == "reboot":
        return "reboot"
    return "shutdown"


# 基础设施白名单：stop-all / restart-all / 关机 / 重启 时不停这些任务，避免 API 先挂
API_SERVER_SCRIPT_NAME = "WindowsBatchScriptManager_api_server.py"
SERVICE_WHITELIST_BASENAMES = frozenset({
    API_SERVER_SCRIPT_NAME.lower(),
    "windowsbatchscriptmanager_api_server.bat",
})


def get_api_server_script_path() -> Optional[str]:
    """默认 API 脚本路径：程序目录下的 WindowsBatchScriptManager_api_server.py"""
    try:
        app_dir = Path(get_app_path()).resolve().parent
        p = app_dir / API_SERVER_SCRIPT_NAME
        if p.is_file():
            return str(p.resolve())
    except Exception:
        pass
    return None


def find_api_server_pids(owned_popen=None) -> list:
    """查找 API 服务进程 PID（含本工具刚启动的实例）。"""
    pids = []
    if owned_popen is not None:
        try:
            pid = owned_popen.pid
            if pid and is_process_running(pid):
                pids.append(pid)
        except Exception:
            pass
    target = get_api_server_script_path()
    target_nc = os.path.normcase(target) if target else None
    target_base = API_SERVER_SCRIPT_NAME.lower()
    if HAS_PSUTIL:
        for x in scan_running_script_processes():
            sp = (x.get("script_path") or "").strip()
            if not sp:
                continue
            abs_sp = os.path.normcase(os.path.abspath(sp))
            if (target_nc and abs_sp == target_nc) or os.path.basename(sp).lower() == target_base:
                pid = x.get("pid")
                if pid and pid not in pids:
                    pids.append(pid)
    return pids


def stop_api_server_processes(owned_popen=None) -> int:
    """停止所有 API 服务进程，返回停止的根进程数。"""
    stopped = 0
    for pid in find_api_server_pids(owned_popen):
        ok = True
        for px in get_process_tree_pids(pid):
            if not kill_process(px):
                ok = False
        if ok or not is_process_running(pid):
            stopped += 1
    return stopped


def default_python_executable() -> Optional[str]:
    if not getattr(sys, "frozen", False):
        return sys.executable
    import shutil

    return shutil.which("python") or shutil.which("py") or shutil.which("python3")


def probe_api_python(executable: str) -> dict:
    """Check the interpreter selected by the API launcher."""
    code = (
        "import importlib.util, json, sys; "
        "print(json.dumps({'executable': sys.executable, 'version': sys.version.split()[0], "
        "'missing': [name for name in ('fastapi', 'uvicorn') "
        "if importlib.util.find_spec(name) is None]}))"
    )
    result = subprocess.run(
        [executable, "-c", code], capture_output=True, encoding="utf-8",
        errors="replace", timeout=8, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return json.loads(result.stdout)


def start_api_server() -> Optional[subprocess.Popen]:
    """启动 API 服务（默认 py 脚本，独立进程）。"""
    path = get_api_server_script_path()
    if not path:
        return None
    work_dir = str(Path(path).parent)
    return start_script(path, work_dir, "python", detach_on_windows=True, extra_args="--docs")


_API_DEFAULT_PORT = 8765
_API_AUTH_HEADER = "X-API-Key"
_API_POST_PATH_NAMES = frozenset({"start", "stop", "restart", "start-all", "stop-all"})
_API_PATH_MARKERS = ("/list", "/start", "/stop", "/restart", "/status", "/start-all", "/stop-all")
LOCAL_API_GROUP_NAMES = ("本机API", "Local API")
API_CALL_TASK_PRESETS = (
    ("api_task_list", "GET", "/list"),
    ("api_task_status", "GET", "/status?id=1"),
    ("api_task_start", "POST", "/start?id=1"),
    ("api_task_stop", "POST", "/stop?id=1"),
    ("api_task_restart", "POST", "/restart?id=1"),
    ("api_task_start_all", "POST", "/start-all"),
    ("api_task_stop_all", "POST", "/stop-all"),
)


def read_api_server_launch_settings(script_path: Optional[str] = None) -> dict:
    """从 API 脚本常量 / 环境变量读取 Key、鉴权、默认端口（与 GUI 启动方式一致：仅 --docs）。"""
    port = _API_DEFAULT_PORT
    file_default_key = "change-me-in-production"
    disable_auth = False
    path = script_path or get_api_server_script_path()
    text = ""
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            text = ""
    if text:
        m = re.search(
            r'^API_KEY\s*=\s*os\.environ\.get\(\s*["\']API_KEY["\']\s*,\s*["\']([^"\']*)["\']',
            text,
            re.M,
        )
        if m:
            file_default_key = m.group(1)
        else:
            m = re.search(r'^API_KEY\s*=\s*["\']([^"\']*)["\']', text, re.M)
            if m:
                file_default_key = m.group(1)
        m = re.search(r'^DISABLE_AUTH\s*=\s*(True|False)\s*$', text, re.M)
        if m:
            disable_auth = m.group(1) == "True"
        m = re.search(r'add_argument\(\s*["\']--port["\'][^)]*default\s*=\s*(\d+)', text)
        if m:
            try:
                port = int(m.group(1))
            except ValueError:
                pass
    env_key = os.environ.get("API_KEY")
    api_key = env_key if env_key else file_default_key
    return {
        "port": port,
        "api_key": api_key,
        "auth_enabled": not disable_auth,
        "auth_header": _API_AUTH_HEADER,
    }


def _looks_like_ipv4(s: str) -> bool:
    return bool(re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", (s or "").strip()))


def build_api_call_config(lan_ip: Optional[str] = None) -> dict:
    """构造写入 api_config.json 的 call 块。"""
    settings = read_api_server_launch_settings()
    ip = (lan_ip or "").strip()
    if not _looks_like_ipv4(ip):
        got = get_local_ip()
        ip = got if _looks_like_ipv4(got) else "127.0.0.1"
    port = int(settings.get("port") or _API_DEFAULT_PORT)
    base_url = "http://%s:%s" % (ip, port)
    auth_enabled = bool(settings.get("auth_enabled"))
    return {
        "base_url": base_url,
        "port": port,
        "docs_url": base_url + "/",
        "auth_header": settings.get("auth_header") or _API_AUTH_HEADER,
        "auth_enabled": auth_enabled,
        "api_key": (settings.get("api_key") or "") if auth_enabled else "",
    }


def resolve_api_call_config(lan_ip: Optional[str] = None) -> dict:
    """优先用已持久化的 call（含启动时记录的局域网 IP），Key/端口仍以服务端脚本为准。"""
    built = build_api_call_config(lan_ip)
    saved = load_api_config()
    call = saved.get("call") if isinstance(saved, dict) else None
    if not isinstance(call, dict):
        return built
    out = dict(built)
    saved_base = (call.get("base_url") or "").strip().rstrip("/")
    if saved_base:
        out["base_url"] = saved_base
        out["docs_url"] = (call.get("docs_url") or (saved_base + "/")).strip() or (saved_base + "/")
    return out


def api_http_method_for_url(url: str) -> str:
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "http://local" + (raw if raw.startswith("/") else "/" + raw)
    path = (urllib.parse.urlparse(raw).path or "/").rstrip("/")
    name = path.rsplit("/", 1)[-1].lower()
    return "POST" if name in _API_POST_PATH_NAMES else "GET"


def rewrite_url_to_local_api(current: str, call_cfg: dict) -> str:
    """空则填 /list；相对路径加 base；已是本机 API 的绝对 URL 则改 host，其它 host 不覆盖。"""
    base = ((call_cfg or {}).get("base_url") or "").rstrip("/")
    port = int((call_cfg or {}).get("port") or _API_DEFAULT_PORT)
    current = (current or "").strip()
    if not current:
        return base + "/list"
    if current.startswith("/"):
        return base + current
    if current.startswith("http://") or current.startswith("https://"):
        p = urllib.parse.urlparse(current)
        path = p.path or "/"
        path_l = path.lower()
        looks = False
        try:
            looks = p.port == port
        except ValueError:
            looks = False
        if any(path_l == m or path_l.startswith(m + "/") for m in _API_PATH_MARKERS):
            looks = True
        if looks:
            q = ("?" + p.query) if p.query else ""
            return base + path + q
        return current
    return base + "/" + current.lstrip("/")


def format_api_key_headers(api_key: str, auth_enabled: bool) -> str:
    if not auth_enabled:
        return ""
    return "%s: %s" % (_API_AUTH_HEADER, api_key or "")


def ensure_api_key_header_line(existing: str, api_key: str, auth_enabled: bool) -> str:
    if not auth_enabled:
        return existing or ""
    wanted = "%s: %s" % (_API_AUTH_HEADER, api_key or "")
    lines = []
    found = False
    for line in (existing or "").replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("x-api-key"):
            lines.append(wanted)
            found = True
        elif stripped:
            lines.append(line)
    if not found:
        lines.append(wanted)
    return "\n".join(lines)


def _http_endpoint_key(url, method) -> tuple:
    raw = (url or "").strip()
    p = urllib.parse.urlparse(raw)
    path = (p.path or "/").rstrip("/") or "/"
    return (normalize_http_method(method), path.lower(), p.query)


def _is_http_task_item(it: dict) -> bool:
    st = (it.get("script_type") or "")
    path = (it.get("path") or "")
    return st == "http_get" or path.startswith("http://") or path.startswith("https://")


def is_service_whitelisted(path: str) -> bool:
    """路径是否在白名单（按文件名匹配 WindowsBatchScriptManager_api_server.py/.bat）。"""
    p = (path or "").strip()
    if not p:
        return False
    if os.path.basename(p).lower() in SERVICE_WHITELIST_BASENAMES:
        return True
    try:
        app_dir = Path(get_app_path()).resolve().parent
        candidate = Path(p)
        if not candidate.is_absolute():
            candidate = (app_dir / p).resolve()
        else:
            candidate = candidate.resolve()
        return candidate.name.lower() in SERVICE_WHITELIST_BASENAMES
    except Exception:
        return False


def is_protected_service_path(path: str) -> bool:
    """兼容旧名，同 is_service_whitelisted。"""
    return is_service_whitelisted(path)


def _extract_script_path(cmdline: list, exe_lower: str, cwd: str = "") -> Optional[str]:
    r"""
    从进程命令行中解析出脚本路径。
    支持：cmd /c "D:\path\a.bat"、python x.py、node app.js、powershell script.ps1，
    以及 Mac/Linux 上 python3/node/bash/sh/pwsh 等形式。
    """
    if not cmdline:
        return None
    def norm(s: str) -> str:
        return (s or "").strip().strip('"').strip("'")

    # 所有支持的扩展名
    all_exts = tuple(sum(SCRIPT_TYPES.values(), []))
    skip = {
        "/c", "/k", "/x", "-file", "-f", "-executionpolicy", "-bypass", "-nologo", "//nologo",
        "-c", "-lc", "-l", "--", "-m",
    }

    def _resolve_candidate(a: str) -> Optional[str]:
        if not a:
            return None
        al = a.lower()
        # 无扩展名但像路径：Mac/Linux 可执行脚本
        looks_script = al.endswith(all_exts) or (sys.platform != "win32" and ("/" in a) and not a.startswith("-"))
        if not looks_script:
            return None
        if os.path.isfile(a):
            return os.path.abspath(a)
        if re.search(r"[\\/:]", a):
            return a
        if cwd and os.path.isfile(os.path.join(cwd, a)):
            return os.path.normpath(os.path.abspath(os.path.join(cwd, a)))
        return None

    # 解释器在 argv[0]：python3 script.py / node app.js / bash run.sh
    exe0 = norm(cmdline[0] if cmdline else "").lower()
    exe_base = os.path.basename(exe0).replace(".exe", "")
    interp_names = ("python", "python3", "py", "node", "nodejs", "bash", "sh", "zsh", "pwsh", "powershell", "cmd", "cscript", "wscript")
    start_idx = 1
    if exe_base in interp_names or any(x in (exe_lower or exe_base) for x in interp_names):
        start_idx = 1
    elif len(cmdline) < 2:
        return None

    for arg in cmdline[start_idx:]:
        a = norm(arg)
        if not a:
            continue
        if a.lower() in skip:
            continue
        if a.startswith("-") and not a.lower().endswith(all_exts):
            continue
        hit = _resolve_candidate(a)
        if hit:
            return hit
    return None


def scan_running_script_processes() -> list:
    """
    扫描系统中正在运行的脚本进程。
    返回 [{"type":"Python"|"Node"|"PowerShell"|"VBScript"|"批处理"|..., "pid":int, "script_path":str, "cwd":str, "name":str}, ...]
    需要 psutil；无 psutil 时返回 []。
    """
    if not HAS_PSUTIL:
        return []
    my_pid = os.getpid()
    out = []
    type_names = {
        "python": "Python",
        "node": "Node.js",
        "powershell": "PowerShell",
        "vbs": "VBScript",
        "jscript": "JScript",
        "batch": "批处理",
        "shell": "Shell",
        "wsf": "WSF",
    }
    try:
        for p in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
            try:
                if p.pid == my_pid:
                    continue
                info = p.info
                name = (info.get("name") or "").lower()
                cmdline = info.get("cmdline") or []
                if not isinstance(cmdline, (list, tuple)):
                    cmdline = []
                cwd = (info.get("cwd") or "").strip() or ""
                script = _extract_script_path(cmdline, name, cwd)
                if not script:
                    continue
                # 排除本管理器自身
                try:
                    app_path = get_app_path()
                    if os.path.normcase(os.path.abspath(script)) == os.path.normcase(app_path):
                        continue
                except Exception:
                    pass
                # 判断脚本类型（不识别 .exe，避免扫描出过多 exe 进程）
                stype = get_script_type(script)
                if stype == "exe":
                    continue
                # 根据进程名进一步判断（如 node.exe 运行 .js 肯定是 Node.js）
                if stype == "jscript" and ("node" in name or "node.exe" in name):
                    stype = "node"
                elif stype == "unknown":
                    # 根据进程名推断
                    if "python" in name or "py.exe" in name or name == "py":
                        stype = "python"
                    elif "node" in name or "node.exe" in name:
                        stype = "node"
                    elif "powershell" in name or "pwsh" in name:
                        stype = "powershell"
                    elif "cscript" in name or "wscript" in name:
                        stype = "vbs"  # 默认 VBScript
                    elif "cmd" in name or "cmd.exe" in name:
                        stype = "batch"
                    elif "bash" in name or "sh" in name:
                        stype = "shell"
                typ = type_names.get(stype, "批处理")
                base = os.path.splitext(os.path.basename(script))[0] or "未命名"
                out.append({"type": typ, "pid": p.pid, "script_path": script, "cwd": cwd, "name": base})
            except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError, IndexError):
                pass
    except Exception:
        pass
    return out


def scan_running_exe_processes() -> list:
    """
    扫描系统中正在运行的 .exe 进程。
    返回 [{"type": "EXE", "pid": int, "script_path": str, "cwd": str, "name": str}, ...]
    需要 psutil；无 psutil 时返回 []。
    """
    if not HAS_PSUTIL:
        return []
    my_pid = os.getpid()
    out = []
    try:
        for p in psutil.process_iter():
            try:
                if p.pid == my_pid:
                    continue
                exe_path = (p.exe() or "").strip()
                if not exe_path or not exe_path.lower().endswith(".exe"):
                    continue
                try:
                    cwd = (p.cwd() or "").strip()
                except (psutil.AccessDenied, OSError):
                    cwd = ""
                # 排除本管理器自身
                try:
                    app_path = get_app_path()
                    if os.path.normcase(os.path.abspath(exe_path)) == os.path.normcase(app_path):
                        continue
                except Exception:
                    pass
                base = os.path.splitext(os.path.basename(exe_path))[0] or "未命名"
                out.append({"type": "EXE", "pid": p.pid, "script_path": exe_path, "cwd": cwd, "name": base})
            except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError, IndexError, OSError):
                pass
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# 运行脚本
# ---------------------------------------------------------------------------

def parse_http_headers(raw) -> list:
    """解析 HTTP 请求头，每行 Name: Value 或 Name=Value。返回 [(name, value), ...]。"""
    headers = []
    if not raw:
        return headers
    if isinstance(raw, dict):
        for k, v in raw.items():
            k = str(k).strip()
            if k:
                headers.append((k, "" if v is None else str(v)))
        return headers
    for line in str(raw).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
        elif "=" in line:
            k, v = line.split("=", 1)
        else:
            continue
        k, v = k.strip(), v.strip()
        if k:
            headers.append((k, v))
    return headers


def normalize_http_method(method) -> str:
    m = (method or "GET").strip().upper()
    return m if m in ("GET", "POST") else "GET"


def build_http_request_curl_args(curl_exe: str, url: str, method=None, headers_raw=None, capture_response=False) -> list:
    """构造 curl 参数（缺省 GET、无额外请求头）。"""
    args = [curl_exe, "-s", "-o", os.devnull, "-w", "%{http_code}", "--max-time", "30", "--connect-timeout", "10"]
    if capture_response:
        args = [curl_exe, "-sS", "-w", "\nHTTP %{http_code}\n", "--max-time", "30",
                "--connect-timeout", "10", "--max-filesize", "1048576"]
    method = normalize_http_method(method)
    if method != "GET":
        args.extend(["-X", method])
    for k, v in parse_http_headers(headers_raw):
        args.extend(["-H", "%s: %s" % (k, v)])
    args.append(url)
    return args


def start_script(
    path: str,
    work_dir: Optional[str],
    script_type: str,
    interpreter: Optional[str] = None,
    log_path: Optional[str] = None,
    detach_on_windows: bool = False,
    extra_args: Optional[str] = None,
    env_vars: Optional[str] = None,
    http_method: Optional[str] = None,
    http_headers: Optional[str] = None,
    capture_http: bool = False,
) -> Optional[subprocess.Popen]:
    path = (path or "").strip()
    _set_start_error("")
    if not path:
        _set_start_error("empty path")
        return None

    st = script_type or ""
    is_http_get = st == "http_get" or path.startswith(("http://", "https://"))
    is_process = st == "process" or (not is_http_get and is_process_name_str(path))
    is_command = st == "command"
    # HTTP/进程名/直接命令在 main 内跨平台处理，不走 platform_impl 文件脚本逻辑
    if _PLATFORM and not (is_http_get or is_process or is_command):
        p = _PLATFORM.start_script(
            path, work_dir, script_type, interpreter, log_path,
            get_app_path(), get_script_type,
            extra_args=extra_args,
            env_vars=env_vars,
        )
        if not p:
            _set_start_error("platform start failed (interpreter/path/permission?)")
        return p

    # 进程名类型：path 为进程名（如 explorer.exe），直接启动
    if is_command:
        # 直接命令：Windows 用 cmd.exe /c；Mac/Linux 用 bash -lc
        full_cmd = path
        if extra_args and extra_args.strip():
            full_cmd = full_cmd + " " + extra_args.strip()
        if sys.platform == "win32":
            args = ["cmd.exe", "/c", full_cmd]
        else:
            import shutil
            shell = shutil.which("bash") or "/bin/bash"
            args = [shell, "-lc", full_cmd]
        cwd = work_dir if work_dir else None
    elif is_process:
        args = [path]
        if extra_args and extra_args.strip():
            from task_features import split_arguments
            args.extend(split_arguments(extra_args))
        cwd = work_dir or None
    elif is_http_get:
        import shutil
        # Windows 10+ 自带 curl.exe，Linux/Mac 也都有
        method = normalize_http_method(http_method)
        headers_raw = http_headers or ""
        curl_exe = shutil.which("curl")
        if curl_exe:
            args = build_http_request_curl_args(curl_exe, path, method, headers_raw, capture_response=capture_http)
        else:
            # 回退：查找系统 python 执行 urllib 请求
            python_exe = shutil.which("python") or shutil.which("python3") or shutil.which("py")
            # 使用 json.dumps 安全转义，避免单引号注入
            _safe_url = json.dumps(path)
            _safe_method = json.dumps(method)
            _safe_headers = json.dumps(dict(parse_http_headers(headers_raw)))
            _py = (
                "import urllib.request;"
                "req=urllib.request.Request(%s,data=(b'' if %s!='GET' else None),method=%s,headers=%s);"
            ) % (_safe_url, _safe_method, _safe_method, _safe_headers)
            if capture_http:
                _py += (
                    "\nimport sys\nfrom urllib.error import HTTPError\n"
                    "try:\n r=urllib.request.urlopen(req,timeout=30)\nexcept HTTPError as exc:\n r=exc\n"
                    "with r:\n sys.stdout.buffer.write(r.read(65537))\n print(f'\\nHTTP {r.status}', flush=True)\n",
                )
                if not python_exe and getattr(sys, "frozen", False):
                    _set_start_error("HTTP: curl or Python is required")
                    return None
            else:
                _py += "r=urllib.request.urlopen(req,timeout=30); print(r.status)"
            if python_exe:
                args = [python_exe, "-c", _py]
            elif not getattr(sys, 'frozen', False):
                # 开发环境可用当前解释器
                args = [sys.executable, "-c", _py]
            else:
                # 打包后的 exe 且无 curl、无 python：写临时脚本用 urllib 发请求
                import tempfile
                tmp_script = os.path.join(tempfile.gettempdir(), f"_http_get_{os.getpid()}_{int(time.time())}.py")
                try:
                    with open(tmp_script, "w", encoding="utf-8") as f:
                        f.write(_py + "; import os,sys; os.remove(__file__); sys.exit(0)")
                    args = [sys.executable, tmp_script]
                except Exception:
                    _set_start_error("http request helper create failed")
                    return None
        cwd = work_dir if work_dir else None
    else:
        if work_dir and not os.path.isabs(path):
            path = os.path.normpath(os.path.join(work_dir, path))
        if not os.path.isfile(path):
            _set_start_error("path not found: %s" % path)
            return None
        cwd = work_dir or os.path.dirname(path)
        if not os.path.isdir(cwd):
            cwd = None

        abs_path = os.path.abspath(path)

        # 防止启动程序本身（会启动新的管理器实例）
        app_path = get_app_path()
        if os.path.normcase(abs_path) == os.path.normcase(app_path):
            # 不启动程序本身
            _set_start_error("refusing to start manager itself")
            return None

        script_type = script_type or get_script_type(path)

        # 根据脚本类型构建执行命令
        if script_type == "python":
            exe = (interpreter or "").strip()
            if exe:
                if exe.startswith("py ") or exe.startswith("python "):
                    parts = exe.split()
                    args = parts + [abs_path]
                else:
                    args = [exe, abs_path]
            else:
                # 如果未指定解释器，尝试使用系统 Python
                # 在 PyInstaller 打包的 exe 中，sys.executable 指向 exe 本身，不能用于运行 Python 脚本
                python_exe = default_python_executable()
                if not python_exe:
                    _set_start_error("python not found (install Python or set interpreter)")
                    return None
                args = [python_exe, abs_path]
        elif script_type == "node":
            exe = (interpreter or "").strip() or "node"
            args = [exe, abs_path]
        elif script_type == "powershell":
            args = [(interpreter or "").strip() or "powershell.exe", "-ExecutionPolicy", "Bypass", "-File", abs_path]
        elif script_type == "vbs":
            args = ["cscript.exe", "//NoLogo", abs_path]
        elif script_type == "jscript":
            args = ["cscript.exe", "//NoLogo", abs_path]
        elif script_type == "wsf":
            args = ["cscript.exe", "//NoLogo", abs_path]
        elif script_type == "shell":
            # Git Bash 或 WSL
            bash_exe = (interpreter or "").strip() or "bash"
            args = [bash_exe, abs_path]
        elif script_type == "exe":
            # 可执行文件直接运行
            args = [abs_path]
        else:  # batch (.bat, .cmd) 或 unknown
            args = ["cmd.exe", "/c", abs_path]

        # 追加额外启动参数
        if extra_args and extra_args.strip():
            from task_features import split_arguments
            args.extend(split_arguments(extra_args))
    
    # 构建环境变量
    proc_env = None
    if env_vars and env_vars.strip():
        proc_env = os.environ.copy()
        for line in env_vars.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                proc_env[k.strip()] = v.strip()
    
    if getattr(sys, "frozen", False) or "_PYI_ARCHIVE_FILE" in os.environ:
        if proc_env is None:
            proc_env = os.environ.copy()
        # Managed jobs and the API service can outlive this application's bundle.
        proc_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"

    if capture_http and is_http_get:
        import tempfile

        try:
            with tempfile.TemporaryFile() as response:
                process = subprocess.Popen(
                    args, cwd=cwd or None, env=proc_env, stdin=subprocess.DEVNULL,
                    stdout=response, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                process.http_response = os.fdopen(os.dup(response.fileno()), "rb")
                return process
        except OSError as exc:
            _set_start_error(f"HTTP: {exc}")
            return None

    # 记录输出到文件
    use_log = bool(log_path)
    if use_log:
        try:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            # HTTP 请求/进程名类型：静默执行，不弹控制台窗口
            log_flags = 0
            if (is_http_get or is_process) and sys.platform == "win32":
                log_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            lf = open(log_path, "w", encoding="utf-8", errors="replace")
            p = subprocess.Popen(
                args,
                cwd=cwd or None,
                creationflags=log_flags,
                stdout=lf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=proc_env,
            )
            lf.close()
            return p
        except Exception as e:
            _set_start_error("Popen(log) failed: %s" % e)
            return None
    
    # Windows：detach_on_windows 时使用 DETACHED_PROCESS，子进程立即独立运行，避免「关掉工具后任务才启动」
    # 不用 close_fds=True，避免连续启动多个子进程时第二个及后续 Popen 失败
    if sys.platform == "win32" and detach_on_windows:
        detach = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        try:
            p = subprocess.Popen(
                args,
                cwd=cwd or None,
                creationflags=detach | new_group,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=proc_env,
            )
            return p
        except Exception as e:
            _set_start_error("Popen(detach) failed: %s" % e)
            return None
    # 独立控制台运行（默认）
    # HTTP GET / 进程名类型不弹控制台窗口，静默执行
    if is_http_get or is_process:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            p = subprocess.Popen(
                args,
                cwd=cwd or None,
                creationflags=creationflags,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=proc_env,
            )
            return p
        except Exception as e:
            _set_start_error("Popen failed: %s" % e)
            return None
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        p = subprocess.Popen(
            args,
            cwd=cwd or None,
            creationflags=creationflags,
            env=proc_env,
        )
        return p
    except Exception as e:
        _set_start_error("Popen failed: %s" % e)
        return None


# ---------------------------------------------------------------------------
# GUI：自动识别（扫描运行中的批处理/Python）
# ---------------------------------------------------------------------------

class ScanRunningDialog(tk.Toplevel):
    """自动识别：扫描并列出系统中正在运行的批处理/Python 脚本进程；或仅 exe（识别exe加入）。"""

    def __init__(self, parent, app: "BatchManagerApp", exe_only: bool = False):
        super().__init__(parent)
        self.app = app
        self._lang = app._lang
        self._exe_only = exe_only
        self.title(self._lang["dialog_scan_exe"] if exe_only else self._lang["dialog_scan_running"])
        self.geometry("800x420")
        self._scan_list: list = []

        ttk.Label(self, text=self._lang["label_scan_result"], font=("", 9, "bold")).pack(side=tk.LEFT, padx=(6, 2), pady=6)
        ttk.Button(self, text=self._lang["btn_refresh_dialog"], command=self._refresh).pack(side=tk.LEFT, padx=2, pady=6)
        ttk.Button(self, text=self._lang["btn_add_all"], command=self._on_add_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(self, text=self._lang["btn_add_selected"], command=self._on_add).pack(side=tk.LEFT, padx=2)
        ttk.Button(self, text=self._lang["btn_kill_selected"], command=self._on_kill).pack(side=tk.LEFT, padx=2)

        cols = ("typ", "path", "pid", "cwd", "ports")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=16, selectmode="extended")
        col_headings = {
            "typ": self._lang["col_type"],
            "path": self._lang["col_path"],
            "pid": self._lang["col_pid"],
            "cwd": self._lang["col_work_dir"],
            "ports": self._lang["col_port"],
        }
        for c in cols:
            self.tree.heading(c, text=col_headings.get(c, c))
        self.tree.column("typ", width=64)
        self.tree.column("path", width=320)
        self.tree.column("pid", width=72)
        self.tree.column("cwd", width=180)
        self.tree.column("ports", width=100)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        self._refresh()

    def _refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        self._scan_list = scan_running_exe_processes() if self._exe_only else scan_running_script_processes()
        if not self._scan_list and not HAS_PSUTIL:
            self.tree.insert("", tk.END, values=("—", self._lang["msg_scan_need_psutil_detail"], "", "", ""))
            return
        if not self._scan_list:
            msg = self._lang["msg_scan_exe_no_processes_detail"] if self._exe_only else self._lang["msg_scan_no_processes_detail"]
            self.tree.insert("", tk.END, values=("—", msg, "", "", ""))
            return
        for r in self._scan_list:
            pids = get_process_tree_pids(r["pid"])
            ports = get_ports_for_pids(pids)
            ports_s = ",".join(map(str, ports)) if ports else "-"
            path = (r["script_path"] or "")[:70] + ("..." if len(r["script_path"] or "") > 70 else "")
            self.tree.insert("", tk.END, values=(r["type"], path, r["pid"], (r.get("cwd") or "-")[:50], ports_s))

    def _get_selected(self) -> list:
        sel = self.tree.selection()
        return [self.tree.index(i) for i in sel]

    def _on_kill(self):
        for i in self._get_selected():
            if 0 <= i < len(self._scan_list):
                rec = self._scan_list[i]
                pids = get_process_tree_pids(rec["pid"])
                for px in pids:
                    kill_process(px)
        self._refresh()

    def _on_add(self):
        rows = self._get_selected()
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_table_rows"], parent=self)
            return
        if not self._scan_list:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_no_processes"], parent=self)
            return
        added = 0
        for i in rows:
            if 0 <= i < len(self._scan_list):
                rec = self._scan_list[i]
                path = rec.get("script_path", "").strip()
                if not path:
                    continue
                self.app._add_from_scan(path, rec.get("cwd", "") or "", rec.get("type") == "Python", rec.get("pid"))
                added += 1
        self.app._sync_tree()
        if added == 0:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_no_items_added"], parent=self)
            return
        self.app.status_var.set(self._lang["msg_scan_added_from"] % added)
        self.app.root.deiconify()
        self.app.root.lift()
        self.app.root.focus_force()
        self.app.root.update_idletasks()

    def _on_add_all(self):
        """把本次扫描到的所有项一次性加入管理器；路径已存在的会跳过。"""
        if not self._scan_list:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_no_processes_refresh"], parent=self)
            return
        existing = {it.get("path", "") for it in self.app.items}
        added, skipped = 0, 0
        for rec in self._scan_list:
            path = rec.get("script_path", "").strip()
            if not path:
                continue
            if path in existing:
                skipped += 1
                continue
            self.app._add_from_scan(path, rec.get("cwd", "") or "", rec.get("type") == "Python", rec.get("pid"))
            existing.add(path)
            added += 1
        self.app._sync_tree()
        msg = self._lang["msg_scan_added"] % added
        if skipped:
            msg += self._lang["msg_scan_skipped_detail"] % skipped
        self.app.status_var.set(self._lang["msg_scan_prefix"] + msg)
        skipped_msg = (self._lang["msg_skipped_paths"] % skipped) if skipped else ""
        messagebox.showinfo(self._lang["msg_complete"], self._lang["msg_added_scripts"] % (added, skipped_msg), parent=self)
        self.app.root.deiconify()
        self.app.root.lift()
        self.app.root.focus_force()
        self.app.root.update_idletasks()


# ---------------------------------------------------------------------------
# GUI：添加/编辑 弹窗
# ---------------------------------------------------------------------------

class AddEditDialog(tk.Toplevel):
    """添加或编辑一条脚本的弹窗表单。"""

    def __init__(self, parent, mode: str, initial: dict, get_next_run=None):
        super().__init__(parent)
        self.mode = mode
        self.result = None
        self._get_next_run = get_next_run  # 主窗口的 _get_next_schedule_run，用于预览“下次约”
        # 获取语言字典：从 BatchManagerApp 实例获取
        # parent 是 root (Tk)，需要找到 BatchManagerApp 实例
        app_instance = None
        # 获取语言字典：直接从保存的语言配置读取（确保使用最新语言）
        current_lang = load_language()
        if current_lang not in LANGUAGES:
            current_lang = "zh-CN"
        self._current_language = current_lang
        self._lang = LANGUAGES[current_lang]
        self.title(self._lang["dialog_edit"] if mode == "edit" else self._lang["dialog_add_edit"])
        self.geometry("620x520")
        self.minsize(560, 420)
        self.transient(parent)
        self.grab_set()

        self._feature_initial = dict(initial)
        self.var_name = tk.StringVar(value=initial.get("name", ""))
        self.var_path = tk.StringVar(value=initial.get("path", ""))
        _init_http_method = (initial.get("http_method") or "GET").strip().upper()
        if _init_http_method not in ("GET", "POST"):
            _init_http_method = "GET"
        self.var_http_method = tk.StringVar(value=_init_http_method)
        self.var_http_headers = tk.StringVar(value=initial.get("http_headers", "") or "")
        self.var_work = tk.StringVar(value=initial.get("work_dir", ""))
        self.var_group = tk.StringVar(value=initial.get("group", ""))
        self.var_interpreter = tk.StringVar(value=initial.get("interpreter", "") or initial.get("python_exe", ""))
        self.var_auto = tk.BooleanVar(value=bool(initial.get("auto_restart", False)))
        self.var_launch_on_app_start = tk.BooleanVar(value=bool(initial.get("launch_on_app_start", False)))
        self.var_schedule_enabled = tk.BooleanVar(value=bool(initial.get("schedule_enabled", False)))
        self.var_depends_on = tk.StringVar(value=initial.get("depends_on", ""))
        self.var_delay_after_dep = tk.StringVar(value=str(initial.get("delay_after_dep", 0)))
        self.var_args = tk.StringVar(value=initial.get("args", ""))
        self.var_env_vars = tk.StringVar(value=initial.get("env_vars", ""))
        self.var_startup_timeout = tk.StringVar(value=str(initial.get("startup_timeout", 0)))
        self.var_schedule_valid_from = tk.StringVar(value=initial.get("schedule_valid_from", ""))
        self.var_schedule_valid_until = tk.StringVar(value=initial.get("schedule_valid_until", ""))
        self.var_schedule_max_runs = tk.StringVar(value=str(initial.get("schedule_max_runs", 0)))
        self.var_schedule_pre_delay = tk.StringVar(value=str(initial.get("schedule_pre_delay", 0)))
        # 类型转换：英文 -> 当前语言
        schedule_type_en = initial.get("schedule_type", "daily")
        schedule_type_map = {
            "daily": self._lang["schedule_type_daily"],
            "hourly": self._lang["schedule_type_hourly"],
            "minutely": self._lang["schedule_type_minutely"],
            "weekly": self._lang["schedule_type_weekly"],
            "monthly": self._lang["schedule_type_monthly"],
            "every_hours": self._lang["schedule_type_every_hours"],
            "quarterly": self._lang["schedule_type_quarterly"],
            "halfyearly": self._lang["schedule_type_halfyearly"],
            "yearly": self._lang["schedule_type_yearly"],
            "once": self._lang["schedule_type_once"],
            "weekday": self._lang["schedule_type_weekday"],
            "weekend": self._lang["schedule_type_weekend"],
        }
        # 反向映射：中文/英文 -> 英文
        reverse_type_map = {
            self._lang["schedule_type_daily"]: "daily",
            self._lang["schedule_type_hourly"]: "hourly",
            self._lang["schedule_type_minutely"]: "minutely",
            self._lang["schedule_type_weekly"]: "weekly",
            self._lang["schedule_type_monthly"]: "monthly",
            self._lang["schedule_type_every_hours"]: "every_hours",
            self._lang["schedule_type_quarterly"]: "quarterly",
            self._lang["schedule_type_halfyearly"]: "halfyearly",
            self._lang["schedule_type_yearly"]: "yearly",
            self._lang["schedule_type_once"]: "once",
            self._lang["schedule_type_weekday"]: "weekday",
            self._lang["schedule_type_weekend"]: "weekend",
            "每天": "daily", "每小时": "hourly", "每分钟": "minutely", "每周": "weekly", "每月": "monthly",
            "每季度": "quarterly", "每半年": "halfyearly", "每年": "yearly", "单次执行": "once", "工作日": "weekday", "每周末": "weekend", "工作日(1-5)": "weekday", "每周末(6-7)": "weekend",
            "Daily": "daily", "Hourly": "hourly", "Minutely": "minutely", "Weekly": "weekly", "Monthly": "monthly",
            "Quarterly": "quarterly", "Half-Yearly": "halfyearly", "Yearly": "yearly", "Once": "once", "Weekday": "weekday", "Weekend": "weekend", "Weekday(1-5)": "weekday", "Weekend(6-7)": "weekend"
        }
        default_type = self._lang["schedule_type_daily"]
        self.var_schedule_type = tk.StringVar(value=schedule_type_map.get(schedule_type_en, reverse_type_map.get(schedule_type_en, default_type) if schedule_type_en in reverse_type_map else default_type))
        self.var_schedule_time = tk.StringVar(value=initial.get("schedule_time", "09:00"))
        self.var_schedule_interval = tk.StringVar(value=str(initial.get("schedule_interval", 5)))
        # 星期转换：数字 -> 当前语言
        schedule_weekday_val = initial.get("schedule_weekday", 0)
        weekday_map = {
            0: self._lang["weekday_mon"],
            1: self._lang["weekday_tue"],
            2: self._lang["weekday_wed"],
            3: self._lang["weekday_thu"],
            4: self._lang["weekday_fri"],
            5: self._lang["weekday_sat"],
            6: self._lang["weekday_sun"]
        }
        # 反向映射
        reverse_weekday_map = {
            self._lang["weekday_mon"]: 0, self._lang["weekday_tue"]: 1, self._lang["weekday_wed"]: 2,
            self._lang["weekday_thu"]: 3, self._lang["weekday_fri"]: 4, self._lang["weekday_sat"]: 5, self._lang["weekday_sun"]: 6,
            "周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6,
            "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6
        }
        self.reverse_weekday_map = reverse_weekday_map
        default_weekday = self._lang["weekday_mon"]
        if isinstance(schedule_weekday_val, (int, str)) and (isinstance(schedule_weekday_val, int) or schedule_weekday_val.isdigit()):
            self.var_schedule_weekday = tk.StringVar(value=weekday_map.get(int(schedule_weekday_val), default_weekday))
        else:
            self.var_schedule_weekday = tk.StringVar(value=str(schedule_weekday_val) if schedule_weekday_val in weekday_map.values() or schedule_weekday_val in reverse_weekday_map else default_weekday)
        # 每月几号（1-30）或月末（31）
        schedule_day_val = initial.get("schedule_day", 1)
        try:
            d = int(schedule_day_val)
            if d == 31:
                self.var_schedule_day = tk.StringVar(value=self._lang["schedule_day_last"])
            else:
                d = max(1, min(30, d))
                self.var_schedule_day = tk.StringVar(value=str(d))
        except (TypeError, ValueError):
            self.var_schedule_day = tk.StringVar(value="1")
        # 季度选择（每季度用）
        schedule_quarter_val = initial.get("schedule_quarter", "q1")
        quarter_map = {"q1": self._lang["schedule_quarter_q1"], "q2": self._lang["schedule_quarter_q2"], "q3": self._lang["schedule_quarter_q3"], "q4": self._lang["schedule_quarter_q4"]}
        reverse_quarter_map = {v: k for k, v in quarter_map.items()}
        reverse_quarter_map.update({"第一季度(1月)": "q1", "第二季度(4月)": "q2", "第三季度(7月)": "q3", "第四季度(10月)": "q4",
                                    "Q1 (Jan)": "q1", "Q2 (Apr)": "q2", "Q3 (Jul)": "q3", "Q4 (Oct)": "q4"})
        self.var_schedule_quarter = tk.StringVar(value=quarter_map.get(schedule_quarter_val, reverse_quarter_map.get(schedule_quarter_val, quarter_map["q1"])))
        # 月份选择（每年用）
        schedule_month_val = initial.get("schedule_month", 1)
        try:
            m = max(1, min(12, int(schedule_month_val)))
        except (TypeError, ValueError):
            m = 1
        self.var_schedule_month = tk.StringVar(value=str(m))
        # 单次执行日期
        self.var_schedule_once_date = tk.StringVar(value=initial.get("schedule_once_date", ""))
        # 定时任务行为：启动、重启、停止
        schedule_action_en = initial.get("schedule_action", "start")
        schedule_action_map = {
            "start": self._lang["schedule_action_start"],
            "restart": self._lang["schedule_action_restart"],
            "stop": self._lang["schedule_action_stop"],
        }
        reverse_action_map = {
            self._lang["schedule_action_start"]: "start",
            self._lang["schedule_action_restart"]: "restart",
            self._lang["schedule_action_stop"]: "stop",
            "启动": "start", "重启": "restart", "重启任务": "restart", "停止": "stop",
            "Start": "start", "Restart": "restart", "Restart Task": "restart", "Stop": "stop",
            # 兼容旧配置
            "关机": "shutdown", "重启系统": "reboot", "Shutdown": "shutdown", "Reboot System": "reboot",
        }
        default_action = self._lang["schedule_action_start"]
        self.var_schedule_action = tk.StringVar(value=schedule_action_map.get(schedule_action_en, reverse_action_map.get(schedule_action_en, default_action) if schedule_action_en in reverse_action_map else default_action))

        # ===== 使用 Notebook 标签页，避免内容溢出导致按钮不可见 =====
        # 底部按钮始终可见（先 pack 按钮，再 pack Notebook）
        btn_frame = ttk.Frame(self)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=(4, 10))
        ttk.Button(btn_frame, text=self._lang["btn_ok"], command=self._on_ok).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(btn_frame, text=self._lang["btn_cancel"], command=self.destroy).pack(side=tk.RIGHT)

        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6, 2))

        # ===== 标签页1：基本 =====
        tab_basic = ttk.Frame(self._notebook)
        self._notebook.add(tab_basic, text=self._lang["tab_basic"])
        tab_basic.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(tab_basic, text=self._lang["label_name"]).grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(tab_basic, textvariable=self.var_name, width=50).grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        row += 1

        # ===== 任务类型下拉框 =====
        # 优先使用 initial 中的 script_type，否则根据路径推断
        _init_stype = initial.get("script_type") or get_script_type(initial.get("path", ""))
        _type_options = [
            ("script", self._lang["label_script_path"].rstrip(":：")),
            ("http_get", "HTTP" + ("请求" if self._lang == LANGUAGES["zh-CN"] else " GET")),
            ("process", "进程管理" if self._lang == LANGUAGES["zh-CN"] else "Process"),
            ("command", "直接命令" if self._lang == LANGUAGES["zh-CN"] else "Command"),
            ("system_shutdown", self._lang["task_type_system_shutdown"]),
            ("system_reboot", self._lang["task_type_system_reboot"]),
        ]
        _type_display_map = {k: v for k, v in _type_options}
        _type_reverse_map = {v: k for k, v in _type_options}
        # 也支持中英文混搜
        _type_reverse_map.update({"脚本路径": "script", "Script Path": "script",
                                   "HTTP请求": "http_get", "HTTP GET": "http_get",
                                   "进程管理": "process", "Process": "process",
                                   "直接命令": "command", "Command": "command",
                                   "系统关机": "system_shutdown", "System Shutdown": "system_shutdown",
                                   "系统重启": "system_reboot", "System Reboot": "system_reboot"})
        if _init_stype in SYSTEM_POWER_SCRIPT_TYPES:
            _init_type_key = _init_stype
        elif _init_stype in ("shutdown", "reboot"):
            _init_type_key = "system_shutdown" if _init_stype == "shutdown" else "system_reboot"
        elif initial.get("schedule_action") in ("shutdown", "reboot"):
            _init_type_key = "system_shutdown" if initial.get("schedule_action") == "shutdown" else "system_reboot"
        else:
            _init_type_key = _init_stype if _init_stype in ("http_get", "process", "command") else "script"
        self.var_task_type = tk.StringVar(value=_type_display_map.get(_init_type_key, _type_options[0][1]))

        ttk.Label(tab_basic, text=self._lang["label_task_type"]).grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        ftype = ttk.Frame(tab_basic)
        ftype.grid(row=row, column=1, sticky=tk.W, padx=4, pady=4)
        self.combo_task_type = ttk.Combobox(ftype, textvariable=self.var_task_type,
                                             values=[v for _, v in _type_options],
                                             state="readonly", width=18)
        self.combo_task_type.pack(side=tk.LEFT)
        self._type_display_map = _type_display_map
        self._type_reverse_map = _type_reverse_map
        self.combo_task_type.bind("<<ComboboxSelected>>", self._on_task_type_changed)
        ttk.Button(ftype, text=self._lang["btn_auto_detect"], command=self._on_auto_detect, width=10).pack(side=tk.LEFT, padx=(6, 0))
        row += 1

        # ===== 路径/URL/进程名 输入行 =====
        self.label_path = ttk.Label(tab_basic, text=self._lang["label_script_path"])
        self.label_path.grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        fpath = ttk.Frame(tab_basic)
        fpath.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        self.entry_path = ttk.Entry(fpath, textvariable=self.var_path, width=44)
        self.entry_path.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.btn_browse = ttk.Button(fpath, text=self._lang["btn_browse"], command=self._on_browse_path, width=6)
        self.btn_browse.pack(side=tk.LEFT, padx=2)
        # 路径提示（独占一行，放在输入框下方，不影响输入框宽度）
        row += 1
        self.label_path_hint = ttk.Label(tab_basic, text="", foreground="gray")
        self.label_path_hint.grid(row=row, column=0, columnspan=3, sticky=tk.W, padx=6, pady=(0, 2))
        row += 1
        self._http_opts_frame = ttk.Frame(tab_basic)
        self._http_opts_frame.grid(row=row, column=0, columnspan=3, sticky=tk.EW, padx=6, pady=2)
        self._http_opts_frame.columnconfigure(1, weight=1)
        ttk.Label(self._http_opts_frame, text=self._lang["label_http_method"]).grid(row=0, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        http_method_row = ttk.Frame(self._http_opts_frame)
        http_method_row.grid(row=0, column=1, sticky=tk.W, padx=4, pady=2)
        self.combo_http_method = ttk.Combobox(
            http_method_row, textvariable=self.var_http_method,
            values=["GET", "POST"], state="readonly", width=10,
        )
        self.combo_http_method.pack(side=tk.LEFT)
        self.btn_fill_local_api = ttk.Button(
            http_method_row, text=self._lang["btn_fill_local_api"],
            command=self._on_fill_local_api,
        )
        self.btn_fill_local_api.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(self._http_opts_frame, text=self._lang["label_http_headers"]).grid(row=1, column=0, sticky=tk.NW, padx=(0, 6), pady=2)
        ttk.Entry(self._http_opts_frame, textvariable=self.var_http_headers, width=50).grid(row=1, column=1, sticky=tk.EW, padx=4, pady=2)
        ttk.Label(self._http_opts_frame, text=self._lang["hint_http_headers"], foreground="gray").grid(row=2, column=1, sticky=tk.W, padx=4, pady=(0, 2))
        row += 1
        ttk.Label(tab_basic, text=self._lang["label_work_dir"]).grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        fwork = ttk.Frame(tab_basic)
        fwork.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        ttk.Entry(fwork, textvariable=self.var_work, width=44).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(fwork, text=self._lang["btn_browse"], command=self._on_browse_dir, width=6).pack(side=tk.LEFT, padx=2)
        row += 1
        ttk.Label(tab_basic, text=self._lang["label_group"]).grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(tab_basic, textvariable=self.var_group, width=50).grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        ttk.Label(tab_basic, text=self._lang["hint_group_empty"]).grid(row=row, column=2, sticky=tk.W, padx=2)
        row += 1
        self.label_type = ttk.Label(tab_basic, text=self._type_from_path(self.var_path.get()))
        self.label_type.grid(row=row, column=1, sticky=tk.W, padx=4, pady=4)
        row += 1
        self.label_interpreter = ttk.Label(tab_basic, text=self._lang["label_interpreter"])
        self.label_interpreter.grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        self.entry_interpreter = ttk.Entry(tab_basic, textvariable=self.var_interpreter, width=50)
        self.entry_interpreter.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        from task_feature_ui import choose_environment
        ttk.Button(tab_basic, text=translate_inline(current_lang, "选择环境", "Environment"), command=lambda: choose_environment(self, self.var_path.get(), self.var_work.get(), self.var_interpreter, self._feature_initial, current_lang)).grid(row=row, column=2, padx=2)
        self.label_interpreter_hint = ttk.Label(tab_basic, text="")
        self.label_interpreter_hint.grid(row=row, column=3, sticky=tk.W, padx=2)
        row += 1
        self.var_log = tk.BooleanVar(value=bool(initial.get("log_output", False)))
        self.cb_log = ttk.Checkbutton(tab_basic, text=self._lang["check_log_output"], variable=self.var_log)
        self.cb_log.grid(row=row, column=1, sticky=tk.W, padx=4, pady=4)
        row += 1
        self._check_auto_restart = ttk.Checkbutton(tab_basic, text=self._lang["check_auto_restart"], variable=self.var_auto)
        self._check_auto_restart.grid(row=row, column=1, sticky=tk.W, padx=4, pady=4)
        # 如果初始类型为 HTTP 请求，禁用保活
        _init_stype_check = initial.get("script_type") or (get_script_type(initial.get("path", "")) if initial.get("path") else "")
        if _init_stype_check == "http_get":
            self._check_auto_restart.config(state=tk.DISABLED)
            self.var_auto.set(False)
        row += 1
        ttk.Checkbutton(tab_basic, text=self._lang["check_launch_on_app_start"], variable=self.var_launch_on_app_start).grid(row=row, column=1, sticky=tk.W, padx=4, pady=4)
        row += 1
        # 启动参数
        ttk.Label(tab_basic, text=self._lang["label_args"]).grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        fargs = ttk.Frame(tab_basic)
        fargs.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        ttk.Entry(fargs, textvariable=self.var_args, width=44).pack(side=tk.LEFT, fill=tk.X, expand=True)
        row += 1
        ttk.Label(tab_basic, text=self._lang["hint_args"], foreground="gray").grid(row=row, column=1, sticky=tk.W, padx=4)
        row += 1
        # 环境变量
        ttk.Label(tab_basic, text=self._lang["label_env_vars"]).grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        fenv = ttk.Frame(tab_basic)
        fenv.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        ttk.Entry(fenv, textvariable=self.var_env_vars, width=44).pack(side=tk.LEFT, fill=tk.X, expand=True)
        row += 1
        ttk.Label(tab_basic, text=self._lang["hint_env_vars"], foreground="gray").grid(row=row, column=1, sticky=tk.W, padx=4)
        row += 1
        # 启动超时
        ttk.Label(tab_basic, text=self._lang["label_startup_timeout"]).grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        ftimeout = ttk.Frame(tab_basic)
        ftimeout.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        ttk.Entry(ftimeout, textvariable=self.var_startup_timeout, width=10).pack(side=tk.LEFT)
        ttk.Label(ftimeout, text=self._lang["hint_startup_timeout"], foreground="gray").pack(side=tk.LEFT, padx=(4, 0))
        row += 1
        # 依赖链
        ttk.Label(tab_basic, text=self._lang["label_depends_on"]).grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        fdep = ttk.Frame(tab_basic)
        fdep.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        ttk.Entry(fdep, textvariable=self.var_depends_on, width=30).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(fdep, text=self._lang["label_delay_after_dep"]).pack(side=tk.LEFT, padx=(8, 2))
        ttk.Entry(fdep, textvariable=self.var_delay_after_dep, width=6).pack(side=tk.LEFT)
        row += 1
        ttk.Label(tab_basic, text=self._lang["hint_depends_on"], foreground="gray").grid(row=row, column=1, sticky=tk.W, padx=4)

        # ===== 标签页2：定时 =====
        tab_schedule = ttk.Frame(self._notebook)
        self._notebook.add(tab_schedule, text=self._lang["tab_schedule"])

        ttk.Checkbutton(tab_schedule, text=self._lang["check_schedule_enabled"], variable=self.var_schedule_enabled, command=self._update_schedule_visible).grid(row=0, column=0, columnspan=4, sticky=tk.W, padx=6, pady=4)

        self.frame_schedule = ttk.Frame(tab_schedule)
        self.frame_schedule.grid(row=1, column=0, columnspan=4, sticky=tk.NSEW, padx=6, pady=4)
        tab_schedule.columnconfigure(0, weight=1)
        tab_schedule.columnconfigure(1, weight=1)
        tab_schedule.columnconfigure(2, weight=1)
        tab_schedule.columnconfigure(3, weight=1)
        self.frame_schedule.columnconfigure(0, weight=1)
        self.frame_schedule.columnconfigure(1, weight=1)
        self.frame_schedule.columnconfigure(2, weight=1)
        self.frame_schedule.columnconfigure(3, weight=1)
        ttk.Label(self.frame_schedule, text=self._lang["label_schedule_action"]).grid(row=0, column=0, sticky=tk.W, padx=(20, 4), pady=2)
        self.combo_schedule_action = ttk.Combobox(self.frame_schedule, textvariable=self.var_schedule_action, width=12, state="readonly", values=[self._lang["schedule_action_start"], self._lang["schedule_action_restart"], self._lang["schedule_action_stop"]])
        self.combo_schedule_action.grid(row=0, column=1, sticky=tk.W, padx=2, pady=2)
        ttk.Label(self.frame_schedule, text=self._lang["label_schedule_type"]).grid(row=0, column=2, sticky=tk.W, padx=(10, 4), pady=2)
        self.combo_schedule_type = ttk.Combobox(self.frame_schedule, textvariable=self.var_schedule_type, width=20, state="readonly", values=[self._lang["schedule_type_once"], self._lang["schedule_type_minutely"], self._lang["schedule_type_every_hours"], self._lang["schedule_type_hourly"], self._lang["schedule_type_daily"], self._lang["schedule_type_weekday"], self._lang["schedule_type_weekend"], self._lang["schedule_type_weekly"], self._lang["schedule_type_monthly"], self._lang["schedule_type_quarterly"], self._lang["schedule_type_halfyearly"], self._lang["schedule_type_yearly"]])
        self.combo_schedule_type.grid(row=0, column=3, sticky=tk.W, padx=2, pady=2)
        self.combo_schedule_type.bind("<<ComboboxSelected>>", lambda e: self._update_schedule_ui())
        ttk.Label(self.frame_schedule, text=self._lang["label_schedule_time"]).grid(row=1, column=0, sticky=tk.W, padx=(20, 4), pady=2)
        self.entry_schedule_time = ttk.Entry(self.frame_schedule, textvariable=self.var_schedule_time, width=8)
        self.entry_schedule_time.grid(row=1, column=1, sticky=tk.W, padx=2, pady=2)
        ttk.Label(self.frame_schedule, text=self._lang["hint_time_format"]).grid(row=1, column=2, sticky=tk.W, padx=2, pady=2)
        self._schedule_interval_label = ttk.Label(self.frame_schedule, text=self._lang["label_schedule_interval"])
        self._schedule_interval_label.grid(row=2, column=0, sticky=tk.W, padx=(20, 4), pady=2)
        self.entry_schedule_interval = ttk.Entry(self.frame_schedule, textvariable=self.var_schedule_interval, width=10)
        self.entry_schedule_interval.grid(row=2, column=1, sticky=tk.W, padx=2, pady=2)
        self._schedule_interval_hint = ttk.Label(self.frame_schedule, text=self._lang["hint_interval_minutes"])
        self._schedule_interval_hint.grid(row=2, column=2, columnspan=2, sticky=tk.W, padx=2, pady=2)
        # 时间快捷：0点、早6、中12、18点（下一行）
        self._time_quick_frame = ttk.Frame(self.frame_schedule)
        self._time_quick_frame.grid(row=3, column=0, columnspan=4, sticky=tk.EW, padx=(20, 0), pady=2)
        ttk.Button(self._time_quick_frame, text=self._lang["btn_time_now1"], width=4, command=lambda: self.var_schedule_time.set(self._get_time_now_plus_1())).pack(side=tk.LEFT, padx=1)
        ttk.Button(self._time_quick_frame, text=self._lang["btn_time_0"], width=3, command=lambda: self.var_schedule_time.set("00:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(self._time_quick_frame, text=self._lang["btn_time_3"], width=3, command=lambda: self.var_schedule_time.set("03:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(self._time_quick_frame, text=self._lang["btn_time_6"], width=3, command=lambda: self.var_schedule_time.set("06:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(self._time_quick_frame, text=self._lang["btn_time_9"], width=3, command=lambda: self.var_schedule_time.set("09:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(self._time_quick_frame, text=self._lang["btn_time_12"], width=3, command=lambda: self.var_schedule_time.set("12:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(self._time_quick_frame, text=self._lang["btn_time_15"], width=3, command=lambda: self.var_schedule_time.set("15:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(self._time_quick_frame, text=self._lang["btn_time_18"], width=3, command=lambda: self.var_schedule_time.set("18:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(self._time_quick_frame, text=self._lang["btn_time_21"], width=3, command=lambda: self.var_schedule_time.set("21:00")).pack(side=tk.LEFT, padx=1)
        self._schedule_weekday_label = ttk.Label(self.frame_schedule, text=self._lang["label_schedule_weekday"])
        self._schedule_weekday_label.grid(row=4, column=0, sticky=tk.W, padx=(20, 4), pady=2)
        self.combo_schedule_weekday = ttk.Combobox(self.frame_schedule, textvariable=self.var_schedule_weekday, width=12, state="readonly", values=[self._lang["weekday_mon"], self._lang["weekday_tue"], self._lang["weekday_wed"], self._lang["weekday_thu"], self._lang["weekday_fri"], self._lang["weekday_sat"], self._lang["weekday_sun"]])
        self.combo_schedule_weekday.grid(row=4, column=1, sticky=tk.W, padx=2, pady=2)
        self._schedule_day_label = ttk.Label(self.frame_schedule, text=self._lang["label_schedule_day"])
        self._schedule_day_label.grid(row=4, column=0, sticky=tk.W, padx=(20, 4), pady=2)
        self.combo_schedule_day = ttk.Combobox(self.frame_schedule, textvariable=self.var_schedule_day, width=8, state="readonly", values=[str(i) for i in range(1, 31)] + [self._lang["schedule_day_last"]])
        self.combo_schedule_day.grid(row=4, column=1, sticky=tk.W, padx=2, pady=2)
        self._schedule_day_label.grid_remove()
        self.combo_schedule_day.grid_remove()
        # 季度选择（每季度用）
        self._schedule_quarter_label = ttk.Label(self.frame_schedule, text=self._lang.get("label_schedule_quarter", "季度:"))
        self._schedule_quarter_label.grid(row=4, column=0, sticky=tk.W, padx=(20, 4), pady=2)
        self.combo_schedule_quarter = ttk.Combobox(self.frame_schedule, textvariable=self.var_schedule_quarter, width=16, state="readonly", values=[self._lang["schedule_quarter_q1"], self._lang["schedule_quarter_q2"], self._lang["schedule_quarter_q3"], self._lang["schedule_quarter_q4"]])
        self.combo_schedule_quarter.grid(row=4, column=1, sticky=tk.W, padx=2, pady=2)
        self._schedule_quarter_label.grid_remove()
        self.combo_schedule_quarter.grid_remove()
        # 月份选择（每年用）
        self._schedule_month_label = ttk.Label(self.frame_schedule, text=self._lang.get("label_schedule_month", "月份:"))
        self._schedule_month_label.grid(row=4, column=0, sticky=tk.W, padx=(20, 4), pady=2)
        self.combo_schedule_month = ttk.Combobox(self.frame_schedule, textvariable=self.var_schedule_month, width=6, state="readonly", values=[str(i) for i in range(1, 13)])
        self.combo_schedule_month.grid(row=4, column=1, sticky=tk.W, padx=2, pady=2)
        self._schedule_month_label.grid_remove()
        self.combo_schedule_month.grid_remove()
        self._schedule_next_preview = ttk.Label(self.frame_schedule, text="", foreground="gray")
        self._schedule_next_preview.grid(row=5, column=0, columnspan=2, sticky=tk.W, padx=(20, 0), pady=2)
        # 多时间点提示
        self._schedule_multi_time_hint = ttk.Label(self.frame_schedule, text=self._lang["hint_multi_time"], foreground="gray")
        self._schedule_multi_time_hint.grid(row=1, column=2, columnspan=2, sticky=tk.W, padx=2, pady=2)
        # 有效期
        self._schedule_valid_frame = ttk.Frame(self.frame_schedule)
        self._schedule_valid_frame.grid(row=6, column=0, columnspan=4, sticky=tk.W, padx=(20, 0), pady=2)
        ttk.Label(self._schedule_valid_frame, text=self._lang["label_schedule_valid_from"]).pack(side=tk.LEFT, padx=(0, 2))
        self.entry_schedule_valid_from = ttk.Entry(self._schedule_valid_frame, textvariable=self.var_schedule_valid_from, width=12)
        self.entry_schedule_valid_from.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(self._schedule_valid_frame, text=self._lang["label_schedule_valid_until"]).pack(side=tk.LEFT, padx=(0, 2))
        self.entry_schedule_valid_until = ttk.Entry(self._schedule_valid_frame, textvariable=self.var_schedule_valid_until, width=12)
        self.entry_schedule_valid_until.pack(side=tk.LEFT, padx=(0, 4))
        # 最大执行次数和执行前延迟
        self._schedule_extra_frame = ttk.Frame(self.frame_schedule)
        self._schedule_extra_frame.grid(row=7, column=0, columnspan=4, sticky=tk.W, padx=(20, 0), pady=2)
        ttk.Label(self._schedule_extra_frame, text=self._lang["label_schedule_max_runs"]).pack(side=tk.LEFT, padx=(0, 2))
        self.entry_schedule_max_runs = ttk.Entry(self._schedule_extra_frame, textvariable=self.var_schedule_max_runs, width=6)
        self.entry_schedule_max_runs.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(self._schedule_extra_frame, text=self._lang["label_schedule_pre_delay"]).pack(side=tk.LEFT, padx=(0, 2))
        self.entry_schedule_pre_delay = ttk.Entry(self._schedule_extra_frame, textvariable=self.var_schedule_pre_delay, width=6)
        self.entry_schedule_pre_delay.pack(side=tk.LEFT, padx=(0, 4))
        # 单次执行日期和提示
        self._schedule_once_frame = ttk.Frame(self.frame_schedule)
        self._schedule_once_frame.grid(row=8, column=0, columnspan=4, sticky=tk.W, padx=(20, 0), pady=2)
        ttk.Label(self._schedule_once_frame, text=self._lang["label_schedule_once_date"]).pack(side=tk.LEFT, padx=(0, 2))
        self.entry_schedule_once_date = ttk.Entry(self._schedule_once_frame, textvariable=self.var_schedule_once_date, width=12)
        self.entry_schedule_once_date.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(self._schedule_once_frame, text=self._lang["hint_schedule_once_date"], foreground="gray").pack(side=tk.LEFT, padx=(0, 8))
        self._schedule_once_hint = ttk.Label(self._schedule_once_frame, text=self._lang["hint_schedule_once_auto"], foreground="blue")
        self._schedule_once_hint.pack(side=tk.LEFT)
        self._schedule_once_frame.grid_remove()  # 默认隐藏，仅在 once 类型时显示
        self._update_schedule_visible()
        self._update_schedule_ui()
        for v in (self.var_schedule_type, self.var_schedule_time, self.var_schedule_interval, self.var_schedule_weekday, self.var_schedule_day):
            v.trace_add("write", lambda *a: self._update_schedule_next_preview())

        # 路径输入时不再自动检测类型，由用户通过下拉框手动选择
        self._path_update_after_id = None
        self._on_task_type_changed()  # 初始化标签/提示/浏览按钮状态
        self._update_type()
        self._update_log_visible()
    
    def _reinit_ui_text(self):
        """重新初始化对话框中的所有界面文本（用于语言切换后）。"""
        # 更新标题
        self.title(self._lang["dialog_edit"] if self.mode == "edit" else self._lang["dialog_add_edit"])
        # 更新 Notebook 标签页文本
        if hasattr(self, '_notebook'):
            self._notebook.tab(0, text=self._lang["tab_basic"])
            self._notebook.tab(1, text=self._lang["tab_schedule"])
        # 更新所有标签和按钮文本
        self._update_all_widgets_text(self)
    
    def _update_all_widgets_text(self, parent_widget):
        """递归更新所有子控件的文本。"""
        for widget in parent_widget.winfo_children():
            widget_type = widget.winfo_class()
            if widget_type == "TLabel":
                text = widget.cget("text")
                # 根据文本匹配语言键
                if text in ["名称:", "Name:"]:
                    widget.config(text=self._lang["label_name"])
                elif text in ["脚本路径:", "Script Path:"]:
                    widget.config(text=self._lang["label_script_path"])
                elif text in ["请求 URL:", "Request URL:"]:
                    widget.config(text=self._lang["label_request_url"])
                elif text in ["请求方法:", "HTTP Method:"]:
                    widget.config(text=self._lang["label_http_method"])
                elif text in ["请求头:", "Headers:"]:
                    widget.config(text=self._lang["label_http_headers"])
                elif text in ["进程名称:", "Process Name:"]:
                    widget.config(text=self._lang["label_process_name"])
                elif text in ["命令:", "Command:"]:
                    widget.config(text=self._lang["label_command"])
                elif text in ["任务类型:", "Task Type:"]:
                    widget.config(text=self._lang["label_task_type"])
                elif text in ["工作目录:", "Work Directory:"]:
                    widget.config(text=self._lang["label_work_dir"])
                elif text in ["分组:", "Group:"]:
                    widget.config(text=self._lang["label_group"])
                elif text in ["解释器:", "Interpreter:"]:
                    widget.config(text=self._lang["label_interpreter"])
                elif text.startswith("Python 解释器") or text.startswith("Python Interpreter"):
                    widget.config(text=self._lang["label_interpreter_python"])
                elif text.startswith("Node.js 解释器") or text.startswith("Node.js Interpreter"):
                    widget.config(text=self._lang["label_interpreter_node"])
                elif text.startswith("Shell 解释器") or text.startswith("Shell Interpreter"):
                    widget.config(text=self._lang["label_interpreter_shell"])
                elif "(留空=无分组" in text or "(empty = no group" in text:
                    widget.config(text=self._lang["hint_group_empty"])
                elif "X-API-Key" in text and ("Header" in text or "请求头" in text or "每行" in text or "one Header" in text):
                    widget.config(text=self._lang["hint_http_headers"])
                elif "(留空=当前" in text or "(empty = current" in text:
                    widget.config(text=self._lang["hint_python_interpreter"])
                elif "(留空=node" in text:
                    widget.config(text=self._lang["hint_node_interpreter"])
                elif "(留空=bash" in text:
                    widget.config(text=self._lang["hint_shell_interpreter"])
                elif "(格式: HH:MM" in text or "(Format: HH:MM" in text:
                    widget.config(text=self._lang["hint_time_format"])
                elif "分钟 (仅用于" in text or "minutes (only for" in text:
                    widget.config(text=self._lang["hint_interval_minutes"])
                elif text in ["行为:", "Action:"]:
                    widget.config(text=self._lang["label_schedule_action"])
                elif text in ["类型:", "Type:"]:
                    widget.config(text=self._lang["label_schedule_type"])
                elif text in ["时间:", "Time:"]:
                    widget.config(text=self._lang["label_schedule_time"])
                elif text in ["间隔:", "Interval:"]:
                    widget.config(text=self._lang["label_schedule_interval"])
                elif text in ["星期:", "Weekday:"]:
                    widget.config(text=self._lang["label_schedule_weekday"])
                elif text in ["几号:", "Day:"]:
                    widget.config(text=self._lang["label_schedule_day"])
            elif widget_type == "TCheckbutton":
                text = widget.cget("text")
                if "启动时记录输出" in text or "Log output to logs" in text:
                    widget.config(text=self._lang["check_log_output"])
                elif "崩溃后自动重启" in text or "Auto restart on crash" in text:
                    widget.config(text=self._lang["check_auto_restart"])
                elif "启动本工具时自动启动" in text or "Launch when app starts" in text:
                    widget.config(text=self._lang["check_launch_on_app_start"])
                elif "启用定时任务" in text or "Enable Schedule" in text:
                    widget.config(text=self._lang["check_schedule_enabled"])
                elif "启用 Webhook 通知" in text or "Enable Webhook Notification" in text:
                    widget.config(text=self._lang["label_webhook_enabled"])
                elif "发送数据" in text or "Send data" in text:
                    widget.config(text=self._lang["label_webhook_send_data"])
                elif "启用异常通知" in text or "Enable Exception Notification" in text:
                    widget.config(text=self._lang["label_enable_notification"])
                elif "退出码非0时通知" in text or "Notify on Non-Zero" in text:
                    widget.config(text=self._lang["label_notify_nonzero"])
                elif "快速退出时通知" in text or "Notify on Quick Exit" in text:
                    widget.config(text=self._lang["label_notify_quick"])
                elif "定时任务执行日志" in text or "Schedule Execution Log" in text:
                    widget.config(text=self._lang["label_schedule_debug_log"])
            elif widget_type == "TButton":
                text = widget.cget("text")
                if text in ["确定", "OK"]:
                    widget.config(text=self._lang["btn_ok"])
                elif text in ["取消", "Cancel"]:
                    widget.config(text=self._lang["btn_cancel"])
                elif "浏览" in text or "Browse" in text:
                    widget.config(text=self._lang["btn_browse"])
                elif "自动识别" in text or "Auto Detect" in text:
                    widget.config(text=self._lang["btn_auto_detect"])
                elif text in ["填入本机API", "Fill Local API"]:
                    widget.config(text=self._lang["btn_fill_local_api"])
                elif "测试 Webhook" in text or "Test Webhook" in text:
                    widget.config(text=self._lang["btn_test"])
                elif text in ["0点", "0:00"]:
                    widget.config(text=self._lang["btn_time_0"])
                elif text in ["3点", "3am"]:
                    widget.config(text=self._lang["btn_time_3"])
                elif text in ["6点", "早6", "6am"]:
                    widget.config(text=self._lang["btn_time_6"])
                elif text in ["9点", "9am"]:
                    widget.config(text=self._lang["btn_time_9"])
                elif text in ["12点", "中12", "12pm"]:
                    widget.config(text=self._lang["btn_time_12"])
                elif text in ["15点", "3pm"]:
                    widget.config(text=self._lang["btn_time_15"])
                elif text in ["18点", "6pm"]:
                    widget.config(text=self._lang["btn_time_18"])
                elif text in ["21点", "9pm"]:
                    widget.config(text=self._lang["btn_time_21"])
                elif text in ["+1分", "+1m"]:
                    widget.config(text=self._lang["btn_time_now1"])
            elif widget_type == "TNotebook":
                # 递归处理标签页内的控件
                for child in widget.winfo_children():
                    self._update_all_widgets_text(child)
            elif widget_type == "TFrame" or widget_type == "TLabelFrame":
                # 递归处理子控件
                self._update_all_widgets_text(widget)
                # 更新 LabelFrame 的标题
                if widget_type == "TLabelFrame":
                    text = widget.cget("text")
                    if "基本设置" in text or "Basic Settings" in text:
                        widget.config(text=self._lang["label_basic_settings"])
                    elif "Webhook 通知" in text or "Webhook Notification" in text:
                        widget.config(text=self._lang["label_webhook_settings"])
                    elif "批量修改" in text or "Batch Edit" in text:
                        widget.config(text=self._lang["label_batch_edit_title"])
            elif widget_type == "TCombobox":
                # 更新下拉框选项（定时任务相关）
                values = widget.cget("values")
                if values:
                    new_values = []
                    for v in values:
                        if v in ["启动", "Start"]:
                            new_values.append(self._lang["schedule_action_start"])
                        elif v in ["重启", "重启任务", "Restart", "Restart Task"]:
                            new_values.append(self._lang["schedule_action_restart"])
                        elif v in ["停止", "Stop"]:
                            new_values.append(self._lang["schedule_action_stop"])
                        elif v in ["关机", "Shutdown"]:
                            new_values.append(self._lang["schedule_action_shutdown"])
                        elif v in ["重启系统", "Reboot System"]:
                            new_values.append(self._lang["schedule_action_reboot"])
                        elif v in ["每天", "Daily"]:
                            new_values.append(self._lang["schedule_type_daily"])
                        elif v in ["每小时", "Hourly"]:
                            new_values.append(self._lang["schedule_type_hourly"])
                        elif v in ["每分钟", "Minutely"]:
                            new_values.append(self._lang["schedule_type_minutely"])
                        elif v in ["每周", "Weekly"]:
                            new_values.append(self._lang["schedule_type_weekly"])
                        elif v in ["每月", "Monthly"]:
                            new_values.append(self._lang["schedule_type_monthly"])
                        elif v in ["每N小时执行", "Every N hours"]:
                            new_values.append(self._lang["schedule_type_every_hours"])
                        elif v in ["每季度", "Quarterly"]:
                            new_values.append(self._lang["schedule_type_quarterly"])
                        elif v in ["每半年", "Half-Yearly"]:
                            new_values.append(self._lang["schedule_type_halfyearly"])
                        elif v in ["每年", "Yearly"]:
                            new_values.append(self._lang["schedule_type_yearly"])
                        elif v in ["单次执行", "Once"]:
                            new_values.append(self._lang["schedule_type_once"])
                        elif v in ["工作日", "工作日(1-5)", "Weekday", "Weekday(1-5)"]:
                            new_values.append(self._lang["schedule_type_weekday"])
                        elif v in ["每周末", "每周末(6-7)", "Weekend", "Weekend(6-7)"]:
                            new_values.append(self._lang["schedule_type_weekend"])
                        elif v in ["周一", "Monday"]:
                            new_values.append(self._lang["weekday_mon"])
                        elif v in ["周二", "Tuesday"]:
                            new_values.append(self._lang["weekday_tue"])
                        elif v in ["周三", "Wednesday"]:
                            new_values.append(self._lang["weekday_wed"])
                        elif v in ["周四", "Thursday"]:
                            new_values.append(self._lang["weekday_thu"])
                        elif v in ["周五", "Friday"]:
                            new_values.append(self._lang["weekday_fri"])
                        elif v in ["周六", "Saturday"]:
                            new_values.append(self._lang["weekday_sat"])
                        elif v in ["周日", "Sunday"]:
                            new_values.append(self._lang["weekday_sun"])
                        elif v in ["月末", "Last day"]:
                            new_values.append(self._lang["schedule_day_last"])
                        else:
                            new_values.append(v)
                    if new_values:
                        widget.config(values=new_values)
                        if widget == self.combo_schedule_day and self.var_schedule_day.get().strip() in ("月末", "Last day"):
                            self.var_schedule_day.set(self._lang["schedule_day_last"])

    def _update_schedule_visible(self):
        """根据是否启用定时任务，显示/隐藏定时任务配置区域。"""
        if self.var_schedule_enabled.get():
            self.frame_schedule.grid()
            self._update_schedule_ui()
        else:
            self.frame_schedule.grid_remove()
            self._update_schedule_next_preview()

    def _get_time_now_plus_1(self):
        """返回当前时间+1分钟的 HH:MM 字符串，用于快捷设置定时时间。"""
        from datetime import datetime, timedelta
        t = datetime.now() + timedelta(minutes=1)
        return t.strftime("%H:%M")

    def _update_schedule_ui(self):
        """根据定时任务类型，显示/隐藏相关输入框。"""
        if not self.var_schedule_enabled.get():
            return
        stype = self.var_schedule_type.get()
        # 统一中英文判断
        is_daily = stype in ("每天", "Daily")
        is_hourly = stype in ("每小时", "Hourly")
        is_minutely = stype in ("每分钟", "Minutely")
        is_weekly = stype in ("每周", "Weekly")
        is_monthly = stype in ("每月", "Monthly")
        is_every_hours = stype in (self._lang["schedule_type_every_hours"], "每N小时执行", "Every N hours", "每N小时")
        is_quarterly = stype in (self._lang["schedule_type_quarterly"], "每季度", "Quarterly")
        is_halfyearly = stype in (self._lang["schedule_type_halfyearly"], "每半年", "Half-Yearly")
        is_yearly = stype in (self._lang["schedule_type_yearly"], "每年", "Yearly")
        is_once = stype in (self._lang["schedule_type_once"], "单次执行", "Once")
        is_weekday = stype in (self._lang["schedule_type_weekday"], "工作日", "工作日(1-5)", "Weekday", "Weekday(1-5)")
        is_weekend = stype in (self._lang["schedule_type_weekend"], "每周末", "每周末(6-7)", "Weekend", "Weekend(6-7)")

        # 时间输入框：每天/每周/每月/每季度/每半年/每年/单次/工作日/每周末 显示
        show_time = is_daily or is_weekly or is_monthly or is_quarterly or is_halfyearly or is_yearly or is_once or is_weekday or is_weekend
        # 间隔输入框：每分钟/每N小时 显示
        show_interval = is_minutely or is_every_hours
        # 星期选择：每周 显示
        show_weekday = is_weekly
        # 日期选择：每月/每季度/每半年/每年 显示
        show_day = is_monthly or is_quarterly or is_halfyearly or is_yearly
        # 季度选择：每季度 显示
        show_quarter = is_quarterly
        # 月份选择：每半年/每年 显示
        show_month = is_halfyearly or is_yearly
        # 单次执行日期：单次 显示
        show_once = is_once

        if show_time:
            self.entry_schedule_time.grid(); self.entry_schedule_time.grid_configure(row=1, column=1)
            self._time_quick_frame.grid()
        else:
            self.entry_schedule_time.grid_remove(); self._time_quick_frame.grid_remove()

        if show_interval:
            if is_minutely:
                self._schedule_interval_label.config(text=self._lang["label_schedule_interval_minutes"])
                self._schedule_interval_hint.config(text=self._lang["hint_interval_minutes"])
            else:
                self._schedule_interval_label.config(text=self._lang["label_schedule_interval_hours"])
                self._schedule_interval_hint.config(text=self._lang["hint_interval_hours_repeat"])
            self._schedule_interval_label.grid(row=2, column=0, sticky=tk.W, padx=(20, 4), pady=2)
            self.entry_schedule_interval.grid(row=2, column=1, sticky=tk.W, padx=2, pady=2)
            self._schedule_interval_hint.grid(row=2, column=2, columnspan=2, sticky=tk.W, padx=2, pady=2)
        else:
            self._schedule_interval_label.grid_remove(); self.entry_schedule_interval.grid_remove()
            self._schedule_interval_hint.grid_remove()

        if show_weekday:
            self._schedule_weekday_label.grid(row=4, column=0, sticky=tk.W, padx=(20, 4), pady=2)
            self.combo_schedule_weekday.grid(row=4, column=1, sticky=tk.W, padx=2, pady=2)
        else:
            self._schedule_weekday_label.grid_remove(); self.combo_schedule_weekday.grid_remove()

        if show_day:
            self._schedule_day_label.grid(row=4, column=0, sticky=tk.W, padx=(20, 4), pady=2)
            self.combo_schedule_day.grid(row=4, column=1, sticky=tk.W, padx=2, pady=2)
        else:
            self._schedule_day_label.grid_remove(); self.combo_schedule_day.grid_remove()

        if show_quarter:
            self._schedule_quarter_label.grid(row=4, column=0, sticky=tk.W, padx=(20, 4), pady=2)
            self.combo_schedule_quarter.grid(row=4, column=1, sticky=tk.W, padx=2, pady=2)
            self._schedule_day_label.grid(row=4, column=2, sticky=tk.W, padx=(4, 4), pady=2)
            self.combo_schedule_day.grid(row=4, column=3, sticky=tk.W, padx=2, pady=2)
        else:
            self._schedule_quarter_label.grid_remove(); self.combo_schedule_quarter.grid_remove()

        if show_month:
            self._schedule_month_label.grid(row=4, column=0, sticky=tk.W, padx=(20, 4), pady=2)
            self.combo_schedule_month.grid(row=4, column=1, sticky=tk.W, padx=2, pady=2)
            self._schedule_day_label.grid(row=4, column=2, sticky=tk.W, padx=(4, 4), pady=2)
            self.combo_schedule_day.grid(row=4, column=3, sticky=tk.W, padx=2, pady=2)
        else:
            self._schedule_month_label.grid_remove(); self.combo_schedule_month.grid_remove()

        # 单次执行日期
        if show_once:
            self._schedule_once_frame.grid()
        else:
            self._schedule_once_frame.grid_remove()

        self._update_schedule_next_preview()

    def _update_schedule_next_preview(self):
        """根据当前表单的定时配置，更新「下次约 xxx」预览（需主窗口传入 get_next_run）。"""
        if not self.var_schedule_enabled.get():
            self._schedule_next_preview.config(text="")
            return
        if not callable(self._get_next_run):
            self._schedule_next_preview.config(text="")
            return
        try:
            type_rev = {
                self._lang["schedule_type_daily"]: "daily", self._lang["schedule_type_hourly"]: "hourly",
                self._lang["schedule_type_minutely"]: "minutely", self._lang["schedule_type_weekly"]: "weekly",
                self._lang["schedule_type_monthly"]: "monthly", self._lang["schedule_type_every_hours"]: "every_hours",
                self._lang["schedule_type_quarterly"]: "quarterly", self._lang["schedule_type_halfyearly"]: "halfyearly",
                self._lang["schedule_type_yearly"]: "yearly", self._lang["schedule_type_once"]: "once",
                self._lang["schedule_type_weekday"]: "weekday", self._lang["schedule_type_weekend"]: "weekend",
            }
            st = type_rev.get(self.var_schedule_type.get().strip(), "daily")
            vd = self.var_schedule_day.get().strip()
            day = 31 if (vd == self._lang["schedule_day_last"] or vd == "31") else max(1, min(30, int(vd) if vd.isdigit() else 1))
            wd_map = getattr(self, "reverse_weekday_map", {})
            wd = wd_map.get(self.var_schedule_weekday.get().strip(), 0)
            try:
                interval = max(1, int(self.var_schedule_interval.get()))
            except (ValueError, TypeError):
                interval = 5
            temp_it = {
                "id": getattr(self, "initial_id", None),
                "schedule_type": st,
                "schedule_time": self.var_schedule_time.get().strip() or "09:00",
                "schedule_interval": interval,
                "schedule_weekday": wd,
                "schedule_day": day,
                "schedule_once_date": self.var_schedule_once_date.get().strip(),
            }
            next_s = self._get_next_run(temp_it)
            self._schedule_next_preview.config(text=(self._lang["schedule_next_run"] % next_s) if next_s else "")
        except Exception:
            self._schedule_next_preview.config(text="")

    def _type_from_path(self, p: str) -> str:
        stype = get_script_type(p)
        type_names = {
            "python": self._lang["label_type_prefix"] + "Python",
            "node": self._lang["label_type_prefix"] + "Node.js",
            "powershell": self._lang["label_type_prefix"] + "PowerShell",
            "vbs": self._lang["label_type_prefix"] + "VBScript",
            "jscript": self._lang["label_type_prefix"] + "JScript",
            "batch": self._lang["label_type_prefix"] + "批处理" if self._lang == LANGUAGES["zh-CN"] else self._lang["label_type_prefix"] + "Batch",
            "shell": self._lang["label_type_prefix"] + "Shell",
            "wsf": self._lang["label_type_prefix"] + "WSF",
            "exe": self._lang["label_type_prefix"] + "EXE",
            "http_get": self._lang["label_type_prefix"] + ("HTTP GET" if self._lang != LANGUAGES["zh-CN"] else "HTTP请求"),
            "process": self._lang["label_type_prefix"] + ("进程管理" if self._lang == LANGUAGES["zh-CN"] else "Process"),
            "command": self._lang["label_type_prefix"] + ("直接命令" if self._lang == LANGUAGES["zh-CN"] else "Command"),
        }
        default = self._lang["label_type_prefix"] + ("批处理" if self._lang == LANGUAGES["zh-CN"] else "Batch")
        return type_names.get(stype, default)

    def _on_task_type_changed(self, event=None):
        """类型下拉框切换时，更新标签、提示和浏览按钮可见性。"""
        type_key = self._type_reverse_map.get(self.var_task_type.get(), "script")
        if type_key != "http_get" and hasattr(self, "_http_opts_frame"):
            self._http_opts_frame.grid_remove()
        if type_key == "http_get":
            self.label_path.config(text=self._lang["label_request_url"])
            self.label_path_hint.config(text=self._lang["hint_request_url"])
            self.entry_path.config(width=60, state=tk.NORMAL)
            # HTTP 请求类型禁用保活（curl 执行完即退出，保活会不停重发请求）
            if hasattr(self, '_check_auto_restart'):
                self._check_auto_restart.config(state=tk.DISABLED)
                self.var_auto.set(False)
            if hasattr(self, "_http_opts_frame"):
                self._http_opts_frame.grid()
        elif type_key == "process":
            self.label_path.config(text=self._lang["label_process_name"])
            self.label_path_hint.config(text=self._lang["hint_process_name"])
            self.entry_path.config(width=60, state=tk.NORMAL)
            if hasattr(self, '_check_auto_restart'):
                self._check_auto_restart.config(state=tk.NORMAL)
        elif type_key == "command":
            self.label_path.config(text=self._lang["label_command"])
            self.label_path_hint.config(text=self._lang["hint_command"])
            self.entry_path.config(width=60, state=tk.NORMAL)
            if hasattr(self, '_check_auto_restart'):
                self._check_auto_restart.config(state=tk.NORMAL)
        elif type_key == "system_shutdown":
            self.label_path.config(text=self._lang["task_type_system_shutdown"])
            self.label_path_hint.config(text=self._lang["hint_system_power_task"])
            self.var_path.set(SYSTEM_SHUTDOWN_PATH)
            self.entry_path.config(width=60, state=tk.DISABLED)
            if not self.var_name.get().strip() or self.var_name.get().strip() == self._lang["default_name"]:
                self.var_name.set(self._lang["task_type_system_shutdown"])
            if hasattr(self, '_check_auto_restart'):
                self._check_auto_restart.config(state=tk.DISABLED)
                self.var_auto.set(False)
            self.var_schedule_action.set(self._lang["schedule_action_start"])
            if hasattr(self, "combo_schedule_action"):
                self.combo_schedule_action.config(values=[self._lang["schedule_action_start"]])
        elif type_key == "system_reboot":
            self.label_path.config(text=self._lang["task_type_system_reboot"])
            self.label_path_hint.config(text=self._lang["hint_system_power_task"])
            self.var_path.set(SYSTEM_REBOOT_PATH)
            self.entry_path.config(width=60, state=tk.DISABLED)
            if not self.var_name.get().strip() or self.var_name.get().strip() == self._lang["default_name"]:
                self.var_name.set(self._lang["task_type_system_reboot"])
            if hasattr(self, '_check_auto_restart'):
                self._check_auto_restart.config(state=tk.DISABLED)
                self.var_auto.set(False)
            self.var_schedule_action.set(self._lang["schedule_action_start"])
            if hasattr(self, "combo_schedule_action"):
                self.combo_schedule_action.config(values=[self._lang["schedule_action_start"]])
        else:
            self.label_path.config(text=self._lang["label_script_path"])
            self.label_path_hint.config(text="")
            self.entry_path.config(width=44, state=tk.NORMAL)
            if hasattr(self, '_check_auto_restart'):
                self._check_auto_restart.config(state=tk.NORMAL)
            if hasattr(self, "combo_schedule_action"):
                self.combo_schedule_action.config(values=[
                    self._lang["schedule_action_start"],
                    self._lang["schedule_action_restart"],
                    self._lang["schedule_action_stop"],
                ])
        if type_key in ("command", "http_get", "process", "system_shutdown", "system_reboot"):
            self.btn_browse.pack_forget()
        else:
            self.btn_browse.pack(side=tk.LEFT, padx=2)
        self._update_type()

    def _on_auto_detect(self):
        """一键自动识别：根据路径自动设置类型下拉框、自动填入名称、更新类型标签。"""
        p = self.var_path.get().strip()
        if not p:
            return
        # 1. 自动识别类型并设置下拉框
        stype = get_script_type(p)
        if stype == "unknown":
            stype = "script"
        type_key = stype if stype in self._type_display_map else "script"
        self.var_task_type.set(self._type_display_map.get(type_key, self._type_display_map["script"]))
        self._on_task_type_changed()
        # 2. 自动填入名称（仅在名称为空或为默认值时）
        current_name = self.var_name.get().strip()
        default_name = self._lang["default_name"]
        if not current_name or current_name == default_name:
            if stype == "http_get":
                # URL: 取域名
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(p)
                    auto_name = parsed.hostname or p[:30]
                except Exception:
                    auto_name = p[:30]
            elif stype == "process":
                # 进程名: 去掉扩展名
                auto_name = os.path.splitext(os.path.basename(p))[0]
            elif stype == "command":
                # 命令: 取第一个单词作为名称
                auto_name = p.split()[0] if p.split() else p[:30]
                # 如果是常见命令的路径，取文件名
                if os.path.sep in auto_name:
                    auto_name = os.path.splitext(os.path.basename(auto_name))[0]
            else:
                # 文件路径: 取文件名去扩展名
                auto_name = os.path.splitext(os.path.basename(p))[0]
            self.var_name.set(auto_name)
        # 3. 更新类型标签
        self._update_type()

    def _update_type(self):
        p = self.var_path.get()
        stype = get_script_type(p)
        # 自动检测类型（仅当检测到 http_get/process/command 时才覆盖下拉框）
        auto_key = stype if stype in ("http_get", "process", "command") else None
        current_key = self._type_reverse_map.get(self.var_task_type.get(), "script")
        # 仅在自动检测到特殊类型时才切换下拉框；如果用户手动选了 http_get/process，不覆盖
        if auto_key and auto_key != current_key:
            self.var_task_type.set(self._type_display_map.get(auto_key, self._type_display_map["script"]))
            # 递归调用一次以更新标签
            self._on_task_type_changed()

        if current_key == "command":
            self.label_type.config(text=self._lang["label_type_prefix"] + ("直接命令" if self._lang == LANGUAGES["zh-CN"] else "Command"))
        else:
            self.label_type.config(text=self._type_from_path(p))
        
        # 根据脚本类型显示/隐藏解释器输入框
        needs_interpreter = stype in ("python", "node", "shell", "powershell")
        if needs_interpreter:
            self.label_interpreter.grid()
            self.entry_interpreter.grid()
            self.label_interpreter_hint.grid()
            if stype == "python":
                self.label_interpreter.config(text=self._lang["label_interpreter_python"])
                self.label_interpreter_hint.config(text=self._lang["hint_python_interpreter"])
            elif stype == "node":
                self.label_interpreter.config(text=self._lang["label_interpreter_node"])
                self.label_interpreter_hint.config(text=self._lang["hint_node_interpreter"])
            elif stype == "shell":
                self.label_interpreter.config(text=self._lang["label_interpreter_shell"])
                self.label_interpreter_hint.config(text=self._lang["hint_shell_interpreter"])
            self.entry_interpreter.config(state=tk.NORMAL)
        else:
            self.label_interpreter.grid_remove()
            self.entry_interpreter.grid_remove()
            self.label_interpreter_hint.grid_remove()
        
        self._update_log_visible()

    def _update_log_visible(self):
        p = self.var_path.get()
        stype = get_script_type(p)
        # Python、Node.js、PowerShell 支持记录输出
        supports_log = stype in ("python", "node", "powershell")
        if supports_log:
            self.cb_log.grid()
        else:
            self.cb_log.grid_remove()

    def _on_browse_path(self):
        all_ext = "*.bat;*.cmd;*.py;*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx;*.ps1;*.vbs;*.wsf;*.sh;*.exe"
        if sys.platform != "win32":
            # Mac/Linux：弱化 Windows 专用扩展名，默认「全部脚本」不含 bat/cmd/vbs/wsf/exe
            all_ext = "*.sh;*.py;*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx;*.ps1"
            if self._lang == LANGUAGES["zh-CN"]:
                filetypes = [
                    ("Shell", "*.sh"),
                    ("Python", "*.py"),
                    ("Node.js", "*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx"),
                    ("PowerShell", "*.ps1"),
                    ("全部脚本", all_ext),
                    ("全部", "*.*"),
                ]
            else:
                filetypes = [
                    ("Shell", "*.sh"),
                    ("Python", "*.py"),
                    ("Node.js", "*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx"),
                    ("PowerShell", "*.ps1"),
                    ("All Scripts", all_ext),
                    ("All Files", "*.*"),
                ]
        elif self._lang == LANGUAGES["zh-CN"]:
            filetypes = [
                ("全部脚本", all_ext),
                ("批处理", "*.bat;*.cmd"),
                ("Python", "*.py"),
                ("Node.js", "*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx"),
                ("PowerShell", "*.ps1"),
                ("VBScript", "*.vbs"),
                ("WSF", "*.wsf"),
                ("Shell", "*.sh"),
                ("可执行文件", "*.exe"),
                ("全部", "*.*"),
            ]
        else:
            filetypes = [
                ("All Scripts", all_ext),
                ("Batch", "*.bat;*.cmd"),
                ("Python", "*.py"),
                ("Node.js", "*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx"),
                ("PowerShell", "*.ps1"),
                ("VBScript", "*.vbs"),
                ("WSF", "*.wsf"),
                ("Shell", "*.sh"),
                ("Executable", "*.exe"),
                ("All Files", "*.*"),
            ]
        path = filedialog.askopenfilename(
            title=self._lang["dialog_select_script"],
            filetypes=filetypes,
            parent=self,
        )
        if path:
            self.var_path.set(path)
            self.var_work.set(os.path.dirname(path))

    def _on_browse_dir(self):
        d = filedialog.askdirectory(title=self._lang["dialog_select_work_dir"], parent=self)
        if d:
            self.var_work.set(d)

    def _on_fill_local_api(self):
        """把本机 API 的 base_url / 方法 / X-API-Key 填入当前 HTTP 任务。"""
        call = resolve_api_call_config()
        url = rewrite_url_to_local_api(self.var_path.get(), call)
        self.var_path.set(url)
        type_key = self._type_reverse_map.get(self.var_task_type.get(), "script")
        if type_key != "http_get":
            display = self._type_display_map.get("http_get")
            if display:
                self.var_task_type.set(display)
                self._on_task_type_changed()
        self.var_http_method.set(api_http_method_for_url(url))
        auth_on = bool(call.get("auth_enabled"))
        self.var_http_headers.set(ensure_api_key_header_line(
            self.var_http_headers.get(),
            call.get("api_key") or "",
            auth_on,
        ))
        if not (self.var_group.get() or "").strip():
            self.var_group.set(self._lang.get("group_local_api", "本机API"))

    def _on_ok(self):
        try:
            self._do_on_ok()
        except Exception as e:
            import traceback
            traceback.print_exc()
            with open("debug_add_error.log", "a", encoding="utf-8") as f:
                f.write(f"[AddEditDialog._on_ok ERROR] {e}\n")
                traceback.print_exc(file=f)
            messagebox.showerror("Error", str(e), parent=self)

    def _do_on_ok(self):
        default_name = self._lang["default_name"]
        name = self.var_name.get().strip() or default_name
        path = self.var_path.get().strip()
        task_type_key = self._type_reverse_map.get(self.var_task_type.get(), "script")
        is_system_power = task_type_key in ("system_shutdown", "system_reboot")
        if not path and not is_system_power:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_enter_script_path"], parent=self)
            return
        # 获取类型下拉框选择（优先于自动检测）
        stype = get_script_type(path) if path else "batch"
        if task_type_key == "http_get":
            stype = "http_get"
        elif task_type_key == "process":
            stype = "process"
        elif task_type_key == "command":
            stype = "command"
        elif task_type_key == "system_shutdown":
            stype = "system_shutdown"
            path = SYSTEM_SHUTDOWN_PATH
        elif task_type_key == "system_reboot":
            stype = "system_reboot"
            path = SYSTEM_REBOOT_PATH
        # URL、进程名、命令、系统关机/重启 跳过文件存在性检查
        is_url = stype == "http_get"
        is_process = stype == "process"
        is_command = stype == "command"
        work = self.var_work.get().strip()
        if not is_url and not is_process and not is_command and not is_system_power:
            check_path = resolve_script_file_path({"path": path, "work_dir": work})
            if not (check_path and os.path.isfile(check_path)):
                messagebox.showinfo(self._lang["msg_info"], self._lang["msg_script_not_exists"] % path, parent=self)
                return
        group = self.var_group.get().strip()
        interpreter = self.var_interpreter.get().strip()
        # 兼容旧配置：如果存在 is_python，转换为 script_type
        is_py = stype == "python"
        self.result = {
            **{key: self._feature_initial[key] for key in FEATURE_FIELDS if key in self._feature_initial},
            "name": name,
            "path": path,
            "work_dir": work,
            "group": group,
            "script_type": stype,
            "interpreter": interpreter,
            # 兼容旧配置
            "is_python": is_py,
            "python_exe": interpreter if is_py else "",
            "auto_restart": self.var_auto.get() if not is_system_power else False,
            "launch_on_app_start": self.var_launch_on_app_start.get() if not is_system_power else False,
            "log_output": self.var_log.get() if stype in ("python", "node", "powershell", "command") else False,
            "schedule_enabled": self.var_schedule_enabled.get(),
            "schedule_action": {
                self._lang["schedule_action_start"]: "start",
                self._lang["schedule_action_restart"]: "restart",
                self._lang["schedule_action_stop"]: "stop",
                "启动": "start", "重启": "restart", "重启任务": "restart", "停止": "stop",
                "Start": "start", "Restart": "restart", "Restart Task": "restart", "Stop": "stop",
            }.get(self.var_schedule_action.get(), "start") if not is_system_power else "start",
            "schedule_type": {
                self._lang["schedule_type_daily"]: "daily",
                self._lang["schedule_type_hourly"]: "hourly",
                self._lang["schedule_type_minutely"]: "minutely",
                self._lang["schedule_type_weekly"]: "weekly",
                self._lang["schedule_type_monthly"]: "monthly",
                self._lang["schedule_type_every_hours"]: "every_hours",
                self._lang["schedule_type_quarterly"]: "quarterly",
                self._lang["schedule_type_halfyearly"]: "halfyearly",
                self._lang["schedule_type_yearly"]: "yearly",
                self._lang["schedule_type_once"]: "once",
                self._lang["schedule_type_weekday"]: "weekday",
                self._lang["schedule_type_weekend"]: "weekend",
                "每天": "daily", "每小时": "hourly", "每分钟": "minutely", "每周": "weekly", "每月": "monthly",
                "每季度": "quarterly", "每半年": "halfyearly", "每年": "yearly", "单次执行": "once", "工作日": "weekday", "每周末": "weekend", "工作日(1-5)": "weekday", "每周末(6-7)": "weekend",
                "Daily": "daily", "Hourly": "hourly", "Minutely": "minutely", "Weekly": "weekly", "Monthly": "monthly",
                "Quarterly": "quarterly", "Half-Yearly": "halfyearly", "Yearly": "yearly", "Once": "once", "Weekday": "weekday", "Weekend": "weekend", "Weekday(1-5)": "weekday", "Weekend(6-7)": "weekend"
            }.get(self.var_schedule_type.get(), "daily"),
            "schedule_time": self.var_schedule_time.get(),
            "schedule_interval": int(self.var_schedule_interval.get()) if self.var_schedule_interval.get().strip().isdigit() else 5,
            "schedule_weekday": {
                self._lang["weekday_mon"]: 0, self._lang["weekday_tue"]: 1, self._lang["weekday_wed"]: 2,
                self._lang["weekday_thu"]: 3, self._lang["weekday_fri"]: 4, self._lang["weekday_sat"]: 5, self._lang["weekday_sun"]: 6,
                "周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6,
                "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6
            }.get(self.var_schedule_weekday.get(), 0),
            "schedule_day": 31 if (self.var_schedule_day.get().strip() == self._lang["schedule_day_last"] or self.var_schedule_day.get().strip() == "31") else max(1, min(30, int(self.var_schedule_day.get()) if self.var_schedule_day.get().strip().isdigit() else 1)),
            "schedule_quarter": {
                self._lang["schedule_quarter_q1"]: "q1", self._lang["schedule_quarter_q2"]: "q2",
                self._lang["schedule_quarter_q3"]: "q3", self._lang["schedule_quarter_q4"]: "q4",
                "第一季度(1月)": "q1", "第二季度(4月)": "q2", "第三季度(7月)": "q3", "第四季度(10月)": "q4",
                "Q1 (Jan)": "q1", "Q2 (Apr)": "q2", "Q3 (Jul)": "q3", "Q4 (Oct)": "q4",
            }.get(self.var_schedule_quarter.get(), "q1"),
            "schedule_month": max(1, min(12, int(self.var_schedule_month.get()))) if self.var_schedule_month.get().strip().isdigit() else 1,
            "depends_on": self.var_depends_on.get().strip(),
            "delay_after_dep": int(self.var_delay_after_dep.get()) if self.var_delay_after_dep.get().strip().isdigit() else 0,
            "args": self.var_args.get().strip(),
            "env_vars": self.var_env_vars.get().strip(),
            "http_method": normalize_http_method(self.var_http_method.get()) if stype == "http_get" else "GET",
            "http_headers": self.var_http_headers.get().strip() if stype == "http_get" else "",
            "startup_timeout": int(self.var_startup_timeout.get()) if self.var_startup_timeout.get().strip().isdigit() else 0,
            "schedule_valid_from": self.var_schedule_valid_from.get().strip(),
            "schedule_valid_until": self.var_schedule_valid_until.get().strip(),
            "schedule_max_runs": int(self.var_schedule_max_runs.get()) if self.var_schedule_max_runs.get().strip().isdigit() else 0,
            "schedule_pre_delay": int(self.var_schedule_pre_delay.get()) if self.var_schedule_pre_delay.get().strip().isdigit() else 0,
            "schedule_once_date": self.var_schedule_once_date.get().strip(),
        }
        self.destroy()


# ---------------------------------------------------------------------------
# GUI：执行历史记录
# ---------------------------------------------------------------------------

class HistoryDialog(tk.Toplevel):
    """执行历史记录查看窗口。"""
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._lang = app._lang
        self.title(self._lang["dialog_history"])
        self.geometry("900x600")
        self.minsize(700, 400)
        
        # 顶部：选择脚本和统计信息
        top_frame = ttk.Frame(self)
        top_frame.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(top_frame, text=self._lang["label_select_script"]).pack(side=tk.LEFT, padx=(0, 4))
        self.script_var = tk.StringVar()
        self.script_combo = ttk.Combobox(top_frame, textvariable=self.script_var, state="readonly", width=30)
        self.script_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.script_combo.bind("<<ComboboxSelected>>", self._on_script_selected)
        ttk.Label(top_frame, text=self._lang.get("label_date_from", "从:")).pack(side=tk.LEFT, padx=(8, 2))
        self.var_from = tk.StringVar()
        ttk.Entry(top_frame, textvariable=self.var_from, width=12).pack(side=tk.LEFT)
        ttk.Label(top_frame, text=self._lang.get("label_date_to", "到:")).pack(side=tk.LEFT, padx=(6, 2))
        self.var_to = tk.StringVar()
        ttk.Entry(top_frame, textvariable=self.var_to, width=12).pack(side=tk.LEFT)
        ttk.Button(top_frame, text=self._lang.get("btn_refresh", "刷新"), command=self._on_script_selected).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(top_frame, text=self._lang.get("hint_date_filter", ""), foreground="gray").pack(side=tk.LEFT, padx=(8, 0))
        
        # 统计信息
        self.stats_label = ttk.Label(top_frame, text="", foreground="blue")
        self.stats_label.pack(side=tk.LEFT, padx=(20, 0))
        
        # 历史记录表格
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("start_time", "stop_time", "duration", "exit_code", "status"),
            show="headings",
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.tree.yview)
        self.tree.pack(fill=tk.BOTH, expand=True)
        
        # 列配置
        self.tree.heading("start_time", text=self._lang["col_start_time"])
        self.tree.heading("stop_time", text=self._lang["col_stop_time"])
        self.tree.heading("duration", text=self._lang["col_duration"])
        self.tree.heading("exit_code", text=self._lang["col_exit_code"])
        self.tree.heading("status", text=self._lang["col_status"])
        self.tree.column("start_time", width=150)
        self.tree.column("stop_time", width=150)
        self.tree.column("duration", width=100)
        self.tree.column("exit_code", width=80)
        self.tree.column("status", width=80)
        
        # 底部按钮
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(btn_frame, text=self._lang["btn_close"], command=self.destroy).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(btn_frame, text=self._lang["btn_clear_history"], command=self._on_clear_history).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text=self._lang.get("btn_export_csv", "导出 CSV"), command=self._on_export_csv).pack(side=tk.LEFT, padx=(8, 0))
        
        # 初始化
        self._update_script_list()
        if self.script_combo['values']:
            self.script_combo.current(0)
            self._on_script_selected()

    def _parse_date_bound(self, s: str, end: bool = False):
        s = (s or "").strip()
        if not s:
            return None
        try:
            d = datetime.strptime(s[:10], "%Y-%m-%d")
            if end:
                return d.replace(hour=23, minute=59, second=59)
            return d.replace(hour=0, minute=0, second=0)
        except ValueError:
            return None

    def _filtered_history(self, item_id: int) -> list:
        history = self.app._execution_history.get(item_id, [])
        rows = [h for h in history if h.get("stop_time")]
        t0 = self._parse_date_bound(self.var_from.get(), end=False)
        t1 = self._parse_date_bound(self.var_to.get(), end=True)
        if t0:
            rows = [h for h in rows if datetime.fromtimestamp(h.get("stop_time", 0)) >= t0]
        if t1:
            rows = [h for h in rows if datetime.fromtimestamp(h.get("stop_time", 0)) <= t1]
        return sorted(rows, key=lambda x: x.get("stop_time", 0), reverse=True)
        
    def _update_script_list(self):
        """更新脚本列表。"""
        scripts = []
        for it in self.app.items:
            item_id = it.get("id")
            if item_id and item_id in self.app._execution_history:
                scripts.append((item_id, it.get("name", "未命名")))
        if not scripts:
            self.script_combo['values'] = []
            return
        scripts.sort(key=lambda x: x[1])
        self.script_combo['values'] = [f"{name} (ID:{item_id})" for item_id, name in scripts]
        
    def _on_script_selected(self, event=None):
        """脚本选择改变时更新历史记录。"""
        selection = self.script_var.get()
        if not selection:
            return
        # 提取 item_id
        try:
            item_id = int(selection.split("ID:")[1].rstrip(")"))
        except (ValueError, IndexError):
            return
        # 显示历史记录
        self._show_history(item_id)
        
    def _show_history(self, item_id: int):
        """显示指定脚本的历史记录。"""
        # 清空表格
        for item in self.tree.get_children():
            self.tree.delete(item)
        sorted_history = self._filtered_history(item_id)
        if not sorted_history:
            self.stats_label.config(text="")
            return
        # 统计信息
        total = len(sorted_history)
        item = next((i for i in self.app.items if i.get('id') == item_id), {})
        codes = parse_codes(item.get('success_exit_codes', [0]))
        success = sum(1 for h in sorted_history if h.get('status') == 'success' or (h.get('status') not in ('stopped', 'failed', 'unknown') and h.get('exit_code') in codes))
        failed = total - success
        avg_duration = sum((h.get("duration") or 0) for h in sorted_history) / total if total > 0 else 0
        self.stats_label.config(
            text=f"{self._lang['stat_total'] % total} | {self._lang['stat_success'] % success} | {self._lang['stat_failed'] % failed} | {self._lang['stat_avg_duration'] % avg_duration}"
        )
        # 填充表格
        for record in sorted_history:
            start_time = datetime.fromtimestamp(record.get("start_time", 0)).strftime("%Y-%m-%d %H:%M:%S")
            stop_time = datetime.fromtimestamp(record.get("stop_time", 0)).strftime("%Y-%m-%d %H:%M:%S") if record.get("stop_time") else "-"
            duration = f"{record.get('duration', 0):.1f} 秒" if record.get("duration") else "-"
            exit_code = str(record.get("exit_code", "?")) if record.get("exit_code") is not None else "?"
            status = {'success': '成功 / Success', 'failed': '失败 / Failed', 'unknown': '未知 / Unknown', 'stopped': '已停止 / Stopped', 'running': '运行中 / Running', 'crashed': '异常 / Failed'}.get(record.get('status'), '未知 / Unknown')
            self.tree.insert("", tk.END, values=(start_time, stop_time, duration, exit_code, status))
            
    def _on_clear_history(self):
        """清除当前选中脚本的历史记录。"""
        selection = self.script_var.get()
        if not selection:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_script"], parent=self)
            return
        try:
            item_id = int(selection.split("ID:")[1].rstrip(")"))
        except (ValueError, IndexError):
            return
        if not messagebox.askyesno(self._lang["msg_confirm"], self._lang["msg_clear_history_confirm"] % selection, parent=self):
            return
        if item_id in self.app._execution_history:
            del self.app._execution_history[item_id]
            self.app._save_history()
            self._show_history(item_id)
            self.app.status_var.set(self._lang["status_history_cleared"])

    def _on_export_csv(self):
        """导出当前脚本历史为 CSV（应用日期筛选）。"""
        import csv
        selection = self.script_var.get()
        if not selection:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_script"], parent=self)
            return
        try:
            item_id = int(selection.split("ID:")[1].rstrip(")"))
        except (ValueError, IndexError):
            return
        rows = self._filtered_history(item_id)
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_script"], parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
            initialfile=f"history_{item_id}.csv",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["start_time", "stop_time", "duration_sec", "exit_code", "status", "trigger"])
                for record in rows:
                    w.writerow([
                        datetime.fromtimestamp(record.get("start_time", 0)).strftime("%Y-%m-%d %H:%M:%S"),
                        datetime.fromtimestamp(record.get("stop_time", 0)).strftime("%Y-%m-%d %H:%M:%S") if record.get("stop_time") else "",
                        f"{record.get('duration', 0):.1f}",
                        record.get("exit_code", ""),
                        record.get("status", ""),
                        record.get("trigger", ""),
                    ])
            self.app.status_var.set(self._lang.get("status_history_exported", "%s") % path)
        except Exception as e:
            messagebox.showerror(self._lang["msg_error"], str(e), parent=self)


class ScheduleOverviewDialog(tk.Toplevel):
    """定时任务总览。"""
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._lang = app._lang
        self.title(self._lang.get("dialog_schedule_overview", "定时任务总览"))
        self.geometry("820x420")
        self.minsize(640, 300)
        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        sb = ttk.Scrollbar(frame)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree = ttk.Treeview(
            frame,
            columns=("id", "name", "action", "stype", "time", "next"),
            show="headings",
            yscrollcommand=sb.set,
        )
        sb.config(command=self.tree.yview)
        self.tree.pack(fill=tk.BOTH, expand=True)
        heads = [
            ("id", "ID", 50),
            ("name", self._lang.get("col_name", "名称"), 160),
            ("action", self._lang.get("label_schedule_action", "动作"), 70),
            ("stype", self._lang.get("col_schedule_type", "类型"), 90),
            ("time", self._lang.get("label_schedule_time", "时间"), 80),
            ("next", self._lang.get("col_next_run", "下次执行"), 160),
        ]
        for key, text, w in heads:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=w)
        btn = ttk.Frame(self)
        btn.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(btn, text=self._lang.get("btn_refresh", "刷新"), command=self._reload).pack(side=tk.LEFT)
        ttk.Button(btn, text=self._lang["btn_close"], command=self.destroy).pack(side=tk.RIGHT)
        self._reload()

    def _reload(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        rows = []
        for it in self.app.items:
            if not it.get("schedule_enabled"):
                continue
            nxt = self.app._get_next_schedule_run(it) or "-"
            rows.append((
                it.get("id", ""),
                it.get("name", ""),
                it.get("schedule_action", "start"),
                it.get("schedule_type", "daily"),
                it.get("schedule_time", ""),
                nxt,
            ))
        rows.sort(key=lambda r: (str(r[5]), str(r[1])))
        for r in rows:
            self.tree.insert("", tk.END, values=r)


# ---------------------------------------------------------------------------
# GUI：将当前运行中设为启动时自动启动
# ---------------------------------------------------------------------------

class MarkRunningLaunchOnStartDialog(tk.Toplevel):
    """确认把当前运行中的任务勾上「启动本工具时自动启动」。"""

    def __init__(self, parent, app, running_items: list):
        super().__init__(parent)
        self.app = app
        self._lang = app._lang
        self.running_items = running_items
        self.title(self._lang.get("dialog_mark_running_launch_on_start", "将当前运行中设为启动时自动启动"))
        self.geometry("520x420")
        self.minsize(440, 320)
        self.transient(parent)
        self.grab_set()

        btn_frame = ttk.Frame(self)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        ttk.Button(btn_frame, text=self._lang["btn_ok"], command=self._on_ok).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(btn_frame, text=self._lang["btn_cancel"], command=self.destroy).pack(side=tk.RIGHT)

        body = ttk.Frame(self, padding=10)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            body,
            text=self._lang.get("label_mark_running_list", "当前运行中的任务（%d 项）：") % len(running_items),
        ).pack(anchor=tk.W, pady=(0, 6))

        list_frame = ttk.Frame(body)
        list_frame.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(list_frame, height=10, yscrollcommand=scrollbar.set)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.listbox.yview)
        for it in running_items:
            name = it.get("name", "") or "未命名"
            self.listbox.insert(tk.END, "#%s  %s  PID=%s" % (it.get("id", ""), name, it.get("pid", "")))

        self.var_clear_stopped = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            body,
            text=self._lang.get("check_mark_running_clear_stopped", "同时取消未运行任务的「启动时自动启动」"),
            variable=self.var_clear_stopped,
        ).pack(anchor=tk.W, pady=(8, 6))

        ttk.Label(
            body,
            text=self._lang.get("hint_mark_running", ""),
            foreground="gray",
            wraplength=480,
        ).pack(anchor=tk.W)

    def _on_ok(self):
        self.app._apply_mark_running_as_launch_on_start(
            running_ids=[it.get("id") for it in self.running_items],
            clear_stopped=bool(self.var_clear_stopped.get()),
        )
        self.destroy()


# ---------------------------------------------------------------------------
# GUI：批量编辑
# ---------------------------------------------------------------------------

class NotificationConfigDialog(tk.Toplevel):
    """通知配置窗口。"""
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._lang = app._lang
        self.title(self._lang["dialog_notification"])
        self.geometry("600x500")
        self.minsize(500, 400)
        
        # 先放底部按钮，确保「确定」「取消」始终可见
        btn_frame = ttk.Frame(self)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        ttk.Button(btn_frame, text=self._lang["btn_ok"], command=self._on_ok).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(btn_frame, text=self._lang["btn_cancel"], command=self.destroy).pack(side=tk.RIGHT)
        
        # 内容区（可滚动）
        content_frame = ttk.Frame(self)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))
        
        # 通知开关
        main_frame = ttk.LabelFrame(content_frame, text=self._lang["label_basic_settings"], padding=10)
        main_frame.pack(fill=tk.X, padx=0, pady=(0, 10))
        
        self.var_enabled = tk.BooleanVar(value=self.app._notification_config.get("enabled", True))
        ttk.Checkbutton(main_frame, text=self._lang["label_enable_notification"], variable=self.var_enabled).pack(anchor=tk.W, pady=5)
        
        self.var_notify_nonzero = tk.BooleanVar(value=self.app._notification_config.get("notify_on_nonzero_exit", True))
        ttk.Checkbutton(main_frame, text=self._lang["label_notify_nonzero"], variable=self.var_notify_nonzero).pack(anchor=tk.W, pady=5)
        
        self.var_notify_quick = tk.BooleanVar(value=self.app._notification_config.get("notify_on_quick_exit", True))
        ttk.Checkbutton(main_frame, text=self._lang["label_notify_quick"], variable=self.var_notify_quick).pack(anchor=tk.W, pady=5)
        
        self.var_schedule_debug_log = tk.BooleanVar(value=self.app._notification_config.get("schedule_debug_log", False))
        ttk.Checkbutton(main_frame, text=self._lang["label_schedule_debug_log"], variable=self.var_schedule_debug_log).pack(anchor=tk.W, pady=5)
        
        # 快速退出阈值
        threshold_frame = ttk.Frame(main_frame)
        threshold_frame.pack(fill=tk.X, pady=5)
        ttk.Label(threshold_frame, text=self._lang["label_quick_exit_threshold"]).pack(side=tk.LEFT, padx=(0, 5))
        self.var_threshold = tk.DoubleVar(value=self.app._notification_config.get("quick_exit_threshold", 5.0))
        ttk.Spinbox(threshold_frame, from_=1.0, to=300.0, increment=1.0, textvariable=self.var_threshold, width=10).pack(side=tk.LEFT)
        
        # Webhook 配置
        webhook_frame = ttk.LabelFrame(content_frame, text=self._lang["label_webhook_settings"], padding=10)
        webhook_frame.pack(fill=tk.X, padx=0, pady=(0, 10))
        
        self.var_webhook_enabled = tk.BooleanVar(value=self.app._notification_config.get("webhook_enabled", False))
        ttk.Checkbutton(webhook_frame, text=self._lang["label_webhook_enabled"], variable=self.var_webhook_enabled).pack(anchor=tk.W, pady=5)
        
        # URL
        url_frame = ttk.Frame(webhook_frame)
        url_frame.pack(fill=tk.X, pady=5)
        ttk.Label(url_frame, text=self._lang["label_webhook_url"]).pack(side=tk.LEFT, padx=(0, 5))
        self.var_webhook_url = tk.StringVar(value=self.app._notification_config.get("webhook_url", ""))
        ttk.Entry(url_frame, textvariable=self.var_webhook_url, width=50).pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # 请求方法
        method_frame = ttk.Frame(webhook_frame)
        method_frame.pack(fill=tk.X, pady=5)
        ttk.Label(method_frame, text=self._lang["label_webhook_method"]).pack(side=tk.LEFT, padx=(0, 5))
        self.var_webhook_method = tk.StringVar(value=self.app._notification_config.get("webhook_method", "POST"))
        ttk.Radiobutton(method_frame, text="GET", variable=self.var_webhook_method, value="GET").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(method_frame, text="POST", variable=self.var_webhook_method, value="POST").pack(side=tk.LEFT, padx=5)
        
        # 是否发送数据
        self.var_webhook_send_data = tk.BooleanVar(value=self.app._notification_config.get("webhook_send_data", False))
        ttk.Checkbutton(webhook_frame, text=self._lang["label_webhook_send_data"], variable=self.var_webhook_send_data).pack(anchor=tk.W, pady=5)
        
        # 提示信息
        if self._lang == LANGUAGES["zh-CN"]:
            hint_text = """Webhook 通知说明：
• GET 请求：数据作为查询参数发送（如不勾选"发送数据"，则只发送 URL）
• POST 请求：数据以 JSON 格式发送（如不勾选"发送数据"，则只发送空请求）
• 数据包含：script_name, reason, exit_code, duration, timestamp, status
• 示例 URL：https://your-server.com/webhook 或 http://localhost:8080/notify"""
        else:
            hint_text = """Webhook Notification:
• GET request: Data sent as query parameters (if "Send data" unchecked, only URL is sent)
• POST request: Data sent as JSON format (if "Send data" unchecked, only empty request is sent)
• Data includes: script_name, reason, exit_code, duration, timestamp, status
• Example URL: https://your-server.com/webhook or http://localhost:8080/notify"""
        ttk.Label(webhook_frame, text=hint_text, foreground="gray", font=("", 9), justify=tk.LEFT).pack(anchor=tk.W, pady=5)
        
        # 测试按钮
        ttk.Button(webhook_frame, text=self._lang["btn_test"], command=self._on_test_webhook).pack(anchor=tk.W, pady=5)
        
    def _on_test_webhook(self):
        """测试 Webhook 连接。"""
        url = self.var_webhook_url.get().strip()
        if not url:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_enter_webhook_url"], parent=self)
            return
        method = self.var_webhook_method.get().upper()
        send_data = self.var_webhook_send_data.get()
        # 发送测试请求
        try:
            if send_data:
                test_data = {
                    "script_name": "测试脚本" if self._lang == LANGUAGES["zh-CN"] else "Test Script",
                    "reason": "测试通知" if self._lang == LANGUAGES["zh-CN"] else "Test Notification",
                    "exit_code": 1,
                    "duration": 2.5,
                    "timestamp": datetime.now().isoformat(),
                    "status": "测试" if self._lang == LANGUAGES["zh-CN"] else "Test",
                }
            else:
                test_data = {}
            
            if method == "GET":
                if test_data:
                    # 如果 URL 已经包含参数（有 ?），则用 & 追加；否则用 ? 添加
                    params = "&".join([f"{k}={urllib.parse.quote(str(v))}" for k, v in test_data.items()])
                    if params:
                        separator = "&" if "?" in url else "?"
                        full_url = f"{url}{separator}{params}"
                    else:
                        full_url = url
                else:
                    full_url = url
                req = Request(full_url, method="GET")
                with urlopen(req, timeout=5) as response:
                    response.read()
            else:
                if test_data:
                    import json
                    json_data = json.dumps(test_data, ensure_ascii=False).encode("utf-8")
                    req = Request(url, data=json_data, method="POST")
                    req.add_header("Content-Type", "application/json; charset=utf-8")
                else:
                    req = Request(url, method="POST")
                with urlopen(req, timeout=5) as response:
                    response.read()
            messagebox.showinfo(self._lang["msg_success"], self._lang["msg_webhook_test_success"], parent=self)
        except Exception as e:
            messagebox.showerror(self._lang["msg_failed"], self._lang["msg_webhook_test_failed"] % str(e), parent=self)
    
    def _on_ok(self):
        """保存配置到内存并写入文件；失败时提示不关闭。"""
        self.app._notification_config["enabled"] = self.var_enabled.get()
        self.app._notification_config["notify_on_nonzero_exit"] = self.var_notify_nonzero.get()
        self.app._notification_config["notify_on_quick_exit"] = self.var_notify_quick.get()
        self.app._notification_config["quick_exit_threshold"] = self.var_threshold.get()
        self.app._notification_config["webhook_enabled"] = self.var_webhook_enabled.get()
        self.app._notification_config["webhook_url"] = self.var_webhook_url.get().strip()
        self.app._notification_config["webhook_method"] = self.var_webhook_method.get().upper()
        self.app._notification_config["webhook_send_data"] = self.var_webhook_send_data.get()
        self.app._notification_config["schedule_debug_log"] = self.var_schedule_debug_log.get()
        if not self.app._save_notification_config():
            return
        self.app.status_var.set(self._lang["status_notification_saved"])
        self.destroy()


class BatchEditDialog(tk.Toplevel):
    """批量编辑窗口。"""
    def __init__(self, parent, app, rows):
        super().__init__(parent)
        self.app = app
        self._lang = app._lang
        self.rows = rows
        self.title(self._lang.get("dialog_batch_edit", "批量编辑"))
        self.geometry("720x520")
        self.minsize(560, 460)
        
        # 说明
        info_label = ttk.Label(
            self,
            text=self._lang["label_batch_selected"] % len(rows),
            foreground="blue"
        )
        info_label.pack(pady=10)
        
        # 编辑区域
        edit_frame = ttk.LabelFrame(self, text=self._lang["label_batch_edit_title"], padding=10)
        edit_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 分组
        ttk.Label(edit_frame, text=self._lang["label_batch_group"]).grid(row=0, column=0, sticky=tk.W, pady=5)
        self.var_group = tk.StringVar()
        ttk.Entry(edit_frame, textvariable=self.var_group, width=30).grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Button(edit_frame, text=self._lang["btn_clear"], command=lambda: self.var_group.set("")).grid(row=0, column=2, padx=5)
        
        # 解释器（Python/Node.js/Shell）
        ttk.Label(edit_frame, text=self._lang["label_batch_interpreter"]).grid(row=1, column=0, sticky=tk.W, pady=5)
        self.var_interpreter = tk.StringVar()
        ttk.Entry(edit_frame, textvariable=self.var_interpreter, width=30).grid(row=1, column=1, sticky=tk.W, pady=5)
        ttk.Button(edit_frame, text=self._lang["btn_clear"], command=lambda: self.var_interpreter.set("")).grid(row=1, column=2, padx=5)
        
        ttk.Label(edit_frame, text=self._lang["label_batch_args"]).grid(row=2, column=0, sticky=tk.W, pady=5)
        self.var_batch_args = tk.StringVar()
        ttk.Entry(edit_frame, textvariable=self.var_batch_args, width=30).grid(row=2, column=1, sticky=tk.W, pady=5)
        ttk.Button(edit_frame, text=self._lang["btn_clear"], command=lambda: self.var_batch_args.set("")).grid(row=2, column=2, padx=5)
        
        # 定时任务启用/禁用
        ttk.Label(edit_frame, text=self._lang["label_batch_schedule"]).grid(row=3, column=0, sticky=tk.W, pady=5)
        schedule_frame = ttk.Frame(edit_frame)
        schedule_frame.grid(row=3, column=1, sticky=tk.W, pady=5)
        self.var_schedule_action = tk.StringVar(value="no_change")
        ttk.Radiobutton(schedule_frame, text=self._lang["hint_no_change"], variable=self.var_schedule_action, value="no_change").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(schedule_frame, text=self._lang["hint_enable"], variable=self.var_schedule_action, value="enable").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(schedule_frame, text=self._lang["hint_disable"], variable=self.var_schedule_action, value="disable").pack(side=tk.LEFT, padx=5)
        # 定时内容（类型/时间/星期/动作），拆成两行避免挤在一行被裁切
        ttk.Label(edit_frame, text=self._lang["label_batch_schedule_detail"]).grid(row=4, column=0, sticky=tk.NW, pady=5)
        detail_row1 = ttk.Frame(edit_frame)
        detail_row1.grid(row=4, column=1, columnspan=2, sticky=tk.W, pady=0)
        type_vals = [self._lang["hint_no_change"], self._lang["schedule_type_once"], self._lang["schedule_type_daily"], self._lang["schedule_type_weekday"], self._lang["schedule_type_weekly"], self._lang["schedule_type_monthly"], self._lang["schedule_type_hourly"], self._lang["schedule_type_minutely"], self._lang["schedule_type_every_hours"]]
        self.var_batch_schedule_type = tk.StringVar(value=self._lang["hint_no_change"])
        ttk.Label(detail_row1, text=self._lang["label_schedule_type"]).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Combobox(detail_row1, textvariable=self.var_batch_schedule_type, width=10, state="readonly", values=type_vals).pack(side=tk.LEFT, padx=(0, 8))
        self.var_batch_schedule_time = tk.StringVar()
        ttk.Label(detail_row1, text=self._lang["label_schedule_time"]).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Entry(detail_row1, textvariable=self.var_batch_schedule_time, width=8).pack(side=tk.LEFT, padx=(0, 4))
        batch_time_quick = ttk.Frame(detail_row1)
        batch_time_quick.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(batch_time_quick, text=self._lang["btn_time_now1"], width=4, command=lambda: self.var_batch_schedule_time.set(self._get_time_now_plus_1())).pack(side=tk.LEFT, padx=1)
        ttk.Button(batch_time_quick, text=self._lang["btn_time_0"], width=3, command=lambda: self.var_batch_schedule_time.set("00:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(batch_time_quick, text=self._lang["btn_time_3"], width=3, command=lambda: self.var_batch_schedule_time.set("03:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(batch_time_quick, text=self._lang["btn_time_6"], width=3, command=lambda: self.var_batch_schedule_time.set("06:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(batch_time_quick, text=self._lang["btn_time_9"], width=3, command=lambda: self.var_batch_schedule_time.set("09:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(batch_time_quick, text=self._lang["btn_time_12"], width=3, command=lambda: self.var_batch_schedule_time.set("12:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(batch_time_quick, text=self._lang["btn_time_15"], width=3, command=lambda: self.var_batch_schedule_time.set("15:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(batch_time_quick, text=self._lang["btn_time_18"], width=3, command=lambda: self.var_batch_schedule_time.set("18:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(batch_time_quick, text=self._lang["btn_time_21"], width=3, command=lambda: self.var_batch_schedule_time.set("21:00")).pack(side=tk.LEFT, padx=1)
        detail_row2 = ttk.Frame(edit_frame)
        detail_row2.grid(row=5, column=1, columnspan=2, sticky=tk.W, pady=2)
        weekday_vals = [self._lang["hint_no_change"], self._lang["weekday_mon"], self._lang["weekday_tue"], self._lang["weekday_wed"], self._lang["weekday_thu"], self._lang["weekday_fri"], self._lang["weekday_sat"], self._lang["weekday_sun"]]
        self.var_batch_schedule_weekday = tk.StringVar(value=self._lang["hint_no_change"])
        ttk.Label(detail_row2, text=self._lang["label_schedule_weekday"]).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Combobox(detail_row2, textvariable=self.var_batch_schedule_weekday, width=10, state="readonly", values=weekday_vals).pack(side=tk.LEFT, padx=(0, 8))
        action_vals = [self._lang["hint_no_change"], self._lang["schedule_action_start"], self._lang["schedule_action_restart"], self._lang["schedule_action_stop"]]
        self.var_batch_schedule_action = tk.StringVar(value=self._lang["hint_no_change"])
        ttk.Label(detail_row2, text=self._lang["label_schedule_action"]).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Combobox(detail_row2, textvariable=self.var_batch_schedule_action, width=8, state="readonly", values=action_vals).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(edit_frame, text=self._lang["hint_batch_schedule_detail"], foreground="gray", font=("", 8)).grid(row=6, column=1, columnspan=2, sticky=tk.W, pady=0)
        # 保活
        ttk.Label(edit_frame, text=self._lang["label_batch_keepalive"]).grid(row=7, column=0, sticky=tk.W, pady=5)
        keepalive_frame = ttk.Frame(edit_frame)
        keepalive_frame.grid(row=7, column=1, sticky=tk.W, pady=5)
        self.var_keepalive_action = tk.StringVar(value="no_change")
        ttk.Radiobutton(keepalive_frame, text=self._lang["hint_no_change"], variable=self.var_keepalive_action, value="no_change").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(keepalive_frame, text=self._lang["hint_enable"], variable=self.var_keepalive_action, value="enable").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(keepalive_frame, text=self._lang["hint_disable"], variable=self.var_keepalive_action, value="disable").pack(side=tk.LEFT, padx=5)
        
        # 启动本工具时自动启动
        ttk.Label(edit_frame, text=self._lang["label_batch_launch_on_start"]).grid(row=8, column=0, sticky=tk.W, pady=5)
        launch_on_frame = ttk.Frame(edit_frame)
        launch_on_frame.grid(row=8, column=1, sticky=tk.W, pady=5)
        self.var_launch_on_app_start_action = tk.StringVar(value="no_change")
        ttk.Radiobutton(launch_on_frame, text=self._lang["hint_no_change"], variable=self.var_launch_on_app_start_action, value="no_change").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(launch_on_frame, text=self._lang["hint_enable"], variable=self.var_launch_on_app_start_action, value="enable").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(launch_on_frame, text=self._lang["hint_disable"], variable=self.var_launch_on_app_start_action, value="disable").pack(side=tk.LEFT, padx=5)
        
        # 日志输出
        ttk.Label(edit_frame, text=self._lang["label_batch_log"]).grid(row=9, column=0, sticky=tk.W, pady=5)
        log_frame = ttk.Frame(edit_frame)
        log_frame.grid(row=9, column=1, sticky=tk.W, pady=5)
        self.var_log_action = tk.StringVar(value="no_change")
        ttk.Radiobutton(log_frame, text=self._lang["hint_no_change"], variable=self.var_log_action, value="no_change").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(log_frame, text=self._lang["hint_enable"], variable=self.var_log_action, value="enable").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(log_frame, text=self._lang["hint_disable"], variable=self.var_log_action, value="disable").pack(side=tk.LEFT, padx=5)
        
        # 提示
        hint_label = ttk.Label(
            edit_frame,
            text=self._lang["hint_batch_edit"],
            foreground="gray",
            font=("", 9)
        )
        hint_label.grid(row=10, column=0, columnspan=3, pady=(4, 6))
        
        # 按钮（保证在窗口内可见）
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=10, pady=(8, 12))
        ttk.Button(btn_frame, text=self._lang["btn_ok"], command=self._on_ok).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(btn_frame, text=self._lang["btn_cancel"], command=self.destroy).pack(side=tk.RIGHT)

    def _get_time_now_plus_1(self):
        """返回当前时间+1分钟的 HH:MM，供批量编辑快捷填入定时时间。"""
        t = datetime.now() + timedelta(minutes=1)
        return t.strftime("%H:%M")
        
    def _on_ok(self):
        """应用批量修改。"""
        modified = 0
        no_cn, no_en = self._lang["hint_no_change"], "No Change"
        for row in self.rows:
            it = self.app._item_at(row)
            if not it:
                continue
            changed = False
            # 修改分组（留空不修改，只有输入了内容才修改）
            group = self.var_group.get().strip()
            if group:
                it["group"] = group
                changed = True
            # 修改解释器（留空不修改，只有输入了内容才修改）
            interpreter = self.var_interpreter.get().strip()
            if interpreter:
                it["interpreter"] = interpreter
                changed = True
            batch_args = self.var_batch_args.get().strip()
            if batch_args:
                it["args"] = batch_args
                changed = True
            # 修改定时任务启用/禁用
            schedule_action = self.var_schedule_action.get()
            if schedule_action == "enable":
                it["schedule_enabled"] = True
                changed = True
            elif schedule_action == "disable":
                it["schedule_enabled"] = False
                changed = True
            # 定时内容（类型/时间/星期/动作），非「不修改」或空则应用
            # 独立于启用/禁用，只要选了具体值就应用
            st = self.var_batch_schedule_type.get().strip()
            if st and st not in (no_cn, no_en):
                type_map = {self._lang["schedule_type_daily"]: "daily", self._lang["schedule_type_weekly"]: "weekly", self._lang["schedule_type_monthly"]: "monthly", self._lang["schedule_type_hourly"]: "hourly", self._lang["schedule_type_minutely"]: "minutely", self._lang["schedule_type_every_hours"]: "every_hours", self._lang["schedule_type_quarterly"]: "quarterly", self._lang["schedule_type_halfyearly"]: "halfyearly", self._lang["schedule_type_yearly"]: "yearly", self._lang["schedule_type_once"]: "once", self._lang["schedule_type_weekday"]: "weekday", self._lang["schedule_type_weekend"]: "weekend"}
                it["schedule_type"] = type_map.get(st, it.get("schedule_type", "daily"))
                changed = True
            tm = self.var_batch_schedule_time.get().strip()
            if tm and ":" in tm:
                it["schedule_time"] = tm
                changed = True
            wd = self.var_batch_schedule_weekday.get().strip()
            if wd and wd not in (no_cn, no_en):
                wd_map = {self._lang["weekday_mon"]: 0, self._lang["weekday_tue"]: 1, self._lang["weekday_wed"]: 2, self._lang["weekday_thu"]: 3, self._lang["weekday_fri"]: 4, self._lang["weekday_sat"]: 5, self._lang["weekday_sun"]: 6}
                it["schedule_weekday"] = wd_map.get(wd, it.get("schedule_weekday", 0))
                changed = True
            ac = self.var_batch_schedule_action.get().strip()
            if ac and ac not in (no_cn, no_en):
                ac_map = {self._lang["schedule_action_start"]: "start", self._lang["schedule_action_restart"]: "restart", self._lang["schedule_action_stop"]: "stop"}
                it["schedule_action"] = ac_map.get(ac, it.get("schedule_action", "start"))
                changed = True
            # 修改保活
            keepalive_action = self.var_keepalive_action.get()
            if keepalive_action == "enable":
                it["auto_restart"] = True
                changed = True
            elif keepalive_action == "disable":
                it["auto_restart"] = False
                changed = True
            # 修改「启动本工具时自动启动」
            launch_on_action = self.var_launch_on_app_start_action.get()
            if launch_on_action == "enable":
                it["launch_on_app_start"] = True
                changed = True
            elif launch_on_action == "disable":
                it["launch_on_app_start"] = False
                changed = True
            # 修改日志输出
            log_action = self.var_log_action.get()
            if log_action == "enable":
                it["log_output"] = True
                changed = True
            elif log_action == "disable":
                it["log_output"] = False
                changed = True
            if changed:
                modified += 1
        if modified > 0:
            self.app._save_config()
            self.app._sync_tree()
            self.app.status_var.set(self._lang["status_batch_modified"] % modified)
        self.destroy()


# ---------------------------------------------------------------------------
# GUI：实时日志查看 (Tail -f)
# ---------------------------------------------------------------------------

class TailLogDialog(tk.Toplevel):
    """实时日志查看弹窗，模拟 tail -f 效果。"""

    def __init__(self, parent, app, log_path: str, script_name: str):
        super().__init__(parent)
        self.app = app
        self._lang = app._lang
        self._log_path = log_path
        self._running = True
        self.title(self._lang["dialog_tail_log"] % script_name)
        self.geometry("700x400")
        self.minsize(500, 300)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=6, pady=4)
        self.btn_stop = ttk.Button(btn_frame, text=self._lang["btn_tail_stop"], command=self._stop)
        self.btn_stop.pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text=self._lang["btn_tail_clear"], command=self._clear).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text=self._lang["btn_close"], command=self._on_close).pack(side=tk.RIGHT, padx=2)

        self.text = tk.Text(self, wrap=tk.WORD, font=("Consolas", 9), state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._offset = 0
        self._poll()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _poll(self):
        if not self._running:
            return
        try:
            if os.path.isfile(self._log_path):
                with open(self._log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(self._offset)
                    new_data = f.read()
                    self._offset = f.tell()
                if new_data:
                    self.text.config(state=tk.NORMAL)
                    self.text.insert(tk.END, new_data)
                    self.text.see(tk.END)
                    self.text.config(state=tk.DISABLED)
        except Exception:
            pass
        if self._running:
            self.after(1000, self._poll)

    def _stop(self):
        self._running = False
        self.btn_stop.config(state=tk.DISABLED)

    def _clear(self):
        self.text.config(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.config(state=tk.DISABLED)

    def _on_close(self):
        self._running = False
        self.destroy()


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class BatchManagerApp(TaskFeatureMixin):
    def __init__(self, start_minimized_cli: bool = False):
        self._start_minimized_cli = bool(start_minimized_cli)
        self._app_config = {"start_minimized": False, "close_to_tray": False}
        self._load_app_config()
        self.root = tk.Tk()
        # 语言设置
        self._current_language = load_language()
        self._lang = LANGUAGES[self._current_language]
        self.root.title(self._format_window_title())
        self.root.geometry("920x520")
        self.root.minsize(700, 400)

        self.items: list = []  # [{id, name, path, work_dir, is_python, pid}]
        self._pid_to_row: dict = {}  # pid -> row index (用于刷新状态)
        self._next_id = 1
        self._refresh_paused = False  # 暂停定时刷新时不再跑 _refresh_status
        self._schedule_paused = False  # 暂停所有定时任务（不触发任何定时）
        self._refresh_interval_ms = 4000  # 定时刷新间隔（毫秒），可在「设置」-「刷新间隔」中修改
        self._port_titles: dict[int, Optional[str]] = {}  # port -> title 缓存，None 表示已尝试但无标题
        self._port_title_pending: set[int] = set()  # 正在获取标题的端口，避免重复请求
        self._drag_start_item = None  # 拖拽起始项
        self._drag_in_progress = False  # 是否正在拖拽
        self._collapsed_groups: set = set()  # 折叠的分组集合
        self._ports_cache: dict[int, list] = {}  # pid -> ports 列表缓存
        self._ports_cache_time: float = 0  # 端口缓存的时间戳
        self._adopt_cache: dict = {}  # norm_path -> [pid, ...] 进程接管缓存
        self._adopt_cache_time: float = 0  # 缓存时间戳
        self._adopt_scanning: bool = False  # 是否正在后台扫描进程
        self._pids_tree_cache: dict[int, set] = {}  # pid -> 子进程PID集合缓存
        self._pids_tree_cache_time: float = 0  # 进程树缓存时间戳
        self._schedule_last_check = time.time()  # 上次检查定时任务的时间
        self._schedule_last_run: dict[int, float] = {}  # item_id -> 上次执行时间
        self._config_mtime: float = 0  # 脚本列表配置上次修改时间，用于检测外部变更
        self._config_reload_cooldown: float = 0  # 上次重载时间，避免频繁重载
        # 执行历史记录：item_id -> [{"start_time": float, "stop_time": float, "duration": float, "exit_code": int|None, "status": "stopped"|"crashed"}, ...]
        self._execution_history: dict[int, list] = {}
        self._api_server_popen = None  # 本工具启动的 API 进程（独立管理，不在任务列表）
        self._api_config = {"launch_on_app_start": False, "call": {}}
        self._api_status_cached_pids: list = []
        self._api_status_last_scan: float = 0.0
        self._load_api_config()
        # 异常通知配置
        self._notification_config = {
            "enabled": True,  # 是否启用异常通知
            "notify_on_nonzero_exit": True,  # 退出码非0时通知
            "notify_on_quick_exit": True,  # 快速退出（<5秒）时通知
            "quick_exit_threshold": 5.0,  # 快速退出阈值（秒）
            "webhook_url": "",  # Webhook URL（GET 或 POST）
            "webhook_method": "POST",  # 请求方法：GET 或 POST
            "webhook_enabled": False,  # 是否启用 Webhook 通知
            "webhook_send_data": False,  # 是否发送数据（False 时只发送 URL，不包含数据）
            "schedule_debug_log": False,  # 定时任务调试日志（默认关闭）
        }
        self._build_ui()
        self._drop_queue: queue.Queue = queue.Queue()
        self._drop_hook_installed = False
        self._load_config()
        self._load_history()
        self._load_notification_config()
        # 启动时为所有启用定时任务的项初始化 last_run，防止重启后补执行旧窗口内的任务
        self._init_schedule_last_run()
        self._start_refresh_loop()
        self._start_schedule_check_loop()
        self._start_adopt_scan()  # 启动时立即后台扫描一次进程
        if not self._should_start_minimized():
            self._raise_window()
        # 拖放：主线程轮询队列 + 延迟注册 windnd，避免在非主线程调用 Tk 导致闪退
        self.root.after(100, self._poll_drop_queue)
        self.root.after(400, self._install_drop_hook)
        # 关闭窗口即退出
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

    def _raise_window(self):
        """启动时把窗口置前，避免被其它窗口挡住。"""
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(220, lambda: self.root.attributes("-topmost", False))

    def _should_start_minimized(self) -> bool:
        return bool(self._start_minimized_cli or self._app_config.get("start_minimized"))

    def _minimize_main_window(self):
        try:
            self.root.update_idletasks()
            self.root.state("iconic")
        except Exception:
            try:
                self.root.iconify()
            except Exception:
                pass

    def _load_app_config(self):
        saved = load_app_config()
        self._app_config['max_concurrent_tasks'] = saved.get('max_concurrent_tasks', 0)
        if "start_minimized" in saved:
            self._app_config["start_minimized"] = bool(saved["start_minimized"])
        if "close_to_tray" in saved:
            self._app_config["close_to_tray"] = bool(saved["close_to_tray"])

    def _save_app_config(self):
        save_app_config(self._app_config)

    def _on_toggle_start_minimized(self):
        self._app_config["start_minimized"] = self._var_start_minimized.get()
        self._save_app_config()
        if _is_startup_enabled():
            _set_startup_enabled(True)

    def _on_mark_running_as_launch_on_start(self):
        """设置 → 将当前运行中的任务勾上「启动本工具时自动启动」。"""
        running = [it for it in self.items if item_is_currently_running(it)]
        if not running:
            messagebox.showinfo(
                self._lang.get("msg_info", "提示"),
                self._lang.get("msg_mark_running_none", "当前没有运行中的任务。"),
                parent=self.root,
            )
            return
        MarkRunningLaunchOnStartDialog(self.root, self, running)

    def _apply_mark_running_as_launch_on_start(self, running_ids=None, clear_stopped: bool = False):
        result = apply_mark_running_as_launch_on_start(
            self.items,
            running_ids=running_ids,
            clear_stopped=clear_stopped,
        )
        self._save_config()
        self._sync_tree()
        msg = self._lang.get("status_mark_running_done", "已为 %d 个运行中任务勾选启动时自动启动（新勾选 %d）") % (
            result.get("running", 0),
            result.get("enabled", 0),
        )
        if result.get("cleared"):
            msg += self._lang.get("status_mark_running_cleared", "，已取消未运行 %d 项") % result["cleared"]
        self.status_var.set(msg)

    def _on_toggle_close_to_tray(self):
        self._app_config["close_to_tray"] = bool(self._var_close_to_tray.get())
        self._save_app_config()

    def _poll_drop_queue(self):
        """主线程轮询拖放队列（windnd 回调在非主线程，禁止在该线程调用任何 Tk API）。"""
        try:
            while True:
                try:
                    batch = self._drop_queue.get_nowait()
                except queue.Empty:
                    break
                if batch:
                    self._on_drop_files(batch)
        except Exception:
            pass
        try:
            self.root.after(100, self._poll_drop_queue)
        except Exception:
            pass

    def _enqueue_drop_files(self, files):
        """拖放回调：只入队，不碰 Tk（可能在非主线程）。"""
        paths_safe = []
        try:
            if files is None:
                pass
            elif isinstance(files, (str, bytes)):
                paths_safe.append(files)
            else:
                for f in files:
                    try:
                        paths_safe.append(f)
                    except Exception:
                        pass
            out = []
            for p in paths_safe:
                try:
                    if isinstance(p, bytes):
                        try:
                            out.append(p.decode("utf-8"))
                        except Exception:
                            out.append(p.decode("gbk", errors="replace"))
                    elif isinstance(p, str):
                        out.append(p)
                    else:
                        out.append(str(p))
                except Exception:
                    pass
            paths_safe = out
        except Exception:
            paths_safe = []
        if not paths_safe:
            return
        try:
            self._drop_queue.put(paths_safe)
        except Exception:
            pass

    def _install_native_drop_hook(self) -> bool:
        """Windows 原生 WM_DROPFILES，打包 exe 不依赖 windnd。挂在 Tk 客户区 HWND 上（与 windnd 相同）。"""
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            from ctypes import wintypes
            self.root.update_idletasks()
            hwnd = int(self.root.winfo_id())
            WNDPROC, set_long, get_long, call_proc = _win_user32_wndproc_api()

            def _wndproc(h, msg, wp, lp):
                try:
                    if msg == WM_DROPFILES:
                        paths = _win_hdrop_paths(wp)
                        if paths:
                            self._enqueue_drop_files(paths)
                        return 0
                except Exception:
                    pass
                try:
                    return call_proc(self._drop_old_wndproc, h, msg, wp, lp)
                except Exception:
                    return 0

            self._drop_wndproc_ref = WNDPROC(_wndproc)
            self._drop_call_proc = call_proc
            ctypes.windll.shell32.DragAcceptFiles(wintypes.HWND(hwnd), True)
            self._drop_old_wndproc = get_long(hwnd, GWL_WNDPROC)
            set_long(hwnd, GWL_WNDPROC, ctypes.cast(self._drop_wndproc_ref, ctypes.c_void_p))
            self._drop_hwnd = hwnd
            return True
        except Exception:
            return False

    def _install_drop_hook(self):
        """延迟注册拖放：窗口已映射后再 hook，且回调内只入队、不碰 Tk。"""
        if self._drop_hook_installed or sys.platform != "win32":
            return
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        if self._install_native_drop_hook():
            self._drop_hook_installed = True
            return
        if not HAS_WINDND:
            return
        try:
            try:
                windnd.hook_dropfiles(self.root, func=self._enqueue_drop_files, force_unicode=True)
            except TypeError:
                windnd.hook_dropfiles(self.root, func=self._enqueue_drop_files)
            self._drop_hook_installed = True
        except Exception:
            try:
                windnd.hook_dropfiles(self.root.winfo_id(), func=self._enqueue_drop_files)
                self._drop_hook_installed = True
            except Exception:
                pass

    def _on_drop_files(self, files):
        """处理从资源管理器拖入的文件，批量新增任务（仅主线程调用）。"""
        try:
            if not files:
                return
            self._on_drop_files_impl(files)
        except Exception:
            try:
                self.status_var.set("拖放处理异常，请重试" if self._current_language == "zh-CN" else "Drag-drop error, please try again.")
            except Exception:
                pass

    def _on_drop_files_impl(self, files):
        if not files:
            return
        decoded = []
        for value in files:
            if isinstance(value, bytes):
                try: value = value.decode("utf-8")
                except UnicodeDecodeError: value = value.decode("gbk", errors="replace")
            decoded.append(str(value).strip().strip('"').replace("\x00", ""))
        if any(os.path.isdir(value) for value in decoded):
            self._preview_script_import(decoded)
            return
        paths = []
        for f in files:
            try:
                if isinstance(f, bytes):
                    try:
                        f = f.decode("utf-8")
                    except Exception:
                        f = f.decode("gbk", errors="replace")
                elif not isinstance(f, str):
                    f = str(f) if f is not None else ""
                s = (f or "").strip().strip('"').replace("\x00", "")
                if s and os.path.isfile(s):
                    paths.append(s)
            except Exception:
                continue
        if not paths:
            return
        # 拖放始终新增任务，避免误替换已有任务路径
        added = 0
        for path in paths:
            try:
                stype = get_script_type(path)
                if not stype or stype == "unknown":
                    if path.lower().endswith((".bat", ".cmd", ".ps1", ".vbs", ".wsf", ".sh")):
                        stype = get_script_type(path)
                    if not stype or stype == "unknown":
                        stype = "batch"
                name = os.path.splitext(os.path.basename(path))[0] or self._lang["default_name"]
                work_dir = os.path.dirname(path)
                interpreter = ""
                self.items.append({
                    "id": self._next_id,
                    "name": name,
                    "path": path,
                    "work_dir": work_dir,
                    "group": "",
                    "script_type": stype,
                    "interpreter": interpreter,
                    "is_python": stype == "python",
                    "python_exe": interpreter if stype == "python" else "",
                    "auto_restart": False,
                    "launch_on_app_start": False,
                    "log_output": False,
                    "schedule_enabled": False,
                    "schedule_action": "start",
                    "schedule_type": "daily",
                    "schedule_time": "09:00",
                    "schedule_interval": 5,
                    "schedule_weekday": 0,
                    "schedule_day": 1,
                    "schedule_quarter": "q1",
                    "schedule_month": 1,
                    "depends_on": "",
                    "delay_after_dep": 0,
                    "args": "",
                    "pid": None,
                })
                self._next_id += 1
                added += 1
            except Exception:
                continue
        if added > 0:
            self._save_config()
            self._sync_tree()
            self.status_var.set(self._lang["status_drop_added"] % added)

    def _build_ui(self):
        # 菜单栏
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # 首页菜单
        menubar.add_command(label=self._lang.get("menu_home", "首页"), command=self._on_home)
        
        # 语言菜单
        lang_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label=self._lang.get("menu_language", "语言"), menu=lang_menu)
        self._language_menu = lang_menu
        lang_menu.add_command(label=self._task_text("跟随系统", "Follow system"), command=lambda: self._switch_language("system"))
        for code, name in LANGUAGE_OPTIONS.items():
            lang_menu.add_command(label=name, command=lambda selected=code: self._switch_language(selected))
        
        # 设置：刷新间隔（保存引用以便切换语言时更新英文）
        self._settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label=self._lang.get("menu_settings", "设置"), menu=self._settings_menu)
        self._refresh_menu = tk.Menu(self._settings_menu, tearoff=0)
        self._settings_menu.add_cascade(label=self._lang.get("menu_refresh_interval", "刷新间隔"), menu=self._refresh_menu)
        for sec, ms in [(2, 2000), (4, 4000), (8, 8000)]:
            key = "refresh_interval_2s" if sec == 2 else ("refresh_interval_4s" if sec == 4 else "refresh_interval_8s")
            self._refresh_menu.add_command(label=self._lang.get(key, "%d 秒" % sec), command=lambda _ms=ms: self._set_refresh_interval(_ms))
        self._refresh_menu.add_separator()
        self._refresh_menu.add_command(label=self._lang.get("btn_pause_refresh", "暂停刷新"), command=self._on_menu_pause_refresh)
        self._refresh_menu.add_command(label=self._lang.get("btn_resume_refresh", "恢复刷新"), command=self._on_menu_resume_refresh)
        self._var_start_minimized = tk.BooleanVar(value=bool(self._app_config.get("start_minimized", False)))
        self._settings_menu.add_checkbutton(
            label=self._lang.get("menu_start_minimized", "启动后最小化到任务栏"),
            variable=self._var_start_minimized,
            command=self._on_toggle_start_minimized,
        )
        self._start_minimized_menu_index = 1
        self._var_close_to_tray = tk.BooleanVar(value=bool(self._app_config.get("close_to_tray", False)))
        if HAS_TRAY:
            self._settings_menu.add_checkbutton(
                label=self._lang.get("menu_close_to_tray", "关闭窗口时最小化到托盘"),
                variable=self._var_close_to_tray,
                command=self._on_toggle_close_to_tray,
            )
            self._settings_menu.add_command(
                label=self._lang.get("menu_minimize_to_tray", "最小化到托盘"),
                command=self._on_minimize_to_tray_menu,
            )
        self._settings_menu.add_command(
            label=self._lang.get("menu_mark_running_launch_on_start", "将当前运行中设为启动时自动启动"),
            command=self._on_mark_running_as_launch_on_start,
        )
        self._mark_running_menu_index = int(self._settings_menu.index("end"))
        self._settings_menu.add_separator()
        self._settings_menu.add_command(label=self._lang.get("menu_schedule_resume" if self._schedule_paused else "menu_schedule_pause"), command=self._on_toggle_schedule_pause)
        self._schedule_pause_menu_index = int(self._settings_menu.index("end"))
        self._settings_menu.add_separator()
        self._settings_menu.add_command(label=self._lang.get("menu_restore_bak", "从上次备份恢复配置"), command=self._on_restore_config_bak)
        self._settings_menu.add_command(label=self._lang.get("menu_global_hotkeys", "快捷键说明"), command=self._on_show_hotkeys)
        self._settings_menu.add_separator()
        self._settings_menu.add_command(label=self._lang.get("menu_about", "关于"), command=self._on_about)
        
        # 开机/登录自启：Windows 注册表；Mac launchd；Linux systemd user
        self._startup_var = tk.BooleanVar(value=_is_startup_enabled())
        if sys.platform == "win32" or _PLATFORM:
            self._startup_menu = tk.Menu(menubar, tearoff=0)
            self._startup_menu.add_command(
                label=self._get_startup_enable_label(),
                command=self._on_startup_enable,
            )
            self._startup_menu.add_command(
                label=self._get_startup_disable_label(),
                command=self._on_startup_disable,
            )
            menubar.add_cascade(label=self._lang.get("menu_startup", "开机启动"), menu=self._startup_menu)
        
        # 工具栏：统一外层内边距，各行从左顺序排列，避免「右侧拖拽区」与下行错位
        _tpad = (8, 6, 8, 2)
        toolbar_outer = ttk.Frame(self.root, padding=_tpad)
        toolbar_outer.pack(fill=tk.X)
        drop_strip = ttk.Frame(toolbar_outer)
        drop_strip.pack(fill=tk.X, pady=(0, 4))
        self.label_drop_line = ttk.Label(
            drop_strip,
            text=self._lang["label_drop_zone"] + " — " + self._lang["hint_drop_zone"],
            anchor=tk.W,
            foreground="gray",
            wraplength=1100,
        )
        self.label_drop_line.pack(fill=tk.X, anchor=tk.W)
        ops_block = ttk.Frame(toolbar_outer)
        ops_block.pack(fill=tk.X)
        # 第一行：添加/编辑/删除/复制 | 启动/停止/重启
        row1 = ttk.Frame(ops_block)
        row1.pack(fill=tk.X, pady=(0, 3))
        self.btn_add = ttk.Button(row1, text=self._lang["btn_add"], command=self._on_add)
        self.btn_add.pack(side=tk.LEFT, padx=2)
        ToolTip(self.btn_add, "Ctrl+N")
        self.btn_browse_main = ttk.Button(row1, text=self._lang["btn_add_manual"], command=self._on_add_manual)
        self.btn_browse_main.pack(side=tk.LEFT, padx=2)
        self.btn_edit = ttk.Button(row1, text=self._lang["btn_edit"], command=self._on_edit)
        self.btn_edit.pack(side=tk.LEFT, padx=2)
        ToolTip(self.btn_edit, "Ctrl+E / 双击")
        self.btn_delete = ttk.Button(row1, text=self._lang["btn_delete"], command=self._on_delete)
        self.btn_delete.pack(side=tk.LEFT, padx=2)
        ToolTip(self.btn_delete, "Ctrl+D")
        self.btn_duplicate = ttk.Button(row1, text=self._lang["btn_duplicate"], command=self._on_duplicate)
        self.btn_duplicate.pack(side=tk.LEFT, padx=2)
        self.btn_select_all = ttk.Button(row1, text=self._lang["btn_select_all"], command=self._on_select_all)
        self.btn_select_all.pack(side=tk.LEFT, padx=2)
        self.btn_select_none = ttk.Button(row1, text=self._lang["btn_select_none"], command=self._on_select_none)
        self.btn_select_none.pack(side=tk.LEFT, padx=2)
        ttk.Separator(row1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        self.btn_start = ttk.Button(row1, text=self._lang["btn_start"], command=self._on_start)
        self.btn_start.pack(side=tk.LEFT, padx=2)
        ToolTip(self.btn_start, "Ctrl+S")
        self.btn_stop = ttk.Button(row1, text=self._lang["btn_stop"], command=self._on_stop)
        self.btn_stop.pack(side=tk.LEFT, padx=2)
        ToolTip(self.btn_stop, "Ctrl+Q")
        self.btn_restart = ttk.Button(row1, text=self._lang["btn_restart"], command=self._on_restart)
        self.btn_restart.pack(side=tk.LEFT, padx=2)
        # 批量启停
        row1b = ttk.Frame(ops_block)
        row1b.pack(fill=tk.X, pady=(0, 3))
        self.label_batch_ops = ttk.Label(row1b, text=self._lang["label_batch_ops"])
        self.label_batch_ops.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_start_all = ttk.Button(row1b, text=self._lang["btn_start_all"], command=self._on_start_all)
        self.btn_start_all.pack(side=tk.LEFT, padx=2)
        ToolTip(self.btn_start_all, "F6")
        self.btn_stop_all = ttk.Button(row1b, text=self._lang["btn_stop_all"], command=self._on_stop_all)
        self.btn_stop_all.pack(side=tk.LEFT, padx=2)
        ToolTip(self.btn_stop_all, "F7")
        self.btn_start_stopped = ttk.Button(row1b, text=self._lang["btn_start_stopped"], command=self._on_start_stopped)
        self.btn_start_stopped.pack(side=tk.LEFT, padx=2)
        self.btn_stop_running = ttk.Button(row1b, text=self._lang["btn_stop_running"], command=self._on_stop_running)
        self.btn_stop_running.pack(side=tk.LEFT, padx=2)
        if sys.platform == "win32":
            self.btn_stop_all_batch = ttk.Button(row1b, text=self._lang["btn_stop_all_batch"], command=self._on_stop_all_batch)
            self.btn_stop_all_batch.pack(side=tk.LEFT, padx=2)
        else:
            self.btn_stop_all_batch = None
        self.btn_open_tool_folder = ttk.Button(row1b, text=self._lang["btn_open_tool_folder"], command=self._on_open_tool_folder)
        self.btn_open_tool_folder.pack(side=tk.LEFT, padx=2)
        # API 服务（独立管理，默认 WindowsBatchScriptManager_api_server.py，不加入任务列表）
        row_api = ttk.Frame(ops_block)
        row_api.pack(fill=tk.X, pady=(0, 3))
        self.label_api_ops = ttk.Label(row_api, text=self._lang["label_api_ops"])
        self.label_api_ops.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_start_api = ttk.Button(row_api, text=self._lang["btn_start_api"], command=self._on_start_api)
        self.btn_start_api.pack(side=tk.LEFT, padx=2)
        self.btn_stop_api = ttk.Button(row_api, text=self._lang["btn_stop_api"], command=self._on_stop_api)
        self.btn_stop_api.pack(side=tk.LEFT, padx=2)
        self.btn_restart_api = ttk.Button(row_api, text=self._lang["btn_restart_api"], command=self._on_restart_api)
        self.btn_restart_api.pack(side=tk.LEFT, padx=2)
        self.btn_load_api_calls = ttk.Button(row_api, text=self._lang["btn_load_api_calls"], command=self._on_load_api_call_tasks)
        self.btn_load_api_calls.pack(side=tk.LEFT, padx=2)
        self.btn_check_api_env = ttk.Button(
            row_api, text=self._lang["btn_check_api_env"], command=self._on_check_api_environment,
        )
        self.btn_check_api_env.pack(side=tk.LEFT, padx=2)
        ttk.Separator(row_api, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        self.var_api_launch_on_start = tk.BooleanVar(value=bool(self._api_config.get("launch_on_app_start", False)))
        self.check_api_launch_on_start = ttk.Checkbutton(
            row_api,
            text=self._lang["check_launch_api_on_start"],
            variable=self.var_api_launch_on_start,
            command=self._on_api_config_changed,
        )
        self.check_api_launch_on_start.pack(side=tk.LEFT, padx=2)
        self.label_api_status = ttk.Label(row_api, text="", font=("", 9), foreground="gray")
        self.label_api_status.pack(side=tk.LEFT, padx=(8, 0))
        # 第二行：自动识别、识别并加入、刷新状态、端口/进程、访问 | 关闭系统 重启系统
        row2 = ttk.Frame(ops_block)
        row2.pack(fill=tk.X, pady=(0, 3))
        self.btn_scan_running = ttk.Button(row2, text=self._lang["btn_scan_running"], command=self._on_scan_running)
        self.btn_scan_running.pack(side=tk.LEFT, padx=2)
        self.btn_scan_and_add = ttk.Button(row2, text=self._lang["btn_scan_and_add"], command=self._on_scan_and_add)
        self.btn_scan_and_add.pack(side=tk.LEFT, padx=2)
        self.btn_scan_exe_add = ttk.Button(row2, text=self._lang["btn_scan_exe_add"], command=self._on_scan_exe_add)
        self.btn_scan_exe_add.pack(side=tk.LEFT, padx=2)
        ttk.Separator(row2, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        self.btn_refresh = ttk.Button(row2, text=self._lang["btn_refresh"], command=lambda: self._refresh_status(and_set_status=True))
        self.btn_refresh.pack(side=tk.LEFT, padx=2)
        ToolTip(self.btn_refresh, "F5 / Ctrl+R")
        self.btn_pause_refresh = ttk.Button(row2, text=self._lang["btn_pause_refresh"], command=self._on_toggle_refresh)
        self.btn_pause_refresh.pack(side=tk.LEFT, padx=2)
        self.btn_ports = ttk.Button(row2, text=self._lang["btn_ports"], command=self._on_show_ports)
        self.btn_ports.pack(side=tk.LEFT, padx=2)
        self.btn_visit = ttk.Button(row2, text=self._lang["btn_visit"], command=self._on_visit_port)
        self.btn_visit.pack(side=tk.LEFT, padx=2)
        if sys.platform == "win32":
            ttk.Separator(row2, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
            self.btn_shutdown = ttk.Button(row2, text=self._lang["btn_shutdown"], command=self._on_system_shutdown)
            self.btn_shutdown.pack(side=tk.LEFT, padx=2)
            self.btn_reboot = ttk.Button(row2, text=self._lang["btn_reboot"], command=self._on_system_reboot)
            self.btn_reboot.pack(side=tk.LEFT, padx=2)
        else:
            self.btn_shutdown = None
            self.btn_reboot = None
        
        # 第三行：管理功能 … 通知配置 | 实时日志 重启工具
        row3 = ttk.Frame(ops_block)
        row3.pack(fill=tk.X, pady=(0, 2))
        self.btn_export = ttk.Button(row3, text=self._lang["btn_export"], command=self._on_export_config)
        self.btn_export.pack(side=tk.LEFT, padx=2)
        self.btn_import = ttk.Button(row3, text=self._lang["btn_import"], command=self._on_import_config)
        self.btn_import.pack(side=tk.LEFT, padx=2)
        ttk.Separator(row3, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        self.btn_history = ttk.Button(row3, text=self._lang["btn_history"], command=self._on_show_history)
        self.btn_history.pack(side=tk.LEFT, padx=2)
        self.btn_schedule_overview = ttk.Button(row3, text=self._lang.get("btn_schedule_overview", "定时总览"), command=self._on_schedule_overview)
        self.btn_schedule_overview.pack(side=tk.LEFT, padx=2)
        self.btn_batch_edit = ttk.Button(row3, text=self._lang["btn_batch_edit"], command=self._on_batch_edit)
        self.btn_batch_edit.pack(side=tk.LEFT, padx=2)
        self.btn_clear_invalid = ttk.Button(row3, text=self._lang["btn_clear_invalid"], command=self._on_clear_invalid)
        self.btn_clear_invalid.pack(side=tk.LEFT, padx=2)
        self.btn_notification = ttk.Button(row3, text=self._lang["btn_notification"], command=self._on_notification_config)
        self.btn_notification.pack(side=tk.LEFT, padx=2)
        ttk.Separator(row3, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        self.btn_tail_log = ttk.Button(row3, text=self._lang["btn_tail_log"], command=self._on_tail_log)
        self.btn_tail_log.pack(side=tk.LEFT, padx=2)
        self.btn_restart_self = ttk.Button(row3, text=self._lang["btn_restart_self"], command=self._on_restart_self)
        self.btn_restart_self.pack(side=tk.LEFT, padx=2)
        self._toolbar_rows = (row1, row1b, row_api, row2, row3)
        for row in self._toolbar_rows:
            row.bind("<Configure>", lambda event, frame=row: self._wrap_toolbar_row(frame), add="+")


        # 公网和局域网 IP 可点击复制
        row_ip = ttk.Frame(toolbar_outer)
        row_ip.pack(fill=tk.X, pady=(4, 0), anchor=tk.W)
        ip_inline = ttk.Frame(row_ip)
        ip_inline.pack(side=tk.LEFT, anchor=tk.W)
        self.label_public_ip = ttk.Label(ip_inline, text=self._lang["label_public_ip"], font=("", 9))
        self.label_public_ip.pack(side=tk.LEFT, padx=(0, 4))
        self.public_ip_label = ttk.Label(ip_inline, text=self._lang["hint_loading"], foreground="gray", cursor="arrow", font=("", 9))
        self.public_ip_label.pack(side=tk.LEFT)
        self.public_ip_label.bind("<Button-1>", self._on_copy_public_ip)
        self.public_ip_label.bind("<Enter>", self._on_public_ip_enter)
        self.public_ip_label.bind("<Leave>", self._on_public_ip_leave)
        self.hint_copy_public_label = ttk.Label(ip_inline, text="", font=("", 8), foreground="gray")
        self.hint_copy_public_label.pack(side=tk.LEFT, padx=(4, 0))
        ttk.Separator(ip_inline, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=12)
        self.label_local_ip = ttk.Label(ip_inline, text=self._lang["label_local_ip"], font=("", 9))
        self.label_local_ip.pack(side=tk.LEFT, padx=(0, 4))
        self.ip_label = ttk.Label(ip_inline, text=self._lang["hint_loading"], foreground="gray", cursor="hand2", font=("", 9, "underline"))
        self.ip_label.pack(side=tk.LEFT)
        self.ip_label.bind("<Button-1>", self._on_copy_ip)
        self.ip_label.bind("<Enter>", lambda e: self.ip_label.config(foreground="darkblue") if self.ip_label.cget("foreground") == "blue" else None)
        self.ip_label.bind("<Leave>", lambda e: self._on_local_ip_leave())
        self.hint_copy_label = ttk.Label(ip_inline, text=self._lang["hint_click_to_copy"], font=("", 8), foreground="gray")
        self.hint_copy_label.pack(side=tk.LEFT, padx=(4, 0))
        # 公网和局域网 IP 在主循环启动后异步加载

        # 筛选行：按分组过滤（带横向滚动，与工具栏左对齐）
        filter_container = ttk.Frame(self.root)
        filter_container.pack(fill=tk.X, padx=8, pady=(0, 4))
        
        # 创建Canvas和滚动条用于横向滚动
        filter_canvas = tk.Canvas(filter_container, height=30, highlightthickness=0)
        filter_scrollbar = ttk.Scrollbar(filter_container, orient=tk.HORIZONTAL, command=filter_canvas.xview)
        filter_frame = ttk.Frame(filter_canvas)
        
        # 配置Canvas滚动
        filter_canvas.configure(xscrollcommand=filter_scrollbar.set)
        filter_canvas_window = filter_canvas.create_window((0, 0), window=filter_frame, anchor=tk.NW)
        
        # 更新滚动区域
        def update_scroll_region(event=None):
            filter_canvas.configure(scrollregion=filter_canvas.bbox("all"))
            # 限制Canvas宽度，使其可以滚动
            canvas_width = filter_canvas.winfo_width()
            if canvas_width > 1:
                frame_width = filter_frame.winfo_reqwidth()
                filter_canvas.itemconfig(filter_canvas_window, width=max(canvas_width, frame_width))
                # 只在内容超出Canvas宽度时显示滚动条
                if frame_width > canvas_width:
                    filter_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
                else:
                    filter_scrollbar.pack_forget()
        
        filter_frame.bind("<Configure>", update_scroll_region)
        filter_canvas.bind("<Configure>", update_scroll_region)
        # 窗口大小改变时也更新
        self.root.bind("<Configure>", lambda e: update_scroll_region() if e.widget == self.root else None)
        
        # 鼠标滚轮支持
        def on_mousewheel(event):
            filter_canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")
        
        filter_canvas.bind("<MouseWheel>", on_mousewheel)
        filter_canvas.bind("<Button-4>", lambda e: filter_canvas.xview_scroll(-1, "units"))
        filter_canvas.bind("<Button-5>", lambda e: filter_canvas.xview_scroll(1, "units"))
        
        # 布局Canvas和滚动条（滚动条初始隐藏，只在需要时显示）
        filter_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # 滚动条初始不显示，在update_scroll_region中根据需要显示/隐藏
        
        # 筛选控件
        self.label_filter = ttk.Label(filter_frame, text=self._lang["label_filter_group"])
        self.label_filter.pack(side=tk.LEFT, padx=(0, 4))
        self.filter_group_var = tk.StringVar(value=self._task_text("全部", "All"))
        self.filter_combo = ttk.Combobox(filter_frame, textvariable=self.filter_group_var, width=20, state="readonly")
        self.filter_combo.pack(side=tk.LEFT, padx=2)
        self.filter_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_group_filter())
        self.btn_clear_filter = ttk.Button(filter_frame, text=self._lang["btn_clear_filter"], command=self._clear_group_filter)
        self.btn_clear_filter.pack(side=tk.LEFT, padx=2)
        
        # 搜索关键词：输入即过滤列表（名称/路径/工作目录/分组），定位/下一个仍可在结果中跳转
        ttk.Separator(filter_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        self.label_search_keyword = ttk.Label(filter_frame, text=self._lang["label_search_keyword"])
        self.label_search_keyword.pack(side=tk.LEFT, padx=(0, 4))
        self.search_keyword_var = tk.StringVar()
        self._search_filter_after_id = None
        self.search_entry = ttk.Entry(filter_frame, textvariable=self.search_keyword_var, width=24)
        self.search_entry.pack(side=tk.LEFT, padx=2)
        self.search_entry.bind("<Return>", lambda e: self._on_search_locate(forward=True))
        self.search_entry.bind("<Escape>", self._on_search_escape)
        self.search_keyword_var.trace_add("write", self._on_search_keyword_changed)
        self.search_tooltip = ToolTip(self.search_entry, self._lang["hint_search_keyword"])
        self.search_match_label = ttk.Label(filter_frame, text="", foreground="gray")
        self.search_match_label.pack(side=tk.LEFT, padx=(4, 2))
        self.btn_locate = ttk.Button(filter_frame, text=self._lang["btn_locate"], command=lambda: self._on_search_locate(forward=True))
        self.btn_locate.pack(side=tk.LEFT, padx=2)
        self.btn_next_match = ttk.Button(filter_frame, text=self._lang["btn_next_match"], command=lambda: self._on_search_locate(forward=False))
        self.btn_next_match.pack(side=tk.LEFT, padx=2)

        # 保存引用以便后续更新
        self.filter_canvas = filter_canvas
        self.filter_frame = filter_frame

        # 列表（PanedWindow 必须用 add 添加子控件；列表加横向滚动条，避免状态、PID、端口列被挤出视区）
        pan = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        self.task_pane = pan
        pan.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        tree_frame = ttk.Frame(pan)
        cols = ("id", "name", "group", "path", "work_dir", "type", "status", "schedule", "title", "ports", "pid", "launch_on", "auto_restart", "depends", "args")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=14, selectmode="extended")
        col_headings = {
            "id": self._lang["col_id"],
            "name": self._lang["col_name"],
            "group": self._lang["col_group"],
            "path": self._lang["col_path"],
            "work_dir": self._lang["col_work_dir"],
            "type": self._lang["col_type"],
            "status": self._lang["col_status"],
            "pid": self._lang["col_pid"],
            "ports": self._lang["col_port"],
            "title": self._lang["col_title"],
            "schedule": self._lang["col_schedule"],
            "launch_on": self._lang["col_launch_on"],
            "auto_restart": self._lang["col_auto_restart"],
            "depends": self._lang["col_depends"],
            "args": self._lang["col_args"],
        }
        for c in cols:
            self.tree.heading(c, text=col_headings.get(c, c))
        self.tree.column("id", width=50)
        self.tree.column("name", width=100)
        self.tree.column("group", width=80)
        self.tree.column("path", width=200)
        self.tree.column("work_dir", width=80)
        self.tree.column("type", width=84)
        self.tree.column("status", width=104)
        self.tree.column("schedule", width=225)
        self.tree.column("title", width=180)
        self.tree.column("ports", width=100)
        self.tree.column("pid", width=52)
        self.tree.column("launch_on", width=64)
        self.tree.column("auto_restart", width=64)
        self.tree.column("depends", width=64)
        self.tree.column("args", width=80)
        tree_vbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        tree_hbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_vbar.set, xscrollcommand=tree_hbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_vbar.grid(row=0, column=1, sticky="ns")
        tree_hbar.grid(row=1, column=0, sticky="ew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        pan.add(tree_frame, weight=1)
        self.tree.bind("<Double-1>", lambda e: self._on_edit())
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<Control-a>", lambda e: self._on_select_all() or "break")
        # 鼠标拖拽多选
        self.tree.bind("<Button-1>", self._on_tree_button1)
        self.tree.bind("<B1-Motion>", self._on_tree_drag)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release)
        # 全局快捷键
        self.root.bind("<Control-n>", lambda e: self._on_add())
        self.root.bind("<Control-e>", lambda e: self._on_edit())
        self.root.bind("<Control-d>", lambda e: self._on_delete())
        self.root.bind("<Control-r>", lambda e: self._refresh_status(and_set_status=True))
        self.root.bind("<Control-s>", lambda e: self._on_start())
        self.root.bind("<Control-q>", lambda e: self._on_stop())
        self.root.bind("<F5>", lambda e: self._refresh_status(and_set_status=True))
        self.root.bind("<F6>", lambda e: self._on_start_all())
        self.root.bind("<F7>", lambda e: self._on_stop_all())
        self.root.bind("<Control-f>", lambda e: self._focus_search())
        # Alt+Up/Down：移动选中行上下排序
        self.tree.bind("<Alt-Up>", lambda e: self._on_move_row(-1))
        self.tree.bind("<Alt-Down>", lambda e: self._on_move_row(1))

        # 详情
        self.detail_frame = ttk.LabelFrame(pan, text=self._lang["detail_label"])
        pan.add(self.detail_frame, weight=0)
        self.detail_text = tk.Text(self.detail_frame, height=3, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9))
        detail_scroll = ttk.Scrollbar(self.detail_frame, command=self.detail_text.yview)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        self.detail_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # 状态栏
        self.status_var = tk.StringVar(value=self._lang["status_ready_msg"])
        ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM, padx=6, pady=2)
        self.root.bind("<Control-a>", lambda e: (self._on_select_all(), "break")[1])

    def _get_selected_rows(self) -> list:
        """返回选中项在 self.items 中的索引。按任务 id 映射，与分组/关键词筛选无关。"""
        rows = []
        seen = set()
        for iid in self.tree.selection():
            idx = self._item_index_from_tree_iid(iid)
            if idx is not None and idx not in seen:
                seen.add(idx)
                rows.append(idx)
        # 无选中且仅有 1 项时，自动视作选中该项，避免重启/停止/编辑等总是弹出「请先选中」
        if not rows and len(self.items) == 1:
            return [0]
        return rows

    def _on_select_all(self):
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(*children)
            self.status_var.set(self._lang["status_selected_all"] % len(children))

    def _on_select_none(self):
        self.tree.selection_set(())
        self.status_var.set(self._lang["status_deselected_all"])

    def _item_at(self, row: int) -> Optional[dict]:
        if 0 <= row < len(self.items):
            return self.items[row]
        return None

    def _get_invalid_rows(self) -> list:
        """返回脚本路径为空或文件不存在的行索引列表（用于一键清除无效项）。URL/进程名类型不参与检查。"""
        invalid = []
        for row in range(len(self.items)):
            it = self.items[row]
            path = (it.get("path") or "").strip()
            if not path:
                invalid.append(row)
                continue
            # URL 类型跳过文件存在性检查
            if path.startswith("http://") or path.startswith("https://"):
                continue
            # 进程名类型跳过文件存在性检查
            if get_script_type(path) == "process":
                continue
            # 命令类型跳过文件存在性检查
            if it.get("script_type") == "command":
                continue
            full = resolve_script_file_path(it)
            if full and not os.path.isfile(full):
                invalid.append(row)
        return invalid

    def _on_select(self, evt):
        sel = self.tree.selection()
        if not sel:
            self.detail_text.config(state=tk.NORMAL)
            self.detail_text.delete("1.0", tk.END)
            self.detail_text.config(state=tk.DISABLED)
            self._update_visit_button_state()
            return
        rows = self._get_selected_rows()
        it = self._item_at(rows[0]) if rows else None
        if it:
            path_label = self._task_text("路径", "Path")
            work_label = self._task_text("工作目录", "Work Directory")
            work_dir = it.get("work_dir") or self._task_text("（同脚本目录）", "(same as script dir)")
            s = f"{path_label}: {it.get('path', '')}\n{work_label}: {work_dir}"
            is_http = _is_http_task_item(it)
            self.detail_text.config(height=8 if is_http else 3)
            if is_http and it.get("_http_response") is not None:
                heading = self._task_text("HTTP 返回结果", "HTTP response")
                s += f"\n\n{heading}\n{it['_http_response']}"
                pane_height = self.task_pane.winfo_height()
                target_height = min(200, pane_height // 3)
                if target_height > self.detail_frame.winfo_height():
                    self.task_pane.sashpos(0, pane_height - target_height)
            self.detail_text.config(state=tk.NORMAL)
            self.detail_text.delete("1.0", tk.END)
            self.detail_text.insert("1.0", s)
            self.detail_text.config(state=tk.DISABLED)
        self._update_visit_button_state()

    def _update_visit_button_state(self):
        """根据当前选中项是否有端口，启用或禁用「访问」按钮。"""
        _, ports = self._get_ports_for_selected()
        self.btn_visit.config(state=tk.NORMAL if ports else tk.DISABLED)

    def _load_history(self):
        """加载执行历史记录。"""
        self._execution_history = load_history()
        # 限制历史记录数量，每个脚本最多保留最近100条
        for item_id in self._execution_history:
            if len(self._execution_history[item_id]) > 100:
                self._execution_history[item_id] = self._execution_history[item_id][-100:]

    def _save_history(self):
        """保存执行历史记录。"""
        save_history(self._execution_history)

    def _load_api_config(self):
        saved = load_api_config()
        if isinstance(saved, dict):
            if "launch_on_app_start" in saved:
                self._api_config["launch_on_app_start"] = bool(saved["launch_on_app_start"])
            if isinstance(saved.get("call"), dict):
                self._api_config["call"] = saved["call"]

    def _save_api_config(self) -> bool:
        return save_api_config(self._api_config)

    def _on_api_config_changed(self):
        self._api_config["launch_on_app_start"] = bool(self.var_api_launch_on_start.get())
        self._save_api_config()

    def _current_lan_ip(self) -> str:
        try:
            t = (self.ip_label.cget("text") or "").strip()
            if _looks_like_ipv4(t):
                return t
        except Exception:
            pass
        got = get_local_ip()
        return got if _looks_like_ipv4(got) else "127.0.0.1"

    def _persist_api_call_config(self) -> dict:
        cfg = build_api_call_config(self._current_lan_ip())
        self._api_config["call"] = cfg
        self._save_api_config()
        return cfg

    def _set_api_call_status_hint(self, call: Optional[dict] = None):
        cfg = call or self._api_config.get("call") or build_api_call_config(self._current_lan_ip())
        docs = cfg.get("docs_url") or ((cfg.get("base_url") or "") + "/")
        self.status_var.set(self._lang["status_api_call_hint"] % docs)

    def _load_notification_config(self):
        """加载通知配置。"""
        saved_config = load_notification_config()
        if saved_config:
            # 合并保存的配置
            for key, value in saved_config.items():
                if key in self._notification_config:
                    self._notification_config[key] = value

    def _save_notification_config(self) -> bool:
        """保存通知配置。成功返回 True，失败返回 False 并弹窗提示。"""
        if save_notification_config(self._notification_config):
            return True
        messagebox.showerror(self._lang["msg_error"], self._lang.get("msg_notification_save_failed", "通知配置保存失败"))
        return False

    def _wrap_toolbar_row(self, row):
        width = row.winfo_width()
        if width <= 4:
            return
        x = y = line_height = 0
        layout = []
        for widget in row.winfo_children():
            widget_width = min(widget.winfo_reqwidth(), width - 4)
            widget_height = widget.winfo_reqheight()
            if x and x + widget_width + 4 > width:
                x = 0
                y += line_height + 3
                line_height = 0
            layout.append((widget, x + 2, y, widget_width))
            x += widget_width + 4
            line_height = max(line_height, widget_height)
        height = y + line_height
        if getattr(row, "_toolbar_layout", None) == (layout, height):
            return
        row._toolbar_layout = (layout, height)
        row.pack_propagate(False)
        row.configure(height=height)
        for widget, left, top, widget_width in layout:
            widget.pack_forget()
            widget.place(x=left, y=top, width=widget_width)

    def _switch_language(self, language: str):
        """切换语言。"""
        preference = normalize_language_preference(language)
        language = detect_system_language() if preference == "system" else preference
        previous_language = self._current_language
        previous_labels = self._lang
        self._current_language = language
        self._lang = LANGUAGES[language]
        save_language(preference)
        self._language_menu.entryconfig(0, label=self._task_text("跟随系统", "Follow system"))
        self._refresh_task_menu_language()
        # 更新窗口标题
        self.root.title(self._format_window_title())
        # 更新所有按钮文本
        self.btn_add.config(text=self._lang["btn_add"])
        self.btn_browse_main.config(text=self._lang["btn_add_manual"])
        self.btn_edit.config(text=self._lang["btn_edit"])
        self.btn_delete.config(text=self._lang["btn_delete"])
        self.btn_duplicate.config(text=self._lang["btn_duplicate"])
        self.btn_select_all.config(text=self._lang["btn_select_all"])
        self.btn_select_none.config(text=self._lang["btn_select_none"])
        self.btn_start.config(text=self._lang["btn_start"])
        self.btn_stop.config(text=self._lang["btn_stop"])
        self.btn_restart.config(text=self._lang["btn_restart"])
        if getattr(self, "label_drop_line", None):
            self.label_drop_line.config(text=self._lang["label_drop_zone"] + " — " + self._lang["hint_drop_zone"])
        if getattr(self, "label_batch_ops", None):
            self.label_batch_ops.config(text=self._lang["label_batch_ops"])
        self.btn_start_all.config(text=self._lang["btn_start_all"])
        self.btn_stop_all.config(text=self._lang["btn_stop_all"])
        self.btn_start_stopped.config(text=self._lang["btn_start_stopped"])
        self.btn_stop_running.config(text=self._lang["btn_stop_running"])
        self.btn_scan_running.config(text=self._lang["btn_scan_running"])
        self.btn_scan_and_add.config(text=self._lang["btn_scan_and_add"])
        self.btn_scan_exe_add.config(text=self._lang["btn_scan_exe_add"])
        self.btn_refresh.config(text=self._lang["btn_refresh"])
        self.btn_pause_refresh.config(text=self._lang["btn_pause_refresh"])
        self.btn_ports.config(text=self._lang["btn_ports"])
        self.btn_visit.config(text=self._lang["btn_visit"])
        self.btn_export.config(text=self._lang["btn_export"])
        self.btn_import.config(text=self._lang["btn_import"])
        self.btn_history.config(text=self._lang["btn_history"])
        if getattr(self, "btn_schedule_overview", None):
            self.btn_schedule_overview.config(text=self._lang.get("btn_schedule_overview", "定时总览"))
        self.btn_batch_edit.config(text=self._lang["btn_batch_edit"])
        self.btn_clear_invalid.config(text=self._lang["btn_clear_invalid"])
        self.btn_notification.config(text=self._lang["btn_notification"])
        if getattr(self, "btn_tail_log", None):
            self.btn_tail_log.config(text=self._lang["btn_tail_log"])
        if getattr(self, "btn_stop_all_batch", None):
            self.btn_stop_all_batch.config(text=self._lang["btn_stop_all_batch"])
        self.btn_open_tool_folder.config(text=self._lang["btn_open_tool_folder"])
        if getattr(self, "btn_restart_self", None):
            self.btn_restart_self.config(text=self._lang["btn_restart_self"])
        if getattr(self, "btn_shutdown", None):
            self.btn_shutdown.config(text=self._lang["btn_shutdown"])
        if getattr(self, "btn_reboot", None):
            self.btn_reboot.config(text=self._lang["btn_reboot"])
        if getattr(self, "label_api_ops", None):
            self.label_api_ops.config(text=self._lang["label_api_ops"])
        if getattr(self, "btn_start_api", None):
            self.btn_start_api.config(text=self._lang["btn_start_api"])
        if getattr(self, "btn_stop_api", None):
            self.btn_stop_api.config(text=self._lang["btn_stop_api"])
        if getattr(self, "btn_restart_api", None):
            self.btn_restart_api.config(text=self._lang["btn_restart_api"])
        if getattr(self, "btn_load_api_calls", None):
            self.btn_load_api_calls.config(text=self._lang["btn_load_api_calls"])
            self.btn_check_api_env.config(text=self._lang["btn_check_api_env"])
        if getattr(self, "check_api_launch_on_start", None):
            self.check_api_launch_on_start.config(text=self._lang["check_launch_api_on_start"])
        self._update_api_status_label()
        # 更新筛选标签
        self.label_filter.config(text=self._lang["label_filter_group"])
        self.btn_clear_filter.config(text=self._lang["btn_clear_filter"])
        # 搜索关键词与定位
        self.label_search_keyword.config(text=self._lang["label_search_keyword"])
        self.btn_locate.config(text=self._lang["btn_locate"])
        self.btn_next_match.config(text=self._lang["btn_next_match"])
        if getattr(self, "search_tooltip", None):
            self.search_tooltip.text = self._lang["hint_search_keyword"]
        self._update_search_match_label(update_status=False)
        # 更新IP标签
        self.label_local_ip.config(text=self._lang["label_local_ip"])
        self.label_public_ip.config(text=self._lang["label_public_ip"])
        self.hint_copy_label.config(text=self._lang["hint_click_to_copy"])
        # 如果IP是"无法获取"，更新显示文本
        current_ip_text = self.ip_label.cget("text")
        if current_ip_text in ("无法获取", "Unable to get IP address", previous_labels["msg_ip_unavailable"]):
            self.ip_label.config(text=self._lang["msg_ip_unavailable"])
        elif current_ip_text == previous_labels["hint_loading"]:
            self.ip_label.config(text=self._lang["hint_loading"])
        # 更新外网IP标签文本（如果已加载）
        current_public_ip_text = self.public_ip_label.cget("text")
        if current_public_ip_text in ("无法获取", "Unable to get IP address", previous_labels["msg_ip_unavailable"]):
            self.public_ip_label.config(text=self._lang["msg_ip_unavailable"], foreground="gray", cursor="arrow")
            self.hint_copy_public_label.config(text="")
        elif current_public_ip_text == previous_labels["hint_loading"]:
            self.public_ip_label.config(text=self._lang["hint_loading"])
            self.hint_copy_public_label.config(text="")
        else:
            # 如果外网IP已加载，显示复制提示
            if current_public_ip_text and current_public_ip_text != self._lang["hint_loading"] and current_public_ip_text != self._lang["msg_ip_unavailable"]:
                self.hint_copy_public_label.config(text=self._lang["hint_click_to_copy"])
        # 更新详情标签
        self.detail_frame.config(text=self._lang["detail_label"])
        # 更新表格列标题
        cols = ("id", "name", "group", "path", "work_dir", "type", "status", "schedule", "title", "ports", "pid", "launch_on", "auto_restart", "depends", "args")
        col_headings = {
            "id": self._lang["col_id"],
            "name": self._lang["col_name"],
            "group": self._lang["col_group"],
            "path": self._lang["col_path"],
            "work_dir": self._lang["col_work_dir"],
            "type": self._lang["col_type"],
            "status": self._lang["col_status"],
            "pid": self._lang["col_pid"],
            "ports": self._lang["col_port"],
            "title": self._lang["col_title"],
            "schedule": self._lang["col_schedule"],
            "launch_on": self._lang["col_launch_on"],
            "auto_restart": self._lang["col_auto_restart"],
            "depends": self._lang["col_depends"],
            "args": self._lang["col_args"],
        }
        for c in cols:
            self.tree.heading(c, text=col_headings.get(c, c))
        # 更新状态栏
        self.status_var.set(self._lang["status_ready_msg"])
        # 更新筛选下拉框的"全部"选项
        if self.filter_group_var.get() == translate_inline(previous_language, "全部", "All"):
            self.filter_group_var.set(self._task_text("全部", "All"))
        # 更新菜单
        try:
            menubar = self.root.nametowidget(self.root.cget("menu"))
            for i in range(menubar.index("end") + 1):
                try:
                    menu_label = menubar.entryconfig(i, "label")[4]
                    if menu_label == previous_labels["menu_language"]:
                        menubar.entryconfig(i, label=self._lang.get("menu_language", "语言"))
                    elif menu_label == previous_labels["menu_home"]:
                        menubar.entryconfig(i, label=self._lang.get("menu_home", "首页"))
                    elif menu_label == previous_labels["menu_startup"]:
                        menubar.entryconfig(i, label=self._lang.get("menu_startup", "开机启动"))
                        if (sys.platform == "win32" or _PLATFORM) and getattr(self, "_startup_menu", None):
                            self._update_startup_submenu_labels()
                    elif menu_label == previous_labels["menu_settings"]:
                        menubar.entryconfig(i, label=self._lang.get("menu_settings", "设置"))
                        if getattr(self, "_settings_menu", None):
                            self._settings_menu.entryconfig(0, label=self._lang.get("menu_refresh_interval", "刷新间隔"))
                            if getattr(self, "_start_minimized_menu_index", None) is not None:
                                self._settings_menu.entryconfig(
                                    self._start_minimized_menu_index,
                                    label=self._lang.get("menu_start_minimized", "启动后最小化到任务栏"),
                                )
                            if getattr(self, "_mark_running_menu_index", None) is not None:
                                self._settings_menu.entryconfig(
                                    self._mark_running_menu_index,
                                    label=self._lang.get("menu_mark_running_launch_on_start", "将当前运行中设为启动时自动启动"),
                                )
                            if getattr(self, "_schedule_pause_menu_index", None) is not None:
                                self._settings_menu.entryconfig(self._schedule_pause_menu_index, label=self._lang.get("menu_schedule_resume" if self._schedule_paused else "menu_schedule_pause"))
                        if getattr(self, "_refresh_menu", None):
                            for idx, sec in enumerate([2, 4, 8]):
                                key = "refresh_interval_2s" if sec == 2 else ("refresh_interval_4s" if sec == 4 else "refresh_interval_8s")
                                self._refresh_menu.entryconfig(idx, label=self._lang.get(key, "%d 秒" % sec))
                except:
                    pass
        except:
            pass
        for menu, keys in (
            (self._settings_menu, ("menu_close_to_tray", "menu_minimize_to_tray",
                                   "menu_restore_bak", "menu_global_hotkeys", "menu_about")),
            (self._refresh_menu, ("btn_pause_refresh", "btn_resume_refresh")),
        ):
            updated_labels = {previous_labels[key]: self._lang[key] for key in keys
                              if key in previous_labels and key in self._lang}
            for index in range(menu.index("end") + 1):
                if menu.type(index) == "separator":
                    continue
                label = menu.entrycget(index, "label")
                if label in updated_labels:
                    menu.entryconfig(index, label=updated_labels[label])
        # 更新分组筛选选项
        self._update_group_filter_options()
        # 刷新表格显示
        self._sync_tree()
        self._on_select(None)
        for row in self._toolbar_rows:
            self._wrap_toolbar_row(row)

    def _record_execution_start(self, item_id: int, trigger: str = "manual"):
        """记录脚本启动时间。trigger: 'manual' 或 'schedule'。"""
        if item_id not in self._execution_history:
            self._execution_history[item_id] = []
        self._execution_history[item_id].append({
            "start_time": time.time(),
            "stop_time": None,
            "duration": None,
            "exit_code": None,
            "status": "running",
            "trigger": trigger,
        })
        # 只保留最近100条
        if len(self._execution_history[item_id]) > 100:
            self._execution_history[item_id] = self._execution_history[item_id][-100:]
        self._save_history()

    def _record_execution_stop(self, item_id: int, exit_code: Optional[int], duration: float):
        """记录脚本停止时间、退出码和运行时长。"""
        if item_id not in self._execution_history:
            return
        if not self._execution_history[item_id]:
            return
        # 更新最后一条记录（应该是状态为 "running" 的）
        for record in reversed(self._execution_history[item_id]):
            if record.get("status") == "running":
                record["stop_time"] = time.time()
                record["duration"] = duration
                record["exit_code"] = exit_code
                item = next((x for x in self.items if x.get("id") == item_id), {})
                record["status"] = "unknown" if exit_code is None else ("success" if exit_code in parse_codes(item.get("success_exit_codes", [0])) else "failed")
                break
        self._save_history()
        # 检查是否需要通知
        self._check_and_notify(item_id, exit_code, duration)

    def _check_and_notify(self, item_id: int, exit_code: Optional[int], duration: float):
        """检查是否需要发送异常通知。"""
        if not self._notification_config.get("enabled", True):
            return
        # 查找对应的脚本项
        item = None
        for it in self.items:
            if it.get("id") == item_id:
                item = it
                break
        if not item:
            return
        name = item.get("name", "未命名")
        should_notify = False
        reason = ""
        # 检查退出码
        if self._notification_config.get("notify_on_nonzero_exit", True):
            if exit_code is not None and exit_code not in parse_codes(item.get("success_exit_codes", [0])):
                should_notify = True
                reason = self._lang["msg_exit_code_detail"] % exit_code
        # 检查快速退出
        if self._notification_config.get("notify_on_quick_exit", True):
            threshold = self._notification_config.get("quick_exit_threshold", 5.0)
            if duration < threshold:
                should_notify = True
                if reason:
                    reason += self._lang["msg_duration_only_with_comma"] % duration
                else:
                    reason = self._lang["msg_duration_only"] % duration
        if should_notify:
            self._show_notification(name, reason, exit_code, duration, item)

    def _show_notification(self, name: str, reason: str, exit_code: Optional[int], duration: float, item=None):
        """显示异常通知（系统通知或弹窗，可选 Webhook），附尾部日志。"""
        title = self._lang["msg_script_abnormal_exit"]
        msg = self._lang["msg_abnormal_exit"] % (name, reason)
        if exit_code is not None:
            msg += "\n" + self._lang["msg_exit_code"] % exit_code
        msg += "\n" + self._lang["msg_duration"] % duration
        # 附上尾部日志（如果有）
        if item:
            log_path = item.get("_log_path")
            if log_path and os.path.isfile(log_path):
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        tail = (f.read() or "")[-500:]
                    if tail.strip():
                        msg += "\n\n--- Log ---\n" + tail
                except Exception:
                    pass

        # 发送 Webhook 通知（如果启用）
        if self._notification_config.get("webhook_enabled", False):
            webhook_url = self._notification_config.get("webhook_url", "").strip()
            if webhook_url:
                self._send_webhook_notification(name, reason, exit_code, duration, webhook_url)
        
        # 使用弹窗通知（更可靠，不依赖额外库）
        self.root.after(0, lambda: messagebox.showwarning(title, msg, parent=self.root))
    
    def _send_notification_if_enabled(self, event_type: str, name: str, extra_info=None):
        """发送通知（崩溃循环等），复用现有 webhook 配置。"""
        if not self._notification_config.get("enabled", True):
            return
        reason = f"Crash Loop ({extra_info} times/min)" if event_type == "crash_loop" else event_type
        if self._notification_config.get("webhook_enabled", False):
            webhook_url = self._notification_config.get("webhook_url", "").strip()
            if webhook_url:
                self._send_webhook_notification(name, reason, None, 0, webhook_url)

    def _send_webhook_notification(self, name: str, reason: str, exit_code: Optional[int], duration: float, url: str):
        """发送 Webhook 通知（GET 或 POST），失败时最多重试 2 次并退避。"""
        method = self._notification_config.get("webhook_method", "POST").upper()
        if method not in ("GET", "POST"):
            method = "POST"
        
        send_data = self._notification_config.get("webhook_send_data", False)
        
        # 构建通知数据（如果启用发送数据）
        if send_data:
            data = {
                "script_name": name,
                "reason": reason,
                "exit_code": exit_code,
                "duration": duration,
                "timestamp": datetime.now().isoformat(),
                "status": "异常退出" if self._current_language == "zh-CN" else "Abnormal Exit",
            }
        else:
            data = {}
        
        # 在后台线程中发送，避免阻塞 UI
        def send_request():
            last_err = None
            for attempt in range(3):
                try:
                    if method == "GET":
                        # GET 请求：将数据作为查询参数（如果启用发送数据）
                        if data:
                            # 如果 URL 已经包含参数（有 ?），则用 & 追加；否则用 ? 添加
                            params = "&".join([f"{k}={urllib.parse.quote(str(v))}" for k, v in data.items()])
                            if params:
                                separator = "&" if "?" in url else "?"
                                full_url = f"{url}{separator}{params}"
                            else:
                                full_url = url
                        else:
                            full_url = url
                        req = Request(full_url, method="GET")
                        with urlopen(req, timeout=5) as response:
                            response.read()
                    else:
                        # POST 请求：发送 JSON 数据（如果启用发送数据）
                        if data:
                            json_data = json.dumps(data, ensure_ascii=False).encode("utf-8")
                            req = Request(url, data=json_data, method="POST")
                            req.add_header("Content-Type", "application/json; charset=utf-8")
                        else:
                            req = Request(url, method="POST")
                        with urlopen(req, timeout=5) as response:
                            response.read()
                    return
                except Exception as e:
                    last_err = e
                    if attempt < 2:
                        time.sleep(0.5 * (2 ** attempt))
            if last_err is not None:
                self.root.after(0, lambda: self.status_var.set(f"Webhook 通知发送失败: {str(last_err)[:50]}"))
        
        threading.Thread(target=send_request, daemon=True).start()

    def _load_config(self):
        raw = load_config()
        self.items = []
        self._next_id = 1
        for r in migrate_items(raw):
            path = r.get("path", "")
            # 兼容旧配置：从 is_python 推断 script_type
            stype = r.get("script_type")
            if not stype:
                if r.get("is_python"):
                    stype = "python"
                else:
                    stype = get_script_type(path) or "batch"
            interpreter = r.get("interpreter") or r.get("python_exe") or ""
            self.items.append({
                "id": r["id"],
                **{key: r[key] for key in FEATURE_FIELDS if key in r},
                "name": r.get("name", "未命名"),
                "path": path,
                "work_dir": r.get("work_dir") or "",
                "group": r.get("group", ""),
                "script_type": stype,
                "interpreter": interpreter,
                # 兼容旧配置
                "is_python": _bool_from_config(r.get("is_python", False)),
                "python_exe": interpreter if stype == "python" else "",
                "auto_restart": _bool_from_config(r.get("auto_restart", False)),
                "launch_on_app_start": _bool_from_config(r.get("launch_on_app_start", False)),
                "log_output": _bool_from_config(r.get("log_output", False)),
                "schedule_enabled": _bool_from_config(r.get("schedule_enabled", False)),
                "schedule_action": r.get("schedule_action", "start"),
                "schedule_type": r.get("schedule_type", "daily"),
                "schedule_time": r.get("schedule_time", "09:00"),
                "schedule_interval": r.get("schedule_interval", 5),
                "schedule_weekday": r.get("schedule_weekday", 0),
                "schedule_day": r.get("schedule_day", 1),
                "schedule_quarter": r.get("schedule_quarter", "q1"),
                "schedule_month": r.get("schedule_month", 1),
                "depends_on": r.get("depends_on", ""),
                "delay_after_dep": r.get("delay_after_dep", 0),
                "args": r.get("args", ""),
                "env_vars": r.get("env_vars", ""),
                "http_method": r.get("http_method") or "GET",
                "http_headers": r.get("http_headers") or "",
                "startup_timeout": r.get("startup_timeout", 0),
                "schedule_valid_from": r.get("schedule_valid_from", ""),
                "schedule_valid_until": r.get("schedule_valid_until", ""),
                "schedule_max_runs": r.get("schedule_max_runs", 0),
                "schedule_pre_delay": r.get("schedule_pre_delay", 0),
                "schedule_once_date": r.get("schedule_once_date", ""),
                "pid": None,
            })
            self._next_id = max(self._next_id, self.items[-1]["id"]) + 1
        self._next_id = max(self._next_id, next_item_id(self.items))
        for item in self.items:
            if item['id'] in getattr(self, '_task_active', {}):
                run = self._task_active[item['id']]
                if run.get('path', run['item'].get('path')) == item.get('path'):
                    item.update(pid=run['process'].pid, _popen=run['process'], pid_set_at=run['start'])
            if item['id'] in getattr(getattr(self, '_task_queue', None), 'pending', []):
                item['_queued'] = True
        # 记录配置文件修改时间，用于检测外部变更
        try:
            self._config_mtime = CONFIG_PATH.stat().st_mtime
        except Exception:
            self._config_mtime = 0
        self._sync_tree()

    def _compute_row_values(self, it):
        """计算单行的显示值，供 _sync_tree 使用。"""
        type_names = {
            "python": "Python",
            "node": "Node.js",
            "powershell": "PowerShell",
            "vbs": "VBScript",
            "jscript": "JScript",
            "batch": self._task_text("批处理", "Batch"),
            "shell": "Shell",
            "wsf": "WSF",
            "exe": "EXE",
            "http_get": "HTTP",
            "process": self._task_text("进程", "Process"),
            "command": self._task_text("命令", "Command"),
            "system_shutdown": self._lang.get("task_type_system_shutdown", "系统关机"),
            "system_reboot": self._lang.get("task_type_system_reboot", "系统重启"),
        }
        stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
        if not stype or stype == "unknown":
            if it.get("is_python"):
                stype = "python"
            else:
                stype = get_script_type(it.get("path", "")) or "batch"
        typ = type_names.get(stype, self._task_text("批处理", "Batch"))
        pid = it.get("pid")
        pid_set_at = it.get("pid_set_at") or 0
        running = pid and (is_process_running(pid) or (pid_set_at and (time.time() - pid_set_at) < 2))
        path_invalid = False
        if not running and not is_system_power_task(it):
            pp = (it.get("path") or "").strip()
            if pp and not (pp.startswith("http://") or pp.startswith("https://")):
                is_process_type = get_script_type(pp) == "process"
                is_command_type = it.get("script_type") == "command"
                if not is_process_type and not is_command_type:
                    full = resolve_script_file_path(it)
                    path_invalid = not (full and os.path.isfile(full))
        # 崩溃循环检测
        crash_loop = it.get("_crash_loop", False)
        if crash_loop and not running:
            status = self._lang["status_crash_loop"]
        else:
            status = self._lang["status_running"] if running else (self._lang["status_path_invalid"] if path_invalid else self._lang["status_stopped"])
        if it.get("_queued"):
            status = self._task_text("排队中", "Queued")
        elif not running and it.get("_last_result"):
            status = it["_last_result"]
        pid_s = str(pid) if pid else "-"
        ports_s = "-"
        title_s = "-"
        schedule_s = "-"
        if it.get("schedule_enabled"):
            schedule_action = it.get("schedule_action", "start")
            _lang = self._lang
            action_label = _lang.get(f"schedule_action_{schedule_action}", _lang["schedule_action_start"])
            if is_system_power_task(it):
                action_label = _lang["schedule_action_start"]
            schedule_type = it.get("schedule_type", "daily")
            type_label = _lang.get(f"schedule_type_{schedule_type}", _lang["schedule_type_daily"])
            schedule_s = f"{action_label} · {type_label}"
            schedule_time = it.get("schedule_time", "09:00")
            if schedule_type == "once":
                date = it.get("schedule_once_date") or datetime.now().strftime("%Y-%m-%d")
                schedule_s += f" {date} {schedule_time}"
            elif schedule_type in ("minutely", "every_hours"):
                interval = it.get("schedule_interval", 5)
                unit = self._task_text("分钟", "min") if schedule_type == "minutely" else self._task_text("小时", "h")
                schedule_s += f" ({interval} {unit})"
            elif schedule_type == "weekly":
                try:
                    weekday = int(it.get("schedule_weekday", 0))
                except (TypeError, ValueError):
                    weekday = 0
                weekday_keys = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
                weekday_label = _lang[f"weekday_{weekday_keys[weekday % 7]}"]
                schedule_s += f" {weekday_label} {schedule_time}"
            elif schedule_type == "monthly":
                try:
                    day = max(1, min(31, int(it.get("schedule_day", 1))))
                except (TypeError, ValueError):
                    day = 1
                day_label = _lang["schedule_day_last"] if day == 31 else str(day)
                schedule_s += f" {day_label} {schedule_time}"
            elif schedule_type != "hourly":
                schedule_s += f" {schedule_time}"
            next_run = self._get_next_schedule_run(it) if it.get("schedule_enabled") else None
            if next_run:
                schedule_s = schedule_s + " (" + (self._lang["schedule_next_run"] % next_run) + ")"
        if running:
            # 使用缓存获取端口，避免每个任务都调 psutil.net_connections / netstat
            now = time.time()
            if now - self._ports_cache_time > 30:
                # 每30秒刷新一次全局端口映射和进程树
                try:
                    if HAS_PSUTIL:
                        conns = psutil.net_connections(kind="inet")
                        pid_ports: dict[int, list[int]] = {}
                        for c in conns:
                            if c.status == "LISTEN" and c.laddr and c.pid:
                                pid_ports.setdefault(c.pid, []).append(c.laddr.port)
                    else:
                        # 降级：用 netstat -ano（慢，但有30秒缓存还行）
                        pid_ports = {}
                        try:
                            r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10, creationflags=0x08000000)
                            if r.returncode == 0:
                                for line in r.stdout.splitlines():
                                    parts = line.split()
                                    if len(parts) >= 5 and parts[0].lower() in ("tcp", "tcpv6") and ("LISTENING" in line or "监听" in line):
                                        try:
                                            port_str = parts[1].rsplit(":", 1)[-1]
                                            pid_str = parts[-1]
                                            if port_str.isdigit() and pid_str.isdigit():
                                                pid_ports.setdefault(int(pid_str), []).append(int(port_str))
                                        except (ValueError, IndexError):
                                            pass
                        except Exception:
                            pass
                    self._ports_cache = pid_ports
                    self._ports_cache_time = now
                    self._pids_tree_cache = {}
                    self._pids_tree_cache_time = now
                except (psutil.AccessDenied, OSError):
                    pass
            # 从缓存中查找：pid 及其子进程的端口
            cached = self._ports_cache
            if pid not in self._pids_tree_cache and now - self._pids_tree_cache_time < 30:
                self._pids_tree_cache[pid] = get_process_tree_pids(pid)
            pids = self._pids_tree_cache.get(pid, {pid})
            ports = sorted({p for px in pids if px in cached for p in cached[px]})
            if ports:
                ports_s = ",".join(map(str, ports))
                first_port = ports[0]
                if first_port in self._port_titles:
                    title = self._port_titles[first_port]
                    if title:
                        title_s = title
                else:
                    if first_port not in self._port_title_pending:
                        self._port_title_pending.add(first_port)
                        threading.Thread(target=self._fetch_port_title_thread, args=(first_port,), daemon=True).start()
        group = it.get("group", "") or "-"
        launch_on_s = self._lang["cell_yes"] if it.get("launch_on_app_start") else self._lang["cell_no"]
        auto_restart_s = self._lang["cell_yes"] if it.get("auto_restart") else self._lang["cell_no"]
        depends_s = (it.get("depends_on") or "-") if it.get("depends_on") else "-"
        args_s = (it.get("args") or "-")[:20] if it.get("args") else "-"
        return (
            str(it.get("id", "")),
            it.get("name", ""),
            group,
            (it.get("path", ""))[:70] + ("..." if len(it.get("path", "")) > 70 else ""),
            (it.get("work_dir", "") or "-")[:50],
            typ,
            status,
            schedule_s,
            title_s,
            ports_s,
            pid_s,
            launch_on_s,
            auto_restart_s,
            depends_s,
            args_s,
        ), group

    def _search_keywords(self) -> list:
        """拆分搜索框中的关键词（空格分隔，忽略大小写）。"""
        raw = (self.search_keyword_var.get() or "").strip().lower()
        if not raw:
            return []
        return [p for p in raw.split() if p]

    def _item_matches_keywords(self, it: dict, keywords: list) -> bool:
        """用完整路径/名称等字段匹配，避免表格截断导致漏搜。"""
        if not keywords:
            return True
        haystack = " ".join([
            str(it.get("id", "")),
            it.get("name", "") or "",
            it.get("group", "") or "",
            it.get("path", "") or "",
            it.get("work_dir", "") or "",
        ]).lower()
        return all(kw in haystack for kw in keywords)

    def _iter_visible_items(self):
        """按当前分组折叠、分组筛选、关键词过滤，产出 (items索引, item)。"""
        keywords = self._search_keywords()
        filter_group = self.filter_group_var.get()
        all_text = self._task_text("全部", "All")
        for i, it in enumerate(self.items):
            group = it.get("group", "") or "-"
            if group != "-" and group in self._collapsed_groups:
                continue
            if filter_group and filter_group != all_text and group != filter_group:
                continue
            if keywords and not self._item_matches_keywords(it, keywords):
                continue
            yield i, it

    def _item_id_from_tree_iid(self, iid):
        try:
            vals = self.tree.item(iid, "values")
            if vals:
                return int(vals[0])
        except (ValueError, TypeError, tk.TclError):
            return None
        return None

    def _item_index_by_id(self, item_id):
        for i, it in enumerate(self.items):
            if it.get("id") == item_id:
                return i
        return None

    def _item_index_from_tree_iid(self, iid):
        item_id = self._item_id_from_tree_iid(iid)
        if item_id is None:
            return None
        return self._item_index_by_id(item_id)

    def _on_search_keyword_changed(self, *_args):
        after_id = getattr(self, "_search_filter_after_id", None)
        if after_id:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
        self._search_filter_after_id = self.root.after(120, self._apply_search_filter)

    def _apply_search_filter(self):
        self._search_filter_after_id = None
        self._sync_tree()
        if self._search_keywords():
            self.tree.yview_moveto(0)
        self._update_search_match_label(update_status=True)

    def _on_search_escape(self, event=None):
        if (self.search_keyword_var.get() or "").strip():
            self.search_keyword_var.set("")
            return "break"
        return None

    def _focus_search(self):
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, tk.END)
        return "break"

    def _update_search_match_label(self, update_status: bool = False):
        if not getattr(self, "search_match_label", None):
            return
        keywords = self._search_keywords()
        if not keywords:
            self.search_match_label.config(text="")
            return
        scoped = 0
        matched = 0
        filter_group = self.filter_group_var.get()
        all_text = self._task_text("全部", "All")
        for it in self.items:
            group = it.get("group", "") or "-"
            if group != "-" and group in self._collapsed_groups:
                continue
            if filter_group and filter_group != all_text and group != filter_group:
                continue
            scoped += 1
            if self._item_matches_keywords(it, keywords):
                matched += 1
        text = self._lang["msg_search_filtered"] % (matched, scoped)
        self.search_match_label.config(text=text)
        if update_status:
            raw = (self.search_keyword_var.get() or "").strip()
            if matched == 0:
                self.status_var.set(self._lang["msg_search_no_match"] % raw)
            else:
                self.status_var.set(text)

    def _sync_tree(self, force_rebuild=False):
        """增量更新树形视图，仅更新变化的行。force_rebuild=True 时全量重建。"""
        # 刷新前记下当前选中项的任务 id（筛选后行号会变）
        saved_ids = []
        try:
            for iid in self.tree.selection():
                item_id = self._item_id_from_tree_iid(iid)
                if item_id is not None:
                    saved_ids.append(item_id)
        except Exception:
            saved_ids = []

        visible_items = []
        for _i, it in self._iter_visible_items():
            vals, _group = self._compute_row_values(it)
            visible_items.append(vals)

        children = self.tree.get_children()
        # 行数变化或强制重建时全量重建
        rebuild = force_rebuild or len(children) != len(visible_items)
        if rebuild:
            scroll_position = self.tree.yview()[0]
            for i in self.tree.get_children():
                self.tree.delete(i)
            for vals in visible_items:
                self.tree.insert("", tk.END, values=vals)
        else:
            # 增量更新：只更新变化的行
            for i, child in enumerate(children):
                new_vals = visible_items[i]
                try:
                    old_vals = self.tree.item(child, "values")
                    # tuple vs list 比较，转为 tuple
                    if tuple(str(v) for v in old_vals) != tuple(str(v) for v in new_vals):
                        self.tree.item(child, values=new_vals)
                except Exception:
                    self.tree.item(child, values=new_vals)

        children = self.tree.get_children()
        to_sel = []
        saved_set = set(saved_ids)
        for child in children:
            item_id = self._item_id_from_tree_iid(child)
            if item_id in saved_set:
                to_sel.append(child)
        if tuple(to_sel) != self.tree.selection():
            self.tree.selection_set(*to_sel)
        if rebuild and children:
            self.tree.yview_moveto(scroll_position)
        self._update_visit_button_state()
        self._update_search_match_label(update_status=False)
        # 分组选项随任务总数变化更新，避免关键词过滤时反复重建 combobox
        if not hasattr(self, '_last_group_count') or len(self.items) != self._last_group_count:
            self._last_group_count = len(self.items)
            self._update_group_filter_options()

    def _update_group_filter_options(self):
        """更新分组筛选下拉框的选项（全部 + 所有分组）。"""
        groups = sorted(set(it.get("group", "") for it in self.items if it.get("group")))
        all_text = self._task_text("全部", "All")
        options = [all_text] + groups
        self.filter_combo["values"] = options
        current = self.filter_group_var.get()
        if current not in options:
            self.filter_group_var.set(all_text)
        # 如果当前是"全部"/"All"，更新为当前语言的文本
        elif current in ("全部", "All"):
            self.filter_group_var.set(all_text)

    def _on_search_locate(self, forward: bool = True):
        """在当前可见列表中定位。列表已按关键词过滤时，相当于在匹配结果间跳转。"""
        keyword = (self.search_keyword_var.get() or "").strip()
        if not keyword:
            self.search_entry.focus_set()
            return
        children = list(self.tree.get_children())
        if not children:
            self.status_var.set(self._lang["msg_search_no_match"] % keyword)
            return
        start_index = 0
        if not forward:
            sel = self.tree.selection()
            if sel:
                try:
                    idx = children.index(sel[0])
                    start_index = (idx + 1) % len(children)
                except ValueError:
                    pass
        pos = start_index
        iid = children[pos]
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        self.status_var.set(self._lang["msg_search_located"] % (pos + 1))

    def _apply_group_filter(self):
        """应用分组筛选。"""
        self._sync_tree()

    def _clear_group_filter(self):
        """清除分组筛选与关键词过滤。"""
        all_text = self._task_text("全部", "All")
        self.filter_group_var.set(all_text)
        if (self.search_keyword_var.get() or "").strip():
            self.search_keyword_var.set("")
        else:
            self._sync_tree()

    def _fetch_port_title_thread(self, port: int):
        """在后台线程中获取端口页面标题，成功后通过 root.after 更新表格（线程安全）。"""
        if port in self._port_titles:
            return
        title = get_page_title(port)
        self._port_titles[port] = title  # title 可能是字符串或 None（已尝试但无标题）
        self._port_title_pending.discard(port)
        if title:
            self.root.after(0, self._sync_tree)  # 通过 after 在主线程更新 UI（线程安全）

    def _on_tree_button1(self, event):
        """鼠标左键按下：记录起始位置，用于判断是单击还是拖拽。"""
        item = self.tree.identify_row(event.y)
        self._drag_start_item = item
        self._drag_in_progress = False
        # 不在这里修改选中状态，让 Treeview 的默认行为处理单击
        # 拖拽时再处理多选

    def _on_tree_drag(self, event):
        """鼠标拖拽：Alt+拖拽排序，普通拖拽多选。"""
        if not self._drag_start_item:
            return
        # Alt+拖拽：排序模式
        if event.state & 0x20000:  # Alt 键
            target = self.tree.identify_row(event.y)
            if not target or target == self._drag_start_item:
                return
            try:
                src_idx = self._item_index_from_tree_iid(self._drag_start_item)
                tgt_idx = self._item_index_from_tree_iid(target)
            except (ValueError, tk.TclError):
                return
            if src_idx is None or tgt_idx is None:
                return
            if src_idx != tgt_idx:
                moved = self.items.pop(src_idx)
                if src_idx < tgt_idx:
                    tgt_idx -= 1
                self.items.insert(tgt_idx, moved)
                self._sync_tree(force_rebuild=True)
                moved_id = moved.get("id")
                for child in self.tree.get_children():
                    if self._item_id_from_tree_iid(child) == moved_id:
                        self.tree.selection_set(child)
                        break
            self._drag_in_progress = True
            return
        # 普通拖拽：多选模式
        self._drag_in_progress = True
        item = self.tree.identify_row(event.y)
        if not item:
            return
        
        # 获取起始和当前行的索引
        try:
            start_idx = self.tree.index(self._drag_start_item)
            current_idx = self.tree.index(item)
        except (ValueError, tk.TclError):
            return
        
        # 选中从起始到当前的所有行
        children = self.tree.get_children()
        if not children:
            return
        
        min_idx = min(start_idx, current_idx)
        max_idx = max(start_idx, current_idx)
        to_select = [children[i] for i in range(min_idx, max_idx + 1) if 0 <= i < len(children)]
        if to_select:
            self.tree.selection_set(*to_select)
            # 滚动到当前行
            self.tree.see(item)
        
        # 自动滚动（当拖拽到边缘时）
        region = self.tree.bbox(item)
        if region:
            y = event.y
            if y < 30:  # 接近顶部
                self.tree.yview_scroll(-1, "units")
            elif y > self.tree.winfo_height() - 30:  # 接近底部
                self.tree.yview_scroll(1, "units")

    def _on_tree_release(self, event):
        """鼠标左键释放：结束拖拽。"""
        if self._drag_in_progress:
            # 拖拽结束，不触发单击事件
            self._drag_in_progress = False
        self._drag_start_item = None

    def _on_move_row(self, direction: int):
        """Alt+Up/Down 移动选中行的顺序。direction=-1 上移，+1 下移。筛选时与相邻可见项交换。"""
        rows = self._get_selected_rows()
        if not rows:
            return
        idx = rows[0]
        visible_indices = [i for i, _it in self._iter_visible_items()]
        try:
            pos = visible_indices.index(idx)
        except ValueError:
            return
        new_pos = pos + direction
        if new_pos < 0 or new_pos >= len(visible_indices):
            return
        other = visible_indices[new_pos]
        self.items[idx], self.items[other] = self.items[other], self.items[idx]
        self._sync_tree(force_rebuild=True)
        self._save_config()
        moved_id = self.items[other].get("id")
        for child in self.tree.get_children():
            if self._item_id_from_tree_iid(child) == moved_id:
                self.tree.selection_set(child)
                self.tree.focus(child)
                self.tree.see(child)
                break
        return "break"

    def _on_tree_right_click(self, event):
        """右键菜单：若点击在某行上则先选中该行，再弹出 启动/停止/重启/编辑/删除/复制/全选/取消全选/端口进程。"""
        item = self.tree.identify_row(event.y)
        if item:
            # 如果右键点击的行未选中，先选中它
            if item not in self.tree.selection():
                self.tree.selection_set(item)
            self.tree.focus(item)
        # 获取选中项信息（提前，后续菜单项需要用到 it）
        rows = self._get_selected_rows()
        it = None
        if rows:
            it = self._item_at(rows[0])
        ctx = tk.Menu(self.root, tearoff=0)
        ctx.add_command(label=self._lang["menu_start"], command=self._on_start)
        ctx.add_command(label=self._lang["menu_stop"], command=self._on_stop)
        ctx.add_command(label=self._lang["menu_restart"], command=self._on_restart)
        ctx.add_separator()
        ctx.add_command(label=self._lang["menu_edit"], command=self._on_edit)
        ctx.add_command(label=self._lang["menu_delete"], command=self._on_delete)
        ctx.add_command(label=self._lang["menu_duplicate"], command=self._on_duplicate)
        # 批量编辑（多选时显示）
        if len(rows) > 1:
            ctx.add_command(label=self._lang["menu_batch_edit"], command=self._on_batch_edit)
        # 模板
        if it:
            ctx.add_command(label=self._lang["menu_save_template"], command=self._on_save_template)
        ctx.add_command(label=self._lang["menu_apply_template"], command=self._on_apply_template)
        ctx.add_command(label=self._lang["menu_manage_templates"], command=self._on_manage_templates)
        # 移动排序
        if it:
            ctx.add_command(label=self._lang["menu_move_up"], command=lambda: self._on_move_row(-1))
            ctx.add_command(label=self._lang["menu_move_down"], command=lambda: self._on_move_row(1))
        # 复制菜单
        if it:
            copy_menu = tk.Menu(ctx, tearoff=0)
            copy_menu.add_command(label=self._lang["menu_copy_name"], command=lambda: self._copy_to_clipboard(it.get("name", "")))
            copy_menu.add_command(label=self._lang["menu_copy_path"], command=lambda: self._copy_to_clipboard(it.get("path", "")))
            pid_val = it.get("pid")
            if pid_val:
                copy_menu.add_command(label=self._lang["menu_copy_pid"], command=lambda: self._copy_to_clipboard(str(pid_val)))
            ctx.add_cascade(label=self._lang.get("menu_copy", "复制"), menu=copy_menu)
        ctx.add_separator()
        
        # 检查所有选中项，看是否有程序本身
        app_path = get_app_path()
        has_app_itself = False
        if rows:
            for row in rows:
                item = self._item_at(row)
                if item:
                    script_path = item.get("path", "")
                    if script_path:
                        try:
                            if os.path.normcase(os.path.abspath(script_path)) == os.path.normcase(app_path):
                                has_app_itself = True
                                break
                        except Exception:
                            pass
        
        # 打开目录/文件（如果选中项包含程序本身，则不显示"打开文件"选项）
        if it and not has_app_itself:
            script_path = it.get("path", "")
            work_dir = it.get("work_dir", "") or (os.path.dirname(script_path) if script_path else "")
            if script_path and os.path.isfile(script_path):
                ctx.add_command(label=self._lang["menu_open_file"], command=lambda p=script_path: self._on_open_file(p))
            if work_dir and os.path.isdir(work_dir):
                ctx.add_command(label=self._lang["menu_open_dir_work"], command=lambda d=work_dir: self._on_open_dir(d))
            elif script_path:
                script_dir = os.path.dirname(script_path)
                if script_dir and os.path.isdir(script_dir):
                    ctx.add_command(label=self._lang["menu_open_dir_script"], command=lambda d=script_dir: self._on_open_dir(d))
        ctx.add_separator()
        ctx.add_command(label=self._lang["menu_select_all"], command=self._on_select_all)
        ctx.add_command(label=self._lang["menu_deselect_all"], command=self._on_select_none)
        ctx.add_separator()
        # 分组批量启停
        if it:
            group = it.get("group", "")
            if group:
                ctx.add_command(label=self._lang["menu_start_group"], command=lambda g=group: self._on_start_group(g))
                ctx.add_command(label=self._lang["menu_stop_group"], command=lambda g=group: self._on_stop_group(g))
        # 开机自启
        if it:
            if self._is_auto_start_enabled():
                ctx.add_command(label=self._lang["menu_remove_auto_start"], command=self._on_toggle_auto_start)
            else:
                ctx.add_command(label=self._lang["menu_auto_start_on_boot"], command=self._on_toggle_auto_start)
        ctx.add_separator()
        ctx.add_command(label=self._lang["menu_ports"], command=self._on_show_ports)
        # 实时日志 & 性能曲线
        if it and (it.get("_log_path") or it.get("log_output")):
            ctx.add_command(label=self._lang["btn_tail_log"], command=self._on_tail_log)
        # 查看日志（无论是否运行都可用）
        if it:
            ctx.add_command(label=self._lang["menu_view_log"], command=self._on_view_log)
            ctx.add_command(label=self._lang.get("menu_diagnose", "诊断启动问题"), command=lambda i=it: self._on_diagnose_item(i))
            ctx.add_command(label=self._lang.get("menu_copy_diagnose", "复制诊断信息"), command=lambda i=it: self._copy_to_clipboard(diagnose_start_item(i)))
        it, ports = self._get_ports_for_selected()
        if ports:
            ctx.add_separator()
            for p in ports[:6]:
                ctx.add_command(label=self._lang["menu_visit"] % p, command=lambda _p=p: self._on_visit_port(_p))
        ctx.tk_popup(event.x_root, event.y_root)

    def _save_config(self):
        arr = [config_item_to_save(it) for it in self.items]
        if not save_config(arr):
            self.status_var.set(self._lang["status_config_save_failed"])
        else:
            # 更新 mtime，防止 GUI 自身写入触发不必要的配置重载
            try:
                self._config_mtime = CONFIG_PATH.stat().st_mtime
            except Exception:
                pass

    def _make_log_path(self, it: dict) -> Optional[str]:
        """当 it 勾选 log_output 且支持日志时，生成 logs/ 下路径；否则 None。"""
        if not it.get("log_output"):
            return None
        stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
        if not stype or stype == "unknown":
            if it.get("is_python"):
                stype = "python"
            else:
                stype = get_script_type(it.get("path", "")) or "batch"
        if stype not in ("python", "node", "powershell"):
            return None
        log_dir = CONFIG_PATH.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        base = "".join(c if c.isalnum() or c in "._-" else "_" for c in (it.get("name") or "out"))[:50]
        return str(log_dir / f"{base}_{int(time.time())}.log")

    def _refresh_status(self, and_set_status: bool = False):
        # 检测脚本列表配置是否被外部修改（如 HTTP API / CLI），自动重载
        self._check_config_changed()
        restarted = None
        for it in self.items:
            if it.get('_queued') or it.get("id") in getattr(self, "_task_active", {}):
                continue
            pid = it.get("pid")
            if not pid:
                continue
            if is_process_running(pid):
                # 启动超时检测：脚本运行超过指定时间未退出，视为启动成功
                startup_timeout = it.get("startup_timeout", 0)
                if startup_timeout > 0 and not it.get("_startup_confirmed"):
                    pid_set_at = it.get("pid_set_at") or 0
                    if pid_set_at and (time.time() - pid_set_at) >= startup_timeout:
                        it["_startup_confirmed"] = True
                        name = it.get("name", "")
                        self.status_var.set(self._lang["status_startup_timeout"] % (name, startup_timeout))
                continue
            # 启动宽容：pid 刚设置不足 2 秒，可能是启动延迟，暂不判为已结束
            pid_set_at = it.get("pid_set_at") or 0
            if pid_set_at and (time.time() - pid_set_at) < 2:
                continue
            # 进程已结束
            # HTTP 请求类型（curl）执行完即退出，保活无意义，跳过保活逻辑
            is_http = (it.get("script_type") == "http_get") or ((it.get("path") or "").startswith("http://") or (it.get("path") or "").startswith("https://"))
            if it.get("auto_restart") and not is_http:
                # 崩溃计数：记录最近1分钟内的重启次数
                now_ts = time.time()
                restart_history = it.get("_restart_history", [])
                restart_history.append(now_ts)
                # 只保留最近60秒的记录
                restart_history = [t for t in restart_history if now_ts - t < 60]
                it["_restart_history"] = restart_history
                # 1分钟内重启超过5次，判定为崩溃循环，自动停止保活
                if len(restart_history) > 5:
                    it["auto_restart"] = False
                    it["_crash_loop"] = True
                    name = it.get("name", "")
                    self.status_var.set(self._lang["msg_crash_loop_notify"] % (name, len(restart_history)))
                    self._send_notification_if_enabled("crash_loop", name, len(restart_history))
                    self._save_config()  # 持久化崩溃循环状态，防止程序重启后保护失效
                    continue
                it.pop("_popen", None)
                logpath = self._make_log_path(it)
                p = self._start_single_script(it)
                if p:
                    it["pid"] = p.pid
                    it["pid_set_at"] = time.time()
                    it["_popen"] = p
                    if logpath:
                        it["_log_path"] = logpath
                    # 记录执行开始（保活重启）
                    item_id = it.get("id")
                    if item_id:
                        self._record_execution_start(item_id)
                    restarted = it.get("name", "")
            else:
                popen = it.pop("_popen", None)
                rc = popen.poll() if popen else None
                duration = time.time() - (it.get("pid_set_at") or time.time())
                name = it.get("name", "") or "未命名"
                # 记录执行历史
                item_id = it.get("id")
                if item_id:
                    self._record_execution_stop(item_id, rc, duration)
                msg = "「%s」运行约 %.1f 秒后退出，退出码 %s" % (name, duration, rc if rc is not None else "?")
                if it.get("_log_path") and os.path.isfile(it.get("_log_path") or ""):
                    msg += "；输出已保存到 %s" % it["_log_path"]
                self.status_var.set(msg)
                it["pid"] = None
                it.pop("pid_set_at", None)

        # 运行中接管：对「无 pid 或 pid 已失效」的条目，按脚本路径匹配系统正在运行的进程并接管
        adopted = 0
        if HAS_PSUTIL:
            # 使用缓存结果（由后台线程刷新），避免每次 refresh 都扫描全系统进程导致卡顿
            path_to_pids = {k: list(v) for k, v in self._adopt_cache.items()}  # 深拷贝，避免 pop 修改缓存
            # 如果缓存超过 8 秒且没有正在进行的扫描，启动后台扫描
            if time.time() - self._adopt_cache_time > 8 and not self._adopt_scanning:
                self._start_adopt_scan()
            for it in self.items:
                if it.get("pid") and is_process_running(it["pid"]):
                    continue
                path = (it.get("path") or "").strip()
                if not path:
                    continue
                try:
                    np = os.path.normcase(os.path.abspath(path))
                except Exception:
                    continue
                if np not in path_to_pids or not path_to_pids[np]:
                    continue
                pid = path_to_pids[np].pop(0)
                it["pid"] = pid
                it["pid_set_at"] = time.time()
                adopted += 1

        self._sync_tree()
        if and_set_status:
            if restarted:
                self.status_var.set(self._lang["status_auto_restarted"] % restarted)
            elif adopted > 0:
                self.status_var.set(self._lang["status_refreshed_adopted"] % adopted)
            else:
                self.status_var.set(self._lang["status_refreshed"])
        # 保活重启或崩溃循环检测时，持久化状态变更（不频繁，仅在状态实际变化时触发）
        if restarted:
            self._save_config()
        # API 状态单独缓存，不每次刷新都全量扫进程
        now = time.time()
        if now - getattr(self, "_api_status_last_scan", 0) >= 8:
            self._update_api_status_label()

    def _api_script_available(self) -> bool:
        path = get_api_server_script_path()
        return bool(path and os.path.isfile(path))

    def _get_api_server_pids_for_ui(self, force_scan: bool = False) -> list:
        """获取 API 进程 PID：优先本工具启动的实例，全量扫描最多每 8 秒一次（避免刷新卡顿）。"""
        if self._api_server_popen is not None:
            try:
                pid = self._api_server_popen.pid
                if pid and is_process_running(pid):
                    return [pid]
            except Exception:
                pass
            self._api_server_popen = None
        now = time.time()
        if not force_scan and self._api_status_cached_pids and (now - self._api_status_last_scan) < 8:
            return [p for p in self._api_status_cached_pids if is_process_running(p)]
        self._api_status_cached_pids = find_api_server_pids(None)
        self._api_status_last_scan = now
        return list(self._api_status_cached_pids)

    def _update_api_status_label(self, force_scan: bool = False):
        if not getattr(self, "label_api_status", None):
            return
        api_ok = self._api_script_available()
        if getattr(self, "btn_start_api", None):
            state = tk.NORMAL if api_ok else tk.DISABLED
            self.btn_start_api.config(state=state)
        if getattr(self, "btn_restart_api", None):
            state = tk.NORMAL if api_ok else tk.DISABLED
            self.btn_restart_api.config(state=state)
        if not api_ok:
            self.label_api_status.config(text=self._lang["status_api_missing"], foreground="orange")
            self._wrap_toolbar_row(self.label_api_status.master)
            return
        if self._api_server_popen is not None:
            try:
                if not is_process_running(self._api_server_popen.pid):
                    self._api_server_popen = None
            except Exception:
                self._api_server_popen = None
        pids = self._get_api_server_pids_for_ui(force_scan=force_scan)
        if pids:
            pid_s = str(pids[0]) if len(pids) == 1 else ",".join(str(x) for x in pids)
            self.label_api_status.config(text=self._lang["status_api_running"] % pid_s, foreground="green")
        else:
            self.label_api_status.config(text=self._lang["status_api_stopped"], foreground="gray")
        self._wrap_toolbar_row(self.label_api_status.master)

    def _apply_api_start_result(self, p, set_status: bool = True):
        self._api_server_popen = p
        call = self._persist_api_call_config()
        if set_status:
            self._set_api_call_status_hint(call)
        self._update_api_status_label(force_scan=True)

    def _do_launch_api_on_start(self):
        """启动本工具时自动启动 API（若已勾选且未在运行）。"""
        if not self._api_config.get("launch_on_app_start"):
            return
        if find_api_server_pids(self._api_server_popen):
            self._persist_api_call_config()
            self._update_api_status_label()
            return
        path = get_api_server_script_path()
        if not path or not os.path.isfile(path):
            self.status_var.set(self._lang["status_api_missing"])
            return

        def run_in_thread():
            p = start_api_server()
            if p and p.pid:
                self.root.after(0, lambda: self._apply_api_start_result(p))

        threading.Thread(target=run_in_thread, daemon=True).start()

    def _on_check_api_environment(self):
        executable = default_python_executable()
        chinese = self._current_language == "zh-CN"
        title = self._lang["btn_check_api_env"]

        def offer_download(message):
            prompt = "打开 Python 官方下载页吗？" if chinese else "Open the official Python download page?"
            if messagebox.askyesno(title, f"{message}\n\n{prompt}", parent=self.root):
                webbrowser.open("https://www.python.org/downloads/")

        if not executable:
            offer_download("未找到系统 Python。" if chinese else "System Python was not found.")
            return

        results = queue.Queue()
        self.btn_check_api_env.state(["disabled"])

        def check():
            try:
                results.put((probe_api_python(executable), None))
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                results.put((None, exc))

        def show_result():
            try:
                info, error = results.get_nowait()
            except queue.Empty:
                self.root.after(100, show_result)
                return
            self.btn_check_api_env.state(["!disabled"])
            if error is not None:
                message = "无法运行检测到的 Python" if chinese else "The detected Python could not run"
                offer_download(f"{message}\n{executable}\n{error}")
                return
            message = f"Python {info['version']}\n{info['executable']}"
            api_path = get_api_server_script_path()
            if not api_path:
                message += "\n\n" + ("缺少 API 服务脚本。" if chinese else "API server script is missing.")
            if info["missing"]:
                missing = ", ".join(info["missing"])
                label = "缺少模块" if chinese else "Missing modules"
                prefix = "& " if sys.platform == "win32" else ""
                command = f'{prefix}"{info["executable"]}" -m pip install {" ".join(info["missing"])}'
                messagebox.showwarning(title, f"{message}\n\n{label}: {missing}\n\n{command}", parent=self.root)
            elif not api_path:
                messagebox.showwarning(title, message, parent=self.root)
            else:
                modules = "已找到模块：fastapi、uvicorn" if chinese else "Modules found: fastapi, uvicorn"
                messagebox.showinfo(title, f"{message}\n\n{modules}", parent=self.root)

        threading.Thread(target=check, daemon=True).start()
        self.root.after(100, show_result)

    def _on_start_api(self):
        path = get_api_server_script_path()
        if not path or not os.path.isfile(path):
            messagebox.showerror(self._lang["msg_error"], self._lang["msg_api_not_found"] % (path or API_SERVER_SCRIPT_NAME))
            return
        pids = find_api_server_pids(self._api_server_popen)
        if pids:
            call = self._persist_api_call_config()
            self._set_api_call_status_hint(call)
            self._update_api_status_label(force_scan=True)
            return
        p = start_api_server()
        if not p or not p.pid:
            messagebox.showerror(self._lang["msg_error"], self._lang["msg_api_start_failed"])
            self._update_api_status_label(force_scan=True)
            return
        self._api_server_popen = p
        call = self._persist_api_call_config()
        self._set_api_call_status_hint(call)
        self._update_api_status_label(force_scan=True)

    def _on_stop_api(self):
        pids = find_api_server_pids(self._api_server_popen)
        if not pids:
            self.status_var.set(self._lang["status_api_stopped"])
            self._api_server_popen = None
            self._api_status_cached_pids = []
            self._update_api_status_label(force_scan=True)
            return
        n = stop_api_server_processes(self._api_server_popen)
        self._api_server_popen = None
        self._api_status_cached_pids = []
        if n <= 0 and find_api_server_pids():
            messagebox.showerror(self._lang["msg_error"], self._lang["msg_api_stop_failed"])
            return
        self.status_var.set(self._lang["msg_api_stopped"])
        self._update_api_status_label(force_scan=True)

    def _on_restart_api(self):
        path = get_api_server_script_path()
        if not path or not os.path.isfile(path):
            messagebox.showerror(self._lang["msg_error"], self._lang["msg_api_not_found"] % (path or API_SERVER_SCRIPT_NAME))
            return
        stop_api_server_processes(self._api_server_popen)
        self._api_server_popen = None
        time.sleep(0.5)
        p = start_api_server()
        if not p or not p.pid:
            messagebox.showerror(self._lang["msg_error"], self._lang["msg_api_start_failed"])
            self._update_api_status_label(force_scan=True)
            return
        self._api_server_popen = p
        call = self._persist_api_call_config()
        self._set_api_call_status_hint(call)
        self._update_api_status_label(force_scan=True)

    def _find_existing_api_http_task(self, method: str, url: str, group_names: tuple):
        target = _http_endpoint_key(url, method)
        target_parsed = urllib.parse.urlparse(url)
        matches = []
        for it in self.items:
            if not _is_http_task_item(it):
                continue
            if _http_endpoint_key(it.get("path"), it.get("http_method")) != target:
                continue
            matches.append(it)
        for it in matches:
            if (it.get("group") or "") in group_names:
                return it
        for it in matches:
            if (it.get("path") or "").rstrip("/") == (url or "").rstrip("/"):
                return it
        for it in matches:
            p = urllib.parse.urlparse(it.get("path") or "")
            if p.hostname == target_parsed.hostname and p.port == target_parsed.port:
                return it
        for it in matches:
            p = urllib.parse.urlparse(it.get("path") or "")
            if p.port == target_parsed.port:
                return it
        path_q = (target[1], target[2])
        for it in self.items:
            if not _is_http_task_item(it):
                continue
            if (it.get("group") or "") not in group_names:
                continue
            k = _http_endpoint_key(it.get("path"), it.get("http_method"))
            if (k[1], k[2]) == path_q:
                return it
        return None

    def _make_local_api_http_item(self, item_id: int, name: str, url: str, method: str, headers: str, group: str) -> dict:
        return {
            "id": item_id,
            "name": name,
            "path": url,
            "work_dir": "",
            "group": group,
            "script_type": "http_get",
            "interpreter": "",
            "is_python": False,
            "python_exe": "",
            "auto_restart": False,
            "launch_on_app_start": False,
            "log_output": False,
            "schedule_enabled": False,
            "schedule_action": "start",
            "schedule_type": "daily",
            "schedule_time": "09:00",
            "schedule_interval": 5,
            "schedule_weekday": 0,
            "schedule_day": 1,
            "schedule_quarter": "q1",
            "schedule_month": 1,
            "depends_on": "",
            "delay_after_dep": 0,
            "args": "",
            "env_vars": "",
            "http_method": method,
            "http_headers": headers,
            "startup_timeout": 0,
            "schedule_valid_from": "",
            "schedule_valid_until": "",
            "schedule_max_runs": 0,
            "schedule_pre_delay": 0,
            "schedule_once_date": "",
            "pid": None,
        }

    def _on_load_api_call_tasks(self):
        """一键写入/更新「本机API」分组下的 HTTP 调用任务，不重复添加相同 URL+方法。"""
        call = resolve_api_call_config(self._current_lan_ip())
        self._api_config["call"] = call
        self._save_api_config()
        base = (call.get("base_url") or "").rstrip("/")
        if not base:
            messagebox.showerror(self._lang["msg_error"], self._lang["msg_api_start_failed"])
            return
        group = self._lang.get("group_local_api", "本机API")
        group_names = tuple(dict.fromkeys(LOCAL_API_GROUP_NAMES + (group,)))
        auth_on = bool(call.get("auth_enabled"))
        headers = format_api_key_headers(call.get("api_key") or "", auth_on)
        added = 0
        updated = 0
        for name_key, method, path in API_CALL_TASK_PRESETS:
            url = base + path
            method = normalize_http_method(method)
            name = self._lang.get(name_key, path)
            existing = self._find_existing_api_http_task(method, url, group_names)
            if existing:
                existing["path"] = url
                existing["http_method"] = method
                existing["http_headers"] = headers
                existing["script_type"] = "http_get"
                existing["auto_restart"] = False
                existing["group"] = group
                updated += 1
            else:
                nid = next_item_id(self.items)
                self.items.append(self._make_local_api_http_item(nid, name, url, method, headers, group))
                self._next_id = max(getattr(self, "_next_id", 1), nid + 1)
                added += 1
        self._save_config()
        self._sync_tree()
        messagebox.showinfo(self._lang["msg_info"], self._lang["msg_api_calls_loaded"] % (added, updated))
        self._set_api_call_status_hint(call)

    def _check_config_changed(self):
        """检测脚本列表配置是否被外部修改（HTTP API / CLI），自动重载并保留运行状态。"""
        try:
            mtime = CONFIG_PATH.stat().st_mtime
        except Exception:
            return
        if mtime == self._config_mtime:
            return
        # 冷却：至少 2 秒内不重复重载（防止文件正在写入时重复触发）
        now = time.time()
        if now - self._config_reload_cooldown < 2:
            return
        self._config_reload_cooldown = now
        # 保存当前运行状态（pid、popen 等）
        running_map = {}  # id -> {pid, pid_set_at, _popen, _log_path, _restart_history, _startup_confirmed, _crash_loop}
        for it in self.items:
            if it.get("pid"):
                running_map[it["id"]] = {
                    "pid": it["pid"],
                    "path": it.get("path"),
                    "pid_set_at": it.get("pid_set_at"),
                    "_popen": it.get("_popen"),
                    "_log_path": it.get("_log_path"),
                    "_restart_history": it.get("_restart_history", []),
                    "_startup_confirmed": it.get("_startup_confirmed", False),
                    "_crash_loop": it.get("_crash_loop", False),
                }
        # 重新加载配置
        self._load_config()
        # 恢复运行状态
        for it in self.items:
            saved = running_map.get(it["id"])
            if saved and saved.get('path') == it.get('path'):
                it["pid"] = saved["pid"]
                it["pid_set_at"] = saved["pid_set_at"]
                it["_popen"] = saved["_popen"]
                it["_log_path"] = saved["_log_path"]
                it["_restart_history"] = saved["_restart_history"]
                it["_startup_confirmed"] = saved["_startup_confirmed"]
                it["_crash_loop"] = saved["_crash_loop"]

    def _start_adopt_scan(self):
        """后台扫描系统进程，更新接管缓存，避免主线程卡顿。"""
        if self._adopt_scanning:
            return
        self._adopt_scanning = True

        def _scan():
            try:
                path_to_pids = {}
                for r in scan_running_script_processes():
                    sp = (r.get("script_path") or "").strip()
                    if not sp:
                        continue
                    try:
                        np = os.path.normcase(os.path.abspath(sp))
                    except Exception:
                        continue
                    path_to_pids.setdefault(np, []).append(r["pid"])
                for r in scan_running_exe_processes():
                    sp = (r.get("script_path") or "").strip()
                    if not sp:
                        continue
                    try:
                        np = os.path.normcase(os.path.abspath(sp))
                    except Exception:
                        continue
                    path_to_pids.setdefault(np, []).append(r["pid"])
                # 回到主线程更新缓存并触发 UI 刷新
                self.root.after(0, lambda: self._on_adopt_scan_done(path_to_pids))
            except Exception:
                self._adopt_scanning = False

        threading.Thread(target=_scan, daemon=True).start()

    def _on_adopt_scan_done(self, path_to_pids: dict):
        """后台进程扫描完成回调（主线程）。"""
        self._adopt_cache = path_to_pids
        self._adopt_cache_time = time.time()
        self._adopt_scanning = False
        self._sync_tree()

    def _start_refresh_loop(self):
        if self._refresh_paused:
            return  # 暂停时不刷新、不续订，等点「恢复刷新」再跑
        self._refresh_status(and_set_status=False)
        self.root.after(self._refresh_interval_ms, self._start_refresh_loop)

    def _init_schedule_last_run(self):
        """启动时为所有启用定时任务的项初始化 last_run 为当前时间戳，
        防止应用重启后因 last_run 丢失而补执行旧窗口内的任务。"""
        now_ts = time.time()
        for it in self.items:
            if it.get("schedule_enabled") and it.get("id"):
                self._schedule_last_run[it["id"]] = now_ts

    def _start_schedule_check_loop(self):
        """定时检查并触发定时任务（每10秒检查一次，提高精度）。"""
        # 注意：定时任务检查不受 _refresh_paused 影响，暂停刷新时仍需执行定时任务
        try:
            self._check_schedules()
        except Exception as e:
            import traceback
            try:
                log_path = CONFIG_PATH.parent / "schedule_debug.log"
                with open(str(log_path), "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] _check_schedules EXCEPTION:\n{traceback.format_exc()}\n")
            except Exception:
                pass
        self.root.after(10000, self._start_schedule_check_loop)  # 每10秒检查一次，提高定时精度

    def _get_next_schedule_run(self, it: dict) -> Optional[str]:
        """计算下次执行时间描述，用于列表预览。返回如 '14:35'、'明天 09:00'、'03-20 09:00' 或 None。"""
        try:
            now = datetime.now()
            st = it.get("schedule_type", "daily")
            type_map = {"daily": "每天", "hourly": "每小时", "minutely": "每分钟", "weekly": "每周", "monthly": "每月", "every_hours": "每N小时", "quarterly": "每季度", "halfyearly": "每半年", "yearly": "每年", "once": "单次执行", "weekday": "工作日", "weekend": "每周末"}
            st_cn = type_map.get(st, st)
            tm = it.get("schedule_time", "09:00")
            interval = it.get("schedule_interval", 5)
            weekday = it.get("schedule_weekday", 0)
            day = it.get("schedule_day", 1)
            try:
                hour, minute = map(int, tm.split(":"))
            except (ValueError, TypeError):
                hour, minute = 9, 0
            if st_cn == "单次执行":
                once_date_str = it.get("schedule_once_date", "").strip()
                if once_date_str:
                    try:
                        from datetime import date as date_cls
                        once_date = date_cls.fromisoformat(once_date_str)
                    except (ValueError, TypeError):
                        once_date = now.date()
                else:
                    once_date = now.date()
                t = datetime(once_date.year, once_date.month, once_date.day, hour, minute, 0)
                if t <= now:
                    return self._lang["hint_expired"]
                return t.strftime("%Y-%m-%d %H:%M")
            if st_cn == "每天":
                t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if t <= now:
                    t += timedelta(days=1)
                if t.date() == now.date():
                    return t.strftime("%H:%M")
                return self._lang["schedule_next_tomorrow"] + " " + t.strftime("%H:%M")
            if st_cn == "工作日":
                t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if t <= now:
                    t += timedelta(days=1)
                # 跳过周末(5=周六,6=周日)
                while t.weekday() >= 5:
                    t += timedelta(days=1)
                if t.date() == now.date():
                    return t.strftime("%H:%M")
                weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                wd_name = weekday_names[t.weekday()] if self._current_language == "zh-CN" else t.strftime("%a")
                return f"{wd_name} {t.strftime('%H:%M')}"
            if st_cn == "每小时":
                t = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
                return t.strftime("%H:%M")
            if st_cn == "每分钟":
                try:
                    n = max(1, int(interval))
                    total = now.hour * 60 + now.minute
                    next_total = ((total // n) + 1) * n
                    if next_total >= 24 * 60:
                        next_total = 0
                    h, m = next_total // 60, next_total % 60
                    return f"{h:02d}:{m:02d}"
                except (ValueError, TypeError):
                    return None
            if st_cn == "每周":
                wd = int(weekday) if isinstance(weekday, (int, str)) and str(weekday).isdigit() else 0
                t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                days_ahead = (wd - now.weekday() + 7) % 7
                if days_ahead == 0 and t <= now:
                    days_ahead = 7
                t += timedelta(days=days_ahead)
                return t.strftime("%m-%d %H:%M")
            if st_cn == "每月":
                try:
                    d = int(day)
                except (TypeError, ValueError):
                    d = 1
                if d == 31:
                    _, last = calendar.monthrange(now.year, now.month)
                    d = last
                t = now.replace(day=min(d, 28), hour=hour, minute=minute, second=0, microsecond=0)
                try:
                    t = t.replace(day=min(d, calendar.monthrange(t.year, t.month)[1]))
                except (ValueError, TypeError):
                    pass
                if t <= now:
                    if now.month == 12:
                        t = t.replace(year=now.year + 1, month=1)
                    else:
                        t = t.replace(month=now.month + 1)
                    try:
                        _, last = calendar.monthrange(t.year, t.month)
                        t = t.replace(day=min(d, last))
                    except (ValueError, TypeError):
                        pass
                return t.strftime("%m-%d %H:%M")
            if st_cn == "每N小时":
                try:
                    nh = max(1, int(interval))
                    last = self._schedule_last_run.get(it.get("id"), 0)
                    if last > 0:
                        from_dt = datetime.fromtimestamp(last)
                        t = from_dt + timedelta(hours=nh)
                    else:
                        t = now + timedelta(hours=nh)
                    return t.strftime("%m-%d %H:%M") if (t.date() != now.date()) else t.strftime("%H:%M")
                except (ValueError, TypeError):
                    return None
            # 每季度：1/4/7/10月的指定几号
            if st_cn == "每季度":
                try:
                    d = int(day)
                except (TypeError, ValueError):
                    d = 1
                q_map = {"q1": 1, "q2": 4, "q3": 7, "q4": 10}
                q_val = it.get("schedule_quarter", "q1")
                target_month = q_map.get(q_val, 1)
                if d == 31:
                    _, last = calendar.monthrange(now.year, target_month)
                    d = last
                t = now.replace(month=target_month, day=min(d, 28), hour=hour, minute=minute, second=0, microsecond=0)
                try:
                    t = t.replace(day=min(d, calendar.monthrange(t.year, t.month)[1]))
                except (ValueError, TypeError):
                    pass
                if t <= now:
                    t = now.replace(year=now.year + 1, month=target_month, day=min(d, 28), hour=hour, minute=minute, second=0, microsecond=0)
                    try:
                        t = t.replace(day=min(d, calendar.monthrange(t.year, t.month)[1]))
                    except (ValueError, TypeError):
                        pass
                return t.strftime("%Y-%m-%d %H:%M")
            # 每年：指定月份的指定几号
            if st_cn == "每年":
                try:
                    d = int(day)
                except (TypeError, ValueError):
                    d = 1
                try:
                    m = max(1, min(12, int(it.get("schedule_month", 1))))
                except (TypeError, ValueError):
                    m = 1
                if d == 31:
                    _, last = calendar.monthrange(now.year, m)
                    d = last
                t = now.replace(month=m, day=min(d, 28), hour=hour, minute=minute, second=0, microsecond=0)
                try:
                    t = t.replace(day=min(d, calendar.monthrange(t.year, t.month)[1]))
                except (ValueError, TypeError):
                    pass
                if t <= now:
                    t = now.replace(year=now.year + 1, month=m, day=min(d, 28), hour=hour, minute=minute, second=0, microsecond=0)
                    try:
                        t = t.replace(day=min(d, calendar.monthrange(t.year, t.month)[1]))
                    except (ValueError, TypeError):
                        pass
                return t.strftime("%Y-%m-%d %H:%M")
            # 每周末：周六、周日指定时间
            if st_cn == "每周末":
                t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                # 找到下一个周末（周六或周日）
                days_to_weekend = (5 - now.weekday()) % 7  # 到周六的天数
                if days_to_weekend == 0 and now.weekday() == 5 and t <= now:
                    # 今天是周六但时间已过，跳到周日
                    days_to_weekend = 1
                elif days_to_weekend == 0 and now.weekday() == 6 and t <= now:
                    # 今天是周日但时间已过，跳到下周六
                    days_to_weekend = 6
                elif now.weekday() < 5:
                    # 工作日，跳到周六
                    days_to_weekend = 5 - now.weekday()
                t += timedelta(days=days_to_weekend)
                # 如果是周六且时间已过，再试周日
                if t.weekday() == 5 and t <= now:
                    t += timedelta(days=1)
                # 如果是周日且时间已过，跳到下周六
                if t.weekday() == 6 and t <= now:
                    t += timedelta(days=6)
                return t.strftime("%m-%d %H:%M")
            # 每半年：指定月份和 base_month+6 的指定几号
            if st_cn == "每半年":
                try:
                    d = int(day)
                except (TypeError, ValueError):
                    d = 1
                try:
                    base_m = max(1, min(12, int(it.get("schedule_month", 1))))
                except (TypeError, ValueError):
                    base_m = 1
                half_months = [base_m, base_m + 6 if base_m + 6 <= 12 else base_m - 6]
                # 找到最近的一次
                candidates = []
                for y in [now.year, now.year + 1]:
                    for m in half_months:
                        td = d
                        if d == 31:
                            _, last = calendar.monthrange(y, m)
                            td = last
                        else:
                            td = min(d, calendar.monthrange(y, m)[1])
                        t = datetime(y, m, td, hour, minute, 0)
                        if t > now:
                            candidates.append(t)
                if candidates:
                    t = min(candidates)
                    return t.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return None
        return None

    def _check_schedules(self):
        """检查所有启用了定时任务的项，如果到时间则启动。"""
        now = datetime.now()
        _debug_lines = [f"=== _check_schedules: paused={self._schedule_paused}, refresh_paused={self._refresh_paused}, now={now.strftime('%H:%M:%S')} ==="]
        if self._schedule_paused:
            _debug_lines.append("  [ABORT] schedule_paused=True")
            self._write_schedule_debug(_debug_lines, now)
            return
        now_date = now.date()
        for it in self.items:
            if not it.get("schedule_enabled"):
                continue
            _debug_lines.append(f"{it.get('name','?')}: type={it.get('schedule_type','?')}, time={it.get('schedule_time','?')}")
            # 有效期检查
            valid_from = it.get("schedule_valid_from", "").strip()
            valid_until = it.get("schedule_valid_until", "").strip()
            if valid_from:
                try:
                    from datetime import date as date_cls
                    vf = date_cls.fromisoformat(valid_from)
                    if now_date < vf:
                        continue
                except (ValueError, TypeError):
                    pass
            if valid_until:
                try:
                    from datetime import date as date_cls
                    vu = date_cls.fromisoformat(valid_until)
                    if now_date > vu:
                        continue
                except (ValueError, TypeError):
                    pass
            # 最大执行次数检查
            max_runs = it.get("schedule_max_runs", 0)
            if max_runs > 0:
                item_id = it.get("id")
                history = self._execution_history.get(item_id, [])
                # 只统计定时任务触发的执行次数（非手动启动）
                schedule_runs = sum(1 for h in history if h.get("trigger") == "schedule" and h.get("status") != "running")
                if schedule_runs >= max_runs:
                    continue
            schedule_action = it.get("schedule_action", "start")
            action_map = {"start": "启动", "restart": "重启任务", "stop": "停止"}
            action_cn = action_map.get(schedule_action, schedule_action if schedule_action in action_map.values() else "启动")
            
            # 如果行为是"启动"且已经在运行，跳过（重启、停止不受此限制；系统关机/重启任务无 pid）
            # HTTP 请求类型（curl）执行完即退出，pid 可能残留但进程已结束，需要先清理再判断
            _skip_running = False
            if (schedule_action == "start" or action_cn == "启动") and it.get("pid"):
                if not is_process_running(it["pid"]):
                    # pid 已失效但未被 _refresh_status 清理（如暂停刷新时），主动清理
                    it["pid"] = None
                    it.pop("pid_set_at", None)
                    it.pop("_popen", None)
                else:
                    _skip_running = True
                    _debug_lines.append(f"  [SKIP] 跳过已运行: {it.get('name','?')}, pid={it.get('pid')}")
            if _skip_running:
                continue
            item_id = it.get("id")
            schedule_type = it.get("schedule_type", "daily")
            schedule_time = it.get("schedule_time", "09:00")
            schedule_interval = it.get("schedule_interval", 5)
            schedule_weekday = it.get("schedule_weekday", 0)
            last_run = self._schedule_last_run.get(item_id, 0)
            should_run = False
            # 解析多个时间点（逗号分隔，如 "09:00,15:00,21:00"）
            schedule_times = [t.strip() for t in schedule_time.split(",") if t.strip()]
            try:
                # 统一转换为中文类型名
                type_map = {"daily": "每天", "hourly": "每小时", "minutely": "每分钟", "weekly": "每周", "monthly": "每月", "every_hours": "每N小时", "quarterly": "每季度", "halfyearly": "每半年", "yearly": "每年", "once": "单次执行", "weekday": "工作日", "weekend": "每周末"}
                schedule_type = type_map.get(schedule_type, schedule_type)
                # DEBUG: 工作日类型检查日志
                if schedule_type == "工作日":
                    _debug_lines.append(f"  [工作日] {it.get('name','?')}: weekday={now.weekday()}, times={schedule_times}, action={schedule_action}, last_run={last_run}")
                try:
                    raw_day = int(it.get("schedule_day", 1))
                    schedule_day = 31 if raw_day == 31 else max(1, min(30, raw_day))
                except (TypeError, ValueError):
                    schedule_day = 1
                
                if schedule_type == "单次执行":
                    # 单次执行：指定日期+时间执行一次（30分钟窗口）
                    once_date_str = it.get("schedule_once_date", "").strip()
                    if once_date_str:
                        try:
                            from datetime import date as date_cls
                            once_date = date_cls.fromisoformat(once_date_str)
                            target_date = once_date
                        except (ValueError, TypeError):
                            target_date = now.date()
                    else:
                        target_date = now.date()
                    if now.date() == target_date:
                        for st in schedule_times:
                            try:
                                hour, minute = map(int, st.split(":"))
                                target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                                if target_time <= now < target_time + timedelta(minutes=30):
                                    if last_run < target_time.timestamp():
                                        should_run = True
                                        break
                            except (ValueError, AttributeError):
                                continue
                elif schedule_type == "每天":
                    # 每天指定时间（支持多时间点，30分钟窗口）
                    for st in schedule_times:
                        try:
                            hour, minute = map(int, st.split(":"))
                            target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                            if target_time <= now < target_time + timedelta(minutes=30):
                                if last_run < target_time.timestamp():
                                    should_run = True
                                    break
                        except (ValueError, AttributeError):
                            continue
                elif schedule_type == "工作日":
                    # 工作日（周一~周五）指定时间（支持多时间点，30分钟窗口）
                    if now.weekday() < 5:  # 0-4 = 周一至周五
                        for st in schedule_times:
                            try:
                                hour, minute = map(int, st.split(":"))
                                target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                                if target_time <= now < target_time + timedelta(minutes=30):
                                    if last_run < target_time.timestamp():
                                        should_run = True
                                        break
                            except (ValueError, AttributeError):
                                continue
                    else:
                        # 周末不执行工作日任务，但更新 last_run 防止周一重复执行
                        pass
                elif schedule_type == "每小时":
                    # 每小时的第0分钟（允许0-2分钟窗口）
                    if 0 <= now.minute < 2:
                        # 检查是否这小时已经执行过
                        hour_start = now.replace(minute=0, second=0, microsecond=0)
                        if last_run < hour_start.timestamp():
                            should_run = True
                elif schedule_type == "每分钟":
                    # 每N分钟
                    try:
                        interval = int(schedule_interval)
                        if interval > 0:
                            minutes_since_start = (now.hour * 60 + now.minute) % interval
                            # 允许0-1分钟的窗口
                            if minutes_since_start == 0 and now.second < 60:
                                # 检查是否这分钟已经执行过
                                minute_start = now.replace(second=0, microsecond=0)
                                if last_run < minute_start.timestamp():
                                    should_run = True
                    except (ValueError, TypeError):
                        pass
                elif schedule_type == "每周":
                    # 每周指定星期几的指定时间（支持多时间点，30分钟窗口）
                    weekday_map = {"周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6, 0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6}
                    if isinstance(schedule_weekday, str) and schedule_weekday.isdigit():
                        target_weekday = int(schedule_weekday)
                    else:
                        target_weekday = weekday_map.get(schedule_weekday, 0)
                    if now.weekday() == target_weekday:
                        for st in schedule_times:
                            try:
                                hour, minute = map(int, st.split(":"))
                                target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                                if target_time <= now < target_time + timedelta(minutes=30):
                                    if last_run < target_time.timestamp():
                                        should_run = True
                                        break
                            except (ValueError, AttributeError):
                                continue
                elif schedule_type == "每月":
                    # 每月几号（1-30）或月末（31）的指定时间（支持多时间点，30分钟窗口）
                    try:
                        target_day = int(schedule_day)
                    except (TypeError, ValueError):
                        target_day = 1
                    if target_day == 31:
                        _, last_day = calendar.monthrange(now.year, now.month)
                        target_day = last_day
                    if now.day == target_day:
                        for st in schedule_times:
                            try:
                                hour, minute = map(int, st.split(":"))
                                target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                                if target_time <= now < target_time + timedelta(minutes=30):
                                    if last_run < target_time.timestamp():
                                        should_run = True
                                        break
                            except (ValueError, AttributeError):
                                continue
                elif schedule_type == "每N小时":
                    # 每N小时循环执行（schedule_interval 为小时数）
                    try:
                        interval_h = max(1, int(schedule_interval))
                        if last_run == 0:
                            should_run = True
                        else:
                            if now.timestamp() - last_run >= interval_h * 3600:
                                should_run = True
                    except (ValueError, TypeError):
                        pass
                elif schedule_type == "每季度":
                    # 每季度：1/4/7/10月的指定几号指定时间（支持多时间点，30分钟窗口）
                    try:
                        target_day = int(schedule_day)
                    except (TypeError, ValueError):
                        target_day = 1
                    q_map = {"q1": 1, "q2": 4, "q3": 7, "q4": 10}
                    q_val = it.get("schedule_quarter", "q1")
                    target_month = q_map.get(q_val, 1)
                    if target_day == 31:
                        _, last_day = calendar.monthrange(now.year, target_month)
                        target_day = last_day
                    if now.month == target_month and now.day == target_day:
                        for st in schedule_times:
                            try:
                                hour, minute = map(int, st.split(":"))
                                target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                                if target_time <= now < target_time + timedelta(minutes=30):
                                    if last_run < target_time.timestamp():
                                        should_run = True
                                        break
                            except (ValueError, AttributeError):
                                continue
                elif schedule_type == "每年":
                    # 每年：指定月份的指定几号指定时间（支持多时间点，30分钟窗口）
                    try:
                        target_day = int(schedule_day)
                    except (TypeError, ValueError):
                        target_day = 1
                    try:
                        target_month = max(1, min(12, int(it.get("schedule_month", 1))))
                    except (TypeError, ValueError):
                        target_month = 1
                    if target_day == 31:
                        _, last_day = calendar.monthrange(now.year, target_month)
                        target_day = last_day
                    if now.month == target_month and now.day == target_day:
                        for st in schedule_times:
                            try:
                                hour, minute = map(int, st.split(":"))
                                target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                                if target_time <= now < target_time + timedelta(minutes=30):
                                    if last_run < target_time.timestamp():
                                        should_run = True
                                        break
                            except (ValueError, AttributeError):
                                continue
                elif schedule_type == "每周末":
                    # 每周末（周六、周日）指定时间（支持多时间点，30分钟窗口）
                    if now.weekday() >= 5:  # 5=周六, 6=周日
                        for st in schedule_times:
                            try:
                                hour, minute = map(int, st.split(":"))
                                target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                                if target_time <= now < target_time + timedelta(minutes=30):
                                    if last_run < target_time.timestamp():
                                        should_run = True
                                        break
                            except (ValueError, AttributeError):
                                continue
                elif schedule_type == "每半年":
                    # 每半年：指定月份的指定几号指定时间（支持多时间点，30分钟窗口）
                    # 默认半年为1月和7月
                    try:
                        target_day = int(schedule_day)
                    except (TypeError, ValueError):
                        target_day = 1
                    try:
                        base_month = max(1, min(12, int(it.get("schedule_month", 1))))
                    except (TypeError, ValueError):
                        base_month = 1
                    # 半年月份：base_month 和 base_month+6（如果超过12则取模）
                    half_months = [base_month, base_month + 6 if base_month + 6 <= 12 else base_month - 6]
                    if target_day == 31:
                        target_days = []
                        for m in half_months:
                            _, last_day = calendar.monthrange(now.year, m)
                            target_days.append(last_day)
                    else:
                        target_days = [target_day, target_day]
                    for idx, m in enumerate(half_months):
                        if now.month == m and now.day == target_days[idx]:
                            for st in schedule_times:
                                try:
                                    hour, minute = map(int, st.split(":"))
                                    target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                                    if target_time <= now < target_time + timedelta(minutes=30):
                                        if last_run < target_time.timestamp():
                                            should_run = True
                                            break
                                except (ValueError, AttributeError):
                                    continue
                            if should_run:
                                break
            except (ValueError, AttributeError) as e:
                _debug_lines.append(f"  [ERROR] {it.get('name','?')}: exception={e}")
                continue
            _debug_lines.append(f"  {it.get('name','?')}: type_cn={schedule_type}, should_run={should_run}, now={now.strftime('%H:%M:%S')}")
            if should_run:
                # 检查脚本路径有效性（stop 动作无需检查路径）
                schedule_action = it.get("schedule_action", "start")
                action_map = {"start": "启动", "restart": "重启任务", "stop": "停止"}
                action_cn = action_map.get(schedule_action, schedule_action if schedule_action in action_map.values() else "启动")
                if is_system_power_task(it):
                    pass
                elif schedule_action != "stop" and action_cn != "停止":
                    sp = (it.get("path") or "").strip()
                    if sp:
                        # URL 类型跳过文件存在性检查
                        is_url = sp.startswith("http://") or sp.startswith("https://")
                        # 进程名类型也跳过文件存在性检查
                        is_process = get_script_type(sp) == "process"
                        # 命令类型也跳过文件存在性检查
                        is_command = it.get("script_type") == "command"
                        if not is_url and not is_process and not is_command:
                            full_sp = resolve_script_file_path(it)
                            if not os.path.isfile(full_sp):
                                _debug_lines.append(f"  [SKIP] 脚本路径无效: {it.get('name','?')}, path={full_sp}")
                                name = it.get("name", self._lang["default_name"])
                                self.status_var.set(self._lang["status_schedule_path_invalid"] % name)
                                continue
                    else:
                        # 路径为空也视为无效
                        _debug_lines.append(f"  [SKIP] 脚本路径为空: {it.get('name','?')}")
                        name = it.get("name", self._lang["default_name"])
                        self.status_var.set(self._lang["status_schedule_path_invalid"] % name)
                        continue
                _debug_lines.append(f"  [EXEC] 触发执行: {it.get('name','?')}, action={schedule_action}")
                self._write_schedule_debug(_debug_lines, now, force=True)
                # 立即更新 last_run，防止并发检查导致重复触发
                self._schedule_last_run[item_id] = time.time()
                # 执行前延迟（用 root.after 非阻塞延迟，避免冻结 UI）
                pre_delay = it.get("schedule_pre_delay", 0)
                if isinstance(pre_delay, (int, float)) and pre_delay > 0:
                    delay_ms = int(min(pre_delay, 60) * 1000)
                    self.root.after(delay_ms, lambda it=it, sa=schedule_action, acn=action_cn, st=schedule_type: self._execute_schedule_action(it, sa, acn, st))
                    continue  # 延迟执行，跳过下方立即执行
                self._execute_schedule_action(it, schedule_action, action_cn, schedule_type)
        # 只在有启用定时的任务时写日志，且每分钟最多写一次
        if len(_debug_lines) > 1:
            _minute_key = now.strftime("%Y-%m-%d %H:%M")
            if not hasattr(self, '_debug_log_minute') or self._debug_log_minute != _minute_key:
                self._debug_log_minute = _minute_key
                self._write_schedule_debug(_debug_lines, now)

    def _execute_schedule_action(self, it, schedule_action, action_cn, schedule_type):
        """执行定时任务动作（启动/停止/重启），由 _check_schedules 或 root.after 调用。"""
        if is_system_power_task(it):
            if schedule_action in ("start", "启动", "shutdown", "reboot") or action_cn in ("启动", "关机", "重启系统"):
                self._run_system_power_task(it, trigger="schedule")
            return
        if schedule_action == "stop" or action_cn == "停止":
            self._cancel_task(it)
            # 定时停止：如果正在运行则停止
            sp = (it.get("path") or "").strip()
            stype = get_script_type(sp)
            if stype == "process":
                killed = stop_process_by_name(sp)
                it["pid"] = None
                it.pop("pid_set_at", None)
                it.pop("_popen", None)
                name = it.get("name", self._lang["default_name"])
                if schedule_type == "单次执行":
                    it["schedule_enabled"] = False
                self.status_var.set(self._lang["status_schedule_triggered_stopped"] % (f"{name} ({killed})"))
                self._sync_tree()
                self._refresh_status(and_set_status=False)
                return
            pid = it.get("pid")
            if pid and is_process_running(pid):
                pids = get_process_tree_pids(pid)
                for px in pids:
                    kill_process(px)
                it["pid"] = None
                it.pop("pid_set_at", None)
                it.pop("_popen", None)
            name = it.get("name", self._lang["default_name"])
            if schedule_type == "单次执行":
                it["schedule_enabled"] = False
            self.status_var.set(self._lang["status_schedule_triggered_stopped"] % name)
            self._sync_tree()
            self._refresh_status(and_set_status=False)
            return
        elif schedule_action == "restart" or action_cn in ("重启", "重启任务"):
            # 定时重启：先停止再启动
            self._cancel_task(it)
            sp = (it.get("path") or "").strip()
            stype = get_script_type(sp)
            if stype == "process":
                stop_process_by_name(sp)
                it["pid"] = None
                it.pop("pid_set_at", None)
                it.pop("_popen", None)
            else:
                pid = it.get("pid")
                if pid and is_process_running(pid):
                    pids = get_process_tree_pids(pid)
                    for px in pids:
                        kill_process(px)
                    it["pid"] = None
                    it.pop("pid_set_at", None)
                    it.pop("_popen", None)
        # 启动脚本（启动或重启）—— 用 after(500) 替代 sleep(0.5) 避免阻塞
        is_restart = schedule_action == "restart" or action_cn in ("重启", "重启任务")
        if is_restart:
            self.root.after(500, lambda: self._do_schedule_start(it, schedule_action, action_cn, schedule_type))
        else:
            self._do_schedule_start(it, schedule_action, action_cn, schedule_type)

    def _do_schedule_start(self, it, schedule_action, action_cn, schedule_type):
        """定时任务：实际启动脚本。"""
        p = self._start_single_script(it, trigger="schedule")
        if p or it.get('_queued'):
            if schedule_type == "单次执行":
                it["schedule_enabled"] = False
            name = it.get("name", self._lang["default_name"])
            if schedule_action == "restart" or action_cn in ("重启", "重启任务"):
                action_text = self._lang["schedule_action_restart"]
            else:
                action_text = self._lang["schedule_action_start"]
            self.status_var.set(self._lang["status_schedule_triggered"] % (name, action_text))
            self._sync_tree()
        else:
            if is_system_power_task(it):
                return
            # 启动失败：重置 last_run，允许下次定时窗口重新触发
            item_id = it.get("id")
            if item_id and item_id in self._schedule_last_run:
                self._schedule_last_run[item_id] = 0
            name = it.get("name", self._lang["default_name"])
            path = it.get("path", "")
            is_url = path.startswith("http://") or path.startswith("https://")
            if is_url:
                self.status_var.set(f"定时任务启动失败: {name}（HTTP 请求失败，请检查 curl 是否可用）")
            else:
                self.status_var.set(f"定时任务启动失败: {name}（路径无效或启动异常）")

    def _write_schedule_debug(self, lines, now, force=False):
        """将调度调试日志写入文件。force=True 时忽略频率限制。需在异常通知配置中启用。"""
        if not self._notification_config.get("schedule_debug_log", False):
            return
        try:
            log_path = CONFIG_PATH.parent / "schedule_debug.log"
            with open(str(log_path), "a", encoding="utf-8") as f:
                f.write(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}]\n")
                for line in lines:
                    f.write(f"  {line}\n")
        except Exception as e:
            # 写日志失败不应影响主逻辑，但至少打印到控制台
            try:
                print(f"[Schedule DEBUG write failed] {e}, log_path={CONFIG_PATH.parent / 'schedule_debug.log'}")
            except Exception:
                pass

    def _set_refresh_interval(self, interval_ms: int):
        """设置定时刷新间隔（毫秒），并更新状态栏。"""
        self._refresh_interval_ms = max(2000, min(30000, interval_ms))
        self.status_var.set(self._lang.get("status_refresh_interval_set", "刷新间隔已设为 %d 秒") % (self._refresh_interval_ms // 1000))

    def _on_toggle_refresh(self):
        """切换「暂停刷新」/「恢复刷新」；暂停时状态栏、保活、运行中接管都会停。"""
        if self._refresh_paused:
            self._refresh_paused = False
            self.btn_pause_refresh.config(text=self._lang["btn_pause_refresh"])
            self.status_var.set(self._lang["status_refresh_resumed"] % (self._refresh_interval_ms // 1000))
            self._refresh_status(and_set_status=False)
            self._start_refresh_loop()
        else:
            self._refresh_paused = True
            self.btn_pause_refresh.config(text=self._lang["btn_resume_refresh"])
            self.status_var.set(self._lang["status_refresh_paused"])

    def _on_menu_pause_refresh(self):
        """菜单: 暂停刷新"""
        if not self._refresh_paused:
            self._on_toggle_refresh()

    def _on_menu_resume_refresh(self):
        """菜单: 恢复刷新"""
        if self._refresh_paused:
            self._on_toggle_refresh()

    def _on_toggle_schedule_pause(self):
        """切换「暂停所有定时」/「恢复所有定时」。"""
        self._schedule_paused = not self._schedule_paused
        if getattr(self, "_settings_menu", None) and getattr(self, "_schedule_pause_menu_index", None) is not None:
            self._settings_menu.entryconfig(self._schedule_pause_menu_index, label=self._lang["menu_schedule_resume"] if self._schedule_paused else self._lang["menu_schedule_pause"])
        self.status_var.set(self._lang["status_schedule_paused"] if self._schedule_paused else self._lang["status_schedule_resumed"])

    def _format_window_title(self) -> str:
        return f"{self._lang['window_title']}  v{__version__}"

    def _on_about(self):
        messagebox.showinfo(
            self._lang.get("menu_about", "关于"),
            self._lang.get("msg_about", "Windows Batch Script Manager\nVersion: %s") % __version__,
            parent=self.root,
        )

    def _on_show_hotkeys(self):
        """显示快捷键说明。"""
        messagebox.showinfo(self._lang.get("menu_global_hotkeys", "快捷键说明"), self._lang.get("msg_hotkeys_info", ""), parent=self.root)

    def _do_launch_on_start_items(self):
        for it in self.items:
            if it.get("launch_on_app_start"):
                self._start_single_script(it, trigger="startup")
        self._sync_tree()

    def _apply_launch_on_start_results(self, results):
        """在主线程中应用「启动时自动启动」的结果并刷新界面。"""
        started_pids = []
        for it, p, logpath in results:
            it["pid"] = p.pid
            it["pid_set_at"] = time.time()
            it["_popen"] = p
            if logpath:
                it["_log_path"] = logpath
            if it.get("id"):
                self._record_execution_start(it["id"])
            started_pids.append(p.pid)
        self._sync_tree()
        if started_pids:
            self.root.after(1500, lambda pids=started_pids: self._check_quick_exit(*pids))
        # 约 8 秒后再尝试一次仍未启动的「自动启动」任务，减少因并发/资源导致部分失败
        if not getattr(self, "_launch_on_start_retry_scheduled", False):
            self._launch_on_start_retry_scheduled = True
            self.root.after(8000, self._do_launch_on_start_items)

    def _on_add_manual(self):
        """直接打开编辑对话框，支持手动输入路径/URL/进程名。"""
        try:
            self._do_add_manual()
        except Exception as e:
            import traceback
            traceback.print_exc()
            with open("debug_add_error.log", "a", encoding="utf-8") as f:
                f.write(f"[_on_add_manual ERROR] {e}\n")
                traceback.print_exc(file=f)
            messagebox.showerror("Error", str(e), parent=self.root)

    def _do_add_manual(self):
        """直接打开编辑对话框，支持手动输入路径/URL/进程名。"""
        initial = {
            "name": "",
            "path": "",
            "work_dir": "",
            "group": "",
            "interpreter": "",
            "is_python": False,
            "python_exe": "",
            "auto_restart": False,
            "log_output": False,
        }
        # 对话框打开期间暂停主窗口刷新，避免扫描进程卡顿输入
        was_paused = self._refresh_paused
        if not was_paused:
            self._refresh_paused = True
        d = AddEditDialog(self.root, "add", initial, get_next_run=self._get_next_schedule_run)
        d._lang = self._lang
        d._reinit_ui_text()
        self.root.wait_window(d)
        # 恢复刷新
        if not was_paused:
            self._refresh_paused = False
            self._start_refresh_loop()
        _res = getattr(d, "result", None)
        if not _res:
            return
        r = d.result
        stype = r.get("script_type") or (get_script_type(r["path"]) if r["path"] else "batch")
        if not stype or stype == "unknown":
            if r.get("is_python"):
                stype = "python"
            else:
                stype = get_script_type(r["path"]) or "batch"
        interpreter = r.get("interpreter") or r.get("python_exe") or ""
        self.items.append({
            "id": self._next_id,
            **{key: r[key] for key in FEATURE_FIELDS if key in r},
            "name": r["name"],
            "path": r["path"],
            "work_dir": r["work_dir"],
            "group": r.get("group", ""),
            "script_type": stype,
            "interpreter": interpreter,
            "is_python": stype == "python",
            "python_exe": interpreter if stype == "python" else "",
            "auto_restart": r.get("auto_restart", False),
            "log_output": r.get("log_output", False),
            "schedule_enabled": r.get("schedule_enabled", False),
            "launch_on_app_start": r.get("launch_on_app_start", False),
            "schedule_action": r.get("schedule_action", "start"),
            "schedule_type": r.get("schedule_type", "daily"),
            "schedule_time": r.get("schedule_time", "09:00"),
            "schedule_interval": r.get("schedule_interval", 5),
            "schedule_weekday": r.get("schedule_weekday", 0),
            "schedule_day": r.get("schedule_day", 1),
            "schedule_quarter": r.get("schedule_quarter", "q1"),
            "schedule_month": r.get("schedule_month", 1),
            "schedule_after_at": r.get("schedule_after_at"),
            "depends_on": r.get("depends_on", ""),
            "delay_after_dep": r.get("delay_after_dep", 0),
            "args": r.get("args", ""),
            "env_vars": r.get("env_vars", ""),
            "http_method": r.get("http_method") or "GET",
            "http_headers": r.get("http_headers") or "",
            "startup_timeout": r.get("startup_timeout", 0),
            "schedule_valid_from": r.get("schedule_valid_from", ""),
            "schedule_valid_until": r.get("schedule_valid_until", ""),
            "schedule_max_runs": r.get("schedule_max_runs", 0),
            "schedule_pre_delay": r.get("schedule_pre_delay", 0),
            "pid": None,
        })
        self._next_id += 1
        self._save_config()
        self._sync_tree()
        self.status_var.set(self._lang["status_added"] % r["name"])

    def _on_add(self):
        all_ext = "*.bat;*.cmd;*.py;*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx;*.ps1;*.vbs;*.wsf;*.sh;*.exe"
        if sys.platform != "win32":
            all_ext = "*.sh;*.py;*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx;*.ps1"
            filetypes = [
                ("Shell", "*.sh"),
                ("Python", "*.py"),
                ("Node.js", "*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx"),
                ("PowerShell", "*.ps1"),
                ("全部脚本", all_ext) if self._lang == LANGUAGES["zh-CN"] else ("All Scripts", all_ext),
                ("全部", "*.*") if self._lang == LANGUAGES["zh-CN"] else ("All Files", "*.*"),
            ]
        else:
            filetypes = [
                ("全部脚本", all_ext) if self._lang == LANGUAGES["zh-CN"] else ("All Scripts", all_ext),
                ("批处理", "*.bat;*.cmd") if self._lang == LANGUAGES["zh-CN"] else ("Batch", "*.bat;*.cmd"),
                ("Python", "*.py"),
                ("Node.js", "*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx"),
                ("PowerShell", "*.ps1"),
                ("VBScript", "*.vbs"),
                ("WSF", "*.wsf"),
                ("Shell", "*.sh"),
                ("可执行文件", "*.exe") if self._lang == LANGUAGES["zh-CN"] else ("Executable", "*.exe"),
                ("全部", "*.*") if self._lang == LANGUAGES["zh-CN"] else ("All Files", "*.*"),
            ]
        path = filedialog.askopenfilename(
            title=self._lang["dialog_select_script"],
            filetypes=filetypes,
        )
        if not path:
            return
        if sys.platform != "win32" and path.lower().endswith((".bat", ".cmd", ".vbs", ".wsf", ".exe")):
            if not messagebox.askyesno(self._lang["msg_confirm"], self._lang.get("msg_win_only_script", "Add Windows-only script anyway?")):
                return
        stype = get_script_type(path)
        initial = {
            "name": os.path.splitext(os.path.basename(path))[0] or "未命名",
            "path": path,
            "script_type": stype,
            "work_dir": os.path.dirname(path),
            "group": "",
            "interpreter": "",
            "is_python": stype == "python",
            "python_exe": "",
            "auto_restart": False,
            "log_output": False,
        }
        # 对话框打开期间暂停主窗口刷新，避免扫描进程卡顿输入
        was_paused = self._refresh_paused
        if not was_paused:
            self._refresh_paused = True
        # 确保对话框使用当前语言设置
        d = AddEditDialog(self.root, "add", initial, get_next_run=self._get_next_schedule_run)
        d._lang = self._lang
        # 重新初始化界面文本
        d._reinit_ui_text()
        self.root.wait_window(d)
        # 恢复刷新
        if not was_paused:
            self._refresh_paused = False
            self._start_refresh_loop()
        if not getattr(d, "result", None):
            return
        r = d.result
        stype = r.get("script_type") or (get_script_type(r["path"]) if r["path"] else "batch")
        if not stype or stype == "unknown":
            if r.get("is_python"):
                stype = "python"
            else:
                stype = get_script_type(r["path"]) or "batch"
        interpreter = r.get("interpreter") or r.get("python_exe") or ""
        self.items.append({
            "id": self._next_id,
            **{key: r[key] for key in FEATURE_FIELDS if key in r},
            "name": r["name"],
            "path": r["path"],
            "work_dir": r["work_dir"],
            "group": r.get("group", ""),
            "script_type": stype,
            "interpreter": interpreter,
            # 兼容旧配置
            "is_python": stype == "python",
            "python_exe": interpreter if stype == "python" else "",
            "auto_restart": r.get("auto_restart", False),
            "log_output": r.get("log_output", False),
            "schedule_enabled": r.get("schedule_enabled", False),
            "launch_on_app_start": r.get("launch_on_app_start", False),
            "schedule_action": r.get("schedule_action", "start"),
            "schedule_type": r.get("schedule_type", "daily"),
            "schedule_time": r.get("schedule_time", "09:00"),
            "schedule_interval": r.get("schedule_interval", 5),
            "schedule_weekday": r.get("schedule_weekday", 0),
            "schedule_day": r.get("schedule_day", 1),
            "schedule_quarter": r.get("schedule_quarter", "q1"),
            "schedule_month": r.get("schedule_month", 1),
            "schedule_after_at": r.get("schedule_after_at"),
            "depends_on": r.get("depends_on", ""),
            "delay_after_dep": r.get("delay_after_dep", 0),
            "args": r.get("args", ""),
            "env_vars": r.get("env_vars", ""),
            "http_method": r.get("http_method") or "GET",
            "http_headers": r.get("http_headers") or "",
            "startup_timeout": r.get("startup_timeout", 0),
            "schedule_valid_from": r.get("schedule_valid_from", ""),
            "schedule_valid_until": r.get("schedule_valid_until", ""),
            "schedule_max_runs": r.get("schedule_max_runs", 0),
            "schedule_pre_delay": r.get("schedule_pre_delay", 0),
            "pid": None,
        })
        self._next_id += 1
        self._save_config()
        self._sync_tree()
        self.status_var.set(self._lang["status_added"] % r["name"])

    def _on_edit(self):
        rows = self._get_selected_rows()
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_first"])
            return
        # 多选时打开批量编辑，使修改应用到所有选中项
        if len(rows) > 1:
            BatchEditDialog(self.root, self, rows)
            return
        it = self._item_at(rows[0])
        if not it:
            return
        stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
        if not stype or stype == "unknown":
            if it.get("is_python"):
                stype = "python"
            else:
                stype = get_script_type(it.get("path", "")) or "batch"
        interpreter = it.get("interpreter") or it.get("python_exe") or ""
        initial = {
            **{key: it[key] for key in FEATURE_FIELDS if key in it},
            "name": it.get("name", ""),
            "path": it.get("path", ""),
            "script_type": stype,
            "work_dir": it.get("work_dir", ""),
            "group": it.get("group", ""),
            "interpreter": interpreter,
            "is_python": stype == "python",
            "python_exe": interpreter if stype == "python" else "",
            "auto_restart": it.get("auto_restart", False),
            "launch_on_app_start": it.get("launch_on_app_start", False),
            "log_output": it.get("log_output", False),
            "schedule_enabled": it.get("schedule_enabled", False),
            "schedule_action": it.get("schedule_action", "start"),
            "schedule_type": it.get("schedule_type", "daily"),
            "schedule_time": it.get("schedule_time", "09:00"),
            "schedule_interval": it.get("schedule_interval", 5),
            "schedule_weekday": it.get("schedule_weekday", 0),
            "schedule_day": it.get("schedule_day", 1),
            "schedule_quarter": it.get("schedule_quarter", "q1"),
            "schedule_month": it.get("schedule_month", 1),
            "depends_on": it.get("depends_on", ""),
            "delay_after_dep": it.get("delay_after_dep", 0),
            "args": it.get("args", ""),
            "env_vars": it.get("env_vars", ""),
            "http_method": it.get("http_method") or "GET",
            "http_headers": it.get("http_headers") or "",
            "startup_timeout": it.get("startup_timeout", 0),
            "schedule_valid_from": it.get("schedule_valid_from", ""),
            "schedule_valid_until": it.get("schedule_valid_until", ""),
            "schedule_max_runs": it.get("schedule_max_runs", 0),
            "schedule_pre_delay": it.get("schedule_pre_delay", 0),
        }
        # 对话框打开期间暂停主窗口刷新，避免扫描进程卡顿输入
        was_paused = self._refresh_paused
        if not was_paused:
            self._refresh_paused = True
        # 确保对话框使用当前语言设置
        d = AddEditDialog(self.root, "edit", initial, get_next_run=self._get_next_schedule_run)
        d._lang = self._lang
        d.initial_id = it.get("id")
        # 重新初始化界面文本
        d._reinit_ui_text()
        self.root.wait_window(d)
        # 恢复刷新
        if not was_paused:
            self._refresh_paused = False
            self._start_refresh_loop()
        if not getattr(d, "result", None):
            return
        r = d.result
        it.update({key: r[key] for key in FEATURE_FIELDS if key in r})
        it["name"] = r["name"]
        it["path"] = r["path"]
        it["work_dir"] = r["work_dir"]
        it["group"] = r.get("group", "")
        it["script_type"] = r.get("script_type", get_script_type(r["path"]) or "batch")
        it["interpreter"] = r.get("interpreter", "")
        it["is_python"] = it["script_type"] == "python"
        it["python_exe"] = it["interpreter"] if it["script_type"] == "python" else ""
        it["auto_restart"] = r.get("auto_restart", False)
        it["log_output"] = r.get("log_output", False)
        it["schedule_enabled"] = r.get("schedule_enabled", False)
        it["launch_on_app_start"] = r.get("launch_on_app_start", False)
        it["schedule_action"] = r.get("schedule_action", "start")
        it["schedule_type"] = r.get("schedule_type", "daily")
        it["schedule_time"] = r.get("schedule_time", "09:00")
        it["schedule_interval"] = r.get("schedule_interval", 5)
        it["schedule_weekday"] = r.get("schedule_weekday", 0)
        it["schedule_day"] = r.get("schedule_day", 1)
        it["schedule_quarter"] = r.get("schedule_quarter", "q1")
        it["schedule_month"] = r.get("schedule_month", 1)
        it["schedule_after_at"] = r.get("schedule_after_at")
        it["depends_on"] = r.get("depends_on", "")
        it["delay_after_dep"] = r.get("delay_after_dep", 0)
        it["args"] = r.get("args", "")
        it["env_vars"] = r.get("env_vars", "")
        it["http_method"] = r.get("http_method") or "GET"
        it["http_headers"] = r.get("http_headers") or ""
        it["startup_timeout"] = r.get("startup_timeout", 0)
        it["schedule_valid_from"] = r.get("schedule_valid_from", "")
        it["schedule_valid_until"] = r.get("schedule_valid_until", "")
        it["schedule_max_runs"] = r.get("schedule_max_runs", 0)
        it["schedule_pre_delay"] = r.get("schedule_pre_delay", 0)
        self._save_config()
        self._sync_tree()
        self.status_var.set(self._lang["status_updated"] % r["name"])

    def _on_delete(self):
        rows = sorted(self._get_selected_rows(), reverse=True)
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_items"])
            return
        deleted = {self.items[row]['id'] for row in rows if 0 <= row < len(self.items)}
        referenced = [item.get('name', '') for item in self.items if item['id'] not in deleted and any(ident in deleted for field in ('depends_on', 'on_success', 'on_failure') for ident in link_ids(item.get(field)))]
        if referenced and not messagebox.askyesno('清理任务引用 / Remove links', '以下任务引用了所选项，删除将同时清理引用：\n' + '\n'.join(referenced), parent=self.root):
            return
        # 检查是否有正在运行的项，仅提示（不停止）
        running_names = []
        for r in rows:
            it = self._item_at(r)
            if it and it.get("pid") and is_process_running(it["pid"]):
                running_names.append(it.get("name", "未命名"))
        if running_names:
            names_str = "\n".join(f"  • {n}" for n in running_names[:5])
            if len(running_names) > 5:
                names_str += self._lang["msg_delete_running_more"] % (len(running_names) - 5)
            msg = self._lang["msg_delete_running_detail"] % (len(running_names), names_str)
            if not messagebox.askyesno(self._lang["msg_confirm_delete"], self._lang["msg_delete_confirm"] % msg):
                return
        # 直接从列表中删除，不停止进程
        for r in rows:
            if 0 <= r < len(self.items):
                self._cancel_task(self.items[r])
                self.items.pop(r)
        self._clean_task_links()
        self._save_config()
        self._sync_tree()
        self.status_var.set(self._lang["status_deleted"])

    def _on_clear_invalid(self):
        """一键清除路径失效或无效的项（脚本不存在或路径为空）。"""
        rows = sorted(self._get_invalid_rows(), reverse=True)
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_no_invalid"])
            return
        if not messagebox.askyesno(self._lang["msg_confirm_delete"], self._lang["msg_clear_invalid_confirm"] % len(rows)):
            return
        for r in rows:
            if 0 <= r < len(self.items):
                self._cancel_task(self.items[r])
                self.items.pop(r)
        self._clean_task_links()
        self._save_config()
        self._sync_tree()
        self.status_var.set(self._lang["status_cleared_invalid"] % len(rows))

    def _check_quick_exit(self, *pids):
        """启动约 1.5 秒后检查：若进程已退出则提示运行时长、退出码，若有 log 可读则展示"""
        exited = []  # [(name, duration, rc, logpath, tail_or_none), ...]
        for pid in pids:
            if not pid or is_process_running(pid):
                continue
            if any(run['process'].pid == pid for run in getattr(self, '_task_active', {}).values()):
                continue
            for it in self.items:
                if it.get("pid") != pid:
                    continue
                popen = it.get("_popen")
                rc = popen.poll() if popen else None
                duration = time.time() - (it.get("pid_set_at") or time.time())
                logpath = it.get("_log_path")
                tail = None
                if logpath and os.path.isfile(logpath):
                    try:
                        with open(logpath, "r", encoding="utf-8", errors="replace") as f:
                            tail = (f.read() or "")[-1500:]
                    except Exception:
                        pass
                name = it.get("name", "") or "未命名"
                exited.append((name, duration, rc, logpath, tail))
                # 记录执行历史（快速退出）
                item_id = it.get("id")
                if item_id:
                    self._record_execution_stop(item_id, rc, duration)
                break
        if not exited:
            return
        if len(exited) == 1:
            name, duration, rc, logpath, tail = exited[0]
            msg = self._lang["msg_quick_exit_start"] % (name, duration, rc if rc is not None else "?")
            if logpath:
                msg += "；输出已保存到 %s" % logpath
            else:
                msg += "；建议在 CMD 中直接运行该脚本排查"
            self.status_var.set(msg)
            if tail and tail.strip():
                messagebox.showinfo(
                    "启动后很快退出",
                    self._lang["msg_quick_exit_with_log"] % (name, duration, rc if rc is not None else "?", tail),
                    parent=self.root,
                )
        else:
            names = [t[0] for t in exited]
            self.status_var.set("以下启动后很快退出：%s" % "、".join(names))
            for name, duration, rc, logpath, tail in exited:
                if tail and tail.strip():
                    messagebox.showinfo(
                        "启动后很快退出",
                        self._lang["msg_quick_exit_with_log"] % (name, duration, rc if rc is not None else "?", tail),
                        parent=self.root,
                    )
                    break

    def _run_system_power_task(self, it, trigger: str = "manual") -> bool:
        """执行系统关机/重启任务。"""
        action = get_system_power_action_for_task(it)
        if trigger == "manual":
            title = self._lang["btn_shutdown"] if action == "shutdown" else self._lang["btn_reboot"]
            msg = self._lang["msg_shutdown_confirm"] if action == "shutdown" else self._lang["msg_reboot_confirm"]
            if not messagebox.askyesno(title, msg):
                return False
        self._save_config()
        self._on_stop_all(skip_whitelist=True)
        name = it.get("name", self._lang["default_name"])
        action_text = self._lang["task_type_system_shutdown"] if action == "shutdown" else self._lang["task_type_system_reboot"]
        schedule_type = it.get("schedule_type", "daily")
        if trigger == "schedule" and schedule_type in ("once", "单次执行"):
            it["schedule_enabled"] = False
            self._save_config()
        self.status_var.set(self._lang["status_schedule_triggered_power"] % (name, action_text))
        self._sync_tree()
        ok = system_power_action(action)
        if not ok:
            self.status_var.set(self._lang["status_power_action_failed"] % action_text)
        return ok

    def _launch_single_script(self, it: dict, trigger: str = "manual") -> Optional[subprocess.Popen]:
        """启动单个脚本（内部方法），处理类型推断、日志、参数、依赖链。trigger: 'manual' 或 'schedule'。"""
        if is_system_power_task(it):
            schedule_action = it.get("schedule_action", "start")
            if trigger == "schedule" and schedule_action not in ("start", "启动") and schedule_action not in ("shutdown", "reboot"):
                return None
            if self._run_system_power_task(it, trigger=trigger):
                return None
            return None
        logpath = self._make_log_path(it)
        stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
        if not stype or stype == "unknown":
            if it.get("is_python"):
                stype = "python"
            else:
                stype = get_script_type(it.get("path", "")) or "batch"
        interpreter = it.get("interpreter") or it.get("python_exe") or None
        extra_args = it.get("args", "") or None
        env_vars = environment_vars(it) or None
        p = start_script(
            it.get("path", ""),
            effective_work_dir(it),
            stype,
            interpreter,
            log_path=logpath,
            extra_args=extra_args,
            env_vars=env_vars,
            http_method=it.get("http_method"),
            http_headers=it.get("http_headers"),
            capture_http=True,
        )
        if p:
            it["pid"] = p.pid
            it["pid_set_at"] = time.time()
            it["_popen"] = p
            if logpath:
                it["_log_path"] = logpath
            item_id = it.get("id")
            if item_id:
                self._record_execution_start(item_id, trigger=trigger)
            # 插件钩子：脚本启动
            fire_plugin_hook("on_script_start", item=it, pid=p.pid)
        return p

    def _on_start(self):
        rows = self._get_selected_rows()
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_items"])
            return
        for row in rows:
            item = self._item_at(row)
            if item:
                process = self._start_single_script(item)
                if process:
                    self.status_var.set(self._lang["msg_started"] % (item.get('name'), process.pid))
                elif item.get('_queued'):
                    self.status_var.set(self._task_text("已加入队列", "Queued") + ": " + item.get("name", ""))
        self._sync_tree()

    def _on_diagnose_item(self, it: dict):
        text = diagnose_start_item(it)
        messagebox.showinfo(self._lang.get("menu_diagnose", "诊断"), text, parent=self.root)

    def _on_schedule_overview(self):
        ScheduleOverviewDialog(self.root, self)

    def _on_restore_config_bak(self):
        bak_path = CONFIG_PATH.parent / (CONFIG_PATH.name + ".bak")
        if not bak_path.exists():
            messagebox.showinfo(self._lang["msg_info"], self._lang.get("msg_restore_bak_missing", "No .bak"), parent=self.root)
            return
        if not messagebox.askyesno(self._lang["msg_confirm"], self._lang.get("msg_restore_bak_confirm", "Restore?"), parent=self.root):
            return
        try:
            import shutil
            # 恢复前再备份当前文件
            if CONFIG_PATH.exists():
                shutil.copy2(CONFIG_PATH, CONFIG_PATH.parent / (CONFIG_PATH.name + ".before_restore.bak"))
            shutil.copy2(bak_path, CONFIG_PATH)
            self._load_config()
            self._sync_tree()
            self.status_var.set(self._lang.get("msg_restore_bak_ok", "Restored"))
        except Exception as e:
            messagebox.showerror(self._lang["msg_error"], str(e), parent=self.root)

    def _on_stop(self):
        rows = self._get_selected_rows()
        for row in rows:
            item = self._item_at(row)
            if item:
                self._cancel_task(item)
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_items"])
            return
        stopped_any = False
        for r in rows:
            it = self._item_at(r)
            if not it:
                continue
            stype = get_script_type(it.get("path", ""))
            # 进程名类型：按进程名停止所有匹配的进程
            if stype == "process":
                pname = (it.get("path") or "").strip()
                killed = stop_process_by_name(pname)
                it["pid"] = None
                it.pop("pid_set_at", None)
                it.pop("_popen", None)
                if killed > 0:
                    self.status_var.set(self._lang["msg_stopped"] % (it.get('name') + f" ({killed})"))
                    stopped_any = True
                else:
                    self.status_var.set(self._lang["status_already_stopped"])
                continue

            pid = it.get("pid")
            if pid and is_process_running(pid):
                pids = get_process_tree_pids(pid)
                kill_ok = True
                for px in pids:
                    if not kill_process(px):
                        kill_ok = False
                if not kill_ok and is_process_running(pid):
                    # 进程未能终止，保留 PID 以便再次尝试
                    self.status_var.set(self._lang.get("msg_stop_failed", "「%s」停止失败，进程可能需要管理员权限") % it.get('name'))
                    continue
                it["pid"] = None
                it.pop("pid_set_at", None)
                it.pop("_popen", None)
                self.status_var.set(self._lang["msg_stopped"] % it.get('name'))
                stopped_any = True
        if not stopped_any:
            self.status_var.set(self._lang["status_already_stopped"])
        self._sync_tree()

    def _on_restart(self):
        rows = self._get_selected_rows()
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_items"])
            return
        # 先收集需要停止的 PID，用于判断进程是否真正退出
        self._restart_pids_to_wait = []
        for r in rows:
            if 0 <= r < len(self.items):
                it = self.items[r]
                pid = it.get("pid")
                if pid and is_process_running(pid):
                    self._restart_pids_to_wait.append(pid)
        self._on_stop()  # 会 _sync_tree 并清空选中
        self._restart_ids = [self.items[r].get("id") for r in rows if 0 <= r < len(self.items)]
        self._restart_wait_start = time.time()
        self._restart_wait_phase()

    def _restart_wait_phase(self):
        """非阻塞等待旧进程退出，最多等 3 秒，然后启动新进程。"""
        # 检查是否所有旧进程都已退出
        still_running = [pid for pid in self._restart_pids_to_wait if is_process_running(pid)]
        elapsed = time.time() - self._restart_wait_start
        if still_running and elapsed < 3:
            # 还有进程没退出，100ms 后再检查
            self.root.after(100, self._restart_wait_phase)
            return
        # 所有进程已退出或超时，执行启动
        id_set = set(getattr(self, "_restart_ids", []) or [])
        to_sel = [child for child in self.tree.get_children() if self._item_id_from_tree_iid(child) in id_set]
        if to_sel:
            self.tree.selection_set(*to_sel)
        self._on_start()

    def _on_start_all(self):
        for item in self.items:
            self._start_single_script(item)
        self._sync_tree()
        self.status_var.set(self._task_text("已提交全部任务", "All tasks submitted"))

    def _on_stop_all(self, skip_whitelist: bool = False):
        for item in self.items:
            if not (skip_whitelist and is_service_whitelisted(item.get('path', ''))):
                self._cancel_task(item)
        n = 0
        failed = 0
        skipped = 0
        for it in self.items:
            if skip_whitelist and is_service_whitelisted(it.get("path", "")):
                skipped += 1
                continue
            pid = it.get("pid")
            if pid and is_process_running(pid):
                pids = get_process_tree_pids(pid)
                kill_ok = True
                for px in pids:
                    if not kill_process(px):
                        kill_ok = False
                if not kill_ok and is_process_running(pid):
                    failed += 1
                    continue
                it["pid"] = None
                it.pop("pid_set_at", None)
                it.pop("_popen", None)
                n += 1
        self._sync_tree()
        msg = self._lang["msg_stop_all"] % n
        if failed:
            msg += "；%d 项停止失败" % failed
        if skip_whitelist and skipped:
            msg += "；白名单跳过 %d 项" % skipped
        self.status_var.set(msg)

    def _on_start_stopped(self):
        """启动所有已停止的任务。"""
        n = 0
        for it in self.items:
            pid = it.get("pid")
            if pid and is_process_running(pid):
                continue
            p = self._start_single_script(it)
            if p:
                n += 1
        self._sync_tree()
        self.status_var.set(self._lang["msg_start_all"] % n)

    def _on_stop_running(self):
        """停止所有正在运行的任务。"""
        n = 0
        for it in self.items:
            pid = it.get("pid")
            if not pid or not is_process_running(pid):
                continue
            stype = get_script_type(it.get("path", ""))
            if stype == "process":
                pname = (it.get("path") or "").strip()
                stop_process_by_name(pname)
            else:
                pids = get_process_tree_pids(pid)
                for px in pids:
                    kill_process(px)
            it["pid"] = None
            it.pop("pid_set_at", None)
            it.pop("_popen", None)
            n += 1
        self._sync_tree()
        self.status_var.set(self._lang["msg_stop_all"] % n)

    def _on_stop_all_batch(self):
        """停止系统中所有正在运行的脚本（批处理、Python、Node.js、PowerShell等），包括列表中的和列表外的。"""
        if not HAS_PSUTIL:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_need_psutil"])
            return
        
        # 使用 scan_running_script_processes 扫描所有类型的脚本
        all_scripts = scan_running_script_processes()
        
        # 排除程序本身
        app_path = get_app_path()
        script_processes = []
        for rec in all_scripts:
            script_path = rec.get("script_path", "")
            if script_path:
                try:
                    # 排除程序本身
                    if os.path.normcase(os.path.abspath(script_path)) == os.path.normcase(app_path):
                        continue
                except Exception:
                    pass
            script_processes.append({
                "pid": rec["pid"],
                "name": rec.get("name", "未命名"),
                "path": script_path,
                "type": rec.get("type", "未知"),
            })
        
        if not script_processes:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_no_running_scripts"])
            return
        
        # 确认对话框
        names = [f"{p['name']} ({p['type']})" for p in script_processes[:5]]
        names_str = "\n".join(f"  • {n}" for n in names)
        if len(script_processes) > 5:
            names_str += self._lang["msg_stop_confirm_more"] % (len(script_processes) - 5)
        msg = self._lang["msg_stop_confirm_detail"] % (len(script_processes), names_str)
        
        if not messagebox.askyesno(self._lang["msg_confirm_stop"], msg):
            return
        
        # 停止所有脚本进程
        stopped = 0
        failed = []
        stopped_names = []
        for proc in script_processes:
            pid = proc["pid"]
            try:
                if is_process_running(pid):
                    # 获取进程树的所有PID
                    pids = get_process_tree_pids(pid)
                    # 停止所有子进程
                    for px in pids:
                        if is_process_running(px):
                            kill_process(px)
                    # 再次检查主进程是否已停止
                    if not is_process_running(pid):
                        stopped += 1
                        stopped_names.append(f"{proc['name']} ({proc['type']})")
                    else:
                        failed.append(f"{proc['name']} ({proc['type']})")
            except Exception as e:
                failed.append(f"{proc['name']} ({proc['type']})")
        
        # 强制刷新状态
        self._refresh_status(and_set_status=False)
        self._sync_tree()
        
        if failed:
            self.status_var.set(self._lang["msg_stopped_external"] % (stopped, len(failed)))
            stopped_names_str = ', '.join(stopped_names[:3]) + ('...' if len(stopped_names) > 3 else '')
            failed_str = "\n".join(failed)
            messagebox.showwarning(self._lang["msg_partial_failed"], self._lang["msg_stopped_count"] % (stopped, stopped_names_str, len(failed), failed_str))
        else:
            self.status_var.set(f"已停止 {stopped} 个脚本：{', '.join(stopped_names[:3])}{'...' if len(stopped_names) > 3 else ''}")

    def _copy_to_clipboard(self, text):
        """复制文本到剪贴板。"""
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set(self._lang.get("msg_copied", "已复制") + f": {text[:50]}")

    def _on_start_group(self, group):
        """启动指定分组的所有任务。"""
        n = 0
        for it in self.items:
            if it.get("group") == group:
                pid = it.get("pid")
                if pid and is_process_running(pid):
                    continue
                p = self._start_single_script(it)
                if p:
                    n += 1
        self._sync_tree()
        self.status_var.set(self._lang["msg_start_all"] % n + f" ({group})")

    def _on_stop_group(self, group):
        """停止指定分组的所有任务。"""
        n = 0
        for it in self.items:
            if it.get("group") == group:
                self._cancel_task(it)
                pid = it.get("pid")
                if not pid or not is_process_running(pid):
                    continue
                stype = get_script_type(it.get("path", ""))
                if stype == "process":
                    stop_process_by_name((it.get("path") or "").strip())
                else:
                    pids = get_process_tree_pids(pid)
                    for px in pids:
                        kill_process(px)
                it["pid"] = None
                it.pop("pid_set_at", None)
                it.pop("_popen", None)
                n += 1
        self._sync_tree()
        self.status_var.set(self._lang["msg_stop_all"] % n + f" ({group})")

    def _is_auto_start_enabled(self):
        """检查是否已启用开机自启。优先检查注册表（与 _set_startup_enabled 一致），兼容检查快捷方式文件。"""
        if sys.platform != "win32":
            return False
        # 优先检查注册表（与菜单"启用/禁用"使用的 _set_startup_enabled 一致）
        try:
            if _is_startup_enabled():
                return True
        except Exception:
            pass
        # 兼容：检查快捷方式文件（旧版创建的）
        try:
            startup_dir = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
            shortcut_path = os.path.join(startup_dir, "BatchScriptManager.lnk")
            return os.path.isfile(shortcut_path)
        except Exception:
            return False

    def _on_toggle_auto_start(self):
        """切换开机自启。使用注册表方式（与菜单启用/禁用一致），兼容清理旧版快捷方式。"""
        if sys.platform != "win32":
            messagebox.showinfo(self._lang["msg_info"], "仅支持 Windows")
            return
        try:
            if self._is_auto_start_enabled():
                # 禁用：清理注册表
                _set_startup_enabled(False)
                # 兼容清理旧版快捷方式
                try:
                    startup_dir = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
                    shortcut_path = os.path.join(startup_dir, "BatchScriptManager.lnk")
                    if os.path.isfile(shortcut_path):
                        os.remove(shortcut_path)
                except Exception:
                    pass
                self.status_var.set(self._lang["msg_auto_start_disabled"])
            else:
                # 启用：写入注册表
                _set_startup_enabled(True)
                self.status_var.set(self._lang["msg_auto_start_enabled"])
        except Exception as e:
            self.status_var.set(self._lang["msg_auto_start_failed"] % str(e)[:80])

    def _on_duplicate(self):
        rows = sorted(self._get_selected_rows(), reverse=False)
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_items"])
            return
        for r in rows:
            it = self._item_at(r)
            if not it:
                continue
            stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
            if not stype or stype == "unknown":
                if it.get("is_python"):
                    stype = "python"
                else:
                    stype = get_script_type(it.get("path", "")) or "batch"
            interpreter = it.get("interpreter") or it.get("python_exe") or ""
            self.items.append({
                "id": self._next_id,
                **{key: it[key] for key in FEATURE_FIELDS if key in it},
                "name": (it.get("name", "") or "未命名") + "(副本)",
                "path": it.get("path", ""),
                "work_dir": it.get("work_dir") or "",
                "group": it.get("group", ""),
                "script_type": stype,
                "interpreter": interpreter,
                "is_python": stype == "python",
                "python_exe": interpreter if stype == "python" else "",
                "auto_restart": it.get("auto_restart", False),
                "launch_on_app_start": it.get("launch_on_app_start", False),
                "log_output": it.get("log_output", False),
                "schedule_enabled": it.get("schedule_enabled", False),
                "schedule_action": it.get("schedule_action", "start"),
                "schedule_type": it.get("schedule_type", "daily"),
                "schedule_time": it.get("schedule_time", "09:00"),
                "schedule_interval": it.get("schedule_interval", 5),
                "schedule_weekday": it.get("schedule_weekday", 0),
                "schedule_day": it.get("schedule_day", 1),
                "schedule_quarter": it.get("schedule_quarter", "q1"),
                "schedule_month": it.get("schedule_month", 1),
                "schedule_after_at": it.get("schedule_after_at"),
                "schedule_once_date": it.get("schedule_once_date", ""),
                "depends_on": it.get("depends_on", ""),
                "delay_after_dep": it.get("delay_after_dep", 0),
                "args": it.get("args", ""),
                "env_vars": it.get("env_vars", ""),
                "http_method": it.get("http_method") or "GET",
                "http_headers": it.get("http_headers") or "",
                "startup_timeout": it.get("startup_timeout", 0),
                "schedule_valid_from": it.get("schedule_valid_from", ""),
                "schedule_valid_until": it.get("schedule_valid_until", ""),
                "schedule_max_runs": it.get("schedule_max_runs", 0),
                "schedule_pre_delay": it.get("schedule_pre_delay", 0),
                "pid": None,
            })
            self._next_id += 1
        self._save_config()
        self._sync_tree()
        self.status_var.set(self._lang["msg_copied"] % len(rows))

    def _ports_from_cache(self, pid: int) -> list:
        """用刷新时的 pid→端口缓存，避免点选时再调 net_connections。"""
        cached = getattr(self, "_ports_cache", None) or {}
        if not cached or not pid:
            return []
        tree = getattr(self, "_pids_tree_cache", None) or {}
        if pid not in tree:
            tree[pid] = get_process_tree_pids(pid)
            self._pids_tree_cache = tree
        pids = tree.get(pid, {pid})
        return sorted({p for px in pids if px in cached for p in cached[px]})

    def _get_ports_for_selected(self) -> tuple:
        """返回 (首个选中项或 None, 端口列表)。仅当该选中项运行中且确有监听端口时端口非空。"""
        first_it = None
        for r in self._get_selected_rows():
            it = self._item_at(r)
            if not it:
                continue
            if first_it is None:
                first_it = it
            pid = it.get("pid")
            if not pid:
                continue
            ports = self._ports_from_cache(pid)
            if ports:
                return (it, ports)
            # 缓存尚未建立时才回退一次系统查询（启动后首几秒）
            if not getattr(self, "_ports_cache", None):
                if not is_process_running(pid):
                    continue
                ports = get_ports_for_pids(get_process_tree_pids(pid))
                if ports:
                    return (it, ports)
        return (first_it, [])

    def _on_open_file(self, file_path: str):
        """使用默认程序打开文件。"""
        if not file_path or not os.path.isfile(file_path):
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_file_not_exists"] % (file_path or ""))
            return
        
        # 防止打开程序本身（会启动新的管理器实例）
        app_path = get_app_path()
        file_abs_path = os.path.abspath(file_path)
        if os.path.normcase(file_abs_path) == os.path.normcase(app_path):
            # 使用文本编辑器打开，而不是执行
            try:
                if sys.platform == "win32":
                    # Windows: 使用 notepad 打开
                    subprocess.Popen(["notepad.exe", file_path])
                else:
                    # Linux/Mac: 使用系统默认文本编辑器
                    editor = os.environ.get("EDITOR", "nano" if sys.platform != "darwin" else "open")
                    subprocess.Popen([editor, file_path])
                self.status_var.set(self._lang["msg_opened_in_editor"] % os.path.basename(file_path))
            except Exception as e:
                messagebox.showerror(self._lang["msg_error"], self._lang["msg_cannot_open_file"] % str(e))
            return
        
        try:
            if sys.platform == "win32":
                os.startfile(file_path)
            else:
                subprocess.Popen(["xdg-open", file_path] if sys.platform != "darwin" else ["open", file_path])
            self.status_var.set(self._lang["msg_opened_file"] % os.path.basename(file_path))
        except Exception as e:
            messagebox.showerror(self._lang["msg_error"], self._lang["msg_cannot_open_file"] % str(e))

    def _on_open_tool_folder(self):
        """在资源管理器中打开程序/工具所在目录。"""
        self._on_open_dir(str(Path(get_app_path()).resolve().parent))

    def _on_open_dir(self, dir_path: str):
        """在资源管理器中打开目录。"""
        if not dir_path or not os.path.isdir(dir_path):
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_dir_not_exists"] % (dir_path or ""))
            return
        try:
            # Windows: 使用 explorer 打开目录
            if sys.platform == "win32":
                os.startfile(dir_path)
            else:
                # Linux/Mac: 使用系统命令
                subprocess.Popen(["xdg-open", dir_path] if sys.platform != "darwin" else ["open", dir_path])
            self.status_var.set(self._lang["msg_opened_dir"] % dir_path)
        except Exception as e:
            messagebox.showerror(self._lang["msg_error"], self._lang["msg_cannot_open_dir"] % str(e))

    def _on_home(self):
        """在浏览器打开项目首页（GitHub）。"""
        url = "https://github.com/shellsec/WindowsBatchScriptManager"
        webbrowser.open(url)
        self.status_var.set(self._lang.get("msg_opened_url", "已打开 %s") % url)

    def _get_startup_enable_label(self) -> str:
        """「启用」项文案：当前已启用时带 ✓。"""
        base = self._lang.get("menu_startup_enable", "启用")
        return (base + " ✓") if self._startup_var.get() else base

    def _get_startup_disable_label(self) -> str:
        """「禁用」项文案：当前已禁用时带 ✓。"""
        base = self._lang.get("menu_startup_disable", "禁用")
        return (base + " ✓") if not self._startup_var.get() else base

    def _update_startup_submenu_labels(self) -> None:
        """刷新开机启动子菜单「启用」「禁用」的 ✓ 显示。"""
        if not getattr(self, "_startup_menu", None):
            return
        try:
            self._startup_menu.entryconfig(0, label=self._get_startup_enable_label())
            self._startup_menu.entryconfig(1, label=self._get_startup_disable_label())
        except Exception:
            pass

    def _on_startup_enable(self):
        """开机/登录自启 → 启用。"""
        if not _PLATFORM and sys.platform != "win32":
            return
        self._startup_var.set(True)
        _set_startup_enabled(True)
        self._update_startup_submenu_labels()

    def _on_startup_disable(self):
        """开机/登录自启 → 禁用。"""
        if not _PLATFORM and sys.platform != "win32":
            return
        self._startup_var.set(False)
        _set_startup_enabled(False)
        self._update_startup_submenu_labels()

    def _on_visit_port(self, port: Optional[int] = None):
        """在浏览器打开 http://localhost:端口。port 为 None 时取选中项的首个端口。"""
        if port is not None:
            url = "http://localhost:%s" % port
            webbrowser.open(url)
            self.status_var.set(self._lang["msg_opened_url"] % url)
            return
        it, ports = self._get_ports_for_selected()
        if ports:
            url = "http://localhost:%s" % ports[0]
            webbrowser.open(url)
            if len(ports) > 1:
                self.status_var.set(self._lang["msg_opened_url_ports"] % (url, len(ports)))
            else:
                self.status_var.set(self._lang["msg_opened_url"] % url)
            return
        # 无端口：按是否选中、是否运行给出具体说明
        if not it:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_one"])
            return
        name = it.get("name", "") or "未命名"
        if not it.get("pid") or not is_process_running(it.get("pid") or 0):
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_not_running_visit"] % name)
            return
        messagebox.showinfo(self._lang["msg_info"], self._lang["msg_no_port_detected"] % name)

    def _on_show_ports(self):
        rows = self._get_selected_rows()
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_first"])
            return
        it = self._item_at(rows[0])
        if not it:
            return
        pid = it.get("pid")
        if not pid or not is_process_running(pid):
            messagebox.showinfo(self._lang["dialog_ports"], self._lang["msg_not_running_ports"] % it.get('name'))
            return
        pids = get_process_tree_pids(pid)
        ports = get_ports_for_pids(pids)
        name = it.get('name', '')
        lines = [self._lang["label_process_info"] % (name, pid), self._lang["label_listening_ports"] + (", ".join(map(str, ports)) if ports else self._lang["label_no_ports"])]
        for px in sorted(pids):
            info = get_process_info(px)
            cpu_mem = ""
            if HAS_PSUTIL:
                try:
                    pr = psutil.Process(px)
                    cpu = pr.cpu_percent(interval=0.1)
                    mem = pr.memory_info().rss / (1024 * 1024)
                    cpu_mem = f" | {self._lang['label_cpu']}: {cpu:.1f}% | {self._lang['label_memory']}: {mem:.1f} {self._lang['label_mb']}"
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if info:
                lines.append(self._lang["label_process_detail"] % (px, info.get('name',''), info.get('exe','')) + cpu_mem)
            else:
                lines.append(self._lang["label_process_detail_no_info"] % px + cpu_mem)
        win = tk.Toplevel(self.root)
        win.title(self._lang["dialog_ports"])
        win.geometry("700x400")
        txt = tk.Text(win, wrap=tk.WORD, font=("Consolas", 10))
        txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        txt.insert("1.0", "\n".join(lines))
        txt.config(state=tk.DISABLED)
        if ports:
            btn_frame = ttk.Frame(win)
            btn_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
            ttk.Label(btn_frame, text=self._lang["label_open_in_browser"]).pack(side=tk.LEFT, padx=(0, 6))
            for p in ports:
                ttk.Button(btn_frame, text=":%s" % p, command=lambda _p=p: self._on_visit_port(_p)).pack(side=tk.LEFT, padx=2)

    def _on_export_config(self):
        path = filedialog.asksaveasfilename(parent=self.root, title=self._lang['dialog_export_config'], defaultextension='.json', filetypes=[('JSON', '*.json')])
        if not path:
            return
        try:
            Path(path).write_text(json.dumps([config_item_to_save(i) for i in self.items], ensure_ascii=False, indent=2), encoding='utf-8')
            self.status_var.set(self._lang['msg_export_to'] % path)
        except Exception as exc:
            messagebox.showerror(self._lang['msg_failed'], str(exc), parent=self.root)

    def _on_import_config(self):
        path = filedialog.askopenfilename(parent=self.root, title=self._lang['dialog_import_config'], filetypes=[('JSON', '*.json')])
        if not path:
            return
        try:
            incoming = json.loads(Path(path).read_text(encoding='utf-8'))
            if not isinstance(incoming, list) or any(not isinstance(i, dict) for i in incoming):
                raise ValueError('配置必须为任务列表 / Expected task list')
            mode = messagebox.askyesnocancel(self._lang['msg_import_way'], self._lang['msg_import_merge'] + self._lang['msg_import_replace'], parent=self.root)
            if mode is None:
                return
            merged = merge_task_items([config_item_to_save(i) for i in self.items], incoming, replace=not mode)
            if not save_config([config_item_to_save(i) for i in merged]):
                raise OSError('无法保存配置 / Failed to save configuration')
            self._load_config()
            self._next_id = next_item_id(self.items)
            self._sync_tree()
        except Exception as exc:
            messagebox.showerror(self._lang['msg_failed'], str(exc), parent=self.root)


    def _on_scan_running(self):
        """打开「自动识别」窗口：识别系统当前运行中的批处理/Python 进程，可全部加入或选中的加入。"""
        ScanRunningDialog(self.root, self, exe_only=False)

    def _on_scan_exe_add(self):
        """打开「识别exe加入」窗口：识别系统当前运行中的 .exe 进程，可全部加入或选中的加入。"""
        ScanRunningDialog(self.root, self, exe_only=True)

    def _on_scan_and_add(self):
        """一键自动识别并加入：扫描系统运行中的批处理/Python，若发现则确认后全部加入。"""
        if not HAS_PSUTIL:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_scan_need_psutil"])
            return
        lst = scan_running_script_processes()
        if not lst:
            messagebox.showinfo(self._lang["msg_scan_confirm_title"], self._lang["msg_scan_no_processes"])
            return
        if not messagebox.askyesno(self._lang["msg_scan_confirm_title"], self._lang["msg_scan_confirm"] % len(lst)):
            return
        existing = {it.get("path", "") for it in self.items}
        added, skipped = 0, 0
        for rec in lst:
            path = rec.get("script_path", "").strip()
            if not path:
                continue
            if path in existing:
                skipped += 1
                continue
            self._add_from_scan(path, rec.get("cwd", "") or "", rec.get("type") == "Python", rec.get("pid"))
            existing.add(path)
            added += 1
        self._sync_tree()
        msg = "已加入 %d 项" % added
        if skipped:
            msg += "，跳过 %d 项（路径已存在）" % skipped
        self.status_var.set(self._lang["msg_scan_prefix"] + msg)
        skipped_msg = (self._lang["msg_skipped_paths"] % skipped) if skipped else ""
        messagebox.showinfo(self._lang["msg_complete"], self._lang["msg_added_scripts"] % (added, skipped_msg))

    def _add_from_scan(self, script_path: str, cwd: str, is_python: bool, pid: Optional[int]):
        """从「自动识别」加入一条：把已在跑的进程纳入管理器。"""
        name = os.path.splitext(os.path.basename(script_path))[0] or "未命名"
        work = (cwd or os.path.dirname(script_path) or "").strip()
        adopt_pid = pid if (pid and is_process_running(pid)) else None
        stype = get_script_type(script_path) or ("python" if is_python else "batch")
        self.items.append({
            "id": self._next_id,
            "name": name,
            "path": script_path,
            "work_dir": work,
            "group": "",
            "script_type": stype,
            "interpreter": "",
            "is_python": stype == "python",
            "python_exe": "",
            "auto_restart": False,
            "launch_on_app_start": False,
            "log_output": False,
            "schedule_enabled": False,
            "schedule_action": "start",
            "schedule_type": "daily",
            "schedule_time": "09:00",
            "schedule_interval": 5,
            "schedule_weekday": 0,
            "schedule_day": 1,
            "schedule_quarter": "q1",
            "schedule_month": 1,
            "schedule_once_date": "",
            "schedule_after_at": None,
            "depends_on": "",
            "delay_after_dep": 0,
            "args": "",
            "env_vars": "",
            "startup_timeout": 0,
            "schedule_valid_from": "",
            "schedule_valid_until": "",
            "schedule_max_runs": 0,
            "schedule_pre_delay": 0,
            "pid": adopt_pid,
            "pid_set_at": time.time() if adopt_pid else None,
        })
        self._next_id += 1
        self._sync_tree()
        self.root.update_idletasks()
        try:
            self._save_config()
        except Exception:
            pass
        self.status_var.set("已加入: " + name + (" (接管运行中)" if adopt_pid else ""))

    def _on_show_history(self):
        """显示执行历史记录窗口。"""
        HistoryDialog(self.root, self)

    def _on_batch_edit(self):
        """批量编辑选中项。"""
        rows = self._get_selected_rows()
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_items"])
            return
        BatchEditDialog(self.root, self, rows)

    def _get_templates_path(self):
        return CONFIG_PATH.parent / "templates.json"

    def _load_templates(self):
        p = self._get_templates_path()
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_templates(self, templates):
        p = self._get_templates_path()
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(templates, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_save_template(self):
        rows = self._get_selected_rows()
        if not rows:
            return
        it = self._item_at(rows[0])
        if not it:
            return
        name = it.get("name", self._lang["default_name"])
        import tkinter.simpledialog as simpledialog
        tpl_name = simpledialog.askstring(self._lang["menu_save_template"], self._lang["label_template_name"], initialvalue=name, parent=self.root)
        if not tpl_name:
            return
        templates = self._load_templates()
        tpl = {k: v for k, v in it.items() if not k.startswith("_") and k not in ("pid", "pid_set_at", "_popen", "_log_path", "_startup_confirmed", "_restart_history", "_crash_loop")}
        tpl["template_name"] = tpl_name
        templates.append(tpl)
        self._save_templates(templates)
        self.status_var.set(self._lang["msg_template_saved"] % tpl_name)

    def _on_apply_template(self):
        templates = self._load_templates()
        if not templates:
            messagebox.showinfo(self._lang["msg_info"], self._lang.get("msg_no_templates", "暂无模板"))
            return
        win = tk.Toplevel(self.root)
        win.title(self._lang["menu_apply_template"])
        win.geometry("400x350")
        win.transient(self.root)
        lb = tk.Listbox(win, font=("Microsoft YaHei UI", 10))
        sb = ttk.Scrollbar(win, command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        for t in templates:
            lb.insert(tk.END, t.get("template_name", "?"))
        def on_ok():
            sel = lb.curselection()
            if not sel:
                return
            tpl = templates[sel[0]]
            new_it = {k: v for k, v in tpl.items() if k != "template_name"}
            new_it["id"] = self._next_id
            new_it["name"] = new_it.get("name", "") + " (2)"
            new_it["pid"] = None
            self.items.append(new_it)
            self._next_id += 1
            self._sync_tree(force_rebuild=True)
            self._save_config()
            win.destroy()
        ttk.Button(win, text="OK", command=on_ok).pack(pady=4)

    def _on_manage_templates(self):
        templates = self._load_templates()
        if not templates:
            messagebox.showinfo(self._lang["msg_info"], self._lang.get("msg_no_templates", "暂无模板"))
            return
        win = tk.Toplevel(self.root)
        win.title(self._lang["title_template_manager"])
        win.geometry("500x400")
        win.transient(self.root)
        lb = tk.Listbox(win, font=("Microsoft YaHei UI", 10))
        sb = ttk.Scrollbar(win, command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        for t in templates:
            lb.insert(tk.END, f"{t.get('template_name', '?')} - {t.get('script_type', '?')} - {t.get('path', '?')[:40]}")
        def on_delete():
            sel = lb.curselection()
            if not sel:
                return
            tn = templates[sel[0]].get("template_name", "?")
            templates.pop(sel[0])
            self._save_templates(templates)
            lb.delete(sel[0])
            self.status_var.set(self._lang["msg_template_deleted"] % tn)
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(btn_frame, text=self._lang["btn_close"], command=win.destroy).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text=self._lang.get("btn_delete", "删除"), command=on_delete).pack(side=tk.RIGHT, padx=4)

    def _on_notification_config(self):
        """打开通知配置窗口。"""
        NotificationConfigDialog(self.root, self)

    def _on_tail_log(self):
        rows = self._get_selected_rows()
        if not rows: messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_first"]); return
        it = self._item_at(rows[0])
        if not it: return
        log_path = it.get("_log_path")
        if not log_path:
            log_dir = CONFIG_PATH.parent / "logs"
            if log_dir.is_dir():
                nb = "".join(c if c.isalnum() or c in "._-" else "_" for c in (it.get("name") or "out"))[:50]
                cs = sorted(log_dir.glob(f"{nb}_*.log"), reverse=True)
                if cs: log_path = str(cs[0])
        if not log_path or not os.path.isfile(log_path):
            messagebox.showinfo(self._lang["msg_info"], self._lang.get("msg_no_log_file", "未找到日志文件，请先启用「记录输出到 logs/」并运行脚本")); return
        TailLogDialog(self.root, self, log_path, it.get("name", ""))

    def _on_view_log(self):
        """查看日志：在弹窗中显示完整日志内容。"""
        rows = self._get_selected_rows()
        if not rows:
            return
        it = self._item_at(rows[0])
        if not it:
            return
        log_path = it.get("_log_path")
        if not log_path:
            log_dir = CONFIG_PATH.parent / "logs"
            if log_dir.is_dir():
                nb = "".join(c if c.isalnum() or c in "._-" else "_" for c in (it.get("name") or "out"))[:50]
                cs = sorted(log_dir.glob(f"{nb}_*.log"), reverse=True)
                if cs:
                    log_path = str(cs[0])
        if not log_path or not os.path.isfile(log_path):
            messagebox.showinfo(self._lang["msg_info"], self._lang["menu_log_not_found"])
            return
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if len(content) > 100000:
                content = content[-100000:]
        except Exception as e:
            content = f"Read error: {e}"
        win = tk.Toplevel(self.root)
        win.title(self._lang["title_log_viewer"] % it.get("name", ""))
        win.geometry("800x500")
        text = tk.Text(win, wrap=tk.WORD, font=("Consolas", 9))
        sb = ttk.Scrollbar(win, command=text.yview)
        text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert("1.0", content)
        text.configure(state=tk.DISABLED)


    def _on_open_plugins_dir(self):
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        n = len(_get_plugins())
        self.status_var.set(self._lang["msg_plugins_loaded"] % n if n else self._lang["msg_plugins_none"])
        try:
            if sys.platform == "win32": os.startfile(str(PLUGIN_DIR))
            elif sys.platform == "darwin": subprocess.Popen(["open", str(PLUGIN_DIR)])
            else: subprocess.Popen(["xdg-open", str(PLUGIN_DIR)])
        except Exception: pass

    def _on_copy_ip(self, event=None):
        """复制本地IP地址到剪贴板。"""
        ip = get_local_ip()
        unavailable_text = "无法获取"
        if ip and ip != unavailable_text:
            self.root.clipboard_clear()
            self.root.clipboard_append(ip)
            self.root.update()  # 确保剪贴板更新
            # 临时显示提示
            original_text = self.ip_label.cget("text")
            copied_text = "已复制!" if self._current_language == "zh-CN" else "Copied!"
            self.ip_label.config(text=copied_text, foreground="green")
            self.root.after(1000, lambda: self.ip_label.config(text=original_text, foreground="blue"))
            self.status_var.set(self._lang["msg_ip_copied"] % ip)
        else:
            self.status_var.set(self._lang["msg_ip_unavailable"])

    def _load_local_ip(self):
        """异步加载本地IP，避免启动时主线程阻塞。"""
        local_ip = get_local_ip()
        self.root.after(0, lambda: self._update_local_ip_display(local_ip))

    def _update_local_ip_display(self, local_ip: str):
        """更新本地IP标签显示。"""
        if local_ip == "无法获取":
            local_ip = self._lang["msg_ip_unavailable"]
            self.ip_label.config(text=local_ip, foreground="gray")
        else:
            self.ip_label.config(text=local_ip, foreground="blue")
        self.hint_copy_label.config(text=self._lang["hint_click_to_copy"])

    def _on_local_ip_leave(self, event=None):
        """本地IP标签鼠标离开：恢复颜色。"""
        text = self.ip_label.cget("text")
        if text == self._lang["msg_ip_unavailable"] or text == self._lang["hint_loading"]:
            self.ip_label.config(foreground="gray")
        else:
            self.ip_label.config(foreground="blue")

    def _load_public_ip(self):
        """异步加载外网IP（先判断是否联网，未联网直接返回「无法获取」）。"""
        public_ip = get_public_ip()
        # 在主线程中更新UI
        self.root.after(0, lambda: self._update_public_ip_display(public_ip))

    def _update_public_ip_display(self, public_ip: str):
        """更新外网IP显示。"""
        if public_ip == "无法获取":
            public_ip = self._lang["msg_ip_unavailable"]
            self.public_ip_label.config(text=public_ip, foreground="gray", cursor="arrow")
            self.hint_copy_public_label.config(text="")
        else:
            self.public_ip_label.config(text=public_ip, foreground="blue", cursor="hand2", font=("", 9, "underline"))
            self.hint_copy_public_label.config(text=self._lang["hint_click_to_copy"])

    def _on_copy_public_ip(self, event=None):
        """复制外网IP地址到剪贴板。"""
        ip_text = self.public_ip_label.cget("text")
        unavailable_text = self._lang["msg_ip_unavailable"]
        loading_text = self._lang["hint_loading"]
        if ip_text and ip_text != unavailable_text and ip_text != loading_text:
            self.root.clipboard_clear()
            self.root.clipboard_append(ip_text)
            self.root.update()  # 确保剪贴板更新
            # 临时显示提示
            original_text = self.public_ip_label.cget("text")
            copied_text = "已复制!" if self._current_language == "zh-CN" else "Copied!"
            self.public_ip_label.config(text=copied_text, foreground="green")
            self.root.after(1000, lambda: self._restore_public_ip_label(original_text))
            self.status_var.set(self._lang["msg_ip_copied"] % ip_text)
        else:
            self.status_var.set(self._lang["msg_ip_unavailable"])

    def _restore_public_ip_label(self, original_text: str):
        """恢复外网IP标签显示。"""
        unavailable_text = self._lang["msg_ip_unavailable"]
        if original_text == unavailable_text:
            self.public_ip_label.config(text=original_text, foreground="gray", cursor="arrow", font=("", 9))
            self.hint_copy_public_label.config(text="")
        else:
            self.public_ip_label.config(text=original_text, foreground="blue", cursor="hand2", font=("", 9, "underline"))
            self.hint_copy_public_label.config(text=self._lang["hint_click_to_copy"])

    def _on_public_ip_enter(self, event=None):
        """外网IP标签鼠标进入事件。"""
        text = self.public_ip_label.cget("text")
        loading_text = self._lang["hint_loading"]
        unavailable_text = self._lang["msg_ip_unavailable"]
        if text != loading_text and text != unavailable_text:
            self.public_ip_label.config(foreground="darkblue")

    def _on_public_ip_leave(self, event=None):
        """外网IP标签鼠标离开事件。"""
        text = self.public_ip_label.cget("text")
        loading_text = self._lang["hint_loading"]
        unavailable_text = self._lang["msg_ip_unavailable"]
        if text != loading_text and text != unavailable_text:
            self.public_ip_label.config(foreground="blue")

    def _start_ip_load_threads(self):
        """在主循环已启动后调用，异步加载公网和局域网 IP，避免子线程里 root.after 报 main thread is not in main loop。"""
        threading.Thread(target=self._load_local_ip, daemon=True).start()
        threading.Thread(target=self._load_public_ip, daemon=True).start()

    def _on_window_close(self):
        if getattr(self, "_tray_visible", False):
            self._quit_app()
            return
        if HAS_TRAY and self._app_config.get("close_to_tray"):
            if self._try_minimize_to_tray():
                return
        self._quit_app()

    def _on_minimize_to_tray_menu(self):
        if not self._try_minimize_to_tray():
            self._minimize_main_window()

    def _quit_app(self):
        """退出应用，不杀运行中的子进程（子进程独立运行）。"""
        self._task_closing = True
        if hasattr(self, '_task_queue'):
            self._task_queue.pending.clear()
        self._remove_tray_icon()
        self.root.quit()

    def _remove_tray_icon(self):
        if getattr(self, "_nid", None):
            try:
                import ctypes
                ctypes.windll.shell32.Shell_NotifyIconW(2, ctypes.byref(self._nid))
            except Exception:
                pass
            self._nid = None
        self._tray_visible = False
        if getattr(self, "_old_wndproc", None) is not None and getattr(self, "_tray_hwnd", None):
            try:
                _, set_long, _, _ = _win_user32_wndproc_api()
                set_long(self._tray_hwnd, GWL_WNDPROC, self._old_wndproc)
            except Exception:
                pass
            self._old_wndproc = None

    def _try_minimize_to_tray(self) -> bool:
        """尝试最小化到系统托盘（ctypes，无 Pillow）。成功返回 True。"""
        try:
            import ctypes
            from ctypes import wintypes
            if sys.platform != "win32" or not HAS_TRAY:
                return False
            if getattr(self, "_tray_visible", False) and getattr(self, "_nid", None):
                self.root.withdraw()
                return True

            class NID(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint), ("hWnd", ctypes.c_void_p), ("uID", ctypes.c_uint),
                    ("uFlags", ctypes.c_uint), ("uCallbackMessage", ctypes.c_uint), ("hIcon", ctypes.c_void_p),
                    ("szTip", ctypes.c_wchar * 128), ("dwState", ctypes.c_uint), ("dwStateMask", ctypes.c_uint),
                    ("szInfo", ctypes.c_wchar * 256), ("uVersion", ctypes.c_uint), ("szInfoTitle", ctypes.c_wchar * 64),
                    ("dwInfoFlags", ctypes.c_uint),
                ]

            WM_TRAYICON = 0x8001
            WM_LBUTTONUP = 0x0202
            WM_LBUTTONDBLCLK = 0x0203
            WM_RBUTTONUP = 0x0205

            hwnd = _win_toplevel_hwnd(self.root)

            hicon = 0
            try:
                ico = os.path.join(os.path.dirname(get_app_path()), "tray_icon_5728293646040626821.ico")
                if getattr(sys, "frozen", False):
                    ico = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "tray_icon_5728293646040626821.ico")
                if os.path.isfile(ico):
                    hicon = ctypes.windll.user32.LoadImageW(None, ico, 1, 0, 0, 0x00000010 | 0x00000040)
            except Exception:
                hicon = 0
            if not hicon:
                hicon = ctypes.windll.shell32.ExtractIconW(0, "shell32.dll", 0) or 0

            WNDPROC, set_long, get_long, call_proc = _win_user32_wndproc_api()

            def _wndproc(h, msg, wparam, lparam):
                try:
                    if msg == WM_DROPFILES:
                        paths = _win_hdrop_paths(wparam)
                        if paths:
                            self._enqueue_drop_files(paths)
                        return 0
                    if msg == WM_TRAYICON:
                        lp = int(lparam) & 0xFFFF
                        if lp == WM_RBUTTONUP:
                            self.root.after(0, self._show_tray_menu)
                            return 0
                        if lp in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                            self.root.after(0, self._show_from_tray)
                            return 0
                except Exception:
                    pass
                try:
                    return call_proc(self._old_wndproc, h, msg, wparam, lparam)
                except Exception:
                    return 0

            self._tray_wndproc_ref = WNDPROC(_wndproc)  # 防 GC
            self._old_wndproc = get_long(hwnd, GWL_WNDPROC)
            set_long(hwnd, GWL_WNDPROC, ctypes.cast(self._tray_wndproc_ref, ctypes.c_void_p))
            self._tray_hwnd = hwnd

            nid = NID()
            nid.cbSize = ctypes.sizeof(NID)
            nid.hWnd = hwnd
            nid.uID = 1
            nid.uFlags = 0x07  # NIF_MESSAGE|NIF_ICON|NIF_TIP
            nid.uCallbackMessage = WM_TRAYICON
            nid.hIcon = hicon
            nid.szTip = "Batch Script Manager"[:127]
            if ctypes.windll.shell32.Shell_NotifyIconW(0, ctypes.byref(nid)):
                self._nid = nid
                self.root.withdraw()
                self._tray_visible = True
                return True
            self._remove_tray_icon()
        except Exception:
            pass
        return False

    def _show_from_tray(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self._remove_tray_icon()
        except Exception:
            pass

    def _show_tray_menu(self):
        """托盘右键：显示 / 全部停止 / 退出。"""
        try:
            import ctypes
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label=self._lang.get("tray_show", "显示主窗口"), command=self._show_from_tray)
            menu.add_command(label=self._lang.get("tray_stop_all", "全部停止"), command=lambda: (self._show_from_tray(), self._on_stop_all()))
            menu.add_separator()
            menu.add_command(label=self._lang.get("tray_quit", "退出"), command=self._quit_app)
            try:
                menu.tk_popup(pt.x, pt.y)
            finally:
                menu.grab_release()
        except Exception:
            self._show_from_tray()

    def _on_restart_self(self):
        """重启管理器自身：启动新进程后退出当前进程。运行中的脚本不停止，重启后自动接管。"""
        if not messagebox.askyesno(self._lang["btn_restart_self"],
                                    self._lang["msg_restart_self_confirm"]):
            return
        # 先保存配置
        self._save_config()
        # 启动新进程
        try:
            import subprocess
            if getattr(sys, 'frozen', False):
                # PyInstaller 打包的 exe
                subprocess.Popen([sys.executable], env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})
            else:
                # Python 脚本
                subprocess.Popen([sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:])
        except Exception as e:
            messagebox.showerror(self._lang["btn_restart_self"], str(e))
            return
        # 移除托盘图标并退出（不杀子进程，让运行中的脚本继续）
        if getattr(self, '_nid', None):
            try:
                import ctypes; ctypes.windll.shell32.Shell_NotifyIconW(2, ctypes.byref(self._nid))
            except Exception: pass
            self._nid = None
        self.root.quit()

    def _on_system_shutdown(self):
        if not messagebox.askyesno(self._lang["btn_shutdown"], self._lang["msg_shutdown_confirm"]):
            return
        self._save_config()
        self._on_stop_all(skip_whitelist=True)
        if not system_power_action("shutdown"):
            messagebox.showerror(self._lang["msg_error"], self._lang["status_power_action_failed"] % self._lang["btn_shutdown"])

    def _on_system_reboot(self):
        if not messagebox.askyesno(self._lang["btn_reboot"], self._lang["msg_reboot_confirm"]):
            return
        self._save_config()
        self._on_stop_all(skip_whitelist=True)
        if not system_power_action("reboot"):
            messagebox.showerror(self._lang["msg_error"], self._lang["status_power_action_failed"] % self._lang["btn_reboot"])

    def run(self):
        self._init_task_features()
        if self._should_start_minimized():
            self.root.after(400, self._minimize_main_window)
        self.root.after(0, self._start_ip_load_threads)  # 主循环启动后立即调度，再启动 IP 加载线程
        self._launch_on_start_retry_scheduled = False  # 用于第二轮自动启动重试（仅一次）
        self.root.after(400, self._do_launch_on_start_items)  # 启动勾选了「启动本工具时自动启动」的任务（400ms 后、在后台线程执行）
        self.root.after(1500, self._do_launch_api_on_start)  # 自动启动 API（若已勾选）
        self.root.after(600, self._update_api_status_label)
        self.root.mainloop()


def cli_main(argv=None) -> int:
    """CLI 入口：见 WindowsBatchScriptManager_api_server 或 命令行 --list --start 等。
    argv 为参数列表（不含程序名）；None 时用 sys.argv。供 API 进程内调用。"""
    def _items():
        raw = load_config()
        out = []
        for i, r in enumerate(migrate_items(raw)):
            path = r.get("path", "")
            stype = r.get("script_type") or ("python" if r.get("is_python") else (get_script_type(path) or "batch"))
            interp = r.get("interpreter") or r.get("python_exe") or ""
            item = config_item_to_save(r)
            item.update(id=r["id"], name=r.get("name", "未命名"), script_type=stype,
                        interpreter=interp, is_python=stype == "python",
                        python_exe=interp if stype == "python" else "")
            out.append(item)
        return out

    def _running_map(force=False):
        now = time.time()
        cache = getattr(cli_main, "_run_map", None)
        ts = getattr(cli_main, "_run_map_t", 0.0)
        if not force and cache is not None and (now - ts) < 2.0:
            return dict(cache)
        m = {}
        if HAS_PSUTIL:
            for x in scan_running_script_processes():
                sp = (x.get("script_path") or "").strip()
                if sp:
                    m[os.path.normcase(os.path.abspath(sp))] = x.get("pid")
        if not force:
            cli_main._run_map = m
            cli_main._run_map_t = now
        return m

    def _find(items, id_or_name):
        id_or_name = (id_or_name or "").strip()
        if not id_or_name:
            return None
        try:
            sid = int(id_or_name)
            for it in items:
                if it.get("id") == sid:
                    return it
            return None
        except ValueError:
            pass
        nl = id_or_name.lower()
        for it in items:
            if nl in (it.get("name") or "").lower():
                return it
        return None

    def _log_path(it):
        if not it.get("log_output"):
            return None
        stype = it.get("script_type") or get_script_type(it.get("path", "")) or "batch"
        if stype not in ("python", "node", "powershell"):
            return None
        log_dir = CONFIG_PATH.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        base = "".join(c if c.isalnum() or c in "._-" else "_" for c in (it.get("name") or "out"))[:50]
        return str(log_dir / f"{base}_{int(time.time())}.log")

    def _to_save(items):
        return [config_item_to_save(it) for it in items]

    import argparse
    ap = argparse.ArgumentParser(description="Batch Script Manager CLI")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--config-path", action="store_true")
    ap.add_argument("--list", "-l", action="store_true")
    ap.add_argument("--status", metavar="ID", nargs="?", const="")
    ap.add_argument("--start", metavar="ID")
    ap.add_argument("--stop", metavar="ID")
    ap.add_argument("--restart", metavar="ID")
    ap.add_argument("--start-all", action="store_true")
    ap.add_argument("--stop-all", action="store_true")
    ap.add_argument("--restart-all", action="store_true")
    ap.add_argument("--shutdown", action="store_true", help="Stop all managed scripts then shut down Windows")
    ap.add_argument("--reboot", action="store_true", help="Stop all managed scripts then reboot Windows")
    ap.add_argument("--force", action="store_true", help="Force close apps when shutting down/rebooting (/f)")
    ap.add_argument("--add", action="store_true")
    ap.add_argument("--name", default="")
    ap.add_argument("--path", default="")
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--group", default=None)
    ap.add_argument("--script-type", default="")
    ap.add_argument("--interpreter", default=None)
    ap.add_argument("--auto-restart", action="store_true")
    ap.add_argument("--no-auto-restart", action="store_true")
    ap.add_argument("--log-output", action="store_true")
    ap.add_argument("--no-log-output", action="store_true")
    ap.add_argument("--success-exit-codes", default=None)
    ap.add_argument("--on-success", default=None)
    ap.add_argument("--on-failure", default=None)
    ap.add_argument("--depends-on", default=None, help="Comma-separated dependency IDs")
    ap.add_argument("--delay-after-dep", type=int, default=None, help="Seconds to wait after deps start")
    ap.add_argument("--args", default=None, help="Extra arguments for script")
    ap.add_argument("--env-vars", default=None, help="Environment variables (one KEY=VALUE per line)")
    # 定时任务参数
    ap.add_argument("--schedule-enabled", type=lambda x: x.lower() in ("true", "1", "yes"), default=None, help="Enable schedule (true/false)")
    ap.add_argument("--schedule-action", default=None, choices=["start", "restart", "stop", "shutdown", "reboot"], help="Schedule action")
    ap.add_argument("--schedule-type", default=None, choices=["daily", "hourly", "minutely", "weekly", "monthly", "every_hours", "quarterly", "yearly", "once", "weekday"], help="Schedule type")
    ap.add_argument("--schedule-time", default=None, help="Schedule time (HH:MM, comma-separated for multiple)")
    ap.add_argument("--schedule-interval", type=int, default=None, help="Schedule interval (for minutely/every_hours)")
    ap.add_argument("--schedule-weekday", type=int, default=None, help="Weekday (0=Mon..6=Sun)")
    ap.add_argument("--schedule-day", type=int, default=None, help="Day of month")
    ap.add_argument("--schedule-once-date", default=None, help="Once date (YYYY-MM-DD)")
    ap.add_argument("--delete", metavar="ID")
    ap.add_argument("--update", metavar="ID")
    ap.add_argument("--export", metavar="F", nargs="?", const="")
    ap.add_argument("--import", dest="import_", metavar="F")
    ap.add_argument("--replace", action="store_true")
    args = ap.parse_args(argv)
    if args.json and argv is None:
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and hasattr(stream, "reconfigure") and not stream.isatty():
                stream.reconfigure(encoding="utf-8")

    if args.config_path:
        print(CONFIG_PATH.resolve())
        return 0

    items = _items()
    # --add/--update/--export/--import 只动配置，不必扫全系统进程
    need_procs = bool(
        args.list or args.start or args.stop or args.restart
        or args.start_all or args.stop_all or args.restart_all
        or args.delete or args.shutdown or args.reboot
        or args.status is not None
    )
    force_scan = bool(
        args.start or args.stop or args.restart
        or args.start_all or args.stop_all or args.restart_all
        or args.delete or args.shutdown or args.reboot
    )
    run_map = _running_map(force=force_scan) if need_procs else {}
    if force_scan:
        cli_main._run_map = None
        cli_main._run_map_t = 0.0

    if args.export is not None:
        data = {"config_path": str(CONFIG_PATH.resolve()), "items": items}
        out = json.dumps(data, ensure_ascii=False, indent=2)
        if args.export:
            try:
                Path(args.export).write_text(out, encoding="utf-8")
                if not args.json:
                    print("Exported to", args.export)
                return 0
            except Exception as e:
                print(str(e), file=sys.stderr)
                return 1
        print(out)
        return 0

    if args.import_:
        path = Path(args.import_)
        if not path.exists():
            print("File not found:", path, file=sys.stderr)
            return 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            new_items = data.get("items", data) if isinstance(data, dict) else data
            if not isinstance(new_items, list):
                return 1
        except Exception as e:
            print("Import error:", e, file=sys.stderr)
            return 1
        try:
            merged = merge_task_items(load_config(), new_items, replace=args.replace)
        except (ValueError, TypeError, KeyError) as exc:
            print('Import error:', exc, file=sys.stderr)
            return 1
        if not save_config([config_item_to_save(i) for i in merged]):
            print('Failed to save configuration', file=sys.stderr)
            return 1
        return 0

    if args.delete:
        it = _find(items, args.delete)
        if not it:
            print("Not found:", args.delete, file=sys.stderr)
            return 1
        abs_path = resolve_script_file_path(it)
        pid = run_map.get(os.path.normcase(abs_path))
        if pid:
            for px in get_process_tree_pids(pid):
                kill_process(px)
        new_items = [x for x in items if x.get("id") != it.get("id")]
        for item in new_items:
            for field in ('on_success', 'on_failure'):
                item[field] = [ident for ident in link_ids(item.get(field)) if ident != it['id']]
            item['depends_on'] = ','.join(str(ident) for ident in link_ids(item.get('depends_on')) if ident != it['id'])
        if not save_config(_to_save(new_items)):
            print("Failed to save configuration", file=sys.stderr)
            return 1
        return 0

    if args.update:
        it = _find(items, args.update)
        if not it:
            print("Not found:", args.update, file=sys.stderr)
            return 1
        updated = dict(it)
        if (args.name or "").strip() != "":
            updated["name"] = (args.name or "").strip()
        if (args.path or "").strip() != "":
            updated["path"] = (args.path or "").strip()
        if args.work_dir is not None:
            updated["work_dir"] = (args.work_dir or "").strip()
        if args.group is not None:
            updated["group"] = (args.group or "").strip()
        if (args.script_type or "").strip() != "":
            updated["script_type"] = (args.script_type or "").strip().lower() or get_script_type(updated.get("path", "")) or "batch"
        if args.interpreter is not None:
            updated["interpreter"] = (args.interpreter or "").strip()
        updated["is_python"] = updated.get("script_type", "batch") == "python"
        updated["python_exe"] = updated.get("interpreter", "") if updated.get("script_type") == "python" else ""
        if args.auto_restart:
            updated["auto_restart"] = True
        if args.no_auto_restart:
            updated["auto_restart"] = False
        if args.log_output:
            updated["log_output"] = True
        if args.no_log_output:
            updated["log_output"] = False
        if args.depends_on is not None:
            updated["depends_on"] = args.depends_on
        if args.delay_after_dep is not None:
            updated["delay_after_dep"] = args.delay_after_dep
        if args.args is not None:
            updated["args"] = args.args
        if args.env_vars is not None:
            updated["env_vars"] = args.env_vars
        # 定时任务参数
        if args.schedule_enabled is not None:
            updated["schedule_enabled"] = args.schedule_enabled
        if args.schedule_action is not None:
            updated["schedule_action"] = args.schedule_action
        if args.schedule_type is not None:
            updated["schedule_type"] = args.schedule_type
        if args.schedule_time is not None:
            updated["schedule_time"] = args.schedule_time
        if args.schedule_interval is not None:
            updated["schedule_interval"] = args.schedule_interval
        if args.schedule_weekday is not None:
            updated["schedule_weekday"] = args.schedule_weekday
        if args.schedule_day is not None:
            updated["schedule_day"] = args.schedule_day
        if args.schedule_once_date is not None:
            updated["schedule_once_date"] = args.schedule_once_date
        try:
            if args.success_exit_codes is not None:
                updated["success_exit_codes"] = parse_codes(args.success_exit_codes)
            if args.on_success is not None:
                updated["on_success"] = link_ids(args.on_success)
            if args.on_failure is not None:
                updated["on_failure"] = link_ids(args.on_failure)
            validate_links([updated if x.get("id") == it.get("id") else x for x in items])
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        new_list = [updated if x.get("id") == it.get("id") else x for x in items]
        if not save_config(_to_save(new_list)):
            print("Failed to save configuration", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"ok": True, "id": it["id"], "name": updated.get("name")}, ensure_ascii=False))
        else:
            print("Updated id=%s" % it["id"])
        return 0

    if args.add:
        name = (args.name or "").strip() or "未命名"
        path = (args.path or "").strip()
        if not path:
            print("--add requires --path", file=sys.stderr)
            return 1
        stype = (args.script_type or "").strip().lower() or get_script_type(path) or "batch"
        interp = (args.interpreter or "").strip() if args.interpreter is not None else ""
        new_item = {"name": name, "path": path, "work_dir": (args.work_dir or "").strip() if args.work_dir is not None else "", "group": (args.group or "").strip() if args.group is not None else "", "script_type": stype, "interpreter": interp, "is_python": stype == "python", "python_exe": interp if stype == "python" else "", "auto_restart": bool(args.auto_restart), "launch_on_app_start": False, "log_output": bool(args.log_output), "schedule_enabled": args.schedule_enabled if args.schedule_enabled is not None else False, "schedule_action": args.schedule_action or "start", "schedule_type": args.schedule_type or "daily", "schedule_time": args.schedule_time or "09:00", "schedule_interval": args.schedule_interval if args.schedule_interval is not None else 5, "schedule_weekday": args.schedule_weekday if args.schedule_weekday is not None else 0, "schedule_day": args.schedule_day if args.schedule_day is not None else 1, "schedule_quarter": "q1", "schedule_month": 1, "schedule_once_date": args.schedule_once_date or "", "depends_on": args.depends_on or "", "delay_after_dep": args.delay_after_dep or 0, "args": args.args or "", "env_vars": (args.env_vars or "") if args.env_vars is not None else ""}
        new_id = next_item_id(items)
        try:
            new_item.update(success_exit_codes=parse_codes(args.success_exit_codes or "0"), on_success=link_ids(args.on_success), on_failure=link_ids(args.on_failure))
            validate_links(items + [{**new_item, "id": new_id}])
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        items.append({**new_item, "id": new_id})
        if not save_config(_to_save(items)):
            print("Failed to save configuration", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"ok": True, "id": new_id, "name": name}, ensure_ascii=False))
        else:
            print("Added id=%s" % new_id)
        return 0

    if args.list:
        out = []
        for it in items:
            abs_path = resolve_script_file_path(it)
            pid = run_map.get(os.path.normcase(abs_path))
            out.append({**{key: it[key] for key in FEATURE_FIELDS if key in it}, "id": it["id"], "name": it.get("name", ""), "group": it.get("group", ""), "path": it.get("path", ""), "running": pid is not None, "pid": pid, "script_type": it.get("script_type", "batch"), "schedule_enabled": it.get("schedule_enabled", False), "schedule_action": it.get("schedule_action", "start"), "schedule_type": it.get("schedule_type", "daily"), "schedule_time": it.get("schedule_time", "09:00"), "schedule_interval": it.get("schedule_interval", 5), "schedule_weekday": it.get("schedule_weekday", 0), "schedule_day": it.get("schedule_day", 1), "schedule_once_date": it.get("schedule_once_date", "")})
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            for row in out:
                print("%s  %s  %s  %s" % (row["id"], row["name"], row["path"], "running pid=%s" % row["pid"] if row["running"] else "stopped"))
        return 0

    if args.status is not None:
        if args.status.strip():
            it = _find(items, args.status)
            if not it:
                print("Not found:", args.status, file=sys.stderr)
                return 1
            items = [it]
        out = []
        for it in items:
            abs_path = resolve_script_file_path(it)
            pid = run_map.get(os.path.normcase(abs_path))
            out.append({"id": it["id"], "name": it.get("name", ""), "path": it.get("path", ""), "running": pid is not None, "pid": pid})
        if args.json:
            print(json.dumps(out[0] if len(out) == 1 else out, ensure_ascii=False, indent=2))
        else:
            for row in out:
                print("id=%s name=%s running=%s pid=%s" % (row["id"], row["name"], row["running"], row["pid"]))
        return 0

    if args.start:
        it = _find(items, args.start)
        if not it:
            print("Not found:", args.start, file=sys.stderr)
            return 1
        abs_path = resolve_script_file_path(it)
        if run_map.get(os.path.normcase(abs_path)):
            if args.json:
                print(json.dumps({"ok": True, "id": it["id"], "message": "already running", "pid": run_map.get(os.path.normcase(abs_path))}, ensure_ascii=False))
            else:
                print("Already running id=%s pid=%s" % (it["id"], run_map.get(os.path.normcase(abs_path))))
            return 0
        p = start_script(it.get("path", ""), effective_work_dir(it), it.get("script_type", "batch"), it.get("interpreter") or None, log_path=_log_path(it), extra_args=it.get("args") or None, env_vars=environment_vars(it) or None, http_method=it.get("http_method"), http_headers=it.get("http_headers"))
        if not p:
            print("Start failed: " + (get_last_start_error() or "could not launch task"), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"ok": True, "id": it["id"], "name": it.get("name"), "pid": p.pid}, ensure_ascii=False))
        else:
            print("Started id=%s pid=%s" % (it["id"], p.pid))
        return 0

    if args.stop:
        it = _find(items, args.stop)
        if not it:
            print("Not found:", args.stop, file=sys.stderr)
            return 1
        abs_path = resolve_script_file_path(it)
        pid = run_map.get(os.path.normcase(abs_path))
        if not pid:
            if args.json:
                print(json.dumps({"ok": True, "id": it["id"], "message": "not running"}, ensure_ascii=False))
            return 0
        for px in get_process_tree_pids(pid):
            kill_process(px)
        if args.json:
            print(json.dumps({"ok": True, "id": it["id"], "stopped": pid}, ensure_ascii=False))
        else:
            print("Stopped id=%s" % it["id"])
        return 0

    if args.restart:
        it = _find(items, args.restart)
        if not it:
            print("Not found:", args.restart, file=sys.stderr)
            return 1
        abs_path = resolve_script_file_path(it)
        pid = run_map.get(os.path.normcase(abs_path))
        if pid:
            for px in get_process_tree_pids(pid):
                kill_process(px)
            time.sleep(0.5)
        p = start_script(it.get("path", ""), effective_work_dir(it), it.get("script_type", "batch"), it.get("interpreter") or None, log_path=_log_path(it), extra_args=it.get("args") or None, env_vars=environment_vars(it) or None, http_method=it.get("http_method"), http_headers=it.get("http_headers"))
        if not p:
            print("Restart failed", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"ok": True, "id": it["id"], "pid": p.pid}, ensure_ascii=False))
        else:
            print("Restarted id=%s pid=%s" % (it["id"], p.pid))
        return 0

    if args.start_all:
        started = []
        for it in items:
            abs_path = resolve_script_file_path(it)
            if run_map.get(os.path.normcase(abs_path)):
                continue
            p = start_script(it.get("path", ""), effective_work_dir(it), it.get("script_type", "batch"), it.get("interpreter") or None, log_path=_log_path(it), extra_args=it.get("args") or None, env_vars=environment_vars(it) or None, http_method=it.get("http_method"), http_headers=it.get("http_headers"))
            if p:
                started.append({"id": it["id"], "name": it.get("name"), "pid": p.pid})
                run_map[os.path.normcase(abs_path)] = p.pid
        if args.json:
            print(json.dumps({"ok": True, "started": started}, ensure_ascii=False))
        else:
            print("Started", len(started), "script(s)")
            return 0

    if args.stop_all:
        stopped = []
        for it in items:
            abs_path = resolve_script_file_path(it)
            pid = run_map.get(os.path.normcase(abs_path))
            if not pid:
                continue
            for px in get_process_tree_pids(pid):
                kill_process(px)
            stopped.append({"id": it["id"], "name": it.get("name"), "pid": pid})
        if args.json:
            print(json.dumps({"ok": True, "stopped": stopped}, ensure_ascii=False))
        else:
            print("Stopped", len(stopped), "script(s)")
        return 0

    if args.restart_all:
        stopped = []
        skipped_whitelist = []
        for it in items:
            abs_path = resolve_script_file_path(it)
            if is_service_whitelisted(it.get("path", "") or abs_path):
                skipped_whitelist.append({"id": it["id"], "name": it.get("name"), "path": it.get("path", "")})
                continue
            pid = run_map.get(os.path.normcase(abs_path))
            if not pid:
                continue
            for px in get_process_tree_pids(pid):
                kill_process(px)
            stopped.append({"id": it["id"], "name": it.get("name"), "pid": pid})
        time.sleep(0.5)
        run_map = _running_map()
        started = []
        for it in items:
            abs_path = resolve_script_file_path(it)
            if is_service_whitelisted(it.get("path", "") or abs_path):
                continue
            if run_map.get(os.path.normcase(abs_path)):
                continue
            p = start_script(it.get("path", ""), effective_work_dir(it), it.get("script_type", "batch"), it.get("interpreter") or None, log_path=_log_path(it), extra_args=it.get("args") or None, env_vars=environment_vars(it) or None, http_method=it.get("http_method"), http_headers=it.get("http_headers"))
            if p:
                started.append({"id": it["id"], "name": it.get("name"), "pid": p.pid})
                run_map[os.path.normcase(abs_path)] = p.pid
        if args.json:
            print(json.dumps({"ok": True, "stopped": stopped, "started": started, "skipped_whitelist": skipped_whitelist}, ensure_ascii=False))
        else:
            print("Restart-all: stopped %s, started %s, whitelist skipped %s" % (len(stopped), len(started), len(skipped_whitelist)))
        return 0

    if args.shutdown or args.reboot:
        stopped = []
        skipped_whitelist = []
        for it in items:
            abs_path = resolve_script_file_path(it)
            if is_service_whitelisted(it.get("path", "") or abs_path):
                skipped_whitelist.append({"id": it["id"], "name": it.get("name"), "path": it.get("path", "")})
                continue
            pid = run_map.get(os.path.normcase(abs_path))
            if not pid:
                continue
            for px in get_process_tree_pids(pid):
                kill_process(px)
            stopped.append({"id": it["id"], "name": it.get("name"), "pid": pid})
        action = "shutdown" if args.shutdown else "reboot"
        if not system_power_action(action, force=args.force):
            print("Power action failed:", action, file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"ok": True, "action": action, "stopped": stopped, "skipped_whitelist": skipped_whitelist, "force": bool(args.force)}, ensure_ascii=False))
        else:
            print("Power action:", action, "stopped", len(stopped), "script(s)", "whitelist skipped", len(skipped_whitelist))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    def _pop_gui_flag(flag: str) -> bool:
        found = False
        while flag in sys.argv:
            sys.argv.remove(flag)
            found = True
        return found

    start_minimized_cli = _pop_gui_flag("--minimized")
    if len(sys.argv) >= 2:
        first = (sys.argv[1] or "").strip().lower()
        if first in ("--list", "-l", "--status", "--start", "--stop", "--restart", "--start-all", "--stop-all", "--restart-all",
                     "--config-path", "--add", "--delete", "--update", "--export", "--import", "--replace", "--json") or first.startswith("--"):
            sys.exit(cli_main())
    BatchManagerApp(start_minimized_cli=start_minimized_cli).run()
