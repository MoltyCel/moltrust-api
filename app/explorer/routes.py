"""Explorer routes: public agent discovery with trust math shown.

All endpoints read-only. No authentication required (public explorer).
Data source: v_explorer_agents view (joined erc8004_outreach + agents + trust_score_cache).
Uses asyncpg pool (same as main app).
"""

from fastapi import APIRouter, Query, HTTPException, Request
from typing import Optional, Literal
import asyncpg
import os

router = APIRouter(prefix="/explorer", tags=["explorer"])

DB_CONFIG = {
    "host": "localhost",
    "database": os.getenv("DB_NAME", "moltstack"),
    "user": "moltstack",
}

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(**DB_CONFIG, min_size=1, max_size=5)
    return _pool


@router.get("/stats")
async def get_stats():
    """Aggregate stats across all indexed sources."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM v_explorer_stats")

        total = sum(r["total_indexed"] for r in rows)
        verified = sum(r["moltrust_verified"] for r in rows)
        contacted = sum(r["contacted"] for r in rows)
        indexed = sum(r["indexed_only"] for r in rows)

        by_source = {}
        by_chain = {}
        for r in rows:
            by_source[r["source"]] = by_source.get(r["source"], 0) + r["total_indexed"]
            by_chain[r["chain"]] = by_chain.get(r["chain"], 0) + r["total_indexed"]

        return {
            "total_indexed": total,
            "moltrust_verified": verified,
            "contacted": contacted,
            "indexed_only": indexed,
            "by_source": by_source,
            "by_chain": by_chain,
        }


@router.get("/agents")
async def list_agents(
    source: Optional[str] = Query(None, description="Filter by source (erc8004, virtuals, farcaster)"),
    chain: Optional[str] = Query(None, description="Filter by chain (base, ethereum, solana)"),
    verification: Optional[Literal["moltrust_verified", "contacted_not_verified", "indexed_only"]] = None,
    min_score: Optional[float] = Query(None, ge=0, le=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Paginated agent list with filters."""
    conditions = []
    params = []
    idx = 1

    if source:
        conditions.append(f"source = ${idx}")
        params.append(source)
        idx += 1
    if chain:
        conditions.append(f"chain = ${idx}")
        params.append(chain)
        idx += 1
    if verification:
        conditions.append(f"verification_status = ${idx}")
        params.append(verification)
        idx += 1
    if min_score is not None:
        conditions.append(f"moltrust_trust_score >= ${idx}")
        params.append(min_score)
        idx += 1

    where = " AND ".join(conditions) if conditions else "TRUE"
    query = f"""
        SELECT * FROM v_explorer_agents
        WHERE {where}
        ORDER BY COALESCE(moltrust_trust_score, 0) DESC,
                 external_registered_at DESC NULLS LAST
        LIMIT ${idx} OFFSET ${idx + 1}
    """  # nosec B608 - interpolated fragments are code literals carrying $N placeholders; every value is bound as a parameter
    params.extend([limit, offset])

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        return [
            {
                "external_agent_id": r["external_agent_id"],
                "wallet_address": r["wallet_address"],
                "chain": r["chain"],
                "source": r["source"],
                "verification_status": r["verification_status"],
                "moltrust_did": r["moltrust_did"],
                "moltrust_trust_score": float(r["moltrust_trust_score"]) if r["moltrust_trust_score"] is not None else None,
                "external_registered_at": r["external_registered_at"].isoformat() if r["external_registered_at"] else None,
                "moltrust_registered_at": r["moltrust_registered_at"].isoformat() if r["moltrust_registered_at"] else None,
            }
            for r in rows
        ]


