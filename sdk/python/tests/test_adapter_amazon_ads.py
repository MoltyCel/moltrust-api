"""Amazon Ads adapter against the real kernel behind a mock transport.

The transport answers /enforce/check with `enforce_check` and /enforce/ratify with `ratify`,
in the endpoint's response shape, so the tests exercise EnforceClient as shipped. Tool
arguments follow the public Amazon Ads MCP schemas (campaign_management.json).
"""
import base64
import datetime as dt
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from moltrust_enforce import (PAYLOAD_TYPE, enforce_check, evidence_payload_bytes,
                              make_envelope, pae)
from moltrust_enforce._ratify_core import ratify, statement_bytes
from moltrust_enforce.adapters.amazon_ads import (
    ALLOW, DOCUMENTED_TOOLS, FORBID, HOLD, KIND_READ, AdsProfile, AmazonAdsGuard, Forbidden,
    HoldRequired, bid_change_bp, build_mandate, minor_units, split_call,
)
from moltrust_enforce.client import EnforceClient

from conftest import as_endpoint_payload

PRINCIPAL_KEY = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32)
APPROVER_KEY = Ed25519PrivateKey.from_private_bytes(b"\x02" * 32)
PRINCIPAL_DID = "did:moltrust:aaaaaaaaaaaaaaaa"
APPROVER_DID = "did:moltrust:bbbbbbbbbbbbbbbb"

BUDGET = "campaign_management-update_campaign_budget"
BID = "campaign_management-update_target_bid"
CREATE = "campaign_management-create_campaign"
STATE = "campaign_management-update_campaign_state"
QUERY = "campaign_management-query_campaign"

NOW = dt.datetime(2026, 10, 12, 12, 0, tzinfo=dt.timezone.utc)
ACCOUNT = {"advertiserAccountId": "ENTITY1ABC"}


def pub_hex(key):
    return key.public_key().public_bytes_raw().hex()


def profile(**over):
    p = dict(
        mandate_id="ads-test-1",
        principal={"did": PRINCIPAL_DID, "public_key": pub_hex(PRINCIPAL_KEY)},
        ratification_authorities=({"did": APPROVER_DID, "public_key": pub_hex(APPROVER_KEY),
                                   "role": "agency_finance"},),
        tools=dict(DOCUMENTED_TOOLS),
        account_ids=("ENTITY1ABC",),
        brands=("Acme",),
        daily_budget_cap=50_000,          # 500.00 per campaign and day
        hold_threshold=20_000,            # above 200.00 a human approves
        total_daily_budget_cap=200_000,   # 2,000.00 per account and day
        period_budget_cap=1_000_000,      # 10,000.00 lifetime / monthly
        max_bid=500,                      # 5.00
        max_bid_change_bp=2_500,          # +/- 25 %
        valid_from="2026-10-01T00:00:00Z",
        valid_until="2026-12-31T23:59:59Z",
    )
    p.update(over)
    return AdsProfile(**p)


CTX = {"campaigns": {"C1": {"brand": "Acme", "daily_budget": 10_000},
                     "C2": {"brand": "Acme", "daily_budget": 10_000},
                     "X9": {"brand": "OtherCo", "daily_budget": 10_000}},
       "targets": {"T1": {"brand": "Acme", "bid": 100}},
       "account_daily_total": 100_000}


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


def client(transport=None):
    return EnforceClient("https://api.test", api_key="k", transport=transport or kernel_transport())


def approver(decision="APPROVED", key=APPROVER_KEY, did=APPROVER_DID):
    def _approve(outcome, res):
        sig = key.sign(statement_bytes(res.verdict.core_digest, decision, did)).hex()
        return decision, {"mandate": outcome.mandate, "authority": did, "signature": sig}
    return _approve


def attest(statement):
    sig = PRINCIPAL_KEY.sign(pae(PAYLOAD_TYPE, evidence_payload_bytes(statement)))
    return make_envelope(statement, [{"keyid": "agency-1",
                                      "sig": base64.b64encode(sig).decode("ascii")}])


