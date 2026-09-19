#!/usr/bin/env python3
"""Launcher for the headless command line: see receiptmaker/tools/cli.py.

Kept at the root so `python cli.py --doctor`, `--check` and `--render-html`
keep working, including the golden gate that runs through them.
"""
from receiptmaker.tools import cli

if __name__ == "__main__":
    raise SystemExit(cli.main())
