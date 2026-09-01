"""Headless receipt generation: numbering, filenames, PDF rendering, signing.

Extracted from ReceiptApp in Stage 1 so generation runs without tkinter -- the
GUI (main.py) collects data and calls generate() on a worker thread; cli.py can
call it headless.

Numbering lives in invoice_counter; this module only translates a receipt-type
label into a series. Note the split between get_next_invoice_number (a peek, for
displaying a suggestion) and reserve_invoice_number (which consumes) -- calling
the wrong one either burns numbers on every form refresh or lets two processes
issue the same one.
"""
import os
import re

import config
import invoice_counter
from config import (
    PDF_MARGIN_TOP,
    PDF_MARGIN_BOTTOM,
    PDF_MARGIN_LEFT,
    PDF_MARGIN_RIGHT,
    load_app_settings,
    load_filename_fields,
)
import product_catalogue
import receipt_history
import receipt_render
import receipt_signing

# Number of progress steps generate() reports (for a determinate progress bar).
GENERATION_STEPS = 4


# ------------------- signing glue -------------------
def resolve_app_path(path):
    """Resolve a config path against APP_DIR (leaves absolute paths untouched)."""
    from config import APP_DIR
    clean = str(path).strip()
    if not clean:
        return ""
    if os.path.isabs(clean):
        return clean
    return os.path.join(APP_DIR, clean)


def signing_key_paths():
    """Resolved (key_path, cert_path) from appsettings.json signing config."""
    signing = load_app_settings()["signing"]
    return (
        resolve_app_path(signing.get("private_key_path", "")),
        resolve_app_path(signing.get("certificate_path", "")),
    )


def sign_receipt_pdf(pdf_path):
    """Sign pdf_path in place using the configured key.

    Returns True when the file was signed, False when signing is disabled in
    appsettings.json. Raises RuntimeError (with a user-facing message) on any
    failure so the caller can treat the receipt as failed -- we must never leave
    behind an unsigned file that claims to be an authentic receipt.
    """
    signing = load_app_settings()["signing"]
    if not signing.get("enabled", True):
        return False

    key_path, cert_path = signing_key_paths()
    if not (key_path and os.path.isfile(key_path) and cert_path and os.path.isfile(cert_path)):
        raise RuntimeError(
            "Signing is enabled but the signing key/certificate was not found.\n"
            f"Expected:\n  {key_path or '(unset)'}\n  {cert_path or '(unset)'}\n\n"
            "Run 'python keygen.py' once to create them, or set signing.enabled to "
            "false in appsettings.json to generate unsigned receipts."
        )

    receipt_signing.sign_pdf(
        pdf_path, key_path, cert_path,
        passphrase=signing.get("key_passphrase", "") or None,
        reason=signing.get("reason", "") or None,
        location=signing.get("location", "") or None,
        name=signing.get("signer_name", "") or None,
        tsa_url=signing.get("tsa_url", "") or None,
    )
    return True


# ------------------- invoice numbering -------------------
def receipt_type_code(type_label):
    return config.receipt_type_by_label(type_label)["code"]


def get_invoice_prefix(type_label):
    return f"{invoice_counter.invoice_prefix()}{receipt_type_code(type_label)}"


def series_code(prefix):
    """The series letter inside a full prefix, e.g. 'INV-W' -> 'W'."""
    base = invoice_counter.invoice_prefix()
    return prefix[len(base):] if prefix.startswith(base) else prefix


def get_next_invoice_number(prefix):
    """The number this series would issue next, WITHOUT consuming it.

    Safe to call whenever the form refreshes -- opening the app or switching
    receipt type must not burn a number. Generation calls reserve_invoice_number
    instead.
    """
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    return invoice_counter.peek(series_code(prefix))


