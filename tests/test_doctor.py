"""`cli.py --doctor` — TODO.md §5 (Stage 7).

`--check` validates the *configuration*. This checks the things around it: a
browser to render with, a folder to write into, a counter to number from, a key
to sign with. Those fail on a new machine or a changed one, and they fail at the
worst possible moment — halfway through issuing a receipt to somebody standing
at the counter.

Two rules shape it:

* **Every check runs.** A doctor that stops at the first problem makes you run
  it four times to find four things.
* **A warning is not a failure.** A receipt without a logo is still a valid
  receipt, and exiting non-zero over one would make the check useless in a
  build script.

The browser check is stubbed throughout: launching a real Chromium per test
would make this file slower than the rest of the suite put together, and
`main.py --smoke-test` already drives a real one.

Run: python -m unittest discover -s tests
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import cli                 # noqa: E402
import config              # noqa: E402
import receipt_signing     # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


class DoctorTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-doctor-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)

        # Never launch a real browser: see the module docstring.
        self._real_browser = cli._check_browser
        cli._check_browser = lambda report: report.ok("browser", "(stubbed)")

    def tearDown(self):
        cli._check_browser = self._real_browser
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_doctor(self):
        """Returns (exit_code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.run_doctor()
        return code, out.getvalue(), err.getvalue()

    def signing(self, **values):
        config.update_app_settings({"signing": values})


class AHealthyInstall(DoctorTestCase):
    def setUp(self):
        super().setUp()
        self.signing(enabled=False)

    def test_it_passes(self):
        code, _, _ = self.run_doctor()
        self.assertEqual(code, cli.EXIT_OK)

    def test_it_says_so_plainly(self):
        _, out, _ = self.run_doctor()
        self.assertIn("Ready to issue receipts", out)

    def test_every_area_is_reported(self):
        _, out, _ = self.run_doctor()
        for area in ("app", "browser", "output", "counter", "signing",
                     "catalogue", "history", "drafts"):
            self.assertIn(area, out, f"{area} was not checked")

    def test_the_next_invoice_number_is_shown(self):
        _, out, _ = self.run_doctor()
        self.assertIn("next W number is", out)

    def test_signing_being_off_is_not_a_problem(self):
        _, out, _ = self.run_doctor()
        self.assertIn("off (receipts will not be signed)", out)


class EveryCheckRuns(DoctorTestCase):
    """One run has to report everything, not stop at the first problem."""

    def test_a_failure_does_not_hide_the_checks_after_it(self):
        self.signing(enabled=True, private_key_path="signing/missing.pem")
        real = cli._check_output_folder
        cli._check_output_folder = lambda report: report.fail("output", "boom")
        try:
            _, out, err = self.run_doctor()
        finally:
            cli._check_output_folder = real
        self.assertIn("boom", err)
        self.assertIn("counter", out, "checks after the failure must still run")
        self.assertIn("drafts", out)


class WarningsAreNotFailures(DoctorTestCase):
    def test_a_missing_key_warns_rather_than_failing(self):
        """Receipts still issue; they just go out unsigned. Say so, do not stop."""
        self.signing(enabled=True, private_key_path="signing/nothing-here.pem")
        code, out, _ = self.run_doctor()
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("WARN", out)
        self.assertIn("unsigned", out)

    def test_an_expiring_certificate_warns(self):
        key, cert = self.make_key()
        self.signing(enabled=True, private_key_path=key, certificate_path=cert)
        self.patch_certificate(days_left=30)
        code, out, _ = self.run_doctor()
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("expires", out)
        self.assertIn("30 days left", out)

    def make_key(self):
        folder = os.path.join(self.dir, "signing")
        os.makedirs(folder, exist_ok=True)
        key = os.path.join(folder, "private_key.pem")
        cert = os.path.join(folder, "certificate.pem")
        receipt_signing.generate_key_pair(key, cert, common_name="T", org_name="T")
        return key, cert

    def patch_certificate(self, days_left):
        import datetime
        real = receipt_signing.certificate_info

        def fake(path):
            info = real(path)
            if info is None:
                return None
            info = dict(info)
            info["days_left"] = days_left
            info["expired"] = days_left < 0
            info["not_after"] = (datetime.datetime.now()
                                 + datetime.timedelta(days=days_left))
            return info

        receipt_signing.certificate_info = fake
        self.addCleanup(setattr, receipt_signing, "certificate_info", real)


class FailuresStopAReceipt(DoctorTestCase):
    def test_an_expired_certificate_fails(self):
        key, cert = WarningsAreNotFailures.make_key(self)
        self.signing(enabled=True, private_key_path=key, certificate_path=cert)
        WarningsAreNotFailures.patch_certificate(self, days_left=-3)
        code, _, err = self.run_doctor()
        self.assertEqual(code, cli.EXIT_ENVIRONMENT)
        self.assertIn("EXPIRED", err)

    def test_an_unreadable_certificate_fails(self):
        folder = os.path.join(self.dir, "signing")
        os.makedirs(folder, exist_ok=True)
        key = os.path.join(folder, "private_key.pem")
        cert = os.path.join(folder, "certificate.pem")
        receipt_signing.generate_key_pair(key, cert, common_name="T", org_name="T")
        with open(cert, "w", encoding="utf-8") as handle:
            handle.write("not a certificate")

        self.signing(enabled=True, private_key_path=key, certificate_path=cert)
        code, _, err = self.run_doctor()
        self.assertEqual(code, cli.EXIT_ENVIRONMENT)
        self.assertIn("could not be read", err)

    def test_the_summary_says_something_is_wrong(self):
        key, cert = WarningsAreNotFailures.make_key(self)
        self.signing(enabled=True, private_key_path=key, certificate_path=cert)
        WarningsAreNotFailures.patch_certificate(self, days_left=-1)
        _, _, err = self.run_doctor()
        self.assertIn("stop a receipt being issued", err)

    def test_failures_go_to_stderr(self):
        """So a build script can separate them from the running commentary."""
        key, cert = WarningsAreNotFailures.make_key(self)
        self.signing(enabled=True, private_key_path=key, certificate_path=cert)
        WarningsAreNotFailures.patch_certificate(self, days_left=-1)
        _, out, err = self.run_doctor()
        self.assertNotIn("FAIL", out)
        self.assertIn("FAIL", err)


class AnUnreadableKeyIsCaught(DoctorTestCase):
    """--doctor said "Ready to issue receipts" while every receipt failed.

    It checked that the key file *existed* and that the certificate could be
    read, and never tried to load the key itself. An encrypted key with no
    passphrase therefore passed every check and then failed at the signing step
    of every single receipt -- the exact situation this command exists to catch
    before a customer is standing at the counter.
    """

    def encrypted_key(self):
        """A real key, encrypted, with the passphrase deliberately not stored."""
        folder = os.path.join(self.dir, "signing")
        os.makedirs(folder, exist_ok=True)
        key = os.path.join(folder, "private_key.pem")
        cert = os.path.join(folder, "certificate.pem")
        receipt_signing.generate_key_pair(key, cert, common_name="T",
                                          org_name="T", passphrase="secret")
        return key, cert

    def test_an_encrypted_key_with_no_passphrase_fails(self):
        key, cert = self.encrypted_key()
        self.signing(enabled=True, private_key_path=key, certificate_path=cert,
                     key_passphrase="")
        code, _, err = self.run_doctor()
        self.assertEqual(code, cli.EXIT_ENVIRONMENT)
        self.assertIn("encrypted and no passphrase", err)

    def test_it_says_what_to_do_about_it(self):
        key, cert = self.encrypted_key()
        self.signing(enabled=True, private_key_path=key, certificate_path=cert,
                     key_passphrase="")
        _, _, err = self.run_doctor()
        self.assertIn("Tools -> Settings -> Signing", err)

    def test_the_right_passphrase_passes(self):
        key, cert = self.encrypted_key()
        self.signing(enabled=True, private_key_path=key, certificate_path=cert,
                     key_passphrase="secret")
        code, out, _ = self.run_doctor()
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("signing", out)

    def test_an_unencrypted_key_passes(self):
        folder = os.path.join(self.dir, "signing")
        os.makedirs(folder, exist_ok=True)
        key = os.path.join(folder, "private_key.pem")
        cert = os.path.join(folder, "certificate.pem")
        receipt_signing.generate_key_pair(key, cert, common_name="T", org_name="T")
        self.signing(enabled=True, private_key_path=key, certificate_path=cert)
        code, _, _ = self.run_doctor()
        self.assertEqual(code, cli.EXIT_OK)


class TheKeyProblemIsNamed(unittest.TestCase):
    """pyHanko answers several distinct problems with the same silence."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rm-keyproblem-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make(self, passphrase=None):
        key = os.path.join(self.dir, "private_key.pem")
        cert = os.path.join(self.dir, "certificate.pem")
        receipt_signing.generate_key_pair(key, cert, common_name="T",
                                          org_name="T", passphrase=passphrase)
        return key, cert

    def test_an_encrypted_key_is_detected_from_the_file(self):
        key, _ = self.make(passphrase="secret")
        self.assertTrue(receipt_signing.key_is_encrypted(key))

    def test_a_plain_key_is_not(self):
        key, _ = self.make()
        self.assertFalse(receipt_signing.key_is_encrypted(key))

    def test_a_missing_file_is_not_reported_as_encrypted(self):
        self.assertFalse(receipt_signing.key_is_encrypted(
            os.path.join(self.dir, "nothing.pem")))

    def test_the_problem_names_the_missing_key(self):
        problem = receipt_signing.key_problem(
            os.path.join(self.dir, "nope.pem"), os.path.join(self.dir, "c.pem"))
        self.assertIn("no signing key", problem)

    def test_the_problem_names_the_missing_certificate(self):
        key, cert = self.make()
        os.remove(cert)
        self.assertIn("no certificate", receipt_signing.key_problem(key, cert))

    def test_the_problem_names_the_passphrase(self):
        key, cert = self.make(passphrase="secret")
        problem = receipt_signing.key_problem(key, cert, "")
        self.assertIn("encrypted and no passphrase", problem)

    def test_a_working_pair_has_no_problem(self):
        key, cert = self.make()
        self.assertEqual(receipt_signing.key_problem(key, cert), "")

    def test_a_passphrase_that_is_supplied_clears_it(self):
        key, cert = self.make(passphrase="secret")
        self.assertEqual(receipt_signing.key_problem(key, cert, "secret"), "")


class BrokenConfigStopsEarly(DoctorTestCase):
    def test_it_reports_the_config_and_gives_up(self):
        """Nothing below can run without config, so this one does stop."""
        import json
        with open(config.APP_SETTINGS_FILE, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": config.SCHEMA_VERSION,
                       "currency": {"decimals": 99}}, handle)
        code, _, err = self.run_doctor()
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("CONFIG ERROR", err)

    def test_an_unreadable_file_recovers_rather_than_stopping(self):
        """Deliberate: a damaged file falls back to defaults, it does not fail.

        Do no harm — an unreadable appsettings.json must not stop a shop
        trading, so it is only an *invalid value* that config refuses.
        """
        with open(config.APP_SETTINGS_FILE, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        code, out, _ = self.run_doctor()
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("app", out)


class TheBrowserCheck(DoctorTestCase):
    def test_a_missing_playwright_is_explained_with_the_command(self):
        cli._check_browser = self._real_browser
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
            else __builtins__.__import__

        def blocked(name, *args, **kwargs):
            if name.startswith("playwright"):
                raise ImportError("no playwright")
            return real_import(name, *args, **kwargs)

        import builtins
        builtins.__import__ = blocked
        try:
            _, _, err = self.run_doctor()
        finally:
            builtins.__import__ = real_import
        self.assertIn("pip install playwright", err)

    def test_a_failure_marks_the_whole_run_as_failed(self):
        report = cli._Report()
        err = io.StringIO()
        with redirect_stderr(err):
            report.fail("browser", "Chromium is not installed.")
        self.assertTrue(report.failed)
        self.assertIn("Chromium is not installed", err.getvalue())

    def test_a_warning_does_not(self):
        """The distinction the exit code depends on."""
        report = cli._Report()
        out = io.StringIO()
        with redirect_stdout(out):
            report.warn("signing", "expiring soon")
        self.assertFalse(report.failed)
        self.assertIn("WARN", out.getvalue())


class TheCommandLine(unittest.TestCase):
    def test_doctor_is_an_option(self):
        out = io.StringIO()
        with redirect_stdout(out):
            with self.assertRaises(SystemExit):
                cli.main(["--help"])
        self.assertIn("--doctor", out.getvalue())

    def test_doing_nothing_mentions_it(self):
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit):
                cli.main([])
        self.assertIn("--doctor", err.getvalue())

    def test_the_environment_exit_code_is_its_own(self):
        """A build script has to tell it apart from a config or render failure."""
        codes = {cli.EXIT_OK, cli.EXIT_CONFIG, cli.EXIT_TEMPLATE,
                 cli.EXIT_RENDER, cli.EXIT_SIGNING, cli.EXIT_ENVIRONMENT}
        self.assertEqual(len(codes), 6)


if __name__ == "__main__":
    unittest.main()
