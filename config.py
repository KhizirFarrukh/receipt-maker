"""Paths, config constants, and config-file loading (tkinter-free).

Stage 2 turns this from a pair of loaders into the one place that owns config:
a declared ``schema_version``, a ``migrate()`` that restructures older files,
a ``validate()`` that rejects nonsense in plain language before a render can
fail halfway, and atomic conflict-aware writes that always leave a ``.bak``.

Governing rules this file implements (PLAN-generalization.md):
  * config is validated, not trusted -- ``validate()`` names the file and key;
  * do no harm -- every rewrite keeps a timestamped ``.bak``, and a config
    written by a newer build is refused rather than silently downgraded;
  * deep-merge only *fills* missing keys, so a user's edits are never replaced
    by defaults; only ``migrate()`` may restructure.
"""
import datetime
import json
import os
import re
import sys

# ------------------- file paths -------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else BASE_DIR
RESOURCE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)

# Templates live beside the executable so they can be edited; the bundled copy
# in RESOURCE_DIR is the read-only source they are seeded from on first run.
TEMPLATES_DIRNAME = "Templates"
TEMPLATES_DIR = os.path.join(APP_DIR, TEMPLATES_DIRNAME)
BUNDLED_TEMPLATES_DIR = os.path.join(RESOURCE_DIR, TEMPLATES_DIRNAME)
INSTALLED_MANIFEST = ".installed.json"


def branding_template_path(filename):
    """Path to an editable template, preferring the user's own copy.

    Search order, first hit wins:
      1. APP_DIR/Templates/     -- where Stage 2 puts editable templates
      2. APP_DIR/               -- pre-Stage-2 layout, still honoured so an
                                   existing install's edits keep working
      3. RESOURCE_DIR/Templates/ and RESOURCE_DIR/ -- the read-only bundled copies

    A copy beside the executable beating the bundled one is what makes the
    README's "edit header.html / footer.html" instructions true in a packaged
    install; previously only the read-only RESOURCE_DIR copy was ever consulted.
    Unfrozen, APP_DIR and RESOURCE_DIR are both the repo root.
    """
    for directory in (TEMPLATES_DIR, APP_DIR, BUNDLED_TEMPLATES_DIR, RESOURCE_DIR):
        candidate = os.path.join(directory, filename)
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(TEMPLATES_DIR, filename)


HEADER_FILE = branding_template_path("header.html")
FOOTER_FILE = branding_template_path("footer.html")
APP_SETTINGS_FILE = os.path.join(APP_DIR, "appsettings.json")
FILENAME_CONFIG_FILE = os.path.join(APP_DIR, "filename_config.json")
OUTPUT_DIR = os.path.join(APP_DIR, "invoices")

PDF_MARGIN_TOP = "150px"
PDF_MARGIN_BOTTOM = "100px"
PDF_MARGIN_LEFT = "24px"
PDF_MARGIN_RIGHT = "24px"


def set_app_dir(path):
    """Re-root every APP_DIR-derived path at ``path``.

    This is what makes ``cli.py --config-dir`` real, and with it a hermetic
    gate: without it, a check or a render validates whatever happens to sit in
    the developer's own APP_DIR, so the result depends on the machine.

    Other modules must read these through the ``config`` module rather than
    importing the names, or they keep a stale copy from import time.
    """
    global APP_DIR, TEMPLATES_DIR, APP_SETTINGS_FILE, FILENAME_CONFIG_FILE
    global OUTPUT_DIR, HEADER_FILE, FOOTER_FILE

    APP_DIR = os.path.abspath(path)
    TEMPLATES_DIR = os.path.join(APP_DIR, TEMPLATES_DIRNAME)
    APP_SETTINGS_FILE = os.path.join(APP_DIR, "appsettings.json")
    FILENAME_CONFIG_FILE = os.path.join(APP_DIR, "filename_config.json")
    OUTPUT_DIR = os.path.join(APP_DIR, "invoices")
    HEADER_FILE = branding_template_path("header.html")
    FOOTER_FILE = branding_template_path("footer.html")
    return APP_DIR

if getattr(sys, "frozen", False):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(RESOURCE_DIR, "ms-playwright"))

