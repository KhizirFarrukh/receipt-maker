# Working checklist — build/run verification + Stage 2

Created 2026-08-22. Companion to [HANDOFF.md](HANDOFF.md) and
[PLAN-generalization.md](PLAN-generalization.md). This is the live task list; tick items as
they land so nothing is lost if the session is interrupted.

**Agreed scope (user, 2026-08-22):** install Python + deps + Chromium and genuinely verify
build/run → fix the concrete defects → then implement Stage 2 in full. Stop before Stage 3.

**Machine note:** this is a *different* machine from the one in HANDOFF.md (`C:\Users\Khizi`,
not `C:\Users\Chichum`). Python was **not installed at all** here — only the Microsoft Store
`WindowsApps\python.exe` stub. Anything HANDOFF.md says about the local interpreter is stale.

---

## Phase A — Environment & verification baseline

- [x] A1. Install Python 3.12 (winget, user scope). → **3.12.10** at
      `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`, tkinter 8.6 present. Note: the
      `py` launcher is not on PATH in this shell; use the full path.
- [x] A2. Deps installed: playwright 1.62.0, pyhanko 0.36.2, cryptography 50.0.0,
      pyinstaller 6.22.2, oscrypto 1.3.0, tzdata 2026.3; Chromium 151.0.7922.34 downloaded.
- [x] A3. `python -m unittest discover -s tests -v` → **11/11 green** (after B0).
- [x] A4. Golden gate → **diff empty** (after B0).
- [x] A5. `python main.py` → window opens, mainloop runs, no exception.
- [x] A6. `python main.py --smoke-test` → **503,960-byte PDF**. First time the real Playwright
      path has been exercised at all — HANDOFF.md §"Environment gotchas" says it could not be.
- [x] A7. `.\build_exe.ps1` → **exit 0**, `ReceiptGenerator.exe` (7.7 MB, 860 MB bundle,
      Chromium + headless shell bundled), packaged `--smoke-test` passed.
- [x] A8. Packaged exe launches and holds a window.
- [x] A9 *(added)*. End-to-end crypto round-trip through the real `receipt_service.generate()`,
      using a throwaway key in the scratchpad so the repo's `signing/` stays untouched:
      genuine → **VERIFIED**, tampered → **INVALID**, forged-with-other-key → **INVALID**,
      unsigned → **NOT_FOUND**. All four correct.

**Answer to "is it buildable and runnable on Windows?" — yes, but only after B0.** On a fresh
Windows clone it was not: the Stage 0 gate failed on checkout line endings before any code ran.

## Phase B — Defects found in static review

Each needs confirming against a real interpreter before/after the fix (several are only
reachable at build time).

- [x] **B0. CRLF checkout broke the Stage 0 gate on Windows — found and fixed.** The repo had no
      `.gitattributes`. Git's Windows default `core.autocrlf=true` rewrote the LF-committed
      `tests/fixtures/golden.html` to CRLF on checkout (261 CR bytes), while the renderer emits
      LF and `test_regression_matches_golden` reads with `newline=""` (no translation) — so
      **every fresh Windows clone failed its own gate before any code was touched**. This is
      almost certainly why HANDOFF.md's environment notes read as they do. Fix: added
      `.gitattributes` (`* text=auto eol=lf`, CRLF kept for `*.ps1`/`*.cmd`/`*.bat`, binaries
      marked) and normalized the working tree + index. `git diff` confirms **no content
      changed** — only line endings. 11/11 tests now pass.

- [x] B1. ~~`receipt_maker.spec` `collect_all()` over a fixed package list fails the build if one
      is absent.~~ **Not a defect — tested and withdrawn.** `collect_all('missing_pkg')` returns
      `([], [], [])` with a warning; it does not raise. `oscrypto` is in any case installed
      (1.3.0, pulled in by the pyhanko stack). No change needed.
