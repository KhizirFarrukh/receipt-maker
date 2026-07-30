"""Receipt HTML construction and page header/footer templates (tkinter-free).

Extracted verbatim from ReceiptApp in Stage 1 (instance methods -> module
functions; none used widget state). Behaviour is unchanged -- the Stage 0 golden
test guards build_html's output byte-for-byte. Stage 2 replaces the internals of
this module with the template-driven engine.
"""
import base64
import html as html_utils
import mimetypes
import os
import re

from config import (
    APP_DIR,
    RESOURCE_DIR,
    HEADER_FILE,
    FOOTER_FILE,
    PDF_MARGIN_LEFT,
    PDF_MARGIN_RIGHT,
    read_html_file,
    load_app_settings,
)


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

    for base_dir in (APP_DIR, RESOURCE_DIR):
        asset_path = os.path.abspath(os.path.join(base_dir, clean_src))
        try:
            if os.path.commonpath([base_dir, asset_path]) != base_dir:
                continue
        except ValueError:
            continue

        if os.path.isfile(asset_path):
            return asset_path
    return ""


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


def render_settings_template(template_html):
    company = load_app_settings()["company"]
    logo_path = company["logo_path"]
    if not logo_source_available(logo_path):
        template_html = remove_logo_image_tags(template_html)
        logo_path = ""

    replacements = {
        "{{company_name}}": escape(company["name"]),
        "{{company_address}}": escape_address(company["address"]),
        "{{company_phone}}": escape(company["phone"]),
        "{{company_email}}": escape(company["email"]),
        "{{company_logo}}": escape(logo_path),
        "{{company_logo_path}}": escape(logo_path),
    }

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
    return """
<div class="footer-text">
    Thank you for choosing {{company_name}}. Warranty claims require original receipt.
</div>
<div class="footer-policy">
    For our detailed warranty policy, visit https://chawlatech.pk/pages/warranty-policy
</div>
<div class="footer-terms">
    By purchasing from Chawla Tech, you agree to our Terms of Service, Privacy Policy, &amp; Warranty Policy (available at chawlatech.pk).
</div>
<div class="signature-notice">
    This receipt is digitally signed by {{company_name}}. Verify its authenticity at chawlatech.pk/verify.
</div>"""


def build_page_header_template():
    header_html = read_html_file(HEADER_FILE).strip() or default_header_html()
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
    footer_html = read_html_file(FOOTER_FILE).strip() or default_footer_html()
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
    .signature-notice {{
        margin-top: 3px;
    }}
</style>
<div class="pdf-footer">
    <div class="footer-inner">{footer_html}</div>
