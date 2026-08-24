"""Stage 5 — custom line-item fields and the configurable warranty.

Covers PLAN-generalization.md §"Stage 5": columns generated from enabled field
definitions with type-driven alignment and formatting, a warranty option list
where an option containing '#' prompts for a positive whole number, and
validate() guarding duplicate, reserved and missing built-in keys.

Run: python -m unittest discover -s tests
"""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config             # noqa: E402
import receipt_render     # noqa: E402
import gate_env           # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


#: The rendered warranty note. Matching on the bare class name would also hit
#: the .item-warranty-text rule in styles.css, which is always present.
WARRANTY_SPAN = '<span class="item-warranty-text">'

ITEM = {"sku": "A1", "desc": "Thing", "serial": "SN1", "qty": 2,
        "price": "10.00", "discount": "0", "tax": "0", "warranty": ""}
MONEY = {"symbol": "$", "symbol_space": False, "decimals": 2, "position": "prefix",
         "group_style": "thousand", "negative_style": "minus",
         "group_line_amounts": True}


def render(fields, items=None, **kwargs):
    return receipt_render.render_receipt(
        {"invoice_no": "I", "date": "d", "customer_name": "c",
         "items": items if items is not None else [dict(ITEM)],
         "receipt_type": "Online", "shipping": 0},
        receipt_render.load_templates(), currency=MONEY, fields=fields, **kwargs)


def fields_with(*changes):
    """Default fields with per-key overrides applied, e.g. ('sku', {'enabled': False})."""
    fields = config.default_fields()
    for key, override in changes:
        for field in fields["line_item_fields"]:
            if field["key"] == key:
                field.update(override)
    return fields


class ColumnsComeFromConfig(unittest.TestCase):
    def test_defaults_render_the_familiar_columns(self):
        html = render(config.default_fields())
        for heading in ("SKU", "Item Description", "Serial Number", "Qty",
                        "Unit Price", "Amount"):
            self.assertIn(f"<th>{heading}</th>", html)

    def test_disabling_a_column_removes_it(self):
        html = render(fields_with(("sku", {"enabled": False})))
        self.assertNotIn("<th>SKU</th>", html)
        self.assertIn("<th>Item Description</th>", html)

    def test_disabling_a_column_removes_its_cells_too(self):
        """A header without its cells would shift every row out of alignment."""
        full = render(config.default_fields())
        trimmed = render(fields_with(("sku", {"enabled": False})))
        self.assertEqual(full.count("<th>"), trimmed.count("<th>") + 1)
        full_cells = full.split("<tbody>")[1].count("<td")
        trimmed_cells = trimmed.split("<tbody>")[1].count("<td")
        self.assertEqual(full_cells, trimmed_cells + 1)

    def test_renaming_a_label_changes_the_heading(self):
        html = render(fields_with(("desc", {"label": "Artikel"})))
        self.assertIn("<th>Artikel</th>", html)
        self.assertNotIn("<th>Item Description</th>", html)

    def test_reordering_fields_reorders_the_columns(self):
        fields = config.default_fields()
        items = fields["line_item_fields"]
        items.insert(0, items.pop([f["key"] for f in items].index("amount")))
        html = render(fields)
        headers = html.split("<thead>")[1].split("</thead>")[0]
        self.assertLess(headers.index("<th>Amount</th>"), headers.index("<th>SKU</th>"))

    def test_optional_column_hidden_when_unused(self):
        html = render(config.default_fields())
        self.assertNotIn("<th>Discount</th>", html)

    def test_optional_column_shown_when_used(self):
        html = render(config.default_fields(), [dict(ITEM, discount="5.00")])
        self.assertIn("<th>Discount</th>", html)

    def test_hidden_builtin_still_feeds_the_arithmetic(self):
        """qty/price/amount may be hidden, but the totals still come from them."""
        html = render(fields_with(("price", {"enabled": False}),
                                  ("qty", {"enabled": False})))
        self.assertNotIn("<th>Unit Price</th>", html)
        self.assertIn("$20.00", html, "2 x 10.00 must still reach the total")


