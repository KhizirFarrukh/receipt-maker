"""TODO.md section 6.8 -- scan a barcode to add a line, or add one to it.

At a till the scanner is the fastest way in: read the code, get the line. Scan
the same thing again and the quantity goes up rather than a second identical
line appearing.

Two details here are the difference between working and looking broken:

* **Enter is swallowed.** A scanner types the code and presses Enter. If that
  reached the window's default action, the first item scanned would submit the
  receipt. `EnterNeverEscapes` holds that.
* **An unknown code says so.** A scan that silently does nothing is
  indistinguishable from a scanner that has stopped working.

Run: python -m unittest discover -s tests
"""
import os
import shutil
import sys
import tempfile
import tkinter as tk
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config              # noqa: E402
import main                # noqa: E402
import product_catalogue   # noqa: E402
import receipt_render      # noqa: E402

import gate_env            # noqa: E402
import tk_support          # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


PRODUCTS = {
    config.SCHEMA_VERSION_KEY: 1,
    "products": [
        {"sku": "KB-87", "barcode": "5901234123457", "name": "Keyboard",
         "list_price": "45.00", "stock_count": 10},
        {"sku": "MS-01", "barcode": "4006381333931", "name": "Mouse",
         "list_price": "19.00", "stock_count": 5},
    ],
}


class ScanTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-scan-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)
        product_catalogue.save(PRODUCTS)
        receipt_render.clear_template_cache()

        self.root = tk.Tk()
        self.root.withdraw()
        self.app = main.ReceiptApp(self.root)

        self.asked = []
        self._askyesno = main.messagebox.askyesno
        main.messagebox.askyesno = lambda t, m, **k: (
            self.asked.append(m) or self.answer)
        self.answer = True

    def tearDown(self):
        main.messagebox.askyesno = self._askyesno
        tk_support.destroy(self)
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def scan(self, code):
        self.app.scan_code.set(code)
        return self.app.on_scan()

    def rows(self):
        return [self.app.item_at(r)
                for r in self.app.items_tree.get_children()]


class ScanningAddsALine(ScanTestCase):
    def test_a_known_barcode_adds_one_line(self):
        self.scan("5901234123457")
        self.assertEqual(len(self.rows()), 1)

    def test_the_line_is_filled_from_the_catalogue(self):
        self.scan("5901234123457")
        item = self.rows()[0]
        self.assertEqual(item["desc"], "Keyboard")
        self.assertEqual(item["sku"], "KB-87")
        self.assertEqual(item["price"], "45.00")

    def test_it_starts_at_one(self):
        self.scan("5901234123457")
        self.assertEqual(str(self.rows()[0]["qty"]), "1")

    def test_a_sku_scans_as_well_as_a_barcode(self):
        """Shops label stock with whichever they use."""
        self.scan("KB-87")
        self.assertEqual(self.rows()[0]["desc"], "Keyboard")

    def test_two_different_products_make_two_lines(self):
        self.scan("5901234123457")
        self.scan("4006381333931")
        self.assertEqual(len(self.rows()), 2)

    def test_an_empty_scan_does_nothing(self):
        self.scan("   ")
        self.assertEqual(self.rows(), [])

    def test_the_box_clears_itself_for_the_next_scan(self):
        self.scan("5901234123457")
        self.assertEqual(self.app.scan_code.get(), "")


