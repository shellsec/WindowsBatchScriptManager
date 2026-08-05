# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for WindowsBatchScriptManager GUI exe (不含 FastAPI/uvicorn)。"""
import os
import sys

# 嵌套平台目录已清理，无需再放大递归深度
ROOT = os.path.dirname(os.path.abspath(SPEC))
ICON = os.path.join(ROOT, "tray_icon_5728293646040626821.ico")
DATAS = [(ICON, ".")] if os.path.isfile(ICON) else []

EXCLUDES = [
    "win32com", "pythoncom", "pywintypes", "win32api", "win32gui", "win32con",
    "fastapi", "uvicorn", "starlette", "pydantic", "pydantic_core", "anyio",
    "matplotlib", "numpy", "pandas", "scipy", "IPython", "jupyter", "notebook",
    "pytest", "sphinx", "docutils", "babel", "pygments",
    "windnd",
    "macBatchScriptManager", "linuxBatchScriptManager",
    "web_dashboard", "api_server", "WindowsBatchScriptManager_api_server",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=DATAS,
    hiddenimports=["psutil"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WindowsBatchScriptManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[ICON] if os.path.isfile(ICON) else None,
)
