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
- [x] **Import/export (CSV) — DONE.** Tools → Products → Import/Export CSV. Variants become
      their own rows carrying a `parent_sku` and serial numbers share a `;`-joined cell, a
      flattening that undoes exactly — which is what makes import safe. Import **merges by
      SKU** by default and says so in the prompt: a CSV is usually a partial list (this week's
      stock, a supplier's price update) and replacing wholesale would delete everything not in
      it. Written with a UTF-8 BOM, or Excel mangles every accented name.
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
- [x] **Voiding a receipt — DONE.** Tools → Receipt History → **Void…** marks a receipt void
      and puts its stock back. The two halves pull opposite ways on purpose: the stock
      returns, because a count can be recounted and goods that were never sold are still on
      the shelf; the invoice number does **not**, because a number that has been on a
      customer's receipt cannot be un-issued. The gap in the sequence is explained by the
      void record rather than avoided. Append-only — the original entry is untouched, since
      issued-then-cancelled is two facts and not one corrected fact — and the PDF is left
      where it is, because the customer may still be holding it.

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
refused. **Voiding now applies the same asymmetry in reverse:** the stock comes back, the number
does not. It reuses `stock_deltas` with an empty sale against what the receipt took, so the deltas
come out negative and the same tested path that removed the stock returns it — rather than a second
implementation that could disagree with the first.

**Serial numbers are a list, not a count.** Selling one unit should remove *that* serial from
stock, which means the item dialog needs to offer the serials actually held.

## 3. Sell price from cost — margin / markup / discount modes — **DONE**

- [x] **Tools → Products → "Work out a sell price…"**. Pick a product, choose the mode, get
      the price. The arithmetic and its tests were already here; what was missing was any way
      to reach them, and doing the sum by hand is where the mistake below gets made.

**Margin and markup are not the same thing**, and treating them as such is a common and expensive
pricing mistake — so the UI must name which one it is using:

- **Markup** — on top of cost: `sell = cost × (1 + markup%)`. Cost 100 at 25% → **125**.
- **Margin** — a share of the sale price: `sell = cost ÷ (1 − margin%)`. Cost 100 at 25% → **133.33**.
- **Discount** — off the list price: `sell = list × (1 − discount%)`.

- [x] The result panel shows the margin **and** the markup whichever mode was used. That is the
      check that catches a margin typed in as a markup, which silently under-prices everything.
- [x] Rounds to the configured currency precision. A 100% margin is refused rather than
      dividing by zero, and so is any figure that lands at or below nothing — that is not a
      sale.

## 4. Carried over from the current work (see TASKS.md Phase H)

- [x] **H3 — receipt history. DONE.** Tools → Receipt History lists every generated receipt,
      searchable by number, customer, date, item or SKU, and loads one back into the form to
      correct and reissue. Stored as one JSON object per line in
      `invoices/.archive/history.jsonl`. The record outlives its PDF, and a reloaded receipt keeps
      its original number so correcting one consumes no new number.
      - [x] *CSV export — DONE.* Tools → Receipt History → Export CSV, one row per line item,
            which is the shape worth pivoting on. **Export only, deliberately:** the history is an
            append-only record of what happened, and reassembling receipts from spreadsheet rows
            would mean inventing a rule for it. A search narrows the export, so a quarter's sales
            is a search away.
- [x] **H2 — save draft. DONE.** *Save Draft* and *Drafts…* beside Generate. A sale that gets
      interrupted keeps its work without issuing a receipt for a sale that has not happened.
      **Consumes no invoice number** — that is the whole rule: numbers are reserved when a receipt
      is *generated*, because a duplicate is unrecoverable, and a draft is not a receipt. The
      number showing in the box is stored as `suggested_inv_no` rather than `inv_no`, so nothing
      downstream can mistake a draft for something that has been numbered, and it is offered again
      on restore. Stored in `drafts.json` (gitignored: it holds customer details).
- [x] **H6 — image signature. DONE.** A scanned signature at the foot of the receipt, under
      **Settings → Advanced**. It is **decorative, not cryptographic**, and the whole design is
      arranged around not letting anyone believe otherwise: the config key is `signature_image`
      rather than `signature`, the settings help opens with "DECORATIVE ONLY … proves nothing",
      and the README carries a table contrasting it with the PAdES signature line by line. There
      are tests asserting each of those, because the risk here is a false sense of security rather
      than a rendering bug.
- [ ] **H7 — audit.** Once the editors are complete, remove every "edit this JSON file"
      instruction from the README that now has an in-app equivalent.

## 5. From the original plan, still outstanding

- [x] **Stage 7 — diagnostics (`--doctor`). DONE.** `python cli.py --doctor` checks the things
      `--check` does not: a browser to render with, a folder to write into, a counter to number
      from, a key to sign with, and the data files. Every check runs — a doctor that stops at the
      first problem makes you run it four times — and a **warning is not a failure**: a missing
      logo or an expiring certificate reports and still exits 0, so the command stays usable in a
      build script. `EXIT_ENVIRONMENT` (6) is its own code so a script can tell it from a config or
      render failure.
- [x] **Stage 8 — polish. DONE.**
      - **Filename patterns.** `invoice.filename_pattern`, e.g. `{invoice_no}-{date}-{name}`,
        with `{email}`, `{phone}` and `{receipt_type}` too. Empty keeps today's names exactly, and
        the old `filename_config.json` field list is now read as a way of *writing* a pattern, so
        there is one mechanism rather than two. A pattern **must contain `{invoice_no}`** — it is
        the only part guaranteed unique, and without it two receipts on one day for one customer
        overwrite each other. An unknown placeholder is refused with the real ones listed.
      - **DUPLICATE on a reissue.** Decided by the history rather than a checkbox: a second PDF for
        a number already issued *is* a second copy. It reuses the lookup stock already needed, so
        history is read once.
      - **`document.title`.** The receipt heading is `strings.json → totals.document_title` instead
        of a literal in `receipt_info.html`.
      - **Restore default templates** (Tools). The way back from an edit that broke rendering.
        Copies what it replaces into a dated folder first — this is the recovery tool, so it must
        not itself lose work.
      - **Pinned dependencies.** `playwright`, `pyhanko` and `cryptography` are pinned exactly.
        The Playwright version *is* the Chromium version, and a different Chromium lays out a PDF
        differently, so a loose range means one receipt can look different on two machines.
- [x] **Neutral defaults — DONE, without changing anybody's receipts.** The terms page's *file*
      is now configurable (`terms_page.template`), so the shipped `terms.html` could be made
      generic while the existing wording moved to `terms.chawlatech.html` with `appsettings.json`
      pointing at it. Receipts are byte-identical; a fresh clone no longer prints another
      business's policy, phone number and support email.
      - [ ] *One step left, and it is a repo decision rather than code:* `appsettings.json`,
            `fields.json` and `filename_config.json` are **tracked**, so a clone still inherits
            this shop's settings — including that pointer. If this repo is ever published, untrack
            them and ship `.example` copies. If it stays private, nothing needs doing.
- [ ] **No bundled font.** The `@font-face` embedding works and is off by default; no OFL font ships
      with the app, so a receipt can still look slightly different on another machine.
- [ ] **`document.title`.** "SALES RECEIPT" is a literal in `receipt_info.html` — editable, but
      never became a config key.
- [x] **Main-window form — DONE, as a prerequisite of §6.3.** The customer form is built from
      `fields.json` now, so receipt-level custom fields can be typed in as well as printed.

## 6. Line-item detail, installments and receipt layout — **DONE** *(2026-08-31)*

Twelve requests: ten agreed together, then §6.9 and §6.10 straight after. They are grouped by what they
touch rather than in the order they were given, because several are the same change seen from
different angles — §6.1 and §6.2 are one mechanism used twice, and §6.7's two halves are the same
widening problem in the form and on the page.

All twelve are built, tested and shipped. The three questions that were open were answered by
the user; two more were settled here and are recorded in the sections that made them.

Everything new defaults **off**, so upgrading changes no existing receipt. Each switch lives in
**Tools → Settings** or **Tools → Fields & Columns** — none of it needs a JSON file edited, which
was the standing request behind all of this.

**What this batch cost, honestly:** four bugs were found while building it, three of them
pre-existing and none of them the feature being worked on. They are listed at the end.

### 6.0 Cross-cutting — read before starting any of these

Reviewed the twelve together on 2026-08-31 rather than taking each at face value. Four things
apply to nearly all of them and are easy to miss when picking one item off the list.

**Every one of these needs a schema migration.** `appsettings.json` is at v4 and `fields.json` at
v2, both with a `migrate()` that runs on load and persists once. New fields do not appear in an
existing install by magic — they arrive by migration, added disabled, exactly as `barcode` was in
§1. Forgetting this means the feature works on a fresh install and is invisible on the user's.

**Several of these will regenerate the golden file.** §6.7 (row height), §6.9 (reordering) and
§6.10 (a new totals row) all change rendered HTML. That is expected — but the rule stands: inspect
the diff, justify it in the commit, never regenerate to make a test pass.

§6.4 was expected to be one of them and **turned out not to be**, which is worth copying. Because
the new column ships disabled, the migration added it to the gate fixture without altering a byte
of `golden.html`. An untouched golden is far better evidence that nothing changed shape than a
regenerated one whose diff someone eyeballed — so prefer designing these features to leave it
alone.

**Reordering must be a *stable* sort (§6.9).** Determinism is a tested invariant
(`tests/test_stage0.py:73`) and the golden gate compares bytes: the same receipt data must render
identically every time. Grouping lines by shipment with an unstable sort would let two renders of
one receipt differ, which breaks the gate in a way that looks like flakiness rather than a bug.
Sort by group, preserve entry order within a group.

**Off by default is not enough for §6.4.** Everything new here defaults off so existing receipts
keep their shape — but §6.4 changes what an *existing* column means, which no toggle covers. See
the resolution recorded there.

### 6.1 Several serial numbers per line — one per unit — **DONE**

Today `serial` is a single text box, so selling three of the same product forces three separate
lines.

- [x] A line of qty *n* carries **n serial numbers**. SKU and barcode stay single — they identify
      the *product*, and every unit of it shares them.
- [x] Keep the count and the list in step: raising qty should ask for more serials, lowering it
      must not silently discard ones already typed.
- [x] Selling from the catalogue should offer the serials actually **held in stock** (§2) rather
      than a blank box, and remove those specific ones on sale.
- [x] Decide how a partly-filled list behaves. Demanding all *n* before the line can be saved will
      be resented at a till; the likely answer is to allow gaps and warn, matching the way
      overselling was settled in §2.

### 6.2 A store-assigned per-unit ID — **DONE**

Serial number and barcode are the **manufacturer's** identifiers. `sku` is the store's own, but it
is per *product*. Nothing today is the store's own *and* per *unit*.

- [x] A new **optional** per-unit field, one value per quantity — mechanically the same list as
      §6.1, so build one mechanism and use it twice.
- [x] **Store the units as a list of records, not two parallel lists.** A line of qty 3 holds three
      *units*, each with its own serial and its own store ID — not a list of serials beside a list
      of IDs. Parallel lists have to be kept aligned by hand, and they drift the first time someone
      deletes the middle serial: every store ID below it then belongs to the wrong unit, silently.
      This is worth getting right before either field ships, because changing it later is a data
      migration rather than a refactor.
- [x] Off by default. A shop that does not label its own stock must never see it.
- [x] It needs a key that cannot be confused with `serial` or `sku` in the field editor. The label
      is user-editable anyway, so the key is what matters.

### 6.3 Order notes — a paragraph, not a line — **DONE**

- [x] A large multi-line text box for free-form notes about the order.
- [x] `multiline` **already exists** as a field type in `config.FIELD_TYPES`, so storage and
      validation are there. What is missing is that receipt-level fields have nowhere to appear:
      the customer form at the top of the main window is still a fixed layout (§5). Doing this
      properly means building that form from `fields.json` — the same prerequisite §5 already
      lists, so do the two together.
- [x] The receipt template needs somewhere for a paragraph to sit, and it must wrap rather than
      overflow.
- [x] A long note spans pages, so it needs the same page-break care as §6.7's rows — and unlike a
      row, a paragraph *should* be allowed to break. Decide where it prints: after the items, or on
      its own at the end.

### 6.4 A real per-line total — **DONE**

The `amount` column computed `qty × price` and nothing else, so a line carrying a discount or a
tax printed a figure that was **not what that line actually came to** — the adjustments surfaced
only in the totals block far below.

Shipped as `line_total`, a **second** column beside `amount` rather than a redefinition of it, and
disabled by default. Enable it under **Tools → Fields & Columns**; a shop that wants only the net
turns `amount` off and this on.

- [x] The line total accounts for that line's own discount and tax. **Shipping is the
      exception** — it is charged per shipment group, not per line (§6.9), and apportioning it
      across lines would invent a split the customer cannot check.
- [x] *The installment part resolved itself.* §6.5 settled that the cash price stays the receipt
      total, so a line's total is its cash figure and the plan is disclosed separately — as a note
      on the line, next to the warranty. Folding a financed figure into the line total would have
      contradicted that decision and pushed tax onto the finance charge.
- [x] The gross stays visible. A customer shown only the net cannot check that the discount was
      applied — so both columns exist, each toggleable per §6.6.
- [x] **Added as a new column rather than redefining the existing one.** This resolved a
      contradiction found on review: §6.6 promises nothing changes shape for existing receipts, but redefining what
      `amount` means would change the figure printed on *every* receipt already being issued —
      something no toggle covers, because the column is already on. So `amount` keeps its present
      meaning (`qty × price`, the gross) and the line total arrives as a **new field, off by
      default**. A shop that wants only the net can then turn `amount` off and the new column on.
- [x] **Uses the existing Decimal path**: each part rounded, then added, matching the way the
      totals block keeps three separate running totals. `SumsMatchTheTotalsBlock` in
      `tests/test_line_total.py` asserts the column adds up to the figures below it.
- [x] **The golden file did not change after all.** The new column ships disabled, so the
      migration adds it to the gate fixture without altering a single byte of `golden.html` —
      which is the strongest available proof that no existing receipt changed shape.

### 6.5 Installment plans — **DONE**

A plan is a **period**, a **down payment** and a **per-month amount**, which together come to a
different — larger — total than the cash price.

- [x] Settable **per line** *or* **for the whole order**, and deliberately **not both**. Different
      lines may carry different plans: 3 months on one, 6 on another, at different prices.
- [x] Enforce that exclusivity in the model, not merely by hiding a control. Two live plans on one
      receipt produce a total nobody can reconstruct.
- [x] Show the arithmetic on the receipt — cash price, down payment, *n* × monthly, financed
      total. A plan the customer cannot check is worse than no plan at all.
- [x] **Toggleable off entirely** (§6.6). A shop that never offers installments must not be asked
      about one on every receipt.

**Settle before building:** does the financed total become *the* receipt total, or does the receipt
show the cash price with the plan beside it? That decides what the tax rows apply to, so choosing
wrong misstates tax. Ask rather than assume.

**This question also decides §6.10.** A payment-method charge is a percentage of a total, so it
cannot be implemented until it is known *which* total — cash or financed. Answer this one first;
the two are the same decision asked twice.

### 6.6 Everything optional is toggleable — **DONE**

- [x] **Line-item fields already are.** `sku`, `barcode`, `serial`, `discount`, `tax` and the rest
      each carry an `enabled` flag, edited under **Tools → Fields & Columns**, and `barcode` ships
      disabled. So for line items this is largely done.
- [x] **Audit what is not.** The shipping fee is a label at `config.py:375`, not a toggleable
      field — and §6.9 rebuilds it anyway, so do that first and make it toggleable there. Confirm
      each of discount, tax, shipping, sku and serial can genuinely be turned off *and that the
      receipt still renders correctly without it* — a disabled column that leaves a gap, or a
      totals line that still prints, is worse than having no toggle.
- [x] Every new field from §6.1–§6.5 arrives toggleable and defaults **off**, so no existing
      receipt changes shape.

### 6.7 Taller rows, and page breaks that respect a line — **DONE**

Once a line carries several serials, per-unit IDs and an installment plan, it no longer fits on one
text row.

- [x] **Grow the row to fit its data** rather than squeezing everything into one line. Wrapping —
      not truncation, and not a horizontal squeeze.
- [x] **Never split a product line across a page break** — unless it began at the top of a page
      (header excluded) and *still* will not fit, in which case it must break, because there is
      nowhere better for it to go.
- [x] **Make that a toggle**, so someone who prefers tight pages can allow mid-line breaks.

**Check before building:** `break-inside: avoid` on the row may already give exactly the described
behaviour — that is close to its defined semantics, since a box that fits on no page has to break
rather than be pushed forever. If it does, the work is the toggle and a test that proves it, not
new layout logic. Verify against a real multi-page PDF before designing anything more elaborate.

### 6.8 Scan a barcode to add or increment a line — **DONE**

- [x] Scanning a known barcode **adds a line** for that product, qty 1, filled from the catalogue.
- [x] Scanning the **same barcode again increments that line's quantity** instead of adding a
      second line. Note that this makes a scan a quantity change, so it must grow the line's unit
      list (§6.1) — scan a thing three times and three serials are now owed, not one.
- [x] The user then edits the line to supply what a scan cannot know — the serials (§6.1) above
      all.
- [x] An **unknown** barcode needs a defined answer: offer to create the product, or add a bare
      line? It must not fail silently. At a till, a scan that does nothing looks like a broken
      scanner.
- [x] Scanner input is keystrokes ending in Enter. §1 already stops Enter from submitting the item
      dialog; the same care is needed wherever the main window accepts a scan.

### 6.9 Shipping fees per group of lines, not per invoice — **DONE**

Shipping is one invoice-level fee today, so it is effectively charged against the whole order.
That is wrong whenever an order ships from more than one place: lines 1, 2 and 4 leave one
warehouse and line 3 leaves another, each with its own carrier cost.

- [x] A line belongs to a **shipment group**, and each group carries its own shipping fee.
- [x] The receipt shows **each group's fee and the combined total**, so the customer can see why
      the shipping came to what it did rather than being handed one unexplained number.
- [x] **Default to today's behaviour.** No groups means one fee and the current single line, so no
      existing receipt changes shape.
- [x] Toggleable per §6.6, like everything else optional here.

**Grouping is a tag on the line, not a range of rows.** The example is lines 1, 2, 4 against line
3 — the groups interleave, so this cannot be modelled as contiguous sections of the table. Each
line stores which shipment it belongs to.

**Settled 2026-08-31:**

- **The table reorders** so a shipment's lines sit together. Note the consequence: the printed
  order will not match the order lines were entered in, so nothing may depend on entry position —
  check whether anything quotes a line number before relying on one.
- **The group's name does not print.** Grouping is internal for now. Keep the label in the data
  model anyway, unset — printing it later is then a template change rather than a migration.
- **Shipping is not taxed.** The document-level tax rows can stay as they are. But the *payment
  method* carries its own charge — see §6.10, which came out of this question.

**Open, found on review: as specified, the grouping is invisible.** The three settled answers
combine into something that does not quite work. If the lines reorder, the group is not named, and
the fees sit in the totals block, then a customer sees a re-sorted list of items and two shipping
charges with nothing connecting them — the reordering communicates nothing, and the second fee
looks like a mistake. Three ways out, needing a decision:

- A neutral marker — "Shipment 1 of 2" — which groups the lines without naming a warehouse. This
  looks like the intent of "no warehouse name" while still making the split legible.
- Print each group's fee directly beneath its own lines rather than in the totals block, with only
  the combined figure below.
- Accept it: reorder purely for internal tidiness and let the customer see one combined shipping
  figure, with the split visible only in the app.

The third is the least work and the least useful; the first is recommended. Worth asking, because
it is the difference between a receipt that explains itself and one that raises a question at the
counter.

**Shipping stays out of the per-line total (§6.4).** A group fee covers several lines at once, and
splitting it across them would mean inventing an apportionment — by value, by weight, by count? —
that the customer cannot check. It belongs in the totals block as several rows, not folded into a
line. Worth stating because §6.4 otherwise pulls every adjustment down to the line.

**Also check against §2's stock rules.** Stock commits *after* the receipt exists and a reissue
adjusts by the difference. Once a sale removes *specific* serials (§6.1) rather than decrementing a
count, "adjust by the difference" needs to mean specific serials returning to stock, not just a
number going back up. That is a real complication in existing, tested behaviour — plan for it
rather than discovering it.

**Later, once the catalogue is richer (§2):** if a product records where it is stocked, the group
could be assigned on its own rather than by hand. Do not build for that yet — it only pays off
once products carry a location, which they do not.

### 6.10 Charges that depend on how the customer pays — **DONE**

Settled alongside §6.9: shipping itself is not taxed, but **the payment method carries its own
charge**, and the amount depends on which one is used.

| Method | Charge |
|---|---|
| Bank transfer | none |
| Cash on delivery | 4%, a government-imposed tax |
| Card (Visa / Mastercard / …) | the processing middleware's handling fee |

- [x] A payment method is chosen on the receipt, and its charge is applied and shown.
- [x] The methods and their rates are **editable in-app**, not hard-coded. The 4% is set by a
      government and card processors change their fees; neither should need a new build.
- [x] Toggleable per §6.6 — a shop that takes only cash must not be asked.
- [x] Default to no methods configured, which behaves exactly as today.

**A tax and a processing fee are not the same thing, and must not share a row type.** They have
identical arithmetic — a percentage of the total — so it is tempting to model them as one. Resist
it. The COD 4% is tax the government levies and that a shop has to account for and remit; the card
fee is a private company's service charge and is not tax at all. Lumping them together overstates
the tax collected on every card sale, which is a filing problem, not a cosmetic one. Model them as
distinct kinds even though the calculation is shared.

**The existing tax-row model is most of the shape already.** `TAX_ROW_TYPES` is
`("percent", "fixed")` with a label and a value, validated and rendered — reuse it rather than
inventing a parallel one. One gap: card processors usually charge **percentage *plus* a fixed
amount** (the familiar "2.9% + 0.30"), which today needs two rows to express. Decide whether one
method may carry both components.

**Naming, worth getting right before it is built:** these were described as *shipping* methods, but
bank transfer and card are how the customer **pays**, not how goods travel. Cash on delivery is
genuinely both. The distinction matters because it decides where the field lives — on the shipment
group (§6.9) or on the order.

**Settle before building:** is the payment method **per order**, or can different shipments be paid
differently? The recommendation is per order: you pay for an order once, and bank transfer and card
are inherently single transactions. But COD is collected per delivery, so a two-warehouse order
could in principle be two collections. Ask before assuming, because it decides whether this hangs
off §6.9's shipment group or off the receipt.

### What section 6 cost

Four bugs surfaced while building it. Three were pre-existing and none was in the feature being
worked on at the time, which is the argument for writing tests around a change rather than only
over it.

- **A contended lock failed a sale.** `os.open(O_CREAT|O_EXCL)` racing another process's delete
  fails on Windows with `EACCES`, not `EEXIST`, and only `EEXIST` was retried — so a lock hand-off
  working exactly as designed surfaced as "Could not lock the invoice counter" and cost the user
  the receipt they were saving. It had been showing up as a test that failed about one run in ten
  and reading as flakiness.
- **The item table ate leading zeros.** `tree.item(row)["values"]` runs every cell through Tcl's
  type guessing, so a UPC of `0000000000000` came back as `0` and a serial of `007` as `7`. Silent
  data loss on a document that gets signed and handed to a customer. `tree.set(row)` returns what
  was stored; `item_at()` is the single reader now.
- **Shipping had no switch.** §6.6 claimed the toggles were "largely done". The audit found the
  shipping fee was a label with no way to turn it off — and turning something off has to mean *not
  charged*, not merely *not shown*.
- **A circular import waiting to happen.** `shipments.py` importing the renderer for its rounding
  hit it immediately; `installments.py` had the identical import and was spared only because the
  renderer happened to import it lazily. `money.py` now sits at the bottom of the import graph.

Two decisions were settled here rather than deferred, and both are recorded where they were made:
the cash price stays the receipt total with a plan shown beside it (§6.5), because tax applies to
the goods and not to financing them; and per-unit values are stored as records rather than parallel
lists (§6.1), because parallel lists drift the first time somebody clears a value in the middle.
