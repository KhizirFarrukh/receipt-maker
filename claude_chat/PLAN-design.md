# Design Plan — the interface, end to end

Companion to [PLAN-generalization.md](PLAN-generalization.md), which covered the architecture. This
one covers what the app *looks like and feels like to use*, and what "finished, on design grounds"
means so there is a line to stop at.

**Status: proposed, nothing implemented.** Written 2026-09-19 against commit `bbac7a1`.

---

## 1. How this was assessed

Not from imagination. Three passes:

1. **Code audit** of `receiptmaker/ui/main_window.py` (2400 lines) and `receiptmaker/ui/settings_ui.py`
   (1500 lines) — every widget, every geometry call, every colour and font literal.
2. **The real widget tree, built headlessly.** The app was instantiated against a temporary
   `APP_DIR` and its grid interrogated, twice, with a receipt-level field off and on. That is how the
   defect in §3.1 was found and confirmed rather than guessed at.
3. **The output**, `Templates/styles.css`, read for its palette and type scale — because that is
   where this plan's design language comes from (§2).

What has *not* happened: the app has not been run on screen and screenshotted. Every finding below
is either a code fact or a measured property of the live widget tree. Screenshots are a deliverable
of Stage D7, not an input to this plan.

---

## 2. The thesis

**The receipt is designed. The app that prints it is not.**

`Templates/styles.css` has a considered palette (a full slate ramp plus one red), a six-step type
scale, deliberate letter-spacing, and a consistent rhythm. It looks like a document a business would
send a customer.

The application window has none of that. It has no `ttk.Style` and no `theme_use` — **not one line
of styling in 3,900 lines of UI code.** It inherits whatever the platform ttk theme does, with three
hex colours dropped inline at eleven call sites and two ad-hoc font overrides that disagree with each
other about what a bold label is (`9` in one place, `10` in another).

The consequence is not only that it is plain. It is that the tool and its product look unrelated —
the shopkeeper edits a 1998 grey form and out comes a crisp modern document. Closing that gap is the
whole design direction, and it has a pleasant property: **the design system does not have to be
invented. It has to be derived.** Every token in §5.1 is lifted from the CSS the app already ships.

---

## 3. Findings

### 3.1 Structural defects

**A layout collision that ships today.** In `main_window.py`, the items frame is placed at a
*computed* row — `row=next_form_row`, which grows with the number of enabled receipt-level fields —
while the action bar and status label are placed at **hardcoded** rows 4 and 5:

```
main_window.py:304   items_frame.grid(row=next_form_row, ...)   # 3 + one per receipt field
main_window.py:381   actions_frame.grid(row=4, ...)             # hardcoded
main_window.py:398   self.status_label.grid(row=5, ...)         # hardcoded
```

With the shipped defaults there are zero enabled receipt fields, so items land on row 3 and it
works. **Enable one — Order Notes, the feature requested as §6.3 — and the items table and the
action bar occupy the same grid cell.** Measured, not inferred:

```
notes disabled:  row 3  TLabelframe 'Items'
                 row 4  TFrame                       <- action bar, alone
notes enabled:   row 3  TLabel 'Order Notes' | Text
                 row 4  TLabelframe 'Items' | TFrame <- collision
```

This is the generated-layout problem in miniature: a screen assembled from `fields.json` cannot have
hand-placed coordinates anywhere in it. The fix is structural (§5.4), not a patch to the row number.

**Dialogs that cannot grow.** The item dialog is `resizable(False, False)` and has no scrolling. Its
row count comes from `fields.json`. A shop that enables sku, barcode, description, serial, unit ID,
qty, price, discount, tax, warranty, shipment and line total gets a dialog taller than a laptop
screen, with the OK button off the bottom and no way to reach it. The settings dialog has the same
shape of problem: ten tabs, 53 rows, the largest tab holding twelve, and no scroll region on any page.

**No window identity.** No `iconbitmap`/`iconphoto` anywhere in the UI, and no `icon=` in
`packaging/receipt_maker.spec`. The packaged executable shows the default Tk feather in the taskbar
and the Alt-Tab switcher — on a white-label product a shop is meant to put its own name on.