# ------------------- receipt type / invoice numbering config -------------------
# Each receipt type keeps its own invoice series via a single-letter prefix.
RECEIPT_TYPES = {
    "Online":   "W",   # web purchase
    "In Store": "S",   # in-store purchase
}
INVOICE_PREFIX_BASE = "INV-"
INVOICE_START_NUMBER = 1001  # first number for a fresh series, e.g. INV-W1001 / INV-S1001
DATE_DISPLAY_FORMAT = "%d %b %Y"
DATE_PARSE_FORMATS = (
    DATE_DISPLAY_FORMAT,
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
)
FILENAME_FIELD_OPTIONS = ("date", "name", "email", "phone")
DEFAULT_FILENAME_FIELDS = ["date", "name"]

# ------------------- schema -------------------
#: Bumped whenever appsettings.json is *restructured*. Adding a key with a
#: default does not need a bump -- deep-merge already fills it in.
SCHEMA_VERSION = 4
SCHEMA_VERSION_KEY = "schema_version"

TAX_MODES = ("exclusive", "inclusive")
TAX_ROW_TYPES = ("percent", "fixed")
TAX_BASES = ("subtotal_after_discount", "subtotal")
GROUP_STYLES = ("thousand", "indian", "none")
SYMBOL_POSITIONS = ("prefix", "suffix")
NEGATIVE_STYLES = ("minus", "parentheses")

#: How amounts rendered before currency became configurable: the symbol "Rs. ",
#: two decimals, grouped in the totals but NOT inside the item table. An existing
#: install migrating to schema 3 is seeded with exactly this, so nobody's
#: receipts change currency or spacing behind their back. Fresh installs get the
#: neutral defaults in DEFAULT_APP_SETTINGS instead.
LEGACY_CURRENCY = {
    "symbol": "Rs.",
    "symbol_space": True,
    "code": "",
    "decimals": 2,
    "position": "prefix",
    "group_style": "thousand",
    "negative_style": "minus",
    "group_line_amounts": False,
}

DEFAULT_APP_SETTINGS = {
    SCHEMA_VERSION_KEY: SCHEMA_VERSION,
    "company": {
        "name": "Your Company",
        "address": "Your business address",
        "phone": "000-000-0000",
        "email": "hello@example.com",
        "logo_path": "logo.png",
    },
    # Digital-signature settings. When enabled, every generated receipt is signed
    # with a PAdES signature using the private key created by keygen.py, so a
    # forged or edited receipt fails verification against the public certificate.
    "signing": {
        "enabled": True,
        "private_key_path": "signing/private_key.pem",
        "certificate_path": "signing/certificate.pem",
        "key_passphrase": "",
        # Neutral defaults -- the store's identity belongs in the user's own
        # appsettings.json, not in the shipped defaults.
        "signer_name": "Your Company",
        "reason": "Receipt authenticity",
        "location": "",
        "tsa_url": "",
    },
    # How every amount on a receipt is rendered.
    #   symbol/symbol_space/position -- "$12.00", "12.00 kr", "Rs. 12.00"
    #   group_style   -- thousand: 1,234,567   indian: 12,34,567   none: 1234567
    #   negative_style -- minus: -$5.00        parentheses: ($5.00)
    #   group_line_amounts -- apply grouping inside the item table too. True here
    #     because a receipt that groups its totals but not its line amounts looks
    #     inconsistent; existing installs migrate to False to keep today's output.
    "currency": {
        "symbol": "$",
        "symbol_space": False,
        "code": "USD",
        "decimals": 2,
        "position": "prefix",
        "group_style": "thousand",
        "negative_style": "minus",
        "group_line_amounts": True,
    },
    # PDF page geometry. The top/bottom margins must reserve room for the page
    # header and footer templates, or Chromium clips them -- see Templates/header.html.
    "document": {
        "margin_top": PDF_MARGIN_TOP,
        "margin_bottom": PDF_MARGIN_BOTTOM,
        "margin_left": PDF_MARGIN_LEFT,
        "margin_right": PDF_MARGIN_RIGHT,
    },
    # Invoice numbering. `counter_file` is the source of truth: it is seeded from
    # the highest number already present in invoices/ and then owns the sequence,
    # because deriving numbers by scanning filenames breaks the moment filenames
    # become configurable -- an unparseable pattern would silently restart at
    # `start` and reissue numbers that are already on customers' receipts.
    # `reconcile_with_filenames` keeps the old scan as a cross-check that warns
    # on disagreement instead of driving the sequence.
    # Changing `prefix` starts a new series; existing files stop being counted.
    "invoice": {
        "prefix": INVOICE_PREFIX_BASE,
        "start": INVOICE_START_NUMBER,
        "counter_file": "invoices/.counters.json",
        "reconcile_with_filenames": True,
    },
    # Document-level tax, applied on top of (or backed out of) the line totals.
    #   mode "exclusive" -- tax is added to the subtotal (US/UK-style quoting)
    #   mode "inclusive" -- shown prices already contain the tax, so it is backed
    #                       out and reported, not added. A market that quotes
    #                       inclusive prices cannot be represented by "add on
    #                       top" alone, which is why this is a mode and not a flag.
    # Each row: {label, type: percent|fixed, value, applies_to}. `applies_to` is
    # document-level in v1: "subtotal" or "subtotal_after_discount". Per-line tax
    # RATES are out of scope; the per-line `tax` column remains an amount.
    # Ordering is fixed and deliberate: discount first, then tax on the result.
    "tax": {
        "mode": "exclusive",
        "rows": [],
    },
    # strftime pattern for the date shown on the receipt and used in filenames.
    "date_format": DATE_DISPLAY_FORMAT,
    # Each receipt type keeps its own invoice series, identified by `code`.
    # Changing a code starts a new series -- existing INV-W#### files stop
    # matching -- so migration preserves W and S verbatim.
    # `legacy_unlettered` marks the series that also counts pre-Stage-0
    # unlettered INV-#### files; exactly one type may claim them.
    "receipt_types": [
        {"label": "Online", "code": "W", "badge_text": "ONLINE ORDER",
         "legacy_unlettered": True},
        {"label": "In Store", "code": "S", "badge_text": "IN-STORE SALE"},
    ],
    # The Warranty & Returns page appended after the receipt. Edit its wording in
    # Templates/terms.html; set enabled to false to drop the page entirely.
    "terms_page": {
        "enabled": True,
    },
    "render": {
        # Receipts must generate offline and identically on every machine, and a
        # template-referenced CDN must not be able to phone out.
        "block_external_requests": True,
        "timeout_ms": 30000,
        "fail_on_missing_image": False,
    },
    # Embedding a font makes a receipt render identically everywhere; with no
    # family set, Chromium substitutes a system font and the same receipt can
    # differ across machines. Empty by default: no font is bundled yet, so the
    # user supplies an OFL-licensed file under Templates/fonts/ and names it
    # here. Files are inlined as base64 @font-face, so rendering stays offline.
    "fonts": {
        "family": "",
        "files": [],
        "fallback": "Helvetica, Arial, sans-serif",
    },
}

