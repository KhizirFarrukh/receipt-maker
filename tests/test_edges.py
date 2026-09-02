"""The remaining reachable branches, swept up.

Written after auditing what coverage still missed and separating "genuinely
needs a failing disk" from "I simply had not tested it". Everything here is the
second kind.

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
import product_catalogue   # noqa: E402
import receipt_history     # noqa: E402
import receipt_render      # noqa: E402
import template_engine     # noqa: E402


class TemplateEngineRemainder(unittest.TestCase):
    def test_repr_names_the_template_and_its_keys(self):
        template = template_engine.compile_template("{{a}}{{b}}", name="thing.html")
        text = repr(template)
        self.assertIn("thing.html", text)
        self.assertIn("'a'", text)

    def test_truthiness_of_collections(self):
        truthy = template_engine._truthy
        self.assertTrue(truthy([1]))
        self.assertFalse(truthy([]))
        self.assertTrue(truthy({"a": 1}))
        self.assertFalse(truthy({}))
        self.assertTrue(truthy({1, 2}))
        self.assertFalse(truthy(set()))

    def test_truthiness_of_scalars(self):
        truthy = template_engine._truthy
        self.assertFalse(truthy(None))
        self.assertFalse(truthy(False))
        self.assertTrue(truthy(True))
        self.assertFalse(truthy(0))
        self.assertTrue(truthy(1))

    def test_an_if_block_over_a_list_renders_when_it_has_items(self):
        self.assertEqual(
            template_engine.render_string("{{#if xs}}yes{{/if}}", {"xs": [1]}), "yes")
        self.assertEqual(
            template_engine.render_string("{{#if xs}}yes{{/if}}", {"xs": []}), "")


class ProductCatalogueRemainder(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-edge-")
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_unreadable_numbers_degrade_to_zero(self):
        self.assertEqual(product_catalogue.to_decimal("what"), Decimal("0"))
        self.assertEqual(product_catalogue.to_decimal(None), Decimal("0"))
        self.assertEqual(product_catalogue.to_decimal(""), Decimal("0"))

    def test_a_catalogue_that_is_not_an_object_is_refused(self):
        path = product_catalogue.catalogue_path()
        with open(path, "w", encoding="utf-8") as f:
            f.write("[]")
        with self.assertRaises(config.ConfigError) as ctx:
            product_catalogue.load()
        self.assertIn("JSON object", str(ctx.exception))

    def test_products_must_be_a_list(self):
        with self.assertRaises(config.ConfigError) as ctx:
            product_catalogue.validate({"products": {}}, "products.json")
        self.assertIn("must be a list", str(ctx.exception))

    def test_variants_must_be_a_list(self):
        with self.assertRaises(config.ConfigError):
            product_catalogue.validate(
                {"products": [{"sku": "A", "name": "A", "variants": "blue"}]},
                "products.json")

    def test_a_variant_must_be_an_object(self):
        with self.assertRaises(config.ConfigError):
            product_catalogue.validate(
                {"products": [{"sku": "A", "name": "A", "variants": ["blue"]}]},
                "products.json")

    def test_a_product_entry_must_be_an_object(self):
        with self.assertRaises(config.ConfigError):
            product_catalogue.validate({"products": ["just a name"]}, "products.json")

    def test_non_dict_entries_are_skipped_when_listing(self):
        items = product_catalogue.sellable_items({"products": [None, "x", {"sku": "A"}]})
        self.assertEqual([i["sku"] for i in items], ["A"])

    def test_a_variant_that_is_not_a_dict_is_skipped(self):
        items = product_catalogue.sellable_items(
            {"products": [{"sku": "A", "variants": [None, {"sku": "A-1"}]}]})
        self.assertEqual([i["sku"] for i in items], ["A", "A-1"])

    def test_a_product_with_no_usable_price_still_converts(self):
        line = product_catalogue.to_line_item({"sku": "A", "name": "Thing"})
        self.assertEqual(line["price"], "")

    def test_a_zero_price_is_skipped_in_favour_of_the_next(self):
        line = product_catalogue.to_line_item(
            {"sku": "A", "name": "Thing", "sell_price": "0", "list_price": "12.00"})
        self.assertEqual(line["price"], "12.00")

    def test_a_variant_with_no_name_falls_back_to_its_sku(self):
        items = product_catalogue.sellable_items(
            {"products": [{"sku": "A", "name": "Thing",
                           "variants": [{"sku": "A-BLUE"}]}]})
        self.assertEqual(items[1]["variant_name"], "A-BLUE")

    def test_empty_stock_and_serials_are_allowed(self):
        product_catalogue.validate(
            {"products": [{"sku": "A", "name": "A", "stock_count": "",
                           "serial_numbers": []}]}, "products.json")


class ReceiptRenderRemainder(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-render-edge-")
        shutil.copy(os.path.join(PROJ, "appsettings.example.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_an_asset_path_escaping_the_app_folder_is_refused(self):
        """A template must not be able to read arbitrary files off the disk."""
        self.assertEqual(receipt_render.resolve_local_asset_path("../../secrets.png"), "")

    def test_an_empty_asset_path_resolves_to_nothing(self):
        self.assertEqual(receipt_render.resolve_local_asset_path(""), "")
        self.assertEqual(receipt_render.resolve_local_asset_path("   "), "")

    def test_query_strings_and_fragments_are_stripped(self):
        with open(os.path.join(self.dir, "logo.png"), "wb") as f:
            f.write(b"\x89PNG")
        self.assertTrue(receipt_render.resolve_local_asset_path("logo.png?v=2"))
        self.assertTrue(receipt_render.resolve_local_asset_path("logo.png#top"))

    def test_remote_and_data_sources_are_treated_as_available(self):
        for src in ("https://x.test/a.png", "data:image/png;base64,AA",
                    "http://x.test/a.png", "file:///a.png", "about:blank"):
            self.assertTrue(receipt_render.logo_source_available(src), src)

    def test_an_empty_logo_source_is_not_available(self):
        self.assertFalse(receipt_render.logo_source_available(""))

    def test_inlining_leaves_remote_images_alone(self):
        html = '<img src="https://x.test/a.png">'
        self.assertEqual(receipt_render.inline_local_images(html), html)

    def test_inlining_leaves_a_missing_local_image_alone(self):
        html = '<img src="not-there.png">'
        self.assertEqual(receipt_render.inline_local_images(html), html)

    def test_an_address_with_blank_lines_is_collapsed(self):
        self.assertEqual(receipt_render.escape_address("A\n\n  \nB"), "A<br>B")

    def test_an_empty_address_renders_empty(self):
        self.assertEqual(receipt_render.escape_address("   "), "")

    def test_suggestions_are_empty_for_an_empty_name(self):
        self.assertEqual(receipt_render.suggest_asset_alternatives(""), [])

    def test_a_font_family_with_no_usable_file_type_is_skipped(self):
        with open(os.path.join(self.dir, "f.txt"), "wb") as f:
            f.write(b"not a font")
        self.assertEqual(
            receipt_render.build_font_faces({"family": "X", "files": ["f.txt"]}), "")

    def test_a_font_with_no_fallback_still_builds(self):
        path = os.path.join(self.dir, "f.woff2")
        with open(path, "wb") as f:
            f.write(b"\x00fake")
        css = receipt_render.build_font_faces(
            {"family": "Inter", "files": [path], "fallback": ""})
        self.assertIn("@font-face", css)
        self.assertIn("'Inter'", css)

    def test_a_fixed_inclusive_tax_row_is_reported_as_is(self):
        rows, added = receipt_render.compute_tax_rows(
            Decimal("100"), Decimal("0"),
            {"mode": "inclusive",
             "rows": [{"label": "Levy", "type": "fixed", "value": "5.00"}]}, 2)
        self.assertEqual(rows[0][1], Decimal("5.00"))
        self.assertEqual(added, Decimal("0"), "inclusive adds nothing")

    def test_a_boolean_column_reads_its_words_from_strings(self):
        strings = dict(config.default_strings(), boolean={"yes": "Ja", "no": "Nein"})
        field = {"key": "gift", "label": "Gift", "type": "boolean"}
        cell = receipt_render._cell_context({"gift": True}, field, strings=strings)
        self.assertEqual(cell["value"], "Ja")
        cell = receipt_render._cell_context({"gift": False}, field, strings=strings)
        self.assertEqual(cell["value"], "Nein")

    def test_a_multiline_cell_keeps_its_text(self):
        field = {"key": "note", "label": "Note", "type": "multiline"}
        cell = receipt_render._cell_context({"note": "one\ntwo"}, field)
        self.assertEqual(cell["value"], "one\ntwo")

    def test_the_file_url_helper_normalises_separators(self):
        url = receipt_render.file_url_for_directory(self.dir)
        self.assertTrue(url.startswith("file:///"))
        self.assertTrue(url.endswith("/"))
        self.assertNotIn("\\", url)


class ReceiptHistoryRemainder(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-hist-edge-")
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_blank_lines_in_the_file_are_skipped(self):
        receipt_history.record({"inv_no": "INV-W1"}, "", True)
        with open(receipt_history.history_path(), "a", encoding="utf-8") as f:
            f.write("\n\n   \n")
        self.assertEqual(len(receipt_history.entries()), 1)

    def test_a_json_line_that_is_not_an_object_is_skipped(self):
        receipt_history.record({"inv_no": "INV-W1"}, "", True)
        with open(receipt_history.history_path(), "a", encoding="utf-8") as f:
            f.write('["not an object"]\n')
        self.assertEqual(len(receipt_history.entries()), 1)

    def test_oldest_first_is_available(self):
        receipt_history.record({"inv_no": "INV-W1"}, "", True)
        receipt_history.record({"inv_no": "INV-W2"}, "", True)
        self.assertEqual([e["invoice_no"] for e in receipt_history.entries(False)],
                         ["INV-W1", "INV-W2"])

    def test_latest_for_finds_the_most_recent_version(self):
        receipt_history.record({"inv_no": "INV-W1", "cust": "First"}, "", True)
        receipt_history.record({"inv_no": "INV-W1", "cust": "Corrected"}, "", True)
        self.assertEqual(receipt_history.latest_for("INV-W1")["customer"]["name"],
                         "Corrected")

    def test_latest_for_an_unknown_number_is_none(self):
        self.assertIsNone(receipt_history.latest_for("INV-W999"))

    def test_latest_for_nothing_is_none(self):
        self.assertIsNone(receipt_history.latest_for(""))
        self.assertIsNone(receipt_history.latest_for(None))

    def test_boolean_item_values_are_kept_as_booleans(self):
        receipt_history.record(
            {"inv_no": "INV-W1", "items": [{"desc": "x", "gift": True}]}, "", True)
        self.assertIs(receipt_history.entries()[0]["items"][0]["gift"], True)

    def test_none_values_become_empty_text(self):
        record = receipt_history.build_record({"inv_no": None, "cust": None})
        self.assertEqual(record["invoice_no"], "")
        self.assertEqual(record["customer"]["name"], "")

    def test_the_pdf_name_is_derived_from_the_path(self):
        record = receipt_history.build_record(
            {"inv_no": "X"}, os.path.join("a", "b", "INV-W1.pdf"))
        self.assertEqual(record["pdf_name"], "INV-W1.pdf")

    def test_summarising_an_entry_with_no_items(self):
        receipt_history.record({"inv_no": "INV-W1", "shipping": "5.00"}, "", False)
        summary = receipt_history.summarise(receipt_history.entries()[0])
        self.assertEqual(summary[4], "unsigned")

    def test_matching_an_entry_with_no_customer(self):
        self.assertTrue(receipt_history.matches({"invoice_no": "INV-W1"}, "w1"))
        self.assertFalse(receipt_history.matches({"invoice_no": "INV-W1"}, "zzz"))


if __name__ == "__main__":
    unittest.main()
