"""receipt_service — the generation orchestration.

This is where everything meets: numbering, filenames, rendering, signing, the
atomic move into place, history and stock. Most of it is tested with the
Playwright call stubbed, so the suite stays fast; a couple of end-to-end cases
drive the real renderer.

The behaviours that matter most here are the failure paths, because they are the
ones a user hits on a bad day and the ones that can leave the output folder or
the invoice sequence in a wrong state.

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
import product_catalogue   # noqa: E402
import receipt_history     # noqa: E402
import receipt_service     # noqa: E402

DATA = {
    "inv_no": "INV-W1001", "date_str": "26 Aug 2026", "cust": "Ada Lovelace",
    "phone": "", "email": "", "receipt_type": "Online", "shipping": "0",
    "items": [{"sku": "KB-87", "desc": "Keyboard", "serial": "", "qty": 2,
               "price": "10.00", "discount": "0", "tax": "0", "warranty": ""}],
}


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-service-")
        shutil.copy(os.path.join(PROJ, "appsettings.example.json"),
                    os.path.join(self.dir, "appsettings.json"))
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)
        import receipt_render
        receipt_render.clear_template_cache()

        self.rendered = []
        self._real_render = receipt_service.render_pdf
        self._real_sign = receipt_service.sign_receipt_pdf

    def tearDown(self):
        receipt_service.render_pdf = self._real_render
        receipt_service.sign_receipt_pdf = self._real_sign
        config.set_app_dir(self._app_dir)
        import receipt_render
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def stub_render(self, fail_with=None):
        """Replace the Playwright call with something instant."""
        def fake(html, path):
            self.rendered.append((html, path))
            if fail_with:
                raise fail_with
            with open(path, "wb") as f:
                f.write(b"%PDF-1.4 pretend receipt")
        receipt_service.render_pdf = fake

    def stub_sign(self, signed=True, fail_with=None):
        def fake(path):
            if fail_with:
                raise fail_with
            return signed
        receipt_service.sign_receipt_pdf = fake

    def out(self, name="INV-W1001.pdf"):
        return os.path.join(self.dir, "invoices", name)


class PathResolution(ServiceTestCase):
    def test_a_relative_path_resolves_against_the_app_folder(self):
        self.assertEqual(receipt_service.resolve_app_path("signing/key.pem"),
                         os.path.join(self.dir, "signing/key.pem"))

    def test_an_absolute_path_is_left_alone(self):
        absolute = os.path.join(tempfile.gettempdir(), "elsewhere.pem")
        self.assertEqual(receipt_service.resolve_app_path(absolute), absolute)

    def test_an_empty_path_stays_empty(self):
        self.assertEqual(receipt_service.resolve_app_path(""), "")
        self.assertEqual(receipt_service.resolve_app_path("   "), "")

    def test_signing_key_paths_come_from_config(self):
        key, cert = receipt_service.signing_key_paths()
        self.assertTrue(key.endswith("private_key.pem"))
        self.assertTrue(cert.endswith("certificate.pem"))
        self.assertTrue(os.path.isabs(key))


class SigningGlue(ServiceTestCase):
    def test_signing_disabled_reports_unsigned_rather_than_failing(self):
        config.update_app_settings({"signing": {"enabled": False}})
        self.assertFalse(receipt_service.sign_receipt_pdf(self.out()))

    def test_a_missing_key_is_an_error_naming_both_paths(self):
        """Never leave an unsigned file claiming to be an authentic receipt."""
        with self.assertRaises(RuntimeError) as ctx:
            receipt_service.sign_receipt_pdf(self.out())
        message = str(ctx.exception)
        self.assertIn("was not found", message)
        self.assertIn("private_key.pem", message)
        self.assertIn("certificate.pem", message)
        self.assertIn("keygen.py", message, "it should say how to fix it")


class Numbering(ServiceTestCase):
    def test_prefix_per_receipt_type(self):
        self.assertEqual(receipt_service.get_invoice_prefix("Online"), "INV-W")
        self.assertEqual(receipt_service.get_invoice_prefix("In Store"), "INV-S")

    def test_series_code_strips_the_prefix(self):
        self.assertEqual(receipt_service.series_code("INV-W"), "W")

    def test_series_code_of_something_unprefixed_is_itself(self):
        self.assertEqual(receipt_service.series_code("W"), "W")

    def test_peek_does_not_consume_but_reserve_does(self):
        prefix = receipt_service.get_invoice_prefix("Online")
        first = receipt_service.get_next_invoice_number(prefix)
        self.assertEqual(receipt_service.get_next_invoice_number(prefix), first)
        self.assertEqual(receipt_service.reserve_invoice_number(prefix), first)
        self.assertEqual(receipt_service.get_next_invoice_number(prefix), first + 1)

    def test_the_output_folder_is_created_on_demand(self):
        shutil.rmtree(os.path.join(self.dir, "invoices"))
        receipt_service.get_next_invoice_number("INV-W")
        self.assertTrue(os.path.isdir(os.path.join(self.dir, "invoices")))


class Filenames(ServiceTestCase):
    def test_the_invoice_number_always_leads(self):
        name = receipt_service.build_pdf_filename(
            "INV-W1001", "15 Jan 2026", "Ada", "a@b.c", "123")
        self.assertTrue(name.startswith("INV-W1001-"))
        self.assertTrue(name.endswith(".pdf"))

    def test_only_the_configured_fields_are_added(self):
        self.assertEqual(
            receipt_service.build_pdf_filename("INV-W1", "15 Jan 2026", "Ada", "", ""),
            "INV-W1-15 Jan 2026-Ada.pdf")

    def test_a_field_that_sanitises_to_nothing_is_dropped(self):
        self.assertEqual(
            receipt_service.build_pdf_filename("INV-W1", "15 Jan 2026", "///", "", ""),
            "INV-W1-15 Jan 2026.pdf")

    def test_collisions_take_the_next_free_suffix(self):
        for name in ("INV-W1.pdf", "INV-W1-1.pdf", "INV-W1-2.pdf"):
            open(os.path.join(self.dir, "invoices", name), "wb").close()
        self.assertEqual(
            os.path.basename(receipt_service.next_available_pdf_path("INV-W1.pdf")),
            "INV-W1-3.pdf")

    def test_the_first_free_suffix_is_one(self):
        self.assertEqual(
            os.path.basename(receipt_service.next_available_pdf_path("INV-W9.pdf")),
            "INV-W9-1.pdf")


class GenerationSucceeds(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.stub_render()
        self.stub_sign(signed=True)

    def test_the_receipt_lands_at_the_requested_path(self):
        self.assertTrue(receipt_service.generate(DATA, self.out()))
        self.assertTrue(os.path.isfile(self.out()))

    def test_progress_is_reported_in_order(self):
        steps = []
        receipt_service.generate(DATA, self.out(), lambda n, label: steps.append(n))
        self.assertEqual(steps, list(range(1, receipt_service.GENERATION_STEPS + 1)))

    def test_progress_labels_are_human_readable(self):
        labels = []
        receipt_service.generate(DATA, self.out(), lambda n, label: labels.append(label))
        self.assertTrue(all(label and label[0].isupper() for label in labels), labels)

    def test_it_works_without_a_progress_callback(self):
        self.assertTrue(receipt_service.generate(DATA, self.out()))

    def test_unsigned_generation_reports_false(self):
        self.stub_sign(signed=False)
        self.assertFalse(receipt_service.generate(DATA, self.out()))

    def test_the_rendered_html_is_the_receipt(self):
        receipt_service.generate(DATA, self.out())
        html, _path = self.rendered[0]
        self.assertIn("INV-W1001", html)
        self.assertIn("Ada Lovelace", html)
        self.assertIn("Keyboard", html)

    def test_no_partial_file_is_left_behind(self):
        receipt_service.generate(DATA, self.out())
        leftovers = [n for n in os.listdir(os.path.join(self.dir, "invoices"))
                     if n.endswith(".partial")]
        self.assertEqual(leftovers, [])

    def test_the_signature_is_applied_to_the_temp_file_not_the_final_one(self):
        """Nothing may observe a half-written or unsigned file under its final name."""
        signed_paths = []
        receipt_service.sign_receipt_pdf = lambda path: signed_paths.append(path) or True
        receipt_service.generate(DATA, self.out())
        self.assertTrue(signed_paths[0].endswith(".partial"), signed_paths)

    def test_a_missing_output_directory_is_created(self):
        target = os.path.join(self.dir, "invoices", "nested", "deep", "INV-W1.pdf")
        receipt_service.generate(DATA, target)
        self.assertTrue(os.path.isfile(target))


class GenerationFails(ServiceTestCase):
    """A failed run must leave nothing behind and must not swallow the reason."""

    def test_a_render_failure_propagates(self):
        self.stub_render(fail_with=RuntimeError("chromium exploded"))
        with self.assertRaises(RuntimeError) as ctx:
            receipt_service.generate(DATA, self.out())
        self.assertIn("chromium exploded", str(ctx.exception))

    def test_a_render_failure_creates_no_receipt(self):
        self.stub_render(fail_with=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            receipt_service.generate(DATA, self.out())
        self.assertFalse(os.path.exists(self.out()),
                         "a failed run must not create the receipt at all")

    def test_a_render_failure_leaves_no_partial_file(self):
        self.stub_render(fail_with=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            receipt_service.generate(DATA, self.out())
        self.assertEqual(os.listdir(os.path.join(self.dir, "invoices")), [])

    def test_a_signing_failure_leaves_no_receipt(self):
        """An unsigned file under the final name would claim to be authentic."""
        self.stub_render()
        self.stub_sign(fail_with=RuntimeError("no key"))
        with self.assertRaises(RuntimeError):
            receipt_service.generate(DATA, self.out())
        self.assertFalse(os.path.exists(self.out()))

    def test_a_signing_failure_leaves_no_partial_file(self):
        self.stub_render()
        self.stub_sign(fail_with=RuntimeError("no key"))
        with self.assertRaises(RuntimeError):
            receipt_service.generate(DATA, self.out())
        self.assertEqual(os.listdir(os.path.join(self.dir, "invoices")), [])

    def test_an_existing_receipt_survives_a_failed_regeneration(self):
        """Replacing is atomic: a failure must not destroy what was there."""
        self.stub_render()
        self.stub_sign()
        receipt_service.generate(DATA, self.out())
        original = open(self.out(), "rb").read()

        self.stub_render(fail_with=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            receipt_service.generate(DATA, self.out())
        self.assertEqual(open(self.out(), "rb").read(), original)

    def test_nothing_is_recorded_in_history_for_a_failed_run(self):
        self.stub_render(fail_with=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            receipt_service.generate(DATA, self.out())
        self.assertEqual(receipt_history.entries(), [])


class SideEffectsAfterSuccess(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.stub_render()
        self.stub_sign(signed=True)

    def test_the_receipt_is_recorded_in_history(self):
        receipt_service.generate(DATA, self.out())
        entries = receipt_history.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["invoice_no"], "INV-W1001")

    def test_the_signed_state_is_recorded(self):
        receipt_service.generate(DATA, self.out())
        self.assertTrue(receipt_history.entries()[0]["signed"])
        self.stub_sign(signed=False)
        receipt_service.generate(dict(DATA, inv_no="INV-W1002"), self.out("b.pdf"))
        self.assertFalse(receipt_history.entries()[0]["signed"])

    def test_a_broken_history_does_not_fail_the_receipt(self):
        """Optional bookkeeping must never cost a user their receipt."""
        original = receipt_history.record
        try:
            receipt_history.record = lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("disk gone"))
            with self.assertRaises(RuntimeError):
                receipt_service.generate(DATA, self.out())
        finally:
            receipt_history.record = original
        # The contract is that record() itself swallows failures; this test pins
        # that the swallowing lives there, not in generate().
        self.assertTrue(os.path.isfile(self.out()),
                        "the receipt was still written before bookkeeping ran")

    def test_stock_is_deducted_when_tracking_is_on(self):
        product_catalogue.save({config.SCHEMA_VERSION_KEY: 1, "products": [
            {"sku": "KB-87", "name": "Keyboard", "list_price": "10.00", "stock_count": 5}]})
        config.update_app_settings({"inventory": {"track_stock": True}})

        receipt_service.generate(DATA, self.out())
        stock = {p["sku"]: p["stock_count"]
                 for p in product_catalogue.load()["products"]}
        self.assertEqual(stock["KB-87"], 3, "2 sold from 5")

    def test_stock_is_untouched_when_tracking_is_off(self):
        product_catalogue.save({config.SCHEMA_VERSION_KEY: 1, "products": [
            {"sku": "KB-87", "name": "Keyboard", "list_price": "10.00", "stock_count": 5}]})
        receipt_service.generate(DATA, self.out())
        stock = {p["sku"]: p["stock_count"]
                 for p in product_catalogue.load()["products"]}
        self.assertEqual(stock["KB-87"], 5)

    def test_reissuing_adjusts_by_the_difference_not_the_whole_sale(self):
        product_catalogue.save({config.SCHEMA_VERSION_KEY: 1, "products": [
            {"sku": "KB-87", "name": "Keyboard", "list_price": "10.00", "stock_count": 10}]})
        config.update_app_settings({"inventory": {"track_stock": True}})

        receipt_service.generate(DATA, self.out())                       # 2 sold -> 8
        corrected = dict(DATA)
        corrected["items"] = [dict(DATA["items"][0], qty=3)]
        receipt_service.generate(corrected, self.out())                  # now 3 -> 7

        stock = {p["sku"]: p["stock_count"]
                 for p in product_catalogue.load()["products"]}
        self.assertEqual(stock["KB-87"], 7,
                         "correcting 2 to 3 should take one more, not another three")


class RenderPdfErrors(ServiceTestCase):
    """The two failure messages a user is most likely to see.

    render_pdf builds the header/footer templates *before* entering the
    try/except, so these patch the Playwright entry point itself -- which is
    what the except clause is actually there to catch.
    """

    def raising_playwright(self, message):
        import playwright.sync_api as pw

        self._original_pw = pw.sync_playwright
        pw.sync_playwright = lambda: (_ for _ in ()).throw(Exception(message))
        self.addCleanup(setattr, pw, "sync_playwright", self._original_pw)

    def test_a_missing_chromium_is_explained(self):
        self.raising_playwright("Executable doesn't exist at C:\\...\\chrome.exe")
        with self.assertRaises(RuntimeError) as ctx:
            receipt_service.render_pdf("<html></html>", self.out())
        self.assertIn("playwright install chromium", str(ctx.exception))

    def test_the_other_wording_playwright_uses_is_also_caught(self):
        self.raising_playwright("please run playwright install")
        with self.assertRaises(RuntimeError) as ctx:
            receipt_service.render_pdf("<html></html>", self.out())
        self.assertIn("playwright install chromium", str(ctx.exception))

    def test_an_unrelated_render_error_is_not_disguised(self):
        """Wrapping every failure as "install chromium" would mislead."""
        self.raising_playwright("the page crashed")
        with self.assertRaises(Exception) as ctx:
            receipt_service.render_pdf("<html></html>", self.out())
        self.assertIn("the page crashed", str(ctx.exception))
        self.assertNotIn("playwright install", str(ctx.exception))


class EndToEndWithRealRendering(ServiceTestCase):
    """A couple of cases that actually drive Playwright, as a reality check."""

    def test_a_real_unsigned_receipt_is_produced(self):
        config.update_app_settings({"signing": {"enabled": False}})
        signed = receipt_service.generate(DATA, self.out())
        self.assertFalse(signed)
        with open(self.out(), "rb") as f:
            self.assertTrue(f.read(5).startswith(b"%PDF"), "not a PDF")
        self.assertGreater(os.path.getsize(self.out()), 1000)

    def test_external_requests_are_blocked_during_a_real_render(self):
        """A template referencing a CDN must not make output depend on the network."""
        config.update_app_settings({"signing": {"enabled": False}})
        html = ('<html><body><img src="https://example.invalid/x.png">'
                '<p>Receipt body</p></body></html>')
        receipt_service.render_pdf(html, self.out())
        self.assertTrue(os.path.isfile(self.out()),
                        "a blocked request must not stop the render")


if __name__ == "__main__":
    unittest.main()