### 3.2 System defects

| | Current state | Consequence |
|---|---|---|
| Theme | No `ttk.Style`, no `theme_use` | Look is whatever Windows does; unrepeatable, untestable |
| Colour | 3 hex values at 11 inline sites (`#64748b`, `#166534`, `#b45309`) | No names, no contrast check, no way to change one thing |
| Type | 2 overrides, `("TkDefaultFont", 10, "bold")` and `(…, 9, "bold")` | Same role, two sizes; fixed pt fights the DPI scaling the app does elsewhere |
| Spacing | `padx` 5/6/8/10/16, `pady` 2/5/10/15, `padding` 5/10/14/16 | Nine values, no rhythm, nothing aligns down a column |
| Density | Treeview default rowheight | The squeezed rows §6.6 asked to fix |

Two of the three inline colours — `#166534` and `#b45309` — do not exist in the receipt's palette.
The third, `#64748b`, does. So the UI is already reaching for the right family by instinct and
missing by hand.

### 3.3 Interaction defects

- **No keyboard path to anything.** Thirteen `bind()` calls in the whole UI, all of them mouse or
  field-local. No accelerators, no `underline=` mnemonics, no `Ctrl+` anything. A till operator
  issuing fifty receipts a day mouses every one.
- **No dialog closes with Escape.** Zero `Escape` bindings across both UI modules.
- **66 modal alerts** (32 + 34 `messagebox` calls). Modality is correct for a question — *overwrite
  this?*, *void that?* — and wrong for news. Reporting "draft saved" by freezing the app until the
  user clicks OK is the single most repeated interaction in the product.
- **One status label, at the bottom, empty most of the time.** There is no non-blocking channel, so
  everything becomes a dialog by default.
- **No empty states.** An empty items table is a blank box. So is an empty products list, drafts
  list, history list. The moment a new user most needs to be told what to do, the app says nothing.

### 3.4 Information architecture

- **18 top-level windows** (8 + 10), reached from one flat `Tools` menu of 8 commands with no
  grouping beyond two separators, and no indication which are everyday (Products, History) versus
  once-a-year (Signing Keys, Restore Default Templates).
- **Three list dialogs built three times.** History, Products and Drafts are the same thing — toolbar,
  treeview, selection-dependent actions — implemented independently, so they drift in button order,
  wording and double-click behaviour.
- **53 settings across 10 tabs with no search.** Finding "digit grouping" means knowing it lives
  under Currency rather than Document.

### 3.5 What is already right — do not break it

Worth naming, because a redesign can easily destroy these:

- **The settings dialog is generated from a declarative table** (`SETTINGS_SECTIONS`,
  `LIST_SETTINGS`). Adding a setting is one row. This is the correct architecture and the redesign
  must keep it — the new design is a better *renderer* for that table, not a hand-built form.
- **DPI awareness is handled properly** — `SetProcessDpiAwareness`, `tk scaling`, and a `ui_scale`
  multiplier. The token scale in §5.1 multiplies through it rather than replacing it.
- **The window sizes itself to its content and clamps to the screen** (`_size_window`), with a floor
  that keeps the action buttons visible.
- **The generation progress dialog is right**: modal, determinate, uncloseable mid-job, worker on a
  thread. Keep as is.
- **The form is configuration-driven end to end.** That is a feature, and it is exactly why the
  design has to be a system.

---

## 4. Principles

Six rules the target obeys. Everything in §5 traces back to one of them.

1. **Derive, don't invent.** The palette, type scale and rhythm come from `Templates/styles.css`. The
   tool should look like it made the document.
2. **A system, not a layout.** Screens are generated from `fields.json`. Anything hand-positioned is a
   latent bug — §3.1 is the proof. Zones own their internal geometry and nothing places itself at a
   coordinate a sibling also knows about.
3. **The till is not the back office.** Issuing a receipt is fast, repeated, keyboard-first,
   one-handed-with-a-scanner. Configuring the shop is slow, careful, mouse-and-read. They should not
   feel the same.
