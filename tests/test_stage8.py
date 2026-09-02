"""Stage 8 polish — TODO.md §5.

Four small things that each remove a reason to edit a file by hand:

* a **filename pattern**, replacing a fixed field list;
* **DUPLICATE** on a reissued receipt;
* the receipt heading as a **config key** rather than a literal in HTML;
* **restore default templates**, the way back from an edit that broke rendering.

The filename pattern carries the one rule worth enforcing: it must contain
`{invoice_no}`. That is the only part of a receipt guaranteed unique, and
without it two receipts on one day for one customer overwrite each other.

Run: python -m unittest discover -s tests
"""
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config              # noqa: E402
import receipt_render      # noqa: E402
import receipt_service     # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


class Stage8TestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-stage8-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def pattern(self, value):
        config.update_app_settings({"invoice": {"filename_pattern": value}})

    def name(self, inv="INV-W1001", date="01 Jan 2026", cust="Ada",
             email="a@b.c", phone="555", receipt_type="Online"):
        return receipt_service.build_pdf_filename(
            inv, date, cust, email, phone, receipt_type)


class TheFilenamePattern(Stage8TestCase):
    def test_no_pattern_keeps_the_existing_names(self):
        """Every install already has filenames; none of them may change."""
        self.assertEqual(self.name(), "INV-W1001-01 Jan 2026-Ada.pdf")

    def test_a_pattern_takes_over(self):
        self.pattern("{invoice_no}_{date}")
        self.assertEqual(self.name(), "INV-W1001_01 Jan 2026.pdf")

    def test_every_token_works(self):
        self.pattern("{invoice_no}-{date}-{name}-{email}-{phone}-{receipt_type}")
        produced = self.name()
        for part in ("INV-W1001", "01 Jan 2026", "Ada", "555", "Online"):
            self.assertIn(part, produced)

    def test_the_invoice_number_alone_is_enough(self):
        self.pattern("{invoice_no}")
        self.assertEqual(self.name(), "INV-W1001.pdf")

    def test_a_blank_value_does_not_leave_a_dangling_separator(self):
        """"INV-1--Ada" and a trailing dash both look like a bug."""
        self.pattern("{invoice_no}-{name}-{phone}")
        self.assertEqual(self.name(cust="", phone=""), "INV-W1001.pdf")

    def test_a_blank_value_in_the_middle_collapses(self):
        self.pattern("{invoice_no}-{name}-{phone}")
        self.assertEqual(self.name(cust=""), "INV-W1001-555.pdf")

    def test_unsafe_characters_are_still_stripped(self):
        self.pattern("{invoice_no}-{name}")
        self.assertNotIn("/", self.name(cust="a/b"))

    def test_the_legacy_field_list_still_drives_the_default(self):
        pattern = receipt_service.filename_pattern()
        self.assertTrue(pattern.startswith("{invoice_no}"))
        self.assertIn("{date}", pattern)


