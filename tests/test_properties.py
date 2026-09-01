"""Invariants that must hold for *any* receipt, not just the ones I thought of.

Every other test file asserts a case somebody chose. That is how two real bugs
reached a customer-facing document with a green suite: the filename builder ate
the space in "Dr. Smith", and a partly-tagged order silently dropped its
shipping fee. Both were obvious the moment somebody asked "what would a real
name do here" — and invisible to a suite built from examples.

So this file states the rules the code must obey and then tries hard to break
them, over hundreds of generated receipts:

* money that is printed must add up to money that is charged;
* anything a value contributes must survive to the output intact;
* nothing a customer types may escape into the HTML as markup;
* the same input must always produce the same bytes;
* every round trip (history, CSV, the item tree) must be lossless.

The generator is seeded, so a failure is reproducible: the seed is in the
assertion message. It is deliberately *not* `random` at import time -- a test
that passes today and fails on Tuesday is worse than no test.

Run: python -m unittest discover -s tests
"""
import os
import random
import shutil
import string
import sys
import tempfile
import unittest
from decimal import Decimal

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config              # noqa: E402
import csv_io              # noqa: E402
import line_units          # noqa: E402
import product_catalogue   # noqa: E402
import receipt_history     # noqa: E402
import receipt_render      # noqa: E402
import receipt_service     # noqa: E402
import shipments           # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


#: Values chosen to be awkward rather than representative. Every one of these
#: is something a real shop has typed into a real till at some point.
NASTY_TEXT = [
    "Dr. Smith",                  # the full stop that broke the filename
    "J. R. Hartley",
    "Anne-Marie O'Brien",
    "Acme Ltd.",
    "  leading and trailing  ",
    "Ünïcödé Nâme",
    "北京市朝阳区",
    "علي محمد",                    # right-to-left
    "<script>alert(1)</script>",
    "Tom & Jerry",
    'He said "hello"',
    "line one\nline two",
    "tab\there",
    "a" * 200,                    # long enough to matter to a filename
    "",
    "0000000000000",              # leading zeros the item tree used to eat
    "007",
    "../../etc/passwd",
    "CON",                        # a reserved device name on Windows
    "%20%2e%2e",
]

MONEY = ["0", "0.00", "0.005", "1", "9.99", "10.00", "19.995", "100",
         "1234.56", "0.01", "999999.99"]


class Generator:
    """Deterministic receipt data. The seed is reported on failure."""

    def __init__(self, seed):
        self.seed = seed
        self.rng = random.Random(seed)

    def text(self):
        return self.rng.choice(NASTY_TEXT)

    def money(self):
        return self.rng.choice(MONEY)

    def item(self, with_units=False, with_shipment=False):
        quantity = self.rng.choice([1, 1, 2, 3, 7, 25])
        item = {
            "sku": self.rng.choice(["A", "B-1", "", "0012", self.text()]),
            "desc": self.text(),
            "serial": self.text(),
            "qty": quantity,
            "price": self.money(),
            "discount": self.rng.choice(["0", "0", self.money()]),
            "tax": self.rng.choice(["0", "0", self.money()]),
            "warranty": self.rng.choice(["", "12 Months Limited Warranty"]),
        }
        if with_units:
            item["units"] = [{"serial": self.text()} for _ in range(quantity)]
        if with_shipment:
            item["shipment"] = self.rng.choice(["", "W1", "W2", "W3"])
        return item

    def items(self, count=None, **kw):
        count = count if count is not None else self.rng.randint(1, 6)
        return [self.item(**kw) for _ in range(count)]


class PropertyTestCase(unittest.TestCase):
    #: Enough to explore the awkward-value space without slowing the suite to a
    #: crawl. Raise it locally when hunting something specific.
    RUNS = 120

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-prop-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def each(self):
        """Yield a fresh generator per run, so a failure names its seed."""
        for seed in range(self.RUNS):
            yield Generator(seed)