4. **Never block a sale.** Modality is for questions, not for news. A customer is standing there.
5. **Monochrome, because it is white-label.** The receipt is slate plus one red. A shop drops its own
   logo in. Introducing a brand accent colour guarantees a clash with somebody's branding — so the
   primary action is a filled slate-900 button, and colour is reserved for state (error, success,
   warning), never decoration.
6. **The output must not change by one byte.** The golden HTML gate stays green through every stage.
   This is a plan about the app, not the receipt.

---

## 5. The target design

### 5.1 Tokens

One module, `receiptmaker/ui/theme.py`, owns all of it. Nothing else may contain a hex literal, a
font tuple, or a raw pixel pad.

**Colour** — the first eight are lifted straight from `styles.css`:

| Token | Value | From | Use |
|---|---|---|---|
| `INK` | `#0f172a` | receipt body | Primary text, filled-button background |
| `INK_SOFT` | `#334155` | receipt | Secondary text, headings in cards |
| `MUTED` | `#64748b` | receipt (already used in the GUI) | Hints, meta, column meta, file paths |
| `FAINT` | `#94a3b8` | receipt | Disabled text, placeholders, empty-state text |
| `RULE` | `#cbd5e1` | receipt | Borders, separators, table grid |
| `SURFACE` | `#ffffff` | receipt | Input and card background |
| `SUNK` | `#f8fafc` | receipt | Window background, table zebra stripe |
| `DANGER` | `#b91c1c` | receipt (the VOID stamp) | Destructive actions, validation errors |
| `SUCCESS` | `#15803d` | new — slate-family green | "3 of 3 entered", verified, saved |
| `WARN` | `#b45309` | already in the GUI | Low stock, incomplete, unsigned |

`SUCCESS` and `WARN` are the only additions, because the receipt never has to express a state. They
are text and icon colours only — never fills — which keeps the surface monochrome.

**Type** — the receipt's scale, in points, so the two stay legible to the same eyes:

| Token | Size | Weight | Use |
|---|---|---|---|
| `DISPLAY` | 14pt | bold | Window title bar content, invoice number |
| `TITLE` | 12pt | bold | Card and section headings, totals |
| `BODY` | 10pt | normal | Default — inputs, table cells, buttons |
| `LABEL` | 9pt | normal | Field labels, column headings |
| `META` | 8pt | normal | Hints, status line, paths |
| `MICRO` | 7.8pt | normal | Footnotes |

Normal and bold only, matching the receipt, and matching what `TkDefaultFont` reliably has. Every
size passes through `ui_scale`.

**Space** — a 4px base, replacing the current nine ad-hoc values:

```
SPACE_1   4   inside a control
SPACE_2   8   label to its field
SPACE_3  12   field to field
SPACE_4  16   group to group
SPACE_5  24   section to section
SPACE_6  32   window margin
```

