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
from decimal import Decimal

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config             # noqa: E402
import tk_support          # noqa: E402
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


class WindowsLockContention(unittest.TestCase):
    """A contended lock on Windows raises EACCES, not EEXIST.

    Found as a test failing roughly one run in ten, which looked like flakiness
    and was not: `os.open(O_CREAT|O_EXCL)` racing another process's *delete* of
    the same name fails on Windows with a permission error rather than "already
    exists". The retry loop only caught EEXIST, so a lock hand-off that was
    working exactly as designed surfaced to the user as "Could not lock the
    invoice counter" and cost them the receipt they were saving.
    """

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-regress-lock-")
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_permission_error_is_retried_not_surfaced(self):
        import errno
        real_open = os.open
        calls = {"n": 0}

        def flaky_open(path, flags, *args):
            if str(path).endswith(".lock"):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise PermissionError(errno.EACCES, "Permission denied", str(path))
            return real_open(path, flags, *args)

        os.open = flaky_open
        try:
            number = invoice_counter.reserve("W")
        finally:
            os.open = real_open

        self.assertEqual(number, config.INVOICE_START_NUMBER)
        self.assertGreater(calls["n"], 1, "the failed attempt should have been retried")

    def test_a_permanent_permission_problem_still_reports_the_real_error(self):
        """Retrying must not turn a read-only folder into "close the other copy"."""
        import errno
        real_open = os.open

        def always_denied(path, flags, *args):
            if str(path).endswith(".lock"):
                raise PermissionError(errno.EACCES, "Permission denied", str(path))
            return real_open(path, flags, *args)

        os.open = always_denied
        try:
            with self.assertRaises(invoice_counter.CounterError) as ctx:
                invoice_counter._FileLock(
                    os.path.join(self.dir, "invoices", "c.json.lock"),
                    timeout=0.2).__enter__()
        finally:
            os.open = real_open

        message = str(ctx.exception)
        self.assertIn("Permission denied", message)
        self.assertNotIn("Another copy", message,
                         "a permission problem must not be blamed on a second instance")

    def test_a_genuinely_fatal_error_is_not_retried(self):
        import errno
        real_open = os.open

        def broken(path, flags, *args):
            if str(path).endswith(".lock"):
                raise OSError(errno.ENOSPC, "No space left on device", str(path))
            return real_open(path, flags, *args)

        os.open = broken
        try:
            with self.assertRaises(invoice_counter.CounterError) as ctx:
                invoice_counter.reserve("W")
        finally:
            os.open = real_open
        self.assertIn("No space", str(ctx.exception))


class TreeviewEatsLeadingZeros(unittest.TestCase):
    """`tree.item(row)["values"]` runs every cell through Tcl type guessing.

    A UPC of "0000000000000" came back as the integer 0 and a serial of "007"
    as 7. Leading zeros are ordinary on barcodes -- UPC-A codes routinely start
    with one -- so this was silent data loss on a document that gets signed and
    handed to a customer. Found while building barcode scanning: a rescan could
    not find the line it had just added, because the code stored was not the
    code scanned.

    `tree.set(row)` returns the strings as stored, which is what item_at uses.
    """

    def setUp(self):
        import tkinter as tk
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-regress-zeros-")
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)
        self.root = tk.Tk()
        self.root.withdraw()
        import main
        self.app = main.ReceiptApp(self.root)

    def tearDown(self):
        tk_support.destroy(self)
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def add(self, **kw):
        import tkinter as tk
        item = {"sku": "A", "desc": "Thing", "serial": "", "qty": "1",
                "price": "1", "discount": "0", "tax": "0", "warranty": ""}
        item.update(kw)
        row = self.app.items_tree.insert("", tk.END,
                                         values=self.app.item_to_row(item))
        return row

    def test_a_barcode_keeps_its_leading_zeros(self):
        row = self.add(sku="0000000000000")
        self.assertEqual(self.app.item_at(row)["sku"], "0000000000000")

    def test_a_short_serial_keeps_its_leading_zeros(self):
        row = self.add(serial="007")
        self.assertEqual(self.app.item_at(row)["serial"], "007")

    def test_the_old_accessor_still_demonstrates_the_bug(self):
        """Guards the fix: if this ever stops mangling, the workaround can go."""
        row = self.add(sku="0000000000000")
        raw = self.app.items_tree.item(row)["values"]
        self.assertIn(0, raw, "Tk no longer coerces; item_at may be simplifiable")

    def test_values_come_back_as_text(self):
        row = self.add(qty="2")
        self.assertIsInstance(self.app.item_at(row)["qty"], str)

    def test_a_decimal_price_is_not_turned_into_a_float(self):
        row = self.add(price="10.50")
        self.assertEqual(self.app.item_at(row)["price"], "10.50")

    def test_a_price_with_a_trailing_zero_keeps_it(self):
        """"10.00" becoming 10.0 would change what prints on the receipt."""
        row = self.add(price="10.00")
        self.assertEqual(self.app.item_at(row)["price"], "10.00")


