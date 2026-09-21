# -*- coding: utf-8 -*-
"""Thin launcher: run repo-root main.py (loads this folder's platform_impl)."""
import os
import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MAIN = _ROOT / "main.py"
if not _MAIN.is_file():
    print("未找到仓库根目录 main.py: %s" % _MAIN, file=sys.stderr)
    sys.exit(1)
root = str(_ROOT)
if root not in sys.path:
    sys.path.insert(0, root)
os.chdir(root)
sys.argv[0] = str(_MAIN)
runpy.run_path(str(_MAIN), run_name="__main__")
