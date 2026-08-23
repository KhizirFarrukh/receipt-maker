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

## Open items for Stage 3+

- Store-specific copy still lives in `Templates/terms.html` and `Templates/footer.html`, and
  `Rs. ` / `SALES RECEIPT` / `ONLINE ORDER` are still produced by `receipt_render.py`. All of it
  is Stage 3 (currency, `strings.json`, `receipt_types`, editable terms). Stage 3 **will**
  legitimately change the golden — regenerate it deliberately then.
- No OFL font is bundled (C5); the mechanism is ready and off by default.
- `format_date` and `--preview` are not implemented; both only become meaningful in Stage 3.
- Invoice numbering is still filename-derived. That is Stage 4, and the plan is explicit that it
  is the riskiest correctness change and must be done alone with its own gate.

## Notes / decisions log

- 2026-08-22 — Python absent on this machine; user approved installing 3.12 + deps + Chromium.
- 2026-08-22 — Scope fixed at: fixes + verified build/run + Stage 2. Stage 3+ explicitly out.
- 2026-08-22 — B1 (spec `collect_all` on a missing package) investigated and **withdrawn**: it
  returns empties, it does not raise.
- 2026-08-23 — C5 font/golden conflict resolved as option (a): plumbing landed, default-off, so
  the Stage 2 golden stays byte-identical.
- 2026-08-23 — `signature.html` deliberately not created; it has no purpose before Stage 6.
