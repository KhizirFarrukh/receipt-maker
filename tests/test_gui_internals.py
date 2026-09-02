"""The smaller pieces of main.py, and the remaining edges of the counter and signing.

Dialog helpers, menu wiring, sticky values, product fill, and the failure branches
that only fire on a bad day — a broken lock file, an unreadable key, a display
that will not report its DPI.

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
import tk_support          # noqa: E402
import invoice_counter     # noqa: E402
import main                # noqa: E402
import product_catalogue   # noqa: E402
import receipt_history     # noqa: E402
import receipt_signing     # noqa: E402


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-int-")
        shutil.copy(os.path.join(PROJ, "appsettings.example.json"),
                    os.path.join(self.dir, "appsettings.json"))
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)
        import receipt_render
        receipt_render.clear_template_cache()
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        tk_support.destroy(self)
        config.set_app_dir(self._app_dir)
        import receipt_render
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def app(self):
        instance = main.ReceiptApp(self.root)
        self.addCleanup(instance.__dict__.clear)
        return instance


class ErrorDialog(AppTestCase):
    """show_error is what a user sees on the worst day; it must build."""

    def build(self, detail=None):
        self.root.wait_window = lambda *a, **k: None
        main.show_error(self.root, "It went wrong", "A plain summary", detail)
        return [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)][-1]

    def test_a_summary_only_dialog_builds(self):
        dialog = self.build()
        self.assertEqual(dialog.title(), "It went wrong")
        dialog.destroy()

    def test_a_dialog_with_details_builds(self):
        dialog = self.build("Traceback (most recent call last): ...")
        self.assertTrue(dialog.winfo_children())
        dialog.destroy()

    def test_the_details_pane_toggles_both_ways(self):
        dialog = self.build("some traceback")
        buttons = [w for w in dialog.winfo_children()[0].winfo_children()
                   if isinstance(w, tk.Frame) or hasattr(w, "winfo_children")]
        toggles = []
        for holder in buttons:
            for child in holder.winfo_children():
                if hasattr(child, "cget"):
                    try:
                        if "details" in str(child.cget("text")).lower():
                            toggles.append(child)
                    except tk.TclError:
                        pass
        self.assertTrue(toggles, "there should be a show/hide details control")
        toggles[0].invoke()          # show
        toggles[0].invoke()          # hide
        dialog.destroy()

    def test_copy_details_puts_the_traceback_on_the_clipboard(self):
        dialog = self.build("the traceback text")
        for holder in dialog.winfo_children()[0].winfo_children():
            for child in getattr(holder, "winfo_children", lambda: [])():
                try:
                    if "Copy" in str(child.cget("text")):
                        child.invoke()
                        self.assertIn("traceback", dialog.clipboard_get())
                except (tk.TclError, AttributeError):
                    pass
        dialog.destroy()

    def test_it_is_written_to_the_log(self):
        with self.assertLogs("receipt_maker", level="ERROR") as captured:
            self.build("detail here")
        self.assertIn("It went wrong", "\n".join(captured.output))


class RememberDialog(AppTestCase):
    def test_it_builds_and_answers_no_when_closed(self):
        self.root.wait_window = lambda *a, **k: None
        answer, remember = main.ask_with_memory(self.root, "Q", "Open the folder?")
        self.assertFalse(answer)
        self.assertFalse(remember)

    def test_the_yes_and_no_buttons_are_present(self):
        self.root.wait_window = lambda *a, **k: None
        main.ask_with_memory(self.root, "Q", "Open the folder?")
        dialog = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
        labels = []
        for frame in dialog.winfo_children():
            for child in frame.winfo_children():
                for widget in [child] + list(getattr(child, "winfo_children",
                                                     lambda: [])()):
                    try:
                        labels.append(str(widget.cget("text")))
                    except tk.TclError:
                        pass
        self.assertIn("Yes", labels)
        self.assertIn("No", labels)
        dialog.destroy()


class WindowSetup(AppTestCase):
    def test_dpi_awareness_never_raises(self):
        main.ReceiptApp.enable_dpi_awareness()

    def test_scaling_falls_back_when_the_display_will_not_say(self):
        app = self.app()

        class Silent:
            def winfo_fpixels(self, _):
                raise tk.TclError("no display")

            def tk_call_stub(self, *a):
                raise tk.TclError("no")
        app._apply_scaling(Silent())
        self.assertGreaterEqual(app.ui_scale, 1.0)

    def test_column_layout_by_type(self):
        layout = main.ReceiptApp._column_layout
        self.assertEqual(layout({"type": "amount"})["anchor"], tk.E)
        self.assertEqual(layout({"type": "integer"})["anchor"], tk.CENTER)
        self.assertEqual(layout({"type": "boolean"})["anchor"], tk.CENTER)
        self.assertEqual(layout({"type": "text", "key": "desc"})["width"], 190)
        self.assertEqual(layout({"type": "text", "key": "sku"})["width"], 110)


class OpenFolder(AppTestCase):
    def test_it_swallows_a_failure_rather_than_crashing(self):
        """Opening a folder is a convenience; failing it must not raise."""
        main.ReceiptApp._open_folder(os.path.join(self.dir, "no-such-folder"))

    def test_it_opens_a_real_folder(self):
        calls = []
        real = os.startfile if hasattr(os, "startfile") else None
        try:
            if real:
                os.startfile = lambda path: calls.append(path)
            main.ReceiptApp._open_folder(self.dir)
        finally:
            if real:
                os.startfile = real
        if real:
            self.assertEqual(calls, [self.dir])


class StickyValues(AppTestCase):
    def test_nothing_remembered_is_an_empty_dict(self):
        self.assertEqual(self.app().sticky_values(), {})

    def test_a_sticky_field_is_remembered_and_offered_back(self):
        app = self.app()
        for field in app.input_fields:
            if field["key"] == "serial":
                field["sticky"] = True
        app.remember_sticky({"serial": "SN-9", "sku": "ignored"})
        self.assertEqual(app.sticky_values(), {"serial": "SN-9"})

    def test_nothing_is_written_when_no_field_is_sticky(self):
        app = self.app()
        app.remember_sticky({"serial": "SN-9"})
        self.assertEqual(config.load_state(), {})

    def test_corrupt_remembered_state_is_ignored(self):
        app = self.app()
        config.save_state({"sticky_line_item": "not a dict"})
        self.assertEqual(app.sticky_values(), {})


class FillFromProduct(AppTestCase):
    def seed(self):
        product_catalogue.save({config.SCHEMA_VERSION_KEY: 1, "products": [
            {"sku": "KB-87", "barcode": "5012345678900", "name": "Keyboard",
             "list_price": "8500.00"}]})

    def test_picking_nothing_changes_nothing(self):
        import settings_ui
        app = self.app()
        original = settings_ui.pick_product
        try:
            settings_ui.pick_product = lambda parent: None
            variables = {"desc": tk.StringVar(value="untouched")}
            app._fill_from_product(variables)
            self.assertEqual(variables["desc"].get(), "untouched")
        finally:
            settings_ui.pick_product = original

    def test_picking_a_product_fills_the_fields(self):
        import settings_ui
        self.seed()
        app = self.app()
        original = settings_ui.pick_product
        try:
            chosen = product_catalogue.find(product_catalogue.load(), "KB-87")
            settings_ui.pick_product = lambda parent: chosen
            variables = {key: tk.StringVar() for key in ("sku", "desc", "price", "qty")}
            app._fill_from_product(variables)
            self.assertEqual(variables["sku"].get(), "KB-87")
            self.assertEqual(variables["desc"].get(), "Keyboard")
            self.assertEqual(variables["price"].get(), "8500.00")
            self.assertEqual(variables["qty"].get(), "",
                             "quantity is the user's, not the product's")
        finally:
            settings_ui.pick_product = original


class MenuHandlers(AppTestCase):
    """Each menu entry must open its dialog without blocking."""

    def setUp(self):
        super().setUp()
        import settings_ui
        self.opened = []
        self._saved = (settings_ui.open_settings, settings_ui.open_fields,
                       settings_ui.open_signing_keys, settings_ui.open_history,
                       settings_ui.open_products)
        settings_ui.open_settings = lambda p, on_saved=None: self.opened.append("settings")
        settings_ui.open_fields = lambda p, on_saved=None: self.opened.append("fields")
        settings_ui.open_signing_keys = lambda p, on_changed=None: self.opened.append("keys")
        settings_ui.open_history = lambda p, on_load=None: self.opened.append("history")
        settings_ui.open_products = lambda p, on_saved=None: self.opened.append("products")

    def tearDown(self):
        import settings_ui
        (settings_ui.open_settings, settings_ui.open_fields,
         settings_ui.open_signing_keys, settings_ui.open_history,
         settings_ui.open_products) = self._saved
        super().tearDown()

    def test_every_menu_entry_opens_its_dialog(self):
        app = self.app()
        app.open_settings_dialog()
        app.open_fields_dialog()
        app.open_signing_keys_dialog()
        app.open_history_dialog()
        app.open_products_dialog()
        self.assertEqual(sorted(self.opened),
                         ["fields", "history", "keys", "products", "settings"])

    def test_saving_settings_reports_what_needs_a_restart(self):
        app = self.app()
        app._settings_saved()
        self.assertIn("next time the app starts", app.status_label["text"])


class LoadingFromHistory(AppTestCase):
    ENTRY = {"inv_no": "INV-W1001", "date_str": "26 Aug 2026", "cust": "Ada",
             "phone": "123", "email": "a@b.c", "receipt_type": "In Store",
             "shipping": "50", "items": [
                 {"sku": "KB-87", "desc": "Keyboard", "serial": "", "qty": 1,
                  "price": "10.00", "discount": "0", "tax": "0", "warranty": ""}]}

    def test_the_whole_form_is_restored(self):
        receipt_history.record(self.ENTRY, "", True)
        app = self.app()
        app.load_from_history(receipt_history.entries()[0])
        self.assertEqual(app.inv_no.get(), "INV-W1001")
        self.assertEqual(app.cust_name.get(), "Ada")
        self.assertEqual(app.cust_phone.get(), "123")
        self.assertEqual(app.cust_email.get(), "a@b.c")
        self.assertEqual(app.shipping.get(), "50")
        self.assertEqual(app.receipt_type.get(), "In Store")
        self.assertEqual(len(app.items_tree.get_children()), 1)

    def test_an_unknown_receipt_type_is_ignored_rather_than_crashing(self):
        receipt_history.record(dict(self.ENTRY, receipt_type="Wholesale"), "", True)
        app = self.app()
        app.load_from_history(receipt_history.entries()[0])
        self.assertIn(app.receipt_type.get(), app.type_labels)

    def test_the_status_says_what_happened(self):
        receipt_history.record(self.ENTRY, "", True)
        app = self.app()
        app.load_from_history(receipt_history.entries()[0])
        self.assertIn("from history", app.status_label["text"])


class RememberingTheFolderAnswer(AppTestCase):
    def test_it_is_persisted_and_reported(self):
        app = self.app()
        app._remember_open_folder(True)
        ui = config.load_app_settings()["ui"]
        self.assertFalse(ui["ask_open_folder"])
        self.assertTrue(ui["open_folder_after_generate"])
        self.assertIn("remembered", app.status_label["text"])

    def test_a_failure_to_persist_is_logged_not_raised(self):
        """A preference is never worth failing over."""
        app = self.app()
        original = config.update_app_settings
        try:
            config.update_app_settings = lambda *a, **k: (_ for _ in ()).throw(
                OSError("read-only"))
            with self.assertLogs("receipt_maker", level="WARNING"):
                app._remember_open_folder(True)
        finally:
            config.update_app_settings = original


class CounterEdges(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-lock-")
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_stale_lock_is_broken(self):
        import time
        path = invoice_counter.counter_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lock = path + ".lock"
        with open(lock, "w", encoding="utf-8") as f:
            f.write("99999")
        old = time.time() - (invoice_counter.LOCK_STALE_SECONDS + 60)
        os.utime(lock, (old, old))

        with self.assertLogs("receipt_maker", level="WARNING"):
            self.assertEqual(invoice_counter.peek("W"), config.INVOICE_START_NUMBER)
        self.assertFalse(os.path.exists(lock))

    def test_a_held_lock_times_out_with_an_explanation(self):
        path = invoice_counter.counter_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".lock", "w", encoding="utf-8") as f:
            f.write("1")
        try:
            with self.assertRaises(invoice_counter.CounterError) as ctx:
                invoice_counter._FileLock(path, timeout=0.2).__enter__()
            self.assertIn("Another copy of the app", str(ctx.exception))
        finally:
            os.remove(path + ".lock")

    def test_a_counter_file_that_is_not_the_expected_shape_is_refused(self):
        path = invoice_counter.counter_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"series": "not an object"}')
        with self.assertRaises(invoice_counter.CounterError) as ctx:
            invoice_counter.peek("W")
        self.assertIn("not in the expected format", str(ctx.exception))

    def test_scanning_an_absent_output_folder_returns_nothing(self):
        shutil.rmtree(os.path.join(self.dir, "invoices"))
        self.assertIsNone(invoice_counter.scan_filenames("W"))

    def test_a_negative_stored_counter_is_reseeded(self):
        invoice_counter.peek("W")
        path = invoice_counter.counter_path()
        import json
        state = json.load(open(path, encoding="utf-8"))
        state["series"]["W"]["next"] = -5
        json.dump(state, open(path, "w", encoding="utf-8"))
        self.assertEqual(invoice_counter.peek("W"), config.INVOICE_START_NUMBER)

    def test_note_unused_names_the_series_and_reason(self):
        with self.assertLogs("receipt_maker", level="WARNING") as captured:
            invoice_counter.note_unused("W", 1001, "the till caught fire")
        joined = "\n".join(captured.output)
        self.assertIn("1001", joined)
        self.assertIn("caught fire", joined)


class SigningEdges(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rm-sign-edge-")
        self._saved = (receipt_signing.SIGNING_DIR, receipt_signing.KNOWN_CERTS_DIR)
        receipt_signing.SIGNING_DIR = self.dir
        receipt_signing.KNOWN_CERTS_DIR = os.path.join(self.dir, "previous")
        self.key = os.path.join(self.dir, "k.pem")
        self.cert = os.path.join(self.dir, "c.pem")

    def tearDown(self):
        (receipt_signing.SIGNING_DIR, receipt_signing.KNOWN_CERTS_DIR) = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_verifying_a_missing_pdf_raises_file_not_found(self):
        receipt_signing.generate_key_pair(self.key, self.cert, org_name="Acme")
        with self.assertRaises(FileNotFoundError):
            receipt_signing.verify_pdf(os.path.join(self.dir, "nope.pdf"), self.cert)

    def test_verifying_without_a_certificate_explains_where_to_put_one(self):
        from tests.test_signing import blank_pdf
        pdf = blank_pdf(os.path.join(self.dir, "d.pdf"))
        with self.assertRaises(FileNotFoundError) as ctx:
            receipt_signing.verify_pdf(pdf, os.path.join(self.dir, "nope.pem"))
        self.assertIn("certificate", str(ctx.exception).lower())

    def test_a_corrupt_pdf_reports_invalid_rather_than_raising(self):
        receipt_signing.generate_key_pair(self.key, self.cert, org_name="Acme")
        junk = os.path.join(self.dir, "junk.pdf")
        with open(junk, "w", encoding="utf-8") as f:
            f.write("not a pdf at all")
        result = receipt_signing.verify_pdf(junk, self.cert)
        self.assertEqual(result.status, receipt_signing.INVALID)
        self.assertIn("corrupted", result.detail)

    def test_is_signed_distinguishes_the_two_cases(self):
        from tests.test_signing import blank_pdf
        receipt_signing.generate_key_pair(self.key, self.cert, org_name="Acme")
        plain = blank_pdf(os.path.join(self.dir, "plain.pdf"))
        self.assertFalse(receipt_signing.is_signed(plain))
        receipt_signing.sign_pdf(plain, self.key, self.cert)
        self.assertTrue(receipt_signing.is_signed(plain))

    def test_signing_with_a_missing_key_is_a_runtime_error(self):
        from tests.test_signing import blank_pdf
        pdf = blank_pdf(os.path.join(self.dir, "d.pdf"))
        with self.assertRaises(RuntimeError) as ctx:
            receipt_signing.sign_pdf(pdf, os.path.join(self.dir, "nope.pem"), self.cert)
        self.assertIn("Could not load", str(ctx.exception))

    def test_a_verified_result_reports_itself_as_verified(self):
        result = receipt_signing.VerifyResult(status=receipt_signing.VERIFIED, title="x")
        self.assertTrue(result.verified)
        self.assertFalse(
            receipt_signing.VerifyResult(status=receipt_signing.INVALID, title="x").verified)

    def test_remembering_an_unreadable_certificate_is_survivable(self):
        with open(self.cert, "w", encoding="utf-8") as f:
            f.write("not a certificate")
        self.assertIsNotNone(receipt_signing.remember_current_certificate(self.cert))


if __name__ == "__main__":
    unittest.main()