TRUST = {"trust_list_version": 1, "sources": {PRINCIPAL_DID: {"keys": [{
    "algorithm": "ed25519", "keyid": "agency-1",
    "public_key": base64.b64encode(PRINCIPAL_KEY.public_key().public_bytes_raw()).decode()}]}}}


def guard(p=None, clock_at=NOW, transport=None, **kw):
    kw.setdefault("attest", attest)
    return AmazonAdsGuard(client(transport), p or profile(), clock=lambda: clock_at, **kw)


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, tool, args):
        self.calls.append((tool, args))
        return {"ok": True}


def budget_entry(value, period="DAILY", marketplaces=()):
    mbv = {"monetaryBudget": {"value": value}}
    if marketplaces:
        mbv["marketplaceSettings"] = [{"marketplace": m, "monetaryBudget": {"value": v}}
                                      for m, v in marketplaces]
    return {"budgetType": "MONETARY", "recurrenceTimePeriod": period,
            "budgetValue": {"monetaryBudgetValue": mbv}}


def budget_args(value, campaign="C1", account=ACCOUNT, **kw):
    return {"body": {"accessRequestedAccount": account,
                     "campaigns": [{"campaignId": campaign,
                                    "budgets": [budget_entry(value, **kw)]}]}}


def bid_args(value, target="T1"):
    return {"body": {"accessRequestedAccount": ACCOUNT,
                     "targets": [{"targetId": target, "bid": {"bid": value}}]}}


# ------------------------------------------------------------------- dispositions

def test_budget_within_threshold_is_allow_and_runs():
    rec = Recorder()
    out = guard().call(BUDGET, budget_args(150.0), rec, CTX)
    assert out.disposition == ALLOW and out.executed and len(rec.calls) == 1


def test_execute_receives_the_callers_arguments_unchanged():
    rec, args = Recorder(), budget_args(150.0)
    guard().call(BUDGET, args, rec, CTX)
    assert rec.calls == [(BUDGET, args)]


def test_budget_above_threshold_is_hold_and_does_not_run_without_approver():
    rec = Recorder()
    with pytest.raises(HoldRequired) as e:
        guard().call(BUDGET, budget_args(300.0), rec, CTX)
    assert e.value.outcome.disposition == HOLD and rec.calls == []


def test_hold_ratified_runs():
    rec = Recorder()
    out = guard(approver=approver()).call(BUDGET, budget_args(300.0), rec, CTX)
    assert out.disposition == HOLD and out.checks[0].ratification.ratified
    assert len(rec.calls) == 1


def test_hold_disapproved_does_not_run():
    rec = Recorder()
    with pytest.raises(HoldRequired):
        guard(approver=approver("DISAPPROVED")).call(BUDGET, budget_args(300.0), rec, CTX)
    assert rec.calls == []


def test_hold_signed_by_stranger_does_not_run():
    rec = Recorder()
    stranger = Ed25519PrivateKey.from_private_bytes(b"\x03" * 32)
    with pytest.raises(HoldRequired):
        guard(approver=approver(key=stranger)).call(BUDGET, budget_args(300.0), rec, CTX)
    assert rec.calls == []


def test_budget_above_cap_is_forbid_and_does_not_run():
    rec = Recorder()
    with pytest.raises(Forbidden):
        guard(approver=approver()).call(BUDGET, budget_args(500.01), rec, CTX)
    assert rec.calls == []


def test_budget_at_cap_is_hold():
    assert guard().decide(BUDGET, budget_args(500.0), CTX).disposition == HOLD


def test_marketplace_value_above_cap_is_forbid():
    args = budget_args(100.0, marketplaces=[("US", 100.0), ("JP", 900.0)])
    assert guard().decide(BUDGET, args, CTX).disposition == FORBID


def test_unreadable_marketplace_value_is_forbid():
    args = budget_args(100.0, marketplaces=[("US", "lots")])
    assert guard().decide(BUDGET, args, CTX).disposition == FORBID


