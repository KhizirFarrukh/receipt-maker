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
# Neutral defaults: this module is white-label, and the store's identity is
# passed in by the caller (keygen.py reads it from appsettings.json). Baking a
# company name in here put the wrong name in the certificate subject of every
# key generated from a config that said otherwise.
DEFAULT_CERT_COMMON_NAME = "Receipt Signing"
DEFAULT_CERT_ORG_NAME = "Your Company"
CERT_VALIDITY_YEARS = 10
RSA_KEY_SIZE = 3072  # broad verifier compatibility (incl. Adobe); one-time cost

# Name of the PDF signature form field. Verification enumerates every embedded
# signature regardless of field name, so changing this does not affect receipts
# already signed under the old name.
SIGNATURE_FIELD_NAME = "ReceiptSignature"

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
    #: True when a retired certificate verified it -- genuine, but issued before
    #: the signing key was last changed.
    signed_with_previous_key: bool = False

    @property
    def verified(self):
        return self.status == VERIFIED


# ------------------- key generation -------------------
def generate_key_pair(key_path=DEFAULT_KEY_PATH, cert_path=DEFAULT_CERT_PATH,
                      force=False, passphrase=None,
                      common_name=None, org_name=None):
    """Create a private key + self-signed certificate. Returns (key_path, cert_path).

    common_name / org_name become the certificate subject -- this is the identity
    a verifier displays for the receipt, so callers should pass the store's real
    name (keygen.py takes it from appsettings.json).
    """
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
        x509.NameAttribute(NameOID.COMMON_NAME,
                           (common_name or "").strip() or DEFAULT_CERT_COMMON_NAME),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME,
                           (org_name or "").strip() or DEFAULT_CERT_ORG_NAME),
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


# ------------------- key import -------------------
#: Name of the folder holding certificates retired by a key rotation. Receipts
#: signed with them must keep verifying: the receipt was genuine when it was
#: issued, and rotating a key cannot retroactively make a customer's copy look
#: forged.
KNOWN_CERTS_DIRNAME = "previous_certificates"

#: The archive for the *default* certificate. Kept for callers that ask for it
#: by name, but nothing here uses it -- see known_certs_dir() for why.
KNOWN_CERTS_DIR = os.path.join(SIGNING_DIR, KNOWN_CERTS_DIRNAME)


def known_certs_dir(cert_path):
    """Where `cert_path`'s retired certificates live: a sibling folder.

    Derived from the certificate rather than from a module-level constant, and
    that is the fix for a real bug rather than a tidy-up. `SIGNING_DIR` is
    computed at import time from this module's own idea of APP_DIR, so it never
    saw `config.set_app_dir()` -- which meant `cli.py --config-dir` archived
    into the wrong folder, and the test suite quietly wrote 65 certificates
    into the developer's real project directory.

    Deriving it from the certificate also happens to be more correct: a
    retired certificate belongs beside the one that replaced it, wherever that
    is, including when someone points the config at a key outside APP_DIR.

    This module deliberately does not import `config` (ARCHITECTURE: "no config
    coupling"), so reading APP_DIR at call time was not an option here.
    """
    return os.path.join(os.path.dirname(os.path.abspath(cert_path)),
                        KNOWN_CERTS_DIRNAME)

MIN_RSA_BITS = 2048


def key_is_encrypted(key_path):
    """Whether the private key file needs a passphrase to be read.

    Cheap and text-only: the PEM header says so. Worth knowing *before* trying
    to sign, because pyHanko answers an encrypted key with no passphrase by
    returning None rather than raising, and "could not load the key" is not a
    sentence anybody can act on.
    """
    try:
        with open(key_path, "rb") as handle:
            head = handle.read(200)
    except OSError:
        return False
    return b"ENCRYPTED PRIVATE KEY" in head or b"Proc-Type: 4,ENCRYPTED" in head


