"""Verdict gate for agents that call the Amazon Ads MCP Server.

The adapter sits between an agent and its own Amazon Ads tool call. Before the call runs it
asks `POST /enforce/check` for a verdict on every change the call would make, and runs the
caller's function only when every verdict is PERMIT, or PENDING with a ratification that
came back RATIFIED and APPROVED.

    from moltrust_enforce import EnforceClient
    from moltrust_enforce.adapters.amazon_ads import AdsProfile, AmazonAdsGuard

    guard = AmazonAdsGuard(EnforceClient("https://api.moltrust.ch", api_key=KEY), profile)
    outcome = guard.call("campaign_management-update_campaign_budget", args, my_ads_call,
                         context=my_state)

`my_ads_call(tool, args)` is the caller's own code. It holds the Amazon credentials and
talks to Amazon. This package contains no Amazon client, never sees a token and sends
nothing to an Amazon endpoint. MolTrust receives the tool name and the few values the
profile constrains, and answers allow, hold or forbid.

Tool names and argument shapes follow the public Amazon Ads MCP tool schemas
(https://advertising.amazon.com/API/docs/en-us/mcp/mcp-overview, one OpenAPI file per tool
group). Amazon lists the server as beta; names may change, which is why a profile names
its tools explicitly and nothing is allowed by pattern.

Profile -> mandate
------------------
A tool call is split into checks, one per item it changes (``body.campaigns[]``,
``body.targets[]``, ...) and per kind of change. Each check is a transaction whose action
is ``{"verb": "ads_mcp_call", "tool": <name>, "change": <class>}``:

- ``budget_daily``   daily budget; allow up to ``hold_threshold``, hold up to
                     ``daily_budget_cap``, above that no grant matches -> DENY. The account's
                     daily total after the change is capped by ``total_daily_budget_cap``.
- ``budget_period``  LIFETIME / MONTHLY budget, capped by ``period_budget_cap``.
- ``bid``            bid change on an existing target or ad group, limited to
                     +/- ``max_bid_change_bp`` against the current bid from the caller's
                     state, and to ``max_bid``.
- ``bid_new``        bid on a new target or ad group, limited to ``max_bid``.
- ``other``          any other field of the item (name, state, dates, ...).
- ``read``           read and query tools.

Every check carries the account and the validity window; write checks carry the brand.
A tool without a grant is unaddressed and the kernel answers DENY: the mandate is
deny-by-default. Tools in a ``forbidden_prefixes`` group (user permissions, user
invitations, manager account links) can never be put into a profile.

Where the values come from
--------------------------
The adapter derives every constrained value in code from the tool arguments and from the
caller's own state (``context``). Nothing is taken from the model's text. The kernel keeps
no state and reads no clock, so ``t_min`` (decision minute), current budgets and current
bids are the caller's statement. A value the adapter cannot derive stays absent, and an
absent value fails its constraint: unknown means forbid.

``context`` keys (all optional, money in integer minor units of the account currency):

- ``campaigns``: ``{campaignId: {"brand": str, "daily_budget": int}}``
- ``targets``:   ``{targetId: {"brand": str, "bid": int}}``
- ``ad_groups``: ``{adGroupId: {"brand": str, "bid": int}}``
- ``account_daily_total``: sum of all current daily campaign budgets on the account
- ``brand``: brand for items that name none and are not in the maps above

AER
---
Every check carries an AER bundle and an `f_ext` record over the same mandate and
transaction, recomputable offline with `verify_record` (V1-V4). The package signs nothing:
the caller passes ``attest(statement) -> envelope`` backed by its own key, and the adapter
puts one statement per check into the bundle - query ``{"kind": "ads_decision", ...}``,
value ``t_min``, window exactly the decision second. Without ``attest`` the bundle carries
no statement: V1-V4 still recompute the verdict, but nothing attests when it was taken.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .._core import DENY, PENDING, PERMIT, action_digest
from .._ext_core import f_ext
from ..evidence import build_bundle, make_statement, parse_timestamp
from ..verify import AerVerifyResult, verify_record

VERB = "ads_mcp_call"
TYPE_FIELDS = ["verb", "tool", "change"]

ALLOW, HOLD, FORBID = "allow", "hold", "forbid"
_DISPOSITION = {PERMIT: ALLOW, PENDING: HOLD, DENY: FORBID}
_SEVERITY = {ALLOW: 0, HOLD: 1, FORBID: 2}

# Tool kinds decide which change classes a tool can produce and which grants it gets.
KIND_META = "meta"              # Lite catalogue tools (search/describe); validity only
KIND_READ = "read"              # account + validity
KIND_WRITE = "write"            # + brand; change class "other"
KIND_BUDGET = "budget"          # write + budget_daily / budget_period
KIND_BID = "bid"                # write + bid / bid_new
KIND_BUDGET_BID = "budget_bid"  # ad groups carry both
_KINDS = (KIND_META, KIND_READ, KIND_WRITE, KIND_BUDGET, KIND_BID, KIND_BUDGET_BID)

C_READ, C_OTHER = "read", "other"
C_DAILY, C_PERIOD = "budget_daily", "budget_period"
C_BID, C_BID_NEW = "bid", "bid_new"

# Groups that change who may act on an account. Never grantable.
DEFAULT_FORBIDDEN_PREFIXES = (
    "user_permissions-", "user_invitations-", "user_invitation-", "manager_accounts-",
)

LITE_INVOKE = "amazon_ads_mcp-invoke_tool"

# Documented Amazon Ads MCP tools a profile may allow, with the kind the adapter treats them
# as. A profile picks from here or names its own; nothing outside the profile is allowed.
DOCUMENTED_TOOLS: Dict[str, str] = {
    "amazon_ads_mcp-search_tools": KIND_META,
    "amazon_ads_mcp-describe_tools": KIND_META,
    "campaign_management-query_campaign": KIND_READ,
    "campaign_management-query_ad_group": KIND_READ,
    "campaign_management-query_ad": KIND_READ,
    "campaign_management-query_target": KIND_READ,
    "campaign_management-query_portfolio": KIND_READ,
    "reporting-create_campaign_report": KIND_READ,
    "reporting-retrieve_report": KIND_READ,
    "campaign_management-create_campaign": KIND_BUDGET,
    "campaign_management-update_campaign": KIND_BUDGET,
    "campaign_management-update_campaign_budget": KIND_BUDGET,
    "campaign_management-update_campaign_state": KIND_WRITE,
    "campaign_management-create_ad_group": KIND_BUDGET_BID,
    "campaign_management-update_ad_group": KIND_BUDGET_BID,
    "campaign_management-create_target": KIND_BID,
    "campaign_management-update_target": KIND_BID,
    "campaign_management-update_target_bid": KIND_BID,
}

# Which list in `body` holds the items, and its id field, per tool.
_ITEM_LISTS = {
    "campaign_management-create_campaign": ("campaigns", "campaignId"),
    "campaign_management-update_campaign": ("campaigns", "campaignId"),
    "campaign_management-update_campaign_budget": ("campaigns", "campaignId"),
    "campaign_management-update_campaign_state": ("campaigns", "campaignId"),
    "campaign_management-create_ad_group": ("adGroups", "adGroupId"),
    "campaign_management-update_ad_group": ("adGroups", "adGroupId"),
    "campaign_management-create_target": ("targets", "targetId"),
    "campaign_management-update_target": ("targets", "targetId"),
    "campaign_management-update_target_bid": ("targets", "targetId"),
}
_BID_CHANGE_TOOLS = ("campaign_management-update_target", "campaign_management-update_target_bid",
                     "campaign_management-update_ad_group")
_READ_OPS = ("query_", "list_", "get_", "retrieve_", "search_", "describe_")


class AdsGuardError(Exception):
    """Base class; the call did not run."""


class Forbidden(AdsGuardError):
    def __init__(self, outcome: "GuardOutcome"):
        super().__init__(f"forbidden: {outcome.reason}")
        self.outcome = outcome


class HoldRequired(AdsGuardError):
    """At least one check is PENDING without an approving ratification."""

    def __init__(self, outcome: "GuardOutcome"):
        super().__init__(f"hold: {outcome.reason}")
        self.outcome = outcome


@dataclass(frozen=True)
class AdsProfile:
    """The `ads` profile of an AAE mandate. Money in integer minor units (cents)."""

    mandate_id: str
    principal: Mapping[str, str]
    tools: Mapping[str, str]
    account_ids: Tuple[str, ...]
    brands: Tuple[str, ...]
    daily_budget_cap: int
    hold_threshold: int
    total_daily_budget_cap: int
    period_budget_cap: int
    max_bid: int
    max_bid_change_bp: int
    valid_from: str
    valid_until: str
    ratification_authorities: Tuple[Mapping[str, str], ...] = ()
    forbidden_prefixes: Tuple[str, ...] = DEFAULT_FORBIDDEN_PREFIXES

    def problem(self) -> Optional[str]:
        """None if the profile can be turned into a mandate, else the reason."""
        if not self.tools:
            return "profile allows no tool"
        for name, kind in self.tools.items():
            if kind not in _KINDS:
                return f"tool {name!r} has unknown kind {kind!r}"
            if any(name.startswith(p) for p in self.forbidden_prefixes):
                return f"tool {name!r} is in a forbidden group and cannot be allowed"
            if name == LITE_INVOKE:
                return f"{LITE_INVOKE} is unwrapped, not granted; allow the inner tools"
        if not self.account_ids:
            return "profile names no ad account"
        if not self.brands:
            return "profile names no brand"
        for label in ("daily_budget_cap", "hold_threshold", "total_daily_budget_cap",
                      "period_budget_cap", "max_bid", "max_bid_change_bp"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return f"{label} is not a non-negative integer"
        if self.hold_threshold > self.daily_budget_cap:
            return "hold_threshold is above daily_budget_cap"
        start, end = epoch_minute(self.valid_from), epoch_minute(self.valid_until)
        if start is None or end is None:
            return "valid_from/valid_until is not an RFC 3339 UTC second"
        if start > end:
            return "validity window is inverted"
        return None


def epoch_minute(ts: str) -> Optional[int]:
    seconds = parse_timestamp(ts)
    return None if seconds is None else seconds // 60


def check_action(tool: str, change: str) -> dict:
    return {"verb": VERB, "tool": tool, "change": change}


def _classes(kind: str) -> Tuple[str, ...]:
    return {
        KIND_META: (C_READ,), KIND_READ: (C_READ,), KIND_WRITE: (C_OTHER,),
        KIND_BUDGET: (C_OTHER, C_DAILY, C_PERIOD), KIND_BID: (C_OTHER, C_BID, C_BID_NEW),
        KIND_BUDGET_BID: (C_OTHER, C_DAILY, C_PERIOD, C_BID, C_BID_NEW),
    }[kind]


def build_mandate(profile: AdsProfile) -> dict:
    """Profile -> AAE mandate for `/enforce/check`. Raises ValueError on a bad profile."""
    problem = profile.problem()
    if problem is not None:
        raise ValueError(problem)
    window = {"type": "range", "field": "t_min",
              "lo": epoch_minute(profile.valid_from), "hi": epoch_minute(profile.valid_until)}
    accounts = {"type": "enum", "field": "account_id", "values": list(profile.account_ids)}
    brands = {"type": "enum", "field": "brand", "values": list(profile.brands)}
    max_bid = {"type": "range", "field": "bid", "lo": 0, "hi": profile.max_bid}

    grants: List[dict] = []
    for tool in sorted(profile.tools):
        kind = profile.tools[tool]
        if kind == KIND_META:
            base = [window]
        elif kind == KIND_READ:
            base = [accounts, window]
        else:
            base = [accounts, brands, window]
        for change in _classes(kind):
            binding = action_digest(check_action(tool, change))

            def grant(disposition: str, extra: Iterable[dict] = (), _b=binding, _base=base):
                return {"action_binding": _b, "disposition": disposition,
                        "type_fields": list(TYPE_FIELDS),
                        "constraints": [dict(c) for c in _base] + [dict(c) for c in extra]}

            if change == C_DAILY:
                total = {"type": "range", "field": "total_daily_budget",
                         "lo": 0, "hi": profile.total_daily_budget_cap}
                grants.append(grant(ALLOW, [{"type": "range", "field": "daily_budget",
                                             "lo": 0, "hi": profile.hold_threshold}, total]))
                if profile.hold_threshold < profile.daily_budget_cap:
                    grants.append(grant(HOLD, [{"type": "range", "field": "daily_budget",
                                                "lo": profile.hold_threshold + 1,
                                                "hi": profile.daily_budget_cap}, total]))
            elif change == C_PERIOD:
                grants.append(grant(ALLOW, [{"type": "range", "field": "period_budget",
                                             "lo": 0, "hi": profile.period_budget_cap}]))
            elif change == C_BID:
                grants.append(grant(ALLOW, [max_bid, {"type": "range", "field": "bid_change_bp",
                                                      "lo": -profile.max_bid_change_bp,
                                                      "hi": profile.max_bid_change_bp}]))
            elif change == C_BID_NEW:
                grants.append(grant(ALLOW, [max_bid]))
            else:
                grants.append(grant(ALLOW))

    mandate: dict = {"mandate_version": "1.0", "profile": "ads",
                     "mandate_id": profile.mandate_id,
                     "principal": dict(profile.principal), "grants": grants}
    if profile.ratification_authorities:
        mandate["ratification_authorities"] = [dict(a) for a in profile.ratification_authorities]
    return mandate


# ------------------------------------------------------------------ argument mapping

def bid_change_bp(current: int, new: int) -> Optional[int]:
    """Bid change in basis points, rounded away from zero so a cap is never undercut."""
    if isinstance(current, bool) or isinstance(new, bool):
        return None
    if not isinstance(current, int) or not isinstance(new, int) or current <= 0 or new < 0:
        return None
    delta = (new - current) * 10000
    q, r = divmod(abs(delta), current)
    bp = q + (1 if r else 0)
    return bp if delta >= 0 else -bp


def minor_units(value: Any) -> Optional[int]:
    """Currency amount -> integer minor units, rounded half up. Negative or unreadable -> None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 100 if value >= 0 else None
    if isinstance(value, float):
        return int(value * 100 + 0.5) if value >= 0 and value == value else None
    if isinstance(value, str):
        try:
            return minor_units(float(value))
        except ValueError:
            return None
    return None


