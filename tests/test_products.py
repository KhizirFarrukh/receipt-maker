"""The product catalogue: sell a known product instead of retyping it.

Covers the model (variants, lookup, line-item conversion), the pricing
arithmetic, and the validation that stops a catalogue you could not sell from.

**Margin and markup get particular attention.** They are not the same thing and
confusing them is a common, expensive pricing error, so the tests pin the actual
numbers rather than trusting the formulas to be self-evident.

Run: python -m unittest discover -s tests
"""
import json
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
import product_catalogue as pc   # noqa: E402

KEYBOARD = {
    "sku": "KB-87", "barcode": "5012345678900", "name": "Mechanical Keyboard",
    "list_price": "8500.00", "cost_price": "6000.00", "bulk_price": "7500.00",
    "stock_count": 12, "serial_numbers": ["SN-1", "SN-2"],
    "variants": [
        {"sku": "KB-87-BLU", "barcode": "5012345678917", "name": "Blue",
         "list_price": "8900.00"},
    ],
}
MOUSE = {"sku": "MOU-1", "barcode": "5099999999999", "name": "Mouse",
         "list_price": "1500.00"}


def catalogue(*products):
    return {config.SCHEMA_VERSION_KEY: 1, "products": [dict(p) for p in products]}


class Pricing(unittest.TestCase):
    """Markup adds to cost; margin is a share of the sale price."""

    def test_markup_adds_to_cost(self):
        self.assertEqual(pc.price_from_markup(100, 25), Decimal("125.00"))

    def test_margin_is_a_share_of_the_sale_price(self):
        self.assertEqual(round(pc.price_from_margin(100, 25), 2), Decimal("133.33"))

    def test_markup_and_margin_differ_at_the_same_percentage(self):
        """The whole reason they are separate functions."""
        self.assertNotEqual(pc.price_from_markup(100, 25),
                            round(pc.price_from_margin(100, 25), 2))

    def test_discount_comes_off_the_list_price(self):
        self.assertEqual(pc.price_from_discount(200, 10), Decimal("180.0"))

    def test_zero_percent_changes_nothing(self):
        self.assertEqual(pc.price_from_markup(100, 0), Decimal("100"))
        self.assertEqual(pc.price_from_margin(100, 0), Decimal("100"))
        self.assertEqual(pc.price_from_discount(100, 0), Decimal("100"))

    def test_margin_of_100_percent_is_refused_not_a_crash(self):
        with self.assertRaises(ValueError) as ctx:
            pc.price_from_margin(100, 100)
        self.assertIn("not a real price", str(ctx.exception))

    def test_margin_over_100_percent_is_refused(self):
        with self.assertRaises(ValueError):
            pc.price_from_margin(100, 150)

    def test_reporting_margin_and_markup_of_a_price(self):
        self.assertEqual(round(pc.margin_of(100, 125), 1), Decimal("20.0"))
        self.assertEqual(round(pc.markup_of(100, 125), 1), Decimal("25.0"))

    def test_a_25_percent_margin_price_reports_back_as_25_percent(self):
        price = pc.price_from_margin(100, 25)
        self.assertEqual(round(pc.margin_of(100, price)), Decimal("25"))

    def test_zero_divisors_do_not_crash(self):
        self.assertEqual(pc.margin_of(100, 0), Decimal("0"))
        self.assertEqual(pc.markup_of(0, 100), Decimal("0"))

    def test_amounts_stay_decimal(self):
        self.assertIsInstance(pc.price_from_markup("10.005", 0), Decimal)


class Variants(unittest.TestCase):
    def setUp(self):
        self.cat = catalogue(KEYBOARD)

    def test_a_variant_inherits_what_it_does_not_state(self):
        blue = pc.find(self.cat, "KB-87-BLU")
        self.assertEqual(blue["cost_price"], "6000.00", "inherited from the parent")

    def test_a_variant_overrides_what_it_does_state(self):
        self.assertEqual(pc.find(self.cat, "KB-87-BLU")["list_price"], "8900.00")

    def test_a_variant_name_is_a_label_not_a_replacement(self):
        """Otherwise the receipt reads "Blue (Blue)" and loses the product."""
        blue = pc.find(self.cat, "KB-87-BLU")
        self.assertEqual(blue["name"], "Mechanical Keyboard")
        self.assertEqual(blue["variant_name"], "Blue")

    def test_the_parent_is_still_sellable(self):
        skus = [i["sku"] for i in pc.sellable_items(self.cat)]
        self.assertIn("KB-87", skus)
        self.assertIn("KB-87-BLU", skus)

    def test_effective_does_not_mutate_the_stored_product(self):
        pc.effective(self.cat["products"][0], self.cat["products"][0]["variants"][0])
        self.assertEqual(self.cat["products"][0]["list_price"], "8500.00")

    def test_variants_are_not_listed_as_a_nested_field(self):
        self.assertNotIn("variants", pc.effective(self.cat["products"][0]))


