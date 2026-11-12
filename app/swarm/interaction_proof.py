"""
Interaction Proof — Whitepaper v4, Section 4.1

Vor jedem Endorsement muss ein Interaction Proof erstellt werden:
1. SHA-256 Hash des Interaction Payloads
2. Ankern auf Base L2 (via bestehenden on-chain Anchor)
3. Rückgabe von evidence_hash + base_tx_hash
4. Gültig für Endorsement innerhalb von 72h

Endorsements OHNE gültigen Interaction Proof sind ungültig.
"""
import hashlib
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
import asyncpg

logger = logging.getLogger(__name__)

PROOF_VALIDITY_HOURS = 72


def compute_evidence_hash(interaction_payload: dict) -> str:
    """
    SHA-256 Hash des kanonischen JSON des Interaction Payloads.
    Kanonisch = keys alphabetisch sortiert, keine Whitespace-Variation.
    """
    canonical = json.dumps(interaction_payload, sort_keys=True,
                           separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def anchor_on_chain(evidence_hash: str) -> Optional[str]:
    """Anchor via anchor_to_base(), or None.

    There was a fallback here that returned
    `"0x" + sha256("mock_base_" + evidence_hash)[:40]` on any exception, with
    the comment "Sandbox-Fallback". It was not a rare path, it was the only
    path: the call below passes one argument to a function that takes two
    (`anchor_to_base(agent_did, timestamp)` in app/main.py), so it raises
    TypeError every time, the except caught it, and a 42-character string went
    out as `base_tx_hash` on POST /skill/interaction-proof. A Base transaction
    hash is 66 characters, which is the second tell.

    The mock is gone. None is what every other anchor path in this codebase
    returns when it cannot anchor, and a caller that reads `base_tx_hash: null`
    learns something true.

    The arity bug is deliberately NOT fixed here: repairing it would start
    sending real transactions from a path whose proof is never persisted, so
    /skill/endorse still could not check the result. The two belong in one
    change, and this is not it. Until then this function returns None, and the
    defect is visible instead of masked.
    """
    try:
        import sys
        sys.path.insert(0, '/home/moltstack/moltstack')
        from app.main import anchor_to_base
        return await anchor_to_base(evidence_hash)
    except Exception as e:
        logger.warning("interaction_proof: no anchor (%s: %s)", type(e).__name__, e)
        return None


async def create_interaction_proof(
    api_key: str,
    interaction_payload: dict,
    conn: asyncpg.Connection
) -> dict:
    """
    Erstellt Interaction Proof:
    1. Agent via API Key auflösen
    2. Payload validieren
    3. SHA-256 hashen
    4. Base L2 ankern
    5. Ergebnis zurückgeben
    """
    # Agent DID aus API Key auflösen (api_keys.key -> api_keys.owner_did)
    agent = await conn.fetchrow(
        "SELECT owner_did AS did FROM api_keys WHERE key = $1 AND active = true",
        api_key
    )
    if not agent or not agent["did"]:
        raise ValueError("Invalid API key")

    # Payload muss required fields enthalten
    required_fields = ["type", "agent_a", "agent_b",
                       "timestamp", "outcome"]
    for field in required_fields:
        if field not in interaction_payload:
            raise ValueError(f"Missing required field: {field}")

    # Self-interaction nicht erlaubt
    if interaction_payload["agent_a"] == interaction_payload["agent_b"]:
        raise ValueError("agent_a and agent_b must be different")

    # Hash berechnen
    evidence_hash = compute_evidence_hash(interaction_payload)

    # Auf Base L2 ankern
    base_tx_hash = await anchor_on_chain(evidence_hash)

    now = datetime.now(timezone.utc)
    valid_until = now + timedelta(hours=PROOF_VALIDITY_HOURS)

    return {
        "evidence_hash": f"sha256:{evidence_hash}",
        "base_tx_hash": base_tx_hash,
        "anchored_at": now.isoformat(),
        "valid_for_endorsement_until": valid_until.isoformat(),
        "agent_did": agent["did"],
    }
