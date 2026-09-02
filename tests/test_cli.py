"""The command-line surface: cli.py, keygen.py and verify_receipt.py.

These are the headless entry points -- the golden gate runs through `cli.py`,
and the other two are what someone reaches for without the GUI. Exit codes are
part of their contract (a script checks them), so they are asserted explicitly
rather than just "did it not crash".

Run: python -m unittest discover -s tests
"""
import io
import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import cli               # noqa: E402
import config            # noqa: E402
import keygen            # noqa: E402
import receipt_render    # noqa: E402
import receipt_signing   # noqa: E402
import verify_receipt    # noqa: E402

FIXTURES = os.path.join(PROJ, "tests", "fixtures")
GOLDEN_INPUT = os.path.join(FIXTURES, "golden_input.json")
GATE_ENV = os.path.join(FIXTURES, "env")


@contextlib.contextmanager
def captured():
    """Run with stdout/stderr captured as text."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-cli-")
        shutil.copy(os.path.join(PROJ, "appsettings.example.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        import receipt_render
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        import receipt_render
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def out_file(self, name="out.html"):
        return os.path.join(self.dir, name)


class RenderHtml(CliTestCase):
    def test_rendering_to_a_file_succeeds(self):
        target = self.out_file()
        with captured():
            code = cli.main(["--render-html", GOLDEN_INPUT, "--out", target])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue(os.path.isfile(target))

    def test_the_output_is_a_receipt(self):
        target = self.out_file()
        with captured():
            cli.main(["--render-html", GOLDEN_INPUT, "--out", target])
        html = open(target, encoding="utf-8").read()
        self.assertIn("INV-W1001", html)
        self.assertIn("Ada Lovelace", html)

    def test_rendering_to_stdout(self):
        """Written as UTF-8 *bytes*: the receipt contains characters a cp1252
        console cannot encode, so cli writes to sys.stdout.buffer directly."""
        class FakeStdout:
            def __init__(self):
                self.buffer = io.BytesIO()

        fake = FakeStdout()
        real = sys.stdout
        try:
            sys.stdout = fake
            code = cli.main(["--render-html", GOLDEN_INPUT])
        finally:
            sys.stdout = real

        self.assertEqual(code, cli.EXIT_OK)
        written = fake.buffer.getvalue()
        self.assertTrue(written.startswith(b"<!DOCTYPE html>"))
        self.assertIn("Ada Lovelace", written.decode("utf-8"))
        # Something outside cp1252 has to reach stdout, or this proves nothing.
        # It used to be an emoji in the shipped policy page; that page is
        # generic now, so the test supplies its own -- and a customer whose name
        # does not fit the console codepage is the realistic version of this
        # anyway.
        settings = config.load_app_settings()
        html = receipt_render.render_receipt(
            {"invoice_no": "INV-1", "date": "1 Jan 2026",
             "customer_name": "Zoë Ünïcödé", "customer_phone": "",
             "customer_email": "", "items": [], "receipt_type": "Online",
             "shipping": 0},
            receipt_render.load_templates(), strings=config.load_strings(),
            currency=settings.get("currency"), fields=config.load_fields())
        self.assertIn("Zoë", html)
        self.assertIn("Zoë".encode("utf-8"), html.encode("utf-8"),
                      "a name outside cp1252 is why stdout is written as bytes")

    def test_the_base_href_is_normalised_by_default(self):
        target = self.out_file()
        with captured():
            cli.main(["--render-html", GOLDEN_INPUT, "--out", target])
        html = open(target, encoding="utf-8").read()
        self.assertIn(cli.RESOURCE_BASE_PLACEHOLDER, html)
        self.assertNotIn("file:///", html)

    def test_raw_keeps_the_machine_specific_base_href(self):
        target = self.out_file()
        with captured():
            cli.main(["--render-html", GOLDEN_INPUT, "--out", target, "--raw"])
        html = open(target, encoding="utf-8").read()
        self.assertIn("file:///", html)
        self.assertNotIn(cli.RESOURCE_BASE_PLACEHOLDER, html)

    def test_freeze_date_overrides_the_data(self):
        target = self.out_file()
        with captured():
            cli.main(["--render-html", GOLDEN_INPUT, "--out", target,
                      "--freeze-date", "01 Jan 1999"])
        self.assertIn("01 Jan 1999", open(target, encoding="utf-8").read())

    def test_invoice_number_overrides_the_data(self):
        target = self.out_file()
        with captured():
            cli.main(["--render-html", GOLDEN_INPUT, "--out", target,
                      "--invoice-number", "INV-OVERRIDE"])
        self.assertIn("INV-OVERRIDE", open(target, encoding="utf-8").read())

    def test_a_missing_data_file_is_a_config_error(self):
        with captured() as (_out, err):
            code = cli.main(["--render-html", os.path.join(self.dir, "nope.json")])
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("could not read", err.getvalue().lower())

    def test_malformed_data_is_a_config_error(self):
        broken = self.out_file("broken.json")
        with open(broken, "w", encoding="utf-8") as f:
            f.write("{ not json")
        with captured() as (_out, err):
            code = cli.main(["--render-html", broken])
        self.assertEqual(code, cli.EXIT_CONFIG)

    def test_data_that_cannot_render_is_a_render_error(self):
        bad = self.out_file("bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            json.dump({"items": [{"qty": "not a number", "unit_price": "x"}]}, f)
        with captured() as (_out, err):
            code = cli.main(["--render-html", bad])
        self.assertEqual(code, cli.EXIT_RENDER)
        self.assertIn("render failed", err.getvalue().lower())


class ConfigDir(CliTestCase):
    def test_it_renders_against_the_given_directory(self):
        target = self.out_file()
        with captured():
            code = cli.main(["--config-dir", GATE_ENV,
                             "--render-html", GOLDEN_INPUT, "--out", target])
        self.assertEqual(code, cli.EXIT_OK)
        config.set_app_dir(self.dir)      # cli re-rooted config; put it back

    def test_a_missing_directory_is_refused(self):
        with captured() as (_out, err):
            code = cli.main(["--config-dir", os.path.join(self.dir, "nope"), "--check"])
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("not a directory", err.getvalue())


class Check(CliTestCase):
    def test_a_healthy_setup_passes(self):
        with captured() as (out, _err):
            code = cli.run_check()
        self.assertEqual(code, cli.EXIT_OK)
        printed = out.getvalue()
        self.assertIn("config   OK", printed)
        self.assertIn("render   OK", printed)

    def test_every_template_is_listed(self):
        with captured() as (out, _err):
            cli.run_check()
        printed = out.getvalue()
        for name in ("base.html", "styles.css", "totals.html"):
            self.assertIn(name, printed)

    def test_a_bad_config_fails_with_the_config_exit_code(self):
        settings = config.default_app_settings()
        settings["currency"]["decimals"] = 99
        with open(os.path.join(self.dir, "appsettings.json"), "w", encoding="utf-8") as f:
            json.dump(settings, f)
        with captured() as (_out, err):
            code = cli.run_check()
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("currency.decimals", err.getvalue())

    def test_a_broken_template_fails_with_the_template_exit_code(self):
        config.install_default_templates()
        target = os.path.join(self.dir, "Templates", "totals_row.html")
        with open(target, "w", encoding="utf-8") as f:
            f.write("<tr>{{nonsense}}</tr>\n")
        with captured() as (_out, err):
            code = cli.run_check()
        self.assertEqual(code, cli.EXIT_TEMPLATE)
        self.assertIn("totals_row.html", err.getvalue())

    def test_a_missing_logo_is_a_warning_not_a_failure(self):
        """A receipt without its logo is still a valid receipt."""
        with captured() as (out, _err):
            code = cli.run_check()
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("logo", out.getvalue())

    def test_check_is_reachable_through_main(self):
        with captured():
            self.assertEqual(cli.main(["--check"]), cli.EXIT_OK)


class ArgumentHandling(CliTestCase):
    def test_no_arguments_is_an_error_not_a_silent_success(self):
        with captured(), self.assertRaises(SystemExit) as ctx:
            cli.main([])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_exit_codes_are_distinct(self):
        codes = [cli.EXIT_OK, cli.EXIT_CONFIG, cli.EXIT_TEMPLATE,
                 cli.EXIT_RENDER, cli.EXIT_SIGNING]
        self.assertEqual(len(set(codes)), len(codes))


class Keygen(CliTestCase):
    """keygen writes where the *app* looks, which is configuration, not a default."""

    def paths(self):
        import receipt_service
        return receipt_service.signing_key_paths()

    def test_it_creates_a_usable_key_pair(self):
        with captured() as (out, _err):
            code = keygen.main([])
        self.assertEqual(code, 0)
        key, cert = self.paths()
        self.assertTrue(os.path.isfile(key))
        self.assertTrue(os.path.isfile(cert))
        self.assertIn("NEXT STEPS", out.getvalue())

    def test_it_writes_to_the_configured_paths_not_the_defaults(self):
        """A key created where the app never looks is silently useless."""
        config.update_app_settings({"signing": {
            "private_key_path": "keys/my_key.pem",
            "certificate_path": "keys/my_cert.pem"}})
        with captured():
            self.assertEqual(keygen.main([]), 0)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "keys", "my_key.pem")))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "keys", "my_cert.pem")))

    def test_the_identity_comes_from_the_configuration(self):
        config.update_app_settings({"signing": {"signer_name": "Bakery Ltd"}})
        with captured():
            keygen.main([])
        info = receipt_signing.certificate_info(self.paths()[1])
        self.assertIn("Bakery Ltd", info["subject"])

    def test_an_explicit_organisation_wins(self):
        with captured():
            keygen.main(["--org-name", "Explicit Ltd"])
        info = receipt_signing.certificate_info(self.paths()[1])
        self.assertIn("Explicit Ltd", info["subject"])

    def test_it_refuses_to_overwrite_an_existing_key(self):
        """Overwriting silently would orphan every receipt already signed."""
        with captured():
            keygen.main([])
        with captured() as (_out, err):
            code = keygen.main([])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err.getvalue())

    def test_force_replaces_it(self):
        with captured():
            keygen.main([])
        first = open(self.paths()[1], "rb").read()
        with captured():
            self.assertEqual(keygen.main(["--force"]), 0)
        self.assertNotEqual(open(self.paths()[1], "rb").read(), first)

    def test_a_passphrase_encrypts_the_key(self):
        with captured():
            keygen.main(["--passphrase", "s3cret"])
        with open(self.paths()[0], "rb") as f:
            self.assertIn(b"ENCRYPTED", f.read())

    def test_the_created_key_actually_signs(self):
        """The point of all of it: the app can use what keygen produced."""
        with captured():
            keygen.main([])
        key, cert = self.paths()
        from tests.test_signing import blank_pdf
        pdf = blank_pdf(os.path.join(self.dir, "doc.pdf"))
        receipt_signing.sign_pdf(pdf, key, cert)
        self.assertEqual(receipt_signing.verify_pdf(pdf, cert).status,
                         receipt_signing.VERIFIED)


class VerifyReceiptCli(CliTestCase):
    """The reference verifier. Its exit codes are the documented contract."""

    def setUp(self):
        super().setUp()
        self.key = os.path.join(self.dir, "k.pem")
        self.cert = os.path.join(self.dir, "c.pem")
        receipt_signing.generate_key_pair(self.key, self.cert, org_name="Acme")
        self._saved_known = receipt_signing.KNOWN_CERTS_DIR
        receipt_signing.KNOWN_CERTS_DIR = os.path.join(self.dir, "previous")

    def tearDown(self):
        receipt_signing.KNOWN_CERTS_DIR = self._saved_known
        super().tearDown()

    def blank_pdf(self, name="doc.pdf"):
        from tests.test_signing import blank_pdf
        return blank_pdf(os.path.join(self.dir, name))

    def test_a_genuine_receipt_exits_zero(self):
        pdf = self.blank_pdf()
        receipt_signing.sign_pdf(pdf, self.key, self.cert)
        with captured() as (out, _err):
            code = verify_receipt.main([pdf, "--cert", self.cert])
        self.assertEqual(code, 0)
        self.assertIn("VERIFIED", out.getvalue())

    def test_an_unsigned_pdf_exits_two(self):
        with captured() as (out, _err):
            code = verify_receipt.main([self.blank_pdf(), "--cert", self.cert])
        self.assertEqual(code, 2)
        self.assertIn("NO SIG", out.getvalue())

    def test_a_tampered_receipt_exits_one(self):
        pdf = self.blank_pdf()
        receipt_signing.sign_pdf(pdf, self.key, self.cert)
        raw = bytearray(open(pdf, "rb").read())
        raw[len(raw) // 2] ^= 0x01
        open(pdf, "wb").write(bytes(raw))
        with captured() as (out, _err):
            code = verify_receipt.main([pdf, "--cert", self.cert])
        self.assertEqual(code, 1)
        self.assertIn("INVALID", out.getvalue())

    def test_a_missing_file_exits_three(self):
        with captured() as (_out, err):
            code = verify_receipt.main([os.path.join(self.dir, "nope.pdf"),
                                        "--cert", self.cert])
        self.assertEqual(code, 3)
        self.assertIn("ERROR", err.getvalue())

    def test_a_missing_certificate_exits_three(self):
        with captured() as (_out, err):
            code = verify_receipt.main([self.blank_pdf(), "--cert",
                                        os.path.join(self.dir, "nope.pem")])
        self.assertEqual(code, 3)

    def test_the_signer_is_reported_for_a_genuine_receipt(self):
        pdf = self.blank_pdf()
        receipt_signing.sign_pdf(pdf, self.key, self.cert)
        with captured() as (out, _err):
            verify_receipt.main([pdf, "--cert", self.cert])
        self.assertIn("Acme", out.getvalue())


if __name__ == "__main__":
    unittest.main()