class Lookup(unittest.TestCase):
    def setUp(self):
        self.cat = catalogue(KEYBOARD, MOUSE)

    def test_by_sku(self):
        self.assertEqual(pc.find(self.cat, "MOU-1")["name"], "Mouse")

    def test_by_barcode(self):
        self.assertEqual(pc.find(self.cat, "5012345678900")["sku"], "KB-87")

    def test_a_variant_barcode_finds_the_variant(self):
        self.assertEqual(pc.find(self.cat, "5012345678917")["sku"], "KB-87-BLU")

    def test_lookup_is_case_insensitive(self):
        self.assertEqual(pc.find(self.cat, "kb-87")["sku"], "KB-87")

    def test_an_unknown_code_finds_nothing(self):
        self.assertIsNone(pc.find(self.cat, "0000000000"))
        self.assertIsNone(pc.find(self.cat, ""))

    def test_search_matches_names(self):
        self.assertEqual([i["sku"] for i in pc.search(self.cat, "mouse")], ["MOU-1"])

    def test_searching_a_product_name_finds_its_variants_too(self):
        """Variants inherit the name, so 'mech' should offer every colour of it."""
        self.assertEqual([i["sku"] for i in pc.search(self.cat, "mech")],
                         ["KB-87", "KB-87-BLU"])

    def test_search_matches_the_variant_label(self):
        self.assertIn("KB-87-BLU", [i["sku"] for i in pc.search(self.cat, "blue")])

    def test_an_empty_search_returns_everything(self):
        self.assertEqual(len(pc.search(self.cat, "")), 3)


class LineItemConversion(unittest.TestCase):
    def setUp(self):
        self.cat = catalogue(KEYBOARD, MOUSE)

    def test_a_product_becomes_a_line(self):
        line = pc.to_line_item(pc.find(self.cat, "MOU-1"))
        self.assertEqual(line["sku"], "MOU-1")
        self.assertEqual(line["desc"], "Mouse")
        self.assertEqual(line["price"], "1500.00")
        self.assertEqual(line["qty"], 1)

    def test_the_barcode_travels_with_it(self):
        self.assertEqual(pc.to_line_item(pc.find(self.cat, "MOU-1"))["barcode"],
                         "5099999999999")

    def test_a_variant_names_both_parts(self):
        self.assertEqual(pc.to_line_item(pc.find(self.cat, "KB-87-BLU"))["desc"],
                         "Mechanical Keyboard (Blue)")

    def test_price_falls_back_rather_than_landing_at_zero(self):
        """A product priced only one way must still sell."""
        only_bulk = {"sku": "X", "name": "Thing", "bulk_price": "50.00"}
        self.assertEqual(pc.to_line_item(only_bulk)["price"], "50.00")

    def test_sell_price_wins_when_set(self):
        priced = {"sku": "X", "name": "Thing", "list_price": "100", "sell_price": "80"}
        self.assertEqual(pc.to_line_item(priced)["price"], "80")

    def test_quantity_can_be_given(self):
        self.assertEqual(pc.to_line_item(pc.find(self.cat, "MOU-1"), quantity=3)["qty"], 3)


