# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


# 构建只使用当前 Python 与 Windows 的 DLL 搜索路径。开发工具可能把
# Poppler/Git 等目录加入 PATH，其中同名 ICU/OpenSSL DLL 与 Qt/Python 不兼容。
windows_dir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
build_search_paths = [
    Path(sys.executable).parent,
    Path(sys.base_prefix),
    Path(sys.base_prefix) / "DLLs",
    windows_dir / "System32",
    windows_dir,
]
os.environ["PATH"] = os.pathsep.join(
    dict.fromkeys(str(path) for path in build_search_paths if path.is_dir())
)


datas = [("README.md", "."), ("docs", "docs")]
binaries = []
hiddenimports = []

playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
datas += playwright_datas
binaries += playwright_binaries
hiddenimports += playwright_hiddenimports

# openai 在答题时才动态导入，必须显式收集，否则免安装包会缺少 AI 依赖。
openai_datas, openai_binaries, openai_hiddenimports = collect_all("openai")
datas += openai_datas
binaries += openai_binaries
hiddenimports += openai_hiddenimports


a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="自动搜题",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="自动搜题",
)

# 同时在软件根目录放一份说明，方便接收者直接查看。
import shutil

release_dir = Path(DISTPATH) / "自动搜题"
shutil.copy2("README.md", release_dir / "README.md")
shutil.copytree("docs", release_dir / "docs", dirs_exist_ok=True)

# 避免从 Git/MSYS 的 PATH 误收同名 DLL；Python 的 _ssl 必须搭配自身 OpenSSL。
python_dlls = Path(sys.base_prefix) / "DLLs"
for dll_name in ("libssl-3-x64.dll", "libcrypto-3-x64.dll"):
    shutil.copy2(python_dlls / dll_name, release_dir / "_internal" / dll_name)
