"""CSV views over the catalogue and the receipt history.

TODO.md §2 and §4. Spreadsheets are how a shop actually works with a list of
products or a quarter of sales, and neither JSON file is pleasant to open in
one.

**JSON stays the source of truth; CSV is a view.** That is not a preference, it
is forced by the data: a product holds a list of variants that override some of
its fields and a list of serial numbers, and a receipt holds a variable-length
list of lines. CSV is a rectangle and none of that is rectangular. So the two
directions are deliberately not symmetric:

* **Products go both ways.** Variants become their own rows carrying a
  `parent_sku`, and serial numbers are joined with `;` — a flattening that can
  be undone exactly, which is what makes import safe.
* **History goes out only.** A receipt exports as one row per *line*, which is
  the shape you would pivot on, and that is lossy by design — the same receipt
  appears on several rows. Importing it back would mean inventing a rule for
  reassembling receipts from rows, and the history file is an append-only record
  of what happened. Editing it in a spreadsheet and pushing it back is exactly
  what it must not allow.

Written with a UTF-8 BOM, because Excel on Windows reads a plain UTF-8 CSV as
the system codepage and mangles every name with an accent in it.
"""
import csv
import io
import logging
import os

import config

logger = logging.getLogger("receipt_maker")

#: Excel needs the BOM to recognise UTF-8. Everything else ignores it.
ENCODING = "utf-8-sig"

#: Separator inside a cell that holds a list. Semicolon rather than comma so the
#: cell needs no quoting, and rather than a newline so the row stays one row.
LIST_SEPARATOR = ";"

#: Columns that are numbers rather than text. A CSV cell is always a string,
#: so these are coerced on the way in -- the catalogue validator wants a whole
#: number of units and would otherwise reject every imported row.
NUMERIC_PRODUCT_COLUMNS = ("stock_count",)

PRODUCT_COLUMNS = ("sku", "barcode", "name", "variant_name", "parent_sku",
                   "list_price", "cost_price", "bulk_price", "sell_price",
                   "stock_count", "serial_numbers")

HISTORY_COLUMNS = ("invoice_no", "date", "recorded_at", "receipt_type",
                   "customer_name", "customer_phone", "customer_email",
                   "status", "pdf_name", "line_no", "sku", "barcode",
                   "description", "serial", "qty", "unit_price", "discount",
                   "tax", "line_total", "shipping", "payment_method")


class CsvError(ValueError):
    """A CSV that could not be read, with a row number when there is one."""


def _text(value):
    return "" if value is None else str(value)


def _join(values):
    if isinstance(values, (list, tuple)):
        return LIST_SEPARATOR.join(_text(v).strip() for v in values if _text(v).strip())
    return _text(values)


def _split(value):
    return [part.strip() for part in _text(value).split(LIST_SEPARATOR) if part.strip()]


def _whole_number(value, column, number):
    """A CSV cell as an int, or a CsvError naming the row and column."""
    text = _text(value).strip()
    try:
        return int(text)
    except ValueError:
        # Accept "10.0" -- spreadsheets format whole numbers that way and
        # refusing would make a file exported from Excel unimportable.
        try:
            as_float = float(text)
        except ValueError:
            as_float = None
        if as_float is not None and as_float == int(as_float):
            return int(as_float)
        raise CsvError(
            f"row {number}: {column} is {value!r}, which is not a whole number "
            f"of units.") from None