- [x] B2. **Build could hang forever.** The spec builds windowed (`console=False`,
      `disable_windowed_traceback=False`), so a *failing* `--smoke-test` pops a modal traceback
      dialog while `Start-Process -Wait` waited with no timeout — and with no console attached
      the reason never surfaced. Fixed: `run_smoke_test()` now catches everything, logs the
      traceback and returns an exit code (it can no longer raise into the bootloader); both build
      scripts bound the wait (180 s) and print the log tail on failure. Verified against the
      packaged exe: exit 0, PDF produced.
- [x] B3. **Packaged app ignored edited `header.html` / `footer.html`.** `config.HEADER_FILE`
      resolved against `RESOURCE_DIR` (`sys._MEIPASS` when frozen), so the bundled copies always
      won and the files were never placed beside the exe — while README's "Branding" section told
      users to edit them. Fixed by `branding_template_path()`, which now searches
      `APP_DIR/Templates` → `APP_DIR` → bundled. Superseded and generalised by C3's `Templates/`.
- [x] B4. **`cli.py` surface was stale and partly dead.** Docstring and the "Stage 0 supports
      only…" error predated Stage 1; `--config-dir` was accepted and **silently ignored**, so a
      gate could render against the wrong config while looking correct. Docstring rewritten;
      `--config-dir` is now genuinely implemented via `config.set_app_dir()` (C8).
- [x] B5. `tzdata` pinned in `requirements.txt` (it was arriving only transitively).
- [x] B6. **Neutrality leaks fixed where they were live defects.** `receipt_signing.py` hardcoded
      the certificate subject to "Chawla Tech" and ignored `signing.signer_name` — proven by A9,
      where a config saying "Your Company" produced a cert saying "Chawla Tech". Now
      configuration-driven with neutral fallbacks; `keygen.py` derives it from appsettings and
      accepts `--org-name`/`--common-name`; the signature field name is neutral.
      Shipped `signing` defaults neutralised. **Still store-specific and deliberately deferred:**
      the terms page and footer copy in `Templates/terms.html` / `Templates/footer.html`. Those
      are Stage 3 (`strings.json`, editable/removable terms) and cannot change here without
      breaking the Stage 2 byte-identical golden gate.
- [x] B7. Checked. Nothing under `Templates/` is caught by `.gitignore`'s blanket rules;
      `Templates/.installed.json` added to `.gitignore` as per-install runtime state.

## Phase C — Stage 2 (template + config foundation, behaviour-preserving)

Spec: PLAN-generalization.md §"Stage 2", §"New modules", §"Templates/ folder", §"Config, string,
state & data files", §"Amounts, arithmetic & tax", §"Testing & gates".

- [x] C1. `template_engine.py` — `{{key}}`, `{{key|raw}}`, `{{#if key}}…{{/if}}`, dotted keys,
      load-time compile + lint, `TemplateError(file, line, message)` with a "did you mean"
      suggestion, `{{! template_api_version: 1 }}`. 37 tests.
- [x] C2. `config.py` — `schema_version` (=2), `migrate()` v1→v2, downgrade guard, `validate()`
      raising `ConfigError(file, key, …)`, atomic write with timestamped `.bak` and mtime
      conflict detection. 47 tests.
- [x] C3. `Templates/` — `base.html`, `styles.css`, `receipt_info.html`, `items_table.html`,
      `item_header_cell.html`, `item_row_cell.html`, `totals.html`, `totals_row.html`,
      `terms.html`; `header.html`/`footer.html` moved in (root copies removed). First-run copy
      into `APP_DIR/Templates/` records `.installed.json` hashes **at copy time**. An existing
      install's edited flat-layout file is used as the seed instead of the shipped default, so
      the new location cannot silently revert someone's branding.
      *Not created:* `signature.html` — it exists only to host the Stage 6 image signature, so
      shipping an unused file now would be speculative.
