"""Receipt PAdES signing, verification and key generation.

This is the single source of truth for the receipt-authenticity feature. It is
deliberately free of any GUI / app-settings coupling so that:

  * main.py            imports sign_pdf() to sign each receipt it generates,
                       and verify_pdf() for the in-app "Verify Receipt" tool;
  * keygen.py          imports generate_key_pair() (one-time setup);
  * verify_receipt.py  imports verify_pdf() (offline CLI verifier);
  * your website       can lift verify_pdf() almost verbatim as the reference
                       implementation of receipt verification.

Security model: a receipt is signed with the store's PRIVATE key. Verification
pins the store's PUBLIC certificate as the sole trust root, so a receipt is only
"verified" when its signature is intact, covers the whole file, and was made by
that exact certificate. A forgery signed with any other key is rejected.
"""
import logging
import os
import sys
from dataclasses import dataclass

# pyHanko logs an ERROR (with a traceback) when a signer cert cannot be validated
# against the trust root. For us that is the *expected* outcome for a forgery
# (we report it as "invalid"), so keep it from spamming the console/UI.
for _logger_name in ("pyhanko", "pyhanko_certvalidator"):
    logging.getLogger(_logger_name).setLevel(logging.CRITICAL)

# ------------------- paths -------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else BASE_DIR
SIGNING_DIR = os.path.join(APP_DIR, "signing")
DEFAULT_KEY_PATH = os.path.join(SIGNING_DIR, "private_key.pem")
DEFAULT_CERT_PATH = os.path.join(SIGNING_DIR, "certificate.pem")

# ------------------- certificate identity -------------------
CERT_COMMON_NAME = "Chawla Tech Receipt Signing"
CERT_ORG_NAME = "Chawla Tech"
CERT_VALIDITY_YEARS = 10
RSA_KEY_SIZE = 3072  # broad verifier compatibility (incl. Adobe); one-time cost

# ------------------- verification verdicts -------------------
VERIFIED = "verified"       # signed, intact, whole-file, and by the pinned cert
INVALID = "invalid"         # signed, but tampered / forged / not the store's cert
NOT_FOUND = "not_found"     # no digital signature present at all


@dataclass
class VerifyResult:
    status: str                       # one of VERIFIED / INVALID / NOT_FOUND
    title: str                        # short human verdict, e.g. "Signature Verified"
    detail: str = ""                  # multi-line explanation
    signer: str = ""                  # signer certificate subject (if any)
    signed_time: str = ""             # signer-reported signing time (if any)
    reason: str = ""                  # declared signing reason (if any)
    location: str = ""                # declared signing location (if any)

    @property
    def verified(self):
        return self.status == VERIFIED


