"""H3 — receipt history, and reloading a past receipt into the form.

The point of this feature is correcting a mistake without re-typing a sale, so
the tests care about two things above all:

* a record **outlives its PDF** — deleting the file must not lose the receipt;
* loading one back reproduces what was entered, and **does not consume a fresh
  invoice number**, because correcting a receipt should reissue *that* receipt.

Run: python -m unittest discover -s tests
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config            # noqa: E402
import receipt_history   # noqa: E402

DATA = {
    "inv_no": "INV-W1001", "date_str": "26 Aug 2026", "cust": "Ada Lovelace",
    "phone": "+92 300 1234567", "email": "ada@example.com",
    "receipt_type": "Online", "shipping": "500.00",
    "items": [{"sku": "KB-87", "desc": "Mechanical Keyboard", "serial": "SN-1",
               "qty": 2, "price": "8500.00", "discount": "500.00",
               "tax": "1200.00", "warranty": "12 Months Limited Warranty"}],
}


class HistoryTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-history-")
        shutil.copy(os.path.join(PROJ, "appsettings.example.json"),
                    os.path.join(self.dir, "appsettings.json"))
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def pdf(self, name="INV-W1001.pdf"):
        path = os.path.join(self.dir, "invoices", name)
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4 pretend")
        return path


class Recording(HistoryTestCase):
    def test_a_receipt_is_recorded(self):
        self.assertTrue(receipt_history.record(DATA, self.pdf(), True))
        self.assertEqual(len(receipt_history.entries()), 1)

    def test_records_land_outside_the_browsable_receipts_folder(self):
        """PII records must not be mixed in with the PDFs someone browses."""
        receipt_history.record(DATA, self.pdf(), True)
        self.assertTrue(receipt_history.history_path().endswith(
            os.path.join(".archive", "history.jsonl")))
        self.assertNotIn("history.jsonl", os.listdir(os.path.join(self.dir, "invoices")))

    def test_newest_first(self):
        receipt_history.record(dict(DATA, inv_no="INV-W1001"), "", True)
        receipt_history.record(dict(DATA, inv_no="INV-W1002"), "", True)
        self.assertEqual([e["invoice_no"] for e in receipt_history.entries()],
                         ["INV-W1002", "INV-W1001"])

    def test_amounts_are_stored_as_text(self):
        """JSON numbers are floats, and money must not round-trip through one."""
        receipt_history.record(DATA, "", True)
        with open(receipt_history.history_path(), encoding="utf-8") as f:
            raw = json.loads(f.readline())
        self.assertIsInstance(raw["items"][0]["price"], str)
        self.assertIsInstance(raw["shipping"], str)

    def test_the_signed_state_is_kept(self):
        receipt_history.record(DATA, "", True)
        receipt_history.record(dict(DATA, inv_no="INV-W1002"), "", False)
        self.assertEqual([e["signed"] for e in receipt_history.entries()], [False, True])

    def test_recording_never_raises(self):
        """Losing a history line must not be able to fail a receipt."""
        config.set_app_dir(os.path.join(self.dir, "nonexistent", "\0bad"))
        try:
            self.assertFalse(receipt_history.record(DATA, "", True))
        finally:
            config.set_app_dir(self.dir)

    def test_a_damaged_line_is_skipped_not_fatal(self):
        receipt_history.record(DATA, "", True)
        with open(receipt_history.history_path(), "a", encoding="utf-8") as f:
            f.write("{ this line is broken\n")
        receipt_history.record(dict(DATA, inv_no="INV-W1002"), "", True)
        self.assertEqual(len(receipt_history.entries()), 2,
                         "the readable lines must still come back")

    def test_no_history_yet_is_an_empty_list(self):
        self.assertEqual(receipt_history.entries(), [])


class RecordsOutliveTheirPdfs(HistoryTestCase):
    """The case the user actually described: deleted, and still needed."""

    def test_a_deleted_pdf_leaves_the_record_intact(self):
        path = self.pdf()
        receipt_history.record(DATA, path, True)
        os.remove(path)

        entries = receipt_history.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(receipt_history.to_form_data(entries[0])["cust"], "Ada Lovelace")

    def test_the_record_does_not_depend_on_the_output_folder(self):
        receipt_history.record(DATA, self.pdf(), True)
        shutil.rmtree(os.path.join(self.dir, "invoices", ".archive"), ignore_errors=False)
        self.assertEqual(receipt_history.entries(), [],
                         "sanity: this is the only place the record lives")


class ReloadingIntoTheForm(HistoryTestCase):
    def test_round_trip_preserves_the_entered_data(self):
        receipt_history.record(DATA, "", True)
        back = receipt_history.to_form_data(receipt_history.entries()[0])
        self.assertEqual(back["inv_no"], DATA["inv_no"])
        self.assertEqual(back["cust"], DATA["cust"])
        self.assertEqual(back["phone"], DATA["phone"])
        self.assertEqual(back["shipping"], DATA["shipping"])
        self.assertEqual(len(back["items"]), 1)
        self.assertEqual(back["items"][0]["desc"], "Mechanical Keyboard")

    def test_the_form_shape_matches_what_generation_expects(self):
        receipt_history.record(DATA, "", True)
        back = receipt_history.to_form_data(receipt_history.entries()[0])
        self.assertEqual(set(back), set(DATA))

    def test_loading_restores_the_original_number_and_consumes_none(self):
        """Correcting a receipt reissues that receipt; it is not a new sale."""
        import invoice_counter
        import tkinter as tk
        import main

        receipt_history.record(DATA, "", True)
        before = invoice_counter.peek("W")

        root = tk.Tk()
        root.withdraw()
        try:
            app = main.ReceiptApp(root)
            app.load_from_history(receipt_history.entries()[0])
            loaded_number = app.inv_no.get()
            items = len(app.items_tree.get_children())
            customer = app.cust_name.get()
        finally:
            root.destroy()

        self.assertEqual(loaded_number, "INV-W1001")
        self.assertEqual(customer, "Ada Lovelace")
        self.assertEqual(items, 1)
        self.assertEqual(invoice_counter.peek("W"), before,
                         "loading a past receipt must not burn a number")

    def test_loading_replaces_rather_than_appends_items(self):
        import tkinter as tk
        import main

        receipt_history.record(DATA, "", True)
        root = tk.Tk()
        root.withdraw()
        try:
            app = main.ReceiptApp(root)
            app.load_from_history(receipt_history.entries()[0])
            app.load_from_history(receipt_history.entries()[0])
            self.assertEqual(len(app.items_tree.get_children()), 1,
                             "loading twice must not duplicate the lines")
        finally:
            root.destroy()


class Summaries(HistoryTestCase):
    def test_the_total_is_computed_from_the_stored_lines(self):
        receipt_history.record(DATA, "", True)
        entry = receipt_history.entries()[0]
        _date, number, customer, total, status = receipt_history.summarise(
            entry, config.load_app_settings()["currency"])
        # 2 x 8500 = 17000, +1200 tax, -500 discount, +500 shipping
        self.assertEqual(total, "Rs. 18,200.00")
        self.assertEqual(number, "INV-W1001")
        self.assertEqual(customer, "Ada Lovelace")
        self.assertEqual(status, "signed")

    def test_the_total_follows_the_configured_currency(self):
        receipt_history.record(DATA, "", True)
        entry = receipt_history.entries()[0]
        usd = {"symbol": "$", "symbol_space": False, "decimals": 2,
               "position": "prefix", "group_style": "thousand",
               "negative_style": "minus"}
        self.assertEqual(receipt_history.summarise(entry, usd)[3], "$18,200.00")


class Searching(HistoryTestCase):
    def setUp(self):
        super().setUp()
        receipt_history.record(DATA, "", True)
        # A distinct email too: search covers it, so sharing one would make
        # "search by customer" look broken when it is working.
        receipt_history.record(
            dict(DATA, inv_no="INV-W1002", cust="Grace Hopper",
                 email="grace@example.com",
                 items=[{"sku": "MOU-1", "desc": "Mouse", "qty": 1,
                         "price": "1000.00", "discount": "0", "tax": "0"}]), "", False)
        self.entries = receipt_history.entries()

    def matching(self, needle):
        return [e["invoice_no"] for e in self.entries
                if receipt_history.matches(e, needle)]

    def test_by_customer(self):
        self.assertEqual(self.matching("grace"), ["INV-W1002"])

    def test_by_invoice_number(self):
        self.assertEqual(self.matching("W1001"), ["INV-W1001"])

    def test_by_item_description(self):
        self.assertEqual(self.matching("keyboard"), ["INV-W1001"])

    def test_by_sku(self):
        self.assertEqual(self.matching("MOU-1"), ["INV-W1002"])

    def test_is_case_insensitive(self):
        self.assertEqual(self.matching("ADA"), ["INV-W1001"])

    def test_an_empty_search_matches_everything(self):
        self.assertEqual(len(self.matching("")), 2)
        self.assertEqual(len(self.matching("   ")), 2)

    def test_no_match(self):
        self.assertEqual(self.matching("nothing here"), [])


if __name__ == "__main__":
    unittest.main()