class TypeDrivenPresentation(unittest.TestCase):
    def test_amount_columns_are_right_aligned(self):
        html = render(config.default_fields())
        body = html.split("<tbody>")[1]
        self.assertIn('<td class="num">$10.00</td>', body)

    def test_text_columns_have_no_alignment_class(self):
        body = render(config.default_fields()).split("<tbody>")[1]
        self.assertIn("<td>A1</td>", body)

    def test_a_custom_amount_column_is_formatted_as_money(self):
        fields = config.default_fields()
        fields["line_item_fields"].insert(-1, {
            "key": "handling", "label": "Handling", "type": "amount", "enabled": True})
        body = render(fields, [dict(ITEM, handling="2.5")]).split("<tbody>")[1]
        self.assertIn('<td class="num">$2.50</td>', body)

    def test_a_custom_text_column_renders_and_is_escaped(self):
        fields = config.default_fields()
        fields["line_item_fields"].insert(0, {
            "key": "bin", "label": "Bin", "type": "text", "enabled": True})
        html = render(fields, [dict(ITEM, bin="<A&1>")])
        self.assertIn("<th>Bin</th>", html)
        self.assertIn("&lt;A&amp;1&gt;", html)

    def test_a_boolean_column_prints_words_not_python(self):
        fields = config.default_fields()
        fields["line_item_fields"].insert(0, {
            "key": "gift", "label": "Gift", "type": "boolean", "enabled": True})
        body = render(fields, [dict(ITEM, gift=True)]).split("<tbody>")[1]
        self.assertIn("<td>Yes</td>", body)
        self.assertNotIn("True", body)

    def test_integer_column_is_right_aligned(self):
        body = render(config.default_fields()).split("<tbody>")[1]
        self.assertIn('<td class="num">2</td>', body)


class WarrantyOptions(unittest.TestCase):
    def test_hash_option_is_detected(self):
        self.assertTrue(config.warranty_option_needs_number("# Months Limited Warranty"))
        self.assertFalse(config.warranty_option_needs_number("No Warranty"))

    def test_number_is_substituted(self):
        self.assertEqual(
            config.fill_warranty_number("# Months Limited Warranty", 24),
            "24 Months Limited Warranty")

    def test_none_option_suppresses_the_note(self):
        html = render(config.default_fields(), [dict(ITEM, warranty="No Warranty")])
        self.assertNotIn(WARRANTY_SPAN, html)

    def test_a_real_warranty_is_printed_under_the_description(self):
        html = render(config.default_fields(),
                      [dict(ITEM, warranty="24 Months Limited Warranty")])
        self.assertIn('<span class="item-warranty-text">24 Months Limited Warranty</span>',
                      html)

    def test_a_custom_none_option_suppresses_the_note(self):
        """A shop wording it differently must not get a note on every line."""
        fields = config.default_fields()
        fields["warranty"]["none_option"] = "Keine Garantie"
        html = render(fields, [dict(ITEM, warranty="Keine Garantie")])
        self.assertNotIn(WARRANTY_SPAN, html)


class WarrantyResolution(unittest.TestCase):
    """The '#' prompt must reject exactly what the plan says it rejects."""

    def resolve(self, option, number):
        import main
        captured = {}
        original = main.messagebox.showerror
        try:
            main.messagebox.showerror = lambda *a, **k: captured.setdefault("shown", True)
            return main.ReceiptApp.resolve_warranty(option, number), captured
        finally:
            main.messagebox.showerror = original

    def test_accepts_a_positive_number(self):
        got, _ = self.resolve("# Months Limited Warranty", "12")
        self.assertEqual(got, "12 Months Limited Warranty")

    def test_rejects_zero(self):
        got, captured = self.resolve("# Months Limited Warranty", "0")
        self.assertIsNone(got)
        self.assertTrue(captured.get("shown"), "the user must be told why")

    def test_rejects_negative(self):
        self.assertIsNone(self.resolve("# Months Limited Warranty", "-5")[0])

    def test_rejects_non_numeric(self):
        self.assertIsNone(self.resolve("# Months Limited Warranty", "abc")[0])

    def test_rejects_blank(self):
        self.assertIsNone(self.resolve("# Months Limited Warranty", "   ")[0])

    def test_option_without_a_hash_needs_no_number(self):
        got, _ = self.resolve("7 Days Checking Warranty", "")
        self.assertEqual(got, "7 Days Checking Warranty")

    def test_hash_anywhere_in_the_option_works(self):
        got, _ = self.resolve("Warranty: # year(s)", "3")
        self.assertEqual(got, "Warranty: 3 year(s)")


