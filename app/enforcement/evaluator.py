"""AAE Evaluator — D3 MANDATE-Enforcement, Komponente 2 (Schritt 3).

Per-Type-Constraint-Handler + Orchestrator. Wertet eine geplante Aktion
(action_context) gegen die CONSTRAINTS + VALIDITY eines gespeicherten Envelopes
aus und schreibt ein SIGNIERTES Eval-Row in aae_evaluations — alles in EINER
advisory-lock-Transaktion (TOCTOU-frei fuer rate_limit/single_use).

Verdict-Form (live ConstraintEvaluation-Mapping):
    {type, threshold, current_value, delta, verdict: ALLOW|DENY, reason}

Kritische Regel (Taxonomie / draft-kroehl-agentic-trust-aae-02 §2.3):
    required:true unauswertbar/unbekannt -> Default-DENY; required:false unbekannt -> ignore.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid

import asyncpg
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
             threshold: Any = None, current: Any = None, delta: Any = None,
             code: str | None = None) -> dict:
    # v0.10 (VD2/VD3): tag the penalty class at the source (P/O/A), so downstream
    # scoring never does fragile reason-string matching. Persisted in
    # aae_evaluations.evaluations[] (jsonb, no schema change).
    #
    # `code` is the machine-readable counterpart of `reason`. The prose reason
    # stays as it is, because classify_constraint matches substrings in it and
    # the penalty class depends on that; the code is what an operator and a
    # dashboard should read instead. Additive: consumers use .get.
    from app.swarm.violation_penalty import classify_constraint
    return {"type": type_, "threshold": threshold, "current_value": current,
            "delta": delta, "verdict": verdict, "reason": reason,
            "code": code, "class": classify_constraint(type_, reason)}


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

async def _eval_max_transaction_value(c: dict, ctx: dict, conn, mandate_ref: str | None = None) -> dict:
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


async def _eval_allowed_domains(c: dict, ctx: dict, conn, mandate_ref: str | None = None) -> dict:
    t = "allowed_domains"
    allow = c.get("value")
    required = _is_required(c)
    domain = ctx.get("domain")
    if not isinstance(allow, list):
        return _verdict(t, DENY, "constraint allowlist invalid (not array)", allow)
    if domain is None:
        return _verdict(t, DENY if required else ALLOW,
                        "missing action domain" if required else "no domain, not required", allow)
    if not isinstance(domain, str):
        return _verdict(t, DENY, "action domain not a string", allow, domain)
    # exakter, case-insensitiver Match (kein substring/suffix-bypass)
    allow_norm = {d.lower() for d in allow if isinstance(d, str)}
    if domain.lower() in allow_norm:
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
    td = timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=sec)
    # Upper-Bound-Cap gegen DoS (z.B. P9999999D) — Fenster > 366 Tage abweisen.
    if td > timedelta(days=366):
        return None
    return td


async def _eval_rate_limit(c: dict, ctx: dict, conn, mandate_ref: str | None = None) -> dict:
    t = "rate_limit"
    limit = c.get("value")
    window = c.get("window")
    required = _is_required(c)
    agent_did = ctx.get("agent_did")
    td = _parse_iso_duration(window)
    if not mandate_ref:
        # The window is counted per envelope, and only the server knows which
        # envelope this is. Without that reference there is nothing to count
        # against, whatever `required` says (ops V5).
        return _verdict(t, DENY, "mandate reference not established server-side -> fail-closed DENY",
                        limit, code="mandate_ref_missing")
    if (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0
            or td is None or not agent_did):
        return _verdict(t, DENY if required else ALLOW, "rate_limit constraint/context invalid", limit)
    # Count ueber aae_evaluations (NICHT IPR), PER (agent, envelope) — akzeptierte (ALLOW) Aktionen im Fenster.
    # The envelope is mandate_ref, the aae_ref of the stored row. action_context.aae_ref
    # is the caller's and was counted here until ops V5: a missing value made the
    # constraint fail, a changing one opened a fresh window on every call.
    cnt = await conn.fetchval(
        "SELECT count(*) FROM aae_evaluations "
        "WHERE agent_did = $1 AND aae_ref = $3 AND verdict = 'ALLOW' AND created_at >= now() - $2::interval",
        agent_did, td, mandate_ref,
    )
    delta = limit - cnt
    if cnt < limit:
        return _verdict(t, ALLOW, "within rate window", limit, cnt, delta)
    return _verdict(t, DENY, "rate_limit exceeded in window", limit, cnt, delta)


async def _eval_single_use(validity: dict, mandate_ref: str | None, conn) -> Optional[dict]:
    # single_use ist VALIDITY-Feld (bool). Nur pruefen wenn true.
    if not validity.get("single_use"):
        return None
    t = "single_use"
    # Counted over mandate_ref, the aae_ref of the stored envelope. Until ops V5 this
    # read action_context.aae_ref: a caller who left it out was counted against NULL
    # and got "first use" on every call.
    if not mandate_ref:
        return _verdict(t, DENY, "mandate reference not established server-side -> fail-closed DENY",
                        1, code="mandate_ref_missing")
    cnt = await conn.fetchval(
        "SELECT count(*) FROM aae_evaluations WHERE aae_ref = $1 AND verdict = 'ALLOW'",
        mandate_ref,
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


async def _dispatch_constraint(c: dict, ctx: dict, conn, mandate_ref: str | None = None) -> dict:
    ctype = c.get("type")
    required = _is_required(c)
    if not isinstance(ctype, str) or not ctype:
        return _verdict(str(ctype), DENY, "constraint missing 'type'",
                        code="constraint_type_missing")
    if ctype not in _KNOWN_CONSTRAINT_TYPES:
        # Kritische Regel: unbekannt + required -> DENY; unbekannt + nicht required -> ignore.
        if required:
            return _verdict(ctype, DENY, "unknown constraint type (required) -> Default-DENY",
                            code="constraint_type_unknown_required")
        return _verdict(ctype, ALLOW, "unknown constraint type (not required) -> ignored",
                        code="constraint_type_unknown_optional")
    handler = {
        "max_transaction_value": _eval_max_transaction_value,
        "allowed_domains": _eval_allowed_domains,
        "rate_limit": _eval_rate_limit,
    }[ctype]
    try:
        return await handler(c, ctx, conn, mandate_ref)
    except Exception as exc:  # fail-closed: JEDER Auswertungsfehler -> DENY (auch bei required=False)
        # Fail-closed is right, but a bare "evaluation error" told an operator
        # nothing about whether the envelope was malformed or this service broke.
        # The exception class is named in the reason and the code is stable, so
        # the two cases can be told apart after the fact. The substring
        # "evaluation error" stays in the reason because classify_constraint
        # reads it to force class O, which is what keeps an agent from being
        # penalized for our own failure.
        return _verdict(ctype, DENY,
                        f"evaluation error ({type(exc).__name__}) -> fail-closed DENY",
                        code="constraint_evaluation_error")


# Where each input of a verdict comes from (ops V5). "established" means this
# service determined or checked the value itself before a verdict rests on it;
# "asserted" means it is the caller's statement and is used as given. "aae_ref"
# is the request field the envelope is looked up by, action_context.* are the
# caller's fields. Every evaluation names the inputs it read, so a reader of a
# stored verdict sees which part of it rests on the caller's word.
ESTABLISHED = "established"
ASSERTED = "asserted"

_INPUTS_READ = {
    "max_transaction_value": ("action_context.value", "action_context.currency",
                              "action_context.value_source"),
    "allowed_domains": ("action_context.domain",),
    "rate_limit": ("aae_ref", "action_context.agent_did"),
    "single_use": ("aae_ref",),
    "validity": ("action_context.timestamp",),
    "agent_did": ("action_context.agent_did",),
    "nonce": ("action_context.nonce",),
    "envelope": ("aae_ref",),
}


def input_sources(action_context: dict, established: frozenset[str] | set[str]) -> dict:
    """{input name: ESTABLISHED|ASSERTED} for aae_ref and every action_context field.

    Anything not named in `established` is ASSERTED: a field nobody checked is the
    caller's statement, including one this module has never heard of.
    """
    names = ["aae_ref"] + [f"action_context.{k}" for k in sorted(action_context)]
    return {n: ESTABLISHED if n in established else ASSERTED for n in names}


def _advisory_sql_key(aae_ref: str) -> str:
    # Lock NUR auf aae_ref: serialisiert ALLE Evals eines Envelopes (auch cross-agent) ->
    # schliesst single_use cross-agent-TOCTOU (zwei agent_dids, gleicher Envelope, parallel).
    # rate_limit bleibt per-agent korrekt, da die count-query weiterhin nach agent_did filtert.
    return hashlib.sha256(aae_ref.encode()).hexdigest()


async def _insert_violation(conn, agent_did: str, evaluations: list, now: datetime) -> str:
    """Schreibt EINEN violation_records-Eintrag pro DENY-Eval (eine Entscheidung = ein Verstoss-Log).

    Write-Contract (v4-Brief): id app-generiert; adjudicator_type='evaluator'; violation_type =
    komma-getrennte Liste der verletzten Constraint-Typen; interaction_proof_id=NULL (Evaluator
    gated PRE-action, IPR evtl. noch nicht da); principal_did=agent_did; reversed=false; alle TEXT.
    """
    violated = sorted({e["type"] for e in evaluations if e["verdict"] == DENY})
    violation_type = ",".join(violated) or "unspecified"
    description = "; ".join(
        r for r in (e.get("reason") for e in evaluations if e["verdict"] == DENY) if r)[:1000]
    viol_id = "viol_" + uuid.uuid4().hex
    now_text = now.isoformat()
    await conn.execute(
        "INSERT INTO violation_records "
        "(id, agent_did, principal_did, violation_type, interaction_proof_id, description, "
        " adjudicator_type, confirmed_at, reversed, created_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, 'evaluator', $7, false, $8)",
        viol_id, agent_did, agent_did, violation_type, None, description, now_text, now_text,
    )
    return viol_id


async def evaluate_envelope(aae_ref: str, action_context: dict, conn,
                            evaluator_version: str = "1.0",
                            request_proof: dict | None = None,
                            established_by_caller: frozenset[str] = frozenset()) -> dict:
    """Orchestrator: laedt Envelope, evaluiert alle Constraints + VALIDITY, aggregiert,
    schreibt signiertes eval-row — alles in EINER advisory-lock-Transaktion (TOCTOU-frei).

    Aggregation: EIN DENY -> Gesamt-DENY (Default-DENY-konform).

    `request_proof` is the RFC 9421 proof the caller authenticated with (ops V6,
    app/enforcement/request_signature.py); it is stored with the row, so the
    record shows who proved possession of agent_did. None for an API key.

    `established_by_caller` names the inputs the caller has checked before this
    call, in the form of `input_sources` ("action_context.agent_did", ...). This
    module adds what it establishes itself: the server time it writes into
    action_context.timestamp, and aae_ref once the envelope row is found. Every
    other input is reported as asserted (ops V5).
    """
    now = datetime.now(timezone.utc)
    # Server-Zeit erzwingen: ueberschreibt client action_context.timestamp (kein Backdating);
    # robust re-verifizierbar, da timestamp verbatim als jsonb gespeichert + mitsigniert wird.
    action_context = {**action_context, "timestamp": now.isoformat()}
    agent_did_raw = action_context.get("agent_did")
    agent_did = agent_did_raw if isinstance(agent_did_raw, str) else ""   # NOT NULL-safe + lock-safe
    client_nonce = action_context.get("nonce")
    sha_hex = _advisory_sql_key(aae_ref)  # Lock per-Envelope (serialisiert auch cross-agent)
    established = set(established_by_caller) | {"action_context.timestamp"}

    try:
        async with conn.transaction():
            # Advisory-Lock (single-bigint, bit(64)::bigint umgeht int4-signedness) — serialisiert
            # konkurrierende Evals pro (agent, envelope); schliesst rate_limit/single_use-TOCTOU.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(('x' || substr($1, 1, 16))::bit(64)::bigint)", sha_hex)

            evaluations: list[dict] = []
            # Preconditions (fail-closed). agent_did + client-nonce sind Pflicht; fehlen -> DENY.
            agent_ok = agent_did.strip() != ""
            nonce_ok = isinstance(client_nonce, str) and client_nonce.strip() != ""
            if not agent_ok:
                evaluations.append(_verdict("agent_did", DENY, "missing/empty agent_did"))
            if not nonce_ok:
                # nonce ist der Client-Replay-Token — der Server mintet ihn NIE.
                evaluations.append(_verdict("nonce", DENY, "missing/empty nonce (client replay-token required)"))

            if agent_ok and nonce_ok:
                try:
                    env = await conn.fetchrow("SELECT * FROM aae_envelopes WHERE aae_ref = $1", aae_ref)
                    if env is None:
                        evaluations.append(_verdict("envelope", DENY, "envelope_not_found", aae_ref))
                    else:
                        # The mandate reference single_use and rate_limit count over:
                        # the key of the stored row, never a value from action_context.
                        mandate_ref = env["aae_ref"]
                        established.add("aae_ref")
                        constraints = env["constraints"]
                        validity = env["validity"]
                        if isinstance(constraints, str):
                            constraints = json.loads(constraints)
                        if isinstance(validity, str):
                            validity = json.loads(validity)
                        if not isinstance(constraints, list):  # Defense (DB-CHECK erzwingt array)
                            evaluations.append(_verdict("constraints", DENY, "constraints not a list"))
                            constraints = []
                        for c in constraints:
                            evaluations.append(await _dispatch_constraint(c, action_context, conn, mandate_ref))
                        vw = _eval_validity_window(validity, now)
                        if vw:
                            evaluations.append(vw)
                        rc = _eval_revocation_check(validity)
                        if rc:
                            evaluations.append(rc)
                        su = await _eval_single_use(validity, mandate_ref, conn)
                        if su:
                            evaluations.append(su)
                except Exception:
                    # never-crash-without-audit: JEDER Auswertungsfehler -> DENY + Audit-Row (unten).
                    evaluations.append(_verdict("evaluator", DENY, "evaluation error -> fail-closed DENY"))

            agg = DENY if any(e["verdict"] == DENY for e in evaluations) else ALLOW
            # Signed with the evaluations, so a stored verdict keeps saying which of
            # its inputs were the caller's word.
            sources = input_sources(action_context, established)
            for e in evaluations:
                e["inputs"] = {n: sources[n] for n in _INPUTS_READ.get(e["type"], ()) if n in sources}

            eval_id = "eval_" + uuid.uuid4().hex
            # Audit-Nonce: client-nonce wenn vorhanden (Replay-Token); sonst server-uuid NUR fuer das
            # precond-DENY-Audit-Row (ein ALLOW gibt es ohne client-nonce nie -> kein Replay-Bypass).
            nonce = client_nonce if nonce_ok else ("srv_" + uuid.uuid4().hex)
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
                "timestamp": action_context["timestamp"],  # Server-Zeit, verbatim in action_context
                "nonce": nonce,
            }
            sig, kid = sign_verdict(record)

            await conn.execute(
                "INSERT INTO aae_evaluations "
                "(eval_id, aae_ref, agent_did, action_context, evaluations, verdict, value_source, "
                " evaluator_version, nonce, verdict_signature, verdict_kid, created_at, request_proof) "
                "VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)",
                eval_id, aae_ref, agent_did, json.dumps(action_context), json.dumps(evaluations),
                agg, value_source, evaluator_version, nonce, sig, kid, now,
                json.dumps(request_proof) if request_proof is not None else None,
            )
            # Verstoss-Log NUR bei DENY, in DERSELBEN Transaktion (atomar mit eval-row;
            # schlaegt der violation-write fehl, rollt auch das eval-row zurueck -> kein Split-State).
            violation_id = await _insert_violation(conn, agent_did, evaluations, now) if agg == DENY else None
            # v0.10 (step 2): a DENY with >=1 penalized (class=P) constraint moves the
            # score -> invalidate the trust-score cache so it propagates before the 1h
            # TTL. class=O (server fail-closed) and class=A (nonce) never trigger this.
            if agg == DENY and agent_did and any(e.get("class") == "P" for e in evaluations):
                await conn.execute("DELETE FROM trust_score_cache WHERE did = $1", agent_did)
            return {"eval_id": eval_id, "verdict": agg, "evaluations": evaluations,
                    "verdict_signature": sig, "verdict_kid": kid, "record": record,
                    "violation_id": violation_id, "inputs": sources}
    except asyncpg.UniqueViolationError:
        # (agent_did, nonce)-Replay: das ERSTE eval-row ist der Audit; der Replay wird abgewiesen.
        # Tx wurde zurueckgerollt (kein doppeltes Row). Controlled DENY, kein Crash.
        return {"eval_id": None, "verdict": DENY,
                "evaluations": [_verdict("nonce", DENY, "replay: (agent_did, nonce) already used")],
                "verdict_signature": None, "verdict_kid": None, "record": None, "violation_id": None,
                "inputs": None}