STRINGS_FILE_NAME = "strings.json"

#: Words the *renderer* produces, as opposed to words a template author types
#: directly into an .html file. Principle 3 says no user-visible string lives in
#: Python; anything the renderer composes therefore lives here.
#:
#: This is a separate file from appsettings.json on purpose: wording edits then
#: stay clear of config migrations and their .bak churn, and a translation is a
#: drop-in file rather than a merge into someone's settings.
DEFAULT_STRINGS = {
    SCHEMA_VERSION_KEY: 1,
    "columns": {
        "sku": "SKU",
        "desc": "Item Description",
        "serial": "Serial Number",
        "qty": "Qty",
        "price": "Unit Price",
        "discount": "Discount",
        "tax": "Tax",
        "amount": "Amount",
    },
    "totals": {
        "subtotal": "Subtotal",
        "taxes": "Taxes",
        "discounts": "Discounts",
        "shipping": "Shipping Fees",
        # Appended to a tax row when tax.mode is "inclusive", so a reader can
        # see why the tax figure is not added into the total.
        "included_suffix": " (included)",
    },
    # Printed in a cell that has no value (an item with no SKU or serial).
    "empty_cell": "-",
}


class ConfigError(Exception):
    """A config file is unusable. Carries the file and key so the UI can say where."""

    def __init__(self, message, filename=None, key=None):
        self.message = message
        self.filename = filename
        self.key = key
        where = " -> ".join(p for p in (os.path.basename(filename) if filename else None, key) if p)
        super().__init__(f"{where}: {message}" if where else message)


class ConfigConflict(Exception):
    """The file changed on disk since it was read; writing would clobber an edit."""


