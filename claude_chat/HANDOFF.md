# Handoff — start here

A complete context dump. If the chat history is gone, read this file and the ones it points to and
you will know what this project is, what state it is in, and what to do next.

**Last updated: 2026-08-28.** Branch `generalization`, in sync with `origin`.

## Read order

1. **This file** — state, environment, how to verify.
2. [`ARCHITECTURE.md`](ARCHITECTURE.md) — what each module does and the invariants that must hold.
3. [`DECISIONS.md`](DECISIONS.md) — the choices already made and *why*. Read before changing
   anything that looks odd; most of the odd things are deliberate.
4. [`PITFALLS.md`](PITFALLS.md) — traps in this codebase and this environment that have already
   cost real time. Read before running anything.
5. [`../TODO.md`](../TODO.md) — what to do next.
6. [`TASKS.md`](TASKS.md) — historical session log, kept for reasoning that did not fit elsewhere.
7. [`PLAN-generalization.md`](PLAN-generalization.md) — the original architecture plan.
   **Partly superseded** — see DECISIONS.md.

---

## What this is

A Windows desktop app that generates A4 PDF sales receipts. Python **tkinter** GUI; PDFs are built
as HTML and rendered by headless **Chromium via Playwright**; packaged to a standalone `.exe` with
**PyInstaller**. Receipts are digitally signed (PAdES) with **pyHanko**.

It began hardwired to one shop (one shop, in one currency). It is now white-label and configuration-driven:
currency, dates, receipt types, tax, fields, columns, warranty options, templates and policy links
are all configurable, and all editable **inside the app**.

Repo: `github.com/KhizirFarrukh/receipt-maker`. Working branch **`generalization`**; default branch
`master`. **The generalization work has never been merged to master.**

## State

Stages 0–6 of the original plan are complete, plus a large block of user-requested work
("Phase H"). **481 tests pass**; the golden-HTML gate is green; the packaged `.exe` builds and runs.

What exists now, beyond the original app:

| Area | State |
|---|---|
| Templates | Receipt layout lives in `Templates/*.html` + `styles.css`, rendered by a small safe engine |
| Config | `appsettings.json` schema v4, with migration, validation, atomic writes and `.bak` |
| Currency / dates / tax / receipt types | Configurable, including inclusive vs exclusive tax |
| Fields & columns | `fields.json` drives the item table *and* the item form |
| Invoice numbering | Counter file, cross-process safe, reserve-and-keep |
| Signing | Create or import keys in-app; key rotation preserves old receipts |
| Receipt history | Every receipt recorded; reload one to correct and reissue |
| Products | Catalogue with variants, barcode lookup, optional stock deduction |
| In-app editing | Tools → Products / Receipt History / Settings / Fields & Columns / Signing Keys |

## How it got here

The sessions of 2026-08-22 → 08-28, in order. Every commit is on `generalization`.