@router.get("/agent/{identifier}")
async def get_agent(identifier: str):
    """
    Single agent detail with full trust math.
    Identifier can be: did:moltrust:..., erc8004:<id>, or 0x wallet address.
    """
    if identifier.startswith("did:moltrust:"):
        where = "moltrust_did = $1"
    elif identifier.startswith("erc8004:"):
        where = "external_agent_id = $1::int AND source = 'erc8004'"
        identifier = identifier.replace("erc8004:", "")
        identifier = int(identifier)
    elif identifier.startswith("0x"):
        where = "wallet_address = $1"
    else:
        try:
            identifier = int(identifier)
            where = "external_agent_id = $1"
        except ValueError:
            raise HTTPException(400, "Invalid identifier format. Use did:moltrust:..., erc8004:<id>, or 0x<wallet>")

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT * FROM v_explorer_agents WHERE {where} LIMIT 1",  # nosec B608 - interpolated fragments are code literals carrying $N placeholders; every value is bound as a parameter
            identifier,
        )

        if not row:
            raise HTTPException(404, f"Agent not found: {identifier}")

        agent = {
            "external_agent_id": row["external_agent_id"],
            "wallet_address": row["wallet_address"],
            "chain": row["chain"],
            "source": row["source"],
            "verification_status": row["verification_status"],
            "moltrust_did": row["moltrust_did"],
            "moltrust_trust_score": float(row["moltrust_trust_score"]) if row["moltrust_trust_score"] is not None else None,
            "external_registered_at": row["external_registered_at"].isoformat() if row["external_registered_at"] else None,
            "moltrust_registered_at": row["moltrust_registered_at"].isoformat() if row["moltrust_registered_at"] else None,
            "metadata_uri": row["metadata_uri"],
        }

        # Trust breakdown — try cache first, fall back to live computation
        trust_breakdown = None
        if row["moltrust_did"]:
            cached = await conn.fetchrow(
                """SELECT score, endorser_count, propagated_score, cross_vertical_bonus,
                          computation_method, cache_valid_until
                   FROM trust_score_cache WHERE did = $1""",
                row["moltrust_did"],
            )

            if cached and cached["score"] is not None and cached["score"] >= 0:
                trust_breakdown = _build_breakdown_from_cache(cached)
                trust_breakdown["status"] = "scored"
            elif cached and (cached["score"] is None or cached["score"] < 0):
                trust_breakdown = {
                    "final_score": None,
                    "status": "withheld",
                    "withheld": True,
                    "reason": f"Fewer than 3 unique endorsers ({cached['endorser_count']} found)",
                    "endorser_count": cached["endorser_count"],
                    "methodology_url": "/explorer/methodology",
                }
            else:
                # No cache — try live computation via swarm module
                try:
                    from app.swarm.trust_score import compute_phase2_score
                    result = await compute_phase2_score(row["moltrust_did"], conn)
                    if result and result.get("score") is not None:
                        agent["moltrust_trust_score"] = result["score"]
                        trust_breakdown = {
                            "final_score": result["score"],
                            "components": {
                                "direct_endorsements": {
                                    "value": result.get("direct_score", 0),
                                    "weight": 0.6,
                                    "contribution": round(0.6 * result.get("direct_score", 0), 1),
                                },
                                "propagated_trust": {
                                    "value": result.get("propagated_score", 0),
                                    "weight": 0.3,
                                    "contribution": round(0.3 * result.get("propagated_score", 0), 1),
                                },
                                "cross_vertical_bonus": {
                                    "value": result.get("cross_vertical_bonus", 0),
                                    "weight": 0.1,
                                    "contribution": round(0.1 * result.get("cross_vertical_bonus", 0), 1),
                                },
                                "interaction_bonus": {"value": result.get("interaction_bonus", 0)},
                                "prediction_bonus": {"value": result.get("prediction_bonus", 0)},
                                "wallet_bonus": {"value": result.get("wallet_bonus", 0)},
                                "agent_class_modifier": {"value": result.get("agent_class_modifier", 0)},
                                "sybil_penalty": {
                                    "value": result.get("sybil_penalty", 0),
                                    "multiplier": 20,
                                    "contribution": round(-result.get("sybil_penalty", 0) * 20, 1),
                                },
                                "inactivity_penalty": {"value": result.get("inactivity_penalty", 0)},
                            },
                            "computation_method": result.get("computation_method", "phase2"),
                            "endorser_count": result.get("endorser_count", 0),
                            "withheld": result.get("withheld", False),
                            "formula": "score = 0.6*direct + 0.3*propagated + 0.1*cross_vertical + interaction + prediction + wallet + class_modifier - sybil*20 + inactivity",
                            "methodology_url": "/explorer/methodology",
                            "status": "scored",
                        }
                    elif result and result.get("withheld"):
                        trust_breakdown = {
                            "final_score": None,
                            "status": "withheld",
                            "withheld": True,
                            "reason": f"Fewer than 3 unique endorsers ({result.get('endorser_count', 0)} found)",
                            "methodology_url": "/explorer/methodology",
                        }
                except Exception:
                    # Swarm module not available in dev — graceful degradation
                    if row["moltrust_did"]:
                        trust_breakdown = {
                            "final_score": None,
                            "status": "pending",
                            "note": "Score not yet computed. Agent is MolTrust-verified but has no cached trust score.",
                            "methodology_url": "/explorer/methodology",
                        }

        # Flags stub (Phase B: real sybil/anomaly data)
        flags = []

        return {
            "agent": agent,
            "trust_breakdown": trust_breakdown,
            "flags": flags,
            "metadata_uri": row["metadata_uri"],
        }


