"""Deposit constraints for Verifiable Intent open payment mandates (`ch.moltrust.deposit.*`).

A savings agent that moves cash between banks carries an open payment mandate. The five
types below bound how it may move deposits. `check_transfer` is the agent-side check that
runs before the agent signs a closed payment mandate for one planned transfer, and it
returns one of the three dispositions the rest of this package uses: `allow`, `hold` or
`forbid`, with the reason.

Types (field names per the public draft schema, see `docs/deposit-constraints-vi-mapping.md`):

- `ch.moltrust.deposit.min_hold_days` — `{"days"}`. Funds that arrived at an account under
  this mandate may leave it again only after `days` full days.
- `ch.moltrust.deposit.sweep_threshold_bp` — `{"min_rate_diff_bp", "rate_basis"}`. The
  target's rate minus the source's rate, in basis points, must reach the threshold.
  `rate_basis` names the published rate that is compared (`APY` or `NOMINAL`).
- `ch.moltrust.deposit.max_sweeps_per_period` — `{"max", "period"}`. At most `max`
  transfers per period; `period` is an ISO 20022 frequency code as in VI §4.6.
- `ch.moltrust.deposit.insured_counterparty_only` — `{"schemes", "max_insured_balance"}`.
  The target must be covered by one of the listed deposit guarantee schemes, and the
  balance at the target after the transfer stays at or below `max_insured_balance`.
- `ch.moltrust.deposit.execution_window_hours` — `{"hours"}`. The transfer executes at
  `trigger_time + window_offset_seconds(mandate_id, trigger_id, hours)`, within
  `TOLERANCE_SECONDS`. The offset is a hash, so anyone can recompute it later.

Amounts are integer minor units per ISO 4217, as everywhere in VI (§4.4). Times are
RFC 3339 UTC seconds, parsed by the same strict reader as AER (`parse_timestamp`).

Purity
------
The check reads no clock, opens no connection and asks no source. Rates and deposit
insurance status come only from an AER evidence bundle: signed statements with a validity
window, bound to this mandate and this transfer, and judged as they stood at the trigger
(`decision_timestamp == trigger_time`). The kernel checks structure, binding and windows;
signatures are checked by `verify_deposit_evidence` against a pinned trust list, exactly as
V2 in `verify_record`. Same inputs, same disposition, on any machine.

Fail-closed
-----------
A malformed deposit constraint, transfer, history or bundle is `forbid`, never an exception
and never a silent pass. An unknown type inside the `ch.moltrust.deposit.` namespace is
malformed: in this namespace every type is known. Types outside the namespace (for example
`mandate.payment.amount_range`) are left to the VI verifier and preserved untouched.
Fields this module does not read are preserved too (VI §3.2): parsing never rewrites or
drops anything in the constraint object.
"""
from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ._core import _TAG_MANDATE, _TAG_TRANSACTION, MAX_ABS_INT, _ct_eq, _is_int
from .evidence import _digest, bundle_problem, evidence_values, parse_timestamp, query_key
from .verify import _check_v2, _check_v3, _Report

ALLOW = "allow"
HOLD = "hold"
FORBID = "forbid"

NAMESPACE = "ch.moltrust.deposit."

MIN_HOLD_DAYS = "ch.moltrust.deposit.min_hold_days"
SWEEP_THRESHOLD_BP = "ch.moltrust.deposit.sweep_threshold_bp"
MAX_SWEEPS_PER_PERIOD = "ch.moltrust.deposit.max_sweeps_per_period"
INSURED_COUNTERPARTY_ONLY = "ch.moltrust.deposit.insured_counterparty_only"
EXECUTION_WINDOW_HOURS = "ch.moltrust.deposit.execution_window_hours"

#: The five types, in the order `check_transfer` evaluates them (the order of the draft sketch).
DEPOSIT_TYPES = (MIN_HOLD_DAYS, SWEEP_THRESHOLD_BP, MAX_SWEEPS_PER_PERIOD,
                 INSURED_COUNTERPARTY_ONLY, EXECUTION_WINDOW_HOURS)

