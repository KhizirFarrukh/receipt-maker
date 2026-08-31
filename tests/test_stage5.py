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


class FieldValueCleaning(unittest.TestCase):
    """Entered values are validated by declared type, custom fields included."""

    def clean(self, field, raw):
        import main
        return main.ReceiptApp.clean_field_value(main.ReceiptApp, field, raw)

    def test_amount_is_normalised_to_two_places(self):
        value, error = self.clean({"key": "p", "label": "P", "type": "amount"}, "2.5")
        self.assertEqual((value, error), ("2.50", None))

    def test_amount_rejects_text(self):
        _, error = self.clean({"key": "p", "label": "Price", "type": "amount"}, "free")
        self.assertIn("Price", error)
        self.assertIn("number", error)

    def test_amount_rejects_negative(self):
        _, error = self.clean({"key": "p", "label": "Price", "type": "amount"}, "-1")
        self.assertIn("negative", error)

    def test_integer_rejects_a_decimal(self):
        _, error = self.clean({"key": "q", "label": "Qty", "type": "integer"}, "1.5")
        self.assertIn("whole number", error)

    def test_required_blank_is_refused(self):
        _, error = self.clean(
            {"key": "d", "label": "Description", "type": "text", "required": True}, "  ")
        self.assertIn("Description is required", error)

    def test_blank_qty_defaults_to_one(self):
        """Quantity is the one field where a blank has an obvious right answer."""
        value, error = self.clean({"key": "qty", "label": "Qty", "type": "integer"}, "")
        self.assertEqual((value, error), (1, None))

    def test_blank_amount_is_zero(self):
        value, error = self.clean({"key": "tax", "label": "Tax", "type": "amount"}, "")
        self.assertEqual((value, error), (0, None))

    def test_blank_optional_text_stays_blank(self):
        value, error = self.clean({"key": "sku", "label": "SKU", "type": "text"}, "")
        self.assertEqual((value, error), ("", None))

    def test_select_rejects_a_value_off_the_list(self):
        field = {"key": "c", "label": "Condition", "type": "select",
                 "options": ["New", "Used"]}
        _, error = self.clean(field, "Refurbished")
        self.assertIn("must be one of", error)
        self.assertEqual(self.clean(field, "Used"), ("Used", None))


class TreeRowRoundTrip(unittest.TestCase):
    """The tree stores rows positionally, so this mapping is load-bearing.

    If it drifts from the column ordering, values land in the wrong column --
    silently, and on a document that goes to a customer.
    """

    def build(self, fields):
        import tkinter as tk
        import main

        root = tk.Tk()
        root.withdraw()
        app = main.ReceiptApp(root)
        app.fields = fields
        app.input_fields = main.ReceiptApp._entry_fields(app)
        app.warranty_enabled = bool(fields.get("warranty", {}).get("options"))
        return app, root

    def test_round_trip_with_the_default_fields(self):
        app, root = self.build(config.default_fields())
        try:
            item = {"sku": "A", "desc": "D", "serial": "S", "qty": 2,
                    "price": "1.00", "discount": "0.00", "tax": "0.00",
                    "warranty": "No Warranty"}
            # The row comes back as text, because the tree stores text. A value
            # that arrived as a number must not reach the renderer as one --
            # Tk's own type guessing turns "007" into 7 and "10.00" into 10.0,
            # so everything read from a row is normalised to str.
            expected = {k: str(v) for k, v in item.items()}
            self.assertEqual(app.row_to_item(app.item_to_row(item)), expected)
        finally:
            root.destroy()

    def test_round_trip_with_a_custom_field(self):
        fields = config.default_fields()
        fields["line_item_fields"].insert(1, {
            "key": "bay", "label": "Bay", "type": "text", "enabled": True})
        app, root = self.build(fields)
        try:
            row = app.item_to_row({"sku": "A", "bay": "B-12", "desc": "D"})
            self.assertEqual(row[1], "B-12", "the custom column must sit where it was ordered")
            self.assertEqual(app.row_to_item(row)["bay"], "B-12")
        finally:
            root.destroy()

    def test_hidden_builtin_is_still_enterable(self):
        """Hiding Unit Price is a layout choice; the price still has to be typed."""
        fields = config.default_fields()
        for field in fields["line_item_fields"]:
            if field["key"] == "price":
                field["enabled"] = False
        app, root = self.build(fields)
        try:
            keys = [f["key"] for f in app.input_fields]
            self.assertIn("price", keys)
        finally:
            root.destroy()

    def test_hidden_custom_field_leaves_the_form(self):
        fields = config.default_fields()
        fields["line_item_fields"].append({
            "key": "bay", "label": "Bay", "type": "text", "enabled": False})
        app, root = self.build(fields)
        try:
            self.assertNotIn("bay", [f["key"] for f in app.input_fields])
        finally:
            root.destroy()

    def test_computed_fields_are_never_entered(self):
        app, root = self.build(config.default_fields())
        try:
            self.assertNotIn("amount", [f["key"] for f in app.input_fields],
                             "amount is qty x price; entering it would let them disagree")
        finally:
            root.destroy()


