"""
SkillEndorsementCredential — Whitepaper v4, Section 4.1

Regeln:
- Endorsement NUR mit gültigem evidence_hash (max 72h alt)
- Kein Self-Endorsement
- evidence_hash muss einmalig sein (UNIQUE constraint)
- Credential als W3C VC mit Ed25519 Signatur
- Endorser braucht keinen Mindest-Score (bootstrapping erlaubt)
- Läuft nach 90 Tagen ab (time-decay)
- Paid: $0.05 USDC via x402
"""
import uuid
import json
from datetime import datetime, timezone, timedelta
from typing import Optional
import asyncpg

ENDORSEMENT_EXPIRY_DAYS = 90
EVIDENCE_MAX_AGE_HOURS = 72

VALID_VERTICALS = {
    "skill", "shopping", "travel",
    "prediction", "salesguard", "sports", "core"
}

VALID_SKILLS = {
    "python", "javascript", "security", "prediction",
    "trading", "data_analysis", "api_integration",
    "smart_contracts", "nlp", "computer_vision", "general"
}


async def issue_endorsement(
    endorser_api_key: str,
    endorsed_did: str,
    skill: str,
    evidence_hash: str,
    evidence_timestamp: str,
    vertical: str,
    conn: asyncpg.Connection
) -> dict:
    """
    Stellt SkillEndorsementCredential aus.
    Returns W3C VC als dict.
    Raises ValueError bei Validierungsfehlern.
    """

    # 1. Endorser via API Key auflösen
    endorser = await conn.fetchrow(
        "SELECT owner_did AS did FROM api_keys WHERE key = $1 AND active = true",
        endorser_api_key
    )
    if not endorser or not endorser["did"]:
        raise ValueError("Invalid API key")
    endorser_did = endorser["did"]

    # 2. Self-Endorsement verhindern
    if endorser_did == endorsed_did:
        raise ValueError("Self-endorsement is not allowed")

    # 3. endorsed_did muss registriert sein
    endorsed_agent = await conn.fetchrow(
        "SELECT did FROM agents WHERE did = $1", endorsed_did
    )
    if not endorsed_agent:
        raise ValueError(f"Agent not found: {endorsed_did}")

    # 4. Vertical validieren
    if vertical not in VALID_VERTICALS:
        raise ValueError(
            f"Invalid vertical: {vertical}. "
            f"Must be one of: {', '.join(sorted(VALID_VERTICALS))}"
        )

    # 5. Skill validieren
    if skill not in VALID_SKILLS:
        raise ValueError(
            f"Invalid skill: {skill}. "
            f"Must be one of: {', '.join(sorted(VALID_SKILLS))}"
        )

    # 6. evidence_hash Format prüfen
    clean_hash = evidence_hash.removeprefix("sha256:")
    if len(clean_hash) != 64:
        raise ValueError(
            "Invalid evidence_hash format. "
            "Expected sha256:<64-char-hex>"
        )

    # 7. evidence_timestamp max 72h alt
    try:
        ev_ts = datetime.fromisoformat(
            evidence_timestamp.replace("Z", "+00:00")
        )
    except ValueError:
        raise ValueError(
            "Invalid evidence_timestamp. Expected ISO 8601."
        )
    age = datetime.now(timezone.utc) - ev_ts
    if age.total_seconds() > EVIDENCE_MAX_AGE_HOURS * 3600:
        raise ValueError(
            f"evidence_timestamp is too old "
            f"(max {EVIDENCE_MAX_AGE_HOURS}h). "
            f"Create a new interaction proof."
        )

    # 8. Timestamps
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=ENDORSEMENT_EXPIRY_DAYS)

    # 9. In DB einfügen (UNIQUE constraint verhindert Duplikate)
    try:
        await conn.execute(
            """
            INSERT INTO endorsements
              (endorser_did, endorsed_did, skill, evidence_hash,
               evidence_timestamp, vertical, weight,
               issued_at, expires_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            endorser_did, endorsed_did, skill,
            clean_hash, ev_ts, vertical, 1.0,
            now, expires_at
        )
    except asyncpg.UniqueViolationError:
        raise ValueError(
            "Duplicate endorsement: this evidence_hash has "
            "already been used for an endorsement."
        )

    # 10. W3C VC aufbauen
    vc_id = f"urn:uuid:{uuid.uuid4()}"
    vc = {
        "@context": [
            "https://www.w3.org/2018/credentials/v1",
            "https://moltrust.ch/credentials/v1"
        ],
        "id": vc_id,
        "type": ["VerifiableCredential", "SkillEndorsementCredential"],
        "issuer": endorser_did,
        "issuanceDate": now.isoformat(),
        "expirationDate": expires_at.isoformat(),
        "credentialSubject": {
            "id": endorsed_did,
            "skill": skill,
            "evidenceHash": f"sha256:{clean_hash}",
            "evidenceTimestamp": evidence_timestamp,
            "weight": 1.0,
            "vertical": vertical
        }
    }

    # 11. Ed25519 Signatur (HIGH-5: real signing, no more sandbox_unsigned)
    from app.credentials import get_signing_key
    import jcs as _jcs
    signing_key = get_signing_key()
    payload = _jcs.canonicalize(vc)
    signed = signing_key.sign(payload)
    vc["proof"] = {
        "type": "Ed25519Signature2020",
        "created": now.isoformat(),
        "verificationMethod": "did:web:api.moltrust.ch#key-1",
        "proofPurpose": "assertionMethod",
        "canonicalizationAlgorithm": "JCS",
        "proofValue": signed.signature.hex()
    }

    # VC JWT in DB speichern
    await conn.execute(
        "UPDATE endorsements SET vc_jwt = $1 "
        "WHERE endorser_did = $2 AND endorsed_did = $3 "
        "AND evidence_hash = $4",
        json.dumps(vc), endorser_did, endorsed_did, clean_hash
    )

    # Trust Score Cache invalidieren
    await conn.execute(
        "DELETE FROM trust_score_cache WHERE did = $1",
        endorsed_did
    )

    return vc
