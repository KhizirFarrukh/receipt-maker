#!/usr/bin/env python3
"""Verify that a receipt PDF is authentic (offline, against the store certificate).

This is the reference implementation of receipt verification. The store website
can lift receipt_signing.verify_pdf() almost verbatim to build its /verify page.

A receipt is authentic only when its digital signature is intact, covers the
whole file, and was made by the store's certificate (signing/certificate.pem).
A fabricated or edited receipt fails.

Usage:
  python verify_receipt.py path/to/receipt.pdf
  python verify_receipt.py path/to/receipt.pdf --cert path/to/certificate.pem

Exit codes:  0 = verified,  1 = invalid signature,  2 = no signature,  3 = error
"""
import argparse
import sys

import receipt_signing

EXIT_CODES = {
    receipt_signing.VERIFIED: 0,
    receipt_signing.INVALID: 1,
    receipt_signing.NOT_FOUND: 2,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify a receipt PDF's authenticity.")
    parser.add_argument("pdf", help="Path to the receipt PDF to verify.")
    parser.add_argument(
        "--cert", default=receipt_signing.DEFAULT_CERT_PATH,
        help="Store public certificate to verify against "
             f"(default: {receipt_signing.DEFAULT_CERT_PATH}).",
    )
    args = parser.parse_args(argv)

    try:
        result = receipt_signing.verify_pdf(args.pdf, args.cert)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # pragma: no cover
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    mark = {
        receipt_signing.VERIFIED: "[ VERIFIED ]",
        receipt_signing.INVALID: "[ INVALID  ]",
        receipt_signing.NOT_FOUND: "[ NO SIG   ]",
    }[result.status]
    print(f"{mark} {result.title}")
    print(result.detail)
    if result.signer:
        print(f"\nSigner: {result.signer}")
    if result.signed_time:
        print(f"Signed: {result.signed_time}")
    return EXIT_CODES.get(result.status, 3)


if __name__ == "__main__":
    raise SystemExit(main())
