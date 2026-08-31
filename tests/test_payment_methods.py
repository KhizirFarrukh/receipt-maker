"""TODO.md section 6.10 -- what the customer pays with, and what that costs.

Bank transfer is free, cash on delivery carries a 4% government levy, a card
carries the processor's handling fee.

`TaxAndFeeStayApart` is the class that matters. The two have identical
arithmetic -- a percentage of a total -- which is exactly why merging them is
tempting and exactly why it must not happen. The COD levy is tax a government
imposes and the shop remits; a card fee is a private company's charge for a
service and is not tax at all. Recording a fee as tax overstates the tax
collected on every card sale, which is a filing problem rather than a cosmetic
one.

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
import payment_methods     # noqa: E402
import receipt_history     # noqa: E402
import receipt_render      # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


METHODS = [
    {"label": "Bank transfer", "kind": "fee", "percent": 0, "fixed": 0},
    {"label": "Cash on delivery", "kind": "tax", "percent": 4},
    {"label": "Card", "kind": "fee", "percent": "2.9", "fixed": "0.30"},
]
SETTINGS = {"payment": {"methods": METHODS}}


class Lookup(unittest.TestCase):
    def test_the_labels_are_offered_in_order(self):
        self.assertEqual(payment_methods.labels(SETTINGS),
                         ["Bank transfer", "Cash on delivery", "Card"])

    def test_a_method_is_found_by_name(self):
        self.assertEqual(payment_methods.find(SETTINGS, "Card")["percent"], "2.9")

    def test_the_name_match_ignores_case_and_padding(self):
        self.assertIsNotNone(payment_methods.find(SETTINGS, "  card  "))

    def test_an_unknown_method_is_none(self):
        self.assertIsNone(payment_methods.find(SETTINGS, "Cheque"))

    def test_no_method_chosen_is_none(self):
        self.assertIsNone(payment_methods.find(SETTINGS, ""))

    def test_no_methods_configured_is_survived(self):
        self.assertEqual(payment_methods.labels({}), [])


class Charges(unittest.TestCase):
    def test_a_percentage(self):
        method = {"percent": 4}
        self.assertEqual(payment_methods.charge(method, Decimal("100"), 2),
                         Decimal("4.00"))

    def test_a_fixed_amount(self):
        self.assertEqual(
            payment_methods.charge({"fixed": "0.30"}, Decimal("100"), 2),
            Decimal("0.30"))

    def test_percent_and_fixed_together(self):
        """How card fees actually work: 2.9% + 0.30."""
        method = {"percent": "2.9", "fixed": "0.30"}
        self.assertEqual(payment_methods.charge(method, Decimal("100"), 2),
                         Decimal("3.20"))

    def test_a_free_method_charges_nothing(self):
        self.assertEqual(
            payment_methods.charge({"percent": 0, "fixed": 0}, Decimal("100"), 2),
            Decimal("0.00"))

    def test_each_part_is_rounded_before_they_are_added(self):
        method = {"percent": "0.5", "fixed": "0.005"}
        self.assertEqual(payment_methods.charge(method, Decimal("1"), 2),
                         Decimal("0.02"))

    def test_junk_is_not_a_charge(self):
        self.assertEqual(payment_methods.charge("not a method", 100, 2),
                         Decimal("0"))

    def test_describe_reads_as_the_customer_would_check_it(self):
        self.assertEqual(payment_methods.describe({"percent": 4}), "4%")
        self.assertEqual(
            payment_methods.describe({"percent": "2.9", "fixed": "0.30"}),
            "2.9% + 0.3")

    def test_a_free_method_describes_as_nothing(self):
        self.assertEqual(payment_methods.describe({"percent": 0}), "")


class TaxAndFeeStayApart(unittest.TestCase):
    """Identical arithmetic, different meaning -- and different reporting."""

    def test_a_government_levy_is_tax(self):
        method = payment_methods.find(SETTINGS, "Cash on delivery")
        self.assertEqual(payment_methods.kind_of(method), payment_methods.KIND_TAX)

    def test_a_processors_charge_is_a_fee(self):
        method = payment_methods.find(SETTINGS, "Card")
        self.assertEqual(payment_methods.kind_of(method), payment_methods.KIND_FEE)

    def test_an_unstated_kind_defaults_to_fee_not_tax(self):
        """The safe default: over-reporting tax is the worse mistake."""
        self.assertEqual(payment_methods.kind_of({"percent": 1}),
                         payment_methods.KIND_FEE)

    def test_an_unknown_kind_falls_back_to_fee(self):
        self.assertEqual(payment_methods.kind_of({"kind": "levy"}),
                         payment_methods.KIND_FEE)

    def test_the_row_carries_the_kind_through(self):
        label, kind, amount = payment_methods.row(
            SETTINGS, "Cash on delivery", Decimal("100"), 2)
        self.assertEqual(kind, "tax")
        self.assertEqual(amount, Decimal("4.00"))
        self.assertIn("4%", label)

    def test_an_invalid_kind_is_refused_with_an_explanation(self):
        settings = {"payment": {"methods": [
            {"label": "Odd", "kind": "levy", "percent": 1}]}}
        with self.assertRaises(payment_methods.PaymentMethodError) as ctx:
            payment_methods.validate(settings)
        self.assertIn("overstates the tax", str(ctx.exception))


class Rows(unittest.TestCase):
    def test_a_free_method_prints_no_row(self):
        """A zero charge is noise, not information."""
        self.assertIsNone(payment_methods.row(SETTINGS, "Bank transfer", 100, 2))

    def test_no_method_chosen_prints_no_row(self):
        self.assertIsNone(payment_methods.row(SETTINGS, "", 100, 2))

    def test_an_unknown_method_prints_no_row(self):
        self.assertIsNone(payment_methods.row(SETTINGS, "Cheque", 100, 2))

    def test_the_label_shows_how_the_charge_was_worked_out(self):
        label, _, _ = payment_methods.row(SETTINGS, "Card", Decimal("100"), 2)
        self.assertIn("Card", label)
        self.assertIn("2.9%", label)


class Validation(unittest.TestCase):
    def valid(self, **kw):
        method = {"label": "M", "kind": "fee", "percent": 1}
        method.update(kw)
        return {"payment": {"methods": [method]}}

    def test_a_sound_method_passes(self):
        payment_methods.validate(self.valid())

    def test_no_methods_at_all_passes(self):
        payment_methods.validate({})

    def test_a_method_needs_a_label(self):
        with self.assertRaises(payment_methods.PaymentMethodError) as ctx:
            payment_methods.validate(self.valid(label="  "))
        self.assertIn("must have a label", str(ctx.exception))

    def test_two_methods_may_not_share_a_name(self):
        settings = {"payment": {"methods": [
            {"label": "Card", "percent": 1}, {"label": "card", "percent": 2}]}}
        with self.assertRaises(payment_methods.PaymentMethodError) as ctx:
            payment_methods.validate(settings)
        self.assertIn("duplicate", str(ctx.exception))

    def test_a_negative_charge_is_refused(self):
        with self.assertRaises(payment_methods.PaymentMethodError) as ctx:
            payment_methods.validate(self.valid(percent=-1))
        self.assertIn("cannot be negative", str(ctx.exception))

    def test_a_percentage_over_a_hundred_is_refused(self):
        with self.assertRaises(payment_methods.PaymentMethodError) as ctx:
            payment_methods.validate(self.valid(percent=150))
        self.assertIn("more than the whole payment", str(ctx.exception))

    def test_a_method_must_be_an_object(self):
        with self.assertRaises(payment_methods.PaymentMethodError):
            payment_methods.validate({"payment": {"methods": ["Card"]}})

    def test_it_runs_as_part_of_config_validation(self):
        """A bad method must fail at load, not halfway through a receipt."""
        settings = config.default_app_settings()
        settings["payment"] = {"methods": [{"label": "X", "kind": "levy"}]}
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(settings, "appsettings.json")
        self.assertEqual(ctx.exception.key, "payment.methods")

    def test_the_methods_list_must_be_a_list(self):
        settings = config.default_app_settings()
        settings["payment"] = {"methods": "card"}
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(settings, "appsettings.json")
        self.assertEqual(ctx.exception.key, "payment.methods")

    def test_none_are_configured_by_default(self):
        self.assertEqual(config.default_app_settings()["payment"]["methods"], [])


class OnTheReceipt(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-pay-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        config.update_app_settings({"payment": {"methods": METHODS}})
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def render(self, method=""):
        templates = receipt_render.load_templates()
        settings = config.load_app_settings()
        data = {"invoice_no": "INV-W1", "date": "1 Jan 2026",
                "customer_name": "Ada", "customer_phone": "", "customer_email": "",
                "items": [{"sku": "A", "desc": "Thing", "serial": "", "qty": 1,
                           "price": "100", "discount": "0", "tax": "0",
                           "warranty": ""}],
                "receipt_type": "Online", "shipping": 0}
        if method:
            data["payment_method"] = method
        return receipt_render.render_receipt(
            data, templates, strings=config.load_strings(),
            currency=settings.get("currency"), tax_config=settings.get("tax"),
            fields=config.load_fields(), payment_config=settings)

    def test_nothing_is_charged_when_no_method_is_chosen(self):
        html = self.render()
        self.assertNotIn("Payment", html)

    def test_a_free_method_adds_nothing(self):
        html = self.render("Bank transfer")
        self.assertNotIn("Payment", html)

    def test_a_levy_prints_under_the_tax_heading(self):
        html = self.render("Cash on delivery")
        self.assertIn("Payment tax", html)
        self.assertNotIn("Payment charge", html)

    def test_a_processing_fee_prints_under_the_fee_heading(self):
        html = self.render("Card")
        self.assertIn("Payment charge", html)
        self.assertNotIn("Payment tax", html)

    def test_the_charge_is_added_to_the_total(self):
        html = self.render("Cash on delivery")
        self.assertIn("104.00", html)

    def test_the_subtotal_still_shows_when_the_charge_is_the_only_adjustment(self):
        """A charge and a total with nothing tying them is worse than nothing."""
        html = self.render("Cash on delivery")
        self.assertIn("Subtotal", html)

    def test_the_customer_can_check_the_arithmetic(self):
        html = self.render("Cash on delivery")
        self.assertIn("4%", html)


class HistoryRoundTrip(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-pay-hist-")
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    BASE = {"inv_no": "INV-W1001", "date_str": "1 Jan 2026", "cust": "Ada",
            "phone": "", "email": "", "receipt_type": "Online", "shipping": "0",
            "items": [{"sku": "A", "desc": "T", "qty": 1, "price": "1",
                       "discount": "0", "tax": "0"}]}

    def test_the_method_survives(self):
        receipt_history.record(dict(self.BASE, payment_method="Card"), "", True)
        back = receipt_history.to_form_data(receipt_history.entries()[0])
        self.assertEqual(back["payment_method"], "Card")

    def test_shipment_fees_survive(self):
        fees = [{"id": "A", "fee": "500"}]
        receipt_history.record(dict(self.BASE, shipments=fees), "", True)
        back = receipt_history.to_form_data(receipt_history.entries()[0])
        self.assertEqual(back["shipments"], fees)

    def test_an_instalment_plan_survives(self):
        plan = {"months": 6, "down": "500", "monthly": "100"}
        receipt_history.record(dict(self.BASE, installment=plan), "", True)
        back = receipt_history.to_form_data(receipt_history.entries()[0])
        self.assertEqual(back["installment"], plan)

    def test_an_ordinary_receipt_gains_no_empty_keys(self):
        """The reload shape must keep matching the shape generation expects."""
        receipt_history.record(self.BASE, "", True)
        back = receipt_history.to_form_data(receipt_history.entries()[0])
        self.assertEqual(set(back), set(self.BASE))


if __name__ == "__main__":
    unittest.main()
