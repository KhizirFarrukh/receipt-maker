"""Stage 3 — configurable primitives (currency, dates, receipt types, tax, terms).

Covers PLAN-generalization.md §"Stage 3" verify list: switch currency to $ and to
0-decimal/no-group, switch date format, disable terms, add a receipt type, add a
15% tax row both inclusive and exclusive -- all reflected; and lines sum to the
total in every rounding config.

The golden gate (tests/test_stage0.py) proves the *default* output did not move;
this module proves the knobs actually do something.

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
import receipt_render      # noqa: E402
import receipt_service     # noqa: E402

import gate_env  # noqa: E402

USD = {"symbol": "$", "symbol_space": False, "code": "USD", "decimals": 2,
       "position": "prefix", "group_style": "thousand", "negative_style": "minus",
       "group_line_amounts": True}


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


def money(**overrides):
    return dict(USD, **overrides)


class CurrencyFormatting(unittest.TestCase):
    def fmt(self, value, **overrides):
        return receipt_render.format_amount(value, money(**overrides))

    def test_neutral_default_is_dollars(self):
        self.assertEqual(self.fmt("1234.5"), "$1,234.50")

    def test_symbol_and_space(self):
        self.assertEqual(self.fmt("12", symbol="Rs.", symbol_space=True), "Rs. 12.00")
        self.assertEqual(self.fmt("12", symbol="Rs.", symbol_space=False), "Rs.12.00")

    def test_suffix_position(self):
        self.assertEqual(
            self.fmt("12", symbol="kr", symbol_space=True, position="suffix"), "12.00 kr")

    def test_zero_decimals(self):
        self.assertEqual(self.fmt("1234.6", decimals=0), "$1,235")
        self.assertEqual(self.fmt("1234.4", decimals=0), "$1,234")

    def test_four_decimals(self):
        self.assertEqual(self.fmt("1.23456", decimals=4), "$1.2346")

    def test_group_style_none(self):
        self.assertEqual(self.fmt("1234567.89", group_style="none"), "$1234567.89")

    def test_group_style_thousand(self):
        self.assertEqual(self.fmt("1234567.89", group_style="thousand"), "$1,234,567.89")

    def test_group_style_indian(self):
        self.assertEqual(self.fmt("1234567.89", group_style="indian"), "$12,34,567.89")
        self.assertEqual(self.fmt("123456.00", group_style="indian"), "$1,23,456.00")

    def test_four_digit_grouping(self):
        """The 1000-vs-1,000 boundary the plan calls out explicitly."""
        self.assertEqual(self.fmt("999"), "$999.00")
        self.assertEqual(self.fmt("1000"), "$1,000.00")
        self.assertEqual(self.fmt("1000", group_style="none"), "$1000.00")

    def test_negative_minus(self):
        self.assertEqual(self.fmt("-5", negative_style="minus"), "-$5.00")

    def test_negative_parentheses(self):
        self.assertEqual(self.fmt("-5", negative_style="parentheses"), "($5.00)")

    def test_negative_zero_is_not_shown_as_negative(self):
        self.assertEqual(self.fmt("-0.001"), "$0.00")

    def test_empty_symbol(self):
        self.assertEqual(self.fmt("12", symbol=""), "12.00")

    def test_group_suppressed_for_line_cells(self):
        self.assertEqual(
            receipt_render.format_amount("17000", money(), group=False), "$17000.00")

    def test_group_digits_directly(self):
        self.assertEqual(receipt_render.group_digits("1234567", "thousand"), "1,234,567")
        self.assertEqual(receipt_render.group_digits("1234567", "indian"), "12,34,567")
        self.assertEqual(receipt_render.group_digits("12", "thousand"), "12")


class CurrencyInReceipts(unittest.TestCase):
    ITEMS = [{"sku": "A", "desc": "Thing", "serial": "", "qty": 2,
              "price": "8500.00", "discount": 0, "tax": 0, "warranty": ""}]

    def render(self, currency, **kwargs):
        return receipt_render.render_receipt(
            {"invoice_no": "INV-W1", "date": "1 Jan 2026", "customer_name": "Ada",
             "items": self.ITEMS, "receipt_type": "Online", "shipping": "500.00"},
            receipt_render.load_templates(), currency=currency, **kwargs)

    def test_switching_to_dollars_changes_every_amount(self):
        html = self.render(money())
        self.assertIn("$17,000.00", html)
        self.assertIn("$17,500.00", html)      # total
        self.assertNotIn("Rs.", html)

    def test_group_line_amounts_off_keeps_line_cells_ungrouped(self):
        html = self.render(money(group_line_amounts=False))
        self.assertIn("$17000.00", html, "line cell should not be grouped")
        self.assertIn("$17,500.00", html, "totals should still be grouped")

    def test_zero_decimal_currency(self):
        html = self.render(money(decimals=0, symbol="¥", code="JPY"))
        self.assertIn("¥17,000", html)
        self.assertNotIn(".00", html)


class RoundingIsSelfConsistent(unittest.TestCase):
    """Whatever the precision, the printed figures must add up on the page."""

    def _amounts(self, html):
        import re
        return [Decimal(m.replace(",", "")) for m in
                re.findall(r'<td align="right">\$?([\d,]+(?:\.\d+)?)</td>', html)]

    def _render(self, decimals, prices):
        items = [{"sku": "", "desc": f"i{n}", "serial": "", "qty": 3, "price": p,
                  "discount": 0, "tax": 0, "warranty": ""} for n, p in enumerate(prices)]
        return receipt_render.render_receipt(
            {"invoice_no": "I", "date": "d", "customer_name": "c",
             "items": items, "receipt_type": "Online", "shipping": "1.005"},
            receipt_render.load_templates(),
            currency=money(decimals=decimals, symbol=""))

    def test_lines_sum_to_total_at_two_decimals(self):
        html = self._render(2, ["1.005", "2.004", "3.006"])
        amounts = self._amounts(html)
        subtotal, shipping, total = amounts[0], amounts[-2], amounts[-1]
        self.assertEqual(subtotal + shipping, total)

    def test_lines_sum_to_total_at_zero_decimals(self):
        html = self._render(0, ["1.5", "2.5", "3.4"])
        amounts = self._amounts(html)
        subtotal, shipping, total = amounts[0], amounts[-2], amounts[-1]
        self.assertEqual(subtotal + shipping, total)


class DocumentTax(unittest.TestCase):
    ITEMS = [{"sku": "", "desc": "Thing", "serial": "", "qty": 1,
              "price": "100.00", "discount": 0, "tax": 0, "warranty": ""}]

    def render(self, tax, items=None, shipping=0):
        return receipt_render.render_receipt(
            {"invoice_no": "I", "date": "d", "customer_name": "c",
             "items": items or self.ITEMS, "receipt_type": "Online",
             "shipping": shipping},
            receipt_render.load_templates(),
            currency=money(symbol=""), tax_config=tax)

    def test_no_rows_adds_nothing(self):
        rows, added = receipt_render.compute_tax_rows(
            Decimal("100"), Decimal("0"), {"mode": "exclusive", "rows": []}, 2)
        self.assertEqual((rows, added), ([], Decimal("0")))

    def test_exclusive_percent_is_added_on_top(self):
        html = self.render({"mode": "exclusive",
                            "rows": [{"label": "VAT 15%", "type": "percent", "value": 15}]})
        self.assertIn("VAT 15%", html)
        self.assertIn("15.00", html)
        self.assertIn("115.00", html, "total should be 100 + 15")

    def test_inclusive_percent_is_reported_not_added(self):
        html = self.render({"mode": "inclusive",
                            "rows": [{"label": "VAT 15%", "type": "percent", "value": 15}]})
        self.assertIn("(included)", html)
        self.assertIn("100.00", html, "total must stay at the quoted price")
        self.assertNotIn("115.00", html)

    def test_inclusive_amount_is_backed_out_correctly(self):
        rows, added = receipt_render.compute_tax_rows(
            Decimal("115"), Decimal("0"),
            {"mode": "inclusive", "rows": [{"label": "VAT", "type": "percent", "value": 15}]}, 2)
        self.assertEqual(added, Decimal("0"))
        self.assertEqual(rows[0][1], Decimal("15.00"),
                         "115 inclusive of 15% contains exactly 15.00 of tax")

    def test_several_inclusive_rows_share_one_back_out(self):
        # 10% + 5% inclusive of 115 -> net 100, so 10.00 and 5.00, not 10.45/5.23.
        rows, _ = receipt_render.compute_tax_rows(
            Decimal("115"), Decimal("0"),
            {"mode": "inclusive", "rows": [
                {"label": "A", "type": "percent", "value": 10},
                {"label": "B", "type": "percent", "value": 5}]}, 2)
        self.assertEqual([r[1] for r in rows], [Decimal("10.00"), Decimal("5.00")])

    def test_fixed_row(self):
        rows, added = receipt_render.compute_tax_rows(
            Decimal("100"), Decimal("0"),
            {"mode": "exclusive", "rows": [{"label": "Levy", "type": "fixed", "value": "2.50"}]}, 2)
        self.assertEqual(rows[0][1], Decimal("2.50"))
        self.assertEqual(added, Decimal("2.50"))

    def test_discount_applies_before_tax_by_default(self):
        rows, _ = receipt_render.compute_tax_rows(
            Decimal("100"), Decimal("20"),
            {"mode": "exclusive", "rows": [{"label": "VAT", "type": "percent", "value": 10}]}, 2)
        self.assertEqual(rows[0][1], Decimal("8.00"), "10% of 80, not of 100")

    def test_applies_to_subtotal_ignores_the_discount(self):
        rows, _ = receipt_render.compute_tax_rows(
            Decimal("100"), Decimal("20"),
            {"mode": "exclusive", "rows": [
                {"label": "VAT", "type": "percent", "value": 10,
                 "applies_to": "subtotal"}]}, 2)
        self.assertEqual(rows[0][1], Decimal("10.00"))


class TermsPage(unittest.TestCase):
    ITEMS = [{"sku": "", "desc": "Thing", "serial": "", "qty": 1,
              "price": "1.00", "discount": 0, "tax": 0, "warranty": ""}]

    def render(self, terms):
        return receipt_render.render_receipt(
            {"invoice_no": "I", "date": "d", "customer_name": "c",
             "items": self.ITEMS, "receipt_type": "Online", "shipping": 0},
            receipt_render.load_templates(), currency=money(), terms=terms)

    # Matched against the terms *content*, not the class name: styles.css keeps
    # its .policy-page rules either way, so the selector is no evidence the page
    # was rendered.
    MARKER = '<div class="policy-page">'

    def test_enabled_includes_the_policy_page(self):
        self.assertIn(self.MARKER, self.render(True))

    def test_disabled_removes_it(self):
        self.assertNotIn(self.MARKER, self.render(False))

    def test_disabling_leaves_no_stray_whitespace(self):
        html = self.render(False)
        self.assertTrue(html.endswith("</table>\n\n</body>\n</html>"),
                        f"disabled terms left blank lines behind: {html[-40:]!r}")


class ReceiptTypes(unittest.TestCase):
    def test_defaults_preserve_the_existing_codes(self):
        """Changing W/S would orphan every INV-W####/INV-S#### file already issued."""
        types = {t["label"]: t["code"] for t in config.receipt_types()}
        self.assertEqual(types, {"Online": "W", "In Store": "S"})

    def test_prefix_comes_from_config(self):
        self.assertEqual(receipt_service.get_invoice_prefix("Online"), "INV-W")
        self.assertEqual(receipt_service.get_invoice_prefix("In Store"), "INV-S")

    def test_unknown_label_falls_back_to_the_first_type(self):
        self.assertEqual(config.receipt_type_by_label("Nonsense")["label"], "Online")

    def test_badge_text_is_configurable(self):
        html = receipt_render.render_receipt(
            {"invoice_no": "I", "date": "d", "customer_name": "c",
             "items": [{"sku": "", "desc": "x", "serial": "", "qty": 1, "price": "1",
                        "discount": 0, "tax": 0, "warranty": ""}],
             "receipt_type": "In Store", "shipping": 0},
            receipt_render.load_templates(), currency=money())
        self.assertIn("IN-STORE SALE", html)