# ------------------- read HTML snippets -------------------
def read_html_file(path):
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ------------------- merge / migrate / validate -------------------
def deep_merge(defaults, override):
    """Return defaults with override's values laid on top. Fills gaps only.

    A key present in override always wins; a key missing from override takes the
    default. Nested dicts merge recursively, so adding a new default key in a
    future build reaches existing configs without touching the user's edits.
    """
    merged = {}
    for key, default_value in defaults.items():
        if isinstance(default_value, dict):
            supplied = override.get(key)
            merged[key] = deep_merge(default_value, supplied if isinstance(supplied, dict) else {})
        elif key in override:
            merged[key] = override[key]
        else:
            merged[key] = default_value
    # Preserve keys the user added that defaults do not know about, so a
    # hand-written extra never silently disappears on the next rewrite.
    for key, value in override.items():
        if key not in merged:
            merged[key] = value
    return merged


def migrate(raw, filename=None):
    """Bring a loaded appsettings dict up to SCHEMA_VERSION.

    Returns ``(settings, changed)``; ``changed`` is True when the file on disk
    should be rewritten. Raises ConfigError for a config written by a newer
    build -- silently downgrading it would drop keys the user relies on.
    """
    if not isinstance(raw, dict):
        raise ConfigError("expected a JSON object at the top level", filename)

    version = raw.get(SCHEMA_VERSION_KEY, 1)
    if not isinstance(version, int) or isinstance(version, bool):
        raise ConfigError(
            f"must be a whole number, got {version!r}", filename, SCHEMA_VERSION_KEY)
    if version > SCHEMA_VERSION:
        raise ConfigError(
            f"this file was written by a newer version of the app "
            f"(schema {version}; this build understands {SCHEMA_VERSION}). "
            f"Update the app, or restore an older copy of the file.",
            filename, SCHEMA_VERSION_KEY)

    migrated = dict(raw)
    changed = False

    if version < 2:
        # v1 -> v2: v1 was a flat file with only `company` and `signing`, and no
        # declared version. Page geometry and render policy were Python
        # constants; they become config so templates and --check can see them.
        # Nothing is renamed, so v1 values carry over untouched -- which is what
        # keeps the next invoice number and the rendered output identical.
        migrated[SCHEMA_VERSION_KEY] = 2
        changed = True

    if version < 3:
        # v2 -> v3: amounts become configurable. A plain deep-merge would hand an
        # existing install the *neutral* default ($, grouped line amounts) and
        # silently restyle -- and re-denominate -- every receipt it goes on to
        # issue. Seed the values that reproduce what this install printed before
        # instead; only a brand-new config gets the neutral defaults.
        if "currency" not in migrated:
            migrated["currency"] = dict(LEGACY_CURRENCY)
        migrated[SCHEMA_VERSION_KEY] = 3
        changed = True

    if version < 4:
        # v3 -> v4: numbering moves from "scan the filenames" to a counter file.
        # The prefix and start carry over verbatim -- changing either would orphan
        # every INV-#### file already issued and restart the sequence. The counter
        # itself is seeded from the existing filenames the first time it is used,
        # which is what keeps the next number identical across this migration.
        if "invoice" not in migrated:
            migrated["invoice"] = dict(DEFAULT_APP_SETTINGS["invoice"])
        migrated[SCHEMA_VERSION_KEY] = 4
        changed = True

    settings = deep_merge(DEFAULT_APP_SETTINGS, migrated)
    if settings != migrated:
        changed = True
    settings[SCHEMA_VERSION_KEY] = SCHEMA_VERSION
    return settings, changed


_ALLOWED_LENGTH_UNITS = ("px", "mm", "cm", "in", "pt", "pc")


