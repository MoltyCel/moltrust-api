"""Leak canary for delegating agents (ops LC-1, ADR-LC1, docs/leak-canary.md).

An issuer asks for one canary credential per counterparty. The credential comes
from `app.main.issue_and_store_vc`, the path every issued credential takes, so
nothing in it marks it as a canary. The only record of what it is lives in
`leak_canaries`, readable by the issuer alone.

When a canary is presented at POST /credentials/verify by a party that should
not hold it, a signed leak event is written, and the counterparty goes on the
issuer's hold list if the canary asked for that. The event carries AER
material (ADR-AER) that `moltrust-verify` recomputes offline.

A canary of kind `token` (ops LC-4) is a UCAN delegation token instead of a
credential. It is minted by `app.delegation.mint_ucan`, the path every token
from POST /delegation/create takes, and only its SHA-256 is stored. Its
presentation point is the token endpoint, POST /delegation/verify; the event
is the same event with `trigger` set to `token_presentation`.

Three rules hold the design together:

- **One lookup for every presentation.** `observe_presentation` runs the same
  query for every credential, and everything that depends on the answer runs
  after the response, in a background task. The presenter's answer and its
  timing are the same for a canary and any other credential.
- **The kernel stays pure.** A hold is written into the issuer's mandate by
  `apply_holds`; the enforce kernel evaluates it unchanged.
- **No personal data.** DIDs, digests and timestamps. The presenter appears as
  the DID behind its API key, or not at all. The key itself is never stored or
  logged.
"""
from __future__ import annotations

import base64
import copy
import datetime
import hashlib
import logging
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from app.signature import canonicalize
from app.registry_keys import REGISTRY_KID, get_private_key, get_public_key_bytes

log = logging.getLogger("moltrust.leak_canary")

router = APIRouter(prefix="/canary", tags=["Leak Canary"])

SOURCE_ID = "did:web:api.moltrust.ch#leak-canary"
EVENT_VERSION = 1
# Domain separation as in app/enforcement/verdict_sign.py: fixed prefix, byte level.
EVENT_TAG = b"moltrust:leak-canary-event:v1\x00"
HOLD_VERB = "leak_canary_hold"
QUERY_KIND = "leak_canary_presented"
EVIDENCE_DAYS = 30
# One event per canary and presenter in this window. Someone holding a canary
# could otherwise fill the table by presenting it in a loop.
DEDUPE_MINUTES = 10
# Same ceiling as POST /credentials/issue (10/minute), counted per issuer.
ISSUE_PER_MINUTE = 10
MODES = ("bound", "decoy")
ON_LEAK = ("hold", "none")
KINDS = ("credential", "token")
# Where a presentation came in. The event carries one of these as `trigger`;
# the path is what a holder proof in that event has to name.
TRIGGER_VERIFIER = "verifier_presentation"
TRIGGER_TOKEN = "token_presentation"
TRIGGER_PATHS = {TRIGGER_VERIFIER: "/credentials/verify", TRIGGER_TOKEN: "/delegation/verify"}

_DID_RE = re.compile(r"^did:(moltrust|web|key):[A-Za-z0-9._:%-]{1,112}$")
_TYPE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9]{1,63}$")


