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
    """The headless seam must reproduce the real GUI's generate output."""

    def test_headless_matches_gui(self):
        import tkinter as tk
        import main

        data = cli.load_data(GOLDEN_INPUT)
        html_headless = cli.render_html_from_data(data, normalize=True)

        root = tk.Tk()
        root.withdraw()
        captured = {}
        original_sign = main.sign_receipt_pdf
        try:
            app = main.ReceiptApp(root)
            # Intercept the HTML the GUI would hand to Playwright; no PDF, no sign.
            app.render_pdf = lambda body_html, pdf_path: captured.__setitem__("html", body_html)
            main.sign_receipt_pdf = lambda pdf_path: False

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
            main.sign_receipt_pdf = original_sign
            root.destroy()

        self.assertIn("html", captured, "GUI never reached render_pdf (generation failed)")
        self.assertEqual(html_headless, cli.normalize_html(captured["html"]))


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
