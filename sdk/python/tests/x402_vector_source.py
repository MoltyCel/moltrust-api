"""Source of tests/x402-vectors.json.

The vectors are the receiver-side cases for x402 payments, shared with the TypeScript
port. `python tests/x402_vector_source.py` rewrites the JSON file; `test_x402_gate.py`
fails when the file and this source disagree.

Every vector is a full x402 V2 payment payload as it arrives (decoded from the
PAYMENT-SIGNATURE header), the server's requirements, the mandate digest the receiver
pins, and the expected outcome. The EIP-3009 signature is a placeholder: checking it is
the facilitator's job, not this gate's.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from moltrust_enforce import action_digest, enforce_check  # noqa: E402
from moltrust_enforce.x402_gate import (  # noqa: E402
    RECEIPT_VERSION, X402_VERB, mandate_digest, x402_transaction,
)

OUT = HERE / "x402-vectors.json"

NETWORK = "eip155:8453"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
PAY_TO = "0x1111111111111111111111111111111111111111"
PAYER = "0x2222222222222222222222222222222222222222"
OTHER = "0x3333333333333333333333333333333333333333"
NONCE = "0x" + "ab" * 32
NONCE_2 = "0x" + "cd" * 32

ACTION = {"verb": X402_VERB, "scheme": "exact", "network": NETWORK, "asset": USDC}
FIELDS = ["verb", "scheme", "network", "asset"]


def mandate(disposition: str = "allow") -> dict:
    return {"grants": [{
        "action_binding": action_digest(ACTION),
        "type_fields": FIELDS,
        "disposition": disposition,
        "constraints": [
            {"type": "exact", "field": "to", "value": PAY_TO},
            {"type": "exact", "field": "from", "value": PAYER},
            {"type": "range", "field": "amount", "lo": 1, "hi": 5_000_000},
        ],
    }]}


def requirements(amount: str = "10000", pay_to: str = PAY_TO, asset: str = USDC,
                 network: str = NETWORK) -> dict:
    return {"scheme": "exact", "network": network, "asset": asset, "amount": amount,
            "payTo": pay_to, "maxTimeoutSeconds": 60,
            "extra": {"name": "USD Coin", "version": "2"}}


def payment(amount: str = "10000", pay_to: str = PAY_TO, asset: str = USDC,
            network: str = NETWORK, nonce: str = NONCE) -> dict:
    return {
        "x402Version": 2,
        "resource": {"url": "https://merchant.example/report", "description": "report",
                     "mimeType": "application/json"},
        "accepted": requirements(amount, pay_to, asset, network),
        "payload": {
            "signature": "0x" + "00" * 65,
            "authorization": {"from": PAYER, "to": pay_to, "value": amount,
                              "validAfter": "0", "validBefore": "4102444800",
                              "nonce": nonce},
        },
    }


def receipt_for(m: dict, pay: dict) -> dict:
    """What an agent sends: the record /enforce/check returns for this exact payment."""
    rec = enforce_check(m, x402_transaction(pay))
    return {"version": RECEIPT_VERSION, "mandate": m,
            "record": {"core": rec["core"], "core_digest": rec["core_digest"]}}


def with_receipt(pay: dict, receipt) -> dict:
    out = copy.deepcopy(pay)
    out["extensions"] = {"moltrust": receipt}
    return out


def build() -> dict:
    allow = mandate("allow")
    base = payment()
    good = receipt_for(allow, base)
    vectors = []

    def add(vid, case, desc, pay, reqs, expect, reason, pinned=allow):
        vectors.append({"id": vid, "case": case, "description": desc,
                        "payment_payload": pay, "requirements": reqs,
                        "accepted_mandate_digests": [mandate_digest(pinned)],
                        "expect": expect, "reason": reason})

    add("a-no-receipt", "a", "payment without a receipt", base, requirements(),
        "abort", "receipt_missing")

    for vid, kw in (("b-other-amount", {"amount": "20000"}),
                    ("b-other-payee", {"pay_to": OTHER}),
                    ("b-other-asset", {"asset": OTHER}),
                    ("b-other-network", {"network": "eip155:84532"}),
                    ("b-other-nonce", {"nonce": NONCE_2})):
        other = payment(**kw)
        reqs = requirements(**{k: v for k, v in kw.items() if k != "nonce"})
        add(vid, "b", f"valid receipt for a different payment ({vid[2:]})",
            with_receipt(other, good), reqs, "abort", "receipt_not_bound")

    for disp in ("hold", "forbid"):
        m = mandate(disp)
        add(f"c-{disp}", "c", f"receipt whose verdict comes from a {disp} grant",
            with_receipt(base, receipt_for(m, base)), requirements(), "abort",
            "verdict_not_permit", pinned=m)

    tampered = copy.deepcopy(good)
    tampered["record"]["core_digest"] = "sha256:" + "0" * 64
    add("d-digest-replaced", "d", "core_digest replaced", with_receipt(base, tampered),
        requirements(), "abort", "receipt_tampered")
    tampered = copy.deepcopy(good)
    tampered["record"]["core"]["verdict"] = "PERMIT"
    tampered["record"]["core"]["mandate_digest"] = "sha256:" + "1" * 64
    add("d-core-edited", "d", "core edited, core_digest left as it was",
        with_receipt(base, tampered), requirements(), "abort", "receipt_tampered")

    add("e-bound-allow", "e", "valid allow receipt bound to this payment",
        with_receipt(base, good), requirements(), "pass", "ok")

    # Fail-closed extras: parse and shape errors.
    add("f-receipt-not-object", "f", "receipt is a string", with_receipt(base, "allow"),
        requirements(), "abort", "receipt_invalid")
    bad = copy.deepcopy(good)
    bad["version"] = "moltrust-x402/v0"
    add("f-receipt-version", "f", "unknown receipt version", with_receipt(base, bad),
        requirements(), "abort", "receipt_invalid")
    bad = copy.deepcopy(good)
    del bad["mandate"]
    add("f-no-mandate", "f", "receipt without mandate", with_receipt(base, bad),
        requirements(), "abort", "receipt_invalid")
    add("f-mandate-not-pinned", "f", "mandate the receiver does not accept",
        with_receipt(base, good), requirements(), "abort", "mandate_not_accepted",
        pinned=mandate("hold"))
    pay = with_receipt(base, good)
    pay["payload"]["authorization"]["value"] = "20000"
    add("f-auth-differs", "f", "authorization.value differs from accepted.amount", pay,
        requirements(), "abort", "payment_unreadable")
    add("f-requirements-differ", "f", "server requirements differ from accepted",
        with_receipt(base, good), requirements(amount="5000"), "abort",
        "payment_unreadable")
    pay = with_receipt(base, good)
    pay["x402Version"] = 1
    add("f-v1", "f", "x402 version 1 payload", pay, requirements(), "abort",
        "payment_unreadable")
    pay = with_receipt(base, good)
    pay["accepted"]["scheme"] = "upto"
    add("f-scheme", "f", "scheme other than exact", pay, requirements(), "abort",
        "payment_unreadable")
    pay = with_receipt(base, good)
    pay["payload"]["authorization"]["value"] = "9007199254740992"
    pay["accepted"]["amount"] = "9007199254740992"
    add("f-amount-too-large", "f", "amount beyond 2**53-1", pay,
        requirements(amount="9007199254740992"), "abort", "payment_unreadable")

    return {
        "description": "Receiver-side x402 gate vectors (moltrust-enforce x402_gate). "
                       "Each payment_payload is the decoded PAYMENT-SIGNATURE body.",
        "receipt_version": RECEIPT_VERSION,
        "vectors": vectors,
    }


def dump(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    OUT.write_text(dump(build()), encoding="utf-8")
    print(f"wrote {OUT} ({len(build()['vectors'])} vectors)")
