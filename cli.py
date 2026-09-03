#!/usr/bin/env python3
"""Headless entry point for the receipt maker.

Imports no tkinter: since Stage 1 the renderer lives in receipt_render and
headless generation in receipt_service, so this module -- and the golden gate
built on it -- runs anywhere Python runs. Stage1Layering asserts that.

--render-html is the golden-diff target: it renders the receipt body exactly as
the GUI's generation path does (both call receipt_render.build_html with the
same data dict), which is what Stage0Fidelity proves. Still to come: --render
(full pipeline), --preview, --check and --doctor in Stage 2.
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
#: --doctor found something that will stop a receipt being issued. Warnings do
#: not reach here: a receipt without a logo is still a valid receipt, and
#: exiting non-zero over one would make the check useless in a build script.
EXIT_ENVIRONMENT = 6


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


def run_check():
    """Validate config and lint every template. Returns a process exit code.

    Deliberately reports config and template problems separately: they have
    different exit codes and different fixes, and a template lint needs the
    config loaded first (the allowed-placeholder set depends on it).
    """
    import config

    try:
        settings = config.load_app_settings()
    except config.ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    print(f"config   OK  {config.APP_SETTINGS_FILE} "
          f"(schema {settings.get(config.SCHEMA_VERSION_KEY)})")

    import receipt_render as _render
    logo_problem = _render.asset_problem(settings["company"].get("logo_path", ""))
    if logo_problem:
        # A warning, not a failure: a receipt without its logo is still a valid
        # receipt. But it must be said out loud -- that is the whole point.
        print("logo     WARN " + logo_problem.replace("\n", "\n              "))
    else:
        print("logo     OK  "
              + (settings["company"].get("logo_path") or "(none configured)"))

    import receipt_render
    from template_engine import TemplateError
    try:
        receipt_render.clear_template_cache()
        templates = receipt_render.load_templates(force=True)
    except TemplateError as exc:
        print(f"TEMPLATE ERROR: {exc}", file=sys.stderr)
        return EXIT_TEMPLATE
    for name in sorted(templates):
        print(f"template OK  {name}")

    # A template that compiles can still fail on real data (a |raw hole, a
    # miscounted column), so finish by actually rendering something.
    try:
        receipt_render.build_html(
            "INV-CHECK", "1 Jan 2026", "Check", "", "",
            [{"sku": "", "desc": "check", "serial": "", "qty": 1,
              "price": 1, "discount": 0, "tax": 0, "warranty": ""}],
            "Online", 0,
        )
    except Exception as exc:
        print(f"RENDER ERROR: {exc}", file=sys.stderr)
        return EXIT_RENDER
    print("render   OK  sample receipt rendered")
    return EXIT_OK


class _Report:
    """Collects check results so one run reports everything, not just the first.

    A doctor that stops at the first problem makes you run it four times to
    find four things. Every check runs; the exit code reflects the worst.
    """

    def __init__(self):
        self.failed = False

    def ok(self, area, message):
        print(f"{area:<10}OK  {message}")

    def warn(self, area, message):
        print(f"{area:<10}WARN {message}".replace("\n", "\n              "))

    def fail(self, area, message):
        self.failed = True
        print(f"{area:<10}FAIL {message}".replace("\n", "\n              "),
              file=sys.stderr)


def _check_browser(report):
    """Is there a Chromium for Playwright to drive?

    The commonest way this app fails on a fresh machine: pip install works,
    `playwright install chromium` is forgotten, and the first receipt dies at
    render time with a stack trace nobody can read.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report.fail("browser", "playwright is not installed. Run:\n"
                               "pip install playwright && playwright install chromium")
        return
    # Launch and close it properly rather than only reading the path. That is
    # the question that actually matters -- "can a receipt be rendered?" -- and
    # tearing the driver down cleanly also avoids the cancellation traceback
    # Playwright prints when the context is left the moment it is entered.
    try:
        with sync_playwright() as playwright:
            path = playwright.chromium.executable_path
            browser = playwright.chromium.launch()
            browser.close()
    except Exception as exc:                     # noqa: BLE001 - reported
        text = str(exc)
        if "install" in text.lower() or "executable doesn" in text.lower():
            report.fail("browser",
                        "Chromium is not installed for Playwright. Run:\n"
                        "playwright install chromium")
        else:
            first_line = text.strip().splitlines()[0] if text.strip() else text
            report.fail("browser", f"could not start Chromium: {first_line}")
        return
    report.ok("browser", path or "(installed)")


def _check_output_folder(report):
    """Can receipts actually be written?"""
    import config

    folder = config.OUTPUT_DIR
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".doctor-write-test")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
    except OSError as exc:
        report.fail("output", f"cannot write to {folder}:\n{exc}")
        return
    report.ok("output", folder)


def _check_counter(report):
    """The invoice counter has to be readable and lockable before a sale."""
    import config
    import invoice_counter

    try:
        types = config.receipt_types()
        series = str((types[0] if types else {}).get("code", "") or "W")
        number = invoice_counter.peek(series)
    except Exception as exc:                     # noqa: BLE001 - reported
        report.fail("counter", f"could not read the invoice counter: {exc}")
        return
    report.ok("counter", f"next {series} number is {number}")


