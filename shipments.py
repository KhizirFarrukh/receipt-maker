"""Shipping charged per group of lines rather than once per invoice.

TODO.md section 6.9. An order that leaves from two warehouses has two carrier
costs, and charging one fee against the whole invoice cannot express that. So a
line belongs to a **shipment**, each shipment carries its own fee, and the
receipt shows every fee and their combined total.

**A shipment is a tag on the line, not a range of rows.** The case that shaped
this was lines 1, 2 and 4 against line 3 -- the groups interleave, so they
cannot be modelled as contiguous sections of the items table.

**The sort is stable.** Lines are reordered so a shipment's items sit together,
and determinism is a tested invariant: the same receipt data must render
identically every time, or the golden gate fails in a way that looks like
flakiness rather than a bug. Sorting by group while preserving entry order
within a group is what makes that true.

**Groups are numbered, not named.** The reason for grouping is which warehouse
dispatched what, which is internal. But an unlabelled regrouping tells the
customer nothing -- they would see a re-sorted list and two unexplained shipping
charges -- so each group prints a neutral "Shipment 1 of 2". The label field
exists in the data model, unset, so printing a real name later is a template
change rather than a migration.
"""
from decimal import Decimal

from money import quantize, to_decimal

#: The line's group tag, and where the fee table lives on the receipt.
GROUP_KEY = "shipment"
SHIPMENTS_KEY = "shipments"

#: More shipments than this on one receipt is a data problem, not an order.
MAX_SHIPMENTS = 50


class ShipmentError(ValueError):
    """A shipment arrangement that cannot be charged honestly."""


def group_of(item):
    """The shipment tag on a line, as text. "" means "not grouped"."""
    return str((item or {}).get(GROUP_KEY, "") or "").strip()


def groups_used(items):
    """Every shipment tag present, in the order lines first mention them.

    First-mention order, not sorted order, so the numbering a customer sees
    follows the order the items were entered rather than how the tags happen to
    sort as strings ("10" before "2").
    """
    seen = []
    for item in items or []:
        tag = group_of(item)
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def order_items(items):
    """Items reordered so each shipment's lines sit together.

    Stable: within a group, entry order is preserved, and ungrouped lines keep
    their positions relative to each other and come last. Without stability the
    same receipt could render two ways -- see the module docstring.
    """
    order = {tag: index for index, tag in enumerate(groups_used(items))}
    # Ungrouped lines sort after every group rather than being scattered.
    return sorted(items or [],
                  key=lambda item: order.get(group_of(item), len(order)))


def fee_table(data):
    """{tag: Decimal fee} from the receipt's shipment list."""
    table = {}
    for entry in (data or {}).get(SHIPMENTS_KEY) or []:
        if not isinstance(entry, dict):
            continue
        tag = str(entry.get("id", "") or "").strip()
        if tag:
            table[tag] = to_decimal(entry.get("fee", 0))
    return table


def validate(data, items=None):
    """Raise ShipmentError on an arrangement the receipt could not explain."""
    items = data.get("items") if items is None else items
    used = groups_used(items)
    if len(used) > MAX_SHIPMENTS:
        raise ShipmentError(
            f"{len(used)} shipments on one receipt is more than any order has "
            f"(the limit is {MAX_SHIPMENTS}) -- check the shipment tags")

    table = fee_table(data)
    for tag, fee in table.items():
        if fee < 0:
            raise ShipmentError(
                f"shipment {tag!r} has a negative fee. A refund belongs on a "
                f"line, not on the shipping")

    # A fee for a shipment no line belongs to would be charged to nobody, and
    # would make the shipping total disagree with the lines above it.
    orphans = [tag for tag in table if tag not in used]
    if orphans:
        raise ShipmentError(
            f"a shipping fee is set for {', '.join(sorted(orphans))}, but no "
            f"line is in that shipment. Assign a line to it or remove the fee")
    return data


#: The implicit shipment that untagged lines belong to. Not a real tag -- it
#: never appears on a line -- but the rest of the order still ships somehow.
UNGROUPED = ""


def rows(data, items=None, decimals=None, flat_shipping=0):
    """Per-shipment shipping rows and their total. Returns (rows, total).

    `rows` is [(tag, position, count, fee)] in the order the groups appear, so a
    caller can label them "Shipment 1 of 2" without knowing anything about how
    they are stored.

    With no groups this returns the single flat fee the receipt has always had,
    so an existing receipt is unchanged.

    **A partly-tagged order keeps its flat fee.** Tagging some lines and not
    others is the ordinary way to arrive at this: one item comes from the other
    warehouse and the rest ship as usual. Dropping the flat fee there would
    undercharge the customer with nothing on the receipt to show for it, which
    is the worst way for this to be wrong.
    """
    items = data.get("items") if items is None else items
    used = groups_used(items)
    flat = quantize(flat_shipping, decimals)
    if not used:
        return [], flat

    table = fee_table(data)
    ungrouped = [item for item in items or [] if not group_of(item)]

    entries = [(tag, quantize(table.get(tag, 0), decimals)) for tag in used]
    if ungrouped and flat:
        entries.append((UNGROUPED, flat))

    out = []
    total = Decimal("0")
    for position, (tag, fee) in enumerate(entries, start=1):
        out.append((tag, position, len(entries), fee))
        total += fee
    return out, total


def marker(position, count, template="Shipment {n} of {total}"):
    """The neutral label a line prints, e.g. "Shipment 1 of 2".

    Neutral on purpose: the warehouse name is internal, but a regrouping with no
    marker at all tells the customer nothing about why their items were
    reordered or why there are two shipping charges.
    """
    if count <= 1:
        return ""
    return template.format(n=position, total=count)


def markers_for(items):
    """{tag: "Shipment 1 of 2"} for every group on the receipt."""
    used = groups_used(items)
    return {tag: marker(position, len(used))
            for position, tag in enumerate(used, start=1)}