def _build_breakdown_from_cache(cached) -> dict:
    """Build trust breakdown from cached score data."""
    return {
        "final_score": float(cached["score"]) if cached["score"] is not None else None,
        "components": {
            "direct_endorsements": {
                "weight": 0.6,
                "note": f"{cached['endorser_count']} endorsers",
            },
            "propagated_trust": {
                "value": float(cached["propagated_score"] or 0),
                "weight": 0.3,
            },
            "cross_vertical_bonus": {
                "value": float(cached["cross_vertical_bonus"] or 0),
                "weight": 0.1,
            },
        },
        "computation_method": cached["computation_method"],
        "endorser_count": cached["endorser_count"],
        "formula": "score = 0.6*direct + 0.3*propagated + 0.1*cross_vertical + interaction + prediction + wallet + class_modifier - sybil*20 + inactivity",
        "methodology_url": "/explorer/methodology",
    }


@router.get("/methodology")
async def get_methodology():
    """
    Full methodology document — honest representation of trust_score.py
    and anti_collusion.py. No made-up weights.
    """
    return {
        "version": "1.1",
        "trust_score_formula": {
            "formula": "score = alpha*direct + beta*propagated + gamma*cross_vertical + interaction_bonus + prediction_bonus + wallet_bonus + agent_class_modifier - sybil_penalty*20 + inactivity_penalty",
            "clamped": "[0, 100]",
            "parameters": {
                "alpha": {"value": 0.6, "meaning": "Weight on direct endorsements from verified agents"},
                "beta": {"value": 0.3, "meaning": "Weight on propagated trust through endorsement graph (endorser scores)"},
                "gamma": {"value": 0.1, "meaning": "Bonus for endorsements spanning multiple verticals (max 30 points from min(unique_verticals * 10, 30))"},
            },
            "bonus_components": {
                "interaction_bonus": "Points from verified interaction proofs (IPR records, capped at 10)",
                "prediction_bonus": "Accuracy bonus/malus from prediction market track record",
                "wallet_bonus": "Skin-in-the-game bonus from wallet attestation",
                "agent_class_modifier": "Per MoltID classification: orchestrator +5, autonomous 0, human_initiated 0, copilot -10",
                "inactivity_penalty": "Penalty for agents inactive >30 days (RSAC Gap 3)",
            },
            "time_decay": {
                "half_life_days": 90,
                "description": "Endorsement evidence decays with 90-day half-life",
            },
            "seed_agents": {
                "description": "Seed agents (bootstrap network) receive a base_score directly. Seed floor guard ensures they never drop below their registered base_score.",
            },
            "min_endorsers": 3,
            "min_endorsers_note": "Agents with fewer than 3 unique endorsers have their score withheld (shown as null)",
            "score_range": "0 to 100, advisory not enforcement",
            "source": "app/swarm/trust_score.py — compute_phase2_score()",
        },
        "sybil_detection": {
            "source": "app/swarm/anti_collusion.py — compute_sybil_penalty()",
            "description": "Graph-based sybil detection per Whitepaper Section 4.3. Returns a penalty value [0, inf) that is multiplied by 20 and subtracted from the raw score.",
            "signals": [
                {
                    "name": "jaccard_clustering",
                    "description": "Pairwise Jaccard similarity of endorser sets. When two agents share >80% of their endorsers (Jaccard > 0.8 threshold), penalty = jaccard * endorser_count * 0.5 per pair.",
                    "threshold": 0.8,
                },
                {
                    "name": "vertical_diversity",
                    "description": "If fewer than 3 unique verticals are represented in the endorsement set, flat penalty of 10.0 applied.",
                    "min_verticals": 3,
                    "flat_penalty": 10.0,
                },
            ],
            "note": "Additional signals (common_funder, inhuman_velocity, sweep_pattern) are described in Whitepaper Section 4.3 but not yet implemented in the scoring pipeline. They will be added as the network grows.",
        },
        "flag_philosophy": {
            "framing": "Patterns detected. You decide.",
            "disclaimer": "A flag describes what happened, not why. The agent may be the beneficiary, the victim, or uninvolved.",
        },
        "enforcement_class": "advisory",
        "refusal_authority": "consumer_policy — MolTrust scores are inputs to policy decisions, not the decisions themselves",
        "standards_alignment": ["W3C DID 1.0", "W3C VC 2.0", "DIF Universal Resolver", "A2A v0.3", "ERC-8004"],
        "sources_indexed": {
            "erc8004": {"status": "live", "chain": "base", "scanner_cron": "daily 06:30 UTC", "agents_indexed": "44,000+"},
            "virtuals": {"status": "planned", "chain": "base"},
            "farcaster": {"status": "planned", "chain": "farcaster"},
        },
    }