- [x] C4. `receipt_render.py` — template-driven; `render_receipt(data, templates)` is pure, with
      the base URL and font payload injected. Owns `BLOCK_CONTEXTS`, `ITEM_COLUMNS`,
      `format_amount`, and the Decimal arithmetic contract (each line rounded, rounded lines
      summed). *`format_date` deferred:* dates are still passed through as preformatted strings;
      it only becomes meaningful with Stage 3's `date_format` config.
- [x] C5. Fonts — resolved as **option (a)**, as recommended above. `fonts.{family,files,
      fallback}` config, `@font-face` inlined as base64, wired into `base.html` via
      `{{font_faces|raw}}`. Empty by default, so the golden diff stays empty. Generated as an
      engine-produced fragment with the family name stripped to `[A-Za-z0-9 _-]`, because
      principle 4 forbids interpolating a user value into a CSS context (escaping cannot make
      CSS safe the way it does an HTML text node).
      *Remaining:* no OFL font file is bundled — fetching one needs network access, and adding an
      unverified binary blob is not something to do silently. The mechanism is ready; drop a font
      in `Templates/fonts/` and name it in `fonts.files`.
- [x] C6. `render.block_external_requests` enforced via a Playwright route that aborts
      http/https/ftp during render. Verified the real PDF still renders (identical bytes).
- [x] C7. Unit tests — 124 total, up from 11.
- [x] C8. `cli.py --check` (validate config → lint every template → render a sample) and a real
      `--config-dir` via `config.set_app_dir()`. Verified hermetic: pointed at an empty directory
      it seeds config + Templates + manifest and passes.
- [x] C9. **Stage 2 gate — all green.** Golden **byte-identical** (9,738 bytes); v1 config
      migrates with a `.bak` and an **identical next invoice number**; typo'd placeholder and
      unclosed `{{#if}}` each fail at load naming file and line; 124 tests and `--check` pass;
      editing `styles.css` changes output.

## Phase D — Wrap-up

- [x] D1. Whole gate re-run after Stage 2: 124 tests, golden byte-identical, `--check` (repo and
      hermetic), full `.exe` rebuild, packaged smoke test, GUI launch, and the end-to-end
      sign/verify matrix — all green. Also confirmed a template edited **beside the packaged exe**
      changes the generated PDF with no rebuild, which is the whole point of Stage 2.
- [x] D2. `HANDOFF.md` updated with a leading 2026-08-23 section correcting the stale environment
      notes and recording Stage 2 as done / Stage 3 as next.
- [x] D3. `README.md` updated: new Templates section (files, syntax, upgrade story, fonts), the
      new `appsettings.json` keys, `cli.py --check`, neutral signing example, packaged-install
      template behaviour.
- [x] D4. Reported.

---

## Phase E — Stage 3 (configurable primitives: cosmetic + tax)

Spec: PLAN-generalization.md §"Stage 3". **No numbering change in this stage** (that is Stage 4,
deliberately isolated).

**Key decision on the golden.** The plan says gates should run against neutral defaults, which
would mean regenerating `golden.html` the moment currency becomes configurable. Doing that throws
away the regression net exactly when the riskiest cosmetic changes land. Instead:
`tests/fixtures/env/` pins a config equivalent to **today's** behaviour (Rs., 2dp, grouped
totals), so **golden.html stays byte-identical through all of Stage 3** and proves the refactor
changed nothing. Neutral defaults, `$`, 0-decimal, ungrouped, inclusive tax etc. get their own
unit tests instead. The golden's job is catching regressions, not advertising defaults.

- [x] E1. Hermetic gate — `tests/fixtures/env/` with a pinned `appsettings.json`; the golden gate
      and every render-based test run against it via `--config-dir`, so output no longer depends
      on the developer's own APP_DIR.
- [x] E2. `strings.json` — column headings, totals labels and the empty-cell marker moved out of
      Python; deep-merged over defaults so a partial file (a translation) works.