| Commit | What and why |
|---|---|
| `ba7071b` | **`.gitattributes`.** The repo had none, so Git's Windows default rewrote the LF-committed golden fixture to CRLF on checkout — *every fresh Windows clone failed its own gate before any code was touched.* This is why "is it buildable on Windows?" was originally "no". |
| `6319cec` | **Stage 2** — template engine, `Templates/`, config schema/migration/validation, pure renderer. Plus four build defects: a build that could hang forever, a packaged app that ignored edited templates, a silently-ignored `--config-dir`, and a hardcoded store name in the certificate. |
| `81ee5af` | **Stage 3** — currency, dates, receipt types, tax (inclusive/exclusive), terms page, `strings.json`. Golden stayed byte-identical by pinning the gate fixture to pre-Stage-3 output. |
| `05e709d` | **Stage 4** — the invoice counter. The plan's riskiest change, shipped alone. Concurrency proven with four real OS processes taking 100 numbers with no duplicates. |
| `205c744` | **Four bugs** found reviewing Stage 4: counter unusable in a fresh folder, a silently burned number on cancel, a config error escaping as a traceback, and `CON.pdf` being unwritable on Windows. |
| `39b9a7e`, `33d1825` | **Stage 5** — `fields.json` drives the item columns *and* the item form; configurable warranty with `#` prompting; receipt-level fields; sticky values. |
| `b2424d1` | **User request list recorded** as Phase H — including the reversal of the plan's files-first non-goal. |
| `7fe4e6d` | **Logo diagnostics + remembered prompts.** Diagnosed the user's missing logo: their file is `logo.png.png`. |
| `f61c3a8` | **In-app Settings and Fields editors.** |
| `a2f3208` | **In-app signing keys**, and key rotation that no longer invalidates previously issued receipts. |
| `1ae7a83` | **Footer** — removed the signature notice, added clickable policy links. |
| `bd9f900` | **Barcode** (distinct from serial number) and **receipt history** with reload-and-reissue. |
| `cd17e34` | **Product catalogue** with variants, lookup and pricing arithmetic. |
| `d890c86` | **Stock deduction**, settled as commit-on-success — the opposite of invoice numbering, for a reason. |

Tests grew **11 → 481** across this work. The golden gate held byte-identical from Stage 2 through
Stage 5 and moved once, deliberately.

## Environment

**This machine is `C:\Users\Khizi`.** Earlier notes referring to `C:\Users\Chichum` are from a
different machine and are stale.

- **Python 3.12.10** at `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`.
  The `py` launcher is **not** on PATH — use the full path.
- Installed: playwright 1.62.0 (+ Chromium 151), pyhanko 0.36.2, cryptography 50.0.0,
  pyinstaller 6.22.2, tzdata.
- Real PDF rendering, the full sign/verify round trip, and the packaged build **all work here**.
  (An older note claiming PDFs could not be rendered locally is stale.)
- VS Code's selected interpreter is *not* this one, so the editor shows spurious "package not
  installed" hints. Point it at the path above.

## How to verify — run all of this before saying anything works

```powershell
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"

# 1. Whole suite (~20s). See PITFALLS.md: always run it under a timeout.
& $py -m unittest discover -s tests

# 2. The golden gate — the receipt HTML must be byte-identical.
Remove-Item -LiteralPath "tests\fixtures\env\Templates" -Recurse -Force -ErrorAction SilentlyContinue
& $py cli.py --config-dir tests/fixtures/env --render-html tests/fixtures/golden_input.json --out "$env:TEMP\gate.html"
# then compare $env:TEMP\gate.html byte-for-byte with tests\fixtures\golden.html

# 3. Config + templates load cleanly.
& $py cli.py --check

# 4. A real PDF through Playwright.
& $py main.py --smoke-test

# 5. The packaged build (~4 min). Run it WITHOUT redirecting stderr — see PITFALLS.md.
.\build_exe.ps1

# 6. Coverage. .coveragerc sets fail_under = 80, so this exits non-zero below it.
& $py -m pip install coverage      # once
& $py -m coverage run -m unittest discover -s tests
& $py -m coverage report           # add --sort=miss to see the worst offenders
& $py -m coverage html             # browsable report in htmlcov/
```

**1358 tests, 90.4% coverage**, with a floor of 80% enforced by `.coveragerc`. Branch coverage is
on, so an untested `else` counts as a miss.

`money`, `shipments`, `keygen` and `verify_receipt` are at 100%; `installments` 98%,
`receipt_render` 97%, `product_catalogue` 96%. The lowest is `main.py` at 85% — the GUI, where what
is left needs a real display or a real event loop.

A note on the shape of that number: adding §6 pushed the total *down* to 88.6% before these tests
were written, because ~1000 statements of new feature code arrived faster than the tests for
constructing its dialogs. The handlers were tested as they were built; the widget-building was not.
Worth remembering that a healthy percentage is a ratio, and a big feature can move it without
anything getting worse.

