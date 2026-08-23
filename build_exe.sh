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

# Templates are NOT copied here on purpose -- the app seeds $DIST_DIR/Templates
# from the bundled copies on first run and records their hashes at that moment,
# which is what later tells a user's edit apart from an untouched default.
cp filename_config.json appsettings.json "$DIST_DIR/"

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
# Bounded, so a build can never wedge on a hung packaged binary.
if ! timeout 180 "$DIST_DIR/ReceiptGenerator" --smoke-test; then
    echo "ERROR: Packaged executable smoke test failed or timed out." >&2
    tail -n 30 "$DIST_DIR/logs/receipt-maker.log" 2>/dev/null || true
    exit 1
fi
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