def _sdk():
    """moltrust_enforce, from the installed package or from this repo's sdk/python/src."""
    try:
        import moltrust_enforce
    except ImportError:
        src = str(Path(__file__).resolve().parents[1] / "sdk" / "python" / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import moltrust_enforce
    return moltrust_enforce


def _ts(dt: datetime.datetime) -> str:
    """RFC 3339 UTC on whole seconds, the only time form AER accepts."""
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canary_ref(credential: dict) -> str:
    """`sha256:` + hex SHA-256 of JCS(credential), as the issuer received it."""
    return "sha256:" + hashlib.sha256(canonicalize(credential)).hexdigest()


def token_hash(token: str) -> str:
    """Hex SHA-256 of the token as the issuer received it: the lookup key, and
    the only form in which a token canary is stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_ref(token: str) -> str:
    """`canary_ref` of a token canary: `sha256:` + `token_hash(token)`."""
    return "sha256:" + token_hash(token)


def token_audience(token: Any) -> Optional[str]:
    """The `aud` of a compact UCAN, read without verifying it, or None."""
    try:
        from app.delegation import _decode_token
        aud = _decode_token(token)[1].get("aud")
    except Exception:
        return None
    return aud if isinstance(aud, str) else None


# ---------------------------------------------------------------- event signature

def _event_signing_input(event: dict) -> bytes:
    body = {k: v for k, v in event.items() if k != "signature"}
    return EVENT_TAG + canonicalize(body)


def sign_event(event: dict) -> dict:
    """The event with `signature` set: Ed25519 by the registry key, hex.

    Hex and not base64: the outbound filter in app.main (SENSITIVE_PATTERNS)
    redacts a 40-character run between two non-alphanumerics, which base64 and
    base64url both produce by chance. A hex string is one alphanumeric run and
    never matches.
    """
    sig = get_private_key().sign(_event_signing_input(event))
    return {**event, "signature": {"alg": "Ed25519", "kid": REGISTRY_KID, "sig": sig.hex()}}


def verify_event(event: Any, public_key: bytes) -> bool:
    """Fail-closed check of an event signature against a raw Ed25519 public key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        raw = bytes.fromhex(event["signature"]["sig"])
        Ed25519PublicKey.from_public_bytes(public_key).verify(raw, _event_signing_input(event))
        return True
    except Exception:  # fail-closed: any decode or verify failure is False
        return False


# --------------------------------------------------------------------- AER part

def trust_list() -> dict:
    """The trust list `moltrust-verify` needs for leak events: one source, the registry key."""
    pub = base64.b64encode(get_public_key_bytes()).decode("ascii")
    return {"trust_list_version": 1,
            "sources": {SOURCE_ID: {"keys": [{"algorithm": "ed25519", "public_key": pub,
                                              "keyid": REGISTRY_KID}]}}}


def build_aer(ref: str, counterparty_did: str, attribution: str, event_id: str,
              observed_at: str, valid_until: str) -> dict:
    """Evidence that the canary was presented, and the decision that follows from it.

    One evidence item signed by SOURCE_ID, a one-grant mandate that allows
    `{"verb": HOLD_VERB}` only when that evidence is true, the bundle and the
    f_ext record. The attribution is part of the signed query, so the offline
    check covers it as well. `moltrust-verify --input <this> --trust-list <trust_list()>`
    recomputes it without contacting MolTrust.
    """
    sdk = _sdk()
    query = {"kind": QUERY_KIND, "canary_ref": ref, "counterparty_did": counterparty_did,
             "attribution": attribution}
    statement = sdk.make_statement(SOURCE_ID, query, True, observed_at, valid_until, event_id)
    payload = sdk.evidence_payload_bytes(statement)
    sig = get_private_key().sign(sdk.pae(sdk.PAYLOAD_TYPE, payload))
    envelope = sdk.make_envelope(statement, [{"keyid": REGISTRY_KID,
                                              "sig": base64.b64encode(sig).decode("ascii")}])
    action = {"verb": HOLD_VERB}
    mandate = {"grants": [{
        "action_binding": sdk.action_digest(action),
        "disposition": "allow",
        "type_fields": ["verb"],
        "constraints": [{"type": "evidence_bool", "query": query, "expect": True}],
    }]}
    transaction = {"action": action, "counterparty_did": counterparty_did, "event_id": event_id}
    bundle = sdk.build_bundle([envelope], mandate, transaction, observed_at)
    record = sdk.f_ext(mandate, transaction, bundle)
    return {"mandate": mandate, "transaction": transaction, "bundle": bundle, "record": record}


def build_event(canary: dict, presentation: dict, credential_valid: bool,
                now: datetime.datetime, trigger: str = TRIGGER_VERIFIER) -> dict:
    """The signed event. `trigger` names where the presentation came in; for a
    token, `credential_valid` says whether the token verified. Nothing else
    differs between the two."""
    if trigger not in TRIGGER_PATHS:
        raise ValueError(f"unknown trigger {trigger!r}")
    event_id = str(uuid.uuid4())
    who = attribution(canary, presentation)
    observed_at = _ts(now)
    valid_until = _ts(now + datetime.timedelta(days=EVIDENCE_DAYS))
    event = {
        "event_version": EVENT_VERSION,
        "event_id": event_id,
        "canary_id": str(canary["id"]),
        "issuer_did": canary["issuer_did"],
        "counterparty_did": canary["counterparty_did"],
        "mode": canary["mode"],
        "trigger": trigger,
        "observed_at": observed_at,
        "presenter_did": presentation.get("api_key_did"),
        "credential_valid": bool(credential_valid),
        "canary_ref": canary["canary_ref"],
        "presentation": presentation,
        "attribution": who,
        "aer": build_aer(canary["canary_ref"], canary["counterparty_did"], who, event_id,
                         observed_at, valid_until),
    }
    return sign_event(event)


# ---------------------------------------------------------- presentation context
#
# Every IPFS-published credential can be fetched by anyone who has its CID, so a
# presentation by itself proves that the credential is out, not through whom.
# What ties a presentation to the counterparty is its key: an RFC 9421 request
# signature (the ops V6 profile, app/enforcement/request_signature.py) by the
# counterparty's DID over the very request that carries the credential. A copy
# from IPFS carries no such signature.
#
# The check runs for every presentation and the result goes into every answer
# under the same field names, canary or not; anything else would let a holder
# tell a canary by the answer.

ATTRIBUTION_COUNTERPARTY = "counterparty"
ATTRIBUTION_OPEN = "open"


def _holder_proof_record(proof: dict) -> dict:
    """The parts of a verified request signature a third party needs, all hex.

    Hex because the outbound filter redacts 40-character base64 runs by chance
    (see sign_event); a hex string is one alphanumeric run.
    """
    from app.enforcement.request_signature import _SIG_RE
    sig_b64 = _SIG_RE.match(proof["signature"]).group(2)
    return {
        "public_key_hex": proof["public_key_hex"],
        "signature_hex": base64.b64decode(sig_b64).hex(),
        "signature_base_hex": proof["signature_base"].encode("utf-8").hex(),
        "request_body_hex": proof["request_body"].hex(),
    }


async def describe_presentation(db_pool, method: str, path: str, headers, body: bytes,
                                credential: Any, *, subject_did: Optional[str] = None) -> dict:
    """What a presentation was authenticated with: the DID behind the API key, and
    the holder's request signature, if any. Never raises.

    `subject_match` compares the holder's DID with the credential's subject, or
    with `subject_did` when the caller names one (a token's audience).

    `holder_proof.record` carries the verified signature for the event; the
    answer to the caller gets `public_presentation()` without it.
    """
    out: dict = {"api_key_did": None, "holder_proof": None}
    api_key = headers.get("x-api-key")
    signed = bool(headers.get("signature-input") or headers.get("signature"))
    hp = None
    if signed:
        did = (headers.get("x-moltrust-did") or "").strip() or None
        hp = {"scheme": "rfc9421", "did": did, "valid": False, "reason": None,
              "subject_match": False}
        out["holder_proof"] = hp
    if db_pool is None:
        if hp is not None:
            hp["reason"] = "database unavailable"
        return out
    from app.credits import resolve_did_from_api_key
    from app.enforcement import request_signature
    try:
        async with db_pool.acquire() as conn:
            if api_key and len(api_key) <= 128:
                out["api_key_did"] = await resolve_did_from_api_key(conn, api_key)
            if hp is not None:
                try:
                    proof = await request_signature.verify_request(
                        conn, method=method, path=path, headers=headers, body=body)
                except request_signature.SignatureError as e:
                    hp["reason"] = str(e)
                else:
                    proof["request_body"] = body
                    hp.update(valid=True, did=proof["did"], keyid=proof["keyid"],
                              record=_holder_proof_record(proof))
    except Exception as e:
        log.warning("presentation context incomplete: %s", type(e).__name__)
        if hp is not None and not hp["valid"]:
            hp["reason"] = hp["reason"] or "holder proof could not be checked"
    if hp is not None and hp["valid"]:
        if subject_did is None:
            subject = credential.get("credentialSubject") if isinstance(credential, dict) else None
            subject_did = subject.get("id") if isinstance(subject, dict) else None
        hp["subject_match"] = subject_did is not None and subject_did == hp["did"]
    return out


def public_presentation(presentation: dict) -> dict:
    """The presentation block as the caller sees it: same fields for every credential."""
    hp = presentation.get("holder_proof")
    if hp is None:
        return {"api_key_did": presentation.get("api_key_did"), "holder_proof": None}
    return {"api_key_did": presentation.get("api_key_did"),
            "holder_proof": {k: hp.get(k) for k in ("scheme", "did", "valid", "reason",
                                                    "subject_match")}}


def attribution(canary: dict, presentation: dict) -> str:
    """`counterparty` when the counterparty's own key signed the presentation, else `open`."""
    hp = presentation.get("holder_proof") or {}
    if hp.get("valid") and hp.get("did") == canary["counterparty_did"]:
        return ATTRIBUTION_COUNTERPARTY
    return ATTRIBUTION_OPEN


def check_holder_proof(holder_proof: dict, path: str = "/credentials/verify") -> bool:
    """Offline check of the holder proof in an event. Fail-closed.

    The Ed25519 signature holds over the signature base; the base names the DID,
    the path and the SHA-256 of the request body that is carried along.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        rec = holder_proof["record"]
        base = bytes.fromhex(rec["signature_base_hex"])
        body = bytes.fromhex(rec["request_body_hex"])
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(rec["public_key_hex"])).verify(
            bytes.fromhex(rec["signature_hex"]), base)
        lines = base.decode("utf-8").split("\n")
        digest = "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"
        return (f'"x-moltrust-did": {holder_proof["did"]}' in lines
                and f'"@path": {path}' in lines
                and any(line.startswith('"content-digest": ') and digest in line
                        for line in lines))
    except Exception:  # fail-closed
        return False


# ------------------------------------------------------------------ enforce hook

def _path_ok(path: Any) -> bool:
    if not isinstance(path, str) or not path or len(path) > 256:
        return False
    segments = path.split(".")
    return len(segments) <= 8 and all(s != "" for s in segments)


def apply_holds(mandate: dict, held: list, counterparty_field: str) -> dict:
    """Place a `hold` copy in front of every `allow` grant, one per held counterparty.

    Each copy carries the grant's constraints plus `exact` on `counterparty_field`.
    The kernel takes the first grant whose constraints hold, so a transaction
    for a held counterparty ends in PENDING and every other one is decided as
    before. Raises ValueError when the result would exceed the kernel's bounds.
    """
    from app.enforcement.enforce_check import MAX_CONSTRAINTS_PER_GRANT, MAX_GRANTS
    out = []
    for grant in mandate["grants"]:
        if grant.get("disposition") == "allow":
            if held and len(grant.get("constraints", [])) + 1 > MAX_CONSTRAINTS_PER_GRANT:
                raise ValueError("a grant already carries the maximum number of constraints")
            for did in held:
                held_copy = copy.deepcopy(grant)
                held_copy["disposition"] = "hold"
                held_copy["constraints"] = list(held_copy["constraints"]) + [
                    {"type": "exact", "field": counterparty_field, "value": did}]
                out.append(held_copy)
        out.append(grant)
    if len(out) > MAX_GRANTS:
        raise ValueError(f"the patched mandate would carry {len(out)} grants, more than {MAX_GRANTS}")
    return {**mandate, "grants": out}


# ----------------------------------------------------------- presentation hook

_LOOKUP = {
    "proof_value": "SELECT id, issuer_did, counterparty_did, mode, on_leak, canary_ref "
                   "FROM leak_canaries WHERE proof_value = $1",
    "token_hash": "SELECT id, issuer_did, counterparty_did, mode, on_leak, canary_ref "
                  "FROM leak_canaries WHERE token_hash = $1",
}


async def _lookup(db_pool, column: str, key: str) -> Optional[dict]:
    """The one indexed query every presentation gets. Never raises."""
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(_LOOKUP[column], key)
    except Exception as e:
        log.warning("canary lookup failed: %s", type(e).__name__)
        return None
    return dict(row) if row is not None else None


async def observe_presentation(db_pool, credential: Any, result: dict, presentation: dict,
                               background_tasks: BackgroundTasks) -> None:
    """Called by POST /credentials/verify for every presentation. Never raises."""
    if db_pool is None or not isinstance(credential, dict):
        return
    try:
        from app.crypto.proof_utils import get_primary_proof_value
        proof = get_primary_proof_value(credential)
    except Exception:
        proof = None
    if not isinstance(proof, str) or not proof:
        return
    row = await _lookup(db_pool, "proof_value", proof)
    if row is None:
        return
    background_tasks.add_task(record_presentation, row, presentation,
                              bool(isinstance(result, dict) and result.get("valid")))


async def observe_token_presentation(db_pool, token: Any, result: dict, presentation: dict,
                                     background_tasks: BackgroundTasks) -> None:
    """Called by POST /delegation/verify for every presented token. Never raises.

    The same single lookup as for a credential, on the token's SHA-256, whatever
    the token looks like; everything else runs after the response.
    """
    if db_pool is None or not isinstance(token, str):
        return
    row = await _lookup(db_pool, "token_hash", token_hash(token))
    if row is None:
        return
    background_tasks.add_task(record_presentation, row, presentation,
                              bool(isinstance(result, dict) and result.get("valid")),
                              TRIGGER_TOKEN)


async def record_presentation(canary: dict, presentation: dict, credential_valid: bool,
                              trigger: str = TRIGGER_VERIFIER) -> Optional[dict]:
    """Decide whether the presentation is a leak and, if so, write event and hold.

    Returns the event, or None when the presenter was expected, the presentation
    falls into the dedupe window, or something failed (logged, never raised).
    """
    from app.main import db_pool
    try:
        async with db_pool.acquire() as conn:
            # Who asked is the API key. A holder signature says who handed the
            # credential over, so it decides attribution, not whether to record.
            presenter = presentation.get("api_key_did")
            expected = {canary["issuer_did"]}
            if canary["mode"] == "bound":
                expected.add(canary["counterparty_did"])
            if presenter in expected:
                return None
            async with conn.transaction():
                recent = await conn.fetchval(
                    "SELECT 1 FROM leak_canary_events WHERE canary_id = $1 "
                    "AND presenter_did IS NOT DISTINCT FROM $2 "
                    "AND event->>'attribution' = $4 "
                    "AND observed_at > now() - make_interval(mins => $3) LIMIT 1",
                    canary["id"], presenter, DEDUPE_MINUTES, attribution(canary, presentation))
                if recent:
                    return None
                now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
                event = build_event(canary, presentation, credential_valid, now, trigger)
                await conn.execute(
                    "INSERT INTO leak_canary_events (id, canary_id, issuer_did, counterparty_did, "
                    "presenter_did, observed_at, event) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
                    uuid.UUID(event["event_id"]), canary["id"], canary["issuer_did"],
                    canary["counterparty_did"], presenter, now, _json(event))
                if canary["on_leak"] == "hold":
                    # An active hold keeps its first event; a released one comes back.
                    await conn.execute(
                        "INSERT INTO leak_canary_holds (issuer_did, counterparty_did, disposition, event_id) "
                        "VALUES ($1, $2, 'hold', $3) "
                        "ON CONFLICT (issuer_did, counterparty_did) DO UPDATE "
                        "SET event_id = EXCLUDED.event_id, created_at = now(), released_at = NULL "
                        "WHERE leak_canary_holds.released_at IS NOT NULL",
                        canary["issuer_did"], canary["counterparty_did"],
                        uuid.UUID(event["event_id"]))
        log.info("leak canary event %s", event["event_id"])
        return event
    except Exception as e:
        log.warning("leak canary event not written: %s", type(e).__name__)
        return None


def _json(value: Any) -> str:
    import json
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


# ------------------------------------------------------------------- endpoints

def _require_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")) -> str:
    """app.main.verify_api_key, imported lazily (app.main imports this module)."""
    from app.main import verify_api_key
    return verify_api_key(x_api_key)


async def _issuer(conn, api_key: str) -> str:
    from app.credits import resolve_did_from_api_key
    did = await resolve_did_from_api_key(conn, api_key)
    if not did:
        raise HTTPException(403, "This API key is not bound to a DID. Bind one first: POST /auth/signup-did")
    return did


def _pool():
    from app.main import db_pool
    if db_pool is None:
        raise HTTPException(503, "Database unavailable")
    return db_pool


def _not_found() -> JSONResponse:
    # Uniform for "not yours" and "does not exist": no oracle for other issuers' rows.
    return JSONResponse(status_code=404, content={"error": "not_found"})


async def _counterparty_problem(conn, did: str) -> Optional[str]:
    """Why `did` cannot receive a canary, or None.

    The counterparty must be a registered agent: a credential's subject
    references agents(did), and a token canary follows the same rule. A revoked
    agent is refused as well; its key no longer authenticates a request, so a
    presentation could never be attributed to it. Checked before anything is
    issued, stored or published.
    """
    row = await conn.fetchrow("SELECT revoked_at FROM agents WHERE did = $1", did)
    if row is None:
        return ("counterparty_did is not a registered agent. The counterparty registers "
                "its DID first: POST /identity/register")
    if row["revoked_at"] is not None:
        return "counterparty_did is revoked"
    return None


class CanaryIssueRequest(BaseModel):
    counterparty_did: str = Field(max_length=128)
    mode: str = Field(default="bound", description="bound | decoy")
    on_leak: str = Field(default="hold", description="hold | none")
    kind: str = Field(default="credential", description="credential | token")
    credential_type: str = Field(default="AgentTrustCredential", max_length=64)
    # kind=token: the fields of POST /delegation/create without proofs. The
    # issuer is the delegator; the audience follows from the mode.
    capabilities: Optional[dict] = Field(default=None, description="UCAN cap map, kind=token only")
    ttl_seconds: int = Field(default=3600, ge=1, le=31536000)
    nonce: Optional[str] = Field(default=None, max_length=128)
    facts: Optional[dict] = None

    @model_validator(mode="after")
    def _kind_fields(self):
        if self.kind == "token":
            if not isinstance(self.capabilities, dict) or not self.capabilities:
                raise ValueError("kind token needs capabilities, a non-empty UCAN cap map")
        elif self.capabilities is not None or self.facts is not None or self.nonce is not None:
            raise ValueError("capabilities, facts and nonce belong to kind token")
        return self

    @field_validator("counterparty_did")
    @classmethod
    def _did(cls, v):
        if not _DID_RE.match(v):
            raise ValueError("Invalid DID format")
        return v

    @field_validator("mode")
    @classmethod
    def _mode(cls, v):
        if v not in MODES:
            raise ValueError("mode must be bound or decoy")
        return v

    @field_validator("on_leak")
    @classmethod
    def _on_leak(cls, v):
        if v not in ON_LEAK:
            raise ValueError("on_leak must be hold or none")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        if v not in KINDS:
            raise ValueError("kind must be credential or token")
        return v

    @field_validator("credential_type")
    @classmethod
    def _type(cls, v):
        if not _TYPE_RE.match(v):
            raise ValueError("Credential type must be alphanumeric, starting with a letter")
        # A canary is a credential about the counterparty, signed by the
        # registry; its type is not the issuer's choice (ops CRED-2410).
        from app.credentials import credential_type_refusal
        refusal = credential_type_refusal(v, admin=False)
        if refusal:
            raise ValueError(refusal)
        return v


@router.post("/issue")
async def canary_issue(body: CanaryIssueRequest, api_key: str = Depends(_require_api_key)):
    """Issue one canary for a counterparty and hand it to that counterparty:
    `credential` for kind credential, `delegation.ucan` for kind token."""
    from app.main import issue_and_store_vc
    from app.crypto.proof_utils import get_primary_proof_value
    from app import delegation as ucan
    pool = _pool()
    async with pool.acquire() as conn:
        issuer = await _issuer(conn, api_key)
        counterparty_problem = await _counterparty_problem(conn, body.counterparty_did)
        recent = await conn.fetchval(
            "SELECT COUNT(*) FROM leak_canaries WHERE issuer_did = $1 "
            "AND created_at > now() - interval '1 minute'", issuer)
        # A token canary passes the gate every token from this delegator passes.
        policy = await ucan.policy_problem(conn, issuer, 0) if body.kind == "token" else None
    if issuer == body.counterparty_did:
        raise HTTPException(422, "counterparty_did is the issuer's own DID")
    if counterparty_problem is not None:
        raise HTTPException(422, counterparty_problem)
    if recent >= ISSUE_PER_MINUTE:
        raise HTTPException(429, f"At most {ISSUE_PER_MINUTE} canaries per minute")
    if policy is not None:
        raise HTTPException(*policy)
    if body.kind == "token":
        return await _issue_token(pool, issuer, body)
    # Both modes: the subject is the counterparty's registered DID. The modes
    # differ only in who is an expected presenter (record_presentation).
    vc = await issue_and_store_vc(body.counterparty_did, body.credential_type)
    canary_id = uuid.uuid4()
    async with pool.acquire() as conn:
        # POST /credentials/issue hands out the credential with `renew_at` beside
        # it (renew_at_middleware, subject = caller). The canary carries the same
        # field with the same value, or a forwarded copy would differ by it.
        from app import renewal
        vc = renewal.with_renew_at(vc, await renewal.renew_at_for_did(conn, issuer), 200)
        ref = canary_ref(vc)
        await conn.execute(
            "INSERT INTO leak_canaries (id, issuer_did, counterparty_did, mode, on_leak, "
            "credential_type, proof_value, canary_ref) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            canary_id, issuer, body.counterparty_did, body.mode, body.on_leak,
            body.credential_type, get_primary_proof_value(vc), ref)
    return {"canary_id": str(canary_id), "counterparty_did": body.counterparty_did,
            "mode": body.mode, "on_leak": body.on_leak, "kind": "credential",
            "canary_ref": ref, "credential": vc}


async def _issue_token(pool, issuer: str, body: CanaryIssueRequest) -> dict:
    """A UCAN from `mint_ucan`, the path of POST /delegation/create, with the
    issuer as delegator. Only its SHA-256 is stored; the token itself exists
    only in this response, as a token from POST /delegation/create does."""
    import asyncpg
    from app import delegation as ucan
    from app.main import update_last_seen
    # Both modes: the audience is the counterparty's registered DID, as the
    # subject of a credential canary is.
    audience = body.counterparty_did
    token = ucan.mint_ucan(delegator_did=issuer, audience_did=audience,
                           capabilities=body.capabilities, ttl_seconds=body.ttl_seconds,
                           nonce=body.nonce, facts=body.facts)
    canary_id = uuid.uuid4()
    ref = token_ref(token)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO leak_canaries (id, issuer_did, counterparty_did, mode, on_leak, "
                "kind, token_hash, canary_ref) VALUES ($1, $2, $3, $4, $5, 'token', $6, $7)",
                canary_id, issuer, body.counterparty_did, body.mode, body.on_leak,
                token_hash(token), ref)
    except asyncpg.UniqueViolationError:
        # Same delegator, audience, capabilities and second: the same token bytes.
        raise HTTPException(409, "An identical token was issued in the same second; retry") from None
    # POST /delegation/create records the delegator as seen; so does this.
    await update_last_seen(issuer)
    return {"canary_id": str(canary_id), "counterparty_did": body.counterparty_did,
            "mode": body.mode, "on_leak": body.on_leak, "kind": "token", "canary_ref": ref,
            # The answer POST /delegation/create gives for the same token.
            "delegation": {"ucan": token, "issuer": ucan.ISSUER_DID, "delegator": issuer,
                           "audience": audience, "ucv": ucan.UCAN_VERSION}}


@router.get("/list")
async def canary_list(api_key: str = Depends(_require_api_key),
                      limit: int = Query(default=50, ge=1, le=200),
                      offset: int = Query(default=0, ge=0)):
    """The caller's canaries, newest first, with their event count."""
    pool = _pool()
    async with pool.acquire() as conn:
        issuer = await _issuer(conn, api_key)
        total = await conn.fetchval("SELECT COUNT(*) FROM leak_canaries WHERE issuer_did = $1", issuer)
        rows = await conn.fetch(
            "SELECT c.id, c.counterparty_did, c.mode, c.on_leak, c.kind, c.credential_type, c.canary_ref, "
            "c.created_at, (SELECT COUNT(*) FROM leak_canary_events e WHERE e.canary_id = c.id) AS events "
            "FROM leak_canaries c WHERE c.issuer_did = $1 ORDER BY c.created_at DESC, c.id "
            "LIMIT $2 OFFSET $3", issuer, limit, offset)
    items = [{"canary_id": str(r["id"]), "counterparty_did": r["counterparty_did"],
              "mode": r["mode"], "on_leak": r["on_leak"], "kind": r["kind"],
              "credential_type": r["credential_type"],
              "canary_ref": r["canary_ref"], "created_at": _ts(r["created_at"]),
              "events": int(r["events"])} for r in rows]
    nxt = offset + len(items)
    return {"total": int(total), "items": items, "next_offset": nxt if nxt < total else None}


@router.get("/events")
async def canary_events(api_key: str = Depends(_require_api_key),
                        canary_id: Optional[str] = Query(default=None, max_length=36),
                        limit: int = Query(default=50, ge=1, le=200),
                        offset: int = Query(default=0, ge=0)):
    """The caller's leak events, newest first, as signed by MolTrust."""
    import json
    pool = _pool()
    cid = None
    if canary_id is not None:
        try:
            cid = uuid.UUID(canary_id)
        except ValueError:
            return _not_found()
    async with pool.acquire() as conn:
        issuer = await _issuer(conn, api_key)
        if cid is not None:
            owned = await conn.fetchval(
                "SELECT 1 FROM leak_canaries WHERE id = $1 AND issuer_did = $2", cid, issuer)
            if not owned:
                return _not_found()
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM leak_canary_events WHERE issuer_did = $1 "
            "AND ($2::uuid IS NULL OR canary_id = $2)", issuer, cid)
        rows = await conn.fetch(
            "SELECT event FROM leak_canary_events WHERE issuer_did = $1 "
            "AND ($2::uuid IS NULL OR canary_id = $2) ORDER BY observed_at DESC, id "
            "LIMIT $3 OFFSET $4", issuer, cid, limit, offset)
    items = [json.loads(r["event"]) if isinstance(r["event"], str) else r["event"] for r in rows]
    nxt = offset + len(items)
    return {"total": int(total), "items": items, "next_offset": nxt if nxt < total else None}


