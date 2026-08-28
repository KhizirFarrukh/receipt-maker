# Pitfalls — things that have already cost time here

Every item below actually happened during development. Several bit **twice**. Read this before
running or changing anything; it will save you an hour.

---

## Environment

### PowerShell 5.1 turns native stderr into a fatal error

`build_exe.ps1` starts with `$ErrorActionPreference = "Stop"`, and PyInstaller writes its INFO log
to **stderr**. Under Windows PowerShell 5.1 that combination makes the first INFO line a
terminating `NativeCommandError` and the build "fails" instantly.

```powershell
.\build_exe.ps1 2>&1 | Out-String     # ✗ fails on the first INFO line
.\build_exe.ps1                       # ✓
.\build_exe.ps1 | Select-Object -Last 3   # ✓
```

**This bit twice.** Both times it looked like a broken build script; both times it was the
invocation. Never pipe `2>&1` around a native command here.

### Python is not on PATH

Use `$env:LOCALAPPDATA\Programs\Python\Python312\python.exe` in full. The `py` launcher is not
available, and bare `python` resolves to the Microsoft Store stub.

### Builds take ~4 minutes and produce ~860 MB

Chromium is bundled. Run builds in the background rather than blocking on them.

---

## The test suite

### Always run it under a hard timeout

A test that opens a real modal dialog **hangs forever** instead of failing. That has happened
twice. Use a job with a deadline:

```powershell
$job = Start-Job -ScriptBlock {
  Set-Location "c:\Users\Khizi\Personal Projects\receipt-maker"
  & "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m unittest discover -s tests 2>&1
}
if (Wait-Job $job -Timeout 300) { Receive-Job $job } else { "HANGING"; Stop-Job $job }
Remove-Job $job -Force
```

### A stub pointed at the wrong function hangs the suite

When `_on_generated` moved from `messagebox.askyesno` to `ask_with_memory`, the existing stub kept
patching `messagebox` — so a real dialog opened and blocked. `tests/test_settings_ui.py` now
treats any unexpected `askyesno` as an assertion failure. **If you change which dialog function a
code path calls, update the stub.**

### tkinter teardown can abort the whole process

A `tk.StringVar` garbage-collected *after* its interpreter is gone raises from `__del__` and can
kill the process with `Tcl_AsyncDelete: async handler deleted by the wrong thread`. Whether it
happens depends only on collection timing, so it stayed hidden while `test_stage0` was the last
module and appeared the moment another ran after it.

Use the `receipt_app()` context manager in `tests/test_stage0.py`, which releases widgets and
variables *while Tk is still alive*.

### Do not assert on a CSS class name

`styles.css` is embedded in the rendered HTML, so `assertNotIn("policy-page", html)` passes on the
stylesheet, not the content. **This mistake was made twice** — once with `policy-page`, once with
`item-warranty-text`.

Match rendered markup instead: `'<div class="policy-page">'`, `'<span class="item-warranty-text">'`.

### Mocking the thing under test proves nothing

A conflict-detection test mocked `getmtime` with a scripted pair of return values and passed
against an implementation where the check could never fire. Rewriting it to edit a real file on
disk failed immediately and exposed the bug. **Prefer driving real files over mocking the call you
are testing.**

---

## The golden gate

### The fixture caches templates

`tests/fixtures/env/` is a real `APP_DIR`, so the app seeds a `Templates/` copy into it and — quite
correctly — never overwrites it. For a *gate* that is a trap: edits to the repository's templates
stop reaching the tests, and the diff quietly validates a stale layout. **One golden check passed
against a stale template before this was caught.**

`tests/gate_env.py` clears the seeded copy on entry. Keep it that way.

### Never regenerate the golden to make a test pass

Inspect the diff first. If it is a real, intended change, regenerate deliberately and say why in
the commit message. It has moved exactly once in the project's history.

---

## Git

### `.gitignore` negations re-expose everything beneath them

The pinned gate fixture lives in a directory called `env`, which the generic virtualenv rules
(`env/`, `ENV/`) match — so without a negation the whole fixture would never be committed and a
fresh clone's gate would fail.

But the negation then un-ignores everything underneath it, which has **already caused two
follow-up problems**: runtime `.bak` files and an `invoices/` folder both became committable.
Rules re-ignoring those must come *after* the negation to win.

### Line endings are content

`.gitattributes` pins LF. Without it, Git's Windows default rewrites the LF-committed
`golden.html` to CRLF on checkout, and the byte-comparison gate fails on a fresh clone before any
code is touched. Do not remove it.

---

## Application traps

### Loading config can rewrite the file

`load_app_settings()` migrates and persists. A dialog that captures the file's mtime *before*
loading will therefore see a "conflict" with an edit the app itself just made. **Capture the mtime
after the load.**

### Windows hides known file extensions

A file saved as `logo.png` frequently ends up named `logo.png.png` while Explorer still displays
it as `logo.png`. This is what made the user's logo silently vanish. `asset_problem()` now names
the near-miss; keep that behaviour.

### A lazily-imported module can be missing only in the packaged build

`settings_ui` is imported inside a menu handler, so a bundling miss would surface only when a user
clicked the menu. It is named in `hiddenimports` *and* imported by `--smoke-test`, so the build
fails instead.

### Row arithmetic in the item dialog

Rows come from `fields.json`, so deriving positions from `len(labels)` breaks as soon as a field or
an optional block is added. Use the explicit `next_row` counter.
