# Receipt Generator

A Python tkinter desktop app for generating A4 PDF sales receipts.

## Features

- Separate invoice number series for online (`INV-W####`) and in-store (`INV-S####`) purchases. The number is editable and defaults to one above the highest previously generated invoice in that series.
- Customer, item, serial number, quantity, price, per-line discount, per-line tax, and warranty fields, plus a global shipping fee.
- Receipt totals show a Subtotal / Taxes / Discounts / Shipping / Total breakdown; taxes, discounts, and shipping rows (and the Discount/Tax columns) appear only when used.
- Auto-incrementing receipt numbers saved under `invoices/`.
- Configurable PDF filenames through `filename_config.json`.
- Configurable business/header details through `appsettings.json`.
- PDF generation through Playwright/Chromium.
- Every receipt is digitally signed (PAdES) with your private key, so a forged or edited receipt fails verification against your public certificate. See [Receipt Authenticity](#receipt-authenticity-digital-signatures).
- A second page with the Warranty & Returns Policy is appended to every receipt.
- `header.html` and `footer.html` are rendered as real PDF page headers and footers on every page.
- The receipt body is laid out inside the reserved PDF content area, so it does not overlap the header or footer.

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

Run this **once** to create your key pair:

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

Once the key exists, every receipt you generate is signed automatically. The footer of
each receipt reads *"This receipt is digitally signed … Verify its authenticity at
chawlatech.pk/verify."*

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
  "signer_name": "Chawla Tech",
  "reason": "Receipt authenticity",
  "location": "chawlatech.pk",
  "tsa_url": ""
}
```

- `enabled` — set to `false` to generate unsigned receipts (legacy behavior).
- `key_passphrase` — only needed if you created the key with `--passphrase`.
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

The build does **not** include your signing key — the `signing/` folder is created beside the executable when you run `keygen.py`. Keep it on your own machine and do not copy it into any `dist/` folder you share, so your private key is never distributed.

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

`logo_path` can be:

- a file beside the executable, such as `logo.png`
- a relative path under the executable folder, such as `assets/logo.png`
- an absolute path, such as `C:\\Business\\logo.png` (Windows) or `/home/user/logo.png` (Linux)

If `logo_path` is empty or the file is not found, the logo image is skipped and the receipt still renders.

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

## Branding

Edit `header.html` and `footer.html` to change the repeating PDF header and footer layout. Business placeholders are filled from `appsettings.json`:

- `{{company_name}}`
- `{{company_address}}`
- `{{company_phone}}`
- `{{company_email}}`
- `{{company_logo}}`
