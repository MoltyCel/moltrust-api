"""Keyless demo for the moltrust.ch/ads widget: one fixed ads mandate, the real kernel.

The browser never holds a key and never sends a mandate. It names a scenario and,
for two of them, one number; the server builds the demo mandate with the SDK
adapter (moltrust_enforce.adapters.amazon_ads), asks the in-process kernel
(app/enforcement/enforce_check.py, no HTTP) and returns the adapter's disposition
and explained reason.

The profile values are the ones in sdk/python/examples/amazon_ads/demo.py; a test
holds the two mandates equal. The decision time is fixed (2026-10-12T14:00Z,
"expired" uses 2027-01-02T09:00Z), so the same request always gives the same
core_digest. Nothing is stored and nothing is counted.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCENARIOS = ("budget", "bid", "user_permissions", "foreign_brand", "expired", "lite_delete")
BODY_MAX_BYTES = 2048
RATE_LIMIT = "10/minute"

DECIDED_AT = _dt.datetime(2026, 10, 12, 14, 0, tzinfo=_dt.timezone.utc)
EXPIRED_AT = _dt.datetime(2027, 1, 2, 9, 0, tzinfo=_dt.timezone.utc)
ACCOUNT = {"advertiserAccountId": "ENTITYDEMO123"}
AGENCY_DID = "did:moltrust:ad5de00000a6e0c1"
FINANCE_DID = "did:moltrust:ad5de0000f1a0ce1"
# What the agency's own system knows about the account (same as the SDK demo).
STATE = {
    "campaigns": {"CAMP-ACME-1": {"brand": "Acme Outdoor", "daily_budget": 12_000},
                  "CAMP-ACME-2": {"brand": "Acme Outdoor", "daily_budget": 15_000},
                  "CAMP-RIVAL-7": {"brand": "Rival Gear", "daily_budget": 30_000}},
    "targets": {"TGT-ACME-KW-1": {"brand": "Acme Outdoor", "bid": 120}},
    "account_daily_total": 57_000,
}
CURRENT_BID_CENTS = 120


class DemoRequest(BaseModel):
    """Body of POST /ads/demo. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    scenario: Literal["budget", "bid", "user_permissions", "foreign_brand", "expired",
                      "lite_delete"]
    daily_budget: float = Field(
        180.0, ge=0, le=100_000,
        description="USD per day, used by scenario 'budget'. <=200 allow, "
                    "200-500 hold, >500 forbid.")
    bid_change_percent: float = Field(
        60.0, ge=-100, le=1000,
        description="Change of the USD 1.20 keyword bid in percent, used by scenario "
                    "'bid'. Within +/-25 % and at most USD 5.00 is allowed.")


def _sdk():
    """The adapter, from the installed package or from this repo's sdk/python/src."""
    try:
        from moltrust_enforce.adapters import amazon_ads
    except ImportError:
        src = str(Path(__file__).resolve().parents[1] / "sdk" / "python" / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from moltrust_enforce.adapters import amazon_ads
    return amazon_ads


def _demo_pub(name: str) -> str:
    """Public key of the SDK demo's fixed-seed demo key (belongs to nobody)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(b"moltrust:ads-demo:" + name.encode()).digest())
    return key.public_key().public_bytes_raw().hex()


def profile():
    ads = _sdk()
    return ads.AdsProfile(
        mandate_id="demo-agency-acme-2026q4",
        principal={"did": AGENCY_DID, "public_key": _demo_pub("agency")},
        ratification_authorities=({"did": FINANCE_DID, "role": "agency_finance",
                                   "public_key": _demo_pub("finance")},),
        tools=dict(ads.DOCUMENTED_TOOLS),
        account_ids=("ENTITYDEMO123",),
        brands=("Acme Outdoor",),
        daily_budget_cap=50_000,
        hold_threshold=20_000,
        total_daily_budget_cap=200_000,
        period_budget_cap=1_500_000,
        max_bid=500,
        max_bid_change_bp=2_500,
        valid_from="2026-10-01T00:00:00Z",
        valid_until="2026-12-31T23:59:59Z",
    )


def _budget(campaign: str, value: float) -> dict:
    return {"body": {"accessRequestedAccount": ACCOUNT, "campaigns": [{
        "campaignId": campaign,
        "budgets": [{"budgetType": "MONETARY", "recurrenceTimePeriod": "DAILY",
                     "budgetValue": {"monetaryBudgetValue": {
                         "monetaryBudget": {"value": value}}}}]}]}}


def call_for(req: DemoRequest):
    """(tool, args, decision time) for a scenario."""
    s = req.scenario
    if s == "budget":
        return ("campaign_management-update_campaign_budget",
                _budget("CAMP-ACME-1", round(req.daily_budget, 2)), DECIDED_AT)
    if s == "bid":
        new_cents = round(CURRENT_BID_CENTS * (1 + req.bid_change_percent / 100))
        return ("campaign_management-update_target_bid",
                {"body": {"accessRequestedAccount": ACCOUNT,
                          "targets": [{"targetId": "TGT-ACME-KW-1",
                                       "bid": {"bid": round(new_cents / 100, 2)}}]}},
                DECIDED_AT)
    if s == "user_permissions":
        return ("user_permissions-update_user_permissions",
                {"body": {"accessRequestedAccount": ACCOUNT,
                          "userPermissions": [{"userId": "someone",
                                               "permissions": ["ADMIN"]}]}}, DECIDED_AT)
    if s == "foreign_brand":
        return ("campaign_management-update_campaign_budget",
                _budget("CAMP-RIVAL-7", 100.0), DECIDED_AT)
    if s == "expired":
        return ("campaign_management-update_campaign_budget",
                _budget("CAMP-ACME-1", 180.0), EXPIRED_AT)
    if s == "lite_delete":
        return ("amazon_ads_mcp-invoke_tool",
                {"toolName": "campaign_management-delete_campaign",
                 "arguments": {"body": {"accessRequestedAccount": ACCOUNT,
                                        "campaignIds": ["CAMP-ACME-1"]}}}, DECIDED_AT)
    raise ValueError(f"unknown scenario {s!r}")


class _KernelClient:
    """`check` against the server's own kernel, in-process. The demo never ratifies."""

    def check(self, mandate, transaction):
        from app.enforcement.enforce_check import enforce_check
        r = enforce_check(mandate, transaction)
        return SimpleNamespace(verdict=r["verdict"], reason=r["reason"],
                               grant_index=r["grant_index"], trace=r["trace"],
                               core=r["core"], core_digest=r["core_digest"])

    def ratify(self, *a, **kw):  # pragma: no cover - decide() never ratifies
        raise RuntimeError("the demo does not ratify")


def run(req: DemoRequest) -> dict:
    ads = _sdk()
    tool, args, when = call_for(req)
    guard = ads.AmazonAdsGuard(_KernelClient(), profile(), clock=lambda: when)
    outcome = guard.decide(tool, args, STATE)
    worst = next(c for c in outcome.checks if c.disposition == outcome.disposition)
    return {
        "scenario": req.scenario,
        "disposition": outcome.disposition,
        "reason": outcome.reason,
        "tool": outcome.tool,
        "change": worst.check.change,
        "decided_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "record": {"core_digest": worst.verdict.core_digest},
        "mandate_digest": worst.verdict.core["mandate_digest"],
    }
