"""
Output Provenance — Confidence Anti-Manipulation (Spec v0.4)

Three layers:
1. Statistical calibration (MAE after >= 10 outcomes)
2. Confidence distribution check (inflation flag)
3. confidence_basis weighting
"""

# --- Layer 3: Basis Weighting ---

BASIS_WEIGHT = {
    "human_reviewed": 1.0,
    "ensemble":       0.9,
    "model_logprob":  0.8,
    "rule_based":     0.7,
    "declared":       0.5,
}


def effective_confidence(declared: float, basis: str) -> float:
    """Compute weighted confidence for trust score integration."""
    weight = BASIS_WEIGHT.get(basis, 0.5)
    return round(declared * weight, 4)


# --- Layer 1: Calibration Score ---

async def compute_calibration_score(conn, agent_did: str) -> dict:
    """
    Compute calibration score via Mean Absolute Error.
    Requires >= 10 IPRs with outcomes.
    """
    rows = await conn.fetch(
        """SELECT confidence, confidence_basis, outcome_correct
           FROM interaction_proof_records
           WHERE agent_did = $1
             AND outcome_correct IS NOT NULL
             AND anchor_status = 'anchored'""",
        agent_did
    )

    if len(rows) < 10:
        return {
            "calibration_score": None,
            "sample_size": len(rows),
            "sufficient": False,
        }

    total_error = 0.0
    for r in rows:
        eff = effective_confidence(r["confidence"], r["confidence_basis"])
        actual = 1.0 if r["outcome_correct"] else 0.0
        total_error += abs(eff - actual)

    mae = total_error / len(rows)
    calibration_score = round(max(0.0, 1.0 - mae), 4)

    return {
        "calibration_score": calibration_score,
        "sample_size": len(rows),
        "mae": round(mae, 4),
        "sufficient": True,
    }


# --- Layer 2: Inflation Detection ---

async def check_confidence_inflation(conn, agent_did: str) -> dict:
    """
    Flag agents with suspiciously uniform or inflated confidence.
    Flags: avg > 0.95 (inflation) or stddev < 0.02 (too uniform).
    """
    row = await conn.fetchrow(
        """SELECT AVG(confidence) as avg_conf,
                  STDDEV(confidence) as std_conf,
                  COUNT(*) as total
           FROM interaction_proof_records
           WHERE agent_did = $1 AND anchor_status = 'anchored'""",
        agent_did
    )

    total = row["total"] or 0
    if total < 10:
        return {"flagged": False, "reason": "insufficient_data", "total": total}

    avg = float(row["avg_conf"] or 0)
    std = float(row["std_conf"] or 0)

    flagged = avg > 0.95 or std < 0.02
    reasons = []
    if avg > 0.95:
        reasons.append(f"avg_confidence={avg:.3f} > 0.95")
    if std < 0.02:
        reasons.append(f"stddev={std:.4f} < 0.02")

    return {
        "flagged": flagged,
        "reason": "; ".join(reasons) if reasons else "ok",
        "avg_confidence": round(avg, 3),
        "stddev": round(std, 4),
        "total": total,
    }


# --- Trust Score Integration ---

async def compute_ipr_bonus(conn, agent_did: str) -> int:
    """
    Compute interaction_bonus from IPR records for trust score.
    Replaces the old interaction_proofs table lookup.
    Base: min(anchored_count * 2, 10)
    Bonus: +2 if calibration_score > 0.7
    Penalty: -3 if confidence_inflation flagged
    """
    cnt = await conn.fetchval(
        "SELECT COUNT(*) FROM interaction_proof_records "
        "WHERE agent_did = $1 AND anchor_status = 'anchored'",
        agent_did
    )
    bonus = min(cnt * 2, 10)

    # Calibration bonus
    cal = await compute_calibration_score(conn, agent_did)
    if cal["sufficient"] and cal["calibration_score"] and cal["calibration_score"] > 0.7:
        bonus += 2

    # Inflation penalty
    inflation = await check_confidence_inflation(conn, agent_did)
    if inflation["flagged"]:
        bonus = max(0, bonus - 3)

    return bonus
