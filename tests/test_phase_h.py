"""Phase H — user-requested work (see claude_chat/TASKS.md).

H1: a configured-but-missing logo must say so instead of vanishing.
H4: the "open containing folder" question can be answered once and remembered.

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

import config             # noqa: E402
import receipt_render     # noqa: E402

# A one-pixel PNG; enough to be a real image file on disk.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc000000301010018dd8db00000000049454e"
    "44ae426082")


class TempApp(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-phaseh-")
        shutil.copy(os.path.join(PROJ, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        receipt_render.clear_template_cache()

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_image(self, name):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(PNG_BYTES)
        return path

    def settings(self):
        with open(os.path.join(self.dir, "appsettings.json"), encoding="utf-8") as f:
            return json.load(f)


class MissingLogoIsReported(TempApp):
    """The logo used to disappear in silence, leaving no way to find out why."""

    def test_a_present_logo_reports_no_problem(self):
        self.write_image("logo.png")
        self.assertEqual(receipt_render.asset_problem("logo.png"), "")

    def test_no_logo_configured_is_not_a_problem(self):
        self.assertEqual(receipt_render.asset_problem(""), "")

    def test_a_missing_logo_is_explained(self):
        problem = receipt_render.asset_problem("logo.png")
        self.assertIn("not found", problem)
        self.assertIn("logo.png", problem)
        self.assertIn(self.dir, problem, "the message must say where it looked")

    def test_the_doubled_extension_is_named_as_the_likely_cause(self):
        """Exactly the failure the user hit: Windows hides known extensions."""
        self.write_image("logo.png.png")
        problem = receipt_render.asset_problem("logo.png")
        self.assertIn("logo.png.png", problem)
        self.assertIn("hides known file extensions", problem)

    def test_a_wrong_extension_is_suggested_too(self):
        self.write_image("logo.jpg")
        self.assertIn("logo.jpg", receipt_render.asset_problem("logo.png"))

    def test_suggestions_ignore_unrelated_files(self):
        self.write_image("banner.png")
        self.assertEqual(receipt_render.suggest_asset_alternatives("logo.png"), [])

    def test_render_still_succeeds_without_the_logo(self):
        """A receipt without its logo is still a valid receipt."""
        header = receipt_render.build_page_header_template()
        self.assertNotIn("<img", header)
        self.assertIn("Your Company", header)

    def test_a_present_logo_is_embedded(self):
        self.write_image("logo.png")
        header = receipt_render.build_page_header_template()
        self.assertIn("<img", header)
        self.assertIn("base64,", header, "the image must be inlined, not linked")

    def test_fail_on_missing_image_turns_it_into_an_error(self):
        """The config knob existed from Stage 2 but was never implemented."""
        config.update_app_settings({"render": {"fail_on_missing_image": True}})
        with self.assertRaises(RuntimeError) as ctx:
            receipt_render.build_page_header_template()
        self.assertIn("not found", str(ctx.exception))

    def test_check_reports_the_problem(self):
        import cli
        self.write_image("logo.png.png")
        code = cli.run_check()
        self.assertEqual(code, cli.EXIT_OK, "a missing logo is a warning, not a failure")


class FooterPolicyLinks(TempApp):
    """The footer links to the policy pages, and never to nowhere."""

    def footer_body(self):
        import receipt_render
        receipt_render.clear_template_cache()
        return receipt_render.build_page_footer_template().split("</style>")[1]

    def test_the_old_signature_notice_is_gone(self):
        self.assertNotIn("digitally signed", self.footer_body())
        self.assertNotIn("chawlatech", self.footer_body().lower())

    def test_no_urls_means_no_anchors_but_the_words_remain(self):
        body = self.footer_body()
        self.assertNotIn("<a href", body, "an empty href is a link to nowhere")
        self.assertIn("Terms of Service", body, "the wording must still print")
        self.assertIn("Privacy Policy", body)
        self.assertIn("Warranty Policy", body)

    def test_configured_urls_become_links(self):
        import re
        config.update_app_settings({"links": {
            "terms_url": "https://example.com/terms",
            "privacy_url": "https://example.com/privacy",
            "warranty_url": "https://example.com/warranty"}})
        found = dict((t, h) for h, t in
                     re.findall(r'<a href="([^"]*)">([^<]*)</a>', self.footer_body()))
        self.assertEqual(found["Terms of Service"], "https://example.com/terms")
        self.assertEqual(found["Privacy Policy"], "https://example.com/privacy")
        self.assertEqual(found["Warranty Policy"], "https://example.com/warranty")

    def test_one_configured_link_does_not_force_the_others(self):
        config.update_app_settings({"links": {"terms_url": "https://example.com/terms"}})
        body = self.footer_body()
        self.assertIn('<a href="https://example.com/terms">Terms of Service</a>', body)
        self.assertIn("Privacy Policy", body)
        self.assertNotIn('href=""', body)

    def test_an_unsafe_scheme_is_refused(self):
        """An href is a code context; escaping cannot make javascript: safe."""
        with self.assertRaises(config.ConfigError) as ctx:
            config.update_app_settings({"links": {"terms_url": "javascript:alert(1)"}})
        self.assertEqual(ctx.exception.key, "links.terms_url")

    def test_mailto_is_allowed(self):
        config.update_app_settings({"links": {"terms_url": "mailto:legal@example.com"}})
        self.assertIn("mailto:legal@example.com", self.footer_body())

    def test_safe_url_helper(self):
        import receipt_render
        self.assertEqual(receipt_render.safe_url("https://x.test"), "https://x.test")
        self.assertEqual(receipt_render.safe_url("javascript:alert(1)"), "")
        self.assertEqual(receipt_render.safe_url("file:///etc/passwd"), "")
        self.assertEqual(receipt_render.safe_url(""), "")

    def test_ampersands_in_a_url_are_escaped_for_the_attribute(self):
        config.update_app_settings(
            {"links": {"terms_url": "https://example.com/t?a=1&b=2"}})
        self.assertIn("a=1&amp;b=2", self.footer_body())


class RememberedAnswers(TempApp):
    """H4: answer 'open the folder?' once and have it stick."""

    def test_default_is_to_keep_asking(self):
        ui = config.load_app_settings()["ui"]
        self.assertTrue(ui["ask_open_folder"])
        self.assertFalse(ui["open_folder_after_generate"])

    def test_remembering_yes_is_persisted(self):
        config.update_app_settings(
            {"ui": {"ask_open_folder": False, "open_folder_after_generate": True}})
        saved = self.settings()["ui"]
        self.assertFalse(saved["ask_open_folder"])
        self.assertTrue(saved["open_folder_after_generate"])

    def test_the_flags_are_validated(self):
        settings = config.default_app_settings()
        settings["ui"]["ask_open_folder"] = "yes"
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(settings, "appsettings.json")
        self.assertEqual(ctx.exception.key, "ui.ask_open_folder")

    def test_no_prompt_once_the_answer_is_remembered(self):
        """Guards the hang this change first caused: _on_generated must not open
        a dialog when the user has said 'stop asking'."""
        import tkinter as tk
        import main

        config.update_app_settings(
            {"ui": {"ask_open_folder": False, "open_folder_after_generate": False}})

        asked, opened = [], []
        original_ask, original_open = main.ask_with_memory, main.ReceiptApp._open_folder
        root = tk.Tk()
        root.withdraw()
        try:
            main.ask_with_memory = lambda *a, **k: asked.append(1) or (False, False)
            main.ReceiptApp._open_folder = staticmethod(lambda path: opened.append(path))
            app = main.ReceiptApp(root)
            app._on_generated(os.path.join(self.dir, "invoices", "x.pdf"), True)
        finally:
            main.ask_with_memory = original_ask
            main.ReceiptApp._open_folder = original_open
            root.destroy()

        self.assertEqual(asked, [], "it should not ask once the answer is remembered")
        self.assertEqual(opened, [], "and it should honour 'no'")

    def test_remembered_yes_opens_without_asking(self):
        import tkinter as tk
        import main

        config.update_app_settings(
            {"ui": {"ask_open_folder": False, "open_folder_after_generate": True}})

        asked, opened = [], []
        original_ask, original_open = main.ask_with_memory, main.ReceiptApp._open_folder
        root = tk.Tk()
        root.withdraw()
        try:
            main.ask_with_memory = lambda *a, **k: asked.append(1) or (False, False)
            main.ReceiptApp._open_folder = staticmethod(lambda path: opened.append(path))
            app = main.ReceiptApp(root)
            app._on_generated(os.path.join(self.dir, "invoices", "x.pdf"), True)
        finally:
            main.ask_with_memory = original_ask
            main.ReceiptApp._open_folder = original_open
            root.destroy()

        self.assertEqual(asked, [])
        self.assertEqual(len(opened), 1, "a remembered yes should open the folder")


class SafeSettingsUpdates(TempApp):
    """update_app_settings is what every in-app editor will save through."""

    def test_a_nested_change_leaves_the_rest_alone(self):
        before = self.settings()
        config.update_app_settings({"company": {"phone": "555-0100"}})
        after = self.settings()
        self.assertEqual(after["company"]["phone"], "555-0100")
        self.assertEqual(after["company"]["name"], before["company"]["name"])
        self.assertEqual(after["currency"], before["currency"])

    def test_a_backup_is_kept(self):
        config.update_app_settings({"company": {"phone": "555-0100"}})
        backups = [n for n in os.listdir(self.dir) if n.endswith(".bak")]
        self.assertEqual(len(backups), 1)

    def test_an_invalid_change_is_refused_before_writing(self):
        """The app must never save a config it would then refuse to load."""
        before = self.settings()
        with self.assertRaises(config.ConfigError):
            config.update_app_settings({"currency": {"decimals": 99}})
        self.assertEqual(self.settings(), before, "the file must be untouched")

    def test_a_concurrent_hand_edit_is_detected(self):
        """The caller passes the mtime it *read* at; a newer file must not be clobbered.

        The earlier version of this test mocked getmtime and so passed against an
        implementation that captured the mtime at save time -- when the file had
        already been edited, making the check useless. Drive the real file
        instead.
        """
        path = os.path.join(self.dir, "appsettings.json")
        read_mtime = config.file_mtime(path)

        # Someone edits the file by hand after we read it.
        with open(path, "r", encoding="utf-8") as f:
            edited = json.load(f)
        edited["company"]["name"] = "Edited By Hand"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(edited, f)
        os.utime(path, (read_mtime + 5, read_mtime + 5))

        with self.assertRaises(config.ConfigConflict):
            config.update_app_settings({"company": {"phone": "555-0100"}},
                                       known_mtime=read_mtime)
        self.assertEqual(self.settings()["company"]["name"], "Edited By Hand",
                         "the hand edit must survive")

    def test_omitting_the_mtime_skips_the_check(self):
        """Documented escape hatch for a read-modify-write that happens in one go."""
        config.update_app_settings({"company": {"phone": "555-0100"}})
        self.assertEqual(self.settings()["company"]["phone"], "555-0100")

    def test_schema_version_is_kept_current(self):
        config.update_app_settings({"company": {"phone": "555-0100"}})
        self.assertEqual(self.settings()[config.SCHEMA_VERSION_KEY], config.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
