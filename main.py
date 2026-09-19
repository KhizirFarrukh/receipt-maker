#!/usr/bin/env python3
"""Launcher for the Receipt Generator GUI.

The window itself lives in receiptmaker/ui/main_window.py. This file stays at
the project root because it is what the README tells people to run, what the
PyInstaller spec builds from, and what `--smoke-test` invokes in the packaged
build -- all three would break if the entry point moved into the package.
"""
import sys

from receiptmaker.ui import main_window

if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        raise SystemExit(main_window.run_smoke_test())
    raise SystemExit(main_window.launch())
