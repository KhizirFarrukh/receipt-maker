"""TODO.md section 6.9 -- shipping charged per group of lines.

An order leaving from two warehouses has two carrier costs. One fee against the
whole invoice cannot express that, so a line belongs to a shipment, each
shipment carries its own fee, and the receipt shows every fee and the combined
total.

Three things here are load-bearing and each has its own class:

* `GroupsInterleave` -- the case that shaped this was lines 1, 2, 4 against
  line 3, so a shipment is a tag on a line and *not* a range of rows.
* `TheSortIsStable` -- determinism is a tested invariant and the golden gate
  compares bytes, so an unstable sort would let one receipt render two ways and
  look like flakiness rather than a bug.
* `GroupingIsVisible` -- reordering lines while saying nothing would show the
  customer a re-sorted list and two unexplained charges.

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
import shipments           # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


def line(desc, shipment=""):
    return {"sku": desc, "desc": desc, "serial": "", "qty": 1, "price": "10",
            "discount": "0", "tax": "0", "warranty": "", "shipment": shipment}


class GroupsInterleave(unittest.TestCase):
    """Lines 1, 2, 4 in one shipment and line 3 in another -- the real case."""

    ITEMS = [line("1", "A"), line("2", "A"), line("3", "B"), line("4", "A")]

    def test_both_groups_are_found(self):
        self.assertEqual(shipments.groups_used(self.ITEMS), ["A", "B"])

    def test_they_are_gathered_despite_interleaving(self):
        ordered = [i["desc"] for i in shipments.order_items(self.ITEMS)]
        self.assertEqual(ordered, ["1", "2", "4", "3"])

    def test_a_tag_is_read_from_the_line_not_its_position(self):
        self.assertEqual(shipments.group_of(line("x", " A ")), "A")

    def test_an_untagged_line_has_no_group(self):
        self.assertEqual(shipments.group_of(line("x")), "")

    def test_groups_are_numbered_by_first_mention_not_by_sorting(self):
        """Otherwise "10" would come before "2" and the numbering would jump."""
        items = [line("1", "10"), line("2", "2")]
        self.assertEqual(shipments.groups_used(items), ["10", "2"])


class TheSortIsStable(unittest.TestCase):
    def test_entry_order_is_kept_within_a_group(self):
        items = [line("a", "X"), line("b", "X"), line("c", "X")]
        self.assertEqual([i["desc"] for i in shipments.order_items(items)],
                         ["a", "b", "c"])

    def test_sorting_twice_gives_the_same_answer(self):
        items = [line("1", "A"), line("2", "B"), line("3", "A"), line("4", "B")]
        once = shipments.order_items(items)
        twice = shipments.order_items(once)
        self.assertEqual([i["desc"] for i in once], [i["desc"] for i in twice])

    def test_ungrouped_lines_come_last_in_their_own_order(self):
        items = [line("loose1"), line("grouped", "A"), line("loose2")]
        self.assertEqual([i["desc"] for i in shipments.order_items(items)],
                         ["grouped", "loose1", "loose2"])

    def test_an_ungrouped_receipt_is_untouched(self):
        items = [line("a"), line("b"), line("c")]
        self.assertEqual([i["desc"] for i in shipments.order_items(items)],
                         ["a", "b", "c"])

    def test_no_items_is_survived(self):
        self.assertEqual(shipments.order_items([]), [])
        self.assertEqual(shipments.order_items(None), [])


class Fees(unittest.TestCase):
    DATA = {"shipments": [{"id": "A", "fee": "500"}, {"id": "B", "fee": "300"}]}
    ITEMS = [line("1", "A"), line("2", "B")]

    def test_each_shipment_gets_its_own_row(self):
        rows, _ = shipments.rows(self.DATA, self.ITEMS, 2)
        self.assertEqual([(t, f) for t, _, _, f in rows],
                         [("A", Decimal("500.00")), ("B", Decimal("300.00"))])

    def test_the_total_is_the_sum(self):
        _, total = shipments.rows(self.DATA, self.ITEMS, 2)
        self.assertEqual(total, Decimal("800.00"))

    def test_a_shipment_with_no_fee_set_counts_as_zero(self):
        rows, total = shipments.rows({"shipments": [{"id": "A", "fee": "500"}]},
                                     self.ITEMS, 2)
        self.assertEqual(total, Decimal("500.00"))
        self.assertEqual(len(rows), 2)

    def test_without_groups_the_flat_fee_is_used(self):
        """An existing receipt keeps the single shipping fee it always had."""
        rows, total = shipments.rows({}, [line("a"), line("b")], 2,
                                     flat_shipping="250")
        self.assertEqual(rows, [])
        self.assertEqual(total, Decimal("250.00"))

    def test_junk_in_the_fee_table_is_ignored_rather_than_crashing(self):
        data = {"shipments": ["not a dict", {"fee": "5"}, {"id": "A", "fee": "1"}]}
        rows, total = shipments.rows(data, [line("1", "A")], 2)
        self.assertEqual(total, Decimal("1.00"))


class Validation(unittest.TestCase):
    def test_a_sound_arrangement_passes(self):
        shipments.validate({"shipments": [{"id": "A", "fee": "5"}]},
                           [line("1", "A")])

    def test_a_fee_for_a_shipment_with_no_lines_is_refused(self):
        """It would be charged to nobody, and the shipping would not add up."""
        with self.assertRaises(shipments.ShipmentError) as ctx:
            shipments.validate({"shipments": [{"id": "Z", "fee": "5"}]},
                               [line("1", "A")])
        self.assertIn("no line is in that shipment", str(ctx.exception))

    def test_a_negative_fee_is_refused(self):
        with self.assertRaises(shipments.ShipmentError) as ctx:
            shipments.validate({"shipments": [{"id": "A", "fee": "-5"}]},
                               [line("1", "A")])
        self.assertIn("negative", str(ctx.exception))

    def test_an_absurd_number_of_shipments_is_refused(self):
        items = [line(str(n), f"S{n}") for n in range(shipments.MAX_SHIPMENTS + 1)]
        with self.assertRaises(shipments.ShipmentError):
            shipments.validate({}, items)

    def test_untagged_lines_with_no_fees_are_fine(self):
        shipments.validate({}, [line("a"), line("b")])


class GroupingIsVisible(unittest.TestCase):
    """A reorder with no marker tells the customer nothing."""

    def test_two_groups_are_numbered(self):
        self.assertEqual(shipments.marker(1, 2), "Shipment 1 of 2")
        self.assertEqual(shipments.marker(2, 2), "Shipment 2 of 2")

    def test_a_single_group_needs_no_marker(self):
        """One shipment is just "the order" -- labelling it adds noise."""
        self.assertEqual(shipments.marker(1, 1), "")

    def test_markers_are_built_for_every_tag(self):
        items = [line("1", "A"), line("2", "B")]
        self.assertEqual(shipments.markers_for(items),
                         {"A": "Shipment 1 of 2", "B": "Shipment 2 of 2"})

    def test_the_wording_is_configurable(self):
        """It prints on a customer's receipt, so it must be translatable."""
        self.assertEqual(shipments.marker(1, 3, "Parcel {n}/{total}"), "Parcel 1/3")