def test_account_daily_total_cap_is_forbid():
    ctx = dict(CTX, account_daily_total=195_001)  # 1,950.01 - 100 + 150 > 2,000
    assert guard().decide(BUDGET, budget_args(150.0), ctx).disposition == FORBID


def test_missing_account_total_is_forbid():
    ctx = {k: v for k, v in CTX.items() if k != "account_daily_total"}
    assert guard().decide(BUDGET, budget_args(150.0), ctx).disposition == FORBID


def test_lifetime_budget_uses_period_cap():
    g = guard()
    assert g.decide(BUDGET, budget_args(9_000.0, period="LIFETIME"), CTX).disposition == ALLOW
    assert g.decide(BUDGET, budget_args(10_000.01, period="LIFETIME"),
                    CTX).disposition == FORBID


def test_batch_takes_the_strictest_verdict():
    args = {"body": {"accessRequestedAccount": ACCOUNT, "campaigns": [
        {"campaignId": "C1", "budgets": [budget_entry(150.0)]},
        {"campaignId": "C2", "budgets": [budget_entry(900.0)]}]}}
    out = guard().decide(BUDGET, args, CTX)
    assert [c.disposition for c in out.checks] == [ALLOW, FORBID]
    assert out.disposition == FORBID and "[C2]" in out.reason


def test_foreign_brand_campaign_is_forbid():
    assert guard().decide(BUDGET, budget_args(150.0, campaign="X9"), CTX).disposition == FORBID


def test_unknown_campaign_brand_is_forbid():
    assert guard().decide(BUDGET, budget_args(150.0, campaign="C404"),
                          CTX).disposition == FORBID


def test_foreign_account_is_forbid():
    args = budget_args(150.0, account={"profileId": "999"})
    assert guard().decide(BUDGET, args, CTX).disposition == FORBID


def test_create_campaign_with_foreign_brand_id_is_forbid():
    args = {"body": {"accessRequestedAccount": ACCOUNT, "campaigns": [
        {"adProduct": "SPONSORED_PRODUCTS", "name": "x", "state": "PAUSED",
         "brandId": "OtherCo", "budgets": [budget_entry(50.0)]}]}}
    assert guard().decide(CREATE, args, dict(CTX, brand="Acme")).disposition == FORBID


def test_create_campaign_in_mandate_is_allow():
    args = {"body": {"accessRequestedAccount": ACCOUNT, "campaigns": [
        {"adProduct": "SPONSORED_PRODUCTS", "name": "x", "state": "PAUSED",
         "budgets": [budget_entry(50.0)]}]}}
    out = guard().decide(CREATE, args, dict(CTX, brand="Acme"))
    assert out.disposition == ALLOW
    assert {c.check.change for c in out.checks} == {"budget_daily", "other"}


def test_pause_is_allow():
    args = {"body": {"accessRequestedAccount": ACCOUNT,
                     "campaigns": [{"campaignId": "C1", "state": "PAUSED"}]}}
    assert guard().decide(STATE, args, CTX).disposition == ALLOW


def test_expired_mandate_is_forbid():
    late = dt.datetime(2027, 1, 1, 0, 0, tzinfo=dt.timezone.utc)
    assert guard(clock_at=late).decide(BUDGET, budget_args(150.0), CTX).disposition == FORBID


def test_not_yet_valid_mandate_is_forbid():
    early = dt.datetime(2026, 9, 30, 23, 59, tzinfo=dt.timezone.utc)
    out = guard(clock_at=early).decide(QUERY, {"body": {"accessRequestedAccount": ACCOUNT}}, {})
    assert out.disposition == FORBID


@pytest.mark.parametrize("tool", [
    "user_permissions-update_user_permissions", "user_permissions-delete_user_permissions",
    "user_permissions-list_user_permissions", "user_invitations-create",
    "user_invitation-redeem", "manager_accounts-associate_accounts",
])
def test_access_tools_are_forbid_and_do_not_run(tool):
    rec = Recorder()
    with pytest.raises(Forbidden):
        guard().call(tool, {"body": {"accessRequestedAccount": ACCOUNT}}, rec, CTX)
    assert rec.calls == []


