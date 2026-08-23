"""Stage 4 — the invoice counter.

PLAN-generalization.md calls this the riskiest correctness change in the project
and gives it its own gate. The failure mode being designed against is a
**duplicate invoice number on a legal document**, so these tests care much more
about what must never happen than about the happy path:

  * the next number is identical before and after the counter is introduced;
  * two concurrent consumers never receive the same number;
  * a deleted or renamed receipt does not reset the sequence;
  * a failed generation burns its number and says so, rather than reusing it;
  * merely looking at the form consumes nothing.

Run: python -m unittest discover -s tests
"""
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import time
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config             # noqa: E402
import invoice_counter    # noqa: E402
import receipt_service    # noqa: E402


def _reserve_in_child(app_dir, code, count, queue):
    """Run in a separate *process* to make the cross-process lock real."""
    sys.path.insert(0, PROJ)
    import config as child_config
    import invoice_counter as child_counter

    child_config.set_app_dir(app_dir)
    got = []
    try:
        for _ in range(count):
            got.append(child_counter.reserve(code))
    except Exception as exc:                       # pragma: no cover - diagnostic
        queue.put(("error", repr(exc)))
        return
    queue.put(("ok", got))


class CounterTestCase(unittest.TestCase):
    def setUp(self):
        self._original_app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-stage4-")
        self.invoices = os.path.join(self.dir, "invoices")
        os.makedirs(self.invoices, exist_ok=True)
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._original_app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def touch(self, *names):
        for name in names:
            open(os.path.join(self.invoices, name), "wb").close()

    def counter_state(self):
        with open(invoice_counter.counter_path(), encoding="utf-8") as f:
            return json.load(f)


class SeedingPreservesTheSequence(CounterTestCase):
    """The whole migration is worthless if the next number moves."""

    def test_seeded_from_the_highest_existing_receipt(self):
        self.touch("INV-W1001.pdf", "INV-W1007-15 Jan 2026-Ada.pdf", "INV-W1003.pdf")
        self.assertEqual(invoice_counter.peek("W"), 1008)

    def test_matches_what_the_old_filename_scan_would_have_returned(self):
        self.touch("INV-W1001.pdf", "INV-W1042.pdf", "INV-S1002.pdf", "INV-1005.pdf")
        for code in ("W", "S"):
            scanned = invoice_counter.scan_filenames(code)
            self.assertEqual(invoice_counter.peek(code), scanned + 1,
                             f"series {code} moved when the counter was introduced")

    def test_legacy_unlettered_files_belong_to_the_claiming_series(self):
        self.touch("INV-1005.pdf")
        self.assertEqual(invoice_counter.peek("W"), 1006, "Online inherits INV-####")
        self.assertEqual(invoice_counter.peek("S"), config.INVOICE_START_NUMBER,
                         "In Store must not inherit them")

    def test_empty_folder_starts_at_the_configured_number(self):
        self.assertEqual(invoice_counter.peek("W"), config.INVOICE_START_NUMBER)

    def test_seed_is_recorded(self):
        self.touch("INV-W1001.pdf")
        invoice_counter.peek("W")
        self.assertEqual(self.counter_state()["series"]["W"]["seeded_from"], "filenames")


class PeekConsumesNothing(CounterTestCase):
    def test_repeated_peeks_return_the_same_number(self):
        self.assertEqual([invoice_counter.peek("W") for _ in range(5)],
                         [config.INVOICE_START_NUMBER] * 5)

    def test_service_level_peek_also_consumes_nothing(self):
        prefix = receipt_service.get_invoice_prefix("Online")
        first = receipt_service.get_next_invoice_number(prefix)
        second = receipt_service.get_next_invoice_number(prefix)
        self.assertEqual(first, second)


