"""TODO.md sections 6.1 and 6.2 -- one serial number for each thing sold.

A line of quantity 3 is three physical units, each with its own identifiers.
Before this, `serial` was one text box for the whole line, so selling three of
the same product meant three separate lines.

The design point these tests protect is that units are **records, not parallel
lists**. A list of serials beside a list of IDs looks equivalent right up until
someone clears the middle serial; with parallel lists every ID below it then
belongs to the wrong unit, and nothing detects it because both lists are still
valid. `UnitsAreRecordsNotParallelLists` is the test that would fail if anyone
"simplified" this back.

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

import config              # noqa: E402
import line_units          # noqa: E402
import receipt_history     # noqa: E402
import receipt_render      # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


class QuantityReading(unittest.TestCase):
    def test_a_normal_quantity(self):
        self.assertEqual(line_units.quantity_of({"qty": 3}), 3)

    def test_a_quantity_typed_as_text(self):
        self.assertEqual(line_units.quantity_of({"qty": " 4 "}), 4)

    def test_junk_reads_as_one_not_zero(self):
        """A broken quantity should still show a box, so the gap is visible."""
        self.assertEqual(line_units.quantity_of({"qty": "lots"}), 1)

    def test_a_missing_quantity_reads_as_one(self):
        self.assertEqual(line_units.quantity_of({}), 1)

    def test_a_negative_quantity_cannot_ask_for_negative_rows(self):
        self.assertEqual(line_units.quantity_of({"qty": -5}), 0)

    def test_an_absurd_quantity_is_capped(self):
        """A typo'd 99999 must not try to build 99999 entry boxes."""
        self.assertEqual(line_units.quantity_of({"qty": 99999}), line_units.MAX_UNITS)


class Normalising(unittest.TestCase):
    KEYS = ["serial", "unit_id"]

    def test_it_produces_exactly_one_record_per_unit(self):
        units = line_units.normalise({"qty": 3}, self.KEYS)
        self.assertEqual(len(units), 3)

    def test_every_record_carries_every_key(self):
        units = line_units.normalise({"qty": 2}, self.KEYS)
        self.assertEqual(sorted(units[0]), ["serial", "unit_id"])

    def test_raising_the_quantity_pads_without_losing_what_was_typed(self):
        item = {"qty": 3, "units": [{"serial": "A"}, {"serial": "B"}]}
        units = line_units.normalise(item, self.KEYS)
        self.assertEqual([u["serial"] for u in units], ["A", "B", ""])

    def test_lowering_the_quantity_trims_from_the_end(self):
        item = {"qty": 1, "units": [{"serial": "A"}, {"serial": "B"}]}
        self.assertEqual([u["serial"] for u in line_units.normalise(item, self.KEYS)],
                         ["A"])

    def test_a_key_added_later_is_filled_in(self):
        """Turning on the shop's own unit ID must not break existing lines."""
        item = {"qty": 1, "units": [{"serial": "A"}]}
        self.assertEqual(line_units.normalise(item, self.KEYS)[0]["unit_id"], "")

    def test_it_does_not_mutate_the_item(self):
        """Rendering must never rewrite the data it was handed."""
        item = {"qty": 3, "units": [{"serial": "A"}]}
        line_units.normalise(item, self.KEYS)
        self.assertEqual(len(item["units"]), 1)

    def test_junk_in_the_units_slot_is_survived(self):
        for junk in ("not a list", 42, {"serial": "A"}, None):
            units = line_units.normalise({"qty": 2, "units": junk}, self.KEYS)
            self.assertEqual(len(units), 2)

    def test_a_non_dict_entry_is_replaced_rather_than_crashing(self):
        item = {"qty": 2, "units": ["SN1", {"serial": "B"}]}
        units = line_units.normalise(item, self.KEYS)
        self.assertEqual([u["serial"] for u in units], ["", "B"])


class UnitsAreRecordsNotParallelLists(unittest.TestCase):
    """The reason this module exists rather than two newline-joined strings."""

    KEYS = ["serial", "unit_id"]

    def test_clearing_one_serial_does_not_shift_the_ids(self):
        item = {"qty": 3, "units": [
            {"serial": "SN1", "unit_id": "A1"},
            {"serial": "SN2", "unit_id": "A2"},
            {"serial": "SN3", "unit_id": "A3"},
        ]}
        item["units"][1]["serial"] = ""          # the middle one goes

        units = line_units.normalise(item, self.KEYS)
        self.assertEqual([u["unit_id"] for u in units], ["A1", "A2", "A3"],
                         "an ID must stay with its own unit")
        self.assertEqual(units[2]["serial"], "SN3",
                         "the last serial must not have moved up")

    def test_a_unit_keeps_its_pairing_through_a_quantity_change(self):
        item = {"qty": 2, "units": [
            {"serial": "SN1", "unit_id": "A1"},
            {"serial": "SN2", "unit_id": "A2"},
            {"serial": "SN3", "unit_id": "A3"},
        ]}
        units = line_units.normalise(item, self.KEYS)
        self.assertEqual(units, [{"serial": "SN1", "unit_id": "A1"},
                                 {"serial": "SN2", "unit_id": "A2"}])


