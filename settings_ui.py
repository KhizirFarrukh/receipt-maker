"""In-app editors for appsettings.json and fields.json.

The app was built files-first: settings are JSON, layout is HTML, and the GUI
only created receipts. That is still true underneath -- this is a front end onto
the same loaders, and hand-editing the files keeps working exactly as before.
What changed is that nobody should *have* to.

Two things make that safe rather than reckless:

* every save goes through ``config.update_app_settings`` / ``save_fields``,
  which validate **before** writing, so the app cannot save a file it would then
  refuse to load; and
* the mtime the read saw is passed back on write, so a file edited by hand while
  the dialog was open is detected instead of being silently overwritten.

The forms are generated from the declarative tables below rather than laid out by
hand. Adding a setting is one row here, which is the only way a settings dialog
stays in step with a config schema that is still growing.
"""
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import config
import product_catalogue

# ---------------------------------------------------------------- form model
# (dotted path, label, kind, options)
#   text / int / bool / choice / path / multiline
SETTINGS_SECTIONS = [
    ("Business", [
        ("company.name", "Business name", "text", {}),
        ("company.address", "Address", "multiline", {}),
        ("company.phone", "Phone", "text", {}),
        ("company.email", "Email", "text", {}),
        ("company.logo_path", "Logo file", "path",
         {"filetypes": [("Images", "*.png *.jpg *.jpeg *.gif"), ("All files", "*.*")],
          "help": "Shown at the top of every page. Leave empty for no logo."}),
    ]),
    ("Links", [
        ("links.terms_url", "Terms of Service", "text",
         {"help": "Linked from the receipt footer. Leave empty and the words still "
                  "print, just without a link."}),
        ("links.privacy_url", "Privacy Policy", "text", {}),
        ("links.warranty_url", "Warranty Policy", "text", {}),
    ]),
    ("Currency", [
        ("currency.symbol", "Symbol", "text", {}),
        ("currency.code", "Code", "text", {"help": "Shown on the form's amount fields only."}),
        ("currency.symbol_space", "Space after symbol", "bool", {}),
        ("currency.position", "Symbol position", "choice",
         {"values": list(config.SYMBOL_POSITIONS)}),
        ("currency.decimals", "Decimal places", "int", {}),
        ("currency.group_style", "Digit grouping", "choice",
         {"values": list(config.GROUP_STYLES),
          "help": "thousand: 1,234,567   indian: 12,34,567   none: 1234567"}),
        ("currency.negative_style", "Negative amounts", "choice",
         {"values": list(config.NEGATIVE_STYLES)}),
        ("currency.group_line_amounts", "Group amounts in the item table", "bool",
         {"help": "Off reproduces how older versions printed line amounts."}),
    ]),
    ("Tax", [
        ("tax.mode", "Tax mode", "choice",
         {"values": list(config.TAX_MODES),
          "help": "exclusive: added to the subtotal.   "
                  "inclusive: already inside your prices, so it is reported not added."}),
    ]),
    ("Document", [
        ("date_format", "Date format", "text",
         {"help": "%d %b %Y gives 31 Jan 2026.   %Y-%m-%d gives 2026-01-31."}),
        ("terms_page.enabled", "Print the terms page", "bool",
         {"help": "Wording lives in Templates/terms.html."}),
        ("document.margin_top", "Top margin", "text",
         {"help": "Must leave room for the page header, or it gets clipped."}),
        ("document.margin_bottom", "Bottom margin", "text", {}),
        ("document.margin_left", "Left margin", "text", {}),
        ("document.margin_right", "Right margin", "text", {}),
    ]),
    ("Numbering", [
        ("invoice.prefix", "Invoice prefix", "text",
         {"help": "Changing this starts a new number series."}),
        ("invoice.start", "First number", "int", {}),
        ("invoice.reconcile_with_filenames", "Cross-check against saved receipts", "bool",
         {"help": "Warns if the counter and the files in invoices/ disagree."}),
    ]),
    ("Signing", [
        ("signing.enabled", "Digitally sign receipts", "bool", {}),
        ("signing.signer_name", "Signer name", "text",
         {"help": "Also becomes the organisation on a certificate you create."}),
        ("signing.private_key_path", "Private key", "path",
         {"filetypes": [("Key files", "*.pem *.key"), ("All files", "*.*")]}),
        ("signing.certificate_path", "Certificate", "path",
         {"filetypes": [("Certificates", "*.pem *.crt *.cer"), ("All files", "*.*")]}),
        ("signing.reason", "Reason", "text", {}),
        ("signing.location", "Location", "text", {}),
        ("signing.tsa_url", "Timestamp server", "text", {"help": "Optional. http:// or https://"}),
    ]),
    ("Interface", [
        ("ui.ask_open_folder", "Ask to open the folder after generating", "bool", {}),
        ("ui.open_folder_after_generate", "…and when not asking, open it anyway", "bool", {}),
    ]),
    ("Advanced", [
        ("render.block_external_requests", "Block internet access while rendering", "bool",
         {"help": "Keeps receipts identical offline. Leave on unless you know why not."}),
        ("render.fail_on_missing_image", "Treat a missing image as an error", "bool",
         {"help": "On: refuse to issue a receipt whose logo is missing."}),
        ("render.timeout_ms", "Render timeout (ms)", "int", {}),
        ("fonts.family", "Embedded font family", "text",
         {"help": "Leave empty to use the system font."}),
        ("fonts.fallback", "Font fallback", "text", {}),
    ]),
]

