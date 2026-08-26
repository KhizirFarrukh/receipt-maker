"""H5 — the in-app settings and fields editors.

These edit the same files the app loads, so the tests care most about the two
ways an editor can do real damage: saving something the app would then refuse to
load, and quietly overwriting an edit made outside the app.

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

import config        # noqa: E402
import settings_ui   # noqa: E402


class PathHelpers(unittest.TestCase):
    def test_get_nested(self):
        self.assertEqual(settings_ui.get_path({"a": {"b": 1}}, "a.b"), 1)

    def test_get_missing_is_none(self):
        self.assertIsNone(settings_ui.get_path({"a": {}}, "a.b"))
        self.assertIsNone(settings_ui.get_path({}, "a.b.c"))

    def test_get_through_a_scalar_is_none(self):
        self.assertIsNone(settings_ui.get_path({"a": 5}, "a.b"))

    def test_set_creates_intermediates(self):
        data = {}
        settings_ui.set_path(data, "a.b.c", 7)
        self.assertEqual(data, {"a": {"b": {"c": 7}}})

    def test_set_preserves_siblings(self):
        data = {"a": {"x": 1}}
        settings_ui.set_path(data, "a.y", 2)
        self.assertEqual(data, {"a": {"x": 1, "y": 2}})


class TheFormMatchesTheConfig(unittest.TestCase):
    """A typo'd path in the declarative table would create a junk key on save."""

    def test_every_settings_path_exists_in_the_defaults(self):
        defaults = config.default_app_settings()
        for section, rows in settings_ui.SETTINGS_SECTIONS:
            for path, label, kind, _options in rows:
                self.assertIsNotNone(
                    settings_ui.get_path(defaults, path),
                    f"{section} → {label!r} points at {path!r}, which is not in "
                    f"the default settings")

    def test_every_list_setting_path_exists(self):
        defaults = config.default_app_settings()
        for section, path, _title, _columns in settings_ui.LIST_SETTINGS:
            self.assertIsNotNone(settings_ui.get_path(defaults, path), path)

    def test_choice_values_match_what_validate_accepts(self):
        """A dropdown offering a value validate() rejects is a trap."""
        expected = {
            "currency.position": config.SYMBOL_POSITIONS,
            "currency.group_style": config.GROUP_STYLES,
            "currency.negative_style": config.NEGATIVE_STYLES,
            "tax.mode": config.TAX_MODES,
        }
        for _section, rows in settings_ui.SETTINGS_SECTIONS:
            for path, _label, kind, options in rows:
                if kind == "choice" and path in expected:
                    self.assertEqual(list(options["values"]), list(expected[path]), path)

    def test_field_type_choices_are_the_closed_set(self):
        types = dict((c[0], c[3]) for c in settings_ui.FIELD_COLUMNS)["type"]
        self.assertEqual(list(types["values"]), list(config.FIELD_TYPES))


class EditorTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-ui-")
        shutil.copy(os.path.join(PROJ, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        self.root = tk.Tk()
        self.root.withdraw()
        self._errors = []
        self._original_error = settings_ui.messagebox.showerror
        settings_ui.messagebox.showerror = lambda title, msg, **k: self._errors.append(msg)

    def tearDown(self):
        settings_ui.messagebox.showerror = self._original_error
        self.root.destroy()
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def saved_settings(self):
        with open(os.path.join(self.dir, "appsettings.json"), encoding="utf-8") as f:
            return json.load(f)

    def saved_fields(self):
        with open(os.path.join(self.dir, "fields.json"), encoding="utf-8") as f:
            return json.load(f)


class SettingsEditor(EditorTestCase):
    def test_a_change_is_saved(self):
        dialog = settings_ui.SettingsDialog(self.root)
        dialog.variables["company.phone"][1].set("555-0199")
        dialog.save()
        self.assertEqual(self.saved_settings()["company"]["phone"], "555-0199")

    def test_unrelated_settings_survive(self):
        before = self.saved_settings()
        dialog = settings_ui.SettingsDialog(self.root)
        dialog.variables["company.phone"][1].set("555-0199")
        dialog.save()
        self.assertEqual(self.saved_settings()["currency"], before["currency"])

    def test_a_checkbox_round_trips(self):
        dialog = settings_ui.SettingsDialog(self.root)
        dialog.variables["signing.enabled"][1].set(False)
        dialog.save()
        self.assertFalse(self.saved_settings()["signing"]["enabled"])

    def test_an_integer_is_stored_as_a_number(self):
        dialog = settings_ui.SettingsDialog(self.root)
        dialog.variables["currency.decimals"][1].set("0")
        dialog.save()
        self.assertEqual(self.saved_settings()["currency"]["decimals"], 0)

    def test_an_invalid_value_is_refused_and_the_window_stays_open(self):
        before = self.saved_settings()
        dialog = settings_ui.SettingsDialog(self.root)
        dialog.variables["currency.decimals"][1].set("99")
        dialog.save()

        self.assertTrue(dialog.win.winfo_exists(), "the window must stay open to fix it")
        self.assertEqual(self.saved_settings(), before, "nothing may be written")
        self.assertTrue(self._errors, "the user must be told")
        self.assertIn("currency.decimals", self._errors[0])
        dialog.win.destroy()

    def test_a_non_numeric_integer_is_refused_by_validate(self):
        dialog = settings_ui.SettingsDialog(self.root)
        dialog.variables["render.timeout_ms"][1].set("soon")
        dialog.save()
        self.assertTrue(self._errors)
        self.assertIn("render.timeout_ms", self._errors[0])
        dialog.win.destroy()

    def test_editing_a_list_setting(self):
        dialog = settings_ui.SettingsDialog(self.root)
        dialog.lists["tax.rows"].records.append(
            {"label": "VAT 15%", "type": "percent", "value": "15",
             "applies_to": "subtotal_after_discount"})
        dialog.save()
        rows = self.saved_settings()["tax"]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "VAT 15%")

    def test_an_invalid_list_row_is_refused(self):
        dialog = settings_ui.SettingsDialog(self.root)
        dialog.lists["receipt_types"].records = [
            {"label": "A", "code": "W"}, {"label": "B", "code": "W"}]
        dialog.save()
        self.assertTrue(self._errors)
        self.assertIn("duplicate", self._errors[0])
        dialog.win.destroy()


class FieldsEditor(EditorTestCase):
    def test_adding_a_receipt_field(self):
        dialog = settings_ui.FieldsDialog(self.root)
        dialog.receipt_editor.records.append(
            {"key": "po_number", "label": "PO Number", "type": "text", "enabled": True})
        dialog.save()
        self.assertEqual([f["key"] for f in self.saved_fields()["receipt_fields"]],
                         ["po_number"])

    def test_editing_warranty_options(self):
        dialog = settings_ui.FieldsDialog(self.root)
        dialog.options_text.delete("1.0", tk.END)
        dialog.options_text.insert("1.0", "# Year International Warranty\nAs-Is\n")
        dialog.save()
        self.assertEqual(self.saved_fields()["warranty"]["options"],
                         ["# Year International Warranty", "As-Is"])

    def test_blank_option_lines_are_dropped(self):
        dialog = settings_ui.FieldsDialog(self.root)
        dialog.options_text.delete("1.0", tk.END)
        dialog.options_text.insert("1.0", "A\n\n   \nB\n")
        dialog.save()
        self.assertEqual(self.saved_fields()["warranty"]["options"], ["A", "B"])

    def test_removing_a_builtin_is_refused_with_an_explanation(self):
        dialog = settings_ui.FieldsDialog(self.root)
        dialog.item_editor.records = [
            r for r in dialog.item_editor.records if r["key"] != "qty"]
        dialog.save()
        self.assertTrue(self._errors)
        self.assertIn("cannot be removed", self._errors[0])
        self.assertTrue(dialog.win.winfo_exists())
        dialog.win.destroy()

    def test_a_duplicate_key_is_refused(self):
        dialog = settings_ui.FieldsDialog(self.root)
        dialog.receipt_editor.records.append(
            {"key": "sku", "label": "Clash", "type": "text", "enabled": True})
        dialog.save()
        self.assertTrue(self._errors)
        self.assertIn("duplicate", self._errors[0])
        dialog.win.destroy()

    def test_hiding_a_builtin_is_allowed(self):
        dialog = settings_ui.FieldsDialog(self.root)
        for record in dialog.item_editor.records:
            if record["key"] == "price":
                record["enabled"] = False
        dialog.save()
        saved = {f["key"]: f for f in self.saved_fields()["line_item_fields"]}
        self.assertFalse(saved["price"]["enabled"])

    def test_column_order_is_preserved(self):
        dialog = settings_ui.FieldsDialog(self.root)
        records = dialog.item_editor.records
        records.insert(0, records.pop())          # move the last column first
        expected = [r["key"] for r in records]
        dialog.save()
        self.assertEqual([f["key"] for f in self.saved_fields()["line_item_fields"]],
                         expected)


class ConcurrentEditIsDetected(EditorTestCase):
    """Someone editing the file by hand while the dialog is open must not lose it."""

    def test_settings_conflict_is_offered_not_silently_overwritten(self):
        dialog = settings_ui.SettingsDialog(self.root)

        path = os.path.join(self.dir, "appsettings.json")
        edited = self.saved_settings()
        edited["company"]["name"] = "Edited By Hand"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(edited, f)
        os.utime(path, (os.path.getmtime(path) + 5,) * 2)

        asked = []
        original = settings_ui.messagebox.askyesno
        try:
            settings_ui.messagebox.askyesno = lambda *a, **k: asked.append(1) or False
            dialog.variables["company.phone"][1].set("555-0199")
            dialog.save()
        finally:
            settings_ui.messagebox.askyesno = original

        self.assertTrue(asked, "the clash must be raised with the user")
        self.assertEqual(self.saved_settings()["company"]["name"], "Edited By Hand",
                         "declining must leave the hand edit intact")
        dialog.win.destroy()


if __name__ == "__main__":
    unittest.main()