class Gaps(unittest.TestCase):
    FIELDS = {"line_item_fields": [
        {"key": "serial", "label": "Serial Number", "type": "text",
         "enabled": True, "per_unit": True},
        {"key": "unit_id", "label": "Unit ID", "type": "text",
         "enabled": True, "per_unit": True},
    ]}

    def test_nothing_missing_says_nothing(self):
        units = [{"serial": "A", "unit_id": "1"}]
        self.assertEqual(line_units.describe_gaps(units, self.FIELDS), "")

    def test_it_counts_what_is_blank(self):
        units = [{"serial": "A", "unit_id": ""}, {"serial": "", "unit_id": ""}]
        message = line_units.describe_gaps(units, self.FIELDS)
        self.assertIn("1 of 2 unit has no Serial Number", message)
        self.assertIn("2 of 2 units have no Unit ID", message)

    def test_a_disabled_field_is_not_nagged_about(self):
        fields = {"line_item_fields": [
            dict(self.FIELDS["line_item_fields"][0]),
            dict(self.FIELDS["line_item_fields"][1], enabled=False),
        ]}
        message = line_units.describe_gaps([{"serial": "A", "unit_id": ""}], fields)
        self.assertEqual(message, "")

    def test_whitespace_does_not_count_as_filled_in(self):
        self.assertEqual(line_units.missing_count([{"serial": "   "}], "serial"), 1)


class Storing(unittest.TestCase):
    def test_units_with_values_are_kept(self):
        item = line_units.set_units({}, [{"serial": "A"}])
        self.assertEqual(item["units"], [{"serial": "A"}])

    def test_all_blank_units_are_dropped_entirely(self):
        """Otherwise every line of every receipt carries empty noise."""
        item = line_units.set_units({}, [{"serial": ""}, {"serial": "  "}])
        self.assertNotIn("units", item)

    def test_setting_empty_units_removes_an_earlier_value(self):
        item = {"units": [{"serial": "A"}]}
        line_units.set_units(item, [])
        self.assertNotIn("units", item)


class PerUnitKeys(unittest.TestCase):
    def test_the_shipped_defaults_offer_serial_and_unit_id(self):
        fields = config.default_fields()
        keys = line_units.per_unit_keys(fields, enabled_only=False)
        self.assertIn("unit_id", keys)

    def test_serial_is_not_per_unit_until_asked_for(self):
        """Existing installs must keep the single box they have today."""
        fields = config.default_fields()
        self.assertNotIn("serial", line_units.per_unit_keys(fields))

    def test_a_disabled_field_is_excluded(self):
        fields = config.default_fields()
        self.assertNotIn("unit_id", line_units.per_unit_keys(fields))

    def test_order_follows_the_configuration(self):
        fields = {"line_item_fields": [
            {"key": "b", "label": "B", "enabled": True, "per_unit": True},
            {"key": "a", "label": "A", "enabled": True, "per_unit": True},
        ]}
        self.assertEqual(line_units.per_unit_keys(fields), ["b", "a"])


class Validation(unittest.TestCase):
    def field(self, **kw):
        base = {"key": "serial", "label": "Serial", "type": "text", "per_unit": True}
        base.update(kw)
        fields = config.default_fields()
        fields["line_item_fields"] = [
            f for f in fields["line_item_fields"] if f["key"] != "serial"]
        fields["line_item_fields"].insert(0, base)
        return fields

    def test_a_text_field_may_be_per_unit(self):
        config.validate_fields(self.field(), "fields.json")

    def test_an_amount_may_not_be(self):
        """A price describes the line, not one unit of it."""
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate_fields(self.field(type="amount"), "fields.json")
        self.assertEqual(ctx.exception.key, "line_item_fields[0].per_unit")

    def test_a_computed_field_may_not_be(self):
        with self.assertRaises(config.ConfigError):
            config.validate_fields(self.field(type="computed"), "fields.json")

    def test_quantity_itself_may_not_be(self):
        """It is what says how many units there are.

        Caught by the type guard first -- qty is an integer -- which is the
        right refusal for the wrong-looking reason, so the next test covers the
        built-in guard on its own.
        """
        fields = config.default_fields()
        for f in fields["line_item_fields"]:
            if f["key"] == "qty":
                f["per_unit"] = True
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate_fields(fields, "fields.json")
        self.assertEqual(ctx.exception.key, "line_item_fields[4].per_unit")

    def test_a_totals_field_may_not_be_even_with_an_allowed_type(self):
        """Retyping qty as text must not open a way round the built-in guard."""
        fields = config.default_fields()
        for f in fields["line_item_fields"]:
            if f["key"] == "qty":
                f.update(type="text", per_unit=True)
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate_fields(fields, "fields.json")
        self.assertIn("cannot be per-unit", str(ctx.exception))

    def test_the_flag_must_be_a_boolean(self):
        with self.assertRaises(config.ConfigError):
            config.validate_fields(self.field(per_unit="yes"), "fields.json")

    def test_units_is_a_reserved_key(self):
        """A custom field taking it would overwrite every serial on the receipt."""
        fields = config.default_fields()
        fields["line_item_fields"].append(
            {"key": "units", "label": "Units", "type": "text", "enabled": True})
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate_fields(fields, "fields.json")
        self.assertIn("reserved", str(ctx.exception))


