#!/usr/bin/env python3
"""Launcher for signing-key creation: see receiptmaker/tools/keygen.py."""
from receiptmaker.tools import keygen

if __name__ == "__main__":
    raise SystemExit(keygen.main())
