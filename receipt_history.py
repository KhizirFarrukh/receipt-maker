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
import line_units

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


#: Receipt-level extras that are absent on most receipts. They are stored and
#: restored only when set, so an ordinary sale carries no empty keys and the
#: shape that comes back out of history matches the shape that went in.
OPTIONAL_KEYS = ("payment_method", "shipments", "installment")


def _optional(source):
    """The optional receipt-level values that are actually present."""
    out = {}
    for key in OPTIONAL_KEYS:
        value = source.get(key)
        if not value:
            continue
        if key == "shipments":
            value = [dict(s) for s in value if isinstance(s, dict)]
        elif key == "installment":
            value = dict(value)
        else:
            value = _as_text(value)
        if value:
            out[key] = value
    return out


def build_record(data, pdf_path="", signed=False, now=None):
    """Turn a generation's input into the record stored for it. Pure."""
    stamp = (now or datetime.datetime.now()).isoformat(timespec="seconds")
    items = []
    for item in data.get("items") or []:
        record = {}
        for key, value in item.items():
            if key == line_units.UNITS_KEY:
                # Per-unit records are the one structured value on a line. Text
                # is right for everything else -- a quantity reloaded as the
                # string it was typed as re-renders identically -- but stringing
                # this one would store "[{'serial': ...}]" and lose the serials
                # the moment the receipt was reloaded to be corrected.
                record[key] = [
                    {k: _as_text(v) for k, v in unit.items()}
                    for unit in (value or []) if isinstance(unit, dict)
                ]
            elif isinstance(value, bool):
                record[key] = value
            else:
                record[key] = _as_text(value)
        items.append(record)
    return {
        **_optional(data),
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


def void(invoice_no, reason="", settings=None):
    """Mark a receipt void and return its stock. Returns (ok, message).

    A void is a *new record*, not an edit: the history file is append-only, and
    a receipt that was issued and later cancelled is two facts rather than one
    corrected fact. The original entry stays exactly as it was written, which is
    what makes the file worth having if anyone ever asks what was sold.

    **The invoice number is not freed.** That is the same rule as everywhere
    else here: a number that has been on a receipt in a customer's hands cannot
    be un-issued, and handing it out again would put two different sales under
    one number. The gap in the sequence is the point -- it is explained by the
    void record sitting in the history.

    **The stock does come back**, which is the opposite decision, and for the
    reason the two were always different: a stock figure can be recounted, so
    getting it wrong is recoverable, while a duplicate invoice number is not.
    Goods that were never sold are still on the shelf.
    """
    entry = latest_for(invoice_no)
    if entry is None:
        return False, f"No receipt numbered {invoice_no} is in the history."
    if entry.get("voided"):
        return False, f"{invoice_no} is already void."

    returned = False
    try:
        import product_catalogue
        # An empty sale against what this receipt took: the deltas come out
        # negative, so the same tested path that deducted the stock puts it
        # back, rather than a second implementation that could disagree.
        returned = product_catalogue.record_sale(
            invoice_no, [], previous_items=entry.get("items") or [],
            settings=settings)
    except Exception as exc:                     # noqa: BLE001 - voiding must not fail
        logger.warning("Could not return stock for %s: %s", invoice_no, exc)

    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    record_line = {
        "history_version": HISTORY_VERSION,
        "recorded_at": stamp,
        "invoice_no": _as_text(entry.get("invoice_no")),
        "receipt_type": _as_text(entry.get("receipt_type")),
        "date": _as_text(entry.get("date")),
        "customer": dict(entry.get("customer") or {}),
        "shipping": _as_text(entry.get("shipping")),
        # The lines are carried across so a void is self-contained: what was
        # cancelled is readable without walking back to the original entry.
        "items": [dict(i) for i in entry.get("items") or []],
        "pdf_path": _as_text(entry.get("pdf_path")),
        "pdf_name": _as_text(entry.get("pdf_name")),
        "signed": bool(entry.get("signed")),
        "voided": True,
        "void_reason": _as_text(reason),
        "stock_returned": bool(returned),
    }
    try:
        os.makedirs(archive_dir(), exist_ok=True)
        with open(history_path(), "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(record_line, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Could not record the void of %s: %s", invoice_no, exc)
        return False, f"Could not write the void record: {exc}"

    logger.warning("Receipt %s was voided%s. Stock %s.", invoice_no,
                   f" ({reason})" if reason else "",
                   "was returned" if returned else "was not tracked")
    return True, ("Voided." + (" Stock returned." if returned else ""))


def is_voided(invoice_no):
    """Whether the most recent record of this receipt is a void."""
    entry = latest_for(invoice_no)
    return bool(entry and entry.get("voided"))


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
        **_optional(entry),
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
        "VOID" if entry.get("voided") else
        ("signed" if entry.get("signed") else "unsigned"),
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
