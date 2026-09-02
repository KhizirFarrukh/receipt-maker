"""main.py — the receipt form, driven headlessly.

Covers the paths a user actually walks: adding and editing items, the date
picker, validation refusals, the threaded generation, and the verify/sign tools.

Two standing rules here (claude_chat/PITFALLS.md):
  * every modal is stubbed — an unstubbed one hangs the suite rather than
    failing it;
  * the app is built through `receipt_app()`, which tears Tk down deterministically.

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

import config             # noqa: E402
import tk_support          # noqa: E402
import invoice_counter    # noqa: E402
import main               # noqa: E402
import receipt_service    # noqa: E402
import receipt_signing    # noqa: E402


class MainTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-main-")
        shutil.copy(os.path.join(PROJ, "appsettings.example.json"),
                    os.path.join(self.dir, "appsettings.json"))
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)
        import receipt_render
        receipt_render.clear_template_cache()

        self.errors, self.infos, self.warnings = [], [], []
        self.asked, self.shown = [], []
        self._saved = (
            main.messagebox.showerror, main.messagebox.showinfo,
            main.messagebox.showwarning, main.messagebox.askyesno,
            main.messagebox.askyesnocancel, main.ask_with_memory,
            main.show_error, main.filedialog.askopenfilename,
            main.filedialog.askopenfilenames, receipt_service.generate,
        )
        main.messagebox.showerror = lambda t, m, **k: self.errors.append(m)
        main.messagebox.showinfo = lambda t, m, **k: self.infos.append(m)
        main.messagebox.showwarning = lambda t, m, **k: self.warnings.append(m)
        main.messagebox.askyesno = lambda *a, **k: True
        main.messagebox.askyesnocancel = lambda *a, **k: True
        main.ask_with_memory = lambda *a, **k: (False, False)
        main.show_error = lambda parent, title, summary, detail=None: self.shown.append(
            (title, summary))
        main.filedialog.askopenfilename = lambda **k: ""
        main.filedialog.askopenfilenames = lambda **k: ()

        self.root = tk.Tk()
        self.root.withdraw()
        self.app = main.ReceiptApp(self.root)

    def tearDown(self):
        (main.messagebox.showerror, main.messagebox.showinfo,
         main.messagebox.showwarning, main.messagebox.askyesno,
         main.messagebox.askyesnocancel, main.ask_with_memory,
         main.show_error, main.filedialog.askopenfilename,
         main.filedialog.askopenfilenames, receipt_service.generate) = self._saved
        self.app.__dict__.clear()
        tk_support.destroy(self)
        config.set_app_dir(self._app_dir)
        import receipt_render
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def add_item(self, **overrides):
        item = {"sku": "KB-87", "desc": "Keyboard", "serial": "SN-1", "qty": "2",
                "price": "10.00", "discount": "0.00", "tax": "0.00",
                "warranty": "No Warranty"}
        item.update(overrides)
        self.app.items_tree.insert("", tk.END, values=self.app.item_to_row(item))

    def fill_customer(self):
        self.app.cust_name.set("Ada Lovelace")


class FormBasics(MainTestCase):
    def test_the_window_is_titled_with_the_business(self):
        self.assertIn("Your Company", self.root.title())

    def test_an_invoice_number_is_suggested(self):
        self.assertTrue(self.app.inv_no.get().startswith("INV-W"))

    def test_switching_receipt_type_changes_the_series(self):
        self.app.receipt_type.set("In Store")
        self.app.refresh_invoice_number()
        self.assertTrue(self.app.inv_no.get().startswith("INV-S"))

    def test_the_date_defaults_to_today(self):
        from datetime import date
        self.assertEqual(self.app.date.get(),
                         date.today().strftime(config.date_display_format()))

    def test_clearing_the_form_empties_it(self):
        self.fill_customer()
        self.add_item()
        self.app.shipping.set("50")
        self.app.clear_form()
        self.assertEqual(self.app.cust_name.get(), "")
        self.assertEqual(self.app.shipping.get(), "")
        self.assertEqual(self.app.items_tree.get_children(), ())

    def rebuilt_with(self, currency):
        """A fresh app against changed currency settings. Caller destroys nothing."""
        config.update_app_settings({"currency": currency})
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        app = main.ReceiptApp(root)
        self.addCleanup(app.__dict__.clear)
        return app

    def test_money_labels_carry_the_currency_code(self):
        app = self.rebuilt_with({"code": "USD", "symbol": "$"})
        self.assertIn("USD", app._money_field("Shipping"))

    def test_a_currency_without_a_code_falls_back_to_the_symbol(self):
        app = self.rebuilt_with({"code": "", "symbol": "€"})
        self.assertIn("€", app._money_field("Shipping"))

    def test_no_code_and_no_symbol_leaves_the_label_bare(self):
        app = self.rebuilt_with({"code": "", "symbol": ""})
        self.assertEqual(app._money_field("Shipping"), "Shipping")


class ItemManagement(MainTestCase):
    def test_removing_the_selected_item(self):
        self.add_item()
        self.app.items_tree.selection_set(self.app.items_tree.get_children()[0])
        self.app.remove_item()
        self.assertEqual(self.app.items_tree.get_children(), ())

    def test_removing_nothing_is_harmless(self):
        self.add_item()
        self.app.remove_item()
        self.assertEqual(len(self.app.items_tree.get_children()), 1)

    def test_editing_without_a_selection_tells_the_user(self):
        self.add_item()
        self.app.edit_item()
        self.assertTrue(self.infos)

    def test_the_item_dialog_builds(self):
        self.root.wait_window = lambda *a, **k: None
        self.app.open_item_dialog()

    def test_the_edit_dialog_builds_from_an_existing_row(self):
        self.add_item()
        self.root.wait_window = lambda *a, **k: None
        self.app.open_item_dialog(self.app.items_tree.get_children()[0])

    def test_a_double_click_off_any_row_does_nothing(self):
        class Event:
            y = 9999
        self.app.on_item_double_click(Event())
        self.assertFalse(self.infos, "clicking empty space should not prompt")


class DatePicker(MainTestCase):
    def test_it_opens_and_closes(self):
        self.app.show_date_picker()
        self.assertIsNotNone(self.app.date_picker)
        self.app.close_date_picker()
        self.assertIsNone(self.app.date_picker)

    def test_opening_twice_reuses_the_same_window(self):
        self.app.show_date_picker()
        first = self.app.date_picker
        self.app.show_date_picker()
        self.assertIs(self.app.date_picker, first)
        self.app.close_date_picker()

    def test_picking_a_date_fills_the_field(self):
        from datetime import date
        self.app.show_date_picker()
        self.app.select_date(date(2026, 1, 31))
        self.assertEqual(self.app.date.get(), "31 Jan 2026")

    def test_changing_month_forwards_and_back(self):
        from datetime import date
        self.app.show_date_picker()
        frame = self.app.date_picker.winfo_children()[0]
        self.app.change_date_picker_month(frame, 2026, 12, 1, date(2026, 12, 1))
        self.app.change_date_picker_month(frame, 2026, 1, -1, date(2026, 1, 1))
        self.app.close_date_picker()

    def day_labels(self, selected):
        """Every day-button caption on the calendar showing `selected`'s month."""
        self.app.show_date_picker()
        frame = self.app.date_picker.winfo_children()[0]
        self.app.change_date_picker_month(
            frame, selected.year, selected.month, 0, selected)
        frame = self.app.date_picker.winfo_children()[0]
        captions = []
        for child in frame.winfo_children():
            try:
                captions.append(str(child.cget("text")))
            except tk.TclError:
                pass
        self.app.close_date_picker()
        return captions

    def test_today_is_marked_when_it_is_not_the_selected_day(self):
        """Two different marks, so neither hides the other."""
        from datetime import date
        today = date.today()
        other = today.replace(day=2 if today.day != 2 else 3)
        captions = self.day_labels(other)
        self.assertIn(f"*{today.day}*", captions,
                      "today should be starred when a different day is selected")
        self.assertIn(f"[{other.day}]", captions,
                      "the selected day should be bracketed")

    def test_the_selected_day_wins_when_it_is_also_today(self):
        """Selected takes priority -- one mark, not both."""
        from datetime import date
        today = date.today()
        captions = self.day_labels(today)
        self.assertIn(f"[{today.day}]", captions)
        self.assertNotIn(f"*{today.day}*", captions)

    def test_a_month_that_is_not_this_one_stars_nothing(self):
        from datetime import date
        far = date(2030, 6, 15)
        captions = self.day_labels(far)
        self.assertFalse([c for c in captions if c.startswith("*")],
                         "no day in a different month is today")

    def test_parsing_accepts_several_formats(self):
        for text in ("31 Jan 2026", "2026-01-31", "31/01/2026"):
            self.app.date.set(text)
            self.assertIsNotNone(self.app.parse_selected_date(), text)

    def test_unparseable_text_is_none_not_a_crash(self):
        self.app.date.set("sometime next week")
        self.assertIsNone(self.app.parse_selected_date())

    def test_an_empty_date_is_none(self):
        self.app.date.set("")
        self.assertIsNone(self.app.parse_selected_date())