def validate(settings, filename=None):
    """Raise ConfigError on anything that would break a render. Returns settings.

    Runs at load and after any app-side write, so problems surface at startup
    with a file+key, not as a broken receipt.
    """
    filename = filename or APP_SETTINGS_FILE

    company = settings.get("company")
    if not isinstance(company, dict):
        raise ConfigError("must be an object", filename, "company")
    for key in ("name", "address", "phone", "email", "logo_path"):
        if not isinstance(company.get(key, ""), str):
            raise ConfigError("must be text", filename, f"company.{key}")
    if not str(company.get("name", "")).strip():
        raise ConfigError(
            "must not be empty -- it names the business on every receipt",
            filename, "company.name")

    signing = settings.get("signing")
    if not isinstance(signing, dict):
        raise ConfigError("must be an object", filename, "signing")
    if not isinstance(signing.get("enabled", True), bool):
        raise ConfigError("must be true or false", filename, "signing.enabled")
    for key in ("private_key_path", "certificate_path", "key_passphrase",
                "signer_name", "reason", "location", "tsa_url"):
        if not isinstance(signing.get(key, ""), str):
            raise ConfigError("must be text", filename, f"signing.{key}")
    if signing.get("enabled", True):
        for key in ("private_key_path", "certificate_path"):
            if not str(signing.get(key, "")).strip():
                raise ConfigError(
                    "signing is enabled, so this path is required "
                    "(or set signing.enabled to false)", filename, f"signing.{key}")
    tsa = str(signing.get("tsa_url", "")).strip()
    if tsa and not tsa.lower().startswith(("http://", "https://")):
        raise ConfigError(
            "must be an http:// or https:// URL when set", filename, "signing.tsa_url")

    currency = settings.get("currency")
    if not isinstance(currency, dict):
        raise ConfigError("must be an object", filename, "currency")
    for key in ("symbol", "code"):
        if not isinstance(currency.get(key, ""), str):
            raise ConfigError("must be text", filename, f"currency.{key}")
    for key in ("symbol_space", "group_line_amounts"):
        if not isinstance(currency.get(key, False), bool):
            raise ConfigError("must be true or false", filename, f"currency.{key}")
    decimals = currency.get("decimals", 2)
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 6:
        raise ConfigError(
            "must be a whole number of decimal places between 0 and 6",
            filename, "currency.decimals")
    for key, allowed in (("position", SYMBOL_POSITIONS),
                         ("group_style", GROUP_STYLES),
                         ("negative_style", NEGATIVE_STYLES)):
        value = currency.get(key, allowed[0])
        if value not in allowed:
            raise ConfigError(
                f"must be one of {', '.join(allowed)} (got {value!r})",
                filename, f"currency.{key}")

    invoice = settings.get("invoice")
    if not isinstance(invoice, dict):
        raise ConfigError("must be an object", filename, "invoice")
    prefix = invoice.get("prefix", INVOICE_PREFIX_BASE)
    if not isinstance(prefix, str):
        raise ConfigError("must be text", filename, "invoice.prefix")
    if re.search(r'[<>:"/\\|?*]', prefix):
        raise ConfigError(
            f"must not contain characters a filename cannot hold (got {prefix!r})",
            filename, "invoice.prefix")
    if prefix and prefix[-1:].isdigit():
        raise ConfigError(
            f"must not end in a digit (got {prefix!r}) -- the number is appended "
            f"directly, so the boundary between prefix and number would be "
            f"ambiguous when reading a receipt back",
            filename, "invoice.prefix")
    start = invoice.get("start", INVOICE_START_NUMBER)
    if isinstance(start, bool) or not isinstance(start, int) or start < 0:
        raise ConfigError(
            "must be a non-negative whole number", filename, "invoice.start")
    if not str(invoice.get("counter_file", "")).strip():
        raise ConfigError(
            "must name the file that stores the invoice sequence",
            filename, "invoice.counter_file")
    if not isinstance(invoice.get("reconcile_with_filenames", True), bool):
        raise ConfigError(
            "must be true or false", filename, "invoice.reconcile_with_filenames")

    tax = settings.get("tax")
    if not isinstance(tax, dict):
        raise ConfigError("must be an object", filename, "tax")
    if tax.get("mode", "exclusive") not in TAX_MODES:
        raise ConfigError(
            f"must be one of {', '.join(TAX_MODES)} (got {tax.get('mode')!r})",
            filename, "tax.mode")
    tax_rows = tax.get("rows", [])
    if not isinstance(tax_rows, list):
        raise ConfigError("must be a list of tax rows", filename, "tax.rows")
    for index, row in enumerate(tax_rows):
        where = f"tax.rows[{index}]"
        if not isinstance(row, dict):
            raise ConfigError("must be an object", filename, where)
        if not str(row.get("label", "")).strip():
            raise ConfigError(
                "must have a label -- it names the line on the receipt",
                filename, f"{where}.label")
        row_type = row.get("type", "percent")
        if row_type not in TAX_ROW_TYPES:
            raise ConfigError(
                f"must be one of {', '.join(TAX_ROW_TYPES)} (got {row_type!r})",
                filename, f"{where}.type")
        value = row.get("value", 0)
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ConfigError("must be a number", filename, f"{where}.value")
        try:
            numeric = float(str(value))
        except ValueError:
            raise ConfigError(
                f"must be a number (got {value!r})", filename, f"{where}.value") from None
        if numeric < 0:
            raise ConfigError("must not be negative", filename, f"{where}.value")
        if row_type == "percent" and numeric > 100:
            raise ConfigError(
                f"is a percentage, so it cannot exceed 100 (got {numeric})",
                filename, f"{where}.value")
        applies_to = row.get("applies_to", TAX_BASES[0])
        if applies_to not in TAX_BASES:
            raise ConfigError(
                f"must be one of {', '.join(TAX_BASES)} (got {applies_to!r})",
                filename, f"{where}.applies_to")

    date_format = settings.get("date_format", DATE_DISPLAY_FORMAT)
    if not isinstance(date_format, str) or not date_format.strip():
        raise ConfigError("must be a non-empty strftime pattern", filename, "date_format")
    try:
        # A pattern that cannot format a real date would fail at generation time,
        # on a receipt, rather than here.
        if not datetime.date(2026, 1, 31).strftime(date_format).strip():
            raise ValueError
    except (ValueError, TypeError):
        raise ConfigError(
            f"is not a usable strftime pattern ({date_format!r}); "
            f"for example \"%d %b %Y\" gives \"31 Jan 2026\"",
            filename, "date_format") from None

    types = settings.get("receipt_types")
    if not isinstance(types, list) or not types:
        raise ConfigError(
            "must be a non-empty list -- every receipt needs a type",
            filename, "receipt_types")
    seen_codes, seen_labels, legacy_claims = set(), set(), 0
    for index, entry in enumerate(types):
        where = f"receipt_types[{index}]"
        if not isinstance(entry, dict):
            raise ConfigError("must be an object", filename, where)
        label = str(entry.get("label", "")).strip()
        code = str(entry.get("code", "")).strip()
        if not label:
            raise ConfigError("must have a label", filename, where)
        if not code:
            raise ConfigError("must have a code", filename, f"{where}.code")
        if not code.isalnum():
            raise ConfigError(
                f"must be letters or digits only -- it becomes part of the "
                f"invoice number and the PDF filename (got {code!r})",
                filename, f"{where}.code")
        if code.upper() in seen_codes:
            raise ConfigError(
                f"duplicate code {code!r} -- two types sharing a code would share "
                f"one invoice series and produce duplicate numbers",
                filename, f"{where}.code")
        if label in seen_labels:
            raise ConfigError(f"duplicate label {label!r}", filename, f"{where}.label")
        seen_codes.add(code.upper())
        seen_labels.add(label)
        if entry.get("legacy_unlettered"):
            legacy_claims += 1
    if legacy_claims > 1:
        raise ConfigError(
            "only one receipt type may set legacy_unlettered -- otherwise two "
            "series would both count the old unlettered INV-#### files",
            filename, "receipt_types")

    terms_page = settings.get("terms_page")
    if not isinstance(terms_page, dict):
        raise ConfigError("must be an object", filename, "terms_page")
    if not isinstance(terms_page.get("enabled", True), bool):
        raise ConfigError("must be true or false", filename, "terms_page.enabled")

    document = settings.get("document")
    if not isinstance(document, dict):
        raise ConfigError("must be an object", filename, "document")
    for key in ("margin_top", "margin_bottom", "margin_left", "margin_right"):
        value = str(document.get(key, "")).strip()
        if not value:
            raise ConfigError("must not be empty", filename, f"document.{key}")
        if value == "0":
            continue                      # a bare zero is a valid CSS length
        if not value.lower().endswith(_ALLOWED_LENGTH_UNITS):
            raise ConfigError(
                f"must be a CSS length ending in one of "
                f"{', '.join(_ALLOWED_LENGTH_UNITS)} (got {value!r})",
                filename, f"document.{key}")
        try:
            if float(value[:-2].strip()) < 0:
                raise ValueError
        except ValueError:
            raise ConfigError(
                f"must be a non-negative CSS length (got {value!r})",
                filename, f"document.{key}") from None

    render = settings.get("render")
    if not isinstance(render, dict):
        raise ConfigError("must be an object", filename, "render")
    for key in ("block_external_requests", "fail_on_missing_image"):
        if not isinstance(render.get(key, False), bool):
            raise ConfigError("must be true or false", filename, f"render.{key}")
    timeout = render.get("timeout_ms", 30000)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ConfigError(
            "must be a positive whole number of milliseconds", filename, "render.timeout_ms")

    fonts = settings.get("fonts")
    if not isinstance(fonts, dict):
        raise ConfigError("must be an object", filename, "fonts")
    for key in ("family", "fallback"):
        if not isinstance(fonts.get(key, ""), str):
            raise ConfigError("must be text", filename, f"fonts.{key}")
    files = fonts.get("files", [])
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        raise ConfigError("must be a list of file paths", filename, "fonts.files")
    if str(fonts.get("family", "")).strip() and not files:
        raise ConfigError(
            "a font family is set but no font files are listed, so the family "
            "could never load -- add the file(s) or clear fonts.family",
            filename, "fonts.files")

    return settings


