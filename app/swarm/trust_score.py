"""
Trust Score Algorithm — Phase 2: Cross-Vertical Trust Propagation

Phase 2 formula:
  score = α × direct_score
        + β × propagated_score(endorsers)
        + γ × cross_vertical_bonus
        + interaction_bonus
        - sybil_penalty × 20

Clamped to [0, 100].

Seed agents get base_score directly when no endorsements exist.
"""
import math
import asyncpg
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from app.swarm.anti_collusion import compute_sybil_penalty
from app.swarm.violation_penalty import compute_violation_penalty

DB_CONFIG = {
    "host": "localhost",
    "database": os.getenv("DB_NAME", "moltstack"),
    "user": "moltstack",
}

MAX_SCORE = 100.0
DECAY_HALF_LIFE_DAYS = 90
MIN_ENDORSERS = 3
DEFAULT_ENDORSER_WEIGHT = 0.1
MAX_RECURSION_DEPTH = 3
CACHE_TTL_HOURS = 1

# Phase 2 weights
ALPHA = 0.6   # direct score weight
BETA  = 0.3   # propagated score weight
GAMMA = 0.1   # cross-vertical bonus weight

# Agent class trust modifiers (ZeroID Feature 1)
AGENT_CLASS_MODIFIER = {
    "orchestrator": 5.0,
    "autonomous": 0.0,
    "human_initiated": 0.0,
    "copilot": -10.0,
}

VERTICAL_TYPES = {
    "VerifiedSkillCredential",
    "BuyerAgentCredential",
    "AuthorizedAgentCredential",
    "TravelAgentCredential",
    "PredictionTrackCredential",
    "ProductProvenanceCredential",
    "AuthorizedResellerCredential",
    "SkillEndorsementCredential",
}

