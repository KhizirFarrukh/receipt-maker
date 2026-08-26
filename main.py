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
import receipt_render
import receipt_service
import receipt_signing
from config import (
    APP_DIR,
    OUTPUT_DIR,
    load_app_settings,
)

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

        ttk.Label(main_frame, text=self._money_field("Shipping")).grid(row=2, column=3, sticky=tk.W, padx=5, pady=2)
        self.shipping = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.shipping, width=15).grid(row=2, column=4, padx=5, pady=2, sticky=tk.W)

        # --- items frame ---
        items_frame = ttk.LabelFrame(main_frame, text="Items", padding=5)
        items_frame.grid(row=3, column=0, columnspan=6, sticky=tk.NSEW, padx=5, pady=10)
        main_frame.rowconfigure(3, weight=1)
        for col in range(6):
            main_frame.columnconfigure(col, weight=1)

        # toolbar
        toolbar = ttk.Frame(items_frame)
        toolbar.pack(fill=tk.X, pady=2)
        ttk.Button(toolbar, text="+ Add Item", command=self.add_item).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="Edit Selected", command=self.edit_item).pack(side=tk.LEFT, padx=5)
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
        columns = [f["key"] for f in self.input_fields]
        if self.warranty_enabled:
            columns.append("warranty")
        self.items_tree = ttk.Treeview(tree_wrap, columns=columns, show="headings",
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
                         state="readonly", width=28).grid(
                row=row, column=1, padx=10, pady=5, sticky=tk.W)
        else:
            ttk.Entry(parent, textvariable=var).grid(row=row, column=1, padx=10, pady=5)
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
        dialog.geometry("400x430")
        dialog.resizable(False, False)
        dialog.transient(self.root)  # stay tied to and above the main window

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

        # Warranty options come from fields.json. An option containing "#"
        # prompts for a whole number, so one entry covers 12 Months, 24 Months
        # and anything else the shop offers.
        warranty_cfg = self.fields.get("warranty", {})
        warranty_options = [str(o) for o in warranty_cfg.get("options", []) if str(o).strip()]
        warranty_row = len(labels)
        warranty_type = tk.StringVar(value=warranty_options[0] if warranty_options else "")
        warranty_number = tk.StringVar(value="12")
        number_entry = None

        if warranty_cfg.get("enabled", True) and warranty_options:
            ttk.Label(dialog, text=warranty_cfg.get("label", "Warranty")).grid(
                row=warranty_row, column=0, padx=10, pady=5, sticky=tk.W)
            warranty_combo = ttk.Combobox(
                dialog,
                textvariable=warranty_type,
                values=warranty_options,
                state="readonly",
                width=28,
            )
            warranty_combo.grid(row=warranty_row, column=1, padx=10, pady=5, sticky=tk.W)

            number_row = warranty_row + 1
            ttk.Label(dialog, text="Warranty Number").grid(
                row=number_row, column=0, padx=10, pady=5, sticky=tk.W)
            number_entry = ttk.Entry(dialog, textvariable=warranty_number, width=10)
            number_entry.grid(row=number_row, column=1, padx=10, pady=5, sticky=tk.W)
            months_row = number_row
        else:
            months_row = warranty_row - 1        # nothing shown; keep the layout tight

        def on_warranty_type_change(event=None):
            if number_entry is None:
                return
            needed = config.warranty_option_needs_number(warranty_type.get())
            number_entry.configure(state="normal" if needed else "disabled")

        if warranty_cfg.get("enabled", True) and warranty_options:
            warranty_combo.bind("<<ComboboxSelected>>", on_warranty_type_change)

        # pre-fill the fields when editing an existing row
        if editing:
            existing = self.row_to_item(self.items_tree.item(item_id)["values"])
            for field in labels:
                value = existing.get(field["key"], "")
                var = vars_[field["key"]]
                if isinstance(var, tk.BooleanVar):
                    var.set(str(value).strip().lower() in ("1", "true", "yes"))
                else:
                    var.set("" if value is None else str(value))
            option, number = self.match_warranty_option(
                str(existing.get("warranty", "")), warranty_options)
            if option:
                warranty_type.set(option)
            if number:
                warranty_number.set(number)
        on_warranty_type_change()

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

            self.remember_sticky(values)
            row_values = self.item_to_row(values)
            if editing:
                self.items_tree.item(item_id, values=row_values)
            else:
                self.items_tree.insert("", tk.END, values=row_values)
            dialog.destroy()

        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=months_row + 1, column=0, columnspan=2, pady=15)
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

    def row_to_item(self, values):
        """Tree row (a positional tuple) -> a dict keyed by field key.

        The tree stores values positionally, so every read of a row has to go
        through the same column ordering that wrote it. Doing this in one place
        is what stops a reordered fields.json from silently shifting data into
        the wrong column.
        """
        keys = [f["key"] for f in self.input_fields]
        if self.warranty_enabled:
            keys.append("warranty")
        item = {}
        for key, value in zip(keys, list(values)):
            item[key] = "" if value is None else value
        return item

    def item_to_row(self, item):
        """The inverse of row_to_item: a dict -> the tree's positional tuple."""
        keys = [f["key"] for f in self.input_fields]
        if self.warranty_enabled:
            keys.append("warranty")
        return tuple(item.get(key, "") for key in keys)

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
            item = self.row_to_item(self.items_tree.item(child)["values"])
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

        # Resolve the output path (and any collision) on the main thread, before
        # the worker starts, because the collision prompt is a UI decision.
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        base_filename = receipt_service.build_pdf_filename(inv_no, date_str, cust, email, phone)
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
                signed = receipt_service.generate(data, out_path, progress_cb)
                result_q.put(("done", signed))
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
                        self._on_generated(out_path, msg[1])
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

    def _on_generated(self, out_path, signed):
        state = "signed" if signed else "unsigned"
        self.status_label.config(text=f"Saved ({state}): {out_path}")
        self.refresh_invoice_number()
        logger.info("Generated %s (%s)", out_path, state)

        ui = load_app_settings().get("ui", {})
        if ui.get("ask_open_folder", True):
            answer, remember = ask_with_memory(
                self.root, "Receipt generated",
                f"✓ Receipt {state} and saved:\n{out_path}\n\nOpen the containing folder?")
            if remember:
                self._remember_open_folder(answer)
        else:
            answer = ui.get("open_folder_after_generate", False)

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
        tools.add_command(label="Verify Receipt...", command=self.verify_receipt_dialog)
        tools.add_command(label="Sign Existing PDF(s)...", command=self.sign_existing_pdfs_dialog)
        menubar.add_cascade(label="Tools", menu=tools)
        root.config(menu=menubar)

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