def _body(args: Any) -> Mapping:
    if not isinstance(args, Mapping):
        return {}
    body = args.get("body")
    return body if isinstance(body, Mapping) else args


def _account(body: Mapping) -> Optional[str]:
    acc = body.get("accessRequestedAccount")
    if isinstance(acc, Mapping):
        for key in ("advertiserAccountId", "profileId", "managerAccountId"):
            v = acc.get(key)
            if isinstance(v, (str, int)) and not isinstance(v, bool):
                return str(v)
    return None


def _max_amount(values: Iterable[Any]) -> Optional[int]:
    """Largest amount among values; any unreadable value makes the whole answer unknown."""
    found: List[int] = []
    for v in values:
        m = minor_units(v)
        if m is None:
            return None
        found.append(m)
    return max(found) if found else None


def _budget_amounts(budget: Mapping) -> List[Any]:
    """Every money value in one `budgets[]` entry: the base value and each marketplace's."""
    out: List[Any] = []
    value = budget.get("budgetValue")
    mbv = value.get("monetaryBudgetValue") if isinstance(value, Mapping) else None
    if isinstance(mbv, Mapping):
        mb = mbv.get("monetaryBudget")
        if isinstance(mb, Mapping) and "value" in mb:
            out.append(mb["value"])
        for ms in mbv.get("marketplaceSettings") or []:
            mb = ms.get("monetaryBudget") if isinstance(ms, Mapping) else None
            out.append(mb.get("value") if isinstance(mb, Mapping) else None)
    return out or [None]  # an entry with no readable amount is unknown, not zero