@router.get("/holds")
async def canary_holds(api_key: str = Depends(_require_api_key)):
    """Counterparties the caller currently holds after a leak event."""
    pool = _pool()
    async with pool.acquire() as conn:
        issuer = await _issuer(conn, api_key)
        rows = await conn.fetch(
            "SELECT counterparty_did, disposition, event_id, created_at FROM leak_canary_holds "
            "WHERE issuer_did = $1 AND released_at IS NULL ORDER BY counterparty_did", issuer)
    return {"holds": [{"counterparty_did": r["counterparty_did"], "disposition": r["disposition"],
                       "event_id": str(r["event_id"]), "since": _ts(r["created_at"])} for r in rows]}


class HoldReleaseRequest(BaseModel):
    counterparty_did: str = Field(max_length=128)


@router.post("/holds/release")
async def canary_hold_release(body: HoldReleaseRequest, api_key: str = Depends(_require_api_key)):
    pool = _pool()
    async with pool.acquire() as conn:
        issuer = await _issuer(conn, api_key)
        done = await conn.fetchval(
            "UPDATE leak_canary_holds SET released_at = now() WHERE issuer_did = $1 "
            "AND counterparty_did = $2 AND released_at IS NULL RETURNING 1",
            issuer, body.counterparty_did)
    if not done:
        return _not_found()
    return {"released": body.counterparty_did}


