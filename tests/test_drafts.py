"""Save draft — TODO.md §4 (H2).

A sale gets interrupted and the work has to survive without issuing a receipt
for a sale that has not happened.

`NoInvoiceNumberIsConsumed` is the class that matters. Invoice numbers are
reserved when a receipt is *generated*, because a duplicate is unrecoverable —
a draft is not a receipt and must not touch the counter. Everything else here is
storage.

Run: python -m unittest discover -s tests
"""
import json
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
import drafts              # noqa: E402
import invoice_counter     # noqa: E402
import main                # noqa: E402
import receipt_render      # noqa: E402
import settings_ui         # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


FORM = {"inv_no": "INV-W1001", "date_str": "1 Jan 2026", "cust": "Ada",
        "phone": "555", "email": "ada@example.com", "receipt_type": "Online",
        "shipping": "5",
        "items": [{"sku": "A", "desc": "Thing", "qty": "2", "price": "10.00",
                   "discount": "0", "tax": "0"}]}


class DraftTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-draft-")
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)


class NoInvoiceNumberIsConsumed(DraftTestCase):
    """The one rule that matters: a draft is not a receipt."""

    def test_saving_leaves_the_counter_alone(self):
        before = invoice_counter.peek("W")
        drafts.add(FORM)
        self.assertEqual(invoice_counter.peek("W"), before)

    def test_saving_many_drafts_leaves_it_alone(self):
        before = invoice_counter.peek("W")
        for index in range(5):
            drafts.add(dict(FORM, cust=f"Customer {index}"))
        self.assertEqual(invoice_counter.peek("W"), before)

    def test_restoring_leaves_it_alone(self):
        drafts.add(FORM)
        before = invoice_counter.peek("W")
        drafts.to_form_data(drafts.load()["drafts"][0])
        self.assertEqual(invoice_counter.peek("W"), before)

    def test_the_number_is_stored_as_a_suggestion_not_a_number(self):
        """Nothing downstream may mistake a draft for a numbered receipt."""
        record = drafts.add(FORM)
        self.assertNotIn("inv_no", record)
        self.assertEqual(record["suggested_inv_no"], "INV-W1001")

    def test_it_comes_back_in_the_number_box(self):
        drafts.add(FORM)
        form = drafts.to_form_data(drafts.load()["drafts"][0])
        self.assertEqual(form["inv_no"], "INV-W1001")


