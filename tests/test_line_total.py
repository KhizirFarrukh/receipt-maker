"""TODO.md section 6.4 -- the per-line total.

Until this landed, the `amount` column was `qty x price` and nothing else, so a
line carrying a discount or a tax printed a figure that was *not* what that line
came to; the adjustments appeared only in the totals block far below.

`line_total` is a second column rather than a redefinition of `amount`, because
changing what `amount` means would have changed the figure on every receipt
already being issued -- and no toggle covers that, since the column is already
on. The pair lets a shop choose: leave `amount` alone, or turn it off and show
the net instead.

The load-bearing test here is `SumsMatchTheTotalsBlock`. A column that disagrees
with the totals underneath it by a penny is worse than no column at all.

Run: python -m unittest discover -s tests
"""
import os
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal

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


def item(qty=1, price="0", discount="0", tax="0", **extra):
    line = {"sku": "A", "desc": "Thing", "serial": "", "qty": qty,
            "price": price, "discount": discount, "tax": tax, "warranty": ""}
    line.update(extra)
    return line


class TheArithmetic(unittest.TestCase):
    """`line_total` in isolation -- no rendering, no config."""

    def total(self, **kw):
        return receipt_render.line_total(item(**kw), 2)

    def test_a_plain_line_is_just_qty_times_price(self):
        self.assertEqual(self.total(qty=3, price="10.00"), Decimal("30.00"))

    def test_a_discount_comes_off(self):
        self.assertEqual(self.total(qty=1, price="10.00", discount="2.50"),
                         Decimal("7.50"))

    def test_tax_goes_on(self):
        self.assertEqual(self.total(qty=1, price="10.00", tax="1.60"),
                         Decimal("11.60"))

    def test_both_at_once(self):
        self.assertEqual(
            self.total(qty=2, price="10.00", discount="5.00", tax="1.50"),
            Decimal("16.50"))

    def test_a_discount_larger_than_the_line_goes_negative(self):
        """A refund line is legitimate; it must not be clamped to zero."""
        self.assertEqual(self.total(qty=1, price="10.00", discount="12.00"),
                         Decimal("-2.00"))

    def test_a_free_item_is_zero_not_blank(self):
        self.assertEqual(self.total(qty=1, price="0"), Decimal("0.00"))

    def test_shipping_on_the_line_is_ignored(self):
        """Shipping is charged per shipment (section 6.9), never per line."""
        self.assertEqual(self.total(qty=1, price="10.00", shipping="99.00"),
                         Decimal("10.00"))

    def test_junk_reads_as_zero_rather_than_raising(self):
        """Mirrors to_decimal everywhere else: a receipt must still render."""
        self.assertEqual(
            receipt_render.line_total(item(qty=1, price="ten"), 2),
            Decimal("0.00"))

    def test_each_part_is_rounded_before_they_are_added(self):
        """Not the same as rounding the sum, and the totals block does it this way.

        Gross 0.125 and tax 0.125 round to 0.13 each, giving 0.26. Rounding the
        sum (0.25) would give 0.25 -- a penny adrift from the figures below.
        """
        value = receipt_render.line_total(
            item(qty=1, price="0.125", tax="0.125"), 2)
        self.assertEqual(value, Decimal("0.26"))

    def test_precision_follows_the_currency(self):
        """A zero-decimal currency rounds to whole units."""
        self.assertEqual(
            receipt_render.line_total(item(qty=1, price="10.60"), 0),
            Decimal("11"))


