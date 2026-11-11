"""MolTrust Ads demo: eight agent tool calls against one ads mandate.

    pip install "moltrust-enforce[client]"
    python demo.py --out ./demo-output            # kernel in-process, no network
    python demo.py --out ./demo-output --live     # verdicts from api.moltrust.ch
                                                  # (needs MOLTRUST_API_KEY, see below)
    python demo.py --out ./demo-output --clean    # replace an earlier run's files
    python demo.py --out ./demo-output --clean    # remove this script's earlier files first

Every scenario writes `<n>-<name>.json` (record, bundle, mandate, transaction) and the
script then runs the offline verifier over each of them, the same check as

    moltrust-verify --input demo-output/01-budget-in-range.json --trust-list demo-output/trust.json

The Amazon side is a stub. It never talks to Amazon: it records the call and returns a
canned answer. The request shapes follow Amazon's public MCP tool schemas; Amazon does not
publish response schemas for the MCP tools, so the stub's answer is illustrative.

The live endpoint answers ads mandates only for an API key: export MOLTRUST_API_KEY
(free key: POST https://api.moltrust.ch/auth/signup with {"email": "..."}). Without it,
--live sends the demo DID header, and api.moltrust.ch refuses every ads check with 401.
The free plan covers one ad account and 1,000 ads verdicts per month; one --live run uses 9.

Keys in this file are demo keys derived from fixed seeds. They belong to nobody and must
not be used anywhere else.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from moltrust_enforce import (PAYLOAD_TYPE, enforce_check, evidence_payload_bytes,
                              make_envelope, pae)
from moltrust_enforce._ratify_core import ratify, statement_bytes
from moltrust_enforce.adapters.amazon_ads import (
    DOCUMENTED_TOOLS, AdsProfile, AmazonAdsGuard, Forbidden, HoldRequired,
)
from moltrust_enforce.client import EnforceClient
from moltrust_enforce.verify import verify_record


def demo_key(name):
    return Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(b"moltrust:ads-demo:" + name.encode()).digest())


AGENCY, FINANCE = demo_key("agency"), demo_key("finance")
AGENCY_DID = "did:moltrust:ad5de00000a6e0c1"
FINANCE_DID = "did:moltrust:ad5de0000f1a0ce1"
ACCOUNT = {"advertiserAccountId": "ENTITYDEMO123"}
NOW = dt.datetime(2026, 10, 12, 14, 0, tzinfo=dt.timezone.utc)

PROFILE = AdsProfile(
    mandate_id="demo-agency-acme-2026q4",
    principal={"did": AGENCY_DID,
               "public_key": AGENCY.public_key().public_bytes_raw().hex()},
    ratification_authorities=({"did": FINANCE_DID, "role": "agency_finance",
                               "public_key": FINANCE.public_key().public_bytes_raw().hex()},),
    tools=dict(DOCUMENTED_TOOLS),
    account_ids=("ENTITYDEMO123",),
    brands=("Acme Outdoor",),
    daily_budget_cap=50_000,          # USD 500 per campaign and day
    hold_threshold=20_000,            # above USD 200 a person at the agency approves
    total_daily_budget_cap=200_000,   # USD 2,000 per account and day
    period_budget_cap=1_500_000,      # USD 15,000 lifetime or monthly
    max_bid=500,                      # USD 5.00
    max_bid_change_bp=2_500,          # +/- 25 % per change
    valid_from="2026-10-01T00:00:00Z",
    valid_until="2026-12-31T23:59:59Z",
)

# What the agency's own system knows about the account. In production this comes from the
# agency's reporting store, not from the model.
STATE = {
    "campaigns": {"CAMP-ACME-1": {"brand": "Acme Outdoor", "daily_budget": 12_000},
                  "CAMP-ACME-2": {"brand": "Acme Outdoor", "daily_budget": 15_000},
                  "CAMP-RIVAL-7": {"brand": "Rival Gear", "daily_budget": 30_000}},
    "targets": {"TGT-ACME-KW-1": {"brand": "Acme Outdoor", "bid": 120}},
    "account_daily_total": 57_000,
}


def budget(campaign, value, period="DAILY"):
    return {"body": {"accessRequestedAccount": ACCOUNT, "campaigns": [{
        "campaignId": campaign,
        "budgets": [{"budgetType": "MONETARY", "recurrenceTimePeriod": period,
                     "budgetValue": {"monetaryBudgetValue": {
                         "monetaryBudget": {"value": value}}}}]}]}}


SCENARIOS = [
    ("budget-in-range", "Daily budget USD 180 on an Acme campaign",
     "campaign_management-update_campaign_budget", budget("CAMP-ACME-1", 180.0), NOW, "allow"),
    ("budget-hold-ratified", "Daily budget USD 350: hold, finance approves, then it runs",
     "campaign_management-update_campaign_budget", budget("CAMP-ACME-2", 350.0), NOW,
     "hold"),
    ("budget-over-cap", "Daily budget USD 5,000 (typo for 500)",
     "campaign_management-update_campaign_budget", budget("CAMP-ACME-1", 5000.0), NOW,
     "forbid"),
    ("foreign-brand", "Budget change on another client's campaign",
     "campaign_management-update_campaign_budget", budget("CAMP-RIVAL-7", 100.0), NOW,
     "forbid"),
    ("mandate-expired", "Same change as scenario 1, on 2 January 2027",
     "campaign_management-update_campaign_budget", budget("CAMP-ACME-1", 180.0),
     dt.datetime(2027, 1, 2, 9, 0, tzinfo=dt.timezone.utc), "forbid"),
    ("user-permissions", "Agent tries to grant a user access to the ad account",
     "user_permissions-update_user_permissions",
     {"body": {"accessRequestedAccount": ACCOUNT,
               "userPermissions": [{"userId": "someone", "permissions": ["ADMIN"]}]}},
     NOW, "forbid"),
    ("bid-plus-60-percent", "Keyword bid USD 1.20 -> 1.92 (+60 %)",
     "campaign_management-update_target_bid",
     {"body": {"accessRequestedAccount": ACCOUNT,
               "targets": [{"targetId": "TGT-ACME-KW-1", "bid": {"bid": 1.92}}]}},
     NOW, "forbid"),
    ("lite-invoke-delete", "Lite endpoint: invoke_tool wraps delete_campaign",
     "amazon_ads_mcp-invoke_tool",
     {"toolName": "campaign_management-delete_campaign",
      "arguments": {"body": {"accessRequestedAccount": ACCOUNT,
                             "campaignIds": ["CAMP-ACME-1"]}}}, NOW, "forbid"),
]


def attest(statement):
    sig = AGENCY.sign(pae(PAYLOAD_TYPE, evidence_payload_bytes(statement)))
    return make_envelope(statement, [{"keyid": "agency-demo-1",
                                      "sig": base64.b64encode(sig).decode("ascii")}])


TRUST = {"trust_list_version": 1, "sources": {AGENCY_DID: {"keys": [{
    "algorithm": "ed25519", "keyid": "agency-demo-1",
    "public_key": base64.b64encode(AGENCY.public_key().public_bytes_raw()).decode()}]}}}


def finance_approves(outcome, res):
    sig = FINANCE.sign(statement_bytes(res.verdict.core_digest, "APPROVED", FINANCE_DID)).hex()
    return "APPROVED", {"mandate": outcome.mandate, "authority": FINANCE_DID, "signature": sig}


def local_transport():
    """The real kernel in-process, answering in the endpoint's shape."""
    def handler(request):
        body = json.loads(request.content)
        if request.url.path == "/enforce/check":
            r = enforce_check(body.get("mandate"), body.get("transaction"))
            return httpx.Response(200, json={
                "verdict": r["verdict"], "reason": r["reason"], "grant_index": r["grant_index"],
                "trace": r["trace"], "record": {"core": r["core"], "core_digest": r["core_digest"]}})
        r = ratify(body.get("prior_record"), body.get("decision"), body.get("authority_proof"))
        return httpx.Response(200, json={
            "status": r["status"], "decision": r["decision"], "ratifies": r["ratifies"],
            "authority": r["authority"], "reason": r["reason"], "trace": r["trace"],
            "record": {"core": r["core"], "core_digest": r["core_digest"]}})
    return httpx.MockTransport(handler)