#: Editable list-of-record settings, rendered as a small table per section.
#: (section, dotted path, columns) where columns are (key, label, kind, options).
LIST_SETTINGS = [
    ("Tax", "tax.rows", "Tax rows", [
        ("label", "Label", "text", {}),
        ("type", "Type", "choice", {"values": list(config.TAX_ROW_TYPES)}),
        ("value", "Value", "text", {}),
        ("applies_to", "Applies to", "choice", {"values": list(config.TAX_BASES)}),
    ]),
    ("Numbering", "receipt_types", "Receipt types", [
        ("label", "Label", "text", {}),
        ("code", "Code", "text", {}),
        ("badge_text", "Badge on receipt", "text", {}),
        ("legacy_unlettered", "Owns old un-lettered numbers", "bool", {}),
    ]),
]


def get_path(data, dotted):
    current = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def set_path(data, dotted, value):
    parts = dotted.split(".")
    current = data
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


# ---------------------------------------------------------------- widgets
def build_row(parent, row, path, label, kind, options, value, variables):
    """Add one labelled input to a grid. Returns nothing; registers its variable."""
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 10), pady=4)

    if kind == "bool":
        var = tk.BooleanVar(value=bool(value))
        ttk.Checkbutton(parent, variable=var).grid(row=row, column=1, sticky=tk.W, pady=4)
    elif kind == "choice":
        var = tk.StringVar(value=str(value or ""))
        ttk.Combobox(parent, textvariable=var, values=options.get("values", []),
                     state="readonly", width=26).grid(row=row, column=1, sticky=tk.W, pady=4)
    elif kind == "multiline":
        var = tk.StringVar(value=str(value or ""))
        text = tk.Text(parent, height=3, width=40, wrap=tk.WORD)
        text.insert("1.0", str(value or ""))
        text.grid(row=row, column=1, sticky=tk.EW, pady=4)
        variables[path] = ("multiline", text)
        _add_help(parent, row, options)
        return
    elif kind == "path":
        var = tk.StringVar(value=str(value or ""))
        wrap = ttk.Frame(parent)
        wrap.grid(row=row, column=1, sticky=tk.EW, pady=4)
        ttk.Entry(wrap, textvariable=var, width=32).pack(side=tk.LEFT)

        def browse():
            chosen = filedialog.askopenfilename(
                title=f"Select {label}", parent=parent.winfo_toplevel(),
                filetypes=options.get("filetypes", [("All files", "*.*")]))
            if chosen:
                # Store a path relative to the app folder when it sits inside it,
                # so a config stays portable between machines.
                try:
                    relative = os.path.relpath(chosen, config.APP_DIR)
                    var.set(chosen if relative.startswith("..") else relative.replace("\\", "/"))
                except ValueError:
                    var.set(chosen)

        ttk.Button(wrap, text="Browse…", command=browse).pack(side=tk.LEFT, padx=(6, 0))
    else:
        var = tk.StringVar(value="" if value is None else str(value))
        ttk.Entry(parent, textvariable=var, width=34).grid(
            row=row, column=1, sticky=tk.EW, pady=4)

    variables[path] = (kind, var)
    _add_help(parent, row, options)


def _add_help(parent, row, options):
    help_text = options.get("help")
    if help_text:
        ttk.Label(parent, text=help_text, foreground="#64748b",
                  wraplength=430, justify=tk.LEFT).grid(
            row=row, column=2, sticky=tk.W, padx=(12, 0))


def read_variables(variables):
    """Collect the form back into a nested dict of changes."""
    changes = {}
    for path, (kind, var) in variables.items():
        if kind == "multiline":
            value = var.get("1.0", tk.END).rstrip("\n")
        elif kind == "int":
            raw = str(var.get()).strip()
            try:
                value = int(raw)
            except ValueError:
                # Leave it as typed; validate() produces the message that names
                # the key, which is better than one invented here.
                value = raw
        else:
            value = var.get()
        set_path(changes, path, value)
    return changes


