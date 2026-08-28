# Decisions — what was chosen, and why

Read this before "fixing" something that looks strange. Most of the strange things are load-bearing.

Where a decision reversed an earlier one, both are kept. Knowing that a choice was reconsidered is
more useful than a tidy record that hides it.

---

## 1. The GUI settings editor reverses the plan's "files-first" non-goal

`PLAN-generalization.md` lists **"a GUI settings editor (files-first is the decision)"** under
**Non-goals**, and the whole architecture was built on it.

The user asked for the opposite, explicitly: *"you have to improvise app in such a way that user
dont have to keep opening appsettings or config files to edit or add anything."* That is their
call and it stands.

**The files-first work was not wasted** — it is what made the editors safe. Every editor saves
through the same validate-then-atomic-write-with-`.bak` path, so the app cannot save a file it
would then refuse to load, and hand-editing still works exactly as before.

*If you read the plan and feel the urge to remove the editors: don't.*

## 2. Invoice numbers: reserve-and-keep

A number is claimed by an atomic increment **before** rendering, and stays consumed even if the
render fails.

Why: two app instances, or the app and the CLI, must never be handed the same number. Releasing a
number on failure reopens exactly the race the reservation closes. The cost is gaps in the
sequence, which is an audit concern in some places — so every burned number is logged with its
reason.

Reconciliation with filenames is **one-directional**: files ahead of the counter pull it forward;
files behind it (a deleted or archived receipt) only warn. Following filenames downward is
precisely how a number gets issued twice.

A corrupt counter file **refuses to load** rather than starting a fresh sequence.

## 3. Stock: commit-on-success — the opposite policy, deliberately

Stock is deducted **after** the receipt exists, not reserved before it.

Why the difference: a duplicate invoice number is unrecoverable, whereas a stock figure can always
be recounted. Stock records that goods actually left, so a failed render must deduct nothing.

- Reissuing a receipt adjusts by the **difference**, not the whole sale again.
- Overselling is **recorded, never refused**. The count goes negative and a warning names the
  product. Blocking a sale over a possibly-stale figure would be far worse at a till, and a
  negative number states plainly that a recount is due.
- **Negative stock counts are valid** and must stay storable. Refusing them was a real bug: the
  save failed, the deduction was lost, and stock was left wrong in the *optimistic* direction.
- Off by default — an uncounted catalogue would go straight to negative everywhere.

## 4. Product catalogue storage: JSON, revising an earlier SQLite recommendation

First recommended SQLite; changed on a closer look. The deciding factor is the **shape** of the
data, not its size: a product holds a list of serials and a list of variants that override some of
its fields. That is natural in JSON and needs three tables and joins in SQL. SQLite is strongest
for flat indexed rows, which this is not, and multi-user is an explicit non-goal.

SQLite would win at multi-till scale or tens of thousands of products. The JSON shape maps onto
tables cleanly enough that it would not be a rewrite.

## 5. Variants override their parent, and `name` is a label

A variant states only what differs and inherits the rest, rather than being a full product with a
parent pointer — otherwise every field is restated on every variant and they drift apart.

A variant's `name` is its **label** ("Blue"), not a replacement product name. Treating it as an
override printed **"Blue (Blue)"** on the receipt and lost the product entirely.

## 6. Receipt history is JSON, not the CSV that was suggested

Line items are a variable-length list whose fields are user-configurable, which CSV cannot
represent without inventing columns that break the moment someone adds a field.

One JSON object per line (`.jsonl`): no index to fall out of step with the data, a half-written
line cannot corrupt the ones before it, and the file stays greppable. **A CSV export on top is
still wanted** — JSON as the source of truth, CSV as a view.

The record **outlives its PDF** on purpose. "I deleted it and now I need it back" is the case the
feature exists for.

## 7. Key rotation must not invalidate history

Verification originally pinned exactly one certificate, so replacing a key would have made every
receipt already in a customer's hands report as a forgery.

The certificate being replaced is now archived first, and verification trusts the current one plus
the retired ones — while still reporting *which* signed it, so an older receipt stays
distinguishable. The widening is narrow: a forgery signed with an unrelated key, and a tampered
receipt, are both still rejected, and tests cover both. *"Trust more certificates" is exactly the
change that can quietly become "trust anyone."*

## 8. Passphrases are never stored

An imported encrypted key is decrypted once, in memory, and re-saved **unencrypted** in the app's
signing folder.

Storing a passphrase beside the key it unlocks protects nobody, and prompting for it on every
receipt is not workable at a till. The app does neither, and the README says so plainly rather
than implying a protection that is not there.

## 9. The golden gate is pinned to pre-Stage-3 output

`tests/fixtures/env/` holds a config reproducing what the app printed *before* currency and labels
became configurable, so `golden.html` stayed byte-identical through Stages 2–5 and remained a real
regression detector during the riskiest changes. Neutral defaults and the other permutations are
covered by unit tests instead.

It has been regenerated **once**: adding `.receipt-fields` styling changed the embedded stylesheet.
The diff was inspected first — exactly nine CSS lines, no change to the receipt body.

## 10. Fonts are plumbed but default-off

Stage 2 asked for both "embed fonts" and "golden diff empty", which cannot both hold. The font
mechanism landed config-gated and off, keeping the diff empty. **No OFL font is bundled** —
fetching one needs network access, and adding an unverified binary blob silently is not
appropriate.

## 11. Only http/https/mailto in footer links

An `href` is a code context; escaping cannot make `javascript:` safe there. Anything else is
refused at save time, because the link is embedded in a PDF that goes to customers.

An unset URL prints its words **without** a hyperlink rather than emitting `href=""` — a link to
nowhere on a receipt is worse than plain text.

## 12. Line-item amounts are ungrouped for existing installs

Older versions grouped digits in the totals but not in the item table. That inconsistency is
preserved for existing installs via `currency.group_line_amounts: false`, because changing it
alters the appearance of a legal document. Fresh installs default to `true` (consistent).

## Smaller ones, quickly

- **`.gitattributes` pins LF.** Without it, Git's Windows default rewrote the golden fixture to
  CRLF on checkout and every fresh clone failed its own gate.
- **Templates are seeded on first run, never overwritten**, and their hashes are recorded *at copy
  time* so an upgrade can tell "user edited this" from "last version's default". An older install's
  edited flat-layout `header.html` is carried into `Templates/` rather than being shadowed.
- **The build smoke test calls `render_pdf`, not `generate()`** — a build check must not add a fake
  receipt to someone's history or consume an invoice number.
- **The smoke test also imports the lazily-imported GUI modules**, so a bundling miss fails the
  build rather than surfacing as a broken menu for the user.
- **Windows device names** (`CON`, `PRN`, `NUL`, `COM1`…) get an underscore appended when they are
  the whole filename stem, because such a file cannot be created at all.
- **Enter in an item field advances focus** rather than submitting, so a barcode scanner that
  types-then-Enters cannot save a line containing only a barcode.
