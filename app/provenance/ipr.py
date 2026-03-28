"""
Output Provenance — IPR Schema, Validation, JCS Signature (Spec v0.4)

Interaction Proof Record: cryptographically signed, on-chain anchored proof
that a specific agent produced a specific output.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import jcs

# --- Constants ---

SCHEMA_VERSION = "1.0"
MAX_SOURCE_HASHES = 20
HASH_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
AAE_REF_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
VALID_CONFIDENCE_BASES = ("declared", "rule_based", "model_logprob", "ensemble", "human_reviewed")
VALID_OUTPUT_TYPES = ("generic", "text", "code", "prediction", "analysis", "recommendation", "credential", "report")


# --- Table Creation ---

ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS interaction_proof_records (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  schema_version   VARCHAR(10)  NOT NULL DEFAULT '1.0',
  agent_did        VARCHAR(255) NOT NULL,
  output_hash      VARCHAR(100) NOT NULL
                   CHECK (output_hash ~ '^sha256:[a-f0-9]{64}$'),
  output_type      VARCHAR(50)  NOT NULL DEFAULT 'generic',
  source_hashes    JSONB        NOT NULL DEFAULT '[]',
  source_refs      JSONB        NOT NULL DEFAULT '[]',
  confidence       FLOAT        NOT NULL
                   CHECK (confidence >= 0.0 AND confidence <= 1.0),
  confidence_basis VARCHAR(50)  NOT NULL DEFAULT 'declared',
  aae_ref          VARCHAR(100)
                   CHECK (aae_ref IS NULL OR aae_ref ~ '^sha256:[a-f0-9]{64}$'),
  agent_signature  VARCHAR(200) NOT NULL,
  produced_at      TIMESTAMPTZ  NOT NULL,
  created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  anchor_tx        VARCHAR(100),
  anchor_block     BIGINT,
  merkle_proof     JSONB,
  anchor_status    VARCHAR(20)  NOT NULL DEFAULT 'pending'
                   CHECK (anchor_status IN ('pending', 'anchored', 'failed')),
  anchor_retries   INTEGER      NOT NULL DEFAULT 0
                   CHECK (anchor_retries <= 3),
  outcome_hash     VARCHAR(100),
  outcome_correct  BOOLEAN,
  outcome_at       TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ipr_agent_output
  ON interaction_proof_records(agent_did, output_hash);
CREATE INDEX IF NOT EXISTS idx_ipr_agent_did
  ON interaction_proof_records(agent_did);
CREATE INDEX IF NOT EXISTS idx_ipr_pending
  ON interaction_proof_records(anchor_status, created_at)
  WHERE anchor_status = 'pending';
CREATE INDEX IF NOT EXISTS idx_ipr_failed
  ON interaction_proof_records(anchor_status)
  WHERE anchor_status = 'failed';
"""


async def ensure_table(conn):
    """Create the IPR table if it doesn't exist."""
    await conn.execute(ENSURE_TABLE_SQL)


# --- JCS Canonical Payload ---

def build_canonical_payload(data: dict) -> bytes:
    """
    Build the canonical payload for Ed25519 signing per RFC 8785 (JCS).
    Fields: aae_ref, agent_did, confidence, confidence_basis,
            output_hash, output_type, produced_at, schema_version, source_hashes (sorted).
    """
    payload = {
        "aae_ref": data.get("aae_ref"),  # null is valid
        "agent_did": data["agent_did"],
        "confidence": data["confidence"],
        "confidence_basis": data["confidence_basis"],
        "output_hash": data["output_hash"],
        "output_type": data["output_type"],
        "produced_at": data["produced_at"],
        "schema_version": SCHEMA_VERSION,
        "source_hashes": sorted(data.get("source_hashes", [])),
    }
    return jcs.canonicalize(payload)


def compute_payload_hash(data: dict) -> str:
    """SHA-256 hash of JCS canonical payload."""
    canonical = build_canonical_payload(data)
    return hashlib.sha256(canonical).hexdigest()


# --- Validation ---

