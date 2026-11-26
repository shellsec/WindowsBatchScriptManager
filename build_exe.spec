# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for WindowsBatchScriptManager GUI exe (不含 FastAPI/uvicorn)。"""
import hashlib
import os
from pathlib import Path
from ui_language import LOCALE_MODULES

# 嵌套平台目录已清理，无需再放大递归深度
ROOT = os.path.dirname(os.path.abspath(SPEC))
ICON = os.path.join(ROOT, "tray_icon_5728293646040626821.ico")
DATAS = [(ICON, ".")] if os.path.isfile(ICON) else []
DATAS.append((os.path.join(ROOT, "remote_access.ps1"), "."))
SSH_INSTALLER = os.environ.get("WBSM_OPENSSH_MSI")
if SSH_INSTALLER:
    installer = Path(SSH_INSTALLER).resolve()
    expected = os.environ.get("WBSM_OPENSSH_SHA256", "").strip().lower()
    if installer.suffix.lower() != ".msi" or hashlib.sha256(installer.read_bytes()).hexdigest() != expected:
        raise ValueError("Set WBSM_OPENSSH_SHA256 to the official x64 MSI SHA-256.")
    DATAS.append((str(installer), "openssh-installer"))
ONEFILE = os.environ.get("WBSM_ONEFILE", "1") == "1"

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
    hiddenimports=["psutil", *LOCALE_MODULES.values()],
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
    a.binaries if ONEFILE else [],
    a.datas if ONEFILE else [],
    [],
    name="WindowsBatchScriptManager",
    exclude_binaries=not ONEFILE,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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

if not ONEFILE:
    coll = COLLECT(
        exe, a.binaries, a.datas,
        strip=False, upx=False, name="WindowsBatchScriptManager",
    )
