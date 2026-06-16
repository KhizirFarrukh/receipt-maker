import tkinter as tk
from tkinter import ttk, messagebox
import base64
import calendar
from datetime import date, datetime
import json
import mimetypes
import os
import re
import sys
import html as html_utils

# ------------------- file paths -------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else BASE_DIR
RESOURCE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
HEADER_FILE = os.path.join(RESOURCE_DIR, "header.html")
FOOTER_FILE = os.path.join(RESOURCE_DIR, "footer.html")
APP_SETTINGS_FILE = os.path.join(APP_DIR, "appsettings.json")
FILENAME_CONFIG_FILE = os.path.join(APP_DIR, "filename_config.json")
OUTPUT_DIR = os.path.join(APP_DIR, "invoices")
PDF_MARGIN_TOP = "150px"
PDF_MARGIN_BOTTOM = "100px"
PDF_MARGIN_LEFT = "24px"
PDF_MARGIN_RIGHT = "24px"

if getattr(sys, "frozen", False):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(RESOURCE_DIR, "ms-playwright"))

# ------------------- receipt type / invoice numbering config -------------------
# Each receipt type keeps its own invoice series via a single-letter prefix.
RECEIPT_TYPES = {
    "Online":   "W",   # web purchase
    "In Store": "S",   # in-store purchase
}
INVOICE_PREFIX_BASE = "INV-"
INVOICE_START_NUMBER = 1001  # first number for a fresh series, e.g. INV-W1001 / INV-S1001
DATE_DISPLAY_FORMAT = "%d %b %Y"
DATE_PARSE_FORMATS = (
    DATE_DISPLAY_FORMAT,
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
)
FILENAME_FIELD_OPTIONS = ("date", "name", "email", "phone")
DEFAULT_FILENAME_FIELDS = ["date", "name"]
DEFAULT_APP_SETTINGS = {
    "company": {
        "name": "Your Company",
        "address": "Your business address",
        "phone": "000-000-0000",
        "email": "hello@example.com",
        "logo_path": "logo.png",
    }
}

