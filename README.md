# Receipt Generator

A Python tkinter desktop app for generating A4 PDF sales receipts.

## Features

- Separate invoice number series for online (`INV-W####`) and in-store (`INV-S####`) purchases. The number is editable and defaults to one above the highest previously generated invoice in that series.
- Customer, item, serial number, quantity, price, per-line discount, per-line tax, and warranty fields, plus a global shipping fee.
- Receipt totals show a Subtotal / Taxes / Discounts / Shipping / Total breakdown; taxes, discounts, and shipping rows (and the Discount/Tax columns) appear only when used.
- Configurable currency (symbol, placement, decimals, thousand/lakh grouping), date format, receipt types, and document-level tax — inclusive or exclusive. See [Business Settings](#business-settings).
- Auto-incrementing receipt numbers saved under `invoices/`.
- Configurable PDF filenames through `filename_config.json`.
- Configurable business/header details through `appsettings.json`.
- PDF generation through Playwright/Chromium.
- Every receipt is digitally signed (PAdES) with your private key, so a forged or edited receipt fails verification against your public certificate. See [Receipt Authenticity](#receipt-authenticity-digital-signatures).
- The footer links to your Terms of Service, Privacy Policy and Warranty Policy — clickable in the PDF, and configurable under Tools → Settings.
- A second page with the Warranty & Returns Policy is appended to every receipt.
- `Templates/header.html` and `Templates/footer.html` are rendered as real PDF page headers and footers on every page.
- The receipt body is laid out inside the reserved PDF content area, so it does not overlap the header or footer.
- The whole receipt layout lives in editable templates under `Templates/` — edit the HTML/CSS and the next receipt changes, with no rebuild. See [Templates](#templates).
- Amounts are computed in decimal (never binary floating point), with each line rounded and the rounded lines summed, so the printed figures add up.

## Requirements

- Python 3.8 or newer
- tkinter (usually needs a separate system package on Linux)
- Playwright
- Chromium installed through Playwright
- pyHanko and cryptography (installed by `requirements.txt`) for receipt signing

## Setup

**Linux (Fedora/RHEL):**

```bash
sudo dnf install python3-tkinter
python -m pip install -r requirements.txt
python -m playwright install chromium
```

**Linux (Debian/Ubuntu):**

```bash
sudo apt install python3-tk
python -m pip install -r requirements.txt
python -m playwright install chromium
```

**Windows/macOS:**

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```bash
python main.py
```

Generated PDFs are written to `invoices/`.

## Receipt Authenticity (Digital Signatures)

Every receipt is sealed with a **PAdES digital signature** using your store's private
key. The signature covers the whole PDF, so a receipt that someone else fabricates —
or a genuine one that has been edited — will not pass verification against your public
certificate. This lets you (and, once you publish the certificate, your customers)
tell a genuine receipt from a fake one.

### One-time setup: create your signing key

**In the app:** *Tools → Signing Keys…* → **Create new key**. It uses the business name from your
settings, shows you where the files went, and tells you when the certificate expires.

That dialog also **imports a key you already have** — PEM or DER, PKCS#8 or PKCS#1, encrypted or
not, or a PKCS#12 `.pfx`/`.p12` bundle. If the key is encrypted you are asked for its passphrase
once, to read it; **the passphrase is never stored**. The key is saved unencrypted in the app's
`signing/` folder, so keep that folder private. (Storing a passphrase next to the key it unlocks
protects nobody, and asking for it on every receipt is not workable at a till — so the app does
neither and says so.)

If the file cannot be used you get a specific reason: wrong passphrase, a certificate picked
instead of a key, a public key, a certificate that does not match the key, or an RSA key under
2048 bits.

**Changing your key does not invalidate receipts you have already issued.** The old certificate is
remembered, so those receipts keep verifying — and verification says when one was signed with a
previous key, so you can still tell them apart.

**From a terminal**, equivalently:

```bash
python keygen.py
```

It writes two files into a `signing/` folder beside the app:

- `signing/private_key.pem` — **SECRET.** Signs your receipts. Keep it on your machine
  only. Never share it, never commit it, and **never bundle it into the distributed
  `.exe`** — anyone who has it can forge your receipts. **Back it up somewhere safe:**
  if you lose it you must issue a new certificate and re-publish it, and receipts signed
  with the old key can no longer be tied to the new one.
- `signing/certificate.pem` — **PUBLIC.** Safe to share; it only verifies, never signs.
  Publish it on your website / verifier so receipts can be checked.

Optional: encrypt the private key with a passphrase and record it in `appsettings.json`:

```bash
python keygen.py --passphrase "your-secret-passphrase"
```

Once the key exists, every receipt you generate is signed automatically. The signature is
embedded in the PDF itself, so a reader can check it without the receipt having to announce it —
which is why the footer carries no "digitally signed" notice.

### Verifying receipts

- **In the app:** `Tools → Verify Receipt…`, pick a PDF. You get one of three results:
  - **Signature Verified** — genuine and unaltered.
  - **Invalid signature** — signed, but tampered, forged, or not your certificate.
  - **Signature not found** — the PDF carries no signature at all.
- **From the command line** (also the reference implementation for your website):

  ```bash
  python verify_receipt.py path/to/receipt.pdf
  ```

  Exit codes: `0` verified, `1` invalid, `2` no signature, `3` error.

### Signing older, unsigned receipts

Receipts created before you set up signing (or with signing disabled) have no signature.
To sign them, use `Tools → Sign Existing PDF(s)…` and select one or more PDFs. Files that
are already signed are skipped.

### Signing settings (`appsettings.json`)

```json
"signing": {
  "enabled": true,
  "private_key_path": "signing/private_key.pem",
  "certificate_path": "signing/certificate.pem",
  "key_passphrase": "",
  "signer_name": "Your Company",
  "reason": "Receipt authenticity",
  "location": "",
  "tsa_url": ""
}
```

- `enabled` — set to `false` to generate unsigned receipts (legacy behavior).
- `key_passphrase` — only needed if you created the key with `--passphrase`.
- `signer_name` — also becomes the organization on the certificate `keygen.py` creates.
  Set it **before** running `keygen.py`, or pass `--org-name` explicitly; the certificate
  subject is what a verifier displays as the receipt's issuer, and changing it later means
  issuing a new certificate.
- `tsa_url` — optional RFC 3161 timestamp-authority URL for a trusted signing time
  (PAdES-T). Leave empty to use the receipt's own date.

If signing is enabled but the key/certificate is missing, receipt generation stops with
an error instead of silently producing an unsigned file.

### Publishing a verify page on your website

`verify_receipt.py` (and `receipt_signing.verify_pdf`) is the reference implementation.
Your website's `/verify` page needs a small server-side function that runs the same
check — pin your published `certificate.pem` as the trust root, accept an uploaded PDF,
and report Verified / Invalid / Not found. (A Shopify storefront cannot do this in
Liquid; it needs a serverless function or a small microservice.)

## Build Executable

### Linux

```bash
bash build_exe.sh
```

The packaged app is created at:

```text
dist/ReceiptGenerator/ReceiptGenerator
```

### Windows

Run this from PowerShell:

```powershell
.\build_exe.ps1
```

The packaged app is created at:

```text
dist\ReceiptGenerator\ReceiptGenerator.exe
```

Distribute the whole `dist/ReceiptGenerator` folder, not only the executable. It includes the bundled Playwright/Chromium files needed for PDF generation.

`appsettings.json` and `filename_config.json` sit beside the executable so users can edit business details and filename options after packaging. Generated PDFs are written to the `invoices` folder beside the executable.

`Templates/` is created beside the executable the first time the app runs, seeded from the
read-only copies inside `_internal`. Edit those files to change the receipt layout of a packaged
install — no rebuild needed. If the app is replacing an older install that had `header.html` /
`footer.html` sitting loose beside the exe, those edited files are carried into `Templates/`
rather than being replaced by the defaults.

The build does **not** include your signing key — the `signing/` folder is created beside the executable when you run `keygen.py`. Keep it on your own machine and do not copy it into any `dist/` folder you share, so your private key is never distributed.

## Settings

Everything below can be edited **inside the app** — you do not need to open a text editor:

| Menu | What it edits |
|---|---|
| **Tools → Settings…** | Business details and logo, currency, tax, dates, page margins, invoice numbering, signing, interface preferences (`appsettings.json`) |
| **Tools → Fields & Columns…** | The item table's columns, extra receipt fields, and the warranty options (`fields.json`) |
| **Tools → Signing Keys…** | Create or import the key that signs receipts, and see when its certificate expires |

Saving validates first, so a value the app would refuse to load is refused *before* anything is
written, with a message naming the exact setting. Every save keeps a timestamped `.bak`, and if the
file was edited outside the app while the window was open you are asked rather than having your
change silently overwritten.

Hand-editing the JSON still works exactly as before — the dialogs are a front end onto the same
files. The sections below document the files themselves.

A few changes (currency labels on the form, the receipt-type dropdown, which item columns exist)
are applied when the app next starts, because they determine how the main window is laid out.

## Business Settings

Edit `appsettings.json` to change the business details shown in the receipt header and footer.

```json
{
  "company": {
    "name": "Your Company",
    "address": "Your business address",
    "phone": "000-000-0000",
    "email": "hello@example.com",
    "logo_path": "logo.png"
  }
}
```

`appsettings.json` also carries:

- `schema_version` — managed by the app. An older file is upgraded automatically on first run,
  keeping a timestamped `.bak` beside it. A file written by a *newer* version is refused with a
  clear message rather than being silently downgraded.
- `currency` — see [Currency](#currency).
- `tax` — see [Tax](#tax).
- `date_format` — strftime pattern for the date on the receipt and in filenames, e.g.
  `"%d %b %Y"` → `31 Jan 2026`, `"%Y-%m-%d"` → `2026-01-31`.
- `receipt_types` — see [Receipt types](#receipt-types).
- `invoice` — see [Invoice numbering](#invoice-numbering).
- `terms_page` — `{"enabled": true}`. Set `false` to drop the Warranty & Returns page; edit its
  wording in `Templates/terms.html`.
- `document` — PDF page margins. `margin_top` / `margin_bottom` must reserve room for the page
  header and footer, or Chromium clips them.
- `render` — `block_external_requests` (default `true`; receipts render offline and cannot fetch
  from a CDN), `timeout_ms`, `fail_on_missing_image`.
- `links` — `terms_url`, `privacy_url` and `warranty_url`, linked from the receipt footer.
  Set them under **Tools → Settings → Links**. Only `http://`, `https://` and `mailto:` are
  accepted, since the link is embedded in a PDF that goes to customers.
  **An unset link still prints its words, just without the hyperlink** — a receipt never carries a
  link to nowhere. The links are genuinely clickable in the generated PDF.
- `fonts` — see [Fonts](#fonts).

### Currency

```json
"currency": {
  "symbol": "$",
  "symbol_space": false,
  "code": "USD",
  "decimals": 2,
  "position": "prefix",
  "group_style": "thousand",
  "negative_style": "minus",
  "group_line_amounts": true
}
```

- `symbol_space` — put a space between symbol and number (`Rs. 12.00` vs `$12.00`).
- `position` — `prefix` or `suffix` (`12.00 kr`).
- `decimals` — 0 to 6. Use `0` for currencies without minor units.
- `group_style` — `thousand` (`1,234,567`), `indian` (`12,34,567`, the lakh/crore convention),
  or `none` (`1234567`).
- `negative_style` — `minus` (`-$5.00`) or `parentheses` (`($5.00)`).
- `group_line_amounts` — whether digit grouping also applies inside the item table. Versions
  before this one grouped the totals but not the line amounts, so **upgrading installs keep
  `false`** to leave existing receipts looking the same. Set it to `true` for consistent
  formatting throughout.
- `code` — shown on the amount fields in the app window; it does not appear on the receipt.

Amounts are computed in decimal, never binary floating point. Each line is rounded to `decimals`
and the **rounded** values are summed, so the figures on the page always add up.

### Tax

Document-level tax, applied on top of the per-line `Tax` column:

```json
"tax": {
  "mode": "exclusive",
  "rows": [
    { "label": "VAT 15%", "type": "percent", "value": 15, "applies_to": "subtotal_after_discount" }
  ]
}
```

- `mode: "exclusive"` — tax is **added** to the subtotal.
- `mode: "inclusive"` — the prices you enter already contain the tax, so it is **backed out and
  reported** rather than added. Those rows are labelled *(included)* so the total still reads
  correctly. With several inclusive percentage rows the combined rate is backed out once, so the
  rows do not compound against each other.
- `type` — `percent` or `fixed`.
- `applies_to` — `subtotal_after_discount` (default) or `subtotal`. Ordering is fixed: discounts
  come off first, then tax applies to the result.

Leave `rows` empty for no document-level tax.

### Receipt types

Each type keeps its own invoice-number series, identified by `code`:

```json
"receipt_types": [
  { "label": "Online",   "code": "W", "badge_text": "ONLINE ORDER", "legacy_unlettered": true },
  { "label": "In Store", "code": "S", "badge_text": "IN-STORE SALE" }
]
```

`label` is what the app's dropdown shows, `badge_text` is printed on the receipt, and `code`
becomes part of the invoice number (`INV-W1001`). Add an entry to add a type.

**Changing an existing `code` starts a new number series** — previously issued `INV-W####` files
stop being counted, so the next number would restart. `legacy_unlettered` marks the one series
that also counts pre-versioning `INV-####` files; only one type may claim them.

### Invoice numbering

```json
"invoice": {
  "prefix": "INV-",
  "start": 1001,
  "counter_file": "invoices/.counters.json",
  "reconcile_with_filenames": true
}
```

Each receipt type has its own series (`INV-W1001`, `INV-S1001`). The counter file holds the next
number for each and is created automatically, **seeded from the highest number already present in
`invoices/`** — so upgrading does not shift your sequence.

Earlier versions worked the number out by scanning PDF filenames each time. That is safe only
while filenames are fixed: once they are configurable, a renamed file the scanner cannot parse
makes it find nothing and restart at `start`, reissuing numbers already on customers' receipts.
The counter file now owns the sequence and the filename scan is only a cross-check.

Two consequences worth knowing about:

- **A number is claimed before the receipt is rendered**, so two open copies of the app — or the
  app and a command-line run — can never be handed the same one. If generation then fails, the
  number is **not** reused; giving it back would reopen that race. This leaves occasional gaps,
  and every one is recorded in the log with the reason.
- **Deleting or renaming a receipt does not free its number.** The counter only ever moves
  forward. If it finds files *ahead* of it (a restored backup, say) it jumps forward to match, and
  logs that it did.

Set `reconcile_with_filenames` to `false` to skip the cross-check entirely and let the counter
alone decide. Changing `prefix` starts a fresh series, since existing files stop matching.

If the counter file is ever corrupted the app refuses to run rather than starting a new sequence —
delete it to have the sequence rebuilt from the receipts on disk.

### Line-item columns and warranty (`fields.json`)

`fields.json` sits beside `appsettings.json` and defines the columns of the item table. The list
order is the column order, so adding, renaming, hiding or reordering a column needs no code:

```json
{
  "line_item_fields": [
    { "key": "sku",   "label": "SKU",        "type": "text",     "enabled": true },
    { "key": "bay",   "label": "Bay",        "type": "text",     "enabled": true },
    { "key": "qty",   "label": "Qty",        "type": "integer",  "enabled": true },
    { "key": "price", "label": "Unit Price", "type": "amount",   "enabled": true },
    { "key": "tax",   "label": "Tax",        "type": "amount",   "enabled": true,
      "optional_column": true },
    { "key": "amount","label": "Amount",     "type": "computed", "enabled": true }
  ]
}
```

- `type` decides both validation and presentation: `amount` and `computed` are right-aligned and
  formatted with your currency, `integer` and `number` are right-aligned, everything else is
  left-aligned and escaped. The full set is `text`, `multiline`, `integer`, `number`, `amount`,
  `computed`, `date`, `select`, `boolean`, `phone`, `email`.
- `enabled: false` hides a column.
- `optional_column: true` shows it only when at least one line actually uses it — which is why an
  unused Discount or Tax column stays off the receipt.
- `key` becomes a template placeholder, so it must be letters, digits and underscores.
- `required: true` blocks saving the item until the field is filled in (the label gets a `*`).
- `sticky: true` remembers the last value entered and pre-fills it on the next item — useful for
  a field that repeats across a whole sale. Remembered values live in `state.json`, which is
  machine state rather than configuration.
- `default` pre-fills a new item when nothing is remembered.

Columns are also the **item form**: adding a field here adds a row to the Add/Edit Item dialog,
with a widget picked from its type — a dropdown for `select`, a checkbox for `boolean`, a
validated entry for numbers.

Hiding a *built-in* (`qty`, `price`, `amount`) removes its column from the receipt but keeps it on
the form, since the totals still need the value. Hiding a *custom* field removes it from both.

**`qty`, `price` and `amount` can be hidden but not removed** — the totals are calculated from
them. Deleting one is refused at startup with an explanation rather than producing a receipt whose
figures do not add up.

#### Receipt-level fields

`receipt_fields` adds lines under the "Bill To" box — a PO number, a salesperson, a vehicle
registration, a deposit already paid:

```json
"receipt_fields": [
  { "key": "po_number",   "label": "PO Number",    "type": "text",   "enabled": true },
  { "key": "salesperson", "label": "Served by",    "type": "text",   "enabled": true },
  { "key": "deposit",     "label": "Deposit paid", "type": "amount", "enabled": true }
]
```

They take the same options as line-item fields, and an `amount` is formatted with your currency.
**A field left empty prints nothing at all** — no dangling label. Style the block through
`.receipt-fields`, `.receipt-field` and `.field-label` in `Templates/styles.css`, or restructure
it in `Templates/field_row.html`.

A key may not be used in both lists: they share one render context, so it would be ambiguous.

#### Warranty

```json
"warranty": {
  "enabled": true,
  "label": "Warranty",
  "none_option": "No Warranty",
  "options": [
    "# Months Limited Warranty",
    "7 Days Checking Warranty",
    "No Warranty"
  ]
}
```

An option containing `#` prompts for a whole number when chosen, so one entry covers 12 months,
24 months and anything else — `# Year International Warranty` works just as well. The number must
be positive; blank, `0`, negative and non-numeric are rejected while the dialog stays open.

`none_option` names the choice that means "no warranty on this line", and no note is printed for
it. Reword it (in any language) and the receipt follows.

The warranty is printed under the item description rather than in a column of its own. Set
`enabled: false` to drop it from the item dialog entirely.

### Wording

Words the app itself composes — column headings, the totals row labels, the marker printed in an
empty cell — live in `strings.json` beside `appsettings.json`, created on first run. A partial
file is fine; anything absent falls back to the default, so a translation only needs the keys it
changes. Wording that belongs to the layout is in the templates instead.

Settings are validated at startup. A bad value is reported with the exact file and key instead of
failing halfway through a receipt; `python cli.py --check` runs the same validation on demand.

`logo_path` can be:

- a file beside the executable, such as `logo.png`
- a relative path under the executable folder, such as `assets/logo.png`
- an absolute path, such as `C:\\Business\\logo.png` (Windows) or `/home/user/logo.png` (Linux)

If `logo_path` is empty the logo is simply skipped. If it is **set but the file cannot be found**,
the receipt still renders without it and the app says so — in the log, and in
`python cli.py --check`:

```text
logo     WARN The logo file was not found, so it will not appear on receipts.
              Configured as: logo.png
              A file with a very similar name is there: logo.png.png.
```

That near-miss is worth knowing about. **Windows hides known file extensions**, so saving or
renaming a file as `logo.png` often produces `logo.png.png` while Explorer still displays it as
`logo.png`. If your logo is not appearing, run `--check` first — this is almost always why.

Set `render.fail_on_missing_image` to `true` to treat a missing image as an error instead of a
warning, so a receipt is never issued without its logo.

## PDF Filename Config

Edit `filename_config.json` to choose which fields are added to generated PDF names.

The invoice/receipt number is always first. Optional fields are added after it with hyphens between each part.

Available optional fields:

- `date`
- `name`
- `email`
- `phone`

Example:

```json
{
  "filename_fields": ["date", "name", "phone"],
  "available_fields": ["date", "name", "email", "phone"]
}
```

That creates names like:

```text
INV-W1001-06 May 2026-Walk-in Customer-000-000-0000.pdf
```

## Templates

The receipt layout lives in editable HTML/CSS files under `Templates/`, rendered by a small
placeholder engine. In a packaged install the folder is created beside the executable the first
time you run the app; edit those files and the next receipt reflects the change — no rebuild.

| File | What it controls |
|---|---|
| `styles.css` | All receipt styling |
| `base.html` | The document skeleton that pulls the blocks together |
| `receipt_info.html` | Title, type badge, receipt number/date, "Bill To" box |
| `items_table.html` | The line-item table shell |
| `item_header_cell.html` / `item_row_cell.html` | One column heading / one cell |
| `totals.html` / `totals_row.html` | Totals block and one breakdown row |
| `terms.html` | The Warranty & Returns page printed after the receipt |
| `header.html` / `footer.html` | The repeating PDF page header and footer |

### Template syntax

Three constructs, deliberately no more — no loops, no expressions, no arbitrary code:

- `{{key}}` — insert a value, escaped for HTML
- `{{key|raw}}` — insert without escaping (reserved for fragments the app builds)
- `{{#if key}}…{{/if}}` — include the block only when `key` has a value

Business placeholders in `header.html` / `footer.html` come from `appsettings.json`:
`{{company_name}}`, `{{company_address}}`, `{{company_phone}}`, `{{company_email}}`,
`{{company_logo}}`.

Templates are checked when the app starts. A misspelt placeholder or an unclosed `{{#if}}` is
reported with the file and line rather than silently leaving a blank space on a receipt. To check
without launching the GUI:

```bash
python cli.py --check
```

### Upgrading

`Templates/.installed.json` records each file's hash at the moment it was copied. That is how a
future version can tell a file you edited from one you never touched — your edits are never
silently overwritten. Deleting a template restores it from the bundled copy on the next run.

### Fonts

By default the receipt uses Helvetica/Arial, so a system font substitution can make the same
receipt look slightly different on another machine. To pin it, put an OFL-licensed font under
`Templates/fonts/` and name it in `appsettings.json`:

```json
"fonts": {
  "family": "Inter",
  "files": ["Templates/fonts/Inter-Regular.woff2"],
  "fallback": "Helvetica, Arial, sans-serif"
}
```

The font is embedded in the PDF as base64, so rendering still works offline. No font ships with
the app; leave `family` empty to keep the current behaviour.