class CustomFieldReachesGeneration(unittest.TestCase):
    """End to end: a custom column typed into the tree arrives at the renderer."""

    def test_custom_value_is_collected_by_generate_pdf(self):
        import tkinter as tk
        import main

        fields = config.default_fields()
        fields["line_item_fields"].insert(1, {
            "key": "bay", "label": "Bay", "type": "text", "enabled": True})

        root = tk.Tk()
        root.withdraw()
        captured = {}
        try:
            app = main.ReceiptApp(root)
            app.fields = fields
            app.input_fields = main.ReceiptApp._entry_fields(app)
            app.warranty_enabled = True
            app._run_generation = lambda d, out, reserved=None: captured.update(data=d)
            app._claim_invoice_number = lambda typed: (typed, None)

            # Ask the app for its column order rather than rebuilding it here.
            # tree_keys() owns that ordering (ARCHITECTURE invariant 11), and a
            # hand-written copy silently drifts the moment a key is added.
            app.items_tree.configure(columns=app.tree_keys())
            app.items_tree.insert("", tk.END, values=app.item_to_row({
                "sku": "A1", "bay": "B-12", "desc": "Thing", "serial": "S",
                "qty": "2", "price": "10.00", "discount": "0", "tax": "0",
                "warranty": "No Warranty"}))
            app.cust_name.set("Ada")
            app.generate_pdf()
        finally:
            root.destroy()

        self.assertIn("data", captured, "generation was not reached")
        item = captured["data"]["items"][0]
        self.assertEqual(item["bay"], "B-12")
        self.assertEqual(item["qty"], 2, "numeric fields must arrive as numbers")
        self.assertEqual(item["price"], 10.0)


class CustomReceiptFields(unittest.TestCase):
    """Receipt-level extras: a PO number, a salesperson, a deposit."""

    def render_with(self, receipt_fields, **data):
        fields = config.default_fields()
        fields["receipt_fields"] = receipt_fields
        payload = {"invoice_no": "I", "date": "d", "customer_name": "Ada",
                   "items": [dict(ITEM)], "receipt_type": "Online", "shipping": 0}
        payload.update(data)
        return receipt_render.render_receipt(
            payload, receipt_render.load_templates(), currency=MONEY, fields=fields)

    def test_none_configured_renders_no_block(self):
        self.assertNotIn('class="receipt-fields"', self.render_with([]))

    def test_a_text_field_is_labelled_and_printed(self):
        html = self.render_with(
            [{"key": "po_number", "label": "PO Number", "type": "text", "enabled": True}],
            po_number="PO-4471")
        self.assertIn("PO Number", html)
        self.assertIn("PO-4471", html)

    def test_an_empty_field_leaves_no_stray_label(self):
        """An unfilled optional field must not print a dangling heading."""
        html = self.render_with(
            [{"key": "po_number", "label": "PO Number", "type": "text", "enabled": True}],
            po_number="")
        self.assertNotIn("PO Number", html)

    def test_an_amount_field_uses_the_currency(self):
        html = self.render_with(
            [{"key": "deposit", "label": "Deposit", "type": "amount", "enabled": True}],
            deposit="250")
        self.assertIn("$250.00", html)

    def test_values_are_escaped(self):
        html = self.render_with(
            [{"key": "note", "label": "Note", "type": "text", "enabled": True}],
            note="<b>&raw</b>")
        self.assertIn("&lt;b&gt;&amp;raw&lt;/b&gt;", html)
        self.assertNotIn("<b>&raw</b>", html)

    def test_a_disabled_field_is_skipped(self):
        html = self.render_with(
            [{"key": "po_number", "label": "PO Number", "type": "text", "enabled": False}],
            po_number="PO-4471")
        self.assertNotIn("PO-4471", html)

    def test_order_follows_the_configuration(self):
        html = self.render_with([
            {"key": "b_field", "label": "Second", "type": "text", "enabled": True},
            {"key": "a_field", "label": "First", "type": "text", "enabled": True},
        ], b_field="2", a_field="1")
        self.assertLess(html.index("Second"), html.index("First"))


class CrossListKeyCollisions(unittest.TestCase):
    """Both lists share one render context, so a shared key would be ambiguous."""

    def test_a_key_used_in_both_lists_is_refused(self):
        fields = config.default_fields()
        fields["receipt_fields"] = [
            {"key": "sku", "label": "Order SKU", "type": "text", "enabled": True}]
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate_fields(fields, "fields.json")
        self.assertIn("duplicate", ctx.exception.message)

    def test_receipt_fields_are_validated_too(self):
        fields = config.default_fields()
        fields["receipt_fields"] = [
            {"key": "po", "label": "PO", "type": "colour", "enabled": True}]
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate_fields(fields, "fields.json")
        self.assertIn("receipt_fields[0]", ctx.exception.key)

    def test_reserved_key_in_receipt_fields(self):
        fields = config.default_fields()
        fields["receipt_fields"] = [
            {"key": "totals", "label": "X", "type": "text", "enabled": True}]
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate_fields(fields, "fields.json")
        self.assertIn("reserved", ctx.exception.message)

    def test_receipt_fields_must_be_a_list(self):
        fields = config.default_fields()
        fields["receipt_fields"] = {"po": "nope"}
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate_fields(fields, "fields.json")
        self.assertEqual(ctx.exception.key, "receipt_fields")


