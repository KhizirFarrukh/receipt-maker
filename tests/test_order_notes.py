"""TODO.md section 6.3 -- a paragraph about the order.

Delivery instructions, what was agreed on the phone, why the discount was
given: none of it fits in a line item, and there was nowhere on a receipt to
put it.

This needed the prerequisite TODO section 5 flagged all along: the item dialog
built itself from `fields.json` while the form above it was hardcoded, so a
receipt-level field could be *printed* but never *typed in*. That form is now
built from the configuration too, which is why the tests here cover more than
notes -- any receipt-level field works now.

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
import receipt_render      # noqa: E402

import gate_env            # noqa: E402
import tk_support          # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


class FormTestCase(unittest.TestCase):
    EXTRA = []

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-notes-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)

        fields = config.load_fields()
        for field in fields["receipt_fields"]:
            if field["key"] == "notes":
                field["enabled"] = True
        fields["receipt_fields"].extend(self.EXTRA)
        config.save_fields(fields)
        receipt_render.clear_template_cache()

        self.root = tk.Tk()
        self.root.withdraw()
        self.app = main.ReceiptApp(self.root)

    def tearDown(self):
        tk_support.destroy(self)
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)


class TheNotesBox(FormTestCase):
    def test_it_is_on_the_form(self):
        self.assertIn("notes", self.app.receipt_field_texts)

    def test_it_is_a_real_text_box_not_a_single_line(self):
        """A paragraph typed into an Entry would scroll sideways forever."""
        self.assertIsInstance(self.app.receipt_field_texts["notes"], tk.Text)

    def test_an_untouched_box_reads_as_empty(self):
        """Tk appends a newline of its own; it must not count as a value."""
        self.assertEqual(self.app.receipt_field_values()["notes"], "")

    def test_what_is_typed_comes_back(self):
        self.app.receipt_field_texts["notes"].insert("1.0", "Leave with neighbour")
        self.assertEqual(self.app.receipt_field_values()["notes"],
                         "Leave with neighbour")

    def test_several_lines_survive(self):
        text = "Line one\nLine two\nLine three"
        self.app.receipt_field_texts["notes"].insert("1.0", text)
        self.assertEqual(self.app.receipt_field_values()["notes"], text)

    def test_setting_values_fills_the_box(self):
        self.app.set_receipt_field_values({"notes": "Call before delivery"})
        self.assertEqual(self.app.receipt_field_values()["notes"],
                         "Call before delivery")

    def test_setting_replaces_rather_than_appending(self):
        self.app.set_receipt_field_values({"notes": "First"})
        self.app.set_receipt_field_values({"notes": "Second"})
        self.assertEqual(self.app.receipt_field_values()["notes"], "Second")


class OtherFieldTypesWork(FormTestCase):
    """The form is built from fields.json now, so any type has to work."""

    EXTRA = [
        {"key": "po_number", "label": "PO Number", "type": "text", "enabled": True},
        {"key": "channel", "label": "Channel", "type": "select",
         "enabled": True, "options": ["Web", "Phone"]},
        {"key": "gift", "label": "Gift", "type": "boolean", "enabled": True},
    ]

    def test_a_text_field_appears(self):
        self.assertIn("po_number", self.app.receipt_field_vars)

    def test_a_select_field_appears(self):
        self.assertIn("channel", self.app.receipt_field_vars)

    def test_a_boolean_field_appears(self):
        self.assertIn("gift", self.app.receipt_field_vars)

    def test_their_values_are_collected(self):
        self.app.receipt_field_vars["po_number"].set("PO-42")
        self.assertEqual(self.app.receipt_field_values()["po_number"], "PO-42")

    def test_a_disabled_field_is_not_built(self):
        fields = config.load_fields()
        fields["receipt_fields"].append(
            {"key": "hidden", "label": "Hidden", "type": "text", "enabled": False})
        config.save_fields(fields)
        app = main.ReceiptApp(tk.Toplevel(self.root))
        self.assertNotIn("hidden", app.receipt_field_vars)


class OnTheReceipt(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-notes-render-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def enable_notes(self, on=True):
        fields = config.load_fields()
        for field in fields["receipt_fields"]:
            if field["key"] == "notes":
                field["enabled"] = on
        config.save_fields(fields)
        receipt_render.clear_template_cache()

    def render(self, **extra):
        templates = receipt_render.load_templates()
        settings = config.load_app_settings()
        data = {"invoice_no": "INV-W1", "date": "1 Jan 2026",
                "customer_name": "Ada", "customer_phone": "", "customer_email": "",
                "items": [{"sku": "A", "desc": "Thing", "serial": "", "qty": 1,
                           "price": "10", "discount": "0", "tax": "0",
                           "warranty": ""}],
                "receipt_type": "Online", "shipping": 0}
        data.update(extra)
        return receipt_render.render_receipt(
            data, templates, strings=config.load_strings(),
            currency=settings.get("currency"), tax_config=settings.get("tax"),
            fields=config.load_fields())

    def test_it_prints_nothing_until_switched_on(self):
        html = self.render(notes="Leave with neighbour")
        self.assertNotIn("Leave with neighbour", html)

    def test_the_note_prints_when_switched_on(self):
        self.enable_notes()
        html = self.render(notes="Leave with neighbour")
        self.assertIn("Leave with neighbour", html)
        self.assertIn("Order Notes", html)

    def test_an_empty_note_leaves_no_stray_label(self):
        self.enable_notes()
        html = self.render(notes="")
        self.assertNotIn("Order Notes", html)

    def test_line_breaks_are_kept(self):
        self.enable_notes()
        html = self.render(notes="First line\nSecond line")
        self.assertIn("field-lines", html)
        self.assertIn("First line\nSecond line", html)

    def test_a_note_is_escaped_like_any_other_value(self):
        self.enable_notes()
        html = self.render(notes="<script>alert(1)</script>")
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_a_single_line_field_gets_no_paragraph_class(self):
        fields = config.load_fields()
        fields["receipt_fields"].append(
            {"key": "po", "label": "PO", "type": "text", "enabled": True})
        config.save_fields(fields)
        receipt_render.clear_template_cache()
        html = self.render(po="PO-42")
        self.assertIn("PO-42", html)
        self.assertNotIn('receipt-field field-lines">'
                         '<span class="field-label">PO', html)


class Migration(unittest.TestCase):
    def v5_fields(self):
        fields = config.default_fields()
        fields["receipt_fields"] = []
        fields[config.SCHEMA_VERSION_KEY] = 5
        return fields

    def test_an_existing_install_gains_the_notes_field(self):
        """`receipt_fields` is the user's own list, so only migration adds to it."""
        fields, changed = config.migrate_fields(self.v5_fields(), 5)
        self.assertTrue(changed)
        self.assertIn("notes", [f["key"] for f in fields["receipt_fields"]])

    def test_it_arrives_disabled(self):
        fields, _ = config.migrate_fields(self.v5_fields(), 5)
        notes = next(f for f in fields["receipt_fields"] if f["key"] == "notes")
        self.assertFalse(notes["enabled"])

    def test_a_shop_that_already_has_notes_keeps_its_own(self):
        fields = self.v5_fields()
        fields["receipt_fields"] = [
            {"key": "notes", "label": "Remarks", "type": "multiline",
             "enabled": True}]
        migrated, _ = config.migrate_fields(fields, 5)
        notes = [f for f in migrated["receipt_fields"] if f["key"] == "notes"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["label"], "Remarks")

    def test_a_migrated_file_validates(self):
        fields, _ = config.migrate_fields(self.v5_fields(), 5)
        config.validate_fields(fields, "fields.json")


if __name__ == "__main__":
    unittest.main()
