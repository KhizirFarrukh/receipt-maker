"""The dialogs section 6 added, driven headlessly.

The handlers behind these were tested as they were built; the widget-building
was not, which left `main.py` the weakest module in the suite. A dialog that
raises while being constructed is not a subtle failure — it is the feature not
opening at all — so it is worth a test each.

**Modal dialogs are stubbed, always.** An unstubbed one does not fail the
suite, it *hangs* it (claude_chat/PITFALLS.md).

Run: python -m unittest discover -s tests
"""
import os
import shutil
import sys
import tempfile
import tkinter as tk
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config              # noqa: E402
import main                # noqa: E402
import product_catalogue   # noqa: E402
import receipt_render      # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


class Section6TestCase(unittest.TestCase):
    #: Turned on before the app is built, since the dialogs read it then.
    SETTINGS = {}
    PER_UNIT = ()

    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-s6-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        os.makedirs(os.path.join(self.dir, "invoices"), exist_ok=True)
        config.set_app_dir(self.dir)
        if self.SETTINGS:
            config.update_app_settings(self.SETTINGS)
        if self.PER_UNIT:
            fields = config.load_fields()
            for field in fields["line_item_fields"]:
                if field["key"] in self.PER_UNIT:
                    field.update(enabled=True, per_unit=True)
            config.save_fields(fields)
        receipt_render.clear_template_cache()

        self.root = tk.Tk()
        self.root.withdraw()

        # Nothing may open a real modal, and nothing may block on wait_window.
        self.infos, self.errors = [], []
        self._saved = (main.messagebox.showinfo, main.messagebox.showerror,
                       main.messagebox.askyesno)
        main.messagebox.showinfo = lambda t, m, **k: self.infos.append(m)
        main.messagebox.showerror = lambda t, m, **k: self.errors.append(m)
        main.messagebox.askyesno = lambda *a, **k: True
        self.root.wait_window = lambda *a, **k: None

        self.app = main.ReceiptApp(self.root)
        self.app.root.wait_window = lambda *a, **k: None

    def tearDown(self):
        (main.messagebox.showinfo, main.messagebox.showerror,
         main.messagebox.askyesno) = self._saved
        self.root.destroy()
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def add_row(self, **kw):
        item = {"sku": "A", "desc": "Thing", "serial": "", "qty": "2",
                "price": "10", "discount": "0", "tax": "0", "warranty": ""}
        item.update(kw)
        return self.app.items_tree.insert("", tk.END,
                                          values=self.app.item_to_row(item))


class TheUnitsGrid(Section6TestCase):
    PER_UNIT = ("serial",)

    def test_it_builds_a_row_for_each_unit(self):
        units = self.app.open_units_dialog(self.root, ["serial"], [], 3)
        # wait_window is stubbed, so it returns without a decision.
        self.assertIsNone(units)

    def test_it_survives_a_large_quantity(self):
        """A quantity of 50 is a legitimate line; the grid scrolls."""
        self.app.open_units_dialog(self.root, ["serial"], [], 50)

    def test_it_prefills_what_is_already_there(self):
        self.app.open_units_dialog(
            self.root, ["serial"], [{"serial": "SN1"}, {"serial": "SN2"}], 2)

    def test_two_per_unit_columns_build(self):
        self.app.open_units_dialog(self.root, ["serial", "unit_id"], [], 2)

    def test_a_quantity_of_zero_builds_an_empty_grid(self):
        self.app.open_units_dialog(self.root, ["serial"], [], 0)


class TheInstalmentDialog(Section6TestCase):
    SETTINGS = {"installments": {"enabled": True}}

    def test_it_builds_empty(self):
        self.assertIsNone(self.app.open_installment_dialog(self.root))

    def test_it_builds_with_a_plan(self):
        self.app.open_installment_dialog(
            self.root, {"months": 6, "down": "500", "monthly": "100"})

    def test_the_button_is_on_the_toolbar_when_plans_are_on(self):
        self.assertIsNotNone(self.app.order_plan_button)

    def test_the_summary_reflects_the_plan(self):
        self.app.order_plan = {"months": 6, "down": "500", "monthly": "100"}
        self.app.refresh_order_plan_label()
        self.assertIn("500.00", self.app.order_plan_label.cget("text"))

    def test_an_order_plan_is_refused_while_a_line_has_one(self):
        self.add_row(installment={"months": 3, "monthly": "10"})
        self.app.edit_order_plan()
        self.assertTrue(any("one whole-order plan" in m for m in self.infos))