# ---------------------------------------------------------------- list editor
class RecordListEditor(ttk.Frame):
    """A small table for a list of records (tax rows, receipt types)."""

    def __init__(self, parent, title, columns, records):
        super().__init__(parent)
        self.columns = columns
        self.records = [dict(r) for r in records or []]

        ttk.Label(self, text=title, font=("TkDefaultFont", 9, "bold")).pack(anchor=tk.W)
        keys = [c[0] for c in columns]
        self.tree = ttk.Treeview(self, columns=keys, show="headings", height=4,
                                 selectmode="browse")
        for key, label, _kind, _options in columns:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=130)
        self.tree.pack(fill=tk.BOTH, expand=True, pady=(2, 4))

        buttons = ttk.Frame(self)
        buttons.pack(anchor=tk.W)
        ttk.Button(buttons, text="Add", command=self.add).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Edit", command=self.edit).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Remove", command=self.remove).pack(side=tk.LEFT)
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for record in self.records:
            self.tree.insert("", tk.END, values=[record.get(c[0], "") for c in self.columns])

    def _selected_index(self):
        selection = self.tree.selection()
        if not selection:
            return None
        return self.tree.index(selection[0])

    def add(self):
        record = self._edit_record({})
        if record is not None:
            self.records.append(record)
            self.refresh()

    def edit(self):
        index = self._selected_index()
        if index is None:
            messagebox.showinfo("Edit", "Select a row first.", parent=self.winfo_toplevel())
            return
        record = self._edit_record(self.records[index])
        if record is not None:
            self.records[index] = record
            self.refresh()

    def remove(self):
        index = self._selected_index()
        if index is not None:
            del self.records[index]
            self.refresh()

    def _edit_record(self, initial):
        dialog = tk.Toplevel(self.winfo_toplevel())
        dialog.title("Row")
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)
        variables = {}
        for row, (key, label, kind, options) in enumerate(self.columns):
            build_row(frame, row, key, label, kind, options, initial.get(key, ""), variables)

        result = {}

        def save():
            result.update(read_variables(variables))
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(self.columns), column=0, columnspan=3, pady=(12, 0))
        ttk.Button(buttons, text="OK", command=save).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        self.winfo_toplevel().wait_window(dialog)
        return result or None


# ---------------------------------------------------------------- settings dialog
class SettingsDialog:
    """Tools → Settings. Edits appsettings.json through the validated save path."""

    def __init__(self, parent, on_saved=None):
        self.parent = parent
        self.on_saved = on_saved
        # Load first, then note the mtime. Loading can legitimately rewrite the
        # file -- a config predating a new setting is migrated on read -- so
        # capturing the mtime beforehand makes every save look like a clash with
        # an edit the app itself just made. The question this answers is only
        # "did the file change while this window was open".
        self.settings = config.load_app_settings()
        self.mtime = config.file_mtime(config.APP_SETTINGS_FILE)
        self.variables = {}
        self.lists = {}

        self.win = tk.Toplevel(parent)
        self.win.title("Settings")
        self.win.transient(parent)

        notebook = ttk.Notebook(self.win)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        pages, next_row = {}, {}
        for name, rows in SETTINGS_SECTIONS:
            page = ttk.Frame(notebook, padding=14)
            page.columnconfigure(1, weight=1)
            notebook.add(page, text=name)
            pages[name] = page
            for index, (path, label, kind, options) in enumerate(rows):
                build_row(page, index, path, label, kind, options,
                          get_path(self.settings, path), self.variables)
            next_row[name] = len(rows)

        for section, path, title, columns in LIST_SETTINGS:
            page = pages.get(section)
            if page is None:
                continue
            editor = RecordListEditor(page, title, columns, get_path(self.settings, path) or [])
            editor.grid(row=next_row[section], column=0, columnspan=3,
                        sticky=tk.EW, pady=(14, 0))
            next_row[section] += 1
            self.lists[path] = editor

        footer = ttk.Frame(self.win, padding=(10, 0, 10, 10))
        footer.pack(fill=tk.X)
        ttk.Label(footer, text=config.APP_SETTINGS_FILE,
                  foreground="#64748b").pack(side=tk.LEFT)
        ttk.Button(footer, text="Cancel", command=self.win.destroy).pack(side=tk.RIGHT)
        ttk.Button(footer, text="Save", command=self.save).pack(side=tk.RIGHT, padx=6)

        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

    def save(self):
        changes = read_variables(self.variables)
        for path, editor in self.lists.items():
            set_path(changes, path, editor.records)

        try:
            config.update_app_settings(changes, known_mtime=self.mtime)
        except config.ConfigConflict:
            if messagebox.askyesno(
                "Settings changed on disk",
                f"{os.path.basename(config.APP_SETTINGS_FILE)} was edited outside the app "
                f"while this window was open.\n\nOverwrite it with what is shown here?",
                parent=self.win,
            ):
                config.update_app_settings(changes)
            else:
                return
        except config.ConfigError as exc:
            # validate() names the file and key; that message beats anything the
            # dialog could invent, and the window stays open on the bad value.
            messagebox.showerror("That setting is not valid", str(exc), parent=self.win)
            return
        except OSError as exc:
            messagebox.showerror("Could not save", str(exc), parent=self.win)
            return

        if self.on_saved:
            self.on_saved()
        self.win.destroy()