#: Allowed deviation, in seconds, between the executed time and `trigger_time + offset`.
#: Five minutes: the same clock-skew allowance the gate uses (`DEFAULT_MAX_AGE_SECONDS`).
#: Large enough for a payment rail that books on the minute, small enough against a window
#: of one hour or more, so the spread the window creates is not undone by the tolerance.
TOLERANCE_SECONDS = 300

#: Rate bases a mandate may name. The evidence states the basis it observed in its query.
RATE_BASES = ("APY", "NOMINAL")

#: ISO 20022 frequency codes from VI §4.6 that delimit a counting period. `INDA` (intraday)
#: is listed there too but has no defined length, so it cannot bound a count and is refused.
PERIOD_CODES = ("DAIL", "WEEK", "TOWK", "TWMN", "MNTH", "TOMN", "QUTR", "FOMN",
                "SEMI", "YEAR", "TYEA")
REFUSED_PERIOD_CODES = ("INDA",)

#: Evidence query kinds. A rate source answers one query per institution and basis, an
#: insurance source one per institution.
RATE_QUERY_KIND = "ch.moltrust.deposit.rate"
INSURANCE_QUERY_KIND = "ch.moltrust.deposit.insurance"

# Bounds, so a mandate or a history cannot slow the check down by size.
MAX_WINDOW_HOURS = 8760          # one year
MAX_SCHEMES = 64
MAX_ID_LEN = 512
MAX_HISTORY = 10000
MAX_DEPOSIT_CONSTRAINTS = 64

_DAY = 86400


@dataclass(frozen=True)
class TransferDecision:
    """Outcome of `check_transfer`.

    `disposition` is `allow`, `hold` or `forbid`. `reason` is None on `allow`, otherwise the
    reason in words; for the six outcomes of the draft sketch it is the sketch's wording.
    `constraint_type` and `constraint_index` (position in the mandate's `constraints`)
    name the constraint that decided, if one did. `detail` carries the numbers behind it.
    """

    disposition: str
    reason: Optional[str]
    constraint_type: Optional[str] = None
    constraint_index: Optional[int] = None
    detail: Any = None

    def __iter__(self):
        # `disposition, reason = check_transfer(...)` as in the draft sketch.
        return iter((self.disposition, self.reason))


# ------------------------------------------------------------------- helpers

def _is_nat(x: Any) -> bool:
    return _is_int(x) and x >= 0


def _id_ok(x: Any) -> bool:
    return isinstance(x, str) and 0 < len(x) <= MAX_ID_LEN


def _currency_ok(x: Any) -> bool:
    # Shape of an ISO 4217 alphabetic code. The code list itself is not pinned here.
    return isinstance(x, str) and len(x) == 3 and x.isascii() and x.isalpha() and x.isupper()


def _money_problem(m: Any, what: str) -> Optional[str]:
    if not isinstance(m, dict):
        return f"{what} is not an object"
    if not _currency_ok(m.get("currency")):
        return f"{what}.currency is not an ISO 4217 alphabetic code"
    if not _is_nat(m.get("amount")):
        return f"{what}.amount is not a non-negative integer in minor units"
    return None


# --------------------------------------------------------------- offset (public)

