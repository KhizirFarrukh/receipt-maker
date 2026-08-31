"""Unfinished receipts, saved to come back to.

TODO.md §4 (H2). A sale gets interrupted -- the customer goes to fetch a card,
the phone rings, the shop closes. Until now the only way to keep the work was to
issue the receipt, which consumes an invoice number and produces a PDF for a
sale that has not happened.

**A draft consumes no invoice number.** That is the whole point and the one rule
that matters here. Numbers are reserved when a receipt is *generated*, because a
duplicate is unrecoverable; a draft is not a receipt and must not touch the
counter. Any invoice number showing in the form when a draft is saved is kept as
a *suggestion* only, and the counter is untouched on both save and restore.

Drafts are stored together in one JSON file rather than one file each: there are
few of them, they are small, and a single file is one atomic write with one
`.bak` -- the same machinery every other config file here uses.
"""
import datetime
import json
import logging
import os

import config

logger = logging.getLogger("receipt_maker")

DRAFTS_FILENAME = "drafts.json"
DRAFTS_VERSION = 1

#: Beyond this the list stops being something you can pick from. The oldest go
#: first, since a draft nobody has come back to in fifty saves is not coming
#: back.
MAX_DRAFTS = 50


def drafts_path():
    return os.path.join(config.APP_DIR, DRAFTS_FILENAME)


def default_drafts():
    return {config.SCHEMA_VERSION_KEY: DRAFTS_VERSION, "drafts": []}


def load():
    """Every saved draft, newest first. A damaged file reads as empty."""
    path = drafts_path()
    if not os.path.exists(path):
        return default_drafts()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read %s (%s); treating it as empty.", path, exc)
        return default_drafts()
    if not isinstance(data, dict):
        return default_drafts()
    data.setdefault(config.SCHEMA_VERSION_KEY, DRAFTS_VERSION)
    drafts = data.get("drafts")
    data["drafts"] = [d for d in drafts if isinstance(d, dict)] if isinstance(drafts, list) else []
    return data


def save(data):
    """Write the draft file atomically, keeping a .bak like every other file."""
    config.atomic_write_json(drafts_path(), data)
    return data


def describe(draft):
    """A line for the picker: who it is for and what is on it."""
    customer = str(draft.get("cust", "") or "").strip() or "(no customer)"
    items = draft.get("items") or []
    count = len(items)
    return f"{customer} — {count} item{'' if count == 1 else 's'}"


def add(form_data, name=""):
    """Save the form as a draft. Returns the stored record.

    The invoice number rides along as `suggested_inv_no` rather than `inv_no`,
    so nothing downstream can mistake a draft for a receipt that has been
    numbered. Restoring puts it back in the box as a suggestion, exactly as if
    it had been typed.
    """
    data = load()
    stamp = datetime.datetime.now().isoformat(timespec="seconds")

    record = {key: value for key, value in (form_data or {}).items()
              if key != "inv_no"}
    record["suggested_inv_no"] = str((form_data or {}).get("inv_no", "") or "")
    record["saved_at"] = stamp
    record["draft_id"] = f"{stamp}-{len(data['drafts'])}"
    record["name"] = str(name or "").strip() or describe(record)

    data["drafts"].insert(0, record)
    # Oldest out first. A draft nobody has come back to in fifty saves is not
    # one anybody is waiting on.
    del data["drafts"][MAX_DRAFTS:]
    save(data)
    logger.info("Saved draft %r (no invoice number consumed).", record["name"])
    return record


def remove(draft_id):
    """Delete one draft. Returns True if it was there."""
    data = load()
    before = len(data["drafts"])
    data["drafts"] = [d for d in data["drafts"]
                      if d.get("draft_id") != draft_id]
    if len(data["drafts"]) == before:
        return False
    save(data)
    return True


def to_form_data(draft):
    """A draft in the shape the form fills itself from.

    `inv_no` comes back from `suggested_inv_no`, so restoring offers the number
    that was showing rather than claiming one. Whether that number is still
    free is decided at generation time by the counter, as it always is.
    """
    form = {key: value for key, value in (draft or {}).items()
            if key not in ("draft_id", "saved_at", "name", "suggested_inv_no")}
    form["inv_no"] = str((draft or {}).get("suggested_inv_no", "") or "")
    return form