class Migration(unittest.TestCase):
    def v3_fields(self):
        fields = config.default_fields()
        fields["line_item_fields"] = [
            f for f in fields["line_item_fields"] if f["key"] != "unit_id"]
        for f in fields["line_item_fields"]:
            f.pop("per_unit", None)
        fields[config.SCHEMA_VERSION_KEY] = 3
        return fields

    def test_unit_id_is_added(self):
        fields, changed = config.migrate_fields(self.v3_fields(), 3)
        self.assertTrue(changed)
        self.assertIn("unit_id", [f["key"] for f in fields["line_item_fields"]])

    def test_it_arrives_disabled(self):
        fields, _ = config.migrate_fields(self.v3_fields(), 3)
        added = next(f for f in fields["line_item_fields"] if f["key"] == "unit_id")
        self.assertFalse(added["enabled"])

    def test_serial_gains_the_flag_switched_off(self):
        """An existing install keeps its single serial box until it asks."""
        fields, _ = config.migrate_fields(self.v3_fields(), 3)
        serial = next(f for f in fields["line_item_fields"] if f["key"] == "serial")
        self.assertIs(serial["per_unit"], False)

    def test_a_shop_that_already_turned_it_on_is_left_alone(self):
        fields = self.v3_fields()
        for f in fields["line_item_fields"]:
            if f["key"] == "serial":
                f["per_unit"] = True
        migrated, _ = config.migrate_fields(fields, 3)
        serial = next(f for f in migrated["line_item_fields"] if f["key"] == "serial")
        self.assertIs(serial["per_unit"], True)

    def test_it_sits_next_to_serial(self):
        fields, _ = config.migrate_fields(self.v3_fields(), 3)
        keys = [f["key"] for f in fields["line_item_fields"]]
        self.assertEqual(keys.index("unit_id"), keys.index("serial") + 1)

    def test_migrating_from_v1_collects_every_step(self):
        fields = self.v3_fields()
        fields["line_item_fields"] = [
            f for f in fields["line_item_fields"]
            if f["key"] not in ("barcode", "line_total")]
        migrated, _ = config.migrate_fields(fields, 1)
        keys = [f["key"] for f in migrated["line_item_fields"]]
        for expected in ("barcode", "line_total", "unit_id"):
            self.assertIn(expected, keys)

    def test_a_migrated_file_validates(self):
        fields, _ = config.migrate_fields(self.v3_fields(), 3)
        config.validate_fields(fields, "fields.json")