def open_settings(parent, on_saved=None):
    dialog = SettingsDialog(parent, on_saved)
    parent.wait_window(dialog.win)


# ---------------------------------------------------------------- fields dialog
FIELD_COLUMNS = [
    ("key", "Key", "text", {}),
    ("label", "Label", "text", {}),
    ("type", "Type", "choice", {"values": list(config.FIELD_TYPES)}),
    ("enabled", "Show on receipt", "bool", {}),
    ("required", "Required", "bool", {}),
    ("sticky", "Remember last value", "bool", {}),
    ("optional_column", "Hide column when unused", "bool", {}),
    ("default", "Default value", "text", {}),
]


class FieldsDialog:
    """Tools → Fields. Edits the item columns, receipt fields and warranty options."""

    def __init__(self, parent, on_saved=None):
        self.parent = parent
        self.on_saved = on_saved
        self.fields = config.load_fields()
        self.mtime = config.file_mtime(config.fields_file())

        self.win = tk.Toplevel(parent)
        self.win.title("Fields")
        self.win.transient(parent)

        notebook = ttk.Notebook(self.win)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        items_page = ttk.Frame(notebook, padding=14)
        notebook.add(items_page, text="Item columns")
        ttk.Label(
            items_page,
            text="The columns of the item table, in order. These are also the rows of the\n"
                 "Add Item form. qty, price and amount can be hidden but not removed —\n"
                 "the totals are calculated from them.",
            foreground="#64748b", justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 8))
        self.item_editor = RecordListEditor(
            items_page, "", FIELD_COLUMNS, self.fields.get("line_item_fields", []))
        self.item_editor.pack(fill=tk.BOTH, expand=True)

        receipt_page = ttk.Frame(notebook, padding=14)
        notebook.add(receipt_page, text="Receipt fields")
        ttk.Label(
            receipt_page,
            text="Extra lines printed under the customer box — a PO number, a\n"
                 "salesperson, a deposit. One left empty prints nothing at all.",
            foreground="#64748b", justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 8))
        self.receipt_editor = RecordListEditor(
            receipt_page, "", FIELD_COLUMNS, self.fields.get("receipt_fields", []))
        self.receipt_editor.pack(fill=tk.BOTH, expand=True)

        warranty_page = ttk.Frame(notebook, padding=14)
        warranty_page.columnconfigure(1, weight=1)
        notebook.add(warranty_page, text="Warranty")
        warranty = self.fields.get("warranty", {})
        self.warranty_vars = {}
        rows = [
            ("enabled", "Offer a warranty choice", "bool", {}),
            ("label", "Field label", "text", {}),
            ("none_option", "Option meaning “no warranty”", "text",
             {"help": "No note is printed for this choice."}),
        ]
        for index, (key, label, kind, options) in enumerate(rows):
            build_row(warranty_page, index, key, label, kind, options,
                      warranty.get(key, ""), self.warranty_vars)

        ttk.Label(warranty_page,
                  text="Options — one per line. Put # where a number should be asked for,\n"
                       "e.g. “# Months Limited Warranty”.",
                  foreground="#64748b", justify=tk.LEFT).grid(
            row=len(rows), column=0, columnspan=3, sticky=tk.W, pady=(12, 4))
        self.options_text = tk.Text(warranty_page, height=6, width=52, wrap=tk.NONE)
        self.options_text.insert("1.0", "\n".join(str(o) for o in warranty.get("options", [])))
        self.options_text.grid(row=len(rows) + 1, column=0, columnspan=3, sticky=tk.EW)

        footer = ttk.Frame(self.win, padding=(10, 0, 10, 10))
        footer.pack(fill=tk.X)
        ttk.Label(footer, text=config.fields_file(), foreground="#64748b").pack(side=tk.LEFT)
        ttk.Button(footer, text="Cancel", command=self.win.destroy).pack(side=tk.RIGHT)
        ttk.Button(footer, text="Save", command=self.save).pack(side=tk.RIGHT, padx=6)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

    def collect(self):
        fields = dict(self.fields)
        fields["line_item_fields"] = [_clean_field(r) for r in self.item_editor.records]
        fields["receipt_fields"] = [_clean_field(r) for r in self.receipt_editor.records]

        warranty = dict(fields.get("warranty", {}))
        for key, (kind, var) in self.warranty_vars.items():
            warranty[key] = var.get()
        warranty["options"] = [line.strip() for line
                               in self.options_text.get("1.0", tk.END).splitlines()
                               if line.strip()]
        fields["warranty"] = warranty
        return fields

    def save(self):
        try:
            config.save_fields(self.collect(), known_mtime=self.mtime)
        except config.ConfigConflict:
            if messagebox.askyesno(
                "Fields changed on disk",
                "fields.json was edited outside the app while this window was open.\n\n"
                "Overwrite it with what is shown here?", parent=self.win):
                config.save_fields(self.collect())
            else:
                return
        except config.ConfigError as exc:
            messagebox.showerror("That field is not valid", str(exc), parent=self.win)
            return
        except OSError as exc:
            messagebox.showerror("Could not save", str(exc), parent=self.win)
            return

        if self.on_saved:
            self.on_saved()
        self.win.destroy()