class GenerationValidation(MainTestCase):
    """Each refusal must name what is wrong, and consume no invoice number."""

    def setUp(self):
        super().setUp()
        self.generated = []
        self.app._run_generation = lambda d, out, reserved=None: self.generated.append(d)

    def test_a_missing_invoice_number_is_refused(self):
        self.app.inv_no.set("")
        self.add_item()
        self.app.generate_pdf()
        self.assertTrue(any("Invoice No" in m for m in self.errors))
        self.assertEqual(self.generated, [])

    def test_no_items_is_refused(self):
        self.fill_customer()
        self.app.generate_pdf()
        self.assertTrue(any("At least one item" in m for m in self.errors))

    def test_non_numeric_shipping_is_refused(self):
        self.add_item()
        self.app.shipping.set("free")
        self.app.generate_pdf()
        self.assertTrue(any("Shipping must be a number" in m for m in self.errors))

    def test_negative_shipping_is_refused(self):
        self.add_item()
        self.app.shipping.set("-5")
        self.app.generate_pdf()
        self.assertTrue(any("cannot be negative" in m for m in self.errors))

    def test_a_non_numeric_quantity_names_the_item(self):
        self.add_item(qty="two")
        self.app.generate_pdf()
        self.assertTrue(any("Keyboard" in m for m in self.errors), self.errors)

    def test_refusals_consume_no_invoice_number(self):
        before = invoice_counter.peek("W")
        self.app.generate_pdf()          # no items
        self.assertEqual(invoice_counter.peek("W"), before)

    def test_a_valid_form_reaches_generation(self):
        self.fill_customer()
        self.add_item()
        self.app.generate_pdf()
        self.assertEqual(len(self.generated), 1)
        self.assertEqual(self.generated[0]["cust"], "Ada Lovelace")

    def test_an_empty_customer_becomes_a_walk_in(self):
        self.add_item()
        self.app.generate_pdf()
        self.assertEqual(self.generated[0]["cust"], "Walk-in Customer")

    def test_numbers_arrive_as_numbers(self):
        self.add_item()
        self.app.generate_pdf()
        item = self.generated[0]["items"][0]
        self.assertEqual(item["qty"], 2)
        self.assertEqual(item["price"], 10.0)


