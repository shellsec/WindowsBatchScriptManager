# -*- coding: utf-8 -*-
"""
Linux 版脚本管理器（LinuxBatchScriptManager）
在 Linux 下统一管理 Shell、Python、Node.js 等脚本，支持启动、停止、重启、定时任务、CLI 与 GUI。
与主仓库 WindowsBatchScriptManager 功能一致，仅包含 Linux 平台实现。
"""

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import ttk, messagebox, filedialog
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import urllib.parse
from html.parser import HTMLParser

# 尝试导入 psutil，用于端口与进程信息；无则降级为 netstat/tasklist
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# 托盘功能已移除（依赖 Pillow 会使打包 exe 增大约 20MB），关闭窗口即退出
HAS_TRAY = False

# 获取实际程序路径（支持 PyInstaller 打包）
def get_app_path():
    """获取应用程序的实际路径（.py 或可执行文件）。"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包的 exe
        return os.path.abspath(sys.executable)
    else:
        # 开发环境中的 .py 文件
        return os.path.abspath(__file__)


# 跨平台：本独立项目仅 Linux，使用 linuxBatchScriptManager 下的 platform_impl
_PLATFORM = None
if sys.platform == "linux":
    try:
        from linuxBatchScriptManager import platform_impl as _PLATFORM  # noqa: F401
    except ImportError:
        pass

if sys.platform != "linux":
    print("LinuxBatchScriptManager 仅支持 Linux，请在 Linux 下运行。", file=sys.stderr)
    sys.exit(1)
if not _PLATFORM:
    print("无法加载 linuxBatchScriptManager，请确保在项目根目录运行：python3 main.py", file=sys.stderr)
    sys.exit(1)
SCRIPT_LIST_CONFIG_FILENAME = "WindowsBatchScriptManager_scripts.json"

if _PLATFORM:
    _config_dir = Path(_PLATFORM.get_config_dir(get_app_path()))
    _config_dir.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH = _config_dir / SCRIPT_LIST_CONFIG_FILENAME
    HISTORY_PATH = _config_dir / "execution_history.json"
    NOTIFICATION_CONFIG_PATH = _config_dir / "notification_config.json"
    LANGUAGE_CONFIG_PATH = _config_dir / "language_config.json"
else:
    CONFIG_PATH = Path(get_app_path()).parent / SCRIPT_LIST_CONFIG_FILENAME
    HISTORY_PATH = Path(get_app_path()).parent / "execution_history.json"
    NOTIFICATION_CONFIG_PATH = Path(get_app_path()).parent / "notification_config.json"
    LANGUAGE_CONFIG_PATH = Path(get_app_path()).parent / "language_config.json"

STARTUP_REG_NAME = "WindowsBatchScriptManager"


def _get_startup_command() -> str:
    """返回要写入「开机/登录自启」的命令。"""
    app = get_app_path()
    if _PLATFORM:
        return _PLATFORM.get_startup_command(app)
    if getattr(sys, "frozen", False):
        return app
    return f'"{sys.executable}" "{app}"'


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
    "zh_CN": {
        # 主窗口
        "window_title": "Script Manager (Linux) - github.com/shellsec/WindowsBatchScriptManager",
        "btn_add": "添加",
        "btn_edit": "编辑",
        "btn_delete": "删除",
        "btn_duplicate": "复制",
        "btn_select_all": "全选",
        "btn_select_none": "取消全选",
        "btn_start": "启动",
        "btn_stop": "停止",
        "btn_restart": "重启",
        "btn_start_all": "全部启动",
        "btn_stop_all": "全部停止",
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
        "btn_notification": "通知配置",
        "btn_stop_all_batch": "停止软件外所有批处理",
        "label_filter_group": "筛选分组:",
        "btn_clear_filter": "清除筛选",
        "label_search_keyword": "关键词:",
        "btn_locate": "定位",
        "btn_next_match": "下一个",
        "msg_search_no_match": "未找到包含「%s」的项",
        "msg_search_located": "已定位到第 %d 项",
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
        # 详情和状态
        "detail_label": "选中项：路径与工作目录",
        "status_ready_msg": "就绪 | 「自动识别」或「识别并加入」可识别系统运行中的脚本；双击编辑；Ctrl+A 全选",
        # 右键菜单
        "menu_start": "启动",
        "menu_stop": "停止",
        "menu_restart": "重启",
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
        "label_scan_result": "识别结果：",
        "label_script_path": "脚本路径:",
        "label_name": "名称:",
        "label_group": "分组:",
        "label_work_dir": "工作目录:",
        "label_interpreter": "解释器:",
        "label_auto_restart": "保活:",
        "label_log_output": "日志输出:",
        "label_schedule": "定时任务:",
        "label_schedule_time": "时间:",
        "btn_time_0": "0点",
        "btn_time_6": "早6",
        "btn_time_12": "中12",
        "btn_time_18": "18点",
        "label_schedule_action": "动作:",
        "label_schedule_enabled": "启用:",
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
        "label_select_script": "选择脚本:",
        "label_batch_group": "分组:",
        "label_batch_interpreter": "解释器:",
        "label_batch_schedule": "定时任务:",
        "label_batch_schedule_detail": "定时内容（可选，启用时应用）:",
        "hint_batch_schedule_detail": "类型/时间/星期/动作 留空或「不修改」则不改该项",
        "label_batch_launch_on_start": "启动时自动启动:",
        "label_batch_keepalive": "保活:",
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
        "schedule_action_restart": "重启",
        "schedule_action_stop": "停止",
        "schedule_type_daily": "每天",
        "schedule_type_hourly": "每小时",
        "schedule_type_minutely": "每分钟",
        "schedule_type_weekly": "每周",
        "schedule_type_monthly": "每月",
        "schedule_type_every_hours": "每N小时执行",
        "label_schedule_day": "几号:",
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
        "status_refresh_resumed": "已恢复定时刷新（%d 秒）",
        "menu_settings": "设置",
        "menu_refresh_interval": "刷新间隔",
        "refresh_interval_2s": "2 秒",
        "refresh_interval_4s": "4 秒",
        "refresh_interval_8s": "8 秒",
        "status_refresh_interval_set": "刷新间隔已设为 %d 秒",
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
    },
    "en_US": {
        # Main window
        "window_title": "Script Manager (Linux)",
        "btn_add": "Add",
        "btn_edit": "Edit",
        "btn_delete": "Delete",
        "btn_duplicate": "Duplicate",
        "btn_select_all": "Select All",
        "btn_select_none": "Deselect All",
        "btn_start": "Start",
        "btn_stop": "Stop",
        "btn_restart": "Restart",
        "btn_start_all": "Start All",
        "btn_stop_all": "Stop All",
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
        "btn_notification": "Notifications",
        "btn_stop_all_batch": "Stop All Batch",
        "label_filter_group": "Filter Group:",
        "btn_clear_filter": "Clear Filter",
        "label_search_keyword": "Keyword:",
        "btn_locate": "Locate",
        "btn_next_match": "Next",
        "msg_search_no_match": "No item containing '%s'",
        "msg_search_located": "Located at item %d",
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
        # Detail and status
        "detail_label": "Selected: Path & Work Directory",
        "status_ready_msg": "Ready | Use 'Scan Running' or 'Scan & Add' to identify running scripts; Double-click to edit; Ctrl+A to select all",
        # Right-click menu
        "menu_start": "Start",
        "menu_stop": "Stop",
        "menu_restart": "Restart",
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
        "label_scan_result": "Scan Result:",
        "label_script_path": "Script Path:",
        "label_name": "Name:",
        "label_group": "Group:",
        "label_work_dir": "Work Directory:",
        "label_interpreter": "Interpreter:",
        "label_auto_restart": "Keep Alive:",
        "label_log_output": "Log Output:",
        "label_schedule": "Schedule:",
        "label_schedule_time": "Time:",
        "btn_time_0": "0:00",
        "btn_time_6": "6am",
        "btn_time_12": "12pm",
        "btn_time_18": "6pm",
        "label_schedule_action": "Action:",
        "label_schedule_enabled": "Enabled:",
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
        "label_select_script": "Select Script:",
        "label_batch_group": "Group:",
        "label_batch_interpreter": "Interpreter:",
        "label_batch_schedule": "Schedule:",
        "label_batch_schedule_detail": "Schedule detail (optional, when enabling):",
        "hint_batch_schedule_detail": "Leave type/time/weekday/action empty or 'No Change' to keep",
        "label_batch_launch_on_start": "Launch when app starts:",
        "label_batch_keepalive": "Keep Alive:",
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
        "check_schedule_enabled": "Enable Schedule",
        "schedule_action_start": "Start",
        "schedule_action_restart": "Restart",
        "schedule_action_stop": "Stop",
        "schedule_type_daily": "Daily",
        "schedule_type_hourly": "Hourly",
        "schedule_type_minutely": "Minutely",
        "schedule_type_weekly": "Weekly",
        "schedule_type_monthly": "Monthly",
        "schedule_type_every_hours": "Every N hours",
        "label_schedule_day": "Day:",
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
        "status_refresh_resumed": "Resumed refresh (%d sec)",
        "status_refresh_interval_set": "Refresh interval set to %d sec",
        "menu_settings": "Settings",
        "menu_refresh_interval": "Refresh interval",
        "refresh_interval_2s": "2 sec",
        "refresh_interval_4s": "4 sec",
        "refresh_interval_8s": "8 sec",
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
    }
}

def _detect_system_language() -> str:
    """根据系统环境检测首选语言（用于首次启动无配置文件时）。Windows/Mac/Linux 通用。"""
    try:
        # 环境变量：LANG / LC_ALL / LC_MESSAGES（常见于 Linux/Mac）
        for key in ("LANG", "LC_ALL", "LC_MESSAGES"):
            val = (os.environ.get(key) or "").strip().lower()
            if val.startswith("en"):
                return "en_US"
            if val.startswith("zh"):
                return "zh_CN"
        # locale 模块（部分环境可用）
        import locale
        code, _ = locale.getdefaultlocale() or (None, None)
        if code and code.lower().startswith("en"):
            return "en_US"
        if code and code.lower().startswith("zh"):
            return "zh_CN"
    except Exception:
        pass
    return "zh_CN"


def load_language() -> str:
    """加载语言偏好。有配置文件则用配置；无则根据系统语言自适应（英文系统用英文，否则中文）。"""
    if LANGUAGE_CONFIG_PATH.exists():
        try:
            with open(LANGUAGE_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
                lang = config.get("language", "zh_CN")
                if lang in LANGUAGES:
                    return lang
        except Exception:
            pass
    return _detect_system_language()

def save_language(language: str) -> None:
    """保存语言偏好。"""
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
    """根据文件路径返回脚本类型：python, node, powershell, vbs, jscript, batch, shell, wsf, unknown"""
    if not path:
        return "unknown"
    ext = os.path.splitext(path)[1].lower()
    for stype, exts in SCRIPT_TYPES.items():
        if ext in exts:
            # .js 优先判断为 Node.js（更常见），除非明确是 JScript
            if ext == ".js" and stype == "jscript":
                continue
            return stype
    return "unknown"


# ---------------------------------------------------------------------------
# 配置与数据
# ---------------------------------------------------------------------------

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
            return json.load(f)
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
    tmp_path = CONFIG_PATH.parent / (CONFIG_PATH.name + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
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


def save_history(history: dict) -> None:
    """保存执行历史记录。"""
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


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
    try:
        NOTIFICATION_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(NOTIFICATION_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


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
        return {"name": p.name(), "exe": getattr(p, "exe", "") or "", "cmdline": " ".join(p.cmdline() or [])[:200]}
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
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def _extract_script_path(cmdline: list, exe_lower: str, cwd: str = "") -> Optional[str]:
    r"""
    从进程命令行中解析出脚本路径。
    支持：cmd /c "D:\path\a.bat"、python x.py、node app.js、powershell script.ps1 等。
    """
    if not cmdline or len(cmdline) < 2:
        return None
    def norm(s: str) -> str:
        return (s or "").strip().strip('"').strip("'")
    
    # 所有支持的扩展名
    all_exts = tuple(sum(SCRIPT_TYPES.values(), []))
    
    for arg in cmdline[1:]:
        a = norm(arg)
        if not a:
            continue
        # 跳过常见参数
        if a.lower() in ("/c", "/k", "/x", "-file", "-f", "-executionpolicy", "-bypass", "-nologo", "//nologo"):
            continue
        if a.startswith("-") and not a.lower().endswith(all_exts):
            continue
        # 检查是否是脚本文件
        if a.lower().endswith(all_exts):
            if os.path.isfile(a):
                return os.path.abspath(a)
            if re.search(r"[\\/:]", a):
                return a
            if cwd and os.path.isfile(os.path.join(cwd, a)):
                return os.path.normpath(os.path.abspath(os.path.join(cwd, a)))
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

def start_script(
    path: str,
    work_dir: Optional[str],
    script_type: str,
    interpreter: Optional[str] = None,
    log_path: Optional[str] = None,
) -> Optional[subprocess.Popen]:
    # Mac/Linux 使用各平台 platform_impl
    if _PLATFORM:
        return _PLATFORM.start_script(
            path, work_dir, script_type, interpreter, log_path,
            get_app_path(), get_script_type,
        )
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
    
    # 防止启动程序本身（会启动新的管理器实例）
    app_path = get_app_path()
    if os.path.normcase(abs_path) == os.path.normcase(app_path):
        # 不启动程序本身
        return None
    
    script_type = script_type or get_script_type(path)
    use_log = bool(log_path)
    
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
            if getattr(sys, 'frozen', False):
                # 打包后的 exe：尝试查找系统 Python
                # 优先尝试 'python' 命令，如果失败则尝试 'py'
                import shutil
                python_exe = shutil.which("python") or shutil.which("py")
                if python_exe:
                    args = [python_exe, abs_path]
                else:
                    # 如果找不到 Python，提示错误
                    return None
            else:
                # 开发环境：使用当前 Python 解释器
                args = [sys.executable, abs_path]
    elif script_type == "node":
        exe = (interpreter or "").strip() or "node"
        args = [exe, abs_path]
    elif script_type == "powershell":
        args = ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", abs_path]
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
        args = ["cmd.exe", "/k", abs_path]
    
    # 记录输出到文件
    if use_log:
        try:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            lf = open(log_path, "w", encoding="utf-8", errors="replace")
            p = subprocess.Popen(
                args,
                cwd=cwd or None,
                creationflags=0,
                stdout=lf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            lf.close()
            return p
        except Exception:
            return None
    
    # 独立控制台运行
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        p = subprocess.Popen(
            args,
            cwd=cwd or None,
            creationflags=creationflags,
        )
        return p
    except Exception:
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

    def __init__(self, parent, mode: str, initial: dict):
        super().__init__(parent)
        self.mode = mode
        self.result = None
        # 获取语言字典：从 BatchManagerApp 实例获取
        # parent 是 root (Tk)，需要找到 BatchManagerApp 实例
        app_instance = None
        # 获取语言字典：直接从保存的语言配置读取（确保使用最新语言）
        current_lang = load_language()
        if current_lang not in LANGUAGES:
            current_lang = "zh_CN"
        self._lang = LANGUAGES[current_lang]
        self.title(self._lang["dialog_edit"] if mode == "edit" else self._lang["dialog_add_edit"])
        self.geometry("500x480")
        self.transient(parent)
        self.grab_set()

        self.var_name = tk.StringVar(value=initial.get("name", ""))
        self.var_path = tk.StringVar(value=initial.get("path", ""))
        self.var_work = tk.StringVar(value=initial.get("work_dir", ""))
        self.var_group = tk.StringVar(value=initial.get("group", ""))
        self.var_interpreter = tk.StringVar(value=initial.get("interpreter", "") or initial.get("python_exe", ""))
        self.var_auto = tk.BooleanVar(value=bool(initial.get("auto_restart", False)))
        self.var_schedule_enabled = tk.BooleanVar(value=bool(initial.get("schedule_enabled", False)))
        # 类型转换：英文 -> 当前语言
        schedule_type_en = initial.get("schedule_type", "daily")
        schedule_type_map = {
            "daily": self._lang["schedule_type_daily"],
            "hourly": self._lang["schedule_type_hourly"],
            "minutely": self._lang["schedule_type_minutely"],
            "weekly": self._lang["schedule_type_weekly"],
            "monthly": self._lang["schedule_type_monthly"],
        }
        # 反向映射：中文/英文 -> 英文
        reverse_type_map = {
            self._lang["schedule_type_daily"]: "daily",
            self._lang["schedule_type_hourly"]: "hourly",
            self._lang["schedule_type_minutely"]: "minutely",
            self._lang["schedule_type_weekly"]: "weekly",
            self._lang["schedule_type_monthly"]: "monthly",
            "每天": "daily", "每小时": "hourly", "每分钟": "minutely", "每周": "weekly", "每月": "monthly",
            "Daily": "daily", "Hourly": "hourly", "Minutely": "minutely", "Weekly": "weekly", "Monthly": "monthly"
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
        default_weekday = self._lang["weekday_mon"]
        if isinstance(schedule_weekday_val, (int, str)) and (isinstance(schedule_weekday_val, int) or schedule_weekday_val.isdigit()):
            self.var_schedule_weekday = tk.StringVar(value=weekday_map.get(int(schedule_weekday_val), default_weekday))
        else:
            self.var_schedule_weekday = tk.StringVar(value=str(schedule_weekday_val) if schedule_weekday_val in weekday_map.values() or schedule_weekday_val in reverse_weekday_map else default_weekday)
        # 每月几号（1-30）
        schedule_day_val = initial.get("schedule_day", 1)
        try:
            d = int(schedule_day_val)
            d = max(1, min(30, d))
        except (TypeError, ValueError):
            d = 1
        self.var_schedule_day = tk.StringVar(value=str(d))
        # 定时任务行为：启动、重启、停止
        schedule_action_en = initial.get("schedule_action", "start")
        schedule_action_map = {
            "start": self._lang["schedule_action_start"],
            "restart": self._lang["schedule_action_restart"],
            "stop": self._lang["schedule_action_stop"]
        }
        reverse_action_map = {
            self._lang["schedule_action_start"]: "start",
            self._lang["schedule_action_restart"]: "restart",
            self._lang["schedule_action_stop"]: "stop",
            "启动": "start", "重启": "restart", "停止": "stop",
            "Start": "start", "Restart": "restart", "Stop": "stop"
        }
        default_action = self._lang["schedule_action_start"]
        self.var_schedule_action = tk.StringVar(value=schedule_action_map.get(schedule_action_en, reverse_action_map.get(schedule_action_en, default_action) if schedule_action_en in reverse_action_map else default_action))

        row = 0
        ttk.Label(self, text=self._lang["label_name"]).grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(self, textvariable=self.var_name, width=50).grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        row += 1
        ttk.Label(self, text=self._lang["label_script_path"]).grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        fpath = ttk.Frame(self)
        fpath.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        ttk.Entry(fpath, textvariable=self.var_path, width=44).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(fpath, text=self._lang["btn_browse"], command=self._on_browse_path, width=6).pack(side=tk.LEFT, padx=2)
        row += 1
        ttk.Label(self, text=self._lang["label_work_dir"]).grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        fwork = ttk.Frame(self)
        fwork.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        ttk.Entry(fwork, textvariable=self.var_work, width=44).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(fwork, text=self._lang["btn_browse"], command=self._on_browse_dir, width=6).pack(side=tk.LEFT, padx=2)
        row += 1
        ttk.Label(self, text=self._lang["label_group"]).grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(self, textvariable=self.var_group, width=50).grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        ttk.Label(self, text=self._lang["hint_group_empty"]).grid(row=row, column=2, sticky=tk.W, padx=2)
        row += 1
        self.label_type = ttk.Label(self, text=self._type_from_path(self.var_path.get()))
        self.label_type.grid(row=row, column=1, sticky=tk.W, padx=4, pady=4)
        row += 1
        self.label_interpreter = ttk.Label(self, text=self._lang["label_interpreter"])
        self.label_interpreter.grid(row=row, column=0, sticky=tk.W, padx=6, pady=4)
        self.entry_interpreter = ttk.Entry(self, textvariable=self.var_interpreter, width=50)
        self.entry_interpreter.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        self.label_interpreter_hint = ttk.Label(self, text="")
        self.label_interpreter_hint.grid(row=row, column=2, sticky=tk.W, padx=2)
        row += 1
        self.var_log = tk.BooleanVar(value=bool(initial.get("log_output", False)))
        self.cb_log = ttk.Checkbutton(self, text=self._lang["check_log_output"], variable=self.var_log)
        self.cb_log.grid(row=row, column=1, sticky=tk.W, padx=4, pady=4)
        row += 1
        ttk.Checkbutton(self, text=self._lang["check_auto_restart"], variable=self.var_auto).grid(row=row, column=1, sticky=tk.W, padx=4, pady=4)
        row += 1
        ttk.Separator(self, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=3, sticky=tk.EW, padx=6, pady=8)
        row += 1
        ttk.Checkbutton(self, text=self._lang["check_schedule_enabled"], variable=self.var_schedule_enabled, command=self._update_schedule_visible).grid(row=row, column=1, sticky=tk.W, padx=4, pady=4)
        row += 1
        self.frame_schedule = ttk.Frame(self)
        self.frame_schedule.grid(row=row, column=0, columnspan=3, sticky=tk.EW, padx=6, pady=4)
        ttk.Label(self.frame_schedule, text=self._lang["label_schedule_action"]).grid(row=0, column=0, sticky=tk.W, padx=(20, 4), pady=2)
        self.combo_schedule_action = ttk.Combobox(self.frame_schedule, textvariable=self.var_schedule_action, width=10, state="readonly", values=[self._lang["schedule_action_start"], self._lang["schedule_action_restart"], self._lang["schedule_action_stop"]])
        self.combo_schedule_action.grid(row=0, column=1, sticky=tk.W, padx=2, pady=2)
        ttk.Label(self.frame_schedule, text=self._lang["label_schedule_type"]).grid(row=0, column=2, sticky=tk.W, padx=(10, 4), pady=2)
        self.combo_schedule_type = ttk.Combobox(self.frame_schedule, textvariable=self.var_schedule_type, width=12, state="readonly", values=[self._lang["schedule_type_daily"], self._lang["schedule_type_hourly"], self._lang["schedule_type_minutely"], self._lang["schedule_type_weekly"], self._lang["schedule_type_monthly"]])
        self.combo_schedule_type.grid(row=0, column=3, sticky=tk.W, padx=2, pady=2)
        self.combo_schedule_type.bind("<<ComboboxSelected>>", lambda e: self._update_schedule_ui())
        ttk.Label(self.frame_schedule, text=self._lang["label_schedule_time"]).grid(row=1, column=0, sticky=tk.W, padx=(20, 4), pady=2)
        self.entry_schedule_time = ttk.Entry(self.frame_schedule, textvariable=self.var_schedule_time, width=8)
        self.entry_schedule_time.grid(row=1, column=1, sticky=tk.W, padx=2, pady=2)
        ttk.Label(self.frame_schedule, text=self._lang["hint_time_format"]).grid(row=1, column=2, sticky=tk.W, padx=2, pady=2)
        ttk.Label(self.frame_schedule, text=self._lang["label_schedule_interval"]).grid(row=1, column=3, sticky=tk.W, padx=(10, 4), pady=2)
        self.entry_schedule_interval = ttk.Entry(self.frame_schedule, textvariable=self.var_schedule_interval, width=8)
        self.entry_schedule_interval.grid(row=1, column=4, sticky=tk.W, padx=2, pady=2)
        ttk.Label(self.frame_schedule, text=self._lang["hint_interval_minutes"]).grid(row=1, column=5, sticky=tk.W, padx=2, pady=2)
        # 时间快捷：0点、早6、中12、18点（下一行）
        self._time_quick_frame = ttk.Frame(self.frame_schedule)
        self._time_quick_frame.grid(row=2, column=0, columnspan=4, sticky=tk.W, padx=(20, 0), pady=2)
        ttk.Button(self._time_quick_frame, text=self._lang["btn_time_0"], width=4, command=lambda: self.var_schedule_time.set("00:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(self._time_quick_frame, text=self._lang["btn_time_6"], width=4, command=lambda: self.var_schedule_time.set("06:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(self._time_quick_frame, text=self._lang["btn_time_12"], width=4, command=lambda: self.var_schedule_time.set("12:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(self._time_quick_frame, text=self._lang["btn_time_18"], width=4, command=lambda: self.var_schedule_time.set("18:00")).pack(side=tk.LEFT, padx=1)
        self._schedule_weekday_label = ttk.Label(self.frame_schedule, text=self._lang["label_schedule_weekday"])
        self._schedule_weekday_label.grid(row=3, column=0, sticky=tk.W, padx=(20, 4), pady=2)
        self.combo_schedule_weekday = ttk.Combobox(self.frame_schedule, textvariable=self.var_schedule_weekday, width=12, state="readonly", values=[self._lang["weekday_mon"], self._lang["weekday_tue"], self._lang["weekday_wed"], self._lang["weekday_thu"], self._lang["weekday_fri"], self._lang["weekday_sat"], self._lang["weekday_sun"]])
        self.combo_schedule_weekday.grid(row=3, column=1, sticky=tk.W, padx=2, pady=2)
        self._schedule_day_label = ttk.Label(self.frame_schedule, text=self._lang["label_schedule_day"])
        self._schedule_day_label.grid(row=3, column=0, sticky=tk.W, padx=(20, 4), pady=2)
        self.combo_schedule_day = ttk.Combobox(self.frame_schedule, textvariable=self.var_schedule_day, width=8, state="readonly", values=[str(i) for i in range(1, 31)])
        self.combo_schedule_day.grid(row=3, column=1, sticky=tk.W, padx=2, pady=2)
        self._schedule_day_label.grid_remove()
        self.combo_schedule_day.grid_remove()
        self._update_schedule_visible()
        self._update_schedule_ui()
        row += 1
        ttk.Frame(self).grid(row=row, column=0, columnspan=3)  # spacer
        row += 1
        ttk.Button(self, text=self._lang["btn_ok"], command=self._on_ok).grid(row=row, column=1, padx=4, pady=8)
        ttk.Button(self, text=self._lang["btn_cancel"], command=self.destroy).grid(row=row, column=2, padx=4, pady=8)

        self.columnconfigure(1, weight=1)
        self.var_path.trace_add("write", lambda *a: self._update_type())
        self._update_type()
        self._update_log_visible()
    
    def _reinit_ui_text(self):
        """重新初始化对话框中的所有界面文本（用于语言切换后）。"""
        # 更新标题
        self.title(self._lang["dialog_edit"] if self.mode == "edit" else self._lang["dialog_add_edit"])
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
            elif widget_type == "TButton":
                text = widget.cget("text")
                if text in ["确定", "OK"]:
                    widget.config(text=self._lang["btn_ok"])
                elif text in ["取消", "Cancel"]:
                    widget.config(text=self._lang["btn_cancel"])
                elif "浏览" in text or "Browse" in text:
                    widget.config(text=self._lang["btn_browse"])
                elif "测试 Webhook" in text or "Test Webhook" in text:
                    widget.config(text=self._lang["btn_test"])
                elif text in ["0点", "0:00"]:
                    widget.config(text=self._lang["btn_time_0"])
                elif text in ["早6", "6am"]:
                    widget.config(text=self._lang["btn_time_6"])
                elif text in ["中12", "12pm"]:
                    widget.config(text=self._lang["btn_time_12"])
                elif text in ["18点", "6pm"]:
                    widget.config(text=self._lang["btn_time_18"])
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
                        elif v in ["重启", "Restart"]:
                            new_values.append(self._lang["schedule_action_restart"])
                        elif v in ["停止", "Stop"]:
                            new_values.append(self._lang["schedule_action_stop"])
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
                        else:
                            new_values.append(v)
                    if new_values:
                        widget.config(values=new_values)

    def _update_schedule_visible(self):
        """根据是否启用定时任务，显示/隐藏定时任务配置区域。"""
        if self.var_schedule_enabled.get():
            self.frame_schedule.grid()
            self._update_schedule_ui()
        else:
            self.frame_schedule.grid_remove()

    def _update_schedule_ui(self):
        """根据定时任务类型，显示/隐藏相关输入框。"""
        if not self.var_schedule_enabled.get():
            return
        stype = self.var_schedule_type.get()
        # 每天、每周：显示时间输入框
        # 每小时：隐藏时间输入框（固定每小时的第0分钟）
        # 每分钟：显示间隔输入框
        if stype == "每天":
            self.entry_schedule_time.grid()
            self.entry_schedule_time.grid_configure(row=1, column=1)
            self._time_quick_frame.grid()
            self.entry_schedule_interval.grid_remove()
            self._schedule_weekday_label.grid_remove()
            self.combo_schedule_weekday.grid_remove()
            self._schedule_day_label.grid_remove()
            self.combo_schedule_day.grid_remove()
        elif stype == "每小时":
            self.entry_schedule_time.grid_remove()
            self._time_quick_frame.grid_remove()
            self.entry_schedule_interval.grid_remove()
            self._schedule_weekday_label.grid_remove()
            self.combo_schedule_weekday.grid_remove()
            self._schedule_day_label.grid_remove()
            self.combo_schedule_day.grid_remove()
        elif stype == "每分钟":
            self.entry_schedule_time.grid_remove()
            self._time_quick_frame.grid_remove()
            self.entry_schedule_interval.grid()
            self.entry_schedule_interval.grid_configure(row=1, column=4)
            self._schedule_weekday_label.grid_remove()
            self.combo_schedule_weekday.grid_remove()
            self._schedule_day_label.grid_remove()
            self.combo_schedule_day.grid_remove()
        elif stype == "每周":
            self.entry_schedule_time.grid()
            self.entry_schedule_time.grid_configure(row=1, column=1)
            self._time_quick_frame.grid()
            self.entry_schedule_interval.grid_remove()
            self._schedule_weekday_label.grid(row=3, column=0, sticky=tk.W, padx=(20, 4), pady=2)
            self.combo_schedule_weekday.grid(row=3, column=1, sticky=tk.W, padx=2, pady=2)
            self._schedule_day_label.grid_remove()
            self.combo_schedule_day.grid_remove()
        elif stype in ("每月", "Monthly"):
            self.entry_schedule_time.grid()
            self.entry_schedule_time.grid_configure(row=1, column=1)
            self._time_quick_frame.grid()
            self.entry_schedule_interval.grid_remove()
            self._schedule_weekday_label.grid_remove()
            self.combo_schedule_weekday.grid_remove()
            self._schedule_day_label.grid(row=3, column=0, sticky=tk.W, padx=(20, 4), pady=2)
            self.combo_schedule_day.grid(row=3, column=1, sticky=tk.W, padx=2, pady=2)

    def _type_from_path(self, p: str) -> str:
        stype = get_script_type(p)
        type_names = {
            "python": self._lang["label_type_prefix"] + "Python",
            "node": self._lang["label_type_prefix"] + "Node.js",
            "powershell": self._lang["label_type_prefix"] + "PowerShell",
            "vbs": self._lang["label_type_prefix"] + "VBScript",
            "jscript": self._lang["label_type_prefix"] + "JScript",
            "batch": self._lang["label_type_prefix"] + "批处理" if self._lang == LANGUAGES["zh_CN"] else self._lang["label_type_prefix"] + "Batch",
            "shell": self._lang["label_type_prefix"] + "Shell",
            "wsf": self._lang["label_type_prefix"] + "WSF",
            "exe": self._lang["label_type_prefix"] + "EXE",
        }
        default = self._lang["label_type_prefix"] + ("批处理" if self._lang == LANGUAGES["zh_CN"] else "Batch")
        return type_names.get(stype, default)

    def _update_type(self):
        p = self.var_path.get()
        stype = get_script_type(p)
        self.label_type.config(text=self._type_from_path(p))
        
        # 根据脚本类型显示/隐藏解释器输入框
        needs_interpreter = stype in ("python", "node", "shell")
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
            self.cb_log.grid(row=6, column=1, sticky=tk.W, padx=4, pady=4)
        else:
            self.cb_log.grid_remove()

    def _on_browse_path(self):
        all_ext = "*.bat;*.cmd;*.py;*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx;*.ps1;*.vbs;*.wsf;*.sh;*.exe"
        if sys.platform != "win32":
            # Mac/Linux：Shell 优先，不展示 Windows 专用类型（批处理/VBS/WSF/exe）以减少误选
            if self._lang == LANGUAGES["zh_CN"]:
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
        elif self._lang == LANGUAGES["zh_CN"]:
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

    def _on_ok(self):
        default_name = "未命名" if self._lang == LANGUAGES["zh_CN"] else "Unnamed"
        name = self.var_name.get().strip() or default_name
        path = self.var_path.get().strip()
        if not path:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_enter_script_path"], parent=self)
            return
        if not os.path.isfile(path):
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_script_not_exists"] % path, parent=self)
            return
        work = self.var_work.get().strip()
        group = self.var_group.get().strip()
        stype = get_script_type(path)
        interpreter = self.var_interpreter.get().strip()
        # 兼容旧配置：如果存在 is_python，转换为 script_type
        is_py = stype == "python"
        self.result = {
            "name": name,
            "path": path,
            "work_dir": work,
            "group": group,
            "script_type": stype,
            "interpreter": interpreter,
            # 兼容旧配置
            "is_python": is_py,
            "python_exe": interpreter if is_py else "",
            "auto_restart": self.var_auto.get(),
            "log_output": self.var_log.get() if stype in ("python", "node", "powershell") else False,
            "schedule_enabled": self.var_schedule_enabled.get(),
            "schedule_action": {
                self._lang["schedule_action_start"]: "start",
                self._lang["schedule_action_restart"]: "restart",
                self._lang["schedule_action_stop"]: "stop",
                "启动": "start", "重启": "restart", "停止": "stop",
                "Start": "start", "Restart": "restart", "Stop": "stop"
            }.get(self.var_schedule_action.get(), "start"),
            "schedule_type": {
                self._lang["schedule_type_daily"]: "daily",
                self._lang["schedule_type_hourly"]: "hourly",
                self._lang["schedule_type_minutely"]: "minutely",
                self._lang["schedule_type_weekly"]: "weekly",
                self._lang["schedule_type_monthly"]: "monthly",
                "每天": "daily", "每小时": "hourly", "每分钟": "minutely", "每周": "weekly", "每月": "monthly",
                "Daily": "daily", "Hourly": "hourly", "Minutely": "minutely", "Weekly": "weekly", "Monthly": "monthly"
            }.get(self.var_schedule_type.get(), "daily"),
            "schedule_time": self.var_schedule_time.get(),
            "schedule_interval": int(self.var_schedule_interval.get()) if self.var_schedule_interval.get().isdigit() else 5,
            "schedule_weekday": {
                self._lang["weekday_mon"]: 0, self._lang["weekday_tue"]: 1, self._lang["weekday_wed"]: 2,
                self._lang["weekday_thu"]: 3, self._lang["weekday_fri"]: 4, self._lang["weekday_sat"]: 5, self._lang["weekday_sun"]: 6,
                "周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6,
                "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6
            }.get(self.var_schedule_weekday.get(), 0),
            "schedule_day": max(1, min(30, int(self.var_schedule_day.get()) if self.var_schedule_day.get().isdigit() else 1)),
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
        
        # 初始化
        self._update_script_list()
        if self.script_combo['values']:
            self.script_combo.current(0)
            self._on_script_selected()
        
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
        # 获取历史记录
        history = self.app._execution_history.get(item_id, [])
        if not history:
            return
        # 按时间倒序显示（最新的在前）
        sorted_history = sorted([h for h in history if h.get("stop_time")], key=lambda x: x.get("stop_time", 0), reverse=True)
        # 统计信息
        total = len(sorted_history)
        success = sum(1 for h in sorted_history if h.get("exit_code") == 0)
        failed = total - success
        avg_duration = sum(h.get("duration", 0) for h in sorted_history) / total if total > 0 else 0
        self.stats_label.config(
            text=f"{self._lang['stat_total'] % total} | {self._lang['stat_success'] % success} | {self._lang['stat_failed'] % failed} | {self._lang['stat_avg_duration'] % avg_duration}"
        )
        # 填充表格
        for record in sorted_history:
            start_time = datetime.fromtimestamp(record.get("start_time", 0)).strftime("%Y-%m-%d %H:%M:%S")
            stop_time = datetime.fromtimestamp(record.get("stop_time", 0)).strftime("%Y-%m-%d %H:%M:%S") if record.get("stop_time") else "-"
            duration = f"{record.get('duration', 0):.1f} 秒" if record.get("duration") else "-"
            exit_code = str(record.get("exit_code", "?")) if record.get("exit_code") is not None else "?"
            status = "异常" if record.get("status") == "crashed" else "正常"
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
        if self._lang == LANGUAGES["zh_CN"]:
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
                    "script_name": "测试脚本" if self._lang == LANGUAGES["zh_CN"] else "Test Script",
                    "reason": "测试通知" if self._lang == LANGUAGES["zh_CN"] else "Test Notification",
                    "exit_code": 1,
                    "duration": 2.5,
                    "timestamp": datetime.now().isoformat(),
                    "status": "测试" if self._lang == LANGUAGES["zh_CN"] else "Test",
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
        
        # 定时任务启用/禁用
        ttk.Label(edit_frame, text=self._lang["label_batch_schedule"]).grid(row=2, column=0, sticky=tk.W, pady=5)
        schedule_frame = ttk.Frame(edit_frame)
        schedule_frame.grid(row=2, column=1, sticky=tk.W, pady=5)
        self.var_schedule_action = tk.StringVar(value="no_change")
        ttk.Radiobutton(schedule_frame, text=self._lang["hint_no_change"], variable=self.var_schedule_action, value="no_change").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(schedule_frame, text=self._lang["hint_enable"], variable=self.var_schedule_action, value="enable").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(schedule_frame, text=self._lang["hint_disable"], variable=self.var_schedule_action, value="disable").pack(side=tk.LEFT, padx=5)
        # 定时内容（类型/时间/星期/动作），拆成两行避免挤在一行被裁切
        ttk.Label(edit_frame, text=self._lang["label_batch_schedule_detail"]).grid(row=3, column=0, sticky=tk.NW, pady=5)
        detail_row1 = ttk.Frame(edit_frame)
        detail_row1.grid(row=3, column=1, columnspan=2, sticky=tk.W, pady=0)
        type_vals = [self._lang["hint_no_change"], self._lang["schedule_type_daily"], self._lang["schedule_type_weekly"], self._lang["schedule_type_monthly"], self._lang["schedule_type_hourly"], self._lang["schedule_type_minutely"], self._lang["schedule_type_every_hours"]]
        self.var_batch_schedule_type = tk.StringVar(value=self._lang["hint_no_change"])
        ttk.Label(detail_row1, text=self._lang["label_schedule_type"]).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Combobox(detail_row1, textvariable=self.var_batch_schedule_type, width=10, state="readonly", values=type_vals).pack(side=tk.LEFT, padx=(0, 8))
        self.var_batch_schedule_time = tk.StringVar()
        ttk.Label(detail_row1, text=self._lang["label_schedule_time"]).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Entry(detail_row1, textvariable=self.var_batch_schedule_time, width=8).pack(side=tk.LEFT, padx=(0, 4))
        batch_time_quick = ttk.Frame(detail_row1)
        batch_time_quick.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(batch_time_quick, text=self._lang["btn_time_0"], width=4, command=lambda: self.var_batch_schedule_time.set("00:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(batch_time_quick, text=self._lang["btn_time_6"], width=4, command=lambda: self.var_batch_schedule_time.set("06:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(batch_time_quick, text=self._lang["btn_time_12"], width=4, command=lambda: self.var_batch_schedule_time.set("12:00")).pack(side=tk.LEFT, padx=1)
        ttk.Button(batch_time_quick, text=self._lang["btn_time_18"], width=4, command=lambda: self.var_batch_schedule_time.set("18:00")).pack(side=tk.LEFT, padx=1)
        detail_row2 = ttk.Frame(edit_frame)
        detail_row2.grid(row=4, column=1, columnspan=2, sticky=tk.W, pady=2)
        weekday_vals = [self._lang["hint_no_change"], self._lang["weekday_mon"], self._lang["weekday_tue"], self._lang["weekday_wed"], self._lang["weekday_thu"], self._lang["weekday_fri"], self._lang["weekday_sat"], self._lang["weekday_sun"]]
        self.var_batch_schedule_weekday = tk.StringVar(value=self._lang["hint_no_change"])
        ttk.Label(detail_row2, text=self._lang["label_schedule_weekday"]).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Combobox(detail_row2, textvariable=self.var_batch_schedule_weekday, width=10, state="readonly", values=weekday_vals).pack(side=tk.LEFT, padx=(0, 8))
        action_vals = [self._lang["hint_no_change"], self._lang["schedule_action_start"], self._lang["schedule_action_restart"], self._lang["schedule_action_stop"]]
        self.var_batch_schedule_action = tk.StringVar(value=self._lang["hint_no_change"])
        ttk.Label(detail_row2, text=self._lang["label_schedule_action"]).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Combobox(detail_row2, textvariable=self.var_batch_schedule_action, width=8, state="readonly", values=action_vals).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(edit_frame, text=self._lang["hint_batch_schedule_detail"], foreground="gray", font=("", 8)).grid(row=5, column=1, columnspan=2, sticky=tk.W, pady=0)
        # 保活
        ttk.Label(edit_frame, text=self._lang["label_batch_keepalive"]).grid(row=6, column=0, sticky=tk.W, pady=5)
        keepalive_frame = ttk.Frame(edit_frame)
        keepalive_frame.grid(row=6, column=1, sticky=tk.W, pady=5)
        self.var_keepalive_action = tk.StringVar(value="no_change")
        ttk.Radiobutton(keepalive_frame, text=self._lang["hint_no_change"], variable=self.var_keepalive_action, value="no_change").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(keepalive_frame, text=self._lang["hint_enable"], variable=self.var_keepalive_action, value="enable").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(keepalive_frame, text=self._lang["hint_disable"], variable=self.var_keepalive_action, value="disable").pack(side=tk.LEFT, padx=5)
        
        # 启动本工具时自动启动
        ttk.Label(edit_frame, text=self._lang["label_batch_launch_on_start"]).grid(row=7, column=0, sticky=tk.W, pady=5)
        launch_on_frame = ttk.Frame(edit_frame)
        launch_on_frame.grid(row=7, column=1, sticky=tk.W, pady=5)
        self.var_launch_on_app_start_action = tk.StringVar(value="no_change")
        ttk.Radiobutton(launch_on_frame, text=self._lang["hint_no_change"], variable=self.var_launch_on_app_start_action, value="no_change").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(launch_on_frame, text=self._lang["hint_enable"], variable=self.var_launch_on_app_start_action, value="enable").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(launch_on_frame, text=self._lang["hint_disable"], variable=self.var_launch_on_app_start_action, value="disable").pack(side=tk.LEFT, padx=5)
        
        # 日志输出
        ttk.Label(edit_frame, text=self._lang["label_batch_log"]).grid(row=8, column=0, sticky=tk.W, pady=5)
        log_frame = ttk.Frame(edit_frame)
        log_frame.grid(row=8, column=1, sticky=tk.W, pady=5)
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
        hint_label.grid(row=9, column=0, columnspan=3, pady=(4, 6))
        
        # 按钮（保证在窗口内可见）
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=10, pady=(8, 12))
        ttk.Button(btn_frame, text=self._lang["btn_ok"], command=self._on_ok).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(btn_frame, text=self._lang["btn_cancel"], command=self.destroy).pack(side=tk.RIGHT)
        
    def _on_ok(self):
        """应用批量修改。"""
        modified = 0
        for row in self.rows:
            it = self.app._item_at(row)
            if not it:
                continue
            changed = False
            # 修改分组
            group = self.var_group.get().strip()
            if group:
                it["group"] = group
                changed = True
            elif group == "" and self.var_group.get() == "":
                # 清空分组
                it["group"] = ""
                changed = True
            # 修改解释器
            interpreter = self.var_interpreter.get().strip()
            if interpreter:
                it["interpreter"] = interpreter
                changed = True
            elif interpreter == "" and self.var_interpreter.get() == "":
                # 清空解释器
                it["interpreter"] = ""
                changed = True
            # 修改定时任务
            schedule_action = self.var_schedule_action.get()
            if schedule_action == "enable":
                it["schedule_enabled"] = True
                changed = True
                # 定时内容（类型/时间/星期/动作），非「不修改」或空则应用
                no_cn, no_en = self._lang["hint_no_change"], "No Change"
                st = self.var_batch_schedule_type.get().strip()
                if st and st not in (no_cn, no_en):
                    type_map = {self._lang["schedule_type_daily"]: "daily", self._lang["schedule_type_weekly"]: "weekly", self._lang["schedule_type_monthly"]: "monthly", self._lang["schedule_type_hourly"]: "hourly", self._lang["schedule_type_minutely"]: "minutely", self._lang["schedule_type_every_hours"]: "every_hours"}
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
            elif schedule_action == "disable":
                it["schedule_enabled"] = False
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
# GUI
# ---------------------------------------------------------------------------

class BatchManagerApp:
    def __init__(self):
        self.root = tk.Tk()
        # 语言设置
        self._current_language = load_language()
        self._lang = LANGUAGES[self._current_language]
        self.root.title(self._lang["window_title"])
        self.root.geometry("920x520")
        self.root.minsize(700, 400)

        self.items: list = []  # [{id, name, path, work_dir, is_python, pid}]
        self._pid_to_row: dict = {}  # pid -> row index (用于刷新状态)
        self._next_id = 1
        self._refresh_paused = False  # 暂停定时刷新时不再跑 _refresh_status
        self._refresh_interval_ms = 4000  # 定时刷新间隔（毫秒），可在「设置」-「刷新间隔」中修改
        self._port_titles: dict[int, Optional[str]] = {}  # port -> title 缓存，None 表示已尝试但无标题
        self._port_title_pending: set[int] = set()  # 正在获取标题的端口，避免重复请求
        self._drag_start_item = None  # 拖拽起始项
        self._drag_in_progress = False  # 是否正在拖拽
        self._schedule_last_check = time.time()  # 上次检查定时任务的时间
        self._schedule_last_run: dict[int, float] = {}  # item_id -> 上次执行时间
        # 执行历史记录：item_id -> [{"start_time": float, "stop_time": float, "duration": float, "exit_code": int|None, "status": "stopped"|"crashed"}, ...]
        self._execution_history: dict[int, list] = {}
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
        }
        self._build_ui()
        self._load_config()
        self._load_history()
        self._load_notification_config()
        self._start_refresh_loop()
        self._start_schedule_check_loop()
        self._raise_window()
        # 关闭窗口即退出
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

    def _raise_window(self):
        """启动时把窗口置前，避免被其它窗口挡住。"""
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(220, lambda: self.root.attributes("-topmost", False))

    def _build_ui(self):
        # 菜单栏
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # 首页菜单
        menubar.add_command(label=self._lang.get("menu_home", "首页"), command=self._on_home)
        
        # 语言菜单
        lang_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label=self._lang.get("menu_language", "语言"), menu=lang_menu)
        lang_menu.add_command(label=self._lang.get("menu_language_zh", "中文"), command=lambda: self._switch_language("zh_CN"))
        lang_menu.add_command(label=self._lang.get("menu_language_en", "English"), command=lambda: self._switch_language("en_US"))
        
        # 设置：刷新间隔（保存引用以便切换语言时更新英文）
        self._settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label=self._lang.get("menu_settings", "设置"), menu=self._settings_menu)
        self._refresh_menu = tk.Menu(self._settings_menu, tearoff=0)
        self._settings_menu.add_cascade(label=self._lang.get("menu_refresh_interval", "刷新间隔"), menu=self._refresh_menu)
        for sec, ms in [(2, 2000), (4, 4000), (8, 8000)]:
            key = "refresh_interval_2s" if sec == 2 else ("refresh_interval_4s" if sec == 4 else "refresh_interval_8s")
            self._refresh_menu.add_command(label=self._lang.get(key, "%d 秒" % sec), command=lambda _ms=ms: self._set_refresh_interval(_ms))
        
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
        
        # 第一行：添加/编辑/删除/复制 | 启动/停止/重启/全部启动/全部停止
        row1 = ttk.Frame(self.root, padding=(6, 6, 6, 2))
        row1.pack(fill=tk.X)
        self.btn_add = ttk.Button(row1, text=self._lang["btn_add"], command=self._on_add)
        self.btn_add.pack(side=tk.LEFT, padx=2)
        self.btn_edit = ttk.Button(row1, text=self._lang["btn_edit"], command=self._on_edit)
        self.btn_edit.pack(side=tk.LEFT, padx=2)
        self.btn_delete = ttk.Button(row1, text=self._lang["btn_delete"], command=self._on_delete)
        self.btn_delete.pack(side=tk.LEFT, padx=2)
        self.btn_duplicate = ttk.Button(row1, text=self._lang["btn_duplicate"], command=self._on_duplicate)
        self.btn_duplicate.pack(side=tk.LEFT, padx=2)
        self.btn_select_all = ttk.Button(row1, text=self._lang["btn_select_all"], command=self._on_select_all)
        self.btn_select_all.pack(side=tk.LEFT, padx=2)
        self.btn_select_none = ttk.Button(row1, text=self._lang["btn_select_none"], command=self._on_select_none)
        self.btn_select_none.pack(side=tk.LEFT, padx=2)
        ttk.Separator(row1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        self.btn_start = ttk.Button(row1, text=self._lang["btn_start"], command=self._on_start)
        self.btn_start.pack(side=tk.LEFT, padx=2)
        self.btn_stop = ttk.Button(row1, text=self._lang["btn_stop"], command=self._on_stop)
        self.btn_stop.pack(side=tk.LEFT, padx=2)
        self.btn_restart = ttk.Button(row1, text=self._lang["btn_restart"], command=self._on_restart)
        self.btn_restart.pack(side=tk.LEFT, padx=2)
        # 第二行：自动识别、识别并加入、刷新状态、端口/进程
        row2 = ttk.Frame(self.root, padding=(6, 2, 6, 2))
        row2.pack(fill=tk.X)
        self.btn_scan_running = ttk.Button(row2, text=self._lang["btn_scan_running"], command=self._on_scan_running)
        self.btn_scan_running.pack(side=tk.LEFT, padx=2)
        self.btn_scan_and_add = ttk.Button(row2, text=self._lang["btn_scan_and_add"], command=self._on_scan_and_add)
        self.btn_scan_and_add.pack(side=tk.LEFT, padx=2)
        self.btn_scan_exe_add = ttk.Button(row2, text=self._lang["btn_scan_exe_add"], command=self._on_scan_exe_add)
        self.btn_scan_exe_add.pack(side=tk.LEFT, padx=2)
        ttk.Separator(row2, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        self.btn_refresh = ttk.Button(row2, text=self._lang["btn_refresh"], command=lambda: self._refresh_status(and_set_status=True))
        self.btn_refresh.pack(side=tk.LEFT, padx=2)
        self.btn_pause_refresh = ttk.Button(row2, text=self._lang["btn_pause_refresh"], command=self._on_toggle_refresh)
        self.btn_pause_refresh.pack(side=tk.LEFT, padx=2)
        self.btn_ports = ttk.Button(row2, text=self._lang["btn_ports"], command=self._on_show_ports)
        self.btn_ports.pack(side=tk.LEFT, padx=2)
        self.btn_visit = ttk.Button(row2, text=self._lang["btn_visit"], command=self._on_visit_port)
        self.btn_visit.pack(side=tk.LEFT, padx=2)
        self.btn_start_all = ttk.Button(row2, text=self._lang["btn_start_all"], command=self._on_start_all)
        self.btn_start_all.pack(side=tk.LEFT, padx=2)
        self.btn_stop_all = ttk.Button(row2, text=self._lang["btn_stop_all"], command=self._on_stop_all)
        self.btn_stop_all.pack(side=tk.LEFT, padx=2)
        
        # 第三行：管理功能
        row3 = ttk.Frame(self.root, padding=(6, 2, 6, 6))
        row3.pack(fill=tk.X)
        self.btn_export = ttk.Button(row3, text=self._lang["btn_export"], command=self._on_export_config)
        self.btn_export.pack(side=tk.LEFT, padx=2)
        self.btn_import = ttk.Button(row3, text=self._lang["btn_import"], command=self._on_import_config)
        self.btn_import.pack(side=tk.LEFT, padx=2)
        ttk.Separator(row3, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        self.btn_history = ttk.Button(row3, text=self._lang["btn_history"], command=self._on_show_history)
        self.btn_history.pack(side=tk.LEFT, padx=2)
        self.btn_batch_edit = ttk.Button(row3, text=self._lang["btn_batch_edit"], command=self._on_batch_edit)
        self.btn_batch_edit.pack(side=tk.LEFT, padx=2)
        self.btn_notification = ttk.Button(row3, text=self._lang["btn_notification"], command=self._on_notification_config)
        self.btn_notification.pack(side=tk.LEFT, padx=2)
        ttk.Separator(row3, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        self.btn_stop_all_batch = ttk.Button(row3, text=self._lang["btn_stop_all_batch"], command=self._on_stop_all_batch)
        self.btn_stop_all_batch.pack(side=tk.LEFT, padx=2)
        # 外网IP显示（在停止软件外所有批处理右边）
        ttk.Separator(row3, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        self.label_public_ip = ttk.Label(row3, text=self._lang["label_public_ip"], font=("", 9))
        self.label_public_ip.pack(side=tk.LEFT, padx=(0, 4))
        self.public_ip_label = ttk.Label(row3, text=self._lang["hint_loading"], foreground="gray", cursor="arrow", font=("", 9))
        self.public_ip_label.pack(side=tk.LEFT)
        self.public_ip_label.bind("<Button-1>", self._on_copy_public_ip)
        self.public_ip_label.bind("<Enter>", self._on_public_ip_enter)
        self.public_ip_label.bind("<Leave>", self._on_public_ip_leave)
        self.hint_copy_public_label = ttk.Label(row3, text="", font=("", 8), foreground="gray")
        self.hint_copy_public_label.pack(side=tk.LEFT, padx=(4, 0))
        # 外网IP/本地IP 在主循环启动后由 run() 里 after(0, _start_ip_load_threads) 异步加载，避免 "main thread is not in main loop"

        # 筛选行：按分组过滤（带横向滚动）
        filter_container = ttk.Frame(self.root)
        filter_container.pack(fill=tk.X, padx=6, pady=(0, 4))
        
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
        self.filter_group_var = tk.StringVar(value="全部" if self._current_language == "zh_CN" else "All")
        self.filter_combo = ttk.Combobox(filter_frame, textvariable=self.filter_group_var, width=20, state="readonly")
        self.filter_combo.pack(side=tk.LEFT, padx=2)
        self.filter_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_group_filter())
        self.btn_clear_filter = ttk.Button(filter_frame, text=self._lang["btn_clear_filter"], command=self._clear_group_filter)
        self.btn_clear_filter.pack(side=tk.LEFT, padx=2)
        
        # 搜索关键词与定位
        ttk.Separator(filter_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        self.label_search_keyword = ttk.Label(filter_frame, text=self._lang["label_search_keyword"])
        self.label_search_keyword.pack(side=tk.LEFT, padx=(0, 4))
        self.search_keyword_var = tk.StringVar()
        self.search_entry = ttk.Entry(filter_frame, textvariable=self.search_keyword_var, width=18)
        self.search_entry.pack(side=tk.LEFT, padx=2)
        self.search_entry.bind("<Return>", lambda e: self._on_search_locate(forward=True))
        self.btn_locate = ttk.Button(filter_frame, text=self._lang["btn_locate"], command=lambda: self._on_search_locate(forward=True))
        self.btn_locate.pack(side=tk.LEFT, padx=2)
        self.btn_next_match = ttk.Button(filter_frame, text=self._lang["btn_next_match"], command=lambda: self._on_search_locate(forward=False))
        self.btn_next_match.pack(side=tk.LEFT, padx=2)
        
        # IP显示（在清除筛选按钮右边）：先显示加载中，异步获取本地 IP 避免启动阻塞
        ttk.Separator(filter_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        self.label_local_ip = ttk.Label(filter_frame, text=self._lang["label_local_ip"], font=("", 9))
        self.label_local_ip.pack(side=tk.LEFT, padx=(0, 4))
        self.ip_label = ttk.Label(filter_frame, text=self._lang["hint_loading"], foreground="gray", cursor="hand2", font=("", 9, "underline"))
        self.ip_label.pack(side=tk.LEFT)
        self.ip_label.bind("<Button-1>", self._on_copy_ip)
        self.ip_label.bind("<Enter>", lambda e: self.ip_label.config(foreground="darkblue") if self.ip_label.cget("foreground") == "blue" else None)
        self.ip_label.bind("<Leave>", lambda e: self._on_local_ip_leave())
        self.hint_copy_label = ttk.Label(filter_frame, text=self._lang["hint_click_to_copy"], font=("", 8), foreground="gray")
        self.hint_copy_label.pack(side=tk.LEFT, padx=(4, 0))

        # 保存引用以便后续更新
        self.filter_canvas = filter_canvas
        self.filter_frame = filter_frame

        # 列表（PanedWindow 必须用 add 添加子控件；列表加横向滚动条，避免状态、PID、端口列被挤出视区）
        pan = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        pan.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        tree_frame = ttk.Frame(pan)
        cols = ("id", "name", "group", "path", "work_dir", "type", "status", "pid", "ports", "title", "schedule")
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
        }
        for c in cols:
            self.tree.heading(c, text=col_headings.get(c, c))
        self.tree.column("id", width=50)
        self.tree.column("name", width=100)
        self.tree.column("group", width=80)
        self.tree.column("path", width=200)
        self.tree.column("work_dir", width=140)
        self.tree.column("type", width=52)
        self.tree.column("status", width=56)
        self.tree.column("pid", width=52)
        self.tree.column("ports", width=100)
        self.tree.column("title", width=180)
        self.tree.column("schedule", width=80)
        tree_hbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(xscrollcommand=tree_hbar.set)
        self.tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        tree_hbar.pack(side=tk.BOTTOM, fill=tk.X)
        pan.add(tree_frame, weight=1)
        self.tree.bind("<Double-1>", lambda e: self._on_edit())
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<Control-a>", lambda e: self._on_select_all() or "break")
        # 鼠标拖拽多选
        self.tree.bind("<Button-1>", self._on_tree_button1)
        self.tree.bind("<B1-Motion>", self._on_tree_drag)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release)

        # 详情
        self.detail_frame = ttk.LabelFrame(pan, text=self._lang["detail_label"])
        pan.add(self.detail_frame, weight=0)
        self.detail_text = tk.Text(self.detail_frame, height=3, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9))
        self.detail_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # 状态栏
        self.status_var = tk.StringVar(value=self._lang["status_ready_msg"])
        ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM, padx=6, pady=2)
        self.root.bind("<Control-a>", lambda e: (self._on_select_all(), "break")[1])

    def _get_selected_rows(self) -> list:
        sel = self.tree.selection()
        rows = [self.tree.index(i) for i in sel]
        # 无选中且仅有 1 项时，自动视作选中该项，避免重启/停止/编辑等总是弹出「请先选中」
        if not rows and len(self.items) == 1:
            return [0]
        return rows

    def _on_select_all(self):
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(*children)
            if self._current_language == "zh_CN":
                self.status_var.set(self._lang["status_selected_all"] % len(children))
            else:
                self.status_var.set(self._lang["status_selected_all"] % len(children))

    def _on_select_none(self):
        self.tree.selection_set(())
        self.status_var.set(self._lang["status_deselected_all"])

    def _item_at(self, row: int) -> Optional[dict]:
        if 0 <= row < len(self.items):
            return self.items[row]
        return None

    def _on_select(self, evt):
        sel = self.tree.selection()
        if not sel:
            self.detail_text.config(state=tk.NORMAL)
            self.detail_text.delete("1.0", tk.END)
            self.detail_text.config(state=tk.DISABLED)
            self._update_visit_button_state()
            return
        idx = self.tree.index(sel[0])
        it = self._item_at(idx)
        if it:
            if self._current_language == "zh_CN":
                s = f"路径: {it.get('path','')}\n工作目录: {it.get('work_dir','') or '(同脚本目录)'}"
            else:
                s = f"Path: {it.get('path','')}\nWork Directory: {it.get('work_dir','') or '(same as script dir)'}"
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

    def _switch_language(self, language: str):
        """切换语言。"""
        if language not in LANGUAGES:
            return
        self._current_language = language
        self._lang = LANGUAGES[language]
        save_language(language)
        # 更新窗口标题
        self.root.title(self._lang["window_title"])
        # 更新所有按钮文本
        self.btn_add.config(text=self._lang["btn_add"])
        self.btn_edit.config(text=self._lang["btn_edit"])
        self.btn_delete.config(text=self._lang["btn_delete"])
        self.btn_duplicate.config(text=self._lang["btn_duplicate"])
        self.btn_select_all.config(text=self._lang["btn_select_all"])
        self.btn_select_none.config(text=self._lang["btn_select_none"])
        self.btn_start.config(text=self._lang["btn_start"])
        self.btn_stop.config(text=self._lang["btn_stop"])
        self.btn_restart.config(text=self._lang["btn_restart"])
        self.btn_start_all.config(text=self._lang["btn_start_all"])
        self.btn_stop_all.config(text=self._lang["btn_stop_all"])
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
        self.btn_batch_edit.config(text=self._lang["btn_batch_edit"])
        self.btn_notification.config(text=self._lang["btn_notification"])
        self.btn_stop_all_batch.config(text=self._lang["btn_stop_all_batch"])
        # 更新筛选标签
        self.label_filter.config(text=self._lang["label_filter_group"])
        self.btn_clear_filter.config(text=self._lang["btn_clear_filter"])
        # 搜索关键词与定位
        self.label_search_keyword.config(text=self._lang["label_search_keyword"])
        self.btn_locate.config(text=self._lang["btn_locate"])
        self.btn_next_match.config(text=self._lang["btn_next_match"])
        # 更新IP标签
        self.label_local_ip.config(text=self._lang["label_local_ip"])
        self.label_public_ip.config(text=self._lang["label_public_ip"])
        self.hint_copy_label.config(text=self._lang["hint_click_to_copy"])
        # 如果IP是"无法获取"，更新显示文本
        current_ip_text = self.ip_label.cget("text")
        if current_ip_text == "无法获取" or current_ip_text == "Unable to get IP address":
            self.ip_label.config(text=self._lang["msg_ip_unavailable"])
        # 更新外网IP标签文本（如果已加载）
        current_public_ip_text = self.public_ip_label.cget("text")
        if current_public_ip_text == "无法获取" or current_public_ip_text == "Unable to get IP address":
            self.public_ip_label.config(text=self._lang["msg_ip_unavailable"], foreground="gray", cursor="arrow")
            self.hint_copy_public_label.config(text="")
        elif current_public_ip_text == "(获取中...)" or current_public_ip_text == "(Loading...)":
            self.public_ip_label.config(text=self._lang["hint_loading"])
            self.hint_copy_public_label.config(text="")
        else:
            # 如果外网IP已加载，显示复制提示
            if current_public_ip_text and current_public_ip_text != self._lang["hint_loading"] and current_public_ip_text != self._lang["msg_ip_unavailable"]:
                self.hint_copy_public_label.config(text=self._lang["hint_click_to_copy"])
        # 更新详情标签
        self.detail_frame.config(text=self._lang["detail_label"])
        # 更新表格列标题
        cols = ("id", "name", "group", "path", "work_dir", "type", "status", "pid", "ports", "title", "schedule")
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
        }
        for c in cols:
            self.tree.heading(c, text=col_headings.get(c, c))
        # 更新状态栏
        self.status_var.set(self._lang["status_ready_msg"])
        # 更新筛选下拉框的"全部"选项
        if self.filter_group_var.get() in ("全部", "All"):
            self.filter_group_var.set("全部" if language == "zh_CN" else "All")
        # 更新菜单
        try:
            menubar = self.root.nametowidget(self.root.cget("menu"))
            for i in range(menubar.index("end") + 1):
                try:
                    menu_label = menubar.entryconfig(i, "label")[4]
                    if menu_label and ("语言" in menu_label or "Language" in menu_label):
                        menubar.entryconfig(i, label=self._lang.get("menu_language", "语言"))
                    elif menu_label and ("首页" in menu_label or "Home" in menu_label):
                        menubar.entryconfig(i, label=self._lang.get("menu_home", "首页"))
                    elif menu_label and ("开机启动" in menu_label or "Start with Windows" in menu_label):
                        menubar.entryconfig(i, label=self._lang.get("menu_startup", "开机启动"))
                        if (sys.platform == "win32" or _PLATFORM) and getattr(self, "_startup_menu", None):
                            self._update_startup_submenu_labels()
                    elif menu_label and ("设置" in menu_label or "Settings" in menu_label):
                        menubar.entryconfig(i, label=self._lang.get("menu_settings", "设置"))
                        if getattr(self, "_settings_menu", None):
                            self._settings_menu.entryconfig(0, label=self._lang.get("menu_refresh_interval", "刷新间隔"))
                        if getattr(self, "_refresh_menu", None):
                            for idx, sec in enumerate([2, 4, 8]):
                                key = "refresh_interval_2s" if sec == 2 else ("refresh_interval_4s" if sec == 4 else "refresh_interval_8s")
                                self._refresh_menu.entryconfig(idx, label=self._lang.get(key, "%d 秒" % sec))
                except:
                    pass
        except:
            pass
        # 更新分组筛选选项
        self._update_group_filter_options()
        # 刷新表格显示
        self._sync_tree()

    def _record_execution_start(self, item_id: int):
        """记录脚本启动时间。"""
        if item_id not in self._execution_history:
            self._execution_history[item_id] = []
        self._execution_history[item_id].append({
            "start_time": time.time(),
            "stop_time": None,
            "duration": None,
            "exit_code": None,
            "status": "running",
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
                record["status"] = "crashed" if (exit_code is not None and exit_code != 0) else "stopped"
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
            if exit_code is not None and exit_code != 0:
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
            self._show_notification(name, reason, exit_code, duration)

    def _show_notification(self, name: str, reason: str, exit_code: Optional[int], duration: float):
        """显示异常通知（系统通知或弹窗，可选 Webhook）。"""
        title = self._lang["msg_script_abnormal_exit"]
        msg = self._lang["msg_abnormal_exit"] % (name, reason)
        if exit_code is not None:
            msg += "\n" + self._lang["msg_exit_code"] % exit_code
        msg += "\n" + self._lang["msg_duration"] % duration
        
        # 发送 Webhook 通知（如果启用）
        if self._notification_config.get("webhook_enabled", False):
            webhook_url = self._notification_config.get("webhook_url", "").strip()
            if webhook_url:
                self._send_webhook_notification(name, reason, exit_code, duration, webhook_url)
        
        # 使用弹窗通知（更可靠，不依赖额外库）
        self.root.after(0, lambda: messagebox.showwarning(title, msg, parent=self.root))
    
    def _send_webhook_notification(self, name: str, reason: str, exit_code: Optional[int], duration: float, url: str):
        """发送 Webhook 通知（GET 或 POST）。"""
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
                "status": "异常退出" if self._current_language == "zh_CN" else "Abnormal Exit",
            }
        else:
            data = {}
        
        # 在后台线程中发送，避免阻塞 UI
        def send_request():
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
                        import json
                        json_data = json.dumps(data, ensure_ascii=False).encode("utf-8")
                        req = Request(url, data=json_data, method="POST")
                        req.add_header("Content-Type", "application/json; charset=utf-8")
                    else:
                        req = Request(url, method="POST")
                    with urlopen(req, timeout=5) as response:
                        response.read()
            except Exception as e:
                # 静默失败，不影响主程序
                self.root.after(0, lambda: self.status_var.set(f"Webhook 通知发送失败: {str(e)[:50]}"))
        
        threading.Thread(target=send_request, daemon=True).start()

    def _load_config(self):
        raw = load_config()
        self.items = []
        self._next_id = 1
        for r in raw:
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
                "id": self._next_id,
                "name": r.get("name", "未命名"),
                "path": path,
                "work_dir": r.get("work_dir") or "",
                "group": r.get("group", ""),
                "script_type": stype,
                "interpreter": interpreter,
                # 兼容旧配置
                "is_python": bool(r.get("is_python", False)),
                "python_exe": interpreter if stype == "python" else "",
                "auto_restart": bool(r.get("auto_restart", False)),
                "launch_on_app_start": bool(r.get("launch_on_app_start", False)),
                "log_output": bool(r.get("log_output", False)),
                "schedule_enabled": bool(r.get("schedule_enabled", False)),
                "schedule_action": r.get("schedule_action", "start"),
                "schedule_type": r.get("schedule_type", "daily"),
                "schedule_time": r.get("schedule_time", "09:00"),
                "schedule_interval": r.get("schedule_interval", 5),
                "schedule_weekday": r.get("schedule_weekday", 0),
                "schedule_day": r.get("schedule_day", 1),
                "pid": None,
            })
            self._next_id = max(self._next_id, self.items[-1]["id"]) + 1
        self._next_id = max(self._next_id, 1)
        self._sync_tree()

    def _sync_tree(self):
        # 刷新前记下当前选中行的行号，重建后恢复，避免定时刷新（约 4 秒）清空选中
        try:
            sel = self.tree.selection()
            saved = sorted(set(self.tree.index(i) for i in sel))
        except Exception:
            saved = []
        for i in self.tree.get_children():
            self.tree.delete(i)
        type_names = {
            "python": "Python",
            "node": "Node.js",
            "powershell": "PowerShell",
            "vbs": "VBScript",
            "jscript": "JScript",
            "batch": "批处理",
            "shell": "Shell",
            "wsf": "WSF",
            "exe": "EXE",
        }
        for row, it in enumerate(self.items):
            stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
            # 兼容旧配置
            if not stype or stype == "unknown":
                if it.get("is_python"):
                    stype = "python"
                else:
                    stype = get_script_type(it.get("path", "")) or "batch"
            typ = type_names.get(stype, "批处理")
            pid = it.get("pid")
            # 运行中：进程存在，或 pid 刚设置不足 2 秒（启动宽容，避免瞬间显示已停止）
            pid_set_at = it.get("pid_set_at") or 0
            running = pid and (is_process_running(pid) or (pid_set_at and (time.time() - pid_set_at) < 2))
            # 未运行且路径不存在时显示「路径无效」
            path_invalid = False
            if not running:
                pp = (it.get("path") or "").strip()
                if pp:
                    full = os.path.normpath(os.path.join(it.get("work_dir") or "", pp)) if (it.get("work_dir") and not os.path.isabs(pp)) else pp
                    path_invalid = not os.path.isfile(full)
            status = self._lang["status_running"] if running else (self._lang["status_path_invalid"] if path_invalid else self._lang["status_stopped"])
            pid_s = str(pid) if pid else "-"
            ports_s = "-"
            title_s = "-"
            # 定时任务显示
            schedule_s = "-"
            if it.get("schedule_enabled"):
                schedule_action = it.get("schedule_action", "start")
                action_map = {"start": "启动", "restart": "重启", "stop": "停止"}
                action_cn = action_map.get(schedule_action, schedule_action if schedule_action in action_map.values() else "启动")
                schedule_type = it.get("schedule_type", "daily")
                type_map = {"daily": "每天", "hourly": "每小时", "minutely": "每分钟", "weekly": "每周", "monthly": "每月"}
                schedule_type_cn = type_map.get(schedule_type, schedule_type)
                prefix = f"{action_cn}·"
                if schedule_type_cn == "每天":
                    schedule_s = f"{prefix}每天 {it.get('schedule_time', '09:00')}"
                elif schedule_type_cn == "每小时":
                    schedule_s = f"{prefix}每小时"
                elif schedule_type_cn == "每分钟":
                    interval = it.get("schedule_interval", 5)
                    schedule_s = f"{prefix}每{interval}分钟"
                elif schedule_type_cn == "每周":
                    weekday_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
                    weekday = it.get("schedule_weekday", 0)
                    if isinstance(weekday, str) and weekday.isdigit():
                        weekday = int(weekday)
                    weekday_cn = weekday_map.get(weekday, weekday) if isinstance(weekday, int) else weekday
                    schedule_s = f"{prefix}{weekday_cn} {it.get('schedule_time', '09:00')}"
                elif schedule_type_cn == "每月":
                    try:
                        day = max(1, min(30, int(it.get("schedule_day", 1))))
                    except (TypeError, ValueError):
                        day = 1
                    schedule_s = f"{prefix}每月{day}号 {it.get('schedule_time', '09:00')}"
            if running:
                pids = get_process_tree_pids(pid)
                ports = get_ports_for_pids(pids)
                if ports:
                    ports_s = ",".join(map(str, ports))
                    # 取第一个端口的标题（多个端口时只显示第一个的标题）
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
            filter_group = self.filter_group_var.get()
            all_text = "全部" if self._current_language == "zh_CN" else "All"
            if filter_group and filter_group != all_text and group != filter_group:
                continue
            self.tree.insert("", tk.END, values=(
                str(it.get("id", "")),
                it.get("name", ""),
                group,
                (it.get("path", ""))[:70] + ("..." if len(it.get("path", "")) > 70 else ""),
                (it.get("work_dir", "") or "-")[:50],
                typ,
                status,
                pid_s,
                ports_s,
                title_s,
                schedule_s,
            ))
        children = self.tree.get_children()
        to_sel = [children[i] for i in saved if 0 <= i < len(children)]
        if to_sel:
            self.tree.selection_set(*to_sel)
            self.tree.see(to_sel[0])
        elif children:
            self.tree.see(children[-1])
        self.root.update_idletasks()
        self._update_visit_button_state()
        self._update_group_filter_options()

    def _update_group_filter_options(self):
        """更新分组筛选下拉框的选项（全部 + 所有分组）。"""
        groups = sorted(set(it.get("group", "") for it in self.items if it.get("group")))
        all_text = "全部" if self._current_language == "zh_CN" else "All"
        options = [all_text] + groups
        self.filter_combo["values"] = options
        current = self.filter_group_var.get()
        if current not in options:
            self.filter_group_var.set(all_text)
        # 如果当前是"全部"/"All"，更新为当前语言的文本
        elif current in ("全部", "All"):
            self.filter_group_var.set(all_text)

    def _on_search_locate(self, forward: bool = True):
        """按关键词在列表中搜索并定位。forward=True 为首次定位，False 为下一个匹配。"""
        keyword = (self.search_keyword_var.get() or "").strip()
        if not keyword:
            return
        children = list(self.tree.get_children())
        if not children:
            self.status_var.set(self._lang["msg_search_no_match"] % keyword)
            return
        kw_lower = keyword.lower()
        # 表格列顺序: id, name, group, path, work_dir, type, status, pid, ports, title, schedule
        def row_matches(iid):
            vals = self.tree.item(iid, "values")
            if not vals or len(vals) < 5:
                return False
            for i in range(5):
                if kw_lower in (str(vals[i]) or "").lower():
                    return True
            return False
        start_index = 0
        if not forward:
            sel = self.tree.selection()
            if sel:
                try:
                    idx = children.index(sel[0])
                    start_index = (idx + 1) % len(children)
                except ValueError:
                    pass
        for i in range(len(children)):
            pos = (start_index + i) % len(children)
            iid = children[pos]
            if row_matches(iid):
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                self.status_var.set(self._lang["msg_search_located"] % (pos + 1))
                return
        self.status_var.set(self._lang["msg_search_no_match"] % keyword)

    def _apply_group_filter(self):
        """应用分组筛选。"""
        self._sync_tree()

    def _clear_group_filter(self):
        """清除分组筛选。"""
        all_text = "全部" if self._current_language == "zh_CN" else "All"
        self.filter_group_var.set(all_text)
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
        """鼠标拖拽：选中经过的所有行。"""
        if not self._drag_start_item:
            return
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

    def _on_tree_right_click(self, event):
        """右键菜单：若点击在某行上则先选中该行，再弹出 启动/停止/重启/编辑/删除/复制/全选/取消全选/端口进程。"""
        item = self.tree.identify_row(event.y)
        if item:
            # 如果右键点击的行未选中，先选中它
            if item not in self.tree.selection():
                self.tree.selection_set(item)
            self.tree.focus(item)
        ctx = tk.Menu(self.root, tearoff=0)
        ctx.add_command(label=self._lang["menu_start"], command=self._on_start)
        ctx.add_command(label=self._lang["menu_stop"], command=self._on_stop)
        ctx.add_command(label=self._lang["menu_restart"], command=self._on_restart)
        ctx.add_separator()
        ctx.add_command(label=self._lang["menu_edit"], command=self._on_edit)
        ctx.add_command(label=self._lang["menu_delete"], command=self._on_delete)
        ctx.add_command(label=self._lang["menu_duplicate"], command=self._on_duplicate)
        ctx.add_separator()
        # 获取选中项信息
        rows = self._get_selected_rows()
        it = None
        if rows:
            it = self._item_at(rows[0])
        
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
        ctx.add_command(label=self._lang["menu_ports"], command=self._on_show_ports)
        it, ports = self._get_ports_for_selected()
        if ports:
            ctx.add_separator()
            for p in ports[:6]:
                ctx.add_command(label=self._lang["menu_visit"] % p, command=lambda _p=p: self._on_visit_port(_p))
        ctx.tk_popup(event.x_root, event.y_root)

    def _save_config(self):
        arr = []
        for it in self.items:
            stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
            # 兼容旧配置：从 is_python 推断
            if not stype or stype == "unknown":
                if it.get("is_python"):
                    stype = "python"
                else:
                    stype = get_script_type(it.get("path", "")) or "batch"
            interpreter = it.get("interpreter") or it.get("python_exe") or ""
            arr.append({
                "name": it.get("name", ""),
                "path": it.get("path", ""),
                "work_dir": it.get("work_dir") or "",
                "group": it.get("group", ""),
                "script_type": stype,
                "interpreter": interpreter,
                # 兼容旧配置
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
            })
        if not save_config(arr):
            self.status_var.set(self._lang["status_config_save_failed"])

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
        restarted = None
        for it in self.items:
            pid = it.get("pid")
            if not pid:
                continue
            if is_process_running(pid):
                continue
            # 启动宽容：pid 刚设置不足 2 秒，可能是启动延迟，暂不判为已结束
            pid_set_at = it.get("pid_set_at") or 0
            if pid_set_at and (time.time() - pid_set_at) < 2:
                continue
            # 进程已结束
            if it.get("auto_restart"):
                it.pop("_popen", None)
                logpath = self._make_log_path(it)
                stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
                # 兼容旧配置：从 is_python 推断
                if not stype or stype == "unknown":
                    if it.get("is_python"):
                        stype = "python"
                    else:
                        stype = get_script_type(it.get("path", "")) or "batch"
                interpreter = it.get("interpreter") or it.get("python_exe") or None
                p = start_script(
                    it.get("path", ""),
                    it.get("work_dir") or None,
                    stype,
                    interpreter,
                    log_path=logpath,
                )
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
            path_to_pids = {}  # norm_path -> [pid, ...]，同一脚本多实例时多个 pid
            # 批处理/Python/Node 等（scan_running_script_processes 不包含 .exe）
            for r in scan_running_script_processes():
                sp = (r.get("script_path") or "").strip()
                if not sp:
                    continue
                try:
                    np = os.path.normcase(os.path.abspath(sp))
                except Exception:
                    continue
                path_to_pids.setdefault(np, []).append(r["pid"])
            # 补充 .exe 进程，使列表中已添加的 exe 在外部运行或 pid 丢失时也能被接管并显示为运行中
            for r in scan_running_exe_processes():
                sp = (r.get("script_path") or "").strip()
                if not sp:
                    continue
                try:
                    np = os.path.normcase(os.path.abspath(sp))
                except Exception:
                    continue
                path_to_pids.setdefault(np, []).append(r["pid"])
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

    def _start_refresh_loop(self):
        if self._refresh_paused:
            return  # 暂停时不刷新、不续订，等点「恢复刷新」再跑
        self._refresh_status(and_set_status=False)
        self.root.after(self._refresh_interval_ms, self._start_refresh_loop)

    def _start_schedule_check_loop(self):
        """定时检查并触发定时任务（每10秒检查一次，提高精度）。"""
        if not self._refresh_paused:
            self._check_schedules()
        self.root.after(10000, self._start_schedule_check_loop)  # 每10秒检查一次，提高定时精度

    def _check_schedules(self):
        """检查所有启用了定时任务的项，如果到时间则启动。"""
        now = datetime.now()
        for it in self.items:
            if not it.get("schedule_enabled"):
                continue
            schedule_action = it.get("schedule_action", "start")
            action_map = {"start": "启动", "restart": "重启", "stop": "停止"}
            action_cn = action_map.get(schedule_action, schedule_action if schedule_action in action_map.values() else "启动")
            
            # 如果行为是"启动"且已经在运行，跳过（重启和停止不受此限制）
            if (schedule_action == "start" or action_cn == "启动") and it.get("pid") and is_process_running(it.get("pid")):
                continue
            item_id = it.get("id")
            schedule_type = it.get("schedule_type", "daily")
            schedule_time = it.get("schedule_time", "09:00")
            schedule_interval = it.get("schedule_interval", 5)
            schedule_weekday = it.get("schedule_weekday", 0)
            last_run = self._schedule_last_run.get(item_id, 0)
            should_run = False
            try:
                # 统一转换为中文类型名
                type_map = {"daily": "每天", "hourly": "每小时", "minutely": "每分钟", "weekly": "每周", "monthly": "每月"}
                schedule_type = type_map.get(schedule_type, schedule_type)
                try:
                    schedule_day = max(1, min(30, int(it.get("schedule_day", 1))))
                except (TypeError, ValueError):
                    schedule_day = 1
                
                if schedule_type == "每天":
                    # 每天指定时间
                    hour, minute = map(int, schedule_time.split(":"))
                    target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    # 对于停止行为，时间到达后持续有效（直到执行）；对于其他行为，使用2分钟窗口
                    if schedule_action == "stop" or action_cn == "停止":
                        # 停止：如果当前时间 >= 目标时间，且今天还没执行过，就执行
                        if now >= target_time:
                            # 检查是否今天已经执行过（比较日期）
                            last_run_dt = datetime.fromtimestamp(last_run) if last_run > 0 else datetime(1970, 1, 1)
                            if last_run_dt.date() < now.date() or (last_run_dt.date() == now.date() and last_run_dt < target_time):
                                should_run = True
                    else:
                        # 启动/重启：使用2分钟窗口
                        if target_time <= now < target_time + timedelta(minutes=2):
                            if last_run < target_time.timestamp():
                                should_run = True
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
                    # 每周指定星期几的指定时间
                    weekday_map = {"周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6, 0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6}
                    if isinstance(schedule_weekday, str) and schedule_weekday.isdigit():
                        target_weekday = int(schedule_weekday)
                    else:
                        target_weekday = weekday_map.get(schedule_weekday, 0)
                    hour, minute = map(int, schedule_time.split(":"))
                    if now.weekday() == target_weekday:
                        target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                        # 对于停止行为，时间到达后持续有效
                        if schedule_action == "stop" or action_cn == "停止":
                            if now >= target_time:
                                last_run_dt = datetime.fromtimestamp(last_run) if last_run > 0 else datetime(1970, 1, 1)
                                if last_run_dt.date() < now.date() or (last_run_dt.date() == now.date() and last_run_dt < target_time):
                                    should_run = True
                        else:
                            # 启动/重启：使用2分钟窗口
                            if target_time <= now < target_time + timedelta(minutes=2):
                                if last_run < target_time.timestamp():
                                    should_run = True
                elif schedule_type == "每月":
                    # 每月几号（1-30）的指定时间
                    if now.day == schedule_day:
                        hour, minute = map(int, schedule_time.split(":"))
                        target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                        if schedule_action == "stop" or action_cn == "停止":
                            if now >= target_time:
                                last_run_dt = datetime.fromtimestamp(last_run) if last_run > 0 else datetime(1970, 1, 1)
                                if last_run_dt.date() < now.date() or (last_run_dt.date() == now.date() and last_run_dt < target_time):
                                    should_run = True
                        else:
                            if target_time <= now < target_time + timedelta(minutes=2):
                                if last_run < target_time.timestamp():
                                    should_run = True
            except (ValueError, AttributeError):
                continue
            if should_run:
                schedule_action = it.get("schedule_action", "start")
                action_map = {"start": "启动", "restart": "重启", "stop": "停止"}
                action_cn = action_map.get(schedule_action, schedule_action if schedule_action in action_map.values() else "启动")
                
                if schedule_action == "stop" or action_cn == "停止":
                    # 定时停止：如果正在运行则停止
                    pid = it.get("pid")
                    if pid and is_process_running(pid):
                        pids = get_process_tree_pids(pid)
                        for px in pids:
                            kill_process(px)
                        it["pid"] = None
                        it.pop("pid_set_at", None)
                        it.pop("_popen", None)
                        self._schedule_last_run[item_id] = time.time()
                        name = it.get("name", "未命名")
                        # 立即刷新状态和UI（在主线程中执行）
                        def update_ui():
                            self.status_var.set(self._lang["status_schedule_triggered_stopped"] % name)
                            self._sync_tree()
                            self._refresh_status(and_set_status=False)
                        self.root.after(0, update_ui)
                    else:
                        # 即使未运行，也记录执行时间，避免重复检查
                        self._schedule_last_run[item_id] = time.time()
                    continue
                elif schedule_action == "restart" or action_cn == "重启":
                    # 定时重启：先停止再启动
                    pid = it.get("pid")
                    if pid and is_process_running(pid):
                        pids = get_process_tree_pids(pid)
                        for px in pids:
                            kill_process(px)
                        it["pid"] = None
                        it.pop("pid_set_at", None)
                        it.pop("_popen", None)
                        # 等待一下再启动
                        time.sleep(0.5)
                
                # 启动脚本（启动或重启）
                logpath = self._make_log_path(it)
                stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
                if not stype or stype == "unknown":
                    if it.get("is_python"):
                        stype = "python"
                    else:
                        stype = get_script_type(it.get("path", "")) or "batch"
                interpreter = it.get("interpreter") or it.get("python_exe") or None
                p = start_script(
                    it.get("path", ""),
                    it.get("work_dir") or None,
                    stype,
                    interpreter,
                    log_path=logpath,
                )
                if p:
                    it["pid"] = p.pid
                    it["pid_set_at"] = time.time()
                    it["_popen"] = p
                    if logpath:
                        it["_log_path"] = logpath
                    # 记录执行开始（定时任务）
                    if item_id:
                        self._record_execution_start(item_id)
                    self._schedule_last_run[item_id] = time.time()
                    name = it.get("name", "未命名" if self._current_language == "zh_CN" else "Unnamed")
                    if schedule_action == "restart" or action_cn == "重启":
                        action_text = self._lang["schedule_action_restart"]
                    else:
                        action_text = self._lang["schedule_action_start"]
                    self.status_var.set(self._lang["status_schedule_triggered"] % (name, action_text))
                    self._sync_tree()

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

    def _on_add(self):
        all_ext = "*.bat;*.cmd;*.py;*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx;*.ps1;*.vbs;*.wsf;*.sh;*.exe"
        if sys.platform != "win32":
            filetypes = [
                ("Shell", "*.sh"),
                ("Python", "*.py"),
                ("Node.js", "*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx"),
                ("PowerShell", "*.ps1"),
                ("全部脚本", all_ext) if self._lang == LANGUAGES["zh_CN"] else ("All Scripts", all_ext),
                ("全部", "*.*") if self._lang == LANGUAGES["zh_CN"] else ("All Files", "*.*"),
            ]
        else:
            filetypes = [
                ("全部脚本", all_ext) if self._lang == LANGUAGES["zh_CN"] else ("All Scripts", all_ext),
                ("批处理", "*.bat;*.cmd") if self._lang == LANGUAGES["zh_CN"] else ("Batch", "*.bat;*.cmd"),
                ("Python", "*.py"),
                ("Node.js", "*.js;*.mjs;*.cjs;*.ts;*.jsx;*.tsx"),
                ("PowerShell", "*.ps1"),
                ("VBScript", "*.vbs"),
                ("WSF", "*.wsf"),
                ("Shell", "*.sh"),
                ("可执行文件", "*.exe") if self._lang == LANGUAGES["zh_CN"] else ("Executable", "*.exe"),
                ("全部", "*.*") if self._lang == LANGUAGES["zh_CN"] else ("All Files", "*.*"),
            ]
        path = filedialog.askopenfilename(
            title=self._lang["dialog_select_script"],
            filetypes=filetypes,
        )
        if not path:
            return
        stype = get_script_type(path)
        initial = {
            "name": os.path.splitext(os.path.basename(path))[0] or "未命名",
            "path": path,
            "work_dir": os.path.dirname(path),
            "group": "",
            "interpreter": "",
            "is_python": stype == "python",
            "python_exe": "",
            "auto_restart": False,
            "log_output": False,
        }
        # 确保对话框使用当前语言设置
        d = AddEditDialog(self.root, "add", initial)
        d._lang = self._lang
        # 重新初始化界面文本
        d._reinit_ui_text()
        self.root.wait_window(d)
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
            "launch_on_app_start": r.get("launch_on_app_start", False),
            "log_output": r.get("log_output", False),
            "schedule_enabled": r.get("schedule_enabled", False),
            "schedule_action": r.get("schedule_action", "start"),
            "schedule_type": r.get("schedule_type", "daily"),
            "schedule_time": r.get("schedule_time", "09:00"),
            "schedule_interval": r.get("schedule_interval", 5),
            "schedule_weekday": r.get("schedule_weekday", 0),
            "schedule_day": r.get("schedule_day", 1),
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
            "name": it.get("name", ""),
            "path": it.get("path", ""),
            "work_dir": it.get("work_dir", ""),
            "group": it.get("group", ""),
            "interpreter": interpreter,
            "is_python": stype == "python",
            "python_exe": interpreter if stype == "python" else "",
            "auto_restart": it.get("auto_restart", False),
            "log_output": it.get("log_output", False),
            "schedule_enabled": it.get("schedule_enabled", False),
            "schedule_action": it.get("schedule_action", "start"),
            "schedule_type": it.get("schedule_type", "daily"),
            "schedule_time": it.get("schedule_time", "09:00"),
            "schedule_interval": it.get("schedule_interval", 5),
            "schedule_weekday": it.get("schedule_weekday", 0),
            "schedule_day": it.get("schedule_day", 1),
        }
        # 确保对话框使用当前语言设置
        d = AddEditDialog(self.root, "edit", initial)
        d._lang = self._lang
        # 重新初始化界面文本
        d._reinit_ui_text()
        self.root.wait_window(d)
        if not getattr(d, "result", None):
            return
        r = d.result
        it["name"] = r["name"]
        it["path"] = r["path"]
        it["work_dir"] = r["work_dir"]
        it["group"] = r.get("group", "")
        it["script_type"] = r.get("script_type", get_script_type(r["path"]) or "batch")
        it["interpreter"] = r.get("interpreter", "")
        it["is_python"] = it["script_type"] == "python"
        it["python_exe"] = it["interpreter"] if it["script_type"] == "python" else ""
        it["auto_restart"] = r.get("auto_restart", False)
        it["launch_on_app_start"] = r.get("launch_on_app_start", it.get("launch_on_app_start", False))
        it["log_output"] = r.get("log_output", False)
        it["schedule_enabled"] = r.get("schedule_enabled", False)
        it["schedule_action"] = r.get("schedule_action", "start")
        it["schedule_type"] = r.get("schedule_type", "daily")
        it["schedule_time"] = r.get("schedule_time", "09:00")
        it["schedule_interval"] = r.get("schedule_interval", 5)
        it["schedule_weekday"] = r.get("schedule_weekday", 0)
        it["schedule_day"] = r.get("schedule_day", 1)
        self._save_config()
        self._sync_tree()
        self.status_var.set(self._lang["status_updated"] % r["name"])

    def _on_delete(self):
        rows = sorted(self._get_selected_rows(), reverse=True)
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_items"])
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
                self.items.pop(r)
        self._save_config()
        self._sync_tree()
        self.status_var.set(self._lang["status_deleted"])

    def _check_quick_exit(self, *pids):
        """启动约 1.5 秒后检查：若进程已退出则提示运行时长、退出码，若有 log 可读则展示"""
        exited = []  # [(name, duration, rc, logpath, tail_or_none), ...]
        for pid in pids:
            if not pid or is_process_running(pid):
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

    def _on_start(self):
        rows = self._get_selected_rows()
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_items"])
            return
        started_pids = []
        for r in rows:
            it = self._item_at(r)
            if not it:
                continue
            if it.get("pid") and is_process_running(it["pid"]):
                self.status_var.set(self._lang["status_script_running"] % it.get('name'))
                continue
            logpath = self._make_log_path(it)
            stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
            # 兼容旧配置：从 is_python 推断
            if not stype or stype == "unknown":
                if it.get("is_python"):
                    stype = "python"
                else:
                    stype = get_script_type(it.get("path", "")) or "batch"
            interpreter = it.get("interpreter") or it.get("python_exe") or None
            p = start_script(
                it.get("path", ""),
                it.get("work_dir") or None,
                stype,
                interpreter,
                log_path=logpath,
            )
            if p:
                it["pid"] = p.pid
                it["pid_set_at"] = time.time()
                it["_popen"] = p
                if logpath:
                    it["_log_path"] = logpath
                # 记录执行开始
                item_id = it.get("id")
                if item_id:
                    self._record_execution_start(item_id)
                started_pids.append(p.pid)
                self.status_var.set(self._lang["msg_started"] % (it.get('name'), p.pid))
            else:
                self.status_var.set(self._lang["msg_start_failed"] % it.get('path',''))
        self._sync_tree()
        if started_pids:
            self.root.after(1500, lambda pids=started_pids: self._check_quick_exit(*pids))

    def _on_stop(self):
        rows = self._get_selected_rows()
        if not rows:
            messagebox.showinfo(self._lang["msg_info"], self._lang["msg_select_items"])
            return
        stopped_any = False
        for r in rows:
            it = self._item_at(r)
            if not it:
                continue
            pid = it.get("pid")
            if pid and is_process_running(pid):
                pids = get_process_tree_pids(pid)
                for px in pids:
                    kill_process(px)
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
        self._on_stop()  # 会 _sync_tree 并清空选中
        def _restart_phase2():
            # 恢复选中，使 _on_start 能作用到同一批
            children = self.tree.get_children()
            to_sel = [children[r] for r in rows if 0 <= r < len(children)]
            if to_sel:
                self.tree.selection_set(*to_sel)
            self._on_start()
        self.root.after(400, _restart_phase2)

    def _on_start_all(self):
        n = 0
        started_pids = []
        for it in self.items:
            if it.get("pid") and is_process_running(it["pid"]):
                continue
            logpath = self._make_log_path(it)
            stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
            # 兼容旧配置：从 is_python 推断
            if not stype or stype == "unknown":
                if it.get("is_python"):
                    stype = "python"
                else:
                    stype = get_script_type(it.get("path", "")) or "batch"
            interpreter = it.get("interpreter") or it.get("python_exe") or None
            p = start_script(
                it.get("path", ""),
                it.get("work_dir") or None,
                stype,
                interpreter,
                log_path=logpath,
            )
            if p:
                it["pid"] = p.pid
                it["pid_set_at"] = time.time()
                it["_popen"] = p
                if logpath:
                    it["_log_path"] = logpath
                # 记录执行开始
                item_id = it.get("id")
                if item_id:
                    self._record_execution_start(item_id)
                n += 1
                started_pids.append(p.pid)
        self._sync_tree()
        self.status_var.set(self._lang["msg_start_all"] % n)
        if started_pids:
            self.root.after(1500, lambda pids=started_pids: self._check_quick_exit(*pids))

    def _on_stop_all(self):
        n = 0
        for it in self.items:
            pid = it.get("pid")
            if pid and is_process_running(pid):
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
                "pid": None,
            })
            self._next_id += 1
        self._save_config()
        self._sync_tree()
        self.status_var.set(self._lang["msg_copied"] % len(rows))

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
            if not pid or not is_process_running(pid):
                continue
            pids = get_process_tree_pids(pid)
            ports = get_ports_for_pids(pids)
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
        """导出配置到文件，便于备份或迁移。"""
        path = filedialog.asksaveasfilename(
            title=self._lang["dialog_export_config"],
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), (self._lang.get("filetype_all", "All"), "*.*")],
            initialfile="batmanager_config_backup.json",
        )
        if not path:
            return
        try:
            arr = []
            for it in self.items:
                stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
                # 兼容旧配置：从 is_python 推断
                if not stype or stype == "unknown":
                    if it.get("is_python"):
                        stype = "python"
                    else:
                        stype = get_script_type(it.get("path", "")) or "batch"
                interpreter = it.get("interpreter") or it.get("python_exe") or ""
                arr.append({
                    "name": it.get("name", ""),
                    "path": it.get("path", ""),
                    "work_dir": it.get("work_dir") or "",
                    "group": it.get("group", ""),
                    "script_type": stype,
                    "interpreter": interpreter,
                    # 兼容旧配置
                    "is_python": bool(it.get("is_python", False)) or (stype == "python"),
                    "python_exe": interpreter if stype == "python" else "",
                    "auto_restart": it.get("auto_restart", False),
                    "launch_on_app_start": it.get("launch_on_app_start", False),
                    "log_output": it.get("log_output", False),
                })
            with open(path, "w", encoding="utf-8") as f:
                json.dump(arr, f, ensure_ascii=False, indent=2)
            self.status_var.set(self._lang["msg_export_to"] % path)
            messagebox.showinfo(self._lang["msg_success"], self._lang["msg_export_success"] % path)
        except Exception as e:
            messagebox.showerror(self._lang["msg_failed"], self._lang["msg_export_failed"] % str(e))

    def _on_import_config(self):
        """从文件导入配置，可选择合并或替换。"""
        path = filedialog.askopenfilename(
            title=self._lang["dialog_import_config"],
            filetypes=[("JSON", "*.json"), (self._lang.get("filetype_all", "All"), "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                imported = json.load(f)
            if not isinstance(imported, list):
                messagebox.showerror(self._lang["msg_failed"], self._lang["msg_import_failed_format"])
                return
            if not imported:
                messagebox.showinfo(self._lang["msg_info"], self._lang["msg_import_empty"])
                return
            action = messagebox.askyesnocancel(
                self._lang["msg_import_way"],
                self._lang["msg_import_found"] % len(imported) +
                self._lang["msg_import_merge"] +
                self._lang["msg_import_replace"] +
                self._lang["msg_import_cancel"],
            )
            if action is None:
                return
            existing_paths = {it.get("path", "") for it in self.items}
            added, skipped = 0, 0
            if not action:
                self.items = []
                self._next_id = 1
            for r in imported:
                path_val = r.get("path", "").strip()
                if not path_val:
                    continue
                if action and path_val in existing_paths:
                    skipped += 1
                    continue
                stype = r.get("script_type") or (get_script_type(path_val) if path_val else "batch")
                if not stype or stype == "unknown":
                    if r.get("is_python"):
                        stype = "python"
                    else:
                        stype = get_script_type(path_val) or "batch"
                interpreter = r.get("interpreter") or r.get("python_exe") or ""
                self.items.append({
                    "id": self._next_id,
                    "name": r.get("name", "未命名"),
                    "path": path_val,
                    "work_dir": r.get("work_dir") or "",
                    "group": r.get("group", ""),
                    "script_type": stype,
                    "interpreter": interpreter,
                    "is_python": stype == "python",
                    "python_exe": interpreter if stype == "python" else "",
                    "auto_restart": bool(r.get("auto_restart", False)),
                    "launch_on_app_start": bool(r.get("launch_on_app_start", False)),
                    "log_output": bool(r.get("log_output", False)),
                    "schedule_enabled": bool(r.get("schedule_enabled", False)),
                    "schedule_action": r.get("schedule_action", "start"),
                    "schedule_type": r.get("schedule_type", "daily"),
                    "schedule_time": r.get("schedule_time", "09:00"),
                    "schedule_interval": r.get("schedule_interval", 5),
                    "schedule_weekday": r.get("schedule_weekday", 0),
                    "schedule_day": r.get("schedule_day", 1),
                    "pid": None,
                })
                self._next_id += 1
                added += 1
                existing_paths.add(path_val)
            self._save_config()
            self._sync_tree()
            msg = self._lang["msg_imported"] % added
            if skipped:
                msg += self._lang["msg_import_skipped_detail"] % skipped
            self.status_var.set(self._lang["status_import_prefix"] + msg)
            skipped_msg = (self._lang["msg_skipped_paths"] % skipped) if skipped else ""
            messagebox.showinfo(self._lang["msg_import_complete_title"], msg + skipped_msg)
        except json.JSONDecodeError as e:
            messagebox.showerror(self._lang["msg_failed"], self._lang["msg_import_json_error"] % str(e))
        except Exception as e:
            messagebox.showerror(self._lang["msg_failed"], self._lang["msg_import_error"] % str(e))

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

    def _on_notification_config(self):
        """打开通知配置窗口。"""
        NotificationConfigDialog(self.root, self)

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
            copied_text = "已复制!" if self._current_language == "zh_CN" else "Copied!"
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
            copied_text = "已复制!" if self._current_language == "zh_CN" else "Copied!"
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
        """在主循环已启动后调用，异步加载本地 IP 与外网 IP，避免子线程里 root.after 报 main thread is not in main loop。"""
        threading.Thread(target=self._load_local_ip, daemon=True).start()
        threading.Thread(target=self._load_public_ip, daemon=True).start()

    def _on_window_close(self):
        """关闭窗口即退出。"""
        self.root.quit()

    def _do_launch_on_start_items(self):
        """启动所有勾选了「启动本工具时自动启动」的任务。在后台线程执行 Popen，避免阻塞主循环。"""
        to_start = []
        for it in self.items:
            if not it.get("launch_on_app_start"):
                continue
            if it.get("pid") and is_process_running(it["pid"]):
                continue
            logpath = self._make_log_path(it)
            stype = it.get("script_type") or (get_script_type(it.get("path", "")) if it.get("path") else "batch")
            if not stype or stype == "unknown":
                if it.get("is_python"):
                    stype = "python"
                else:
                    stype = get_script_type(it.get("path", "")) or "batch"
            interpreter = it.get("interpreter") or it.get("python_exe") or None
            to_start.append((it, logpath, stype, interpreter))
        if not to_start:
            return

        def run_in_thread():
            results = []
            for i, (it, logpath, stype, interpreter) in enumerate(to_start):
                if i > 0:
                    time.sleep(1.0)
                p = None
                for attempt in range(3):
                    try:
                        p = start_script(
                            it.get("path", ""),
                            it.get("work_dir") or None,
                            stype,
                            interpreter,
                            log_path=logpath,
                        )
                        if p:
                            break
                    except Exception:
                        pass
                    if attempt < 2:
                        time.sleep(0.6)
                if p:
                    results.append((it, p, logpath))
            if results:
                self.root.after(0, lambda: self._apply_launch_on_start_results(results))

        threading.Thread(target=run_in_thread, daemon=True).start()

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
        if not getattr(self, "_launch_on_start_retry_scheduled", False):
            self._launch_on_start_retry_scheduled = True
            self.root.after(8000, self._do_launch_on_start_items)

    def run(self):
        self.root.after(0, self._start_ip_load_threads)  # 主循环启动后立即调度，再启动 IP 加载线程
        self._launch_on_start_retry_scheduled = False
        self.root.after(400, self._do_launch_on_start_items)
        self.root.mainloop()


def cli_main() -> int:
    """CLI 入口：见 WindowsBatchScriptManager_api_server 或 命令行 --list --start 等。"""
    def _items():
        raw = load_config()
        out = []
        for i, r in enumerate(raw):
            path = r.get("path", "")
            stype = r.get("script_type") or ("python" if r.get("is_python") else (get_script_type(path) or "batch"))
            interp = r.get("interpreter") or r.get("python_exe") or ""
            out.append({"id": i + 1, "name": r.get("name", "未命名"), "path": path, "work_dir": r.get("work_dir") or "", "group": r.get("group", ""), "script_type": stype, "interpreter": interp, "is_python": stype == "python", "python_exe": interp if stype == "python" else "", "auto_restart": bool(r.get("auto_restart", False)), "launch_on_app_start": bool(r.get("launch_on_app_start", False)), "log_output": bool(r.get("log_output", False))})
        return out

    def _running_map():
        m = {}
        if HAS_PSUTIL:
            for x in scan_running_script_processes():
                sp = (x.get("script_path") or "").strip()
                if sp:
                    m[os.path.normcase(os.path.abspath(sp))] = x.get("pid")
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

    config_dir = str(CONFIG_PATH.resolve().parent)

    def _effective_work_dir(it):
        """相对路径且未配置 work_dir 时，以配置目录为基准，避免 API 子进程 cwd 与 GUI 不一致导致找不到脚本。"""
        w = (it.get("work_dir") or "").strip()
        if w:
            return w
        p = (it.get("path") or "").strip()
        if p and not os.path.isabs(p):
            return config_dir
        return None

    def _resolved_path(it):
        """与 start_script 一致：返回该条脚本解析后的绝对路径（用于 run_map 等）。"""
        path = (it.get("path") or "").strip()
        if not path:
            return ""
        work = _effective_work_dir(it)
        if work and not os.path.isabs(path):
            path = os.path.normpath(os.path.join(work, path))
        return os.path.normpath(os.path.abspath(path))

    def _to_save(items):
        return [{"name": it.get("name", ""), "path": it.get("path", ""), "work_dir": it.get("work_dir") or "", "group": it.get("group", ""), "script_type": it.get("script_type", "batch"), "interpreter": it.get("interpreter") or "", "is_python": it.get("is_python", False), "python_exe": it.get("python_exe") or "", "auto_restart": it.get("auto_restart", False), "launch_on_app_start": it.get("launch_on_app_start", False), "log_output": it.get("log_output", False), "schedule_enabled": it.get("schedule_enabled", False), "schedule_action": it.get("schedule_action", "start"), "schedule_type": it.get("schedule_type", "daily"), "schedule_time": it.get("schedule_time", "09:00"), "schedule_interval": it.get("schedule_interval", 5), "schedule_weekday": it.get("schedule_weekday", 0), "schedule_day": it.get("schedule_day", 1)} for it in items]

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
    ap.add_argument("--delete", metavar="ID")
    ap.add_argument("--update", metavar="ID")
    ap.add_argument("--export", metavar="F", nargs="?", const="")
    ap.add_argument("--import", dest="import_", metavar="F")
    ap.add_argument("--replace", action="store_true")
    args = ap.parse_args()

    if args.config_path:
        print(CONFIG_PATH.resolve())
        return 0

    items = _items()
    run_map = _running_map()

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
        if args.replace:
            arr = []
            for r in new_items:
                if not isinstance(r, dict):
                    continue
                path_s = r.get("path", "")
                stype = r.get("script_type") or ("python" if r.get("is_python") else (get_script_type(path_s) or "batch"))
                interp = r.get("interpreter") or r.get("python_exe") or ""
                arr.append({"name": r.get("name", ""), "path": path_s, "work_dir": r.get("work_dir") or "", "group": r.get("group", ""), "script_type": stype, "interpreter": interp, "is_python": stype == "python", "python_exe": interp if stype == "python" else "", "auto_restart": bool(r.get("auto_restart", False)), "launch_on_app_start": bool(r.get("launch_on_app_start", False)), "log_output": bool(r.get("log_output", False)), "schedule_enabled": False, "schedule_action": "start", "schedule_type": "daily", "schedule_time": "09:00", "schedule_interval": 5, "schedule_weekday": 0, "schedule_day": 1})
            save_config(arr)
            return 0
        cur = load_config()
        seen = {os.path.normcase(os.path.abspath((r.get("path") or "").strip())) for r in cur}
        merged = list(cur)
        for r in new_items:
            if not isinstance(r, dict):
                continue
            p = (r.get("path") or "").strip()
            if p and os.path.normcase(os.path.abspath(p)) not in seen:
                merged.append(r)
                seen.add(os.path.normcase(os.path.abspath(p)))
        save_config(merged)
        if not args.json:
            print("Merged; total:", len(merged))
        return 0

    if args.delete:
        it = _find(items, args.delete)
        if not it:
            print("Not found:", args.delete, file=sys.stderr)
            return 1
        abs_path = _resolved_path(it)
        pid = run_map.get(os.path.normcase(abs_path))
        if pid:
            for px in get_process_tree_pids(pid):
                kill_process(px)
        new_items = [x for x in items if x.get("id") != it.get("id")]
        save_config(_to_save(new_items))
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
        new_list = [updated if x.get("id") == it.get("id") else x for x in items]
        save_config(_to_save(new_list))
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
        new_item = {"name": name, "path": path, "work_dir": (args.work_dir or "").strip() if args.work_dir is not None else "", "group": (args.group or "").strip() if args.group is not None else "", "script_type": stype, "interpreter": interp, "is_python": stype == "python", "python_exe": interp if stype == "python" else "", "auto_restart": bool(args.auto_restart), "launch_on_app_start": False, "log_output": bool(args.log_output), "schedule_enabled": False, "schedule_action": "start", "schedule_type": "daily", "schedule_time": "09:00", "schedule_interval": 5, "schedule_weekday": 0, "schedule_day": 1}
        items.append({**new_item, "id": len(items) + 1})
        save_config(_to_save(items))
        if args.json:
            print(json.dumps({"ok": True, "id": len(items), "name": name}, ensure_ascii=False))
        else:
            print("Added id=%s" % len(items))
        return 0

    if args.list:
        out = []
        for it in items:
            abs_path = _resolved_path(it)
            pid = run_map.get(os.path.normcase(abs_path))
            out.append({"id": it["id"], "name": it.get("name", ""), "group": it.get("group", ""), "path": it.get("path", ""), "running": pid is not None, "pid": pid, "script_type": it.get("script_type", "batch")})
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
            abs_path = _resolved_path(it)
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
        abs_path = _resolved_path(it)
        if run_map.get(os.path.normcase(abs_path)):
            if args.json:
                print(json.dumps({"ok": True, "id": it["id"], "message": "already running", "pid": run_map.get(os.path.normcase(abs_path))}, ensure_ascii=False))
            else:
                print("Already running id=%s pid=%s" % (it["id"], run_map.get(os.path.normcase(abs_path))))
            return 0
        p = start_script(it.get("path", ""), _effective_work_dir(it), it.get("script_type", "batch"), it.get("interpreter") or None, log_path=_log_path(it))
        if not p:
            print("Start failed", file=sys.stderr)
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
        abs_path = _resolved_path(it)
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
        abs_path = _resolved_path(it)
        pid = run_map.get(os.path.normcase(abs_path))
        if pid:
            for px in get_process_tree_pids(pid):
                kill_process(px)
            time.sleep(0.5)
        p = start_script(it.get("path", ""), _effective_work_dir(it), it.get("script_type", "batch"), it.get("interpreter") or None, log_path=_log_path(it))
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
            abs_path = _resolved_path(it)
            if run_map.get(os.path.normcase(abs_path)):
                continue
            p = start_script(it.get("path", ""), _effective_work_dir(it), it.get("script_type", "batch"), it.get("interpreter") or None, log_path=_log_path(it))
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
            abs_path = _resolved_path(it)
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
        for it in items:
            abs_path = _resolved_path(it)
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
            abs_path = _resolved_path(it)
            if run_map.get(os.path.normcase(abs_path)):
                continue
            p = start_script(it.get("path", ""), _effective_work_dir(it), it.get("script_type", "batch"), it.get("interpreter") or None, log_path=_log_path(it))
            if p:
                started.append({"id": it["id"], "name": it.get("name"), "pid": p.pid})
                run_map[os.path.normcase(abs_path)] = p.pid
        if args.json:
            print(json.dumps({"ok": True, "stopped": stopped, "started": started}, ensure_ascii=False))
        else:
            print("Restart-all: stopped %s, started %s" % (len(stopped), len(started)))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        first = (sys.argv[1] or "").strip().lower()
        if first in ("--list", "-l", "--status", "--start", "--stop", "--restart", "--start-all", "--stop-all", "--restart-all",
                     "--config-path", "--add", "--delete", "--update", "--export", "--import", "--replace", "--json") or first.startswith("--"):
            sys.exit(cli_main())
    BatchManagerApp().run()