def _check_signing(report):
    """A key that is missing, unreadable, or expiring is worth knowing early.

    Signing being *off* is fine and reported as such. Signing being on with a
    broken key is the case that matters: every receipt issued from now on goes
    out unsigned, and nothing else says so.
    """
    import config
    import receipt_service

    settings = config.load_app_settings()
    if not settings.get("signing", {}).get("enabled", False):
        report.ok("signing", "off (receipts will not be signed)")
        return

    key_path, cert_path = receipt_service.signing_key_paths()
    if not os.path.isfile(key_path):
        report.warn("signing",
                    f"signing is on but there is no key at\n{key_path}\n"
                    f"Receipts will be issued unsigned. Create one under "
                    f"Tools -> Signing Keys.")
        return

    # Whether the key can actually be *read*, not merely whether it exists.
    # This check was missing, so --doctor reported "Ready to issue receipts"
    # while every receipt failed at the signing step -- the exact situation it
    # exists to catch.
    import receipt_signing
    problem = receipt_signing.key_problem(
        key_path, cert_path,
        settings.get("signing", {}).get("key_passphrase", ""))
    if problem:
        report.fail("signing", problem)
        return

    try:
        import receipt_signing
        info = receipt_signing.certificate_info(cert_path)
    except Exception as exc:                     # noqa: BLE001 - reported
        report.fail("signing", f"the certificate could not be read: {exc}")
        return
    if info is None:
        report.fail("signing", f"the certificate at {cert_path} could not be read.")
        return

    expiry = info["not_after"].strftime("%d %b %Y")
    if info["expired"]:
        report.fail("signing", f"the certificate EXPIRED on {expiry}.")
    elif info["days_left"] < 60:
        report.warn("signing",
                    f"the certificate expires {expiry} "
                    f"({info['days_left']} days left).")
    else:
        report.ok("signing", f"{info['subject']}, valid until {expiry}")


def _check_data_files(report):
    """The optional data files: present and readable, or absent and fine."""
    import config

    for label, loader in (("catalogue", _load_catalogue),
                          ("history", _load_history),
                          ("drafts", _load_drafts)):
        try:
            report.ok(label, loader())
        except Exception as exc:                 # noqa: BLE001 - reported
            report.fail(label, f"{exc}")


def _load_catalogue():
    import product_catalogue
    products = product_catalogue.load().get("products", [])
    return f"{len(products)} product(s)"


def _load_history():
    import receipt_history
    return f"{len(receipt_history.entries())} receipt(s) recorded"


def _load_drafts():
    import drafts
    return f"{len(drafts.load().get('drafts', []))} draft(s)"


def run_doctor():
    """Check the environment a receipt needs. Returns a process exit code.

    Complements --check, which validates the *configuration*. This checks the
    things around it: a browser to render with, a folder to write into, a key
    to sign with, a counter to number from. Those fail on a new machine or a
    changed one, and they fail at the worst moment -- halfway through issuing a
    receipt to somebody standing at the counter.
    """
    import config

    report = _Report()
    report.ok("app", f"{config.APP_DIR}")

    try:
        config.load_app_settings()
    except config.ConfigError as exc:
        # Without config nothing below can run, so this one does stop early.
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    _check_browser(report)
    _check_output_folder(report)
    _check_counter(report)
    _check_signing(report)
    _check_data_files(report)

    if report.failed:
        print("\nSomething above will stop a receipt being issued.",
              file=sys.stderr)
        return EXIT_ENVIRONMENT
    print("\nReady to issue receipts.")
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="cli", description="Receipt maker headless CLI."
    )
    parser.add_argument("--render-html", metavar="DATA_JSON",
                        help="Render receipt HTML from a data JSON file (no Playwright).")
    parser.add_argument("--out", metavar="FILE", help="Write output here instead of stdout.")
    parser.add_argument("--freeze-date", metavar="STR",
                        help="Override the receipt date string (determinism).")
    parser.add_argument("--invoice-number", metavar="STR",
                        help="Override the invoice number (determinism).")
    parser.add_argument("--doctor", action="store_true",
                        help="Check the environment: browser, output folder, "
                             "counter, signing key, data files.")
    parser.add_argument("--check", action="store_true",
                        help="Load and validate the config and lint every template. "
                             "Non-zero exit on any problem.")
    parser.add_argument("--config-dir", metavar="DIR",
                        help="Config/output root to use instead of the directory beside the "
                             "app. Required for a hermetic gate -- otherwise a check runs "
                             "against whatever is in the developer's own APP_DIR.")
    parser.add_argument("--raw", action="store_true",
                        help="Do not normalize the machine-specific <base href>.")
    args = parser.parse_args(argv)

    if args.config_dir:
        import config
        if not os.path.isdir(args.config_dir):
            print(f"ERROR: --config-dir is not a directory: {args.config_dir}", file=sys.stderr)
            return EXIT_CONFIG
        config.set_app_dir(args.config_dir)

    if args.doctor:
        return run_doctor()

    if args.check:
        return run_check()

    if not args.render_html:
        parser.error(
            "nothing to do: pass --render-html DATA_JSON, --check or --doctor")

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