class InvoiceNumberClaiming(MainTestCase):
    def setUp(self):
        super().setUp()
        self.app._run_generation = lambda d, out, reserved=None: None

    def test_the_suggested_number_is_consumed(self):
        before = invoice_counter.peek("W")
        self.add_item()
        self.app.generate_pdf()
        self.assertEqual(invoice_counter.peek("W"), before + 1)

    def test_a_hand_typed_number_is_honoured(self):
        self.add_item()
        self.app.inv_no.set("INV-W5000")
        number, reserved = self.app._claim_invoice_number("INV-W5000")
        self.assertEqual(number, "INV-W5000")
        self.assertIsNone(reserved, "a typed number consumes nothing")

    def test_the_counter_is_pushed_past_a_hand_typed_number(self):
        self.app._claim_invoice_number("INV-W5000")
        self.assertEqual(invoice_counter.peek("W"), 5001)

    def test_an_unparseable_number_is_still_used(self):
        number, reserved = self.app._claim_invoice_number("CUSTOM-THING")
        self.assertEqual(number, "CUSTOM-THING")
        self.assertIsNone(reserved)


class ThreadedGeneration(MainTestCase):
    def drive(self, out_path):
        import time
        self.app._run_generation(
            {"inv_no": "INV-W1", "date_str": "d", "cust": "c", "phone": "", "email": "",
             "items": [{"desc": "x", "qty": 1, "price": 1.0}],
             "receipt_type": "Online", "shipping": 0.0},
            out_path, "W")
        for _ in range(400):
            self.root.update()
            if not getattr(self.app, "_generating", True):
                return
            time.sleep(0.005)

    def test_a_successful_run_reports_and_re_enables(self):
        receipt_service.generate = lambda d, o, cb=None, **kw: True
        self.drive(os.path.join(self.dir, "invoices", "x.pdf"))
        self.assertFalse(self.app._generating)
        self.assertEqual(str(self.app.generate_button["state"]), "normal")
        self.assertIn("signed", self.app.status_label["text"])

    def test_an_unsigned_run_says_unsigned(self):
        receipt_service.generate = lambda d, o, cb=None, **kw: False
        self.drive(os.path.join(self.dir, "invoices", "x.pdf"))
        self.assertIn("unsigned", self.app.status_label["text"])

    def test_a_failure_surfaces_a_diagnostic_with_a_traceback(self):
        def boom(d, o, cb=None, **kw):
            raise RuntimeError("signing key not found")
        receipt_service.generate = boom
        self.drive(os.path.join(self.dir, "invoices", "x.pdf"))
        self.assertTrue(self.shown)
        self.assertIn("signing key not found", self.shown[0][1])
        self.assertEqual(str(self.app.generate_button["state"]), "normal")

    def test_a_second_job_cannot_start_while_one_runs(self):
        self.app._generating = True
        calls = []
        self.app.root.after = lambda *a, **k: calls.append(1)
        self.app._run_generation({"inv_no": "X"}, "x.pdf")
        self.assertEqual(calls, [])


