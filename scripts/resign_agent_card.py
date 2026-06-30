#!/usr/bin/env python3
"""Apply the A2A transport fix to the public agent card and re-sign it.

Background
----------
The public ``/.well-known/agent-card.json`` is a *static, pre-signed* artifact
served by nginx (``alias /var/www/html/.well-known/agent-card.json``). It is NOT
regenerated at request time, and until now there was no committed generator — so
editing it by hand would invalidate the Ed25519 ``signatures[]`` and external
verifiers (and the a2aregistry SDK) would reject it.

This script is that missing, reproducible pipeline. It:
  1. loads the current card (default: the live web-root file),
  2. fixes ``supportedInterfaces[0]`` so the declared transport is real:
       protocolBinding -> "JSONRPC",  url -> https://api.moltrust.ch/a2a
     (without this an A2A registry reports NO_TRANSPORTS / 404),
  3. re-signs via ``app.signature.sign_agent_card`` (strips any old signature
     first, so the new signature covers the corrected body),
  4. VERIFIES the produced signature against the public key and refuses to emit
     an unverifiable card,
  5. writes the result (stdout by default; ``--out PATH``; ``--in-place`` to
     overwrite the input — guarded, deploy-time only).

Signing needs ``MOLTRUST_REGISTRY_PRIVATE_KEY`` (env, hex). ``--verify-only``
needs only the public key and re-checks an already-signed card.

Run from the repo root:  ``python -m scripts.resign_agent_card --help``
"""

import argparse
import base64
import json
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.registry_keys import get_public_key_bytes
from app.signature import canonicalize, sign_agent_card

DEFAULT_CARD_PATH = "/var/www/html/.well-known/agent-card.json"
A2A_URL = "https://api.moltrust.ch/a2a"
A2A_BINDING = "JSONRPC"


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def apply_transport_fix(card: dict) -> tuple[dict, list[str]]:
    """Set the first supported interface to a real A2A JSONRPC transport.

    Returns (new_card, changes). Idempotent; only touches the two fields.
    """
    interfaces = card.get("supportedInterfaces")
    if not isinstance(interfaces, list) or not interfaces:
        raise SystemExit("ERROR: card has no supportedInterfaces[] to fix")

    changes = []
    iface = dict(interfaces[0])
    if iface.get("protocolBinding") != A2A_BINDING:
        changes.append(f"protocolBinding: {iface.get('protocolBinding')!r} -> {A2A_BINDING!r}")
        iface["protocolBinding"] = A2A_BINDING
    if iface.get("url") != A2A_URL:
        changes.append(f"url: {iface.get('url')!r} -> {A2A_URL!r}")
        iface["url"] = A2A_URL

    new_card = dict(card)
    new_card["supportedInterfaces"] = [iface] + interfaces[1:]
    return new_card, changes


def verify_signed_card(card: dict) -> None:
    """Raise if the card's signatures[0] does not verify against our public key."""
    sigs = card.get("signatures")
    if not sigs:
        raise SystemExit("ERROR: signed card has no signatures[]")
    protected_b64 = sigs[0]["protected"]
    signature = _b64url_decode(sigs[0]["signature"])

    body = {k: v for k, v in card.items() if k != "signatures"}
    payload_b64 = base64.urlsafe_b64encode(canonicalize(body)).rstrip(b"=").decode("ascii")
    signing_input = f"{protected_b64}.{payload_b64}".encode("ascii")

    pub = Ed25519PublicKey.from_public_bytes(get_public_key_bytes())
    try:
        pub.verify(signature, signing_input)
    except InvalidSignature:
        raise SystemExit("ERROR: produced signature does NOT verify — refusing to emit")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", default=DEFAULT_CARD_PATH, help=f"input card (default: {DEFAULT_CARD_PATH})")
    ap.add_argument("--out", help="output path (default: stdout)")
    ap.add_argument("--in-place", action="store_true", help="overwrite --in (deploy-time only)")
    ap.add_argument("--verify-only", action="store_true", help="verify the input card's signature and exit")
    args = ap.parse_args(argv)

    with open(args.inp) as f:
        card = json.load(f)

    if args.verify_only:
        verify_signed_card(card)
        print(f"OK: signature verifies for {args.inp}", file=sys.stderr)
        return 0

    fixed, changes = apply_transport_fix(card)
    signed = sign_agent_card(fixed)
    verify_signed_card(signed)

    print("transport fix applied:" if changes else "no transport change needed (already JSONRPC/url).", file=sys.stderr)
    for c in changes:
        print("  - " + c, file=sys.stderr)
    print("signature: re-signed and verified OK", file=sys.stderr)

    out_text = json.dumps(signed, indent=2, ensure_ascii=False) + "\n"
    target = args.inp if args.in_place else args.out
    if target:
        with open(target, "w") as f:
            f.write(out_text)
        print(f"written: {target}", file=sys.stderr)
    else:
        sys.stdout.write(out_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
