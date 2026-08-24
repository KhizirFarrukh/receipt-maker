"""Regressions — one test per bug actually found, so it cannot come back.

Each of these was reproduced before being fixed. The comments say what the bug
was and why it mattered, because a bare assertion tends to get "simplified" away
by someone who cannot see what it was protecting.

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

import config             # noqa: E402
import invoice_counter    # noqa: E402
import receipt_service    # noqa: E402


class CounterWorksInAFreshFolder(unittest.TestCase):
    """The counter's lock file was created before its directory existed.

    First run against an output folder that did not exist yet failed with
    "Could not lock the invoice counter" instead of simply starting the
    sequence -- reachable on a fresh install, or with counter_file pointed
    anywhere not already created.
    """

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-regress-")
        config.set_app_dir(self.dir)     # deliberately no invoices/ created

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_peek_creates_what_it_needs(self):
        self.assertEqual(invoice_counter.peek("W"), config.INVOICE_START_NUMBER)

    def test_reserve_creates_what_it_needs(self):
        self.assertEqual(invoice_counter.reserve("W"), config.INVOICE_START_NUMBER)

    def test_counter_file_lands_in_the_configured_place(self):
        invoice_counter.reserve("W")
        self.assertTrue(os.path.isfile(invoice_counter.counter_path()))

    def test_no_lock_file_is_left_behind(self):
        invoice_counter.reserve("W")
        self.assertFalse(os.path.exists(invoice_counter.counter_path() + ".lock"))

    def test_a_counter_file_in_a_deep_path_still_works(self):
        settings = config.load_app_settings()
        settings["invoice"]["counter_file"] = "state/nested/deeper/counters.json"
        self.assertEqual(invoice_counter.peek("W", settings), config.INVOICE_START_NUMBER)


class ReservedWindowsFilenames(unittest.TestCase):
    """A receipt whose whole filename stem is a DOS device name cannot be written.

    "CON.pdf" fails on Windows regardless of extension, and the invoice number is
    user-editable and leads the filename -- so a receipt could fail to save with
    a confusing OS error. Only the *whole* stem is reserved; "INV-W1001-CON" is
    fine and must not be mangled.
    """

    def test_bare_device_name_is_made_writable(self):
        self.assertEqual(receipt_service.build_pdf_filename("CON", "", "", "", ""),
                         "CON_.pdf")

    def test_case_insensitive(self):
        self.assertEqual(receipt_service.avoid_reserved_name("con"), "con_")
        self.assertEqual(receipt_service.avoid_reserved_name("Prn"), "Prn_")

    def test_numbered_devices(self):
        for name in ("COM1", "LPT9", "NUL", "AUX"):
            self.assertEqual(receipt_service.avoid_reserved_name(name), name + "_")

    def test_device_name_as_part_of_a_longer_name_is_untouched(self):
        for stem in ("INV-W1001-CON", "CONSOLE", "NULL", "COM10"):
            self.assertEqual(receipt_service.avoid_reserved_name(stem), stem,
                             "only the whole stem is reserved")

    def test_normal_filenames_are_unaffected(self):
        self.assertEqual(
            receipt_service.build_pdf_filename("INV-W1001", "15 Jan 2026", "Ada", "", ""),
            "INV-W1001-15 Jan 2026-Ada.pdf")


class StartupReportsConfigProblemsReadably(unittest.TestCase):
    """An invalid settings file used to escape __init__ as a traceback.

    In the packaged build that is a raw traceback dialog, or nothing at all --
    a windowed exe has no console. A settings mistake is the most likely reason
    the app will not open and the one a user can actually fix, so it has to
    arrive as a sentence naming the file and the key.
    """

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-regress-cfg-")

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_broken(self, section, key, value):
        settings = config.default_app_settings()
        settings[section][key] = value
        with open(os.path.join(self.dir, "appsettings.json"), "w", encoding="utf-8") as f:
            json.dump(settings, f)
        config.set_app_dir(self.dir)

    def test_launch_reports_a_config_error_instead_of_crashing(self):
        import main

        self.write_broken("currency", "decimals", 99)
        shown = {}
        original = main.show_error
        try:
            main.show_error = lambda parent, title, summary, detail=None: shown.update(
                title=title, summary=summary)
            code = main.launch()
        finally:
            main.show_error = original

        self.assertEqual(code, 2, "a settings problem should exit non-zero, not raise")
        self.assertIn("settings", shown.get("title", "").lower())
        self.assertIn("currency.decimals", shown.get("summary", ""),
                      "the message must name the offending key")
        self.assertIn("appsettings.json", shown.get("summary", ""),
                      "and the file it is in")


class CancelledSaveLeavesAnExplainedGap(unittest.TestCase):
    """Cancelling the overwrite prompt burned an invoice number silently.

    The number is claimed before rendering and deliberately never returned, so
    cancelling leaves a gap in the sequence. That is fine -- an *unexplained*
    gap is not, which is the whole reason burned numbers are logged.
    """

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-regress-gap-")
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_cancelling_logs_the_burned_number(self):
        number = invoice_counter.reserve("W")
        with self.assertLogs("receipt_maker", level="WARNING") as captured:
            invoice_counter.note_unused("W", f"INV-W{number}", "save cancelled by the user")
        joined = "\n".join(captured.output)
        self.assertIn(str(number), joined)
        self.assertIn("cancelled", joined)

    def test_the_number_is_not_handed_out_again(self):
        number = invoice_counter.reserve("W")
        invoice_counter.note_unused("W", number, "save cancelled by the user")
        self.assertEqual(invoice_counter.peek("W"), number + 1)


if __name__ == "__main__":
    unittest.main()
