"""AAE Envelope Store — D3 MANDATE-Enforcement, Komponente 1 (App-Layer).

Persistiert AAE-Envelopes in der `aae_envelopes`-Tabelle (Migration 010).

Designprinzipien (ADR-D3-v3 ACCEPTED + Brief #108):
- aae_ref wird NICHT app-seitig gesetzt — der DB-Trigger `trg_aae_bind_ref`
  berechnet ihn server-seitig aus raw_canonical (Hash-Bindung = DB-Invariante);
  die App liefert nur raw_canonical.
- scope_canonical / raw_canonical sind JCS (RFC 8785) bytes, app-seitig VOR
  INSERT berechnet (Postgres kann kein JCS).
- single_use-Replay-Schutz ist DB-Invariante (Unique-Index auf
  (aae_id, digest(scope_canonical))).
- Tabelle ist append-only/immutable (UPDATE/DELETE per Trigger + REVOKE geblockt).

Konvention wie übrige app/*-Module: Funktionen nehmen `conn` (asyncpg) als
erstes Argument, damit Caller Transaktionen klammern können.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg

from app.signature import canonicalize  # RFC 8785 JCS -> bytes

MAX_JSON_DEPTH = 32

# Normative Constraint-Typen (aae-constraint-taxonomy.md / AAE draft-04 §2.3).
# Unbekannte Typen sind strukturell erlaubt; ihre Enforcement-Semantik
# (required:true -> DENY) entscheidet der Evaluator/Acceptance-Gate, nicht der Store.
_REQUIRED_FIELDS = {
    "max_transaction_value": ("value", "currency"),
    "allowed_domains": ("value",),
    "rate_limit": ("value", "window"),
}


class EnvelopeValidationError(ValueError):
    """App-seitige Validierung fehlgeschlagen (vor INSERT)."""


def _json_depth(obj: Any, _d: int = 0) -> int:
    if _d > MAX_JSON_DEPTH:
        raise EnvelopeValidationError(f"JSON nesting exceeds MAX_JSON_DEPTH={MAX_JSON_DEPTH}")
    if isinstance(obj, dict):
        return max((_json_depth(v, _d + 1) for v in obj.values()), default=_d)
    if isinstance(obj, list):
        return max((_json_depth(v, _d + 1) for v in obj), default=_d)
    return _d


def validate_constraints(constraints: Any) -> None:
    """Strukturelle + typisierte Per-Constraint-Shape-Validierung.

    DB erzwingt nur jsonb_typeof='array'; die typisierte Logik lebt hier.
    """
    if not isinstance(constraints, list):
        raise EnvelopeValidationError("constraints must be a JSON array")
    for i, c in enumerate(constraints):
        if not isinstance(c, dict):
            raise EnvelopeValidationError(f"constraint[{i}] must be an object")
        ctype = c.get("type")
        if not isinstance(ctype, str) or not ctype:
            raise EnvelopeValidationError(f"constraint[{i}] missing string 'type'")
        if "required" in c and not isinstance(c["required"], bool):
            raise EnvelopeValidationError(f"constraint[{i}].required must be bool")
        req = _REQUIRED_FIELDS.get(ctype)
        if req:
            for field in req:
                if field not in c:
                    raise EnvelopeValidationError(
                        f"constraint[{i}] type={ctype} missing required field '{field}'"
                    )
            if ctype == "max_transaction_value" and not isinstance(c["value"], (int, float)):
                raise EnvelopeValidationError(f"constraint[{i}] max_transaction_value.value must be number")
            if ctype == "allowed_domains" and not isinstance(c["value"], list):
                raise EnvelopeValidationError(f"constraint[{i}] allowed_domains.value must be array")
            if ctype == "rate_limit" and not isinstance(c["value"], int):
                raise EnvelopeValidationError(f"constraint[{i}] rate_limit.value must be integer")


def validate_envelope(mandate: dict, constraints: Any, validity: dict) -> None:
    if not isinstance(mandate, dict):
        raise EnvelopeValidationError("mandate must be an object")
    if not isinstance(validity, dict):
        raise EnvelopeValidationError("validity must be an object")
    for part in (mandate, constraints, validity):
        _json_depth(part)
    validate_constraints(constraints)


def canonical_scope(scope: Any) -> bytes:
    """JCS-canonical bytes des MANDATE.scope (key-order-invariant, deterministisch)."""
    return canonicalize(scope)


def canonical_raw(envelope: dict) -> bytes:
    """JCS-canonical bytes des vollen Envelopes (Quelle des aae_ref-Hash via Trigger)."""
    return canonicalize(envelope)


_INSERT_SQL = """
    INSERT INTO aae_envelopes
        (aae_id, issuer_did, envelope_signature,
         mandate_scope, constraints, validity,
         scope_canonical, aae_version, taxonomy_version, evaluator_version,
         raw_canonical, issuer_trust_tier)
    VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7, $8, $9, $10, $11, $12)
    RETURNING aae_ref
"""


async def persist_envelope(
    conn: asyncpg.Connection,
    *,
    aae_id: str,
    issuer_did: str,
    envelope_signature: str,
    mandate: dict,
    constraints: list,
    validity: dict,
    aae_version: str,
    taxonomy_version: str,
    raw_canonical: bytes,
    issuer_trust_tier: str,
    evaluator_version: Optional[str] = None,
) -> str:
    """Persistiert einen AAE-Envelope; gibt den vom Trigger gesetzten aae_ref zurück.

    aae_ref wird NICHT übergeben — der DB-Trigger berechnet sha256(raw_canonical).
    raw_canonical = die EXAKTEN signierten JWS-payload-Bytes (D-1 Acceptance-Gate) —
    NICHT app-seitig re-serialisiert (sonst Signatur-Bypass). scope_canonical bleibt
    JCS(scope) für den single_use-Unique-Index.
    """
    validate_envelope(mandate, constraints, validity)
    if not isinstance(raw_canonical, (bytes, bytearray)):
        raise EnvelopeValidationError("raw_canonical must be bytes (the exact signed JWS payload)")
    scope = mandate.get("scope", mandate)
    scope_canonical = canonical_scope(scope)
    row = await conn.fetchrow(
        _INSERT_SQL,
        aae_id, issuer_did, envelope_signature,
        json.dumps(mandate), json.dumps(constraints), json.dumps(validity),
        scope_canonical, aae_version, taxonomy_version, evaluator_version,
        bytes(raw_canonical), issuer_trust_tier,
    )
    return row["aae_ref"]


async def persist_with_delegation(
    conn: asyncpg.Connection,
    *,
    parent_did: Optional[str],
    child_did: str,
    credential_type: str,
    hop_depth: int,
    **envelope_kwargs: Any,
) -> str:
    """Atomare no-FK-Mitigation: Envelope-INSERT + agent_delegations-Write in EINER Transaktion.

    Schlägt der Delegations-Write fehl, wird auch der Envelope-INSERT zurückgerollt.
    """
    async with conn.transaction():
        aae_ref = await persist_envelope(conn, **envelope_kwargs)
        await conn.execute(
            """INSERT INTO agent_delegations
                   (parent_did, child_did, aae_id, credential_type, hop_depth, created_at)
               VALUES ($1, $2, $3, $4, $5, NOW())
               ON CONFLICT (parent_did, child_did, aae_id) DO NOTHING""",
            parent_did, child_did, envelope_kwargs["aae_id"],
            credential_type, hop_depth,
        )
        return aae_ref