class DateFormat(unittest.TestCase):
    def test_default_format(self):
        self.assertEqual(config.date_display_format(), "%d %b %Y")

    def test_configured_format_is_tried_first_when_parsing(self):
        formats = config.date_parse_formats({"date_format": "%Y-%m-%d"})
        self.assertEqual(formats[0], "%Y-%m-%d")
        self.assertEqual(len(set(formats)), len(formats), "no duplicate formats")


class Stage3Validation(unittest.TestCase):
    def settings(self, **overrides):
        s = config.default_app_settings()
        s.update(overrides)
        return s

    def assert_rejects(self, key, settings):
        with self.assertRaises(config.ConfigError, msg=f"{key} should be rejected") as ctx:
            config.validate(settings, "appsettings.json")
        self.assertEqual(ctx.exception.key, key)

    def test_defaults_validate(self):
        config.validate(config.default_app_settings(), "appsettings.json")

    def test_bad_group_style(self):
        s = self.settings()
        s["currency"]["group_style"] = "lakhs"
        self.assert_rejects("currency.group_style", s)

    def test_decimals_out_of_range(self):
        s = self.settings()
        s["currency"]["decimals"] = 9
        self.assert_rejects("currency.decimals", s)

    def test_bad_date_format(self):
        self.assert_rejects("date_format", self.settings(date_format=""))

    def test_empty_receipt_types(self):
        self.assert_rejects("receipt_types", self.settings(receipt_types=[]))

    def test_duplicate_receipt_code(self):
        self.assert_rejects("receipt_types[1].code", self.settings(receipt_types=[
            {"label": "A", "code": "W"}, {"label": "B", "code": "W"}]))

    def test_duplicate_receipt_label(self):
        self.assert_rejects("receipt_types[1].label", self.settings(receipt_types=[
            {"label": "A", "code": "W"}, {"label": "A", "code": "S"}]))

    def test_receipt_code_must_be_filename_safe(self):
        self.assert_rejects("receipt_types[0].code", self.settings(receipt_types=[
            {"label": "A", "code": "W/2"}]))

    def test_two_types_cannot_both_claim_legacy_numbers(self):
        self.assert_rejects("receipt_types", self.settings(receipt_types=[
            {"label": "A", "code": "W", "legacy_unlettered": True},
            {"label": "B", "code": "S", "legacy_unlettered": True}]))

    def test_adding_a_receipt_type_is_accepted(self):
        config.validate(self.settings(receipt_types=[
            {"label": "Online", "code": "W", "badge_text": "ONLINE", "legacy_unlettered": True},
            {"label": "In Store", "code": "S", "badge_text": "IN-STORE"},
            {"label": "Wholesale", "code": "B", "badge_text": "WHOLESALE"}]),
            "appsettings.json")

    def test_bad_tax_mode(self):
        self.assert_rejects("tax.mode", self.settings(tax={"mode": "vat", "rows": []}))

    def test_tax_row_needs_a_label(self):
        self.assert_rejects("tax.rows[0].label", self.settings(
            tax={"mode": "exclusive", "rows": [{"type": "percent", "value": 15}]}))

    def test_tax_percent_over_100(self):
        self.assert_rejects("tax.rows[0].value", self.settings(
            tax={"mode": "exclusive",
                 "rows": [{"label": "X", "type": "percent", "value": 150}]}))

    def test_negative_tax_rejected(self):
        self.assert_rejects("tax.rows[0].value", self.settings(
            tax={"mode": "exclusive",
                 "rows": [{"label": "X", "type": "percent", "value": -1}]}))

    def test_bad_applies_to(self):
        self.assert_rejects("tax.rows[0].applies_to", self.settings(
            tax={"mode": "exclusive", "rows": [
                {"label": "X", "type": "percent", "value": 5, "applies_to": "everything"}]}))

    def test_terms_page_flag_must_be_boolean(self):
        self.assert_rejects("terms_page.enabled", self.settings(terms_page={"enabled": "yes"}))