# ------------------- read HTML snippets -------------------
def read_html_file(path):
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def load_app_settings():
    if not os.path.exists(APP_SETTINGS_FILE):
        save_default_app_settings()
        return default_app_settings()

    try:
        with open(APP_SETTINGS_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return default_app_settings()

    settings = default_app_settings()
    company_config = config.get("company", {}) if isinstance(config, dict) else {}
    if isinstance(company_config, dict):
        for key in settings["company"]:
            value = company_config.get(key)
            if isinstance(value, str):
                settings["company"][key] = value.strip()
    return settings

def default_app_settings():
    return {
        "company": dict(DEFAULT_APP_SETTINGS["company"])
    }

def save_default_app_settings():
    try:
        with open(APP_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_APP_SETTINGS, f, indent=2)
            f.write("\n")
    except OSError:
        pass

def load_filename_fields():
    if not os.path.exists(FILENAME_CONFIG_FILE):
        save_default_filename_config()
        return DEFAULT_FILENAME_FIELDS

    try:
        with open(FILENAME_CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_FILENAME_FIELDS

    fields = config.get("filename_fields", DEFAULT_FILENAME_FIELDS)
    if not isinstance(fields, list):
        return DEFAULT_FILENAME_FIELDS

    selected_fields = []
    for field in fields:
        if field in FILENAME_FIELD_OPTIONS and field not in selected_fields:
            selected_fields.append(field)
    return selected_fields

def save_default_filename_config():
    config = {
        "filename_fields": DEFAULT_FILENAME_FIELDS,
        "available_fields": list(FILENAME_FIELD_OPTIONS),
    }
    try:
        with open(FILENAME_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
    except OSError:
        pass

# ------------------- main application -------------------
class ReceiptApp:
    def __init__(self, root):
        self.root = root
        company = load_app_settings()["company"]
        root.title(f"{company['name']} - Receipt Generator")
        root.resizable(True, True)
        self._apply_scaling(root)

        # --- form fields ---
        main_frame = ttk.Frame(root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # row 0: receipt type, invoice no, date
        ttk.Label(main_frame, text="Receipt Type").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.receipt_type = tk.StringVar(value="Online")
        type_combo = ttk.Combobox(
            main_frame,
            textvariable=self.receipt_type,
            values=list(RECEIPT_TYPES.keys()),
            state="readonly",
            width=12,
        )
        type_combo.grid(row=0, column=1, padx=5, pady=2, sticky=tk.W)
        type_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_invoice_number())

        ttk.Label(main_frame, text="Invoice No.").grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)
        self.inv_no = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.inv_no, width=18).grid(row=0, column=3, padx=5, pady=2, sticky=tk.W)

        ttk.Label(main_frame, text="Date").grid(row=0, column=4, sticky=tk.W, padx=5, pady=2)
        self.date = tk.StringVar(value=date.today().strftime(DATE_DISPLAY_FORMAT))
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

        ttk.Label(main_frame, text="Shipping (PKR)").grid(row=2, column=3, sticky=tk.W, padx=5, pady=2)
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

        columns = ("sku", "desc", "serial", "qty", "price", "discount", "tax", "warranty")
        self.items_tree = ttk.Treeview(tree_wrap, columns=columns, show="headings", height=6, selectmode="browse")
        self.items_tree.heading("sku", text="SKU")
        self.items_tree.heading("desc", text="Description")
        self.items_tree.heading("serial", text="Serial Number")
        self.items_tree.heading("qty", text="Qty")
        self.items_tree.heading("price", text="Unit Price (PKR)")
        self.items_tree.heading("discount", text="Discount (PKR)")
        self.items_tree.heading("tax", text="Tax (PKR)")
        self.items_tree.heading("warranty", text="Warranty")

        self.items_tree.column("sku", width=70)
        self.items_tree.column("desc", width=190)
        self.items_tree.column("serial", width=110)
        self.items_tree.column("qty", width=45, anchor=tk.CENTER)
        self.items_tree.column("price", width=90, anchor=tk.E)
        self.items_tree.column("discount", width=90, anchor=tk.E)
        self.items_tree.column("tax", width=90, anchor=tk.E)
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
        ttk.Button(actions_frame, text="Generate PDF Receipt", command=self.generate_pdf).pack(side=tk.LEFT, padx=5)
        ttk.Button(actions_frame, text="Clear Form", command=self.clear_form).pack(side=tk.LEFT, padx=5)

        # status label
        self.status_label = ttk.Label(main_frame, text="")
        self.status_label.grid(row=5, column=0, columnspan=6)

        # populate the initial invoice number once everything is wired
        self.refresh_invoice_number()

        # size the window to fit its contents, clamped to the screen so the
        # action buttons are always visible (even at 1024x768)
        self._size_window(root, main_frame)

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
    def get_invoice_prefix(self, type_label=None):
        type_label = type_label or self.receipt_type.get()
        return f"{INVOICE_PREFIX_BASE}{RECEIPT_TYPES[type_label]}"

    def get_next_invoice_number(self, prefix):
        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)
        max_num = INVOICE_START_NUMBER - 1
        letter = prefix[len(INVOICE_PREFIX_BASE):]
        # The online series also counts legacy unlettered INV-#### files
        # (those belong to online); the in-store series counts only its own.
        if letter.upper() == RECEIPT_TYPES["Online"]:
            letter_pattern = f"{re.escape(letter)}?"
        else:
            letter_pattern = re.escape(letter)
        pattern = re.compile(
            rf"^{re.escape(INVOICE_PREFIX_BASE)}{letter_pattern}(\d+)(?:-.*)?\.pdf$",
            re.IGNORECASE,
        )
        for fname in os.listdir(OUTPUT_DIR):
            match = pattern.match(fname)
            if match:
                num = int(match.group(1))
                if num > max_num:
                    max_num = num
        return max_num + 1

    def refresh_invoice_number(self):
        prefix = self.get_invoice_prefix()
        next_num = self.get_next_invoice_number(prefix)
        self.inv_no.set(f"{prefix}{next_num}")

    # ------------------- date picker -------------------
    def parse_selected_date(self):
        raw_date = self.date.get().strip()
        if not raw_date:
            return None

        for fmt in DATE_PARSE_FORMATS:
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
        self.date.set(selected_date.strftime(DATE_DISPLAY_FORMAT))
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

        labels = ["SKU", "Description", "Serial No.", "Quantity", "Unit Price (PKR)", "Discount (PKR)", "Tax (PKR)"]
        vars_ = [tk.StringVar() for _ in labels]

        for i, (label, var) in enumerate(zip(labels, vars_)):
            ttk.Label(dialog, text=label).grid(row=i, column=0, padx=10, pady=5, sticky=tk.W)
            ttk.Entry(dialog, textvariable=var).grid(row=i, column=1, padx=10, pady=5)

        # warranty: a type, plus a months count used only for "Months"
        warranty_row = len(labels)
        ttk.Label(dialog, text="Warranty").grid(row=warranty_row, column=0, padx=10, pady=5, sticky=tk.W)
        warranty_type = tk.StringVar(value="Months")
        warranty_combo = ttk.Combobox(
            dialog,
            textvariable=warranty_type,
            values=["Months", "7 Days Checking", "No Warranty"],
            state="readonly",
            width=16,
        )
        warranty_combo.grid(row=warranty_row, column=1, padx=10, pady=5, sticky=tk.W)

        months_row = warranty_row + 1
        ttk.Label(dialog, text="Warranty Months").grid(row=months_row, column=0, padx=10, pady=5, sticky=tk.W)
        warranty_months = tk.StringVar(value="12")
        months_entry = ttk.Entry(dialog, textvariable=warranty_months, width=10)
        months_entry.grid(row=months_row, column=1, padx=10, pady=5, sticky=tk.W)

        def on_warranty_type_change(event=None):
            months_entry.configure(state="normal" if warranty_type.get() == "Months" else "disabled")

        warranty_combo.bind("<<ComboboxSelected>>", on_warranty_type_change)

        # pre-fill the fields when editing an existing row
        if editing:
            current = self.items_tree.item(item_id)["values"]
            sku0, desc0, serial0, qty0, price0, discount0, tax0, warranty0 = current
            for var, value in zip(vars_, (sku0, desc0, serial0, qty0, price0, discount0, tax0)):
                var.set("" if value is None else str(value))
            wtype, wmonths = self.parse_warranty_text(str(warranty0))
            warranty_type.set(wtype)
            if wmonths:
                warranty_months.set(wmonths)
        on_warranty_type_change()

        def save():
            sku = vars_[0].get().strip()
            desc = vars_[1].get().strip()
            serial = vars_[2].get().strip()
            qty = vars_[3].get().strip()
            price = vars_[4].get().strip()
            discount = vars_[5].get().strip()
            tax = vars_[6].get().strip()

            if not desc:
                messagebox.showerror("Error", "Description is required.", parent=dialog)
                return
            try:
                qty_int = int(qty) if qty else 1
                price_float = float(price) if price else 0.0
                discount_float = float(discount) if discount else 0.0
                tax_float = float(tax) if tax else 0.0
            except ValueError:
                messagebox.showerror("Error", "Qty, Price, Discount, and Tax must be numbers.", parent=dialog)
                return
            if discount_float < 0 or tax_float < 0:
                messagebox.showerror("Error", "Discount and Tax cannot be negative.", parent=dialog)
                return

            warranty = self.build_warranty_text(warranty_type.get(), warranty_months.get().strip(), dialog)
            if warranty is None:
                return

            row_values = (
                sku, desc, serial, qty_int,
                f"{price_float:.2f}", f"{discount_float:.2f}", f"{tax_float:.2f}", warranty,
            )
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

    @staticmethod
    def build_warranty_text(warranty_type, months_raw, parent=None):
        """Return the warranty label string, or None if validation failed."""
        if warranty_type == "7 Days Checking":
            return "7 Days Checking Warranty"
        if warranty_type == "No Warranty":
            return "No Warranty"

        # "Months": require a positive whole number.
        try:
            months = int(months_raw)
        except ValueError:
            messagebox.showerror("Error", "Warranty months must be a positive whole number.", parent=parent)
            return None
        if months <= 0:
            messagebox.showerror("Error", "Warranty months must be a positive whole number.", parent=parent)
            return None

        unit = "Month" if months == 1 else "Months"
        return f"{months} {unit} Limited Warranty"

    @staticmethod
    def parse_warranty_text(warranty):
        """Reverse of build_warranty_text: -> (warranty_type, months_str)."""
        text = (warranty or "").strip()
        if "7 Days Checking" in text:
            return ("7 Days Checking", "")
        match = re.match(r"^(\d+)\s+Months?\b", text)
        if match:
            return ("Months", match.group(1))
        return ("No Warranty", "")

    def remove_item(self):
        selected = self.items_tree.selection()
        if selected:
            self.items_tree.delete(selected)

    def clear_form(self):
        self.close_date_picker()
        self.receipt_type.set("Online")
        self.refresh_invoice_number()
        self.cust_name.set("")
        self.cust_phone.set("")
        self.cust_email.set("")
        self.shipping.set("")

        for child in self.items_tree.get_children():
            self.items_tree.delete(child)

        self.status_label.config(text="Form cleared")

    # ------------------- PDF generation -------------------
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
            vals = self.items_tree.item(child)["values"]
            sku, desc, serial, qty, price, discount, tax, warranty = vals
            try:
                qty_int = int(qty)
                price_float = float(price)
                discount_float = float(discount)
                tax_float = float(tax)
            except (ValueError, TypeError):
                messagebox.showerror("Error", f"Invalid numbers in item: {desc}")
                return
            items.append({
                "sku": sku,
                "desc": desc,
                "serial": serial,
                "qty": qty_int,
                "price": price_float,
                "discount": discount_float,
                "tax": tax_float,
                "warranty": warranty
            })

        if not items:
            messagebox.showerror("Error", "At least one item is required.")
            return

        html_content = self.build_html(
            inv_no, date_str, cust, phone, email, items, receipt_type, shipping_float
        )

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        base_filename = self.build_pdf_filename(inv_no, date_str, cust, email, phone)
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
                self.status_label.config(text="Save cancelled")
                return
            if not answer:  # No -> keep the existing file, save a numbered copy
                pdf_path = self.next_available_pdf_path(base_filename)

        try:
            self.render_pdf(html_content, pdf_path)
            self.status_label.config(text=f"Saved: {pdf_path}")
            self.refresh_invoice_number()
        except Exception as e:
            messagebox.showerror("PDF Error", f"Failed to create PDF:\n{e}")
            self.status_label.config(text="PDF generation failed")

    def render_pdf(self, body_html, pdf_path):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed.\n"
                "Run: python -m pip install -r requirements.txt\n"
                "Then run once: python -m playwright install chromium"
            ) from exc

        pdf_options = {
            "path": pdf_path,
            "format": "A4",
            "print_background": True,
            "display_header_footer": True,
            "header_template": self.build_page_header_template(),
            "footer_template": self.build_page_footer_template(),
            "margin": {
                "top": PDF_MARGIN_TOP,
                "bottom": PDF_MARGIN_BOTTOM,
                "left": PDF_MARGIN_LEFT,
                "right": PDF_MARGIN_RIGHT,
            },
        }

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page()
                    page.set_content(body_html, wait_until="load")
                    page.pdf(**pdf_options)
                finally:
                    browser.close()
        except Exception as exc:
            message = str(exc)
            if "Executable doesn't exist" in message or "playwright install" in message:
                raise RuntimeError(
                    "Playwright's Chromium browser is not installed.\n"
                    "Run once: python -m playwright install chromium"
                ) from exc
            raise

    def build_pdf_filename(self, inv_no, date_str, cust, email, phone):
        filename_values = {
            "date": date_str,
            "name": cust,
            "email": email,
            "phone": phone,
        }
        filename_parts = [self.sanitize_filename_part(inv_no)]

        for field in load_filename_fields():
            value = filename_values.get(field, "")
            clean_value = self.sanitize_filename_part(value)
            if clean_value:
                filename_parts.append(clean_value)

        return "-".join(filename_parts) + ".pdf"

    @staticmethod
    def next_available_pdf_path(base_filename):
        """Return a path for base_filename with the smallest free -N suffix
        (e.g. INV-W1001.pdf -> INV-W1001-1.pdf -> INV-W1001-2.pdf)."""
        stem, ext = os.path.splitext(base_filename)
        n = 1
        while True:
            candidate = os.path.join(OUTPUT_DIR, f"{stem}-{n}{ext}")
            if not os.path.exists(candidate):
                return candidate
            n += 1

    def sanitize_filename_part(self, value):
        clean_value = str(value).strip()
        clean_value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", clean_value)
        clean_value = re.sub(r"\s+", " ", clean_value).strip(" .")
        return clean_value

    def build_page_header_template(self):
        header_html = read_html_file(HEADER_FILE).strip() or self.default_header_html()
        header_html = self.render_settings_template(header_html)
        header_html = self.inline_local_images(header_html)
        return f"""<style>
    html, body {{
        margin: 0;
        padding: 0;
        width: 100%;
        font-family: Arial, Helvetica, sans-serif;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
    }}
    .pdf-header {{
        box-sizing: border-box;
        width: 100%;
        padding: 12px {PDF_MARGIN_RIGHT} 0 {PDF_MARGIN_LEFT};
        color: #111827;
    }}
    .store-header {{
        text-align: center;
        border-bottom: 2px solid #1e293b;
        padding-bottom: 7px;
    }}
    .store-logo-img {{
        display: block;
        max-height: 52px;
        max-width: 190px;
        object-fit: contain;
        margin: 0 auto 3px auto;
    }}
    .store-name {{
        font-size: 18px;
        line-height: 1.1;
        font-weight: 700;
    }}
    .store-details {{
        margin-top: 3px;
        font-size: 8px;
        line-height: 1.35;
        color: #334155;
    }}
</style>
<div class="pdf-header">{header_html}</div>"""

    def build_page_footer_template(self):
        footer_html = read_html_file(FOOTER_FILE).strip() or self.default_footer_html()
        footer_html = self.render_settings_template(footer_html)
        footer_html = self.inline_local_images(footer_html)
        return f"""<style>
    html, body {{
        margin: 0;
        padding: 0;
        width: 100%;
        font-family: Arial, Helvetica, sans-serif;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
    }}
    .pdf-footer {{
        box-sizing: border-box;
        width: 100%;
        padding: 6px {PDF_MARGIN_RIGHT} 0 {PDF_MARGIN_LEFT};
        text-align: center;
        color: #475569;
        font-size: 8px;
        line-height: 1.35;
    }}
    .footer-inner {{
        border-top: 1px solid #e2e8f0;
        padding-top: 7px;
    }}
    .footer-text {{
        font-weight: 600;
    }}
    .footer-policy {{
        margin-top: 3px;
    }}
    .footer-terms {{
        margin-top: 2px;
        color: #64748b;
    }}
    .no-signature {{
        margin-top: 3px;
    }}
</style>
<div class="pdf-footer">
    <div class="footer-inner">{footer_html}</div>
</div>"""

    def default_header_html(self):
        return """
<div class="store-header">
    <img src="{{company_logo}}" alt="{{company_name}} Logo" class="store-logo-img" onerror="this.style.display='none';">
    <div class="store-name">{{company_name}}</div>
    <div class="store-details">
        {{company_address}}<br>
        {{company_phone}} | {{company_email}}
    </div>
</div>"""

    @staticmethod
    def default_footer_html():
        return """
<div class="footer-text">
    Thank you for choosing {{company_name}}. Warranty claims require original receipt.
</div>
<div class="footer-policy">
    For our detailed warranty policy, visit https://chawlatech.pk/pages/warranty-policy
</div>
<div class="footer-terms">
    By purchasing from Chawla Tech, you agree to our Terms of Service, Privacy Policy, &amp; Warranty Policy (available at chawlatech.pk).
</div>
<div class="no-signature">
    This is a computer-generated receipt and does not require a signature.
</div>"""

    def render_settings_template(self, template_html):
        company = load_app_settings()["company"]
        logo_path = company["logo_path"]
        if not self.logo_source_available(logo_path):
            template_html = self.remove_logo_image_tags(template_html)
            logo_path = ""

        replacements = {
            "{{company_name}}": self.escape(company["name"]),
            "{{company_address}}": self.escape_address(company["address"]),
            "{{company_phone}}": self.escape(company["phone"]),
            "{{company_email}}": self.escape(company["email"]),
            "{{company_logo}}": self.escape(logo_path),
            "{{company_logo_path}}": self.escape(logo_path),
        }

        for placeholder, value in replacements.items():
            template_html = template_html.replace(placeholder, value)
        return template_html

    @staticmethod
    def logo_source_available(src):
        clean_src = str(src).strip()
        if not clean_src:
            return False

        lowered = clean_src.lower()
        if lowered.startswith(("data:", "http://", "https://", "file:", "about:")):
            return True

        return os.path.isfile(ReceiptApp.resolve_local_asset_path(clean_src))

    @staticmethod
    def remove_logo_image_tags(template_html):
        return re.sub(
            r"\s*<img\b[^>]*\bsrc\s*=\s*(['\"])\{\{company_logo(?:_path)?\}\}\1[^>]*>\s*",
            "\n",
            template_html,
            flags=re.IGNORECASE,
        )

    def escape_address(self, address):
        lines = [line.strip() for line in str(address).splitlines() if line.strip()]
        if not lines:
            return ""
        return "<br>".join(self.escape(line) for line in lines)

    @staticmethod
    def inline_local_images(html):
        def replace_src(match):
            quote = match.group(1)
            src = html_utils.unescape(match.group(2).strip())
            lowered = src.lower()
            if lowered.startswith(("data:", "http://", "https://", "file:", "about:")):
                return match.group(0)

            image_path = ReceiptApp.resolve_local_asset_path(src)
            if not os.path.isfile(image_path):
                return match.group(0)

            mime_type = mimetypes.guess_type(image_path)[0] or "application/octet-stream"
            with open(image_path, "rb") as image_file:
                encoded = base64.b64encode(image_file.read()).decode("ascii")
            return f'src={quote}data:{mime_type};base64,{encoded}{quote}'

        return re.sub(r"src=(['\"])([^'\"]+)\1", replace_src, html, flags=re.IGNORECASE)

    @staticmethod
    def resolve_local_asset_path(src):
        clean_src = src.split("#", 1)[0].split("?", 1)[0].strip()
        if not clean_src:
            return ""

        if os.path.isabs(clean_src):
            return os.path.abspath(clean_src)

        for base_dir in (APP_DIR, RESOURCE_DIR):
            asset_path = os.path.abspath(os.path.join(base_dir, clean_src))
            try:
                if os.path.commonpath([base_dir, asset_path]) != base_dir:
                    continue
            except ValueError:
                continue

            if os.path.isfile(asset_path):
                return asset_path
        return ""

    @staticmethod
    def file_url_for_directory(path):
        normalized = os.path.abspath(path).replace("\\", "/").rstrip("/")
        return f"file:///{normalized}/"

    # ------------------- HTML construction -------------------
    def build_html(self, inv_no, date_str, cust, phone, email, items, receipt_type="Online", shipping=0.0):
        type_badge = "ONLINE ORDER" if receipt_type == "Online" else "IN-STORE SALE"

        # Optional columns appear only when at least one line item uses them.
        show_discount = any(i.get("discount", 0.0) for i in items)
        show_tax = any(i.get("tax", 0.0) for i in items)

        header_cells = (
            "<th>SKU</th><th>Item Description</th><th>Serial Number</th>"
            "<th>Qty</th><th>Unit Price</th>"
        )
        if show_discount:
            header_cells += "<th>Discount</th>"
        if show_tax:
            header_cells += "<th>Tax</th>"
        header_cells += "<th>Amount</th>"

        rows_html = ""
        for item in items:
            warranty_display = ""
            if item["warranty"] and item["warranty"] != "No Warranty":
                warranty_display = (
                    f'<br/><span class="item-warranty-text">'
                    f'{self.escape(item["warranty"])}</span>'
                )
            discount = item.get("discount", 0.0)
            tax = item.get("tax", 0.0)
            amount = item["qty"] * item["price"]
            cells = (
                f"<td>{self.escape(item['sku']) or '-'}</td>"
                f"<td>{self.escape(item['desc'])}{warranty_display}</td>"
                f"<td>{self.escape(item['serial']) or '-'}</td>"
                f'<td class="num">{item["qty"]}</td>'
                f'<td class="num">Rs. {item["price"]:.2f}</td>'
            )
            if show_discount:
                cells += f'<td class="num">{("Rs. %.2f" % discount) if discount else "-"}</td>'
            if show_tax:
                cells += f'<td class="num">{("Rs. %.2f" % tax) if tax else "-"}</td>'
            cells += f'<td class="num">Rs. {amount:.2f}</td>'
            rows_html += f"<tr>{cells}</tr>"

        subtotal = sum(i["qty"] * i["price"] for i in items)
        total_discount = sum(i.get("discount", 0.0) for i in items)
        total_tax = sum(i.get("tax", 0.0) for i in items)
        total = subtotal + total_tax - total_discount + shipping

        # Break out the subtotal/components only when there is something besides
        # the line items to show; otherwise just show TOTAL.
        totals_rows = ""
        if total_tax or total_discount or shipping:
            totals_rows += (
                f'<tr class="totals-sub"><td>Subtotal</td>'
                f'<td align="right">Rs. {subtotal:,.2f}</td></tr>'
            )
            if total_tax:
                totals_rows += (
                    f'<tr class="totals-sub"><td>Taxes</td>'
                    f'<td align="right">Rs. {total_tax:,.2f}</td></tr>'
                )
            if total_discount:
                totals_rows += (
                    f'<tr class="totals-sub"><td>Discounts</td>'
                    f'<td align="right">- Rs. {total_discount:,.2f}</td></tr>'
                )
            if shipping:
                totals_rows += (
                    f'<tr class="totals-sub"><td>Shipping Fees</td>'
                    f'<td align="right">Rs. {shipping:,.2f}</td></tr>'
                )

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<base href="{self.file_url_for_directory(RESOURCE_DIR)}">
<style>
    @page {{
        size: A4;
    }}
    html, body {{
        margin: 0;
        padding: 0;
    }}
    body {{
        font-family: Helvetica, Arial, sans-serif;
        font-size: 10pt;
        color: #111;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
    }}
    .receipt-title {{
        font-size: 14pt;
        font-weight: bold;
        text-align: center;
        margin: 0 0 4px 0;
        letter-spacing: 0;
    }}
    .type-badge {{
        text-align: center;
        font-size: 9pt;
        font-weight: bold;
        color: #ffffff;
        background-color: #0f172a;
        padding: 4px 0;
        margin-bottom: 12px;
    }}
    .meta-table {{
        width: 100%;
        margin-bottom: 12px;
        border-bottom: 1px dashed #94a3b8;
    }}
    .meta-table td {{
        font-size: 9pt;
        padding: 2px 0 6px 0;
    }}
    .customer-box {{
        background-color: #f8fafc;
        padding: 8px;
        margin-bottom: 12px;
        font-size: 9pt;
    }}
    table.items {{
        width: 100%;
        border-collapse: collapse;
        margin: 6px 0;
    }}
    table.items thead {{
        display: table-header-group;
    }}
    table.items th {{
        background-color: #0f172a;
        color: #ffffff;
        padding: 5px 4px;
        font-size: 8pt;
        text-align: left;
        border: 1px solid #0f172a;
    }}
    table.items td {{
        border-bottom: 1px solid #cbd5e1;
        padding: 5px 4px;
        font-size: 9pt;
        vertical-align: top;
    }}
    table.items tr {{
        break-inside: avoid;
        page-break-inside: avoid;
    }}
    table.items td.num {{
        text-align: right;
    }}
    .item-warranty-text {{
        color: #6b7280;
        font-style: italic;
        font-size: 8pt;
    }}
    .totals-table {{
        width: 50%;
        margin-left: 50%;
        margin-top: 8px;
        font-size: 10pt;
    }}
    .totals-table td {{
        padding: 3px 0;
        font-size: 10pt;
    }}
    .totals-table tr.totals-sub td {{
        color: #334155;
    }}
    .totals-table tr.totals-grand td {{
        padding: 6px 0;
        border-top: 2px solid #0f172a;
        border-bottom: 2px solid #0f172a;
        font-weight: bold;
        font-size: 12pt;
    }}
    .customer-box,
    .totals-table {{
        break-inside: avoid;
        page-break-inside: avoid;
    }}
    .policy-page {{
        page-break-before: always;
        break-before: page;
    }}
    .policy-title {{
        font-size: 12pt;
        font-weight: bold;
        text-align: center;
        margin: 0 0 10px 0;
        color: #0f172a;
    }}
    .policy-columns {{
        column-count: 2;
        column-gap: 22px;
    }}
    .policy-section {{
        break-inside: avoid;
        page-break-inside: avoid;
        margin: 0 0 9px 0;
    }}
    .policy-heading {{
        font-size: 9pt;
        font-weight: bold;
        color: #0f172a;
        margin: 0 0 3px 0;
    }}
    .policy-section ul {{
        margin: 0;
        padding-left: 14px;
    }}
    .policy-section li {{
        font-size: 7.8pt;
        line-height: 1.35;
        margin-bottom: 2px;
        color: #1f2937;
    }}
