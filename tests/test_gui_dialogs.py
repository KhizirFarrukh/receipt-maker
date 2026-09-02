"""settings_ui — the in-app editors, driven headlessly.

Every dialog here writes to a file the app later reads, so the tests care about
what reaches disk, not about pixels. They construct the real dialogs against a
withdrawn Tk root and call the same handlers the buttons call.

**Modal dialogs are stubbed, always.** An unstubbed one does not fail the suite,
it *hangs* it — see claude_chat/PITFALLS.md.

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
import tk_support          # noqa: E402
import product_catalogue   # noqa: E402
import receipt_history     # noqa: E402
import receipt_signing     # noqa: E402
import settings_ui         # noqa: E402


class DialogTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-gui-")
        shutil.copy(os.path.join(PROJ, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)
        import receipt_render
        receipt_render.clear_template_cache()

        self.root = tk.Tk()
        self.root.withdraw()

        # Nothing may open a real modal window.
        self.errors, self.infos, self.asked = [], [], []
        self._saved = (settings_ui.messagebox.showerror,
                       settings_ui.messagebox.showinfo,
                       settings_ui.messagebox.askyesno,
                       settings_ui.filedialog.askopenfilename)
        settings_ui.messagebox.showerror = lambda t, m, **k: self.errors.append(m)
        settings_ui.messagebox.showinfo = lambda t, m, **k: self.infos.append(m)
        settings_ui.messagebox.askyesno = lambda *a, **k: self.asked.append(a) or True
        settings_ui.filedialog.askopenfilename = lambda **k: ""

    def tearDown(self):
        (settings_ui.messagebox.showerror, settings_ui.messagebox.showinfo,
         settings_ui.messagebox.askyesno,
         settings_ui.filedialog.askopenfilename) = self._saved
        tk_support.destroy(self)
        config.set_app_dir(self._app_dir)
        import receipt_render
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)


class RecordListEditorBehaviour(DialogTestCase):
    COLUMNS = [("label", "Label", "text", {}),
               ("kind", "Kind", "choice", {"values": ["a", "b"]}),
               ("on", "On", "bool", {})]

    def editor(self, records=None):
        return settings_ui.RecordListEditor(
            self.root, "Rows", self.COLUMNS, records or [])

    def test_it_lists_the_records_it_is_given(self):
        editor = self.editor([{"label": "One"}, {"label": "Two"}])
        self.assertEqual(len(editor.tree.get_children()), 2)

    def test_removing_the_selected_row(self):
        editor = self.editor([{"label": "One"}, {"label": "Two"}])
        editor.tree.selection_set(editor.tree.get_children()[0])
        editor.remove()
        self.assertEqual([r["label"] for r in editor.records], ["Two"])

    def test_removing_with_nothing_selected_does_nothing(self):
        editor = self.editor([{"label": "One"}])
        editor.remove()
        self.assertEqual(len(editor.records), 1)

    def test_editing_without_a_selection_tells_the_user(self):
        editor = self.editor([{"label": "One"}])
        editor.edit()
        self.assertTrue(self.infos, "the user should be told to select a row")

    def test_adding_a_row_through_the_sub_dialog(self):
        editor = self.editor([])
        # _edit_record builds a Toplevel and waits; drive it without blocking.
        self.root.wait_window = lambda *a, **k: None
        editor.add()
        # With wait_window stubbed the sub-dialog never returns a value, so the
        # list is unchanged -- what matters is that building it did not raise.
        self.assertEqual(editor.records, [])

    def test_refresh_survives_a_record_missing_keys(self):
        editor = self.editor([{"label": "Only a label"}])
        editor.refresh()
        self.assertEqual(len(editor.tree.get_children()), 1)


class HistoryDialogBehaviour(DialogTestCase):
    ENTRY = {"inv_no": "INV-W1001", "date_str": "26 Aug 2026", "cust": "Ada",
             "phone": "", "email": "", "receipt_type": "Online", "shipping": "0",
             "items": [{"sku": "KB-87", "desc": "Keyboard", "qty": 1,
                        "price": "10.00", "discount": "0", "tax": "0"}]}

    def test_an_empty_history_says_so(self):
        dialog = settings_ui.HistoryDialog(self.root)
        self.assertIn("No receipts recorded", dialog.note.cget("text"))
        dialog.win.destroy()

    def test_recorded_receipts_are_listed(self):
        receipt_history.record(self.ENTRY, "", True)
        dialog = settings_ui.HistoryDialog(self.root)
        self.assertEqual(len(dialog.tree.get_children()), 1)
        dialog.win.destroy()

    def test_the_search_box_filters(self):
        receipt_history.record(self.ENTRY, "", True)
        receipt_history.record(dict(self.ENTRY, inv_no="INV-W1002", cust="Grace"), "", True)
        dialog = settings_ui.HistoryDialog(self.root)
        dialog.search.set("grace")
        dialog.refresh()
        self.assertEqual(len(dialog.tree.get_children()), 1)
        dialog.win.destroy()

    def test_a_search_matching_nothing_says_so(self):
        receipt_history.record(self.ENTRY, "", True)
        dialog = settings_ui.HistoryDialog(self.root)
        dialog.search.set("no such thing")
        dialog.refresh()
        self.assertIn("Nothing matches", dialog.note.cget("text"))
        dialog.win.destroy()

    def test_loading_hands_the_entry_to_the_callback(self):
        receipt_history.record(self.ENTRY, "", True)
        loaded = []
        dialog = settings_ui.HistoryDialog(self.root, on_load=loaded.append)
        dialog.tree.selection_set(dialog.tree.get_children()[0])
        dialog.load()
        self.assertEqual(loaded[0]["invoice_no"], "INV-W1001")

    def test_loading_without_a_selection_tells_the_user(self):
        receipt_history.record(self.ENTRY, "", True)
        dialog = settings_ui.HistoryDialog(self.root)
        dialog.load()
        self.assertTrue(self.infos)
        dialog.win.destroy()

    def test_opening_a_pdf_that_is_gone_explains_rather_than_failing(self):
        """The record outliving its PDF is the point; say so kindly."""
        receipt_history.record(self.ENTRY, os.path.join(self.dir, "gone.pdf"), True)
        dialog = settings_ui.HistoryDialog(self.root)
        dialog.tree.selection_set(dialog.tree.get_children()[0])
        dialog.open_pdf()
        self.assertTrue(any("no longer where it was saved" in m for m in self.infos))
        dialog.win.destroy()


class VoidingFromTheHistory(DialogTestCase):
    ENTRY = {"inv_no": "INV-W1001", "date_str": "26 Aug 2026", "cust": "Ada",
             "phone": "", "email": "", "receipt_type": "Online", "shipping": "0",
             "items": [{"sku": "KB-87", "desc": "Keyboard", "qty": 1,
                        "price": "10.00", "discount": "0", "tax": "0"}]}

    def test_voiding_without_a_selection_asks_for_one(self):
        receipt_history.record(self.ENTRY, "", True)
        dialog = settings_ui.HistoryDialog(self.root)
        dialog.void()
        self.assertTrue(self.infos)
        dialog.win.destroy()

    def test_confirming_marks_it_void(self):
        receipt_history.record(self.ENTRY, "", True)
        dialog = settings_ui.HistoryDialog(self.root)
        dialog.tree.selection_set(dialog.tree.get_children()[0])
        dialog.void()                            # askyesno is stubbed to Yes
        self.assertTrue(receipt_history.is_voided("INV-W1001"))
        dialog.win.destroy()

    def test_declining_leaves_it_alone(self):
        receipt_history.record(self.ENTRY, "", True)
        settings_ui.messagebox.askyesno = lambda *a, **k: False
        dialog = settings_ui.HistoryDialog(self.root)
        dialog.tree.selection_set(dialog.tree.get_children()[0])
        dialog.void()
        self.assertFalse(receipt_history.is_voided("INV-W1001"))
        dialog.win.destroy()

    def test_voiding_an_already_void_receipt_says_so(self):
        receipt_history.record(self.ENTRY, "", True)
        receipt_history.void("INV-W1001")
        dialog = settings_ui.HistoryDialog(self.root)
        dialog.tree.selection_set(dialog.tree.get_children()[0])
        dialog.void()
        self.assertTrue(any("already void" in m for m in self.infos))
        dialog.win.destroy()

    def test_the_list_refreshes_to_show_it(self):
        receipt_history.record(self.ENTRY, "", True)
        dialog = settings_ui.HistoryDialog(self.root)
        dialog.tree.selection_set(dialog.tree.get_children()[0])
        dialog.void()
        values = dialog.tree.item(dialog.tree.get_children()[0])["values"]
        self.assertIn("VOID", [str(v) for v in values])
        dialog.win.destroy()


class ProductsDialogBehaviour(DialogTestCase):
    def test_an_empty_catalogue_opens(self):
        dialog = settings_ui.ProductsDialog(self.root)
        self.assertEqual(dialog.editor.records, [])
        dialog.win.destroy()

    def test_saving_a_product(self):
        dialog = settings_ui.ProductsDialog(self.root)
        dialog.editor.records.append(
            {"sku": "KB-87", "name": "Keyboard", "list_price": "10.00",
             "stock_count": "5"})
        dialog.save()
        products = product_catalogue.load()["products"]
        self.assertEqual(products[0]["sku"], "KB-87")
        self.assertEqual(products[0]["stock_count"], 5, "stock is stored as a number")

    def test_blank_columns_are_not_stored(self):
        dialog = settings_ui.ProductsDialog(self.root)
        dialog.editor.records.append(
            {"sku": "KB-87", "name": "Keyboard", "barcode": "", "cost_price": ""})
        dialog.save()
        stored = product_catalogue.load()["products"][0]
        self.assertNotIn("barcode", stored)
        self.assertNotIn("cost_price", stored)

    def test_a_duplicate_sku_is_refused_and_the_window_stays_open(self):
        dialog = settings_ui.ProductsDialog(self.root)
        dialog.editor.records.extend([
            {"sku": "KB-87", "name": "One"}, {"sku": "KB-87", "name": "Two"}])
        dialog.save()
        self.assertTrue(any("duplicate SKU" in m for m in self.errors))
        self.assertTrue(dialog.win.winfo_exists())
        dialog.win.destroy()

    def test_variants_survive_an_edit_made_through_the_grid(self):
        """The grid cannot show variants, so saving must not drop them."""
        product_catalogue.save({config.SCHEMA_VERSION_KEY: 1, "products": [
            {"sku": "KB-87", "name": "Keyboard", "list_price": "10.00",
             "variants": [{"sku": "KB-87-BLU", "name": "Blue"}]}]})
        dialog = settings_ui.ProductsDialog(self.root)
        for record in dialog.editor.records:
            record["name"] = "Renamed Keyboard"
        dialog.save()
        stored = product_catalogue.load()["products"][0]
        self.assertEqual(stored["name"], "Renamed Keyboard")
        self.assertEqual(stored["variants"][0]["sku"], "KB-87-BLU")

    def test_serial_numbers_survive_too(self):
        product_catalogue.save({config.SCHEMA_VERSION_KEY: 1, "products": [
            {"sku": "KB-87", "name": "Keyboard", "serial_numbers": ["SN-1"]}]})
        dialog = settings_ui.ProductsDialog(self.root)
        dialog.save()
        self.assertEqual(product_catalogue.load()["products"][0]["serial_numbers"],
                         ["SN-1"])

    def test_the_saved_callback_fires(self):
        fired = []
        dialog = settings_ui.ProductsDialog(self.root, on_saved=lambda: fired.append(1))
        dialog.editor.records.append({"sku": "X", "name": "Thing"})
        dialog.save()
        self.assertEqual(fired, [1])


class ProductPickerBehaviour(DialogTestCase):
    def seed(self):
        product_catalogue.save({config.SCHEMA_VERSION_KEY: 1, "products": [
            {"sku": "KB-87", "barcode": "5012345678900", "name": "Mechanical Keyboard",
             "list_price": "8500.00",
             "variants": [{"sku": "KB-87-BLU", "barcode": "5012345678917",
                           "name": "Blue", "list_price": "8900.00"}]},
            {"sku": "MOU-1", "barcode": "5099999999999", "name": "Mouse",
             "list_price": "1500.00"}]})

    def test_an_empty_catalogue_points_at_the_products_editor(self):
        picker = settings_ui.ProductPicker(self.root)
        self.assertIn("Tools → Products", picker.note.cget("text"))
        picker.win.destroy()

    def test_products_and_variants_are_both_listed(self):
        self.seed()
        picker = settings_ui.ProductPicker(self.root)
        self.assertEqual(len(picker.tree.get_children()), 3)
        picker.win.destroy()

    def test_typing_filters_the_list(self):
        self.seed()
        picker = settings_ui.ProductPicker(self.root)
        picker.search.set("mouse")
        picker.refresh()
        self.assertEqual(len(picker.tree.get_children()), 1)
        picker.win.destroy()

    def test_scanning_an_exact_barcode_selects_it_immediately(self):
        """The whole point of a scan: no further clicks."""
        self.seed()
        picker = settings_ui.ProductPicker(self.root)
        picker.search.set("5012345678917")
        picker.refresh()
        picker.accept_scan()
        self.assertEqual(picker.chosen["sku"], "KB-87-BLU")

    def test_scanning_an_exact_sku_also_works(self):
        self.seed()
        picker = settings_ui.ProductPicker(self.root)
        picker.search.set("MOU-1")
        picker.refresh()
        picker.accept_scan()
        self.assertEqual(picker.chosen["sku"], "MOU-1")

    def test_a_search_narrowing_to_one_is_accepted_on_enter(self):
        self.seed()
        picker = settings_ui.ProductPicker(self.root)
        picker.search.set("mouse")
        picker.refresh()
        picker.accept_scan()
        self.assertEqual(picker.chosen["sku"], "MOU-1")

    def test_an_ambiguous_search_is_not_auto_accepted(self):
        self.seed()
        picker = settings_ui.ProductPicker(self.root)
        picker.search.set("mech")          # matches the product and its variant
        picker.refresh()
        picker.accept_scan()
        self.assertIsNone(picker.chosen)
        picker.win.destroy()

    def test_choosing_the_selected_row(self):
        self.seed()
        picker = settings_ui.ProductPicker(self.root)
        picker.tree.selection_set(picker.tree.get_children()[0])
        picker.choose()
        self.assertEqual(picker.chosen["sku"], "KB-87")

    def test_choosing_nothing_tells_the_user(self):
        self.seed()
        picker = settings_ui.ProductPicker(self.root)
        picker.choose()
        self.assertTrue(self.infos)
        self.assertIsNone(picker.chosen)
        picker.win.destroy()


class SigningKeysDialogBehaviour(DialogTestCase):
    def dialog(self):
        return settings_ui.SigningKeysDialog(self.root)

    def test_no_key_yet_is_stated_plainly(self):
        dialog = self.dialog()
        self.assertIn("No signing key yet", dialog.status.cget("text"))
        dialog.win.destroy()

    def test_creating_a_key(self):
        dialog = self.dialog()
        dialog.create()
        self.assertTrue(os.path.isfile(dialog.key_path))
        self.assertTrue(os.path.isfile(dialog.cert_path))
        self.assertIn("Signing as:", dialog.status.cget("text"))
        dialog.win.destroy()

    def test_the_expiry_is_shown(self):
        dialog = self.dialog()
        dialog.create()
        self.assertIn("Valid until", dialog.status.cget("text"))
        dialog.win.destroy()

    def test_replacing_a_key_remembers_the_previous_certificate(self):
        dialog = self.dialog()
        dialog.create()
        dialog.create()          # askyesno is stubbed to Yes
        self.assertIn("previous certificate", dialog.status.cget("text"))
        dialog.win.destroy()

    def test_declining_the_replace_prompt_leaves_the_key_alone(self):
        dialog = self.dialog()
        dialog.create()
        original = open(dialog.cert_path, "rb").read()
        settings_ui.messagebox.askyesno = lambda *a, **k: False
        dialog.create()
        self.assertEqual(open(dialog.cert_path, "rb").read(), original)
        dialog.win.destroy()

    def stub_certificate(self, days_left):
        """Report a certificate `days_left` from expiry, without waiting a year."""
        import receipt_signing, datetime
        real = receipt_signing.certificate_info

        def fake(path):
            info = real(path)
            if info is None:
                return None
            info = dict(info)
            info["days_left"] = days_left
            info["expired"] = days_left < 0
            info["not_after"] = (datetime.datetime.now()
                                 + datetime.timedelta(days=days_left))
            return info

        receipt_signing.certificate_info = fake
        self.addCleanup(setattr, receipt_signing, "certificate_info", real)

    def test_an_expired_certificate_is_called_out_in_red(self):
        """Receipts signed with it still verify, but new ones should not use it."""
        dialog = self.dialog()
        dialog.create()
        self.stub_certificate(days_left=-3)
        dialog.refresh()
        self.assertIn("EXPIRED on", dialog.status.cget("text"))
        self.assertEqual(str(dialog.status.cget("foreground")), "#b91c1c")
        dialog.win.destroy()

    def test_a_certificate_near_expiry_warns_before_it_lapses(self):
        dialog = self.dialog()
        dialog.create()
        self.stub_certificate(days_left=30)
        dialog.refresh()
        text = dialog.status.cget("text")
        self.assertIn("Expires", text)
        self.assertIn("30 days left", text)
        self.assertEqual(str(dialog.status.cget("foreground")), "#b45309")
        dialog.win.destroy()

    def test_a_certificate_well_short_of_expiry_stays_green(self):
        """The boundary is 60 days; 60 itself must not warn."""
        dialog = self.dialog()
        dialog.create()
        self.stub_certificate(days_left=60)
        dialog.refresh()
        self.assertIn("Valid until", dialog.status.cget("text"))
        self.assertEqual(str(dialog.status.cget("foreground")), "#166534")
        dialog.win.destroy()

    def test_a_broken_certificate_is_reported(self):
        dialog = self.dialog()
        dialog.create()
        with open(dialog.cert_path, "w", encoding="utf-8") as f:
            f.write("not a certificate")
        dialog.refresh()
        self.assertIn("could not be read", dialog.status.cget("text"))
        dialog.win.destroy()

    def test_cancelling_the_file_picker_imports_nothing(self):
        dialog = self.dialog()
        settings_ui.filedialog.askopenfilename = lambda **k: ""
        dialog.import_key()
        self.assertFalse(os.path.isfile(dialog.key_path))
        dialog.win.destroy()

    def test_importing_an_existing_key(self):
        source = os.path.join(self.dir, "source.pem")
        receipt_signing.generate_key_pair(source, os.path.join(self.dir, "source_cert.pem"),
                                          org_name="Acme")
        dialog = self.dialog()
        settings_ui.filedialog.askopenfilename = lambda **k: source
        dialog.import_key()
        self.assertTrue(os.path.isfile(dialog.key_path))
        self.assertTrue(any("not stored anywhere" in m for m in self.infos))
        dialog.win.destroy()

    def test_importing_something_that_is_not_a_key_is_explained(self):
        junk = os.path.join(self.dir, "notes.txt")
        with open(junk, "w", encoding="utf-8") as f:
            f.write("hello")
        dialog = self.dialog()
        settings_ui.filedialog.askopenfilename = lambda **k: junk
        dialog.import_key()
        self.assertTrue(self.errors)
        self.assertIn("not a private key", self.errors[0])
        dialog.win.destroy()

    def test_importing_a_certificate_by_mistake_is_explained(self):
        _key = os.path.join(self.dir, "s.pem")
        cert = os.path.join(self.dir, "s_cert.pem")
        receipt_signing.generate_key_pair(_key, cert, org_name="Acme")
        dialog = self.dialog()
        settings_ui.filedialog.askopenfilename = lambda **k: cert
        dialog.import_key()
        self.assertTrue(any("certificate, not a private key" in m for m in self.errors))
        dialog.win.destroy()


class PassphrasePrompt(DialogTestCase):
    """Used once, in memory, never stored — so it needs its own dialog."""

    def prompt(self, action):
        """Build the dialog, run `action` against it, return the result."""
        result = {}

        def drive(dialog):
            action(dialog)

        self.root.wait_window = lambda win: drive(win)
        result["value"] = settings_ui._ask_passphrase(self.root)
        return result["value"]

    def find_button(self, dialog, label):
        for frame in dialog.winfo_children():
            for child in frame.winfo_children():
                for widget in [child] + list(getattr(child, "winfo_children",
                                                     lambda: [])()):
                    try:
                        if str(widget.cget("text")) == label:
                            return widget
                    except tk.TclError:
                        pass
        return None

    def test_cancelling_returns_nothing(self):
        value = self.prompt(lambda dialog: self.find_button(dialog, "Cancel").invoke())
        self.assertIsNone(value)

    def test_confirming_returns_what_was_typed(self):
        def action(dialog):
            for frame in dialog.winfo_children():
                for child in frame.winfo_children():
                    if isinstance(child, type(tk.Entry(dialog))) or "entry" in str(child):
                        try:
                            child.insert(0, "s3cret")
                        except tk.TclError:
                            pass
            self.find_button(dialog, "OK").invoke()

        self.assertEqual(self.prompt(action), "s3cret")

    def test_the_input_is_masked(self):
        masked = {}

        def action(dialog):
            for frame in dialog.winfo_children():
                for child in frame.winfo_children():
                    try:
                        if child.cget("show"):
                            masked["yes"] = True
                    except tk.TclError:
                        pass
            self.find_button(dialog, "Cancel").invoke()

        self.prompt(action)
        self.assertTrue(masked.get("yes"), "a passphrase box must not show its text")


class BrowseButtons(DialogTestCase):
    """The Browse callback is what makes the logo.png.png problem unrepeatable."""

    def build_path_row(self, chosen):
        settings_ui.filedialog.askopenfilename = lambda **k: chosen
        frame = tk.Frame(self.root)
        variables = {}
        settings_ui.build_row(frame, 0, "company.logo_path", "Logo", "path",
                              {"filetypes": [("Images", "*.png")]}, "", variables)
        for child in frame.winfo_children():
            for widget in getattr(child, "winfo_children", lambda: [])():
                try:
                    if "Browse" in str(widget.cget("text")):
                        widget.invoke()
                except tk.TclError:
                    pass
        return variables["company.logo_path"][1].get()

    def test_a_file_inside_the_app_folder_is_stored_relatively(self):
        """Keeps a config portable between machines."""
        chosen = os.path.join(self.dir, "logo.png")
        self.assertEqual(self.build_path_row(chosen), "logo.png")

    def test_a_nested_file_keeps_forward_slashes(self):
        nested = os.path.join(self.dir, "assets", "logo.png")
        self.assertEqual(self.build_path_row(nested), "assets/logo.png")

    def test_a_file_outside_the_app_folder_stays_absolute(self):
        outside = os.path.join(tempfile.gettempdir(), "elsewhere", "logo.png")
        self.assertEqual(self.build_path_row(outside), outside)

    def test_cancelling_the_browse_leaves_the_value_alone(self):
        self.assertEqual(self.build_path_row(""), "")


class RecordSubDialog(DialogTestCase):
    COLUMNS = [("label", "Label", "text", {}), ("on", "On", "bool", {})]

    def editor(self, records=None):
        return settings_ui.RecordListEditor(self.root, "Rows", self.COLUMNS,
                                            records or [])

    def click(self, dialog, label):
        for frame in dialog.winfo_children():
            for child in frame.winfo_children():
                for widget in [child] + list(getattr(child, "winfo_children",
                                                     lambda: [])()):
                    try:
                        if str(widget.cget("text")) == label:
                            widget.invoke()
                            return True
                    except tk.TclError:
                        pass
        return False

    def test_adding_a_row_through_the_sub_dialog(self):
        editor = self.editor()
        self.root.wait_window = lambda win: self.click(win, "OK")
        editor.add()
        self.assertEqual(len(editor.records), 1)

    def test_cancelling_the_sub_dialog_adds_nothing(self):
        editor = self.editor()
        self.root.wait_window = lambda win: self.click(win, "Cancel")
        editor.add()
        self.assertEqual(editor.records, [])

    def test_editing_an_existing_row(self):
        editor = self.editor([{"label": "One", "on": False}])
        editor.tree.selection_set(editor.tree.get_children()[0])
        self.root.wait_window = lambda win: self.click(win, "OK")
        editor.edit()
        self.assertEqual(len(editor.records), 1)


class ProductsDialogConflicts(DialogTestCase):
    def test_a_concurrent_edit_offers_a_choice(self):
        import product_catalogue as pc
        pc.save({config.SCHEMA_VERSION_KEY: 1, "products": [{"sku": "A", "name": "A"}]})
        dialog = settings_ui.ProductsDialog(self.root)

        # Someone edits the file while the window is open.
        pc.save({config.SCHEMA_VERSION_KEY: 1, "products": [{"sku": "B", "name": "B"}]})
        os.utime(pc.catalogue_path(),
                 (config.file_mtime(pc.catalogue_path()) + 5,) * 2)

        settings_ui.messagebox.askyesno = lambda *a, **k: False    # decline
        dialog.save()
        self.assertEqual(pc.load()["products"][0]["sku"], "B",
                         "declining must leave the other edit intact")
        dialog.win.destroy()

    def test_accepting_the_overwrite_saves_anyway(self):
        import product_catalogue as pc
        pc.save({config.SCHEMA_VERSION_KEY: 1, "products": [{"sku": "A", "name": "A"}]})
        dialog = settings_ui.ProductsDialog(self.root)
        pc.save({config.SCHEMA_VERSION_KEY: 1, "products": [{"sku": "B", "name": "B"}]})
        os.utime(pc.catalogue_path(),
                 (config.file_mtime(pc.catalogue_path()) + 5,) * 2)

        settings_ui.messagebox.askyesno = lambda *a, **k: True     # overwrite
        dialog.save()
        self.assertEqual(pc.load()["products"][0]["sku"], "A")

    def test_a_non_numeric_stock_is_left_for_validate_to_refuse(self):
        dialog = settings_ui.ProductsDialog(self.root)
        dialog.editor.records.append(
            {"sku": "A", "name": "A", "stock_count": "many"})
        dialog.save()
        self.assertTrue(any("whole number" in m for m in self.errors))
        dialog.win.destroy()


class FieldsDialogConflicts(DialogTestCase):
    def test_a_concurrent_edit_offers_a_choice(self):
        dialog = settings_ui.FieldsDialog(self.root)
        fields = config.load_fields()
        config.save_fields(fields)
        os.utime(config.fields_file(),
                 (config.file_mtime(config.fields_file()) + 5,) * 2)

        settings_ui.messagebox.askyesno = lambda *a, **k: False
        dialog.save()
        self.assertTrue(dialog.win.winfo_exists(), "declining should keep the window")
        dialog.win.destroy()

    def test_the_saved_callback_fires(self):
        fired = []
        dialog = settings_ui.FieldsDialog(self.root, on_saved=lambda: fired.append(1))
        dialog.save()
        self.assertEqual(fired, [1])


class LineTotalIsReachableFromTheUI(DialogTestCase):
    """The point of the whole in-app-editing effort: no JSON editing required."""

    def test_it_is_listed_among_the_line_item_fields(self):
        dialog = settings_ui.FieldsDialog(self.root)
        keys = [r.get("key") for r in dialog.item_editor.records]
        self.assertIn("line_total", keys)
        dialog.win.destroy()

    def test_switching_it_on_reaches_disk(self):
        dialog = settings_ui.FieldsDialog(self.root)
        for record in dialog.item_editor.records:
            if record.get("key") == "line_total":
                record["enabled"] = True
        dialog.save()
        saved = {f["key"]: f for f in config.load_fields()["line_item_fields"]}
        self.assertTrue(saved["line_total"]["enabled"])

    def test_it_arrives_switched_off(self):
        dialog = settings_ui.FieldsDialog(self.root)
        line_total = next(r for r in dialog.item_editor.records
                          if r.get("key") == "line_total")
        self.assertFalse(line_total.get("enabled", True))
        dialog.win.destroy()


class SettingsDialogRemainder(DialogTestCase):
    def test_the_saved_callback_fires(self):
        fired = []
        dialog = settings_ui.SettingsDialog(self.root, on_saved=lambda: fired.append(1))
        dialog.save()
        self.assertEqual(fired, [1])

    def test_accepting_an_overwrite_on_conflict(self):
        dialog = settings_ui.SettingsDialog(self.root)
        config.update_app_settings({"company": {"name": "Edited Elsewhere"}})
        os.utime(config.APP_SETTINGS_FILE,
                 (config.file_mtime(config.APP_SETTINGS_FILE) + 5,) * 2)
        settings_ui.messagebox.askyesno = lambda *a, **k: True
        dialog.variables["company.phone"][1].set("555-0100")
        dialog.save()
        self.assertEqual(config.load_app_settings()["company"]["phone"], "555-0100")


class FormHelpers(DialogTestCase):
    """build_row / read_variables cover every widget kind the editors use."""

    def build(self, rows, values):
        frame = tk.Frame(self.root)
        variables = {}
        for index, (path, label, kind, options) in enumerate(rows):
            settings_ui.build_row(frame, index, path, label, kind, options,
                                  values.get(path), variables)
        return variables

    def test_text_round_trips(self):
        variables = self.build([("a.b", "A", "text", {})], {"a.b": "hello"})
        self.assertEqual(settings_ui.read_variables(variables), {"a": {"b": "hello"}})

    def test_bool_round_trips(self):
        variables = self.build([("a.b", "A", "bool", {})], {"a.b": True})
        self.assertEqual(settings_ui.read_variables(variables), {"a": {"b": True}})

    def test_choice_round_trips(self):
        variables = self.build(
            [("a.b", "A", "choice", {"values": ["x", "y"]})], {"a.b": "y"})
        self.assertEqual(settings_ui.read_variables(variables), {"a": {"b": "y"}})

    def test_multiline_round_trips(self):
        variables = self.build([("a.b", "A", "multiline", {})], {"a.b": "one\ntwo"})
        self.assertEqual(settings_ui.read_variables(variables), {"a": {"b": "one\ntwo"}})

    def test_int_is_read_back_as_a_number(self):
        variables = self.build([("a.b", "A", "int", {})], {"a.b": 7})
        self.assertEqual(settings_ui.read_variables(variables), {"a": {"b": 7}})

    def test_a_non_numeric_int_is_passed_through_for_validate_to_reject(self):
        """Inventing an error message here would be worse than validate's."""
        variables = self.build([("a.b", "A", "int", {})], {"a.b": 1})
        variables["a.b"][1].set("soon")
        self.assertEqual(settings_ui.read_variables(variables), {"a": {"b": "soon"}})

    def test_a_path_row_builds_with_a_browse_button(self):
        variables = self.build([("a.b", "A", "path", {})], {"a.b": "logo.png"})
        self.assertEqual(settings_ui.read_variables(variables), {"a": {"b": "logo.png"}})

    def test_help_text_is_rendered(self):
        frame = tk.Frame(self.root)
        variables = {}
        settings_ui.build_row(frame, 0, "a.b", "A", "text",
                              {"help": "explain this"}, "", variables)
        labels = [w.cget("text") for w in frame.winfo_children()
                  if isinstance(w, type(tk.Label(frame))) or hasattr(w, "cget")]
        self.assertTrue(any("explain this" in str(t) for t in labels))


if __name__ == "__main__":
    unittest.main()