class MigrationToV3(unittest.TestCase):
    """An existing install must not silently change currency."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rm-stage3-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, data):
        path = os.path.join(self.dir, "appsettings.json")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
        return path

    def test_v2_config_is_seeded_with_the_old_hardcoded_currency(self):
        v2 = config.default_app_settings()
        v2[config.SCHEMA_VERSION_KEY] = 2
        del v2["currency"]
        settings, changed = config.migrate(v2)

        self.assertTrue(changed)
        self.assertEqual(settings[config.SCHEMA_VERSION_KEY], 3)
        self.assertEqual(settings["currency"]["symbol"], "Rs.")
        self.assertTrue(settings["currency"]["symbol_space"])
        self.assertFalse(settings["currency"]["group_line_amounts"],
                         "line amounts were ungrouped before Stage 3 and must stay so")

    def test_a_fresh_config_gets_the_neutral_default(self):
        path = os.path.join(self.dir, "appsettings.json")
        settings = config.load_app_settings(path)
        self.assertEqual(settings["currency"]["symbol"], "$")
        self.assertTrue(settings["currency"]["group_line_amounts"])

    def test_an_explicit_currency_is_never_overwritten(self):
        v2 = config.default_app_settings()
        v2[config.SCHEMA_VERSION_KEY] = 2
        v2["currency"] = dict(USD, symbol="€", code="EUR")
        settings, _ = config.migrate(v2)
        self.assertEqual(settings["currency"]["symbol"], "€")

    def test_v1_config_reaches_v3_in_one_step(self):
        settings, changed = config.migrate({
            "company": {"name": "Acme", "address": "a", "phone": "1",
                        "email": "e@x.c", "logo_path": ""},
        })
        self.assertTrue(changed)
        self.assertEqual(settings[config.SCHEMA_VERSION_KEY], 3)
        self.assertEqual(settings["company"]["name"], "Acme")
        self.assertEqual(settings["currency"]["symbol"], "Rs.")


if __name__ == "__main__":
    unittest.main()