class MoneyPrintedIsMoneyCharged(PropertyTestCase):
    """The figures on the page must add up to the figure at the bottom."""

    def test_line_totals_sum_to_the_totals_block(self):
        for gen in self.each():
            items = gen.items()
            lines = sum((receipt_render.line_total(i, 2) for i in items),
                        Decimal("0"))
            gross = sum((receipt_render.quantize(receipt_render.line_gross(i), 2)
                         for i in items), Decimal("0"))
            taxes = sum((receipt_render.quantize(i["tax"], 2) for i in items),
                        Decimal("0"))
            discounts = sum((receipt_render.quantize(i["discount"], 2)
                             for i in items), Decimal("0"))
            self.assertEqual(lines, gross + taxes - discounts,
                             f"seed {gen.seed}: line totals disagree")

    def test_every_line_total_is_the_sum_of_its_own_parts(self):
        for gen in self.each():
            for item in gen.items():
                expected = (receipt_render.quantize(receipt_render.line_gross(item), 2)
                            + receipt_render.quantize(item["tax"], 2)
                            - receipt_render.quantize(item["discount"], 2))
                self.assertEqual(receipt_render.line_total(item, 2), expected,
                                 f"seed {gen.seed}")

    def test_rounding_never_loses_more_than_a_penny_per_line(self):
        """The reason each line is rounded before summing rather than after."""
        for gen in self.each():
            items = gen.items()
            rounded = sum((receipt_render.quantize(receipt_render.line_gross(i), 2)
                           for i in items), Decimal("0"))
            exact = sum((receipt_render.line_gross(i) for i in items),
                        Decimal("0"))
            self.assertLessEqual(abs(rounded - exact),
                                 Decimal("0.005") * len(items),
                                 f"seed {gen.seed}")

    def test_shipping_is_never_silently_dropped(self):
        """The bug: tagging some lines and not others lost the flat fee."""
        for gen in self.each():
            items = gen.items(with_shipment=True)
            fees = [{"id": tag, "fee": "10"}
                    for tag in shipments.groups_used(items)]
            _, total = shipments.rows({"shipments": fees}, items, 2,
                                      flat_shipping="25")
            expected = Decimal("10.00") * len(fees)
            if any(not shipments.group_of(i) for i in items):
                expected += Decimal("25.00")
            self.assertEqual(total, expected, f"seed {gen.seed}")


class NothingAValueContributesIsLost(PropertyTestCase):
    """The filename bug: a tidy-up ate a character that came from a name."""

    def test_a_name_survives_into_the_filename(self):
        config.update_app_settings(
            {"invoice": {"filename_pattern": "{invoice_no}-{name}"}})
        for text in NASTY_TEXT:
            expected = receipt_service.sanitize_filename_part(text)
            produced = receipt_service.build_pdf_filename(
                "INV-1", "", text, "", "")
            if expected:
                self.assertIn(expected, produced,
                              f"{text!r} was mangled into {produced!r}")

    def test_the_invoice_number_is_always_there(self):
        """Its absence is what makes two receipts overwrite each other."""
        config.update_app_settings(
            {"invoice": {"filename_pattern": "{invoice_no}-{name}-{phone}"}})
        for gen in self.each():
            produced = receipt_service.build_pdf_filename(
                "INV-W1001", gen.text(), gen.text(), gen.text(), gen.text())
            self.assertIn("INV-W1001", produced, f"seed {gen.seed}")

    def test_a_filename_is_never_empty_or_only_separators(self):
        config.update_app_settings(
            {"invoice": {"filename_pattern": "{name}-{phone}-{invoice_no}"}})
        for text in NASTY_TEXT:
            produced = receipt_service.build_pdf_filename("INV-1", "", text, "", "")
            stem = produced[:-len(".pdf")]
            self.assertTrue(stem.strip("-_ ."), f"{text!r} gave {produced!r}")

    def test_a_filename_never_contains_a_path_separator(self):
        config.update_app_settings(
            {"invoice": {"filename_pattern": "{invoice_no}-{name}"}})
        for text in NASTY_TEXT:
            produced = receipt_service.build_pdf_filename("INV-1", "", text, "", "")
            self.assertNotIn("/", produced)
            self.assertNotIn("\\", produced)
            self.assertNotIn("..", produced)