def key_problem(key_path, cert_path, passphrase=""):
    """Why this key cannot sign, in a sentence, or "" if it can.

    Checked up front rather than inferred from a failure, so the message names
    the actual cause and what to do about it.
    """
    if not os.path.isfile(key_path):
        return (f"There is no signing key at\n{key_path}\n\n"
                f"Create one under Tools -> Signing Keys.")
    if not os.path.isfile(cert_path):
        return (f"There is no certificate at\n{cert_path}\n\n"
                f"Create or import one under Tools -> Signing Keys.")
    if key_is_encrypted(key_path) and not passphrase:
        return (f"The signing key is encrypted and no passphrase is set, so it "
                f"cannot be read.\n{key_path}\n\n"
                f"Either set the passphrase under Tools -> Settings -> Signing, "
                f"or import the key again under Tools -> Signing Keys -- the "
                f"import asks for the passphrase once and saves the key so the "
                f"app can read it without one.")
    return ""


class KeyImportError(RuntimeError):
    """An existing key could not be imported. Message is meant for the user."""


def _describe_key(private_key):
    """(algorithm, bits) for a loaded private key, or raise for an unusable one."""
    from cryptography.hazmat.primitives.asymmetric import rsa, ec

    if isinstance(private_key, rsa.RSAPrivateKey):
        if private_key.key_size < MIN_RSA_BITS:
            raise KeyImportError(
                f"This RSA key is {private_key.key_size} bits. Receipts need at "
                f"least {MIN_RSA_BITS} bits to be accepted by PDF readers.")
        return "RSA", private_key.key_size
    if isinstance(private_key, ec.EllipticCurvePrivateKey):
        return "EC", private_key.curve.key_size
    raise KeyImportError(
        "That key type is not supported for signing receipts. Use an RSA or an "
        "EC key.")


def load_private_key_file(path, passphrase=None):
    """Load a private key from PEM or DER, PKCS#8 or PKCS#1, encrypted or not.

    Each failure gets a message saying what to do about it. A stack trace here
    would be useless: the person importing a key knows what file they picked,
    not what an ASN.1 parser thinks of it.
    """
    from cryptography.hazmat.primitives import serialization

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise KeyImportError(f"Could not read {path}:\n{exc}") from exc

    secret = passphrase.encode("utf-8") if passphrase else None
    looks_encrypted = b"ENCRYPTED" in data[:200]

    for loader in (serialization.load_pem_private_key,
                   serialization.load_der_private_key):
        try:
            return loader(data, password=secret)
        except (TypeError, ValueError):
            continue
        except Exception:
            continue

    if looks_encrypted and not passphrase:
        raise KeyImportError(
            "This key is encrypted. Enter its passphrase and try again.")
    if looks_encrypted:
        raise KeyImportError(
            "The passphrase did not unlock this key. Check it and try again.")
    if b"BEGIN CERTIFICATE" in data:
        raise KeyImportError(
            "That file is a certificate, not a private key. The certificate is "
            "the public half -- pick the private key file instead.")
    if b"BEGIN PUBLIC KEY" in data:
        raise KeyImportError(
            "That is a public key. Signing needs the matching private key.")
    raise KeyImportError(
        "This file is not a private key the app recognises. Supported: PEM or "
        "DER, PKCS#8 or PKCS#1, encrypted or not, and PKCS#12 (.pfx/.p12).")


def load_pkcs12_file(path, passphrase=None):
    """Load (key, cert) from a .pfx/.p12 bundle."""
    from cryptography.hazmat.primitives.serialization import pkcs12

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise KeyImportError(f"Could not read {path}:\n{exc}") from exc

    secret = passphrase.encode("utf-8") if passphrase else None
    try:
        private_key, certificate, _extra = pkcs12.load_key_and_certificates(data, secret)
    except Exception as exc:
        raise KeyImportError(
            "Could not open this PKCS#12 file. If it has a passphrase, enter it "
            "and try again." if not passphrase else
            "The passphrase did not open this PKCS#12 file.") from exc

    if private_key is None:
        raise KeyImportError("This PKCS#12 file contains no private key.")
    return private_key, certificate