class EnforceHookRequest(BaseModel):
    mandate: dict
    counterparty_field: str = Field(max_length=256, description="Dotted path of the transaction "
                                    "field that names the counterparty, e.g. counterparty_did")


@router.post("/enforce-hook")
async def canary_enforce_hook(body: EnforceHookRequest, api_key: str = Depends(_require_api_key)):
    """The caller's mandate with its held counterparties applied. Pass the result to /enforce/check."""
    from app.enforcement.enforce_check import _mandate_problem
    problem = _mandate_problem(body.mandate)
    if problem is not None:
        raise HTTPException(422, problem)
    if not _path_ok(body.counterparty_field):
        raise HTTPException(422, "counterparty_field is not a dotted path of at most 8 segments")
    pool = _pool()
    async with pool.acquire() as conn:
        issuer = await _issuer(conn, api_key)
        held = [r["counterparty_did"] for r in await conn.fetch(
            "SELECT counterparty_did FROM leak_canary_holds WHERE issuer_did = $1 "
            "AND released_at IS NULL ORDER BY counterparty_did", issuer)]
    try:
        patched = apply_holds(body.mandate, held, body.counterparty_field)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"mandate": patched, "held_counterparties": held,
            "grants_added": len(patched["grants"]) - len(body.mandate["grants"])}


@router.get("/trust-list")
async def canary_trust_list():
    """Trust list for `moltrust-verify` on the `aer` member of a leak event."""
    return trust_list()