def _bid_amounts(bid: Any) -> List[Any]:
    """Every bid value in a `bid` object (`bid`, `defaultBid`, `baseBid`, marketplaces)."""
    if isinstance(bid, (int, float)) and not isinstance(bid, bool):
        return [bid]
    out: List[Any] = []
    if isinstance(bid, Mapping):
        for key in ("bid", "defaultBid", "baseBid"):
            if key in bid:
                out.append(bid[key])
        for ms in bid.get("marketplaceSettings") or []:
            if isinstance(ms, Mapping):
                out.extend(ms[k] for k in ("bid", "defaultBid") if k in ms)
            else:
                out.append(None)
    return out or [None]


@dataclass(frozen=True)
class Check:
    """One transaction the adapter sends for a verdict."""

    change: str
    item_ref: Optional[str]
    facts: Mapping[str, Any]


def _ref(value: Any) -> Optional[str]:
    return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else None


def split_call(tool: str, args: Any, context: Mapping) -> List[Check]:
    """Tool call -> list of checks.

    A tool without a known item list is one check: ``read`` if its operation reads, else
    ``other``. A write tool whose items cannot be read gives one ``other`` check without a
    brand, which ends in DENY.
    """
    body = _body(args)
    account = _account(body)
    base: Dict[str, Any] = {"account_id": account} if account is not None else {}
    ctx_brand = context.get("brand") if isinstance(context.get("brand"), str) else None

    spec = _ITEM_LISTS.get(tool)
    if spec is None:
        op = tool.split("-", 1)[1] if "-" in tool else tool
        if op.startswith(_READ_OPS) or (tool.startswith("reporting-") and
                                        op.startswith("create_") and op.endswith("report")):
            return [Check(C_READ, None, dict(base))]
        facts = dict(base, brand=ctx_brand) if ctx_brand else dict(base)
        return [Check(C_OTHER, None, facts)]

    list_key, id_key = spec
    items = body.get(list_key)
    if not isinstance(items, list) or not items:
        return [Check(C_OTHER, None, dict(base))]

    campaigns = context.get("campaigns") or {}
    known = {"campaignId": campaigns, "targetId": context.get("targets") or {},
             "adGroupId": context.get("ad_groups") or {}}[id_key]
    total_now = context.get("account_daily_total")

    checks: List[Check] = []
    for item in items:
        if not isinstance(item, Mapping):
            checks.append(Check(C_OTHER, None, dict(base)))
            continue
        ref = _ref(item.get(id_key))
        state = known.get(ref) if ref is not None else None
        state = state if isinstance(state, Mapping) else {}
        group = (context.get("ad_groups") or {}).get(_ref(item.get("adGroupId"))) \
            if item.get("adGroupId") is not None else None
        parent = campaigns.get(_ref(item.get("campaignId"))) \
            if item.get("campaignId") is not None else None
        brand = item.get("brandId") if isinstance(item.get("brandId"), str) else None
        for source in (state.get("brand"), (group or {}).get("brand"),
                       (parent or {}).get("brand"), ctx_brand):
            if brand is None and isinstance(source, str):
                brand = source
        facts = dict(base)
        if brand is not None:
            facts["brand"] = brand

        changed = False
        budgets = item.get("budgets")
        if budgets is not None:
            for b in budgets if isinstance(budgets, list) else [budgets]:
                changed = True
                if not isinstance(b, Mapping):
                    checks.append(Check(C_DAILY, ref, dict(facts)))
                    continue
                amount = _max_amount(_budget_amounts(b))
                f = dict(facts)
                if b.get("recurrenceTimePeriod") == "DAILY":
                    if amount is not None:
                        f["daily_budget"] = amount
                        current = state.get("daily_budget", 0) if id_key == "campaignId" else 0
                        if isinstance(total_now, int) and isinstance(current, int):
                            f["total_daily_budget"] = total_now - current + amount
                    checks.append(Check(C_DAILY, ref, f))
                else:
                    if amount is not None:
                        f["period_budget"] = amount
                    checks.append(Check(C_PERIOD, ref, f))

        if "bid" in item:
            changed = True
            amount = _max_amount(_bid_amounts(item["bid"]))
            f = dict(facts)
            if amount is not None:
                f["bid"] = amount
            if tool in _BID_CHANGE_TOOLS:
                current = state.get("bid")
                if amount is not None and isinstance(current, int):
                    bp = bid_change_bp(current, amount)
                    if bp is not None:
                        f["bid_change_bp"] = bp
                checks.append(Check(C_BID, ref, f))
            else:
                checks.append(Check(C_BID_NEW, ref, f))

        if set(item) - {id_key, "budgets", "bid"} or not changed:
            checks.append(Check(C_OTHER, ref, dict(facts)))
    return checks


