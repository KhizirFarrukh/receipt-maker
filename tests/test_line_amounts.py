"""What a line's discount and tax come to — per line, per unit, or a percentage.

A discount of 1,000 on a line of five is ambiguous, and the ambiguity is worth
4,000: either 1,000 off the line or 1,000 off each item. Both are things shops
do, so it is a setting.

`TheDefaultIsWhatItAlreadyDid` is the load-bearing class. A reissued receipt has
to reproduce the figures the customer was given, so `line` stays the default and
`unit` is opted into. Getting that backwards would silently change the money on
every receipt ever corrected.

Run: python -m unittest discover -s tests
"""
import os
import re
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config              # noqa: E402
import line_amounts        # noqa: E402
import receipt_render      # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


#: The example from the request: five at 10,000 with 1,000 off.
LINE = {"sku": "A", "desc": "Widget", "serial": "", "qty": 5,
        "price": "10000", "discount": "1000", "tax": "0", "warranty": ""}

PER_UNIT = {"discount_scope": "unit", "tax_scope": "unit"}


class ParsingWhatWasTyped(unittest.TestCase):
    def test_a_plain_amount(self):
        self.assertEqual(line_amounts.parse("1000"),
                         (line_amounts.AMOUNT, Decimal("1000")))

    def test_a_percentage(self):
        self.assertEqual(line_amounts.parse("10%"),
                         (line_amounts.PERCENT, Decimal("10")))

    def test_a_percentage_with_a_space(self):
        self.assertEqual(line_amounts.parse("10 %")[0], line_amounts.PERCENT)

    def test_a_decimal_percentage(self):
        self.assertEqual(line_amounts.parse("2.5%")[1], Decimal("2.5"))

    def test_junk_reads_as_zero_rather_than_raising(self):
        """A receipt has to render; a wrong zero is visible, a crash is not."""
        for junk in ("lots", "%", "", None, "abc%"):
            with self.subTest(value=junk):
                self.assertEqual(line_amounts.parse(junk)[1], Decimal("0"))


class TheDefaultIsWhatItAlreadyDid(unittest.TestCase):
    """`line` scope, because reissuing a receipt must reproduce its figures."""

    def test_the_shipped_default_is_per_line(self):
        settings = config.default_app_settings()
        self.assertEqual(settings["line_amounts"]["discount_scope"], "line")
        self.assertEqual(settings["line_amounts"]["tax_scope"], "line")

    def test_a_discount_is_taken_off_the_line_once(self):
        self.assertEqual(receipt_render.line_discount(LINE, 2),
                         Decimal("1000.00"))

    def test_the_total_matches_what_the_app_produced_before(self):
        self.assertEqual(receipt_render.line_total(LINE, 2), Decimal("49000.00"))

    def test_no_scopes_at_all_behaves_the_same(self):
        self.assertEqual(receipt_render.line_discount(LINE, 2, None),
                         receipt_render.line_discount(LINE, 2, {}))

    def test_an_unknown_scope_falls_back_rather_than_inventing_one(self):
        odd = {"discount_scope": "sideways"}
        self.assertEqual(receipt_render.line_discount(LINE, 2, odd),
                         Decimal("1000.00"))


