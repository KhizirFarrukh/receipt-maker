"""What a line's discount and tax actually come to.

A discount of 1000 on a line of five is ambiguous, and the ambiguity is worth
money: it is either 1000 off the line, or 1000 off each of the five. Both are
things shops do, so it is a setting rather than a guess.

    qty 5, price 10,000, discount 1,000

        scope "line"  ->  1,000 off      (total 49,000)
        scope "unit"  ->  5,000 off      (total 45,000)

**`line` is the default because it is what this app already did.** Reissuing an
old receipt has to reproduce the figures the customer was given, so the setting
starts where the arithmetic already was and a shop opts into the other reading.

**A percentage is never ambiguous**, so it ignores the scope entirely. "10%" is
ten percent of what the line comes to, whichever way the setting is pointing --
per-unit and whole-line give the identical answer for a percentage, and
pretending otherwise would just be a way to get it wrong. Type a trailing `%` in
a discount or tax box and it is read that way.

Rounding follows the rule the rest of the money code uses: round the part, then
multiply or add. Rounding at the end instead would let a line's own figures
disagree with the totals underneath them by a penny.
"""
from decimal import Decimal

from money import quantize, to_decimal

#: How a plain amount on a line is read.
PER_LINE = "line"
PER_UNIT = "unit"
SCOPES = (PER_LINE, PER_UNIT)

#: What a value turned out to be.
AMOUNT = "amount"
PERCENT = "percent"

#: The config keys, so the lookup here and the settings file cannot drift.
SCOPE_KEYS = {"discount": "discount_scope", "tax": "tax_scope"}


def parse(value):
    """(kind, number) for a discount or tax entry.

    A trailing `%` makes it a percentage; anything else is an amount. Junk
    reads as zero rather than raising, like every other money value here -- a
    receipt has to render.
    """
    text = str(value if value is not None else "").strip()
    if text.endswith("%"):
        return PERCENT, to_decimal(text[:-1].strip())
    return AMOUNT, to_decimal(text)


def quantity_of(item):
    """The line's quantity as a whole number, at least zero."""
    try:
        return max(0, int(str(item.get("qty", 0) or 0).strip() or 0))
    except (TypeError, ValueError):
        return 0


def resolve(value, gross, quantity, scope=PER_LINE, decimals=None):
    """What `value` comes to for the whole line.

    `gross` is the line before adjustments (qty x price), which is what a
    percentage is a percentage *of*.
    """
    kind, number = parse(value)
    if kind == PERCENT:
        # Whole-line by definition: ten percent of five units at 10,000 is the
        # same number whether you take it per unit or all at once.
        return quantize(to_decimal(gross) * number / Decimal(100), decimals)
    if scope == PER_UNIT:
        # Round the per-unit figure before multiplying, so the line shows a
        # whole number of pennies per unit rather than a rounded total.
        return quantize(number, decimals) * quantity
    return quantize(number, decimals)


def scope_for(what, scopes=None):
    """The configured scope for "discount" or "tax", falling back to `line`.

    Accepts the settings shape (`{"discount_scope": "unit"}`) and the bare name
    (`{"discount": "unit"}`), because callers reach for both and a silent
    fall-back to the default is the worst possible way to get this wrong -- it
    looks like the setting is being ignored, which it would be.
    """
    scopes = scopes or {}
    key = SCOPE_KEYS.get(what, what)
    value = scopes.get(key, scopes.get(what, PER_LINE))
    value = str(value or "").strip()
    return value if value in SCOPES else PER_LINE


def discount_of(item, gross=None, scopes=None, decimals=None):
    """The discount this line actually gives."""
    if gross is None:
        gross = to_decimal(item.get("qty", 0)) * to_decimal(item.get("price", 0))
    return resolve(item.get("discount", 0), gross, quantity_of(item),
                   scope_for("discount", scopes), decimals)


def tax_of(item, gross=None, scopes=None, decimals=None):
    """The tax this line actually adds."""
    if gross is None:
        gross = to_decimal(item.get("qty", 0)) * to_decimal(item.get("price", 0))
    return resolve(item.get("tax", 0), gross, quantity_of(item),
                   scope_for("tax", scopes), decimals)


def describe(value, scope=PER_LINE):
    """How a value will be read, for a form's hint: "1,000 per item"."""
    kind, number = parse(value)
    if not number:
        return ""
    if kind == PERCENT:
        return f"{number.normalize():f}% of the line"
    return f"{number.normalize():f} " + ("per item" if scope == PER_UNIT
                                         else "off the line")