class RescanningIncrements(ScanTestCase):
    def test_the_same_code_twice_makes_one_line_of_two(self):
        self.scan("5901234123457")
        self.scan("5901234123457")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["qty"]), "2")

    def test_it_keeps_counting(self):
        for _ in range(5):
            self.scan("5901234123457")
        self.assertEqual(str(self.rows()[0]["qty"]), "5")

    def test_scanning_a_sku_increments_the_line_added_by_barcode(self):
        """Same product, whichever code the label happened to carry."""
        self.scan("5901234123457")
        self.scan("KB-87")
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(str(self.rows()[0]["qty"]), "2")

    def test_incrementing_one_line_leaves_the_other_alone(self):
        self.scan("5901234123457")
        self.scan("4006381333931")
        self.scan("5901234123457")
        quantities = {r["desc"]: str(r["qty"]) for r in self.rows()}
        self.assertEqual(quantities, {"Keyboard": "2", "Mouse": "1"})

    def test_a_broken_quantity_does_not_stop_the_scan(self):
        self.scan("5901234123457")
        row = self.app.items_tree.get_children()[0]
        item = self.app.item_at(row)
        item["qty"] = "lots"
        self.app.items_tree.item(row, values=self.app.item_to_row(item))
        self.scan("5901234123457")
        self.assertEqual(str(self.rows()[0]["qty"]), "2")

    def test_a_rescan_grows_the_unit_list(self):
        """Scan a thing three times and three serials are now owed."""
        fields = config.load_fields()
        for field in fields["line_item_fields"]:
            if field["key"] == "serial":
                field.update(enabled=True, per_unit=True)
        config.save_fields(fields)
        self.app.fields = config.load_fields()

        import line_units
        self.scan("5901234123457")
        self.scan("5901234123457")
        self.scan("5901234123457")
        item = self.rows()[0]
        self.assertEqual(str(item["qty"]), "3")
        # Blank units are not stored -- they would be noise on every line -- so
        # what matters is that reading the line now asks for three serials.
        self.assertEqual(len(line_units.normalise(item, ["serial"])), 3)


class AnUnknownCodeSaysSo(ScanTestCase):
    def test_the_user_is_asked_rather_than_ignored(self):
        self.answer = False
        self.scan("0000000000000")
        self.assertTrue(self.asked, "a scan that does nothing looks broken")
        self.assertIn("0000000000000", self.asked[0])

    def test_declining_adds_nothing(self):
        self.answer = False
        self.scan("0000000000000")
        self.assertEqual(self.rows(), [])

    def test_accepting_adds_a_line_carrying_the_code(self):
        """Kept wherever the form can actually store it.

        `barcode` ships as a disabled column, so on a default install the code
        goes in `sku` -- writing it only to `barcode` would lose it, and the
        line could never be rescanned.
        """
        self.answer = True
        self.scan("0000000000000")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertIn("0000000000000",
                      [rows[0].get("barcode"), rows[0].get("sku")])

    def test_the_bare_line_can_then_be_rescanned_to_increment(self):
        self.answer = True
        self.scan("0000000000000")
        self.scan("0000000000000")
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(str(self.rows()[0]["qty"]), "2")

    def test_a_missing_catalogue_does_not_break_scanning(self):
        os.remove(product_catalogue.catalogue_path())
        self.answer = False
        self.scan("5901234123457")
        self.assertTrue(self.asked)


class EnterNeverEscapes(ScanTestCase):
    """A scanner ends every read with Enter."""

    def test_a_successful_scan_stops_the_event(self):
        self.assertEqual(self.scan("5901234123457"), "break")

    def test_an_empty_scan_stops_it_too(self):
        self.assertEqual(self.scan(""), "break")

    def test_an_unknown_code_stops_it_too(self):
        self.answer = False
        self.assertEqual(self.scan("0000000000000"), "break")

    def test_a_rescan_stops_it_too(self):
        self.scan("5901234123457")
        self.assertEqual(self.scan("5901234123457"), "break")


class TheStatusLine(ScanTestCase):
    def test_it_reports_what_was_added(self):
        self.scan("5901234123457")
        self.assertIn("Keyboard", self.app.scan_status.cget("text"))

    def test_it_reports_the_running_count(self):
        self.scan("5901234123457")
        self.scan("5901234123457")
        self.assertIn("2", self.app.scan_status.cget("text"))

    def test_a_refused_unknown_code_is_reported_as_a_warning(self):
        self.answer = False
        self.scan("0000000000000")
        self.assertIn("not found", self.app.scan_status.cget("text"))
        self.assertEqual(str(self.app.scan_status.cget("foreground")), "#b45309")


if __name__ == "__main__":
    unittest.main()