def window_offset_seconds(mandate_id: str, trigger_id: str, hours: int) -> int:
    """Execution offset in seconds, in `[0, hours * 3600)`.

    `int.from_bytes(sha256(f"{mandate_id}|{trigger_id}")[:8], "big") % (hours * 3600)`,
    UTF-8 encoded, exactly as in the draft sketch. Each agent gets its own delay for the same
    trigger, and a third party recomputes it from the two identifiers alone.

    Raises ValueError on input that would make the derivation ambiguous or undefined: a
    `mandate_id` containing `|` (then `"a|b" + "c"` and `"a" + "b|c"` would hash alike), an
    empty identifier, or `hours` outside `1..MAX_WINDOW_HOURS`.
    """
    if not _id_ok(mandate_id) or "|" in mandate_id:
        raise ValueError("mandate_id must be a bounded non-empty string without '|'")
    if not _id_ok(trigger_id):
        raise ValueError("trigger_id must be a bounded non-empty string")
    if not _is_int(hours) or not 1 <= hours <= MAX_WINDOW_HOURS:
        raise ValueError(f"hours must be an integer in 1..{MAX_WINDOW_HOURS}")
    seed = hashlib.sha256(f"{mandate_id}|{trigger_id}".encode("utf-8")).digest()
    return int.from_bytes(seed[:8], "big") % (hours * 3600)


# ------------------------------------------------------------ evidence queries

def rate_query(institution: str, rate_basis: str) -> Dict[str, str]:
    """The AER query a rate observation answers. Value: the rate as an integer in basis
    points (`415` = 4.15 %), negative allowed."""
    return {"kind": RATE_QUERY_KIND, "institution": institution, "rate_basis": rate_basis}


def insurance_query(institution: str) -> Dict[str, str]:
    """The AER query an insurance observation answers. Value: the scheme code that covers
    the institution's deposits (for example `"US-FDIC"`), or any other string for none."""
    return {"kind": INSURANCE_QUERY_KIND, "institution": institution}


# ------------------------------------------------------------- period arithmetic