### What the remaining ~8% actually is

This was audited line by line rather than assumed — an earlier version of this file called the
gap "mostly defensive", and that was **wrong**: it was hiding real holes, including
`load_pkcs12_file`, a README-documented feature (`.pfx`/`.p12` import) with *zero* coverage.
Those are now tested. What is left, by inspection of every uncovered line:

| Kind | Where | Why it is not covered |
|---|---|---|
| `except OSError` / `JSONDecodeError` rescue paths | most of `config.py`, `invoice_counter.py` | Need a disk that fails mid-write. The recovery is one line — log and fall back. |
| `except tk.TclError`, `except queue.Empty` | `main.py` | Fire only when the interpreter is being torn down under a real event loop. |
| macOS and Linux "open containing folder" | `main.py`, `settings_ui.py` | `sys.platform` branches that cannot run on the target OS. |
| `root.mainloop()`, `if __name__ == "__main__"` | entry points | Need a real display. |
| The real Playwright launch, the real `sign_pdf` | `receipt_service.py` 209-213, 77-85 | Covered by `main.py --smoke-test`, which drives a real browser; the unit suite stubs them so it stays fast and offline. |
| Two-line wrappers (`open_settings(parent)` and friends) | `settings_ui.py` | The dialogs they construct are each tested directly. |

Treat that table as a judgement, not a proof. If you are about to change one of those lines,
it has no test holding it — write one first.

The **golden gate** is the load-bearing check. `tests/fixtures/golden.html` is the receipt HTML
rendered from a pinned fixture, compared byte for byte. It stayed identical through Stages 2–5 and
has been regenerated exactly **once**, deliberately, when receipt-field styling was added.
**Never regenerate it to make a failing test pass** — inspect the diff first and justify it.

## Things waiting on the user

- **The logo.** Their file is named `logo.png.png` (Windows hides known extensions). Renaming it to
  `logo.png` fixes it; `cli.py --check` now says so explicitly.
- **Policy links** are empty. They said they would supply URLs for Terms / Privacy / Warranty —
  they go in **Tools → Settings → Links**, no code change needed.
- **No signing key exists** in the repo. Signing is enabled by default, so a receipt cannot be
  generated until one is created — **Tools → Signing Keys → Create new key**. Set
  `signing.signer_name` first, since it becomes the certificate subject.
- **`Templates/terms.html` still carries one shop's own wording.** It is an ordinary editable template
  now, so this is a content edit, but the shipped default should be generic.

## What to do next

See [`../TODO.md`](../TODO.md). **The backlog is essentially done** — §6 (all twelve items), §3
(the pricing UI), the void §2 was missing, CSV in and out, save-draft, `--doctor`, the image
signature and Stage 8 polish have all shipped.

Everything added defaults **off**, so an upgrade changes no existing receipt, and every switch is in
**Tools → Settings** or **Tools → Fields & Columns**. That was the standing request behind all of
it: no JSON file should need editing.

What is genuinely left:

1. **No bundled font.** `fonts.family` works, but no OFL font ships, so a receipt can still look
   slightly different on another machine. A licensing and asset choice rather than code.
2. **Untracking this shop's own config**, *if this repo is ever published*: `appsettings.json`,
   `fields.json` and `filename_config.json` are tracked, so a clone inherits one business's
   settings. Ship `.example` copies instead. Nothing to do while it stays private.
3. **Serial-number selection** (§2) — sell a *specific* held serial rather than typing one, now
   that a line carries one serial per unit.
4. **A low-stock warning at the point of sale**, rather than only in the log.
5. **H7 audit** — re-read the README for any "edit this JSON file" instruction that now has an
   in-app equivalent.

The terms page and `document.title` were on this list and are done: the shipped `terms.html` is
generic now, with the previous wording kept in `terms.<yourshop>.html` and `terms_page.template`
pointing at it, and the heading is `strings.json → totals.document_title`.