def import_key_pair(source_path, key_path=None, cert_path=None, passphrase=None,
                    certificate_source=None, common_name=None, org_name=None,
                    force=False):
    """Import an existing key (and certificate) into the app's signing folder.

    Accepts a bare private key or a PKCS#12 bundle. When the source carries no
    certificate and none is supplied, a self-signed one is derived from the key
    so verification has something to pin.

    The key is re-written unencrypted into the app's own folder, because the
    passphrase is never persisted -- it exists only for as long as the import
    dialog is open. That is a deliberate trade: a passphrase stored beside the
    key it unlocks protects nobody, and prompting on every receipt is not
    workable for a till.
    """
    from cryptography.hazmat.primitives import serialization

    key_path = key_path or DEFAULT_KEY_PATH
    cert_path = cert_path or DEFAULT_CERT_PATH
    if not force and (os.path.exists(key_path) or os.path.exists(cert_path)):
        raise KeyImportError(
            f"A signing key already exists at:\n  {key_path}\n\n"
            f"Replacing it means receipts you issue from now on are signed with "
            f"the new key. Existing receipts keep verifying, because the old "
            f"certificate is remembered.")

    certificate = None
    if os.path.splitext(source_path)[1].lower() in (".pfx", ".p12"):
        private_key, certificate = load_pkcs12_file(source_path, passphrase)
    else:
        private_key = load_private_key_file(source_path, passphrase)
    _describe_key(private_key)

    if certificate_source:
        certificate = _load_certificate_file(certificate_source)
        _check_key_matches_certificate(private_key, certificate)
    elif certificate is None:
        certificate = _self_signed_for(private_key, common_name, org_name)

    remember_current_certificate(cert_path)
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    _write_key(key_path, private_key)
    with open(cert_path, "wb") as f:
        f.write(certificate.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


def _write_key(key_path, private_key):
    from cryptography.hazmat.primitives import serialization

    with open(key_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    _restrict_permissions(key_path)


def _restrict_permissions(path):
    """Best effort: keep the private key readable only by its owner."""
    try:
        if os.name == "posix":
            os.chmod(path, 0o600)
    except OSError:
        pass


def _load_certificate_file(path):
    from cryptography import x509

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise KeyImportError(f"Could not read {path}:\n{exc}") from exc
    for loader in (x509.load_pem_x509_certificate, x509.load_der_x509_certificate):
        try:
            return loader(data)
        except Exception:
            continue
    raise KeyImportError("That file is not a certificate the app can read.")


def _check_key_matches_certificate(private_key, certificate):
    from cryptography.hazmat.primitives import serialization

    def public_bytes(key):
        return key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)

    if public_bytes(private_key.public_key()) != public_bytes(certificate.public_key()):
        raise KeyImportError(
            "This certificate does not belong to this private key. A receipt "
            "signed with them would never verify.")


def _self_signed_for(private_key, common_name=None, org_name=None):
    """Derive a self-signed certificate for an imported bare key."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID

    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME,
                           (common_name or "").strip() or DEFAULT_CERT_COMMON_NAME),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME,
                           (org_name or "").strip() or DEFAULT_CERT_ORG_NAME),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=365 * CERT_VALIDITY_YEARS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )


# ------------------- key rotation -------------------
def remember_current_certificate(cert_path=DEFAULT_CERT_PATH):
    """Archive the certificate about to be replaced, so old receipts still verify.

    Without this, rotating a key would make every previously issued receipt
    report as a forgery -- which is both wrong and alarming to a customer
    holding a perfectly genuine one.
    """
    if not os.path.isfile(cert_path):
        return None
    try:
        import hashlib

        with open(cert_path, "rb") as f:
            data = f.read()
        archive = known_certs_dir(cert_path)
        os.makedirs(archive, exist_ok=True)
        target = os.path.join(
            archive, f"certificate-{hashlib.sha256(data).hexdigest()[:16]}.pem")
        if not os.path.exists(target):
            with open(target, "wb") as f:
                f.write(data)
        return target
    except OSError:
        return None


def known_certificate_paths(cert_path=DEFAULT_CERT_PATH):
    """Current certificate first, then any retired ones."""
    paths = [cert_path] if os.path.isfile(cert_path) else []
    archive = known_certs_dir(cert_path)
    try:
        for name in sorted(os.listdir(archive)):
            if name.lower().endswith((".pem", ".crt", ".cer")):
                paths.append(os.path.join(archive, name))
    except OSError:
        pass
    return paths


# ------------------- certificate inspection -------------------
def certificate_info(cert_path=DEFAULT_CERT_PATH):
    """Human-facing summary of a certificate, or None if it cannot be read."""
    import datetime

    if not os.path.isfile(cert_path):
        return None
    try:
        certificate = _load_certificate_file(cert_path)
    except KeyImportError:
        return None

    try:
        not_after = certificate.not_valid_after_utc
        not_before = certificate.not_valid_before_utc
    except AttributeError:                      # cryptography < 42
        not_after = certificate.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        not_before = certificate.not_valid_before.replace(tzinfo=datetime.timezone.utc)

    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "subject": certificate.subject.rfc4514_string(),
        "issuer": certificate.issuer.rfc4514_string(),
        "self_signed": certificate.subject == certificate.issuer,
        "not_before": not_before,
        "not_after": not_after,
        "days_left": (not_after - now).days,
        "expired": not_after < now,
        "path": cert_path,
    }


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
        # pyHanko returns None rather than raising for several distinct
        # problems, so ask what is actually wrong before falling back to the
        # catch-all. An encrypted key with no passphrase is the common one and
        # used to be indistinguishable from a mismatched pair.
        problem = key_problem(key_path, cert_path, passphrase)
        raise RuntimeError(problem or (
            "Could not load the signing key/certificate. Check that the key and "
            "certificate match and that the passphrase is correct."
        ))

    metadata = signers.PdfSignatureMetadata(
        field_name=SIGNATURE_FIELD_NAME,
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
def verify_pdf(pdf_path, cert_path=DEFAULT_CERT_PATH, include_previous=True):
    """Verify a receipt PDF against the store's certificate(s).

    Returns a VerifyResult whose ``status`` is one of:
      VERIFIED  - a valid signature made by one of the store's certificates
      INVALID   - a signature exists but is tampered, forged, or not the store's
      NOT_FOUND - the PDF has no digital signature at all

    ``include_previous`` also trusts certificates retired by a key rotation. A
    receipt was genuine when it was issued; replacing a key must not turn every
    copy already in customers' hands into an apparent forgery. The result says
    which certificate signed it, so a receipt signed under an older key is still
    distinguishable from one signed today.

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

    paths = known_certificate_paths(cert_path) if include_previous else [cert_path]
    trusted = []
    for path in paths:
        try:
            trusted.append(load_cert_from_pemder(path))
        except Exception:
            continue          # a damaged archived cert must not block verification
    if not trusted:
        trusted = [load_cert_from_pemder(cert_path)]

    current = trusted[0]
    trusted_dumps = {c.dump() for c in trusted}
    validation_context = ValidationContext(trust_roots=trusted)

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
            signed_with_previous = False
            for sig in signatures:
                status = validate_pdf_signature(sig, signer_validation_context=validation_context)
                signer_dump = status.signing_cert.dump()
                pin_match = signer_dump in trusted_dumps
                if pin_match and signer_dump != current.dump():
                    signed_with_previous = True
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
                detail = ("This receipt carries a valid digital signature from the "
                          "store's certificate and has not been altered since it "
                          "was issued.")
                if signed_with_previous:
                    detail += ("\n\nIt was signed with a previous signing key, which "
                               "is expected for a receipt issued before the key was "
                               "changed.")
                return VerifyResult(
                    status=VERIFIED, title="Signature Verified", detail=detail,
                    signer=signer_name, signed_time=signed_time,
                    reason=declared_reason, location=declared_location,
                    signed_with_previous_key=signed_with_previous,
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