def test_unlisted_tool_is_forbid():
    out = guard().decide("campaign_management-delete_campaign",
                         {"body": {"accessRequestedAccount": ACCOUNT,
                                   "campaignIds": ["C1"]}}, dict(CTX, brand="Acme"))
    assert out.disposition == FORBID and "is not in the mandate" in out.reason


def test_bid_within_limit_is_allow_and_above_is_forbid():
    g = guard()
    assert g.decide(BID, bid_args(1.25), CTX).disposition == ALLOW
    assert g.decide(BID, bid_args(1.26), CTX).disposition == FORBID
    assert g.decide(BID, bid_args(0.74), CTX).disposition == FORBID


def test_bid_above_absolute_cap_is_forbid():
    ctx = dict(CTX, targets={"T1": {"brand": "Acme", "bid": 450}})
    assert guard().decide(BID, bid_args(5.01), ctx).disposition == FORBID


def test_bid_without_current_bid_is_forbid():
    ctx = dict(CTX, targets={"T1": {"brand": "Acme"}})
    assert guard().decide(BID, bid_args(1.0), ctx).disposition == FORBID


def test_read_tool_is_allow():
    out = guard().decide(QUERY, {"body": {"accessRequestedAccount": ACCOUNT}}, {})
    assert out.disposition == ALLOW


def test_lite_invoke_is_unwrapped():
    args = {"toolName": BUDGET, "arguments": budget_args(900.0)}
    out = guard().decide("amazon_ads_mcp-invoke_tool", args, CTX)
    assert out.tool == BUDGET and out.disposition == FORBID
    ok = guard().decide("amazon_ads_mcp-invoke_tool",
                        {"toolName": BUDGET, "arguments": budget_args(150.0)}, CTX)
    assert ok.disposition == ALLOW


def test_lite_invoke_with_unreadable_wrapper_is_forbid():
    out = guard().decide("amazon_ads_mcp-invoke_tool", {"what": BUDGET}, CTX)
    assert out.disposition == FORBID


# ------------------------------------------------------------------- fail closed

def test_transport_failure_is_forbid_and_does_not_run():
    def boom(_r):
        raise httpx.ConnectError("down")
    rec = Recorder()
    with pytest.raises(Forbidden):
        guard(transport=httpx.MockTransport(boom)).call(
            QUERY, {"body": {"accessRequestedAccount": ACCOUNT}}, rec)
    assert rec.calls == []


def test_server_permit_that_does_not_recompute_is_forbid():
    def lying(request):
        body = json.loads(request.content)
        r = as_endpoint_payload(enforce_check(body["mandate"], body["transaction"]))
        r["verdict"] = "PERMIT"
        return httpx.Response(200, json=r)
    out = guard(transport=httpx.MockTransport(lying)).decide(BUDGET, budget_args(900.0), CTX)
    assert out.disposition == FORBID and "does not recompute" in out.reason


# ------------------------------------------------------------------- AER

@pytest.mark.parametrize("amount,expected", [(150.0, ALLOW), (300.0, HOLD), (600.0, FORBID)])
def test_every_check_carries_a_verifying_aer_record(amount, expected):
    out = guard().decide(BUDGET, budget_args(amount), CTX)
    assert out.disposition == expected
    for res, result in zip(out.checks, out.aer_verify(TRUST)):
        assert result.ok, result.failures
        assert result.recomputed_verdict == res.verdict.verdict
        assert res.aer_bundle["decision_timestamp"] == "2026-10-12T12:00:00Z"
        assert res.transaction["t_min"] * 60 == int(NOW.timestamp())


def test_tampered_aer_transaction_fails_verification():
    res = guard().decide(BUDGET, budget_args(150.0), CTX).checks[0]
    res.transaction = dict(res.transaction, daily_budget=1)
    assert not res.aer_verify(build_mandate(profile()), TRUST).ok