class Validation(unittest.TestCase):
    def assert_rejects(self, fragment, cat):
        with self.assertRaises(config.ConfigError) as ctx:
            pc.validate(cat, "products.json")
        self.assertIn(fragment, str(ctx.exception))

    def test_a_good_catalogue_validates(self):
        pc.validate(catalogue(KEYBOARD, MOUSE), "products.json")

    def test_an_empty_catalogue_is_fine(self):
        pc.validate(pc.default_catalogue(), "products.json")

    def test_duplicate_sku_is_refused(self):
        self.assert_rejects("duplicate SKU", catalogue(MOUSE, MOUSE))

    def test_duplicate_barcode_is_refused(self):
        """A scan has to identify exactly one product."""
        other = dict(MOUSE, sku="MOU-2")
        self.assert_rejects("duplicate barcode", catalogue(MOUSE, other))

    def test_a_variant_barcode_clashing_with_a_product_is_refused(self):
        clash = dict(MOUSE, barcode=KEYBOARD["variants"][0]["barcode"])
        self.assert_rejects("duplicate barcode", catalogue(KEYBOARD, clash))

    def test_a_product_needs_a_name_or_a_sku(self):
        self.assert_rejects("findable", catalogue({"barcode": "123"}))

    def test_a_negative_price_is_refused(self):
        self.assert_rejects("negative", catalogue(dict(MOUSE, list_price="-5")))

    def test_a_non_numeric_price_is_refused(self):
        self.assert_rejects("must be a number", catalogue(dict(MOUSE, list_price="free")))

    def test_a_negative_stock_count_is_allowed(self):
        """Deliberately permitted. See StockDeduction for why refusing it was a bug:
        a sale that took stock below zero could not be recorded at all, leaving
        the figure wrong in the optimistic direction."""
        pc.validate(catalogue(dict(MOUSE, stock_count=-1)), "products.json")

    def test_a_fractional_stock_count_is_refused(self):
        self.assert_rejects("whole number", catalogue(dict(MOUSE, stock_count=1.5)))

    def test_serial_numbers_must_be_a_list(self):
        self.assert_rejects("list of serial numbers",
                            catalogue(dict(MOUSE, serial_numbers="SN-1")))

    def test_empty_prices_are_allowed(self):
        pc.validate(catalogue(dict(MOUSE, cost_price="", bulk_price="")), "products.json")


class StockDeduction(unittest.TestCase):
    """Stock is committed *after* the receipt exists — the opposite of numbering.

    An invoice number is reserved before rendering and kept even on failure,
    because a duplicate number is unrecoverable. Stock records that goods
    actually left, so a failed render must deduct nothing.
    """

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-stock-")
        shutil.copy(os.path.join(PROJ, "appsettings.example.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        pc.save(catalogue(
            {"sku": "KB-87", "name": "Keyboard", "stock_count": 10},
            {"sku": "MOU-1", "name": "Mouse", "stock_count": 3},
            {"sku": "NOCOUNT", "name": "Never counted"}))

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def track(self, on=True):
        config.update_app_settings({"inventory": {"track_stock": on}})

    def stock(self):
        return {p["sku"]: p.get("stock_count") for p in pc.load()["products"]}

    # -- the delta calculation, in isolation --------------------------------
    def test_a_first_sale_deducts_the_whole_quantity(self):
        self.assertEqual(pc.stock_deltas([{"sku": "KB-87", "qty": 2}]), {"kb-87": 2})

    def test_a_reissue_deducts_only_the_difference(self):
        """Correcting a receipt must not deduct the same sale twice."""
        self.assertEqual(
            pc.stock_deltas([{"sku": "KB-87", "qty": 3}], [{"sku": "KB-87", "qty": 2}]),
            {"kb-87": 1})

    def test_reducing_a_quantity_returns_stock(self):
        self.assertEqual(
            pc.stock_deltas([{"sku": "KB-87", "qty": 1}], [{"sku": "KB-87", "qty": 3}]),
            {"kb-87": -2})

    def test_an_unchanged_reissue_moves_nothing(self):
        self.assertEqual(
            pc.stock_deltas([{"sku": "KB-87", "qty": 2}], [{"sku": "KB-87", "qty": 2}]), {})

    def test_a_removed_line_gives_its_stock_back(self):
        self.assertEqual(
            pc.stock_deltas([], [{"sku": "MOU-1", "qty": 1}]), {"mou-1": -1})

    def test_lines_without_a_sku_are_ignored(self):
        self.assertEqual(pc.stock_deltas([{"desc": "Labour", "qty": 2}]), {})

    def test_repeated_skus_on_one_receipt_are_summed(self):
        self.assertEqual(
            pc.stock_deltas([{"sku": "KB-87", "qty": 1}, {"sku": "KB-87", "qty": 2}]),
            {"kb-87": 3})

    # -- the end-to-end behaviour -------------------------------------------
    def test_nothing_happens_while_tracking_is_off(self):
        """Off by default: an uncounted catalogue must not go straight negative."""
        self.assertFalse(config.load_app_settings()["inventory"]["track_stock"])
        pc.record_sale("INV-W1", [{"sku": "KB-87", "qty": 2}])
        self.assertEqual(self.stock()["KB-87"], 10)

    def test_a_sale_deducts_when_tracking_is_on(self):
        self.track()
        pc.record_sale("INV-W1", [{"sku": "KB-87", "qty": 2}])
        self.assertEqual(self.stock()["KB-87"], 8)

    def test_a_reissue_adjusts_rather_than_deducting_again(self):
        self.track()
        pc.record_sale("INV-W1", [{"sku": "KB-87", "qty": 2}])
        pc.record_sale("INV-W1", [{"sku": "KB-87", "qty": 3}],
                       previous_items=[{"sku": "KB-87", "qty": 2}])
        self.assertEqual(self.stock()["KB-87"], 7, "one more unit, not four")

    def test_a_product_that_was_never_counted_is_left_alone(self):
        self.track()
        pc.record_sale("INV-W1", [{"sku": "NOCOUNT", "qty": 5}])
        self.assertIsNone(self.stock()["NOCOUNT"],
                          "inventing a count from a sale would be a guess")

    def test_an_unknown_sku_changes_nothing(self):
        self.track()
        pc.record_sale("INV-W1", [{"sku": "NOT-STOCKED", "qty": 5}])
        self.assertEqual(self.stock()["KB-87"], 10)

    def test_overselling_is_recorded_and_the_receipt_still_stands(self):
        """Blocking a sale over a possibly-stale count would be worse."""
        self.track()
        self.assertTrue(pc.record_sale("INV-W1", [{"sku": "MOU-1", "qty": 99}]))
        self.assertEqual(self.stock()["MOU-1"], -96)

    def test_a_negative_count_is_storable(self):
        """The bug this exposed: validation refusing it meant the sale silently
        failed to record, leaving stock wrong in the optimistic direction."""
        pc.validate(catalogue({"sku": "X", "name": "X", "stock_count": -4}),
                    "products.json")

    def test_overselling_is_warned_about(self):
        self.track()
        with self.assertLogs("receipt_maker", level="WARNING") as captured:
            pc.record_sale("INV-W1", [{"sku": "MOU-1", "qty": 99}])
        joined = "\n".join(captured.output)
        self.assertIn("MOU-1", joined)
        self.assertIn("recount", joined)

    def test_variants_are_deducted_too(self):
        self.track()
        pc.save(catalogue({"sku": "KB", "name": "Keyboard", "stock_count": 5,
                           "variants": [{"sku": "KB-BLU", "stock_count": 2}]}))
        pc.record_sale("INV-W1", [{"sku": "KB-BLU", "qty": 1}])
        variant = pc.load()["products"][0]["variants"][0]
        self.assertEqual(variant["stock_count"], 1)
        self.assertEqual(pc.load()["products"][0]["stock_count"], 5,
                         "the parent's own count is separate")

    def test_recording_never_raises(self):
        """Stock is a convenience; it must never be able to fail a receipt."""
        self.track()
        with open(pc.catalogue_path(), "w", encoding="utf-8") as f:
            f.write("{ this catalogue is corrupt")
        self.assertFalse(pc.record_sale("INV-W1", [{"sku": "KB-87", "qty": 1}]))


