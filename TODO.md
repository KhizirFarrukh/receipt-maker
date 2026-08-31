# Backlog — planned work, not yet started

The durable record of what is still to do. Anything agreed but not built belongs here, so it
survives between sessions.

- **This file** = future work, in priority order.
- [`claude_chat/HANDOFF.md`](claude_chat/HANDOFF.md) = **start here** — current state, environment,
  how to verify anything.
- [`claude_chat/ARCHITECTURE.md`](claude_chat/ARCHITECTURE.md) = modules, data files, invariants.
- [`claude_chat/DECISIONS.md`](claude_chat/DECISIONS.md) = why things are the way they are. Read it
  before changing something that looks odd.
- [`claude_chat/PITFALLS.md`](claude_chat/PITFALLS.md) = traps that have already cost time.
- [`claude_chat/TASKS.md`](claude_chat/TASKS.md) = historical session log.
- [`claude_chat/PLAN-generalization.md`](claude_chat/PLAN-generalization.md) = the original plan,
  **partly superseded**.

Last updated: 2026-08-31. 838 tests passing; golden gate green; packaged build verified.

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
- [ ] Once the catalogue exists (§2), scanning a barcode should fill the whole line. **Now
      specified in §6.8** — a scan adds the line outright and a rescan increments it.

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

- [x] **Variants — done, settled as a lightweight child that overrides its parent.** A variant
      states only what differs and inherits the rest, so nothing has to be kept in step by hand.
      Its `name` is the *label* ("Blue"), not a replacement product name — treating it as an
      override printed "Blue (Blue)" and lost the product.
- [x] **Pick a product** in the item dialog — search by name, SKU or barcode, or scan straight
      into the search box. Fills the line and leaves anything already typed alone.
- [x] Manage the catalogue in-app (**Tools → Products**), with the same validate-then-atomic-write
      path as the other editors: duplicate SKUs and barcodes are refused, because a scan has to
      identify exactly one product.
- [ ] Import/export (CSV) for bulk editing.
- [x] **Stock deduction — done, and settled the opposite way to invoice numbering.** Numbers are
      reserved *before* rendering and kept on failure, because a duplicate is unrecoverable. Stock
      is committed *after* the receipt exists, because it records that goods actually left — a
      failed render deducts nothing. Reissuing adjusts by the difference rather than deducting
      twice, and overselling is recorded (negative, with a warning) rather than refused, since
      blocking a sale over a stale count is worse than showing a number that prompts a recount.
      Off by default.
- [ ] Serial-number selection: sell a *specific* held serial rather than typing one.
      **Superseded in scope by §6.1** — a line of qty 3 needs *three* serials, not one.
- [ ] A low-stock warning at the point of sale, rather than only in the log.
- [ ] Voiding a receipt should return its stock — there is no "void" concept yet.

### Decisions to settle before building

**Storage — settled on JSON, revising an earlier recommendation.**

This file first recommended SQLite. On a closer look that was the wrong call, and the deciding
factor is the shape of the data rather than its size:

- **Variants and serial-number lists are nested and variable.** A product holds a list of serials
  and a list of variants that override some of the parent's fields. That is natural in JSON and
  needs three tables and joins in SQL. SQLite is strongest for flat, indexed rows — which this is
  not.
- **The scale does not justify it.** The plan lists multi-user as a non-goal, so this is one shop
  on one machine: hundreds to low thousands of products, where a whole-file read is microseconds.
- **It keeps the app's character.** Every other piece of data here is an inspectable file the user
  can back up, diff, or fix by hand, and the validated atomic-write-with-`.bak` machinery already
  exists to write it safely.

SQLite would win if this became multi-till or grew to tens of thousands of products with
concurrent writers. It is worth revisiting then; the JSON shape maps onto tables cleanly enough
that it would not be a rewrite.

**Stock counting — settled.** It looked like the invoice-numbering problem but wanted the opposite
answer, because the risk is different: a duplicate invoice number is unrecoverable, while a stock
figure can always be recounted. So stock commits *after* the receipt exists rather than being
reserved before it, a reissue adjusts by the difference, and overselling is recorded rather than
refused. Voiding a receipt is still not modelled.

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

## 6. Line-item detail, installments and receipt layout  *(agreed 2026-08-31)*

Ten requests that arrived together. They are grouped by what they touch rather than in the order
they were given, because several are the same change seen from different angles — §6.1 and §6.2
are one mechanism used twice, and §6.7's two halves are the same widening problem in the form and
on the page.

**Do §6.4 first.** It is the smallest of these and unblocks the most: it is what makes every other
per-line number trustworthy.

### 6.1 Several serial numbers per line — one per unit

Today `serial` is a single text box, so selling three of the same product forces three separate
lines.

- [ ] A line of qty *n* carries **n serial numbers**. SKU and barcode stay single — they identify
      the *product*, and every unit of it shares them.
- [ ] Keep the count and the list in step: raising qty should ask for more serials, lowering it
      must not silently discard ones already typed.
- [ ] Selling from the catalogue should offer the serials actually **held in stock** (§2) rather
      than a blank box, and remove those specific ones on sale.
- [ ] Decide how a partly-filled list behaves. Demanding all *n* before the line can be saved will
      be resented at a till; the likely answer is to allow gaps and warn, matching the way
      overselling was settled in §2.