def unwrap_lite(tool: str, args: Any) -> Tuple[str, Any]:
    """Amazon's Lite endpoint wraps every operation in `amazon_ads_mcp-invoke_tool`. The gate
    decides on the inner tool and arguments. The wrapper's argument names are not in
    Amazon's public docs; the readers below accept the common spellings, and anything else
    stays wrapped, which no profile can grant."""
    if tool != LITE_INVOKE or not isinstance(args, Mapping):
        return tool, args
    name = next((args[k] for k in ("toolName", "tool_name", "name", "tool")
                 if isinstance(args.get(k), str)), None)
    inner = next((args[k] for k in ("arguments", "args", "input", "toolInput")
                  if isinstance(args.get(k), Mapping)), None)
    if name is None or inner is None:
        return tool, args
    return name, inner


# ------------------------------------------------------------------ explanation

_MONEY = ("daily_budget", "total_daily_budget", "period_budget", "bid")


def _fmt(field_name: str, value: Any) -> str:
    if field_name in _MONEY and isinstance(value, int):
        return f"{value / 100:,.2f}"
    if field_name == "bid_change_bp" and isinstance(value, int):
        return f"{value / 100:+.2f} %"
    if field_name == "t_min" and isinstance(value, int):
        return _rfc3339_minute(value)
    return str(value)