class PerUnitMultipliesByTheQuantity(unittest.TestCase):
    def test_the_discount_is_taken_off_each_item(self):
        """1,000 off each of five is 5,000 -- the request's own example."""
        self.assertEqual(receipt_render.line_discount(LINE, 2, PER_UNIT),
                         Decimal("5000.00"))

    def test_the_total_reflects_it(self):
        self.assertEqual(receipt_render.line_total(LINE, 2, PER_UNIT),
                         Decimal("45000.00"))

    def test_tax_follows_the_same_rule(self):
        line = dict(LINE, discount="0", tax="200")
        self.assertEqual(receipt_render.line_tax(line, 2, PER_UNIT),
                         Decimal("1000.00"))

    def test_the_two_scopes_are_independent(self):
        """A shop can read discounts per unit and taxes per line."""
        mixed = {"discount_scope": "unit", "tax_scope": "line"}
        line = dict(LINE, tax="200")
        self.assertEqual(receipt_render.line_discount(line, 2, mixed),
                         Decimal("5000.00"))
        self.assertEqual(receipt_render.line_tax(line, 2, mixed),
                         Decimal("200.00"))

    def test_a_quantity_of_one_is_the_same_either_way(self):
        line = dict(LINE, qty=1)
        self.assertEqual(receipt_render.line_discount(line, 2, PER_UNIT),
                         receipt_render.line_discount(line, 2))

    def test_a_quantity_of_zero_discounts_nothing(self):
        line = dict(LINE, qty=0)
        self.assertEqual(receipt_render.line_discount(line, 2, PER_UNIT),
                         Decimal("0.00"))

    def test_the_per_unit_figure_is_rounded_before_multiplying(self):
        """So the line shows a whole number of pennies per item."""
        line = dict(LINE, qty=3, discount="0.005")
        self.assertEqual(receipt_render.line_discount(line, 2, PER_UNIT),
                         Decimal("0.03"))


class APercentageIgnoresTheScope(unittest.TestCase):
    """Ten percent of a line is one number, whichever way you read it."""

    def test_a_percentage_discount(self):
        line = dict(LINE, discount="10%")
        self.assertEqual(receipt_render.line_discount(line, 2), Decimal("5000.00"))

    def test_it_is_the_same_under_either_scope(self):
        line = dict(LINE, discount="10%")
        self.assertEqual(receipt_render.line_discount(line, 2),
                         receipt_render.line_discount(line, 2, PER_UNIT))

    def test_a_percentage_tax(self):
        line = dict(LINE, discount="0", tax="5%")
        self.assertEqual(receipt_render.line_tax(line, 2), Decimal("2500.00"))

    def test_it_is_a_percentage_of_the_line_not_of_one_item(self):
        line = dict(LINE, discount="0", tax="5%")
        one = dict(line, qty=1)
        self.assertEqual(receipt_render.line_tax(one, 2), Decimal("500.00"))

    def test_a_hundred_percent_discount_makes_the_line_free(self):
        line = dict(LINE, discount="100%")
        self.assertEqual(receipt_render.line_total(line, 2), Decimal("0.00"))


class TheFiguresStillAddUp(unittest.TestCase):
    """The invariant the whole money model rests on, under the new arithmetic."""

    ITEMS = [
        dict(LINE, qty=5, price="10000", discount="1000", tax="5%"),
        dict(LINE, qty=2, price="19.99", discount="10%", tax="1.50"),
        dict(LINE, qty=1, price="100", discount="0", tax="0"),
    ]

    def check(self, scopes):
        lines = sum((receipt_render.line_total(i, 2, scopes) for i in self.ITEMS),
                    Decimal("0"))
        gross = sum((receipt_render.quantize(receipt_render.line_gross(i), 2)
                     for i in self.ITEMS), Decimal("0"))
        tax = sum((receipt_render.line_tax(i, 2, scopes) for i in self.ITEMS),
                  Decimal("0"))
        discount = sum((receipt_render.line_discount(i, 2, scopes)
                        for i in self.ITEMS), Decimal("0"))
        self.assertEqual(lines, gross + tax - discount)

    def test_per_line(self):
        self.check(None)

    def test_per_unit(self):
        self.check(PER_UNIT)

    def test_mixed(self):
        self.check({"discount_scope": "unit", "tax_scope": "line"})


