"""CSV views over the catalogue and the history — TODO.md §2 and §4.

`ProductsRoundTrip` is the load-bearing class. A CSV is a rectangle and the
catalogue is not: a product holds variants that override some of its fields, and
a list of serial numbers. Flattening that is only safe if it can be undone
*exactly*, so the round trip is asserted rather than the two directions
separately.

History goes out only, and `HistoryIsExportOnly` records why: the file is an
append-only record of what happened, and reassembling receipts from spreadsheet
rows would mean inventing a rule for it.

Run: python -m unittest discover -s tests
"""
import csv
import io
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config              # noqa: E402
import csv_io              # noqa: E402
import product_catalogue   # noqa: E402
import receipt_history     # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


CATALOGUE = {
    config.SCHEMA_VERSION_KEY: 1,
    "products": [
        {"sku": "KB-87", "barcode": "5901234123457", "name": "Keyboard",
         "list_price": "45.00", "cost_price": "30.00", "stock_count": 10,
         "serial_numbers": ["S1", "S2"],
         "variants": [{"name": "Blue", "sku": "KB-87-B", "stock_count": 3},
                      {"name": "Red", "sku": "KB-87-R"}]},
        {"sku": "MS-01", "name": "Mouse", "list_price": "19.00",
         "stock_count": 5},
    ],
}


class CsvTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-csv-")
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, name="out.csv"):
        return os.path.join(self.dir, name)

    def read_rows(self, path):
        with open(path, encoding=csv_io.ENCODING, newline="") as handle:
            return list(csv.DictReader(handle))


class ProductsRoundTrip(CsvTestCase):
    def test_a_catalogue_survives_export_and_import(self):
        product_catalogue.save(CATALOGUE)
        csv_io.export_products(self.path())
        catalogue, _, _ = csv_io.import_products(self.path(), replace=True)
        self.assertEqual(catalogue["products"], CATALOGUE["products"])

    def test_variants_come_back_attached_to_their_product(self):
        product_catalogue.save(CATALOGUE)
        csv_io.export_products(self.path())
        catalogue, _, _ = csv_io.import_products(self.path(), replace=True)
        names = [v["name"] for v in catalogue["products"][0]["variants"]]
        self.assertEqual(names, ["Blue", "Red"])

    def test_a_variant_that_overrides_nothing_stays_that_way(self):
        """A blank cell means inherit, which is what the variant record means."""
        product_catalogue.save(CATALOGUE)
        csv_io.export_products(self.path())
        catalogue, _, _ = csv_io.import_products(self.path(), replace=True)
        red = catalogue["products"][0]["variants"][1]
        self.assertEqual(red, {"name": "Red", "sku": "KB-87-R"})

    def test_serial_numbers_survive_as_a_list(self):
        product_catalogue.save(CATALOGUE)
        csv_io.export_products(self.path())
        catalogue, _, _ = csv_io.import_products(self.path(), replace=True)
        self.assertEqual(catalogue["products"][0]["serial_numbers"], ["S1", "S2"])

    def test_the_stock_count_comes_back_as_a_number(self):
        """Every CSV cell is a string; the validator wants a whole number."""
        product_catalogue.save(CATALOGUE)
        csv_io.export_products(self.path())
        catalogue, _, _ = csv_io.import_products(self.path(), replace=True)
        self.assertIsInstance(catalogue["products"][0]["stock_count"], int)

    def test_an_empty_catalogue_round_trips(self):
        product_catalogue.save({config.SCHEMA_VERSION_KEY: 1, "products": []})
        self.assertEqual(csv_io.export_products(self.path()), 0)
        catalogue, _, _ = csv_io.import_products(self.path(), replace=True)
        self.assertEqual(catalogue["products"], [])


class TheExportedFile(CsvTestCase):
    def test_it_has_one_row_per_product_and_variant(self):
        product_catalogue.save(CATALOGUE)
        written = csv_io.export_products(self.path())
        self.assertEqual(written, 4)             # 2 products + 2 variants

    def test_a_variant_row_names_its_parent(self):
        product_catalogue.save(CATALOGUE)
        csv_io.export_products(self.path())
        rows = self.read_rows(self.path())
        self.assertEqual(rows[1]["parent_sku"], "KB-87")
        self.assertEqual(rows[1]["variant_name"], "Blue")

    def test_serials_share_one_cell(self):
        product_catalogue.save(CATALOGUE)
        csv_io.export_products(self.path())
        self.assertEqual(self.read_rows(self.path())[0]["serial_numbers"], "S1;S2")

    def test_it_is_written_with_a_bom_for_excel(self):
        """Without it Excel reads UTF-8 as the system codepage and mangles names."""
        product_catalogue.save(CATALOGUE)
        csv_io.export_products(self.path())
        with open(self.path(), "rb") as handle:
            self.assertTrue(handle.read(3) == b"\xef\xbb\xbf")

    def test_non_ascii_names_survive(self):
        product_catalogue.save({config.SCHEMA_VERSION_KEY: 1, "products": [
            {"sku": "A", "name": "Café Crème", "stock_count": 1}]})
        csv_io.export_products(self.path())
        catalogue, _, _ = csv_io.import_products(self.path(), replace=True)
        self.assertEqual(catalogue["products"][0]["name"], "Café Crème")

    def test_rows_do_not_gain_blank_lines(self):
        """csv needs newline="" or every row is double-spaced on Windows."""
        product_catalogue.save(CATALOGUE)
        csv_io.export_products(self.path())
        text = io.open(self.path(), encoding=csv_io.ENCODING, newline="").read()
        self.assertNotIn("\r\n\r\n", text)


