"""TODO.md section 3 -- working a sell price out from cost or list price.

The arithmetic has been in `product_catalogue` and tested since the catalogue
landed; what was missing was any way to reach it. Doing the sum by hand is
exactly where the mistake this guards against gets made.

**Margin and markup are not the same thing.** Cost 100 at 25% markup is 125; at
25% margin it is 133.33. Getting them the wrong way round silently
under-prices every item, so the dialog names the mode rather than asking for an
unlabelled percentage, and `BothNumbersAreAlwaysShown` holds the rule that the
result panel reports the margin *and* the markup whichever way the price was
reached.

Run: python -m unittest discover -s tests
"""
import os
import shutil
import sys
import tempfile
import tkinter as tk
import unittest
from decimal import Decimal

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config              # noqa: E402
import product_catalogue   # noqa: E402
import settings_ui         # noqa: E402

import gate_env            # noqa: E402
import tk_support          # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


class PricingTestCase(unittest.TestCase):
    PRODUCT = {"sku": "KB-87", "name": "Keyboard",
               "cost_price": "100", "list_price": "200"}

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-price-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)
        self.root = tk.Tk()
        self.root.withdraw()

        self.infos = []
        self._showinfo = settings_ui.messagebox.showinfo
        settings_ui.messagebox.showinfo = lambda t, m, **k: self.infos.append(m)

    def tearDown(self):
        settings_ui.messagebox.showinfo = self._showinfo
        tk_support.destroy(self)
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def dialog(self, product=None, mode="markup", percent="25"):
        d = settings_ui.PricingDialog(self.root, product or dict(self.PRODUCT))
        d.mode.set(mode)
        d.percent.set(percent)
        d.refresh()
        return d


class TheThreeModes(PricingTestCase):
    def test_markup_is_added_to_cost(self):
        d = self.dialog(mode="markup", percent="25")
        self.assertEqual(product_catalogue.quantize(d.computed(), 2),
                         Decimal("125.00"))

    def test_margin_is_a_share_of_the_sale_price(self):
        """The number that differs from markup, and the reason for the labels."""
        d = self.dialog(mode="margin", percent="25")
        self.assertEqual(product_catalogue.quantize(d.computed(), 2),
                         Decimal("133.33"))

    def test_discount_comes_off_the_list_price(self):
        d = self.dialog(mode="discount", percent="10")
        self.assertEqual(product_catalogue.quantize(d.computed(), 2),
                         Decimal("180.00"))

    def test_the_mode_is_explained_on_screen(self):
        d = self.dialog(mode="margin")
        self.assertIn("share of what you charge", d.explain.cget("text"))

    def test_switching_mode_changes_the_answer(self):
        d = self.dialog(mode="markup", percent="25")
        markup_price = d.computed()
        d.mode.set("margin")
        d.refresh()
        self.assertNotEqual(d.computed(), markup_price)


class BothNumbersAreAlwaysShown(PricingTestCase):
    """Whichever way the price was reached, the other number is the check."""

    def test_a_markup_price_reports_its_margin_too(self):
        d = self.dialog(mode="markup", percent="25")
        text = d.result_label.cget("text")
        self.assertIn("markup of 25.0%", text)
        self.assertIn("margin of 20.0%", text)

    def test_a_margin_price_reports_its_markup_too(self):
        d = self.dialog(mode="margin", percent="25")
        text = d.result_label.cget("text")
        self.assertIn("margin of 25.0%", text)
        self.assertIn("markup of 33.3%", text)

    def test_a_discount_price_reports_both_against_cost(self):
        d = self.dialog(mode="discount", percent="10")
        text = d.result_label.cget("text")
        self.assertIn("margin of", text)
        self.assertIn("markup of", text)

    def test_the_price_itself_is_shown(self):
        d = self.dialog(mode="markup", percent="25")
        self.assertIn("125.00", d.result_label.cget("text"))


class RefusingWhatCannotBePriced(PricingTestCase):
    def test_a_hundred_percent_margin_is_refused(self):
        """Dividing by zero: it would mean an infinite selling price."""
        d = self.dialog(mode="margin", percent="100")
        self.assertIn("infinite", d.result_label.cget("text"))
        self.assertIn("disabled", d.apply_button.state())

    def test_a_full_discount_is_refused_as_not_a_sale(self):
        d = self.dialog(mode="discount", percent="100")
        self.assertIn("not a sale", d.result_label.cget("text"))
        self.assertIn("disabled", d.apply_button.state())

    def test_applying_a_refused_price_does_nothing(self):
        d = self.dialog(mode="margin", percent="100")
        d.apply()
        self.assertIsNone(d.result)

    def test_a_workable_price_re_enables_the_button(self):
        d = self.dialog(mode="margin", percent="100")
        d.percent.set("25")
        d.refresh()
        self.assertNotIn("disabled", d.apply_button.state())

    def test_junk_in_a_box_does_not_crash_it(self):
        d = self.dialog(mode="markup", percent="lots")
        self.assertIsNotNone(d.result_label.cget("text"))


class ApplyingThePrice(PricingTestCase):
    def test_it_returns_the_rounded_price(self):
        d = self.dialog(mode="margin", percent="25")
        d.apply()
        self.assertEqual(d.result, "133.33")

    def test_cancelling_returns_nothing(self):
        d = self.dialog()
        d.win.destroy()
        self.assertIsNone(d.result)

    def test_the_precision_follows_the_currency(self):
        d = settings_ui.PricingDialog(self.root, dict(self.PRODUCT), decimals=0)
        d.mode.set("margin")
        d.percent.set("25")
        d.refresh()
        d.apply()
        self.assertEqual(d.result, "133")


class FromTheProductsDialog(PricingTestCase):
    def products(self):
        dialog = settings_ui.ProductsDialog(self.root)
        dialog.editor.records.append(dict(self.PRODUCT))
        dialog.editor.refresh()
        return dialog

    def test_it_asks_for_a_selection_first(self):
        dialog = self.products()
        dialog.open_pricing()
        self.assertTrue(self.infos)
        self.assertIn("Select the product", self.infos[0])
        dialog.win.destroy()

    def test_the_worked_out_price_lands_on_the_product(self):
        dialog = self.products()
        dialog.editor.tree.selection_set(dialog.editor.tree.get_children()[0])

        # Drive the sub-dialog without opening a real modal.
        def drive(win):
            for child in self.root.winfo_children():
                pass
        original = settings_ui.PricingDialog

        class Stub(original):
            def __init__(self, parent, product, decimals=2):
                super().__init__(parent, product, decimals)
                self.mode.set("markup")
                self.percent.set("50")
                self.refresh()
                self.apply()

        settings_ui.PricingDialog = Stub
        self.win_wait = self.root.wait_window
        self.root.wait_window = lambda w: None
        try:
            dialog.win.wait_window = lambda w: None
            dialog.open_pricing()
        finally:
            settings_ui.PricingDialog = original
            self.root.wait_window = self.win_wait

        self.assertEqual(dialog.editor.records[0]["sell_price"], "150.00")
        dialog.win.destroy()

    def test_the_stale_note_about_stock_is_gone(self):
        """Stock deduction shipped; the dialog said it had not."""
        dialog = self.products()
        text = " ".join(str(child.cget("text"))
                        for child in dialog.win.winfo_children()[0].winfo_children()
                        if "text" in child.keys())
        self.assertNotIn("not yet deducted", text)
        dialog.win.destroy()


if __name__ == "__main__":
    unittest.main()