- [x] E3. `currency` — `{symbol, symbol_space, code, decimals, position, group_style,
      negative_style, group_line_amounts}`, applied everywhere amounts render. Grouping supports
      `thousand`, `indian` (lakh/crore) and `none`. **Migration v2→v3 seeds existing configs with
      the pre-Stage-3 values** (`Rs.`, 2dp, ungrouped line amounts) so no one's receipts change
      currency or spacing; only fresh installs get the neutral `$`. Verified against the user's
      real config.
- [x] E4. `date_format` + `config.date_display_format()` / `date_parse_formats()`; the GUI reads
      both instead of module constants. *`format_date` on a date object is still unnecessary —
      the renderer receives a preformatted string by design (principle 2).*
- [x] E5. `receipt_types` config (`label`/`code`/`badge_text`/`legacy_unlettered`) driving the
      dropdown, the invoice prefix and the badge. Codes `W`/`S` preserved. `validate()` rejects
      duplicate codes or labels, filename-unsafe codes, and two types both claiming the legacy
      unlettered series.
- [x] E6. `tax` — `mode: exclusive|inclusive` with document-level `rows`
      (`percent`/`fixed`, `applies_to`). Inclusive rows are backed out and labelled *(included)*
      rather than added; several inclusive percent rows share a single back-out so they cannot
      compound against each other. Discount-before-tax ordering documented and tested.
- [x] E7. `terms_page.enabled` — the page is dropped cleanly, with no stray whitespace, via an
      `{{#if}}` in `base.html`.
- [x] E8. Tests: **181 total** (was 124). Currency permutations, rounding self-consistency at 0
      and 2 decimals, inclusive vs exclusive tax, receipt-type and tax validation, migration
      behaviour. Golden **still byte-identical**, and the real PDF is byte-for-byte the same size
      as before Stage 3.

**Two traps found and fixed during E8, both of which would have silently rotted the gate:**

1. `tests/fixtures/env/Templates/` is seeded on first use and — correctly, for a real install —
   never overwritten afterwards. For the *gate* that meant edits to the repository's own
   `Templates/` stopped reaching the tests: one golden check passed against a stale layout before
   this was caught. `tests/gate_env.py` now clears the seeded copy on entry, so the repository
   stays the single source of truth.
2. `.gitignore`'s generic virtualenv rule `ENV/` matched `tests/fixtures/env/`, so the entire
   pinned gate fixture would never have been committed and a fresh clone's gate would fail.
   Negation rules added, ordered after the rule they undo.

## Phase F — Stage 4 (invoice counter migration, isolated)

Spec: PLAN-generalization.md §"Stage 4" and §"Invoice numbering, migration & compatibility".
The plan calls this the riskiest correctness change and insists it ships alone with its own gate.
**Duplicate invoice numbers on a legal document are the failure mode to design against.**

Why it is needed: numbering is derived by scanning PDF filenames. Once filenames become
configurable (Stage 8) a reordered or renamed pattern the scanner cannot parse silently resets
numbering to the start — reissuing numbers that are already on customers' receipts.

- [x] F1. `invoice` config block (`prefix`, `start`, `counter_file`,
      `reconcile_with_filenames`); migration v3→v4 preserves `INV-` and the start verbatim.
      `validate()` also rejects a prefix ending in a digit — `INV1` + `1001` reads as `INV11001`,
      and the boundary can never be recovered from the filename.
- [x] F2. `invoice_counter.py` — counter file is the source of truth, seeded from the current
      filename maximum so the next number does not move. `peek()` reads without consuming,
      `reserve()` consumes under an `O_EXCL` lock with stale-lock recovery.
- [x] F3. Reserve-and-keep, with `note_unused()` logging every burned number and its reason.
- [x] F4. Reconciliation is one-directional: files ahead pull the counter forward, files behind
      only warn. A corrupt counter file **refuses to load** rather than silently restarting the
      sequence — restarting is precisely how duplicates get issued.
