"""The product catalogue: sell a known product instead of retyping it.

Stored as JSON in ``products.json`` beside the other config. That choice is
about the *shape* of the data rather than its size: a product carries a list of
serial numbers and a list of variants that override some of its fields, which is
natural in JSON and would need three tables and joins in SQL. See TODO.md for
the full reasoning, including when SQLite would become the better answer.

**Variants override their parent.** A variant only states what differs -- a
colour with its own SKU and barcode inherits the parent's name and prices unless
it says otherwise. The alternative, a full product with a parent pointer, means
restating every field on every variant and lets the two drift apart.

**Stock is stored but not yet spent.** Nothing here decrements on sale, because
"what happens when generation fails, or when a receipt is reissued from history"
has the same shape as the invoice-numbering problem and deserves the same
explicit answer rather than one arrived at by accident. Recorded in TODO.md.
"""
import json
import logging
import os
from decimal import Decimal, InvalidOperation

import config

logger = logging.getLogger("receipt_maker")

CATALOGUE_FILE_NAME = "products.json"
CATALOGUE_SCHEMA_VERSION = 1

#: Fields a variant may override. Anything else is inherited from the parent.
#:
#: `name` is deliberately absent: on a variant it is the variant's *label*
#: ("Blue", "64GB"), not a replacement product name. Treating it as an override
#: loses the parent's name and prints "Blue (Blue)" on the receipt.
VARIANT_FIELDS = ("sku", "barcode", "list_price", "cost_price",
                  "bulk_price", "sell_price", "stock_count", "serial_numbers")

MONEY_FIELDS = ("list_price", "cost_price", "bulk_price", "sell_price")

DEFAULT_CATALOGUE = {
    config.SCHEMA_VERSION_KEY: CATALOGUE_SCHEMA_VERSION,
    "products": [],
}


def catalogue_path():
    return os.path.join(config.APP_DIR, CATALOGUE_FILE_NAME)


# ------------------- money helpers -------------------
def to_decimal(value):
    try:
        return Decimal(str(value).strip() or "0")
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def price_from_markup(cost, percent):
    """Markup is added *to cost*: cost 100 at 25% -> 125."""
    return to_decimal(cost) * (Decimal(1) + to_decimal(percent) / Decimal(100))


def price_from_margin(cost, percent):
    """Margin is a share *of the sale price*: cost 100 at 25% -> 133.33.

    Not the same as markup, and confusing the two is a common and expensive
    pricing mistake -- which is why they are separate functions with separate
    names rather than one function with a flag.
    """
    share = to_decimal(percent) / Decimal(100)
    if share >= 1:
        # A 100% margin implies infinite price; refuse rather than divide by zero.
        raise ValueError("A margin of 100% or more is not a real price.")
    return to_decimal(cost) / (Decimal(1) - share)


def price_from_discount(list_price, percent):
    """Discount comes off the list price."""
    return to_decimal(list_price) * (Decimal(1) - to_decimal(percent) / Decimal(100))


def margin_of(cost, sell):
    """The margin a given selling price achieves, as a percentage."""
    sell = to_decimal(sell)
    if sell == 0:
        return Decimal("0")
    return (sell - to_decimal(cost)) / sell * Decimal(100)


def markup_of(cost, sell):
    """The markup a given selling price represents, as a percentage."""
    cost = to_decimal(cost)
    if cost == 0:
        return Decimal("0")
    return (to_decimal(sell) - cost) / cost * Decimal(100)


# ------------------- model -------------------
def effective(product, variant=None):
    """A variant merged over its parent, or the product itself. Never mutates."""
    merged = dict(product)
    merged.pop("variants", None)
    if variant:
        for key in VARIANT_FIELDS:
            value = variant.get(key)
            if value not in (None, "", []):
                merged[key] = value
        merged["variant_of"] = product.get("sku", "")
        merged["variant_name"] = variant.get("name", "") or variant.get("sku", "")
    return merged


def sellable_items(catalogue):
    """Everything that can go on a receipt: products, plus each variant.

    A product with variants is still listed itself -- a shop may well sell "the
    keyboard" generically and only pick a colour sometimes.
    """
    items = []
    for product in catalogue.get("products", []):
        if not isinstance(product, dict):
            continue
        items.append(effective(product))
        for variant in product.get("variants") or []:
            if isinstance(variant, dict):
                items.append(effective(product, variant))
    return items


