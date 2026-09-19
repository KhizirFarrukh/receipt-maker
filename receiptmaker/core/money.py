"""Decimal money primitives, with no dependencies of their own.

These lived in receipt_render.py, which was fine while it was the only thing
doing arithmetic. It is not any more: installments.py and shipments.py both need
to round the same way, and importing them from the renderer put a cycle one
module away -- shipments.py hit it immediately, and installments.py was only
spared because the renderer happened to import it lazily.

So they live here, at the bottom of the import graph where anything can reach
them. receipt_render re-exports the names, because they are part of its public
surface and the tests, the CLI and the templates all reach them through it.

The governing rule (PLAN-generalization.md, principle 7) is that money is
Decimal end to end: each line is rounded to the display precision and the
rounded values are summed, so the printed figures visibly add up instead of
drifting by a cent against an unrounded total.
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

#: Fallback precision when no currency config is supplied.
AMOUNT_DECIMALS = 2


def to_decimal(value):
    """Coerce user/JSON input to Decimal without inheriting binary float noise.

    Never raises: a receipt has to render even when a field holds something
    unexpected, and a zero that shows up as a wrong total is easier to spot and
    fix than a crash halfway through generating a legal document.
    """
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip() or "0")
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def quantize(value, decimals=None):
    """Round to the display precision, half-up (what a till receipt does)."""
    places = AMOUNT_DECIMALS if decimals is None else decimals
    return to_decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