def _clean_field(record):
    """Drop the empty strings the grid produces for untouched optional keys."""
    cleaned = {}
    for key, value in record.items():
        if key == "default" and value in ("", None):
            continue
        cleaned[key] = value
    return cleaned


def open_fields(parent, on_saved=None):
    dialog = FieldsDialog(parent, on_saved)
    parent.wait_window(dialog.win)


# ---------------------------------------------------------------- signing keys
class SigningKeysDialog:
    """Tools → Signing Keys. Create or import the key that signs receipts.

    The private key never leaves this machine and a passphrase is never stored:
    an imported encrypted key is decrypted once, here, and re-saved into the
    app's own folder. Keeping the passphrase beside the key it unlocks would
    protect nobody, and prompting for it on every receipt is not workable at a
    till -- so the honest thing is to say what happens and let the file
    permissions do the work.
    """

    def __init__(self, parent, on_changed=None):
        self.parent = parent
        self.on_changed = on_changed

        import receipt_service
        self.key_path, self.cert_path = receipt_service.signing_key_paths()

        self.win = tk.Toplevel(parent)
        self.win.title("Signing Keys")
        self.win.transient(parent)

        frame = ttk.Frame(self.win, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        self.status = ttk.Label(frame, justify=tk.LEFT, wraplength=520)
        self.status.pack(anchor=tk.W)

        buttons = ttk.Frame(frame)
        buttons.pack(anchor=tk.W, pady=(14, 0))
        ttk.Button(buttons, text="Create new key…", command=self.create).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Import existing key…",
                   command=self.import_key).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Close", command=self.win.destroy).pack(side=tk.LEFT)

        ttk.Label(
            frame, wraplength=520, justify=tk.LEFT, foreground="#64748b",
            text="The certificate is self-signed, so Adobe Reader shows “valid signature, "
                 "untrusted certificate” — that is expected and not a fault. Publish the "
                 "certificate file so customers can check a receipt against it.\n\n"
                 "Changing the key does not invalidate receipts already issued: the old "
                 "certificate is remembered and they keep verifying.",
        ).pack(anchor=tk.W, pady=(14, 0))

        self.refresh()
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

    def refresh(self):
        import receipt_signing

        if not os.path.isfile(self.key_path):
            self.status.config(
                text="No signing key yet.\n\n"
                     "Receipts cannot be signed until one exists. Create one, or import "
                     "a key you already have.",
                foreground="#b45309")
            return

        info = receipt_signing.certificate_info(self.cert_path)
        if info is None:
            self.status.config(
                text=f"A private key is present at\n  {self.key_path}\n\n"
                     f"but its certificate could not be read:\n  {self.cert_path}",
                foreground="#b91c1c")
            return

        previous = max(0, len(receipt_signing.known_certificate_paths(self.cert_path)) - 1)
        expiry = info["not_after"].strftime("%d %b %Y")
        if info["expired"]:
            note, colour = f"EXPIRED on {expiry}.", "#b91c1c"
        elif info["days_left"] < 60:
            note, colour = f"Expires {expiry} — {info['days_left']} days left.", "#b45309"
        else:
            note, colour = f"Valid until {expiry}.", "#166534"

        lines = [f"Signing as: {info['subject']}", note,
                 f"Key:         {self.key_path}",
                 f"Certificate: {self.cert_path}"]
        if previous:
            lines.append(f"{previous} previous certificate(s) remembered, so older "
                         f"receipts still verify.")
        self.status.config(text="\n".join(lines), foreground=colour)

    # -- actions ---------------------------------------------------------
    def _confirm_replace(self):
        if not os.path.isfile(self.key_path):
            return True
        return messagebox.askyesno(
            "Replace the signing key?",
            "A signing key already exists.\n\n"
            "Receipts you issue from now on will be signed with the new key. "
            "Receipts already issued keep verifying, because the current "
            "certificate is remembered first.\n\nReplace it?",
            parent=self.win)

    def create(self):
        import receipt_signing

        if not self._confirm_replace():
            return
        settings = config.load_app_settings()
        org = (settings["signing"].get("signer_name")
               or settings["company"].get("name") or "").strip()
        try:
            receipt_signing.remember_current_certificate(self.cert_path)
            receipt_signing.generate_key_pair(
                self.key_path, self.cert_path, force=True,
                common_name=f"{org} Receipt Signing" if org else None, org_name=org)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            messagebox.showerror("Could not create the key", str(exc), parent=self.win)
            return
        messagebox.showinfo(
            "Signing key created",
            f"Created for: {org or '(no name set)'}\n\n"
            f"Keep {os.path.basename(self.key_path)} private and back it up. "
            f"Publish {os.path.basename(self.cert_path)} so receipts can be checked.",
            parent=self.win)
        self._changed()

    def import_key(self):
        import receipt_signing

        source = filedialog.askopenfilename(
            title="Select the private key or PKCS#12 file", parent=self.win,
            filetypes=[("Keys and bundles", "*.pem *.key *.der *.p12 *.pfx"),
                       ("All files", "*.*")])
        if not source:
            return
        if not self._confirm_replace():
            return

        passphrase = None
        settings = config.load_app_settings()
        org = (settings["signing"].get("signer_name")
               or settings["company"].get("name") or "").strip()

        # Try without a passphrase first, and only ask if the file needs one --
        # most keys do not, and prompting regardless trains people to type
        # secrets into boxes that did not need them.
        for attempt in range(2):
            try:
                receipt_signing.import_key_pair(
                    source, self.key_path, self.cert_path, passphrase=passphrase,
                    org_name=org, common_name=f"{org} Receipt Signing" if org else None,
                    force=True)
                break
            except receipt_signing.KeyImportError as exc:
                needs_secret = "passphrase" in str(exc).lower()
                if needs_secret and attempt == 0:
                    passphrase = _ask_passphrase(self.win)
                    if passphrase is None:
                        return
                    continue
                messagebox.showerror("Could not import that key", str(exc), parent=self.win)
                return
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Could not import that key", str(exc), parent=self.win)
                return

        messagebox.showinfo(
            "Key imported",
            "The key was imported and saved unencrypted in the app's signing "
            "folder — the passphrase you typed is not stored anywhere.\n\n"
            "Keep that folder private and back it up.",
            parent=self.win)
        self._changed()

    def _changed(self):
        self.refresh()
        if self.on_changed:
            self.on_changed()


