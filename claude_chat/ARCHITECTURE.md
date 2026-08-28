# Architecture — modules, data, and the rules that must hold

Companion to [HANDOFF.md](HANDOFF.md). This is what the code *is*; [DECISIONS.md](DECISIONS.md) is
why it is that way.

## The layering rule

**`config`, `template_engine`, `receipt_render`, `receipt_service`, `product_catalogue`,
`receipt_history`, `invoice_counter`, `receipt_signing` and `cli` must never import tkinter.**

Only `main.py` and `settings_ui.py` are GUI. Two tests assert this by importing the render path in
a subprocess and checking `tkinter` is absent from `sys.modules`. It is what lets the golden gate
and most of the suite run without a display.

## Modules

| Module | Responsibility |
|---|---|
| `main.py` | The tkinter GUI. Form, item dialog, menus, threaded generation behind a modal progress dialog. |
| `settings_ui.py` | Every in-app editor: Settings, Fields & Columns, Signing Keys, Receipt History, Products, and the product picker. |
| `config.py` | All configuration. Paths, schema version, migration, validation, atomic writes, `strings.json`, `fields.json`, `state.json`. |
| `template_engine.py` | The deliberately dumb placeholder engine. `{{key}}`, `{{key\|raw}}`, `{{#if}}`, dotted keys. Compile-time linting. |
| `receipt_render.py` | Builds the receipt HTML from `Templates/` + config + data. Owns money formatting and the arithmetic. |
| `receipt_service.py` | Headless orchestration: numbering, filenames, Playwright render, signing, history, stock. |
| `invoice_counter.py` | The invoice sequence. Cross-process locking, reserve-and-keep, reconciliation. |
| `receipt_signing.py` | PAdES signing, verification, key generation and import. No GUI, no config coupling. |
| `receipt_history.py` | The record of every generated receipt, and reloading one. |
| `product_catalogue.py` | Products, variants, lookup, pricing arithmetic, stock deduction. |
| `cli.py` | Headless entry point. `--render-html` (the golden target), `--check`, `--config-dir`. |
| `keygen.py`, `verify_receipt.py` | Command-line signing helpers; the reference verifier. |

## Data files

All live beside the executable (`APP_DIR`). All are hand-editable; the in-app editors are a front
end onto the same loaders.

| File | Contents | Tracked? |
|---|---|---|
| `appsettings.json` | Company, currency, tax, dates, receipt types, invoice, signing, links, UI, inventory, render, fonts | yes |
| `fields.json` | Line-item and receipt fields, warranty options | yes |
| `strings.json` | Words the renderer composes (column headings, totals labels) | yes |
| `filename_config.json` | Which fields go into a PDF filename | yes |
| `products.json` | The product catalogue | **no** (user data) |
| `state.json` | Remembered sticky values | **no** (machine state) |
| `Templates/` | The receipt layout | yes |
| `invoices/` | Generated PDFs | **no** |
| `invoices/.archive/history.jsonl` | Receipt history — **contains customer PII** | **no** |
| `invoices/.counters.json` | The invoice sequence | **no** |
| `signing/` | Private key and certificate — **never commit, never bundle** | **no** |

## The render pipeline

```
data (dict)  +  Templates/  +  config
        │
        ▼
receipt_render.build_html()          loads templates (cached), then:
        └── render_receipt()         PURE: no clock, no IO, no globals
                 │                   everything non-deterministic is injected
                 ▼
            receipt HTML
                 │
receipt_service.render_pdf()         Playwright → PDF (external requests blocked)
                 │
        sign_receipt_pdf()           PAdES, into a .partial file
                 │
            os.replace()             atomic move into invoices/
                 │
        record stock → record history
```

`render_receipt` being pure is what makes the golden diff possible. Do not read config, the clock
or the filesystem inside it — inject those from `build_html`.

## Invariants — break these and something important breaks quietly

1. **The renderer is pure.** `render_receipt(data, templates, ...)` must not touch IO or the clock.
2. **The renderer never applies `default`/`sticky`.** Those are UI-side. Applying them would make
   the golden depend on `fields.json`.
3. **Money is `Decimal`, and JSON stores amounts as strings.** JSON numbers are floats.
4. **Each line is rounded, then the rounded values are summed** — so printed figures add up.
5. **`qty`, `price` and `amount` can be hidden but never removed.** The totals derive from them.
6. **`enabled` controls whether a column is *printed*, not whether it is *entered*.** A hidden
   built-in stays on the form; a hidden custom field leaves it.
7. **Invoice numbers are reserve-and-keep**; **stock is commit-on-success**. Opposite policies, on
   purpose — see DECISIONS.md.
8. **Reconciliation never moves the counter backwards.**
9. **Verification trusts retired certificates**, so rotating a key does not invalidate history —
   but only certificates this install actually archived.
10. **Nothing optional may fail a receipt.** History, stock and logging all warn and carry on. The
    signed PDF is the legal artifact.
11. **`row_to_item` / `item_to_row` in `main.py` are the only place** the item tree's positional
    storage maps to field keys. Drift there silently puts values in the wrong column.

## Config schema versions

`appsettings.json` is at **v4**. Migration runs on load, is persisted once, and keeps a `.bak`.

- v1 → v2: added `document`, `render` (page geometry and render policy left Python).
- v2 → v3: added `currency`. **Existing installs are seeded with the old hardcoded values**
  (`Rs.`, 2dp, ungrouped line amounts) so nobody's receipts silently change currency; only fresh
  installs get the neutral `$`.
- v3 → v4: added `invoice`, preserving the `INV-` prefix and start number verbatim.

`fields.json` is at **v2** (v2 added the product barcode, disabled).

A config written by a *newer* version is refused with a clear message rather than downgraded.
