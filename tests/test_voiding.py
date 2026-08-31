"""Voiding a receipt -- TODO.md section 2's last open item.

Stock deduction shipped without a way to cancel a sale, so a receipt raised by
mistake took its stock off the shelf permanently. This closes that.

The two halves pull in opposite directions and that is deliberate:

* **The invoice number is not freed.** A number that has been on a receipt in a
  customer's hands cannot be un-issued, and reusing it would put two different
  sales under one number. The gap in the sequence is explained by the void
  record, not avoided.
* **The stock does come back.** A stock figure can be recounted, so getting it
  wrong is recoverable; goods that were never sold are still on the shelf.

That is the same asymmetry settled when stock deduction was designed, applied
to the reverse operation.

Run: python -m unittest discover -s tests
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config              # noqa: E402
import invoice_counter     # noqa: E402
import product_catalogue   # noqa: E402
import receipt_history     # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


class VoidTestCase(unittest.TestCase):
    DATA = {"inv_no": "INV-W1001", "date_str": "1 Jan 2026", "cust": "Ada",
            "phone": "", "email": "", "receipt_type": "Online", "shipping": "0",
            "items": [{"sku": "KB-87", "desc": "Keyboard", "qty": 3,
                       "price": "10.00", "discount": "0", "tax": "0"}]}

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-void-")
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)
        product_catalogue.save({
            config.SCHEMA_VERSION_KEY: 1,
            "products": [{"sku": "KB-87", "name": "Keyboard", "stock_count": 10}],
        })
        config.update_app_settings({"inventory": {"track_stock": True}})

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def sell(self, data=None):
        data = data or self.DATA
        receipt_history.record(data, "", True)
        product_catalogue.record_sale(data["inv_no"], data["items"])
        return data

    def stock(self, sku="KB-87"):
        for product in product_catalogue.load()["products"]:
            if product["sku"] == sku:
                return product["stock_count"]
        return None


class StockComesBack(VoidTestCase):
    def test_a_sale_takes_stock_off(self):
        self.sell()
        self.assertEqual(self.stock(), 7)

    def test_voiding_puts_it_back(self):
        self.sell()
        ok, _ = receipt_history.void("INV-W1001")
        self.assertTrue(ok)
        self.assertEqual(self.stock(), 10)

    def test_it_returns_exactly_what_was_taken(self):
        """Not a reset: only this receipt's lines come back."""
        self.sell()
        product_catalogue.record_sale("INV-W1002", [
            {"sku": "KB-87", "qty": 2, "price": "10"}])
        self.assertEqual(self.stock(), 5)
        receipt_history.void("INV-W1001")
        self.assertEqual(self.stock(), 8, "the other sale must stand")

    def test_the_message_says_stock_came_back(self):
        self.sell()
        _, message = receipt_history.void("INV-W1001")
        self.assertIn("Stock returned", message)

    def test_nothing_is_returned_when_stock_is_not_tracked(self):
        config.update_app_settings({"inventory": {"track_stock": False}})
        self.sell()
        ok, message = receipt_history.void("INV-W1001")
        self.assertTrue(ok, "voiding must still work without stock tracking")
        self.assertNotIn("Stock returned", message)

    def test_a_missing_product_does_not_stop_the_void(self):
        """Voiding is a record first; stock is best-effort, as it is on sale."""
        self.sell()
        product_catalogue.save({config.SCHEMA_VERSION_KEY: 1, "products": []})
        ok, _ = receipt_history.void("INV-W1001")
        self.assertTrue(ok)


class TheNumberIsNotFreed(VoidTestCase):
    def test_voiding_does_not_rewind_the_counter(self):
        number = invoice_counter.reserve("W")
        self.sell(dict(self.DATA, inv_no=f"INV-W{number}"))
        receipt_history.void(f"INV-W{number}")
        self.assertEqual(invoice_counter.peek("W"), number + 1,
                         "a voided number must not be handed out again")

    def test_the_next_receipt_gets_a_fresh_number(self):
        first = invoice_counter.reserve("W")
        self.sell(dict(self.DATA, inv_no=f"INV-W{first}"))
        receipt_history.void(f"INV-W{first}")
        self.assertNotEqual(invoice_counter.reserve("W"), first)


class TheRecordIsAppended(VoidTestCase):
    def test_the_original_entry_is_left_alone(self):
        """Append-only: issued-then-cancelled is two facts, not one edit."""
        self.sell()
        receipt_history.void("INV-W1001", "customer changed their mind")
        lines = open(receipt_history.history_path(), encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertNotIn("voided", json.loads(lines[0]))

    def test_the_void_record_carries_the_reason(self):
        self.sell()
        receipt_history.void("INV-W1001", "customer changed their mind")
        self.assertEqual(receipt_history.entries()[0]["void_reason"],
                         "customer changed their mind")

    def test_the_void_record_carries_the_lines_it_cancelled(self):
        """Self-contained: what was voided is readable without the original."""
        self.sell()
        receipt_history.void("INV-W1001")
        self.assertEqual(receipt_history.entries()[0]["items"][0]["sku"], "KB-87")

    def test_the_receipt_reads_as_void_afterwards(self):
        self.sell()
        receipt_history.void("INV-W1001")
        self.assertTrue(receipt_history.is_voided("INV-W1001"))

    def test_an_unvoided_receipt_does_not(self):
        self.sell()
        self.assertFalse(receipt_history.is_voided("INV-W1001"))

    def test_the_list_shows_it_as_void(self):
        self.sell()
        receipt_history.void("INV-W1001")
        summary = receipt_history.summarise(receipt_history.entries()[0])
        self.assertIn("VOID", summary)

    def test_the_pdf_is_left_where_it_is(self):
        """Voiding is a record, not a deletion -- the customer may still have it."""
        pdf = os.path.join(self.dir, "invoices", "r.pdf")
        open(pdf, "wb").write(b"%PDF")
        receipt_history.record(self.DATA, pdf, True)
        receipt_history.void("INV-W1001")
        self.assertTrue(os.path.isfile(pdf))


class RefusingWhatCannotBeVoided(VoidTestCase):
    def test_an_unknown_receipt_is_refused(self):
        ok, message = receipt_history.void("INV-W9999")
        self.assertFalse(ok)
        self.assertIn("No receipt numbered", message)

    def test_voiding_twice_is_refused(self):
        self.sell()
        receipt_history.void("INV-W1001")
        ok, message = receipt_history.void("INV-W1001")
        self.assertFalse(ok)
        self.assertIn("already void", message)

    def test_the_second_void_does_not_return_stock_again(self):
        """The bug this guards: double-voiding would invent stock."""
        self.sell()
        receipt_history.void("INV-W1001")
        receipt_history.void("INV-W1001")
        self.assertEqual(self.stock(), 10)


class ReissuingAfterAVoid(VoidTestCase):
    def test_a_corrected_receipt_deducts_again_from_the_returned_stock(self):
        self.sell()
        receipt_history.void("INV-W1001")
        self.assertEqual(self.stock(), 10)
        self.sell(dict(self.DATA, items=[
            {"sku": "KB-87", "desc": "Keyboard", "qty": 1, "price": "10.00",
             "discount": "0", "tax": "0"}]))
        self.assertEqual(self.stock(), 9)


if __name__ == "__main__":
    unittest.main()