class MergingIsTheSafeDefault(CsvTestCase):
    def setUp(self):
        super().setUp()
        product_catalogue.save(CATALOGUE)

    def write(self, rows):
        csv_io._write(self.path(), csv_io.PRODUCT_COLUMNS, rows)

    def test_a_partial_file_does_not_delete_anything(self):
        """A supplier's price list is not the whole catalogue."""
        self.write([{"sku": "KB-87", "name": "Keyboard", "list_price": "49.00"}])
        catalogue, added, updated = csv_io.import_products(self.path())
        self.assertEqual((added, updated), (0, 1))
        self.assertEqual(len(catalogue["products"]), 2, "MS-01 must survive")

    def test_matching_products_are_updated(self):
        self.write([{"sku": "KB-87", "name": "Keyboard", "list_price": "49.00"}])
        catalogue, _, _ = csv_io.import_products(self.path())
        self.assertEqual(catalogue["products"][0]["list_price"], "49.00")

    def test_new_products_are_added(self):
        self.write([{"sku": "NEW-1", "name": "New", "stock_count": "2"}])
        catalogue, added, updated = csv_io.import_products(self.path())
        self.assertEqual((added, updated), (1, 0))
        self.assertEqual(len(catalogue["products"]), 3)

    def test_matching_ignores_case(self):
        self.write([{"sku": "kb-87", "name": "Keyboard", "list_price": "49.00"}])
        _, added, updated = csv_io.import_products(self.path())
        self.assertEqual((added, updated), (0, 1))

    def test_replace_does_delete(self):
        self.write([{"sku": "ONLY", "name": "Only", "stock_count": "1"}])
        catalogue, _, _ = csv_io.import_products(self.path(), replace=True)
        self.assertEqual([p["sku"] for p in catalogue["products"]], ["ONLY"])


class RefusingABadFile(CsvTestCase):
    def write(self, rows, columns=None):
        csv_io._write(self.path(), columns or csv_io.PRODUCT_COLUMNS, rows)

    def test_a_missing_file(self):
        with self.assertRaises(csv_io.CsvError) as ctx:
            csv_io.import_products(os.path.join(self.dir, "nope.csv"))
        self.assertIn("does not exist", str(ctx.exception))

    def test_an_empty_file(self):
        open(self.path(), "w", encoding=csv_io.ENCODING).close()
        with self.assertRaises(csv_io.CsvError) as ctx:
            csv_io.import_products(self.path())
        self.assertIn("empty", str(ctx.exception))

    def test_a_file_with_none_of_the_expected_columns(self):
        self.write([{"colour": "red"}], columns=("colour",))
        with self.assertRaises(csv_io.CsvError) as ctx:
            csv_io.import_products(self.path())
        self.assertIn("Is it the right file?", str(ctx.exception))

    def test_a_product_with_no_sku_names_the_row(self):
        self.write([{"name": "Nameless"}])
        with self.assertRaises(csv_io.CsvError) as ctx:
            csv_io.import_products(self.path())
        self.assertIn("row 2", str(ctx.exception))

    def test_a_duplicate_sku_is_refused(self):
        """A scan has to identify exactly one product."""
        self.write([{"sku": "A", "name": "One"}, {"sku": "A", "name": "Two"}])
        with self.assertRaises(csv_io.CsvError) as ctx:
            csv_io.import_products(self.path())
        self.assertIn("row 3", str(ctx.exception))
        self.assertIn("twice", str(ctx.exception))

    def test_an_orphan_variant_is_refused(self):
        self.write([{"parent_sku": "MISSING", "variant_name": "Blue"}])
        with self.assertRaises(csv_io.CsvError) as ctx:
            csv_io.import_products(self.path())
        self.assertIn("has to follow its product", str(ctx.exception))

    def test_a_variant_with_no_name_is_refused(self):
        self.write([{"sku": "A", "name": "One"},
                    {"parent_sku": "A", "sku": "A-B"}])
        with self.assertRaises(csv_io.CsvError) as ctx:
            csv_io.import_products(self.path())
        self.assertIn("needs a name", str(ctx.exception))

    def test_a_non_numeric_stock_count_names_the_row_and_column(self):
        self.write([{"sku": "A", "name": "One", "stock_count": "loads"}])
        with self.assertRaises(csv_io.CsvError) as ctx:
            csv_io.import_products(self.path())
        self.assertIn("row 2", str(ctx.exception))
        self.assertIn("stock_count", str(ctx.exception))

    def test_a_spreadsheet_style_whole_number_is_accepted(self):
        """Excel writes 10 as "10.0"; refusing that would be unusable."""
        self.write([{"sku": "A", "name": "One", "stock_count": "10.0"}])
        catalogue, _, _ = csv_io.import_products(self.path(), replace=True)
        self.assertEqual(catalogue["products"][0]["stock_count"], 10)

    def test_an_invalid_catalogue_is_refused_before_it_is_kept(self):
        """The catalogue's own validator runs on the imported result."""
        self.write([{"sku": "A", "name": "One", "barcode": "123"},
                    {"sku": "B", "name": "Two", "barcode": "123"}])
        with self.assertRaises(config.ConfigError) as ctx:
            csv_io.import_products(self.path(), replace=True)
        self.assertIn("barcode", str(ctx.exception).lower())

    def test_a_negative_stock_count_is_allowed_on_purpose(self):
        """Overselling is recorded rather than refused (TODO §2), so an import
        carrying a negative count is data to fix, not a file to reject."""
        self.write([{"sku": "A", "name": "One", "stock_count": "-5"}])
        catalogue, _, _ = csv_io.import_products(self.path(), replace=True)
        self.assertEqual(catalogue["products"][0]["stock_count"], -5)


