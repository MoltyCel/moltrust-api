"""MolTrust Sports — Signal Provider Registration & Verification."""

import hashlib
import json
import datetime as _dt
import logging

logger = logging.getLogger("moltrust.signals")


# --- DB Schema ---

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS signal_providers (
    id                SERIAL PRIMARY KEY,
    provider_id       VARCHAR(11)  NOT NULL UNIQUE,
    agent_did         VARCHAR(40)  NOT NULL UNIQUE REFERENCES agents(did),
    provider_name     VARCHAR(128) NOT NULL,
    provider_url      VARCHAR(512),
    sport_focus       JSONB        NOT NULL DEFAULT '[]',
    description       TEXT,
    credential_hash   VARCHAR(64),
    credential_tx_hash TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sigprov_did ON signal_providers(agent_did);
"""


async def ensure_signal_table(conn):
    """Create signal_providers table if it doesn't exist."""
    await conn.execute(CREATE_TABLE_SQL)


# --- Helpers ---

def generate_provider_id(agent_did: str, ts: str) -> str:
    """sp_ + first 8 chars of SHA256(agent_did + timestamp)."""
    raw = hashlib.sha256(f"{agent_did}{ts}".encode()).hexdigest()
    return f"sp_{raw[:8]}"


def compute_credential_hash(provider_id: str, agent_did: str, provider_name: str, ts: str) -> str:
    """SHA-256 hash of credential payload for on-chain anchoring."""
    payload = {
        "type": "MolTrustVerifiedSignalProvider",
        "provider_id": provider_id,
        "agent_did": agent_did,
        "provider_name": provider_name,
        "issued_at": ts,
        "issuer": "did:web:moltrust.ch",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- DB Operations ---

async def insert_provider(conn, provider_id: str, agent_did: str, provider_name: str,
                          provider_url: str | None, sport_focus: list, description: str | None,
                          credential_hash: str, credential_tx_hash: str | None) -> dict:
    """Insert a new signal provider."""
    row = await conn.fetchrow(
        """
        INSERT INTO signal_providers
            (provider_id, agent_did, provider_name, provider_url, sport_focus, description,
             credential_hash, credential_tx_hash)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)
        RETURNING id, provider_id, agent_did, provider_name, provider_url,
                  sport_focus, description, credential_hash, credential_tx_hash, created_at
        """,
        provider_id, agent_did, provider_name, provider_url,
        json.dumps(sport_focus), description, credential_hash, credential_tx_hash,
    )
    return dict(row) if row else None


async def get_provider_by_id(conn, provider_id: str) -> dict | None:
    """Look up a signal provider by provider_id."""
    row = await conn.fetchrow(
        "SELECT * FROM signal_providers WHERE provider_id = $1",
        provider_id,
    )
    return dict(row) if row else None


async def get_provider_by_did(conn, agent_did: str) -> dict | None:
    """Look up a signal provider by agent DID."""
    row = await conn.fetchrow(
        "SELECT * FROM signal_providers WHERE agent_did = $1",
        agent_did,
    )
    return dict(row) if row else None


async def get_track_record(conn, agent_did: str) -> dict:
    """Aggregate prediction stats for a signal provider from sports_predictions."""
    row = await conn.fetchrow(
        """
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE settled_at IS NOT NULL) as settled,
            COUNT(*) FILTER (WHERE correct = true) as correct_count,
            AVG((prediction->>'confidence')::float)
                FILTER (WHERE prediction ? 'confidence') as avg_confidence
        FROM sports_predictions
        WHERE agent_did = $1
        """,
        agent_did,
    )
    total = int(row["total"])
    settled = int(row["settled"])
    correct = int(row["correct_count"])
    accuracy = round(correct / settled, 3) if settled > 0 else 0.0
    avg_conf = round(float(row["avg_confidence"]), 3) if row["avg_confidence"] else None

    # ROI estimate: (correct * avg_odds - total_settled) / total_settled
    # Using 1.9 as default odds assumption
    avg_odds = 1.9
    roi = round((correct * avg_odds - settled) / settled, 3) if settled > 0 else 0.0

    return {
        "total_signals": total,
        "settled": settled,
        "correct": correct,
        "accuracy": accuracy,
        "avg_confidence": avg_conf,
        "roi_estimate": roi,
    }


async def get_recent_signals(conn, agent_did: str, limit: int = 10) -> list[dict]:
    """Get most recent settled predictions for a provider."""
    rows = await conn.fetch(
        """
        SELECT commitment_hash, event_id, prediction, correct, created_at
        FROM sports_predictions
        WHERE agent_did = $1 AND settled_at IS NOT NULL
        ORDER BY settled_at DESC
        LIMIT $2
        """,
        agent_did, limit,
    )
    result = []
    for r in rows:
        pred = r["prediction"]
        if isinstance(pred, str):
            pred = json.loads(pred)
        result.append({
            "commitment_hash": r["commitment_hash"],
            "event_id": r["event_id"],
            "prediction": pred.get("outcome", pred.get("result", str(pred))),
            "correct": r["correct"],
            "committed_at": r["created_at"].isoformat(),
        })
    return result


async def get_leaderboard(conn, min_settled: int = 20, limit: int = 20) -> list[dict]:
    """Top signal providers ranked by accuracy (min_settled threshold)."""
    rows = await conn.fetch(
        """
        SELECT
            sp.provider_id,
            sp.provider_name,
            COUNT(*) FILTER (WHERE p.settled_at IS NOT NULL) as settled,
            COUNT(*) FILTER (WHERE p.correct = true) as correct_count,
            COUNT(*) as total
        FROM signal_providers sp
        JOIN sports_predictions p ON p.agent_did = sp.agent_did
        GROUP BY sp.provider_id, sp.provider_name
        HAVING COUNT(*) FILTER (WHERE p.settled_at IS NOT NULL) >= $1
        ORDER BY (COUNT(*) FILTER (WHERE p.correct = true))::float /
                 NULLIF(COUNT(*) FILTER (WHERE p.settled_at IS NOT NULL), 0) DESC
        LIMIT $2
        """,
        min_settled, limit,
    )
    result = []
    for i, r in enumerate(rows, 1):
        settled = int(r["settled"])
        correct = int(r["correct_count"])
        accuracy = round(correct / settled, 3) if settled > 0 else 0.0
        result.append({
            "rank": i,
            "provider_id": r["provider_id"],
            "provider_name": r["provider_name"],
            "accuracy": accuracy,
            "total_signals": int(r["total"]),
            "badge_url": f"https://moltrust.ch/badges/signals/{r['provider_id']}",
        })
    return result


# --- Badge SVG ---

def generate_badge_svg(provider_name: str, accuracy: float | None, verified: bool = True) -> str:
    """Generate an SVG badge for a verified signal provider."""
    acc_text = f"{accuracy * 100:.1f}% accuracy" if accuracy is not None else "New Provider"
    check = (
        '<circle cx="16" cy="20" r="10" fill="#E85D26"/>'
        '<path d="M12 20l3 3 5-6" stroke="white" stroke-width="2" '
        'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    )
    # Truncate provider_name to fit
    name = provider_name[:20] + "..." if len(provider_name) > 20 else provider_name

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60" viewBox="0 0 200 60">
  <rect width="200" height="60" rx="6" fill="#0F172A"/>
  {check}
  <text x="32" y="17" font-family="sans-serif" font-size="9" font-weight="700" fill="#E85D26">
    Verified Signal Provider
  </text>
  <text x="32" y="30" font-family="sans-serif" font-size="10" font-weight="600" fill="white">
    {name}
  </text>
  <text x="32" y="44" font-family="monospace" font-size="11" font-weight="700" fill="#E85D26">
    {acc_text}
  </text>
  <text x="32" y="55" font-family="sans-serif" font-size="7" fill="#94A3B8">
    moltrust.ch
  </text>
</svg>'''