def _ask_passphrase(parent):
    """Prompt for a passphrase. Returns None if cancelled. Never persisted."""
    dialog = tk.Toplevel(parent)
    dialog.title("Passphrase")
    frame = ttk.Frame(dialog, padding=16)
    frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(frame, text="This key is encrypted. Enter its passphrase:").pack(anchor=tk.W)
    value = tk.StringVar()
    entry = ttk.Entry(frame, textvariable=value, show="•", width=34)
    entry.pack(anchor=tk.W, pady=(8, 0))
    entry.focus_set()
    ttk.Label(frame, text="It is used once, to read the key, and is not saved.",
              foreground="#64748b").pack(anchor=tk.W, pady=(6, 0))

    result = {"value": None}

    def ok():
        result["value"] = value.get()
        dialog.destroy()

    buttons = ttk.Frame(frame)
    buttons.pack(pady=(14, 0))
    ttk.Button(buttons, text="OK", command=ok).pack(side=tk.LEFT, padx=4)
    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT)
    entry.bind("<Return>", lambda e: ok())
    dialog.transient(parent)
    dialog.grab_set()
    parent.wait_window(dialog)
    return result["value"]


def open_signing_keys(parent, on_changed=None):
    dialog = SigningKeysDialog(parent, on_changed)
    parent.wait_window(dialog.win)


