"""MolTrust Sports — Fantasy Lineup Commitment & Verification."""

import hashlib
import json
import datetime as _dt
import logging

from app.credentials import issue_credential

logger = logging.getLogger("moltrust.fantasy")


# --- DB Schema ---

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fantasy_lineups (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_did         TEXT         NOT NULL,
    contest_id        TEXT         NOT NULL,
    platform          TEXT         NOT NULL,
    sport             TEXT         NOT NULL,
    contest_type      TEXT,
    contest_start     TIMESTAMPTZ  NOT NULL,
    entry_fee_usd     FLOAT,
    lineup            JSONB        NOT NULL,
    lineup_hash       TEXT         NOT NULL,
    projected_score   FLOAT,
    confidence        FLOAT,
    commitment_hash   TEXT         UNIQUE NOT NULL,
    tx_hash           TEXT,
    committed_at      TIMESTAMPTZ  DEFAULT now(),
    actual_score      FLOAT,
    rank              INTEGER,
    total_entries     INTEGER,
    prize_usd         FLOAT,
    percentile        FLOAT,
    settled_at        TIMESTAMPTZ,
    UNIQUE(agent_did, contest_id)
);
CREATE INDEX IF NOT EXISTS idx_fl_agent ON fantasy_lineups(agent_did);
CREATE INDEX IF NOT EXISTS idx_fl_hash  ON fantasy_lineups(commitment_hash);
CREATE INDEX IF NOT EXISTS idx_fl_contest ON fantasy_lineups(contest_id);
"""

VALID_PLATFORMS = {"draftkings", "fanduel", "yahoo", "sleeper", "custom"}
VALID_SPORTS = {"nfl", "nba", "mlb", "nhl", "pga", "nascar", "soccer", "custom"}


async def ensure_fantasy_table(conn):
    """Create fantasy_lineups table if it doesn't exist."""
    await conn.execute(CREATE_TABLE_SQL)


# --- Hash Helpers ---

