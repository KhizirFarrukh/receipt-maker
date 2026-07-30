"""Paths, config constants, and config-file loaders (tkinter-free).

Extracted from main.py in Stage 1 so the render/service layers, cli.py, and the
tests can import configuration without pulling in tkinter. Stage 2 adds
schema_version/migrate/validate/atomic-writes here; for now this is a faithful
relocation of the existing loaders.
"""
import json
import os
import sys

# ------------------- file paths -------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else BASE_DIR
RESOURCE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
HEADER_FILE = os.path.join(RESOURCE_DIR, "header.html")
FOOTER_FILE = os.path.join(RESOURCE_DIR, "footer.html")
APP_SETTINGS_FILE = os.path.join(APP_DIR, "appsettings.json")
FILENAME_CONFIG_FILE = os.path.join(APP_DIR, "filename_config.json")
OUTPUT_DIR = os.path.join(APP_DIR, "invoices")
PDF_MARGIN_TOP = "150px"
PDF_MARGIN_BOTTOM = "100px"
PDF_MARGIN_LEFT = "24px"
PDF_MARGIN_RIGHT = "24px"

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
DEFAULT_APP_SETTINGS = {
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
        "signer_name": "Chawla Tech",
        "reason": "Receipt authenticity",
        "location": "chawlatech.pk",
        "tsa_url": "",
    },
}


# ------------------- read HTML snippets -------------------
def read_html_file(path):
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_app_settings():
    if not os.path.exists(APP_SETTINGS_FILE):
        save_default_app_settings()
        return default_app_settings()

    try:
        with open(APP_SETTINGS_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return default_app_settings()

    settings = default_app_settings()
    if not isinstance(config, dict):
        return settings

    company_config = config.get("company", {})
    if isinstance(company_config, dict):
        for key in settings["company"]:
            value = company_config.get(key)
            if isinstance(value, str):
                settings["company"][key] = value.strip()

    signing_config = config.get("signing", {})
    if isinstance(signing_config, dict):
        for key, default_value in settings["signing"].items():
            value = signing_config.get(key)
            if isinstance(default_value, bool):
                if isinstance(value, bool):
                    settings["signing"][key] = value
            elif isinstance(value, str):
                settings["signing"][key] = value.strip()
    return settings


def default_app_settings():
    return {
        "company": dict(DEFAULT_APP_SETTINGS["company"]),
        "signing": dict(DEFAULT_APP_SETTINGS["signing"]),
    }


def save_default_app_settings():
    try:
        with open(APP_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_APP_SETTINGS, f, indent=2)
            f.write("\n")
    except OSError:
        pass


def load_filename_fields():
    if not os.path.exists(FILENAME_CONFIG_FILE):
        save_default_filename_config()
        return DEFAULT_FILENAME_FIELDS

    try:
        with open(FILENAME_CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_FILENAME_FIELDS

    fields = config.get("filename_fields", DEFAULT_FILENAME_FIELDS)
    if not isinstance(fields, list):
        return DEFAULT_FILENAME_FIELDS

    selected_fields = []
    for field in fields:
        if field in FILENAME_FIELD_OPTIONS and field not in selected_fields:
            selected_fields.append(field)
    return selected_fields


def save_default_filename_config():
    config = {
        "filename_fields": DEFAULT_FILENAME_FIELDS,
        "available_fields": list(FILENAME_FIELD_OPTIONS),
    }
    try:
        with open(FILENAME_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
    except OSError:
        pass
