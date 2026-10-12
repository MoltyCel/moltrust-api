"""Amazon Ads adapter against the real kernel behind a mock transport.

The transport answers /enforce/check with `enforce_check` and /enforce/ratify with `ratify`,
in the endpoint's response shape, so the tests exercise EnforceClient as shipped.
"""
import datetime as dt
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import base64

from moltrust_enforce import (PAYLOAD_TYPE, enforce_check, evidence_payload_bytes,
                              make_envelope, pae)
from moltrust_enforce._ratify_core import ratify, statement_bytes
from moltrust_enforce.adapters.amazon_ads import (
    ALLOW, FORBID, HOLD, KIND_BID, KIND_BUDGET, KIND_READ, KIND_WRITE, AdsProfile,
    AmazonAdsGuard, Forbidden, HoldRequired, bid_change_bp, build_mandate,
)
from moltrust_enforce.client import EnforceClient

from conftest import as_endpoint_payload

PRINCIPAL_KEY = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32)
APPROVER_KEY = Ed25519PrivateKey.from_private_bytes(b"\x02" * 32)
PRINCIPAL_DID = "did:moltrust:aaaaaaaaaaaaaaaa"
APPROVER_DID = "did:moltrust:bbbbbbbbbbbbbbbb"

BUDGET_TOOL = "campaign_management-update_campaign_budget"
BID_TOOL = "campaign_management-update_target_bid"
WRITE_TOOL = "campaign_management-create_campaign"
READ_TOOL = "campaign_management-list_campaigns"

NOW = dt.datetime(2026, 10, 12, 12, 0, tzinfo=dt.timezone.utc)


def pub_hex(key):
    return key.public_key().public_bytes_raw().hex()


def profile(**over):
    p = dict(
        mandate_id="ads-test-1",
        principal={"did": PRINCIPAL_DID, "public_key": pub_hex(PRINCIPAL_KEY)},
        ratification_authorities=({"did": APPROVER_DID, "public_key": pub_hex(APPROVER_KEY),
                                   "role": "agency_finance"},),
        tools={BUDGET_TOOL: KIND_BUDGET, BID_TOOL: KIND_BID, WRITE_TOOL: KIND_WRITE,
               READ_TOOL: KIND_READ},
        account_ids=("ENTITY1ABC",),
        brands=("Acme",),
        daily_budget_cap=50_000,      # 500.00
        hold_threshold=20_000,        # 200.00
        total_budget_cap=200_000,     # 2,000.00 across campaigns
        max_bid_change_bp=2_500,      # +/- 25 %
        valid_from="2026-10-01T00:00:00Z",
        valid_until="2026-12-31T23:59:59Z",
    )
    p.update(over)
    return AdsProfile(**p)


def kernel_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/enforce/check":
            return httpx.Response(200, json=as_endpoint_payload(
                enforce_check(body.get("mandate"), body.get("transaction"))))
        if request.url.path == "/enforce/ratify":
            r = ratify(body.get("prior_record"), body.get("decision"),
                       body.get("authority_proof"))
            return httpx.Response(200, json={
                "status": r["status"], "decision": r["decision"], "ratifies": r["ratifies"],
                "authority": r["authority"], "reason": r["reason"], "trace": r["trace"],
                "record": {"core": r["core"], "core_digest": r["core_digest"]}})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def client():
    return EnforceClient("https://api.test", api_key="k", transport=kernel_transport())


def approver(decision="APPROVED", key=APPROVER_KEY, did=APPROVER_DID):
    def _approve(outcome):
        sig = key.sign(statement_bytes(outcome.verdict.core_digest, decision, did)).hex()
        return decision, {"mandate": outcome.mandate, "authority": did, "signature": sig}
    return _approve


def attest(statement):
    sig = PRINCIPAL_KEY.sign(pae(PAYLOAD_TYPE, evidence_payload_bytes(statement)))
    return make_envelope(statement, [{"keyid": "agency-1",
                                      "sig": base64.b64encode(sig).decode("ascii")}])


TRUST = {"trust_list_version": 1, "sources": {PRINCIPAL_DID: {"keys": [{
    "algorithm": "ed25519", "keyid": "agency-1",
    "public_key": base64.b64encode(PRINCIPAL_KEY.public_key().public_bytes_raw()).decode()}]}}}


def guard(p=None, clock_at=NOW, **kw):
    kw.setdefault("attest", attest)
    return AmazonAdsGuard(client(), p or profile(), clock=lambda: clock_at, **kw)


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, tool, args):
        self.calls.append((tool, dict(args)))
        return {"ok": True}


BUDGET_CTX = {"other_daily_budgets": 100_000}


