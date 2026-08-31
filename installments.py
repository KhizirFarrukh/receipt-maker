"""Installment plans: pay a deposit now and the rest monthly.

TODO.md section 6.5. A plan is three numbers -- a **period** in months, a **down
payment** made now, and a **monthly amount** -- which together come to more than
the cash price, because financing is not free.

Two rules shape everything here.

**A receipt carries one plan, or one plan per line, never both.** Different lines
may legitimately be financed differently (3 months on one, 6 on another), but a
whole-order plan and per-line plans on the same receipt produce a total nobody
can reconstruct. `scope_of` refuses that combination rather than picking one.

**The cash price stays the receipt total.** The plan is shown beside it, not
instead of it. This was the open question in TODO 6.5 and it was settled this
way because the tax rows apply to what was sold: the goods have one price, and
financing them is a separate arrangement on top. Making the financed figure the
total would silently push tax onto the finance charge. The financed total is
disclosed in full -- a customer must be able to see what the plan costs -- but
it is labelled as what it is rather than presented as the price of the goods.
"""
from decimal import Decimal

from money import quantize, to_decimal

#: Where a plan hangs, on the receipt data or on one line item.
PLAN_KEY = "installment"

#: A plan longer than this is a data-entry slip rather than an offer.
MAX_MONTHS = 600


class InstallmentError(ValueError):
    """A plan that cannot be shown honestly on a receipt."""


def normalise(plan):
    """Coerce a stored plan to {months, down, monthly}, or None if there is none.

    Returns None rather than raising for an absent or empty plan: no plan is the
    normal case, and every caller would otherwise have to guard first.
    """
    if not isinstance(plan, dict):
        return None
    months = plan.get("months", 0)
    try:
        months = int(str(months).strip() or 0)
    except (TypeError, ValueError):
        months = 0
    down = to_decimal(plan.get("down", 0))
    monthly = to_decimal(plan.get("monthly", 0))
    if months <= 0 and not down and not monthly:
        return None
    return {"months": months, "down": down, "monthly": monthly}


def validate(plan, where="installment"):
    """Raise InstallmentError on a plan a customer could not check. Returns it."""
    plan = normalise(plan)
    if plan is None:
        return None
    if plan["months"] <= 0:
        raise InstallmentError(
            f"{where}: a plan needs a period of at least one month")
    if plan["months"] > MAX_MONTHS:
        raise InstallmentError(
            f"{where}: {plan['months']} months is longer than any plan should be "
            f"(the limit is {MAX_MONTHS}) -- check the figure")
    if plan["down"] < 0 or plan["monthly"] < 0:
        raise InstallmentError(
            f"{where}: a down payment and a monthly amount cannot be negative")
    if not plan["monthly"] and not plan["down"]:
        raise InstallmentError(
            f"{where}: a plan that collects nothing is not a plan")
    return plan


def financed_total(plan, decimals=None):
    """What the customer pays in total under the plan: down + months x monthly.

    Rounded the same way as every other figure on the receipt -- each component
    rounded, then added -- so the plan's arithmetic visibly adds up on the page.
    """
    plan = normalise(plan)
    if plan is None:
        return Decimal("0")
    return (quantize(plan["down"], decimals)
            + quantize(plan["monthly"], decimals) * plan["months"])


def surcharge(plan, cash_total, decimals=None):
    """What the plan costs over paying cash. Negative if the plan is cheaper."""
    return quantize(financed_total(plan, decimals), decimals) - quantize(cash_total, decimals)


def plan_of(obj):
    """The plan on a receipt or a line item, normalised, or None."""
    return normalise((obj or {}).get(PLAN_KEY))


def scope_of(data, items=None):
    """Where this receipt's plans live: "order", "line", or "" for none.

    Raises InstallmentError when both are present. Hiding one in the UI is not
    enough -- a receipt loaded from history, or a file edited by hand, can carry
    both, and silently ignoring one would print a total that cannot be checked.
    """
    items = data.get("items") if items is None else items
    order = plan_of(data)
    lines = [i for i in (items or []) if plan_of(i)]
    if order and lines:
        raise InstallmentError(
            "this receipt has both a whole-order installment plan and plans on "
            f"{len(lines)} line(s). It can have one or the other, because two "
            "sets of plans give a total nobody can reconstruct. Remove one.")
    if order:
        return "order"
    return "line" if lines else ""


def collect(data, items=None, decimals=None):
    """Every plan on the receipt, summed. Returns (scope, rows, totals).

    `rows` is one (label_key, plan) per plan, in line order, so a caller can
    show which line each belongs to. `totals` holds the combined down payment,
    combined monthly amount and combined financed total -- what the receipt
    prints under the cash total.
    """
    items = data.get("items") if items is None else items
    scope = scope_of(data, items)

    rows = []
    if scope == "order":
        rows.append((None, plan_of(data)))
    elif scope == "line":
        for index, item in enumerate(items or []):
            plan = plan_of(item)
            if plan:
                rows.append((index, plan))

    totals = {
        "down": sum((quantize(p["down"], decimals) for _, p in rows), Decimal("0")),
        "monthly": sum((quantize(p["monthly"], decimals) for _, p in rows), Decimal("0")),
        "financed": sum((financed_total(p, decimals) for _, p in rows), Decimal("0")),
        # The longest period, because that is when the customer finishes paying.
        # Summing the months would be meaningless: two 6-month plans running
        # side by side take six months, not twelve.
        "months": max((p["months"] for _, p in rows), default=0),
    }
    return scope, rows, totals


def describe(plan, format_amount):
    """One line a customer can check: "5,000.00 down, then 6 x 3,000.00".

    Takes the formatter rather than the currency config so this module never
    has to know how money is written.
    """
    plan = normalise(plan)
    if plan is None:
        return ""
    parts = []
    if plan["down"]:
        parts.append(f"{format_amount(plan['down'])} down")
    if plan["monthly"]:
        monthly = f"{plan['months']} × {format_amount(plan['monthly'])}"
        parts.append(f"then {monthly}" if parts else monthly)
    return ", ".join(parts)
