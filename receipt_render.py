"""Receipt HTML construction from Templates/ (tkinter-free).

Stage 2 moved the receipt body out of one large f-string and into editable
template files, rendered by the deliberately-dumb template_engine. This module
is the "renderer precomputes" half of governing principle 1: templates get
booleans and finished strings, never logic.

Layout of the render:

    base.html            the document; pulls in styles.css and the blocks below
    receipt_info.html    title, type badge, receipt meta, bill-to box
    items_table.html     the line-item table shell
    item_header_cell.html / item_row_cell.html
                         one column each -- the renderer iterates ITEM_COLUMNS
                         and joins, so a column can appear or vanish without any
                         template edit (there are no loops in the engine)
    totals.html / totals_row.html
    terms.html           the warranty/returns page
    header.html / footer.html
                         Chromium page header/footer; a restricted context where
                         styles.css does NOT apply and CSS must be inline

Money is Decimal end to end (principle 7): each line is rounded to the display
precision and the rounded values are summed, so the printed figures visibly add
up rather than drifting by a cent against an unrounded total.

BLOCK_CONTEXTS is the single source of truth for which placeholders each block
may use. The linter checks templates against it at load time and the renderer
builds each block's context from the same map, so a typo is a startup error
naming the file and line -- never a silently blank field on a legal document.
"""
import base64
import html as html_utils
import logging
import mimetypes
import os
import re
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

import config
import line_units
import shipments
from money import AMOUNT_DECIMALS, to_decimal, quantize
import template_engine
from template_engine import TemplateError
from config import (
    RESOURCE_DIR,
    PDF_MARGIN_LEFT,
    PDF_MARGIN_RIGHT,
    branding_template_path,
    install_default_templates,
    read_html_file,
    load_app_settings,
)


logger = logging.getLogger("receipt_maker")


def escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def escape_address(address):
    lines = [line.strip() for line in str(address).splitlines() if line.strip()]
    if not lines:
        return ""
    return "<br>".join(escape(line) for line in lines)


def file_url_for_directory(path):
    normalized = os.path.abspath(path).replace("\\", "/").rstrip("/")
    return f"file:///{normalized}/"


def resolve_local_asset_path(src):
    clean_src = src.split("#", 1)[0].split("?", 1)[0].strip()
    if not clean_src:
        return ""

    if os.path.isabs(clean_src):
        return os.path.abspath(clean_src)

    # Read through the module so --config-dir re-rooting is picked up.
    for base_dir in (config.APP_DIR, RESOURCE_DIR):
        asset_path = os.path.abspath(os.path.join(base_dir, clean_src))
        try:
            if os.path.commonpath([base_dir, asset_path]) != base_dir:
                continue
        except ValueError:
            continue

        if os.path.isfile(asset_path):
            return asset_path
    return ""


def suggest_asset_alternatives(src, limit=3):
    """Files that look like what `src` was probably meant to name.

    Overwhelmingly the doubled-extension case: Windows hides known file
    extensions, so saving or renaming a file as "logo.png" produces
    "logo.png.png" while still *displaying* it as "logo.png". The user sees the
    right name in Explorer and the app sees a name that does not exist, which is
    unguessable without being told.
    """
    name = os.path.basename(str(src).strip())
    if not name:
        return []
    stem = os.path.splitext(name)[0]

    found = []
    for base_dir in (config.APP_DIR, RESOURCE_DIR):
        try:
            entries = sorted(os.listdir(base_dir))
        except OSError:
            continue
        for entry in entries:
            if entry == name or entry in found:
                continue
            entry_stem = os.path.splitext(entry)[0]
            if entry.lower().startswith(name.lower() + ".") or entry_stem in (name, stem):
                if os.path.isfile(os.path.join(base_dir, entry)):
                    found.append(entry)
    return found[:limit]


def asset_problem(src, label="logo"):
    """Explain why a configured image cannot be used, or '' when it is fine.

    A configured-but-unusable image used to be dropped in silence, which left no
    way to find out why a receipt came out without its logo.
    """
    clean = str(src or "").strip()
    if not clean or logo_source_available(clean):
        return ""

    message = (f"The {label} file was not found, so it will not appear on receipts.\n"
               f"Configured as: {clean}\n"
               f"Looked in: {config.APP_DIR}")
    alternatives = suggest_asset_alternatives(clean)
    if alternatives:
        message += ("\n\nA file with a very similar name is there: "
                    + ", ".join(alternatives)
                    + f".\nWindows hides known file extensions, so a file saved as "
                      f"\"{clean}\" can end up named \"{alternatives[0]}\". "
                      f"Rename it, or point logo_path at the name it actually has.")
    return message


def logo_source_available(src):
    clean_src = str(src).strip()
    if not clean_src:
        return False

    lowered = clean_src.lower()
    if lowered.startswith(("data:", "http://", "https://", "file:", "about:")):
        return True

    return os.path.isfile(resolve_local_asset_path(clean_src))


def remove_logo_image_tags(template_html):
    return re.sub(
        r"\s*<img\b[^>]*\bsrc\s*=\s*(['\"])\{\{company_logo(?:_path)?\}\}\1[^>]*>\s*",
        "\n",
        template_html,
        flags=re.IGNORECASE,
    )


