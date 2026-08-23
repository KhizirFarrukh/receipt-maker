# Receipt Maker — Session Handoff / Context Archive

**Purpose of this file:** a complete context dump so that if this chat is lost, a fresh AI agent
can read this document (+ [`PLAN-generalization.md`](PLAN-generalization.md) in the same folder) and
continue the work exactly where it was left off — currently: **Stage 2 is done; Stage 3 is next.**

**Read order for a new agent:** (1) this file top-to-bottom, (2) `PLAN-generalization.md` (the full
approved plan / roadmap), (3) [`TASKS.md`](TASKS.md) (what the 2026-08-22/23 session did, and the
open items it left), (4) the test suite, (5) then start Stage 3.

Last updated: 2026-08-23.

---

## 0. UPDATE 2026-08-23 — read this before the older sections

A later session ran on a **different machine** and completed Stage 2. Several statements below are
now stale; this section wins where they conflict. Full detail in [`TASKS.md`](TASKS.md).

**Environment (supersedes §1 "Environment gotchas"):**
- Machine is now `C:\Users\Khizi\...`, **not** `C:\Users\Chichum\...`.
- Python was **not installed at all**; Python **3.12.10** is now at
  `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` (the `py` launcher is not on PATH).
- Playwright **1.62.0** + Chromium 151, pyhanko 0.36.2, cryptography 50.0.0, pyinstaller 6.22.2
  are installed. **The old note that "you cannot render a real PDF here" no longer applies** —
  real PDFs, the full sign/verify round-trip, and the packaged `.exe` build all work and were
  verified end to end.
- Auto-commit is **not** running in this session; the working tree is left uncommitted on purpose.

**The bug that had been silently breaking the gate:** the repo had no `.gitattributes`, so Git's
Windows default (`core.autocrlf=true`) rewrote the LF-committed `tests/fixtures/golden.html` to
CRLF on checkout while the renderer emits LF — so `test_regression_matches_golden` failed on
**every fresh Windows clone**. Fixed with a `.gitattributes`. Do not remove it.

**Stage 2 is complete and the golden is still byte-identical.** New: `template_engine.py`,
`Templates/` (the receipt layout, incl. `header.html`/`footer.html` — the root copies are gone),
a rewritten config layer with `schema_version`/`migrate`/`validate`/atomic writes, a
template-driven `receipt_render.py`, and `cli.py --check` / a real `--config-dir`. Tests went
**11 → 124**. `appsettings.json` is now schema 2.

**Next:** Stage 3 (currency, date_format, receipt_types, strings.json, tax, editable terms page).
Note that Stage 3 **will** legitimately change the golden — it is the stage that replaces the
hardcoded `Rs. `, `SALES RECEIPT`, `ONLINE ORDER` and the Chawla Tech terms copy with config.
Regenerate the golden then, deliberately.

---

## 1. Project snapshot

- **What it is:** a Windows desktop app that generates A4 PDF sales receipts. Python **tkinter** GUI;
  PDFs are produced by building an HTML string and rendering it with headless **Chromium via
  Playwright**; packaged to a standalone `.exe` with **PyInstaller**. Receipts are digitally signed
  (PAdES) with **pyHanko**.