- [x] F5. Atomic PDF write — render and sign a `.partial` in the target directory, then
      `os.replace`. A failed run now never creates the receipt at all, instead of creating one
      and deleting it afterwards.
- [x] F6. The editable invoice-number field still works: a hand-typed number is honoured and
      `claim_at_least()` pushes the counter past it.
- [x] F7. **31 Stage 4 tests**, including 100 reservations across 4 real OS processes coming back
      contiguous with no duplicates.
- [x] F8. Gate: 212 tests green, golden byte-identical, `--check` clean, real PDF renders, full
      sign/verify matrix still correct, packaged build.

*Deferred with reason:* `validate()` enforcing an invoice-number token in filename patterns is on
the Stage 4 list, but filename *patterns* do not exist yet — the filename is built from an ordered
field list with the number always first, so there is no token to require. It belongs with Stage 8.

*Deferred with reason:* `validate()` enforcing an invoice-number token in filename patterns is in
the Stage 4 list, but filename *patterns* do not exist yet — filenames are built from an ordered
field list with the number always first, so there is no token to require. It belongs with Stage 8,
where patterns are introduced.

## State left behind

- Working tree is **uncommitted** on branch `generalization` (auto-commit is not running in this
  session). `header.html`/`footer.html` are staged as deleted — they moved into `Templates/`.
- `appsettings.json` was migrated in place to schema 2 and gained `document`, `render` and
  `fonts` sections. Two timestamped `.bak` files sit beside it (gitignored) — the safety net
  working as designed; delete them whenever.
- VS Code's selected Python interpreter is not the newly installed one, so the editor shows
  "package not installed" hints. Point it at
  `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`.
- No signing key exists in `signing/`. Signing is enabled by default, so **the app cannot generate
  a receipt until `python keygen.py` is run once** — that is by design. The end-to-end crypto
  checks used a throwaway key in a temp directory precisely so key creation stays the owner's.
  Set `signing.signer_name` in `appsettings.json` *before* running it.

## Open items for Stage 5+

- **Stage 5 — custom fields + configurable warranty** is next: `fields.json`-driven receipt and
  line-item fields, the item dialog building itself from field definitions, and a warranty option
  list where an option containing `#` prompts for a positive whole number. That last one finally
  removes `NO_WARRANTY_LABEL`, the sentinel `receipt_render` and the GUI currently share.
- Store-specific copy still lives in `Templates/terms.html` and `Templates/footer.html` (Chawla
  Tech wording, chawlatech.pk links). These are now plain editable templates, so this is a
  content edit rather than a code change — but shipping them as the neutral default is still
  outstanding.
- `SALES RECEIPT`, `Receipt No:`, `Bill To:` etc. are literals inside `receipt_info.html`. That
  satisfies "no user-visible strings in Python" and they are user-editable, but a `document.title`
  config key was never added.
- `NO_WARRANTY_LABEL` is a sentinel shared between `receipt_render` and the GUI's warranty
  dropdown. Stage 5's configurable warranty options replace both.
- No OFL font is bundled (C5); the mechanism is ready and off by default.
- `--preview` is still not implemented.
- Per-line tax *rates* remain out of scope by design — the per-line `tax` column is an amount,
  and v1 tax is document-level (plan backlog item 7).

## Notes / decisions log

- 2026-08-22 — Python absent on this machine; user approved installing 3.12 + deps + Chromium.
- 2026-08-22 — Scope fixed at: fixes + verified build/run + Stage 2. Stage 3+ explicitly out.
- 2026-08-22 — B1 (spec `collect_all` on a missing package) investigated and **withdrawn**: it
  returns empties, it does not raise.
- 2026-08-23 — C5 font/golden conflict resolved as option (a): plumbing landed, default-off, so
  the Stage 2 golden stays byte-identical.
- 2026-08-23 — `signature.html` deliberately not created; it has no purpose before Stage 6.
