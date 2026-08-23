"""Stage 2 — template/config foundation gate.

The Stage 2 promise is that the app becomes template-driven **without changing
what it produces**. tests/test_stage0.py already guards the rendered bytes; this
module covers the rest of PLAN-generalization.md §"Stage 2" verify list:

  * an old-schema appsettings.json migrates with a .bak and an IDENTICAL next
    invoice number (the one thing that must never shift on a legal document);
  * a typo'd placeholder and an unclosed {{#if}} each fail at load, naming the
    file and the line;
  * editing a template actually changes the output;
  * the shipped templates reference only keys BLOCK_CONTEXTS declares;
  * the money contract: Decimal throughout, rounded lines that sum to the total,
    at 0 and 2 decimals.

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
import template_engine     # noqa: E402


class TempAppDir(unittest.TestCase):
    """Runs each test against a throwaway APP_DIR, restoring the real one after."""

    def setUp(self):
        self._original_app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-stage2-")
        config.set_app_dir(self.dir)
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._original_app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_settings(self, data):
        path = os.path.join(self.dir, "appsettings.json")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
        return path


class InvoiceNumberSurvivesMigration(TempAppDir):
    """Numbering must not shift when the config schema changes underneath it."""

    V1_SETTINGS = {
        "company": {"name": "Acme", "address": "1 Road", "phone": "1",
                    "email": "a@b.c", "logo_path": "logo.png"},
        "signing": {"enabled": False, "private_key_path": "", "certificate_path": "",
                    "key_passphrase": "", "signer_name": "Acme",
                    "reason": "", "location": "", "tsa_url": ""},
    }

    def _seed_invoices(self):
        invoices = os.path.join(self.dir, "invoices")
        os.makedirs(invoices, exist_ok=True)
        for name in ("INV-W1001.pdf", "INV-W1007-15 Jan 2026-Ada.pdf",
                     "INV-W1003.pdf", "INV-S1002.pdf", "INV-1005.pdf"):
            open(os.path.join(invoices, name), "wb").close()

    def test_next_number_identical_before_and_after_migration(self):
        self._seed_invoices()
        self.write_settings(dict(self.V1_SETTINGS))

        before_online = receipt_service.get_next_invoice_number("INV-W")
        before_store = receipt_service.get_next_invoice_number("INV-S")

        settings = config.load_app_settings()          # performs the migration
        self.assertEqual(settings[config.SCHEMA_VERSION_KEY], config.SCHEMA_VERSION)

        self.assertEqual(receipt_service.get_next_invoice_number("INV-W"), before_online)
        self.assertEqual(receipt_service.get_next_invoice_number("INV-S"), before_store)

    def test_the_numbers_are_the_expected_ones(self):
        # Guards the assertion above from passing because both sides are wrong:
        # online counts legacy unlettered INV-#### too, so 1007 wins; in-store
        # sees only INV-S1002.
        self._seed_invoices()
        self.write_settings(dict(self.V1_SETTINGS))
        config.load_app_settings()
        self.assertEqual(receipt_service.get_next_invoice_number("INV-W"), 1008)
        self.assertEqual(receipt_service.get_next_invoice_number("INV-S"), 1003)

    def test_migration_leaves_a_backup(self):
        self.write_settings(dict(self.V1_SETTINGS))
        config.load_app_settings()
        backups = [n for n in os.listdir(self.dir) if n.endswith(".bak")]
        self.assertEqual(len(backups), 1, f"expected one .bak, got {backups}")

    def test_empty_series_still_starts_at_the_configured_number(self):
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        self.assertEqual(receipt_service.get_next_invoice_number("INV-W"),
                         config.INVOICE_START_NUMBER)


class TemplateFailuresAreLoud(TempAppDir):
    """A broken template must fail at load with the file and line, not blank a field."""

    def _install_and_break(self, name, contents):
        config.install_default_templates()
        path = os.path.join(self.dir, "Templates", name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(contents)
        receipt_render.clear_template_cache()

    def test_typoed_placeholder_is_rejected(self):
        self._install_and_break("totals_row.html",
                                '<tr><td>{{labell}}</td><td>{{amount}}</td></tr>\n')
        with self.assertRaises(template_engine.TemplateError) as ctx:
            receipt_render.load_templates(force=True)
        self.assertEqual(ctx.exception.filename, "totals_row.html")
        self.assertEqual(ctx.exception.line, 1)
        self.assertIn("unknown placeholder", ctx.exception.message)

    def test_unclosed_if_is_rejected_with_its_line(self):
        self._install_and_break(
            "receipt_info.html",
            "<div>\n<span>{{invoice_no}}</span>\n{{#if customer_phone}}\n<p>x</p>\n")
        with self.assertRaises(template_engine.TemplateError) as ctx:
            receipt_render.load_templates(force=True)
        self.assertEqual(ctx.exception.filename, "receipt_info.html")
        self.assertEqual(ctx.exception.line, 3)

    def test_deleted_template_is_restored_from_the_bundled_defaults(self):
        """Losing a template should self-heal, not brick the app."""
        config.install_default_templates()
        target = os.path.join(self.dir, "Templates", "totals.html")
        os.remove(target)
        receipt_render.clear_template_cache()

        receipt_render.load_templates(force=True)      # must not raise
        self.assertTrue(os.path.isfile(target), "template was not reinstalled")

    def test_missing_template_with_no_bundled_copy_is_reported_by_name(self):
        """The unrecoverable case still fails loudly rather than rendering a hole."""
        original_bundled = config.BUNDLED_TEMPLATES_DIR
        original_resource = config.RESOURCE_DIR
        empty = os.path.join(self.dir, "empty-resources")
        os.makedirs(empty, exist_ok=True)
        try:
            config.BUNDLED_TEMPLATES_DIR = os.path.join(empty, "Templates")
            config.RESOURCE_DIR = empty
            receipt_render.clear_template_cache()
            with self.assertRaises(template_engine.TemplateError) as ctx:
                receipt_render.load_templates(force=True)
            self.assertIn("missing", str(ctx.exception))
        finally:
            config.BUNDLED_TEMPLATES_DIR = original_bundled
            config.RESOURCE_DIR = original_resource
            receipt_render.clear_template_cache()


class TemplatesDriveOutput(TempAppDir):
    """If editing a template does not change the receipt, it is not template-driven."""

    def _render(self):
        return receipt_render.build_html(
            "INV-W1", "1 Jan 2026", "Ada", "", "",
            [{"sku": "A", "desc": "Thing", "serial": "", "qty": 1,
              "price": 10, "discount": 0, "tax": 0, "warranty": ""}],
            "Online", 0)

    def test_editing_styles_changes_output(self):
        config.install_default_templates()
        before = self._render()

        path = os.path.join(self.dir, "Templates", "styles.css")
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write("\n    .stage2-marker { color: rebeccapurple; }")
        receipt_render.clear_template_cache()

        after = self._render()
        self.assertNotEqual(before, after)
        self.assertIn("stage2-marker", after)

    def test_editing_a_block_changes_output(self):
        config.install_default_templates()
        path = os.path.join(self.dir, "Templates", "receipt_info.html")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(source.replace("SALES RECEIPT", "TAX INVOICE"))
        receipt_render.clear_template_cache()

        self.assertIn("TAX INVOICE", self._render())

    def test_first_run_records_hashes_at_copy_time(self):
        copied = config.install_default_templates()
        self.assertIn("base.html", copied)

        manifest_path = os.path.join(self.dir, "Templates", config.INSTALLED_MANIFEST)
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        installed = os.path.join(self.dir, "Templates", "base.html")
        self.assertEqual(manifest["base.html"]["hash"], config.file_digest(installed))

        # After the user edits the file, the recorded hash must NOT follow it --
        # that difference is exactly how an upgrade tells "edited" from "default".
        with open(installed, "a", encoding="utf-8", newline="\n") as f:
            f.write("<!-- mine -->\n")
        self.assertNotEqual(manifest["base.html"]["hash"], config.file_digest(installed))

    def test_install_never_overwrites_a_users_edit(self):
        config.install_default_templates()
        path = os.path.join(self.dir, "Templates", "terms.html")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("<div>my own terms</div>\n")

        config.install_default_templates()          # a later launch
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "<div>my own terms</div>\n")


class ShippedTemplatesAreClean(unittest.TestCase):
    def test_defaults_reference_only_declared_keys(self):
        """Guards against shipping a template the linter would reject on a user's machine."""
        receipt_render.clear_template_cache()
        templates = receipt_render.load_templates(force=True)
        for name, template in templates.items():
            allowed = receipt_render.BLOCK_CONTEXTS[name]
            unknown = {k for k in template.keys
                       if k not in allowed and k.split(".", 1)[0] not in allowed}
            self.assertEqual(unknown, set(), f"{name} references undeclared keys")

    def test_every_declared_block_exists(self):
        for name in receipt_render.BLOCK_CONTEXTS:
            path = config.branding_template_path(name)
            self.assertTrue(os.path.isfile(path), f"missing shipped template {name}")