class SumsMatchTheTotalsBlock(unittest.TestCase):
    """The property that makes the column trustworthy.

    The totals block keeps three separate running totals -- rounded gross,
    rounded taxes, rounded discounts. If the line totals do not add up to
    subtotal + taxes - discounts exactly, the receipt contradicts itself.
    """

    CASES = [
        [item(qty=1, price="10.00")],
        [item(qty=3, price="19.99", discount="5.00", tax="2.85")],
        [item(qty=1, price="0.125", tax="0.125"),
         item(qty=7, price="3.335", discount="0.005")],
        [item(qty=2, price="10.00", discount="25.00")],          # goes negative
        [item(qty=i, price=f"{i}.{i}{i}", discount=f"0.{i}", tax=f"0.0{i}")
         for i in range(1, 10)],
    ]

    def test_line_totals_sum_to_the_totals_block(self):
        for decimals in (0, 2, 3):
            for case in self.CASES:
                lines = sum((receipt_render.line_total(i, decimals) for i in case),
                            Decimal("0"))
                subtotal = sum((receipt_render.quantize(receipt_render.line_gross(i), decimals)
                                for i in case), Decimal("0"))
                taxes = sum((receipt_render.quantize(i["tax"], decimals) for i in case),
                            Decimal("0"))
                discounts = sum((receipt_render.quantize(i["discount"], decimals) for i in case),
                                Decimal("0"))
                self.assertEqual(
                    lines, subtotal + taxes - discounts,
                    f"line totals disagree with the totals block at {decimals}dp: {case}")


class TheColumnOnTheReceipt(unittest.TestCase):
    """Rendering, against a throwaway app dir so fields.json can be edited."""

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-linetotal-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def enable(self, key, on=True):
        fields = config.load_fields()
        for field in fields["line_item_fields"]:
            if field["key"] == key:
                field["enabled"] = on
        config.save_fields(fields)
        receipt_render.clear_template_cache()

    def render(self, items):
        return receipt_render.build_html(
            "INV-W1", "1 Jan 2026", "Ada", "", "", items, "Online", 0)

    def rows(self, html):
        """The item table's data cells, in order, stripped of markup."""
        import re
        body = html.split("<tbody")[1] if "<tbody" in html else html
        body = body.split("</tbody>")[0]
        return [re.sub(r"<[^>]+>", "", c).strip()
                for c in re.findall(r"<td.*?</td>", body, re.S)]

    def assertMoney(self, cells, figure, msg=None):
        """A money cell is prefixed with the currency, so match the figure.

        Asserting the bare number would fail against this fixture, whose symbol
        is the migrated "Rs." rather than the neutral default -- see PITFALLS.md.
        """
        self.assertTrue(any(c.endswith(figure) for c in cells),
                        msg or f"no cell ending in {figure}: {cells}")

    def assertNoMoney(self, cells, figure):
        self.assertFalse(any(c.endswith(figure) for c in cells),
                         f"unexpected cell ending in {figure}: {cells}")

    def test_it_is_off_until_asked_for(self):
        """No existing receipt changes shape when the app is upgraded."""
        html = self.render([item(qty=1, price="10.00", discount="2.00")])
        self.assertNotIn("Line Total", html)

    def test_enabling_it_prints_the_net(self):
        self.enable("line_total")
        html = self.render([item(qty=1, price="10.00", discount="2.00", tax="1.00")])
        self.assertIn("Line Total", html)
        self.assertMoney(self.rows(html), "9.00")

    def test_amount_keeps_its_old_meaning_alongside(self):
        """The whole reason this is a second column and not a redefinition."""
        self.enable("line_total")
        cells = self.rows(self.render(
            [item(qty=2, price="10.00", discount="5.00")]))
        self.assertMoney(cells, "20.00", "amount must still be the gross")
        self.assertMoney(cells, "15.00", "line total must be the net")

    def test_turning_amount_off_leaves_only_the_net(self):
        """A shop that wants one money column can have the net one."""
        self.enable("line_total")
        self.enable("amount", False)
        cells = self.rows(self.render([item(qty=2, price="10.00", discount="5.00")]))
        self.assertMoney(cells, "15.00")
        self.assertNoMoney(cells, "20.00")

    def test_a_zero_line_prints_a_figure_not_a_dash(self):
        """Unlike discount and tax, a zero total is a real answer: it was free."""
        self.enable("line_total")
        cells = self.rows(self.render([item(qty=1, price="0")]))
        self.assertMoney(cells, "0.00")

    def test_the_column_and_the_totals_agree_on_the_page(self):
        """End to end: what is printed per line adds up to what is printed below."""
        self.enable("line_total")
        html = self.render([
            item(qty=3, price="19.99", discount="5.00", tax="2.85"),
            item(qty=1, price="0.125", tax="0.125"),
        ])
        expected = sum((receipt_render.line_total(i, 2) for i in [
            item(qty=3, price="19.99", discount="5.00", tax="2.85"),
            item(qty=1, price="0.125", tax="0.125"),
        ]), Decimal("0"))
        # No shipping and no document tax, so the printed TOTAL is exactly this.
        self.assertIn(f"{expected:.2f}", html)