class SigningTools(MainTestCase):
    def setUp(self):
        super().setUp()
        self.key, self.cert = receipt_service.signing_key_paths()
        os.makedirs(os.path.dirname(self.key), exist_ok=True)
        receipt_signing.generate_key_pair(self.key, self.cert, org_name="Acme")
        from tests.test_signing import blank_pdf
        self.blank_pdf = blank_pdf

    def signed_pdf(self, name="signed.pdf"):
        path = self.blank_pdf(os.path.join(self.dir, name))
        receipt_signing.sign_pdf(path, self.key, self.cert)
        return path

    def test_verifying_a_genuine_receipt(self):
        main.filedialog.askopenfilename = lambda **k: self.signed_pdf()
        self.app.verify_receipt_dialog()
        self.assertTrue(self.infos, "a genuine receipt should be reported as verified")
        self.assertIn("Verified", self.app.status_label["text"])

    def test_verifying_an_unsigned_pdf_warns(self):
        main.filedialog.askopenfilename = lambda **k: self.blank_pdf("plain.pdf")
        self.app.verify_receipt_dialog()
        self.assertTrue(self.warnings)

    def test_verifying_a_tampered_receipt_reports_invalid(self):
        path = self.signed_pdf()
        raw = bytearray(open(path, "rb").read())
        raw[len(raw) // 2] ^= 0x01
        open(path, "wb").write(bytes(raw))
        main.filedialog.askopenfilename = lambda **k: path
        self.app.verify_receipt_dialog()
        self.assertTrue(self.errors)

    def test_cancelling_the_picker_does_nothing(self):
        main.filedialog.askopenfilename = lambda **k: ""
        self.app.verify_receipt_dialog()
        self.assertFalse(self.infos + self.errors + self.warnings)

    def test_verifying_without_a_certificate_explains(self):
        os.remove(self.cert)
        main.filedialog.askopenfilename = lambda **k: self.blank_pdf("x.pdf")
        self.app.verify_receipt_dialog()
        self.assertTrue(self.errors)

    def test_signing_existing_pdfs(self):
        one = self.blank_pdf("one.pdf")
        main.filedialog.askopenfilenames = lambda **k: (one,)
        self.app.sign_existing_pdfs_dialog()
        self.assertTrue(self.infos)
        self.assertEqual(receipt_signing.verify_pdf(one, self.cert).status,
                         receipt_signing.VERIFIED)

    def test_already_signed_files_are_skipped(self):
        signed = self.signed_pdf()
        main.filedialog.askopenfilenames = lambda **k: (signed,)
        self.app.sign_existing_pdfs_dialog()
        self.assertIn("skipped", self.app.status_label["text"])

    def test_signing_without_a_key_explains(self):
        os.remove(self.key)
        main.filedialog.askopenfilenames = lambda **k: (self.blank_pdf("x.pdf"),)
        self.app.sign_existing_pdfs_dialog()
        self.assertTrue(any("not found" in m for m in self.errors))

    def test_a_file_that_cannot_be_signed_is_reported_not_fatal(self):
        junk = os.path.join(self.dir, "junk.pdf")
        with open(junk, "w", encoding="utf-8") as f:
            f.write("not a pdf")
        main.filedialog.askopenfilenames = lambda **k: (junk,)
        self.app.sign_existing_pdfs_dialog()
        self.assertTrue(self.warnings, "failures should be summarised, not raised")

    def test_cancelling_the_multi_picker_does_nothing(self):
        main.filedialog.askopenfilenames = lambda **k: ()
        self.app.sign_existing_pdfs_dialog()
        self.assertFalse(self.infos + self.warnings)


class WarrantyHelpers(MainTestCase):
    def test_an_option_without_a_hash_is_returned_as_is(self):
        self.assertEqual(main.ReceiptApp.resolve_warranty("No Warranty", ""),
                         "No Warranty")

    def test_a_hash_option_needs_a_positive_number(self):
        self.assertIsNone(main.ReceiptApp.resolve_warranty("# Months", "0"))
        self.assertEqual(main.ReceiptApp.resolve_warranty("# Months", "6"), "6 Months")

    def test_matching_recovers_the_number(self):
        self.assertEqual(
            main.ReceiptApp.match_warranty_option("6 Months", ["# Months", "None"]),
            ("# Months", "6"))


class StartupFailures(MainTestCase):
    def test_launch_reports_a_settings_problem_and_exits_non_zero(self):
        import json
        settings = config.default_app_settings()
        settings["currency"]["decimals"] = 99
        with open(os.path.join(self.dir, "appsettings.json"), "w", encoding="utf-8") as f:
            json.dump(settings, f)

        shown = {}
        original = main.show_error
        try:
            main.show_error = lambda p, t, s, d=None: shown.update(title=t, summary=s)
            code = main.launch()
        finally:
            main.show_error = original

        self.assertEqual(code, 2)
        self.assertIn("currency.decimals", shown["summary"])

    def test_an_unexpected_startup_failure_exits_one(self):
        shown = {}

        class Exploding:
            """launch() calls enable_dpi_awareness() on the class first."""
            @staticmethod
            def enable_dpi_awareness():
                pass

            def __init__(self, root):
                raise RuntimeError("boom")

        original_error, original_app = main.show_error, main.ReceiptApp
        try:
            main.show_error = lambda p, t, s, d=None: shown.update(title=t)
            main.ReceiptApp = Exploding
            code = main.launch()
        finally:
            main.show_error, main.ReceiptApp = original_error, original_app
        self.assertEqual(code, 1)
        self.assertEqual(shown["title"], "Cannot start")


class SmokeTestEntryPoint(MainTestCase):
    def test_a_failing_smoke_test_returns_non_zero_rather_than_raising(self):
        """A windowed build has no console; an escaping exception would hang."""
        import logging

        original = receipt_service.render_pdf
        log = logging.getLogger("receipt_maker")
        previous_level = log.level
        try:
            log.setLevel(logging.CRITICAL)   # the traceback is expected; do not print it
            receipt_service.render_pdf = lambda html, path: (_ for _ in ()).throw(
                RuntimeError("no chromium"))
            self.assertEqual(main.run_smoke_test(), 1)
        finally:
            log.setLevel(previous_level)
            receipt_service.render_pdf = original

    def test_a_working_smoke_test_returns_zero(self):
        original = receipt_service.render_pdf
        try:
            receipt_service.render_pdf = lambda html, path: open(path, "wb").write(b"%PDF")
            self.assertEqual(main.run_smoke_test(), 0)
        finally:
            receipt_service.render_pdf = original


if __name__ == "__main__":
    unittest.main()