# ---------------------------------------------------------------- history
class HistoryDialog:
    """Tools → Receipt History. Reopen a past receipt to correct it.

    The point is not archiving -- it is that noticing a wrong price after the
    fact should not mean re-typing the whole sale. Records survive their PDFs, so
    a deleted or misplaced file is still recoverable here.
    """

    def __init__(self, parent, on_load=None):
        self.parent = parent
        self.on_load = on_load
        self.entries = []

        self.win = tk.Toplevel(parent)
        self.win.title("Receipt History")
        self.win.transient(parent)

        frame = ttk.Frame(self.win, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        search_row = ttk.Frame(frame)
        search_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(search_row, text="Search").pack(side=tk.LEFT)
        self.search = tk.StringVar()
        entry = ttk.Entry(search_row, textvariable=self.search, width=40)
        entry.pack(side=tk.LEFT, padx=(8, 0))
        entry.focus_set()
        self.search.trace_add("write", lambda *a: self.refresh())
        ttk.Label(search_row, text="number, customer, date, item or SKU",
                  foreground="#64748b").pack(side=tk.LEFT, padx=(8, 0))

        columns = ("date", "number", "customer", "total", "status")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings",
                                 height=14, selectmode="browse")
        for key, label, width in (("date", "Date", 110), ("number", "Receipt No.", 120),
                                  ("customer", "Customer", 200), ("total", "Total", 110),
                                  ("status", "Signed", 80)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width,
                             anchor=tk.E if key == "total" else tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", lambda e: self.load())

        self.note = ttk.Label(frame, foreground="#64748b", wraplength=640,
                              justify=tk.LEFT)
        self.note.pack(anchor=tk.W, pady=(8, 0))

        buttons = ttk.Frame(frame)
        buttons.pack(anchor=tk.W, pady=(10, 0))
        ttk.Button(buttons, text="Load into form", command=self.load).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Open PDF", command=self.open_pdf).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Close", command=self.win.destroy).pack(side=tk.LEFT)

        self.refresh()
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

    def refresh(self):
        import receipt_history

        currency = config.load_app_settings().get("currency")
        everything = receipt_history.entries()
        needle = self.search.get()
        self.entries = [e for e in everything if receipt_history.matches(e, needle)]

        self.tree.delete(*self.tree.get_children())
        for entry in self.entries:
            self.tree.insert("", tk.END, values=receipt_history.summarise(entry, currency))

        if not everything:
            self.note.config(
                text="No receipts recorded yet. Every receipt you generate from now on "
                     "is listed here, and stays listed even if its PDF is deleted.")
        elif not self.entries:
            self.note.config(text=f"Nothing matches “{needle}”.")
        else:
            self.note.config(
                text=f"{len(self.entries)} of {len(everything)} receipts. "
                     f"Loading one fills the form with what it contained — correct it and "
                     f"generate again. It keeps its original number unless you change it.")

    def _selected(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Receipt History", "Select a receipt first.",
                                parent=self.win)
            return None
        return self.entries[self.tree.index(selection[0])]

    def load(self):
        entry = self._selected()
        if entry is None:
            return
        if self.on_load:
            self.on_load(entry)
        self.win.destroy()

    def open_pdf(self):
        entry = self._selected()
        if entry is None:
            return
        path = entry.get("pdf_path", "")
        if not path or not os.path.isfile(path):
            messagebox.showinfo(
                "Receipt History",
                "That receipt's PDF is no longer where it was saved.\n\n"
                "Its details are still here, so you can load it into the form and "
                "generate it again.",
                parent=self.win)
            return
        try:
            os.startfile(path)                                   # noqa: S606
        except AttributeError:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            messagebox.showerror("Could not open", str(exc), parent=self.win)


def open_history(parent, on_load=None):
    dialog = HistoryDialog(parent, on_load)
    parent.wait_window(dialog.win)


# ---------------------------------------------------------------- products
PRODUCT_COLUMNS = [
    ("sku", "SKU", "text", {}),
    ("barcode", "Barcode", "text", {}),
    ("name", "Name", "text", {}),
    ("list_price", "List price", "text", {"help": "Price of a single item."}),
    ("cost_price", "Cost price", "text", {"help": "What you paid for it."}),
    ("bulk_price", "Bulk price", "text", {"help": "Price when selling in quantity."}),
    ("sell_price", "Sell price", "text", {"help": "What a receipt uses. Falls back to "
                                                  "the list price if empty."}),
    ("stock_count", "In stock", "text", {}),
]