class MoneyContract(unittest.TestCase):
    """Decimal end to end; rounded lines sum to the printed total."""

    def test_amounts_never_become_floats(self):
        self.assertIsInstance(receipt_render.to_decimal("8500.00"), Decimal)
        self.assertIsInstance(receipt_render.quantize(1.005), Decimal)

    def test_float_input_does_not_inherit_binary_noise(self):
        # Decimal(0.1) is 0.1000000000000000055511151231257827; via str it is not.
        self.assertEqual(receipt_render.to_decimal(0.1), Decimal("0.1"))

    def test_string_amounts_round_trip_exactly(self):
        for raw in ("0.00", "8500.00", "1234567.89", "0.01"):
            self.assertEqual(str(receipt_render.quantize(raw)), raw)

    def test_half_up_rounding(self):
        self.assertEqual(receipt_render.quantize("0.125"), Decimal("0.13"))
        self.assertEqual(receipt_render.quantize("0.135"), Decimal("0.14"))

    def test_format_amount_grouping(self):
        self.assertEqual(receipt_render.format_amount("22450"), "Rs. 22450.00")
        self.assertEqual(receipt_render.format_amount("22450", group=True), "Rs. 22,450.00")

    def test_format_amount_at_zero_decimals(self):
        original = receipt_render.AMOUNT_DECIMALS
        try:
            receipt_render.AMOUNT_DECIMALS = 0
            self.assertEqual(receipt_render.format_amount("1234.6", group=True), "Rs. 1,235")
        finally:
            receipt_render.AMOUNT_DECIMALS = original

    def test_bad_input_degrades_to_zero_rather_than_crashing(self):
        self.assertEqual(receipt_render.quantize("not a number"), Decimal("0.00"))
        self.assertEqual(receipt_render.quantize(None), Decimal("0.00"))

    def _totals_from(self, html):
        import re
        rows = re.findall(r'<td align="right">(?:- )?Rs\. ([\d,]+\.\d\d)</td>', html)
        return [Decimal(r.replace(",", "")) for r in rows]

    def test_lines_visibly_sum_to_the_total(self):
        items = [
            {"sku": "", "desc": "a", "serial": "", "qty": 3, "price": "1.005",
             "discount": 0, "tax": 0, "warranty": ""},
            {"sku": "", "desc": "b", "serial": "", "qty": 7, "price": "2.004",
             "discount": 0, "tax": 0, "warranty": ""},
        ]
        html = receipt_render.build_html("INV-W1", "1 Jan 2026", "Ada", "", "",
                                         items, "Online", "10.00")
        amounts = self._totals_from(html)
        subtotal, shipping, total = amounts[0], amounts[-2], amounts[-1]
        self.assertEqual(subtotal + shipping, total,
                         "the printed subtotal and shipping must add up to the printed total")

    def test_totals_breakdown_hidden_when_there_is_nothing_to_break_down(self):
        items = [{"sku": "", "desc": "a", "serial": "", "qty": 1, "price": "10.00",
                  "discount": 0, "tax": 0, "warranty": ""}]
        html = receipt_render.build_html("INV-W1", "1 Jan 2026", "Ada", "", "",
                                         items, "Online", 0)
        self.assertNotIn("Subtotal", html)
        self.assertIn("TOTAL", html)

    def test_optional_columns_appear_only_when_used(self):
        base = {"sku": "", "desc": "a", "serial": "", "qty": 1, "price": "10.00",
                "discount": 0, "tax": 0, "warranty": ""}
        plain = receipt_render.build_html("I", "d", "c", "", "", [dict(base)], "Online", 0)
        self.assertNotIn("<th>Discount</th>", plain)
        self.assertNotIn("<th>Tax</th>", plain)

        taxed = receipt_render.build_html("I", "d", "c", "", "",
                                          [dict(base, tax="1.00")], "Online", 0)
        self.assertIn("<th>Tax</th>", taxed)
        self.assertNotIn("<th>Discount</th>", taxed)


