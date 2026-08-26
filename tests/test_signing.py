"""H6 / Stage 6 — bring-your-own signing key, and key rotation.

The load-bearing guarantee here is **rotation must not invalidate history**. A
receipt was genuine when it was issued; replacing the signing key cannot
retroactively make the copy in a customer's hand look like a forgery. Everything
else in this file exists so that guarantee cannot be quietly lost.

Signatures are exercised against a ~600-byte blank PDF built by pyhanko rather
than a rendered receipt, so the whole file runs in seconds and needs no browser.

Run: python -m unittest discover -s tests
"""
import io
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import receipt_signing  # noqa: E402


def blank_pdf(path):
    """The smallest thing pyhanko will sign. Enough to test signatures with."""
    from pyhanko.pdf_utils.writer import PdfFileWriter
    from pyhanko.pdf_utils import generic

    writer = PdfFileWriter()
    writer.insert_page(generic.DictionaryObject({
        generic.pdf_name("/Type"): generic.pdf_name("/Page"),
        generic.pdf_name("/MediaBox"): generic.ArrayObject(
            [generic.NumberObject(0), generic.NumberObject(0),
             generic.NumberObject(200), generic.NumberObject(200)]),
    }))
    buffer = io.BytesIO()
    writer.write(buffer)
    with open(path, "wb") as f:
        f.write(buffer.getvalue())
    return path


class SigningTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rm-signing-")
        self.key = os.path.join(self.dir, "private_key.pem")
        self.cert = os.path.join(self.dir, "certificate.pem")
        self._saved = (receipt_signing.SIGNING_DIR, receipt_signing.KNOWN_CERTS_DIR,
                       receipt_signing.DEFAULT_KEY_PATH, receipt_signing.DEFAULT_CERT_PATH)
        receipt_signing.SIGNING_DIR = self.dir
        receipt_signing.KNOWN_CERTS_DIR = os.path.join(self.dir, "previous_certificates")
        receipt_signing.DEFAULT_KEY_PATH = self.key
        receipt_signing.DEFAULT_CERT_PATH = self.cert

    def tearDown(self):
        (receipt_signing.SIGNING_DIR, receipt_signing.KNOWN_CERTS_DIR,
         receipt_signing.DEFAULT_KEY_PATH, receipt_signing.DEFAULT_CERT_PATH) = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def new_key(self, name="k", org="Acme Ltd", passphrase=None):
        key = os.path.join(self.dir, f"{name}.pem")
        cert = os.path.join(self.dir, f"{name}_cert.pem")
        receipt_signing.generate_key_pair(key, cert, org_name=org, passphrase=passphrase)
        return key, cert

    def signed_pdf(self, key, cert, name="doc.pdf"):
        path = blank_pdf(os.path.join(self.dir, name))
        receipt_signing.sign_pdf(path, key, cert)
        return path