</div>"""


def build_html(inv_no, date_str, cust, phone, email, items, receipt_type="Online", shipping=0.0):
    type_badge = "ONLINE ORDER" if receipt_type == "Online" else "IN-STORE SALE"

    # Optional columns appear only when at least one line item uses them.
    show_discount = any(i.get("discount", 0.0) for i in items)
    show_tax = any(i.get("tax", 0.0) for i in items)

    header_cells = (
        "<th>SKU</th><th>Item Description</th><th>Serial Number</th>"
        "<th>Qty</th><th>Unit Price</th>"
    )
    if show_discount:
        header_cells += "<th>Discount</th>"
    if show_tax:
        header_cells += "<th>Tax</th>"
    header_cells += "<th>Amount</th>"

    rows_html = ""
    for item in items:
        warranty_display = ""
        if item["warranty"] and item["warranty"] != "No Warranty":
            warranty_display = (
                f'<br/><span class="item-warranty-text">'
                f'{escape(item["warranty"])}</span>'
            )
        discount = item.get("discount", 0.0)
        tax = item.get("tax", 0.0)
        amount = item["qty"] * item["price"]
        cells = (
            f"<td>{escape(item['sku']) or '-'}</td>"
            f"<td>{escape(item['desc'])}{warranty_display}</td>"
            f"<td>{escape(item['serial']) or '-'}</td>"
            f'<td class="num">{item["qty"]}</td>'
            f'<td class="num">Rs. {item["price"]:.2f}</td>'
        )
        if show_discount:
            cells += f'<td class="num">{("Rs. %.2f" % discount) if discount else "-"}</td>'
        if show_tax:
            cells += f'<td class="num">{("Rs. %.2f" % tax) if tax else "-"}</td>'
        cells += f'<td class="num">Rs. {amount:.2f}</td>'
        rows_html += f"<tr>{cells}</tr>"

    subtotal = sum(i["qty"] * i["price"] for i in items)
    total_discount = sum(i.get("discount", 0.0) for i in items)
    total_tax = sum(i.get("tax", 0.0) for i in items)
    total = subtotal + total_tax - total_discount + shipping

    # Break out the subtotal/components only when there is something besides
    # the line items to show; otherwise just show TOTAL.
    totals_rows = ""
    if total_tax or total_discount or shipping:
        totals_rows += (
            f'<tr class="totals-sub"><td>Subtotal</td>'
            f'<td align="right">Rs. {subtotal:,.2f}</td></tr>'
        )
        if total_tax:
            totals_rows += (
                f'<tr class="totals-sub"><td>Taxes</td>'
                f'<td align="right">Rs. {total_tax:,.2f}</td></tr>'
            )
        if total_discount:
            totals_rows += (
                f'<tr class="totals-sub"><td>Discounts</td>'
                f'<td align="right">- Rs. {total_discount:,.2f}</td></tr>'
            )
        if shipping:
            totals_rows += (
                f'<tr class="totals-sub"><td>Shipping Fees</td>'
                f'<td align="right">Rs. {shipping:,.2f}</td></tr>'
            )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<base href="{file_url_for_directory(RESOURCE_DIR)}">
<style>
    @page {{
        size: A4;
    }}
    html, body {{
        margin: 0;
        padding: 0;
    }}
    body {{
        font-family: Helvetica, Arial, sans-serif;
        font-size: 10pt;
        color: #111;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
    }}
    .receipt-title {{
        font-size: 14pt;
        font-weight: bold;
        text-align: center;
        margin: 0 0 4px 0;
        letter-spacing: 0;
    }}
    .type-badge {{
        text-align: center;
        font-size: 9pt;
        font-weight: bold;
        color: #ffffff;
        background-color: #0f172a;
        padding: 4px 0;
        margin-bottom: 12px;
    }}
    .meta-table {{
        width: 100%;
        margin-bottom: 12px;
        border-bottom: 1px dashed #94a3b8;
    }}
    .meta-table td {{
        font-size: 9pt;
        padding: 2px 0 6px 0;
    }}
    .customer-box {{
        background-color: #f8fafc;
        padding: 8px;
        margin-bottom: 12px;
        font-size: 9pt;
    }}
    table.items {{
        width: 100%;
        border-collapse: collapse;
        margin: 6px 0;
    }}
    table.items thead {{
        display: table-header-group;
    }}
    table.items th {{
        background-color: #0f172a;
        color: #ffffff;
        padding: 5px 4px;
        font-size: 8pt;
        text-align: left;
        border: 1px solid #0f172a;
    }}
    table.items td {{
        border-bottom: 1px solid #cbd5e1;
        padding: 5px 4px;
        font-size: 9pt;
        vertical-align: top;
    }}
    table.items tr {{
        break-inside: avoid;
        page-break-inside: avoid;
    }}
    table.items td.num {{
        text-align: right;
    }}
    .item-warranty-text {{
        color: #6b7280;
        font-style: italic;
        font-size: 8pt;
    }}
    .totals-table {{
        width: 50%;
        margin-left: 50%;
        margin-top: 8px;
        font-size: 10pt;
    }}
    .totals-table td {{
        padding: 3px 0;
        font-size: 10pt;
    }}
    .totals-table tr.totals-sub td {{
        color: #334155;
    }}
    .totals-table tr.totals-grand td {{
        padding: 6px 0;
        border-top: 2px solid #0f172a;
        border-bottom: 2px solid #0f172a;
        font-weight: bold;
        font-size: 12pt;
    }}
    .customer-box,
    .totals-table {{
        break-inside: avoid;
        page-break-inside: avoid;
    }}
    .policy-page {{
        page-break-before: always;
        break-before: page;
    }}
    .policy-title {{
        font-size: 12pt;
        font-weight: bold;
        text-align: center;
        margin: 0 0 10px 0;
        color: #0f172a;
    }}
    .policy-columns {{
        column-count: 2;
        column-gap: 22px;
    }}
    .policy-section {{
        break-inside: avoid;
        page-break-inside: avoid;
        margin: 0 0 9px 0;
    }}
    .policy-heading {{
        font-size: 9pt;
        font-weight: bold;
        color: #0f172a;
        margin: 0 0 3px 0;
    }}
    .policy-section ul {{
        margin: 0;
        padding-left: 14px;
    }}
    .policy-section li {{
        font-size: 7.8pt;
        line-height: 1.35;
        margin-bottom: 2px;
        color: #1f2937;
    }}
</style>
</head>
<body>

<div class="receipt-title">SALES RECEIPT</div>
<div class="type-badge">{type_badge}</div>

<table class="meta-table">
    <tr>
        <td><strong>Receipt No:</strong> {escape(inv_no)}</td>
        <td align="right"><strong>Date:</strong> {escape(date_str)}</td>
    </tr>
</table>

<div class="customer-box">
    <strong>Bill To:</strong><br/>
    {escape(cust)}<br/>
    {('Phone: ' + escape(phone) + '<br/>') if phone else ''}
    {('Email: ' + escape(email)) if email else ''}
</div>

<table class="items">
    <thead>
        <tr>{header_cells}</tr>
    </thead>
    <tbody>
        {rows_html}
    </tbody>
</table>

<table class="totals-table">{totals_rows}
    <tr class="totals-grand">
        <td>TOTAL</td>
        <td align="right">Rs. {total:,.2f}</td>
    </tr>
</table>

{warranty_policy_html()}

</body>
</html>"""


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