def validate_ipr_input(data: dict) -> dict:
    """
    Validate IPR submit input. Returns cleaned data or raises ValueError.
    """
    errors = []

    # output_hash
    oh = data.get("output_hash", "")
    if not HASH_PATTERN.match(oh):
        errors.append("output_hash must be 'sha256:<64 hex chars>'")

    # agent_did
    agent_did = data.get("agent_did", "")
    if not agent_did.startswith("did:moltrust:"):
        errors.append("agent_did must start with 'did:moltrust:'")

    # output_type
    output_type = data.get("output_type", "generic")
    if output_type not in VALID_OUTPUT_TYPES:
        errors.append(f"output_type must be one of {VALID_OUTPUT_TYPES}")

    # source_hashes
    source_hashes = data.get("source_hashes", [])
    if not isinstance(source_hashes, list):
        errors.append("source_hashes must be a list")
    elif len(source_hashes) > MAX_SOURCE_HASHES:
        errors.append(f"source_hashes max {MAX_SOURCE_HASHES} entries")
    else:
        for i, sh in enumerate(source_hashes):
            if not HASH_PATTERN.match(sh):
                errors.append(f"source_hashes[{i}] must be 'sha256:<64 hex chars>'")

    # source_refs
    source_refs = data.get("source_refs", [])
    if not isinstance(source_refs, list):
        errors.append("source_refs must be a list")
    elif len(source_refs) > MAX_SOURCE_HASHES:
        errors.append(f"source_refs max {MAX_SOURCE_HASHES} entries")

    # confidence
    confidence = data.get("confidence")
    if confidence is None or not isinstance(confidence, (int, float)):
        errors.append("confidence must be a float 0.0-1.0")
    elif not (0.0 <= confidence <= 1.0):
        errors.append("confidence must be between 0.0 and 1.0")

    # confidence_basis
    cb = data.get("confidence_basis", "declared")
    if cb not in VALID_CONFIDENCE_BASES:
        errors.append(f"confidence_basis must be one of {VALID_CONFIDENCE_BASES}")

    # aae_ref
    aae_ref = data.get("aae_ref")
    if aae_ref is not None and not AAE_REF_PATTERN.match(aae_ref):
        errors.append("aae_ref must be 'sha256:<64 hex chars>' or null")

    # agent_signature
    sig = data.get("agent_signature", "")
    if not sig or len(sig) < 10:
        errors.append("agent_signature is required")

    # produced_at
    produced_at = data.get("produced_at", "")
    try:
        if isinstance(produced_at, str):
            datetime.fromisoformat(produced_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        errors.append("produced_at must be a valid ISO 8601 timestamp")

    if errors:
        raise ValueError("; ".join(errors))

    return {
        "output_hash": oh,
        "agent_did": agent_did,
        "output_type": output_type,
        "source_hashes": source_hashes,
        "source_refs": source_refs,
        "confidence": float(confidence),
        "confidence_basis": cb,
        "aae_ref": aae_ref,
        "agent_signature": sig,
        "produced_at": produced_at,
    }


# --- DB Operations ---

async def insert_ipr(conn, data: dict) -> dict:
    """Insert a new IPR record. Returns the created record or existing if duplicate."""
    # Check for duplicate (idempotent)
    existing = await conn.fetchrow(
        "SELECT id, anchor_status FROM interaction_proof_records "
        "WHERE agent_did = $1 AND output_hash = $2",
        data["agent_did"], data["output_hash"]
    )
    if existing:
        return {
            "ipr_id": str(existing["id"]),
            "accepted": False,
            "reason": "duplicate",
            "anchor_status": existing["anchor_status"],
        }

    ipr_id = uuid.uuid4()
    await conn.execute(
        """INSERT INTO interaction_proof_records
           (id, schema_version, agent_did, output_hash, output_type,
            source_hashes, source_refs, confidence, confidence_basis,
            aae_ref, agent_signature, produced_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
        ipr_id, SCHEMA_VERSION, data["agent_did"], data["output_hash"],
        data["output_type"], json.dumps(data["source_hashes"]),
        json.dumps(data["source_refs"]), data["confidence"],
        data["confidence_basis"], data["aae_ref"], data["agent_signature"],
        data["produced_at"],
    )
    return {
        "ipr_id": str(ipr_id),
        "accepted": True,
        "anchor_status": "pending",
    }


async def get_ipr(conn, ipr_id: str) -> Optional[dict]:
    """Get a single IPR by ID."""
    row = await conn.fetchrow(
        "SELECT * FROM interaction_proof_records WHERE id = $1",
        uuid.UUID(ipr_id)
    )
    if not row:
        return None
    return _row_to_dict(row)


async def get_iprs_by_agent(conn, agent_did: str, limit: int = 20, offset: int = 0) -> list:
    """Get IPRs for an agent, newest first."""
    rows = await conn.fetch(
        "SELECT * FROM interaction_proof_records "
        "WHERE agent_did = $1 ORDER BY produced_at DESC LIMIT $2 OFFSET $3",
        agent_did, limit, offset
    )
    return [_row_to_dict(r) for r in rows]


async def get_ipr_stats(conn) -> dict:
    """Get aggregate IPR stats."""
    row = await conn.fetchrow("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE anchor_status = 'anchored') as anchored,
            COUNT(*) FILTER (WHERE anchor_status = 'pending') as pending,
            COUNT(*) FILTER (WHERE anchor_status = 'failed') as failed,
            COUNT(DISTINCT agent_did) as unique_agents,
            AVG(confidence) as avg_confidence
        FROM interaction_proof_records
    """)
    return {
        "total_iprs": row["total"],
        "anchored": row["anchored"],
        "pending": row["pending"],
        "failed": row["failed"],
        "unique_agents": row["unique_agents"],
        "avg_confidence": round(float(row["avg_confidence"] or 0), 3),
    }


async def submit_outcome(conn, ipr_id: str, outcome_hash: str, outcome_correct: bool) -> bool:
    """Record an outcome for an IPR (for confidence calibration)."""
    result = await conn.execute(
        """UPDATE interaction_proof_records
           SET outcome_hash = $1, outcome_correct = $2, outcome_at = NOW()
           WHERE id = $3 AND outcome_hash IS NULL""",
        outcome_hash, outcome_correct, uuid.UUID(ipr_id)
    )
    return "UPDATE 1" in result


def _row_to_dict(row) -> dict:
    """Convert asyncpg Record to dict."""
    d = dict(row)
    d["id"] = str(d["id"])
    d["source_hashes"] = json.loads(d["source_hashes"]) if isinstance(d["source_hashes"], str) else d["source_hashes"]
    d["source_refs"] = json.loads(d["source_refs"]) if isinstance(d["source_refs"], str) else d["source_refs"]
    d["merkle_proof"] = json.loads(d["merkle_proof"]) if isinstance(d["merkle_proof"], str) else d["merkle_proof"]
    for k in ("produced_at", "created_at", "outcome_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    return d