class StickyValues(unittest.TestCase):
    """Remembered between items; state, not configuration."""

    def setUp(self):
        import shutil
        import tempfile
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-sticky-")
        self._cleanup = lambda: shutil.rmtree(self.dir, ignore_errors=True)
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        self._cleanup()

    def test_state_round_trips(self):
        config.save_state({"sticky_line_item": {"serial": "SN-9"}})
        self.assertEqual(config.load_state()["sticky_line_item"]["serial"], "SN-9")

    def test_missing_state_is_empty_not_an_error(self):
        self.assertEqual(config.load_state(), {})

    def test_corrupt_state_is_ignored_rather_than_fatal(self):
        """Losing remembered values must never stop a receipt being issued."""
        with open(config.state_file(), "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertEqual(config.load_state(), {})

    def test_only_fields_still_marked_sticky_are_returned(self):
        import tkinter as tk
        import main

        fields = config.default_fields()
        for field in fields["line_item_fields"]:
            if field["key"] == "serial":
                field["sticky"] = True
        config.save_state({"sticky_line_item": {"serial": "SN-9", "sku": "OLD"}})

        root = tk.Tk()
        root.withdraw()
        try:
            app = main.ReceiptApp(root)
            app.fields = fields
            app.input_fields = main.ReceiptApp._entry_fields(app)
            remembered = app.sticky_values()
        finally:
            root.destroy()

        self.assertEqual(remembered, {"serial": "SN-9"},
                         "un-marking a field must stop it pre-filling immediately")

    def test_sticky_is_validated_as_a_flag(self):
        fields = config.default_fields()
        fields["line_item_fields"][0]["sticky"] = "yes"
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate_fields(fields, "fields.json")
        self.assertIn("sticky", ctx.exception.key)


class ProductBarcode(unittest.TestCase):
    """A product barcode is not a serial number, and gets its own field.

    Serial number identifies one physical unit; a barcode identifies the
    product, so every unit of it carries the same code. Conflating them loses
    the ability to look a product up.
    """

    def setUp(self):
        import shutil
        import tempfile
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-barcode-")
        shutil.copy(os.path.join(PROJ, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        self._cleanup = lambda: shutil.rmtree(self.dir, ignore_errors=True)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        self._cleanup()

    def write_fields(self, fields):
        import json
        with open(os.path.join(self.dir, "fields.json"), "w", encoding="utf-8") as f:
            json.dump(fields, f, indent=2)

    def test_shipped_disabled_so_existing_receipts_do_not_change(self):
        barcode = next(f for f in config.default_fields()["line_item_fields"]
                       if f["key"] == "barcode")
        self.assertFalse(barcode["enabled"])

    def test_it_is_separate_from_the_serial_number(self):
        keys = [f["key"] for f in config.default_fields()["line_item_fields"]]
        self.assertIn("barcode", keys)
        self.assertIn("serial", keys)

    def test_an_older_fields_file_gains_it(self):
        old = config.default_fields()
        old[config.SCHEMA_VERSION_KEY] = 1
        old["line_item_fields"] = [f for f in old["line_item_fields"]
                                   if f["key"] != "barcode"]
        self.write_fields(old)

        fields = config.load_fields()
        keys = [f["key"] for f in fields["line_item_fields"]]
        self.assertIn("barcode", keys)
        self.assertEqual(keys.index("barcode"), keys.index("sku") + 1,
                         "a product code belongs next to the SKU")

    def test_the_migration_is_persisted(self):
        import json
        old = config.default_fields()
        old[config.SCHEMA_VERSION_KEY] = 1
        old["line_item_fields"] = [f for f in old["line_item_fields"]
                                   if f["key"] != "barcode"]
        self.write_fields(old)
        config.load_fields()
        with open(os.path.join(self.dir, "fields.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)[config.SCHEMA_VERSION_KEY],
                             config.FIELDS_SCHEMA_VERSION)

    def test_a_field_removed_after_the_migration_stays_removed(self):
        """The version stamp moves with the file, so this is not re-added forever."""
        current = config.default_fields()
        current["line_item_fields"] = [f for f in current["line_item_fields"]
                                       if f["key"] != "barcode"]
        self.write_fields(current)
        fields = config.load_fields()
        self.assertNotIn("barcode", [f["key"] for f in fields["line_item_fields"]])

    def test_enabling_it_puts_the_column_on_the_receipt(self):
        html = render(fields_with(("barcode", {"enabled": True})),
                      [dict(ITEM, barcode="5012345678900")])
        self.assertIn("<th>Barcode</th>", html)
        self.assertIn("5012345678900", html)

    def test_disabled_by_default_means_no_column(self):
        self.assertNotIn("<th>Barcode</th>", render(config.default_fields()))


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