class OnTheReceipt(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-ship-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def render(self, items, fees=None, shipping=0):
        templates = receipt_render.load_templates()
        settings = config.load_app_settings()
        data = {"invoice_no": "INV-W1", "date": "1 Jan 2026",
                "customer_name": "Ada", "customer_phone": "", "customer_email": "",
                "items": items, "receipt_type": "Online", "shipping": shipping}
        if fees:
            data["shipments"] = fees
        return receipt_render.render_receipt(
            data, templates, strings=config.load_strings(),
            currency=settings.get("currency"), tax_config=settings.get("tax"),
            fields=config.load_fields())

    def body_order(self, html):
        """The row order, one entry per line.

        Both the SKU and the description carry the name, so each line matches
        twice; collapsing consecutive repeats gives one entry per row.
        """
        import re
        import itertools
        body = html.split("<tbody")[1].split("</tbody>")[0]
        found = re.findall(r"Item-(\d)", body)
        return [key for key, _ in itertools.groupby(found)]

    def test_lines_are_grouped_on_the_page(self):
        html = self.render([line("Item-1", "A"), line("Item-2", "B"),
                            line("Item-3", "A")])
        self.assertEqual(self.body_order(html), ["1", "3", "2"])

    def test_each_line_says_which_shipment_it_is_in(self):
        html = self.render([line("Item-1", "A"), line("Item-2", "B")])
        self.assertIn("Shipment 1 of 2", html)
        self.assertIn("Shipment 2 of 2", html)

    def test_each_shipment_shows_its_own_fee(self):
        html = self.render([line("Item-1", "A"), line("Item-2", "B")],
                           fees=[{"id": "A", "fee": "500"},
                                 {"id": "B", "fee": "300"}])
        self.assertIn("500.00", html)
        self.assertIn("300.00", html)

    def test_the_combined_shipping_is_shown_too(self):
        """A customer seeing two charges needs the total they add to."""
        html = self.render([line("Item-1", "A"), line("Item-2", "B")],
                           fees=[{"id": "A", "fee": "500"},
                                 {"id": "B", "fee": "300"}])
        self.assertIn("800.00", html)

    def test_one_shipment_prints_no_marker_and_no_split(self):
        html = self.render([line("Item-1", "A"), line("Item-2", "A")],
                           fees=[{"id": "A", "fee": "500"}])
        self.assertNotIn("Shipment 1 of 1", html)

    def test_an_ungrouped_receipt_is_completely_unchanged(self):
        """The flat shipping fee behaves exactly as it always has."""
        html = self.render([line("Item-1"), line("Item-2")], shipping=250)
        self.assertIn("250.00", html)
        self.assertNotIn("Shipment", html)

    def test_the_marker_and_a_warranty_share_the_line(self):
        item = line("Item-1", "A")
        item["warranty"] = "12 Months Limited Warranty"
        html = self.render([item, line("Item-2", "B")])
        self.assertIn("12 Months", html)
        self.assertIn("Shipment 1 of 2", html)

    def test_rendering_is_deterministic(self):
        """The invariant the stable sort exists to protect."""
        items = [line("Item-1", "B"), line("Item-2", "A"), line("Item-3", "B")]
        first = self.render(items)
        second = self.render(items)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
