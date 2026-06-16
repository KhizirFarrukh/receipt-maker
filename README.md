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
- A second page with the Warranty & Returns Policy is appended to every receipt.
- `header.html` and `footer.html` are rendered as real PDF page headers and footers on every page.
- The receipt body is laid out inside the reserved PDF content area, so it does not overlap the header or footer.

## Requirements

- Python 3.8 or newer
- tkinter (usually needs a separate system package on Linux)
- Playwright
- Chromium installed through Playwright

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