# ------------------- key generation -------------------
def generate_key_pair(key_path=DEFAULT_KEY_PATH, cert_path=DEFAULT_CERT_PATH,
                      force=False, passphrase=None):
    """Create a private key + self-signed certificate. Returns (key_path, cert_path)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import datetime

    if not force and (os.path.exists(key_path) or os.path.exists(cert_path)):
        raise FileExistsError(
            f"A signing key already exists at:\n  {key_path}\n"
            "Refusing to overwrite it -- that would invalidate every receipt already\n"
            "signed with it. Use force=True only if you really mean to replace it."
        )

    os.makedirs(os.path.dirname(key_path), exist_ok=True)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_SIZE)

    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, CERT_COMMON_NAME),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, CERT_ORG_NAME),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)  # self-signed: subject == issuer
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))  # tolerate clock skew
        .not_valid_after(now + datetime.timedelta(days=365 * CERT_VALIDITY_YEARS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,  # non-repudiation
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    if passphrase:
        encryption = serialization.BestAvailableEncryption(passphrase.encode("utf-8"))
    else:
        encryption = serialization.NoEncryption()

    with open(key_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=encryption,
        ))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    return key_path, cert_path


# ------------------- signing -------------------
def sign_pdf(pdf_path, key_path, cert_path, *, passphrase=None, reason=None,
             location=None, name=None, tsa_url=None):
    """Apply a PAdES signature to pdf_path in place. Raises RuntimeError on failure."""
    try:
        from pyhanko.sign import signers
        from pyhanko.sign.fields import SigSeedSubFilter
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    except ImportError as exc:
        raise RuntimeError(
            "pyHanko is not installed.\n"
            "Run: python -m pip install -r requirements.txt"
        ) from exc

    try:
        signer = signers.SimpleSigner.load(
            key_path, cert_path,
            key_passphrase=passphrase.encode("utf-8") if passphrase else None,
        )
    except Exception as exc:
        raise RuntimeError(f"Could not load the signing key/certificate:\n{exc}") from exc
    if signer is None:
        raise RuntimeError(
            "Could not load the signing key/certificate. Check that the key and "
            "certificate match and that the passphrase is correct."
        )

    metadata = signers.PdfSignatureMetadata(
        field_name="ChawlaTechSignature",
        subfilter=SigSeedSubFilter.PADES,
        reason=reason or None,
        location=location or None,
        name=name or None,
    )

    timestamper = None
    if tsa_url:
        try:
            from pyhanko.sign.timestamps import HTTPTimeStamper
            timestamper = HTTPTimeStamper(tsa_url)
        except Exception as exc:
            raise RuntimeError(f"Could not set up the timestamp authority:\n{exc}") from exc

    pdf_signer = signers.PdfSigner(metadata, signer=signer, timestamper=timestamper)

    tmp_path = pdf_path + ".signing.tmp"
    try:
        with open(pdf_path, "rb") as inf:
            writer = IncrementalPdfFileWriter(inf)
            with open(tmp_path, "wb") as outf:
                pdf_signer.sign_pdf(writer, output=outf)
        os.replace(tmp_path, pdf_path)
    except Exception as exc:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise RuntimeError(f"Could not sign the PDF:\n{exc}") from exc


def is_signed(pdf_path):
    """Return True if the PDF already carries at least one digital signature."""
    from pyhanko.pdf_utils.reader import PdfFileReader
    with open(pdf_path, "rb") as f:
        reader = PdfFileReader(f)
        return len(reader.embedded_signatures) > 0


# ------------------- verification -------------------
def verify_pdf(pdf_path, cert_path=DEFAULT_CERT_PATH):
    """Verify a receipt PDF against the pinned store certificate.

    Returns a VerifyResult whose ``status`` is one of:
      VERIFIED  - a valid signature made by the store's certificate, whole file
      INVALID   - a signature exists but is tampered, forged, or not the store's
      NOT_FOUND - the PDF has no digital signature at all
    Only raises for unreadable inputs (missing file / missing certificate).
    """
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko.sign.validation.status import SignatureCoverageLevel
    from pyhanko_certvalidator import ValidationContext
    from pyhanko.keys import load_cert_from_pemder

    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if not os.path.isfile(cert_path):
        raise FileNotFoundError(
            f"Store certificate not found: {cert_path}\n"
            "Place your public certificate.pem there (run keygen.py to create the pair)."
        )

    pinned = load_cert_from_pemder(cert_path)
    validation_context = ValidationContext(trust_roots=[pinned])

    try:
        with open(pdf_path, "rb") as f:
            reader = PdfFileReader(f)
            signatures = reader.embedded_signatures
            if not signatures:
                return VerifyResult(
                    status=NOT_FOUND,
                    title="Signature not found",
                    detail="This PDF has no digital signature. It was not produced by "
                           "the signed-receipt generator (or the signature was stripped).",
                )

            # A genuine receipt is signed exactly once and untouched afterwards, so
            # EVERY embedded signature must pass. If any fails, the file is invalid.
            first = signatures[0]
            reasons = []
            signer_name = ""
            signed_time = ""
            declared_reason = ""
            declared_location = ""

            all_ok = True
            for sig in signatures:
                status = validate_pdf_signature(sig, signer_validation_context=validation_context)
                pin_match = status.signing_cert.dump() == pinned.dump()
                whole_file = status.coverage == SignatureCoverageLevel.ENTIRE_FILE
                if sig is first:
                    signer_name = status.signing_cert.subject.human_friendly
                    if status.signer_reported_dt:
                        signed_time = status.signer_reported_dt.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
                    declared_reason = str(sig.sig_object.get("/Reason", "") or "")
                    declared_location = str(sig.sig_object.get("/Location", "") or "")

                if not status.intact:
                    all_ok = False
                    reasons.append("the document was modified after signing (content no longer matches)")
                elif not status.valid:
                    all_ok = False
                    reasons.append("the cryptographic signature is not valid")
                elif not pin_match or not status.trusted:
                    all_ok = False
                    reasons.append("it was signed by a different key, not the store's certificate (possible forgery)")
                elif not whole_file:
                    all_ok = False
                    reasons.append("content was appended after signing (the signature does not cover the whole file)")

            if all_ok:
                detail = "This receipt carries a valid digital signature from the store's certificate "
                detail += "and has not been altered since it was issued."
                return VerifyResult(
                    status=VERIFIED, title="Signature Verified", detail=detail,
                    signer=signer_name, signed_time=signed_time,
                    reason=declared_reason, location=declared_location,
                )

            # de-duplicate reasons, keep order
            seen = []
            for r in reasons:
                if r not in seen:
                    seen.append(r)
            detail = "This receipt has a signature, but it did not pass verification:\n  - " + \
                     "\n  - ".join(seen)
            return VerifyResult(
                status=INVALID, title="Invalid signature", detail=detail,
                signer=signer_name, signed_time=signed_time,
            )
    except FileNotFoundError:
        raise
    except Exception as exc:
        # A malformed/corrupted PDF (e.g. crudely edited bytes) lands here.
        return VerifyResult(
            status=INVALID,
            title="Invalid signature",
            detail="The signature could not be verified because the file appears to be "
                   f"corrupted or was tampered with.\n\nDetails: {exc}",
        )