def inline_local_images(html):
    def replace_src(match):
        quote = match.group(1)
        src = html_utils.unescape(match.group(2).strip())
        lowered = src.lower()
        if lowered.startswith(("data:", "http://", "https://", "file:", "about:")):
            return match.group(0)

        image_path = resolve_local_asset_path(src)
        if not os.path.isfile(image_path):
            return match.group(0)

        mime_type = mimetypes.guess_type(image_path)[0] or "application/octet-stream"
        with open(image_path, "rb") as image_file:
            encoded = base64.b64encode(image_file.read()).decode("ascii")
        return f'src={quote}data:{mime_type};base64,{encoded}{quote}'

    return re.sub(r"src=(['\"])([^'\"]+)\1", replace_src, html, flags=re.IGNORECASE)


_SAFE_URL_SCHEMES = ("http://", "https://", "mailto:")


def safe_url(value):
    """A URL fit to put in an href, or '' if it is not one.

    An href is a code context, not a text node -- escaping does not make
    `javascript:` safe there, so anything outside a known-harmless scheme is
    dropped rather than sanitised. Config is the user's own, but this is
    embedded in a PDF that goes to customers.
    """
    url = str(value or "").strip()
    return url if url.lower().startswith(_SAFE_URL_SCHEMES) else ""


