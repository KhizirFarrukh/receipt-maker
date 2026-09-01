"""Receipt Generator — tkinter GUI.

Stage 1 refactor: this module is GUI-only. HTML rendering lives in
receipt_render, headless generation (numbering/PDF/signing) in receipt_service,
config in config. Generation runs on a worker thread behind a modal progress
dialog; failures surface through show_error.
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import calendar
from datetime import date, datetime
import json
import logging
import logging.handlers
import os
import queue
import re
import sys
import threading
import traceback

import config  # noqa: F401  (import sets frozen PLAYWRIGHT_BROWSERS_PATH)
import invoice_counter
import line_units
import receipt_render
import receipt_service
import receipt_signing
from config import (
    APP_DIR,
    OUTPUT_DIR,
    load_app_settings,
)

#: Character width shared by every input in the item dialog. Entries and
#: comboboxes measure it slightly differently, but combined with sticky=EW and a
#: weighted column they end up flush on both edges.
INPUT_WIDTH = 24

# ------------------- logging -------------------
LOG_DIR = os.path.join(APP_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "receipt-maker.log")


def _setup_logging():
    """Rotating log file so failures can be diagnosed after the fact."""
    log = logging.getLogger("receipt_maker")
    log.setLevel(logging.INFO)
    if log.handlers:
        return log
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=512 * 1024, backupCount=5, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
    except OSError:
        pass
    return log


logger = _setup_logging()


# ------------------- modal dialog helpers -------------------
def _center_over(win, parent):
    win.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - win.winfo_width()) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - win.winfo_height()) // 2)
    win.geometry(f"+{x}+{y}")


def _safe_grab(win):
    try:
        win.grab_set()
    except tk.TclError:
        pass


def _make_modal(win, parent):
    """Tie win to parent and grab input so the main window is locked while open."""
    win.transient(parent)
    win.resizable(False, False)
    _center_over(win, parent)
    try:
        win.grab_set()
    except tk.TclError:
        # Not viewable yet (or parent hidden) -- grab once it maps.
        win.after(50, lambda: _safe_grab(win))
    win.focus_set()


def show_error(parent, title, summary, detail=None):
    """Modal, diagnosable error dialog: plain summary + expandable Details.

    The main window stays locked until this is dismissed. Everything is logged.
    """
    logger.error("%s -- %s%s", title, summary, ("\n" + detail) if detail else "")
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    frame = ttk.Frame(dialog, padding=16)
    frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(frame, text=summary, wraplength=460, justify=tk.LEFT).pack(anchor=tk.W, fill=tk.X)

    if detail:
        state = {"open": False}

        controls = ttk.Frame(frame)
        controls.pack(anchor=tk.W, fill=tk.X, pady=(10, 0))
        detail_box = tk.Text(frame, height=10, width=72, wrap=tk.WORD)
        detail_box.insert("1.0", detail)
        detail_box.config(state=tk.DISABLED)

        def toggle():
            if state["open"]:
                detail_box.pack_forget()
                toggle_btn.config(text="Show details ▾")
            else:
                detail_box.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
                toggle_btn.config(text="Hide details ▴")
            state["open"] = not state["open"]
            dialog.update_idletasks()
            _center_over(dialog, parent)

        def copy_details():
            dialog.clipboard_clear()
            dialog.clipboard_append(detail)

        toggle_btn = ttk.Button(controls, text="Show details ▾", command=toggle)
        toggle_btn.pack(side=tk.LEFT)
        ttk.Button(controls, text="Copy details", command=copy_details).pack(side=tk.LEFT, padx=6)
        ttk.Label(frame, text=f"Log: {LOG_FILE}", foreground="#64748b").pack(anchor=tk.W, pady=(6, 0))

    ttk.Button(frame, text="OK", command=dialog.destroy).pack(pady=(14, 0))
    _make_modal(dialog, parent)
    parent.wait_window(dialog)


def ask_with_memory(parent, title, message, remember_label="Don't ask me again"):
    """Yes/No dialog with a "remember this" checkbox. Returns (answer, remember).

    tkinter's messagebox cannot carry a checkbox, so this is a small Toplevel.
    The checkbox is deliberately *below* the buttons' question and unchecked by
    default: silently training the app to stop asking should be a choice, not
    something a user does by reflex.
    """
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    frame = ttk.Frame(dialog, padding=16)
    frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(frame, text=message, wraplength=460, justify=tk.LEFT).pack(anchor=tk.W, fill=tk.X)

    remember = tk.BooleanVar(value=False)
    ttk.Checkbutton(frame, text=remember_label, variable=remember).pack(
        anchor=tk.W, pady=(12, 0))

    result = {"answer": False}

    def close(answer):
        result["answer"] = answer
        dialog.destroy()

    buttons = ttk.Frame(frame)
    buttons.pack(pady=(14, 0))
    ttk.Button(buttons, text="Yes", command=lambda: close(True)).pack(side=tk.LEFT, padx=5)
    ttk.Button(buttons, text="No", command=lambda: close(False)).pack(side=tk.LEFT, padx=5)
    dialog.protocol("WM_DELETE_WINDOW", lambda: close(False))

    _make_modal(dialog, parent)
    parent.wait_window(dialog)
    return result["answer"], bool(remember.get())


# ------------------- main application -------------------


class ReceiptApp:
    def __init__(self, root):
        self.root = root
        settings = load_app_settings()
        company = settings["company"]
        # Label the amount fields with the configured currency rather than a
        # hardcoded one, so the form matches what the receipt will print.
        currency = settings.get("currency", {})
        self.money_label = (str(currency.get("code", "")).strip()
                            or str(currency.get("symbol", "")).strip())
        self.type_labels = config.receipt_type_labels(settings)
        self.fields = config.load_fields()
        self.input_fields = self._entry_fields()
        self.warranty_enabled = bool(
            self.fields.get("warranty", {}).get("enabled", True)
            and self.fields.get("warranty", {}).get("options"))
        root.title(f"{company['name']} - Receipt Generator")
        root.resizable(True, True)
        self._apply_scaling(root)
        self._build_menu(root)

        # --- form fields ---
        main_frame = ttk.Frame(root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # row 0: receipt type, invoice no, date
        ttk.Label(main_frame, text="Receipt Type").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.receipt_type = tk.StringVar(value=self.type_labels[0])
        type_combo = ttk.Combobox(
            main_frame,
            textvariable=self.receipt_type,
            values=self.type_labels,
            state="readonly",
            width=12,
        )
        type_combo.grid(row=0, column=1, padx=5, pady=2, sticky=tk.W)
        type_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_invoice_number())

        ttk.Label(main_frame, text="Invoice No.").grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)
        self.inv_no = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.inv_no, width=18).grid(row=0, column=3, padx=5, pady=2, sticky=tk.W)

        ttk.Label(main_frame, text="Date").grid(row=0, column=4, sticky=tk.W, padx=5, pady=2)
        self.date = tk.StringVar(value=date.today().strftime(config.date_display_format()))
        self.date_entry = ttk.Entry(main_frame, textvariable=self.date, width=15)
        self.date_entry.grid(row=0, column=5, padx=5, pady=2, sticky=tk.W)
        self.date_entry.bind("<Button-1>", self.show_date_picker)
        self.date_entry.bind("<Down>", self.show_date_picker)

        # row 1: customer
        ttk.Label(main_frame, text="Customer Name").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.cust_name = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.cust_name, width=30).grid(row=1, column=1, columnspan=2, padx=5, pady=2)

        ttk.Label(main_frame, text="Phone").grid(row=1, column=3, sticky=tk.W, padx=5, pady=2)
        self.cust_phone = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.cust_phone, width=15).grid(row=1, column=4, padx=5, pady=2)

        # row 2: email + shipping fees (global)
        ttk.Label(main_frame, text="Email").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        self.cust_email = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.cust_email, width=30).grid(row=2, column=1, columnspan=2, padx=5, pady=2)

        self.shipping = tk.StringVar()
        shipping_on = True
        try:
            shipping_on = bool(config.load_app_settings()
                               .get("shipping", {}).get("enabled", True))
        except Exception:                        # noqa: BLE001 - never block a sale
            shipping_on = True
        if shipping_on:
            ttk.Label(main_frame, text=self._money_field("Shipping")).grid(
                row=2, column=3, sticky=tk.W, padx=5, pady=2)
            ttk.Entry(main_frame, textvariable=self.shipping, width=15).grid(
                row=2, column=4, padx=5, pady=2, sticky=tk.W)
        # A plan covering the whole order. Kept as a plain dict rather than a Tk
        # variable because it is three numbers, not one, and nothing binds to it.
        self.order_plan = {}
        # Per-shipment shipping fees: [{"id": "1", "fee": "500"}]. Empty means
        # the single flat shipping fee beside it, which is the normal case.
        self.shipment_fees = []
        # How the customer is paying. Only shown when methods are configured,
        # so a shop that takes one kind of payment is never asked.
        self.payment_method = tk.StringVar()
        payment_options = []
        try:
            import payment_methods
            payment_options = payment_methods.labels(config.load_app_settings())
        except Exception:                        # noqa: BLE001 - never block a sale
            payment_options = []
        if payment_options:
            ttk.Label(main_frame, text="Paid by").grid(
                row=2, column=5, sticky=tk.W, padx=5, pady=2)
            ttk.Combobox(main_frame, textvariable=self.payment_method,
                         values=[""] + payment_options, state="readonly",
                         width=16).grid(row=2, column=6, padx=5, pady=2,
                                        sticky=tk.W)

        # Receipt-level custom fields, built from fields.json rather than
        # hardcoded -- until now the item dialog was configurable and the form
        # above it was not, so a receipt-level field could be printed but never
        # typed in. A `multiline` field (order notes) gets a real text box;
        # everything else follows its type like the item dialog does.
        self.receipt_field_vars = {}
        self.receipt_field_texts = {}
        next_form_row = 3
        for field in self.receipt_fields():
            label = field.get("label", field["key"])
            if field.get("required"):
                label += " *"
            ttk.Label(main_frame, text=label).grid(
                row=next_form_row, column=0, sticky=tk.NW, padx=5, pady=2)
            if field.get("type") == "multiline":
                box = tk.Text(main_frame, height=3, wrap=tk.WORD)
                box.grid(row=next_form_row, column=1, columnspan=5,
                         sticky=tk.EW, padx=5, pady=2)
                self.receipt_field_texts[field["key"]] = box
            else:
                var = tk.StringVar()
                widget = self._build_receipt_widget(main_frame, field, var)
                widget.grid(row=next_form_row, column=1, columnspan=2,
                            sticky=tk.W, padx=5, pady=2)
                self.receipt_field_vars[field["key"]] = var
            next_form_row += 1

        # --- items frame ---
        items_frame = ttk.LabelFrame(main_frame, text="Items", padding=5)
        items_frame.grid(row=next_form_row, column=0, columnspan=6,
                         sticky=tk.NSEW, padx=5, pady=10)
        main_frame.rowconfigure(next_form_row, weight=1)
        for col in range(6):
            main_frame.columnconfigure(col, weight=1)

        # toolbar
        toolbar = ttk.Frame(items_frame)
        toolbar.pack(fill=tk.X, pady=2)
        ttk.Button(toolbar, text="+ Add Item", command=self.add_item).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="Edit Selected", command=self.edit_item).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="Shipping per shipment…",
                   command=self.edit_shipments).pack(side=tk.LEFT, padx=5)

        # Scan straight into the list. A scanner types the code and presses
        # Enter, so the box needs no button -- and Enter must never reach the
        # form's default action, or the first scan would submit the receipt.
        ttk.Label(toolbar, text="Scan:").pack(side=tk.LEFT, padx=(16, 2))
        self.scan_code = tk.StringVar()
        scan_entry = ttk.Entry(toolbar, textvariable=self.scan_code, width=18)
        scan_entry.pack(side=tk.LEFT)
        scan_entry.bind("<Return>", self.on_scan)
        self.scan_status = ttk.Label(toolbar, text="", foreground="#64748b")
        self.scan_status.pack(side=tk.LEFT, padx=(6, 0))
        # A plan for the whole order. Only built when plans are switched on, so
        # a shop that never finances anything never sees the button.
        self.order_plan_button = None
        self.order_plan_label = None
        if self.installments_enabled():
            self.order_plan_button = ttk.Button(
                toolbar, text="Order instalment plan…",
                command=self.edit_order_plan)
            self.order_plan_button.pack(side=tk.LEFT, padx=5)
            self.order_plan_label = ttk.Label(toolbar, text="", foreground="#64748b")
            self.order_plan_label.pack(side=tk.LEFT, padx=(2, 0))
        ttk.Button(toolbar, text="- Remove Selected", command=self.remove_item).pack(side=tk.LEFT, padx=5)

        # Treeview for items (single selection: edit/remove act on one row).
        # Wrapped with scrollbars so it stays usable on small windows.
        tree_wrap = ttk.Frame(items_frame)
        tree_wrap.pack(fill=tk.BOTH, expand=True, pady=5)
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)

        # Columns follow fields.json, plus warranty, so a custom field can be
        # typed in as well as printed. self.input_fields is the single ordering
        # everything else keys off -- tree, dialog and item collection.
        columns = self.tree_keys()
        # The units column carries JSON, not something anyone should read: it is
        # in `columns` so the row tuple has a slot for it, and out of
        # `displaycolumns` so it never appears. The serials themselves are shown
        # in their own field's column, summarised.
        shown = [c for c in columns if c != line_units.UNITS_KEY]
        self.items_tree = ttk.Treeview(tree_wrap, columns=columns,
                                       displaycolumns=shown, show="headings",
                                       height=6, selectmode="browse")
        for field in self.input_fields:
            label = field.get("label", field["key"])
            if field.get("type") in ("amount", "number"):
                label = self._money_field(label) if field.get("type") == "amount" else label
            self.items_tree.heading(field["key"], text=label)
            self.items_tree.column(field["key"], **self._column_layout(field))
        if self.warranty_enabled:
            self.items_tree.heading(
                "warranty", text=self.fields.get("warranty", {}).get("label", "Warranty"))
            self.items_tree.column("warranty", width=150)

        vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.items_tree.yview)
        hsb = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self.items_tree.xview)
        self.items_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.items_tree.grid(row=0, column=0, sticky=tk.NSEW)
        vsb.grid(row=0, column=1, sticky=tk.NS)
        hsb.grid(row=1, column=0, sticky=tk.EW)
        self.items_tree.bind("<Double-1>", self.on_item_double_click)

        # --- action buttons ---
        actions_frame = ttk.Frame(main_frame)
        actions_frame.grid(row=4, column=0, columnspan=6, pady=15)
        self.generate_button = ttk.Button(actions_frame, text="Generate PDF Receipt", command=self.generate_pdf)
        self.generate_button.pack(side=tk.LEFT, padx=5)
        ttk.Button(actions_frame, text="Save Draft",
                   command=self.save_draft).pack(side=tk.LEFT, padx=5)
        ttk.Button(actions_frame, text="Drafts…",
                   command=self.open_drafts).pack(side=tk.LEFT, padx=5)
        ttk.Button(actions_frame, text="Clear Form", command=self.clear_form).pack(side=tk.LEFT, padx=5)

        # status label
        self.status_label = ttk.Label(main_frame, text="")
        self.status_label.grid(row=5, column=0, columnspan=6)

        # populate the initial invoice number once everything is wired
        self.refresh_invoice_number()

        # size the window to fit its contents, clamped to the screen so the
        # action buttons are always visible (even at 1024x768)
        self._size_window(root, main_frame)

    def _money_field(self, label):
        """Label an amount field with the configured currency, e.g. 'Shipping (USD)'."""
        return f"{label} ({self.money_label})" if self.money_label else label

    def _entry_fields(self):
        """Line-item fields the user types in, in configured order.

        Two rules that are easy to get backwards:

        * `computed` fields are never entered -- `amount` is qty x price, so
          offering it as an input would let the two disagree.
        * `enabled` controls whether a column is *printed*, not whether it is
          *entered*. Hiding the built-in Unit Price is a legitimate layout choice
          (show only line totals), but the price still has to be typed in or the
          totals have nothing to work from. So a hidden built-in stays on the
          form, while a hidden custom field disappears from it entirely.
        """
        entry = []
        for field in self.fields.get("line_item_fields", []):
            if field.get("type") == "computed":
                continue
            if not field.get("enabled", True) and field["key"] not in config.BUILTIN_LINE_ITEM_KEYS:
                continue
            entry.append(field)
        return entry

    @staticmethod
    def _build_field_widget(parent, field, row, initial=None):
        """Create the input widget for one field and return its variable.

        The widget follows the field's type, so a `select` is a dropdown that
        cannot hold an invalid value and a `boolean` is a checkbox -- validation
        the user cannot trip over is better than an error message.

        ``initial`` (a remembered sticky value) wins over the field's `default`,
        which is only a starting point for a field nothing is remembered for.
        """
        field_type = field.get("type", "text")
        if field_type == "boolean":
            var = tk.BooleanVar(value=bool(initial if initial is not None
                                           else field.get("default")))
            ttk.Checkbutton(parent, variable=var).grid(
                row=row, column=1, padx=10, pady=5, sticky=tk.W)
            return var

        var = tk.StringVar(value=str(initial if initial is not None
                                     else field.get("default", "") or ""))
        if field_type == "select":
            ttk.Combobox(parent, textvariable=var,
                         values=[str(o) for o in field.get("options", [])],
                         state="readonly", width=INPUT_WIDTH).grid(
                row=row, column=1, padx=10, pady=5, sticky=tk.EW)
        else:
            entry = ttk.Entry(parent, textvariable=var, width=INPUT_WIDTH)
            entry.grid(row=row, column=1, padx=10, pady=5, sticky=tk.EW)
            # A barcode scanner types the code and then sends Enter. Advancing to
            # the next field turns that into something useful; letting Enter
            # submit would save a line item containing nothing but a barcode.
            entry.bind("<Return>", lambda event: event.widget.tk_focusNext().focus_set()
                       or "break")
        return var

    @staticmethod
    def _column_layout(field):
        """Tree column width and alignment for a field type."""
        field_type = field.get("type", "text")
        if field_type in ("amount", "number"):
            return {"width": 90, "anchor": tk.E}
        if field_type == "integer":
            return {"width": 55, "anchor": tk.CENTER}
        if field_type == "boolean":
            return {"width": 60, "anchor": tk.CENTER}
        if field_type in ("multiline", "text") and field.get("key") == "desc":
            return {"width": 190}
        return {"width": 110}

    # ------------------- scaling / window sizing -------------------
    @staticmethod
    def enable_dpi_awareness():
        """Tell Windows we scale ourselves, so high-DPI displays stay crisp.
        Must run before the Tk root is created."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    def _apply_scaling(self, root):
        # Scale fonts/widgets to the monitor DPI (96 dpi = 1.0). On high-DPI
        # screens this makes text and controls the right physical size.
        try:
            dpi = root.winfo_fpixels("1i")
        except Exception:
            dpi = 96.0
        self.ui_scale = max(dpi / 96.0, 1.0)
        try:
            root.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass

    def _size_window(self, root, content):
        root.update_idletasks()
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        req_w = content.winfo_reqwidth()
        req_h = content.winfo_reqheight()
        # Never let the window be smaller than what shows every row (which
        # includes the action buttons), nor larger than the screen.
        min_w = min(req_w, screen_w - 20)
        min_h = min(req_h, screen_h - 80)  # leave room for the taskbar
        root.minsize(min_w, min_h)
        # Open a little roomier than the minimum, clamped to the screen.
        open_w = min(max(req_w, int(980 * self.ui_scale)), screen_w - 20)
        open_h = min(max(req_h, int(720 * self.ui_scale)), screen_h - 80)
        x = max(0, (screen_w - open_w) // 2)
        y = max(0, (screen_h - open_h) // 2 - 20)
        root.geometry(f"{open_w}x{open_h}+{x}+{y}")

    # ------------------- invoice numbering -------------------
    def refresh_invoice_number(self):
        """Show the number this series would issue next, without consuming it.

        Only generate_pdf reserves. Opening the app or flipping receipt type must
        not burn a number -- gaps in an invoice sequence have to be explainable.
        """
        prefix = receipt_service.get_invoice_prefix(self.receipt_type.get())
        self._suggested_inv_no = f"{prefix}{receipt_service.get_next_invoice_number(prefix)}"
        self.inv_no.set(self._suggested_inv_no)

    # ------------------- date picker -------------------
    def parse_selected_date(self):
        raw_date = self.date.get().strip()
        if not raw_date:
            return None

        for fmt in config.date_parse_formats():
            try:
                return datetime.strptime(raw_date, fmt).date()
            except ValueError:
                pass
        return None

    def show_date_picker(self, event=None):
        if getattr(self, "date_picker", None) is not None and self.date_picker.winfo_exists():
            self.date_picker.lift()
            self.date_picker.focus_force()
            return

        selected_date = self.parse_selected_date() or date.today()

        self.date_picker = tk.Toplevel(self.root)
        self.date_picker.title("Select Date")
        self.date_picker.resizable(False, False)
        self.date_picker.transient(self.root)
        self.date_picker.protocol("WM_DELETE_WINDOW", self.close_date_picker)

        picker_frame = ttk.Frame(self.date_picker, padding=8)
        picker_frame.pack(fill=tk.BOTH, expand=True)

        self.render_date_picker_month(
            picker_frame,
            selected_date.year,
            selected_date.month,
            selected_date,
        )

        self.date_picker.update_idletasks()
        x = self.date_entry.winfo_rootx()
        y = self.date_entry.winfo_rooty() + self.date_entry.winfo_height()
        self.date_picker.geometry(f"+{x}+{y}")
        self.date_picker.focus_force()

    def render_date_picker_month(self, frame, year, month, selected_date=None):
        for widget in frame.winfo_children():
            widget.destroy()

        header = ttk.Frame(frame)
        header.grid(row=0, column=0, columnspan=7, sticky=tk.EW, pady=(0, 6))
        header.columnconfigure(1, weight=1)

        ttk.Button(
            header,
            text="<",
            width=3,
            command=lambda: self.change_date_picker_month(frame, year, month, -1, selected_date),
        ).grid(row=0, column=0, sticky=tk.W)

        ttk.Label(
            header,
            text=f"{calendar.month_name[month]} {year}",
            anchor=tk.CENTER,
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=1, sticky=tk.EW, padx=8)

        ttk.Button(
            header,
            text=">",
            width=3,
            command=lambda: self.change_date_picker_month(frame, year, month, 1, selected_date),
        ).grid(row=0, column=2, sticky=tk.E)

        for col, weekday in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            ttk.Label(frame, text=weekday, anchor=tk.CENTER).grid(row=1, column=col, padx=2, pady=(0, 2))

        today = date.today()
        month_days = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
        for row, week in enumerate(month_days, start=2):
            for col, day in enumerate(week):
                if day == 0:
                    ttk.Label(frame, text="", width=4).grid(row=row, column=col, padx=2, pady=2)
                    continue

                current_date = date(year, month, day)
                day_text = str(day)
                if selected_date == current_date:
                    day_text = f"[{day}]"
                elif today == current_date:
                    day_text = f"*{day}*"

                ttk.Button(
                    frame,
                    text=day_text,
                    width=4,
                    command=lambda picked=current_date: self.select_date(picked),
                ).grid(row=row, column=col, padx=2, pady=2)

        footer_row = len(month_days) + 2
        ttk.Button(frame, text="Today", command=lambda: self.select_date(today)).grid(
            row=footer_row,
            column=0,
            columnspan=3,
            sticky=tk.EW,
            padx=2,
            pady=(6, 0),
        )
        ttk.Button(frame, text="Close", command=self.close_date_picker).grid(
            row=footer_row,
            column=4,
            columnspan=3,
            sticky=tk.EW,
            padx=2,
            pady=(6, 0),
        )

    def change_date_picker_month(self, frame, year, month, offset, selected_date):
        month += offset
        if month < 1:
            month = 12
            year -= 1
        elif month > 12:
            month = 1
            year += 1

        self.render_date_picker_month(frame, year, month, selected_date)

    def select_date(self, selected_date):
        self.date.set(selected_date.strftime(config.date_display_format()))
        self.close_date_picker()

    def close_date_picker(self):
        if getattr(self, "date_picker", None) is not None and self.date_picker.winfo_exists():
            self.date_picker.destroy()
        self.date_picker = None

    # ------------------- item management -------------------
    def add_item(self):
        self.open_item_dialog()

    def edit_item(self):
        selected = self.items_tree.selection()
        if not selected:
            messagebox.showinfo("Edit Item", "Select an item to edit first.")
            return
        self.open_item_dialog(selected[0])

    def on_item_double_click(self, event):
        if self.items_tree.identify_row(event.y):
            self.edit_item()

    def open_item_dialog(self, item_id=None):
        editing = item_id is not None
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Item" if editing else "New Item")
        # No fixed size: the rows come from fields.json, so a hardcoded height
        # clips as soon as anyone adds a column. Let it size to its contents.
        dialog.resizable(False, False)
        dialog.transient(self.root)  # stay tied to and above the main window
        dialog.columnconfigure(1, weight=1)

        # One row per configured field, with a widget chosen by its type. This
        # is what lets a custom column be typed in rather than only printed.
        labels = self.input_fields
        # Sticky values are only a starting point for a *new* item; when editing,
        # the row's own values are filled in below and must not be pre-empted.
        remembered = {} if editing else self.sticky_values()
        vars_ = {}
        for row, field in enumerate(labels):
            label = field.get("label", field["key"])
            if field.get("type") == "amount":
                label = self._money_field(label)
            if field.get("required"):
                label += " *"
            ttk.Label(dialog, text=label).grid(row=row, column=0, padx=10, pady=5, sticky=tk.W)
            vars_[field["key"]] = self._build_field_widget(
                dialog, field, row, remembered.get(field["key"]))

        # Per-unit fields (serial numbers, the shop's own unit IDs) are not a
        # single box: a line of quantity 3 needs three of each. They are edited
        # in a grid opened from here, so the main dialog keeps one row per field
        # however many units the line covers.
        unit_keys = line_units.per_unit_keys(self.fields)
        unit_state = {"units": []}
        units_button = None
        units_summary = None
        if unit_keys:
            units_row = len(labels)
            unit_labels = [f.get("label", f["key"]) for f in self.input_fields
                           if f["key"] in unit_keys]
            ttk.Label(dialog, text=" / ".join(unit_labels)).grid(
                row=units_row, column=0, padx=10, pady=5, sticky=tk.W)
            units_holder = ttk.Frame(dialog)
            units_holder.grid(row=units_row, column=1, padx=10, pady=5, sticky=tk.EW)
            units_button = ttk.Button(
                units_holder, text="Enter per item…",
                command=lambda: open_units())
            units_button.pack(side=tk.LEFT)
            units_summary = ttk.Label(units_holder, text="", foreground="#64748b")
            units_summary.pack(side=tk.LEFT, padx=(8, 0))

        def refresh_units_summary():
            if units_summary is None:
                return
            qty = line_units.quantity_of({"qty": vars_["qty"].get()}) if "qty" in vars_ else 0
            units = line_units.normalise({line_units.UNITS_KEY: unit_state["units"],
                                          "qty": qty}, unit_keys, qty)
            unit_state["units"] = units
            filled = sum(1 for u in units
                         if any(str(u.get(k, "")).strip() for k in unit_keys))
            if not qty:
                text = ""
            elif filled == qty:
                text = f"{qty} of {qty} entered"
            else:
                text = f"{filled} of {qty} entered"
            units_summary.config(
                text=text, foreground="#166534" if qty and filled == qty else "#b45309")

        def open_units():
            qty = line_units.quantity_of({"qty": vars_["qty"].get()}) if "qty" in vars_ else 0
            if not qty:
                messagebox.showinfo(
                    "Quantity first",
                    "Set the quantity before entering per-item details -- it is "
                    "what says how many are needed.", parent=dialog)
                return
            entered = self.open_units_dialog(
                dialog, unit_keys, unit_state["units"], qty,
                sku=str(vars_["sku"].get()).strip() if "sku" in vars_ else "")
            if entered is not None:
                unit_state["units"] = entered
                refresh_units_summary()

        # Pick from the catalogue instead of typing the same product again.
        # Sits above the fields it fills in, where it reads as a starting point
        # rather than an afterthought.
        # Rows are counted rather than derived from list lengths: the field list
        # is user-configurable and the warranty block is optional, so arithmetic
        # on len(labels) breaks the moment either changes.
        next_row = len(labels) + (1 if unit_keys else 0)

        picker_row = ttk.Frame(dialog)
        picker_row.grid(row=next_row, column=0, columnspan=2,
                        padx=10, pady=(4, 2), sticky=tk.W)
        ttk.Button(picker_row, text="Pick a product…",
                   command=lambda: self._fill_from_product(vars_)).pack(side=tk.LEFT)
        ttk.Label(picker_row, text="or scan a barcode into it",
                  foreground="#64748b").pack(side=tk.LEFT, padx=(8, 0))
        next_row += 1

        # A plan for this line alone. Only offered when plans are switched on,
        # so a shop that never finances anything sees nothing about it.
        plan_state = {"plan": {}}
        plan_summary = None
        if self.installments_enabled():
            import installments

            plan_row = ttk.Frame(dialog)
            plan_row.grid(row=next_row, column=0, columnspan=2,
                          padx=10, pady=(2, 2), sticky=tk.W)

            def edit_line_plan():
                if installments.plan_of({"installment": self.order_plan}):
                    messagebox.showinfo(
                        "One plan or the other",
                        "This receipt already has a whole-order instalment plan. "
                        "A receipt can carry one plan or one per line, not both -- "
                        "two sets of plans give a total nobody can reconstruct.\n\n"
                        "Clear the order plan first if you want per-line plans.",
                        parent=dialog)
                    return
                chosen = self.open_installment_dialog(
                    dialog, plan_state["plan"], "Instalment plan for this item")
                if chosen is not None:
                    plan_state["plan"] = chosen
                    refresh_plan_summary()

            ttk.Button(plan_row, text="Instalment plan…",
                       command=edit_line_plan).pack(side=tk.LEFT)
            plan_summary = ttk.Label(plan_row, text="", foreground="#64748b")
            plan_summary.pack(side=tk.LEFT, padx=(8, 0))
            next_row += 1

        def refresh_plan_summary():
            if plan_summary is None:
                return
            import installments
            text = installments.describe(plan_state["plan"], lambda v: f"{v:,.2f}")
            plan_summary.config(text=text or "none")

        # Warranty options come from fields.json. An option containing "#"
        # prompts for a whole number, so one entry covers 12 Months, 24 Months
        # and anything else the shop offers.
        warranty_cfg = self.fields.get("warranty", {})
        warranty_options = [str(o) for o in warranty_cfg.get("options", []) if str(o).strip()]
        warranty_type = tk.StringVar(value=warranty_options[0] if warranty_options else "")
        warranty_number = tk.StringVar(value="12")
        number_entry = None

        if warranty_cfg.get("enabled", True) and warranty_options:
            warranty_row = next_row
            ttk.Label(dialog, text=warranty_cfg.get("label", "Warranty")).grid(
                row=warranty_row, column=0, padx=10, pady=5, sticky=tk.W)
            # Same width and sticky as every other input, so the right-hand edge
            # lines up instead of the dropdown running into the window border.
            warranty_combo = ttk.Combobox(
                dialog,
                textvariable=warranty_type,
                values=warranty_options,
                state="readonly",
                width=INPUT_WIDTH,
            )
            warranty_combo.grid(row=warranty_row, column=1, padx=10, pady=5, sticky=tk.EW)

            number_row = warranty_row + 1
            ttk.Label(dialog, text="Warranty Period").grid(
                row=number_row, column=0, padx=10, pady=5, sticky=tk.W)
            number_entry = ttk.Entry(dialog, textvariable=warranty_number,
                                     width=INPUT_WIDTH)
            number_entry.grid(row=number_row, column=1, padx=10, pady=5, sticky=tk.EW)
            next_row = number_row + 1

        def on_warranty_type_change(event=None):
            if number_entry is None:
                return
            needed = config.warranty_option_needs_number(warranty_type.get())
            number_entry.configure(state="normal" if needed else "disabled")

        if warranty_cfg.get("enabled", True) and warranty_options:
            warranty_combo.bind("<<ComboboxSelected>>", on_warranty_type_change)

        # pre-fill the fields when editing an existing row
        if editing:
            existing = self.item_at(item_id)
            for field in labels:
                value = existing.get(field["key"], "")
                var = vars_[field["key"]]
                if isinstance(var, tk.BooleanVar):
                    var.set(str(value).strip().lower() in ("1", "true", "yes"))
                else:
                    var.set("" if value is None else str(value))
            unit_state["units"] = existing.get(line_units.UNITS_KEY) or []
            plan_state["plan"] = existing.get("installment") or {}
            option, number = self.match_warranty_option(
                str(existing.get("warranty", "")), warranty_options)
            if option:
                warranty_type.set(option)
            if number:
                warranty_number.set(number)
        on_warranty_type_change()
        refresh_units_summary()
        refresh_plan_summary()

        def save():
            values = {}
            for field in labels:
                raw = vars_[field["key"]].get()
                value, error = self.clean_field_value(field, raw)
                if error:
                    messagebox.showerror("Error", error, parent=dialog)
                    return
                values[field["key"]] = value

            warranty = self.resolve_warranty(
                warranty_type.get(), warranty_number.get().strip(), dialog)
            if warranty is None:
                return
            values["warranty"] = warranty

            if unit_keys:
                qty = line_units.quantity_of(values)
                units = line_units.normalise(
                    {line_units.UNITS_KEY: unit_state["units"]}, unit_keys, qty)
                gaps = line_units.describe_gaps(units, self.fields)
                # A warning, not a refusal. Insisting on every serial before the
                # line can be saved would be resented at a till, and the same
                # argument settled overselling the same way: record it and say
                # so, rather than blocking the sale.
                if gaps and not messagebox.askyesno(
                        "Some per-item details are blank",
                        f"{gaps}.\n\nSave the line anyway?", parent=dialog):
                    return
                line_units.set_units(values, units)

            if plan_state["plan"]:
                values["installment"] = plan_state["plan"]

            self.remember_sticky(values)
            row_values = self.item_to_row(values)
            if editing:
                self.items_tree.item(item_id, values=row_values)
            else:
                self.items_tree.insert("", tk.END, values=row_values)
            dialog.destroy()

        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=next_row, column=0, columnspan=2, pady=15)
        ttk.Button(button_frame, text="Save" if editing else "Add", command=save).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

        # make the dialog modal: centre it over the main window, take the input
        # grab so the main window can't be used until this dialog is closed.
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - dialog.winfo_width()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.grab_set()
        dialog.focus_set()
        self.root.wait_window(dialog)

    def held_serials_for(self, sku):
        """Serials the catalogue says are in stock for this SKU.

        Best-effort: a catalogue that will not load must not stop somebody
        entering serials by hand, which is what the box did before it offered
        anything.
        """
        try:
            import product_catalogue
            return product_catalogue.held_serials(product_catalogue.load(), sku)
        except Exception:                        # noqa: BLE001 - never block a sale
            return []

    def open_units_dialog(self, parent, keys, units, qty, sku=""):
        """Collect one row of values for each thing sold. Returns None if cancelled.

        A line of quantity 3 is three physical units, and each carries its own
        serial number -- and, where the shop labels its own stock, its own ID.
        They are edited as *rows* rather than as two separate lists, which is
        what stops the two drifting apart: clearing a serial clears that unit's
        serial, it does not shift every ID below it up by one.

        Scrolls rather than growing without limit, because a quantity of 50 is a
        legitimate line and a 50-row dialog would run off the screen.
        """
        labels = {f["key"]: f.get("label", f["key"])
                  for f in self.input_fields if f["key"] in keys}
        for key in keys:
            labels.setdefault(key, key)

        existing = line_units.normalise({line_units.UNITS_KEY: units}, keys, qty)
        held = self.held_serials_for(sku) if "serial" in keys else []

        win = tk.Toplevel(parent)
        win.title("Per-item details")
        win.transient(parent)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)

        ttk.Label(
            win, padding=(12, 10, 12, 4), wraplength=460, justify=tk.LEFT,
            text=(f"One row for each of the {qty} item(s) on this line. Leave a "
                  f"box blank if you do not have that detail yet -- the line "
                  f"still saves."
                  + (f"\n\n{len(held)} serial(s) in stock for {sku}; pick one "
                     f"or type another." if held else "")),
        ).grid(row=0, column=0, sticky=tk.W)

        # A canvas is the only way to scroll a grid of widgets in tkinter.
        canvas = tk.Canvas(win, highlightthickness=0, height=min(320, 40 + qty * 30))
        scroll = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        table = ttk.Frame(canvas, padding=(12, 4))
        table.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=table, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=1, column=0, sticky=tk.NSEW)
        scroll.grid(row=1, column=1, sticky=tk.NS)

        ttk.Label(table, text="#", width=4).grid(row=0, column=0, sticky=tk.W)
        for column, key in enumerate(keys, start=1):
            ttk.Label(table, text=labels[key], font=("TkDefaultFont", 9, "bold")).grid(
                row=0, column=column, padx=6, pady=(0, 4), sticky=tk.W)

        variables = []
        for index in range(qty):
            ttk.Label(table, text=str(index + 1), width=4).grid(
                row=index + 1, column=0, sticky=tk.W, pady=2)
            row_vars = {}
            for column, key in enumerate(keys, start=1):
                var = tk.StringVar(value=existing[index].get(key, ""))
                if key == "serial" and held:
                    # Offer what is actually on the shelf, but stay editable: a
                    # unit can predate the catalogue, and refusing a serial it
                    # has never heard of would block a legitimate sale.
                    entry = ttk.Combobox(table, textvariable=var, values=held,
                                         width=INPUT_WIDTH - 2)
                else:
                    entry = ttk.Entry(table, textvariable=var, width=INPUT_WIDTH)
                entry.grid(row=index + 1, column=column, padx=6, pady=2, sticky=tk.EW)
                # A scanner types the value then presses Enter. Enter must move
                # to the next box, not submit -- otherwise scanning the first
                # serial closes the dialog and the rest are never asked for.
                entry.bind("<Return>", lambda e: (e.widget.tk_focusNext().focus(), "break")[1])
                row_vars[key] = var
            variables.append(row_vars)

        result = {"units": None}

        def save():
            result["units"] = [
                {key: var.get().strip() for key, var in row.items()}
                for row in variables
            ]
            win.destroy()

        def clear_all():
            for row in variables:
                for var in row.values():
                    var.set("")

        buttons = ttk.Frame(win, padding=(12, 8))
        buttons.grid(row=2, column=0, columnspan=2, sticky=tk.E)
        ttk.Button(buttons, text="Save", command=save).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Clear all", command=clear_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side=tk.LEFT, padx=4)

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        _safe_grab(win)
        parent.wait_window(win)
        return result["units"]

    def codes_for(self, code):
        """Every code that identifies the same product as `code`.

        A scan has to find the line it already added, and the line may not have
        stored the code that was scanned: `barcode` ships as a disabled column,
        so a line added by scanning a barcode holds only the SKU. Resolving
        through the catalogue first means either code finds the same line.
        """
        codes = {str(code or "").strip().casefold()}
        try:
            import product_catalogue
            product = product_catalogue.find(product_catalogue.load(), code)
        except Exception:                        # noqa: BLE001 - never block a sale
            product = None
        if product:
            for key in ("sku", "barcode"):
                value = str(product.get(key, "") or "").strip().casefold()
                if value:
                    codes.add(value)
        return {c for c in codes if c}

    def find_row_by_code(self, code):
        """The first item row that is the same product as `code`, or None."""
        wanted = self.codes_for(code)
        if not wanted:
            return None
        for row in self.items_tree.get_children():
            item = self.item_at(row)
            for key in ("barcode", "sku"):
                value = str(item.get(key, "")).strip().casefold()
                if value and value in wanted:
                    return row
        return None

    def on_scan(self, event=None):
        """Add a line for a scanned code, or add one to the line already there.

        Returns "break" so Enter never propagates: a scanner ends every read
        with it, and letting it through would fire the window's default action
        on the first item scanned.
        """
        code = self.scan_code.get().strip()
        self.scan_code.set("")
        if not code:
            return "break"

        existing = self.find_row_by_code(code)
        if existing is not None:
            item = self.item_at(existing)
            try:
                quantity = int(str(item.get("qty", "1")).strip() or 1)
            except ValueError:
                quantity = 1
            item["qty"] = str(quantity + 1)
            # The unit list is sized by the quantity, so scanning a third one
            # means a third serial is now owed. normalise() pads it here rather
            # than leaving the line quietly short.
            unit_keys = line_units.per_unit_keys(self.fields)
            if unit_keys:
                line_units.set_units(
                    item, line_units.normalise(item, unit_keys))
            self.items_tree.item(existing, values=self.item_to_row(item))
            self.items_tree.selection_set(existing)
            self.items_tree.see(existing)
            self.set_scan_status(f"{item.get('desc') or code} × {item['qty']}")
            return "break"

        self.add_scanned_product(code)
        return "break"

    def add_scanned_product(self, code):
        """Insert a new line for `code`, asking what to do if it is unknown."""
        import product_catalogue

        try:
            product = product_catalogue.find(product_catalogue.load(), code)
        except Exception:                        # noqa: BLE001 - never block a sale
            product = None

        if product is None:
            # A scan that does nothing looks like a broken scanner, so say what
            # happened and offer the two useful answers.
            add_blank = messagebox.askyesno(
                "Not in the catalogue",
                f"Nothing in the product catalogue has the code {code!r}.\n\n"
                "Yes  -  add a line with this code, to fill in by hand\n"
                "No  -  do nothing (then add it under Tools → Products)",
                parent=self.root)
            if not add_blank:
                self.set_scan_status(f"{code}: not found", warn=True)
                return
            # Store the code where the form can actually keep it. `barcode`
            # is a disabled column by default, so a line written only to
            # `barcode` would lose the code and could never be rescanned.
            entry_keys = {field["key"] for field in self.input_fields}
            code_key = "barcode" if "barcode" in entry_keys else "sku"
            line = {code_key: code, "qty": "1"}
        else:
            line = product_catalogue.to_line_item(product)
            line["qty"] = "1"

        item = {field["key"]: line.get(field["key"], "")
                for field in self.input_fields}
        item.update({k: v for k, v in line.items() if k in item})
        item["qty"] = "1"
        if self.warranty_enabled:
            item.setdefault("warranty", "")

        self.items_tree.insert("", tk.END, values=self.item_to_row(item))
        rows = self.items_tree.get_children()
        self.items_tree.selection_set(rows[-1])
        self.items_tree.see(rows[-1])
        self.set_scan_status(f"{item.get('desc') or code} added")

    def set_scan_status(self, text, warn=False):
        if getattr(self, "scan_status", None) is None:
            return
        self.scan_status.config(text=text,
                                foreground="#b45309" if warn else "#166534")

    def shipment_tags_used(self):
        """Shipment tags present on the item rows, in first-mention order."""
        import shipments
        items = [self.item_at(row)
                 for row in self.items_tree.get_children()]
        return shipments.groups_used(items)

    def edit_shipments(self):
        """Set a shipping fee for each shipment the lines are grouped into.

        Only fees for shipments that actually have lines: a fee for a group
        nobody is in would be charged to nobody, and would make the shipping
        total disagree with the lines above it.
        """
        import shipments

        tags = self.shipment_tags_used()
        if not tags:
            # The Shipment column ships disabled, so "give an item a shipment"
            # is not actionable until it is switched on -- saying that without
            # saying where sends someone hunting for a control that is not
            # there yet.
            enabled = any(f["key"] == "shipment" for f in self.input_fields)
            if not enabled:
                messagebox.showinfo(
                    "Shipments are switched off",
                    "Turn the Shipment column on under Tools → Fields & "
                    "Columns first. Then give each item a shipment, and set a "
                    "fee for each one here.\n\nUntil then, the single "
                    "Shipping fee at the top covers the whole order.",
                    parent=self.root)
            else:
                messagebox.showinfo(
                    "No shipments yet",
                    "Give at least one item a shipment before setting "
                    "per-shipment shipping. Items left without one share the "
                    "single Shipping fee at the top.", parent=self.root)
            return

        existing = {str(e.get("id", "")): str(e.get("fee", ""))
                    for e in self.shipment_fees if isinstance(e, dict)}

        win = tk.Toplevel(self.root)
        win.title("Shipping per shipment")
        win.transient(self.root)
        win.resizable(False, False)
        win.columnconfigure(1, weight=1)

        ttk.Label(
            win, padding=(12, 10, 12, 6), wraplength=430, justify=tk.LEFT,
            text="One fee for each shipment. The receipt groups each "
                 "shipment's lines together and shows every fee and their "
                 "combined total, so a customer can see why the shipping came "
                 "to what it did.",
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W)

        variables = {}
        for index, tag in enumerate(tags, start=1):
            label = shipments.marker(index, len(tags)) or f"Shipment {tag}"
            ttk.Label(win, text=f"{label}  ({tag})").grid(
                row=index, column=0, padx=12, pady=5, sticky=tk.W)
            var = tk.StringVar(value=existing.get(tag, ""))
            ttk.Entry(win, textvariable=var, width=INPUT_WIDTH).grid(
                row=index, column=1, padx=12, pady=5, sticky=tk.EW)
            variables[tag] = var

        def save():
            fees = []
            for tag, var in variables.items():
                text = var.get().strip()
                if not text:
                    continue
                try:
                    value = float(text)
                except ValueError:
                    messagebox.showerror(
                        "Shipping", f"The fee for shipment {tag} must be a number.",
                        parent=win)
                    return
                if value < 0:
                    messagebox.showerror(
                        "Shipping",
                        f"The fee for shipment {tag} cannot be negative. A "
                        f"refund belongs on a line, not on the shipping.",
                        parent=win)
                    return
                fees.append({"id": tag, "fee": text})
            self.shipment_fees = fees
            win.destroy()

        buttons = ttk.Frame(win, padding=(12, 10))
        buttons.grid(row=len(tags) + 1, column=0, columnspan=2, sticky=tk.E)
        ttk.Button(buttons, text="Save", command=save).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side=tk.LEFT, padx=4)

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        _safe_grab(win)
        self.root.wait_window(win)

    def lines_with_plans(self):
        """How many item rows carry their own instalment plan."""
        count = 0
        for row in self.items_tree.get_children():
            item = self.item_at(row)
            if item.get("installment"):
                count += 1
        return count

    def edit_order_plan(self):
        """Set or clear the plan covering the whole receipt."""
        lines = self.lines_with_plans()
        if lines:
            messagebox.showinfo(
                "One plan or the other",
                f"{lines} item(s) already carry their own instalment plan. A "
                "receipt can have one whole-order plan or one per line, not "
                "both -- two sets of plans give a total nobody can "
                "reconstruct.\n\nClear the per-item plans first.",
                parent=self.root)
            return
        chosen = self.open_installment_dialog(
            self.root, self.order_plan, "Instalment plan for the whole order")
        if chosen is not None:
            self.order_plan = chosen
            self.refresh_order_plan_label()

    def refresh_order_plan_label(self):
        if self.order_plan_label is None:
            return
        import installments
        text = installments.describe(self.order_plan, lambda v: f"{v:,.2f}")
        self.order_plan_label.config(text=text)

    def open_installment_dialog(self, parent, plan=None, title="Instalment plan"):
        """Collect a period, a deposit and a monthly amount. Returns:

          * a plan dict   -- saved
          * {}            -- the user cleared the plan
          * None          -- cancelled, leave whatever was there alone

        The three outcomes are distinct because "no plan" and "did not decide"
        must not be the same answer: cancelling a dialog should never silently
        remove a plan that was already agreed.
        """
        import installments

        current = installments.normalise(plan) or {}
        win = tk.Toplevel(parent)
        win.title(title)
        win.transient(parent)
        win.resizable(False, False)
        win.columnconfigure(1, weight=1)

        ttk.Label(
            win, padding=(12, 10, 12, 6), wraplength=430, justify=tk.LEFT,
            text="The cash price stays the receipt total. The plan is shown "
                 "beside it, so the customer can see both what the goods cost "
                 "and what paying monthly comes to.",
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W)

        months = tk.StringVar(value=str(current.get("months", "") or ""))
        down = tk.StringVar(value=str(current.get("down", "") or ""))
        monthly = tk.StringVar(value=str(current.get("monthly", "") or ""))

        rows = (("Period (months)", months),
                (self._money_field("Down payment"), down),
                (self._money_field("Monthly payment"), monthly))
        for index, (label, var) in enumerate(rows, start=1):
            ttk.Label(win, text=label).grid(row=index, column=0, padx=12, pady=5,
                                            sticky=tk.W)
            ttk.Entry(win, textvariable=var, width=INPUT_WIDTH).grid(
                row=index, column=1, padx=12, pady=5, sticky=tk.EW)

        summary = ttk.Label(win, text="", foreground="#64748b", padding=(12, 0))
        summary.grid(row=4, column=0, columnspan=2, sticky=tk.W)

        def refresh(*_):
            candidate = {"months": months.get(), "down": down.get(),
                         "monthly": monthly.get()}
            try:
                installments.validate(candidate)
            except installments.InstallmentError as exc:
                summary.config(text=str(exc).split(": ", 1)[-1], foreground="#b45309")
                return
            total = installments.financed_total(candidate)
            if total:
                summary.config(text=f"Total under this plan: {total:,.2f}",
                               foreground="#166534")
            else:
                summary.config(text="")

        for var in (months, down, monthly):
            var.trace_add("write", refresh)
        refresh()

        result = {"plan": None}

        def save():
            candidate = {"months": months.get(), "down": down.get(),
                         "monthly": monthly.get()}
            try:
                validated = installments.validate(candidate)
            except installments.InstallmentError as exc:
                messagebox.showerror("Instalment plan", str(exc), parent=win)
                return
            result["plan"] = {} if validated is None else candidate
            win.destroy()

        def clear():
            result["plan"] = {}
            win.destroy()

        buttons = ttk.Frame(win, padding=(12, 10))
        buttons.grid(row=5, column=0, columnspan=2, sticky=tk.E)
        ttk.Button(buttons, text="Save", command=save).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="No plan", command=clear).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side=tk.LEFT, padx=4)

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        _safe_grab(win)
        parent.wait_window(win)
        return result["plan"]

    def installments_enabled(self):
        """Whether plans are offered at all -- section 6.5's global switch."""
        try:
            return bool(config.load_app_settings()
                        .get("installments", {}).get("enabled", False))
        except Exception:                        # noqa: BLE001 - never block a sale
            return False

    def sticky_values(self):
        """Values remembered from the last item, for fields marked `sticky`.

        Only fields still marked sticky are returned, so un-marking one stops it
        pre-filling immediately rather than the stale value lingering.
        """
        remembered = config.load_state().get("sticky_line_item", {})
        if not isinstance(remembered, dict):
            return {}
        return {f["key"]: remembered[f["key"]] for f in self.input_fields
                if f.get("sticky") and f["key"] in remembered}

    def remember_sticky(self, values):
        """Persist the sticky fields of the item just saved."""
        sticky = {f["key"]: values.get(f["key"], "") for f in self.input_fields
                  if f.get("sticky")}
        if not sticky:
            return
        state = config.load_state()
        state["sticky_line_item"] = sticky
        config.save_state(state)

    def _fill_from_product(self, vars_):
        """Fill the item dialog from a catalogue product.

        Only fills fields the product actually carries, and leaves quantity and
        anything already typed alone -- picking a product should be a shortcut,
        not a reset of work already done.
        """
        import product_catalogue
        import settings_ui

        chosen = settings_ui.pick_product(self.root)
        if not chosen:
            return

        line = product_catalogue.to_line_item(chosen)
        for key, value in line.items():
            if key == "qty" or key not in vars_ or value in ("", None):
                continue
            var = vars_[key]
            if isinstance(var, tk.BooleanVar):
                continue
            var.set(str(value))

    def tree_keys(self):
        """The tree's column order: the input fields, then the extras.

        `warranty` and `units` are stored but never typed into a column of their
        own -- warranty has its own control, and units are edited in a sub-grid.
        They ride along at the end so one ordering describes the whole row.
        """
        keys = [f["key"] for f in self.input_fields]
        if self.warranty_enabled:
            keys.append("warranty")
        keys.append(line_units.UNITS_KEY)
        keys.append("installment")
        return keys

    def receipt_fields(self):
        """The receipt-level fields to show on the form, in configured order.

        Only enabled ones: unlike a line-item field -- where `enabled` controls
        printing and a hidden built-in still has to be typed in for the totals
        to work -- nothing here feeds a calculation, so hiding one means the
        shop does not use it.
        """
        return [f for f in self.fields.get("receipt_fields", [])
                if isinstance(f, dict) and f.get("enabled", True)]

    def _build_receipt_widget(self, parent, field, var):
        """One input for a receipt-level field, following its declared type."""
        field_type = field.get("type", "text")
        if field_type == "select":
            options = [str(o) for o in field.get("options", [])]
            return ttk.Combobox(parent, textvariable=var, values=options,
                                state="readonly", width=28)
        if field_type == "boolean":
            return ttk.Checkbutton(parent, variable=var, onvalue="true",
                                   offvalue="")
        return ttk.Entry(parent, textvariable=var, width=30)

    def receipt_field_values(self):
        """What was typed into the receipt-level fields, keyed by field key."""
        values = {key: var.get() for key, var in self.receipt_field_vars.items()}
        for key, box in self.receipt_field_texts.items():
            # Tk appends a newline of its own to every Text widget; stripping it
            # stops an untouched box counting as a value and printing an empty
            # block on the receipt.
            values[key] = box.get("1.0", tk.END).strip()
        return values

    def set_receipt_field_values(self, source):
        """Fill the receipt-level fields from a stored receipt."""
        for key, var in self.receipt_field_vars.items():
            var.set(str(source.get(key, "") or ""))
        for key, box in self.receipt_field_texts.items():
            box.delete("1.0", tk.END)
            box.insert("1.0", str(source.get(key, "") or ""))

    def item_at(self, row_id):
        """Read one item row as a dict, without Tk mangling the values.

        `tree.item(row)["values"]` runs every cell through Tcl's type guessing,
        which turns a UPC of "0000000000000" into the integer 0 and a serial of
        "007" into 7. Leading zeros are common on barcodes, so that is silent
        data loss on a legal document. `tree.set(row)` returns the strings as
        stored, which is what this uses.
        """
        cells = self.items_tree.set(row_id)
        return self.row_to_item([cells.get(key, "") for key in self.tree_keys()])

    def row_to_item(self, values):
        """Tree row (a positional tuple) -> a dict keyed by field key.

        The tree stores values positionally, so every read of a row has to go
        through the same column ordering that wrote it. Doing this in one place
        is what stops a reordered fields.json from silently shifting data into
        the wrong column.
        """
        item = {}
        for key, value in zip(self.tree_keys(), list(values)):
            # Text, always: a value that arrived as a number (Tk's doing, or a
            # caller's) must not reach the renderer as one, or "007" prints as 7.
            item[key] = "" if value is None else str(value)

        # Units are the one non-string value on a row. The tree can only hold
        # text, so they travel as JSON and are parsed back here -- in the same
        # single place that owns the column ordering.
        raw_units = item.get(line_units.UNITS_KEY, "")
        item.pop(line_units.UNITS_KEY, None)
        if raw_units:
            try:
                parsed = json.loads(raw_units)
            except (ValueError, TypeError):
                parsed = []
            if isinstance(parsed, list):
                line_units.set_units(item, [u for u in parsed if isinstance(u, dict)])

        raw_plan = item.get("installment", "")
        item.pop("installment", None)
        if raw_plan:
            try:
                parsed = json.loads(raw_plan)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict) and parsed:
                item["installment"] = parsed
        return item

    def item_to_row(self, item):
        """The inverse of row_to_item: a dict -> the tree's positional tuple."""
        structured = {
            line_units.UNITS_KEY: item.get(line_units.UNITS_KEY) or [],
            "installment": item.get("installment") or {},
        }
        row = []
        for key in self.tree_keys():
            if key in structured:
                value = structured[key]
                row.append(json.dumps(value, ensure_ascii=False) if value else "")
            else:
                row.append(item.get(key, ""))
        return tuple(row)

    def clean_field_value(self, field, raw):
        """Validate and normalise one entered value. Returns (value, error).

        `error` is a message to show; when it is set the dialog stays open. The
        checks follow the field's declared type, so a custom `amount` column is
        validated exactly like the built-in ones rather than being trusted.
        """
        label = field.get("label", field["key"])
        field_type = field.get("type", "text")

        if isinstance(raw, bool):
            return ("true" if raw else ""), None

        text = str(raw).strip()
        if not text:
            if field.get("required"):
                return None, f"{label} is required."
            # An absent value stays absent -- the renderer decides how to show a
            # blank cell. Only the arithmetic triple needs a usable fallback.
            if field["key"] == "qty":
                return 1, None
            if field_type in ("amount", "number", "integer"):
                return 0, None
            return "", None

        if field_type == "integer":
            try:
                return int(text), None
            except ValueError:
                return None, f"{label} must be a whole number."

        if field_type in ("amount", "number"):
            try:
                number = float(text)
            except ValueError:
                return None, f"{label} must be a number."
            if field_type == "amount" and number < 0:
                return None, f"{label} cannot be negative."
            return f"{number:.2f}", None

        if field_type == "select":
            options = [str(o) for o in field.get("options", [])]
            if options and text not in options:
                return None, f"{label} must be one of: {', '.join(options)}."

        return text, None

    @staticmethod
    def resolve_warranty(option, number_raw, parent=None):
        """Turn the chosen option into the text printed on the receipt.

        Returns None when validation failed (the dialog stays open). An option
        containing '#' needs a positive whole number: blank, 0, negative and
        non-numeric are all rejected, because "0 Months Warranty" on a receipt is
        worse than no warranty line at all.
        """
        if not config.warranty_option_needs_number(option):
            return option

        try:
            number = int(str(number_raw).strip())
        except ValueError:
            number = 0
        if number <= 0:
            messagebox.showerror(
                "Error",
                f"“{option}” needs a positive whole number in place of the #.",
                parent=parent)
            return None
        return config.fill_warranty_number(option, number)

    @staticmethod
    def match_warranty_option(text, options):
        """Best-effort reverse of resolve_warranty, for re-opening a saved item.

        Returns (option, number). An exact match wins; otherwise a '#' option is
        matched by turning it into a pattern, so "12 Months Limited Warranty"
        re-selects "# Months Limited Warranty" with 12 in the number box.
        """
        text = (text or "").strip()
        for option in options:
            if option == text:
                return option, ""
        for option in options:
            if not config.warranty_option_needs_number(option):
                continue
            pattern = "^" + r"(\d+)".join(
                re.escape(part) for part in str(option).split("#", 1)) + "$"
            match = re.match(pattern, text)
            if match:
                return option, match.group(1)
        return "", ""

    def remove_item(self):
        selected = self.items_tree.selection()
        if selected:
            self.items_tree.delete(selected)

    def clear_form(self):
        self.close_date_picker()
        self.receipt_type.set(self.type_labels[0])
        self.refresh_invoice_number()
        self.cust_name.set("")
        self.cust_phone.set("")
        self.cust_email.set("")
        self.shipping.set("")

        for child in self.items_tree.get_children():
            self.items_tree.delete(child)

        self.status_label.config(text="Form cleared")

    # ------------------- PDF generation -------------------
    def _claim_invoice_number(self, typed):
        """Settle on the invoice number to issue. Returns (number, reserved_code).

        If the field still holds what the form suggested, the number is reserved
        atomically -- which may return a *different* number if another process
        took that one meanwhile, and that is the point.

        If the user typed their own, it is honoured verbatim, and the counter is
        pushed past it so the app never hands the same number out again.
        ``reserved_code`` is the series a number was consumed from, or None, so a
        failed generation can log the gap it leaves.
        """
        prefix = receipt_service.get_invoice_prefix(self.receipt_type.get())
        code = receipt_service.series_code(prefix)

        if typed == getattr(self, "_suggested_inv_no", None):
            return f"{prefix}{receipt_service.reserve_invoice_number(prefix)}", code

        match = re.match(rf"^{re.escape(prefix)}(\d+)$", typed, re.IGNORECASE)
        if match:
            invoice_counter.claim_at_least(code, int(match.group(1)))
        return typed, None

    def generate_pdf(self):
        inv_no = self.inv_no.get().strip()
        if not inv_no:
            messagebox.showerror("Error", "Invoice No. is required.")
            return
        date_str = self.date.get().strip() or " "
        cust = self.cust_name.get().strip() or "Walk-in Customer"
        phone = self.cust_phone.get().strip()
        email = self.cust_email.get().strip()
        receipt_type = self.receipt_type.get()

        shipping_raw = self.shipping.get().strip()
        try:
            shipping_float = float(shipping_raw) if shipping_raw else 0.0
        except ValueError:
            messagebox.showerror("Error", "Shipping must be a number.")
            return
        if shipping_float < 0:
            messagebox.showerror("Error", "Shipping cannot be negative.")
            return

        items = []
        for child in self.items_tree.get_children():
            item = self.item_at(child)
            # Numeric fields are stored as text in the tree; convert them back so
            # the renderer receives numbers, and say which item is wrong if one
            # cannot be read rather than failing anonymously.
            for field in self.input_fields:
                field_type = field.get("type", "text")
                if field_type not in ("integer", "amount", "number"):
                    continue
                raw = item.get(field["key"], "")
                try:
                    item[field["key"]] = (int(raw) if field_type == "integer"
                                          else float(raw or 0))
                except (ValueError, TypeError):
                    messagebox.showerror(
                        "Error",
                        f"{field.get('label', field['key'])} is not a number "
                        f"on item: {item.get('desc', '') or '(no description)'}")
                    return
            items.append(item)

        if not items:
            messagebox.showerror("Error", "At least one item is required.")
            return

        # Claim the number now, before anything can fail. Two app instances (or
        # the app and the CLI) must never be handed the same one, and the only
        # way to guarantee that is to consume it up front rather than on success.
        try:
            inv_no, reserved = self._claim_invoice_number(inv_no)
        except Exception as exc:
            show_error(self.root, "Could not reserve an invoice number", str(exc),
                       traceback.format_exc())
            return

        data = {
            "inv_no": inv_no,
            "date_str": date_str,
            "cust": cust,
            "phone": phone,
            "email": email,
            "items": items,
            "receipt_type": receipt_type,
            "shipping": shipping_float,
        }
        if self.order_plan:
            data["installment"] = self.order_plan
        if self.shipment_fees:
            data["shipments"] = self.shipment_fees
        if self.payment_method.get().strip():
            data["payment_method"] = self.payment_method.get().strip()

        for key, value in self.receipt_field_values().items():
            if str(value).strip():
                data[key] = value

        try:
            import shipments
            shipments.validate(data, items)
        except Exception as exc:                 # noqa: BLE001 - shown, not swallowed
            show_error(self.root, "Shipping does not add up", str(exc),
                       traceback.format_exc())
            if reserved:
                invoice_counter.note_unused(
                    reserved, inv_no, "shipment fees did not match the lines")
            return

        # One plan, or one per line, never both. Enforced here as well as in the
        # dialogs, because a receipt reloaded from history can carry a
        # combination no dialog would have allowed.
        try:
            import installments
            installments.scope_of(data, items)
        except Exception as exc:                 # noqa: BLE001 - shown, not swallowed
            show_error(self.root, "Instalment plans conflict", str(exc),
                       traceback.format_exc())
            # The number stays consumed -- handing it back would reopen the
            # race the reservation closes -- so record why the gap exists.
            if reserved:
                invoice_counter.note_unused(
                    reserved, inv_no, "instalment plans conflicted")
            return

        # Resolve the output path (and any collision) on the main thread, before
        # the worker starts, because the collision prompt is a UI decision.
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        base_filename = receipt_service.build_pdf_filename(
            inv_no, date_str, cust, email, phone, receipt_type)
        pdf_path = os.path.join(OUTPUT_DIR, base_filename)
        if os.path.exists(pdf_path):
            answer = messagebox.askyesnocancel(
                "File Already Exists",
                f"{base_filename} already exists.\n\n"
                "Yes  -  Replace the existing file\n"
                "No  -  Save as a new copy (-1, -2, ...)\n"
                "Cancel  -  Don't save",
            )
            if answer is None:
                # The number was already claimed, and it stays claimed -- but an
                # unexplained gap in an invoice sequence is exactly what this
                # logging exists to prevent, so record why this one is missing.
                if reserved:
                    invoice_counter.note_unused(reserved, inv_no, "save cancelled by the user")
                self.status_label.config(text="Save cancelled")
                return
            if not answer:  # No -> keep the existing file, save a numbered copy
                pdf_path = receipt_service.next_available_pdf_path(base_filename)

        self._run_generation(data, pdf_path, reserved)

    def _run_generation(self, data, out_path, reserved_code=None):
        """Generate on a worker thread behind a modal progress dialog.

        The main window is locked and the Generate button disabled for the
        duration, so a second job can't start against the same path/number.
        """
        if getattr(self, "_generating", False):
            return  # a job is already running; never spawn a second worker
        self._generating = True
        self.generate_button.config(state=tk.DISABLED)
        self.status_label.config(text="Generating...")

        dialog = tk.Toplevel(self.root)
        dialog.title("Generating Receipt")
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)  # can't close mid-job
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        status_var = tk.StringVar(value="Starting...")
        ttk.Label(frame, textvariable=status_var, width=42, anchor=tk.W).pack(anchor=tk.W, fill=tk.X)
        bar = ttk.Progressbar(frame, mode="determinate",
                              maximum=receipt_service.GENERATION_STEPS, length=320)
        bar.pack(fill=tk.X, pady=(10, 0))
        _make_modal(dialog, self.root)

        result_q = queue.Queue()

        def progress_cb(step, label):
            result_q.put(("progress", step, label))

        def worker():
            try:
                # Collected in the worker and reported on the main thread with
                # the result: a low-stock notice belongs with "receipt saved",
                # not in a second dialog a moment later.
                stock_warnings = []
                signed = receipt_service.generate(
                    data, out_path, progress_cb, warnings=stock_warnings)
                result_q.put(("done", signed, stock_warnings))
            except Exception as exc:  # noqa: BLE001 - reported via show_error
                result_q.put(("error", exc, traceback.format_exc()))

        threading.Thread(target=worker, daemon=True).start()

        def close_dialog():
            self._generating = False
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()
            self.generate_button.config(state=tk.NORMAL)

        def poll():
            try:
                while True:
                    msg = result_q.get_nowait()
                    if msg[0] == "progress":
                        _, step, label = msg
                        bar["value"] = step
                        status_var.set(label)
                    elif msg[0] == "done":
                        close_dialog()
                        self._on_generated(out_path, msg[1],
                                           msg[2] if len(msg) > 2 else None)
                        return
                    elif msg[0] == "error":
                        _, exc, tb = msg
                        close_dialog()
                        self.status_label.config(text="PDF generation failed")
                        logger.error("Generation failed for %s", out_path)
                        if reserved_code:
                            # The number stays consumed on purpose -- handing it
                            # back would reopen the race the reservation closes.
                            # Record the gap so it can be explained later.
                            invoice_counter.note_unused(
                                reserved_code, data.get("inv_no", ""), str(exc))
                        show_error(self.root, "Receipt generation failed", str(exc), tb)
                        return
            except queue.Empty:
                pass
            self.root.after(50, poll)

        self.root.after(50, poll)

    def _on_generated(self, out_path, signed, stock_warnings=None):
        state = "signed" if signed else "unsigned"
        self.status_label.config(text=f"Saved ({state}): {out_path}")
        self.refresh_invoice_number()
        logger.info("Generated %s (%s)", out_path, state)

        # Said after the sale, never before it: the receipt is already written
        # and a stale stock count must not stop a customer being served. It
        # rides along with the confirmation rather than arriving as a second
        # dialog, which would just be dismissed.
        notice = ""
        if stock_warnings:
            notice = "\n\nStock: " + "\n       ".join(stock_warnings)
            self.status_label.config(
                text=f"Saved ({state}): {out_path} — {stock_warnings[0]}")

        ui = load_app_settings().get("ui", {})
        if ui.get("ask_open_folder", True):
            answer, remember = ask_with_memory(
                self.root, "Receipt generated",
                f"✓ Receipt {state} and saved:\n{out_path}{notice}"
                f"\n\nOpen the containing folder?")
            if remember:
                self._remember_open_folder(answer)
        else:
            answer = ui.get("open_folder_after_generate", False)
            if stock_warnings:
                # Nothing else would show it: the confirmation dialog is the
                # only place these appear, and it was switched off.
                messagebox.showinfo("Stock", "\n".join(stock_warnings),
                                    parent=self.root)

        if answer:
            self._open_folder(os.path.dirname(out_path))

    def _remember_open_folder(self, answer):
        """Persist the answer so the question is not asked again."""
        try:
            config.update_app_settings(
                {"ui": {"ask_open_folder": False, "open_folder_after_generate": answer}})
            self.status_label.config(
                text=f"{self.status_label['text']}  (choice remembered; "
                     f"change it under Tools → Settings)")
        except Exception as exc:  # noqa: BLE001 - a preference is never worth failing over
            logger.warning("Could not remember the open-folder choice: %s", exc)

    @staticmethod
    def _open_folder(path):
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    # ------------------- signature tools (menu) -------------------
    def _build_menu(self, root):
        menubar = tk.Menu(root)
        tools = tk.Menu(menubar, tearoff=0)
        tools.add_command(label="Products...", command=self.open_products_dialog)
        tools.add_command(label="Receipt History...", command=self.open_history_dialog)
        tools.add_separator()
        tools.add_command(label="Settings...", command=self.open_settings_dialog)
        tools.add_command(label="Fields && Columns...", command=self.open_fields_dialog)
        tools.add_command(label="Signing Keys...", command=self.open_signing_keys_dialog)
        tools.add_command(label="Restore Default Templates...",
                          command=self.restore_default_templates)
        tools.add_separator()
        tools.add_command(label="Verify Receipt...", command=self.verify_receipt_dialog)
        tools.add_command(label="Sign Existing PDF(s)...", command=self.sign_existing_pdfs_dialog)
        menubar.add_cascade(label="Tools", menu=tools)
        root.config(menu=menubar)

    def restore_default_templates(self):
        """Put the shipped templates back, keeping a copy of what was there.

        The way out of an edit that broke rendering. Templates are ordinary
        HTML and CSS, and the app refuses to start against a broken one -- which
        is correct, and leaves someone with no way back if they cannot spot the
        typo. Every replaced file is copied to Templates/.replaced-<stamp>/
        rather than deleted: this is the recovery tool, so it cannot itself be
        the thing that loses work.
        """
        import datetime
        import shutil
        import receipt_render

        if not messagebox.askyesno(
                "Restore default templates?",
                "Replace every template with the version the app ships with.\n\n"
                "Your current templates are copied into a dated folder inside "
                "Templates first, so nothing is lost -- but any styling you "
                "changed will stop applying until you copy it back.",
                parent=self.root):
            return

        templates_dir = config.TEMPLATES_DIR
        backup = os.path.join(
            templates_dir,
            ".replaced-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        try:
            saved = 0
            if os.path.isdir(templates_dir):
                os.makedirs(backup, exist_ok=True)
                for name in sorted(os.listdir(templates_dir)):
                    source = os.path.join(templates_dir, name)
                    if os.path.isfile(source) and not name.startswith("."):
                        shutil.copy2(source, os.path.join(backup, name))
                        saved += 1

            restored = config.install_default_templates(force=True)
            receipt_render.clear_template_cache()
        except Exception as exc:                 # noqa: BLE001 - reported
            show_error(self.root, "Could not restore the templates", str(exc),
                       traceback.format_exc())
            return

        if not restored:
            # Running from a source checkout: the bundled and installed
            # directories are the same folder, so there is nothing to copy from.
            messagebox.showinfo(
                "Nothing to restore",
                "This build reads its templates straight from the source "
                "folder, so there is no separate bundled copy to restore from.",
                parent=self.root)
            return

        messagebox.showinfo(
            "Templates restored",
            f"{len(restored)} template(s) restored.\n\n"
            f"The previous {saved} file(s) were copied to:\n{backup}",
            parent=self.root)

    def open_settings_dialog(self):
        """Edit appsettings.json in the app rather than in a text editor."""
        import settings_ui

        settings_ui.open_settings(self.root, on_saved=self._settings_saved)

    def open_products_dialog(self):
        """Manage the catalogue of products a receipt can be built from."""
        import settings_ui

        settings_ui.open_products(
            self.root, on_saved=lambda: self.status_label.config(text="Products saved"))

    def open_history_dialog(self):
        """Browse past receipts and pull one back into the form to correct it."""
        import settings_ui

        settings_ui.open_history(self.root, on_load=self.load_from_history)

    def load_from_history(self, entry):
        """Fill the form from a recorded receipt.

        The original invoice number is restored deliberately: correcting a
        receipt should reissue *that* receipt, not consume a fresh number. The
        existing collision prompt then decides whether to replace the old PDF or
        keep both. Changing the number by hand still works, and the counter is
        pushed past whatever is used.
        """
        import receipt_history

        data = receipt_history.to_form_data(entry)
        self.fill_form(data)
        self.status_label.config(
            text=f"Loaded {data['inv_no']} from history — edit and generate to reissue it")

    def fill_form(self, data):
        """Put a stored receipt back on the form.

        Shared by the history reload and the draft restore: they differ only in
        where the data came from, and two copies of this would drift the moment
        a field was added.
        """
        self.close_date_picker()

        if data.get("receipt_type") in self.type_labels:
            self.receipt_type.set(data["receipt_type"])
        self.cust_name.set(data.get("cust", ""))
        self.cust_phone.set(data.get("phone", ""))
        self.cust_email.set(data.get("email", ""))
        self.shipping.set(data.get("shipping", ""))
        if data.get("date_str"):
            self.date.set(data["date_str"])

        self.order_plan = data.get("installment") or {}
        self.shipment_fees = data.get("shipments") or []
        self.payment_method.set(data.get("payment_method", "") or "")
        self.set_receipt_field_values(data)
        self.refresh_order_plan_label()
        for child in self.items_tree.get_children():
            self.items_tree.delete(child)
        for item in data.get("items") or []:
            self.items_tree.insert("", tk.END, values=self.item_to_row(item))

        # Set the number last: refresh_invoice_number would otherwise overwrite
        # it, and _claim_invoice_number compares against the suggestion to decide
        # whether to consume a new number.
        self.inv_no.set(data.get("inv_no", ""))

    def current_form_data(self):
        """Everything typed in, in the shape generation and drafts both use.

        Reads the widgets and *nothing else* -- in particular it does not touch
        the invoice counter, which is what lets a draft be saved without
        consuming a number.
        """
        items = [self.item_at(row) for row in self.items_tree.get_children()]
        data = {
            "inv_no": self.inv_no.get().strip(),
            "date_str": self.date.get().strip(),
            "cust": self.cust_name.get().strip(),
            "phone": self.cust_phone.get().strip(),
            "email": self.cust_email.get().strip(),
            "receipt_type": self.receipt_type.get(),
            "shipping": self.shipping.get().strip(),
            "items": items,
        }
        if self.order_plan:
            data["installment"] = self.order_plan
        if self.shipment_fees:
            data["shipments"] = self.shipment_fees
        if self.payment_method.get().strip():
            data["payment_method"] = self.payment_method.get().strip()
        for key, value in self.receipt_field_values().items():
            if str(value).strip():
                data[key] = value
        return data

    def save_draft(self):
        """Keep an unfinished receipt without issuing it.

        Consumes no invoice number: a draft is not a receipt. The number showing
        in the box is kept as a suggestion and offered again on restore.
        """
        import drafts

        data = self.current_form_data()
        if not data["cust"] and not data["items"]:
            messagebox.showinfo(
                "Nothing to save",
                "Fill in a customer or add an item first.", parent=self.root)
            return
        try:
            record = drafts.add(data)
        except Exception as exc:                 # noqa: BLE001 - reported
            show_error(self.root, "Could not save the draft", str(exc),
                       traceback.format_exc())
            return
        self.status_label.config(
            text=f"Draft saved: {record['name']} — no invoice number was used")

    def open_drafts(self):
        """Pick a saved draft to carry on with, or delete one."""
        import settings_ui
        settings_ui.open_drafts(self.root, on_load=self.load_draft)

    def load_draft(self, draft):
        import drafts

        data = drafts.to_form_data(draft)
        self.fill_form(data)
        self.status_label.config(
            text=f"Restored draft: {draft.get('name', '')} — "
                 f"generate when you are ready")

    def open_signing_keys_dialog(self):
        """Create or import the key that signs receipts, without a command line."""
        import settings_ui

        settings_ui.open_signing_keys(
            self.root,
            on_changed=lambda: self.status_label.config(text="Signing key updated"))

    def open_fields_dialog(self):
        """Edit the item columns, receipt fields and warranty options."""
        import settings_ui

        settings_ui.open_fields(self.root, on_saved=self._settings_saved)

    def _settings_saved(self):
        """Apply what can be applied live, and say what needs a restart.

        Currency labels, the receipt-type list and the item columns are all built
        during __init__, so changing them re-lays-out the whole window. Rather
        than rebuild it underneath the user -- and risk losing a part-typed
        receipt -- take the safe subset now and be explicit about the rest.
        """
        receipt_render.clear_template_cache()
        self.refresh_invoice_number()
        self.status_label.config(
            text="Settings saved. Some changes (currency labels, receipt types, "
                 "item columns) apply next time the app starts.")

    def verify_receipt_dialog(self):
        """Pick a PDF and report one of: Verified / Invalid / Not found."""
        pdf_path = filedialog.askopenfilename(
            title="Select a receipt PDF to verify",
            initialdir=OUTPUT_DIR if os.path.isdir(OUTPUT_DIR) else APP_DIR,
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not pdf_path:
            return

        _key_path, cert_path = receipt_service.signing_key_paths()
        try:
            result = receipt_signing.verify_pdf(pdf_path, cert_path)
        except FileNotFoundError as exc:
            messagebox.showerror("Cannot verify", str(exc))
            self.status_label.config(text="Verification unavailable")
            return
        except Exception as exc:
            messagebox.showerror("Cannot verify", f"Verification failed:\n{exc}")
            self.status_label.config(text="Verification failed")
            return

        lines = [result.detail]
        if result.signer:
            lines.append(f"\nSigner: {result.signer}")
        if result.signed_time:
            lines.append(f"Signed: {result.signed_time}")
        body = "\n".join(lines)

        if result.status == receipt_signing.VERIFIED:
            messagebox.showinfo(result.title, body)
        elif result.status == receipt_signing.NOT_FOUND:
            messagebox.showwarning(result.title, body)
        else:
            messagebox.showerror(result.title, body)
        self.status_label.config(text=f"{result.title}: {os.path.basename(pdf_path)}")

    def sign_existing_pdfs_dialog(self):
        """Sign one or more previously generated (unsigned) receipt PDFs in place."""
        paths = filedialog.askopenfilenames(
            title="Select PDF(s) to sign",
            initialdir=OUTPUT_DIR if os.path.isdir(OUTPUT_DIR) else APP_DIR,
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not paths:
            return

        signing = load_app_settings()["signing"]
        key_path, cert_path = receipt_service.signing_key_paths()
        if not (key_path and os.path.isfile(key_path) and cert_path and os.path.isfile(cert_path)):
            messagebox.showerror(
                "Cannot sign",
                "The signing key/certificate was not found.\n"
                f"Expected:\n  {key_path or '(unset)'}\n  {cert_path or '(unset)'}\n\n"
                "Run 'python keygen.py' once to create them.",
            )
            return

        signed, skipped, failed = 0, 0, []
        for path in paths:
            try:
                if receipt_signing.is_signed(path):
                    skipped += 1
                    continue
                receipt_signing.sign_pdf(
                    path, key_path, cert_path,
                    passphrase=signing.get("key_passphrase", "") or None,
                    reason=signing.get("reason", "") or None,
                    location=signing.get("location", "") or None,
                    name=signing.get("signer_name", "") or None,
                    tsa_url=signing.get("tsa_url", "") or None,
                )
                signed += 1
            except Exception as exc:
                failed.append(f"{os.path.basename(path)}: {exc}")

        summary = [f"Signed: {signed}", f"Already signed (skipped): {skipped}", f"Failed: {len(failed)}"]
        if failed:
            summary.append("\n" + "\n".join(failed))
        text = "\n".join(summary)
        if failed:
            messagebox.showwarning("Sign Existing PDF(s)", text)
        else:
            messagebox.showinfo("Sign Existing PDF(s)", text)
        self.status_label.config(text=f"Signed {signed}, skipped {skipped}, failed {len(failed)}")

def run_smoke_test():
    """Render one PDF headlessly to prove a packaged build works. Returns an exit code.

    Deliberately never raises. The packaged exe is built windowed
    (console=False), where an escaping exception is shown as a modal traceback
    dialog -- which would block the build script's wait indefinitely and, with no
    console attached, report nothing. Failures go to the log and the exit code.
    """
    try:
        # Import what the menus import lazily. A module that PyInstaller failed
        # to bundle would otherwise only surface when a user clicks the menu in
        # the packaged app -- the hardest place to notice it, and long after the
        # build reported success.
        import settings_ui  # noqa: F401
        import invoice_counter  # noqa: F401
        import receipt_history  # noqa: F401
        import product_catalogue  # noqa: F401

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        pdf_path = os.path.join(OUTPUT_DIR, "_packaged_smoke_test.pdf")
        html_content = receipt_render.build_html(
            "INV-W0000",
            date.today().strftime(config.date_display_format()),
            "Smoke Test Customer",
            "000-000-0000",
            "smoke@example.com",
            [{
                "sku": "TEST",
                "desc": "Packaged executable smoke test item",
                "serial": "-",
                "qty": 2,
                "price": 1.0,
                "discount": 0.5,
                "tax": 0.2,
                "warranty": "No Warranty",
            }],
            "Online",
            1.0,
        )
        # Deliberately render_pdf and not generate(): a build check must not add
        # a fake receipt to the user's history or consume an invoice number.
        receipt_service.render_pdf(html_content, pdf_path)
    except Exception:
        detail = traceback.format_exc()
        logger.error("Packaged smoke test failed\n%s", detail)
        if sys.stderr is not None:      # None in a windowed (console=False) build
            sys.stderr.write(detail)
        return 1
    return 0

# ------------------- run -------------------
def launch():
    """Start the GUI, or explain in plain language why it cannot start.

    Startup reads and validates the settings, and a bad value there used to
    escape as a traceback -- which in the packaged build means a raw traceback
    dialog, or nothing at all, since a windowed exe has no console. A settings
    problem is the most likely reason the app will not open and the one a user
    can actually fix, so it gets a readable message naming the file and key.
    """
    ReceiptApp.enable_dpi_awareness()
    root = tk.Tk()
    try:
        ReceiptApp(root)
    except config.ConfigError as exc:
        return _report_startup_failure(
            root, "Cannot start: settings problem", str(exc), 2)
    except Exception as exc:  # noqa: BLE001 - last resort, reported not swallowed
        return _report_startup_failure(root, "Cannot start", str(exc), 1)
    root.mainloop()
    return 0


def _report_startup_failure(root, title, summary, code):
    """Show why startup failed, then tear the half-built root down cleanly."""
    detail = traceback.format_exc()
    try:
        root.withdraw()
        show_error(root, title, summary, detail)
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
    return code


if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        raise SystemExit(run_smoke_test())
    raise SystemExit(launch())
