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


if __name__ == "__main__":
    unittest.main()