### 6.2 A store-assigned per-unit ID

Serial number and barcode are the **manufacturer's** identifiers. `sku` is the store's own, but it
is per *product*. Nothing today is the store's own *and* per *unit*.

- [ ] A new **optional** per-unit field, one value per quantity — mechanically the same list as
      §6.1, so build one mechanism and use it twice.
- [ ] Off by default. A shop that does not label its own stock must never see it.
- [ ] It needs a key that cannot be confused with `serial` or `sku` in the field editor. The label
      is user-editable anyway, so the key is what matters.

### 6.3 Order notes — a paragraph, not a line

- [ ] A large multi-line text box for free-form notes about the order.
- [ ] `multiline` **already exists** as a field type in `config.FIELD_TYPES`, so storage and
      validation are there. What is missing is that receipt-level fields have nowhere to appear:
      the customer form at the top of the main window is still a fixed layout (§5). Doing this
      properly means building that form from `fields.json` — the same prerequisite §5 already
      lists, so do the two together.
- [ ] The receipt template needs somewhere for a paragraph to sit, and it must wrap rather than
      overflow.

### 6.4 A real per-line total — **do this one first**

`receipt_render.py:875` computes the line amount as `qty × price` and nothing else, and
`receipt_render.py:631` builds the subtotal the same way. A line carrying a discount or a tax
therefore shows a figure that is **not what that line actually came to** — the adjustments surface
only in the totals block far below.

- [ ] The line total must account for that line's own discount and tax, and for its installment
      plan (§6.5) where it has one.
- [ ] Keep the gross visible too. A customer shown only the net cannot check that the discount was
      applied — so likely both columns, each toggleable per §6.6.
- [ ] **Use the existing Decimal path**: round each line, then sum the rounded values. Do not
      re-derive it. That rule is an invariant (ARCHITECTURE.md), and breaking it makes a receipt
      disagree with itself by a penny.
- [ ] **This changes the golden file.** Regenerating it is correct here — but inspect the diff
      first and justify it in the commit (PITFALLS.md).

### 6.5 Installment plans

A plan is a **period**, a **down payment** and a **per-month amount**, which together come to a
different — larger — total than the cash price.

- [ ] Settable **per line** *or* **for the whole order**, and deliberately **not both**. Different
      lines may carry different plans: 3 months on one, 6 on another, at different prices.
- [ ] Enforce that exclusivity in the model, not merely by hiding a control. Two live plans on one
      receipt produce a total nobody can reconstruct.
- [ ] Show the arithmetic on the receipt — cash price, down payment, *n* × monthly, financed
      total. A plan the customer cannot check is worse than no plan at all.
- [ ] **Toggleable off entirely** (§6.6). A shop that never offers installments must not be asked
      about one on every receipt.

**Settle before building:** does the financed total become *the* receipt total, or does the receipt
show the cash price with the plan beside it? That decides what the tax rows apply to, so choosing
wrong misstates tax. Ask rather than assume.

### 6.6 Everything optional is toggleable

- [x] **Line-item fields already are.** `sku`, `barcode`, `serial`, `discount`, `tax` and the rest
      each carry an `enabled` flag, edited under **Tools → Fields & Columns**, and `barcode` ships
      disabled. So for line items this is largely done.
- [ ] **Audit what is not.** The shipping fee is a label at `config.py:375`, not a toggleable
      field. Confirm each of discount, tax, shipping, sku and serial can genuinely be turned off
      *and that the receipt still renders correctly without it* — a disabled column that leaves a
      gap, or a totals line that still prints, is worse than having no toggle.
- [ ] Every new field from §6.1–§6.5 arrives toggleable and defaults **off**, so no existing
      receipt changes shape.

### 6.7 Taller rows, and page breaks that respect a line

Once a line carries several serials, per-unit IDs and an installment plan, it no longer fits on one
text row.

- [ ] **Grow the row to fit its data** rather than squeezing everything into one line. Wrapping —
      not truncation, and not a horizontal squeeze.
- [ ] **Never split a product line across a page break** — unless it began at the top of a page
      (header excluded) and *still* will not fit, in which case it must break, because there is
      nowhere better for it to go.
- [ ] **Make that a toggle**, so someone who prefers tight pages can allow mid-line breaks.

**Check before building:** `break-inside: avoid` on the row may already give exactly the described
behaviour — that is close to its defined semantics, since a box that fits on no page has to break
rather than be pushed forever. If it does, the work is the toggle and a test that proves it, not
new layout logic. Verify against a real multi-page PDF before designing anything more elaborate.

### 6.8 Scan a barcode to add or increment a line

- [ ] Scanning a known barcode **adds a line** for that product, qty 1, filled from the catalogue.
- [ ] Scanning the **same barcode again increments that line's quantity** instead of adding a
      second line.
- [ ] The user then edits the line to supply what a scan cannot know — the serials (§6.1) above
      all.
- [ ] An **unknown** barcode needs a defined answer: offer to create the product, or add a bare
      line? It must not fail silently. At a till, a scan that does nothing looks like a broken
      scanner.
- [ ] Scanner input is keystrokes ending in Enter. §1 already stops Enter from submitting the item
      dialog; the same care is needed wherever the main window accepts a scan.