class ReserveConsumes(CounterTestCase):
    def test_sequential_reserves_never_repeat(self):
        got = [invoice_counter.reserve("W") for _ in range(5)]
        self.assertEqual(got, sorted(got))
        self.assertEqual(len(set(got)), 5)

    def test_reserve_then_peek_shows_the_next_one(self):
        used = invoice_counter.reserve("W")
        self.assertEqual(invoice_counter.peek("W"), used + 1)

    def test_series_are_independent(self):
        self.assertEqual(invoice_counter.reserve("W"), config.INVOICE_START_NUMBER)
        self.assertEqual(invoice_counter.reserve("S"), config.INVOICE_START_NUMBER)

    def test_reservation_is_durable_across_a_reload(self):
        used = invoice_counter.reserve("W")
        state = self.counter_state()
        self.assertEqual(state["series"]["W"]["next"], used + 1,
                         "the increment must be on disk before the caller gets the number")


class ConcurrentConsumersNeverCollide(CounterTestCase):
    """Two app instances, or app + CLI, must not be handed the same number."""

    def test_two_processes_reserving_at_once(self):
        per_process, processes = 25, 4
        queue = multiprocessing.Queue()
        workers = [
            multiprocessing.Process(target=_reserve_in_child,
                                    args=(self.dir, "W", per_process, queue))
            for _ in range(processes)
        ]
        for worker in workers:
            worker.start()

        results = [queue.get(timeout=90) for _ in range(processes)]
        for worker in workers:
            worker.join(timeout=30)

        numbers = []
        for status, payload in results:
            self.assertEqual(status, "ok", f"child process failed: {payload}")
            numbers.extend(payload)

        expected = per_process * processes
        self.assertEqual(len(numbers), expected)
        self.assertEqual(len(set(numbers)), expected,
                         "the same invoice number was issued twice")
        self.assertEqual(sorted(numbers),
                         list(range(config.INVOICE_START_NUMBER,
                                    config.INVOICE_START_NUMBER + expected)),
                         "the sequence should be contiguous with no gaps or repeats")


class ReconciliationNeverResets(CounterTestCase):
    def test_deleting_a_receipt_does_not_rewind_the_counter(self):
        self.touch("INV-W1001.pdf", "INV-W1002.pdf")
        self.assertEqual(invoice_counter.reserve("W"), 1003)

        os.remove(os.path.join(self.invoices, "INV-W1002.pdf"))
        os.remove(os.path.join(self.invoices, "INV-W1001.pdf"))

        self.assertEqual(invoice_counter.peek("W"), 1004,
                         "a deleted receipt must not free its number for reuse")

    def test_renaming_a_receipt_does_not_rewind_the_counter(self):
        self.touch("INV-W1005.pdf")
        self.assertEqual(invoice_counter.reserve("W"), 1006)
        os.rename(os.path.join(self.invoices, "INV-W1005.pdf"),
                  os.path.join(self.invoices, "archived-copy.pdf"))
        self.assertEqual(invoice_counter.peek("W"), 1007)

    def test_files_ahead_of_the_counter_pull_it_forward(self):
        """A restored backup must not cause the next receipt to duplicate one."""
        invoice_counter.reserve("W")                       # counter now at start+1
        self.touch("INV-W9000.pdf")
        self.assertEqual(invoice_counter.peek("W"), 9001)

    def test_reconciliation_can_be_switched_off(self):
        invoice_counter.reserve("W")
        self.touch("INV-W9000.pdf")
        settings = config.load_app_settings()
        settings["invoice"]["reconcile_with_filenames"] = False
        self.assertEqual(invoice_counter.peek("W", settings),
                         config.INVOICE_START_NUMBER + 1,
                         "with reconciliation off the counter alone decides")

    def test_a_corrupt_counter_file_refuses_rather_than_restarting(self):
        self.touch("INV-W1001.pdf")
        path = invoice_counter.counter_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ not json")
        with self.assertRaises(invoice_counter.CounterError) as ctx:
            invoice_counter.peek("W")
        self.assertIn("could not be read", str(ctx.exception))


class ManuallyEnteredNumbers(CounterTestCase):
    def test_counter_advances_past_a_hand_typed_number(self):
        invoice_counter.claim_at_least("W", 5000)
        self.assertEqual(invoice_counter.peek("W"), 5001,
                         "a number typed by hand must never be handed out again")

    def test_a_lower_hand_typed_number_does_not_rewind(self):
        invoice_counter.claim_at_least("W", 5000)
        invoice_counter.claim_at_least("W", 10)
        self.assertEqual(invoice_counter.peek("W"), 5001)