def explain(trace: Iterable[Mapping]) -> Optional[str]:
    """Name the constraints that failed in every grant the kernel tried.

    Each grant of the ads profile carries the validity window, so the number of `t_min`
    entries is the number of grants evaluated. A field that failed that many times is the
    reason no grant held; a field that failed only in some grants is not.
    """
    entries = [e for e in trace if isinstance(e, Mapping)]
    tried = sum(1 for e in entries if e.get("field") == "t_min")
    if not tried:
        return None
    fails: Dict[str, List[Mapping]] = {}
    for e in entries:
        if e.get("result") == "FAIL" and isinstance(e.get("field"), str):
            fails.setdefault(e["field"], []).append(e)
    parts = []
    for name, hits in fails.items():
        if len(hits) < tried:
            continue
        value, bound = hits[-1].get("value"), hits[-1].get("bound")
        ranges = [h["bound"] for h in hits if isinstance(h.get("bound"), Mapping)]
        if value is None:
            parts.append(f"{name} unknown")
        elif ranges:
            # Allow and hold grants split one range in two; report the span they cover.
            lo = min(r.get("lo") for r in ranges if isinstance(r.get("lo"), int))
            hi = max(r.get("hi") for r in ranges if isinstance(r.get("hi"), int))
            parts.append(f"{name} {_fmt(name, value)} outside {_fmt(name, lo)}..{_fmt(name, hi)}")
        else:
            parts.append(f"{name} {value!r} not allowed")
    return "; ".join(parts) or None