class KeyRotationPreservesHistory(SigningTestCase):
    """The reason retired certificates are kept at all."""

    def rotate_to_a_new_key(self):
        replacement, _ = self.new_key("replacement")
        receipt_signing.import_key_pair(replacement, self.key, self.cert,
                                        org_name="Acme Ltd", force=True)

    def setUp(self):
        super().setUp()
        receipt_signing.generate_key_pair(self.key, self.cert, org_name="Acme Ltd")
        self.old_receipt = self.signed_pdf(self.key, self.cert, "old.pdf")

    def test_the_receipt_verifies_before_rotation(self):
        result = receipt_signing.verify_pdf(self.old_receipt, self.cert)
        self.assertEqual(result.status, receipt_signing.VERIFIED)
        self.assertFalse(result.signed_with_previous_key)

    def test_the_old_certificate_is_remembered(self):
        self.rotate_to_a_new_key()
        self.assertGreaterEqual(
            len(receipt_signing.known_certificate_paths(self.cert)), 2)

    def test_an_old_receipt_still_verifies_after_rotation(self):
        self.rotate_to_a_new_key()
        result = receipt_signing.verify_pdf(self.old_receipt, self.cert)
        self.assertEqual(result.status, receipt_signing.VERIFIED,
                         "rotating a key must not turn issued receipts into forgeries")

    def test_and_is_reported_as_signed_with_a_previous_key(self):
        self.rotate_to_a_new_key()
        result = receipt_signing.verify_pdf(self.old_receipt, self.cert)
        self.assertTrue(result.signed_with_previous_key)
        self.assertIn("previous signing key", result.detail)

    def test_a_receipt_signed_after_rotation_is_not_flagged(self):
        self.rotate_to_a_new_key()
        fresh = self.signed_pdf(self.key, self.cert, "fresh.pdf")
        result = receipt_signing.verify_pdf(fresh, self.cert)
        self.assertEqual(result.status, receipt_signing.VERIFIED)
        self.assertFalse(result.signed_with_previous_key)

    def test_a_forgery_is_still_rejected_after_rotation(self):
        """Trusting more certificates must not mean trusting anyone's."""
        self.rotate_to_a_new_key()
        outsider_key, outsider_cert = self.new_key("outsider", org="Not Acme")
        forged = self.signed_pdf(outsider_key, outsider_cert, "forged.pdf")
        result = receipt_signing.verify_pdf(forged, self.cert)
        self.assertEqual(result.status, receipt_signing.INVALID)

    def test_tampering_is_still_caught_after_rotation(self):
        self.rotate_to_a_new_key()
        tampered = os.path.join(self.dir, "tampered.pdf")
        with open(self.old_receipt, "rb") as f:
            raw = bytearray(f.read())
        raw[len(raw) // 2] ^= 0x01
        with open(tampered, "wb") as f:
            f.write(bytes(raw))
        self.assertEqual(receipt_signing.verify_pdf(tampered, self.cert).status,
                         receipt_signing.INVALID)

    def test_verification_can_be_limited_to_the_current_certificate(self):
        self.rotate_to_a_new_key()
        result = receipt_signing.verify_pdf(self.old_receipt, self.cert,
                                            include_previous=False)
        self.assertEqual(result.status, receipt_signing.INVALID,
                         "opting out of the history should pin only today's key")


class ImportFormats(SigningTestCase):
    """Each unsupported case must say what to do, not raise a parser error."""

    def test_pkcs8_pem(self):
        source, _ = self.new_key("source")
        receipt_signing.import_key_pair(source, self.key, self.cert, org_name="Acme")
        self.assertTrue(os.path.isfile(self.key))
        self.assertTrue(os.path.isfile(self.cert))

    def test_an_imported_bare_key_gets_a_usable_certificate(self):
        source, _ = self.new_key("source")
        receipt_signing.import_key_pair(source, self.key, self.cert, org_name="Acme Ltd")
        signed = self.signed_pdf(self.key, self.cert)
        self.assertEqual(receipt_signing.verify_pdf(signed, self.cert).status,
                         receipt_signing.VERIFIED)

    def test_the_derived_certificate_carries_the_configured_name(self):
        source, _ = self.new_key("source")
        receipt_signing.import_key_pair(source, self.key, self.cert,
                                        org_name="Bakery Ltd",
                                        common_name="Bakery Ltd Receipt Signing")
        info = receipt_signing.certificate_info(self.cert)
        self.assertIn("Bakery Ltd", info["subject"])

    def test_der_encoded_key(self):
        from cryptography.hazmat.primitives import serialization
        source, _ = self.new_key("source")
        loaded = receipt_signing.load_private_key_file(source)
        der = os.path.join(self.dir, "key.der")
        with open(der, "wb") as f:
            f.write(loaded.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()))
        self.assertIsNotNone(receipt_signing.load_private_key_file(der))

    def test_encrypted_key_needs_its_passphrase(self):
        source, _ = self.new_key("locked", passphrase="s3cret")
        with self.assertRaises(receipt_signing.KeyImportError) as ctx:
            receipt_signing.load_private_key_file(source)
        self.assertIn("encrypted", str(ctx.exception).lower())

    def test_encrypted_key_opens_with_the_right_passphrase(self):
        source, _ = self.new_key("locked", passphrase="s3cret")
        self.assertIsNotNone(
            receipt_signing.load_private_key_file(source, passphrase="s3cret"))

    def test_a_wrong_passphrase_says_so(self):
        source, _ = self.new_key("locked", passphrase="s3cret")
        with self.assertRaises(receipt_signing.KeyImportError) as ctx:
            receipt_signing.load_private_key_file(source, passphrase="wrong")
        self.assertIn("passphrase", str(ctx.exception).lower())

    def test_an_imported_encrypted_key_is_stored_unencrypted(self):
        """Documented trade-off: the passphrase is never persisted."""
        source, _ = self.new_key("locked", passphrase="s3cret")
        receipt_signing.import_key_pair(source, self.key, self.cert,
                                        passphrase="s3cret", org_name="Acme")
        with open(self.key, "rb") as f:
            self.assertNotIn(b"ENCRYPTED", f.read())

    def test_picking_a_certificate_by_mistake_is_explained(self):
        _, cert = self.new_key("source")
        with self.assertRaises(receipt_signing.KeyImportError) as ctx:
            receipt_signing.load_private_key_file(cert)
        self.assertIn("certificate, not a private key", str(ctx.exception))

    def test_picking_a_public_key_is_explained(self):
        from cryptography.hazmat.primitives import serialization
        source, _ = self.new_key("source")
        public = os.path.join(self.dir, "public.pem")
        with open(public, "wb") as f:
            f.write(receipt_signing.load_private_key_file(source).public_key()
                    .public_bytes(serialization.Encoding.PEM,
                                  serialization.PublicFormat.SubjectPublicKeyInfo))
        with self.assertRaises(receipt_signing.KeyImportError) as ctx:
            receipt_signing.load_private_key_file(public)
        self.assertIn("public key", str(ctx.exception))

    def test_a_random_file_lists_what_is_supported(self):
        junk = os.path.join(self.dir, "notes.txt")
        with open(junk, "w", encoding="utf-8") as f:
            f.write("this is not a key")
        with self.assertRaises(receipt_signing.KeyImportError) as ctx:
            receipt_signing.load_private_key_file(junk)
        self.assertIn("PKCS#8", str(ctx.exception))

    def test_a_certificate_that_does_not_match_the_key_is_refused(self):
        source, _ = self.new_key("source")
        _, unrelated_cert = self.new_key("unrelated")
        with self.assertRaises(receipt_signing.KeyImportError) as ctx:
            receipt_signing.import_key_pair(source, self.key, self.cert,
                                            certificate_source=unrelated_cert)
        self.assertIn("does not belong", str(ctx.exception))

    def test_importing_over_an_existing_key_needs_force(self):
        receipt_signing.generate_key_pair(self.key, self.cert, org_name="Acme")
        source, _ = self.new_key("source")
        with self.assertRaises(receipt_signing.KeyImportError) as ctx:
            receipt_signing.import_key_pair(source, self.key, self.cert)
        self.assertIn("already exists", str(ctx.exception))

    def test_a_too_small_rsa_key_is_refused(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        with self.assertRaises(receipt_signing.KeyImportError) as ctx:
            receipt_signing._describe_key(weak)
        self.assertIn("2048", str(ctx.exception))


class CertificateInspection(SigningTestCase):
    def test_missing_certificate_reads_as_none(self):
        self.assertIsNone(receipt_signing.certificate_info(self.cert))

    def test_reports_subject_and_expiry(self):
        receipt_signing.generate_key_pair(self.key, self.cert, org_name="Acme Ltd")
        info = receipt_signing.certificate_info(self.cert)
        self.assertIn("Acme Ltd", info["subject"])
        self.assertTrue(info["self_signed"])
        self.assertFalse(info["expired"])
        self.assertGreater(info["days_left"], 3000, "a 10-year certificate")

    def test_a_non_certificate_reads_as_none(self):
        with open(self.cert, "w", encoding="utf-8") as f:
            f.write("not a certificate")
        self.assertIsNone(receipt_signing.certificate_info(self.cert))


class RememberingCertificates(SigningTestCase):
    def test_nothing_to_remember_is_not_an_error(self):
        self.assertIsNone(receipt_signing.remember_current_certificate(self.cert))

    def test_remembering_is_idempotent(self):
        receipt_signing.generate_key_pair(self.key, self.cert, org_name="Acme")
        receipt_signing.remember_current_certificate(self.cert)
        receipt_signing.remember_current_certificate(self.cert)
        archived = [p for p in receipt_signing.known_certificate_paths(self.cert)
                    if "previous_certificates" in p]
        self.assertEqual(len(archived), 1, "the same certificate twice is one entry")

    def test_the_current_certificate_is_listed_first(self):
        receipt_signing.generate_key_pair(self.key, self.cert, org_name="Acme")
        receipt_signing.remember_current_certificate(self.cert)
        self.assertEqual(receipt_signing.known_certificate_paths(self.cert)[0], self.cert)


if __name__ == "__main__":
    unittest.main()