class Migration(unittest.TestCase):
    """An existing install must gain the field, disabled, without losing anything."""

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-linetotal-mig-")
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def v2_fields(self):
        fields = config.default_fields()
        fields["line_item_fields"] = [
            f for f in fields["line_item_fields"] if f["key"] != "line_total"]
        fields[config.SCHEMA_VERSION_KEY] = 2
        return fields

    def test_an_older_file_gains_it(self):
        fields, changed = config.migrate_fields(self.v2_fields(), 2)
        self.assertTrue(changed)
        keys = [f["key"] for f in fields["line_item_fields"]]
        self.assertIn("line_total", keys)

    def test_it_arrives_disabled(self):
        """Otherwise upgrading would silently add a column to every receipt."""
        fields, _ = config.migrate_fields(self.v2_fields(), 2)
        added = next(f for f in fields["line_item_fields"] if f["key"] == "line_total")
        self.assertFalse(added["enabled"])

    def test_it_sits_next_to_amount(self):
        fields, _ = config.migrate_fields(self.v2_fields(), 2)
        keys = [f["key"] for f in fields["line_item_fields"]]
        self.assertEqual(keys.index("line_total"), keys.index("amount") + 1)

    def test_the_version_stamp_moves(self):
        fields, _ = config.migrate_fields(self.v2_fields(), 2)
        self.assertEqual(fields[config.SCHEMA_VERSION_KEY], config.FIELDS_SCHEMA_VERSION)

    def test_a_field_deleted_after_migrating_stays_deleted(self):
        """The version stamp moves with the file, so v3 does not re-add it."""
        fields, _ = config.migrate_fields(self.v2_fields(), 2)
        fields["line_item_fields"] = [
            f for f in fields["line_item_fields"] if f["key"] != "line_total"]
        again, changed = config.migrate_fields(fields, config.FIELDS_SCHEMA_VERSION)
        self.assertFalse(changed)
        self.assertNotIn("line_total", [f["key"] for f in again["line_item_fields"]])

    def test_migrating_twice_adds_one_copy(self):
        fields, _ = config.migrate_fields(self.v2_fields(), 2)
        again, _ = config.migrate_fields(fields, 2)
        keys = [f["key"] for f in again["line_item_fields"]]
        self.assertEqual(keys.count("line_total"), 1)

    def test_the_barcode_migration_still_runs_from_v1(self):
        """A file two versions behind must collect both, not just the newest."""
        fields = self.v2_fields()
        fields["line_item_fields"] = [
            f for f in fields["line_item_fields"] if f["key"] != "barcode"]
        fields[config.SCHEMA_VERSION_KEY] = 1
        migrated, _ = config.migrate_fields(fields, 1)
        keys = [f["key"] for f in migrated["line_item_fields"]]
        self.assertIn("barcode", keys)
        self.assertIn("line_total", keys)

    def test_a_migrated_file_still_validates(self):
        fields, _ = config.migrate_fields(self.v2_fields(), 2)
        config.validate_fields(fields, "fields.json")


if __name__ == "__main__":
    unittest.main()
