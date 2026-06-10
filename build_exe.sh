#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python -m pip install -r requirements-build.txt

PLAYWRIGHT_BROWSER_DIR="${HOME}/.cache/ms-playwright"
if [ -d "$PLAYWRIGHT_BROWSER_DIR" ] && ls "$PLAYWRIGHT_BROWSER_DIR"/chromium-* &>/dev/null 2>&1; then
    echo "Playwright Chromium already installed."
else
    python -m playwright install chromium
fi

python -m PyInstaller --clean --noconfirm receipt_maker.spec

DIST_DIR="dist/ReceiptGenerator"

cp filename_config.json "$DIST_DIR/"
cp appsettings.json "$DIST_DIR/"

mkdir -p "$DIST_DIR/invoices"

cat > "$DIST_DIR/README.txt" << 'EOF'
Receipt Generator

Run ./ReceiptGenerator to open the app.

Keep this whole folder together. The _internal folder contains the bundled runtime and Chromium files required for PDF generation.

Edit filename_config.json to control optional filename fields. The receipt/invoice number is always included first.

Edit appsettings.json to control the business name, address, phone, email, and logo path shown on receipts.

Generated PDFs are saved in the invoices folder beside the executable.
EOF

SMOKE_PDF="$DIST_DIR/invoices/_packaged_smoke_test.pdf"
rm -f "$SMOKE_PDF"
"$DIST_DIR/ReceiptGenerator" --smoke-test
if [ ! -f "$SMOKE_PDF" ]; then
    echo "ERROR: Packaged executable smoke test did not create a PDF." >&2
    exit 1
fi
rm -f "$SMOKE_PDF"

echo ""
echo "Executable created:"
echo "$(pwd)/$DIST_DIR/ReceiptGenerator"
echo ""
echo "Keep appsettings.json and filename_config.json beside the executable so users can edit business details and filename options."
