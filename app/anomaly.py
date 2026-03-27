"""
Behavioral Anomaly Scoring — MolTrust Swarm Intelligence

Computes anomaly flags for a given DID. Flags are informational signals
for verifiers — they do NOT affect the trust score itself.

Flags:
  - score_drop_anomaly:       Score dropped >20 points in 24h
  - young_endorser_cluster:   >5 endorsers registered within last 7 days
  - low_confidence:           Agent 30+ days old but active in ≤1 vertical
  - repetitive_endorsements:  >80% of outgoing endorsements target same DID
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import List

import asyncpg

logger = logging.getLogger(__name__)


async def compute_flags(did: str, trust_score: float, conn: asyncpg.Connection) -> List[str]:
    """
    Compute behavioral anomaly flags for a DID.
    Returns list of flag strings — empty list if clean.
    Always computed fresh (never cached).
    """
    flags: List[str] = []

    # Flag 1: Score Drop > 20 in 24h
    try:
        if await _check_score_drop(did, trust_score, conn):
            flags.append("score_drop_anomaly")
    except Exception as e:
        logger.warning("score_drop check failed for %s: %s", did, e)

    # Flag 2: Young Endorser Cluster
    try:
        if await _check_young_endorsers(did, conn):
            flags.append("young_endorser_cluster")
    except Exception as e:
        logger.warning("young_endorser check failed for %s: %s", did, e)

    # Flag 3: Low Confidence — Cross-Vertical Inactivity
    try:
        if await _check_cross_vertical_inactivity(did, conn):
            flags.append("low_confidence")
    except Exception as e:
        logger.warning("cross_vertical check failed for %s: %s", did, e)

    # Flag 4: Repetitive Endorsements
    try:
        if await _check_repetitive_endorsements(did, conn):
            flags.append("repetitive_endorsements")
    except Exception as e:
        logger.warning("repetitive_endorsement check failed for %s: %s", did, e)

    return flags


async def _check_score_drop(
    did: str, current_score: float, conn: asyncpg.Connection
) -> bool:
    """Flag if score dropped more than 20 points in last 24h."""
    if current_score is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    # Get the most recent cached score from BEFORE the cutoff
    row = await conn.fetchrow(
        """SELECT score FROM trust_score_cache
           WHERE did = $1 AND computed_at < $2
           AND score >= 0""",
        did, cutoff,
    )
    if row is None:
        return False
    previous = row["score"]
    if previous is None or previous < 0:
        return False
    return (previous - current_score) > 20


async def _check_young_endorsers(
    did: str, conn: asyncpg.Connection
) -> bool:
    """Flag if more than 5 endorsers are younger than 7 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    # agents.created_at is timestamp without tz — compare as-is
    row = await conn.fetchrow(
        """SELECT COUNT(DISTINCT e.endorser_did) AS cnt
           FROM endorsements e
           JOIN agents a ON e.endorser_did = a.did
           WHERE e.endorsed_did = $1
             AND e.expires_at > NOW()
             AND a.created_at > $2""",
        did, cutoff.replace(tzinfo=None),
    )
    return row is not None and row["cnt"] > 5


async def _check_cross_vertical_inactivity(
    did: str, conn: asyncpg.Connection
) -> bool:
    """Flag if agent is 30+ days old but active in only 1 vertical."""
    agent = await conn.fetchrow(
        "SELECT created_at FROM agents WHERE did = $1", did,
    )
    if agent is None:
        return False
    created = agent["created_at"]
    # created_at is timestamp without tz — treat as UTC
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - created).days
    if age_days < 30:
        return False

    row = await conn.fetchrow(
        """SELECT COUNT(DISTINCT vertical) AS verticals
           FROM endorsements
           WHERE (endorsed_did = $1 OR endorser_did = $1)
             AND expires_at > NOW()
             AND vertical IS NOT NULL""",
        did,
    )
    return row is not None and row["verticals"] <= 1


async def _check_repetitive_endorsements(
    did: str, conn: asyncpg.Connection
) -> bool:
    """Flag if >80% of outgoing endorsements target the same DID."""
    rows = await conn.fetch(
        """SELECT endorsed_did, COUNT(*) AS cnt
           FROM endorsements
           WHERE endorser_did = $1
             AND expires_at > NOW()
           GROUP BY endorsed_did
           ORDER BY cnt DESC""",
        did,
    )
    if not rows:
        return False
    total = sum(r["cnt"] for r in rows)
    if total < 5:
        return False  # not enough data to judge
    top_count = rows[0]["cnt"]
    return top_count / total > 0.8