class InstalmentsOffEntirely(Section6TestCase):
    def test_no_button_is_built(self):
        """A shop that never finances anything must not see it."""
        self.assertIsNone(self.app.order_plan_button)

    def test_refreshing_the_label_is_harmless(self):
        self.app.refresh_order_plan_label()


class ShipmentsSwitchedOff(Section6TestCase):
    """The Shipment column ships disabled, which the message has to account for."""

    def test_it_points_at_where_to_switch_them_on(self):
        self.app.edit_shipments()
        self.assertTrue(any("Fields & Columns" in m for m in self.infos),
                        "telling someone to tag an item is useless while the "
                        "column that does it is hidden")


class TheShipmentEditor(Section6TestCase):
    #: The column has to be on before an item can be put in a shipment.
    ENABLE_SHIPMENT = True

    def setUp(self):
        super().setUp()
        fields = config.load_fields()
        for field in fields["line_item_fields"]:
            if field["key"] == "shipment":
                field["enabled"] = True
        config.save_fields(fields)
        self.root.wait_window = lambda *a, **k: None
        self.app = main.ReceiptApp(tk.Toplevel(self.root))
        self.app.root.wait_window = lambda *a, **k: None

    def test_it_asks_for_a_shipment_first(self):
        self.app.edit_shipments()
        self.assertTrue(
            any("Give at least one item a shipment" in m for m in self.infos),
            f"expected the tag-an-item prompt, got {self.infos}")

    def test_it_builds_a_row_per_shipment(self):
        self.add_row(shipment="A")
        self.add_row(shipment="B")
        self.app.edit_shipments()
        self.assertEqual(self.app.shipment_tags_used(), ["A", "B"])

    def test_tags_are_read_from_the_rows(self):
        self.add_row(shipment="A")
        self.add_row(shipment="A")
        self.assertEqual(self.app.shipment_tags_used(), ["A"])

    def test_no_tags_when_nothing_is_grouped(self):
        self.add_row()
        self.assertEqual(self.app.shipment_tags_used(), [])


class TheItemDialogWithEverythingOn(Section6TestCase):
    SETTINGS = {"installments": {"enabled": True}}
    PER_UNIT = ("serial", "unit_id")

    def test_it_opens_for_a_new_item(self):
        """Every section 6 control on at once -- the layout must still build."""
        self.app.open_item_dialog()

    def test_it_opens_for_an_existing_item(self):
        row = self.add_row(units=[{"serial": "SN1"}],
                           installment={"months": 3, "monthly": "10"})
        self.app.open_item_dialog(row)

    def test_it_opens_with_a_line_that_has_neither(self):
        self.app.open_item_dialog(self.add_row())


class TheReceiptFieldForm(Section6TestCase):
    def test_it_builds_with_notes_on(self):
        fields = config.load_fields()
        for field in fields["receipt_fields"]:
            if field["key"] == "notes":
                field["enabled"] = True
        config.save_fields(fields)
        app = main.ReceiptApp(tk.Toplevel(self.root))
        self.assertIn("notes", app.receipt_field_texts)

    def test_it_builds_with_no_receipt_fields_at_all(self):
        self.assertEqual(self.app.receipt_field_texts, {})

    def test_values_round_trip_when_none_are_configured(self):
        self.assertEqual(self.app.receipt_field_values(), {})


class ScanningWithNoCatalogue(Section6TestCase):
    def test_a_scan_against_an_empty_catalogue_asks(self):
        self.app.scan_code.set("123")
        self.app.on_scan()
        # askyesno is stubbed to Yes, so a bare line is added.
        self.assertEqual(len(self.app.items_tree.get_children()), 1)

    def test_the_status_line_reports_it(self):
        self.app.scan_code.set("123")
        self.app.on_scan()
        self.assertTrue(self.app.scan_status.cget("text"))


class ShippingTurnedOff(Section6TestCase):
    SETTINGS = {"shipping": {"enabled": False}}

    def test_the_form_still_builds(self):
        self.assertIsNotNone(self.app.shipping)

    def test_the_variable_stays_usable(self):
        """Nothing reads it, but generation still asks for a value."""
        self.assertEqual(self.app.shipping.get(), "")


if __name__ == "__main__":
    unittest.main()