# ------------------- atomic, conflict-aware writes -------------------
def backup_path(path, now=None):
    """Timestamped sibling backup name, e.g. appsettings.json.20260822-2215.bak."""
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{path}.{stamp}.bak"


def atomic_write_json(path, data, expected_mtime=None, keep_backup=True):
    """Write data to path atomically, keeping a .bak of what was there.

    ``expected_mtime`` is the mtime the caller last read. If the file has
    changed since, ConfigConflict is raised instead of clobbering someone's
    edit. The write goes to a temp file in the same directory and is moved into
    place with os.replace, so a crash mid-write can never leave a half file.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    if expected_mtime is not None and os.path.exists(path):
        if os.path.getmtime(path) != expected_mtime:
            raise ConfigConflict(
                f"{os.path.basename(path)} was changed on disk since it was read. "
                f"Reload it, or overwrite deliberately.")

    if keep_backup and os.path.exists(path):
        try:
            backup = backup_path(path)
            with open(path, "rb") as src, open(backup, "wb") as dst:
                dst.write(src.read())
        except OSError:
            pass  # a missing backup must not stop the app from saving

    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


# ------------------- loaders -------------------
def default_app_settings():
    return json.loads(json.dumps(DEFAULT_APP_SETTINGS))   # deep copy


def load_app_settings(path=None, validate_settings=True):
    """Load, migrate and validate appsettings.json.

    A file that needs migrating is rewritten in place (keeping a ``.bak``) so the
    migration happens once rather than on every launch. An unreadable or invalid
    file falls back to defaults rather than taking the app down -- the file is
    left untouched in that case so the user can fix it.
    """
    path = path or APP_SETTINGS_FILE
    if not os.path.exists(path):
        save_default_app_settings(path)
        return default_app_settings()

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        mtime = os.path.getmtime(path)
    except (OSError, json.JSONDecodeError):
        return default_app_settings()

    try:
        settings, changed = migrate(raw, path)
        if validate_settings:
            validate(settings, path)
    except ConfigError:
        raise
    except Exception:
        return default_app_settings()

    if changed:
        try:
            atomic_write_json(path, settings, expected_mtime=mtime)
        except (OSError, ConfigConflict):
            pass  # keep running on the in-memory value; retry next launch

    settings = _normalize_strings(settings)
    return settings


def _normalize_strings(settings):
    """Trim whitespace on the string values the renderer interpolates."""
    for section in ("company", "signing"):
        block = settings.get(section)
        if isinstance(block, dict):
            for key, value in list(block.items()):
                if isinstance(value, str):
                    block[key] = value.strip()
    return settings


def save_default_app_settings(path=None):
    path = path or APP_SETTINGS_FILE
    try:
        atomic_write_json(path, DEFAULT_APP_SETTINGS, keep_backup=False)
    except OSError:
        pass


def load_filename_fields(path=None):
    path = path or FILENAME_CONFIG_FILE
    if not os.path.exists(path):
        save_default_filename_config(path)
        return list(DEFAULT_FILENAME_FIELDS)

    try:
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return list(DEFAULT_FILENAME_FIELDS)

    if not isinstance(config, dict):
        return list(DEFAULT_FILENAME_FIELDS)

    fields = config.get("filename_fields", DEFAULT_FILENAME_FIELDS)
    if not isinstance(fields, list):
        return list(DEFAULT_FILENAME_FIELDS)

    selected_fields = []
    for field in fields:
        if field in FILENAME_FIELD_OPTIONS and field not in selected_fields:
            selected_fields.append(field)
    return selected_fields


def receipt_types(settings=None):
    """The configured receipt types as an ordered list of dicts."""
    settings = settings if settings is not None else load_app_settings()
    types = settings.get("receipt_types") or DEFAULT_APP_SETTINGS["receipt_types"]
    return [dict(t) for t in types]


def receipt_type_labels(settings=None):
    """Type labels in config order -- what the GUI dropdown offers."""
    return [t["label"] for t in receipt_types(settings)]


def receipt_type_by_label(label, settings=None):
    """Look a type up by its label, falling back to the first configured type."""
    types = receipt_types(settings)
    for entry in types:
        if entry.get("label") == label:
            return entry
    return types[0]


def date_display_format(settings=None):
    settings = settings if settings is not None else load_app_settings()
    return settings.get("date_format") or DATE_DISPLAY_FORMAT


def date_parse_formats(settings=None):
    """Formats the date entry accepts, with the configured one tried first."""
    configured = date_display_format(settings)
    formats = [configured]
    formats.extend(f for f in DATE_PARSE_FORMATS if f != configured)
    return tuple(formats)


def strings_file():
    return os.path.join(APP_DIR, STRINGS_FILE_NAME)


def default_strings():
    return json.loads(json.dumps(DEFAULT_STRINGS))   # deep copy


def load_strings(path=None):
    """Load strings.json, deep-merged over the defaults.

    Missing or unreadable falls back to defaults rather than taking the app
    down: a typo in a wording file must never stop a receipt being issued.
    Missing individual keys are filled from defaults, so a partial file (a
    translation covering only some strings) works.
    """
    path = path or strings_file()
    if not os.path.exists(path):
        try:
            atomic_write_json(path, DEFAULT_STRINGS, keep_backup=False)
        except OSError:
            pass
        return default_strings()

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return default_strings()
    if not isinstance(raw, dict):
        return default_strings()
    return deep_merge(DEFAULT_STRINGS, raw)


def file_digest(path):
    """sha256 of a file's bytes, or '' if it cannot be read."""
    import hashlib
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def install_default_templates(force=False):
    """Seed APP_DIR/Templates from the bundled copies on first run.

    Records ``Templates/.installed.json`` -- ``{filename: {hash, installed}}``
    taken **at copy time**. A later upgrade needs that to tell "the user edited
    this" from "this is last version's default": unchanged files can be replaced
    silently, edited ones must be left alone. Recording the hashes later, or
    deriving them from the new build, would lose that distinction forever.

    Returns the list of filenames copied. Never overwrites an existing file
    unless ``force`` (principle 6: user-edited templates are never silently
    replaced). No-op when the bundled and installed directories are the same
    path, which is the case when running from a source checkout.
    """
    source, target = BUNDLED_TEMPLATES_DIR, TEMPLATES_DIR
    if not os.path.isdir(source) or os.path.abspath(source) == os.path.abspath(target):
        return []

    os.makedirs(target, exist_ok=True)
    manifest_path = os.path.join(target, INSTALLED_MANIFEST)
    manifest = {}
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}

    copied = []
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    for name in sorted(os.listdir(source)):
        src = os.path.join(source, name)
        if not os.path.isfile(src) or name == INSTALLED_MANIFEST:
            continue
        dst = os.path.join(target, name)
        if os.path.exists(dst) and not force:
            continue
        # If this install already had an edited copy in the old flat layout,
        # seed Templates/ from *that* rather than the shipped default. Copying
        # the default here would put an unedited file in a location that now
        # shadows the user's own, silently reverting their branding.
        legacy = os.path.join(APP_DIR, name)
        if not force and os.path.isfile(legacy):
            src = legacy
        try:
            with open(src, "rb") as s, open(dst, "wb") as d:
                d.write(s.read())
        except OSError:
            continue
        manifest[name] = {"hash": file_digest(dst), "installed": stamp}
        copied.append(name)

    if copied:
        try:
            atomic_write_json(manifest_path, manifest, keep_backup=False)
        except OSError:
            pass
    return copied


def save_default_filename_config(path=None):
    path = path or FILENAME_CONFIG_FILE
    config = {
        "filename_fields": list(DEFAULT_FILENAME_FIELDS),
        "available_fields": list(FILENAME_FIELD_OPTIONS),
    }
    try:
        atomic_write_json(path, config, keep_backup=False)
    except OSError:
        pass
