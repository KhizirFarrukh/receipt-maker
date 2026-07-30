#!/usr/bin/env python3
"""Headless entry point for the receipt maker.

STAGE 0 NOTE (temporary baseline harness). Because build_html still lives in
main.py, importing this module pulls in tkinter, so the gate is not yet
CI-hermetic. Stage 1 extracts receipt_service.py and drops the tkinter import;
Stage 2 adds --check / --render / --preview / --doctor. For now only
--render-html is implemented (pure HTML, no Playwright).

The renderer is invoked exactly as the GUI invokes it: build_html only uses
@staticmethods, so we can bypass ReceiptApp.__init__ (no widgets, no mainloop)
and call the real, unmodified method. That keeps this harness faithful to the
GUI's own output -- proven by the Stage 0 fidelity test.
"""
import argparse
import json
import os
import re
import sys

# The only machine-dependent element in Stage 0 output is build_html's
# `<base href="file:///<abs path to RESOURCE_DIR>/">`. Normalize it to a stable
# placeholder so the committed golden HTML is deterministic across machines.
RESOURCE_BASE_PLACEHOLDER = "{{RESOURCE_BASE}}"
_BASE_HREF_RE = re.compile(r'(<base href=")file:///[^"]*(">)')

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_TEMPLATE = 3
EXIT_RENDER = 4
EXIT_SIGNING = 5


def load_data(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _to_build_html_args(data, *, freeze_date=None, invoice_number=None):
    """Adapt the forward-compatible data.json shape to build_html's arguments.

    Amounts are stored as strings (principle 7); build_html today consumes floats,
    so this adapter is the temporary bridge. When the pure Decimal renderer lands
    in Stage 2, only this adapter changes -- the fixture does not.
    """
    customer = data.get("customer", {}) or {}
    items = []
    for it in data.get("items", []):
        items.append({
            "sku": it.get("sku", ""),
            "desc": it.get("desc", ""),
            "serial": it.get("serial", ""),
            "qty": int(it.get("qty", 1)),
            "price": float(it.get("unit_price", "0")),
            "discount": float(it.get("discount", "0")),
            "tax": float(it.get("tax", "0")),
            "warranty": it.get("warranty", ""),
        })
    return {
        "inv_no": invoice_number or data.get("invoice_no", ""),
        "date_str": freeze_date or data.get("date", ""),
        "cust": customer.get("name", ""),
        "phone": customer.get("phone", ""),
        "email": customer.get("email", ""),
        "items": items,
        "receipt_type": data.get("receipt_type", "Online"),
        "shipping": float(data.get("shipping", "0")),
    }


def normalize_html(html):
    """Neutralize the machine-dependent file:/// <base href> for stable goldens."""
    return _BASE_HREF_RE.sub(r"\1" + RESOURCE_BASE_PLACEHOLDER + r"\2", html)


def render_html_from_data(data, *, freeze_date=None, invoice_number=None, normalize=True):
    """Render receipt HTML from data, without tkinter or Playwright.

    Uses the tk-free receipt_render.build_html (extracted from ReceiptApp in
    Stage 1). This is the golden-diff target.
    """
    import receipt_render
    a = _to_build_html_args(data, freeze_date=freeze_date, invoice_number=invoice_number)
    html = receipt_render.build_html(
        a["inv_no"], a["date_str"], a["cust"], a["phone"], a["email"],
        a["items"], a["receipt_type"], a["shipping"],
    )
    return normalize_html(html) if normalize else html


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="cli", description="Receipt maker headless CLI (Stage 0 baseline harness)."
    )
    parser.add_argument("--render-html", metavar="DATA_JSON",
                        help="Render receipt HTML from a data JSON file (no Playwright).")
    parser.add_argument("--out", metavar="FILE", help="Write output here instead of stdout.")
    parser.add_argument("--freeze-date", metavar="STR",
                        help="Override the receipt date string (determinism).")
    parser.add_argument("--invoice-number", metavar="STR",
                        help="Override the invoice number (determinism).")
    parser.add_argument("--config-dir", metavar="DIR",
                        help="Config/output root (reserved; takes effect once config.py lands in Stage 2).")
    parser.add_argument("--raw", action="store_true",
                        help="Do not normalize the machine-specific <base href>.")
    args = parser.parse_args(argv)

    if not args.render_html:
        parser.error("Stage 0 supports only --render-html; more commands arrive with Stage 1/2.")

    try:
        data = load_data(args.render_html)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: could not read data file: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        html = render_html_from_data(
            data, freeze_date=args.freeze_date,
            invoice_number=args.invoice_number, normalize=not args.raw,
        )
    except Exception as exc:  # pragma: no cover - surfaced to the user
        print(f"ERROR: render failed: {exc}", file=sys.stderr)
        return EXIT_RENDER

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
    else:
        # Write UTF-8 bytes directly: the receipt contains non-Latin-1 characters
        # (e.g. emoji), which crash a cp1252 Windows console via sys.stdout.write.
        sys.stdout.buffer.write(html.encode("utf-8"))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