def find(catalogue, needle):
    """Exact match on SKU or barcode, which is what a scanner produces."""
    needle = str(needle or "").strip().lower()
    if not needle:
        return None
    for item in sellable_items(catalogue):
        if needle in (str(item.get("sku", "")).lower(),
                      str(item.get("barcode", "")).lower()):
            return item
    return None


def search(catalogue, needle):
    """Free-text match over the fields someone would type to find a product."""
    needle = str(needle or "").strip().lower()
    items = sellable_items(catalogue)
    if not needle:
        return items
    return [item for item in items
            if any(needle in str(item.get(key, "")).lower()
                   for key in ("sku", "barcode", "name", "variant_name"))]


def to_line_item(item, quantity=1, price_field="sell_price"):
    """Turn a catalogue entry into the line-item shape the form expects.

    Falls back through sell -> list -> bulk so a product priced only one way
    still sells, rather than landing on the receipt at zero.
    """
    price = ""
    for key in (price_field, "sell_price", "list_price", "bulk_price"):
        candidate = str(item.get(key, "") or "").strip()
        if candidate and to_decimal(candidate) != 0:
            price = candidate
            break

    name = item.get("name", "")
    if item.get("variant_name"):
        name = f"{name} ({item['variant_name']})" if name else item["variant_name"]

    return {
        "sku": item.get("sku", ""),
        "barcode": item.get("barcode", ""),
        "desc": name,
        "serial": "",
        "qty": quantity,
        "price": price,
        "discount": "0",
        "tax": "0",
        "warranty": "",
    }


# ------------------- stock -------------------
def quantities_by_sku(items):
    """Total quantity per SKU across a receipt's lines. Lines with no SKU are ignored."""
    totals = {}
    for item in items or []:
        sku = str(item.get("sku", "") or "").strip()
        if not sku:
            continue                       # nothing to match against the catalogue
        try:
            quantity = int(str(item.get("qty", 0) or 0).strip() or 0)
        except ValueError:
            continue
        totals[sku.lower()] = totals.get(sku.lower(), 0) + quantity
    return totals


def stock_deltas(new_items, previous_items=None):
    """How much stock each SKU should lose for this generation.

    The subtlety is reissuing. Correcting a receipt writes it again, and
    deducting the full quantity a second time would double-count a sale that
    only happened once. So the deduction is the *difference* from what that same
    receipt previously took: change a line from 2 to 3 and one more unit leaves;
    change it from 3 to 2 and one comes back.
    """
    new_totals = quantities_by_sku(new_items)
    old_totals = quantities_by_sku(previous_items) if previous_items else {}
    deltas = {}
    for sku in set(new_totals) | set(old_totals):
        delta = new_totals.get(sku, 0) - old_totals.get(sku, 0)
        if delta:
            deltas[sku] = delta
    return deltas


def apply_stock_deltas(catalogue, deltas):
    """Subtract deltas from the matching products. Returns (catalogue, changed, shortfalls).

    A count going negative is reported but never refused: the stock figure is
    frequently a little stale, and blocking a sale at the till over a number that
    might simply not have been counted would be far worse than recording it and
    saying so.
    """
    if not deltas:
        return catalogue, False, []

    changed = False
    shortfalls = []

    def adjust(entry):
        nonlocal changed
        sku = str(entry.get("sku", "") or "").strip().lower()
        if sku not in deltas:
            return
        current = entry.get("stock_count")
        if not isinstance(current, int) or isinstance(current, bool):
            return                          # never counted; leave it alone
        updated = current - deltas[sku]
        entry["stock_count"] = updated
        changed = True
        if updated < 0:
            shortfalls.append((entry.get("sku", ""), updated))

    for product in catalogue.get("products", []):
        if not isinstance(product, dict):
            continue
        adjust(product)
        for variant in product.get("variants") or []:
            if isinstance(variant, dict):
                adjust(variant)

    return catalogue, changed, shortfalls


def record_sale(receipt_no, items, previous_items=None, settings=None):
    """Deduct a receipt's lines from stock. Never raises.

    Called *after* the receipt exists, so a failed render deducts nothing. Does
    nothing at all unless inventory.track_stock is on.
    """
    settings = settings if settings is not None else config.load_app_settings()
    if not settings.get("inventory", {}).get("track_stock", False):
        return False

    try:
        deltas = stock_deltas(items, previous_items)
        if not deltas:
            return False
        catalogue = load()
        catalogue, changed, shortfalls = apply_stock_deltas(catalogue, deltas)
        if not changed:
            return False
        save(catalogue)
        for sku, level in shortfalls:
            logger.warning(
                "Stock for %r is now %d after %s. The receipt was still issued -- "
                "recount if that looks wrong.", sku, level, receipt_no)
        return True
    except Exception as exc:  # noqa: BLE001 - stock must never fail a receipt
        logger.warning("Could not update stock for %s: %s", receipt_no, exc)
        return False