class WarrantyRoundTrip(unittest.TestCase):
    """Re-opening a saved item must re-select the option it was built from."""

    OPTIONS = ["# Months Limited Warranty", "7 Days Checking Warranty", "No Warranty"]

    def match(self, text):
        import main
        return main.ReceiptApp.match_warranty_option(text, self.OPTIONS)

    def test_exact_option_matches(self):
        self.assertEqual(self.match("No Warranty"), ("No Warranty", ""))

    def test_filled_hash_option_matches_and_recovers_the_number(self):
        self.assertEqual(self.match("18 Months Limited Warranty"),
                         ("# Months Limited Warranty", "18"))

    def test_unknown_text_matches_nothing(self):
        self.assertEqual(self.match("Something else entirely"), ("", ""))

    def test_round_trip_through_resolve(self):
        import main
        for number in ("1", "12", "240"):
            text = main.ReceiptApp.resolve_warranty("# Months Limited Warranty", number)
            self.assertEqual(self.match(text), ("# Months Limited Warranty", number))


class FieldValidation(unittest.TestCase):
    def assert_rejects(self, key_fragment, fields):
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate_fields(fields, "fields.json")
        self.assertIn(key_fragment, ctx.exception.key or "")
        return ctx.exception

    def test_defaults_validate(self):
        config.validate_fields(config.default_fields(), "fields.json")

    def test_duplicate_key(self):
        fields = config.default_fields()
        fields["line_item_fields"].append(
            {"key": "sku", "label": "Another SKU", "type": "text"})
        err = self.assert_rejects("key", fields)
        self.assertIn("duplicate", err.message)

    def test_reserved_key(self):
        fields = config.default_fields()
        fields["line_item_fields"].append(
            {"key": "css_class", "label": "Nope", "type": "text"})
        err = self.assert_rejects("key", fields)
        self.assertIn("reserved", err.message)

    def test_removing_a_builtin_is_refused(self):
        fields = config.default_fields()
        fields["line_item_fields"] = [
            f for f in fields["line_item_fields"] if f["key"] != "qty"]
        err = self.assert_rejects("line_item_fields", fields)
        self.assertIn("cannot be removed", err.message)

    def test_hiding_a_builtin_is_allowed(self):
        config.validate_fields(fields_with(("qty", {"enabled": False})), "fields.json")

    def test_unknown_type(self):
        self.assert_rejects("type", fields_with(("sku", {"type": "colour"})))

    def test_missing_label(self):
        self.assert_rejects("label", fields_with(("sku", {"label": "  "})))

    def test_key_must_be_a_valid_placeholder_name(self):
        fields = config.default_fields()
        fields["line_item_fields"].append(
            {"key": "my key", "label": "X", "type": "text"})
        err = self.assert_rejects("key", fields)
        self.assertIn("placeholder", err.message)

    def test_select_needs_options(self):
        self.assert_rejects("options", fields_with(("sku", {"type": "select"})))

    def test_enabled_warranty_needs_options(self):
        fields = config.default_fields()
        fields["warranty"]["options"] = []
        self.assert_rejects("warranty.options", fields)

    def test_disabled_warranty_needs_no_options(self):
        fields = config.default_fields()
        fields["warranty"] = {"enabled": False, "options": []}
        config.validate_fields(fields, "fields.json")

    def test_option_with_two_hashes_is_refused(self):
        fields = config.default_fields()
        fields["warranty"]["options"] = ["# of # Months"]
        err = self.assert_rejects("warranty.options", fields)
        self.assertIn("at most one", err.message)

    def test_empty_field_list(self):
        self.assert_rejects("line_item_fields", {"line_item_fields": [],
                                                 "warranty": {"enabled": False}})


class ItemDialogBuildsFromFields(unittest.TestCase):
    """The dialog has to survive a warranty config that is not the default."""

    def open_dialog(self, fields):
        import tkinter as tk
        import main

        root = tk.Tk()
        root.withdraw()
        try:
            app = main.ReceiptApp(root)
            app.fields = fields
            # The dialog is modal; stop it blocking and capture that it built.
            root.wait_window = lambda *a, **k: None
            app.open_item_dialog()
            root.update_idletasks()
        finally:
            root.destroy()

    def test_default_warranty_config(self):
        self.open_dialog(config.default_fields())

    def test_warranty_disabled(self):
        fields = config.default_fields()
        fields["warranty"] = {"enabled": False, "options": [], "label": "Warranty"}
        self.open_dialog(fields)

    def test_custom_option_list(self):
        fields = config.default_fields()
        fields["warranty"]["options"] = ["# Year International Warranty", "As-Is"]
        self.open_dialog(fields)


if __name__ == "__main__":
    unittest.main()
