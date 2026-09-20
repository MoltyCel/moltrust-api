#!/usr/bin/env python3
"""Pay one of our own x402 endpoints from the test wallet, for real, on mainnet.

A 402 challenge can be inspected without spending anything, and that is how it
was checked until now. It is not enough for discovery: a facilitator catalogues
an endpoint from the PaymentPayload it receives at /settle, so an endpoint
nobody has ever paid for stays invisible however complete its challenge. One
real settlement is the only thing that registers it.

The payer signs an EIP-3009 authorization and the facilitator broadcasts it, so
this spends USDC and no gas.

    python3 scripts/x402_self_payment.py --path /api/agent/score/0x…
    python3 scripts/x402_self_payment.py --path … --dry-run

Reads BASE_ANCHOR_KEY. That wallet — 0xd8f5bB747f7459BF3e1cc1aD041E2cA57B946C38
— is the only signer the console may use unattended, and only for x402/funnel
tests and bounty payouts. Every transaction belongs in the report and in
Telegram with hash, amount and purpose. Nothing here touches any other key.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

# The exception covers this address and no other. Signing with a key that
# resolves to anything else is a rule break, so it is checked rather than
# assumed — an env file edited by hand is exactly how the wrong key gets used.
TEST_WALLET = "0xd8f5bB747f7459BF3e1cc1aD041E2cA57B946C38"

# Per-run ceiling, independent of the cumulative cap the operator tracks. A
# challenge that asks for more than this is not paid; it is reported. The point
# is that a misconfigured price cannot quietly drain the allowance.
MAX_USDC_PER_RUN = 0.10

USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
CHAIN_ID = 8453
BASE_URL = "https://api.moltrust.ch/guard"

UA = {"User-Agent": "moltrust-self-payment/1.0 (+https://moltrust.ch)",
      "Accept": "application/json"}


def _get(url: str):
    req = urllib.request.Request(url, headers=dict(UA))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 — https literal above  # nosec B310 - BASE_URL is a module constant and the path is an operator argument
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:400].decode("utf-8", "replace")}


def _get_paid(url: str, header: str):
    h = dict(UA)
    h["PAYMENT-SIGNATURE"] = header
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 — https literal above  # nosec B310 - same URL as the unpaid probe above
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:600].decode("utf-8", "replace")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True,
                    help="Endpoint path below /guard, e.g. /api/agent/score/0x…")
    ap.add_argument("--dry-run", action="store_true",
                    help="Read the challenge and build the authorization, sign nothing.")
    args = ap.parse_args()

    url = BASE_URL + args.path
    status, body = _get(url)
    if status != 402:
        print(f"Kein 402 — {status}. Entweder ist der Endpoint frei oder x402 ist aus.")
        print(json.dumps(body)[:400])
        return 1

    x402 = body.get("x402") or {}
    accepts = x402.get("accepts") or []
    if not accepts:
        print("402 ohne accepts — nichts zu bezahlen.")
        return 1
    offer = accepts[0]

    # Everything below is checked against the challenge rather than hardcoded,
    # except the things that must not move: the asset, the chain, and the cap.
    if offer.get("scheme") != "exact":
        print(f"Schema {offer.get('scheme')!r} wird hier nicht unterstuetzt.")
        return 1
    if offer.get("network") != f"eip155:{CHAIN_ID}":
        print(f"Netz {offer.get('network')!r} ist nicht Base mainnet.")
        return 1
    if (offer.get("asset") or "").lower() != USDC_BASE.lower():
        print(f"Asset {offer.get('asset')!r} ist nicht USDC auf Base.")
        return 1

    amount_units = int(offer["amount"])
    amount_usdc = amount_units / 10 ** 6
    if amount_usdc > MAX_USDC_PER_RUN:
        print(f"Gefordert {amount_usdc} USDC, Obergrenze pro Lauf {MAX_USDC_PER_RUN}. "
              "Nicht gezahlt.")
        return 1

    bazaar = (x402.get("extensions") or {}).get("bazaar")
    print(f"Endpoint      : {url}")
    print(f"payTo         : {offer['payTo']}")
    print(f"Betrag        : {amount_usdc} USDC ({amount_units} Basiseinheiten)")
    print(f"bazaar        : {'vorhanden' if bazaar else 'FEHLT — nichts zu katalogisieren'}")
    if bazaar:
        print(f"  routeTemplate: {bazaar.get('routeTemplate', '(statisch)')}")
        print(f"  method       : {(bazaar.get('info') or {}).get('input', {}).get('method')}")

    key = os.environ.get("BASE_ANCHOR_KEY", "").strip()
    if not key:
        print("BASE_ANCHOR_KEY ist nicht gesetzt.")
        return 1

    from eth_account import Account
    from eth_account.messages import encode_typed_data

    acct = Account.from_key(key if key.startswith("0x") else "0x" + key)
    if acct.address.lower() != TEST_WALLET.lower():
        print(f"BASE_ANCHOR_KEY ergibt {acct.address}, erwartet {TEST_WALLET}. "
              "Abbruch — nur diese Adresse ist freigegeben.")
        return 1
    print(f"Zahler        : {acct.address}")

    now = int(time.time())
    authorization = {
        "from": acct.address,
        "to": offer["payTo"],
        "value": str(amount_units),
        # A minute back absorbs clock skew between us and the node that will
        # check validAfter; the window is short enough that an unused
        # authorization expires rather than sitting around signed.
        "validAfter": str(now - 60),
        "validBefore": str(now + int(offer.get("maxTimeoutSeconds") or 300) + 60),
        "nonce": "0x" + secrets.token_hex(32),
    }

    if args.dry_run:
        print("\n--dry-run: Autorisierung gebaut, nichts signiert, nichts gesendet.")
        print(json.dumps(authorization, indent=2))
        return 0

    extra = offer.get("extra") or {}
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "primaryType": "TransferWithAuthorization",
        # The domain comes from the challenge's `extra`, which is where the
        # token's EIP-712 name and version are advertised. Guessing them yields
        # a signature the token rejects with no useful error.
        "domain": {
            "name": extra.get("name", "USD Coin"),
            "version": extra.get("version", "2"),
            "chainId": CHAIN_ID,
            "verifyingContract": offer["asset"],
        },
        "message": {
            "from": authorization["from"],
            "to": authorization["to"],
            "value": int(authorization["value"]),
            "validAfter": int(authorization["validAfter"]),
            "validBefore": int(authorization["validBefore"]),
            "nonce": bytes.fromhex(authorization["nonce"][2:]),
        },
    }
    signed = Account.sign_message(encode_typed_data(full_message=typed), acct.key)

    payload = {
        "x402Version": 2,
        "resource": x402.get("resource"),
        "accepted": offer,
        "payload": {"signature": signed.signature.hex()
                    if signed.signature.hex().startswith("0x")
                    else "0x" + signed.signature.hex(),
                    "authorization": authorization},
        # Echoed back as the spec expects. The server rebuilds it anyway rather
        # than trusting this copy, so it is a courtesy and a check: if what
        # comes back differs from what we sent, one of us is wrong.
        **({"extensions": {"bazaar": bazaar}} if bazaar else {}),
    }
    header = "x402 " + base64.b64encode(json.dumps(payload).encode()).decode()

    print(f"\nNonce         : {authorization['nonce']}")
    print("Sende Zahlung …")
    status, resp = _get_paid(url, header)
    print(f"Antwort       : HTTP {status}")
    if status == 200:
        print("Bezahlt und ausgeliefert.")
        print(json.dumps(resp)[:500])
        return 0
    print(json.dumps(resp, indent=2)[:1200])
    return 1


if __name__ == "__main__":
    sys.exit(main())
