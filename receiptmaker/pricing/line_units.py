"""Per-unit values on a line item -- one serial number for each thing sold.

TODO.md sections 6.1 and 6.2. A line of quantity 3 is three physical units, and
each one carries its own identifiers: the manufacturer's serial number, and
optionally an ID the shop assigns itself. SKU and barcode are *not* here -- they
name the product, so every unit of it shares them.

**Units are records, not parallel lists.** A line holds

    item["units"] = [{"serial": "SN1", "unit_id": "A1"},
                     {"serial": "SN2", "unit_id": "A2"}]

rather than a list of serials beside a list of IDs. The two look equivalent
until someone removes the middle serial: with parallel lists every ID below it
then silently belongs to the wrong unit, and nothing detects it because both
lists are still perfectly valid. With records there is no alignment to lose.

The other rule here is that the list length **is** the quantity. Everything that
reads units normalises first, so a hand-edited file, an older receipt reloaded
from history, or a quantity changed after the serials were typed all end up with
exactly as many units as there are things being sold.
"""

#: The line-item field flag that marks a field as holding one value per unit.
PER_UNIT_FLAG = "per_unit"

#: Where the records live on an item. Reserved so no custom field can shadow it.
UNITS_KEY = "units"

#: A quantity nobody typed. Guards against a blank or junk qty asking for a
#: negative number of rows, and against a typo'd 99999 building a huge dialog.
MAX_UNITS = 999


def quantity_of(item):
    """How many units this line sells, as a whole number at least zero.

    Anything unparseable reads as 1 rather than 0: a line with a broken quantity
    is still a line someone is selling something on, and offering no serial box
    at all would hide the problem instead of showing it.
    """
    raw = item.get("qty", 1)
    try:
        qty = int(str(raw).strip() or 1)
    except (TypeError, ValueError):
        return 1
    return max(0, min(qty, MAX_UNITS))


def per_unit_keys(fields, enabled_only=True):
    """The field keys that carry one value per unit, in configured order."""
    keys = []
    for field in (fields or {}).get("line_item_fields", []):
        if not isinstance(field, dict) or not field.get(PER_UNIT_FLAG):
            continue
        if enabled_only and not field.get("enabled", True):
            continue
        keys.append(field["key"])
    return keys


def normalise(item, keys, qty=None):
    """The line's units as exactly `qty` records, each holding every key.

    Padded when the quantity went up, trimmed when it went down, and filled in
    when a key was added after the line was written. Never mutates `item` --
    rendering must not rewrite the data it is handed.
    """
    if qty is None:
        qty = quantity_of(item)
    stored = item.get(UNITS_KEY) or []
    if not isinstance(stored, list):
        stored = []

    units = []
    for index in range(qty):
        source = stored[index] if index < len(stored) and isinstance(stored[index], dict) else {}
        units.append({key: str(source.get(key, "") or "") for key in keys})
    return units


def values_for(item, key, qty=None):
    """One field's per-unit values, in unit order, as strings."""
    return [unit.get(key, "") for unit in normalise(item, [key], qty)]


def is_blank(units, keys=None):
    """True when no unit carries any value -- the line was left unidentified."""
    for unit in units:
        for key, value in unit.items():
            if (keys is None or key in keys) and str(value).strip():
                return False
    return True


def missing_count(units, key):
    """How many units are missing a value for `key`.

    Used for the warning rather than a refusal. Demanding every serial before a
    line can be saved would be resented at a till, and the same argument settled
    overselling the same way (see TODO.md section 2): record what happened and
    say so, rather than blocking the sale.
    """
    return sum(1 for unit in units if not str(unit.get(key, "")).strip())


def describe_gaps(units, fields):
    """A sentence naming what is unfilled, or "" when nothing is.

    Reads like something a person would say, because it is shown to one:
    "2 of 3 units have no Serial Number."
    """
    if not units:
        return ""
    parts = []
    for field in (fields or {}).get("line_item_fields", []):
        if not isinstance(field, dict) or not field.get(PER_UNIT_FLAG):
            continue
        if not field.get("enabled", True):
            continue
        missing = missing_count(units, field["key"])
        if missing:
            label = field.get("label", field["key"])
            parts.append(f"{missing} of {len(units)} "
                         f"{'units have' if missing > 1 else 'unit has'} no {label}")
    return "; ".join(parts)


def set_units(item, units):
    """Store units on an item, dropping the key entirely when there are none.

    An empty list would otherwise show up in the history file and the receipt
    data as noise on every line of every receipt that does not use the feature.
    """
    if units and not is_blank(units):
        item[UNITS_KEY] = [dict(unit) for unit in units]
    else:
        item.pop(UNITS_KEY, None)
    return item


def to_text(values, separator="\n"):
    """Join a unit's values for display, skipping the blanks."""
    return separator.join(v for v in (str(x or "").strip() for x in values) if v)
