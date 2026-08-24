"""The invoice sequence: a counter file that no two processes can share a number from.

Numbers used to be derived by scanning the PDFs in ``invoices/`` for the highest
one. That works only while filenames are fixed: as soon as they are configurable
a renamed or reordered pattern stops parsing, the scan finds nothing, and the
sequence silently restarts at ``start`` -- reissuing numbers that are already on
customers' receipts. A duplicate number on a legal document is the failure this
module exists to prevent.

So the counter file owns the sequence, and filename scanning is demoted to a
cross-check. Two rules follow from "never issue a duplicate":

* **Reserve and keep.** A number is claimed by an atomic increment *before* the
  receipt is rendered, so a second app instance -- or the CLI running alongside
  the GUI -- cannot take the same one. It stays consumed even if the render then
  fails, because handing it back would reopen exactly the race the reservation
  closes. That leaves gaps in the sequence, which is an audit concern in some
  places, so every burned number is logged with the reason.
* **Reconcile, never reset.** If the filenames run ahead of the counter (restored
  backups, a counter file lost), the counter jumps forward to match. If the
  counter runs ahead (a deleted or renamed PDF), it stays where it is and warns.
  Following the filenames downward is precisely how a duplicate happens.

Cross-process exclusion uses an ``O_EXCL`` lock file, which is atomic on Windows
and POSIX alike and needs no third-party dependency.
"""
import errno
import json
import logging
import os
import re
import time

import config

logger = logging.getLogger("receipt_maker")

COUNTER_SCHEMA_VERSION = 1
#: How long to wait for another process to release the lock before assuming it
#: died. Generation takes milliseconds, so anything this old is a crashed run.
LOCK_TIMEOUT_SECONDS = 10.0
LOCK_STALE_SECONDS = 60.0
_LOCK_POLL_SECONDS = 0.05


class CounterError(RuntimeError):
    """The invoice sequence could not be read or advanced."""


# ------------------- paths -------------------
def counter_path(settings=None):
    settings = settings if settings is not None else config.load_app_settings()
    configured = str(settings.get("invoice", {}).get("counter_file", "")).strip()
    if not configured:
        configured = config.DEFAULT_APP_SETTINGS["invoice"]["counter_file"]
    if os.path.isabs(configured):
        return configured
    return os.path.join(config.APP_DIR, configured)


def invoice_prefix(settings=None):
    settings = settings if settings is not None else config.load_app_settings()
    return settings.get("invoice", {}).get("prefix", config.INVOICE_PREFIX_BASE)


def start_number(settings=None):
    settings = settings if settings is not None else config.load_app_settings()
    return int(settings.get("invoice", {}).get("start", config.INVOICE_START_NUMBER))


# ------------------- cross-process lock -------------------
class _FileLock:
    """Mutual exclusion via an atomically created lock file.

    ``O_CREAT | O_EXCL`` either creates the file or fails -- there is no window
    between checking and creating, which is what makes it safe between
    processes. A lock older than LOCK_STALE_SECONDS is treated as abandoned by a
    crashed run and broken, so a crash cannot wedge the app permanently.
    """

    def __init__(self, path, timeout=LOCK_TIMEOUT_SECONDS):
        self.path = path + ".lock"
        self.timeout = timeout
        self._fd = None

    def __enter__(self):
        # The lock lives beside the counter file, so its directory has to exist
        # before the lock can be taken -- otherwise the very first run against a
        # fresh output folder fails with "could not lock" instead of just working.
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        except OSError as exc:
            raise CounterError(
                f"Could not create the folder for the invoice counter:\n{exc}") from exc

        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return self
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise CounterError(
                        f"Could not lock the invoice counter:\n{exc}") from exc
                if self._break_if_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise CounterError(
                        "Another copy of the app is still writing the invoice "
                        "number and did not finish in time. Close any other "
                        "instance and try again."
                    ) from exc
                time.sleep(_LOCK_POLL_SECONDS)

    def _break_if_stale(self):
        try:
            age = time.time() - os.path.getmtime(self.path)
        except OSError:
            return True          # vanished underneath us; retry immediately
        if age < LOCK_STALE_SECONDS:
            return False
        logger.warning("Breaking a stale invoice-counter lock (%.0fs old): %s",
                       age, self.path)
        try:
            os.remove(self.path)
        except OSError:
            pass
        return True

    def __exit__(self, *exc_info):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False


# ------------------- counter file -------------------
def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        # Refuse rather than starting a fresh sequence: an unreadable counter
        # whose numbers we silently restart is how duplicates get issued.
        raise CounterError(
            f"The invoice counter file could not be read:\n  {path}\n{exc}\n\n"
            f"Fix or remove the file. Removing it makes the app rebuild the "
            f"sequence from the receipts already in the output folder."
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("series"), dict):
        raise CounterError(
            f"The invoice counter file is not in the expected format:\n  {path}")
    return data


def _write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError as exc:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise CounterError(f"Could not update the invoice counter:\n{exc}") from exc


