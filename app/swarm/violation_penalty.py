"""Violation penalty term for the trust score (TechSpec v0.10).

Two sources, split by adjudicator (verified against live data):
  * adjudicated (admin, adjudicator_type != 'evaluator'): the five §2.7 types.
    Only authorization-abuse / behavioral-fraud are graded (score-affecting);
    identity-spoofing / sybil / clone-impersonation are revoke-tier (score 0,
    handled elsewhere) and never feed this term.
  * constraint-breach (auto, adjudicator_type = 'evaluator'): AAE-evaluator
    guardrail DENYs. Each breached constraint type is classified P/O/A:
      P = penalized (max_transaction_value/validity/single_use/allowed_domains/
          rate_limit); counted once per distinct-unresolved type, capped.
      O = operational-exempt (revocation_check + server fail-closed): NEVER a
          penalty — it is a MolTrust-side deferral, not agent misbehaviour.
      A = attack-signal (nonce replay): never a score penalty (admin review is a
          separate concern / PR).

All reference numbers are config constants (VD5) so calibration is a config
change, not a code change. Pure helpers are import-clean (no db/app deps) so they
unit-test in isolation; the async readers do the DB I/O.
"""

from __future__ import annotations

# --- tunable reference constants (VD5) --------------------------------------
SEVERITY_ADJUDICATED = {
    "authorization-abuse": 15.0,
    "behavioral-fraud": 25.0,
}
GRADED_ADJUDICATED = frozenset(SEVERITY_ADJUDICATED)  # graded admin types

SEVERITY_P = {
    "max_transaction_value": 20.0,
    "validity": 20.0,
    "single_use": 15.0,
    "allowed_domains": 15.0,
    "rate_limit": 10.0,
}
CLASS_P = frozenset(SEVERITY_P)              # penalized constraint types
CLASS_O = frozenset({"revocation_check"})    # operational-exempt (never penalty)
CLASS_A = frozenset({"nonce"})               # attack-signal (never score penalty)

CAP_CB = 30.0        # cap on total constraint_breach contribution
R_TARGET = 5         # distinct new endorsers to fully rehabilitate an adjudicated graded
DECAY_TARGET = 5     # ALLOW evals of a constraint (after its last DENY) to fully decay it

# Operational reason substrings that force class=O regardless of type (VD3).
_OPERATIONAL_REASONS = (
    "fail-closed",
    "evaluation error",
    "not yet enforceable",
    "unknown constraint (required)",
)


def classify_constraint(ctype: str, reason: str = "") -> str:
    """Map a breached constraint to class P/O/A (VD2/VD3).

    Server-side fail-closed reasons force O even for an otherwise-P type, so an
    agent is never penalized for a MolTrust-side deferral.
    """
    r = (reason or "").lower()
    if any(s in r for s in _OPERATIONAL_REASONS):
        return "O"
    if ctype in CLASS_O:
        return "O"
    if ctype in CLASS_A:
        return "A"
    if ctype in CLASS_P:
        return "P"
    return "O"  # unknown/unmapped -> exempt (never penalize the unclassified)


def rehab(distinct_new_endorsers: int) -> float:
    """1.0 (full penalty) -> 0.0 (rehabilitated) over R_TARGET distinct endorsers."""
    return max(0.0, 1.0 - distinct_new_endorsers / R_TARGET)


def decay_c(allow_since_last_deny: int) -> float:
    """0.0 (fresh DENY) -> 1.0 (resolved) over DECAY_TARGET subsequent ALLOWs of c."""
    return min(1.0, allow_since_last_deny / DECAY_TARGET)


def adjudicated_graded_penalty(records: list) -> float:
    """Sum severity(type)*rehab over active adjudicated graded records.

    records: iterable of dicts {type, rehab} (already filtered: confirmed,
    ¬reversed, adjudicator != evaluator, type in GRADED_ADJUDICATED).
    """
    total = 0.0
    for r in records:
        sev = SEVERITY_ADJUDICATED.get(r["type"])
        if sev is not None:
            total += sev * r["rehab"]
    return total


def constraint_breach_penalty(breaches: list) -> float:
    """min(CAP_CB, Σ severity_c(c)·(1−decay_c(c))) over DISTINCT class=P types.

    breaches: iterable of dicts {type, decay} — one entry per distinct unresolved
    class=P constraint type (dedup already done, so 16 single_use DENYs = 1 entry).
    """
    total = 0.0
    for b in breaches:
        sev = SEVERITY_P.get(b["type"])
        if sev is not None:
            total += sev * (1.0 - b["decay"])
    return min(CAP_CB, total)