class RetiredCertificatesFollowTheirKey(unittest.TestCase):
    """`SIGNING_DIR` was computed at import time and never saw set_app_dir().

    Found by running --doctor and noticing a signing certificate in the real
    project folder: the suite had quietly written 65 retired certificates there,
    one per run since the key-rotation feature landed. The same bug meant
    `cli.py --config-dir` archived into the wrong folder.

    receipt_signing deliberately does not import config, so the archive folder
    is derived from the certificate being retired instead -- which is also just
    more correct, since a retired certificate belongs beside its replacement.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rm-regress-certs-")
        self.other = tempfile.mkdtemp(prefix="rm-regress-certs2-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.rmtree(self.other, ignore_errors=True)

    def make_key(self, folder):
        import receipt_signing
        return receipt_signing.generate_key_pair(
            os.path.join(folder, "private_key.pem"),
            os.path.join(folder, "certificate.pem"),
            common_name="Test", org_name="Test")

    def test_the_archive_sits_beside_the_certificate(self):
        import receipt_signing
        cert = os.path.join(self.dir, "certificate.pem")
        self.assertEqual(receipt_signing.known_certs_dir(cert),
                         os.path.join(self.dir, "previous_certificates"))

    def test_rotating_a_key_archives_next_to_that_key(self):
        import receipt_signing
        _, cert = self.make_key(self.dir)
        receipt_signing.remember_current_certificate(cert)
        self.assertTrue(os.path.isdir(
            os.path.join(self.dir, "previous_certificates")))

    def test_it_does_not_write_into_the_module_directory(self):
        """The actual leak: certificates landing in the project folder."""
        import receipt_signing
        module_archive = os.path.join(
            os.path.dirname(os.path.abspath(receipt_signing.__file__)),
            "signing", "previous_certificates")
        before = (len(os.listdir(module_archive))
                  if os.path.isdir(module_archive) else 0)

        _, cert = self.make_key(self.dir)
        receipt_signing.remember_current_certificate(cert)

        after = (len(os.listdir(module_archive))
                 if os.path.isdir(module_archive) else 0)
        self.assertEqual(before, after,
                         "archiving wrote into the project's own signing folder")

    def test_two_key_locations_keep_separate_archives(self):
        import receipt_signing
        _, first = self.make_key(self.dir)
        _, second = self.make_key(self.other)
        receipt_signing.remember_current_certificate(first)

        self.assertEqual(receipt_signing.known_certificate_paths(second),
                         [second], "the other key's archive must not leak in")

    def test_known_paths_lists_the_current_certificate_first(self):
        import receipt_signing
        _, cert = self.make_key(self.dir)
        receipt_signing.remember_current_certificate(cert)
        paths = receipt_signing.known_certificate_paths(cert)
        self.assertEqual(paths[0], cert)
        self.assertEqual(len(paths), 2)


class FilenameTidyingAteRealCharacters(unittest.TestCase):
    """"Dr. Smith" came out as "Dr.Smith".

    build_pdf_filename substituted the placeholders and then tidied the result,
    collapsing any run of "-_ ." to remove the dangling separator a blank value
    leaves behind. But a tidy-up on the finished string cannot tell a separator
    the *pattern* supplied from one that is part of somebody's name, so every
    "Dr. ", "J. R." and "Ltd. " lost its space.

    It is built from the pattern's segments now, so a blank value drops only the
    separator beside it and nothing ever touches a character that came from a
    value.
    """

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-regress-name-")
        shutil.copy(os.path.join(PROJ, "tests", "fixtures", "env", "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def pattern(self, value):
        config.update_app_settings({"invoice": {"filename_pattern": value}})

    def name(self, **kw):
        args = {"inv": "INV-1", "date": "", "cust": "", "email": "", "phone": ""}
        args.update(kw)
        return receipt_service.build_pdf_filename(
            args["inv"], args["date"], args["cust"], args["email"], args["phone"])

    def test_a_full_stop_in_a_name_keeps_its_space(self):
        self.pattern("{invoice_no}-{name}")
        self.assertEqual(self.name(cust="Dr. Smith"), "INV-1-Dr. Smith.pdf")

    def test_several_initials_survive(self):
        self.pattern("{invoice_no}-{name}")
        self.assertEqual(self.name(cust="J. R. Hartley"), "INV-1-J. R. Hartley.pdf")

    def test_a_hyphenated_name_survives(self):
        self.pattern("{invoice_no}-{name}")
        self.assertEqual(self.name(cust="Anne-Marie"), "INV-1-Anne-Marie.pdf")

    def test_a_blank_value_still_drops_its_separator(self):
        """The behaviour the tidy-up existed for, kept."""
        self.pattern("{invoice_no}-{name}-{phone}")
        self.assertEqual(self.name(phone="555"), "INV-1-555.pdf")

    def test_a_blank_value_at_the_end_leaves_no_trailing_separator(self):
        self.pattern("{invoice_no}-{name}")
        self.assertEqual(self.name(), "INV-1.pdf")

    def test_every_value_blank_falls_back_to_the_number(self):
        self.pattern("{invoice_no}-{name}-{phone}")
        self.assertEqual(self.name(), "INV-1.pdf")

    def test_a_multi_character_separator_is_honoured(self):
        self.pattern("{invoice_no} -- {name}")
        self.assertEqual(self.name(cust="Ada"), "INV-1 -- Ada.pdf")


class PartlyTaggedOrderLostItsShipping(unittest.TestCase):
    """Tagging one line and not the rest silently dropped the flat fee.

    `rows()` returned the per-shipment fees and ignored `flat_shipping` as soon
    as any line carried a tag -- so an order where one item ships from the other
    warehouse and the rest go as usual charged for the one and not the others.
    The customer is undercharged and the receipt shows nothing to explain it,
    which is the worst way for a money bug to behave.
    """

    def line(self, sku, shipment=""):
        item = {"sku": sku, "qty": 1, "price": "10"}
        if shipment:
            item["shipment"] = shipment
        return item

    def test_the_flat_fee_survives_alongside_a_group(self):
        import shipments
        items = [self.line("A", "W1"), self.line("B")]
        rows, total = shipments.rows(
            {"shipments": [{"id": "W1", "fee": "500"}]}, items, 2,
            flat_shipping="250")
        self.assertEqual(total, Decimal("750.00"))
        self.assertEqual(len(rows), 2)

    def test_the_untagged_lines_get_their_own_row(self):
        import shipments
        items = [self.line("A", "W1"), self.line("B")]
        rows, _ = shipments.rows({"shipments": [{"id": "W1", "fee": "500"}]},
                                 items, 2, flat_shipping="250")
        self.assertEqual(rows[-1][0], shipments.UNGROUPED)
        self.assertEqual(rows[-1][3], Decimal("250.00"))

    def test_the_marker_counts_it(self):
        """Two charges means "1 of 2", or the second looks like a mistake."""
        import shipments
        items = [self.line("A", "W1"), self.line("B")]
        rows, _ = shipments.rows({"shipments": [{"id": "W1", "fee": "500"}]},
                                 items, 2, flat_shipping="250")
        self.assertEqual([r[2] for r in rows], [2, 2])

    def test_no_flat_fee_means_no_extra_row(self):
        import shipments
        items = [self.line("A", "W1"), self.line("B")]
        rows, total = shipments.rows({"shipments": [{"id": "W1", "fee": "500"}]},
                                     items, 2, flat_shipping=0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(total, Decimal("500.00"))

    def test_every_line_tagged_needs_no_extra_row(self):
        import shipments
        items = [self.line("A", "W1"), self.line("B", "W2")]
        rows, _ = shipments.rows(
            {"shipments": [{"id": "W1", "fee": "5"}, {"id": "W2", "fee": "7"}]},
            items, 2, flat_shipping="250")
        self.assertEqual(len(rows), 2, "nothing is ungrouped, so no flat row")

    def test_an_untagged_receipt_is_unchanged(self):
        import shipments
        rows, total = shipments.rows({}, [self.line("A"), self.line("B")], 2,
                                     flat_shipping="250")
        self.assertEqual(rows, [])
        self.assertEqual(total, Decimal("250.00"))


if __name__ == "__main__":
    unittest.main()
