"""MolTrust Ads T4: billing auth, Ads Pro checkout, webhook quantity, the ads plan
at the /enforce boundary, GET /ads/plan and POST /ads/demo.

No database and no network: tests/ads_fakes.py stands in for the tables, Stripe
calls are monkeypatched, webhook events are signed with the process's test secret
and go through stripe.Webhook.construct_event like a real delivery.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
import stripe
from fastapi.testclient import TestClient

import app.billing as billing
import app.main as main
from app import ads_demo, ads_plan
from tests.ads_fakes import FakeDB

FREE_KEY = "mt_test_ads_free_0000000000000000"
PRO_KEY = "mt_test_ads_pro_00000000000000000"
OTHER_KEY = "mt_test_ads_other_000000000000000"
LEGACY_KEY = "mt_test_ads_legacy_00000000000000"
DID = "did:moltrust:0123456789abcdef"


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    fake.add_key(FREE_KEY, payer_ref="pyr_free")
    fake.add_key(PRO_KEY, payer_ref="pyr_pro", customer="cus_pro")
    fake.add_key(OTHER_KEY, payer_ref="pyr_other", customer="cus_other")
    fake.add_key(LEGACY_KEY, payer_ref=None)
    for k in (FREE_KEY, PRO_KEY, OTHER_KEY, LEGACY_KEY):
        main.API_KEYS.add(k)
    monkeypatch.setattr(main, "db_pool", fake.pool())
    main.limiter.reset()
    yield fake
    main.limiter.reset()


@pytest.fixture
def client(db):
    return TestClient(main.app, raise_server_exceptions=False)


# ── the ads mandate, built the way a customer builds it ───────────────────────

def _ads():
    return ads_demo._sdk()


def _mandate(accounts=("ENTITYDEMO123",)):
    ads = _ads()
    p = ads_demo.profile()
    import dataclasses
    return ads.build_mandate(dataclasses.replace(p, account_ids=tuple(accounts)))


def _tx(budget_cents=18_000):
    ads = _ads()
    return {"action": ads.check_action("campaign_management-update_campaign_budget",
                                       "budget_daily"),
            "t_min": int(ads_demo.DECIDED_AT.timestamp()) // 60,
            "account_id": "ENTITYDEMO123", "brand": "Acme Outdoor",
            "daily_budget": budget_cents, "total_daily_budget": 57_000 - 12_000 + budget_cents}


def _check(client, mandate, headers):
    return client.post("/enforce/check", json={"mandate": mandate, "transaction": _tx()},
                       headers=headers)


# ── /enforce boundary: who may send an ads mandate ────────────────────────────

def test_ads_mandate_with_did_only_is_401_pointing_to_signup(client, db):
    r = _check(client, _mandate(), {"X-MolTrust-DID": DID})
    assert r.status_code == 401
    body = r.json()
    assert body["error"] == "ads_requires_api_key"
    assert "/auth/signup" in body["message"]
    assert db.meter == {}


def test_non_ads_mandate_with_did_only_is_untouched(client, db):
    """Silent normal case: other profiles keep their auth and count nothing."""
    m = _mandate()
    m.pop("profile")
    r = _check(client, m, {"X-MolTrust-DID": DID})
    assert r.status_code == 200
    assert r.json()["verdict"] == "PERMIT"
    assert db.meter == {}


def test_free_key_one_account_is_permitted_and_counted(client, db):
    r = _check(client, _mandate(), {"X-API-Key": FREE_KEY})
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "PERMIT"
    assert list(db.meter.values()) == [1]


def test_free_key_two_accounts_is_402(client, db):
    r = _check(client, _mandate(("ENTITYDEMO123", "ENTITYOTHER9")), {"X-API-Key": FREE_KEY})
    assert r.status_code == 402
    body = r.json()
    assert body["error"] == "ads_plan_ad_accounts_exceeded"
    assert body["plan"] == "free"
    assert body["limit"]["ad_accounts"] == 1
    assert body["requested_ad_accounts"] == 2
    assert body["upgrade"] == "https://moltrust.ch/ads#pricing"
    assert "verdict" not in body
    assert db.meter == {}


def test_free_key_over_monthly_cap_is_402_and_never_permit(client, db, monkeypatch):
    fp, mk = ads_plan.key_fp(FREE_KEY), ads_plan.month_key()
    db.meter[(fp, mk)] = ads_plan.FREE_VERDICTS_PER_MONTH - 1
    called = []
    real = main.enforce_check
    monkeypatch.setattr(main, "enforce_check", lambda *a, **k: called.append(1) or real(*a, **k))

    last = _check(client, _mandate(), {"X-API-Key": FREE_KEY})
    assert last.status_code == 200 and last.json()["verdict"] == "PERMIT"
    over = _check(client, _mandate(), {"X-API-Key": FREE_KEY})
    assert over.status_code == 402
    body = over.json()
    assert body["error"] == "ads_plan_verdicts_exhausted"
    assert body["limit"]["verdicts_per_month"] == 1000
    assert body["month"] == mk
    assert called == [1]                       # the kernel was not asked the second time
    assert db.meter[(fp, mk)] == ads_plan.FREE_VERDICTS_PER_MONTH


def test_pro_key_accounts_up_to_quantity(client, db):
    db.add_sub("sub_pro", "pyr_pro", quantity=2)
    ok = _check(client, _mandate(("A1", "A2")), {"X-API-Key": PRO_KEY})
    assert ok.status_code == 200
    over = _check(client, _mandate(("A1", "A2", "A3")), {"X-API-Key": PRO_KEY})
    assert over.status_code == 402
    assert over.json()["plan"] == "pro"
    assert over.json()["limit"] == {"ad_accounts": 2, "verdicts_per_month": None}


def test_pro_key_has_no_monthly_cap(client, db):
    db.add_sub("sub_pro", "pyr_pro", quantity=1)
    db.meter[(ads_plan.key_fp(PRO_KEY), ads_plan.month_key())] = 50_000
    r = _check(client, _mandate(), {"X-API-Key": PRO_KEY})
    assert r.status_code == 200
    assert db.meter[(ads_plan.key_fp(PRO_KEY), ads_plan.month_key())] == 50_001


def test_cancelled_pro_falls_back_to_free(client, db):
    db.add_sub("sub_pro", "pyr_pro", quantity=5, active=False)
    r = _check(client, _mandate(("A1", "A2")), {"X-API-Key": PRO_KEY})
    assert r.status_code == 402 and r.json()["plan"] == "free"


def test_ratify_with_ads_mandate_needs_key(client, db):
    r = client.post("/enforce/ratify", json={
        "prior_record": {"core": {}, "core_digest": "sha256:" + "0" * 64},
        "decision": "APPROVED",
        "authority_proof": {"mandate": _mandate(), "authority": DID, "signature": "00"}},
        headers={"X-MolTrust-DID": DID})
    assert r.status_code == 401
    assert r.json()["error"] == "ads_requires_api_key"


def test_ratify_counts_against_the_free_cap(client, db):
    db.meter[(ads_plan.key_fp(FREE_KEY), ads_plan.month_key())] = 1000
    r = client.post("/enforce/ratify", json={
        "prior_record": {"core": {}, "core_digest": "sha256:" + "0" * 64},
        "decision": "APPROVED",
        "authority_proof": {"mandate": _mandate(), "authority": DID, "signature": "00"}},
        headers={"X-API-Key": FREE_KEY})
    assert r.status_code == 402


def test_account_ids_union_across_grants():
    m = {"profile": "ads", "grants": [
        {"constraints": [{"type": "enum", "field": "account_id", "values": ["A", "B"]}]},
        {"constraints": [{"type": "enum", "field": "account_id", "values": ["B", "C"]},
                         {"type": "enum", "field": "brand", "values": ["X", "Y", "Z"]}]},
        {"constraints": [{"type": "range", "field": "t_min", "lo": 0, "hi": 1}]},
        "junk", {"constraints": "junk"}]}
    assert ads_plan.mandate_account_ids(m) == {"A", "B", "C"}
    assert ads_plan.mandate_account_ids({"grants": "x"}) == set()


# ── GET /ads/plan ─────────────────────────────────────────────────────────────

def test_ads_plan_free(client, db):
    db.meter[(ads_plan.key_fp(FREE_KEY), ads_plan.month_key())] = 7
    r = client.get("/ads/plan", headers={"X-API-Key": FREE_KEY})
    assert r.status_code == 200
    assert r.json() | {} == {
        "plan": "free", "ad_accounts_allowed": 1, "verdicts_used": 7,
        "verdicts_limit": 1000, "month": ads_plan.month_key(), "rate_limit": "60/minute",
        "upgrade": "https://moltrust.ch/ads#pricing", "manage": None}


def test_ads_plan_pro(client, db):
    db.add_sub("sub_pro", "pyr_pro", quantity=3)
    r = client.get("/ads/plan", headers={"X-API-Key": PRO_KEY})
    assert r.json()["plan"] == "pro"
    assert r.json()["ad_accounts_allowed"] == 3
    assert r.json()["verdicts_limit"] is None


def test_ads_plan_needs_key(client):
    assert client.get("/ads/plan").status_code == 401
    assert client.get("/ads/plan", headers={"X-API-Key": "mt_nope"}).status_code == 403


# ── billing: portal and subscription status ───────────────────────────────────

def test_portal_without_key_is_401(client, db):
    r = client.post("/billing/portal", json={"customer_id": "cus_pro"})
    assert r.status_code == 401


def test_subscription_without_key_is_401(client, db):
    assert client.get("/billing/subscription/cus_pro").status_code == 401


def test_portal_serves_only_the_bound_customer(client, db, monkeypatch):
    seen = []
    monkeypatch.setattr(stripe.billing_portal.Session, "create",
                        lambda **kw: seen.append(kw) or SimpleNamespace(url="https://portal/x"))
    r = client.post("/billing/portal", json={}, headers={"X-API-Key": PRO_KEY})
    assert r.status_code == 200 and r.json() == {"portal_url": "https://portal/x"}
    assert seen[0]["customer"] == "cus_pro"
    assert seen[0]["return_url"] == billing.PORTAL_DEFAULT_RETURN

    other = client.post("/billing/portal", json={"customer_id": "cus_other"},
                        headers={"X-API-Key": PRO_KEY})
    assert other.status_code == 403
    assert len(seen) == 1


def test_portal_rejects_foreign_return_url(client, db):
    r = client.post("/billing/portal", json={"return_url": "https://evil.example/"},
                    headers={"X-API-Key": PRO_KEY})
    assert r.status_code == 400


def test_portal_without_bound_customer_is_404(client, db):
    r = client.post("/billing/portal", json={}, headers={"X-API-Key": FREE_KEY})
    assert r.status_code == 404
    assert r.json()["error"] == "no_billing_customer"


def test_subscription_status_of_another_customer_is_403(client, db):
    r = client.get("/billing/subscription/cus_other", headers={"X-API-Key": PRO_KEY})
    assert r.status_code == 403


def test_subscription_status_reads_items_period_end(client, db, monkeypatch):
    sub = stripe.Subscription.construct_from({
        "id": "sub_1", "object": "subscription", "metadata": {"tier": "ads_pro"},
        "cancel_at_period_end": False,
        "items": {"object": "list", "data": [
            {"id": "si_1", "quantity": 4, "current_period_end": 1_800_000_000}]}}, "k")
    monkeypatch.setattr(stripe.Subscription, "list",
                        lambda **kw: SimpleNamespace(data=[sub]))
    r = client.get("/billing/subscription/cus_pro", headers={"X-API-Key": PRO_KEY})
    assert r.status_code == 200, r.text
    assert r.json()["quantity"] == 4
    assert r.json()["current_period_end"].startswith("2027-01-15")


# ── billing: Ads Pro checkout ─────────────────────────────────────────────────

@pytest.fixture
def stripe_checkout(monkeypatch):
    calls = {"session": [], "customer": []}
    price = SimpleNamespace(id="price_ads", tax_behavior=None)
    monkeypatch.setattr(stripe.Price, "list", lambda **kw: SimpleNamespace(data=[price]))
    monkeypatch.setattr(stripe.Customer, "create",
                        lambda **kw: calls["customer"].append(kw) or SimpleNamespace(id="cus_new"))
    monkeypatch.setattr(stripe.Customer, "list",
                        lambda **kw: pytest.fail("ads checkout must not look customers up by email"))
    monkeypatch.setattr(stripe.checkout.Session, "create",
                        lambda **kw: calls["session"].append(kw)
                        or SimpleNamespace(id="cs_test_1", url="https://checkout/x"))
    calls["tax"] = SimpleNamespace(status="pending",
                                   defaults=SimpleNamespace(tax_behavior=None))
    monkeypatch.setattr(stripe.tax.Settings, "retrieve", lambda **kw: calls["tax"])
    billing._TAX_CACHE.update(at=0.0, status=None)
    return calls


def test_ads_checkout_needs_key(client, db, stripe_checkout):
    r = client.post("/billing/checkout", json={"tier": "ads_pro", "quantity": 2})
    assert r.status_code == 401
    assert stripe_checkout["session"] == []


def test_ads_checkout_session_shape(client, db, stripe_checkout):
    r = client.post("/billing/checkout", json={"tier": "ads_pro", "quantity": 3,
                                               "success_url": "https://evil.example/"},
                    headers={"X-API-Key": FREE_KEY})
    assert r.status_code == 200, r.text
    assert r.json() == {"checkout_url": "https://checkout/x", "session_id": "cs_test_1",
                        "tier": "ads_pro", "quantity": 3, "automatic_tax": False}
    kw = stripe_checkout["session"][0]
    assert kw["customer"] == "cus_new"
    assert kw["line_items"] == [{"price": "price_ads", "quantity": 3, "adjustable_quantity":
                                 {"enabled": True, "minimum": 1, "maximum": 100}}]
    assert kw["success_url"] == billing.ADS_SUCCESS_URL
    assert kw["cancel_url"] == billing.ADS_CANCEL_URL
    assert kw["subscription_data"]["metadata"]["payer_ref"] == "pyr_free"
    assert kw["subscription_data"]["metadata"]["tier"] == "ads_pro"
    assert "automatic_tax" not in kw
    assert kw["billing_address_collection"] == "required"
    assert kw["tax_id_collection"] == {"enabled": True}
    assert kw["customer_update"] == {"address": "auto", "name": "auto"}
    assert db.accounts["pyr_free"]["stripe_customer_id"] == "cus_new"


def test_ads_checkout_tax_only_when_active(client, db, stripe_checkout):
    stripe_checkout["tax"] = SimpleNamespace(status="active",
                                             defaults=SimpleNamespace(tax_behavior="exclusive"))
    r = client.post("/billing/checkout", json={"tier": "ads_pro"}, headers={"X-API-Key": FREE_KEY})
    assert r.json()["automatic_tax"] is True
    assert stripe_checkout["session"][0]["automatic_tax"] == {"enabled": True}
    assert stripe_checkout["session"][0]["tax_id_collection"] == {"enabled": True}
    assert stripe_checkout["session"][0]["billing_address_collection"] == "required"

    billing._TAX_CACHE.update(at=0.0)
    stripe_checkout["tax"] = SimpleNamespace(status="active",
                                             defaults=SimpleNamespace(tax_behavior=None))
    r = client.post("/billing/checkout", json={"tier": "ads_pro"}, headers={"X-API-Key": OTHER_KEY})
    assert r.json()["automatic_tax"] is False   # active, but no tax behaviour known


def test_ads_checkout_reuses_bound_customer_and_mints_payer_for_legacy(client, db, stripe_checkout):
    client.post("/billing/checkout", json={"tier": "ads_pro"}, headers={"X-API-Key": OTHER_KEY})
    assert stripe_checkout["session"][0]["customer"] == "cus_other"
    assert stripe_checkout["customer"] == []
    r = client.post("/billing/checkout", json={"tier": "ads_pro"}, headers={"X-API-Key": LEGACY_KEY})
    assert r.status_code == 200
    assert db.api_keys[LEGACY_KEY]["payer_ref"].startswith("pyr_")


def test_ads_checkout_quantity_bounds(client, db, stripe_checkout):
    for q in (0, 101):
        r = client.post("/billing/checkout", json={"tier": "ads_pro", "quantity": q},
                        headers={"X-API-Key": FREE_KEY})
        assert r.status_code == 422


def test_ads_checkout_when_already_pro_is_409(client, db, stripe_checkout):
    db.add_sub("sub_pro", "pyr_pro", quantity=1)
    r = client.post("/billing/checkout", json={"tier": "ads_pro"}, headers={"X-API-Key": PRO_KEY})
    assert r.status_code == 409


def test_other_tiers_reject_quantity(client, db):
    r = client.post("/billing/checkout", json={"tier": "base", "quantity": 2})
    assert r.status_code == 400


# ── webhook: signed fixtures through construct_event ─────────────────────────

def _signed(event: dict):
    payload = json.dumps(event).encode()
    t = int(time.time())
    sig = hmac.new(billing.WEBHOOK_SECRET.encode(), f"{t}.".encode() + payload,
                   hashlib.sha256).hexdigest()
    return payload, {"Stripe-Signature": f"t={t},v1={sig}", "Content-Type": "application/json"}


def _sub_event(kind, sub_id="sub_ads_1", qty=3, status="active", cancel=False):
    return {"id": f"evt_{kind}_{qty}", "object": "event", "api_version": "2026-03-25.dahlia",
            "type": f"customer.subscription.{kind}",
            "data": {"object": {
                "id": sub_id, "object": "subscription", "customer": "cus_new",
                "status": status, "cancel_at_period_end": cancel,
                "metadata": {"tier": "ads_pro", "payer_ref": "pyr_free", "agent_did": ""},
                "items": {"object": "list", "data": [{
                    "id": "si_1", "object": "subscription_item", "quantity": qty,
                    "current_period_end": 1_800_000_000,
                    "price": {"id": "price_ads", "lookup_key": "mt_ads_pro_monthly"}}]}}}}


@pytest.fixture
def no_stripe_reads(monkeypatch):
    monkeypatch.setattr(stripe.Customer, "retrieve",
                        lambda *a, **kw: stripe.Customer.construct_from(
                            {"id": "cus_new", "metadata": {}}, "k"))


def test_webhook_stores_quantity_and_unlocks_pro(client, db, no_stripe_reads):
    payload, headers = _signed(_sub_event("created", qty=3))
    r = client.post("/billing/webhook", content=payload, headers=headers)
    assert r.status_code == 200, r.text
    row = db.subs["sub_ads_1"]
    assert (row["tier"], row["quantity"], row["active"], row["payer_ref"]) == \
        ("ads_pro", 3, True, "pyr_free")
    assert row["current_period_end"] == 1_800_000_000
    assert db.accounts["pyr_free"]["stripe_customer_id"] == "cus_new"

    plan = client.get("/ads/plan", headers={"X-API-Key": FREE_KEY}).json()
    assert (plan["plan"], plan["ad_accounts_allowed"]) == ("pro", 3)

    payload, headers = _signed(_sub_event("updated", qty=5))
    client.post("/billing/webhook", content=payload, headers=headers)
    assert db.subs["sub_ads_1"]["quantity"] == 5


def test_webhook_cancel_at_period_end_then_deleted(client, db, no_stripe_reads):
    for ev in (_sub_event("created"), _sub_event("updated", cancel=True)):
        payload, headers = _signed(ev)
        assert client.post("/billing/webhook", content=payload, headers=headers).status_code == 200
    assert db.subs["sub_ads_1"]["cancel_at_period_end"] is True
    assert db.subs["sub_ads_1"]["active"] is True

    payload, headers = _signed(_sub_event("deleted", status="canceled"))
    assert client.post("/billing/webhook", content=payload, headers=headers).status_code == 200
    assert db.subs["sub_ads_1"]["active"] is False
    plan = client.get("/ads/plan", headers={"X-API-Key": FREE_KEY}).json()
    assert plan["plan"] == "free"


def test_webhook_invoice_paid_is_logged_once(client, db, no_stripe_reads):
    ev = {"id": "evt_inv", "object": "event", "type": "invoice.payment_succeeded",
          "data": {"object": {"id": "in_1", "object": "invoice", "customer": "cus_new",
                              "amount_paid": 4900, "currency": "usd",
                              "parent": {"subscription_details": {"subscription": "sub_ads_1"}}}}}
    for _ in range(2):
        payload, headers = _signed(ev)
        assert client.post("/billing/webhook", content=payload, headers=headers).status_code == 200
    assert db.payments == {"in_1"}


def test_webhook_bad_signature_is_400(client, db):
    payload, headers = _signed(_sub_event("created"))
    headers["Stripe-Signature"] = headers["Stripe-Signature"][:-4] + "0000"
    assert client.post("/billing/webhook", content=payload, headers=headers).status_code == 400
    assert db.subs == {}


def test_handled_events_match_the_invariant_script():
    import importlib.util
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check_stripe_webhook_events.py"
    spec = importlib.util.spec_from_file_location("cswe", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert tuple(mod.HANDLED_EVENTS) == tuple(billing.HANDLED_EVENTS)
    ep = [{"url": mod.URL, "status": "enabled",
           "enabled_events": ["checkout.session.completed", "customer.subscription.deleted"]}]
    assert mod.missing_events(ep) == ["customer.subscription.created",
                                      "customer.subscription.updated",
                                      "invoice.payment_succeeded", "invoice.payment_failed"]
    ep[0]["enabled_events"] = list(billing.HANDLED_EVENTS)
    assert mod.missing_events(ep) == []
    assert mod.missing_events([]) is None


# ── POST /ads/demo ────────────────────────────────────────────────────────────

EXPECTED = {"budget": "allow", "bid": "forbid", "user_permissions": "forbid",
            "foreign_brand": "forbid", "expired": "forbid", "lite_delete": "forbid"}


@pytest.mark.parametrize("scenario", sorted(EXPECTED))
def test_demo_scenarios(client, scenario):
    r = client.post("/ads/demo", json={"scenario": scenario})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["disposition"] == EXPECTED[scenario]
    assert set(body) == {"scenario", "disposition", "reason", "tool", "change", "decided_at",
                         "record", "mandate_digest"}
    assert body["record"]["core_digest"].startswith("sha256:")
    assert body["mandate_digest"].startswith("sha256:")


@pytest.mark.parametrize("value,want", [(150, "allow"), (350, "hold"), (600, "forbid")])
def test_demo_budget_thresholds(client, value, want):
    r = client.post("/ads/demo", json={"scenario": "budget", "daily_budget": value})
    assert r.json()["disposition"] == want


@pytest.mark.parametrize("value,want", [(10, "allow"), (-25, "allow"), (30, "forbid")])
def test_demo_bid_thresholds(client, value, want):
    r = client.post("/ads/demo", json={"scenario": "bid", "bid_change_percent": value})
    assert r.json()["disposition"] == want


def test_demo_is_deterministic(client):
    a = client.post("/ads/demo", json={"scenario": "budget"}).json()
    b = client.post("/ads/demo", json={"scenario": "budget"}).json()
    assert a == b


@pytest.mark.parametrize("body", [
    {"scenario": "budget", "extra": 1},
    {"scenario": "nope"},
    {},
    {"scenario": "budget", "daily_budget": 100_001},
    {"scenario": "bid", "bid_change_percent": -101},
])
def test_demo_rejects_bad_bodies_with_422(client, body):
    assert client.post("/ads/demo", json=body).status_code == 422


def test_demo_body_cap(client):
    r = client.post("/ads/demo", content=b'{"scenario":"budget","pad":"' + b"x" * 2100 + b'"}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_demo_rate_limit_10_per_minute(client):
    codes = [client.post("/ads/demo", json={"scenario": "budget"}).status_code
             for _ in range(11)]
    assert codes[:10] == [200] * 10
    assert codes[10] == 429


def test_demo_needs_no_key_and_writes_nothing(client, db):
    r = client.post("/ads/demo", json={"scenario": "budget"})
    assert r.status_code == 200
    assert db.meter == {} and db.subs == {}


def test_demo_profile_equals_the_sdk_example():
    """The widget shows the documented demo mandate, not a lookalike."""
    import importlib.util
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "ads_demo_example", root / "sdk/python/examples/amazon_ads/demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ads = _ads()
    assert ads.build_mandate(ads_demo.profile()) == ads.build_mandate(mod.PROFILE)
    assert ads_demo.STATE == mod.STATE


# ── CORS for the website ──────────────────────────────────────────────────────

@pytest.mark.parametrize("path,method", [
    ("/billing/portal", "POST"), ("/billing/checkout", "POST"), ("/auth/signup", "POST"),
    ("/ads/plan", "GET"), ("/ads/demo", "POST")])
def test_cors_preflight_from_moltrust_ch(client, path, method):
    r = client.options(path, headers={
        "Origin": "https://moltrust.ch", "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "content-type,x-api-key"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://moltrust.ch"
    assert "x-api-key" in r.headers["access-control-allow-headers"].lower()
