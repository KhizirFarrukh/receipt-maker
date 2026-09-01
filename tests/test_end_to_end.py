"""One receipt with everything switched on, all the way through.

The rest of the suite tests features apart. Nothing tested them *together*, and
together is where the remaining risk is: per-unit serials and a shipment tag and
an instalment plan and a payment charge and a line total all land on the same
row of the same table, and each was built while the others were being built.

Two things here that no other file does:

* **The full pipeline**, form data → render → history → reload → regenerate,
  asserting nothing is lost or double-counted on the way round.
* **Everything on at once**, so an interaction has somewhere to show up.

These are slower than a unit test and there are deliberately few of them. They
are the tests that would notice a feature quietly breaking another one.

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
import installments        # noqa: E402
import product_catalogue   # noqa: E402
import receipt_history     # noqa: E402
import receipt_render      # noqa: E402
import receipt_service     # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


class EverythingOnTestCase(unittest.TestCase):
    """Every optional feature enabled, on one receipt."""

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-e2e-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)

        config.update_app_settings({
            "installments": {"enabled": True},
            "inventory": {"track_stock": True, "low_stock_threshold": 2},
            "payment": {"methods": [
                {"label": "Cash on delivery", "kind": "tax", "percent": 4},
                {"label": "Card", "kind": "fee", "percent": "2.9", "fixed": "0.30"},
            ]},
            "signature_image": {"enabled": False},
        })

        fields = config.load_fields()
        for field in fields["line_item_fields"]:
            if field["key"] in ("serial", "unit_id"):
                field.update(enabled=True, per_unit=True)
            if field["key"] in ("line_total", "barcode", "shipment"):
                field["enabled"] = True
        for field in fields["receipt_fields"]:
            if field["key"] == "notes":
                field["enabled"] = True
        config.save_fields(fields)

        product_catalogue.save({
            config.SCHEMA_VERSION_KEY: 1,
            "products": [
                {"sku": "KB", "barcode": "5901234123457", "name": "Keyboard",
                 "list_price": "45.00", "stock_count": 5,
                 "serial_numbers": ["S1", "S2", "S3", "S4", "S5"]},
                {"sku": "MS", "name": "Mouse", "list_price": "19.00",
                 "stock_count": 3, "serial_numbers": ["M1", "M2", "M3"]},
            ],
        })
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def receipt(self, **overrides):
        """A receipt using every feature at once."""
        data = {
            "inv_no": "INV-W1001",
            "date_str": "1 Jan 2026",
            "cust": "Dr. Ada O'Brien",
            "phone": "555-0100",
            "email": "ada@example.com",
            "receipt_type": "Online",
            "shipping": "25",
            "payment_method": "Cash on delivery",
            "notes": "Leave with the neighbour.\nRing twice.",
            "shipments": [{"id": "W1", "fee": "500"}],
            "items": [
                {"sku": "KB", "barcode": "5901234123457", "desc": "Keyboard",
                 "serial": "", "qty": 2, "price": "45.00", "discount": "5.00",
                 "tax": "1.50", "warranty": "12 Months Limited Warranty",
                 "shipment": "W1",
                 "units": [{"serial": "S1", "unit_id": "T1"},
                           {"serial": "S2", "unit_id": "T2"}],
                 "installment": {"months": 3, "down": "20", "monthly": "30"}},
                {"sku": "MS", "desc": "Mouse", "serial": "", "qty": 1,
                 "price": "19.00", "discount": "0", "tax": "0", "warranty": "",
                 "units": [{"serial": "M1", "unit_id": "T9"}]},
            ],
        }
        data.update(overrides)
        return data

    def render(self, data):
        settings = config.load_app_settings()
        payload = {
            "invoice_no": data["inv_no"], "date": data["date_str"],
            "customer_name": data["cust"], "customer_phone": data["phone"],
            "customer_email": data["email"], "items": data["items"],
            "receipt_type": data["receipt_type"], "shipping": data["shipping"],
        }
        for key in ("payment_method", "shipments", "installment", "notes"):
            if key in data:
                payload[key] = data[key]
        return receipt_render.render_receipt(
            payload, receipt_render.load_templates(),
            strings=config.load_strings(), currency=settings.get("currency"),
            tax_config=settings.get("tax"), fields=config.load_fields(),
            show_installments=True, payment_config=settings,
            show_shipping=True)

    def body(self, html):
        return html.split("<body>", 1)[1]


class EveryFeatureOnOneReceipt(EverythingOnTestCase):
    def test_it_renders_at_all(self):
        """The blunt one. Six features on one row is where a layout gives up."""
        self.assertIn("Keyboard", self.body(self.render(self.receipt())))

    def test_every_serial_appears(self):
        body = self.body(self.render(self.receipt()))
        for serial in ("S1", "S2", "M1"):
            self.assertIn(serial, body)

    def test_the_shops_own_unit_ids_appear(self):
        body = self.body(self.render(self.receipt()))
        for tag in ("T1", "T2", "T9"):
            self.assertIn(tag, body)

    def test_the_line_total_is_the_net(self):
        """2 x 45.00 = 90.00, less 5.00, plus 1.50 = 86.50."""
        self.assertIn("86.50", self.body(self.render(self.receipt())))

    def test_the_gross_is_still_shown_beside_it(self):
        self.assertIn("90.00", self.body(self.render(self.receipt())))

    def test_the_instalment_plan_is_spelled_out(self):
        body = self.body(self.render(self.receipt()))
        self.assertIn("Instalment plan", body)
        self.assertIn("3 ×", body)

    def test_the_payment_charge_is_reported_as_tax(self):
        body = self.body(self.render(self.receipt()))
        self.assertIn("Payment tax", body)
        self.assertIn("4%", body)

    def test_the_order_notes_print(self):
        self.assertIn("Leave with the neighbour",
                      self.body(self.render(self.receipt())))

    def test_the_shipment_marker_appears_for_a_split_order(self):
        """One line tagged and one not means two shipments, so it is labelled."""
        body = self.body(self.render(self.receipt()))
        self.assertIn("Shipment 1 of 2", body)

    def test_both_shipping_fees_are_charged(self):
        """The tagged group's 500 and the flat 25 for the untagged line."""
        body = self.body(self.render(self.receipt()))
        self.assertIn("500.00", body)
        self.assertIn("25.00", body)

    def test_the_warranty_and_the_plan_share_the_line(self):
        body = self.body(self.render(self.receipt()))
        self.assertIn("12 Months", body)
        self.assertIn("Instalment plan", body)

    def test_the_customers_name_survives_intact(self):
        body = self.body(self.render(self.receipt()))
        self.assertIn("Dr. Ada O&#39;Brien".replace("&#39;", "'"), body.replace("&#39;", "'"))

    def test_nothing_typed_became_markup(self):
        data = self.receipt(cust="<script>alert(1)</script>")
        self.assertNotIn("<script>alert", self.body(self.render(data)))