class NothingTypedBecomesMarkup(PropertyTestCase):
    """A customer's name is data. It must never reach the PDF as HTML."""

    def render(self, items, **data):
        templates = receipt_render.load_templates()
        settings = config.load_app_settings()
        payload = {"invoice_no": "INV-W1", "date": "1 Jan 2026",
                   "customer_name": "Ada", "customer_phone": "",
                   "customer_email": "", "items": items,
                   "receipt_type": "Online", "shipping": 0}
        payload.update(data)
        return receipt_render.render_receipt(
            payload, templates, strings=config.load_strings(),
            currency=settings.get("currency"), tax_config=settings.get("tax"),
            fields=config.load_fields())

    def body(self, html):
        return html.split("<body>", 1)[1]

    def test_a_script_tag_in_any_field_is_escaped(self):
        payload = "<script>alert(1)</script>"
        for field in ("customer_name", "customer_phone", "customer_email"):
            with self.subTest(field=field):
                html = self.body(self.render([], **{field: payload}))
                self.assertNotIn("<script>", html)
                self.assertIn("&lt;script&gt;", html)

    def test_a_script_tag_in_a_line_item_is_escaped(self):
        for key in ("sku", "desc", "serial"):
            with self.subTest(field=key):
                item = {"sku": "", "desc": "", "serial": "", "qty": 1,
                        "price": "1", "discount": "0", "tax": "0", "warranty": ""}
                item[key] = "<script>alert(1)</script>"
                html = self.body(self.render([item]))
                self.assertNotIn("<script>", html)

    def test_no_generated_receipt_leaks_an_unescaped_angle_bracket(self):
        for gen in self.each():
            items = gen.items()
            html = self.body(self.render(items))
            for item in items:
                for value in (item["desc"], item["sku"], item["serial"]):
                    if "<" in str(value):
                        self.assertNotIn(str(value), html,
                                         f"seed {gen.seed}: {value!r} unescaped")

    def test_quotes_and_ampersands_survive_as_text(self):
        item = {"sku": "", "desc": 'Tom & Jerry "boxed"', "serial": "", "qty": 1,
                "price": "1", "discount": "0", "tax": "0", "warranty": ""}
        html = self.body(self.render([item]))
        self.assertIn("&amp;", html)


class TheSameInputGivesTheSameBytes(PropertyTestCase):
    """Determinism, over data rather than over one fixture."""

    def render(self, items):
        return receipt_render.build_html(
            "INV-W1", "1 Jan 2026", "Ada", "", "", items, "Online", 0)

    def test_rendering_twice_is_identical(self):
        for gen in self.each():
            items = gen.items()
            self.assertEqual(self.render(items), self.render(items),
                             f"seed {gen.seed}")

    def test_grouping_by_shipment_is_stable(self):
        """An unstable sort would render one receipt two ways."""
        for gen in self.each():
            items = gen.items(with_shipment=True)
            first = [id(i) for i in shipments.order_items(items)]
            second = [id(i) for i in shipments.order_items(items)]
            self.assertEqual(first, second, f"seed {gen.seed}")

    def test_ordering_is_idempotent(self):
        for gen in self.each():
            items = gen.items(with_shipment=True)
            once = shipments.order_items(items)
            twice = shipments.order_items(once)
            self.assertEqual([id(i) for i in once], [id(i) for i in twice],
                             f"seed {gen.seed}")