class BurnedNumbers(CounterTestCase):
    def test_a_failed_generation_keeps_the_number_consumed(self):
        used = invoice_counter.reserve("W")
        invoice_counter.note_unused("W", used, "Playwright is not installed")
        self.assertEqual(invoice_counter.peek("W"), used + 1,
                         "returning the number would reopen the race it closes")

    def test_the_gap_is_logged(self):
        with self.assertLogs("receipt_maker", level="WARNING") as captured:
            invoice_counter.note_unused("W", 1001, "signing key not found")
        joined = "\n".join(captured.output)
        self.assertIn("1001", joined)
        self.assertIn("signing key not found", joined)


class LockBehaviour(CounterTestCase):
    def test_a_stale_lock_is_broken_rather_than_wedging_the_app(self):
        path = invoice_counter.counter_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lock = path + ".lock"
        with open(lock, "w", encoding="utf-8") as f:
            f.write("99999")
        old = time.time() - (invoice_counter.LOCK_STALE_SECONDS + 30)
        os.utime(lock, (old, old))

        self.assertEqual(invoice_counter.peek("W"), config.INVOICE_START_NUMBER)
        self.assertFalse(os.path.exists(lock), "the stale lock should be gone")

    def test_lock_is_released_even_when_the_body_raises(self):
        path = invoice_counter.counter_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with self.assertRaises(ValueError):
            with invoice_counter._FileLock(path):
                raise ValueError("boom")
        self.assertFalse(os.path.exists(path + ".lock"))


class InvoiceConfigValidation(unittest.TestCase):
    def settings(self, **invoice):
        s = config.default_app_settings()
        s["invoice"].update(invoice)
        return s

    def assert_rejects(self, key, settings):
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(settings, "appsettings.json")
        self.assertEqual(ctx.exception.key, key)

    def test_defaults_are_valid(self):
        config.validate(config.default_app_settings(), "appsettings.json")

    def test_prefix_cannot_contain_path_characters(self):
        self.assert_rejects("invoice.prefix", self.settings(prefix="INV/"))

    def test_prefix_cannot_end_in_a_digit(self):
        """'INV1' + 1001 reads as INV11001 -- the boundary becomes unrecoverable."""
        self.assert_rejects("invoice.prefix", self.settings(prefix="INV1"))

    def test_start_must_be_a_non_negative_int(self):
        self.assert_rejects("invoice.start", self.settings(start=-1))
        self.assert_rejects("invoice.start", self.settings(start="1001"))

    def test_counter_file_required(self):
        self.assert_rejects("invoice.counter_file", self.settings(counter_file="  "))

    def test_reconcile_flag_must_be_boolean(self):
        self.assert_rejects("invoice.reconcile_with_filenames",
                            self.settings(reconcile_with_filenames="yes"))


class MigrationToV4(unittest.TestCase):
    def test_v3_config_gains_invoice_settings_preserving_prefix_and_start(self):
        v3 = config.default_app_settings()
        v3[config.SCHEMA_VERSION_KEY] = 3
        del v3["invoice"]
        settings, changed = config.migrate(v3)

        self.assertTrue(changed)
        self.assertEqual(settings[config.SCHEMA_VERSION_KEY], 4)
        self.assertEqual(settings["invoice"]["prefix"], config.INVOICE_PREFIX_BASE)
        self.assertEqual(settings["invoice"]["start"], config.INVOICE_START_NUMBER)

    def test_v1_config_reaches_v4_in_one_step(self):
        settings, _ = config.migrate({
            "company": {"name": "Acme", "address": "a", "phone": "1",
                        "email": "e@x.c", "logo_path": ""},
        })
        self.assertEqual(settings[config.SCHEMA_VERSION_KEY], 4)
        self.assertEqual(settings["company"]["name"], "Acme")
        self.assertEqual(settings["currency"]["symbol"], "Rs.")
        self.assertEqual(settings["invoice"]["prefix"], "INV-")



if __name__ == "__main__":
    unittest.main()