class OnTheReceipt(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-scope-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def show_columns(self, *keys):
        fields = config.load_fields()
        for field in fields["line_item_fields"]:
            if field["key"] in keys:
                field["enabled"] = True
        config.save_fields(fields)
        receipt_render.clear_template_cache()

    def render(self, item=None, **settings):
        if settings:
            config.update_app_settings(settings)
            receipt_render.clear_template_cache()
        return receipt_render.build_html(
            "INV-1", "1 Jan 2026", "Ada", "", "", [item or dict(LINE)],
            "Online", 0)

    def table(self, html):
        body = html.split("<body>", 1)[1]
        head = body.split("<thead")[1].split("</thead>")[0]
        row = body.split("<tbody")[1].split("</tbody>")[0]
        headers = re.findall(r"<th[^>]*>([^<]*)</th>", head)
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<td.*?</td>", row, re.S)]
        return dict(zip(headers, cells))

    def test_the_new_columns_are_off_until_asked_for(self):
        html = self.render()
        self.assertNotIn("Total Discount", html)
        self.assertNotIn("Total Tax", html)

    def test_the_resolved_discount_gets_its_own_column(self):
        self.show_columns("line_discount")
        cells = self.table(self.render(
            line_amounts={"discount_scope": "unit"}))
        self.assertEqual(cells["Discount"], "Rs. 1000.00", "what was typed")
        self.assertEqual(cells["Total Discount"], "Rs. 5000.00", "what it cost")

    def test_the_resolved_tax_gets_its_own_column(self):
        self.show_columns("line_tax")
        cells = self.table(self.render(dict(LINE, tax="5%")))
        self.assertEqual(cells["Total Tax"], "Rs. 2500.00")

    def test_a_percentage_prints_as_typed_in_its_own_column(self):
        """Formatting "5%" as money gives 0.00, which reads as no tax at all."""
        self.show_columns("line_tax")
        cells = self.table(self.render(dict(LINE, tax="5%")))
        self.assertEqual(cells["Tax"], "5%")

    def test_a_percentage_does_not_hide_its_own_column(self):
        """`optional_column` asked quantize(), which reads "5%" as zero."""
        html = self.render(dict(LINE, tax="5%"))
        self.assertIn("Tax", self.table(html))

    def test_the_final_price_column(self):
        self.show_columns("line_total")
        cells = self.table(self.render(
            dict(LINE, tax="5%"), line_amounts={"discount_scope": "unit"}))
        self.assertEqual(cells["Amount"], "Rs. 50000.00", "the subtotal")
        self.assertEqual(cells["Line Total"], "Rs. 47500.00", "the final price")

    def test_the_totals_block_agrees_with_the_columns(self):
        html = self.render(dict(LINE, tax="5%"),
                           line_amounts={"discount_scope": "unit"})
        body = html.split("<body>", 1)[1]
        totals = dict(re.findall(
            r"<td>([^<]+)</td>\s*<td align=\"right\">([^<]+)</td>", body))
        self.assertEqual(totals["Subtotal"], "Rs. 50,000.00")
        self.assertEqual(totals["Taxes"], "Rs. 2,500.00")
        self.assertEqual(totals["Discounts"], "- Rs. 5,000.00")
        self.assertEqual(totals["TOTAL"], "Rs. 47,500.00")


class TheBreakdownCanAlwaysShow(OnTheReceipt):
    PLAIN = dict(LINE, discount="0", tax="0")

    def test_it_is_hidden_by_default_on_a_plain_receipt(self):
        html = self.render(self.PLAIN)
        self.assertNotIn("Subtotal", html.split("<body>", 1)[1])

    def test_turning_it_on_prints_it(self):
        html = self.render(self.PLAIN, totals={"always_show_breakdown": True})
        self.assertIn("Subtotal", html.split("<body>", 1)[1])

    def test_the_default_is_off(self):
        self.assertFalse(
            config.default_app_settings()["totals"]["always_show_breakdown"])


class Validation(unittest.TestCase):
    def test_the_defaults_are_valid(self):
        config.validate(config.default_app_settings(), "appsettings.json")

    def test_an_unknown_scope_is_refused_with_an_explanation(self):
        settings = config.default_app_settings()
        settings["line_amounts"]["discount_scope"] = "sideways"
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(settings, "appsettings.json")
        self.assertEqual(ctx.exception.key, "line_amounts.discount_scope")
        self.assertIn("off each item", str(ctx.exception))

    def test_the_section_must_be_an_object(self):
        settings = config.default_app_settings()
        settings["line_amounts"] = "unit"
        with self.assertRaises(config.ConfigError):
            config.validate(settings, "appsettings.json")

    def test_the_breakdown_flag_must_be_a_boolean(self):
        settings = config.default_app_settings()
        settings["totals"]["always_show_breakdown"] = "yes"
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(settings, "appsettings.json")
        self.assertEqual(ctx.exception.key, "totals.always_show_breakdown")


class TheFormAcceptsAPercentage(unittest.TestCase):
    """Typing "10%" into a discount box must survive validation."""

    def clean(self, key, text):
        import tkinter as tk
        import main
        import tk_support

        self.root = tk.Tk()
        self.root.withdraw()
        try:
            app = main.ReceiptApp(self.root)
            self.app = app
            field = {"key": key, "label": key.title(), "type": "amount"}
            return app.clean_field_value(field, text)
        finally:
            tk_support.destroy(self)

    def test_a_percentage_discount_is_kept_as_typed(self):
        value, error = self.clean("discount", "10%")
        self.assertIsNone(error)
        self.assertEqual(value, "10%")

    def test_a_percentage_tax_is_kept_as_typed(self):
        value, error = self.clean("tax", "2.5%")
        self.assertIsNone(error)
        self.assertEqual(value, "2.5%")

    def test_a_plain_amount_still_becomes_a_money_string(self):
        value, error = self.clean("discount", "1000")
        self.assertIsNone(error)
        self.assertEqual(value, "1000.00")

    def test_a_discount_over_a_hundred_percent_is_refused(self):
        _, error = self.clean("discount", "150%")
        self.assertIn("more than the whole line", error)

    def test_a_negative_percentage_is_refused(self):
        _, error = self.clean("discount", "-5%")
        self.assertIn("cannot be negative", error)

    def test_nonsense_before_the_sign_is_refused(self):
        _, error = self.clean("tax", "lots%")
        self.assertIn("not a percentage", error)


class Migration(unittest.TestCase):
    def v6_fields(self):
        fields = config.default_fields()
        fields["line_item_fields"] = [
            f for f in fields["line_item_fields"]
            if f["key"] not in ("line_discount", "line_tax")]
        fields[config.SCHEMA_VERSION_KEY] = 6
        return fields

    def test_both_columns_are_added(self):
        fields, changed = config.migrate_fields(self.v6_fields(), 6)
        self.assertTrue(changed)
        keys = [f["key"] for f in fields["line_item_fields"]]
        self.assertIn("line_discount", keys)
        self.assertIn("line_tax", keys)

    def test_they_arrive_disabled(self):
        fields, _ = config.migrate_fields(self.v6_fields(), 6)
        for key in ("line_discount", "line_tax"):
            field = next(f for f in fields["line_item_fields"] if f["key"] == key)
            self.assertFalse(field["enabled"], f"{key} would change every receipt")

    def test_they_sit_before_the_line_total(self):
        fields, _ = config.migrate_fields(self.v6_fields(), 6)
        keys = [f["key"] for f in fields["line_item_fields"]]
        self.assertLess(keys.index("line_discount"), keys.index("line_total"))
        self.assertLess(keys.index("line_tax"), keys.index("line_total"))

    def test_migrating_twice_adds_one_of_each(self):
        fields, _ = config.migrate_fields(self.v6_fields(), 6)
        again, _ = config.migrate_fields(fields, 6)
        keys = [f["key"] for f in again["line_item_fields"]]
        self.assertEqual(keys.count("line_discount"), 1)
        self.assertEqual(keys.count("line_tax"), 1)

    def test_a_migrated_file_validates(self):
        fields, _ = config.migrate_fields(self.v6_fields(), 6)
        config.validate_fields(fields, "fields.json")


if __name__ == "__main__":
    unittest.main()