class RoundTripsAreLossless(PropertyTestCase):
    """Anything stored must come back the same, or a correction loses data."""

    def test_history_gives_back_what_it_was_given(self):
        for gen in self.each():
            shutil.rmtree(os.path.join(self.dir, "invoices", ".archive"),
                          ignore_errors=True)
            items = gen.items(with_units=True)
            data = {"inv_no": "INV-W1001", "date_str": "1 Jan 2026",
                    "cust": gen.text(), "phone": gen.text(), "email": gen.text(),
                    "receipt_type": "Online", "shipping": "0", "items": items}
            receipt_history.record(data, "", True)
            back = receipt_history.to_form_data(receipt_history.entries()[0])

            self.assertEqual(back["cust"], str(data["cust"]), f"seed {gen.seed}")
            self.assertEqual(len(back["items"]), len(items), f"seed {gen.seed}")
            for original, restored in zip(items, back["items"]):
                self.assertEqual(restored["desc"], str(original["desc"]),
                                 f"seed {gen.seed}")
                self.assertEqual(restored.get("units"), original.get("units"),
                                 f"seed {gen.seed}: units changed")

    def test_the_catalogue_survives_csv(self):
        """Lossless for values as the catalogue stores them.

        Import strips surrounding whitespace, deliberately -- see
        `test_csv_import_strips_surrounding_space` below -- so the invariant is
        stated over already-trimmed values. Generating untrimmed ones here
        would only be asserting that the trim does not happen.
        """
        for gen in self.each():
            products = []
            for index in range(gen.rng.randint(1, 4)):
                product = {"sku": f"P{index}-{gen.seed}",
                           # A name is required for the round trip to be exact:
                           # an empty value is *dropped* on import rather than
                           # stored as "", which is right (absent and empty mean
                           # the same for a product) and is asserted separately
                           # below.
                           "name": gen.text().strip() or f"Product {index}",
                           "list_price": gen.money(),
                           "stock_count": gen.rng.randint(0, 50)}
                serials = [gen.text().strip()
                           for _ in range(gen.rng.randint(0, 3))]
                serials = [s for s in serials if s and ";" not in s]
                if serials:
                    product["serial_numbers"] = serials
                products.append(product)

            catalogue = {config.SCHEMA_VERSION_KEY: 1, "products": products}
            path = os.path.join(self.dir, "round.csv")
            csv_io.export_products(path, catalogue)
            back, _, _ = csv_io.import_products(path, replace=True)
            self.assertEqual(back["products"], products, f"seed {gen.seed}")

    def test_csv_import_drops_empty_values_rather_than_storing_them(self):
        """Absent and empty mean the same thing for a product field.

        Keeping `"name": ""` would put an empty key in every product for every
        column the shop does not use, which is noise in a file people read.
        """
        catalogue = {config.SCHEMA_VERSION_KEY: 1, "products": [
            {"sku": "A", "name": "Thing", "stock_count": 1}]}
        path = os.path.join(self.dir, "empties.csv")
        csv_io.export_products(path, catalogue)
        back, _, _ = csv_io.import_products(path, replace=True)
        product = back["products"][0]
        self.assertNotIn("barcode", product)
        self.assertNotIn("cost_price", product)
        self.assertEqual(product["name"], "Thing")

    def test_csv_import_strips_surrounding_space(self):
        """Deliberate, and worth a test of its own rather than a surprise.

        A SKU with a trailing space read from a spreadsheet is the sort of thing
        that makes a scan silently stop matching, and nobody ever means it. So
        import trims -- which is the one place a CSV round trip is not byte-for-
        byte, and this says so out loud.
        """
        catalogue = {config.SCHEMA_VERSION_KEY: 1, "products": [
            {"sku": "  A  ", "name": "  Spaced Name  ", "stock_count": 1}]}
        path = os.path.join(self.dir, "spaces.csv")
        csv_io.export_products(path, catalogue)
        back, _, _ = csv_io.import_products(path, replace=True)
        self.assertEqual(back["products"][0]["sku"], "A")
        self.assertEqual(back["products"][0]["name"], "Spaced Name")

    def test_units_normalise_to_exactly_the_quantity(self):
        for gen in self.each():
            item = gen.item(with_units=True)
            keys = ["serial", "unit_id"]
            units = line_units.normalise(item, keys)
            self.assertEqual(len(units), line_units.quantity_of(item),
                             f"seed {gen.seed}")
            for unit in units:
                self.assertEqual(sorted(unit), sorted(keys), f"seed {gen.seed}")

    def test_normalising_twice_changes_nothing(self):
        for gen in self.each():
            item = gen.item(with_units=True)
            keys = ["serial", "unit_id"]
            once = line_units.normalise(item, keys)
            twice = line_units.normalise(
                {"qty": item["qty"], "units": once}, keys)
            self.assertEqual(once, twice, f"seed {gen.seed}")


class NothingRaisesOnAwkwardInput(PropertyTestCase):
    """A receipt must render. Failing one over a strange name is not an option."""

    def test_rendering_survives_every_nasty_value(self):
        for gen in self.each():
            items = gen.items(with_units=True, with_shipment=True)
            try:
                receipt_render.build_html(
                    "INV-W1", gen.text(), gen.text(), gen.text(), gen.text(),
                    items, "Online", gen.money())
            except Exception as exc:              # noqa: BLE001 - that is the test
                self.fail(f"seed {gen.seed}: rendering raised {exc!r}")

    def test_a_filename_can_be_built_from_anything(self):
        for gen in self.each():
            try:
                receipt_service.build_pdf_filename(
                    gen.text() or "INV-1", gen.text(), gen.text(), gen.text(),
                    gen.text())
            except Exception as exc:              # noqa: BLE001
                self.fail(f"seed {gen.seed}: filename raised {exc!r}")

    def test_stock_never_raises(self):
        """record_sale is documented as unfailable; a sale must not depend on it."""
        import logging

        product_catalogue.save({config.SCHEMA_VERSION_KEY: 1, "products": [
            {"sku": "A", "name": "A", "stock_count": 10}]})
        config.update_app_settings({"inventory": {"track_stock": True}})

        # The oversell warnings are correct and expected here; 120 of them just
        # bury the rest of the run.
        logger = logging.getLogger("receipt_maker")
        previous = logger.level
        logger.setLevel(logging.ERROR)
        try:
            for gen in self.each():
                product_catalogue.record_sale(
                    "INV-1", gen.items(with_units=True))
        finally:
            logger.setLevel(previous)


if __name__ == "__main__":
    unittest.main()