def period_key(code: str, epoch: int) -> Tuple[int, ...]:
    """The calendar period, in UTC, that contains `epoch`, for an ISO 20022 frequency code.

    Two times fall into the same period exactly when their keys are equal. Periods are
    calendar periods, not rolling windows: `MNTH` is the calendar month, `QUTR` the calendar
    quarter, `WEEK` the ISO week from Monday 00:00 UTC. Multi-unit periods are anchored so
    that each is a fixed partition: `TOWK` in 14-day blocks from Monday 1969-12-29, `TWMN`
    as days 1-15 and 16-end, `TOMN`/`FOMN`/`SEMI` from January, `TYEA` from even years.
    """
    dt = datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
    day = epoch // _DAY                  # 0 = Thursday 1970-01-01
    y, m = dt.year, dt.month
    if code == "DAIL":
        return (day,)
    if code == "WEEK":
        return ((day + 3) // 7,)
    if code == "TOWK":
        return ((day + 3) // 14,)
    if code == "TWMN":
        return (y, m, 0 if dt.day <= 15 else 1)
    if code == "MNTH":
        return (y, m)
    if code == "TOMN":
        return (y, (m - 1) // 2)
    if code == "QUTR":
        return (y, (m - 1) // 3)
    if code == "FOMN":
        return (y, (m - 1) // 4)
    if code == "SEMI":
        return (y, (m - 1) // 6)
    if code == "YEAR":
        return (y,)
    if code == "TYEA":
        return (y // 2,)
    raise ValueError(f"not a counting period code: {code!r}")


# ------------------------------------------------------------- constraint parsing

def is_deposit_constraint(constraint: Any) -> bool:
    """Is this a constraint in the `ch.moltrust.deposit.` namespace (known type or not)?"""
    return (isinstance(constraint, dict) and isinstance(constraint.get("type"), str)
            and constraint["type"].startswith(NAMESPACE))


def deposit_constraint_problem(c: Any) -> Optional[str]:
    """None if `c` is a well-formed deposit constraint, otherwise the reason.

    Only the fields this module reads are checked. Further fields are allowed and stay in
    the object (VI §3.2).
    """
    if not isinstance(c, dict):
        return "constraint is not an object"
    ctype = c.get("type")
    if not isinstance(ctype, str) or not ctype:
        return "constraint has no type (VI §3.1)"
    if ctype not in DEPOSIT_TYPES:
        return f"unknown type in the {NAMESPACE}* namespace: {ctype}"
    if ctype == MIN_HOLD_DAYS:
        if not _is_nat(c.get("days")):
            return "days is not a non-negative integer"
    elif ctype == SWEEP_THRESHOLD_BP:
        if not _is_nat(c.get("min_rate_diff_bp")):
            return "min_rate_diff_bp is not a non-negative integer"
        if c.get("rate_basis") not in RATE_BASES:
            return f"rate_basis is not one of {list(RATE_BASES)}"
    elif ctype == MAX_SWEEPS_PER_PERIOD:
        if not _is_nat(c.get("max")):
            return "max is not a non-negative integer"
        period = c.get("period")
        if period in REFUSED_PERIOD_CODES:
            return f"period {period} has no defined length and cannot bound a count"
        if period not in PERIOD_CODES:
            return "period is not an ISO 20022 frequency code (VI §4.6)"
    elif ctype == INSURED_COUNTERPARTY_ONLY:
        schemes = c.get("schemes")
        if not isinstance(schemes, list) or len(schemes) > MAX_SCHEMES:
            return f"schemes is not an array of at most {MAX_SCHEMES} entries"
        if any(not _id_ok(s) for s in schemes):
            return "schemes contains an entry that is not a bounded non-empty string"
        if len(set(schemes)) != len(schemes):
            return "schemes contains a duplicate"
        problem = _money_problem(c.get("max_insured_balance"), "max_insured_balance")
        if problem is not None:
            return problem
    elif ctype == EXECUTION_WINDOW_HOURS:
        hours = c.get("hours")
        if not _is_int(hours) or not 1 <= hours <= MAX_WINDOW_HOURS:
            return f"hours is not an integer in 1..{MAX_WINDOW_HOURS}"
    return None


def parse_deposit_constraints(constraints: Any) -> Tuple[List[Tuple[int, dict]], Optional[str]]:
    """Pick the deposit constraints out of a VI `constraints` array and validate them.

    Returns `([(index, constraint), ...], problem)`. The constraint objects are the original
    ones, not copies with fields stripped. Constraints outside the namespace are skipped
    here, not rejected: they belong to the VI verifier (§5.3).
    """
    if not isinstance(constraints, list):
        return [], "constraints is not an array"
    picked: List[Tuple[int, dict]] = []
    for i, c in enumerate(constraints):
        if not isinstance(c, dict) or not isinstance(c.get("type"), str):
            return [], f"constraints[{i}]: constraint has no type (VI §3.1)"
        if not is_deposit_constraint(c):
            continue
        problem = deposit_constraint_problem(c)
        if problem is not None:
            return [], f"constraints[{i}]: {problem}"
        picked.append((i, c))
    if len(picked) > MAX_DEPOSIT_CONSTRAINTS:
        return [], f"more than {MAX_DEPOSIT_CONSTRAINTS} deposit constraints"
    return picked, None


# ------------------------------------------------------------ input validation

def _mandate_problem(mandate: Any) -> Optional[str]:
    if not isinstance(mandate, dict):
        return "mandate is not an object"
    mid = mandate.get("mandate_id")
    if not _id_ok(mid) or "|" in mid:
        return "mandate_id is not a bounded non-empty string without '|'"
    return None


def _transfer_problem(t: Any, mandate_id: str) -> Optional[str]:
    if not isinstance(t, dict):
        return "transfer is not an object"
    if not _ct_eq(t.get("mandate_id"), mandate_id):
        return "transfer mandate_id does not match the mandate"
    for key in ("source", "target", "trigger_id"):
        if not _id_ok(t.get(key)):
            return f"transfer {key} is not a bounded non-empty string"
    if t["source"] == t["target"]:
        return "transfer source and target are the same institution"
    problem = _money_problem(t.get("amount"), "transfer amount")
    if problem is not None:
        return problem
    trigger = parse_timestamp(t.get("trigger_time"))
    execute = parse_timestamp(t.get("exec_time"))
    if trigger is None:
        return "transfer trigger_time is not an RFC 3339 UTC second"
    if execute is None:
        return "transfer exec_time is not an RFC 3339 UTC second"
    if execute < trigger:
        return "transfer exec_time is before trigger_time"
    return None


def _history_problem(history: Any, exec_epoch: int) -> Optional[str]:
    if not isinstance(history, dict):
        return "history is not an object"
    transfers = history.get("transfers")
    if not isinstance(transfers, list) or len(transfers) > MAX_HISTORY:
        return f"history transfers is not an array of at most {MAX_HISTORY} entries"
    opening = history.get("opening_balances", {})
    if not isinstance(opening, dict):
        return "history opening_balances is not an object"
    for inst, money in opening.items():
        if not _id_ok(inst):
            return "history opening_balances has an institution that is not a bounded string"
        problem = _money_problem(money, f"opening balance at {inst}")
        if problem is not None:
            return problem
    for i, h in enumerate(transfers):
        if not isinstance(h, dict):
            return f"history transfers[{i}] is not an object"
        if not _id_ok(h.get("source")) or not _id_ok(h.get("target")):
            return f"history transfers[{i}] source/target is not a bounded string"
        problem = _money_problem(h.get("amount"), f"history transfers[{i}] amount")
        if problem is not None:
            return problem
        when = parse_timestamp(h.get("exec_time"))
        if when is None:
            return f"history transfers[{i}] exec_time is not an RFC 3339 UTC second"
        if when > exec_epoch:
            return f"history transfers[{i}] executes after the planned transfer"
    return None


def _bundle_binding_problem(bundle: Any, mandate: dict, transfer: dict) -> Optional[str]:
    problem = bundle_problem(bundle)
    if problem is not None:
        return problem
    if not _ct_eq(bundle["mandate_ref"], _digest(_TAG_MANDATE, mandate) or ""):
        return "bundle mandate_ref does not bind this mandate"
    if not _ct_eq(bundle["transaction_ref"], _digest(_TAG_TRANSACTION, transfer) or ""):
        return "bundle transaction_ref does not bind this transfer"
    if not _ct_eq(bundle["decision_timestamp"], transfer["trigger_time"]):
        return "bundle decision_timestamp is not the trigger_time"
    return None


def _observation(values: Dict[str, dict], query: dict, at: int, what: str
                 ) -> Tuple[Any, Optional[str]]:
    """Value of the statement answering `query`, valid at `at`. `(value, problem)`."""
    key = query_key(query)
    statement = values.get(key) if key is not None else None
    if statement is None:
        return None, f"no {what} evidence in the bundle"
    start = parse_timestamp(statement["valid_from"])
    end = parse_timestamp(statement["valid_until"])
    if start is None or end is None or not start <= at <= end:
        return None, f"{what} evidence window does not cover the trigger time"
    return statement["value"], None


# ---------------------------------------------------------------- the check

def check_transfer(mandate: Any, transfer: Any, history: Any,
                   bundle: Any = None) -> TransferDecision:
    """Check one planned transfer against the deposit constraints of an open mandate.

    `mandate`   `{"mandate_id": str, "constraints": [...]}` — the open payment mandate, or
                the part of it that carries these two fields. Other fields are ignored.
    `transfer`  `{"mandate_id", "source", "target", "amount": {"currency", "amount"},
                "trigger_id", "trigger_time", "exec_time"}` — the planned transfer.
    `history`   `{"transfers": [{"source", "target", "amount", "exec_time"}, ...],
                "opening_balances": {institution: {"currency", "amount"}}}` — earlier
                transfers under the same mandate, none after `exec_time`; opening balances
                are optional, an institution without one starts at zero.
    `bundle`    AER evidence bundle with the rate and insurance observations for this
                trigger, bound to `mandate` and `transfer`, `decision_timestamp` equal to
                `trigger_time`. Required when the mandate carries a sweep threshold or an
                insured-counterparty constraint; checked whenever it is passed.

    Order as in the draft sketch: holding period, rate difference, sweep count, coverage,
    insured limit, execution window. The first constraint that fails decides; only the
    execution window yields `hold`, everything else that fails is `forbid`.
    """
    def forbid(reason, ctype=None, index=None, detail=None):
        return TransferDecision(FORBID, reason, ctype, index, detail)

    problem = _mandate_problem(mandate)
    if problem is not None:
        return forbid(problem)
    picked, problem = parse_deposit_constraints(mandate.get("constraints"))
    if problem is not None:
        return forbid(problem)
    problem = _transfer_problem(transfer, mandate["mandate_id"])
    if problem is not None:
        return forbid(problem)
    trigger = parse_timestamp(transfer["trigger_time"])
    execute = parse_timestamp(transfer["exec_time"])
    problem = _history_problem(history, execute)
    if problem is not None:
        return forbid(problem)

    by_type: Dict[str, List[Tuple[int, dict]]] = {t: [] for t in DEPOSIT_TYPES}
    for i, c in picked:
        by_type[c["type"]].append((i, c))

    needs_evidence = bool(by_type[SWEEP_THRESHOLD_BP] or by_type[INSURED_COUNTERPARTY_ONLY])
    values: Dict[str, dict] = {}
    if bundle is not None or needs_evidence:
        problem = _bundle_binding_problem(bundle, mandate, transfer)
        if problem is not None:
            return forbid(problem)
        values = evidence_values(bundle)

    source, target = transfer["source"], transfer["target"]
    amount = transfer["amount"]
    past = history["transfers"]

    # 1. Holding period at the source.
    arrivals = [parse_timestamp(h["exec_time"]) for h in past if h["target"] == source]
    last_in = max(arrivals) if arrivals else None
    for i, c in by_type[MIN_HOLD_DAYS]:
        if last_in is not None:
            held_days = (execute - last_in) // _DAY
            if held_days < c["days"]:
                return forbid("holding period not elapsed", MIN_HOLD_DAYS, i,
                              {"held_days": held_days, "days": c["days"]})

    # 2. Rate difference, from the evidence, at the trigger.
    for i, c in by_type[SWEEP_THRESHOLD_BP]:
        rates = {}
        for side, inst in (("source", source), ("target", target)):
            value, problem = _observation(values, rate_query(inst, c["rate_basis"]),
                                          trigger, f"{side} rate")
            if problem is not None:
                return forbid(problem, SWEEP_THRESHOLD_BP, i)
            if not _is_int(value):
                return forbid(f"{side} rate evidence is not an integer in basis points",
                              SWEEP_THRESHOLD_BP, i)
            rates[side] = value
        diff = rates["target"] - rates["source"]
        if diff < c["min_rate_diff_bp"]:
            return forbid("rate difference below threshold", SWEEP_THRESHOLD_BP, i,
                          {"diff_bp": diff, "min_rate_diff_bp": c["min_rate_diff_bp"],
                           "rate_basis": c["rate_basis"]})

    # 3. Transfers already executed in the period of this one.
    for i, c in by_type[MAX_SWEEPS_PER_PERIOD]:
        here = period_key(c["period"], execute)
        count = sum(1 for h in past
                    if period_key(c["period"], parse_timestamp(h["exec_time"])) == here)
        if count >= c["max"]:
            return forbid("sweep limit reached", MAX_SWEEPS_PER_PERIOD, i,
                          {"count": count, "max": c["max"], "period": c["period"]})

    # 4./5. Coverage of the target, then the balance there after the transfer.
    for i, c in by_type[INSURED_COUNTERPARTY_ONLY]:
        scheme, problem = _observation(values, insurance_query(target), trigger, "insurance")
        if problem is not None:
            return forbid(problem, INSURED_COUNTERPARTY_ONLY, i)
        if not isinstance(scheme, str):
            return forbid("insurance evidence is not a scheme code", INSURED_COUNTERPARTY_ONLY, i)
        # Without a short circuit, like the enum predicate in `_core`. An empty `schemes`
        # never matches: an empty allowlist is unsatisfiable, by the same rule VI §5 sets
        # for its own allowlists.
        hits = sum(1 for s in c["schemes"] if _ct_eq(scheme, s))
        if not hits:
            return forbid("target not covered", INSURED_COUNTERPARTY_ONLY, i,
                          {"scheme": scheme, "schemes": list(c["schemes"])})
        limit = c["max_insured_balance"]
        if amount["currency"] != limit["currency"]:
            return forbid("transfer currency differs from max_insured_balance currency",
                          INSURED_COUNTERPARTY_ONLY, i,
                          {"transfer": amount["currency"], "limit": limit["currency"]})
        balance, problem = _balance_at(target, limit["currency"], history)
        if problem is not None:
            return forbid(problem, INSURED_COUNTERPARTY_ONLY, i)
        if balance + amount["amount"] > limit["amount"]:
            return forbid("above insured limit", INSURED_COUNTERPARTY_ONLY, i,
                          {"balance": balance, "amount": amount["amount"],
                           "max_insured_balance": limit["amount"],
                           "currency": limit["currency"]})

    # 6. Execution window. The only check that holds instead of forbidding: the transfer
    # itself is admissible, only not at this time.
    for i, c in by_type[EXECUTION_WINDOW_HOURS]:
        offset = window_offset_seconds(mandate["mandate_id"], transfer["trigger_id"], c["hours"])
        expected = trigger + offset
        if abs(execute - expected) > TOLERANCE_SECONDS:
            return TransferDecision(HOLD, "outside execution window", EXECUTION_WINDOW_HOURS, i,
                                    {"offset_seconds": offset,
                                     "expected_exec_time": _format_ts(expected),
                                     "tolerance_seconds": TOLERANCE_SECONDS})

    return TransferDecision(ALLOW, None)


def _balance_at(inst: str, currency: str, history: dict) -> Tuple[int, Optional[str]]:
    """Balance at `inst` from the opening balance and the history. `(balance, problem)`.

    Every amount that touches `inst` must be in `currency`; a mixed-currency balance has no
    single value to compare against a limit.
    """
    opening = history.get("opening_balances", {}).get(inst)
    balance = 0
    if opening is not None:
        if opening["currency"] != currency:
            return 0, "opening balance currency differs from max_insured_balance currency"
        balance = opening["amount"]
    for h in history["transfers"]:
        if inst not in (h["source"], h["target"]):
            continue
        if h["amount"]["currency"] != currency:
            return 0, "history currency at the target differs from max_insured_balance currency"
        if h["target"] == inst:
            balance += h["amount"]["amount"]
        if h["source"] == inst:
            balance -= h["amount"]["amount"]
    if balance < 0:
        return 0, "history leaves a negative balance at the target"
    if balance > MAX_ABS_INT:
        return 0, "balance at the target exceeds the integer bound"
    return balance, None


def _format_ts(epoch: int) -> str:
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------ evidence authenticity

def verify_deposit_evidence(bundle: Any, trust_list: Any) -> Optional[str]:
    """None if every item in `bundle` is signed by a source the pinned `trust_list` names and
    is valid at the bundle's `decision_timestamp`; otherwise the reason.

    The same checks as V2 and V3 of `verify_record`, so a rate or insurance observation is
    trusted on the same terms as any other AER evidence. Offline; the trust list is brought
    along, never fetched.
    """
    report = _Report()
    _check_v2(report, bundle, trust_list)
    _check_v3(report, bundle)
    return "; ".join(report.failures) if report.failures else None