</style>
</head>
<body>

<div class="receipt-title">SALES RECEIPT</div>
<div class="type-badge">{type_badge}</div>

<table class="meta-table">
    <tr>
        <td><strong>Receipt No:</strong> {self.escape(inv_no)}</td>
        <td align="right"><strong>Date:</strong> {self.escape(date_str)}</td>
    </tr>
</table>

<div class="customer-box">
    <strong>Bill To:</strong><br/>
    {self.escape(cust)}<br/>
    {('Phone: ' + self.escape(phone) + '<br/>') if phone else ''}
    {('Email: ' + self.escape(email)) if email else ''}
</div>

<table class="items">
    <thead>
        <tr>{header_cells}</tr>
    </thead>
    <tbody>
        {rows_html}
    </tbody>
</table>

<table class="totals-table">{totals_rows}
    <tr class="totals-grand">
        <td>TOTAL</td>
        <td align="right">Rs. {total:,.2f}</td>
    </tr>
</table>

{self.warranty_policy_html()}

</body>
</html>"""

    @staticmethod
    def escape(text):
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    @staticmethod
    def warranty_policy_html():
        # Second page printed on every receipt. Plain string (not an f-string),
        # so literal braces are not an issue and ampersands are written as &amp;.
        return """
<div class="policy-page">
    <div class="policy-title">🛡️ Chawla Tech — Warranty &amp; Returns Policy (Key Points)</div>
    <div class="policy-columns">
        <div class="policy-section">
            <div class="policy-heading">📦 Returns &amp; Exchanges</div>
            <ul>
                <li><strong>Unopened / Sealed items:</strong> Returnable within 7 days for a full refund or exchange — original seal must be completely intact. Buyer pays return shipping.</li>
                <li><strong>Once opened:</strong> Change-of-mind return is no longer valid, even if the item was never powered on.</li>
                <li><strong>Opened / Used items with a defect:</strong> Eligible for return or replacement within 7 days, after in-store testing confirms the fault. Buyer pays return shipping.</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">📹 Video Proof Requirement (Important)</div>
            <ul>
                <li>For any return or exchange claim involving a wrong, defective, broken, missing, or faulty item (or any missing/faulty part), a video proof is mandatory.</li>
                <li>The video must include the unboxing of the item from the moment the parcel/box is opened. Claims without this video proof will not be accepted.</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">🔧 What's Covered</div>
            <ul>
                <li>Manufacturing/material defects discovered under normal use within 7 days.</li>
                <li>Some products have an extended manufacturer warranty (e.g., 6 months, 1 year) — check the product listing.</li>
                <li>Chinese-imported/grey-market items: 7-day Chawla Tech warranty only — no brand warranty.</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">❌ What's NOT Covered (Warranty Void)</div>
            <ul>
                <li>Electrical damage from power surges, over-voltage, load-shedding, or wrong PSU</li>
                <li>Physical damage (drops, broken pins, cracked screens, bent parts)</li>
                <li>ESD (static electricity) damage</li>
                <li>Liquid, fire, heat, or environmental damage</li>
                <li>Damage from incorrect installation or incompatible parts</li>
                <li>Unauthorized modifications, overclocking, or BIOS/firmware flashing</li>
                <li>Pest damage (insects, lizards, rodents)</li>
                <li>Tampered, removed, or defaced serial numbers/warranty seals</li>
                <li>Continued use after a fault appeared</li>
                <li>Software issues, data loss, viruses</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">🚫 No Returns At All (Ever)</div>
            <ul>
                <li>Digital products &amp; license keys — zero exceptions, no matter what</li>
                <li>Opened hygiene items (earphones, screen protectors)</li>
                <li>Clearance / As-Is items</li>
                <li>Used single-use consumables (thermal paste, cleaning wipes, applied stickers/skins)</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">💸 Refunds</div>
            <ul>
                <li>Cash refunds are processed physically; online refunds within 5–7 business days.</li>
                <li>Items sold at a discount will be refunded the discounted price only.</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">📋 To Make a Claim, You Need</div>
            <ul>
                <li>Proof of purchase (receipt, invoice, or registered mobile/email)</li>
                <li>Video proof including unboxing (for wrong/defective/broken/missing/faulty items)</li>
                <li>Item returned within the applicable window</li>
            </ul>
        </div>
        <div class="policy-section">
            <div class="policy-heading">📞 Contact</div>
            <ul>
                <li><strong>WhatsApp/Phone:</strong> +92 339 282 5523 (Mon–Thu &amp; Sat, 10am–8pm; Fri, 10am-12pm &amp; 3pm-8pm)</li>
                <li><strong>Email:</strong> support@chawlatech.pk (reply within 24 hours)</li>
                <li><strong>In-store:</strong> Karachi — bring the item and receipt</li>
            </ul>
        </div>
    </div>
</div>"""

def run_smoke_test():
    app = ReceiptApp.__new__(ReceiptApp)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf_path = os.path.join(OUTPUT_DIR, "_packaged_smoke_test.pdf")
    html_content = app.build_html(
        "INV-W0000",
        date.today().strftime(DATE_DISPLAY_FORMAT),
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
    app.render_pdf(html_content, pdf_path)

# ------------------- run -------------------
if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        run_smoke_test()
    else:
        ReceiptApp.enable_dpi_awareness()
        root = tk.Tk()
        app = ReceiptApp(root)
        root.mainloop()