def budget_args(amount):
    return {"advertiserAccountId": "ENTITY1ABC", "brand": "Acme", "dailyBudget": amount}


# ------------------------------------------------------------------- dispositions

def test_budget_within_threshold_is_allow_and_runs():
    rec = Recorder()
    out = guard().call(BUDGET_TOOL, budget_args(150.0), rec, BUDGET_CTX)
    assert out.disposition == ALLOW and out.executed and len(rec.calls) == 1


def test_budget_above_threshold_is_hold_and_does_not_run_without_approver():
    rec = Recorder()
    with pytest.raises(HoldRequired) as e:
        guard().call(BUDGET_TOOL, budget_args(300.0), rec, BUDGET_CTX)
    assert e.value.outcome.disposition == HOLD and rec.calls == []


def test_hold_ratified_runs():
    rec = Recorder()
    out = guard(approver=approver()).call(BUDGET_TOOL, budget_args(300.0), rec, BUDGET_CTX)
    assert out.disposition == HOLD and out.ratification.ratified and len(rec.calls) == 1


def test_hold_disapproved_does_not_run():
    rec = Recorder()
    with pytest.raises(HoldRequired):
        guard(approver=approver("DISAPPROVED")).call(BUDGET_TOOL, budget_args(300.0), rec,
                                                    BUDGET_CTX)
    assert rec.calls == []


def test_hold_signed_by_stranger_does_not_run():
    rec = Recorder()
    stranger = Ed25519PrivateKey.from_private_bytes(b"\x03" * 32)
    with pytest.raises(HoldRequired):
        guard(approver=approver(key=stranger)).call(BUDGET_TOOL, budget_args(300.0), rec,
                                                   BUDGET_CTX)
    assert rec.calls == []


def test_budget_above_cap_is_forbid():
    rec = Recorder()
    with pytest.raises(Forbidden):
        guard().call(BUDGET_TOOL, budget_args(500.01), rec, BUDGET_CTX)
    assert rec.calls == []


def test_budget_at_cap_is_hold_not_forbid():
    out = guard().decide(BUDGET_TOOL, budget_args(500.0), BUDGET_CTX)
    assert out.disposition == HOLD


def test_total_budget_cap_is_forbid():
    out = guard().decide(BUDGET_TOOL, budget_args(150.0), {"other_daily_budgets": 190_000})
    assert out.disposition == FORBID


def test_missing_total_is_forbid():
    # Without the caller's running total the range constraint has no value: DENY.
    out = guard().decide(BUDGET_TOOL, budget_args(150.0), {})
    assert out.disposition == FORBID


def test_foreign_brand_is_forbid():
    args = dict(budget_args(150.0), brand="OtherCo")
    assert guard().decide(BUDGET_TOOL, args, BUDGET_CTX).disposition == FORBID


def test_foreign_account_is_forbid():
    args = dict(budget_args(150.0), advertiserAccountId="ENTITY9XYZ")
    assert guard().decide(BUDGET_TOOL, args, BUDGET_CTX).disposition == FORBID


def test_expired_mandate_is_forbid():
    late = dt.datetime(2027, 1, 1, 0, 0, tzinfo=dt.timezone.utc)
    assert guard(clock_at=late).decide(BUDGET_TOOL, budget_args(150.0),
                                       BUDGET_CTX).disposition == FORBID


def test_not_yet_valid_mandate_is_forbid():
    early = dt.datetime(2026, 9, 30, 23, 59, tzinfo=dt.timezone.utc)
    assert guard(clock_at=early).decide(READ_TOOL, {"advertiserAccountId": "ENTITY1ABC"},
                                        {}).disposition == FORBID


def test_user_permissions_tool_is_forbid():
    rec = Recorder()
    with pytest.raises(Forbidden):
        guard().call("user_permissions-grant_access", {"advertiserAccountId": "ENTITY1ABC"},
                     rec)
    assert rec.calls == []


def test_unlisted_tool_is_forbid():
    out = guard().decide("campaign_management-delete_campaign",
                         {"advertiserAccountId": "ENTITY1ABC", "brand": "Acme"}, {})
    assert out.disposition == FORBID and "unaddressed" in out.reason


def test_bid_within_limit_is_allow_and_above_is_forbid():
    g = guard()
    ok = g.decide(BID_TOOL, {"advertiserAccountId": "ENTITY1ABC", "brand": "Acme",
                             "bid": 1.25}, {"current_bid": 100})
    bad = g.decide(BID_TOOL, {"advertiserAccountId": "ENTITY1ABC", "brand": "Acme",
                              "bid": 1.26}, {"current_bid": 100})
    assert ok.disposition == ALLOW and bad.disposition == FORBID


