"""MolTrust Ads MCP tools — onboarding, mandate template and verdict for agents that use
the Amazon Ads MCP Server.

Three tools on the hosted endpoint (https://api.moltrust.ch/mcp):

  * ads_onboard           — what an agent needs to start: plans, key, SDK, endpoints. Static.
  * ads_mandate_template  — an ads profile and the AAE mandate built from it. Pure.
  * ads_check             — allow / hold / forbid for one intended Amazon Ads tool call,
                            via POST /enforce/check with the caller's own MolTrust key.

Verdict-only, as on the website: nothing here holds, stores or forwards Amazon
credentials, tokens or Amazon data, and nothing calls an Amazon endpoint. Inputs that look
like credentials (an LwA token, a client secret, a key field) are refused before anything
else runs, and the value is never echoed.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from typing import Any, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_SDK = os.path.join(os.path.dirname(_HERE), "sdk", "python", "src")
if _SDK not in sys.path:
    sys.path.insert(0, _SDK)

from moltrust_enforce import EnforceClient  # noqa: E402
from moltrust_enforce.adapters.amazon_ads import (  # noqa: E402
    DOCUMENTED_TOOLS, AdsProfile, AmazonAdsGuard, build_mandate,
)

from mcp.server.fastmcp import Context  # noqa: E402

from molcheck_mcp_tools import _scan, SecretShapedInput  # noqa: E402

API_URL = os.environ.get("MOLTRUST_API_URL", "http://127.0.0.1:8000")

# Login with Amazon access/refresh tokens and Amazon client secrets. Anything shaped like
# these never reaches the kernel or a log line.
_AMAZON_SECRET = [
    re.compile(r"\bAtz[ar]\|[A-Za-z0-9_\-|]{20,}"),
    re.compile(r"\bamzn1\.oa2-cs\.v1\.[A-Za-z0-9]{20,}"),
]
_AMAZON_SECRET_KEYS = {"authorization", "amazon-ads-clientid", "client_secret", "clientsecret",
                       "refresh_token", "access_token", "lwa_token", "bearer"}

ONBOARDING = {
    "what": "MolTrust checks each Amazon Ads MCP tool call your agent intends to make against "
            "the mandate for that ad account, before the call runs: allow, hold (a named "
            "person approves) or forbid. Your own MCP client makes the Amazon call with your "
            "own credentials; MolTrust never sees them and never calls Amazon.",
    "steps": [
        {"step": 1, "do": "Get a free API key",
         "http": "POST https://api.moltrust.ch/auth/signup {\"email\": \"...\"}",
         "returns": "api_key (shown once)"},
        {"step": 2, "do": "Build a mandate for your ad account",
         "mcp_tool": "ads_mandate_template",
         "or_sdk": "moltrust_enforce.adapters.amazon_ads.build_mandate"},
        {"step": 3, "do": "Check each intended call before it goes to Amazon",
         "mcp_tool": "ads_check",
         "or_sdk": "AmazonAdsGuard(EnforceClient(url, api_key=KEY), profile).call(tool, args, "
                   "your_amazon_call, context)"},
        {"step": 4, "do": "Approve holds",
         "http": "POST https://api.moltrust.ch/enforce/ratify (signature of an authority named "
                 "in the mandate)"},
    ],
    "plans": {
        "free": {"ad_accounts": 1, "checks_per_month": 1000, "price_usd": 0},
        "pro": {"price_usd_per_ad_account_per_month": 49, "cancel": "any month, customer portal",
                "checkout": "POST https://api.moltrust.ch/billing/checkout {\"tier\": \"ads_pro\", "
                            "\"quantity\": n} with X-API-Key"},
    },
    "endpoints": {
        "plan": "GET https://api.moltrust.ch/ads/plan (X-API-Key)",
        "check": "POST https://api.moltrust.ch/enforce/check (X-API-Key, mandate with "
                 "\"profile\": \"ads\")",
        "demo": "POST https://api.moltrust.ch/ads/demo (no key, 10/min)",
    },
    "sdk": "pip install \"moltrust-enforce[client]>=0.7\"",
    "example": "https://github.com/MoltyCel/moltrust-ads-example",
    "spec": "https://github.com/MoltyCel/moltrust-api/blob/main/docs/spec-fakten/aae-ads-profile.md",
    "docs": "https://moltrust.ch/ads/docs.html",
    "documented_tools": sorted(DOCUMENTED_TOOLS),
    "never_grantable": ["user_permissions-*", "user_invitations-*", "user_invitation-*",
                        "manager_accounts-*"],
    "notice": "MolTrust is an independent service provider and is not affiliated with or "
              "endorsed by Amazon. The Amazon Ads MCP Server is in beta; tool names follow "
              "Amazon's public tool schemas as of October 2026.",
}


def _reject_secrets(**inputs: Any) -> Optional[str]:
    """Field name of the first credential-shaped input, or None. Never the value."""
    def walk(v: Any, path: str) -> Optional[str]:
        if isinstance(v, str):
            return path if any(rx.search(v) for rx in _AMAZON_SECRET) else None
        if isinstance(v, dict):
            for k, x in v.items():
                if str(k).lower() in _AMAZON_SECRET_KEYS:
                    return f"{path}.{k}" if path else str(k)
                hit = walk(x, f"{path}.{k}" if path else str(k))
                if hit:
                    return hit
        if isinstance(v, (list, tuple)):
            for i, x in enumerate(v):
                hit = walk(x, f"{path}[{i}]")
                if hit:
                    return hit
        return None

    for name, value in inputs.items():
        hit = walk(value, name)
        if hit:
            return hit
        try:
            _scan(value, name)
        except SecretShapedInput as e:
            return str(e)
    return None


def _refusal(field: str) -> str:
    return json.dumps({
        "error": "credential_shaped_input_rejected",
        "detail": "MolTrust Ads tools take the tool name, its arguments and your account "
                  "state, never Amazon or MolTrust credentials. Send your MolTrust key as "
                  "the X-API-Key header of the MCP connection.",
        "field": field,
    })


def _usd_to_cents(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{label} must be a non-negative number of USD")
    return int(round(value * 100))


def profile_from_args(*, mandate_id: str, account_ids: list, brands: list,
                      daily_budget_cap_usd: float, hold_threshold_usd: float,
                      total_daily_budget_cap_usd: float, period_budget_cap_usd: float,
                      max_bid_usd: float, max_bid_change_percent: float,
                      valid_from: str, valid_until: str, tools: Optional[list],
                      principal_did: str, principal_public_key: str,
                      approvers: Optional[list]) -> AdsProfile:
    chosen = tools or sorted(DOCUMENTED_TOOLS)
    unknown = [t for t in chosen if t not in DOCUMENTED_TOOLS]
    if unknown:
        raise ValueError(f"not a documented Amazon Ads MCP tool for this profile: {unknown}")
    return AdsProfile(
        mandate_id=mandate_id,
        principal={"did": principal_did, "public_key": principal_public_key},
        tools={t: DOCUMENTED_TOOLS[t] for t in chosen},
        account_ids=tuple(str(a) for a in account_ids),
        brands=tuple(str(b) for b in brands),
        daily_budget_cap=_usd_to_cents(daily_budget_cap_usd, "daily_budget_cap_usd"),
        hold_threshold=_usd_to_cents(hold_threshold_usd, "hold_threshold_usd"),
        total_daily_budget_cap=_usd_to_cents(total_daily_budget_cap_usd,
                                             "total_daily_budget_cap_usd"),
        period_budget_cap=_usd_to_cents(period_budget_cap_usd, "period_budget_cap_usd"),
        max_bid=_usd_to_cents(max_bid_usd, "max_bid_usd"),
        max_bid_change_bp=int(round(float(max_bid_change_percent) * 100)),
        valid_from=valid_from, valid_until=valid_until,
        ratification_authorities=tuple(dict(a) for a in (approvers or [])),
    )


def _profile_from_dict(d: dict) -> AdsProfile:
    """The `profile` object ads_mandate_template returns, back into an AdsProfile."""
    return AdsProfile(
        mandate_id=d["mandate_id"], principal=d["principal"], tools=d["tools"],
        account_ids=tuple(d["account_ids"]), brands=tuple(d["brands"]),
        daily_budget_cap=d["daily_budget_cap"], hold_threshold=d["hold_threshold"],
        total_daily_budget_cap=d["total_daily_budget_cap"],
        period_budget_cap=d["period_budget_cap"], max_bid=d["max_bid"],
        max_bid_change_bp=d["max_bid_change_bp"], valid_from=d["valid_from"],
        valid_until=d["valid_until"],
        ratification_authorities=tuple(d.get("ratification_authorities") or ()))


def profile_to_dict(p: AdsProfile) -> dict:
    return {"mandate_id": p.mandate_id, "principal": dict(p.principal), "tools": dict(p.tools),
            "account_ids": list(p.account_ids), "brands": list(p.brands),
            "daily_budget_cap": p.daily_budget_cap, "hold_threshold": p.hold_threshold,
            "total_daily_budget_cap": p.total_daily_budget_cap,
            "period_budget_cap": p.period_budget_cap, "max_bid": p.max_bid,
            "max_bid_change_bp": p.max_bid_change_bp, "valid_from": p.valid_from,
            "valid_until": p.valid_until,
            "ratification_authorities": [dict(a) for a in p.ratification_authorities]}


def _request_api_key(ctx) -> Optional[str]:
    req = getattr(getattr(ctx, "request_context", None), "request", None) if ctx else None
    if req is None:
        return None
    key = req.headers.get("x-api-key")
    if not key:
        auth = req.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    return key or None


def run_check(api_key: str, profile: dict, tool: str, arguments: dict,
              context: Optional[dict], client: Any = None) -> dict:
    p = _profile_from_dict(profile)
    enforce = client or EnforceClient(API_URL, api_key=api_key)
    outcome = AmazonAdsGuard(enforce, p).decide(tool, arguments or {}, context or {})
    return {
        "disposition": outcome.disposition,
        "reason": outcome.reason,
        "tool": outcome.tool,
        "checks": [{"change": c.check.change, "item": c.check.item_ref,
                    "disposition": c.disposition, "reason": c.reason,
                    "core_digest": getattr(c.verdict, "core_digest", None)}
                   for c in outcome.checks],
        "next": {"allow": "send the call to Amazon with your own MCP client",
                 "hold": "get a signature from an approver named in the mandate, then "
                         "POST /enforce/ratify; send the call only on RATIFIED",
                 "forbid": "do not send the call"}[outcome.disposition],
    }


def register_ads_tools(mcp) -> None:
    """Register ads_onboard, ads_mandate_template and ads_check on the shared server."""

    @mcp.tool()
    async def ads_onboard() -> str:
        """How an agent starts with MolTrust Ads: verdicts (allow / hold / forbid) on Amazon
        Ads MCP tool calls before they run. Plans, key sign-up, SDK, endpoints. No input,
        no account needed.
        """
        return json.dumps(ONBOARDING, indent=2)

    @mcp.tool()
    async def ads_mandate_template(account_ids: list, brands: list,
                                   daily_budget_cap_usd: float = 500,
                                   hold_threshold_usd: float = 200,
                                   total_daily_budget_cap_usd: float = 2000,
                                   period_budget_cap_usd: float = 15000,
                                   max_bid_usd: float = 5,
                                   max_bid_change_percent: float = 25,
                                   valid_from: str = "2026-10-01T00:00:00Z",
                                   valid_until: str = "2026-12-31T23:59:59Z",
                                   tools: Optional[list] = None,
                                   mandate_id: str = "ads-mandate-1",
                                   principal_did: str = "did:moltrust:<your agency DID>",
                                   principal_public_key: str = "<ed25519 public key, hex>",
                                   approvers: Optional[list] = None) -> str:
        """Build an ads profile and its AAE mandate for one or more Amazon Ads accounts.

        Pure: nothing is stored. Returns `profile` (pass it to ads_check) and `mandate`
        (what /enforce/check evaluates).

        Args:
            account_ids: advertiserAccountId / profileId values the agent may act on
            brands: brands the agent may touch
            daily_budget_cap_usd: largest daily campaign budget; above it: forbid
            hold_threshold_usd: above it a person named in `approvers` must approve
            total_daily_budget_cap_usd: sum of daily budgets on the account after a change
            period_budget_cap_usd: largest lifetime or monthly budget
            max_bid_usd: largest bid
            max_bid_change_percent: largest bid change per step, in percent
            valid_from: RFC 3339 UTC second
            valid_until: RFC 3339 UTC second; after it every call is forbidden
            tools: allowed tool names (default: all documented in ads_onboard)
            mandate_id: your identifier for this mandate
            principal_did: the agency's DID
            principal_public_key: the agency's Ed25519 public key, hex
            approvers: [{"did", "public_key", "role"}] who may approve a hold
        """
        err = _reject_secrets(account_ids=account_ids, brands=brands, approvers=approvers)
        if err:
            return _refusal(err)
        try:
            p = profile_from_args(
                mandate_id=mandate_id, account_ids=account_ids, brands=brands,
                daily_budget_cap_usd=daily_budget_cap_usd, hold_threshold_usd=hold_threshold_usd,
                total_daily_budget_cap_usd=total_daily_budget_cap_usd,
                period_budget_cap_usd=period_budget_cap_usd, max_bid_usd=max_bid_usd,
                max_bid_change_percent=max_bid_change_percent, valid_from=valid_from,
                valid_until=valid_until, tools=tools, principal_did=principal_did,
                principal_public_key=principal_public_key, approvers=approvers)
            mandate = build_mandate(p)
        except (ValueError, TypeError) as e:
            return json.dumps({"error": "invalid_profile", "detail": str(e)})
        return json.dumps({"profile": profile_to_dict(p), "mandate": mandate,
                           "grants": len(mandate["grants"]),
                           "note": "Money values in the profile are cents; percent values "
                                   "basis points."}, indent=2)

    @mcp.tool()
    async def ads_check(profile: dict, tool: str, arguments: dict,
                        context: Optional[dict] = None,
                        ctx: Optional[Context] = None) -> str:
        """Allow / hold / forbid for one intended Amazon Ads MCP tool call, before it runs.

        Needs your MolTrust API key as the X-API-Key header of the MCP connection (free key:
        POST https://api.moltrust.ch/auth/signup). Counts against your plan like any check.
        Never send Amazon credentials: they are refused.

        Args:
            profile: the `profile` object from ads_mandate_template
            tool: the Amazon Ads MCP tool name, e.g. campaign_management-update_campaign_budget
            arguments: the tool arguments exactly as your agent would send them
            context: your view of the account — campaigns {id: {brand, daily_budget(cents)}},
                     targets {id: {brand, bid(cents)}}, ad_groups, account_daily_total(cents).
                     Values the check cannot derive count as unknown and end in forbid.
        """
        err = _reject_secrets(tool=tool, arguments=arguments, context=context, profile=profile)
        if err:
            return _refusal(err)
        key = _request_api_key(ctx)
        if not key:
            return json.dumps({"error": "api_key_required",
                               "detail": "Send your MolTrust API key as X-API-Key on the MCP "
                                         "connection. Free key: POST "
                                         "https://api.moltrust.ch/auth/signup."})
        try:
            result = await asyncio.to_thread(run_check, key, profile, tool, arguments, context)
            return json.dumps(result, indent=2)
        except (KeyError, TypeError, ValueError) as e:
            return json.dumps({"error": "invalid_input", "detail": f"{type(e).__name__}: {e}"})