def unlink_empty_anchors(template_html, placeholder):
    """Turn `<a href="{{x_url}}">Text</a>` into plain `Text`.

    A policy line should still read correctly when no URL is configured. Leaving
    the anchor with an empty href would print a link to nowhere; deleting it
    would silently drop the words.
    """
    pattern = re.compile(
        r"<a\b[^>]*\bhref\s*=\s*(['\"])\s*\{\{" + re.escape(placeholder) +
        r"\}\}\s*\1[^>]*>(.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL)
    return pattern.sub(lambda m: m.group(2), template_html)


def render_settings_template(template_html):
    settings = load_app_settings()
    company = settings["company"]
    logo_path = company["logo_path"]
    if not logo_source_available(logo_path):
        problem = asset_problem(logo_path)
        if problem:
            # Never fail quietly: without this the receipt just comes out with no
            # logo and no clue why.
            if settings.get("render", {}).get("fail_on_missing_image"):
                raise RuntimeError(problem)
            logger.warning("%s", problem.replace("\n", " "))
        template_html = remove_logo_image_tags(template_html)
        logo_path = ""

    # Policy links. An unset or unsafe URL drops the anchor but keeps its words,
    # so the footer never carries a link to nowhere.
    links = settings.get("links", {})
    link_values = {}
    for key in set(links) | set(config.DEFAULT_APP_SETTINGS.get("links", {})):
        url = safe_url(links.get(key, ""))
        link_values[key] = url
        if not url:
            template_html = unlink_empty_anchors(template_html, key)

    replacements = {f"{{{{{key}}}}}": escape(url) for key, url in link_values.items()}
    replacements.update({
        "{{company_name}}": escape(company["name"]),
        "{{company_address}}": escape_address(company["address"]),
        "{{company_phone}}": escape(company["phone"]),
        "{{company_email}}": escape(company["email"]),
        "{{company_logo}}": escape(logo_path),
        "{{company_logo_path}}": escape(logo_path),
    })

    for placeholder, value in replacements.items():
        template_html = template_html.replace(placeholder, value)
    return template_html


def default_header_html():
    return """
<div class="store-header">
    <img src="{{company_logo}}" alt="{{company_name}} Logo" class="store-logo-img" onerror="this.style.display='none';">
    <div class="store-name">{{company_name}}</div>
    <div class="store-details">
        {{company_address}}<br>
        {{company_phone}} | {{company_email}}
    </div>
</div>"""


def default_footer_html():
    """Fallback footer, used only if Templates/footer.html is missing."""
    return """
<div class="footer-text">
    Thank you for choosing {{company_name}}. Warranty claims require original receipt.
</div>
<div class="footer-terms">
    By purchasing from {{company_name}} you agree to our
    <a href="{{terms_url}}">Terms of Service</a>,
    <a href="{{privacy_url}}">Privacy Policy</a> and
    <a href="{{warranty_url}}">Warranty Policy</a>.
</div>"""


def build_page_header_template():
    # Resolved per render, so dropping a header.html beside the exe takes effect
    # without a restart.
    header_html = read_html_file(branding_template_path("header.html")).strip() or default_header_html()
    header_html = render_settings_template(header_html)
    header_html = inline_local_images(header_html)
    return f"""<style>
    html, body {{
        margin: 0;
        padding: 0;
        width: 100%;
        font-family: Arial, Helvetica, sans-serif;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
    }}
    .pdf-header {{
        box-sizing: border-box;
        width: 100%;
        padding: 12px {PDF_MARGIN_RIGHT} 0 {PDF_MARGIN_LEFT};
        color: #111827;
    }}
    .store-header {{
        text-align: center;
        border-bottom: 2px solid #1e293b;
        padding-bottom: 7px;
    }}
    .store-logo-img {{
        display: block;
        max-height: 52px;
        max-width: 190px;
        object-fit: contain;
        margin: 0 auto 3px auto;
    }}
    .store-name {{
        font-size: 18px;
        line-height: 1.1;
        font-weight: 700;
    }}
    .store-details {{
        margin-top: 3px;
        font-size: 8px;
        line-height: 1.35;
        color: #334155;
    }}
</style>
<div class="pdf-header">{header_html}</div>"""


def build_page_footer_template():
    footer_html = read_html_file(branding_template_path("footer.html")).strip() or default_footer_html()
    footer_html = render_settings_template(footer_html)
    footer_html = inline_local_images(footer_html)
    return f"""<style>
    html, body {{
        margin: 0;
        padding: 0;
        width: 100%;
        font-family: Arial, Helvetica, sans-serif;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
    }}
    .pdf-footer {{
        box-sizing: border-box;
        width: 100%;
        padding: 6px {PDF_MARGIN_RIGHT} 0 {PDF_MARGIN_LEFT};
        text-align: center;
        color: #475569;
        font-size: 8px;
        line-height: 1.35;
    }}
    .footer-inner {{
        border-top: 1px solid #e2e8f0;
        padding-top: 7px;
    }}
    .footer-text {{
        font-weight: 600;
    }}
    .footer-policy {{
        margin-top: 3px;
    }}
    .footer-terms {{
        margin-top: 2px;
        color: #64748b;
    }}
    .footer-terms a {{
        color: #334155;
        text-decoration: underline;
    }}
</style>
<div class="pdf-footer">
    <div class="footer-inner">{footer_html}</div>
</div>"""


# ------------------- amounts (Decimal end to end) -------------------
# Defined in money.py so installments.py and shipments.py can round the same way
# without importing the renderer -- doing that put a circular import one module
# away. Re-exported here because this is where the rest of the app reaches them.


def line_gross(item):
    """qty x price for one line, unrounded. The `amount` column's value."""
    return to_decimal(item.get("qty", 0)) * to_decimal(item.get("price", 0))


def line_total(item, decimals=None):
    """What one line actually came to: gross, plus its tax, less its discount.

    Each part is rounded *before* they are added, which is not fussiness -- the
    totals block sums the rounded gross, the rounded taxes and the rounded
    discounts as three separate running totals. Rounding the sum instead would
    let this column differ from the figures below it by a penny on some receipts
    and not others, which is exactly the kind of discrepancy a customer notices
    and nobody can explain.

    Shipping is deliberately absent: it is charged per shipment, across several
    lines at once, so charging it to a line would mean inventing a split.
    """
    return (quantize(line_gross(item), decimals)
            + quantize(item.get("tax", 0), decimals)
            - quantize(item.get("discount", 0), decimals))


def group_digits(digits, style="thousand"):
    """Insert digit-group separators into a run of integer digits.

    ``indian`` is the South-Asian lakh/crore convention: the last three digits
    form one group and everything above it is grouped in pairs (12,34,567),
    which is what a receipt issued in PKR or INR is expected to look like.
    """
    if style == "none" or len(digits) <= 3:
        return digits
    if style == "indian":
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            head, pair = head[:-2], head[-2:]
            parts.insert(0, pair)
        if head:
            parts.insert(0, head)
        return ",".join(parts + [tail])
    parts = []                                   # "thousand"
    while len(digits) > 3:
        digits, chunk = digits[:-3], digits[-3:]
        parts.insert(0, chunk)
    return ",".join([digits] + parts)


def format_amount(value, currency=None, group=True):
    """Render one amount per the currency config: '$12.00', 'Rs. 22,450.00', '($5.00)'.

    ``group`` lets the caller suppress digit grouping for line-item cells, which
    is how an install that predates configurable currency keeps printing
    "Rs. 17000.00" in the item table while its totals stay grouped.
    """
    currency = currency or {}
    decimals = currency.get("decimals", AMOUNT_DECIMALS)
    amount = quantize(value, decimals)
    negative = amount < 0

    plain = f"{abs(amount):.{decimals}f}"
    integer, _, fraction = plain.partition(".")
    number = group_digits(integer, currency.get("group_style", "thousand") if group else "none")
    if fraction:
        number = f"{number}.{fraction}"

    symbol = str(currency.get("symbol", ""))
    if symbol:
        gap = " " if currency.get("symbol_space", False) else ""
        if currency.get("position", "prefix") == "suffix":
            number = f"{number}{gap}{symbol}"
        else:
            number = f"{symbol}{gap}{number}"

    if not negative:
        return number
    if currency.get("negative_style", "minus") == "parentheses":
        return f"({number})"
    return f"-{number}"


# ------------------- line-item columns -------------------
# Columns come from fields.json, so adding, hiding or reordering one needs no
# code and no template edit. `type` decides presentation as well as validation:
# a dynamically generated column has to work out on its own whether to
# right-align and format as money, or left-align and wrap.
#
#   type -> (css class, how the cell value is produced)
TYPE_PRESENTATION = {
    "text":      ("",    "plain"),
    "multiline": ("",    "lines"),
    "select":    ("",    "plain"),
    "phone":     ("",    "plain"),
    "email":     ("",    "plain"),
    "date":      ("",    "plain"),
    "boolean":   ("",    "boolean"),
    "integer":   ("num", "plain"),
    "number":    ("num", "plain"),
    "amount":    ("num", "money"),
    "computed":  ("num", "money"),
}

#: Keys whose value the renderer computes rather than reads from the item.
#:
#: `amount` is the gross -- qty x price, nothing else. `line_total` is what the
#: line actually came to once its own discount and tax are applied. Both exist
#: because redefining `amount` would silently change the figure printed on every
#: receipt already being issued; a shop that wants only the net turns `amount`
#: off and `line_total` on. See TODO.md section 6.4.
DERIVED_KEYS = {"amount", "line_total"}

#: Fallback for "this line carries no warranty, print no note". The real value
#: comes from fields.json (`warranty.none_option`) so a shop that words it
#: differently -- or in another language -- does not get a stray note on every
#: line. Kept as a module constant only for callers that render without config.
NO_WARRANTY_LABEL = "No Warranty"

# ------------------- template blocks -------------------
#: The one source of truth for what each block may reference. The load-time lint
#: checks templates against this; the renderer builds contexts from it.
BLOCK_CONTEXTS = {
    "base.html": {"resource_base", "styles", "font_faces", "receipt_info",
                  "items_table", "totals", "terms"},
    "styles.css": {"keep_rows_whole"},
    "receipt_info.html": {"type_badge", "invoice_no", "date", "customer_name",
                          "customer_phone", "customer_email",
                          "custom_receipt_fields"},
    "field_row.html": {"label", "value", "value_class"},
    "items_table.html": {"header_cells", "rows"},
    "item_header_cell.html": {"label", "css_class"},
    "item_row_cell.html": {"value", "css_class", "note"},
    "totals.html": {"totals_rows", "total"},
    "totals_row.html": {"label", "amount"},
    "terms.html": set(),
}

_TEMPLATE_CACHE = {}


def clear_template_cache():
    """Drop compiled templates so the next render re-reads from disk."""
    _TEMPLATE_CACHE.clear()


def load_templates(force=False):
    """Compile and lint every block. Raises TemplateError naming file and line.

    This is the only IO in the render path; everything downstream of it is pure,
    which is what makes the golden diff and the unit tests possible.
    """
    if _TEMPLATE_CACHE and not force:
        return _TEMPLATE_CACHE

    install_default_templates()
    compiled = {}
    for name, allowed in BLOCK_CONTEXTS.items():
        path = branding_template_path(name)
        if not os.path.isfile(path):
            raise TemplateError(
                f"template is missing. Expected it at {path}. Reinstall the app "
                f"or restore the file from the repository.", name)
        compiled[name] = template_engine.load_template(path, allowed=allowed)

    _TEMPLATE_CACHE.clear()
    _TEMPLATE_CACHE.update(compiled)
    return _TEMPLATE_CACHE


def _block(templates, name, context=None):
    """Render one block, dropping the single trailing newline the file carries.

    Template files end with a newline (POSIX convention, and what every editor
    writes) while base.html supplies its own separators, so that newline would
    otherwise double up. Exactly one is removed, so a template that deliberately
    ends in a blank line keeps it.
    """
    rendered = templates[name].render(context or {})
    return rendered[:-1] if rendered.endswith("\n") else rendered


def build_html(inv_no, date_str, cust, phone, email, items, receipt_type="Online", shipping=0.0):
    """Render a complete receipt document.

    Signature unchanged from Stage 1 so the GUI, cli.py and the fidelity test
    keep calling it the same way; the internals are now template-driven.
    """
    templates = load_templates()
    settings = load_app_settings()
    return render_receipt(
        {
            "invoice_no": inv_no,
            "date": date_str,
            "customer_name": cust,
            "customer_phone": phone,
            "customer_email": email,
            "items": items,
            "receipt_type": receipt_type,
            "shipping": shipping,
        },
        templates,
        resource_base=file_url_for_directory(RESOURCE_DIR),
        font_faces=build_font_faces(settings.get("fonts")),
        strings=config.load_strings(),
        currency=settings.get("currency"),
        terms=settings.get("terms_page", {}).get("enabled", True),
        tax_config=settings.get("tax"),
        fields=config.load_fields(),
        keep_rows_whole=settings.get("render", {}).get("keep_rows_whole", True),
        show_installments=settings.get("installments", {}).get("enabled", False),
        payment_config=settings,
    )


def render_receipt(data, templates, resource_base="", font_faces="", strings=None,
                   currency=None, terms=True, tax_config=None, fields=None,
                   keep_rows_whole=True, show_installments=False,
                   payment_config=None):
    """Pure render: (data, templates, strings, currency) -> html.

    No clock, no IO, no globals. Everything non-deterministic (the resource base
    URL, the invoice number, the date string, the base64 font payload) is
    injected by the caller -- principle 2.
    """
    strings = strings or config.default_strings()
    fields = fields if fields is not None else config.default_fields()
    currency = currency if currency is not None else config.DEFAULT_APP_SETTINGS["currency"]
    decimals = currency.get("decimals", AMOUNT_DECIMALS)
    group_lines = currency.get("group_line_amounts", True)
    column_labels = strings.get("columns", {})
    totals_labels = strings.get("totals", {})
    empty_cell = strings.get("empty_cell", "-")
    items = data.get("items") or []

    none_warranty = fields.get("warranty", {}).get("none_option", NO_WARRANTY_LABEL)

    # --- line items -----------------------------------------------------
    columns = visible_columns(fields, items, decimals)

    header_cells = "".join(
        _block(templates, "item_header_cell.html", {
            # fields.json owns the heading; strings.json is consulted only so an
            # existing translation of the built-in columns keeps working.
            "label": field.get("label") or column_labels.get(field["key"], field["key"]),
            "css_class": _css_class(field),
        })
        for field in columns
    )

    rows = []
    # Group a shipment's lines together. Stable, so the same data always
    # renders identically -- determinism is a tested invariant. Ungrouped
    # receipts come back in their original order untouched.
    items = shipments.order_items(items)
    shipment_markers = shipments.markers_for(items)

    for item in items:
        cells = "".join(
            _block(templates, "item_row_cell.html",
                   _cell_context(item, field, empty_cell, currency, group_lines,
                                 strings, none_warranty, show_installments,
                                 shipment_markers))
            for field in columns
        )
        rows.append(f"<tr>{cells}</tr>")

    # --- totals ---------------------------------------------------------
    # Sum the *rounded* line values so the printed figures add up on the page.
    subtotal = sum((quantize(line_gross(i), decimals) for i in items), Decimal("0"))
    total_discount = sum((quantize(i.get("discount", 0), decimals) for i in items), Decimal("0"))
    total_tax = sum((quantize(i.get("tax", 0), decimals) for i in items), Decimal("0"))
    # Shipping is charged per shipment when the lines are grouped, and as the
    # single flat fee it has always been when they are not.
    shipment_rows, ship = shipments.rows(
        data, items, decimals, flat_shipping=data.get("shipping", 0))

    # Document-level tax rows, on top of the per-line tax amounts above.
    doc_tax_rows, doc_tax_added = compute_tax_rows(
        subtotal, total_discount, tax_config, decimals)

    total = subtotal + total_tax - total_discount + ship + doc_tax_added

    # What the customer pays with. Worked out here, before the breakdown, so a
    # receipt whose only adjustment is a payment charge still shows a subtotal
    # -- a charge and a total with nothing tying them together is worse than no
    # breakdown at all. See payment_methods.py for why tax and fee stay apart.
    payment_row = None
    if payment_config:
        import payment_methods
        payment_row = payment_methods.row(
            payment_config, data.get(payment_methods.METHOD_KEY), total, decimals)

    # Break the subtotal out only when there is something besides the line items
    # to show; otherwise TOTAL alone says everything.
    totals_rows = ""
    if (total_tax or total_discount or ship or doc_tax_rows or shipment_rows
            or payment_row):
        breakdown = [(totals_labels.get("subtotal", "Subtotal"),
                      format_amount(subtotal, currency))]
        if total_tax:
            breakdown.append((totals_labels.get("taxes", "Taxes"),
                              format_amount(total_tax, currency)))
        if total_discount:
            # A discount is shown as a deduction from a positive figure, so the
            # sign is part of the row's wording -- not negative_style, which is
            # for an amount that is itself negative (a refund line).
            breakdown.append((totals_labels.get("discounts", "Discounts"),
                              "- " + format_amount(total_discount, currency)))
        if shipment_rows:
            # Each shipment's own fee, then the combined total. A customer
            # seeing two charges needs to see what each one was for, or the
            # second reads as a mistake.
            for tag, position, count, fee in shipment_rows:
                label = shipments.marker(
                    position, count,
                    strings.get("totals", {}).get("shipment_marker",
                                                  "Shipment {n} of {total}"))
                base = totals_labels.get("shipping", "Shipping Fees")
                breakdown.append((f"{base} — {label}" if label else base,
                                  format_amount(fee, currency)))
            if len(shipment_rows) > 1:
                breakdown.append((totals_labels.get("shipping", "Shipping Fees"),
                                  format_amount(ship, currency)))
        elif ship:
            breakdown.append((totals_labels.get("shipping", "Shipping Fees"),
                              format_amount(ship, currency)))
        # In inclusive mode these are reported, not added, so the label has to
        # say so -- otherwise the figures look like they do not add up.
        included_suffix = totals_labels.get("included_suffix", " (included)")
        inclusive = (tax_config or {}).get("mode", "exclusive") == "inclusive"
        for label, amount in doc_tax_rows:
            breakdown.append((f"{label}{included_suffix}" if inclusive else label,
                              format_amount(amount, currency)))
        totals_rows = "".join(
            _block(templates, "totals_row.html", {"label": label, "amount": amount})
            for label, amount in breakdown
        )

    if payment_row:
        label, kind, amount = payment_row
        heading = totals_labels.get(
            "payment_tax" if kind == "tax" else "payment_fee", "Payment charge")
        totals_rows += _block(templates, "totals_row.html", {
            "label": f"{heading} — {label}",
            "amount": format_amount(amount, currency),
        })
        total += amount

    # The instalment plan, shown *beside* the cash total rather than replacing
    # it. The tax rows above apply to the goods; financing them is a separate
    # arrangement, so the TOTAL stays what the goods cost and the plan is
    # disclosed in full underneath. See installments.py.
    if show_installments:
        import installments
        scope, plan_rows, plan_totals = installments.collect(
            {"items": items, installments.PLAN_KEY: data.get(installments.PLAN_KEY)},
            items, decimals)
        if plan_rows:
            plan_breakdown = []
            if plan_totals["down"]:
                plan_breakdown.append(
                    (totals_labels.get("installment_down", "Down payment"),
                     format_amount(plan_totals["down"], currency)))
            if plan_totals["monthly"]:
                months = plan_totals["months"]
                label = totals_labels.get("installment_monthly", "Monthly payment")
                plan_breakdown.append(
                    (f"{label} × {months}" if months else label,
                     format_amount(plan_totals["monthly"], currency)))
            plan_breakdown.append(
                (totals_labels.get("installment_total", "Total if paid in instalments"),
                 format_amount(plan_totals["financed"], currency)))
            totals_rows += "".join(
                _block(templates, "totals_row.html", {"label": label, "amount": amount})
                for label, amount in plan_breakdown
            )

    # --- assemble -------------------------------------------------------
    receipt_type = data.get("receipt_type", "")
    receipt_info = _block(templates, "receipt_info.html", {
        "type_badge": config.receipt_type_by_label(receipt_type).get("badge_text", ""),
        "invoice_no": data.get("invoice_no", ""),
        "date": data.get("date", ""),
        "customer_name": data.get("customer_name", ""),
        "customer_phone": data.get("customer_phone", ""),
        "customer_email": data.get("customer_email", ""),
        # Configured extras (a PO number, a salesperson, a registration). Each
        # row hides itself when empty, so an unfilled field leaves no stray
        # label behind.
        "custom_receipt_fields": "".join(
            _block(templates, "field_row.html", {
                "label": field.get("label", field["key"]),
                "value": _receipt_field_value(data, field, currency, strings),
                # A paragraph field keeps its line breaks; everything else is
                # one line and needs no class of its own.
                "value_class": ("field-lines"
                                if field.get("type") == "multiline" else ""),
            })
            for field in fields.get("receipt_fields", [])
            if field.get("enabled", True)
        ),
    })
    items_table = _block(templates, "items_table.html", {
        "header_cells": header_cells,
        "rows": "".join(rows),
    })
    totals = _block(templates, "totals.html", {
        "totals_rows": totals_rows,
        "total": format_amount(total, currency),
    })

    return _block(templates, "base.html", {
        "resource_base": resource_base,
        "styles": _block(templates, "styles.css", {
            "keep_rows_whole": keep_rows_whole,
        }),
        "font_faces": font_faces,
        "receipt_info": receipt_info,
        "items_table": items_table,
        "totals": totals,
        # Empty when the terms page is switched off; base.html wraps it in an
        # {{#if}} so disabling it leaves no stray blank page or whitespace.
        "terms": _block(templates, "terms.html") if terms else "",
    })


def compute_tax_rows(subtotal, total_discount, tax_config, decimals):
    """Work out the document-level tax lines.

    Returns ``(rows, added_to_total)`` where ``rows`` is a list of
    ``(label, amount)`` and ``added_to_total`` is what the grand total gains --
    zero in inclusive mode, because there the tax is already inside the prices.

    Ordering is fixed: any discount comes off first, then tax applies to the
    result (unless a row asks for the pre-discount subtotal). With several
    inclusive percent rows the combined rate is backed out **once** rather than
    each row being backed out of the gross independently, which would over-state
    every row after the first.
    """
    tax_config = tax_config or {}
    rows = tax_config.get("rows") or []
    if not rows:
        return [], Decimal("0")

    inclusive = tax_config.get("mode", "exclusive") == "inclusive"

    def base_for(row):
        if row.get("applies_to", "subtotal_after_discount") == "subtotal":
            return subtotal
        return subtotal - total_discount

    if inclusive:
        # Fixed inclusive amounts sit inside the price as-is; percent rows share
        # whatever is left once those are removed.
        fixed_total = sum(
            (quantize(r.get("value", 0), decimals) for r in rows
             if r.get("type", "percent") == "fixed"), Decimal("0"))
        combined_rate = sum(
            (to_decimal(r.get("value", 0)) for r in rows
             if r.get("type", "percent") == "percent"), Decimal("0")) / Decimal(100)

        results = []
        for row in rows:
            label = str(row.get("label", ""))
            if row.get("type", "percent") == "fixed":
                results.append((label, quantize(row.get("value", 0), decimals)))
                continue
            gross = base_for(row) - fixed_total
            net = gross / (Decimal(1) + combined_rate) if combined_rate else gross
            amount = net * to_decimal(row.get("value", 0)) / Decimal(100)
            results.append((label, quantize(amount, decimals)))
        return results, Decimal("0")

    results, added = [], Decimal("0")
    for row in rows:
        label = str(row.get("label", ""))
        if row.get("type", "percent") == "fixed":
            amount = quantize(row.get("value", 0), decimals)
        else:
            amount = quantize(
                base_for(row) * to_decimal(row.get("value", 0)) / Decimal(100), decimals)
        results.append((label, amount))
        added += amount
    return results, added


_FONT_MIME = {
    ".woff2": "font/woff2", ".woff": "font/woff",
    ".ttf": "font/ttf", ".otf": "font/otf",
}
_CSS_UNSAFE = re.compile(r"[^A-Za-z0-9 _-]")


def build_font_faces(fonts):
    """CSS embedding the configured font, or '' when none is configured.

    Returned as an engine-produced fragment inserted with ``|raw`` after
    styles.css, so it overrides the stylesheet's default family. The family name
    is stripped of everything but letters, digits, spaces, hyphens and
    underscores before it reaches CSS: principle 4 allows no user value to be
    interpolated into a CSS context, and escaping cannot make CSS safe the way
    it does for an HTML text node.

    Font files are inlined as base64 so a receipt still renders with no network.
    """
    fonts = fonts or {}
    family = _CSS_UNSAFE.sub("", str(fonts.get("family", "")).strip())
    files = [f for f in (fonts.get("files") or []) if str(f).strip()]
    if not family or not files:
        return ""

    faces = []
    for entry in files:
        path = resolve_local_asset_path(str(entry))
        if not path or not os.path.isfile(path):
            continue
        mime = _FONT_MIME.get(os.path.splitext(path)[1].lower())
        if not mime:
            continue
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        faces.append(
            f"    @font-face {{\n"
            f"        font-family: '{family}';\n"
            f"        src: url(data:{mime};base64,{encoded});\n"
            f"        font-display: block;\n"
            f"    }}"
        )
    if not faces:
        return ""

    fallback = _CSS_UNSAFE.sub("", str(fonts.get("fallback", "")).strip().replace(",", "\x00"))
    fallback = fallback.replace("\x00", ",")
    stack = f"'{family}'" + (f", {fallback}" if fallback else "")
    faces.append(f"    body {{\n        font-family: {stack};\n    }}")
    return "\n" + "\n".join(faces)


def _receipt_field_value(data, field, currency, strings):
    """Format one receipt-level custom field for display."""
    raw = data.get(field["key"], "")
    style = TYPE_PRESENTATION.get(field.get("type", "text"), ("", "plain"))[1]
    if raw in ("", None):
        return ""
    if style == "money":
        return format_amount(raw, currency)
    if style == "boolean":
        yes_no = (strings or {}).get("boolean", {})
        return yes_no.get("yes", "Yes") if raw else yes_no.get("no", "No")
    return str(raw)


def _css_class(field):
    base = TYPE_PRESENTATION.get(field.get("type", "text"), ("", "plain"))[0]
    if field.get(line_units.PER_UNIT_FLAG):
        # The cell holds several lines; the class is what stops them running
        # together into one paragraph (styles.css: white-space: pre-line).
        return f"{base} item-units".strip()
    return base


def visible_columns(fields, items, decimals):
    """The line-item columns this receipt actually shows, in configured order.

    A disabled field is never shown. An `optional_column` is shown only when at
    least one line uses it, which is what keeps an unused Discount or Tax column
    off an otherwise clean receipt.
    """
    columns = []
    for field in fields.get("line_item_fields", []):
        if not field.get("enabled", True):
            continue
        if field.get("optional_column"):
            key = field["key"]
            if field.get(line_units.PER_UNIT_FLAG):
                # A per-unit column holds text in `units`, not a number on the
                # item, so "is it used?" is whether any unit carries a value --
                # quantize() would read every serial as 0 and hide the column.
                used = any(line_units.to_text(line_units.values_for(item, key))
                           for item in items)
            else:
                used = any(quantize(item.get(key, 0), decimals) for item in items)
            if not used:
                continue
        columns.append(field)
    return columns


def _cell_context(item, field, empty_cell="-", currency=None, group=True,
                  strings=None, none_warranty=NO_WARRANTY_LABEL,
                  show_installments=False, shipment_markers=None):
    """Precompute one cell's finished strings -- templates make no decisions."""
    currency = currency or {}
    strings = strings or {}
    decimals = currency.get("decimals", AMOUNT_DECIMALS)
    key = field["key"]
    style = TYPE_PRESENTATION.get(field.get("type", "text"), ("", "plain"))[1]

    if key == "line_total":
        raw = line_total(item, decimals)
    elif key in DERIVED_KEYS:
        raw = line_gross(item)
    elif field.get(line_units.PER_UNIT_FLAG):
        # One value for each thing sold, stacked in the cell. Blanks are left
        # out rather than printed as gaps: a line where only two of three
        # serials were captured should show the two, not two and an empty row.
        raw = line_units.to_text(line_units.values_for(item, key))
        style = "lines"
    else:
        raw = item.get(key, "")

    if style == "money":
        rounded = quantize(raw, decimals)
        # An optional money column prints a marker rather than a bare zero, so an
        # unused per-line discount does not read as "discounted by nothing".
        value = (format_amount(rounded, currency, group)
                 if rounded or not field.get("optional_column") else empty_cell)
    elif style == "boolean":
        yes_no = strings.get("boolean", {})
        value = yes_no.get("yes", "Yes") if raw else yes_no.get("no", "No")
    elif style == "lines":
        value = str(raw or "")
    else:
        value = str(raw if raw != "" else "") or (empty_cell if raw == "" else str(raw))

    # The warranty rides under the description rather than taking a column of
    # its own, which is why it is a note instead of a field.
    note = ""
    if key == "desc":
        warranty = str(item.get("warranty", "") or "")
        if warranty and warranty != none_warranty:
            note = warranty
        # A line's own instalment plan belongs with the line, not in a column:
        # it is a sentence, and it only applies to this one item.
        marker = (shipment_markers or {}).get(shipments.group_of(item), "")
        if marker:
            note = f"{note} · {marker}" if note else marker
        if show_installments:
            import installments
            plan = installments.describe(
                item.get(installments.PLAN_KEY),
                lambda value: format_amount(value, currency, group))
            if plan:
                label = strings.get("totals", {}).get("installment_note",
                                                      "Instalment plan")
                note = f"{note} · {label}: {plan}" if note else f"{label}: {plan}"

    return {"value": value, "css_class": _css_class(field), "note": note}


def warranty_policy_html():
    # Second page printed on every receipt. Plain string (not an f-string),
    # so literal braces are not an issue and ampersands are written as &amp;.
    return """
<div class="policy-page">
    <div class="policy-title">🛡️ Chawla Tech — Warranty &amp; Returns Policy (Key Points)</div>
    <div class="policy-columns">
        <div class="policy-section">
            <div class="policy-heading">📦 Returns &amp; Exchanges</div>
            <ul>
                <li><strong>Unopened / Sealed items:</strong> Returnable within 7 days for a full refund or exchange — original seal must be completely intact. Buyer pays return shipping.</li>
                <li><strong>Once opened:</strong> Change-of-mind return is no longer valid, even if the item was never powered on.</li>
                <li><strong>Opened / Used items with a defect:</strong> Eligible for return or replacement within 7 days, after in-store testing confirms the fault. Buyer pays return shipping.</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">📹 Video Proof Requirement (Important)</div>
            <ul>
                <li>For any return or exchange claim involving a wrong, defective, broken, missing, or faulty item (or any missing/faulty part), a video proof is mandatory.</li>
                <li>The video must include the unboxing of the item from the moment the parcel/box is opened. Claims without this video proof will not be accepted.</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">🔧 What's Covered</div>
            <ul>
                <li>Manufacturing/material defects discovered under normal use within 7 days.</li>
                <li>Some products have an extended manufacturer warranty (e.g., 6 months, 1 year) — check the product listing.</li>
                <li>Chinese-imported/grey-market items: 7-day Chawla Tech warranty only — no brand warranty.</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">❌ What's NOT Covered (Warranty Void)</div>
            <ul>
                <li>Electrical damage from power surges, over-voltage, load-shedding, or wrong PSU</li>
                <li>Physical damage (drops, broken pins, cracked screens, bent parts)</li>
                <li>ESD (static electricity) damage</li>
                <li>Liquid, fire, heat, or environmental damage</li>
                <li>Damage from incorrect installation or incompatible parts</li>
                <li>Unauthorized modifications, overclocking, or BIOS/firmware flashing</li>
                <li>Pest damage (insects, lizards, rodents)</li>
                <li>Tampered, removed, or defaced serial numbers/warranty seals</li>
                <li>Continued use after a fault appeared</li>
                <li>Software issues, data loss, viruses</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">🚫 No Returns At All (Ever)</div>
            <ul>
                <li>Digital products &amp; license keys — zero exceptions, no matter what</li>
                <li>Opened hygiene items (earphones, screen protectors)</li>
                <li>Clearance / As-Is items</li>
                <li>Used single-use consumables (thermal paste, cleaning wipes, applied stickers/skins)</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">💸 Refunds</div>
            <ul>
                <li>Cash refunds are processed physically; online refunds within 5–7 business days.</li>
                <li>Items sold at a discount will be refunded the discounted price only.</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">📋 To Make a Claim, You Need</div>
            <ul>
                <li>Proof of purchase (receipt, invoice, or registered mobile/email)</li>
                <li>Video proof including unboxing (for wrong/defective/broken/missing/faulty items)</li>
                <li>Item returned within the applicable window</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">📞 Contact</div>
            <ul>
                <li><strong>WhatsApp/Phone:</strong> +92 339 282 5523 (Mon–Thu &amp; Sat, 10am–8pm; Fri, 10am-12pm &amp; 3pm-8pm)</li>
                <li><strong>Email:</strong> support@chawlatech.pk (reply within 24 hours)</li>
                <li><strong>In-store:</strong> Karachi — bring the item and receipt</li>
            </ul>
        </div>
    </div>
</div>"""