def _blank():
    return {"schema_version": COUNTER_SCHEMA_VERSION, "series": {}}


# ------------------- filename scan (now only a cross-check) -------------------
def scan_filenames(code, settings=None):
    """Highest number already present in the output folder for one series.

    This drove numbering before Stage 4 and is kept as the seed for a new
    counter and as the reconciliation cross-check. Returns None when nothing
    matches, which is different from 0.
    """
    settings = settings if settings is not None else config.load_app_settings()
    prefix = invoice_prefix(settings)
    output_dir = config.OUTPUT_DIR
    if not os.path.isdir(output_dir):
        return None

    letter = re.escape(code)
    for entry in config.receipt_types(settings):
        if str(entry.get("code", "")).upper() == code.upper() and entry.get("legacy_unlettered"):
            letter = f"{letter}?"     # this series also owns the old INV-#### files
            break
    pattern = re.compile(
        rf"^{re.escape(prefix)}{letter}(\d+)(?:-.*)?\.pdf$", re.IGNORECASE)

    highest = None
    try:
        names = os.listdir(output_dir)
    except OSError:
        return None
    for name in names:
        match = pattern.match(name)
        if match:
            number = int(match.group(1))
            if highest is None or number > highest:
                highest = number
    return highest


def _reconcile(state, code, settings):
    """Bring one series up to the filenames if they are ahead. Returns next number.

    Deliberately one-directional. Filenames ahead means numbers exist that the
    counter would hand out again, so it jumps forward. Filenames *behind* means a
    receipt was deleted, renamed or archived -- the number was still issued, so
    the counter holds its ground and only warns.
    """
    series = state["series"].setdefault(code, {})
    configured_start = start_number(settings)
    next_number = series.get("next")
    if not isinstance(next_number, int) or next_number < 0:
        next_number = None

    scanned = None
    if settings.get("invoice", {}).get("reconcile_with_filenames", True):
        scanned = scan_filenames(code, settings)

    if next_number is None:
        # First use: seed from the receipts already on disk so the sequence
        # continues exactly where filename-derived numbering left off.
        next_number = (scanned + 1) if scanned is not None else configured_start
        series["next"] = next_number
        series["seeded_from"] = "filenames" if scanned is not None else "config"
        return next_number, True

    if scanned is not None and scanned >= next_number:
        logger.warning(
            "Invoice counter for series %r was behind the receipts on disk "
            "(counter %d, highest file %d); advancing to avoid reissuing a "
            "number that is already on a receipt.", code, next_number, scanned)
        next_number = scanned + 1
        series["next"] = next_number
        return next_number, True

    if scanned is not None and scanned + 1 < next_number:
        logger.warning(
            "Invoice counter for series %r is ahead of the receipts on disk "
            "(counter %d, highest file %d). This is expected after a failed "
            "generation or a moved/deleted receipt; the counter is kept so no "
            "number is ever issued twice.", code, next_number, scanned)

    return next_number, False


# ------------------- public API -------------------
def peek(code, settings=None):
    """The number this series would issue next, without consuming it.

    Used for the form's editable invoice-number field: merely opening the app,
    or switching receipt type, must not burn a number.
    """
    settings = settings if settings is not None else config.load_app_settings()
    path = counter_path(settings)
    with _FileLock(path):
        state = _read(path) or _blank()
        number, changed = _reconcile(state, code, settings)
        if changed:
            _write(path, state)
    return number


def reserve(code, settings=None):
    """Atomically consume and return the next number for this series.

    The increment happens under the lock and is persisted before the caller gets
    the number, so a concurrent process can never be handed the same one.
    """
    settings = settings if settings is not None else config.load_app_settings()
    path = counter_path(settings)
    with _FileLock(path):
        state = _read(path) or _blank()
        number, _ = _reconcile(state, code, settings)
        state["series"][code]["next"] = number + 1
        _write(path, state)
    logger.info("Reserved invoice number %s%s%d", invoice_prefix(settings), code, number)
    return number


def note_unused(code, number, reason, settings=None):
    """Record that a reserved number never made it onto a receipt.

    The number is *not* returned to the pool -- see the module docstring -- so
    this leaves a gap. Logging it is what makes the gap explainable later.
    """
    logger.warning("Invoice number %s%s%s was reserved but not used: %s",
                   invoice_prefix(settings), code, number, reason)


def claim_at_least(code, number, settings=None):
    """Ensure the sequence resumes above ``number``.

    For a hand-typed invoice number: the app must not later hand the same one
    out again. Raises nothing and does nothing if the counter is already past it.
    """
    settings = settings if settings is not None else config.load_app_settings()
    path = counter_path(settings)
    with _FileLock(path):
        state = _read(path) or _blank()
        current, _ = _reconcile(state, code, settings)
        if number >= current:
            state["series"][code]["next"] = number + 1
            _write(path, state)
            logger.info("Invoice counter for series %r advanced past a manually "
                        "entered number (%d).", code, number)
