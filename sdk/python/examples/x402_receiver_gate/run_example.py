"""Receiver-side gate for x402, end to end, offline.

    pip install moltrust-enforce          # or: pip install -e sdk/python
    python examples/x402_receiver_gate/run_example.py

Plays both sides of one x402 payment on Base USDC:

1. the agent builds its payment, derives the transaction from it, gets a verdict and
   builds the PAYMENT-SIGNATURE header with the receipt inside;
2. the receiver decodes the header and runs the hook it would register with
   x402ResourceServer.on_before_verify / on_before_settle.

The verdict comes from the local `enforce_check`, which is the same function the
/enforce/check endpoint runs, so the example needs no API key and no network. With a key,
replace `local_check` by `EnforceClient(...).check(mandate, transaction)`.
Exit code 0 means every expectation held.
"""
from __future__ import annotations

import copy
import sys

from moltrust_enforce import action_digest, enforce_check
from moltrust_enforce.x402_gate import (
    X402_VERB, build_x402_headers, decode_payment_header, mandate_digest,
    require_moltrust_verdict, trust_mandates, x402_transaction,
)

NETWORK = "eip155:8453"                                     # Base mainnet
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"         # USDC on Base, lowercase
MERCHANT = "0x1111111111111111111111111111111111111111"
AGENT_WALLET = "0x2222222222222222222222222222222222222222"

# --- the mandate: this agent may pay this merchant up to 5 USDC per payment ----------
ACTION = {"verb": X402_VERB, "scheme": "exact", "network": NETWORK, "asset": USDC}
MANDATE = {"grants": [{
    "action_binding": action_digest(ACTION),
    "type_fields": ["verb", "scheme", "network", "asset"],
    "disposition": "allow",
    "constraints": [
        {"type": "exact", "field": "to", "value": MERCHANT},
        {"type": "exact", "field": "from", "value": AGENT_WALLET},
        {"type": "range", "field": "amount", "lo": 1, "hi": 5_000_000},
    ],
}]}

# --- what the merchant asks for (its 402 response) -----------------------------------
REQUIREMENTS = {"scheme": "exact", "network": NETWORK, "asset": USDC, "amount": "10000",
                "payTo": MERCHANT, "maxTimeoutSeconds": 60,
                "extra": {"name": "USD Coin", "version": "2"}}


def signed_payment(nonce: str) -> dict:
    """The x402 V2 payload the agent's x402 client signs. Signature is a placeholder:
    checking it is the facilitator's job."""
    return {"x402Version": 2, "accepted": dict(REQUIREMENTS),
            "payload": {"signature": "0x" + "00" * 65,
                        "authorization": {"from": AGENT_WALLET, "to": MERCHANT,
                                          "value": "10000", "validAfter": "0",
                                          "validBefore": "4102444800", "nonce": nonce}}}


def local_check(mandate: dict, transaction: dict) -> dict:
    """Same body POST /enforce/check returns."""
    rec = enforce_check(mandate, transaction)
    return {"verdict": rec["verdict"], "reason": rec["reason"],
            "record": {"core": rec["core"], "core_digest": rec["core_digest"]}}


def main() -> int:
    ok = True

    # ---------------- agent side
    payment = signed_payment("0x" + "ab" * 32)
    transaction = x402_transaction(payment)
    response = local_check(MANDATE, transaction)
    print(f"agent:    verdict {response['verdict']}, core_digest "
          f"{response['record']['core_digest'][:23]}...")
    headers = build_x402_headers(MANDATE, payment, response)
    print(f"agent:    PAYMENT-SIGNATURE header, {len(headers['PAYMENT-SIGNATURE'])} chars")

    # ---------------- receiver side
    pinned = mandate_digest(MANDATE)
    hook = require_moltrust_verdict(accept_mandate=trust_mandates(pinned))

    class Ctx:                       # stands in for x402's VerifyContext / SettleContext
        def __init__(self, payload, requirements):
            self.payment_payload, self.requirements = payload, requirements

    def run(label, payload, requirements, expect_pass):
        nonlocal ok
        out = hook(Ctx(payload, requirements))
        passed = out is None
        verdict = "PASS " if passed else "ABORT"
        mark = "ok" if passed == expect_pass else "UNEXPECTED"
        print(f"receiver: {verdict} {label:38s} {'' if passed else out.reason}  [{mark}]")
        ok = ok and passed == expect_pass

    received = decode_payment_header(headers["PAYMENT-SIGNATURE"])
    run("bound allow receipt", received, REQUIREMENTS, True)

    no_receipt = copy.deepcopy(received)
    del no_receipt["extensions"]
    run("no receipt", no_receipt, REQUIREMENTS, False)

    replayed = copy.deepcopy(received)
    replayed["payload"]["authorization"]["nonce"] = "0x" + "cd" * 32
    run("same receipt, another payment", replayed, REQUIREMENTS, False)

    tampered = copy.deepcopy(received)
    tampered["extensions"]["moltrust"]["record"]["core_digest"] = "sha256:" + "0" * 64
    run("tampered core_digest", tampered, REQUIREMENTS, False)

    print("result:   all expectations held" if ok else "result:   FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
