"""A record of every receipt generated, so a mistake can be corrected.

Getting a detail wrong on a receipt is ordinary, and re-typing a whole sale to
fix one line is not. Every generation appends its **input data** here, which is
what makes it possible to pull a past receipt back into the form, change the
wrong thing and issue it again.

Deliberate choices:

* **The record survives the PDF.** It is keyed by nothing on disk, so deleting,
  moving or renaming the file changes nothing here. "I deleted it and now I need
  it back" is exactly the case this exists for.
* **JSON, not CSV.** A receipt holds a variable-length list of line items whose
  fields are user-configurable, which CSV cannot represent without inventing
  unstable columns that break the moment someone adds a field. (A CSV *export*
  for spreadsheets is a different job and can sit on top of this.)
* **One JSON object per line**, appended. There is no index to fall out of step
  with the data, a half-written line cannot corrupt the lines before it, and the
  file stays greppable.
* **Amounts are stored as strings.** JSON numbers are floats, and round-tripping
  money through a float loses exactness.
* **Failing to record must never fail a receipt.** The signed PDF is the legal
  artifact; losing a history line is an inconvenience, so writes here warn and
  carry on.
"""
import datetime
import json
import logging
import os

import config

logger = logging.getLogger("receipt_maker")

HISTORY_VERSION = 1
ARCHIVE_DIRNAME = ".archive"
HISTORY_FILENAME = "history.jsonl"


def archive_dir():
    """Kept in a subfolder so PII records are not mixed into browsable receipts."""
    return os.path.join(config.OUTPUT_DIR, ARCHIVE_DIRNAME)


def history_path():
    return os.path.join(archive_dir(), HISTORY_FILENAME)


def _as_text(value):
    return "" if value is None else str(value)


def build_record(data, pdf_path="", signed=False, now=None):
    """Turn a generation's input into the record stored for it. Pure."""
    stamp = (now or datetime.datetime.now()).isoformat(timespec="seconds")
    items = []
    for item in data.get("items") or []:
        items.append({key: (_as_text(value) if not isinstance(value, bool) else value)
                      for key, value in item.items()})
    return {
        "history_version": HISTORY_VERSION,
        "recorded_at": stamp,
        "invoice_no": _as_text(data.get("inv_no")),
        "receipt_type": _as_text(data.get("receipt_type")),
        "date": _as_text(data.get("date_str")),
        "customer": {
            "name": _as_text(data.get("cust")),
            "phone": _as_text(data.get("phone")),
            "email": _as_text(data.get("email")),
        },
        "shipping": _as_text(data.get("shipping")),
        "items": items,
        "pdf_path": _as_text(pdf_path),
        "pdf_name": os.path.basename(_as_text(pdf_path)),
        "signed": bool(signed),
    }


def record(data, pdf_path="", signed=False):
    """Append one receipt to the history. Never raises."""
    try:
        os.makedirs(archive_dir(), exist_ok=True)
        line = json.dumps(build_record(data, pdf_path, signed), ensure_ascii=False)
        with open(history_path(), "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Could not record %s in the receipt history: %s",
                       data.get("inv_no", "?"), exc)
        return False


def entries(newest_first=True):
    """Every recorded receipt. A damaged line is skipped, not fatal."""
    path = history_path()
    found = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for number, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping unreadable history line %d in %s",
                                   number, path)
                    continue
                if isinstance(entry, dict):
                    entry["_line"] = number
                    found.append(entry)
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("Could not read the receipt history: %s", exc)
        return []
    return list(reversed(found)) if newest_first else found


def latest_for(invoice_no):
    """The most recent record of one receipt, or None.

    Used to work out what a reissue changed, so a corrected receipt adjusts
    stock by the difference rather than deducting the whole sale twice.
    """
    wanted = str(invoice_no or "").strip().lower()
    if not wanted:
        return None
    for entry in entries():                      # already newest-first
        if str(entry.get("invoice_no", "")).strip().lower() == wanted:
            return entry
    return None


def to_form_data(entry):
    """Turn a stored record back into the shape the form and renderer expect."""
    customer = entry.get("customer") or {}
    return {
        "inv_no": entry.get("invoice_no", ""),
        "date_str": entry.get("date", ""),
        "cust": customer.get("name", ""),
        "phone": customer.get("phone", ""),
        "email": customer.get("email", ""),
        "receipt_type": entry.get("receipt_type", ""),
        "shipping": entry.get("shipping", ""),
        "items": [dict(item) for item in entry.get("items") or []],
    }


def summarise(entry, currency=None):
    """(date, invoice_no, customer, total, status) for the history list."""
    import receipt_render

    currency = currency if currency is not None else config.DEFAULT_APP_SETTINGS["currency"]
    decimals = currency.get("decimals", 2)

    total = receipt_render.to_decimal(0)
    for item in entry.get("items") or []:
        line = (receipt_render.to_decimal(item.get("qty", 0))
                * receipt_render.to_decimal(item.get("price", 0)))
        total += receipt_render.quantize(line, decimals)
        total += receipt_render.quantize(item.get("tax", 0), decimals)
        total -= receipt_render.quantize(item.get("discount", 0), decimals)
    total += receipt_render.quantize(entry.get("shipping", 0), decimals)

    return (
        entry.get("date", ""),
        entry.get("invoice_no", ""),
        (entry.get("customer") or {}).get("name", ""),
        receipt_render.format_amount(total, currency),
        "signed" if entry.get("signed") else "unsigned",
    )


def matches(entry, needle):
    """Free-text search across the fields someone would actually search by."""
    needle = str(needle or "").strip().lower()
    if not needle:
        return True
    haystack = [entry.get("invoice_no", ""), entry.get("date", ""),
                (entry.get("customer") or {}).get("name", ""),
                (entry.get("customer") or {}).get("phone", ""),
                (entry.get("customer") or {}).get("email", ""),
                entry.get("pdf_name", "")]
    haystack.extend(str(item.get("desc", "")) for item in entry.get("items") or [])
    haystack.extend(str(item.get("sku", "")) for item in entry.get("items") or [])
    return any(needle in str(value).lower() for value in haystack)