# ------------------------------------------------------------------ outcome

@dataclass
class CheckResult:
    check: Check
    transaction: dict
    verdict: Any
    disposition: str
    reason: str
    aer_record: Optional[dict] = None
    aer_bundle: Optional[dict] = None
    ratification: Any = None

    def aer_verify(self, mandate: Mapping, trust_list: Mapping) -> AerVerifyResult:
        return verify_record(self.aer_record, self.aer_bundle, mandate, self.transaction,
                             trust_list)


@dataclass
class GuardOutcome:
    tool: str
    disposition: str
    reason: str
    mandate: dict
    checks: List[CheckResult] = field(default_factory=list)
    result: Any = None
    executed: bool = False

    def aer_verify(self, trust_list: Mapping) -> List[AerVerifyResult]:
        """Offline V1-V4 over every check of this call. No network."""
        return [c.aer_verify(self.mandate, trust_list) for c in self.checks]


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _rfc3339_minute(t_min: int) -> str:
    return _dt.datetime.fromtimestamp(t_min * 60, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class AmazonAdsGuard:
    """Runs the caller's Amazon Ads call only when MolTrust's verdicts let it through.

    Parameters
    ----------
    client
        A `moltrust_enforce.EnforceClient` (or anything with the same `check`/`ratify`).
    profile
        The `AdsProfile`; turned into a mandate once.
    approver
        Optional ``approver(outcome, check_result) -> (decision, authority_proof)`` for a
        hold. Without it a hold raises `HoldRequired` and the call does not run.
    attest
        Optional ``attest(statement) -> envelope``: the caller signs the decision statement
        with its own key (see AER above).
    clock
        Returns an aware UTC datetime; injectable for tests and demos.
    """

    def __init__(self, client: Any, profile: AdsProfile, *,
                 approver: Optional[Callable[["GuardOutcome", CheckResult],
                                             Tuple[str, dict]]] = None,
                 attest: Optional[Callable[[dict], dict]] = None,
                 clock: Callable[[], _dt.datetime] = _utc_now) -> None:
        self.client = client
        self.profile = profile
        self.mandate = build_mandate(profile)
        self.approver = approver
        self.attest = attest
        self.clock = clock

    def decide(self, tool: str, args: Any, context: Optional[Mapping] = None) -> GuardOutcome:
        """Verdicts only; runs nothing."""
        context = context or {}
        inner_tool, inner_args = unwrap_lite(tool, args)
        t_min = int(self.clock().timestamp()) // 60
        outcome = GuardOutcome(tool=inner_tool, disposition=ALLOW, reason="",
                               mandate=self.mandate)
        for chk in split_call(inner_tool, inner_args, context):
            tx: Dict[str, Any] = {"action": check_action(inner_tool, chk.change),
                                  "t_min": t_min}
            tx.update(chk.facts)
            verdict = self.client.check(self.mandate, tx)
            server = getattr(verdict, "verdict", DENY)
            reason = getattr(verdict, "reason", "")
            if server == DENY:
                if "unaddressed action" in reason:
                    reason = f"tool {inner_tool} ({chk.change}) is not in the mandate"
                else:
                    reason = explain(getattr(verdict, "trace", ()) or ()) or reason
            res = CheckResult(check=chk, transaction=tx, verdict=verdict,
                              disposition=_DISPOSITION.get(server, FORBID), reason=reason)
            self._attach_aer(res, inner_tool, t_min)
            if res.aer_record["verdict"] != server:
                res.disposition = FORBID
                res.reason = (f"server verdict {server} does not recompute "
                              f"(local {res.aer_record['verdict']})")
            outcome.checks.append(res)
        worst = max(outcome.checks, key=lambda r: _SEVERITY[r.disposition])
        outcome.disposition = worst.disposition
        where = f"[{worst.check.item_ref}]" if worst.check.item_ref else ""
        outcome.reason = f"{worst.check.change}{where}: {worst.reason}"
        return outcome

    def call(self, tool: str, args: Any, execute: Callable[[str, Any], Any],
             context: Optional[Mapping] = None) -> GuardOutcome:
        """Decide, and run ``execute(tool, args)`` only if every check is allow or a
        ratified hold. ``execute`` receives exactly what the caller passed in."""
        outcome = self.decide(tool, args, context)
        if outcome.disposition == FORBID:
            raise Forbidden(outcome)
        for res in outcome.checks:
            if res.disposition != HOLD:
                continue
            if self.approver is None:
                raise HoldRequired(outcome)
            decision, proof = self.approver(outcome, res)
            prior = {"core": res.verdict.core, "core_digest": res.verdict.core_digest}
            res.ratification = self.client.ratify(prior, decision, proof)
            if not getattr(res.ratification, "ratified", False) or \
                    getattr(res.ratification, "decision", "") != "APPROVED":
                outcome.reason = f"hold not approved: {getattr(res.ratification, 'reason', '')}"
                raise HoldRequired(outcome)
        outcome.result = execute(tool, args)
        outcome.executed = True
        return outcome

    def _attach_aer(self, res: CheckResult, tool: str, t_min: int) -> None:
        ts = _rfc3339_minute(t_min)
        items = []
        if self.attest is not None:
            nonce = f"{action_digest(res.transaction['action'])}:{res.check.item_ref}:{t_min}"
            statement = make_statement(
                str(self.profile.principal.get("did", "")),
                {"kind": "ads_decision", "mandate": self.profile.mandate_id, "tool": tool,
                 "change": res.check.change, "item": res.check.item_ref or ""},
                t_min, ts, ts, nonce)
            items.append(self.attest(statement))
        res.aer_bundle = build_bundle(items, self.mandate, res.transaction, ts)
        res.aer_record = f_ext(self.mandate, res.transaction, res.aer_bundle)


__all__ = [
    "ALLOW", "HOLD", "FORBID", "KIND_META", "KIND_READ", "KIND_WRITE", "KIND_BUDGET",
    "KIND_BID", "KIND_BUDGET_BID", "DOCUMENTED_TOOLS", "DEFAULT_FORBIDDEN_PREFIXES",
    "LITE_INVOKE", "AdsProfile", "AmazonAdsGuard", "Check", "CheckResult", "GuardOutcome",
    "AdsGuardError", "Forbidden", "HoldRequired", "build_mandate", "split_call",
    "unwrap_lite", "explain", "bid_change_bp", "minor_units", "check_action", "epoch_minute",
]