# ------------------- products -------------------
def products_to_rows(catalogue):
    """Flatten the catalogue into rows, variants included.

    A variant becomes its own row naming its `parent_sku`. It carries only what
    it overrides, so a blank cell means "inherit", which is exactly what the
    variant record means and survives the round trip unchanged.
    """
    rows = []
    for product in (catalogue or {}).get("products") or []:
        if not isinstance(product, dict):
            continue
        row = {key: "" for key in PRODUCT_COLUMNS}
        for key in PRODUCT_COLUMNS:
            if key in ("variant_name", "parent_sku"):
                continue
            if key == "serial_numbers":
                row[key] = _join(product.get(key))
            else:
                row[key] = _text(product.get(key, ""))
        rows.append(row)

        for variant in product.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            child = {key: "" for key in PRODUCT_COLUMNS}
            child["parent_sku"] = _text(product.get("sku", ""))
            child["variant_name"] = _text(variant.get("name", ""))
            for key in PRODUCT_COLUMNS:
                if key in ("variant_name", "parent_sku", "name"):
                    continue
                if key not in variant:
                    continue
                child[key] = (_join(variant.get(key)) if key == "serial_numbers"
                              else _text(variant.get(key)))
            rows.append(child)
    return rows


def rows_to_products(rows):
    """Rebuild products from flattened rows. Raises CsvError on nonsense.

    Order matters only for variants: a row naming a `parent_sku` is attached to
    that product, which must already have been seen. Reporting the row number is
    the difference between "fix line 42" and "something is wrong with your file".
    """
    products = []
    by_sku = {}

    for number, row in enumerate(rows, start=2):     # row 1 is the header
        parent_sku = _text(row.get("parent_sku", "")).strip()
        sku = _text(row.get("sku", "")).strip()

        if parent_sku:
            parent = by_sku.get(parent_sku.casefold())
            if parent is None:
                raise CsvError(
                    f"row {number}: this is a variant of {parent_sku!r}, but no "
                    f"product with that SKU appears above it. A variant has to "
                    f"follow its product.")
            variant = {"name": _text(row.get("variant_name", "")).strip()}
            if not variant["name"]:
                raise CsvError(
                    f"row {number}: a variant needs a name in `variant_name` — "
                    f"it is the label the receipt prints.")
            for key in PRODUCT_COLUMNS:
                if key in ("variant_name", "parent_sku", "name"):
                    continue
                value = _text(row.get(key, "")).strip()
                if not value:
                    continue        # blank means inherit, which is the default
                if key == "serial_numbers":
                    variant[key] = _split(value)
                elif key in NUMERIC_PRODUCT_COLUMNS:
                    variant[key] = _whole_number(value, key, number)
                else:
                    variant[key] = value
            parent.setdefault("variants", []).append(variant)
            continue

        if not sku:
            raise CsvError(f"row {number}: every product needs a SKU.")
        if sku.casefold() in by_sku:
            raise CsvError(
                f"row {number}: SKU {sku!r} appears twice. A scan has to "
                f"identify exactly one product.")

        product = {}
        for key in PRODUCT_COLUMNS:
            if key in ("variant_name", "parent_sku"):
                continue
            value = _text(row.get(key, "")).strip()
            if not value:
                continue
            if key == "serial_numbers":
                product[key] = _split(value)
            elif key in NUMERIC_PRODUCT_COLUMNS:
                product[key] = _whole_number(value, key, number)
            else:
                product[key] = value
        by_sku[sku.casefold()] = product
        products.append(product)

    return products


def export_products(path, catalogue=None):
    """Write the catalogue to `path`. Returns the number of rows written."""
    import product_catalogue
    catalogue = catalogue if catalogue is not None else product_catalogue.load()
    rows = products_to_rows(catalogue)
    _write(path, PRODUCT_COLUMNS, rows)
    return len(rows)


def import_products(path, replace=False):
    """Read products from `path`. Returns (catalogue, added, updated).

    Merges by SKU rather than replacing, unless asked. Merging is the safe
    default because a CSV is usually a partial list — this week's new stock, a
    price update from a supplier — and replacing wholesale would delete
    everything not in it. `replace=True` is there for a deliberate full reload.
    """
    import product_catalogue

    rows = _read(path, PRODUCT_COLUMNS)
    incoming = rows_to_products(rows)

    if replace:
        catalogue = product_catalogue.default_catalogue()
        catalogue["products"] = incoming
        product_catalogue.validate(catalogue, path)
        return catalogue, len(incoming), 0

    catalogue = product_catalogue.load()
    existing = catalogue.setdefault("products", [])
    by_sku = {str(p.get("sku", "")).casefold(): p
              for p in existing if isinstance(p, dict)}

    added = updated = 0
    for product in incoming:
        key = str(product.get("sku", "")).casefold()
        if key in by_sku:
            by_sku[key].update(product)
            updated += 1
        else:
            existing.append(product)
            by_sku[key] = product
            added += 1

    product_catalogue.validate(catalogue, path)
    return catalogue, added, updated