# ------------------- storage -------------------
def default_catalogue():
    return json.loads(json.dumps(DEFAULT_CATALOGUE))


def load(path=None):
    """Load products.json, validated. Missing file means an empty catalogue."""
    path = path or catalogue_path()
    if not os.path.exists(path):
        return default_catalogue()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise config.ConfigError(f"could not be read: {exc}", path) from exc
    if not isinstance(raw, dict):
        raise config.ConfigError("expected a JSON object at the top level", path)

    catalogue = default_catalogue()
    if isinstance(raw.get("products"), list):
        catalogue["products"] = raw["products"]
    validate(catalogue, path)
    return catalogue


def save(catalogue, path=None, known_mtime=None):
    """Validate and save. Same contract as the other config writers."""
    path = path or catalogue_path()
    validate(catalogue, path)
    config.atomic_write_json(path, catalogue, expected_mtime=known_mtime)
    return catalogue


def validate(catalogue, filename=None):
    """Reject a catalogue that could not be sold from. Returns it."""
    filename = filename or catalogue_path()
    products = catalogue.get("products")
    if not isinstance(products, list):
        raise config.ConfigError("must be a list", filename, "products")

    seen_skus, seen_barcodes = {}, {}

    def check_entry(entry, where, is_variant):
        if not isinstance(entry, dict):
            raise config.ConfigError("must be an object", filename, where)
        if not is_variant and not str(entry.get("name", "")).strip() \
                and not str(entry.get("sku", "")).strip():
            raise config.ConfigError(
                "needs at least a name or a SKU to be findable",
                filename, where)

        sku = str(entry.get("sku", "")).strip()
        if sku:
            if sku.lower() in seen_skus:
                raise config.ConfigError(
                    f"duplicate SKU {sku!r} (already used by {seen_skus[sku.lower()]}). "
                    f"Scanning or typing it would be ambiguous.",
                    filename, f"{where}.sku")
            seen_skus[sku.lower()] = where

        barcode = str(entry.get("barcode", "")).strip()
        if barcode:
            if barcode.lower() in seen_barcodes:
                raise config.ConfigError(
                    f"duplicate barcode {barcode!r} (already used by "
                    f"{seen_barcodes[barcode.lower()]}). A scan must identify one product.",
                    filename, f"{where}.barcode")
            seen_barcodes[barcode.lower()] = where

        for key in MONEY_FIELDS:
            value = entry.get(key, "")
            if value in ("", None):
                continue
            try:
                if Decimal(str(value)) < 0:
                    raise config.ConfigError(
                        "must not be negative", filename, f"{where}.{key}")
            except InvalidOperation:
                raise config.ConfigError(
                    f"must be a number (got {value!r})", filename, f"{where}.{key}") from None

        stock = entry.get("stock_count", 0)
        if stock not in ("", None):
            # Negative is permitted on purpose. It is a real state -- oversold,
            # or simply never counted accurately -- and refusing to store it
            # would mean a sale that took stock below zero could not be recorded
            # at all, leaving the figure wrong in the *optimistic* direction.
            # Better to show -4 and prompt a recount than to show 3 and be wrong.
            if isinstance(stock, bool) or not isinstance(stock, int):
                raise config.ConfigError(
                    "must be a whole number of units", filename, f"{where}.stock_count")

        serials = entry.get("serial_numbers", [])
        if serials and (not isinstance(serials, list)
                        or not all(isinstance(s, str) for s in serials)):
            raise config.ConfigError(
                "must be a list of serial numbers", filename, f"{where}.serial_numbers")

    for index, product in enumerate(products):
        where = f"products[{index}]"
        check_entry(product, where, is_variant=False)
        variants = product.get("variants", []) if isinstance(product, dict) else []
        if variants and not isinstance(variants, list):
            raise config.ConfigError("must be a list", filename, f"{where}.variants")
        for variant_index, variant in enumerate(variants or []):
            check_entry(variant, f"{where}.variants[{variant_index}]", is_variant=True)

    return catalogue