class HistoryIsExportOnly(CsvTestCase):
    ENTRY = {"inv_no": "INV-W1001", "date_str": "1 Jan 2026", "cust": "Ada",
             "phone": "555", "email": "ada@example.com", "receipt_type": "Online",
             "shipping": "5", "payment_method": "Card",
             "items": [{"sku": "A", "desc": "Thing", "qty": 2, "price": "10.00",
                        "discount": "1.00", "tax": "0.50"},
                       {"sku": "B", "desc": "Other", "qty": 1, "price": "5.00",
                        "discount": "0", "tax": "0"}]}

    def test_there_is_no_import_function(self):
        """Deliberate: the history is an append-only record of what happened."""
        self.assertFalse(hasattr(csv_io, "import_history"))

    def test_one_row_per_line_item(self):
        receipt_history.record(self.ENTRY, "", True)
        self.assertEqual(csv_io.export_history(self.path()), 2)

    def test_receipt_values_repeat_on_every_row(self):
        """Each row has to stand on its own to be filtered or summed."""
        receipt_history.record(self.ENTRY, "", True)
        csv_io.export_history(self.path())
        rows = self.read_rows(self.path())
        self.assertEqual({r["invoice_no"] for r in rows}, {"INV-W1001"})
        self.assertEqual({r["customer_name"] for r in rows}, {"Ada"})

    def test_the_lines_are_numbered(self):
        receipt_history.record(self.ENTRY, "", True)
        csv_io.export_history(self.path())
        self.assertEqual([r["line_no"] for r in self.read_rows(self.path())],
                         ["1", "2"])

    def test_the_line_total_is_worked_out(self):
        """2 x 10.00, less 1.00, plus 0.50."""
        receipt_history.record(self.ENTRY, "", True)
        csv_io.export_history(self.path())
        self.assertEqual(self.read_rows(self.path())[0]["line_total"], "19.50")

    def test_a_voided_receipt_says_so(self):
        receipt_history.record(self.ENTRY, "", True)
        receipt_history.void("INV-W1001")
        csv_io.export_history(self.path())
        self.assertIn("void", {r["status"] for r in self.read_rows(self.path())})

    def test_per_unit_serials_share_a_cell(self):
        """Splitting rows by unit would double the money on the line."""
        entry = dict(self.ENTRY, items=[
            {"sku": "A", "desc": "Thing", "qty": 2, "price": "10.00",
             "discount": "0", "tax": "0",
             "units": [{"serial": "S1"}, {"serial": "S2"}]}])
        receipt_history.record(entry, "", True)
        csv_io.export_history(self.path())
        self.assertEqual(self.read_rows(self.path())[0]["serial"], "S1;S2")

    def test_a_receipt_with_no_lines_still_appears(self):
        """Dropping it would make the export disagree with the history."""
        receipt_history.record(dict(self.ENTRY, items=[]), "", True)
        self.assertEqual(csv_io.export_history(self.path()), 1)

    def test_an_empty_history_writes_a_header_only(self):
        self.assertEqual(csv_io.export_history(self.path()), 0)
        self.assertEqual(self.read_rows(self.path()), [])

    def test_the_payment_method_is_carried(self):
        receipt_history.record(self.ENTRY, "", True)
        csv_io.export_history(self.path())
        self.assertEqual(self.read_rows(self.path())[0]["payment_method"], "Card")


if __name__ == "__main__":
    unittest.main()
