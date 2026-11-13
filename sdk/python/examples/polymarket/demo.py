"""MolCheck before Polymarket orders: a demo without real money.

    pip install "moltrust-enforce[client]"
    python demo.py --out ./demo-output

Nothing here touches money, a wallet or Polymarket. The kernel runs in-process behind an
httpx.MockTransport, and the signer is a stub that records the order it would have signed
and returns a canned answer. Every scenario writes `<n>-<disposition>-<id>.json` (the
MolCheck record, its bundle, mandate, transaction, order and commitment), and the script
then runs the offline verifier over each of them, the same check as

    moltrust-verify --input demo-output/001-allow-buy-in-band.json \\
                    --trust-list demo-output/trust.json

The token ids, addresses and keys are demo values derived from fixed seeds. They belong to
nobody and must not be used anywhere else.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from moltrust_enforce import enforce_check
from moltrust_enforce._ratify_core import ratify
from moltrust_enforce.adapters.polymarket import (
    CommitmentParties, PolymarketOrderGuard, PolymarketProfile, ed25519_attester,
    ratification_proof, trust_list_for, verify_commitment,
)
from moltrust_enforce.cli import main as verify_cli
from moltrust_enforce.client import EnforceClient


def demo_key(name):
    return Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(b"moltrust:polymarket-demo:" + name.encode()).digest())


def demo_token(name):
    return str(int.from_bytes(hashlib.sha256(name.encode()).digest(), "big"))


AGENT, RISK = demo_key("agent"), demo_key("risk")
AGENT_DID = "did:moltrust:0000de000000a6e7"
RISK_DID = "did:moltrust:0000de0000000415"
YES, NO, ELSEWHERE = demo_token("demo-yes"), demo_token("demo-no"), demo_token("elsewhere")
SIGNER = "0x" + hashlib.sha256(b"demo-signer").hexdigest()[:40]
MAKER = "0x" + hashlib.sha256(b"demo-maker").hexdigest()[:40]
NOW = dt.datetime(2026, 11, 13, 12, 0, tzinfo=dt.timezone.utc)

PROFILE = PolymarketProfile(
    mandate_id="pm-demo-1",
    principal={"did": AGENT_DID, "public_key": AGENT.public_key().public_bytes_raw().hex()},
    ratification_authorities=({"did": RISK_DID, "role": "risk_desk",
                               "public_key": RISK.public_key().public_bytes_raw().hex()},),
    markets=(YES, NO), sides=("BUY",), max_size="100", min_price="0.10", max_price="0.90",
    max_market_day_exposure="25.00", max_day_exposure="40.00", hold_above="10.00",
    valid_from="2026-11-01T00:00:00Z", valid_until="2026-12-31T23:59:59Z",
)

SCENARIOS = [
    ("buy-in-band", {"token_id": YES, "side": "BUY", "price": "0.42", "size": "20"}),
    ("price-below-band", {"token_id": YES, "side": "BUY", "price": "0.05", "size": "20"}),
    ("market-not-in-mandate", {"token_id": ELSEWHERE, "side": "BUY", "price": "0.50",
                               "size": "5"}),
    ("sell-not-allowed", {"token_id": NO, "side": "SELL", "price": "0.60", "size": "5"}),
    ("hold-approved", {"token_id": NO, "side": "BUY", "price": "0.60", "size": "20"}),
    ("market-day-cap", {"token_id": YES, "side": "BUY", "price": "0.80", "size": "30"}),
]


def kernel():
    def handler(request):
        body = json.loads(request.content)
        if request.url.path == "/enforce/check":
            r = enforce_check(body.get("mandate"), body.get("transaction"))
            return httpx.Response(200, json={
                "verdict": r["verdict"], "reason": r["reason"], "grant_index": r["grant_index"],
                "trace": r["trace"], "record": {"core": r["core"],
                                                "core_digest": r["core_digest"]}})
        r = ratify(body.get("prior_record"), body.get("decision"), body.get("authority_proof"))
        return httpx.Response(200, json={
            "status": r["status"], "decision": r["decision"], "ratifies": r["ratifies"],
            "authority": r["authority"], "reason": r["reason"], "trace": r["trace"],
            "record": {"core": r["core"], "core_digest": r["core_digest"]}})
    return httpx.MockTransport(handler)


def run(out_dir: pathlib.Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    signed = []

    def stub_signer(order):
        signed.append(order)
        return {"stub": True, "would_sign": order}

    def risk_desk(outcome):
        return ratification_proof(outcome, True, RISK_DID, RISK)

    guard = PolymarketOrderGuard(
        EnforceClient("https://enforce.demo", api_key="demo", transport=kernel()),
        PROFILE, stub_signer, commitment=CommitmentParties(SIGNER, MAKER),
        approver=risk_desk, attest=ed25519_attester(AGENT_DID, AGENT, keyid="agent-1"),
        receipts_dir=str(out_dir), clock=lambda: NOW, nonce=lambda: "0x00c0ffee")
    trust = out_dir / "trust.json"
    trust.write_text(json.dumps(trust_list_for(AGENT_DID, AGENT.public_key(),
                                               keyid="agent-1")))
    failures = 0
    for name, order in SCENARIOS:
        out = guard.place_order(dict(order, client_order_id=name))
        rc = verify_cli(["--input", out.receipt_path, "--trust-list", str(trust)])
        committed = out.commitment is not None and verify_commitment(
            out.commitment["preimage"], out.commitment["metadata"])
        print(f"{name:24s} {out.disposition:6s} sent={str(out.sent):5s} "
              f"verify={'ok' if rc == 0 else 'FAIL'} commitment={'ok' if committed else '-'}"
              f"  {out.reason}")
        failures += rc != 0
    print(f"{len(signed)} order(s) handed to the stub signer, {failures} verify failure(s)")
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="demo-output", type=pathlib.Path)
    return run(ap.parse_args(argv).out)


if __name__ == "__main__":
    sys.exit(main())