**Density** — `ROW_HEIGHT = 28 * ui_scale` for the items Treeview, which is what §6.6 ("taller rows
so per-line data isn't squeezed") asked for, set once as a style rather than per-table.

### 5.2 The ttk style layer

`theme.apply(root)` is called once, before any widget is built.

**It switches the theme to `clam`.** This is a deliberate trade with a real cost. On Windows the
default `vista` theme draws buttons and entries with native OS elements and **silently ignores most
`Style.configure()` colour settings** — you cannot build a design system on it. `clam` is fully
restylable and renders identically on Windows 10, 11 and a build server. The cost is that the app
stops looking like a native Windows app. For a branded, white-label, single-purpose till application
whose whole design thesis is "match the document you print", that is the right side of the trade —
but it is a visible change and it belongs in one commit with screenshots, not smuggled in.

Named styles, so intent is in the widget declaration rather than in a colour argument:

```
Primary.TButton     filled INK, white text          — one per screen, the commit action
Danger.TButton      outlined DANGER                 — void, delete, replace key
TButton             default outlined                — everything else
Ghost.TButton       borderless, MUTED text          — tertiary (Clear Form, Cancel)
Field.TEntry        SURFACE on RULE border
Invalid.TEntry      DANGER border + inline message under the field
Muted.TLabel        MUTED, META size
Success.TLabel      SUCCESS, META
Warn.TLabel         WARN, META
Title.TLabel        TITLE
Card.TFrame         SURFACE on RULE, SPACE_4 padding
Toolbar.TFrame      SUNK
Items.Treeview      ROW_HEIGHT, zebra SUNK, RULE gridlines
```

### 5.3 Component inventory

Eight components, built once in `receiptmaker/ui/components.py`, replacing the ad-hoc assembly:

| Component | Replaces | Notes |
|---|---|---|
| `Card` | bare `ttk.LabelFrame` | Titled surface, consistent padding, owns its own grid |
| `FormRow` | 40+ hand-built label/entry pairs | Label above field, hint under, error slot under that |
| `ActionBar` | `actions_frame` | Pinned bottom, primary right, ghost left, status strip inline |
| `Toast` | ~40 of the 66 messageboxes | Non-blocking, auto-dismiss, lives in the ActionBar |
| `RecordBrowser` | History + Products + Drafts | Toolbar, filter box, Treeview, selection-driven footer |
| `ScrollPage` | settings tab pages, item dialog | Canvas + scrollbar, only scrolls when it has to |
| `EmptyState` | four blank boxes | Centred icon-free hint + the shortcut that fixes it |
| `TotalsStrip` | nothing — new | Live subtotal/discount/tax/shipping/total under the items table |

### 5.4 Screen by screen

**Main window — the till.** Restructured from one 6-column grid into four stacked zones, packed
vertically. Each zone grids internally. **No zone knows another zone's row number** — that is the
structural fix for §3.1, and it holds no matter how many fields a shop enables.

```
┌──────────────────────────────────────────────────────────────┐
│ HEADER      Type ▾   Invoice No. W-1001      Date 19 Sep 2026│  fixed height
│                                     Acme Ltd ● ready to sign │
├──────────────────────────────────────────────────────────────┤
│ CUSTOMER    Name                     Phone                   │  Card, 2 cols
│             Email                    Paid by ▾               │  grows with
│             Order Notes  ┌─────────────────────┐             │  fields.json
│                          └─────────────────────┘             │
├──────────────────────────────────────────────────────────────┤
│ ITEMS   [+ Add] [Edit] [Remove]   Scan ▭     [Shipping…]     │  expands to
│  ┌─────────────────────────────────────────────────────────┐ │  fill
│  │ SKU   Description   Qty  Price  Disc  Tax   Line total  │ │
│  │ ...                                                     │ │
│  └─────────────────────────────────────────────────────────┘ │
│  Subtotal 45,000   Discount −5,000   Tax 1,800   Ship 500    │  TotalsStrip
│                                        Total  42,300        │
├──────────────────────────────────────────────────────────────┤
│ Draft saved.            [Clear] [Save Draft] [Generate ▸]    │  ActionBar
└──────────────────────────────────────────────────────────────┘
```

Three substantive changes beyond the restyle:

- **Labels move above their fields.** Beside-the-field labels break as soon as a shop writes a long
  custom label or runs in a language with longer words — and the labels are user-configurable. Above
  is the only layout that survives arbitrary label text.
- **The invoice number gets `DISPLAY` weight.** It is the receipt's identity and the thing most
  worth double-checking; right now it is an 18-character entry indistinguishable from Phone.
- **A live totals strip.** The §3 work added subtotal/discount/tax/total to the *receipt*. Showing
  them live in the app is the other half: it catches a wrong discount scope before the PDF exists,
  rather than after the customer has it.

**Item dialog.** Currently a flat list of up to fourteen equal-weight rows. Target: four labelled
groups in a `ScrollPage`, resizable, with a live line total.

```
What            SKU · Barcode · Description
How many        Qty · Price · Discount · Tax     → Line total 42,300  (live)
Per item        Serial / Unit ID     [Enter per item…]  3 of 3 entered
Extras          Warranty · Shipment · Instalment plan
```

Validation moves inline — the offending field gets `Invalid.TEntry`, the message appears beneath it,
and focus lands there. Today a bad quantity is a modal alert that says which field, then leaves the
user to find it.

**Settings.** Keep the Notebook and keep the declarative table — the architecture is right. Add:
scrollable pages; a filter box that searches all 53 rows across all 10 tabs and jumps to a hit; help
text rendered as persistent `META` hint under the control instead of a popup; a changed-since-opened
marker per row so Save is not a leap of faith; and the twelve-row Advanced tab split into labelled
sub-groups.

**History, Products, Drafts.** One `RecordBrowser`, configured three ways. Same toolbar position,
same filter box, same double-click-to-open, same destructive actions in `Danger.TButton`.

**Progress dialog.** Unchanged except tokens. It is already correct.

### 5.5 Interaction model

**Keyboard map** — the till half of principle 3:

| Key | Action |
|---|---|
| `Ctrl+N` | New / clear form |
| `Ctrl+I` *or* `Insert` | Add item |
| `F2` | Edit selected item |
| `Delete` | Remove selected item |
| `Ctrl+F` | Focus the scan box |
| `Ctrl+S` | Save draft |
| `Ctrl+D` | Drafts |
| `Ctrl+Enter` | Generate receipt |
| `Escape` | Close dialog / cancel |
| `F1` | Environment check (`--doctor` in a window) |
| `Alt+T` | Tools menu |

Plus visible accelerators in the menu and `underline=` mnemonics.

**Feedback ladder** — replacing "everything is a messagebox":

| Severity | Channel | Example |
|---|---|---|
| Field is wrong | Inline, under the field, focus moves | "Quantity must be a whole number" |
| Something happened | Toast in the action bar, auto-dismiss | "Draft saved", "12 products imported" |
| Something needs watching | Persistent `Warn.TLabel` in context | "Only 2 left in stock" |
| A decision is needed | Modal | "Overwrite the settings file?" |
| It failed | Modal with detail expander | Generation failed, log path |

Target: **modal alerts only ever ask a question.** Everything that merely reports moves down the
ladder. That is roughly 40 of the current 66.

**Empty states.** Items: *"No items yet — press Ctrl+I to add one, or scan a barcode."* Products,
Drafts, History each get the equivalent, naming the action that fills them.

### 5.6 Accessibility and scaling

- Contrast: body text ≥ 4.5:1, computed from the tokens and asserted by test. Measured, against
  both `SURFACE #ffffff` and `SUNK #f8fafc`:

  | Token | on white | on sunk | Verdict |
  |---|---|---|---|
  | `INK` | 17.85:1 | 17.06:1 | body |
  | `INK_SOFT` | 10.35:1 | 9.90:1 | body |
  | `MUTED` | 4.76:1 | 4.55:1 | body — clears on both, which is why it can carry hints |
  | `DANGER` | 6.47:1 | 6.18:1 | body |
  | `SUCCESS` | 5.02:1 | 4.79:1 | body |
  | `WARN` | 5.02:1 | 4.80:1 | body |
  | `FAINT` | 2.56:1 | 2.45:1 | **never content** — disabled/placeholder only |
  | `RULE` | 1.48:1 | 1.42:1 | borders only |

  White on `INK` (the filled primary button) is 17.85:1. Every colour that carries meaning clears
  4.5:1 on both backgrounds; the two that do not are structurally incapable of carrying meaning,
  which is the rule the tokens exist to make enforceable.
- Every screen usable at 1024×768.
- Every screen correct at 100%, 125%, 150% and 200% Windows scaling.
- Full keyboard reachability (§5.5) — no action that exists only as a click.
- Focus visible on every control; tab order follows reading order in every dialog.

---

## 6. Definition of done

Design-complete when all twelve hold. Seven are machine-checkable and belong in
`tests/test_design.py`, in the same spirit as `tests/test_layout.py`:

| # | Criterion | Checked by |
|---|---|---|
| 1 | No hex literal, font tuple or raw pad outside `theme.py` | test (AST scan) |
| 2 | No widget grids into a row a sibling zone also uses | test + structure |
| 3 | Every `Toplevel` binds `Escape` | test (walks the classes) |
| 4 | Every action in the §5.5 map has a binding | test |
| 5 | Token contrast ratios meet §5.6 | test (computed) |
| 6 | Every `messagebox` call site is a question, not a report | test (allow-list) |
| 7 | History, Products and Drafts all instantiate `RecordBrowser` | test |
| 8 | Palette and type scale still match `Templates/styles.css` | test (parses the CSS) |
| 9 | Usable at 1024×768 and at 100/125/150/200% scaling | manual matrix, screenshots |
| 10 | Packaged exe carries a real icon | build check |
| 11 | Item dialog and settings pages scroll rather than clip at any field count | manual + test at max fields |
| 12 | **Golden HTML gate byte-identical throughout** | existing gate |

Criterion 12 is the hard one. A UI redesign that changes the receipt has failed regardless of how it
looks.

---

## 7. Work stages

Sequenced so that risk falls before appearance changes, and so each stage is independently
shippable. Sizes are relative, not hours.

| Stage | Name | Contents | Size | Exit criteria |
|---|---|---|---|---|
| **D1** | Fix what's broken | Zone restructure of the main window (kills §3.1); Escape on every dialog; the §5.5 keyboard map; scrollable item dialog and settings pages; window + exe icon | M | DoD 2, 3, 4, 10, 11. No visual restyle yet. Suite green, gate green |
| **D2** | The token layer | `theme.py`; `clam`; every hex/font/pad replaced by a token; `Items.Treeview` row height | M | DoD 1, 5, 8. **This is where the app visibly changes** — one commit, with before/after screenshots |
| **D3** | Layout system | `Card`, `FormRow`, `ActionBar`, `TotalsStrip`; labels above fields; invoice number promoted; live totals | L | Main window matches §5.4. Gate green |
| **D4** | The item dialog | Four groups; live line total; inline validation replacing modal validation | M | §5.4 dialog; validation never opens a messagebox |
| **D5** | Feedback ladder | `Toast`; demote ~40 messageboxes; `EmptyState` in all four lists | M | DoD 6 |
| **D6** | Back office | `RecordBrowser` across History/Products/Drafts; settings filter, hints, changed-markers, Advanced sub-groups | L | DoD 7; three dialogs, one component |
| **D7** | Proof | Contrast audit; DPI matrix 100/125/150/200; 1024×768 pass; screenshots; README design section; `ARCHITECTURE.md` UI section | S | DoD 9 and the full table green |

**Dependencies.** D1 before D3 (zones must exist before components fill them). D2 before D3–D6 (the
components consume tokens). D5 after D4 (the item dialog is the biggest messagebox consumer). D7 last.

**Suggested order if the whole thing is not taken on:** D1 alone is worth doing regardless — it fixes
a shipped bug and adds the keyboard map, with no visual risk. D1 + D2 is the smallest combination
that makes the app look deliberate.

---

## 8. Non-goals and risks

**Not in scope:**

- **Any move off tkinter.** No Qt, no Electron, no web front end. The packaging, signing and offline
  story all depend on the current stack.
- **Changing the receipt.** Principle 6, DoD 12.
- **Changing config file formats.** The design renders `fields.json` and `SETTINGS_SECTIONS` better;
  it does not restructure them.
- **Dark mode.** Technically reachable once §5.1 exists — it is a palette swap — but it is a large
  surface to verify for a till that runs under shop lighting, and tkinter gives no reliable Windows
  dark-mode signal. Deferred, explicitly, and cheap to add later *because* of the token layer.

**Risks:**

| Risk | Mitigation |
|---|---|
| `clam` looks non-native and the owner dislikes it | Land D2 alone, with screenshots, before D3 builds on it. Reverting is one commit |
| The redesign perturbs the golden HTML | The gate runs on every commit; UI and renderer are already separate packages |
| GUI tests are brittle against restructuring | 243 GUI tests across six files (`test_gui_main` 58, `test_gui_dialogs` 75, `test_gui_internals` 38, `test_gui_section6` 27, `test_settings_ui` 25, `test_pricing_ui` 20). Expect churn in D1 and D3; budget for it rather than being surprised |
| Scope creep into "while we're in here" features | The DoD table is the stop line. Anything not in it is a separate plan |