def compute_lineup_hash(lineup: dict) -> str:
    """SHA-256 of sorted lineup JSON."""
    canonical = json.dumps(lineup, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def compute_fantasy_commitment_hash(agent_did: str, contest_id: str,
                                     lineup_hash: str, ts: str) -> str:
    """SHA-256 commitment hash for a fantasy lineup."""
    raw = f"{agent_did}:{contest_id}:{lineup_hash}:{ts}"
    return hashlib.sha256(raw.encode()).hexdigest()



def issue_fantasy_lineup_credential(agent_did: str, commit_data: dict) -> dict:
    """Issue a FantasyLineupCredential W3C VC for a committed lineup."""
    claims = {
        "contestId": commit_data["contest_id"],
        "platform": commit_data["platform"],
        "sport": commit_data["sport"],
        "lineupHash": commit_data["lineup_hash"],
        "commitmentHash": commit_data["commitment_hash"],
        "contestStartIso": commit_data["contest_start_iso"],
        "projectedScore": commit_data.get("projected_score"),
        "confidence": commit_data.get("confidence"),
        "baseAnchor": commit_data.get("tx_hash"),
    }
    return issue_credential(agent_did, "FantasyLineupCredential", claims)


# --- DB Operations ---

async def insert_lineup(conn, agent_did: str, contest_id: str, platform: str,
                        sport: str, contest_type: str | None, contest_start: str,
                        entry_fee_usd: float | None, lineup: dict, lineup_hash: str,
                        projected_score: float | None, confidence: float | None,
                        commitment_hash: str, tx_hash: str | None,
                        credential: dict | None = None) -> dict:
    """Insert a fantasy lineup commitment."""
    cs_dt = _dt.datetime.fromisoformat(contest_start.replace("Z", "+00:00"))
    cred_json = json.dumps(credential) if credential else None
    row = await conn.fetchrow(
        """
        INSERT INTO fantasy_lineups
            (agent_did, contest_id, platform, sport, contest_type, contest_start,
             entry_fee_usd, lineup, lineup_hash, projected_score, confidence,
             commitment_hash, tx_hash, credential)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12, $13, $14::jsonb)
        RETURNING id, agent_did, contest_id, platform, sport, commitment_hash,
                  tx_hash, committed_at, lineup_hash, credential
        """,
        agent_did, contest_id, platform, sport, contest_type, cs_dt,
        entry_fee_usd, json.dumps(lineup), lineup_hash, projected_score,
        confidence, commitment_hash, tx_hash, cred_json,
    )
    return dict(row) if row else None


async def get_lineup_by_hash(conn, commitment_hash: str) -> dict | None:
    """Look up a fantasy lineup by commitment hash."""
    row = await conn.fetchrow(
        "SELECT * FROM fantasy_lineups WHERE commitment_hash = $1",
        commitment_hash,
    )
    return dict(row) if row else None


async def settle_lineup(conn, commitment_hash: str, actual_score: float,
                        rank: int | None, total_entries: int | None,
                        prize_usd: float | None, percentile: float | None) -> bool:
    """Settle a fantasy lineup with results."""
    result = await conn.execute(
        """
        UPDATE fantasy_lineups
        SET actual_score = $2, rank = $3, total_entries = $4,
            prize_usd = $5, percentile = $6, settled_at = now()
        WHERE commitment_hash = $1 AND settled_at IS NULL
        """,
        commitment_hash, actual_score, rank, total_entries, prize_usd, percentile,
    )
    return result.endswith("1")


async def get_fantasy_history(conn, agent_did: str, limit: int = 10) -> list[dict]:
    """Get recent fantasy lineups for an agent."""
    rows = await conn.fetch(
        """
        SELECT commitment_hash, contest_id, platform, sport, contest_type,
               contest_start, entry_fee_usd, lineup, lineup_hash,
               projected_score, confidence, tx_hash, committed_at,
               actual_score, rank, total_entries, prize_usd, percentile, settled_at
        FROM fantasy_lineups
        WHERE agent_did = $1
        ORDER BY committed_at DESC
        LIMIT $2
        """,
        agent_did, limit,
    )
    return [dict(r) for r in rows]


async def get_fantasy_stats(conn, agent_did: str) -> dict:
    """Compute fantasy stats for an agent."""
    row = await conn.fetchrow(
        """
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE settled_at IS NOT NULL) as settled,
            COUNT(*) FILTER (WHERE prize_usd > 0) as itm,
            COALESCE(SUM(entry_fee_usd) FILTER (WHERE settled_at IS NOT NULL), 0) as total_fees,
            COALESCE(SUM(prize_usd) FILTER (WHERE settled_at IS NOT NULL), 0) as total_prizes,
            AVG(projected_score) FILTER (WHERE projected_score IS NOT NULL) as avg_proj,
            AVG(actual_score) FILTER (WHERE actual_score IS NOT NULL) as avg_actual,
            array_agg(DISTINCT platform) as platforms,
            array_agg(DISTINCT sport) as sports
        FROM fantasy_lineups
        WHERE agent_did = $1
        """,
        agent_did,
    )
    total = int(row["total"])
    settled = int(row["settled"])
    itm = int(row["itm"])
    total_fees = float(row["total_fees"])
    total_prizes = float(row["total_prizes"])
    avg_proj = round(float(row["avg_proj"]), 1) if row["avg_proj"] else None
    avg_actual = round(float(row["avg_actual"]), 1) if row["avg_actual"] else None

    itm_rate = round(itm / settled, 3) if settled > 0 else 0.0
    roi = round((total_prizes - total_fees) / total_fees, 3) if total_fees > 0 else 0.0

    proj_acc = None
    if avg_proj and avg_actual and avg_proj > 0:
        proj_acc = round(1.0 - abs(avg_proj - avg_actual) / avg_proj, 3)

    platforms = [p for p in (row["platforms"] or []) if p]
    sports = [s for s in (row["sports"] or []) if s]

    return {
        "total_lineups": total,
        "settled": settled,
        "in_the_money": itm,
        "itm_rate": itm_rate,
        "total_entry_fees_usd": round(total_fees, 2),
        "total_prizes_usd": round(total_prizes, 2),
        "roi": roi,
        "avg_projected_score": avg_proj,
        "avg_actual_score": avg_actual,
        "projection_accuracy": proj_acc,
        "platforms": platforms,
        "sports": sports,
    }
