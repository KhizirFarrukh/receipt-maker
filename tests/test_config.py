"""Stage 2 — config unit tests.

Covers PLAN-generalization.md §"Testing & gates" for config: deep-merge fills
only missing keys, migrate v1->v2, the downgrade guard, a .bak on every rewrite,
each validate() case raising the right ConfigError, atomic writes leaving no
partial file, and mtime-conflict detection.

Every test works on a temp file; none touch the real appsettings.json.

Run: python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config  # noqa: E402


def valid_settings(**overrides):
    settings = config.default_app_settings()
    for dotted, value in overrides.items():
        section, _, key = dotted.partition("__")
        if key:
            settings[section][key] = value
        else:
            settings[section] = value
    return settings


class TempConfig(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rm-config-")
        self.path = os.path.join(self.dir, "appsettings.json")

    def write(self, data):
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
        return self.path

    def read(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def backups(self):
        return [n for n in os.listdir(self.dir) if n.endswith(".bak")]


class DeepMerge(unittest.TestCase):
    def test_fills_only_missing_keys(self):
        defaults = {"a": 1, "b": {"c": 2, "d": 3}}
        merged = config.deep_merge(defaults, {"b": {"c": 99}})
        self.assertEqual(merged, {"a": 1, "b": {"c": 99, "d": 3}})

    def test_user_value_always_wins(self):
        merged = config.deep_merge({"a": 1}, {"a": 0})
        self.assertEqual(merged["a"], 0, "a falsey user value must not be replaced")

    def test_unknown_user_keys_are_preserved(self):
        merged = config.deep_merge({"a": 1}, {"a": 1, "mine": "keep"})
        self.assertEqual(merged["mine"], "keep")

    def test_does_not_mutate_defaults(self):
        defaults = {"b": {"c": 1}}
        config.deep_merge(defaults, {"b": {"c": 2}})
        self.assertEqual(defaults, {"b": {"c": 1}})

    def test_non_dict_override_for_dict_key_falls_back_to_defaults(self):
        merged = config.deep_merge({"b": {"c": 1}}, {"b": "oops"})
        self.assertEqual(merged["b"], {"c": 1})


class Migrate(unittest.TestCase):
    V1 = {
        "company": {"name": "Acme", "address": "1 Road", "phone": "1",
                    "email": "a@b.c", "logo_path": "logo.png"},
        "signing": {"enabled": True, "private_key_path": "signing/private_key.pem",
                    "certificate_path": "signing/certificate.pem",
                    "key_passphrase": "", "signer_name": "Acme",
                    "reason": "Receipt authenticity", "location": "acme.example",
                    "tsa_url": ""},
    }

    def test_v1_is_detected_and_stamped(self):
        settings, changed = config.migrate(dict(self.V1))
        self.assertTrue(changed)
        self.assertEqual(settings[config.SCHEMA_VERSION_KEY], config.SCHEMA_VERSION)

    def test_v1_values_carry_over_verbatim(self):
        settings, _ = config.migrate(dict(self.V1))
        self.assertEqual(settings["company"], self.V1["company"])
        self.assertEqual(settings["signing"], self.V1["signing"])

    def test_v1_gains_the_new_sections(self):
        settings, _ = config.migrate(dict(self.V1))
        self.assertIn("document", settings)
        self.assertIn("render", settings)
        self.assertEqual(settings["document"]["margin_top"], config.PDF_MARGIN_TOP)

    def test_current_version_is_a_no_op(self):
        current = config.default_app_settings()
        settings, changed = config.migrate(current)
        self.assertFalse(changed, "a current-schema config must not be rewritten")
        self.assertEqual(settings, current)

    def test_migration_is_idempotent(self):
        once, _ = config.migrate(dict(self.V1))
        twice, changed = config.migrate(once)
        self.assertFalse(changed)
        self.assertEqual(once, twice)

    def test_downgrade_guard(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.migrate({config.SCHEMA_VERSION_KEY: config.SCHEMA_VERSION + 1})
        self.assertIn("newer version", ctx.exception.message)
        self.assertEqual(ctx.exception.key, config.SCHEMA_VERSION_KEY)

    def test_non_integer_version_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.migrate({config.SCHEMA_VERSION_KEY: "2"})

    def test_top_level_must_be_an_object(self):
        with self.assertRaises(config.ConfigError):
            config.migrate([1, 2, 3])


class Validate(unittest.TestCase):
    def assert_rejects(self, key, settings):
        with self.assertRaises(config.ConfigError, msg=f"{key} should have been rejected") as ctx:
            config.validate(settings, "appsettings.json")
        self.assertEqual(ctx.exception.key, key)
        return ctx.exception

    def test_defaults_are_valid(self):
        config.validate(config.default_app_settings(), "appsettings.json")

    def test_company_must_be_object(self):
        self.assert_rejects("company", valid_settings(company="nope"))

    def test_company_name_required(self):
        self.assert_rejects("company.name", valid_settings(company__name="   "))

    def test_company_field_must_be_text(self):
        self.assert_rejects("company.phone", valid_settings(company__phone=12345))

    def test_signing_enabled_must_be_boolean(self):
        self.assert_rejects("signing.enabled", valid_settings(signing__enabled="yes"))

    def test_enabled_signing_requires_key_path(self):
        self.assert_rejects("signing.private_key_path",
                            valid_settings(signing__private_key_path=""))

    def test_disabled_signing_allows_empty_paths(self):
        settings = valid_settings(signing__enabled=False, signing__private_key_path="")
        config.validate(settings, "appsettings.json")

    def test_tsa_url_must_be_http(self):
        self.assert_rejects("signing.tsa_url", valid_settings(signing__tsa_url="ftp://x"))

    def test_tsa_url_may_be_empty(self):
        config.validate(valid_settings(signing__tsa_url=""), "appsettings.json")

    def test_margin_needs_units(self):
        err = self.assert_rejects("document.margin_top",
                                  valid_settings(document__margin_top="150"))
        self.assertIn("CSS length", err.message)

    def test_margin_rejects_negative(self):
        self.assert_rejects("document.margin_left", valid_settings(document__margin_left="-5px"))

    def test_margin_allows_bare_zero(self):
        config.validate(valid_settings(document__margin_left="0"), "appsettings.json")

    def test_margin_must_not_be_empty(self):
        self.assert_rejects("document.margin_bottom", valid_settings(document__margin_bottom=""))

    def test_timeout_must_be_positive_int(self):
        self.assert_rejects("render.timeout_ms", valid_settings(render__timeout_ms=0))
        self.assert_rejects("render.timeout_ms", valid_settings(render__timeout_ms="fast"))

    def test_block_external_requests_must_be_boolean(self):
        self.assert_rejects("render.block_external_requests",
                            valid_settings(render__block_external_requests="yes"))

    def test_error_message_names_file_and_key(self):
        err = self.assert_rejects("company.name", valid_settings(company__name=""))
        self.assertIn("appsettings.json", str(err))
        self.assertIn("company.name", str(err))


class EveryValidationCase(unittest.TestCase):
    """One test per rejection `validate()` can produce.

    The plan asks for "every validate case raises the right ConfigError". These
    are the cases that were still unexercised: each names the key it guards, so
    a message that stops naming the right key fails here rather than confusing
    someone at a till.
    """

    def reject(self, key, mutate):
        settings = config.default_app_settings()
        mutate(settings)
        with self.assertRaises(config.ConfigError,
                               msg=f"{key} should have been rejected") as ctx:
            config.validate(settings, "appsettings.json")
        self.assertEqual(ctx.exception.key, key)
        return ctx.exception

    def accept(self, mutate):
        settings = config.default_app_settings()
        mutate(settings)
        return config.validate(settings, "appsettings.json")

    # -- signing ---------------------------------------------------------
    def test_signing_must_be_an_object(self):
        self.reject("signing", lambda s: s.update(signing="nope"))

    def test_each_signing_field_must_be_text(self):
        for key in ("private_key_path", "certificate_path", "key_passphrase",
                    "signer_name", "reason", "location", "tsa_url"):
            self.reject(f"signing.{key}",
                        lambda s, k=key: s["signing"].update({k: 123}))

    def test_an_enabled_signing_needs_a_certificate_path(self):
        self.reject("signing.certificate_path",
                    lambda s: s["signing"].update(certificate_path="  "))

    def test_an_https_tsa_url_is_accepted(self):
        self.accept(lambda s: s["signing"].update(tsa_url="https://tsa.example"))

    # -- company ---------------------------------------------------------
    def test_every_company_field_must_be_text(self):
        for key in ("name", "address", "phone", "email", "logo_path"):
            self.reject(f"company.{key}",
                        lambda s, k=key: s["company"].update({k: []}))

    # -- currency --------------------------------------------------------
    def test_currency_must_be_an_object(self):
        self.reject("currency", lambda s: s.update(currency=[]))

    def test_currency_symbol_and_code_must_be_text(self):
        for key in ("symbol", "code"):
            self.reject(f"currency.{key}",
                        lambda s, k=key: s["currency"].update({k: 5}))

    def test_currency_flags_must_be_boolean(self):
        for key in ("symbol_space", "group_line_amounts"):
            self.reject(f"currency.{key}",
                        lambda s, k=key: s["currency"].update({k: "yes"}))

    def test_decimals_must_not_be_negative(self):
        self.reject("currency.decimals", lambda s: s["currency"].update(decimals=-1))

    def test_decimals_must_be_a_whole_number(self):
        self.reject("currency.decimals", lambda s: s["currency"].update(decimals=2.5))

    def test_decimals_of_zero_and_six_are_the_boundaries(self):
        self.accept(lambda s: s["currency"].update(decimals=0))
        self.accept(lambda s: s["currency"].update(decimals=6))
        self.reject("currency.decimals", lambda s: s["currency"].update(decimals=7))

    def test_symbol_position_must_be_known(self):
        self.reject("currency.position", lambda s: s["currency"].update(position="middle"))

    # -- document --------------------------------------------------------
    def test_document_must_be_an_object(self):
        self.reject("document", lambda s: s.update(document="nope"))

    def test_every_margin_is_checked(self):
        for key in ("margin_top", "margin_bottom", "margin_left", "margin_right"):
            self.reject(f"document.{key}",
                        lambda s, k=key: s["document"].update({k: "wide"}))

    def test_each_css_unit_is_accepted(self):
        for unit in ("px", "mm", "cm", "in", "pt", "pc"):
            self.accept(lambda s, u=unit: s["document"].update(margin_top=f"10{u}"))

    # -- render ----------------------------------------------------------
    def test_render_must_be_an_object(self):
        self.reject("render", lambda s: s.update(render=None))

    def test_a_negative_timeout_is_refused(self):
        self.reject("render.timeout_ms", lambda s: s["render"].update(timeout_ms=-1))

    def test_a_boolean_timeout_is_refused(self):
        """True is an int in Python; it is not a timeout."""
        self.reject("render.timeout_ms", lambda s: s["render"].update(timeout_ms=True))

    # -- fonts -----------------------------------------------------------
    def test_fonts_must_be_an_object(self):
        self.reject("fonts", lambda s: s.update(fonts="Inter"))

    def test_font_names_must_be_text(self):
        for key in ("family", "fallback"):
            self.reject(f"fonts.{key}", lambda s, k=key: s["fonts"].update({k: 1}))

    def test_font_files_must_be_a_list_of_paths(self):
        self.reject("fonts.files", lambda s: s["fonts"].update(files="one.woff2"))
        self.reject("fonts.files", lambda s: s["fonts"].update(files=[1, 2]))

    def test_a_family_without_files_is_refused(self):
        """A family that can never load would silently do nothing."""
        self.reject("fonts.files", lambda s: s["fonts"].update(family="Inter", files=[]))

    # -- links -----------------------------------------------------------
    def test_links_must_be_an_object(self):
        self.reject("links", lambda s: s.update(links=[]))

    def test_link_values_must_be_text(self):
        self.reject("links.terms_url", lambda s: s["links"].update(terms_url=1))

    def test_each_safe_scheme_is_accepted(self):
        for url in ("http://x.test", "https://x.test", "mailto:a@b.c"):
            self.accept(lambda s, u=url: s["links"].update(terms_url=u))

    def test_unsafe_schemes_are_refused(self):
        for url in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,x"):
            self.reject("links.terms_url",
                        lambda s, u=url: s["links"].update(terms_url=u))

    # -- ui / terms / invoice -------------------------------------------
    def test_ui_must_be_an_object(self):
        self.reject("ui", lambda s: s.update(ui="yes"))

    def test_terms_page_must_be_an_object(self):
        self.reject("terms_page", lambda s: s.update(terms_page=True))

    def test_invoice_must_be_an_object(self):
        self.reject("invoice", lambda s: s.update(invoice="INV-"))

    def test_invoice_prefix_must_be_text(self):
        self.reject("invoice.prefix", lambda s: s["invoice"].update(prefix=1))

    def test_a_prefix_with_a_path_separator_is_refused(self):
        for bad in ("INV/", "INV\\", "INV:", "INV*"):
            self.reject("invoice.prefix",
                        lambda s, b=bad: s["invoice"].update(prefix=b))

    def test_an_empty_prefix_is_allowed(self):
        self.accept(lambda s: s["invoice"].update(prefix=""))

    # -- tax -------------------------------------------------------------
    def test_tax_rows_must_be_a_list(self):
        self.reject("tax.rows", lambda s: s["tax"].update(rows={}))

    def test_a_tax_row_must_be_an_object(self):
        self.reject("tax.rows[0]", lambda s: s["tax"].update(rows=["15%"]))

    def test_a_tax_value_must_be_a_number(self):
        self.reject("tax.rows[0].value", lambda s: s["tax"].update(
            rows=[{"label": "VAT", "type": "percent", "value": []}]))

    def test_a_non_numeric_tax_string_is_refused(self):
        self.reject("tax.rows[0].value", lambda s: s["tax"].update(
            rows=[{"label": "VAT", "type": "percent", "value": "lots"}]))

    def test_a_fixed_tax_row_may_exceed_one_hundred(self):
        """Only percentages are capped; a fixed levy can be any amount."""
        self.accept(lambda s: s["tax"].update(
            rows=[{"label": "Levy", "type": "fixed", "value": 500}]))

    # -- the remaining whole-section guards ------------------------------
    def test_tax_must_be_an_object(self):
        """Reached only when the whole section is the wrong shape."""
        self.reject("tax", lambda s: s.update(tax=[]))

    def test_inventory_must_be_an_object(self):
        self.reject("inventory", lambda s: s.update(inventory="on"))

    def test_a_tax_row_needs_a_label(self):
        """An unlabelled row would print a blank line on the receipt."""
        self.reject("tax.rows[0].label", lambda s: s["tax"].update(
            rows=[{"label": "  ", "type": "percent", "value": 5}]))

    def test_a_tax_row_type_must_be_known(self):
        self.reject("tax.rows[0].type", lambda s: s["tax"].update(
            rows=[{"label": "VAT", "type": "compound", "value": 5}]))

    def test_a_date_format_that_formats_to_nothing_is_refused(self):
        """`strftime` accepts "%t", but it renders a tab -- a blank date."""
        error = self.reject("date_format", lambda s: s.update(date_format="%t"))
        self.assertIn("usable strftime", str(error))

    def test_an_unknown_strftime_directive_is_refused(self):
        """Caught here rather than blowing up mid-receipt at generation time."""
        error = self.reject("date_format", lambda s: s.update(date_format="%Q"))
        self.assertIn("usable strftime", str(error))

    # -- receipt types ---------------------------------------------------
    def test_receipt_types_must_be_a_list(self):
        self.reject("receipt_types", lambda s: s.update(receipt_types={}))

    def test_a_receipt_type_must_be_an_object(self):
        self.reject("receipt_types[0]", lambda s: s.update(receipt_types=["Online"]))

    def test_a_receipt_type_needs_a_label(self):
        self.reject("receipt_types[0]",
                    lambda s: s.update(receipt_types=[{"code": "W"}]))

    def test_a_receipt_type_needs_a_code(self):
        self.reject("receipt_types[0].code",
                    lambda s: s.update(receipt_types=[{"label": "Online"}]))


class AtomicWrites(TempConfig):
    def test_writes_and_reads_back(self):
        config.atomic_write_json(self.path, {"a": 1})
        self.assertEqual(self.read(), {"a": 1})

    def test_keeps_a_backup(self):
        self.write({"a": 1})
        config.atomic_write_json(self.path, {"a": 2})
        backups = self.backups()
        self.assertEqual(len(backups), 1, f"expected exactly one .bak, got {backups}")
        with open(os.path.join(self.dir, backups[0]), "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 1}, "backup must hold the OLD content")

    def test_no_backup_when_asked(self):
        self.write({"a": 1})
        config.atomic_write_json(self.path, {"a": 2}, keep_backup=False)
        self.assertEqual(self.backups(), [])

    def test_leaves_no_temp_file(self):
        config.atomic_write_json(self.path, {"a": 1})
        leftovers = [n for n in os.listdir(self.dir) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_mtime_conflict_detected(self):
        self.write({"a": 1})
        stale = os.path.getmtime(self.path) - 10   # pretend we read it earlier
        with self.assertRaises(config.ConfigConflict):
            config.atomic_write_json(self.path, {"a": 2}, expected_mtime=stale)
        self.assertEqual(self.read(), {"a": 1}, "conflicting write must not clobber")

    def test_matching_mtime_allows_write(self):
        self.write({"a": 1})
        config.atomic_write_json(self.path, {"a": 2}, expected_mtime=os.path.getmtime(self.path))
        self.assertEqual(self.read(), {"a": 2})

    def test_written_json_uses_lf_and_trailing_newline(self):
        config.atomic_write_json(self.path, {"a": 1})
        with open(self.path, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"\r\n", raw, "config must be written with LF")
        self.assertTrue(raw.endswith(b"\n"))


class LoadAppSettings(TempConfig):
    def test_missing_file_is_created_with_defaults(self):
        settings = config.load_app_settings(self.path)
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(settings["company"]["name"],
                         config.DEFAULT_APP_SETTINGS["company"]["name"])

    def test_v1_file_is_migrated_in_place_with_a_backup(self):
        self.write(Migrate.V1)
        settings = config.load_app_settings(self.path)
        self.assertEqual(settings[config.SCHEMA_VERSION_KEY], config.SCHEMA_VERSION)
        self.assertEqual(self.read()[config.SCHEMA_VERSION_KEY], config.SCHEMA_VERSION,
                         "migration must be persisted, not just in memory")
        self.assertEqual(len(self.backups()), 1, "migration must leave a .bak")

    def test_migration_preserves_the_users_values(self):
        self.write(Migrate.V1)
        settings = config.load_app_settings(self.path)
        self.assertEqual(settings["company"]["name"], "Acme")
        self.assertEqual(settings["signing"]["signer_name"], "Acme")

    def test_second_load_does_not_rewrite(self):
        self.write(Migrate.V1)
        config.load_app_settings(self.path)
        before = os.path.getmtime(self.path)
        config.load_app_settings(self.path)
        self.assertEqual(os.path.getmtime(self.path), before,
                         "a migrated file must not be rewritten on every launch")
        self.assertEqual(len(self.backups()), 1, "no extra .bak on an unchanged load")

    def test_unreadable_json_falls_back_to_defaults_without_touching_the_file(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ not json")
        settings = config.load_app_settings(self.path)
        self.assertEqual(settings["company"]["name"],
                         config.DEFAULT_APP_SETTINGS["company"]["name"])
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ not json",
                             "a broken file must be left alone for the user to fix")

    def test_invalid_config_raises_with_file_and_key(self):
        bad = config.default_app_settings()
        bad["company"]["name"] = ""
        self.write(bad)
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_app_settings(self.path)
        self.assertEqual(ctx.exception.key, "company.name")

    def test_newer_schema_refuses_to_load(self):
        future = config.default_app_settings()
        future[config.SCHEMA_VERSION_KEY] = config.SCHEMA_VERSION + 5
        self.write(future)
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_app_settings(self.path)
        self.assertIn("newer version", ctx.exception.message)

    def test_strings_are_trimmed(self):
        settings = config.default_app_settings()
        settings["company"]["name"] = "  Spaced  "
        self.write(settings)
        self.assertEqual(config.load_app_settings(self.path)["company"]["name"], "Spaced")


class FilenameFields(TempConfig):
    def test_missing_file_created_with_defaults(self):
        path = os.path.join(self.dir, "filename_config.json")
        self.assertEqual(config.load_filename_fields(path), config.DEFAULT_FILENAME_FIELDS)
        self.assertTrue(os.path.exists(path))

    def test_unknown_fields_dropped_and_order_kept(self):
        path = os.path.join(self.dir, "filename_config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"filename_fields": ["phone", "bogus", "date", "phone"]}, f)
        self.assertEqual(config.load_filename_fields(path), ["phone", "date"])

    def test_returned_list_is_not_the_shared_default(self):
        path = os.path.join(self.dir, "filename_config.json")
        got = config.load_filename_fields(path)
        got.append("email")
        self.assertEqual(config.DEFAULT_FILENAME_FIELDS, ["date", "name"],
                         "callers must not be able to mutate the module default")



class EveryFieldsValidationCase(unittest.TestCase):
    """The `fields.json` guards, which `validate()` above never reaches."""

    def reject(self, key, mutate):
        fields = config.default_fields()
        mutate(fields)
        with self.assertRaises(config.ConfigError,
                               msg=f"{key} should have been rejected") as ctx:
            config.validate_fields(fields, "fields.json")
        self.assertEqual(ctx.exception.key, key)
        return ctx.exception

    def test_a_field_must_be_an_object(self):
        self.reject("receipt_fields[0]",
                    lambda f: f.update(receipt_fields=["customer_name"]))

    def test_warranty_must_be_an_object(self):
        self.reject("warranty", lambda f: f.update(warranty=[]))

    def test_warranty_enabled_must_be_a_boolean(self):
        self.reject("warranty.enabled",
                    lambda f: f["warranty"].update(enabled="yes"))

    def test_an_enabled_warranty_needs_options(self):
        """Enabled with nothing to choose would give an empty dropdown."""
        self.reject("warranty.options",
                    lambda f: f["warranty"].update(enabled=True, options=[]))

    def test_a_warranty_of_only_blanks_counts_as_empty(self):
        self.reject("warranty.options",
                    lambda f: f["warranty"].update(enabled=True, options=["", "  "]))

    def test_a_warranty_option_must_be_text(self):
        self.reject("warranty.options[1]",
                    lambda f: f["warranty"].update(options=["1 Year", 12]))

    def test_a_warranty_option_takes_at_most_one_placeholder(self):
        """Two '#' marks would make the period ambiguous to substitute into."""
        error = self.reject("warranty.options[0]",
                            lambda f: f["warranty"].update(options=["# of # Months"]))
        self.assertIn("at most one", str(error))

    def test_a_disabled_warranty_needs_no_options(self):
        fields = config.default_fields()
        fields["warranty"] = {"enabled": False, "options": []}
        config.validate_fields(fields, "fields.json")

if __name__ == "__main__":
    unittest.main()