class AmazonStub:
    """Stands in for the agency's own Amazon Ads MCP client. Never contacts Amazon."""

    def __init__(self):
        self.calls = []

    def __call__(self, tool, args):
        self.calls.append(tool)
        return {"stub": True, "tool": tool, "note": "illustrative; not an Amazon response"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="demo-output")
    ap.add_argument("--live", action="store_true", help="ask api.moltrust.ch for verdicts")
    ap.add_argument("--base-url", default="https://api.moltrust.ch")
    ap.add_argument("--clean", action="store_true",
                    help="remove files this script wrote earlier (*.json, demo-output.txt)")
    args = ap.parse_args(argv)

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.clean:
        for old in list(out.glob("[0-9][0-9]-*.json")) + [out / "trust.json",
                                                          out / "demo-output.txt"]:
            if old.is_file():
                old.unlink()
    (out / "trust.json").write_text(json.dumps(TRUST, indent=2) + "\n")
    if args.live:
        api_key = os.environ.get("MOLTRUST_API_KEY", "").strip()
        if api_key:
            client = EnforceClient(args.base_url, api_key=api_key)
        else:
            sys.stderr.write("MOLTRUST_API_KEY not set: sending the demo DID, which the live "
                             "endpoint refuses for ads mandates (HTTP 401).\n")
            client = EnforceClient(args.base_url, did=AGENCY_DID)
    else:
        client = EnforceClient("https://local.kernel", api_key="demo",
                               transport=local_transport())

    amazon = AmazonStub()
    lines = [f"MolTrust Ads demo — verdicts from {'api.moltrust.ch' if args.live else 'the in-process kernel'}",
             f"mandate {PROFILE.mandate_id}: account ENTITYDEMO123, brand Acme Outdoor, "
             "daily cap USD 500, hold above USD 200, bids +/-25 %, valid until 2026-12-31", ""]
    failures = 0
    for n, (slug, title, tool, call_args, when, expected) in enumerate(SCENARIOS, 1):
        guard = AmazonAdsGuard(client, PROFILE, approver=finance_approves, attest=attest,
                               clock=lambda when=when: when)
        before = len(amazon.calls)
        try:
            outcome = guard.call(tool, call_args, amazon, STATE)
        except (Forbidden, HoldRequired) as exc:
            outcome = exc.outcome
        ran = len(amazon.calls) > before
        ratified = [c for c in outcome.checks if c.ratification is not None]
        verified = [verify_record(c.aer_record, c.aer_bundle, outcome.mandate, c.transaction,
                                  TRUST) for c in outcome.checks]
        ok_aer = all(v.ok for v in verified)
        ok = outcome.disposition == expected and ok_aer and \
            ran == (expected == "allow" or (expected == "hold" and bool(ratified)))
        failures += 0 if ok else 1

        for i, c in enumerate(outcome.checks):
            suffix = f"-{i + 1}" if len(outcome.checks) > 1 else ""
            (out / f"{n:02d}-{slug}{suffix}.json").write_text(json.dumps({
                "scenario": title, "tool": outcome.tool, "change": c.check.change,
                "disposition": c.disposition, "reason": c.reason,
                "ratification": None if c.ratification is None else {
                    "status": c.ratification.status, "decision": c.ratification.decision,
                    "core_digest": c.ratification.core_digest},
                "record": c.aer_record, "bundle": c.aer_bundle,
                "mandate": outcome.mandate, "transaction": c.transaction}, indent=2) + "\n")

        verdict = outcome.disposition.upper()
        if ratified:
            verdict += f" -> ratified by finance ({ratified[0].ratification.status})"
        lines.append(f"{n}. {title}")
        lines.append(f"   tool      {outcome.tool}")
        lines.append(f"   verdict   {verdict}")
        lines.append(f"   reason    {outcome.reason}")
        lines.append(f"   Amazon    {'call ran' if ran else 'call did not run'}")
        lines.append(f"   AER       {'V1-V4 pass' if ok_aer else 'VERIFY FAILED'} "
                     f"({len(outcome.checks)} record{'s' if len(outcome.checks) > 1 else ''})")
        lines.append(f"   expected  {expected} -> {'ok' if ok else 'MISMATCH'}")
        lines.append("")

    lines.append(f"{len(SCENARIOS) - failures}/{len(SCENARIOS)} scenarios as expected; "
                 f"Amazon stub called {len(amazon.calls)} times")
    text = "\n".join(lines) + "\n"
    (out / "demo-output.txt").write_text(text)
    sys.stdout.write(text)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
