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
# to_decimal and quantize live in money.py, below everything, so the rounding
# here is the same rounding the renderer and the totals use. This module kept
# its own copy of to_decimal until money.py was extracted; two implementations
# of "read a number the user typed" is one too many on a document about money.
from money import to_decimal, quantize          # noqa: E402,F401


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


def held_serials(catalogue, sku):
    """The serial numbers this SKU has in stock, in the order they were entered.

    Matches a variant's own SKU as well as a product's, because a variant is
    what is actually sold when one exists.
    """
    wanted = str(sku or "").strip().lower()
    if not wanted:
        return []
    for entry in sellable_items(catalogue):
        if str(entry.get("sku", "") or "").strip().lower() == wanted:
            return [str(serial) for serial in entry.get("serial_numbers") or []
                    if str(serial).strip()]
    return []


def serials_by_sku(items):
    """{sku: [serial, ...]} actually named on a receipt's lines.

    Reads the per-unit records (line_units), which is where a serial lives once
    a line can sell more than one of something. A blank unit contributes
    nothing: a line where only two of three serials were captured should return
    the two it has, not an empty string standing in for the third.
    """
    found = {}
    for item in items or []:
        sku = str(item.get("sku", "") or "").strip().lower()
        if not sku:
            continue
        for unit in item.get("units") or []:
            if not isinstance(unit, dict):
                continue
            serial = str(unit.get("serial", "") or "").strip()
            if serial:
                found.setdefault(sku, []).append(serial)
    return found


def serial_deltas(new_items, previous_items=None):
    """(to_remove, to_restore) serials per SKU for this generation.

    The same reissue subtlety as `stock_deltas`: correcting a receipt writes it
    again, so a serial that was on it before and still is must not be removed
    twice, and one that has been taken off the receipt has not been sold and
    belongs back on the shelf.
    """
    new = serials_by_sku(new_items)
    old = serials_by_sku(previous_items) if previous_items else {}

    remove, restore = {}, {}
    for sku in set(new) | set(old):
        before = old.get(sku, [])
        after = new.get(sku, [])
        gone = [s for s in after if s not in before]
        back = [s for s in before if s not in after]
        if gone:
            remove[sku] = gone
        if back:
            restore[sku] = back
    return remove, restore


def apply_serial_deltas(catalogue, remove, restore):
    """Take sold serials off the shelf and put un-sold ones back.

    A serial that is not in the held list is left alone rather than reported:
    somebody typing a serial the catalogue never knew about is ordinary -- the
    unit may predate the catalogue -- and it is not a reason to interrupt a sale.
    """
    if not remove and not restore:
        return catalogue, False

    changed = False

    def adjust(entry):
        nonlocal changed
        sku = str(entry.get("sku", "") or "").strip().lower()
        held = entry.get("serial_numbers")
        if not isinstance(held, list):
            if sku not in remove and sku not in restore:
                return
            held = []

        updated = [str(s) for s in held]
        for serial in remove.get(sku, []):
            if serial in updated:
                updated.remove(serial)
                changed = True
        for serial in restore.get(sku, []):
            if serial not in updated:
                updated.append(serial)
                changed = True
        if updated != held:
            entry["serial_numbers"] = updated

    for product in catalogue.get("products", []):
        if not isinstance(product, dict):
            continue
        adjust(product)
        for variant in product.get("variants") or []:
            if isinstance(variant, dict):
                adjust(variant)
    return catalogue, changed


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


def apply_stock_deltas(catalogue, deltas, threshold=0):
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
        # Negative means more was sold than the catalogue thought was held, and
        # is always worth saying. At or below the threshold is not a shortfall
        # but is worth saying while there is still time to reorder -- and with
        # the default threshold of 0 that covers running out, which is the one
        # every shop wants to hear about.
        if updated < 0 or updated <= threshold:
            shortfalls.append((entry.get("sku", ""), updated))

    for product in catalogue.get("products", []):
        if not isinstance(product, dict):
            continue
        adjust(product)
        for variant in product.get("variants") or []:
            if isinstance(variant, dict):
                adjust(variant)

    return catalogue, changed, shortfalls


def record_sale(receipt_no, items, previous_items=None, settings=None,
                warnings=None):
    """Deduct a receipt's lines from stock. Never raises.

    Called *after* the receipt exists, so a failed render deducts nothing. Does
    nothing at all unless inventory.track_stock is on.

    `warnings` is an optional list to append low-stock messages to, so they can
    reach the person who just made the sale instead of only the log. It is a
    list rather than a return value because this function must stay
    unfailable -- every caller ignores its result already, and adding a second
    thing to check would be a new way to get it wrong.
    """
    settings = settings if settings is not None else config.load_app_settings()
    if not settings.get("inventory", {}).get("track_stock", False):
        return False

    try:
        deltas = stock_deltas(items, previous_items)
        remove, restore = serial_deltas(items, previous_items)
        if not deltas and not remove and not restore:
            return False
        catalogue = load()
        threshold = settings.get("inventory", {}).get("low_stock_threshold", 0)
        catalogue, changed, shortfalls = apply_stock_deltas(
            catalogue, deltas, threshold)
        # Serials follow the count. Selling one unit should take *that* serial
        # off the shelf, not merely decrement a number -- otherwise the held
        # list drifts away from the count and stops being worth offering.
        catalogue, serials_changed = apply_serial_deltas(catalogue, remove, restore)
        changed = changed or serials_changed
        if not changed:
            return False
        save(catalogue)
        for sku, level in shortfalls:
            logger.warning(
                "Stock for %r is now %d after %s. The receipt was still issued -- "
                "recount if that looks wrong.", sku, level, receipt_no)
            if warnings is not None:
                if level < 0:
                    warnings.append(
                        f"{sku}: stock is now {level}. More were sold than the "
                        f"catalogue thought were held -- worth a recount.")
                elif level == 0:
                    warnings.append(f"{sku}: that was the last one in stock.")
                else:
                    warnings.append(f"{sku}: only {level} left in stock.")
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