async def compute_prediction_accuracy_bonus(conn, did: str) -> float:
    """
    Prediction accuracy bonus/malus for trust score.
    Requires >= 3 settled predictions to activate.
    Accuracy >= 60% → bonus up to +10
    Accuracy < 40% → malus down to -10
    Between 40-60% → 0 (neutral)
    """
    row = await conn.fetchrow(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN correct THEN 1 ELSE 0 END) as wins
           FROM sports_predictions
           WHERE agent_did = $1 AND settled_at IS NOT NULL""",
        did
    )
    if not row or row["total"] < 3:
        return 0.0

    total = row["total"]
    wins = row["wins"] or 0
    accuracy = wins / total

    if accuracy >= 0.6:
        # Linear scale: 60% → +2, 100% → +10
        return round(2 + (accuracy - 0.6) / 0.4 * 8, 1)
    elif accuracy < 0.4:
        # Linear scale: 40% → -2, 0% → -10
        return round(-2 - (0.4 - accuracy) / 0.4 * 8, 1)
    else:
        return 0.0



async def compute_wallet_attestation_bonus(conn, did: str) -> float:
    """
    Wallet attestation bonus for trust score.
    Reads wallet_score (0-20) from wallet_attestations table.
    Only uses attestations < 30 min old (TTL).
    """
    row = await conn.fetchrow(
        """SELECT wallet_score FROM wallet_attestations
           WHERE did = $1 AND attested_at > NOW() - INTERVAL '30 minutes'""",
        did
    )
    if not row:
        return 0.0
    return float(row["wallet_score"])

def compute_time_decay(issued_at: datetime) -> float:
    """d_i = 2^(-Δt/90), Δt in Tagen. Whitepaper Section 4.2."""
    now = datetime.now(timezone.utc)
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)
    delta_days = (now - issued_at).total_seconds() / 86400
    return math.pow(2, -delta_days / DECAY_HALF_LIFE_DAYS)


async def get_endorser_weight(
    endorser_did: str,
    conn: asyncpg.Connection,
    depth: int = 0
) -> float:
    """
    w_i rekursiv, max depth=3.
    Default 0.1 für Agents ohne Score (bootstrapping).
    """
    if depth >= MAX_RECURSION_DEPTH:
        return DEFAULT_ENDORSER_WEIGHT
    score = await compute_trust_score(
        endorser_did, conn, depth=depth + 1
    )
    if score is None:
        return DEFAULT_ENDORSER_WEIGHT
    return min(score / MAX_SCORE, 1.0)


async def compute_trust_score(
    did: str,
    conn: asyncpg.Connection,
    depth: int = 0
) -> Optional[float]:
    """
    Phase 2 Trust Score — on-demand computation.
    Returns None only if < 3 endorsers AND not a seed agent.
    Writes to trust_score_cache at depth=0.
    """
    result = await compute_phase2_score(did, conn, depth)
    return result["score"]


async def compute_phase2_score(
    did: str,
    conn: asyncpg.Connection,
    depth: int = 0
) -> dict:
    """
    Full Phase 2 computation with breakdown.

    score = α × direct_score
          + β × propagated_score
          + γ × cross_vertical_bonus
          + interaction_bonus
          - sybil_penalty × 20

    Clamped to [0, 100].
    """
    now = datetime.now(timezone.utc)

    # 1h Cache check (top-level only)
    if depth == 0:
        cached = await conn.fetchrow(
            "SELECT score, endorser_count, cache_valid_until, "
            "propagated_score, cross_vertical_bonus, computation_method, sybil_penalty "
            "FROM trust_score_cache WHERE did = $1",
            did
        )
        if cached:
            valid_until = cached["cache_valid_until"]
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=timezone.utc)
            if now < valid_until:
                score_val = cached["score"]
                if score_val is not None and score_val < 0:
                    score_val = None
                # Honest cache-hit breakdown: sybil_penalty is persisted in the cache
                # and returned as applied. violation_penalty is NOT cached, but it is
                # cheap (two light queries) and the cache is invalidated on any new
                # class=P DENY — so recompute it here for a consistent, itemized
                # breakdown on cache-hit AND cache-miss. (interaction_bonus is not
                # stored and not recomputed here -> null, not a misleading 0.)
                vp, vp_breakdown = await compute_violation_penalty(conn, did)
                return {
                    "score": score_val,
                    "direct_score": score_val or 0.0,
                    "propagated_score": float(cached["propagated_score"] or 0),
                    "cross_vertical_bonus": float(cached["cross_vertical_bonus"] or 0),
                    "interaction_bonus": None,
                    "sybil_penalty": float(cached["sybil_penalty"] or 0),
                    "violation_penalty": round(vp, 2),
                    "violation_breakdown": vp_breakdown,
                    "cached": True,
                    "endorser_count": cached["endorser_count"],
                    "computation_method": cached["computation_method"] or "phase1",
                    "withheld": score_val is None,
                }

    # Check if seed agent
    seed_row = await conn.fetchrow(
        "SELECT base_score FROM swarm_seeds WHERE did = $1", did
    )

    # Fetch valid endorsements
    endorsements = await conn.fetch(
        """
        SELECT endorser_did, evidence_hash, vertical,
               issued_at, weight, skill
        FROM endorsements
        WHERE endorsed_did = $1
          AND expires_at > $2
        ORDER BY issued_at DESC
        """,
        did, now
    )

    unique_endorsers = {e["endorser_did"] for e in endorsements}

    # Seed agent with no endorsements gets base score directly
    if seed_row and not endorsements:
        score = seed_row["base_score"]
        result = {
            "score": round(score, 1),
            "direct_score": 0.0,
            "propagated_score": 0.0,
            "cross_vertical_bonus": 0,
            "interaction_bonus": 0,
            "sybil_penalty": 0.0,
            "endorser_count": 0,
            "computation_method": "seed",
            "withheld": False,
        }
        if depth == 0:
            await _write_cache(conn, did, score, 0, 0.0, now,
                               0.0, 0, "seed")
        return result

    # Non-seed with < 3 endorsers: withheld
    if len(unique_endorsers) < MIN_ENDORSERS and not seed_row:
        result = {
            "score": None,
            "direct_score": 0.0,
            "propagated_score": 0.0,
            "cross_vertical_bonus": 0,
            "interaction_bonus": 0,
            "sybil_penalty": 0.0,
            "endorser_count": len(unique_endorsers),
            "computation_method": "phase2",
            "withheld": True,
        }
        if depth == 0:
            await _write_cache(conn, did, None, len(unique_endorsers),
                               0.0, now, 0.0, 0, "phase2")
        return result

    # --- Full Phase 2 computation ---

    # 1. Direct score (Phase 1 formula)
    sybil_penalty = await compute_sybil_penalty(did, unique_endorsers, conn)

    direct_score = 0.0
    for e in endorsements:
        w_i = await get_endorser_weight(e["endorser_did"], conn, depth)
        e_i = 1.0  # Phase 1: direct interactions only
        issued_at = e["issued_at"]
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)
        d_i = compute_time_decay(issued_at)
        direct_score += w_i * e_i * d_i

    if endorsements:
        direct_score = min(direct_score / len(endorsements) * 100, 100)

    # 2. Propagated score from endorsers
    propagated_score = 0.0
    endorser_dids = list(unique_endorsers)

    if endorser_dids:
        endorser_scores = []
        for endorser_did in endorser_dids:
            # Check cache first
            cached_e = await conn.fetchrow(
                "SELECT score FROM trust_score_cache "
                "WHERE did = $1 AND cache_valid_until > $2",
                endorser_did, now
            )
            # Check if seed
            seed_e = await conn.fetchrow(
                "SELECT base_score FROM swarm_seeds WHERE did = $1",
                endorser_did
            )

            if cached_e and cached_e["score"] is not None and cached_e["score"] >= 0:
                endorser_scores.append(cached_e["score"])
            elif seed_e:
                endorser_scores.append(seed_e["base_score"])

        if endorser_scores:
            propagated_score = sum(endorser_scores) / len(endorser_scores)

    # 3. Cross-vertical bonus
    skill_types = set()
    for e in endorsements:
        if e["vertical"]:
            skill_types.add(e["vertical"])

    # Also check credentials table for VC diversity
    try:
        cred_types = await conn.fetch(
            "SELECT DISTINCT credential_type FROM credentials "
            "WHERE subject_did = $1", did
        )
        for c in cred_types:
            if c["credential_type"] in VERTICAL_TYPES:
                skill_types.add(c["credential_type"])
    except Exception:
        pass

    cross_vertical_bonus = min(len(skill_types) * 10, 30)

    # 4. Interaction proof contribution (IPR + legacy)
    interaction_bonus = 0
    try:
        # New: Output Provenance IPR table (primary source)
        ipr_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_tables "
            "WHERE tablename='interaction_proof_records')"
        )
        if ipr_exists:
            from app.provenance.confidence import compute_ipr_bonus
            interaction_bonus = await compute_ipr_bonus(conn, did)
        else:
            # Legacy fallback: old interaction_proofs table
            legacy_exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_tables "
                "WHERE tablename='interaction_proofs')"
            )
            if legacy_exists:
                cnt = await conn.fetchval(
                    "SELECT COUNT(*) FROM interaction_proofs "
                    "WHERE prover_did = $1 OR verifier_did = $1", did
                )
                interaction_bonus = min(cnt * 2, 10)
    except Exception:
        pass

    # 5. Inactivity penalty (RSAC Gap 3)
    inactivity_penalty = 0.0
    try:
        from app.anomaly import get_inactivity_penalty
        inactivity_penalty = await get_inactivity_penalty(did, conn)
    except Exception:
        pass

    # 6. Prediction accuracy bonus/malus
    prediction_bonus = 0.0
    try:
        prediction_bonus = await compute_prediction_accuracy_bonus(conn, did)
    except Exception:
        pass

    # 7. Wallet attestation bonus (skin-in-the-game)
    wallet_bonus = 0.0
    try:
        wallet_bonus = await compute_wallet_attestation_bonus(conn, did)
    except Exception:
        pass

    # 8. Agent class modifier (ZeroID Feature 1)
    agent_class_modifier = 0.0
    try:
        ac_row = await conn.fetchrow(
            "SELECT agent_class FROM agents WHERE did = $1", did
        )
        if ac_row and ac_row["agent_class"]:
            agent_class_modifier = AGENT_CLASS_MODIFIER.get(
                ac_row["agent_class"], 0.0
            )
    except Exception:
        pass

    # 9. Final score
    raw = (ALPHA * direct_score
           + BETA * propagated_score
           + GAMMA * cross_vertical_bonus
           + interaction_bonus
           + prediction_bonus
           + wallet_bonus
           + agent_class_modifier)
    # sybil_penalty is now bounded [0,6] (v0.10, VD1) — no legacy `* 20` here.
    comp = min(100, max(0, raw - sybil_penalty + inactivity_penalty))

    # Violation penalty (v0.10): adjudicated graded + auto constraint-breach.
    violation_penalty, vp_breakdown = await compute_violation_penalty(conn, did)

    # CRITICAL: Seed floor guard — DO NOT REMOVE
    # Seed agents must never fall below their base_score.
    # This prevents the 2-agent mutual endorsement echo chamber problem.
    # See: git log --oneline | grep "seed floor"
    # Deployed: 2026-03-22
    floored = comp
    if seed_row:
        floored = max(seed_row["base_score"], floored)

    # G2: violation_penalty applies AFTER the floor — the floor protects against
    # endorsement-thinness, NOT against confirmed violations. (bootstrap_weight
    # per §4.4 is a separate PR; the hard floor above is unchanged.)
    final_score = round(min(100, max(0, floored - violation_penalty)), 1)

    result = {
        "score": final_score,
        "direct_score": round(direct_score, 1),
        "propagated_score": round(propagated_score, 1),
        "cross_vertical_bonus": cross_vertical_bonus,
        "interaction_bonus": interaction_bonus,
        "sybil_penalty": round(sybil_penalty, 2),
        "violation_penalty": round(violation_penalty, 2),
        "violation_breakdown": vp_breakdown,
        "prediction_bonus": prediction_bonus,
        "wallet_bonus": wallet_bonus,
        "inactivity_penalty": inactivity_penalty,
        "agent_class_modifier": agent_class_modifier,
        "endorser_count": len(unique_endorsers),
        "computation_method": "phase2",
        "withheld": False,
    }

    if depth == 0:
        await _write_cache(conn, did, final_score, len(unique_endorsers),
                           sybil_penalty, now, propagated_score,
                           cross_vertical_bonus, "phase2")

    return result


def score_to_grade(score: Optional[float]) -> str:
    """Convert numeric score to letter grade."""
    if score is None:
        return "N/A"
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    if score >= 20:
        return "D"
    return "F"


async def _write_cache(
    conn: asyncpg.Connection,
    did: str,
    score: Optional[float],
    endorser_count: int,
    sybil_penalty: float,
    now: datetime,
    propagated_score: float = 0.0,
    cross_vertical_bonus: float = 0,
    computation_method: str = "phase2"
) -> None:
    """Cache-Eintrag schreiben oder aktualisieren. Emits CAEP trust_score_change on >=10pt delta."""
    valid_until = now + timedelta(hours=CACHE_TTL_HOURS)

    # Read old score for CAEP delta detection (raw value: -1.0 means previously withheld)
    old_row = await conn.fetchrow(
        "SELECT score FROM trust_score_cache WHERE did = $1", did
    )
    old_score = old_row["score"] if old_row else None
    if old_score is not None and old_score < 0:
        old_score = None  # withheld sentinel

    await conn.execute(
        """
        INSERT INTO trust_score_cache
          (did, score, endorser_count, sybil_penalty,
           computed_at, cache_valid_until,
           propagated_score, cross_vertical_bonus, computation_method)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (did) DO UPDATE SET
          score               = EXCLUDED.score,
          endorser_count      = EXCLUDED.endorser_count,
          sybil_penalty       = EXCLUDED.sybil_penalty,
          computed_at         = EXCLUDED.computed_at,
          cache_valid_until   = EXCLUDED.cache_valid_until,
          propagated_score    = EXCLUDED.propagated_score,
          cross_vertical_bonus = EXCLUDED.cross_vertical_bonus,
          computation_method  = EXCLUDED.computation_method
        """,
        did,
        score if score is not None else -1.0,
        endorser_count,
        sybil_penalty,
        now,
        valid_until,
        propagated_score,
        cross_vertical_bonus,
        computation_method
    )

    # CAEP: emit trust_score_change on >=10pt delta (skip None transitions)
    if old_score is not None and score is not None:
        delta = abs(score - old_score)
        if delta >= 10.0:
            try:
                from app.caep import emit_caep_event
                await emit_caep_event(
                    conn, did, "trust_score_change",
                    {
                        "old_score": round(old_score, 2),
                        "new_score": round(score, 2),
                        "delta": round(score - old_score, 2),
                        "reason": "recompute",
                        "computed_at": now.isoformat(),
                    },
                )
            except Exception:
                # Never let CAEP emission break score recompute
                import logging
                logging.getLogger("trust_score").exception("CAEP emit failed for %s", did)
