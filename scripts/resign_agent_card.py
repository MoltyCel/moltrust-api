#!/usr/bin/env python3
"""Normalize the public agent card and re-sign it.

Background
----------
The public ``/.well-known/agent-card.json`` is a *static, pre-signed* artifact
served by nginx (``alias /var/www/html/.well-known/agent-card.json``). It is NOT
regenerated at request time, so editing it by hand invalidates the Ed25519
``signatures[]`` and external verifiers (and the a2aregistry SDK) reject it.

That is not hypothetical. On 2026-09-20 the web-root file was edited in place,
the signature was not regenerated, and for a day the card shipped a signature
covering a body that no longer existed. A card with a broken signature is worse
than an unsigned one: it claims verifiability and then fails the check.

The durable fix is that the card now lives in this repo at
``.well-known/agent-card.json`` and is deployed from there. This script is the
only supported way to produce it. It:
  1. loads the card (default: the committed repo copy),
  2. normalizes it — see ``normalize`` for the individual fixes, all idempotent,
  3. re-signs via ``app.signature.sign_agent_card`` (strips any old signature
     first, so the new signature covers the corrected body),
  4. VERIFIES the result with ``lib.agent_card_verify``, which shares no code
     with the signing path, and refuses to emit a card it cannot verify,
  5. writes the result (stdout by default; ``--out PATH``; ``--in-place`` to
     overwrite the input).

Signing needs ``MOLTRUST_REGISTRY_PRIVATE_KEY`` (env, hex). ``--verify-only``
needs only the published JWK and re-checks an already-signed card.

Run from the repo root:  ``python -m scripts.resign_agent_card --help``
"""

import argparse
import base64
import json
import os
import sys

from app.registry_keys import get_public_key_bytes
from app.signature import sign_agent_card
from lib.agent_card_verify import CardVerificationError, verify_agent_card

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CARD_PATH = os.path.join(REPO_ROOT, ".well-known", "agent-card.json")
DEPLOY_CARD_PATH = "/var/www/html/.well-known/agent-card.json"
A2A_URL = "https://api.moltrust.ch/a2a"
A2A_BINDING = "JSONRPC"

# The x402 facilitator we name must be one that can actually settle the network
# we invoice on (Base mainnet, eip155:8453). The card used to name
# https://api.moltrust.ch/x402 — our own API, which serves /x402/verify but has
# no /supported, so it is not a facilitator interface at all. The public
# x402.org facilitator only lists testnets (checked 2026-09-21: base-sepolia,
# eip155:84532, and non-EVM testnets — no eip155:8453). Coinbase CDP is the
# mainnet one. Its /supported answers 401 without CDP credentials; that is the
# documented behaviour of an authenticated facilitator, not a dead endpoint.
X402_FACILITATOR = "https://api.cdp.coinbase.com/platform/v2/x402"
X402_EXTENSION_URI = "https://moltrust.ch/extensions/x402-payment/v1"

# A2A declares these fields as media types. The card shipped the A2A *part*
# kind names instead ("text", "data"), which no content negotiator resolves.
# This is the mapping between the two: a TextPart is text/plain, a DataPart is
# a JSON object.
MODE_MEDIA_TYPES = {
    "text": "text/plain",
    "data": "application/json",
}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


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


def apply_facilitator_fix(card: dict) -> tuple[dict, list[str]]:
    """Point the x402 extension at a facilitator that settles Base mainnet."""
    capabilities = card.get("capabilities")
    if not isinstance(capabilities, dict):
        return card, []
    extensions = capabilities.get("extensions")
    if not isinstance(extensions, list):
        return card, []

    changes: list[str] = []
    new_extensions = []
    for ext in extensions:
        if isinstance(ext, dict) and ext.get("uri") == X402_EXTENSION_URI:
            params = dict(ext.get("params") or {})
            if params.get("facilitator") != X402_FACILITATOR:
                changes.append(
                    f"x402 facilitator: {params.get('facilitator')!r} -> {X402_FACILITATOR!r}"
                )
                params["facilitator"] = X402_FACILITATOR
            ext = {**ext, "params": params}
        new_extensions.append(ext)

    if not changes:
        return card, []
    return {**card, "capabilities": {**capabilities, "extensions": new_extensions}}, changes


def _media_types(modes, where: str, changes: list[str]):
    """Map A2A part-kind names to media types, leaving real media types alone."""
    if not isinstance(modes, list):
        return modes
    out = []
    for mode in modes:
        mapped = MODE_MEDIA_TYPES.get(mode, mode)
        if mapped != mode:
            changes.append(f"{where}: {mode!r} -> {mapped!r}")
        if mapped not in out:
            out.append(mapped)
    return out


def apply_media_type_fix(card: dict) -> tuple[dict, list[str]]:
    """Rewrite every input/output mode list as media types.

    Covers the card-level defaults *and* every skill. Fixing only the defaults
    would leave thirteen skills advertising a mode name the defaults no longer
    use, which reads as a worse inconsistency than the one being repaired.
    """
    changes: list[str] = []
    new_card = dict(card)

    for field in ("defaultInputModes", "defaultOutputModes"):
        if field in new_card:
            new_card[field] = _media_types(new_card[field], field, changes)

    skills = new_card.get("skills")
    if isinstance(skills, list):
        new_skills = []
        for skill in skills:
            if isinstance(skill, dict):
                skill = dict(skill)
                for field in ("inputModes", "outputModes"):
                    if field in skill:
                        skill[field] = _media_types(
                            skill[field], f"skills[{skill.get('id')}].{field}", changes
                        )
            new_skills.append(skill)
        new_card["skills"] = new_skills

    return new_card, changes


NORMALIZERS = (apply_transport_fix, apply_facilitator_fix, apply_media_type_fix)


def normalize(card: dict) -> tuple[dict, list[str]]:
    """Apply every card fix in order. All are idempotent. Returns (card, changes)."""
    changes: list[str] = []
    for fn in NORMALIZERS:
        card, made = fn(card)
        changes.extend(made)
    return card, changes


def published_jwk() -> dict:
    """The verification key as a third party receives it from /.well-known."""
    return {"kty": "OKP", "crv": "Ed25519", "x": _b64url(get_public_key_bytes())}


def verify_signed_card(card: dict) -> None:
    """Refuse the card unless the independent verifier accepts it.

    Checked with ``lib.agent_card_verify``, which reimplements RFC 8785 instead
    of importing the canonicalizer that produced the signature. Verifying a
    signature with the same code that made it proves only self-consistency; the
    audience that matters is a stranger holding our published JWK.
    """
    try:
        verify_agent_card(card, published_jwk())
    except CardVerificationError as exc:
        raise SystemExit(f"ERROR: card does NOT verify ({exc}) — refusing to emit")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--in", dest="inp", default=DEFAULT_CARD_PATH,
        help="input card (default: the committed repo copy .well-known/agent-card.json)",
    )
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

    fixed, changes = normalize(card)
    signed = sign_agent_card(fixed)
    verify_signed_card(signed)

    print(f"normalization: {len(changes)} change(s)", file=sys.stderr)
    for c in changes:
        print("  - " + c, file=sys.stderr)
    print("signature: re-signed and verified with lib.agent_card_verify OK", file=sys.stderr)

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