# ------------------- history -------------------
def history_to_rows(entries, currency=None):
    """One row per line item, which is the shape worth pivoting on.

    Receipt-level values repeat on each of a receipt's rows. That is redundant
    in a file and exactly right in a spreadsheet, where every row has to stand
    on its own to be filtered or summed.
    """
    import receipt_render

    currency = currency or config.DEFAULT_APP_SETTINGS["currency"]
    decimals = currency.get("decimals", 2)

    rows = []
    for entry in entries or []:
        customer = entry.get("customer") or {}
        status = ("void" if entry.get("voided")
                  else ("signed" if entry.get("signed") else "unsigned"))
        shared = {
            "invoice_no": _text(entry.get("invoice_no")),
            "date": _text(entry.get("date")),
            "recorded_at": _text(entry.get("recorded_at")),
            "receipt_type": _text(entry.get("receipt_type")),
            "customer_name": _text(customer.get("name")),
            "customer_phone": _text(customer.get("phone")),
            "customer_email": _text(customer.get("email")),
            "status": status,
            "pdf_name": _text(entry.get("pdf_name")),
            "shipping": _text(entry.get("shipping")),
            "payment_method": _text(entry.get("payment_method")),
        }
        for index, item in enumerate(entry.get("items") or [], start=1):
            row = dict.fromkeys(HISTORY_COLUMNS, "")
            row.update(shared)
            row.update({
                "line_no": str(index),
                "sku": _text(item.get("sku")),
                "barcode": _text(item.get("barcode")),
                "description": _text(item.get("desc")),
                "qty": _text(item.get("qty")),
                "unit_price": _text(item.get("price")),
                "discount": _text(item.get("discount")),
                "tax": _text(item.get("tax")),
                "line_total": str(receipt_render.line_total(item, decimals)),
            })
            # Per-unit serials collapse into one cell; a line's serials belong
            # to that line, so splitting rows by unit would double the money.
            units = item.get("units") or []
            serials = [u.get("serial", "") for u in units if isinstance(u, dict)]
            row["serial"] = _join(serials) or _text(item.get("serial"))
            rows.append(row)

        if not entry.get("items"):
            # A receipt with no lines still happened; dropping it would make the
            # export disagree with the history it came from.
            row = dict.fromkeys(HISTORY_COLUMNS, "")
            row.update(shared)
            rows.append(row)
    return rows


def export_history(path, entries=None, currency=None):
    """Write the receipt history to `path`. Returns the number of rows."""
    import receipt_history
    entries = entries if entries is not None else receipt_history.entries()
    rows = history_to_rows(entries, currency)
    _write(path, HISTORY_COLUMNS, rows)
    return len(rows)


# ------------------- file io -------------------
def _write(path, columns, rows):
    # newline="" is required by the csv module: without it every row gains a
    # blank line on Windows.
    with open(path, "w", encoding=ENCODING, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns),
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read(path, columns):
    if not os.path.isfile(path):
        raise CsvError(f"{path} does not exist.")
    with open(path, "r", encoding=ENCODING, newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise CsvError(f"{os.path.basename(path)} is empty.")
        # Accept a subset and any order -- a supplier's export will not match
        # this file's column list exactly, and refusing over that would make
        # the feature useless. Unknown columns are ignored; missing ones read
        # as blank.
        known = {name.strip() for name in reader.fieldnames if name}
        if not known & set(columns):
            raise CsvError(
                f"{os.path.basename(path)} has none of the expected columns "
                f"({', '.join(columns)}). Is it the right file?")
        return [{(k or "").strip(): v for k, v in row.items()} for row in reader]
