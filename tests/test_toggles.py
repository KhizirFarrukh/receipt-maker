"""TODO.md section 6.6 -- everything optional can genuinely be turned off.

The claim was that line-item fields "already are" toggleable, since each
carries an `enabled` flag. That is only half of it: the flag existing is not the
same as the receipt still being right without the column. A disabled field that
leaves a gap, a stray label, or a totals line for something no longer shown is
worse than having no toggle at all, because it looks like a bug in the receipt
rather than a setting.

So this is the audit, one test per switch, checking the *rendered receipt* and
not the configuration.

Run: python -m unittest discover -s tests
"""
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config              # noqa: E402
import receipt_render      # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


class ToggleTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-toggle-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def disable(self, *keys):
        fields = config.load_fields()
        for field in fields["line_item_fields"]:
            if field["key"] in keys:
                field["enabled"] = False
        config.save_fields(fields)
        receipt_render.clear_template_cache()

    def label_of(self, key):
        for field in config.load_fields()["line_item_fields"]:
            if field["key"] == key:
                return field.get("label", key)
        return key

    def render(self, shipping=0, **item_extra):
        item = {"sku": "SKU-1", "desc": "Thing", "serial": "SER-1", "qty": 2,
                "price": "10.00", "discount": "1.00", "tax": "0.50",
                "warranty": ""}
        item.update(item_extra)
        return receipt_render.build_html(
            "INV-W1", "1 Jan 2026", "Ada", "", "", [item], "Online", shipping)

    def column_count(self, html):
        """How many header cells the item table has."""
        import re
        head = html.split("<thead")[1].split("</thead>")[0]
        return len(re.findall(r"<th", head))

    def row_cell_count(self, html):
        import re
        body = html.split("<tbody")[1].split("</tbody>")[0]
        first = body.split("</tr>")[0]
        return len(re.findall(r"<td", first))


class EveryColumnCanGo(ToggleTestCase):
    """Each optional column off, one at a time, with the table still square."""

    OPTIONAL = ("sku", "serial", "discount", "tax", "amount")

    def test_each_one_can_be_turned_off(self):
        for key in self.OPTIONAL:
            with self.subTest(field=key):
                self.setUp()
                try:
                    self.disable(key)
                    html = self.render()
                    self.assertNotIn(f">{self.label_of(key)}<", html,
                                     f"{key} was turned off but still has a header")
                finally:
                    self.tearDown()

    def test_the_table_stays_square(self):
        """A missing cell shifts every value after it into the wrong column."""
        for key in self.OPTIONAL:
            with self.subTest(field=key):
                self.setUp()
                try:
                    self.disable(key)
                    html = self.render()
                    self.assertEqual(self.column_count(html),
                                     self.row_cell_count(html),
                                     f"header and row disagree with {key} off")
                finally:
                    self.tearDown()

    def test_all_of_them_at_once(self):
        self.disable(*self.OPTIONAL)
        html = self.render()
        self.assertEqual(self.column_count(html), self.row_cell_count(html))
        self.assertIn("Thing", html, "the description must survive")

    def test_the_totals_still_add_up_with_columns_hidden(self):
        """Hiding a column changes what prints, never what is charged."""
        before = self.render()
        self.disable("discount", "tax")
        after = self.render()
        # 2 x 10.00 = 20.00, less 1.00 discount, plus 0.50 tax = 19.50.
        for html in (before, after):
            self.assertIn("19.50", html)


class HidingTaxAndDiscountLeavesNoTrace(ToggleTestCase):
    def test_no_stray_discount_label_remains(self):
        self.disable("discount")
        html = self.render()
        self.assertNotIn(f">{self.label_of('discount')}<", html)

    def test_the_totals_block_still_reports_them(self):
        """The column is presentation; the money was still taken off."""
        self.disable("discount", "tax")
        html = self.render()
        self.assertIn("Discounts", html)
        self.assertIn("Taxes", html)

    def test_a_line_with_no_discount_hides_the_column_on_its_own(self):
        """`optional_column` -- an unused Discount column stays off the page."""
        html = self.render(discount="0", tax="0")
        self.assertNotIn(f">{self.label_of('discount')}<", html)


class ShippingCanBeTurnedOff(ToggleTestCase):
    """The gap the audit was looking for: shipping was a label, not a switch."""

    def set_enabled(self, value):
        config.update_app_settings({"shipping": {"enabled": value}})
        receipt_render.clear_template_cache()

    def test_it_is_offered_by_default(self):
        self.assertTrue(
            config.default_app_settings()["shipping"]["enabled"])

    def test_a_fee_prints_while_it_is_on(self):
        html = self.render(shipping=25)
        self.assertIn("25.00", html)

    def test_turning_it_off_leaves_no_shipping_row(self):
        self.set_enabled(False)
        html = self.render(shipping=25)
        self.assertNotIn("Shipping", html)

    def test_turning_it_off_removes_it_from_the_total(self):
        """A hidden charge that is still charged is the worst outcome."""
        self.set_enabled(False)
        html = self.render(shipping=25)
        self.assertIn("19.50", html)
        self.assertNotIn("44.50", html)

    def test_it_must_be_a_boolean(self):
        settings = config.default_app_settings()
        settings["shipping"]["enabled"] = "yes"
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(settings, "appsettings.json")
        self.assertEqual(ctx.exception.key, "shipping.enabled")


class NewFieldsDefaultOff(unittest.TestCase):
    """Section 6.6's rule for everything section 6 added."""

    def test_the_ones_that_change_a_receipt_ship_disabled(self):
        fields = config.default_fields()
        by_key = {f["key"]: f for f in fields["line_item_fields"]}
        for key in ("barcode", "line_total", "unit_id", "shipment"):
            with self.subTest(field=key):
                self.assertFalse(by_key[key].get("enabled", True),
                                 f"{key} would change every existing receipt")

    def test_the_serial_stays_a_single_box_until_asked(self):
        by_key = {f["key"]: f
                  for f in config.default_fields()["line_item_fields"]}
        self.assertFalse(by_key["serial"].get("per_unit", False))

    def test_order_notes_ship_disabled(self):
        by_key = {f["key"]: f
                  for f in config.default_fields()["receipt_fields"]}
        self.assertFalse(by_key["notes"].get("enabled", True))

    def test_instalments_are_off(self):
        self.assertFalse(
            config.default_app_settings()["installments"]["enabled"])

    def test_no_payment_methods_are_configured(self):
        self.assertEqual(
            config.default_app_settings()["payment"]["methods"], [])

    def test_keeping_rows_whole_is_on_because_it_was_the_old_behaviour(self):
        """Not everything defaults off -- it defaults to what it already did."""
        self.assertTrue(
            config.default_app_settings()["render"]["keep_rows_whole"])


if __name__ == "__main__":
    unittest.main()
