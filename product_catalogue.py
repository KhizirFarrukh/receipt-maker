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
            if isinstance(stock, bool) or not isinstance(stock, int) or stock < 0:
                raise config.ConfigError(
                    "must be a whole number of units, zero or more",
                    filename, f"{where}.stock_count")

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
