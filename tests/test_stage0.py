"""Stage 0 — baseline harness gate.

Locks in TODAY's receipt HTML before any refactor, so later stages can prove
they preserve it. Three guarantees:
  * determinism  — the harness is reproducible;
  * regression   — the harness still matches the committed golden;
  * fidelity     — the headless harness reproduces what the real GUI generate
                   path emits (the check that stops us freezing a subtly-wrong
                   baseline and faithfully preserving the wrong thing).
Plus a smoke test that the GUI still constructs.

Run: python -m unittest discover -s tests
"""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import cli  # noqa: E402

FIX = os.path.join(PROJ, "tests", "fixtures")
GOLDEN_INPUT = os.path.join(FIX, "golden_input.json")
GOLDEN_HTML = os.path.join(FIX, "golden.html")


class Stage0Golden(unittest.TestCase):
    def setUp(self):
        self.data = cli.load_data(GOLDEN_INPUT)

    def test_determinism(self):
        """Rendering the same fixture twice is byte-identical."""
        self.assertEqual(
            cli.render_html_from_data(self.data),
            cli.render_html_from_data(self.data),
        )

    def test_regression_matches_golden(self):
        """The harness still produces the committed golden HTML."""
        with open(GOLDEN_HTML, "r", encoding="utf-8", newline="") as f:
            golden = f.read()
        self.assertEqual(cli.render_html_from_data(self.data), golden)

    def test_base_href_normalized(self):
        """The one machine-dependent element is neutralized."""
        got = cli.render_html_from_data(self.data)
        self.assertIn(cli.RESOURCE_BASE_PLACEHOLDER, got)
        self.assertNotIn("file:///", got)

    def test_totals_arithmetic(self):
        """Sanity: printed TOTAL equals subtotal + tax - discount + shipping."""
        got = cli.render_html_from_data(self.data)
        # 2*8500 + 3*750 + 1*3200 = 22450; +1200 tax -500 disc +500 ship = 23650
        self.assertIn("Rs. 23,650.00", got)


class Stage0Fidelity(unittest.TestCase):
    """The headless harness must reproduce what the real GUI generate path emits.

    Post-Stage-1 the GUI collects a `data` dict and hands it to a worker thread;
    the HTML is produced by receipt_render.build_html from that dict. So fidelity
    = the GUI's form-collected data, rendered, equals the harness output.
    """

    def test_headless_matches_gui(self):
        import tkinter as tk
        import main
        import receipt_render

        data = cli.load_data(GOLDEN_INPUT)
        html_headless = cli.render_html_from_data(data, normalize=True)

        root = tk.Tk()
        root.withdraw()
        captured = {}
        try:
            app = main.ReceiptApp(root)
            # Capture the data the GUI collected; do not start the worker/Playwright.
            app._run_generation = lambda d, out_path: captured.update(data=d, out=out_path)

            a = cli._to_build_html_args(data)
            app.receipt_type.set(a["receipt_type"])
            app.inv_no.set(a["inv_no"])
            app.date.set(a["date_str"])
            app.cust_name.set(a["cust"])
            app.cust_phone.set(a["phone"])
            app.cust_email.set(a["email"])
            app.shipping.set(data.get("shipping", ""))
            for child in app.items_tree.get_children():
                app.items_tree.delete(child)
            for it in a["items"]:
                app.items_tree.insert("", tk.END, values=(
                    it["sku"], it["desc"], it["serial"], it["qty"],
                    f'{it["price"]:.2f}', f'{it["discount"]:.2f}', f'{it["tax"]:.2f}', it["warranty"],
                ))

            app.generate_pdf()
        finally:
            root.destroy()

        self.assertIn("data", captured, "GUI never reached generation (validation failed)")
        d = captured["data"]
        html_gui = cli.normalize_html(receipt_render.build_html(
            d["inv_no"], d["date_str"], d["cust"], d["phone"], d["email"],
            d["items"], d["receipt_type"], d["shipping"],
        ))
        self.assertEqual(html_headless, html_gui)