class Storing(DraftTestCase):
    def test_an_empty_store_reads_as_empty(self):
        self.assertEqual(drafts.load()["drafts"], [])

    def test_a_saved_draft_comes_back(self):
        drafts.add(FORM)
        self.assertEqual(len(drafts.load()["drafts"]), 1)

    def test_the_newest_is_first(self):
        drafts.add(dict(FORM, cust="First"))
        drafts.add(dict(FORM, cust="Second"))
        self.assertEqual(drafts.load()["drafts"][0]["cust"], "Second")

    def test_items_survive(self):
        drafts.add(FORM)
        form = drafts.to_form_data(drafts.load()["drafts"][0])
        self.assertEqual(form["items"][0]["desc"], "Thing")

    def test_the_extras_survive(self):
        rich = dict(FORM, installment={"months": 6, "monthly": "100"},
                    shipments=[{"id": "A", "fee": "5"}],
                    payment_method="Card", notes="Leave with neighbour")
        drafts.add(rich)
        form = drafts.to_form_data(drafts.load()["drafts"][0])
        self.assertEqual(form["installment"]["months"], 6)
        self.assertEqual(form["shipments"][0]["fee"], "5")
        self.assertEqual(form["payment_method"], "Card")
        self.assertEqual(form["notes"], "Leave with neighbour")

    def test_the_bookkeeping_does_not_leak_into_the_form(self):
        drafts.add(FORM)
        form = drafts.to_form_data(drafts.load()["drafts"][0])
        for key in ("draft_id", "saved_at", "name", "suggested_inv_no"):
            self.assertNotIn(key, form)

    def test_a_draft_is_named_after_who_it_is_for(self):
        record = drafts.add(FORM)
        self.assertIn("Ada", record["name"])
        self.assertIn("1 item", record["name"])

    def test_a_draft_with_no_customer_still_gets_a_name(self):
        record = drafts.add(dict(FORM, cust=""))
        self.assertIn("no customer", record["name"])

    def test_an_explicit_name_wins(self):
        record = drafts.add(FORM, name="Phone order")
        self.assertEqual(record["name"], "Phone order")

    def test_removing_one(self):
        record = drafts.add(FORM)
        self.assertTrue(drafts.remove(record["draft_id"]))
        self.assertEqual(drafts.load()["drafts"], [])

    def test_removing_one_that_is_not_there(self):
        self.assertFalse(drafts.remove("nope"))

    def test_the_oldest_are_dropped_past_the_limit(self):
        for index in range(drafts.MAX_DRAFTS + 5):
            drafts.add(dict(FORM, cust=f"C{index}"))
        stored = drafts.load()["drafts"]
        self.assertEqual(len(stored), drafts.MAX_DRAFTS)
        self.assertEqual(stored[0]["cust"], f"C{drafts.MAX_DRAFTS + 4}")

    def test_a_damaged_file_reads_as_empty_rather_than_crashing(self):
        with open(drafts.drafts_path(), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(drafts.load()["drafts"], [])

    def test_a_file_that_is_not_an_object_reads_as_empty(self):
        with open(drafts.drafts_path(), "w", encoding="utf-8") as handle:
            json.dump(["nope"], handle)
        self.assertEqual(drafts.load()["drafts"], [])

    def test_junk_entries_are_skipped(self):
        drafts.save({config.SCHEMA_VERSION_KEY: 1,
                     "drafts": ["nope", {"cust": "Ada"}]})
        self.assertEqual(len(drafts.load()["drafts"]), 1)

    def test_saving_keeps_a_backup(self):
        """Backups are timestamped siblings: drafts.json.20260831-2215.bak."""
        import glob
        drafts.add(FORM)
        drafts.add(dict(FORM, cust="Second"))
        self.assertTrue(glob.glob(drafts.drafts_path() + ".*.bak"))


class FromTheApp(DraftTestCase):
    def setUp(self):
        super().setUp()
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        receipt_render.clear_template_cache()
        self.root = tk.Tk()
        self.root.withdraw()
        self.infos = []
        self._showinfo = main.messagebox.showinfo
        main.messagebox.showinfo = lambda t, m, **k: self.infos.append(m)
        self.app = main.ReceiptApp(self.root)

    def tearDown(self):
        main.messagebox.showinfo = self._showinfo
        self.root.destroy()
        receipt_render.clear_template_cache()
        super().tearDown()

    def fill(self):
        self.app.cust_name.set("Ada")
        self.app.items_tree.insert("", tk.END, values=self.app.item_to_row(
            {"sku": "A", "desc": "Thing", "serial": "", "qty": "2",
             "price": "10", "discount": "0", "tax": "0", "warranty": ""}))

    def test_an_empty_form_is_not_saved(self):
        self.app.save_draft()
        self.assertTrue(self.infos)
        self.assertEqual(drafts.load()["drafts"], [])

    def test_saving_from_the_form(self):
        self.fill()
        self.app.save_draft()
        self.assertEqual(len(drafts.load()["drafts"]), 1)

    def test_the_status_line_says_no_number_was_used(self):
        self.fill()
        self.app.save_draft()
        self.assertIn("no invoice number", self.app.status_label.cget("text"))

    def test_saving_does_not_move_the_counter(self):
        before = invoice_counter.peek("W")
        self.fill()
        self.app.save_draft()
        self.assertEqual(invoice_counter.peek("W"), before)

    def test_restoring_puts_it_back_on_the_form(self):
        self.fill()
        self.app.save_draft()
        self.app.clear_form()
        self.app.load_draft(drafts.load()["drafts"][0])
        self.assertEqual(self.app.cust_name.get(), "Ada")
        self.assertEqual(len(self.app.items_tree.get_children()), 1)

    def test_the_restored_line_keeps_its_values(self):
        self.fill()
        self.app.save_draft()
        self.app.clear_form()
        self.app.load_draft(drafts.load()["drafts"][0])
        item = self.app.item_at(self.app.items_tree.get_children()[0])
        self.assertEqual(item["desc"], "Thing")
        self.assertEqual(item["qty"], "2")

    def test_the_current_form_reader_touches_no_counter(self):
        before = invoice_counter.peek("W")
        self.fill()
        self.app.current_form_data()
        self.assertEqual(invoice_counter.peek("W"), before)


class TheDraftsDialog(DraftTestCase):
    def setUp(self):
        super().setUp()
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        self.root = tk.Tk()
        self.root.withdraw()
        self.infos = []
        self._saved = (settings_ui.messagebox.showinfo,
                       settings_ui.messagebox.askyesno)
        settings_ui.messagebox.showinfo = lambda t, m, **k: self.infos.append(m)
        settings_ui.messagebox.askyesno = lambda *a, **k: True

    def tearDown(self):
        (settings_ui.messagebox.showinfo,
         settings_ui.messagebox.askyesno) = self._saved
        self.root.destroy()
        super().tearDown()

    def test_an_empty_list_says_so(self):
        dialog = settings_ui.DraftsDialog(self.root)
        self.assertIn("No drafts", dialog.note.cget("text"))
        dialog.win.destroy()

    def test_drafts_are_listed(self):
        drafts.add(FORM)
        dialog = settings_ui.DraftsDialog(self.root)
        self.assertEqual(len(dialog.tree.get_children()), 1)
        dialog.win.destroy()

    def test_the_number_it_had_is_shown(self):
        drafts.add(FORM)
        dialog = settings_ui.DraftsDialog(self.root)
        values = dialog.tree.item(dialog.tree.get_children()[0])["values"]
        self.assertIn("INV-W1001", [str(v) for v in values])
        dialog.win.destroy()

    def test_loading_hands_it_to_the_callback(self):
        drafts.add(FORM)
        loaded = []
        dialog = settings_ui.DraftsDialog(self.root, on_load=loaded.append)
        dialog.tree.selection_set(dialog.tree.get_children()[0])
        dialog.load()
        self.assertEqual(loaded[0]["cust"], "Ada")

    def test_loading_nothing_asks_for_a_selection(self):
        drafts.add(FORM)
        dialog = settings_ui.DraftsDialog(self.root)
        dialog.load()
        self.assertTrue(self.infos)
        dialog.win.destroy()

    def test_deleting_removes_it(self):
        drafts.add(FORM)
        dialog = settings_ui.DraftsDialog(self.root)
        dialog.tree.selection_set(dialog.tree.get_children()[0])
        dialog.delete()
        self.assertEqual(drafts.load()["drafts"], [])
        dialog.win.destroy()

    def test_declining_the_delete_keeps_it(self):
        drafts.add(FORM)
        settings_ui.messagebox.askyesno = lambda *a, **k: False
        dialog = settings_ui.DraftsDialog(self.root)
        dialog.tree.selection_set(dialog.tree.get_children()[0])
        dialog.delete()
        self.assertEqual(len(drafts.load()["drafts"]), 1)
        dialog.win.destroy()


if __name__ == "__main__":
    unittest.main()