- **The store today:** "Chawla Tech" (chawlatech.pk), currency PKR ("Rs."). The **active project** is
  generalizing this into a **white-label, template-driven, config-driven** receipt maker (neutral
  defaults; the store's identity lives only in local config).
- **Repo:** `github.com/KhizirFarrukh/receipt-maker`. Working branch **`generalization`** (in sync
  with `origin/generalization`). Default/main branch: `master`. Git user: Khizir Farrukh.
- **User:** khizirkfc@gmail.com. Works iteratively and reviews each step carefully (the generalization
  plan went through ~5 rounds of expert review before approval — see `PLAN-generalization.md`).

### Environment gotchas (important — a fresh agent WILL hit these)
- **Playwright is NOT installed in the CLI Python interpreter used here** (Python 3.14 at
  `C:\Users\Chichum\AppData\Local\Programs\Python\Python314`). Therefore you **cannot render a real
  PDF or drive the GUI's live generation** in this environment. The **HTML golden test is the
  authoritative invariant**; the actual `render_pdf` (Playwright) code is exercised only in the user's
  own environment. `python -c "import main; main.run_smoke_test()"` correctly fails with
  `RuntimeError: Playwright is not installed` — that proves wiring, not a bug.
- **tkinter DOES work headlessly here** — tests construct `ReceiptApp` with a withdrawn root.
- **Windows console is cp1252** — writing receipt HTML (contains 🛡️ emoji) to stdout via
  `sys.stdout.write` crashes; `cli.py` writes UTF-8 bytes via `sys.stdout.buffer` instead.
- **Auto-commit/push is active:** work gets committed automatically as commits named `stage 0`,
  `stage 1`, etc., and pushed to `origin/generalization`. Do NOT assume you must commit manually; check
  `git log`/`git status` for reality. (Earlier in the session a *manual* `git push origin develop`
  failed on interactive auth — don't rely on manual push; the auto mechanism handles it.)
- Shell: PowerShell primary; a Git Bash tool is also available (use POSIX syntax there).

---

## 2. Git state (as of this handoff)

- Branch `generalization`, clean working tree, in sync with `origin/generalization`.
- Relevant recent commits (newest first):
  - `02d31a3 stage 1`  ← Stage 1 of generalization (service extraction + generation UX)
  - `bcfc5f0 stage 0`  ← Stage 0 of generalization (baseline harness + fidelity)
  - `0eeaf5c Add PAdES digital signatures for receipt authenticity`  ← the signing feature
  - older: shipping/discount/tax, online/in-store types, scaling, etc.

Everything described below is **already committed**.

---

## 3. Two bodies of work completed this session

### A) PAdES digital signatures  (DONE — commit `0eeaf5c`)
Goal: so the owner (and later customers via the store website) can tell a genuine receipt from a
forgery. Design: each PDF is signed with the store's **private key** (PAdES, whole-file); anyone with
the **public certificate** can verify; a self-signed cert is fine because verification **pins** the
store's own certificate. Forged/edited receipts fail.

- **`receipt_signing.py`** — the crypto core (tk-free): `sign_pdf`, `verify_pdf` (returns
  `VERIFIED` / `INVALID` / `NOT_FOUND`), `is_signed`, `generate_key_pair`. Uses `pyhanko` +
  `cryptography`. Verification pins `signing/certificate.pem` as sole trust root and also checks the
  signer cert DER matches (belt-and-suspenders).
- **`keygen.py`** — one-time `python keygen.py` → self-signed RSA-3072 key pair in `signing/`
  (`private_key.pem` = SECRET, gitignored, never bundle into the exe; `certificate.pem` = PUBLIC).
- **`verify_receipt.py`** — offline CLI verifier + reference impl for the website `/verify` page.
- Config: `signing` block in `appsettings.json` (enabled, key/cert paths, passphrase, signer/reason/
  location, tsa_url).
- GUI: **Tools → Verify Receipt** (3-way result) and **Tools → Sign Existing PDF(s)** (batch-sign old
  unsigned receipts).
- Footer changed from "does not require a signature" → "digitally signed… verify at chawlatech.pk/verify".
- `receipt_maker.spec` bundles pyHanko; `.gitignore` excludes `signing/`, `*.pem`, keys.
- **Verified** end-to-end (crypto): genuine→VERIFIED, tampered→INVALID, forged(diff key)→INVALID,
  unsigned→NOT_FOUND.
- **PENDING (separate project, NOT this repo):** the customer-facing `chawlatech.pk/verify` page.
  Shopify can't do crypto in Liquid — needs a small serverless function reusing `verify_pdf` logic +
  the pinned `certificate.pem`. See memory note `receipt-signing-feature.md`.
- **Note:** a test key pair was generated at `signing/` during dev; the owner may prefer to delete it
  and re-run `keygen.py` to own key creation.

### B) Generalization into a template-driven app  (IN PROGRESS — Stages 0–1 done)
The big refactor. Full roadmap in **`PLAN-generalization.md`**. 9 stages (0–8). **Stage 0 and Stage 1
are complete and committed. Stage 2 is next.**

---

## 4. Current architecture (after Stage 1)

Stage 1 split the former monolithic `main.py` (was 1576 lines) into **three tk-free layers** + a
GUI-only `main.py`. The rule: **`config.py`, `receipt_render.py`, `receipt_service.py`, `cli.py`, and
the pure tests must never import tkinter.** This is enforced by tests (`Stage1Layering`).

| File | Lines | tkinter? | Responsibility |
|---|---|---|---|
| `config.py` | 161 | no | Path constants, receipt/date/filename constants, `DEFAULT_APP_SETTINGS`, config loaders (`load_app_settings`, `load_filename_fields`, `read_html_file`). **Stage 2 adds `schema_version`/`migrate`/`validate`/atomic-writes here.** |
| `receipt_render.py` | 588 | no | `build_html` (the receipt body) + header/footer templates + `render_settings_template`, `inline_local_images`, `escape`, `warranty_policy_html`, logo helpers. **Currently the old inline-HTML implementation, moved verbatim. Stage 2 replaces internals with the template engine.** |
| `receipt_service.py` | 230 | no | Headless generation: `get_invoice_prefix`/`get_next_invoice_number` (numbering), `build_pdf_filename`/`next_available_pdf_path`/`sanitize_filename_part`, `render_pdf` (Playwright), signing glue (`sign_receipt_pdf`, `signing_key_paths`, `resolve_app_path`), and **`generate(data, out_path, progress_cb) -> bool`** (build→render→sign, cleans up on failure). `GENERATION_STEPS = 4`. |
| `main.py` | 923 | YES | GUI only. `ReceiptApp` (form, date picker, item dialog, menu, verify/sign dialogs). Generation runs on a **worker thread** behind a **modal progress dialog**. Module-level UX helpers: `show_error`, `_make_modal`/`_center_over`/`_safe_grab`, rotating `logger`. |
| `cli.py` | 137 | no | Headless entry. Stage-0 harness: `--render-html`, `--freeze-date`, `--invoice-number`, `--config-dir` (reserved), `--out`, `--raw`. Exit codes 0/2/3/4/5. `render_html_from_data()` = the golden target (calls `receipt_render.build_html`). Normalizes the machine-specific `<base href>` to `{{RESOURCE_BASE}}`. |
| `receipt_signing.py`, `keygen.py`, `verify_receipt.py` | — | no | Signing feature (section 3A). |
| `header.html`, `footer.html` | — | — | Playwright page header/footer templates (with `{{company_*}}` placeholders). Stage 2 moves these into `Templates/`. |
| `appsettings.json`, `filename_config.json` | — | — | User config beside the exe (gitignored-ish; appsettings tracked). |

**Data flow for generation (current):** GUI `generate_pdf()` validates form → builds a `data` dict
`{inv_no, date_str, cust, phone, email, items[], receipt_type, shipping}` → resolves filename +
collision on the main thread → `_run_generation(data, out_path)` spawns a worker calling
`receipt_service.generate(data, out_path, progress_cb)`; progress crosses thread→UI via a
`queue.Queue` polled with `root.after`. Success → status + "Open folder?"; failure → `show_error`
(diagnostic, with traceback + log path). Generate button disabled + `_generating` flag guard prevent a
second concurrent job.

---

## 5. Stage 0 & Stage 1 deliverables (detail)

### Stage 0 — baseline harness + fidelity (commit `bcfc5f0`)
- `tests/fixtures/golden_input.json` — deterministic fixture (fixed date/invoice, 3 items exercising
  discount col, tax col, shipping, empty SKU/serial fallbacks, all 3 warranty kinds). **Amounts stored
  as strings** (Decimal-as-strings principle) — `cli._to_build_html_args` bridges to floats for
  today's `build_html`.
- `tests/fixtures/golden.html` — the committed baseline HTML (base href normalized).
- `cli.py` harness renders today's `build_html` output headlessly + deterministically.

### Stage 1 — service extraction + generation UX (commit `02d31a3`)
- Extracted the three tk-free modules (section 4). `build_html` moved **verbatim** (golden guards it).
- Generation UX: modal progress dialog + worker thread; `show_error` (plain summary + expandable
  Details/traceback + Copy + log path); rotating log at `logs/receipt-maker.log`; every dialog modal
  (`transient`+`grab_set`+`wait_window`), main window locked; Generate disabled during a job.

### Test suite — `tests/test_stage0.py` (11 tests, all green here)
Run: `python -m unittest discover -s tests`
- **Stage0Golden**: `determinism` (harness twice → byte-identical), `regression` (matches committed
  golden), `base_href_normalized`, `totals_arithmetic` (TOTAL = Rs. 23,650.00).
- **Stage0Fidelity** `test_headless_matches_gui`: drives the real GUI (`generate_pdf` with
  `_run_generation` stubbed to capture the collected `data`), renders that data via
  `receipt_render.build_html`, asserts it equals the harness output. **This is the check that proves
  the golden faithfully represents the GUI — keep it working.**
- **Stage1Layering**: render path + tk-free modules import with **no tkinter** (subprocess asserts).
- **Stage1GenerationUX**: `success_path` (progress steps 1–4, button re-enabled, status "signed"),
  `error_path_shows_diagnostic` (traceback routed to `show_error`), `concurrent_guard` (second job
  blocked). These stub `receipt_service.generate` so they run without Playwright.
- **Stage0Smoke**: `ReceiptApp` constructs.

### The one-command per-stage gate (from the plan)
```
python -m unittest discover -s tests \
  && python cli.py --render-html tests/fixtures/golden_input.json --out /tmp/gate.html \
  && diff /tmp/gate.html tests/fixtures/golden.html
```
(Full plan gate also runs `cli --check` once Stage 2 implements it.)

---

## 6. Non-negotiable principles (from the approved plan — read `PLAN-generalization.md` §"Governing principles")

1. **Engine stays dumb; renderer precomputes.** Placeholder engine has only `{{key}}` (HTML-escaped),
   `{{key|raw}}` (engine fragments only), `{{#if key}}…{{/if}}`. No loops/logic in templates.
2. **Rendering is a pure function** `render(data, config, templates) -> html` — no clock/IO/globals;
   this is what makes the golden diff + unit tests possible.
3. **No user-visible strings in Python** — they live in templates or `strings.json`.
4. **Escaping is HTML-context only**; user data never eligible for `|raw`, never into CSS/attrs.
5. **Config validated, not trusted** — `validate()` raises plain-language `ConfigError` at startup.
6. **Do no harm** — invoice numbers must not shift; existing PDFs untouched; `.bak` on every rewrite;
   user-edited templates never silently overwritten.
7. **Money is `Decimal`; JSON stores amounts as strings** (data.json/fixtures/sidecar).

Other locked decisions: **files-first** config (GUI is for creating/verifying receipts; settings are
JSON, layout is HTML files); **neutral defaults** ("Your Company", `$`); **reserve-and-keep** invoice
numbering (atomic O_EXCL claim up front, kept on failure, gaps logged); tax has a **mode**
(inclusive/exclusive); line items have a **builtin arithmetic triple** `qty`/`unit_price`/`line_total`
(line_total derived unless `manual`); sidecar reproduction claim is honest (hashes = drift detection;
`store_template_contents` for true reprint).

---

## 7. What to do NEXT — Stage 2 (template + config foundation, behaviour-preserving)

From `PLAN-generalization.md` §"Stage 2". Build:
- **`template_engine.py`** — the safe placeholder engine with **load-time compile/lint** (malformed
  `{{#if}}` → `TemplateError(file,line)`; unknown placeholder rejected against the block's allowed set;
  `{{! template_api_version: 1 }}`). One source of truth for per-block context = `BLOCK_CONTEXTS`.
- **`config.py` enhancements** — `schema_version`, `migrate(cfg)` v1→v2 (restructure old flat schema),
  downgrade guard, `validate(cfg)` (all the enumerated cases), atomic conflict-aware writes (`.bak`).
- **`Templates/`** folder — extract today's HTML (`build_html`, header/footer, warranty page) into
  `styles.css`, `base.html`, `receipt_info.html`, item cell templates, `totals*`, `signature.html`,
  `terms.html`, move `header.html`/`footer.html` in. Record `Templates/.installed.json` hashes **at
  first-run copy time** (needed for the upgrade story).
- **`receipt_render.py` → pure, template-driven** internals (owns `BLOCK_CONTEXTS`, `format_amount`,
  `format_date`, arithmetic contract). Embed fonts (`@font-face` base64, OFL font), block external
  requests during render.
- **Unit tests** for `template_engine` and `config` (stdlib `unittest`).

**CRITICAL for Stage 2:** it must be **behaviour-preserving** — `python cli.py --render-html
tests/fixtures/golden_input.json` must still equal `tests/fixtures/golden.html` (the golden diff must
stay empty), and an old-schema `appsettings.json` must migrate with an **identical next invoice
number**. The golden + fidelity tests are the safety net; run them after every change. If the golden
legitimately must change (it shouldn't in Stage 2), regenerate with
`python cli.py --render-html tests/fixtures/golden_input.json --out tests/fixtures/golden.html` and
justify it.

**Then:** Stage 3 (currency/date/receipt-types/labels/strings/tax + terms page), Stage 4 (invoice
counter migration — isolated), Stage 5 (custom fields + configurable `#` warranty), Stage 6 (signing:
key import matrix + image signature), Stage 7 (archive sidecar + diagnostics), Stage 8 (polish +
PyInstaller/Chromium bundling). Each stage in `PLAN-generalization.md` has its own verify steps.

---

## 8. Reference: how things are verified / commands

```bash
# from repo root
python -m unittest discover -s tests -v      # full test suite (11 tests today)
python cli.py --render-html tests/fixtures/golden_input.json --out /tmp/x.html
diff /tmp/x.html tests/fixtures/golden.html  # must be empty (behaviour-preserving gate)
python -m py_compile main.py config.py receipt_render.py receipt_service.py cli.py
python keygen.py                             # one-time signing key (already generated in dev)
python verify_receipt.py <pdf>               # offline verifier
```
- Deps: `requirements.txt` = playwright, pyhanko, cryptography (Stage-8 will add tzdata). Install
  Playwright browser once: `python -m playwright install chromium` (needed for actual PDF output).
- Persistent memory index for the project: `~/.claude/projects/…/memory/MEMORY.md` (contains
  `receipt-signing-feature.md`).

---

## 9. Open items / reminders
- Website `chawlatech.pk/verify` page — separate project, not started (needs a serverless verifier).
- The approved plan also lives at `~/.claude/plans/new-task-add-receipt-cozy-donut.md`; it's copied
  here as `PLAN-generalization.md` in case that path doesn't persist.
- `Stage0Fidelity` is your regression conscience across the whole generalization — never let it rot.
- If Playwright output ever needs checking, do it in the user's environment; here, trust the HTML
  golden + fidelity + layering tests.