class Stage1Layering(unittest.TestCase):
    """The render/service/config layers must not depend on tkinter."""

    def test_render_path_imports_no_tkinter(self):
        import subprocess

        code = (
            "import sys, cli;"
            "cli.render_html_from_data(cli.load_data(r'%s'));"
            "assert 'tkinter' not in sys.modules, 'tkinter leaked into render path';"
            "print('ok')" % GOLDEN_INPUT.replace("\\", "\\\\")
        )
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=PROJ,
            capture_output=True, text=True,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("ok", out.stdout)

    def test_tkfree_modules_import_without_tkinter(self):
        import subprocess

        code = (
            "import sys, config, receipt_render, receipt_service;"
            "assert 'tkinter' not in sys.modules, 'tkinter leaked';"
            "print('ok')"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=PROJ,
            capture_output=True, text=True,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("ok", out.stdout)


class Stage1GenerationUX(unittest.TestCase):
    """The threaded progress + error wiring works without Playwright."""

    DATA = {
        "inv_no": "INV-W1001", "date_str": "15 Jan 2026", "cust": "Ada",
        "phone": "", "email": "", "items": [{"desc": "x", "qty": 1, "price": 1.0}],
        "receipt_type": "Online", "shipping": 0.0,
    }

    def _drive(self, app, root, out_path):
        import time
        app._run_generation(dict(self.DATA), out_path)
        for _ in range(400):
            root.update()
            if not getattr(app, "_generating", True):
                break
            time.sleep(0.005)

    def test_success_path(self):
        import tkinter as tk
        import main
        import receipt_service

        root = tk.Tk(); root.withdraw()
        orig_gen, orig_ask = receipt_service.generate, main.messagebox.askyesno
        try:
            app = main.ReceiptApp(root)
            steps = []

            def fake_generate(data, out_path, progress_cb=None):
                for i in range(1, receipt_service.GENERATION_STEPS + 1):
                    if progress_cb:
                        progress_cb(i, f"step {i}")
                    steps.append(i)
                return True

            receipt_service.generate = fake_generate
            main.messagebox.askyesno = lambda *a, **k: False  # skip folder prompt

            self.assertEqual(str(app.generate_button["state"]), "normal")
            self._drive(app, root, os.path.join(PROJ, "invoices", "_uxtest.pdf"))

            self.assertFalse(app._generating, "generation flag not reset")
            self.assertEqual(str(app.generate_button["state"]), "normal", "button not re-enabled")
            self.assertEqual(steps, [1, 2, 3, 4], "progress steps not reported")
            self.assertIn("signed", app.status_label["text"])
        finally:
            receipt_service.generate, main.messagebox.askyesno = orig_gen, orig_ask
            root.destroy()

    def test_error_path_shows_diagnostic(self):
        import tkinter as tk
        import main
        import receipt_service

        root = tk.Tk(); root.withdraw()
        orig_gen, orig_err = receipt_service.generate, main.show_error
        captured = {}
        try:
            app = main.ReceiptApp(root)

            def boom(data, out_path, progress_cb=None):
                raise RuntimeError("signing key not found")

            receipt_service.generate = boom
            main.show_error = lambda parent, title, summary, detail=None: captured.update(
                title=title, summary=summary, detail=detail)

            self._drive(app, root, os.path.join(PROJ, "invoices", "_uxtest.pdf"))

            self.assertFalse(app._generating)
            self.assertEqual(str(app.generate_button["state"]), "normal")
            self.assertIn("signing key not found", captured.get("summary", ""))
            self.assertIsNotNone(captured.get("detail"), "no traceback passed to show_error")
            self.assertIn("PDF generation failed", app.status_label["text"])
        finally:
            receipt_service.generate, main.show_error = orig_gen, orig_err
            root.destroy()

    def test_concurrent_guard(self):
        import tkinter as tk
        import main

        root = tk.Tk(); root.withdraw()
        try:
            app = main.ReceiptApp(root)
            app._generating = True  # pretend a job is in flight
            calls = []
            app.root.after = lambda *a, **k: calls.append(1)  # would schedule the poll loop
            app._run_generation(dict(self.DATA), "x.pdf")
            self.assertEqual(calls, [], "a second worker/poll was scheduled while generating")
        finally:
            root.destroy()


class Stage0Smoke(unittest.TestCase):
    def test_app_constructs(self):
        import tkinter as tk
        import main

        root = tk.Tk()
        root.withdraw()
        try:
            main.ReceiptApp(root)  # __init__ must not raise
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
