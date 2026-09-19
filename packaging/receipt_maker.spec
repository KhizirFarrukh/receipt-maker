# -*- mode: python ; coding: utf-8 -*-

import os
import sys

from PyInstaller.utils.hooks import collect_all


playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")

# pyHanko (PAdES signing/verification) + its crypto stack pull in data files,
# native extensions and dynamically-referenced submodules that PyInstaller does
# not discover on its own. collect_all grabs each package wholesale.
signing_datas = []
signing_binaries = []
# settings_ui is imported lazily inside a menu handler and invoice_counter only
# through receipt_service, so name them explicitly rather than trusting the
# static analysis. A module missed here breaks only the packaged build, which is
# the hardest place to notice it.
signing_hiddenimports = [
    "receiptmaker.output.receipt_signing",
    "receiptmaker.ui.settings_ui",
    "receiptmaker.storage.invoice_counter",
    "receiptmaker.storage.product_catalogue",
    "receiptmaker.storage.receipt_history",
    # Imported inside functions rather than at module top level, which is
    # exactly the shape static analysis is least reliable about. Listing them
    # costs nothing and the alternative is a packaged build that starts fine and
    # dies when somebody opens the drafts list.
    "receiptmaker.core.money",
    "receiptmaker.pricing.line_units",
    "receiptmaker.pricing.installments",
    "receiptmaker.pricing.shipments",
    "receiptmaker.pricing.payment_methods",
    "receiptmaker.storage.csv_io",
    "receiptmaker.storage.drafts",
]
for _pkg in ("pyhanko", "pyhanko_certvalidator", "asn1crypto", "oscrypto",
             "cryptography", "certifi", "tzlocal", "uritools"):
    _d, _b, _h = collect_all(_pkg)
    signing_datas += _d
    signing_binaries += _b
    signing_hiddenimports += _h

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

# This spec lives in packaging/, so resolve everything against the project root
# rather than the current directory -- PyInstaller may be invoked from anywhere.
PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
TEMPLATES_DIR = os.path.join(PROJECT_ROOT, "Templates")

if not os.path.isdir(TEMPLATES_DIR):
    raise SystemExit(
        "Templates/ is missing. It holds the receipt layout the app renders from; "
        "restore it from the repository before building."
    )

datas = [
    # Read-only defaults. config.install_default_templates() copies these next to
    # the executable on first run and records their hashes, so the user gets an
    # editable set and a later upgrade can tell edited files from stale defaults.
    (TEMPLATES_DIR, "Templates"),
    (playwright_browser_dir, "ms-playwright"),
] + playwright_datas + signing_datas


a = Analysis(
    [os.path.join(PROJECT_ROOT, "main.py")],
    # main.py imports the `receiptmaker` package, which sits beside it rather
    # than beside this spec.
    pathex=[PROJECT_ROOT],
    binaries=playwright_binaries + signing_binaries,
    datas=datas,
    hiddenimports=playwright_hiddenimports + signing_hiddenimports,
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
