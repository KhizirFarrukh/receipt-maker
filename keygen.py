#!/usr/bin/env python3
"""Generate the receipt-signing key pair (run this once).

Creates a private key + self-signed X.509 certificate used to apply a PAdES
digital signature to every generated receipt PDF:

  signing/private_key.pem  -> SECRET. Stays on this machine only. Never share it,
                              never commit it, never bundle it into the distributed
                              .exe. Anyone who has it can forge your receipts.
                              Back it up somewhere safe -- losing it means you must
                              issue a new certificate and re-publish it.

  signing/certificate.pem  -> PUBLIC. This is your store's identity for receipts.
                              Upload it to your website / verifier so anyone can
                              confirm a receipt genuinely came from you.

Usage:
  python keygen.py                 # create the key pair (refuses to overwrite)
  python keygen.py --passphrase X  # also encrypt the private key with passphrase X
                                   # (put the same value in appsettings.json ->
                                   #  signing.key_passphrase)
  python keygen.py --force         # DANGER: replace an existing key. This
                                   # invalidates every receipt already signed.
"""
import argparse
import sys

import receipt_signing


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate the receipt-signing key pair (run once)."
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing key. DANGER: invalidates receipts already signed.",
    )
    parser.add_argument(
        "--passphrase", default=None,
        help="Optional passphrase to encrypt the private key. Put the same value in "
             "appsettings.json -> signing.key_passphrase.",
    )
    args = parser.parse_args(argv)

    try:
        key_path, cert_path = receipt_signing.generate_key_pair(
            force=args.force, passphrase=args.passphrase
        )
    except FileExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - surfaced to the user
        print(f"ERROR: could not generate the key pair:\n{exc}", file=sys.stderr)
        return 1

    print("Signing key pair created:")
    print(f"  private key : {key_path}")
    print(f"  certificate : {cert_path}")
    print()
    print("NEXT STEPS")
    print("  1. Keep private_key.pem SECRET. Do not share or commit it, and do not")
    print("     bundle it into the distributed .exe. Back it up somewhere safe.")
    print("  2. Publish certificate.pem on your verifier / website so receipts can")
    print("     be checked. (It is safe to share; it only verifies, never signs.)")
    print("  3. Receipts you generate from now on will be digitally signed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