def test_aer_without_attester_recomputes_but_attests_nothing():
    out = guard(attest=None).decide(QUERY, {"body": {"accessRequestedAccount": ACCOUNT}}, {})
    assert out.checks[0].aer_bundle["items"] == []
    assert all(r.ok for r in out.aer_verify(TRUST))


def test_aer_statement_from_untrusted_key_fails_v2():
    out = guard().decide(QUERY, {"body": {"accessRequestedAccount": ACCOUNT}}, {})
    other = Ed25519PrivateKey.from_private_bytes(b"\x04" * 32)
    trust = {"trust_list_version": 1, "sources": {PRINCIPAL_DID: {"keys": [{
        "algorithm": "ed25519", "keyid": "agency-1",
        "public_key": base64.b64encode(other.public_key().public_bytes_raw()).decode()}]}}}
    result = out.aer_verify(trust)[0]
    assert not result.ok and any(f.startswith("V2") for f in result.failures)


# ------------------------------------------------------------------- profile + helpers

@pytest.mark.parametrize("tool", ["user_permissions-list_user_permissions",
                                  "user_invitations-create", "manager_accounts-get_manager_accounts",
                                  "amazon_ads_mcp-invoke_tool"])
def test_profile_cannot_grant_access_tools_or_the_lite_wrapper(tool):
    with pytest.raises(ValueError):
        build_mandate(profile(tools={tool: KIND_READ}))


@pytest.mark.parametrize("over", [
    {"tools": {}}, {"account_ids": ()}, {"brands": ()}, {"hold_threshold": 60_000},
    {"valid_from": "2027-01-01T00:00:00Z"}, {"daily_budget_cap": -1}, {"max_bid": True},
    {"tools": {QUERY: "delete"}}, {"valid_until": "tomorrow"},
])
def test_bad_profiles_are_refused(over):
    with pytest.raises(ValueError):
        build_mandate(profile(**over))


def test_mandate_is_deterministic():
    assert build_mandate(profile()) == build_mandate(profile())


def test_documented_tools_carry_no_access_group():
    assert not [t for t in DOCUMENTED_TOOLS if t.split("-")[0] in
                ("user_permissions", "user_invitations", "user_invitation", "manager_accounts")]


def test_split_unknown_shape_has_no_brand():
    checks = split_call(BUDGET, {"body": {"accessRequestedAccount": ACCOUNT,
                                          "campaigns": "C1"}}, CTX)
    assert len(checks) == 1 and "brand" not in checks[0].facts


def test_bid_change_bp_rounds_away_from_zero():
    assert bid_change_bp(100, 125) == 2500
    assert bid_change_bp(300, 301) == 34      # 33.33 -> 34
    assert bid_change_bp(300, 299) == -34
    assert bid_change_bp(0, 10) is None and bid_change_bp(100, -1) is None


def test_minor_units():
    assert minor_units(1.005) in (100, 101)   # binary float; never below a cent of truth
    assert minor_units(2) == 200 and minor_units("3.10") == 310
    assert minor_units(-1) is None and minor_units(True) is None and minor_units("x") is None


def test_forbid_reason_names_the_limit():
    out = guard().decide(BUDGET, budget_args(900.0), CTX)
    assert "daily_budget 900.00 outside 0.00..500.00" in out.reason
    out = guard().decide(BUDGET, budget_args(150.0, campaign="X9"), CTX)
    assert "brand 'OtherCo' not allowed" in out.reason


def test_new_target_takes_brand_from_its_ad_group():
    args = {"body": {"accessRequestedAccount": ACCOUNT, "targets": [
        {"adGroupId": "G1", "adProduct": "SPONSORED_PRODUCTS", "negative": False,
         "state": "ENABLED", "targetType": "KEYWORD", "targetDetails": {}, "bid": {"bid": 0.8}}]}}
    ok = dict(CTX, ad_groups={"G1": {"brand": "Acme"}})
    bad = dict(CTX, ad_groups={"G1": {"brand": "OtherCo"}})
    g = guard()
    assert g.decide("campaign_management-create_target", args, ok).disposition == ALLOW
    assert g.decide("campaign_management-create_target", args, bad).disposition == FORBID
