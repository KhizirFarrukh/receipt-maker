"""Selling a specific serial, and hearing about low stock — TODO.md §2.

Two loose ends the per-unit work (§6.1) left behind.

**Serials follow the count.** Once a line carries one serial per unit, selling
should take *that* serial off the shelf rather than only decrementing a number.
Otherwise the held list drifts away from the count and stops being worth
offering — which defeats the point of offering it.

**A low-stock warning belongs at the till.** It only ever reached the log, where
nobody selling anything is looking. It is said *after* the receipt is written,
because a stale stock figure must never stop a customer being served.

Run: python -m unittest discover -s tests
"""
import contextlib
import gc
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config              # noqa: E402
import product_catalogue   # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


@contextlib.contextmanager
def receipt_app():
    """A withdrawn ReceiptApp, torn down so Tk cannot abort the process.

    A tk.StringVar collected *after* its interpreter is gone raises from
    __del__ and can take the whole run down with "Tcl_AsyncDelete: async
    handler deleted by the wrong thread". Whether it happens depends on when
    the collector runs, which makes it look like a random failure in whichever
    module happens to run next. See claude_chat/PITFALLS.md.
    """
    import tkinter as tk
    import main

    root = tk.Tk()
    root.withdraw()
    app = main.ReceiptApp(root)
    try:
        yield app
    finally:
        app.__dict__.clear()
        del app
        gc.collect()
        root.destroy()
        gc.collect()


def line(sku="KB", qty=2, serials=()):
    item = {"sku": sku, "desc": "Keyboard", "qty": qty, "price": "10",
            "discount": "0", "tax": "0"}
    if serials:
        item["units"] = [{"serial": s} for s in serials]
    return item


class StockTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-serials-")
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)
        product_catalogue.save({
            config.SCHEMA_VERSION_KEY: 1,
            "products": [{
                "sku": "KB", "name": "Keyboard", "stock_count": 3,
                "serial_numbers": ["S1", "S2", "S3"],
                "variants": [{"name": "Blue", "sku": "KB-B", "stock_count": 2,
                              "serial_numbers": ["B1", "B2"]}],
            }],
        })
        config.update_app_settings({"inventory": {"track_stock": True}})

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def product(self, sku="KB"):
        for entry in product_catalogue.sellable_items(product_catalogue.load()):
            if entry.get("sku") == sku:
                return entry
        return None

    def serials(self, sku="KB"):
        return product_catalogue.held_serials(product_catalogue.load(), sku)


class OfferingWhatIsHeld(StockTestCase):
    def test_a_products_serials_are_listed(self):
        self.assertEqual(self.serials("KB"), ["S1", "S2", "S3"])

    def test_a_variant_has_its_own(self):
        """A variant is what is actually sold when one exists."""
        self.assertEqual(self.serials("KB-B"), ["B1", "B2"])

    def test_an_unknown_sku_offers_nothing(self):
        self.assertEqual(self.serials("NOPE"), [])

    def test_no_sku_offers_nothing(self):
        self.assertEqual(self.serials(""), [])

    def test_the_form_reads_them_without_a_catalogue(self):
        """A broken catalogue must not stop somebody typing serials by hand."""
        os.remove(product_catalogue.catalogue_path())
        with receipt_app() as app:
            self.assertEqual(app.held_serials_for("KB"), [])

    def test_the_form_offers_what_is_held(self):
        with receipt_app() as app:
            self.assertEqual(app.held_serials_for("KB"), ["S1", "S2", "S3"])


class SellingTakesTheSerialOffTheShelf(StockTestCase):
    def test_the_sold_serials_go(self):
        product_catalogue.record_sale("INV-1", [line(serials=["S1", "S3"])])
        self.assertEqual(self.serials(), ["S2"])

    def test_the_count_goes_down_too(self):
        product_catalogue.record_sale("INV-1", [line(serials=["S1", "S3"])])
        self.assertEqual(self.product()["stock_count"], 1)

    def test_a_serial_the_catalogue_never_knew_is_ignored(self):
        """A unit can predate the catalogue; that is not a reason to complain."""
        product_catalogue.record_sale("INV-1", [line(qty=1, serials=["OLD-1"])])
        self.assertEqual(self.serials(), ["S1", "S2", "S3"])

    def test_a_variants_serials_are_taken_from_the_variant(self):
        product_catalogue.record_sale(
            "INV-1", [line(sku="KB-B", qty=1, serials=["B1"])])
        self.assertEqual(self.serials("KB-B"), ["B2"])
        self.assertEqual(self.serials("KB"), ["S1", "S2", "S3"],
                         "the parent's serials must not be touched")

    def test_a_line_with_no_serials_still_moves_the_count(self):
        product_catalogue.record_sale("INV-1", [line()])
        self.assertEqual(self.product()["stock_count"], 1)
        self.assertEqual(self.serials(), ["S1", "S2", "S3"])

    def test_nothing_happens_when_stock_is_not_tracked(self):
        config.update_app_settings({"inventory": {"track_stock": False}})
        product_catalogue.record_sale("INV-1", [line(serials=["S1"])])
        self.assertEqual(self.serials(), ["S1", "S2", "S3"])


