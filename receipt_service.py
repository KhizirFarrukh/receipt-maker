"""Headless receipt generation: numbering, filenames, PDF rendering, signing.

Extracted from ReceiptApp in Stage 1 so generation runs without tkinter -- the
GUI (main.py) collects data and calls generate() on a worker thread; cli.py can
call it headless. Behaviour matches the pre-refactor generate_pdf path.
"""
import os
import re

import config
from config import (
    RECEIPT_TYPES,
    INVOICE_PREFIX_BASE,
    INVOICE_START_NUMBER,
    PDF_MARGIN_TOP,
    PDF_MARGIN_BOTTOM,
    PDF_MARGIN_LEFT,
    PDF_MARGIN_RIGHT,
    load_app_settings,
    load_filename_fields,
)
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
def get_invoice_prefix(type_label):
    return f"{INVOICE_PREFIX_BASE}{config.receipt_type_by_label(type_label)['code']}"


def _claims_legacy_unlettered(letter):
    """Whether this series also counts the old unlettered INV-#### files.

    Those predate receipt types, so exactly one series inherits them; validate()
    enforces that only one type may claim them.
    """
    for entry in config.receipt_types():
        if str(entry.get("code", "")).upper() == letter.upper():
            return bool(entry.get("legacy_unlettered"))
    return False


def get_next_invoice_number(prefix):
    if not os.path.exists(config.OUTPUT_DIR):
        os.makedirs(config.OUTPUT_DIR)
    max_num = INVOICE_START_NUMBER - 1
    letter = prefix[len(INVOICE_PREFIX_BASE):]
    if _claims_legacy_unlettered(letter):
        letter_pattern = f"{re.escape(letter)}?"
    else:
        letter_pattern = re.escape(letter)
    pattern = re.compile(
        rf"^{re.escape(INVOICE_PREFIX_BASE)}{letter_pattern}(\d+)(?:-.*)?\.pdf$",
        re.IGNORECASE,
    )
    for fname in os.listdir(config.OUTPUT_DIR):
        match = pattern.match(fname)
        if match:
            num = int(match.group(1))
            if num > max_num:
                max_num = num
    return max_num + 1


# ------------------- output filenames -------------------
def sanitize_filename_part(value):
    clean_value = str(value).strip()
    clean_value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", clean_value)
    clean_value = re.sub(r"\s+", " ", clean_value).strip(" .")
    return clean_value


def build_pdf_filename(inv_no, date_str, cust, email, phone):
    filename_values = {
        "date": date_str,
        "name": cust,
        "email": email,
        "phone": phone,
    }
    filename_parts = [sanitize_filename_part(inv_no)]

    for field in load_filename_fields():
        value = filename_values.get(field, "")
        clean_value = sanitize_filename_part(value)
        if clean_value:
            filename_parts.append(clean_value)

    return "-".join(filename_parts) + ".pdf"


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
def generate(data, out_path, progress_cb=None):
    """Build -> render -> sign a receipt at out_path. Returns True if signed.

    data keys: inv_no, date_str, cust, phone, email, items, receipt_type, shipping.
    progress_cb(step, label) is called with step in 1..GENERATION_STEPS for a
    progress UI. Raises (with a user-facing message) on any failure, and never
    leaves a half-written / unsigned file behind.
    """
    def report(step, label):
        if progress_cb:
            progress_cb(step, label)

    report(1, "Building receipt...")
    html = receipt_render.build_html(
        data["inv_no"], data["date_str"], data["cust"], data["phone"],
        data["email"], data["items"], data.get("receipt_type", "Online"),
        data.get("shipping", 0.0),
    )

    try:
        report(2, "Rendering PDF...")
        render_pdf(html, out_path)
        report(3, "Signing...")
        signed = sign_receipt_pdf(out_path)
    except Exception:
        # If rendering or signing failed, remove the file so a non-authentic
        # receipt is never left behind.
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        raise

    report(4, "Saved")
    return signed
