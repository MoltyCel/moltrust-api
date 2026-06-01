"""AAE Evaluator — D3 MANDATE-Enforcement, Komponente 2 (Schritt 3).

Per-Type-Constraint-Handler + Orchestrator. Wertet eine geplante Aktion
(action_context) gegen die CONSTRAINTS + VALIDITY eines gespeicherten Envelopes
aus und schreibt ein SIGNIERTES Eval-Row in aae_evaluations — alles in EINER
advisory-lock-Transaktion (TOCTOU-frei fuer rate_limit/single_use).

Verdict-Form (live ConstraintEvaluation-Mapping):
    {type, threshold, current_value, delta, verdict: ALLOW|DENY, reason}

Kritische Regel (Taxonomie / AAE draft-04 §2.3):
    required:true unauswertbar/unbekannt -> Default-DENY; required:false unbekannt -> ignore.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.enforcement.verdict_sign import sign_verdict

ALLOW = "ALLOW"
DENY = "DENY"

# Numeric-Hardening (D1-Baseline-B-Block): Betraege als integer-minor-units, bounded.
MAX_MINOR_UNITS = 10 ** 15          # Upper-Bound gegen Overflow
# Clock-Skew-Toleranz (D1-B-Block); Platzhalter — mit D1-Wert abgleichen.
CLOCK_SKEW = timedelta(seconds=30)

_KNOWN_CONSTRAINT_TYPES = {"max_transaction_value", "allowed_domains", "rate_limit"}


def _verdict(type_: str, verdict: str, reason: str,
             threshold: Any = None, current: Any = None, delta: Any = None) -> dict:
    return {"type": type_, "threshold": threshold, "current_value": current,
            "delta": delta, "verdict": verdict, "reason": reason}


def _is_required(c: dict) -> bool:
    # required default true wenn fehlend (Taxonomie).
    return bool(c.get("required", True))


def _valid_minor_units(x: Any) -> bool:
    # integer-minor-units, non-negativ, finite, bounded; lehnt float/NaN/Inf/str/bool ab.
    if isinstance(x, bool):
        return False
    if not isinstance(x, int):
        return False
    return 0 <= x <= MAX_MINOR_UNITS


# --- Per-Type-Handler (alle async, uniforme Signatur; stateless ignorieren conn) ---

async def _eval_max_transaction_value(c: dict, ctx: dict, conn) -> dict:
    t = "max_transaction_value"
    threshold = c.get("value")
    cur_c = c.get("currency")
    required = _is_required(c)
    value_source = ctx.get("value_source", "self_asserted")
    actual = ctx.get("value")
    cur_a = ctx.get("currency")

    # Envelope-Schwelle selbst valide?
    if not _valid_minor_units(threshold) or not isinstance(cur_c, str):
        return _verdict(t, DENY, "constraint threshold invalid (not integer-minor-units / currency)", threshold)
    # fehlender actual fuer required -> DENY
    if actual is None:
        return _verdict(t, DENY if required else ALLOW,
                        "missing action value" if required else "no value, not required", threshold)
    # numeric-hardening: negativ/NaN/Inf/float/str -> reject
    if not _valid_minor_units(actual):
        return _verdict(t, DENY, "action value not a valid non-negative integer-minor-units amount", threshold, actual)
    # currency-match
    if cur_a != cur_c:
        return _verdict(t, DENY, f"currency mismatch (action={cur_a} constraint={cur_c})", threshold, actual)
    # value-source-Gating: self_asserted kann required Betrags-Constraint NICHT hart erfuellen
    if required and value_source != "rail_verified":
        return _verdict(t, DENY, "self_asserted value cannot satisfy a required max_transaction_value", threshold, actual)
    # eigentliche Schranke
    delta = threshold - actual
    if actual <= threshold:
        return _verdict(t, ALLOW, "within limit", threshold, actual, delta)
    return _verdict(t, DENY, "exceeds max_transaction_value", threshold, actual, delta)


async def _eval_allowed_domains(c: dict, ctx: dict, conn) -> dict:
    t = "allowed_domains"
    allow = c.get("value")
    required = _is_required(c)
    domain = ctx.get("domain")
    if not isinstance(allow, list):
        return _verdict(t, DENY, "constraint allowlist invalid (not array)", allow)
    if domain is None:
        return _verdict(t, DENY if required else ALLOW,
                        "missing action domain" if required else "no domain, not required", allow)
    # exakter Match (kein substring/suffix-bypass)
    if domain in allow:
        return _verdict(t, ALLOW, "domain in allowlist", allow, domain)
    return _verdict(t, DENY, "domain not in allowlist", allow, domain)


def _parse_iso_duration(s: str) -> Optional[timedelta]:
    # Minimal ISO-8601-Duration: P[nW] | P[nD]T[nH][nM][nS] (gaengige Faelle).
    if not isinstance(s, str):
        return None
    m = re.fullmatch(
        r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", s)
    if not m or s == "P":
        return None
    w, d, h, mi, sec = (int(x) if x else 0 for x in m.groups())
    return timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=sec)


async def _eval_rate_limit(c: dict, ctx: dict, conn) -> dict:
    t = "rate_limit"
    limit = c.get("value")
    window = c.get("window")
    required = _is_required(c)
    agent_did = ctx.get("agent_did")
    td = _parse_iso_duration(window)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0 or td is None or not agent_did:
        return _verdict(t, DENY if required else ALLOW, "rate_limit constraint/context invalid", limit)
    # Count ueber aae_evaluations (NICHT IPR) — bereits akzeptierte (ALLOW) Aktionen im Fenster.
    cnt = await conn.fetchval(
        "SELECT count(*) FROM aae_evaluations "
        "WHERE agent_did = $1 AND verdict = 'ALLOW' AND created_at >= now() - $2::interval",
        agent_did, td,
    )
    delta = limit - cnt
    if cnt < limit:
        return _verdict(t, ALLOW, "within rate window", limit, cnt, delta)
    return _verdict(t, DENY, "rate_limit exceeded in window", limit, cnt, delta)


async def _eval_single_use(validity: dict, ctx: dict, conn) -> Optional[dict]:
    # single_use ist VALIDITY-Feld (bool). Nur pruefen wenn true.
    if not validity.get("single_use"):
        return None
    t = "single_use"
    aae_ref = ctx.get("aae_ref")
    cnt = await conn.fetchval(
        "SELECT count(*) FROM aae_evaluations WHERE aae_ref = $1 AND verdict = 'ALLOW'",
        aae_ref,
    )
    if cnt == 0:
        return _verdict(t, ALLOW, "first use", 1, cnt, 1 - cnt)
    return _verdict(t, DENY, "single_use already consumed", 1, cnt, 1 - cnt)


def _eval_validity_window(validity: dict, now: datetime) -> Optional[dict]:
    nb, na = validity.get("not_before"), validity.get("not_after")
    t = "validity"
    def _parse(x):
        return datetime.fromisoformat(x.replace("Z", "+00:00"))
    try:
        if nb is not None and now < _parse(nb) - CLOCK_SKEW:
            return _verdict(t, DENY, "not yet valid (not_before)", nb, now.isoformat())
        if na is not None and now > _parse(na) + CLOCK_SKEW:
            return _verdict(t, DENY, "expired (not_after)", na, now.isoformat())
    except Exception:
        return _verdict(t, DENY, "validity window unparseable", {"not_before": nb, "not_after": na})
    if nb is None and na is None:
        return None
    return _verdict(t, ALLOW, "within validity window", {"not_before": nb, "not_after": na}, now.isoformat())


def _eval_revocation_check(validity: dict) -> Optional[dict]:
    # DEFERRED: outbound revocation braucht SSRF-Egress-Proxy (eigene Sub-Komponente).
    # Praesenz = MUST-enforce -> da nicht durchfuehrbar: fail-closed DENY.
    if validity.get("revocation_check"):
        return _verdict("revocation_check", DENY,
                        "revocation_check not yet enforceable (deferred to SSRF-proxy subcomponent)",
                        validity.get("revocation_check"))
    return None


async def _dispatch_constraint(c: dict, ctx: dict, conn) -> dict:
    ctype = c.get("type")
    required = _is_required(c)
    if not isinstance(ctype, str) or not ctype:
        return _verdict(str(ctype), DENY, "constraint missing 'type'")
    if ctype not in _KNOWN_CONSTRAINT_TYPES:
        # Kritische Regel: unbekannt + required -> DENY; unbekannt + nicht required -> ignore.
        if required:
            return _verdict(ctype, DENY, "unknown constraint type (required) -> Default-DENY")
        return _verdict(ctype, ALLOW, "unknown constraint type (not required) -> ignored")
    handler = {
        "max_transaction_value": _eval_max_transaction_value,
        "allowed_domains": _eval_allowed_domains,
        "rate_limit": _eval_rate_limit,
    }[ctype]
    try:
        return await handler(c, ctx, conn)
    except Exception as e:  # fail-closed: Auswertungsfehler bei required -> DENY
        return _verdict(ctype, DENY if required else ALLOW, f"evaluation error: {e.__class__.__name__}")


def _advisory_sql_key(agent_did: str, aae_ref: str) -> str:
    return hashlib.sha256(f"{agent_did}:{aae_ref}".encode()).hexdigest()


async def evaluate_envelope(aae_ref: str, action_context: dict, conn,
                            evaluator_version: str = "1.0") -> dict:
    """Orchestrator: laedt Envelope, evaluiert alle Constraints + VALIDITY, aggregiert,
    schreibt signiertes eval-row — alles in EINER advisory-lock-Transaktion (TOCTOU-frei).

    Aggregation: EIN DENY -> Gesamt-DENY (Default-DENY-konform).
    """
    agent_did = action_context.get("agent_did")
    now = datetime.now(timezone.utc)
    sha_hex = _advisory_sql_key(agent_did or "", aae_ref)

    async with conn.transaction():
        # Advisory-Lock (single-bigint, bit(64)::bigint umgeht int4-signedness) — serialisiert
        # konkurrierende Evals pro (agent, envelope); schliesst rate_limit/single_use-TOCTOU.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(('x' || substr($1, 1, 16))::bit(64)::bigint)", sha_hex)

        env = await conn.fetchrow("SELECT * FROM aae_envelopes WHERE aae_ref = $1", aae_ref)
        evaluations: list[dict] = []
        if env is None:
            evaluations.append(_verdict("envelope", DENY, "envelope_not_found", aae_ref))
        else:
            constraints = env["constraints"]
            validity = env["validity"]
            if isinstance(constraints, str):
                constraints = json.loads(constraints)
            if isinstance(validity, str):
                validity = json.loads(validity)
            for c in constraints:
                evaluations.append(await _dispatch_constraint(c, action_context, conn))
            vw = _eval_validity_window(validity, now)
            if vw:
                evaluations.append(vw)
            rc = _eval_revocation_check(validity)
            if rc:
                evaluations.append(rc)
            su = await _eval_single_use(validity, action_context, conn)
            if su:
                evaluations.append(su)

        agg = DENY if any(e["verdict"] == DENY for e in evaluations) else ALLOW

        eval_id = "eval_" + uuid.uuid4().hex
        nonce = action_context.get("nonce") or uuid.uuid4().hex
        value_source = action_context.get("value_source", "self_asserted")
        record = {
            "eval_id": eval_id,
            "aae_ref": aae_ref,
            "agent_did": agent_did,
            "action_context": action_context,
            "evaluations": evaluations,
            "verdict": agg,
            "value_source": value_source,
            "evaluator_version": evaluator_version,
            "timestamp": now.isoformat(),
            "nonce": nonce,
        }
        sig, kid = sign_verdict(record)

        await conn.execute(
            "INSERT INTO aae_evaluations "
            "(eval_id, aae_ref, agent_did, action_context, evaluations, verdict, value_source, "
            " evaluator_version, nonce, verdict_signature, verdict_kid, created_at) "
            "VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7,$8,$9,$10,$11,$12)",
            eval_id, aae_ref, agent_did, json.dumps(action_context), json.dumps(evaluations),
            agg, value_source, evaluator_version, nonce, sig, kid, now,
        )

        return {"eval_id": eval_id, "verdict": agg, "evaluations": evaluations,
                "verdict_signature": sig, "verdict_kid": kid, "record": record}