# --- async DB readers -------------------------------------------------------
async def _adjudicated_breaches(conn, did: str) -> list:
    """Active adjudicated graded records for `did`, each with its rehab factor.

    rehab counts distinct endorsers that vouched for the agent after confirmedAt
    (excluding the record's principal). issued_at/confirmed_at casts per schema.
    """
    rows = await conn.fetch(
        "SELECT violation_type, confirmed_at, principal_did FROM violation_records "
        "WHERE agent_did = $1 AND reversed = false AND adjudicator_type <> 'evaluator' "
        "AND violation_type = ANY($2::text[])",
        did, list(GRADED_ADJUDICATED),
    )
    out = []
    for r in rows:
        new_endorsers = await conn.fetchval(
            "SELECT COUNT(DISTINCT endorser_did) FROM endorsements "
            "WHERE endorsed_did = $1 AND issued_at > $2::timestamptz "
            "AND endorser_did <> $3",
            did, r["confirmed_at"], r["principal_did"],
        )
        out.append({"type": r["violation_type"], "rehab": rehab(int(new_endorsers or 0))})
    return out


async def _constraint_breaches(conn, did: str) -> list:
    """Distinct unresolved class=P constraint types for `did`, each with decay.

    Reads per-constraint verdicts from aae_evaluations.evaluations[] (jsonb) — NOT
    the aggregate row verdict, which is 'one DENY -> whole DENY'. A type is a
    breach if its most recent element is a DENY; decay counts ALLOW elements of
    that type after that last DENY. class from the persisted tag, else derived.
    """
    rows = await conn.fetch(
        "SELECT (elem->>'type') AS ctype, (elem->>'verdict') AS v, "
        "       (elem->>'reason') AS reason, (elem->>'class') AS cls, created_at "
        "FROM aae_evaluations, jsonb_array_elements(evaluations) AS elem "
        "WHERE agent_did = $1 "
        "ORDER BY created_at ASC",
        did,
    )
    per_type: dict[str, list] = {}
    for r in rows:
        ctype = r["ctype"]
        if not ctype:
            continue
        cls = r["cls"] or classify_constraint(ctype, r["reason"] or "")
        if cls != "P":
            continue
        per_type.setdefault(ctype, []).append(r["v"])
    breaches = []
    for ctype, verdicts in per_type.items():
        last_deny = max((i for i, v in enumerate(verdicts) if v == "DENY"), default=-1)
        if last_deny < 0:
            continue  # never denied -> not a breach
        allow_since = sum(1 for v in verdicts[last_deny + 1:] if v == "ALLOW")
        d = decay_c(allow_since)
        if d < 1.0:  # still unresolved
            breaches.append({"type": ctype, "decay": d})
    return breaches


async def compute_violation_penalty(conn, did: str) -> tuple[float, dict]:
    """Return (violation_penalty, breakdown) for `did`. Never raises for a
    missing table/agent — a scoring term must not crash the score."""
    try:
        adj = await _adjudicated_breaches(conn, did)
        cb = await _constraint_breaches(conn, did)
    except Exception:
        return 0.0, {"adjudicated_graded": 0.0, "constraint_breach": 0.0, "error": True}
    ag = adjudicated_graded_penalty(adj)
    cbp = constraint_breach_penalty(cb)
    # Itemize which violation contributes how much, after rehab/decay. The
    # per-item 'applied' values are pre-cap for constraint_breach; the top-level
    # 'constraint_breach' is the CAP_CB-capped total (items sum may exceed it).
    adj_items = [
        {"type": a["type"], "rehab": round(a["rehab"], 2),
         "applied": round(SEVERITY_ADJUDICATED.get(a["type"], 0.0) * a["rehab"], 2)}
        for a in adj
    ]
    cb_items = [
        {"type": b["type"], "decay": round(b["decay"], 2),
         "applied": round(SEVERITY_P.get(b["type"], 0.0) * (1.0 - b["decay"]), 2)}
        for b in cb
    ]
    return ag + cbp, {
        "adjudicated_graded": round(ag, 2),
        "constraint_breach": round(cbp, 2),
        "cap_cb": CAP_CB,
        "adjudicated_items": adj_items,
        "constraint_breach_items": cb_items,
    }
