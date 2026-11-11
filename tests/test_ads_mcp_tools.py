"""MolTrust Ads MCP tools: onboarding, template, check — and the credential gate.

The check runs the real kernel behind an in-process transport, so the verdicts here are
the ones /enforce/check would give for the same mandate and transaction.
"""
import asyncio
import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk", "python", "src"))

import ads_mcp_tools as ads  # noqa: E402
from moltrust_enforce import enforce_check  # noqa: E402
from moltrust_enforce.client import EnforceClient  # noqa: E402


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture()
def tools():
    fake = FakeMCP()
    ads.register_ads_tools(fake)
    return fake.tools


def run(coro):
    return json.loads(asyncio.run(coro))


def template(registered, **over):
    args = dict(account_ids=["ENTITY1ABC"], brands=["Acme"],
                valid_from="2026-01-01T00:00:00Z", valid_until="2099-12-31T23:59:59Z")
    args.update(over)
    return run(registered["ads_mandate_template"](**args))


def kernel_client():
    def handler(request):
        body = json.loads(request.content)
        r = enforce_check(body["mandate"], body["transaction"])
        return httpx.Response(200, json={
            "verdict": r["verdict"], "reason": r["reason"], "grant_index": r["grant_index"],
            "trace": r["trace"], "record": {"core": r["core"], "core_digest": r["core_digest"]}})
    return EnforceClient("https://local", api_key="k", transport=httpx.MockTransport(handler))


def budget_args(value, campaign="C1"):
    return {"body": {"accessRequestedAccount": {"advertiserAccountId": "ENTITY1ABC"},
                     "campaigns": [{"campaignId": campaign, "budgets": [{
                         "budgetType": "MONETARY", "recurrenceTimePeriod": "DAILY",
                         "budgetValue": {"monetaryBudgetValue": {
                             "monetaryBudget": {"value": value}}}}]}]}}


CTX = {"campaigns": {"C1": {"brand": "Acme", "daily_budget": 10000}},
       "account_daily_total": 10000}


def test_three_tools_registered(tools):
    assert set(tools) == {"ads_onboard", "ads_mandate_template", "ads_check"}


def test_onboard_names_plans_and_never_grantable_groups(tools):
    d = run(tools["ads_onboard"]())
    assert d["plans"]["free"]["ad_accounts"] == 1
    assert d["plans"]["pro"]["price_usd_per_ad_account_per_month"] == 49
    assert "user_permissions-*" in d["never_grantable"]
    assert "not affiliated with or endorsed by Amazon" in d["notice"]


def test_template_builds_a_mandate_in_cents(tools):
    d = template(tools)
    assert d["profile"]["daily_budget_cap"] == 50000 and d["profile"]["hold_threshold"] == 20000
    assert d["mandate"]["profile"] == "ads" and d["grants"] == len(d["mandate"]["grants"]) > 0


def test_template_refuses_undocumented_tool(tools):
    d = template(tools, tools=["user_permissions-update_user_permissions"])
    assert d["error"] == "invalid_profile"


def test_template_refuses_hold_above_cap(tools):
    assert template(tools, hold_threshold_usd=900)["error"] == "invalid_profile"


@pytest.mark.parametrize("arguments", [
    {"headers": {"Authorization": "Bearer x"}},
    {"token": "Atza|IwEBIExampleExampleExampleExample"},
    {"body": {"client_secret": "x"}},
    {"api_key": "mt_live_whatever"},
])
def test_check_refuses_credentials_and_never_echoes_them(tools, arguments):
    profile = template(tools)["profile"]
    out = asyncio.run(tools["ads_check"](profile=profile, tool="x", arguments=arguments))
    assert json.loads(out)["error"] == "credential_shaped_input_rejected"
    assert "Atza|" not in out and "mt_live_whatever" not in out


def test_check_without_key_asks_for_one(tools):
    profile = template(tools)["profile"]
    d = run(tools["ads_check"](profile=profile, tool="campaign_management-query_campaign",
                               arguments={}))
    assert d["error"] == "api_key_required"


@pytest.mark.parametrize("value,expected", [(150.0, "allow"), (300.0, "hold"), (900.0, "forbid")])
def test_run_check_gives_the_kernel_verdict(tools, value, expected):
    profile = template(tools)["profile"]
    d = ads.run_check("k", profile, "campaign_management-update_campaign_budget",
                      budget_args(value), CTX, client=kernel_client())
    assert d["disposition"] == expected
    assert d["checks"][0]["core_digest"].startswith("sha256:")


def test_run_check_forbids_access_tools(tools):
    profile = template(tools)["profile"]
    d = ads.run_check("k", profile, "user_permissions-update_user_permissions",
                      {"body": {"accessRequestedAccount": {"advertiserAccountId": "ENTITY1ABC"}}},
                      CTX, client=kernel_client())
    assert d["disposition"] == "forbid" and d["next"] == "do not send the call"


def test_plain_arguments_pass_the_gate():
    assert ads._reject_secrets(arguments=budget_args(150.0), context=CTX) is None
