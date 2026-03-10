"""MolTrust Sports — Phase 1: Prediction Commitment & Verification."""

import hashlib
import json
import datetime
import re
import logging
import datetime as _dt

logger = logging.getLogger("moltrust.sports")

# --- Event ID Normalization ---
# Canonical format: {sport}:{league}:{YYYYMMDD}:{team_a}-{team_b}
# e.g. "football:epl:20260315:arsenal-chelsea"

def normalize_event_id(raw: str) -> str:
    """Normalize event ID to canonical lowercase slug form."""
    s = raw.strip().lower()
    # collapse whitespace and special chars to hyphens
    s = re.sub(r"[\s_/\\]+", "-", s)
    # remove anything that isn't alphanumeric, colon, or hyphen
    s = re.sub(r"[^a-z0-9:\-]", "", s)
    # collapse multiple hyphens
    s = re.sub(r"-{2,}", "-", s)
    # strip leading/trailing hyphens from each colon-segment
    parts = s.split(":")
    parts = [p.strip("-") for p in parts if p.strip("-")]
    return ":".join(parts)


def compute_commitment_hash(agent_did: str, event_id: str, prediction: dict, event_start: str) -> str:
    """SHA-256 commitment hash over canonical JSON payload."""
    payload = {
        "agent_did": agent_did,
        "event_id": event_id,
        "prediction": prediction,
        "event_start": event_start,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- Database Helpers ---

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sports_predictions (
    id              SERIAL PRIMARY KEY,
    agent_did       VARCHAR(40)  NOT NULL REFERENCES agents(did),
    event_id        VARCHAR(256) NOT NULL,
    prediction      JSONB        NOT NULL,
    event_start     TIMESTAMPTZ  NOT NULL,
    commitment_hash VARCHAR(64)  NOT NULL UNIQUE,
    base_tx_hash    TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    outcome         JSONB,
    correct         BOOLEAN,
    settled_at      TIMESTAMPTZ,
    UNIQUE (agent_did, event_id)
);
CREATE INDEX IF NOT EXISTS idx_sp_event ON sports_predictions(event_id);
CREATE INDEX IF NOT EXISTS idx_sp_agent ON sports_predictions(agent_did);
CREATE INDEX IF NOT EXISTS idx_sp_hash  ON sports_predictions(commitment_hash);
"""


async def ensure_table(conn):
    """Create table if it doesn't exist, and run migrations."""
    await conn.execute(CREATE_TABLE_SQL)
    # Phase 2 migration: add settlement columns if missing
    for col, typ in [("outcome", "JSONB"), ("correct", "BOOLEAN"), ("settled_at", "TIMESTAMPTZ")]:
        await conn.execute(f"""
            DO $$ BEGIN
                ALTER TABLE sports_predictions ADD COLUMN {col} {typ};
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$;
        """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sp_unsettled ON sports_predictions(event_start) WHERE settled_at IS NULL")


async def insert_prediction(conn, agent_did: str, event_id: str, prediction: dict,
                            event_start: str, commitment_hash: str, base_tx_hash: str | None) -> dict:
    """Insert a prediction commitment and return the row."""
    # Parse event_start string to datetime for asyncpg
    es_dt = _dt.datetime.fromisoformat(event_start.replace("Z", "+00:00"))
    row = await conn.fetchrow(
        """
        INSERT INTO sports_predictions (agent_did, event_id, prediction, event_start, commitment_hash, base_tx_hash)
        VALUES ($1, $2, $3::jsonb, $4, $5, $6)
        RETURNING id, agent_did, event_id, prediction, event_start, commitment_hash, base_tx_hash, created_at
        """,
        agent_did, event_id, json.dumps(prediction), es_dt, commitment_hash, base_tx_hash,
    )
    return dict(row) if row else None


async def get_prediction_by_hash(conn, commitment_hash: str) -> dict | None:
    """Look up a prediction by its commitment hash."""
    row = await conn.fetchrow(
        "SELECT id, agent_did, event_id, prediction, event_start, commitment_hash, base_tx_hash, created_at "
        "FROM sports_predictions WHERE commitment_hash = $1",
        commitment_hash,
    )
    return dict(row) if row else None


async def agent_exists(conn, did: str) -> bool:
    """Check if agent DID is registered."""
    row = await conn.fetchval("SELECT 1 FROM agents WHERE did = $1", did)
    return row is not None



# --- Phase 2: History & Stats ---

async def get_prediction_history(conn, agent_did: str, limit: int = 100, offset: int = 0) -> list[dict]:
    """Get all predictions for an agent, newest first."""
    rows = await conn.fetch(
        """
        SELECT commitment_hash, event_id, prediction, event_start,
               outcome, correct, settled_at, base_tx_hash, created_at
        FROM sports_predictions
        WHERE agent_did = $1
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
        """,
        agent_did, limit, offset,
    )
    return [dict(r) for r in rows]


async def get_prediction_stats(conn, agent_did: str) -> dict:
    """Compute betting stats for an agent."""
    row = await conn.fetchrow(
        """
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE settled_at IS NOT NULL) as settled,
            COUNT(*) FILTER (WHERE settled_at IS NULL) as pending,
            COUNT(*) FILTER (WHERE correct = true) as correct_count
        FROM sports_predictions
        WHERE agent_did = $1
        """,
        agent_did,
    )
    total = int(row["total"])
    settled = int(row["settled"])
    pending = int(row["pending"])
    correct_count = int(row["correct_count"])
    accuracy = round(correct_count / settled, 3) if settled > 0 else 0.0

    # Average confidence from prediction JSONB
    conf_row = await conn.fetchrow(
        """
        SELECT AVG((prediction->>'confidence')::float) as avg_conf
        FROM sports_predictions
        WHERE agent_did = $1 AND prediction ? 'confidence'
        """,
        agent_did,
    )
    avg_confidence = round(float(conf_row["avg_conf"]), 3) if conf_row and conf_row["avg_conf"] else None

    return {
        "total_predictions": total,
        "settled": settled,
        "pending": pending,
        "correct": correct_count,
        "accuracy": accuracy,
        "avg_confidence": avg_confidence,
    }


async def compute_calibration_score(conn, agent_did: str) -> float | None:
    """
    Compute calibration score for an agent.
    Groups settled predictions into confidence buckets, measures
    deviation between stated confidence and actual accuracy.
    Returns 1.0 = perfectly calibrated, lower = worse.
    Requires >= 10 settled predictions, else returns None.
    """
    rows = await conn.fetch(
        """
        SELECT (prediction->>'confidence')::float as confidence, correct
        FROM sports_predictions
        WHERE agent_did = $1
          AND settled_at IS NOT NULL
          AND correct IS NOT NULL
          AND prediction ? 'confidence'
        """,
        agent_did,
    )

    if len(rows) < 10:
        return None

    # Bucket boundaries: 0.5-0.6, 0.6-0.7, 0.7-0.8, 0.8-0.9, 0.9-1.0
    buckets = {i: {"conf_sum": 0.0, "correct_sum": 0, "count": 0} for i in range(5)}

    for r in rows:
        conf = r["confidence"]
        if conf is None:
            continue
        conf = max(0.5, min(1.0, conf))  # clamp
        idx = min(int((conf - 0.5) * 10), 4)  # 0-4
        buckets[idx]["conf_sum"] += conf
        buckets[idx]["correct_sum"] += 1 if r["correct"] else 0
        buckets[idx]["count"] += 1

    deviations = []
    for b in buckets.values():
        if b["count"] == 0:
            continue
        avg_conf = b["conf_sum"] / b["count"]
        actual_acc = b["correct_sum"] / b["count"]
        deviations.append(abs(avg_conf - actual_acc))

    if not deviations:
        return None

    return round(1.0 - (sum(deviations) / len(deviations)), 3)