class ReissuingDoesNotSellTwice(StockTestCase):
    def test_the_same_serials_again_are_not_removed_twice(self):
        first = [line(serials=["S1", "S3"])]
        product_catalogue.record_sale("INV-1", first)
        product_catalogue.record_sale("INV-1", first, previous_items=first)
        self.assertEqual(self.serials(), ["S2"])

    def test_a_serial_taken_off_the_receipt_comes_back(self):
        """It was not sold after all, so it is still on the shelf."""
        first = [line(serials=["S1", "S3"])]
        product_catalogue.record_sale("INV-1", first)
        corrected = [line(qty=1, serials=["S1"])]
        product_catalogue.record_sale("INV-1", corrected, previous_items=first)
        self.assertIn("S3", self.serials())
        self.assertNotIn("S1", self.serials())

    def test_a_serial_added_by_a_correction_is_removed(self):
        first = [line(qty=1, serials=["S1"])]
        product_catalogue.record_sale("INV-1", first)
        corrected = [line(qty=2, serials=["S1", "S2"])]
        product_catalogue.record_sale("INV-1", corrected, previous_items=first)
        self.assertEqual(self.serials(), ["S3"])

    def test_voiding_puts_them_all_back(self):
        sold = [line(serials=["S1", "S3"])]
        product_catalogue.record_sale("INV-1", sold)
        product_catalogue.record_sale("INV-1", [], previous_items=sold)
        self.assertEqual(sorted(self.serials()), ["S1", "S2", "S3"])
        self.assertEqual(self.product()["stock_count"], 3)


class TheLowStockWarning(StockTestCase):
    def warn(self, items, threshold=0, previous=None):
        config.update_app_settings(
            {"inventory": {"low_stock_threshold": threshold}})
        warnings = []
        product_catalogue.record_sale("INV-1", items, previous_items=previous,
                                      warnings=warnings)
        return warnings

    def test_selling_the_last_one_says_so(self):
        self.assertIn("that was the last one in stock",
                      " ".join(self.warn([line(qty=3)])))

    def test_overselling_suggests_a_recount(self):
        messages = " ".join(self.warn([line(qty=5)]))
        self.assertIn("-2", messages)
        self.assertIn("recount", messages)

    def test_a_comfortable_sale_says_nothing(self):
        self.assertEqual(self.warn([line(qty=1)]), [])

    def test_a_threshold_warns_early(self):
        """Set 3 and hear about it while there is still time to reorder."""
        messages = " ".join(self.warn([line(qty=1)], threshold=3))
        self.assertIn("only 2 left", messages)

    def test_the_threshold_is_off_by_default(self):
        self.assertEqual(
            config.default_app_settings()["inventory"]["low_stock_threshold"], 0)

    def test_it_must_be_a_whole_number_of_units(self):
        for bad in (-1, "three", True, 1.5):
            with self.subTest(value=bad):
                settings = config.default_app_settings()
                settings["inventory"]["low_stock_threshold"] = bad
                with self.assertRaises(config.ConfigError) as ctx:
                    config.validate(settings, "appsettings.json")
                self.assertEqual(ctx.exception.key,
                                 "inventory.low_stock_threshold")

    def test_the_warning_never_stops_the_sale(self):
        """It is said after the receipt exists; record_sale cannot fail one."""
        warnings = self.warn([line(qty=99)])
        self.assertTrue(warnings)
        self.assertEqual(self.product()["stock_count"], -96,
                         "the sale went through regardless")

    def test_asking_for_no_warnings_is_still_fine(self):
        product_catalogue.record_sale("INV-1", [line(qty=3)])


class ItReachesTheTill(StockTestCase):
    """The point of the exercise: the log is not where a shopkeeper looks."""

    def test_generate_passes_the_list_through(self):
        import inspect
        import receipt_service
        signature = inspect.signature(receipt_service.generate)
        self.assertIn("warnings", signature.parameters)

    def test_the_confirmation_carries_it(self):
        import main

        asked = {}
        original = main.ask_with_memory
        main.ask_with_memory = lambda parent, title, message: (
            asked.update(message=message) or (False, False))
        try:
            with receipt_app() as app:
                app._on_generated("out.pdf", True, ["KB: only 2 left in stock."])
        finally:
            main.ask_with_memory = original

        self.assertIn("only 2 left in stock", asked["message"])

    def test_the_status_line_carries_it_too(self):
        import main

        original = main.ask_with_memory
        main.ask_with_memory = lambda *a, **k: (False, False)
        try:
            with receipt_app() as app:
                app._on_generated("out.pdf", True, ["KB: only 2 left in stock."])
                self.assertIn("only 2 left", app.status_label.cget("text"))
        finally:
            main.ask_with_memory = original

    def test_a_clean_sale_says_nothing_extra(self):
        import main

        asked = {}
        original = main.ask_with_memory
        main.ask_with_memory = lambda parent, title, message: (
            asked.update(message=message) or (False, False))
        try:
            with receipt_app() as app:
                app._on_generated("out.pdf", True, [])
        finally:
            main.ask_with_memory = original
        self.assertNotIn("Stock:", asked["message"])


if __name__ == "__main__":
    unittest.main()