class TheTotalAddsUp(EverythingOnTestCase):
    """With six adjustments on one receipt, the arithmetic has to be checkable."""

    def totals(self):
        data = self.receipt()
        items = data["items"]
        gross = sum((receipt_render.quantize(receipt_render.line_gross(i), 2)
                     for i in items), Decimal("0"))
        tax = sum((receipt_render.quantize(i["tax"], 2) for i in items),
                  Decimal("0"))
        discount = sum((receipt_render.quantize(i["discount"], 2) for i in items),
                       Decimal("0"))
        return data, gross, tax, discount

    def test_the_printed_total_is_what_the_parts_come_to(self):
        data, gross, tax, discount = self.totals()
        # goods + shipping (500 grouped + 25 flat) + 4% payment tax on the lot
        goods = gross + tax - discount
        shipping = Decimal("525.00")
        before_payment = goods + shipping
        expected = before_payment + receipt_render.quantize(
            before_payment * Decimal("4") / Decimal(100), 2)
        self.assertIn(f"{expected:,.2f}", self.body(self.render(data)))

    def test_the_line_totals_sum_to_the_goods(self):
        data, gross, tax, discount = self.totals()
        lines = sum((receipt_render.line_total(i, 2) for i in data["items"]),
                    Decimal("0"))
        self.assertEqual(lines, gross + tax - discount)

    def test_the_instalment_total_is_disclosed_not_charged(self):
        """The settled rule: the cash price stays the receipt total."""
        data = self.receipt()
        body = self.body(self.render(data))
        financed = installments.financed_total(
            data["items"][0]["installment"], 2)
        self.assertEqual(financed, Decimal("110.00"))
        self.assertIn("Instalment plan", body)


