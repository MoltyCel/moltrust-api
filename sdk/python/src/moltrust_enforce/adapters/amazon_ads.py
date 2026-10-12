"""Verdict gate for agents that call the Amazon Ads MCP Server.

The adapter sits between an agent and its own Amazon Ads tool call. Before the call runs it
asks `POST /enforce/check` for a verdict on the intended call and runs the caller's function
only on PERMIT, or on PENDING after a ratification came back RATIFIED.

    from moltrust_enforce import EnforceClient
    from moltrust_enforce.adapters.amazon_ads import AdsProfile, AmazonAdsGuard

    guard = AmazonAdsGuard(EnforceClient("https://api.moltrust.ch", api_key=KEY), profile)
    outcome = guard.call("campaign_management-update_campaign", args, my_ads_call)

`my_ads_call(tool, args)` is the caller's own code. It holds the Amazon credentials and
talks to Amazon. This package contains no Amazon client, never sees a token and sends
nothing to an Amazon endpoint: MolTrust receives the tool name and the values the profile
constrains, and answers allow, hold or forbid.

Profile -> mandate
------------------
Every allowed tool becomes one grant whose action is ``{"verb": "ads_mcp_call", "tool":
<name>}``. A tool that has no grant is unaddressed and the kernel answers DENY, so the
mandate is deny-by-default. Tools whose name starts with a prefix in ``forbidden_prefixes``
(default ``user_permissions-``) are refused locally as well and can never be put into a
profile.

The values a grant constrains are derived by this adapter, in code, from the tool
arguments and from the caller's own state (``context``). They are never taken from the
model's text:

- ``account_id``      -> enum over the profile's ad accounts / profile IDs
- ``brand``           -> enum over the profile's brands (tools that touch a brand)
- ``daily_budget``    -> integer minor units; allow up to ``hold_threshold``, hold up to
                         ``daily_budget_cap``, above the cap no grant matches -> DENY
- ``total_daily_budget`` -> the caller's sum of daily budgets after this change, capped
                         by ``total_budget_cap``
- ``bid_change_bp``   -> bid change in basis points against the current bid the caller
                         supplies; limited to +/- ``max_bid_change_bp``
- ``t_min``           -> UTC epoch minute of the decision; the validity window

The kernel keeps no state and reads no clock. ``t_min`` and the running budget total are
therefore the caller's own statement.

AER
---
Every decision carries an AER bundle and an `f_ext` record over the same mandate and
transaction, recomputable offline with `verify_record` (V1-V4). The package signs nothing:
the caller passes ``attest(statement) -> envelope`` backed by its own key, and the adapter
puts one statement per decision into the bundle - query ``{"kind": "ads_decision", ...}``,
value ``t_min``, window exactly the decision second. A verifier that trusts the caller's
key then sees who stated the decision time. Without ``attest`` the bundle carries no
statement: V1-V4 still recompute the verdict, but nothing attests when it was taken.
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
TYPE_FIELDS = ["verb", "tool"]
DEFAULT_FORBIDDEN_PREFIXES = ("user_permissions-",)

ALLOW, HOLD, FORBID = "allow", "hold", "forbid"
_DISPOSITION = {PERMIT: ALLOW, PENDING: HOLD, DENY: FORBID}

# Tool kinds decide which constraints a grant carries.
KIND_READ = "read"          # account scope + validity
KIND_WRITE = "write"        # + brand
KIND_BUDGET = "budget"      # + brand, daily budget with hold band, total cap
KIND_BID = "bid"            # + brand, bid change limit
_KINDS = (KIND_READ, KIND_WRITE, KIND_BUDGET, KIND_BID)


class AdsGuardError(Exception):
    """Base class; the call did not run."""


class Forbidden(AdsGuardError):
    def __init__(self, outcome: "GuardOutcome"):
        super().__init__(f"forbidden: {outcome.reason}")
        self.outcome = outcome


class HoldRequired(AdsGuardError):
    """PENDING and no ratification that came back RATIFIED."""

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
    total_budget_cap: int
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
                return f"tool {name!r} is forbidden by prefix and cannot be allowed"
        if not self.account_ids:
            return "profile names no ad account"
        if not self.brands:
            return "profile names no brand"
        for label, value in (("daily_budget_cap", self.daily_budget_cap),
                             ("hold_threshold", self.hold_threshold),
                             ("total_budget_cap", self.total_budget_cap),
                             ("max_bid_change_bp", self.max_bid_change_bp)):
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


def tool_action(tool: str) -> dict:
    return {"verb": VERB, "tool": tool}


def build_mandate(profile: AdsProfile) -> dict:
    """Profile -> AAE mandate for `/enforce/check`. Raises ValueError on a bad profile."""
    problem = profile.problem()
    if problem is not None:
        raise ValueError(problem)
    window = {"type": "range", "field": "t_min",
              "lo": epoch_minute(profile.valid_from), "hi": epoch_minute(profile.valid_until)}
    accounts = {"type": "enum", "field": "account_id", "values": list(profile.account_ids)}
    brands = {"type": "enum", "field": "brand", "values": list(profile.brands)}

    grants: List[dict] = []
    for tool in sorted(profile.tools):
        kind = profile.tools[tool]
        binding = action_digest(tool_action(tool))
        base = [accounts, window] if kind == KIND_READ else [accounts, brands, window]

        def grant(disposition: str, extra: Iterable[dict] = ()) -> dict:
            return {"action_binding": binding, "disposition": disposition,
                    "type_fields": list(TYPE_FIELDS),
                    "constraints": [dict(c) for c in base] + [dict(c) for c in extra]}

        if kind == KIND_BUDGET:
            total = {"type": "range", "field": "total_daily_budget",
                     "lo": 0, "hi": profile.total_budget_cap}
            grants.append(grant(ALLOW, [{"type": "range", "field": "daily_budget",
                                         "lo": 0, "hi": profile.hold_threshold}, total]))
            if profile.hold_threshold < profile.daily_budget_cap:
                grants.append(grant(HOLD, [{"type": "range", "field": "daily_budget",
                                            "lo": profile.hold_threshold + 1,
                                            "hi": profile.daily_budget_cap}, total]))
        elif kind == KIND_BID:
            grants.append(grant(ALLOW, [{"type": "range", "field": "bid_change_bp",
                                         "lo": -profile.max_bid_change_bp,
                                         "hi": profile.max_bid_change_bp}]))
        else:
            grants.append(grant(ALLOW))

    mandate: dict = {"mandate_version": "1.0", "profile": "ads",
                     "mandate_id": profile.mandate_id,
                     "principal": dict(profile.principal), "grants": grants}
    if profile.ratification_authorities:
        mandate["ratification_authorities"] = [dict(a) for a in profile.ratification_authorities]
    return mandate


@dataclass(frozen=True)
class CallFacts:
    """What the adapter derived from one tool call. Money in minor units."""

    account_id: Optional[str] = None
    brand: Optional[str] = None
    daily_budget: Optional[int] = None
    total_daily_budget: Optional[int] = None
    bid_change_bp: Optional[int] = None


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


def build_transaction(tool: str, facts: CallFacts, t_min: int) -> dict:
    tx: Dict[str, Any] = {"action": tool_action(tool), "t_min": t_min}
    for name in ("account_id", "brand", "daily_budget", "total_daily_budget", "bid_change_bp"):
        value = getattr(facts, name)
        if value is not None:
            tx[name] = value
    return tx


@dataclass
class GuardOutcome:
    tool: str
    disposition: str
    reason: str
    verdict: Any
    mandate: dict
    transaction: dict
    ratification: Any = None
    result: Any = None
    executed: bool = False
    aer_record: Optional[dict] = None
    aer_bundle: Optional[dict] = None

    def transaction_digest_hint(self) -> str:
        """Nonce for the decision statement: the action digest plus the decision minute."""
        return f"{action_digest(self.transaction['action'])}:{self.transaction['t_min']}"

    def aer_verify(self, trust_list: Mapping) -> AerVerifyResult:
        """Offline V1-V4 over this decision's AER record. No network."""
        return verify_record(self.aer_record, self.aer_bundle, self.mandate,
                             self.transaction, trust_list)


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _rfc3339_minute(t_min: int) -> str:
    return _dt.datetime.fromtimestamp(t_min * 60, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AmazonAdsGuard:
    """Runs the caller's Amazon Ads call only when MolTrust's verdict lets it through.

    Parameters
    ----------
    client
        A `moltrust_enforce.EnforceClient` (or anything with the same `check`/`ratify`).
    profile
        The `AdsProfile`; turned into a mandate once.
    extract
        ``extract(tool, args, context) -> CallFacts``. Default: `default_extract`, which
        reads the argument names documented for the Amazon Ads MCP Server.
    approver
        Optional ``approver(outcome) -> (decision, authority_proof)`` for a hold. Without
        it a hold raises `HoldRequired` and the call does not run.
    attest
        Optional ``attest(statement) -> envelope``: the caller signs the decision statement
        with its own key (see AER above).
    clock
        Returns an aware UTC datetime; injectable for tests and demos.
    """

    def __init__(self, client: Any, profile: AdsProfile, *,
                 extract: Optional[Callable[[str, Mapping, Mapping], CallFacts]] = None,
                 approver: Optional[Callable[[GuardOutcome], Tuple[str, dict]]] = None,
                 attest: Optional[Callable[[dict], dict]] = None,
                 clock: Callable[[], _dt.datetime] = _utc_now) -> None:
        self.client = client
        self.profile = profile
        self.mandate = build_mandate(profile)
        self.extract = extract or default_extract
        self.approver = approver
        self.attest = attest
        self.clock = clock

    def decide(self, tool: str, args: Mapping, context: Optional[Mapping] = None) -> GuardOutcome:
        """Verdict only; runs nothing."""
        context = context or {}
        t_min = int(self.clock().timestamp()) // 60
        facts = self.extract(tool, args, context)
        tx = build_transaction(tool, facts, t_min)
        # A forbidden-prefix tool is asked like any other so the record exists. No grant can
        # address it (build_mandate refuses to create one), so the kernel answers DENY.
        verdict = self.client.check(self.mandate, tx)
        disposition = _DISPOSITION.get(getattr(verdict, "verdict", DENY), FORBID)
        outcome = GuardOutcome(tool=tool, disposition=disposition,
                               reason=getattr(verdict, "reason", ""), verdict=verdict,
                               mandate=self.mandate, transaction=tx)
        self._attach_aer(outcome, t_min)
        if outcome.aer_record is not None and outcome.aer_record["verdict"] != verdict.verdict:
            # The local recompute disagrees with the server. Fail closed.
            outcome.disposition = FORBID
            outcome.reason = (f"server verdict {verdict.verdict} does not recompute "
                              f"(local {outcome.aer_record['verdict']})")
        return outcome

    def call(self, tool: str, args: Mapping, execute: Callable[[str, Mapping], Any],
             context: Optional[Mapping] = None) -> GuardOutcome:
        """Decide, and run ``execute(tool, args)`` only on allow or a ratified hold."""
        outcome = self.decide(tool, args, context)
        if outcome.disposition == HOLD:
            if self.approver is None:
                raise HoldRequired(outcome)
            decision, proof = self.approver(outcome)
            prior = {"core": outcome.verdict.core, "core_digest": outcome.verdict.core_digest}
            outcome.ratification = self.client.ratify(prior, decision, proof)
            if not getattr(outcome.ratification, "ratified", False) or \
                    getattr(outcome.ratification, "decision", "") != "APPROVED":
                raise HoldRequired(outcome)
        elif outcome.disposition != ALLOW:
            raise Forbidden(outcome)
        outcome.result = execute(tool, args)
        outcome.executed = True
        return outcome

    def _attach_aer(self, outcome: GuardOutcome, t_min: int) -> None:
        ts = _rfc3339_minute(t_min)
        items = []
        if self.attest is not None:
            statement = make_statement(
                str(self.profile.principal.get("did", "")),
                {"kind": "ads_decision", "mandate": self.profile.mandate_id,
                 "tool": outcome.tool},
                t_min, ts, ts, outcome.transaction_digest_hint())
            items.append(self.attest(statement))
        bundle = build_bundle(items, outcome.mandate, outcome.transaction, ts)
        outcome.aer_bundle = bundle
        outcome.aer_record = f_ext(outcome.mandate, outcome.transaction, bundle)


# ------------------------------------------------------------------ argument mapping

def _first(args: Mapping, *names: str) -> Any:
    for n in names:
        if n in args and args[n] is not None:
            return args[n]
    return None


def _minor(value: Any) -> Optional[int]:
    """Currency amount -> integer minor units. Floats are rounded half up to the cent."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 100
    if isinstance(value, float):
        return int((value * 100) + 0.5) if value >= 0 else None
    if isinstance(value, str):
        try:
            return _minor(float(value))
        except ValueError:
            return None
    return None


def default_extract(tool: str, args: Mapping, context: Mapping) -> CallFacts:
    """Map tool arguments to the constrained values.

    ``context`` is the caller's own state and may carry ``account_id``, ``brand``,
    ``current_bid`` (minor units), ``other_daily_budgets`` (minor units, sum of all other
    campaigns' daily budgets) — anything the tool arguments do not carry themselves.
    """
    account = _first(args, "advertiserAccountId", "profileId", "profile_id", "accountId",
                     "advertiser_account_id") or context.get("account_id")
    brand = _first(args, "brand", "brandName", "brand_name") or context.get("brand")
    budget = _minor(_first(args, "dailyBudget", "daily_budget", "budget"))
    total = None
    if budget is not None and isinstance(context.get("other_daily_budgets"), int):
        total = context["other_daily_budgets"] + budget
    bid_bp = None
    new_bid = _minor(_first(args, "bid", "newBid", "defaultBid", "new_bid"))
    if new_bid is not None and isinstance(context.get("current_bid"), int):
        bid_bp = bid_change_bp(context["current_bid"], new_bid)
    return CallFacts(account_id=str(account) if account is not None else None,
                     brand=brand if isinstance(brand, str) else None,
                     daily_budget=budget, total_daily_budget=total, bid_change_bp=bid_bp)


__all__ = [
    "ALLOW", "HOLD", "FORBID", "KIND_READ", "KIND_WRITE", "KIND_BUDGET", "KIND_BID",
    "AdsProfile", "AmazonAdsGuard", "CallFacts", "GuardOutcome", "AdsGuardError",
    "Forbidden", "HoldRequired", "build_mandate", "build_transaction", "bid_change_bp",
    "default_extract", "tool_action", "epoch_minute",
]