def reserve_invoice_number(prefix):
    """Atomically consume the next number for this series and return it."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    return invoice_counter.reserve(series_code(prefix))


# ------------------- output filenames -------------------
#: Names Windows reserves for devices. A file whose stem is one of these cannot
#: be created at all, whatever the extension -- "CON.pdf" fails. The invoice
#: number is user-editable and leads the filename, so this is reachable.
_WINDOWS_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{n}" for n in range(1, 10)]
    + [f"LPT{n}" for n in range(1, 10)]
)


def sanitize_filename_part(value):
    clean_value = str(value).strip()
    clean_value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", clean_value)
    clean_value = re.sub(r"\s+", " ", clean_value).strip(" .")
    return clean_value


def avoid_reserved_name(stem):
    """Nudge a filename stem off a Windows device name.

    Only the whole stem matters -- "INV-W1001-CON" is fine, bare "CON" is not.
    A trailing underscore keeps the name recognisable while making it writable.
    """
    return f"{stem}_" if stem.upper() in _WINDOWS_DEVICE_NAMES else stem


def filename_pattern():
    """The configured pattern, or one built from the legacy field list.

    Two mechanisms existed for naming a receipt and only one of them could be
    expressive, so the pattern is now the mechanism and the old
    `filename_config.json` field list is read as a way of *writing* one. That
    keeps every existing install producing byte-identical filenames while
    giving anyone who wants "{invoice_no}_{date}" a way to say so.
    """
    settings = load_app_settings()
    configured = str(settings.get("invoice", {}).get("filename_pattern", "") or "").strip()
    if configured:
        return configured
    parts = ["{invoice_no}"] + [f"{{{field}}}" for field in load_filename_fields()]
    return "-".join(parts)


def build_pdf_filename(inv_no, date_str, cust, email, phone, receipt_type=""):
    values = {
        "invoice_no": inv_no,
        "date": date_str,
        "name": cust,
        "email": email,
        "phone": phone,
        "receipt_type": receipt_type,
    }

    def replace(match):
        return sanitize_filename_part(values.get(match.group(1), ""))

    name = re.sub(r"\{([^{}]*)\}", replace, filename_pattern())
    # A blank value leaves a separator with nothing beside it: "INV-1--Ada" or
    # a trailing dash. Collapse them rather than printing the gap.
    name = re.sub(r"[-_ .]{2,}", lambda m: m.group(0)[0], name).strip("-_ .")
    return avoid_reserved_name(name or sanitize_filename_part(inv_no)) + ".pdf"


def next_available_pdf_path(base_filename):
    """Return a path for base_filename with the smallest free -N suffix
    (e.g. INV-W1001.pdf -> INV-W1001-1.pdf -> INV-W1001-2.pdf)."""
    stem, ext = os.path.splitext(base_filename)
    n = 1
    while True:
        candidate = os.path.join(config.OUTPUT_DIR, f"{stem}-{n}{ext}")
        if not os.path.exists(candidate):
            return candidate
        n += 1


# ------------------- PDF rendering -------------------
def render_pdf(body_html, pdf_path):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed.\n"
            "Run: python -m pip install -r requirements.txt\n"
            "Then run once: python -m playwright install chromium"
        ) from exc

    pdf_options = {
        "path": pdf_path,
        "format": "A4",
        "print_background": True,
        "display_header_footer": True,
        "header_template": receipt_render.build_page_header_template(),
        "footer_template": receipt_render.build_page_footer_template(),
        "margin": {
            "top": PDF_MARGIN_TOP,
            "bottom": PDF_MARGIN_BOTTOM,
            "left": PDF_MARGIN_LEFT,
            "right": PDF_MARGIN_RIGHT,
        },
    }

    render_cfg = load_app_settings().get("render", {})
    block_external = render_cfg.get("block_external_requests", True)
    timeout_ms = render_cfg.get("timeout_ms", 30000)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.set_default_timeout(timeout_ms)
                if block_external:
                    # Receipts must render offline and identically on every
                    # machine. Everything a receipt legitimately needs is already
                    # inlined (images as data: URIs, fonts as base64), so any
                    # remaining off-box request is either a template referencing
                    # a CDN -- which would make output depend on the network --
                    # or an exfiltration path. Abort them; local schemes stay.
                    page.route(
                        "**/*",
                        lambda route: route.abort()
                        if route.request.url.startswith(("http://", "https://", "ftp://"))
                        else route.continue_(),
                    )
                page.set_content(body_html, wait_until="load")
                page.pdf(**pdf_options)
            finally:
                browser.close()
    except Exception as exc:
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            raise RuntimeError(
                "Playwright's Chromium browser is not installed.\n"
                "Run once: python -m playwright install chromium"
            ) from exc
        raise


# ------------------- orchestration -------------------
def generate(data, out_path, progress_cb=None, warnings=None):
    """Build -> render -> sign a receipt at out_path. Returns True if signed.

    data keys: inv_no, date_str, cust, phone, email, items, receipt_type, shipping.
    progress_cb(step, label) is called with step in 1..GENERATION_STEPS for a
    progress UI. Raises (with a user-facing message) on any failure, and never
    leaves a half-written / unsigned file behind.
    """
    def report(step, label):
        if progress_cb:
            progress_cb(step, label)

    # Read history before anything is written. It answers two questions with
    # one lookup: has this number been issued before (so the PDF is a duplicate
    # and must say so), and what did it contain last time (so stock adjusts by
    # the difference rather than deducting the sale twice).
    previous = receipt_history.latest_for(data.get("inv_no", ""))

    report(1, "Building receipt...")
    html = receipt_render.build_html(
        data["inv_no"], data["date_str"], data["cust"], data["phone"],
        data["email"], data["items"], data.get("receipt_type", "Online"),
        data.get("shipping", 0.0),
        is_duplicate=previous is not None,
    )

    # Render and sign a temp file in the same directory, then move it into place
    # in one atomic step. A failed or interrupted run therefore never creates the
    # receipt at all, rather than creating one and deleting it afterwards -- and
    # nothing can observe a half-written or unsigned file under its final name.
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    tmp_path = f"{out_path}.{os.getpid()}.partial"
    try:
        report(2, "Rendering PDF...")
        render_pdf(html, tmp_path)
        report(3, "Signing...")
        signed = sign_receipt_pdf(tmp_path)
        os.replace(tmp_path, out_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise

    # Both of these happen after the file is safely in place, and neither is
    # allowed to fail the receipt: the signed PDF is the legal artifact.
    #
    # Stock first, and deliberately: it needs the *previous* version of this
    # receipt to work out what changed. `previous` was read at the top, before
    # anything was written and before the new record is appended.
    product_catalogue.record_sale(
        data.get("inv_no", ""), data.get("items"),
        previous.get("items") if previous else None,
        warnings=warnings)

    receipt_history.record(data, out_path, signed)

    report(4, "Saved")
    return signed