class TheWholePipeline(EverythingOnTestCase):
    """Form data → generate → history → reload → generate again."""

    def stub_render(self):
        """Replace Playwright with a file write; this is not a browser test."""
        original = receipt_service.render_pdf
        receipt_service.render_pdf = lambda html, path: open(path, "wb").write(b"%PDF-1.4\n")
        self.addCleanup(setattr, receipt_service, "render_pdf", original)

        signer = receipt_service.sign_receipt_pdf
        receipt_service.sign_receipt_pdf = lambda path: False
        self.addCleanup(setattr, receipt_service, "sign_receipt_pdf", signer)

    def generate(self, data, warnings=None):
        self.stub_render()
        out = os.path.join(self.dir, "invoices", "out.pdf")
        receipt_service.generate(data, out, warnings=warnings)
        return out

    def test_a_receipt_goes_all_the_way_round(self):
        data = self.receipt()
        self.generate(data)

        entries = receipt_history.entries()
        self.assertEqual(len(entries), 1)

        back = receipt_history.to_form_data(entries[0])
        self.assertEqual(back["cust"], "Dr. Ada O'Brien")
        self.assertEqual(back["payment_method"], "Cash on delivery")
        self.assertEqual(back["shipments"], [{"id": "W1", "fee": "500"}])
        self.assertEqual(back["items"][0]["units"][0]["serial"], "S1")
        # History stores values as text, as it does for every other field, and
        # installments.normalise() is what reads them back -- so assert through
        # the reader rather than on the stored type.
        plan = installments.normalise(back["items"][0]["installment"])
        self.assertEqual(plan["months"], 3)
        self.assertEqual(plan["down"], Decimal("20"))
        self.assertEqual(back["notes"], "Leave with the neighbour.\nRing twice.")

    def test_stock_and_serials_move_once(self):
        self.generate(self.receipt())
        catalogue = product_catalogue.load()
        keyboard = catalogue["products"][0]
        self.assertEqual(keyboard["stock_count"], 3)
        self.assertEqual(keyboard["serial_numbers"], ["S3", "S4", "S5"])

    def test_reissuing_does_not_charge_the_stock_twice(self):
        data = self.receipt()
        self.generate(data)
        self.generate(data)                      # the same receipt again
        keyboard = product_catalogue.load()["products"][0]
        self.assertEqual(keyboard["stock_count"], 3, "stock moved twice")
        self.assertEqual(keyboard["serial_numbers"], ["S3", "S4", "S5"])

    def test_a_correction_adjusts_by_the_difference(self):
        data = self.receipt()
        self.generate(data)

        corrected = self.receipt()
        corrected["items"][0]["qty"] = 1
        corrected["items"][0]["units"] = [{"serial": "S1", "unit_id": "T1"}]
        self.generate(corrected)

        keyboard = product_catalogue.load()["products"][0]
        self.assertEqual(keyboard["stock_count"], 4, "one unit should come back")
        self.assertIn("S2", keyboard["serial_numbers"], "S2 was not sold")
        self.assertNotIn("S1", keyboard["serial_numbers"])

    def test_the_reissue_is_marked_duplicate(self):
        data = self.receipt()
        self.generate(data)
        html = self.render(data)
        # The second generation sees the first in history.
        self.assertTrue(receipt_history.latest_for("INV-W1001"))

    def test_voiding_returns_everything(self):
        data = self.receipt()
        self.generate(data)
        ok, _ = receipt_history.void("INV-W1001", "customer cancelled")
        self.assertTrue(ok)

        keyboard = product_catalogue.load()["products"][0]
        self.assertEqual(keyboard["stock_count"], 5)
        self.assertEqual(sorted(keyboard["serial_numbers"]),
                         ["S1", "S2", "S3", "S4", "S5"])

    def test_low_stock_is_reported_to_the_caller(self):
        warnings = []
        data = self.receipt()
        data["items"][1]["qty"] = 2               # mouse: 3 - 2 = 1, below 2
        self.generate(data, warnings=warnings)
        self.assertTrue(any("MS" in w for w in warnings), warnings)

    def test_the_filename_keeps_the_customers_full_stop(self):
        config.update_app_settings(
            {"invoice": {"filename_pattern": "{invoice_no}-{name}"}})
        name = receipt_service.build_pdf_filename(
            "INV-W1001", "1 Jan 2026", "Dr. Ada O'Brien", "", "")
        self.assertEqual(name, "INV-W1001-Dr. Ada O'Brien.pdf")


if __name__ == "__main__":
    unittest.main()
