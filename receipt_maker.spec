# -*- mode: python ; coding: utf-8 -*-

import os
import sys

from PyInstaller.utils.hooks import collect_all


playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")

if sys.platform == "win32":
    playwright_browser_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright")
elif sys.platform == "darwin":
    playwright_browser_dir = os.path.expanduser("~/Library/Caches/ms-playwright")
else:
    playwright_browser_dir = os.path.expanduser("~/.cache/ms-playwright")

if not os.path.isdir(playwright_browser_dir):
    raise SystemExit(
        "Playwright Chromium is not installed. Run: python -m playwright install chromium"
    )

datas = [
    ("header.html", "."),
    ("footer.html", "."),
    (playwright_browser_dir, "ms-playwright"),
] + playwright_datas


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=playwright_binaries,
    datas=datas,
    hiddenimports=playwright_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ReceiptGenerator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name="ReceiptGenerator",
)