class Storage(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-products-")
        shutil.copy(os.path.join(PROJ, "appsettings.example.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_missing_file_is_an_empty_catalogue(self):
        self.assertEqual(pc.load()["products"], [])

    def test_round_trip(self):
        pc.save(catalogue(KEYBOARD, MOUSE))
        loaded = pc.load()
        self.assertEqual(len(loaded["products"]), 2)
        self.assertEqual(loaded["products"][0]["variants"][0]["sku"], "KB-87-BLU")

    def test_saving_an_invalid_catalogue_is_refused_before_writing(self):
        pc.save(catalogue(MOUSE))
        with self.assertRaises(config.ConfigError):
            pc.save(catalogue(MOUSE, MOUSE))
        self.assertEqual(len(pc.load()["products"]), 1, "the file must be untouched")

    def test_a_backup_is_kept(self):
        pc.save(catalogue(MOUSE))
        pc.save(catalogue(MOUSE, KEYBOARD))
        self.assertTrue([n for n in os.listdir(self.dir) if n.endswith(".bak")])

    def test_a_concurrent_edit_is_detected(self):
        pc.save(catalogue(MOUSE))
        read_mtime = config.file_mtime(pc.catalogue_path())
        with open(pc.catalogue_path(), "w", encoding="utf-8") as f:
            json.dump(catalogue(KEYBOARD), f)
        os.utime(pc.catalogue_path(), (read_mtime + 5, read_mtime + 5))
        with self.assertRaises(config.ConfigConflict):
            pc.save(catalogue(MOUSE), known_mtime=read_mtime)

    def test_a_broken_file_is_reported_not_silently_emptied(self):
        with open(pc.catalogue_path(), "w", encoding="utf-8") as f:
            f.write("{ not json")
        with self.assertRaises(config.ConfigError):
            pc.load()


if __name__ == "__main__":
    unittest.main()
