"""What the customer pays *with*, and what that costs.

TODO.md section 6.10. Bank transfer is free; cash on delivery carries a 4%
government levy; a card carries the processor's handling fee. So the method has
to be recorded on the receipt and its charge applied.

**A tax and a processing fee are not the same thing.** The arithmetic is
identical -- a percentage of a total -- which is exactly why they are tempting
to merge, and exactly why they must not be. The COD levy is tax a government
imposes and a shop has to account for and remit; a card handling fee is a
private company's charge for a service and is not tax at all. Lumping them
together overstates the tax collected on every card sale, which is a filing
problem rather than a cosmetic one. `KIND_TAX` and `KIND_FEE` keep them apart
all the way to the receipt, where they print under different headings and total
separately.

**A method may charge a percentage and a fixed amount together.** Card
processors almost always do ("2.9% + 0.30"), and forcing that into two separate
methods would make the receipt list one payment twice.

**Per order, not per shipment.** You pay for an order once: a bank transfer and
a card payment are single transactions. Cash on delivery is arguably collected
per delivery, but modelling that would mean a payment method on every shipment
to serve one case, so it hangs off the receipt.
"""
from decimal import Decimal

from money import quantize, to_decimal

#: Where the chosen method's label sits on the receipt data.
METHOD_KEY = "payment_method"

#: What a charge *is*, which decides how it is reported -- never merge these.
KIND_TAX = "tax"
KIND_FEE = "fee"
KINDS = (KIND_TAX, KIND_FEE)

#: How a charge is calculated. Both may be present on one method.
CHARGE_PERCENT = "percent"
CHARGE_FIXED = "fixed"


class PaymentMethodError(ValueError):
    """A payment method that could not be charged or explained."""


def defined(settings):
    """The configured methods, as a list of dicts. Never None."""
    methods = (settings or {}).get("payment", {}).get("methods")
    return [m for m in (methods or []) if isinstance(m, dict)]


def labels(settings):
    """Method names, for a dropdown."""
    return [str(m.get("label", "")).strip() for m in defined(settings)
            if str(m.get("label", "")).strip()]


def find(settings, label):
    """The method with this label, or None."""
    wanted = str(label or "").strip().casefold()
    if not wanted:
        return None
    for method in defined(settings):
        if str(method.get("label", "")).strip().casefold() == wanted:
            return method
    return None


def charge(method, base, decimals=None):
    """What this method adds to `base`. Returns Decimal, never negative.

    The percentage and the fixed part are rounded separately and then added,
    matching how every other figure on the receipt is built up -- so the charge
    a customer sees is the charge that was added to the total.
    """
    if not isinstance(method, dict):
        return Decimal("0")
    percent = to_decimal(method.get("percent", 0))
    fixed = to_decimal(method.get("fixed", 0))
    from_percent = quantize(to_decimal(base) * percent / Decimal(100), decimals)
    return from_percent + quantize(fixed, decimals)


def kind_of(method):
    """Whether this method's charge is tax or a service fee."""
    kind = str((method or {}).get("kind", KIND_FEE)).strip().casefold()
    return kind if kind in KINDS else KIND_FEE


def describe(method):
    """How the charge is worked out, for the receipt line: "4%", "2.9% + 0.30"."""
    if not isinstance(method, dict):
        return ""
    parts = []
    percent = to_decimal(method.get("percent", 0))
    fixed = to_decimal(method.get("fixed", 0))
    if percent:
        parts.append(f"{percent.normalize():f}%")
    if fixed:
        parts.append(f"{fixed.normalize():f}")
    return " + ".join(parts)


def validate(settings, filename="appsettings.json"):
    """Raise PaymentMethodError on a method that could not be charged."""
    seen = set()
    for index, method in enumerate((settings or {}).get("payment", {}).get("methods") or []):
        where = f"payment.methods[{index}]"
        if not isinstance(method, dict):
            raise PaymentMethodError(f"{filename} -> {where}: must be an object")

        label = str(method.get("label", "")).strip()
        if not label:
            raise PaymentMethodError(
                f"{filename} -> {where}.label: must have a label -- it is what "
                f"the receipt prints and what the user picks")
        if label.casefold() in seen:
            raise PaymentMethodError(
                f"{filename} -> {where}.label: duplicate method {label!r}; two "
                f"methods with one name cannot be told apart")
        seen.add(label.casefold())

        kind = str(method.get("kind", KIND_FEE)).strip().casefold()
        if kind not in KINDS:
            raise PaymentMethodError(
                f"{filename} -> {where}.kind: must be {' or '.join(KINDS)} "
                f"(got {method.get('kind')!r}). A government levy is 'tax' and a "
                f"processor's charge is 'fee' -- they are reported differently, "
                f"so a fee recorded as tax overstates the tax collected")

        for key in ("percent", "fixed"):
            raw = method.get(key, 0)
            if isinstance(raw, bool):
                raise PaymentMethodError(
                    f"{filename} -> {where}.{key}: must be a number")
            value = to_decimal(raw)
            if str(raw).strip() not in ("", "0") and not value and raw not in (0, None):
                raise PaymentMethodError(
                    f"{filename} -> {where}.{key}: must be a number (got {raw!r})")
            if value < 0:
                raise PaymentMethodError(
                    f"{filename} -> {where}.{key}: cannot be negative. A discount "
                    f"for paying a particular way belongs on a line, not here")
        if to_decimal(method.get("percent", 0)) > 100:
            raise PaymentMethodError(
                f"{filename} -> {where}.percent: {method.get('percent')!r} is "
                f"more than the whole payment -- check the figure")
    return settings


def row(settings, label, base, decimals=None):
    """The receipt row for the chosen method: (label, kind, amount).

    Returns None when no method is chosen or the method adds nothing, so a
    free-to-use method never prints a zero row.
    """
    method = find(settings, label)
    if method is None:
        return None
    amount = charge(method, base, decimals)
    if not amount:
        return None
    how = describe(method)
    name = str(method.get("label", "")).strip()
    return (f"{name} ({how})" if how else name, kind_of(method), amount)