def test_bid_without_current_bid_is_forbid():
    out = guard().decide(BID_TOOL, {"advertiserAccountId": "ENTITY1ABC", "brand": "Acme",
                                    "bid": 1.0}, {})
    assert out.disposition == FORBID


def test_read_tool_is_allow():
    out = guard().decide(READ_TOOL, {"advertiserAccountId": "ENTITY1ABC"}, {})
    assert out.disposition == ALLOW


# ------------------------------------------------------------------- fail closed

def test_transport_failure_is_forbid_and_does_not_run():
    def boom(_r):
        raise httpx.ConnectError("down")
    g = AmazonAdsGuard(EnforceClient("https://api.test", api_key="k",
                                     transport=httpx.MockTransport(boom)),
                       profile(), clock=lambda: NOW)
    rec = Recorder()
    with pytest.raises(Forbidden):
        g.call(READ_TOOL, {"advertiserAccountId": "ENTITY1ABC"}, rec)
    assert rec.calls == []


def test_server_permit_that_does_not_recompute_is_forbid():
    def lying(request):
        body = json.loads(request.content)
        r = as_endpoint_payload(enforce_check(body["mandate"], body["transaction"]))
        r["verdict"] = "PERMIT"
        return httpx.Response(200, json=r)
    g = AmazonAdsGuard(EnforceClient("https://api.test", api_key="k",
                                     transport=httpx.MockTransport(lying)),
                       profile(), clock=lambda: NOW)
    out = g.decide(BUDGET_TOOL, budget_args(500.01), BUDGET_CTX)
    assert out.disposition == FORBID and "does not recompute" in out.reason


# ------------------------------------------------------------------- AER

@pytest.mark.parametrize("amount,expected", [(150.0, ALLOW), (300.0, HOLD), (600.0, FORBID)])
def test_every_decision_carries_a_verifying_aer_record(amount, expected):
    out = guard().decide(BUDGET_TOOL, budget_args(amount), BUDGET_CTX)
    assert out.disposition == expected
    result = out.aer_verify(TRUST)
    assert result.ok, result.failures
    assert result.recomputed_verdict == out.verdict.verdict
    assert out.aer_bundle["decision_timestamp"] == "2026-10-12T12:00:00Z"
    assert out.transaction["t_min"] * 60 == int(NOW.timestamp())


def test_tampered_aer_transaction_fails_verification():
    out = guard().decide(BUDGET_TOOL, budget_args(150.0), BUDGET_CTX)
    out.transaction = dict(out.transaction, daily_budget=1)
    assert not out.aer_verify(TRUST).ok


def test_aer_without_attester_recomputes_but_attests_nothing():
    out = guard(attest=None).decide(READ_TOOL, {"advertiserAccountId": "ENTITY1ABC"}, {})
    assert out.aer_bundle["items"] == []
    assert out.aer_verify(TRUST).ok


def test_aer_statement_from_untrusted_key_fails_v2():
    out = guard().decide(READ_TOOL, {"advertiserAccountId": "ENTITY1ABC"}, {})
    other = Ed25519PrivateKey.from_private_bytes(b"\x04" * 32)
    trust = {"trust_list_version": 1, "sources": {PRINCIPAL_DID: {"keys": [{
        "algorithm": "ed25519", "keyid": "agency-1",
        "public_key": base64.b64encode(other.public_key().public_bytes_raw()).decode()}]}}}
    result = out.aer_verify(trust)
    assert not result.ok and any(f.startswith("V2") for f in result.failures)


# ------------------------------------------------------------------- profile

def test_profile_cannot_allow_user_permissions():
    with pytest.raises(ValueError):
        build_mandate(profile(tools={"user_permissions-list_users": KIND_READ}))


@pytest.mark.parametrize("over", [
    {"tools": {}}, {"account_ids": ()}, {"brands": ()}, {"hold_threshold": 60_000},
    {"valid_from": "2027-01-01T00:00:00Z"}, {"daily_budget_cap": -1},
    {"tools": {READ_TOOL: "delete"}}, {"valid_until": "tomorrow"},
])
def test_bad_profiles_are_refused(over):
    with pytest.raises(ValueError):
        build_mandate(profile(**over))


def test_mandate_is_deterministic_and_has_one_or_two_grants_per_tool():
    m1, m2 = build_mandate(profile()), build_mandate(profile())
    assert m1 == m2
    assert len(m1["grants"]) == 5  # budget: allow + hold; bid, write, read: one each


def test_bid_change_bp_rounds_away_from_zero():
    assert bid_change_bp(100, 125) == 2500
    assert bid_change_bp(300, 301) == 34      # 33.33 -> 34
    assert bid_change_bp(300, 299) == -34
    assert bid_change_bp(0, 10) is None and bid_change_bp(100, -1) is None
