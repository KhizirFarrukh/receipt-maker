# Plan: Turn the receipt maker into a generic, template-driven app

## Context

The app is currently hardwired to one store (Chawla Tech / PKR / chawlatech.pk). The entire receipt is
a single giant f-string in [build_html](main.py#L1192): fixed receipt fields, fixed line-item columns,
hardcoded currency and labels, a hardcoded 3-way warranty dropdown in [open_item_dialog](main.py#L564),
and an inline terms page in [warranty_policy_html](main.py#L1462). Signing (from the last task)
auto-generates keys only.

**Goal:** a generic, white-label receipt maker where presentation lives in editable HTML template blocks
and content/behaviour lives in JSON config, driven by a small safe rendering engine. Plus: bring-your-own
signing key, custom fields, configurable warranty values, editable/removable terms page, optional image
signature, and a much better UX (clear diagnostic error pop-ups + a modal progress dialog). Delivered in
independently shippable stages.

**Decisions (confirmed):** files-first configuration (GUI is for creating/verifying receipts; settings are
JSON, layout is HTML files); a simple safe placeholder engine (no Jinja2, no arbitrary code); neutral
defaults (`Your Company`, currency `$`, generic terms) — Chawla Tech/PKR live only in the user's local
config. Same stack (Python/tkinter/Playwright/pyHanko). Req 4 (verify existing PDFs) is already built via
**Tools → Verify Receipt** and will be carried into the generic app.

## Governing principles

These exist to settle future arguments cheaply. When a later-stage feature request arrives, it gets
answered by one of these rather than by growing the engine.

1. **The engine stays dumb; the renderer precomputes.** No `{{#unless}}`, no `and`/`or`, no loops, no
   filters beyond `|raw`, no template inheritance. If a template needs a decision, `receipt_render.py`
   computes a boolean or a pre-formatted string and passes it in.
2. **Rendering is a pure function.** `render(data, config, templates) -> html: str` — no clock reads, no
   file writes, no network, no globals. Everything non-deterministic (`now()`, invoice number, absolute
   paths) is injected by the caller. This is what makes the golden-diff gate and the unit tests possible.
3. **No user-visible strings in Python.** Every word that can appear on a receipt or a receipt dialog lives
   in a template or in config (`strings`) — including renderer-derived text ("Page", "DUPLICATE", composed
   warranty text).
4. **Escaping is contextual, and only HTML context is safe.** `{{key}}` escapes for HTML text nodes; values
   are never interpolated into CSS, `style=`, `href`/`src`, or `<script>`. `|raw` is reserved for
   engine-produced fragments; user-entered data is never eligible for it, not even via a custom field.
5. **Config is validated, not trusted.** Deep-merge fills gaps; `validate()` rejects nonsense with a
   file+key message at startup, never mid-render.
6. **Do no harm to existing users.** Invoice numbers must not shift, existing PDFs are untouched, every
   rewritten file gets a `.bak`, and user-edited templates are never silently overwritten.
7. **Money is `Decimal`, and JSON stores it as strings.** Amounts parse to `Decimal` at the form/data
   boundary and never touch `float`. JSON numbers *are* floats, so `data.json`, fixtures, and the sidecar
   store amounts as **strings** — otherwise round-tripping loses exactness and the golden diff flaps.

## Architecture

The single most important structural change: **UI, headless orchestration, and pure rendering become three
separate layers**, because `cli.py`, the golden gate, and every unit test must run without importing
tkinter.

### New modules

**`receipt_service.py`** — headless orchestration, the layer the plan previously lacked. `cli.py` cannot
import `main.py` (that pulls in tkinter and import-time state like [load_app_settings](main.py#L65)). But
generation needs the invoice-number logic ([get_next_invoice_number](main.py#L384)), collision handling
([next_available_pdf_path](main.py#L786)), the Playwright call, the signing call, and the sidecar write —
all of which live in `main.py` today. So they move here: `generate(data, config, progress_cb) -> path`
resolves/consumes the invoice number, calls the pure renderer, drives Playwright, signs, and writes the PDF
+ sidecar. After this, `main.py` is tkinter-only and `cli.py` is argparse-only; both call the service.
**Stage 1's real work is extracting this module** — a bigger change than "wrap generate_pdf," and its true
shape depends on how entangled `build_html`/`generate_pdf` are with `self` and the tk `StringVar`s.

**`template_engine.py`** — safe renderer. Three features only: `{{key}}` (auto HTML-escaped), `{{key|raw}}`
(engine-produced fragments only), `{{#if key}}…{{/if}}`; dotted keys (`{{item.sku}}`). Repetition (line
items, totals rows, custom fields) is done in Python by rendering a small row template N times and joining.

Failure modes are explicit, at load time — not silent. Each template is compiled and linted on start:
- Malformed structure (unclosed/mismatched `{{#if}}`, bad tag, unknown filter) → `TemplateError(file, line,
  message)`.
- Every `{{key}}` is checked against the **allowed-placeholder set the app publishes for that block**, so a
  typo can't silently blank a line item on a legal receipt.
- A known-but-absent value (empty phone, disabled field) renders empty by design; an unknown key is an
  error.
- Templates carry `{{! template_api_version: 1 }}` so a future engine change warns precisely.

Generalizes the existing [render_settings_template](main.py#L1098) pattern.

**One source of truth for per-block context.** The linter's allowed set and the dict the renderer passes
both read the same `BLOCK_CONTEXTS` map in `receipt_render.py`. Allowed set per block = builtin keys
regardless of enabled state ∪ custom keys from `fields.json` ∪ derived keys. Because it depends on config,
lint runs after config load/validate and re-runs when `fields.json` changes; a unit test asserts the
shipped defaults reference only declared keys.

**`config.py`** — one place to load/save config.
- **`schema_version` + `migrate(cfg)` (v1→v2).** Deep-merge only fills missing keys; migration explicitly
  *rewrites restructured* ones (old flat company-only `appsettings.json` → nested
  `currency`/`document`/`receipt_types`; flat currency string → `currency.{…}`), stamps the new version,
  rewrites the file keeping a timestamped `.bak`. **Downgrade guard:** if `schema_version` > the build's
  `SUPPORTED`, fail with "this config was written by a newer version," never a crash. Absorbs
  [load_app_settings](main.py#L65) / [load_filename_fields](main.py#L98).
- **`validate(cfg)`** raising plain-language `ConfigError(file, key, message)` for: `currency.decimals` < 0
  or > 6; empty `receipt_types`; duplicate receipt-type `code`; duplicate field `key`; a custom key
  colliding with a builtin key (even a disabled one), with a key in the other field list, or with an
  engine-reserved name; enabled warranty with no options; `currency.position`/`group_style`/`date_format`
  outside the allowed set; a `filename_config` pattern missing the invoice-number token; a `select` field
  with no options; the line-item arithmetic dependency (below) unsatisfiable; a referenced image/font path
  that doesn't exist. Runs at startup and after any app-side write.
- **Atomic, conflict-aware writes.** temp-file + `os.replace`; mtime checked before writing, conflict → a
  reload-or-overwrite dialog; key order preserved. **Tools → Reload Config** exists.

**`receipt_render.py`** — replaces `build_html`: assembles receipt HTML from `Templates/` + config + entered
data. Pure (principle 2). Owns `BLOCK_CONTEXTS`, `format_amount`, `format_date`, the arithmetic, and the
`type` → formatter/CSS-class map. Reuses [inline_local_images](main.py#L1146) for base64 embedding.

**`cli.py`** — headless entry point calling `receipt_service`/`receipt_render` (never `main.py`). Makes the
per-stage gates mechanical and diagnostics possible.

| Command | Purpose |
|---|---|
| `--render-html data.json --out x.html` | Pure render, no Playwright. The golden-diff target. |
| `--render data.json --out x.pdf` | Full pipeline headless. |
| `--preview [data.json]` | Render sample/given data to temp HTML and open it. Consumes no invoice number. |
| `--check` | Load + migrate-dry-run + validate config, lint all templates. Non-zero on any problem. |
| `--doctor` | `--check` + environment (Playwright browser, key readable + cert expiry, output writable, fonts, versions). |
| `--freeze-date ISO --invoice-number N` | Determinism switches for fixtures/tests. |
| `--app-dir DIR` / `--config-dir DIR` | Point at a specific config/output root. **Required for a hermetic gate** — otherwise `--check` validates whatever is in the user's real APP_DIR and CI becomes machine-dependent. Gates run against neutral defaults in a temp dir. |

Exit codes: `0` ok, `2` config error, `3` template error, `4` render error, `5` signing error.

**UI helpers in `main.py`:** `show_error(...)` (diagnostic modal dialog) and a modal threaded progress
dialog (below).

### Templates/ folder

Bundled read-only defaults in `RESOURCE_DIR`, auto-copied to `APP_DIR/Templates/` on first run (same pattern
as [save_default_app_settings](main.py#L90)).

`styles.css`, `base.html`, `receipt_info.html` (+ `{{custom_receipt_fields|raw}}`), `field_row.html`,
`item_header_cell.html` + `item_row_cell.html` (renderer iterates enabled columns → dynamic columns "just
work"), `totals.html` + `totals_row.html`, `signature.html`, `terms.html`, and `header.html`/`footer.html`
moved here. Plus `Templates/fonts/` (see Determinism).

**Template upgrade path — record hashes at copy time (Stage 2, not later).** First-run copy writes
`Templates/.installed.json`: `{filename: {hash, shipped_version}}`. On a future upgrade this distinguishes
"user edited this" from "last version's default": unchanged → replace silently; modified → leave it, write
the new default as `name.html.new`, report the list once. "Restore default templates" (final stage)
re-copies and re-stamps with a warning.

### Config, string, state & data files (in APP_DIR, files-first)

**`appsettings.json`** — `schema_version`; `company` (name/address/phone/email/website/logo/tax id);
`currency` `{symbol, code, decimals, position, group_style, negative_style}`; `tax` `{mode:
inclusive|exclusive, rows: [...]}`; `date_format` (+ `time_format`, `timezone`); `document`
(title/badge/PDF metadata/page size + **margins that reserve header & footer height**); `receipt_types`
(`{label, code, badge_text}`); `invoice` (prefix/start/`counter_file`/`reconcile_with_filenames`);
`signing`; `signature_image` `{enabled, path, placement, max_width}`; `terms_page` `{enabled}`; `fonts`
`{family, files, fallback}`; `render` `{block_external_requests, timeout_ms, fail_on_missing_image}`;
`archive` `{enabled, sidecar, dir:"invoices/.archive", store_template_contents}`; `logging`
`{level, keep_days}`.

**`strings.json`** — its own file, merged with defaults, so wording edits don't land in the file migration
rewrites and a language pack is a drop-in. Every renderer-derived word lives here (principle 3).

**`state.json`** — machine state, not config: `sticky` last-used field values, window geometry, recent
customers. **Excluded from migration, `.bak`, and mtime-conflict checks** so it doesn't churn hand-edited
config or fight conflict detection.

**`fields.json`** — `receipt_fields` and `line_item_fields`: ordered `{key, label, type, enabled, source,
required, default, sticky, options, width, align, help, manual, formula}` (`manual`/`formula` drive the
line-item arithmetic contract below). Defaults can be disabled; customs append. `warranty`:
`{enabled, label, options}` where an option containing `#` prompts for a number on use.
**The renderer never applies `default`/`sticky`** — absent stays absent; the UI/service applies them when
building `data.json`. Otherwise a `fields.json` edit would change golden output without the fixture
changing, and purity (principle 2) leaks.

**`filename_config.json`** — may reference custom field keys (see Invoice numbering).

**`data.json`** (the CLI/render input, fixture, and sidecar payload format) — carries its own `data_version`
+ validator, with a documented rule for reading a v1 sidecar in a v2 app. Amounts are strings (principle 7).

### Field types (closed set)

`type` drives presentation, not just formatting — dynamic columns need it (a price column is right-aligned
+ `format_amount`; a description is left-aligned + wrapping). Renderer maps `type` → formatter + CSS class;
`item_row_cell.html` exposes `{{css_class}}` / `{{value}}` / `{{label}}`.

| type | widget | validation | render |
|---|---|---|---|
| `text` | Entry | max length | left, escaped |
| `multiline` | Text | max length | left, escaped, `\n`→`<br>` |
| `integer` | Entry | whole number, optional min/max | right |
| `number` | Entry | decimal | right, fixed dp |
| `amount` | Entry | Decimal ≥ 0 (any if refunds) | right, `format_amount` |
| `computed` | — (read-only) | closed formula set (e.g. `qty*unit_price`) | right, `format_amount` |
| `date` | Entry + picker | parses to date | `format_date` |
| `select` | Combobox | must be in options | left |
| `boolean` | Checkbutton | — | `strings.yes`/`.no` |
| `phone`/`email` | Entry | loose warn, never block | left |

`required: true` blocks generation with an inline form error (not a pop-up). `default` pre-fills; `sticky`
remembers across receipts (stored in `state.json`). Unknown `type` → `ConfigError` listing the valid set.

## Amounts, arithmetic & tax

**Line-item arithmetic contract.** `qty`, `unit_price`, `line_total` are a **special builtin triple**:
they can be *hidden* (not shown as columns) but not *removed*. `line_total` is **derived**
(`qty * unit_price`, type `computed`) unless a field marks it `manual: true` (then it's entered and
`unit_price` may be hidden). `validate()` enforces the dependency — you can't hide `unit_price` and `qty`
while leaving `line_total` derived with nothing to derive from. This is the closed-formula answer, not an
expression language (principle 1).

**Rounding is pinned.** Compute in `Decimal`; round each line to `currency.decimals` for display; **sum the
rounded line values** so the printed arithmetic is self-consistent (lines visibly add up to the total).
Unit-tested at 0 and 2 decimals.

**Tax has a mode.** `tax.mode: exclusive` adds tax on top; `inclusive` (EU/PK quoted-inclusive prices) backs
tax out of the shown prices — a receipt in an inclusive market cannot be represented by "add on top" alone.
`tax.rows` = `{label, type: percent|fixed, value, applies_to}`, where **`applies_to` is document-level in
v1** (subtotal / subtotal-after-discount). Per-line tax rates are backlog — they'd imply a per-line tax
column that isn't in the field-type table or the arithmetic contract. Ordering when both a discount and a
tax exist is defined (discount before tax) and documented.

## Invoice numbering, migration & compatibility

**Numbering must not shift, and filename-derived numbering breaks once filenames are configurable.** Numbers
are currently derived by scanning PDF filenames ([get_next_invoice_number](main.py#L384)). When filenames
become config-driven (final stage), a reordered/renamed pattern the scanner can't parse silently resets
numbering to 1 → duplicate numbers on a legal document.

Fix: a **counter file** is the source of truth (seeded at migration from the max scanned number); filename
scanning demotes to a reconciliation check that warns loudly on disagreement. `validate()` enforces every
filename pattern contains the invoice-number token.

**Reservation policy — reserve-and-keep** (resolves the atomicity-vs-consume-on-success tension; you can't
have both without reopening the race). The number is claimed by an **atomic `O_EXCL`/lock-based increment**
up front so two app instances, or app + CLI, can never take the same N (this is one user double-clicking the
exe, *not* the multi-user non-goal). It is **kept even if the render then fails** — reserve-and-release would
reopen the race. This produces gaps in the sequence, which is an audit concern in some jurisdictions, so
**every burned number is logged with its failure reason**. `--preview` reserves nothing. The PDF is written
to a temp file and `os.replace`d into `invoices/` so a failed render never leaves a half-written receipt;
the sidecar is written the same way, and a **sidecar-write failure warns but does not invalidate** the
receipt (the signed PDF is the legal artifact).

Other compatibility rules:
- Migration preserves `invoice.prefix` (`"INV-"`) and receipt-type codes (`W`/`S`) verbatim, or existing
  `INV-W####` files stop matching; a regression check confirms the next number is unchanged before/after.
- Output folder and existing PDFs untouched; [next_available_pdf_path](main.py#L786) collision handling
  preserved.
- Every rewritten config gets a timestamped `.bak`; rollback is a documented **manual** README step.
- Already-generated PDFs are static artifacts — disabling/deleting a field affects future receipts only;
  nothing rewrites an existing PDF. A `filename_config.json` reference to a deleted custom key is caught by
  `validate()` at load with the exact key name.

## Determinism, fonts & offline rendering

- **Fonts.** System-font substitution makes the same receipt differ across OSes (and breaks the golden
  diff). Ship a default **OFL-licensed** font in `Templates/fonts/`, embed via `@font-face` base64, config
  `fonts.family/files`. `--doctor` reports unresolvable fonts.
- **Block external requests during render** (`render.block_external_requests`) — receipts generate offline
  and identically, and can't phone out via a template-referenced CDN (also closes the principle-4 CSS
  exfiltration path).
- **Fail loudly on a missing logo/signature image** instead of a broken-image box.
- **Injected clock and invoice number** for fixtures (`--freeze-date`, `--invoice-number`).
- **`zoneinfo` needs the `tzdata` package on Windows** — add it to requirements.

## Rendering pipeline details

- **Playwright's sync API can't be shared across threads.** `sync_playwright()` is entered *inside* the
  worker (the Stage-1 seam is `generate(data, progress_cb) -> path`), and tracebacks marshal to the UI as
  strings via the queue for `show_error`'s detail pane.
- **Header/footer render in a restricted context.** As Playwright `headerTemplate`/`footerTemplate`,
  `styles.css` does not apply, styles must be inline, and page numbers use the special
  `pageNumber`/`totalPages` classes. `document.margins` must reserve header/footer height or Playwright
  clips them — stated in the templates themselves (a `validate()` can't catch it).
- **Pagination is the classic post-refactor regression.** Document: repeating table headers on page 2+,
  totals kept intact across a break (`break-inside: avoid`), the terms page-break convention users edit
  around, and that the terms page counts toward "Page 1 of N" by default.

## Security & signing

- **Key import is a format matrix.** PKCS#8/PKCS#1 PEM, DER, encrypted PEM (passphrase prompted, in memory
  only, never persisted), PKCS#12/`.pfx` (key+cert), RSA vs EC with a minimum size — each unsupported case
  gets a specific `show_error`, not a stack trace.
- **Key rotation must not invalidate history.** Lean on the signer cert embedded in the CMS, keep a set of
  known certs, and report **integrity valid** (bytes unmodified) and **signer recognized** separately.
- **Self-signed cert expectations in README** — Adobe shows "valid signature, untrusted certificate"; else
  "Verified" here vs a yellow warning in Acrobat looks like a bug.
- **Image signature is decorative, not cryptographic** (README must not blur this); constrain size/DPI,
  allow transparency, warn against putting it in a synced/shared config folder.
- **Key hygiene:** `0600`, kept out of the shared config folder, never logged, passphrase never persisted;
  `--doctor` warns on world-readable keys and near-expiry certs.
- **Sign last, never touch bytes afterward.**
- **Tamper test modifies a byte inside a signed content stream (or edits visible text)** — flipping a random
  byte usually corrupts parsing, a different code path than "signature invalid."

## Cross-cutting: error handling, feedback & logging

- **All dialogs are modal** (`transient` + `grab_set` + `wait_window`) — main window unusable while any
  pop-up is open, codified in one helper and applied to existing dialogs (pattern already in
  [open_item_dialog](main.py#L658)).
- **Diagnostic error pop-ups:** `show_error(title, summary, detail)` — plain-language summary + what to do,
  an expandable Details (traceback), a "Copy details" button, and the log path. Template/config problems
  name the exact file, key, and line.
- **Modal progress dialog on generation:** worker thread; modal Toplevel with progress bar + live status
  ("Validating…", "Building receipt…", "Rendering PDF…", "Signing…", "Saving…"), then "✓ Receipt generated"
  with the path + "Open folder". Status crosses thread→UI via a queue polled with `root.after`. Errors close
  the bar and raise the diagnostic dialog. Batch sign/verify get the same treatment.
- **Generate is disabled while a job runs** — modality isn't enough; a double-click must not spin up two
  workers against the same path/number.
- **Rotating log file** (`APP_DIR/logs/`, last N days): full tracebacks + one line per generation (invoice
  no, template hashes, config version, **Playwright/Chromium build**). Note: **logs can contain customer
  PII** (tracebacks) and users email them to support — say so in the README.

## Archive / audit sidecar

`archive.enabled` writes, in **`invoices/.archive/`** (a subfolder, so PII sidecars aren't mixed into the
browsable receipts folder and mis-attached), a `.json` sidecar per PDF: exact input data, config snapshot,
template hashes, app version, Chromium build, and the invoice number. The README documents what it contains.

**Honest reproduction claim:** hashes give *drift detection*, not reproduction — after a template edit the
old HTML can't be rebuilt from hashes alone. So reprint reproduces the original HTML **only when template
hashes still match**, and warns otherwise; `archive.store_template_contents: true` stores the templates
inline for guaranteed byte-faithful reprint (bigger files, opt-in). Reprint (final stage) re-renders with
the archived data + invoice number (consuming no new number) and stamps `strings.duplicate_badge`.

## Testing & gates

Pure logic gets **stdlib `unittest`** (no new dep), `python -m unittest`; fixtures committed under
`tests/fixtures/`.
- `template_engine`: escaping; `{{#if}}` true/false/nested; `|raw`; dotted keys; repetition/join; malformed
  → `TemplateError` w/ file+line; unknown placeholder rejected; known-empty renders blank; shipped defaults
  reference only `BLOCK_CONTEXTS` keys; `template_api_version` mismatch warns.
- `config`: deep-merge fills only missing keys; `migrate` v1→v2; downgrade guard; `.bak` written; every
  `validate` case raises the right `ConfigError`; atomic write leaves no partial file; key order preserved;
  mtime conflict detected.
- `format_amount`: each `group_style`; 0 and 2 decimals; negative/negative-zero; both `negative_styles`;
  4-digit grouping (1000 vs 1,000); large values. `format_date`: each allowed format.
- Arithmetic/tax: the pinned rounding rule at 0 and 2 decimals (lines sum to total); inclusive vs exclusive
  tax; discount-then-tax ordering; Decimal-as-strings round-trips exactly.
- Invoice numbering: carry-over across migration; counter/filename reconciliation warning; atomic
  cross-process consume; preview/failed render consume nothing.

**Per-stage gate is one command against a temp dir:**
`python -m unittest && python -m cli --config-dir tests/fixtures/env --check && python -m cli --config-dir tests/fixtures/env --render-html tests/fixtures/golden.json --out /tmp/out.html && diff tests/fixtures/golden.html /tmp/out.html`.

## Staged delivery

Each stage compiles, runs, and is verifiable on its own.

### Stage 0 — Baseline harness + fidelity check (before any refactor)
Pin a deterministic fixture (frozen date, fixed invoice number, fixed items, signing off, local logo).
Extract the minimal seam to render today's `build_html` output headlessly with injected clock/number; stand
up the `--render-html`/`--freeze-date`/`--config-dir` CLI skeleton; commit `tests/fixtures/golden.html`
(+ the PDF for eyeballing).

*Verify — two checks, both required:* (a) **determinism** — the harness run twice is byte-identical HTML;
(b) **fidelity** — generate one receipt through the **GUI by hand** with the same inputs and diff it against
the harness output *once*, before committing the golden file, so we don't freeze a subtly-wrong baseline and
faithfully preserve the wrong thing. Stage 0 is itself a small refactor → also run an "app still launches
and generates" smoke test. **Unrecoverable if skipped** — the baseline can't be captured after the refactor.
*Caveat:* because `build_html` still lives in `main.py` here, this harness/CLI skeleton **still imports
tkinter** — it is temporary, and the one-command gate is not hermetic (CI-runnable) until Stage 1 finishes
the service extraction. Don't treat Stage 0's harness as the permanent one.

### Stage 1 — Service extraction + Generation UX
Extract `receipt_service.py` (`generate(data, config, progress_cb) -> path`) so `main.py` is tkinter-only
and `cli.py`/tests import no tk; move `sync_playwright()` into a worker thread; add the modal progress
dialog, `show_error`, the modal-dialog helper, and the rotating log; disable Generate while running; route
existing errors through the new dialog.

*Verify:* generate → progress steps, main window locked, success dialog + Open folder; double-click → one
job; bad key → clear diagnostic dialog with traceback + log entry; **golden HTML unchanged**; `cli.py`
imports and `--render-html` works without tkinter present.

### Stage 2 — Template + config foundation (behaviour-preserving)
`template_engine.py` (load-time compile/lint + `BLOCK_CONTEXTS`), `config.py` (`schema_version`, `migrate`
v1→v2, downgrade guard, `validate`, atomic writes), `Templates/` (defaults extracted from today's HTML,
`.installed.json` recorded at copy), `receipt_render.py` (pure, with the arithmetic contract). Switch
generation to templates; neutralize defaults while the user's config reproduces the current look; embed
fonts; block external requests; ship the `template_engine`/`config`/`format_*`/arithmetic unit tests.

*Verify:* golden HTML diff empty (PDF visually equivalent); old-schema `appsettings.json` migrates with
`.bak` + identical next number; typo'd placeholder / unclosed `{{#if}}` fails at load with file+line;
`python -m unittest` and `--check` pass; editing `styles.css`/`base.html` changes output; `--preview`
consumes no number.

### Stage 3 — Configurable primitives (cosmetic + tax)
Currency (all knobs everywhere amounts render), `date_format`, `receipt_types`, document title/labels/
metadata, `strings.json`, `tax` (inclusive/exclusive + rows), terms page editable + removable (req 5).
**No numbering change here.**

*Verify:* switch currency to `$` and to 0-decimal/no-group, switch date format, disable terms, add a receipt
type, add a 15% tax row both inclusive and exclusive → all reflected; lines sum to total in every rounding
config; footer page numbering correct with/without terms.

### Stage 4 — Invoice counter migration (isolated, its own gate)
The riskiest correctness change, kept alone: introduce the atomic counter file seeded from the current max;
demote filename scanning to reconciliation; add the `O_EXCL`/single-instance guard; `validate()` enforces
the invoice-number token in filename patterns.

*Verify:* next number identical before/after on a populated `invoices/`; two concurrent consumes never
collide; a hand-deleted/renamed PDF triggers the reconciliation warning, not a silent reset; preview/failed
render burn nothing.

### Stage 5 — Custom fields + configurable warranty (req 2, 3)
`fields.json`-driven receipt/line-item fields with enable/disable + user customs; the item dialog and form
build themselves from field defs (widget/validation/required/default/sticky); columns generated from enabled
columns with type-driven alignment/formatting; per-line customs render under the description. Warranty → a
configurable option list where `#` prompts for a **positive whole number** inline (rejects `abc`/`-5`/`0`/
blank), replacing [build_warranty_text](main.py#L670)/[parse_warranty_text](main.py#L690); phrasing → `strings`.
`validate()` guards duplicate/cross-list/builtin/reserved key collisions.

*Verify:* add a custom receipt field + a custom column, disable a default column, define
`# Months International Warranty` → all render with right-aligned amounts; number prompt accepts `12`,
rejects `-5`/`abc`; a required field blocks generation inline; a duplicate/reserved key → clear
`ConfigError`; lint re-runs after a `fields.json` change.

### Stage 6 — Signing enhancements (req 1, 6; confirm req 4)
Bring-your-own key: `keygen.py` + `receipt_signing.py` import an existing private key across the format
matrix (derive/attach a self-signed cert, or accept a supplied cert) alongside auto-generate; **Tools →
Signing Keys** shows status/expiry/import/generate; known-cert set so rotation preserves historical
verification (integrity + signer reported separately). Image signature via `signature_image` +
`{{signature_image}}`; user picks image, crypto, or both. Confirm Verify Receipt on generic output.

*Verify:* import PKCS#8/PKCS#1/encrypted-PEM/`.pfx` → sign+verify; unsupported → specific messages; rotate
key → old receipts still verify ("signer: previous key"); image signature appears; genuine/tampered/unsigned
→ Verified/Invalid/Not found; `--doctor` flags a world-readable key.

### Stage 7 — Archive & diagnostics
`archive` sidecar in `invoices/.archive/` per receipt; `--doctor` / **Tools → Diagnostics**; log-viewer link
in `show_error`; config reload + mtime-conflict wired into the UI.

*Verify:* sidecar matches input; **regenerate from sidecar reproduces the same HTML when template hashes
match, warns when they don't** (and `store_template_contents` reproduces regardless); `--doctor` reports a
missing browser, unreadable key, expiring cert, read-only output; external edit + in-app save → conflict
dialog, no clobber.

### Stage 8 — Polish
"Restore default templates" (modified-file warnings) + `.new`-file upgrade classification; filename config
with custom fields + token validation; reprint-from-sidecar with duplicate badge; README rewrite documenting
every config key (incl. the PII notes for logs/sidecars and the self-signed-cert expectation); neutral QA
pass; PyInstaller `--add-data` for `Templates/`+`fonts/`, **pinned Playwright version + verified Chromium
bundling/first-run copy from a `sys._MEIPASS` read-only build** (Chromium isn't bundled by default and is
large; version drift changes layout, so the HTML diff stays authoritative and the PDF check is advisory).

## QA matrix (run at Stage 2 and again at Stage 8)

| Case | Watching for |
|---|---|
| 1 line item | No dangling headers/empty totals |
| 40 line items | Pagination, repeating headers, totals not split, page numbers |
| Zero line items | Renders sensibly, doesn't crash |
| No customer, no logo, all optional fields empty | No stray labels or blank rows |
| 200-char description, very long company name | Wraps, never clips |
| Very large amount, 0-decimal currency, negative line | Formatting/grouping; inclusive & exclusive tax |
| Terms page on and off | Page count, break placement |
| All defaults disabled + only custom fields | Table still coherent; arithmetic contract holds |
| Bundled (PyInstaller) first run into an empty APP_DIR | Templates+fonts copied, config created |
| Non-ASCII (Urdu/Arabic/accented) name & company | Escaping, font coverage, no mojibake |

## Files

- **New:** `receipt_service.py`, `template_engine.py`, `config.py`, `receipt_render.py`, `cli.py`,
  `Templates/*` + `Templates/fonts/*`, `strings.json`, `state.json` (runtime), `fields.json`, `tests/`
  (+ committed fixtures).
- **Modify:** [main.py](main.py) (tkinter-only: progress/error UX, modal dialogs, form/item dialog from
  field defs, menu, reload/diagnostics), [receipt_signing.py](receipt_signing.py) + [keygen.py](keygen.py)
  (key-import matrix, known-cert set, image-sig helper), [receipt_maker.spec](receipt_maker.spec) (bundle
  `Templates/`+`fonts`, pin Playwright), [appsettings.json](appsettings.json),
  [requirements.txt](requirements.txt) (`tzdata`), [README.md](README.md). Retire the inline HTML in
  [build_html](main.py#L1192)/[warranty_policy_html](main.py#L1462) into templates.

## End-to-end verification

Per-stage checks above. Overall: from neutral defaults configure a fake store (currency + group style, date
format, a custom receipt field, a custom line-item column, a `#` warranty option, an inclusive tax row, an
imported key, image signature, edited terms), generate watching the progress dialog, open the PDF to confirm
every element renders and the arithmetic is self-consistent, then Tools → Verify Receipt → Verified; tamper a
content byte → Invalid; disable signing → unsigned + "Not found". Regenerate from the sidecar → identical
HTML. Trigger deliberate errors (missing template, typo'd placeholder, bad key, malformed config) to confirm
each pop-up names the file/key/line, the main window stays locked, and the log captures the traceback.

## Non-goals (for this project)

Multi-currency within one receipt · RTL layout and i18n beyond editing templates/strings · a GUI settings
editor (files-first is the decision) · template loops, inheritance, or expressions · PDF/A conformance ·
RFC 3161 timestamping authority · a trusted (CA-issued) certificate chain · multi-user or networked invoice
counters · emailing receipts · database storage.

## Backlog (post-1.0, ordered by likely demand)

1. Void / credit note as a receipt type with negative amounts (needs `negative_style`, already in Stage 3).
2. Config profiles — multiple brands in one install (`--profile`, or `APP_DIR` via env var).
3. QR code placeholder encoding a verification hash/URL (pure-python `qrcode` → base64; adds a dependency).
4. Amount in words (`{{amount_in_words}}`) — common on South-Asian/Gulf invoices; English-only initially.
5. Draft autosave / crash recovery for in-progress form entry.
6. Batch generate from a CSV, reusing the CLI and progress dialog.
7. Per-line tax rates (a line-item tax column) — v1 tax is document-level only.