class ProductsDialog:
    """Tools → Products. The catalogue you sell from."""

    def __init__(self, parent, on_saved=None):
        self.parent = parent
        self.on_saved = on_saved
        self.catalogue = product_catalogue.load()
        self.mtime = config.file_mtime(product_catalogue.catalogue_path())

        self.win = tk.Toplevel(parent)
        self.win.title("Products")
        self.win.transient(parent)

        frame = ttk.Frame(self.win, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame, foreground="#64748b", justify=tk.LEFT, wraplength=640,
            text="Products you can pick from when adding an item to a receipt.\n"
                 "Stock is recorded but not yet deducted when you sell — that is still "
                 "to come.").pack(anchor=tk.W, pady=(0, 8))

        self.editor = RecordListEditor(frame, "", PRODUCT_COLUMNS,
                                       self.catalogue.get("products", []))
        self.editor.pack(fill=tk.BOTH, expand=True)

        footer = ttk.Frame(self.win, padding=(12, 0, 12, 12))
        footer.pack(fill=tk.X)
        ttk.Label(footer, text=product_catalogue.catalogue_path(),
                  foreground="#64748b").pack(side=tk.LEFT)
        ttk.Button(footer, text="Cancel", command=self.win.destroy).pack(side=tk.RIGHT)
        ttk.Button(footer, text="Save", command=self.save).pack(side=tk.RIGHT, padx=6)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

    def save(self):
        catalogue = dict(self.catalogue)
        # Keep any variants the grid does not show, so editing a product here
        # cannot silently drop the colours or sizes hanging off it.
        existing = {str(p.get("sku", "")).lower(): p
                    for p in self.catalogue.get("products", []) if isinstance(p, dict)}
        products = []
        for record in self.editor.records:
            entry = {k: v for k, v in record.items() if str(v).strip() != ""}
            previous = existing.get(str(record.get("sku", "")).lower())
            if previous and previous.get("variants"):
                entry["variants"] = previous["variants"]
            if previous and previous.get("serial_numbers"):
                entry.setdefault("serial_numbers", previous["serial_numbers"])
            if "stock_count" in entry:
                try:
                    entry["stock_count"] = int(str(entry["stock_count"]).strip())
                except ValueError:
                    pass          # let validate() produce the message
            products.append(entry)
        catalogue["products"] = products

        try:
            product_catalogue.save(catalogue, known_mtime=self.mtime)
        except config.ConfigConflict:
            if messagebox.askyesno(
                "Products changed on disk",
                "products.json was edited outside the app while this window was open.\n\n"
                "Overwrite it with what is shown here?", parent=self.win):
                product_catalogue.save(catalogue)
            else:
                return
        except config.ConfigError as exc:
            messagebox.showerror("That product is not valid", str(exc), parent=self.win)
            return
        except OSError as exc:
            messagebox.showerror("Could not save", str(exc), parent=self.win)
            return

        if self.on_saved:
            self.on_saved()
        self.win.destroy()


def open_products(parent, on_saved=None):
    dialog = ProductsDialog(parent, on_saved)
    parent.wait_window(dialog.win)


class ProductPicker:
    """Choose a product to put on a receipt. Type to search, or scan a barcode."""

    def __init__(self, parent):
        self.chosen = None
        self.catalogue = product_catalogue.load()
        self.items = product_catalogue.sellable_items(self.catalogue)

        self.win = tk.Toplevel(parent)
        self.win.title("Pick a product")
        self.win.transient(parent)

        frame = ttk.Frame(self.win, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(row, text="Search or scan").pack(side=tk.LEFT)
        self.search = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.search, width=36)
        entry.pack(side=tk.LEFT, padx=(8, 0))
        entry.focus_set()
        self.search.trace_add("write", lambda *a: self.refresh())
        # A scanner types the code then sends Enter. If it matches a barcode or
        # SKU exactly, take it straight away -- that is the whole point of a scan.
        entry.bind("<Return>", lambda e: self.accept_scan())

        columns = ("sku", "barcode", "name", "price", "stock")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings",
                                 height=12, selectmode="browse")
        for key, label, width in (("sku", "SKU", 110), ("barcode", "Barcode", 130),
                                  ("name", "Name", 240), ("price", "Price", 100),
                                  ("stock", "In stock", 80)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width,
                             anchor=tk.E if key in ("price", "stock") else tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", lambda e: self.choose())

        self.note = ttk.Label(frame, foreground="#64748b", wraplength=640, justify=tk.LEFT)
        self.note.pack(anchor=tk.W, pady=(8, 0))

        buttons = ttk.Frame(frame)
        buttons.pack(anchor=tk.W, pady=(10, 0))
        ttk.Button(buttons, text="Use this product", command=self.choose).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Cancel", command=self.win.destroy).pack(side=tk.LEFT, padx=6)

        self.refresh()
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

    def refresh(self):
        self.filtered = product_catalogue.search(self.catalogue, self.search.get())
        self.tree.delete(*self.tree.get_children())
        for item in self.filtered:
            name = item.get("name", "")
            if item.get("variant_name"):
                name = f"{name} ({item['variant_name']})" if name else item["variant_name"]
            self.tree.insert("", tk.END, values=(
                item.get("sku", ""), item.get("barcode", ""), name,
                item.get("sell_price") or item.get("list_price", ""),
                item.get("stock_count", "")))
        if not self.items:
            self.note.config(text="No products yet. Add them under Tools → Products.")
        else:
            self.note.config(text=f"{len(self.filtered)} of {len(self.items)} products.")

    def accept_scan(self):
        match = product_catalogue.find(self.catalogue, self.search.get())
        if match:
            self.chosen = match
            self.win.destroy()
        elif len(self.filtered) == 1:
            self.chosen = self.filtered[0]
            self.win.destroy()

    def choose(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Pick a product", "Select a product first.", parent=self.win)
            return
        self.chosen = self.filtered[self.tree.index(selection[0])]
        self.win.destroy()


def pick_product(parent):
    """Show the picker. Returns the chosen catalogue entry, or None."""
    picker = ProductPicker(parent)
    parent.wait_window(picker.win)
    return picker.chosen