class FontEmbedding(unittest.TestCase):
    """Default-off, and never lets a user string reach a CSS context raw."""

    def test_no_font_configured_emits_nothing(self):
        self.assertEqual(receipt_render.build_font_faces({}), "")
        self.assertEqual(
            receipt_render.build_font_faces(
                {"family": "", "files": [], "fallback": "Helvetica"}), "")

    def test_family_without_files_emits_nothing(self):
        self.assertEqual(receipt_render.build_font_faces({"family": "X", "files": []}), "")

    def test_missing_font_file_is_skipped_rather_than_half_written(self):
        self.assertEqual(
            receipt_render.build_font_faces(
                {"family": "X", "files": ["nope.woff2"]}), "")

    def test_css_hostile_family_name_is_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            font = os.path.join(tmp, "f.woff2")
            with open(font, "wb") as f:
                f.write(b"\x00\x01fake")
            css = receipt_render.build_font_faces(
                {"family": "Evil'} body{display:none}/*", "files": [font],
                 "fallback": "Arial"})
        self.assertNotIn("display:none", css)
        self.assertNotIn("'}", css)
        self.assertIn("@font-face", css)
        self.assertIn("base64,", css)

    def test_default_config_leaves_the_document_unchanged(self):
        settings = config.default_app_settings()
        self.assertEqual(receipt_render.build_font_faces(settings["fonts"]), "")


if __name__ == "__main__":
    unittest.main()