class OnTheReceipt(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-units-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def turn_on(self, key, per_unit=True):
        fields = config.load_fields()
        for field in fields["line_item_fields"]:
            if field["key"] == key:
                field["enabled"] = True
                field["per_unit"] = per_unit
        config.save_fields(fields)
        receipt_render.clear_template_cache()

    def render(self, item):
        return receipt_render.build_html(
            "INV-W1", "1 Jan 2026", "Ada", "", "", [item], "Online", 0)

    def line(self, **kw):
        base = {"sku": "A", "desc": "Thing", "serial": "", "qty": 3,
                "price": "10.00", "discount": "0", "tax": "0", "warranty": ""}
        base.update(kw)
        return base

    def test_every_serial_is_printed(self):
        self.turn_on("serial")
        html = self.render(self.line(units=[
            {"serial": "SN1"}, {"serial": "SN2"}, {"serial": "SN3"}]))
        for serial in ("SN1", "SN2", "SN3"):
            self.assertIn(serial, html)

    def test_they_are_kept_on_separate_lines(self):
        """Without the pre-line rule they run together into one string."""
        self.turn_on("serial")
        html = self.render(self.line(units=[{"serial": "SN1"}, {"serial": "SN2"}]))
        self.assertIn("SN1\nSN2", html)
        self.assertIn("item-units", html)

    def test_a_blank_unit_leaves_no_empty_row(self):
        self.turn_on("serial")
        html = self.render(self.line(units=[
            {"serial": "SN1"}, {"serial": ""}, {"serial": "SN3"}]))
        self.assertIn("SN1\nSN3", html)

    def test_serials_are_escaped_like_any_other_value(self):
        self.turn_on("serial")
        html = self.render(self.line(units=[{"serial": "<script>x</script>"}]))
        self.assertNotIn("<script>x", html)
        self.assertIn("&lt;script&gt;", html)

    def test_the_single_box_still_works_when_the_flag_is_off(self):
        """Nothing about an existing install changes until it opts in."""
        html = self.render(self.line(serial="ONE-FOR-THE-LINE"))
        self.assertIn("ONE-FOR-THE-LINE", html)

    def test_the_shops_own_unit_id_prints_too(self):
        self.turn_on("unit_id")
        html = self.render(self.line(units=[{"unit_id": "TAG-1"}, {"unit_id": "TAG-2"}]))
        self.assertIn("TAG-1\nTAG-2", html)

    def test_both_columns_at_once_stay_in_step(self):
        self.turn_on("serial")
        self.turn_on("unit_id")
        html = self.render(self.line(qty=2, units=[
            {"serial": "SN1", "unit_id": "TAG-1"},
            {"serial": "SN2", "unit_id": "TAG-2"}]))
        self.assertIn("SN1\nSN2", html)
        self.assertIn("TAG-1\nTAG-2", html)

    def test_more_units_than_the_quantity_are_not_printed(self):
        """The quantity is what is being sold; the rest is stale data."""
        self.turn_on("serial")
        html = self.render(self.line(qty=1, units=[
            {"serial": "SOLD"}, {"serial": "STALE"}]))
        self.assertIn("SOLD", html)
        self.assertNotIn("STALE", html)

    def test_an_optional_per_unit_column_appears_only_when_used(self):
        fields = config.load_fields()
        for field in fields["line_item_fields"]:
            if field["key"] == "unit_id":
                field.update(enabled=True, per_unit=True, optional_column=True)
        config.save_fields(fields)
        receipt_render.clear_template_cache()

        without = self.render(self.line())
        self.assertNotIn("Unit ID", without)
        with_values = self.render(self.line(units=[{"unit_id": "TAG-1"}]))
        self.assertIn("Unit ID", with_values)


class HistoryRoundTrip(unittest.TestCase):
    """A receipt reloaded to be corrected must still know its serials."""

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-units-hist-")
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    ENTRY = {
        "inv_no": "INV-W1001", "date_str": "1 Jan 2026", "cust": "Ada",
        "phone": "", "email": "", "receipt_type": "Online", "shipping": "0",
        "items": [{"sku": "A", "desc": "Thing", "qty": 2, "price": "10.00",
                   "discount": "0", "tax": "0",
                   "units": [{"serial": "SN1", "unit_id": "T1"},
                             {"serial": "SN2", "unit_id": "T2"}]}],
    }

    def test_units_survive_being_recorded(self):
        receipt_history.record(self.ENTRY, "", True)
        stored = receipt_history.entries()[0]["items"][0]["units"]
        self.assertEqual(stored, [{"serial": "SN1", "unit_id": "T1"},
                                  {"serial": "SN2", "unit_id": "T2"}])

    def test_they_are_stored_as_a_list_not_a_string(self):
        """Stringing them would lose every serial the moment it was reloaded."""
        receipt_history.record(self.ENTRY, "", True)
        raw = open(receipt_history.history_path(), encoding="utf-8").read()
        self.assertIn('"units"', raw)
        self.assertIsInstance(json.loads(raw.splitlines()[0])["items"][0]["units"], list)

    def test_they_come_back_through_to_form_data(self):
        receipt_history.record(self.ENTRY, "", True)
        item = receipt_history.to_form_data(receipt_history.entries()[0])["items"][0]
        self.assertEqual(item["units"][1]["serial"], "SN2")

    def test_a_receipt_without_units_gains_no_empty_key(self):
        plain = dict(self.ENTRY, items=[{"sku": "A", "desc": "T", "qty": 1,
                                         "price": "1", "discount": "0", "tax": "0"}])
        receipt_history.record(plain, "", True)
        self.assertNotIn("units", receipt_history.entries()[0]["items"][0])


if __name__ == "__main__":
    unittest.main()
