# Backlog — planned work, not yet started

The durable record of what is still to do. Anything agreed but not built belongs here, so it
survives between sessions.

- **This file** = future work, in priority order.
- [`claude_chat/TASKS.md`](claude_chat/TASKS.md) = the working checklist for whatever is currently
  in progress, with the reasoning behind decisions already made.
- [`claude_chat/PLAN-generalization.md`](claude_chat/PLAN-generalization.md) = the original
  approved architecture plan. **Note it is now partly superseded** — its "no GUI settings editor"
  non-goal was reversed by the in-app editing work.

Last updated: 2026-08-26.

---

## 1. Product barcode — **DONE**

A **product barcode** (EAN/UPC/GTIN), separate from the serial number. The two are different
things and do not share a field:

- **Serial number** identifies *this one physical unit* — unique per item sold.
- **Barcode** identifies *the product* — every unit of it carries the same code.

- [x] `barcode` ships as a built-in line-item field, **disabled by default** so existing receipts
      do not change. Enable it under **Tools → Fields & Columns**.
- [x] An existing `fields.json` gains it by migration (added next to SKU, disabled). A field
      deleted *after* that migration stays deleted — the version stamp moves with the file.
- [x] Scanner support: Enter in an item field advances to the next field rather than submitting,
      so a scanner that types-then-Enters cannot save a line containing only a barcode.
- [ ] Once the catalogue exists (§2), scanning a barcode should fill the whole line.

## 2. Product catalogue and inventory  *(large — the main new feature)*

Pick a product instead of retyping its details on every receipt.

**Per product:**

| Field | Meaning |
|---|---|
| `sku` | Internal code |
| `barcode` | Product barcode (EAN/UPC/GTIN) |
| `name` | Product name |
| `list_price` | Price of a single item |
| `cost_price` | What it cost to buy in |
| `bulk_price` | Price when selling in quantity |
| `sell_price` | Computed — see §3 |
| `stock_count` | Units in stock |
| `serial_numbers` | Serials of the units held |

- [ ] **Variants.** A product has variants (size, colour, capacity) and each variant carries its
      own sku, barcode, stock and prices. Worth settling early whether a variant is a full product
      with a parent, or a lightweight child — retrofitting that later is painful.
- [ ] **Pick a product** in the item dialog, by name, SKU or barcode, filling the line in one step.
- [ ] Manage the catalogue in-app (add, edit, search, import/export).

### Decisions to settle before building

**Storage — JSON or SQLite?** Everything else in this app is hand-editable JSON, and that has
worked well. But a catalogue is a *database*: it wants lookup by three different keys, it grows to
thousands of rows, and stock counts are written far more often than settings are.

- *JSON* keeps it inspectable and consistent with the rest of the app; fine into the low thousands
  of products, and easy to back up or diff.
- *SQLite* handles size and concurrent access properly and makes barcode lookup instant, at the
  cost of no longer being readable in a text editor.

**Recommendation:** SQLite for the catalogue, with JSON/CSV import and export so nothing is
locked in. Worth a decision before any code is written.

**Stock counting has the same trap as invoice numbers.** If generating a receipt decrements stock,
then: what happens when generation fails after the decrement? When a receipt is deleted? When one
is edited later from history? Invoice numbering settled this with *reserve-and-keep* plus a logged
audit trail; stock needs its own explicit answer rather than inheriting one by accident.

**Serial numbers are a list, not a count.** Selling one unit should remove *that* serial from
stock, which means the item dialog needs to offer the serials actually held.

## 3. Sell price from cost — margin / markup / discount modes

- [ ] Compute `sell_price` from `cost_price` or `list_price` by a chosen mode.

**Margin and markup are not the same thing**, and treating them as such is a common and expensive
pricing mistake — so the UI must name which one it is using:

- **Markup** — on top of cost: `sell = cost × (1 + markup%)`. Cost 100 at 25% → **125**.
- **Margin** — a share of the sale price: `sell = cost ÷ (1 − margin%)`. Cost 100 at 25% → **133.33**.
- **Discount** — off the list price: `sell = list × (1 − discount%)`.

- [ ] Show the resulting margin *and* markup side by side, so the number can be sanity-checked.
- [ ] Round to the configured currency precision, using the existing Decimal arithmetic.

## 4. Carried over from the current work (see TASKS.md Phase H)

- [x] **H3 — receipt history. DONE.** Tools → Receipt History lists every generated receipt,
      searchable by number, customer, date, item or SKU, and loads one back into the form to
      correct and reissue. Stored as one JSON object per line in
      `invoices/.archive/history.jsonl`. The record outlives its PDF, and a reloaded receipt keeps
      its original number so correcting one consumes no new number.
      - [ ] *Still open:* a CSV **export** for spreadsheet use — easy on top of the JSON, and the
            right way round (JSON as the source of truth, CSV as a view).
- [ ] **H2 — save draft.** Store an in-progress receipt and restore it later. Consumes no invoice
      number.
- [ ] **H6 (remainder) — image signature.** A scanned signature image on the receipt. **It is
      decorative, not cryptographic** — the README must keep saying so, or it reads as equivalent
      to the real PAdES signature.
- [ ] **H7 — audit.** Once the editors are complete, remove every "edit this JSON file"
      instruction from the README that now has an in-app equivalent.

## 5. From the original plan, still outstanding

- [ ] **Stage 7 — diagnostics (`--doctor`).** The archive half is now covered by receipt history;
      what remains is the environment check: missing browser, unreadable key, expiring certificate,
      read-only output folder.
- [ ] **Stage 8 — polish.** Configurable filename patterns (and the validation that a pattern must
      contain the invoice-number token), reprint from archive with a DUPLICATE badge, "restore
      default templates", pinned Playwright version.
- [ ] **Neutral defaults.** `Templates/terms.html` still carries Chawla Tech wording. It is an
      ordinary editable template now, so this is a content edit — but the shipped default should be
      generic. (`footer.html` was neutralised when the policy links landed.)
- [ ] **No bundled font.** The `@font-face` embedding works and is off by default; no OFL font ships
      with the app, so a receipt can still look slightly different on another machine.
- [ ] **`document.title`.** "SALES RECEIPT" is a literal in `receipt_info.html` — editable, but
      never became a config key.
- [ ] **Main-window form.** The item dialog builds itself from `fields.json`; the customer form at
      the top of the main window is still a fixed layout, so receipt-level custom fields cannot yet
      be typed in.
