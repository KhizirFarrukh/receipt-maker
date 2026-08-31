"""TODO.md section 6.5 -- pay a deposit now and the rest monthly.

Two decisions are encoded here and both are load-bearing.

**One plan, or one per line, never both.** `BothScopesAreRefused` is the test
that matters: hiding a control in the UI is not enough, because a receipt loaded
from history or a file edited by hand can carry both, and silently ignoring one
prints a total nobody can reconstruct.

**The cash price stays the receipt total.** This was the open question in TODO
6.5 and it was settled this way because the tax rows apply to what was sold:
the goods have a price, and financing them is a separate arrangement on top.
Making the financed figure the total would push tax onto the finance charge.
`TheCashTotalIsUnchanged` holds that line.

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
import receipt_render      # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


PLAN = {"months": 6, "down": "5000", "monthly": "3000"}


class Normalising(unittest.TestCase):
    def test_a_plan_becomes_numbers(self):
        plan = installments.normalise(PLAN)
        self.assertEqual(plan["months"], 6)
        self.assertEqual(plan["down"], Decimal("5000"))

    def test_no_plan_is_none_rather_than_an_empty_plan(self):
        for empty in (None, {}, {"months": 0, "down": "0", "monthly": "0"}, "nope"):
            self.assertIsNone(installments.normalise(empty))

    def test_months_typed_as_text(self):
        self.assertEqual(installments.normalise({"months": " 12 ", "down": "1"})["months"], 12)

    def test_junk_months_do_not_crash(self):
        plan = installments.normalise({"months": "six", "monthly": "100"})
        self.assertEqual(plan["months"], 0)


class Validating(unittest.TestCase):
    def test_a_good_plan_passes(self):
        self.assertIsNotNone(installments.validate(PLAN))

    def test_no_plan_is_not_an_error(self):
        self.assertIsNone(installments.validate(None))

    def test_a_period_of_zero_is_refused(self):
        with self.assertRaises(installments.InstallmentError):
            installments.validate({"months": 0, "monthly": "100"})

    def test_an_absurd_period_is_refused(self):
        """A four-digit month count is a typo, not an offer."""
        with self.assertRaises(installments.InstallmentError) as ctx:
            installments.validate({"months": 5000, "monthly": "100"})
        self.assertIn("longer than any plan", str(ctx.exception))

    def test_a_negative_amount_is_refused(self):
        with self.assertRaises(installments.InstallmentError):
            installments.validate({"months": 6, "monthly": "-100"})

    def test_a_plan_that_collects_nothing_is_refused(self):
        with self.assertRaises(installments.InstallmentError) as ctx:
            installments.validate({"months": 6, "down": "0", "monthly": "0"})
        self.assertIn("not a plan", str(ctx.exception))

    def test_a_deposit_only_plan_is_allowed(self):
        """Paid up front over an agreed period, with nothing monthly."""
        self.assertIsNotNone(installments.validate({"months": 1, "down": "500"}))


class Arithmetic(unittest.TestCase):
    def test_the_financed_total(self):
        self.assertEqual(installments.financed_total(PLAN, 2), Decimal("23000.00"))

    def test_no_plan_totals_nothing(self):
        self.assertEqual(installments.financed_total(None, 2), Decimal("0"))

    def test_the_surcharge_is_what_the_plan_costs_extra(self):
        self.assertEqual(installments.surcharge(PLAN, Decimal("20000"), 2),
                         Decimal("3000.00"))

    def test_a_plan_cheaper_than_cash_shows_negative(self):
        """Unusual but legitimate -- a promotion. It must not be hidden."""
        self.assertLess(installments.surcharge(PLAN, Decimal("30000"), 2), 0)

    def test_each_part_is_rounded_before_multiplying(self):
        """Matches how every other figure on the receipt is built up."""
        plan = {"months": 3, "down": "0.005", "monthly": "0.005"}
        self.assertEqual(installments.financed_total(plan, 2), Decimal("0.04"))

    def test_describe_reads_like_a_sentence(self):
        text = installments.describe(PLAN, lambda v: f"{v:,.2f}")
        self.assertEqual(text, "5,000.00 down, then 6 × 3,000.00")

    def test_describe_with_no_deposit(self):
        text = installments.describe({"months": 4, "monthly": "250"},
                                     lambda v: f"{v:,.2f}")
        self.assertEqual(text, "4 × 250.00")

    def test_describe_of_nothing_is_empty(self):
        self.assertEqual(installments.describe(None, str), "")


class Scope(unittest.TestCase):
    def test_a_whole_order_plan(self):
        self.assertEqual(
            installments.scope_of({"installment": PLAN, "items": [{}]}), "order")

    def test_per_line_plans(self):
        self.assertEqual(
            installments.scope_of({"items": [{"installment": PLAN}, {}]}), "line")

    def test_no_plans_at_all(self):
        self.assertEqual(installments.scope_of({"items": [{}, {}]}), "")


class BothScopesAreRefused(unittest.TestCase):
    """The exclusivity rule, enforced in the model rather than only in the UI."""

    def test_an_order_plan_and_a_line_plan_together_are_refused(self):
        with self.assertRaises(installments.InstallmentError) as ctx:
            installments.scope_of({"installment": PLAN,
                                   "items": [{"installment": PLAN}]})
        self.assertIn("one or the other", str(ctx.exception))

    def test_the_message_says_how_many_lines_are_involved(self):
        with self.assertRaises(installments.InstallmentError) as ctx:
            installments.scope_of({"installment": PLAN,
                                   "items": [{"installment": PLAN},
                                             {"installment": PLAN}, {}]})
        self.assertIn("2 line", str(ctx.exception))

    def test_collect_refuses_too_rather_than_picking_one(self):
        with self.assertRaises(installments.InstallmentError):
            installments.collect({"installment": PLAN,
                                  "items": [{"installment": PLAN}]})


class Collecting(unittest.TestCase):
    def test_several_line_plans_are_summed(self):
        data = {"items": [
            {"installment": {"months": 3, "down": "100", "monthly": "50"}},
            {"installment": {"months": 6, "down": "200", "monthly": "75"}},
        ]}
        _, rows, totals = installments.collect(data, decimals=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(totals["down"], Decimal("300.00"))
        self.assertEqual(totals["monthly"], Decimal("125.00"))

    def test_the_period_is_the_longest_not_the_sum(self):
        """Two 6-month plans running side by side take six months, not twelve."""
        data = {"items": [
            {"installment": {"months": 6, "monthly": "50"}},
            {"installment": {"months": 6, "monthly": "75"}},
        ]}
        _, _, totals = installments.collect(data, decimals=2)
        self.assertEqual(totals["months"], 6)

    def test_rows_remember_which_line_they_came_from(self):
        data = {"items": [{}, {"installment": PLAN}]}
        _, rows, _ = installments.collect(data, decimals=2)
        self.assertEqual(rows[0][0], 1)

    def test_an_order_plan_has_no_line_index(self):
        _, rows, _ = installments.collect({"installment": PLAN, "items": []},
                                          decimals=2)
        self.assertIsNone(rows[0][0])


class OnTheReceipt(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-inst-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def enable(self, on=True):
        config.update_app_settings({"installments": {"enabled": on}})
        receipt_render.clear_template_cache()

    def line(self, **kw):
        base = {"sku": "A", "desc": "Thing", "serial": "", "qty": 1,
                "price": "20000", "discount": "0", "tax": "0", "warranty": ""}
        base.update(kw)
        return base

    def render(self, items, plan=None):
        templates = receipt_render.load_templates()
        settings = config.load_app_settings()
        data = {"invoice_no": "INV-W1", "date": "1 Jan 2026",
                "customer_name": "Ada", "customer_phone": "", "customer_email": "",
                "items": items, "receipt_type": "Online", "shipping": 0}
        if plan:
            data["installment"] = plan
        return receipt_render.render_receipt(
            data, templates, strings=config.load_strings(),
            currency=settings.get("currency"), tax_config=settings.get("tax"),
            fields=config.load_fields(),
            show_installments=settings.get("installments", {}).get("enabled", False))

    def test_nothing_shows_while_the_feature_is_off(self):
        self.enable(False)
        html = self.render([self.line()], PLAN)
        self.assertNotIn("instalment", html.lower())

    def test_a_whole_order_plan_is_printed(self):
        self.enable()
        html = self.render([self.line()], PLAN)
        self.assertIn("Down payment", html)
        self.assertIn("Total if paid in instalments", html)

    def test_the_financed_figure_appears(self):
        self.enable()
        html = self.render([self.line()], PLAN)
        self.assertIn("23,000.00", html)

    def test_the_period_is_shown_with_the_monthly_amount(self):
        self.enable()
        html = self.render([self.line()], PLAN)
        self.assertIn("× 6", html)

    def test_a_line_plan_rides_under_its_description(self):
        self.enable()
        html = self.render([self.line(installment={"months": 3, "monthly": "500"})])
        self.assertIn("Instalment plan", html)
        self.assertIn("3 ×", html)

    def test_a_line_plan_does_not_disturb_a_line_without_one(self):
        self.enable()
        html = self.render([self.line(desc="Financed",
                                      installment={"months": 3, "monthly": "500"}),
                            self.line(desc="Paid outright")])
        self.assertIn("Paid outright", html)

    def test_the_warranty_and_the_plan_can_share_the_line(self):
        self.enable()
        html = self.render([self.line(warranty="12 Months Limited Warranty",
                                      installment={"months": 3, "monthly": "500"})])
        self.assertIn("12 Months", html)
        self.assertIn("Instalment plan", html)


class TheCashTotalIsUnchanged(OnTheReceipt):
    """The settled answer: financing does not change what the goods cost."""

    def total_of(self, html):
        import re
        block = html.split("totals-table")[1]
        figures = re.findall(r"[\d,]+\.\d\d", block)
        return figures

    def test_the_receipt_total_is_the_cash_price(self):
        self.enable()
        with_plan = self.render([self.line()], PLAN)
        self.enable(False)
        without = self.render([self.line()])
        self.assertIn("20,000.00", with_plan)
        self.assertIn("20,000.00", without)

    def test_the_plan_does_not_move_the_tax_base(self):
        """Tax applies to the goods; financing them is a separate arrangement."""
        self.enable()
        taxed = self.render([self.line(tax="1000")], PLAN)
        self.enable(False)
        plain = self.render([self.line(tax="1000")])
        # The taxes row is identical either way.
        self.assertIn("1,000.00", taxed)
        self.assertIn("1,000.00", plain)

    def test_the_financed_total_is_labelled_as_such(self):
        """A customer must not read it as the price of the goods."""
        self.enable()
        html = self.render([self.line()], PLAN)
        self.assertIn("Total if paid in instalments", html)


class Settings(unittest.TestCase):
    def test_it_is_off_by_default(self):
        self.assertFalse(
            config.default_app_settings()["installments"]["enabled"])

    def test_the_section_must_be_an_object(self):
        settings = config.default_app_settings()
        settings["installments"] = "on"
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(settings, "appsettings.json")
        self.assertEqual(ctx.exception.key, "installments")

    def test_enabled_must_be_a_boolean(self):
        settings = config.default_app_settings()
        settings["installments"]["enabled"] = "yes"
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(settings, "appsettings.json")
        self.assertEqual(ctx.exception.key, "installments.enabled")


class TheFormCarriesPlans(unittest.TestCase):
    """The exclusivity rule holds in the app, not only in installments.py."""

    def setUp(self):
        import tkinter as tk
        import main
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-inst-ui-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)
        config.update_app_settings({"installments": {"enabled": True}})
        receipt_render.clear_template_cache()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = main.ReceiptApp(self.root)

    def tearDown(self):
        self.root.destroy()
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def add_row(self, **kw):
        import tkinter as tk
        item = {"sku": "A", "desc": "Thing", "serial": "", "qty": "1",
                "price": "10", "discount": "0", "tax": "0", "warranty": ""}
        item.update(kw)
        self.app.items_tree.insert("", tk.END, values=self.app.item_to_row(item))

    def test_a_plan_survives_the_tree_round_trip(self):
        """The tree stores strings, so a plan has to travel as JSON."""
        plan = {"months": 6, "down": "5000", "monthly": "3000"}
        self.add_row(installment=plan)
        row = self.app.items_tree.get_children()[0]
        back = self.app.row_to_item(self.app.items_tree.item(row)["values"])
        self.assertEqual(back["installment"], plan)

    def test_a_line_without_a_plan_gains_no_empty_key(self):
        self.add_row()
        row = self.app.items_tree.get_children()[0]
        back = self.app.row_to_item(self.app.items_tree.item(row)["values"])
        self.assertNotIn("installment", back)

    def test_lines_with_plans_are_counted(self):
        self.add_row(installment={"months": 3, "monthly": "10"})
        self.add_row()
        self.assertEqual(self.app.lines_with_plans(), 1)

    def test_an_order_plan_is_refused_while_lines_have_their_own(self):
        infos = []
        import main as main_module
        original = main_module.messagebox.showinfo
        main_module.messagebox.showinfo = lambda t, m, **k: infos.append(m)
        try:
            self.add_row(installment={"months": 3, "monthly": "10"})
            self.app.edit_order_plan()
        finally:
            main_module.messagebox.showinfo = original
        self.assertTrue(infos, "the user must be told why")
        self.assertIn("one whole-order plan or one per line", infos[0])
        self.assertEqual(self.app.order_plan, {},
                         "no order plan may have been set")

    def test_units_and_a_plan_coexist_on_one_row(self):
        """Both are structured values sharing the tree's string storage."""
        self.add_row(units=[{"serial": "SN1"}],
                     installment={"months": 3, "monthly": "10"})
        row = self.app.items_tree.get_children()[0]
        back = self.app.row_to_item(self.app.items_tree.item(row)["values"])
        self.assertEqual(back["units"], [{"serial": "SN1"}])
        self.assertEqual(back["installment"]["months"], 3)

    def test_a_corrupt_plan_on_a_row_does_not_break_reading_it(self):
        import tkinter as tk
        keys = self.app.tree_keys()
        values = ["" for _ in keys]
        values[keys.index("installment")] = "{not json"
        values[keys.index("desc")] = "Thing"
        self.app.items_tree.insert("", tk.END, values=values)
        row = self.app.items_tree.get_children()[0]
        back = self.app.row_to_item(self.app.items_tree.item(row)["values"])
        self.assertEqual(back["desc"], "Thing")
        self.assertNotIn("installment", back)


if __name__ == "__main__":
    unittest.main()