class ThePatternIsValidated(unittest.TestCase):
    def settings(self, pattern):
        settings = config.default_app_settings()
        settings["invoice"]["filename_pattern"] = pattern
        return settings

    def test_a_good_pattern_passes(self):
        config.validate(self.settings("{invoice_no}-{date}"), "appsettings.json")

    def test_an_empty_pattern_passes(self):
        config.validate(self.settings(""), "appsettings.json")

    def test_it_must_contain_the_invoice_number(self):
        """The rule TODO §5 asked for, and the reason for it."""
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(self.settings("{date}-{name}"), "appsettings.json")
        self.assertEqual(ctx.exception.key, "invoice.filename_pattern")
        self.assertIn("overwrite each other", str(ctx.exception))

    def test_an_unknown_placeholder_is_refused_and_the_real_ones_listed(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(self.settings("{invoice_no}-{customer}"),
                            "appsettings.json")
        message = str(ctx.exception)
        self.assertIn("'customer'", message)
        self.assertIn("{invoice_no}", message)

    def test_it_must_be_text(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(self.settings(42), "appsettings.json")
        self.assertEqual(ctx.exception.key, "invoice.filename_pattern")


class TheDocumentTitle(Stage8TestCase):
    def write_string(self, key, value):
        """Set one `strings.json` label. There is no save_strings(); the file
        is the interface, which is the point of it being editable."""
        import json
        strings = config.load_strings()
        strings.setdefault("totals", {})[key] = value
        with open(config.strings_file(), "w", encoding="utf-8") as handle:
            json.dump(strings, handle, indent=2)
        receipt_render.clear_template_cache()

    def render(self, **extra):
        return receipt_render.build_html(
            "INV-W1", "1 Jan 2026", "Ada", "", "",
            [{"sku": "A", "desc": "Thing", "serial": "", "qty": 1, "price": "10",
              "discount": "0", "tax": "0", "warranty": ""}], "Online", 0, **extra)

    def test_the_default_is_unchanged(self):
        self.assertIn("SALES RECEIPT", self.render())

    def test_it_can_be_changed_without_editing_html(self):
        self.write_string("document_title", "TAX INVOICE")
        html = self.render()
        self.assertIn("TAX INVOICE", html)
        self.assertNotIn("SALES RECEIPT", html)


class TheDuplicateNotice(TheDocumentTitle):
    def test_an_ordinary_receipt_carries_none(self):
        body = self.render().split("<body>", 1)[1]
        self.assertNotIn("duplicate-notice", body)

    def test_a_reissue_says_so_on_its_face(self):
        body = self.render(is_duplicate=True).split("<body>", 1)[1]
        self.assertIn("DUPLICATE", body)

    def test_the_wording_is_configurable(self):
        self.write_string("duplicate_notice", "COPY")
        self.assertIn("COPY", self.render(is_duplicate=True))

    def test_it_is_decided_by_the_history_not_by_a_checkbox(self):
        """A second PDF for a number already issued is a second copy, always."""
        import inspect
        source = inspect.getsource(receipt_service.generate)
        self.assertIn("is_duplicate=previous is not None", source)

    def test_history_is_read_once_for_both_questions(self):
        """Duplicate-or-not and stock-difference need the same lookup."""
        import inspect
        source = inspect.getsource(receipt_service.generate)
        self.assertEqual(source.count("receipt_history.latest_for"), 1)


class TheTermsPageIsSelectable(Stage8TestCase):
    """A shop's own policy wording lives in its own file.

    The shipped `terms.html` used to carry one particular business's returns
    policy, phone number and support email, so every clone of this repo printed
    them. Making the *file* configurable fixes that without anyone having to
    give up their wording: keep it under another name and point at it.
    """

    def write_terms(self, name, marker):
        path = os.path.join(self.dir, "Templates", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(f'<div class="policy-page">{marker}</div>\n')

    def render(self):
        receipt_render.clear_template_cache()
        return receipt_render.build_html(
            "INV-W1", "1 Jan 2026", "Ada", "", "",
            [{"sku": "A", "desc": "Thing", "serial": "", "qty": 1, "price": "10",
              "discount": "0", "tax": "0", "warranty": ""}], "Online", 0)

    def test_the_default_is_terms_html(self):
        self.assertEqual(
            config.default_app_settings()["terms_page"]["template"], "terms.html")

    def test_the_shipped_page_carries_nobody_business_details(self):
        """The whole point: a clone must not print someone else's policy."""
        with open(os.path.join(PROJ, "Templates", "terms.html"),
                  encoding="utf-8") as handle:
            shipped = handle.read()
        # Patterns rather than one shop's actual details: naming them here would
        # put them back into the repository this check exists to keep them out
        # of, which is the mistake the check is about.
        import re
        for pattern, what in (
                (r"\+?\d[\d\s().-]{8,}\d", "a phone number"),
                (r"[\w.+-]+@[\w-]+\.[\w.]+", "an email address"),
                (r"https?://(?!example\.)", "a live URL")):
            self.assertIsNone(
                re.search(pattern, shipped),
                f"the shipped terms page carries what looks like {what}")

    def test_a_named_file_is_used_instead(self):
        config.install_default_templates()
        self.write_terms("terms.mine.html", "MY OWN POLICY")
        config.update_app_settings({"terms_page": {"template": "terms.mine.html"}})
        self.assertIn("MY OWN POLICY", self.render())

    def test_switching_back_restores_the_shipped_one(self):
        config.install_default_templates()
        self.write_terms("terms.mine.html", "MY OWN POLICY")
        config.update_app_settings({"terms_page": {"template": "terms.mine.html"}})
        self.assertIn("MY OWN POLICY", self.render())
        config.update_app_settings({"terms_page": {"template": "terms.html"}})
        self.assertNotIn("MY OWN POLICY", self.render())

    def test_a_missing_file_is_reported_by_name(self):
        config.update_app_settings({"terms_page": {"template": "terms.gone.html"}})
        from template_engine import TemplateError
        with self.assertRaises(TemplateError) as ctx:
            self.render()
        self.assertIn("terms.gone.html", str(ctx.exception))


class TheTermsTemplateIsValidated(unittest.TestCase):
    def settings(self, template):
        settings = config.default_app_settings()
        settings["terms_page"]["template"] = template
        return settings

    def test_a_filename_passes(self):
        config.validate(self.settings("terms.mine.html"), "appsettings.json")

    def test_a_path_is_refused(self):
        """The value is joined onto a directory and read from disk."""
        # Raw strings: "..\terms.html" is "..<TAB>erms.html", which contains no
        # backslash at all and would quietly test nothing.
        for bad in ("../secrets.html", "sub/terms.html", r"..\terms.html",
                    r"C:\Windows\win.ini", "/etc/passwd"):
            with self.subTest(template=bad):
                with self.assertRaises(config.ConfigError) as ctx:
                    config.validate(self.settings(bad), "appsettings.json")
                self.assertEqual(ctx.exception.key, "terms_page.template")

    def test_it_must_be_html(self):
        with self.assertRaises(config.ConfigError):
            config.validate(self.settings("terms.txt"), "appsettings.json")

    def test_it_cannot_be_empty(self):
        with self.assertRaises(config.ConfigError):
            config.validate(self.settings("   "), "appsettings.json")


class PinnedDependencies(unittest.TestCase):
    """A different Chromium lays out a PDF differently."""

    def requirements(self):
        with open(os.path.join(PROJ, "requirements.txt"), encoding="utf-8") as f:
            return [line.strip() for line in f
                    if line.strip() and not line.startswith("#")]

    def test_playwright_is_pinned_exactly(self):
        line = next(l for l in self.requirements() if l.startswith("playwright"))
        self.assertIn("==", line, "a loose range means a different browser build")

    def test_the_signing_libraries_are_pinned_exactly(self):
        for package in ("pyhanko", "cryptography"):
            line = next(l for l in self.requirements() if l.startswith(package))
            self.assertIn("==", line, f"{package} produces the signature")

    def test_the_reason_is_written_down(self):
        with open(os.path.join(PROJ, "requirements.txt"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Chromium version", text)


if __name__ == "__main__":
    unittest.main()
