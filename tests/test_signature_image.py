"""A scanned handwritten signature on the receipt — TODO.md §4 (H6).

`ItIsNotADigitalSignature` is the class that matters, and it is mostly about
naming. This feature is a *picture*. It proves nothing, anyone holding one
receipt can lift it, and the thing that actually makes a forged receipt
detectable is the PAdES signature over the document bytes — a completely
separate setting.

The risk here is not a rendering bug, it is somebody reading "signature" in a
config file and believing the receipt is protected. So the key is
`signature_image`, never `signature`, and these tests hold that.

Run: python -m unittest discover -s tests
"""
import base64
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import config              # noqa: E402
import receipt_render      # noqa: E402

import gate_env            # noqa: E402


def setUpModule():
    gate_env.use_gate_env()


def tearDownModule():
    gate_env.restore()


#: The smallest valid PNG, so the test does not need a fixture file.
TINY_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class SignatureTestCase(unittest.TestCase):
    def setUp(self):
        self._app_dir = config.APP_DIR
        self.dir = tempfile.mkdtemp(prefix="rm-sig-")
        shutil.copy(os.path.join(gate_env.GATE_ENV, "appsettings.json"),
                    os.path.join(self.dir, "appsettings.json"))
        config.set_app_dir(self.dir)
        receipt_render.clear_template_cache()

        self.image = os.path.join(self.dir, "signature.png")
        with open(self.image, "wb") as handle:
            handle.write(TINY_PNG)

    def tearDown(self):
        config.set_app_dir(self._app_dir)
        receipt_render.clear_template_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def enable(self, **values):
        settings = {"enabled": True, "path": "signature.png"}
        settings.update(values)
        config.update_app_settings({"signature_image": settings})
        receipt_render.clear_template_cache()

    def body(self):
        """The document body only.

        `styles.css` is embedded in the HTML, so searching the whole document
        for a class name finds the *rule* whether or not anything uses it --
        the mistake PITFALLS.md warns about, made here first time out.
        """
        html = self.render()
        return html.split("<body>", 1)[1]

    def render(self):
        return receipt_render.build_html(
            "INV-W1", "1 Jan 2026", "Ada", "", "",
            [{"sku": "A", "desc": "Thing", "serial": "", "qty": 1, "price": "10",
              "discount": "0", "tax": "0", "warranty": ""}], "Online", 0)


class ItIsNotADigitalSignature(unittest.TestCase):
    """Naming, mostly. The danger is a false sense of security, not a bug."""

    def test_the_setting_is_called_signature_image(self):
        """`signature` would read as the cryptographic one in a config file."""
        settings = config.default_app_settings()
        self.assertIn("signature_image", settings)
        self.assertNotIn("signature", settings)

    def test_the_cryptographic_setting_keeps_its_own_name(self):
        settings = config.default_app_settings()
        self.assertIn("signing", settings)
        self.assertIn("private_key_path", settings["signing"])

    def test_the_two_are_independent(self):
        """Adding a picture must not touch whether receipts are actually signed."""
        settings = config.default_app_settings()
        self.assertTrue(settings["signing"]["enabled"])
        self.assertFalse(settings["signature_image"]["enabled"])

    def test_the_settings_dialog_says_it_is_decorative(self):
        import settings_ui
        rows = [row for _, rows in settings_ui.SETTINGS_SECTIONS for row in rows]
        help_text = next(options.get("help", "")
                         for path, _, _, options in rows
                         if path == "signature_image.enabled")
        self.assertIn("DECORATIVE", help_text)
        self.assertIn("proves nothing", help_text)

    def test_the_readme_spells_out_the_difference(self):
        readme = open(os.path.join(PROJ, "README.md"), encoding="utf-8").read()
        self.assertIn("A scanned signature is not a digital signature", readme)


class OnTheReceipt(SignatureTestCase):
    def test_nothing_prints_while_it_is_off(self):
        self.assertNotIn("signature-block", self.body())

    def test_it_prints_when_switched_on(self):
        self.enable()
        body = self.body()
        self.assertIn("signature-block", body)
        self.assertIn("signature-image", body)

    def test_the_image_is_inlined_rather_than_linked(self):
        """A receipt has to render with no network and no missing-file box."""
        self.enable()
        html = self.render()
        self.assertIn("data:image/png;base64,", html)
        self.assertNotIn('src="signature.png"', html)

    def test_the_caption_prints(self):
        self.enable(label="For and on behalf of Acme")
        self.assertIn("For and on behalf of Acme", self.render())

    def test_no_caption_leaves_no_empty_line(self):
        self.enable(label="")
        self.assertNotIn("signature-label", self.body())

    def test_the_width_is_applied(self):
        self.enable(width_px=240)
        self.assertIn('width="240"', self.render())

    def test_the_caption_is_escaped(self):
        self.enable(label="<script>alert(1)</script>")
        html = self.render()
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_a_missing_file_does_not_stop_the_receipt(self):
        """A decoration must never fail a sale."""
        self.enable(path="not-there.png")
        html = self.render()
        self.assertIn("Thing", html, "the receipt must still render")

    def test_it_sits_after_the_totals(self):
        self.enable()
        body = self.body()
        self.assertLess(body.index("totals-table"), body.index("signature-block"))

    def test_it_is_kept_whole_across_a_page_break(self):
        """A signature split down the middle looks like a printing fault."""
        self.enable()
        html = self.render()
        block = html[html.index(".signature-block"):]
        self.assertIn("break-inside: avoid", block[:200])


class Validation(unittest.TestCase):
    def settings(self, **values):
        settings = config.default_app_settings()
        settings["signature_image"].update(values)
        return settings

    def test_the_defaults_are_valid(self):
        config.validate(config.default_app_settings(), "appsettings.json")

    def test_the_section_must_be_an_object(self):
        settings = config.default_app_settings()
        settings["signature_image"] = "yes"
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(settings, "appsettings.json")
        self.assertEqual(ctx.exception.key, "signature_image")

    def test_enabled_must_be_a_boolean(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(self.settings(enabled="yes"), "appsettings.json")
        self.assertEqual(ctx.exception.key, "signature_image.enabled")

    def test_the_path_must_be_text(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(self.settings(path=42), "appsettings.json")
        self.assertEqual(ctx.exception.key, "signature_image.path")

    def test_the_width_must_be_a_positive_number(self):
        for bad in (0, -10, "wide", True):
            with self.subTest(width=bad):
                with self.assertRaises(config.ConfigError) as ctx:
                    config.validate(self.settings(width_px=bad), "appsettings.json")
                self.assertEqual(ctx.exception.key, "signature_image.width_px")

    def test_switching_it_on_without_a_file_is_refused(self):
        """Otherwise it silently does nothing and looks broken."""
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(self.settings(enabled=True, path=""), "appsettings.json")
        self.assertEqual(ctx.exception.key, "signature_image.path")

    def test_a_path_with_it_switched_off_is_fine(self):
        config.validate(self.settings(enabled=False, path=""), "appsettings.json")


if __name__ == "__main__":
    unittest.main()
