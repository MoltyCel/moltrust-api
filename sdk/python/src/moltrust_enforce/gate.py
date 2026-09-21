"""Offline trust gate for x402 endpoints — ``require_moltrust``.

The point of this module is that it never calls MolTrust. A request arrives
carrying a MolTrust-signed attestation and a signature made with the calling
agent's own key; both are checked against a cached JWKS and nothing else. No
network call sits in the request path, so the gate cannot be opened by an
outage on our side and cannot be slowed by one.

    from moltrust_enforce.gate import require_moltrust, load_jwks

    JWKS = load_jwks("/etc/moltrust/jwks.json")
    gate = require_moltrust(min_score=60, jwks=JWKS)

    decision = gate(method="POST", path="/guard/vc/skill/issue", headers=request.headers)
    if not decision.allowed:
        return 403, {"error": decision.reason, "detail": decision.detail}

What the caller sends
---------------------
``X-MolTrust-Attestation``  the ``gate_attestation`` field from
                            ``GET /skill/trust-score/<did>`` — a compact JWS
                            carrying did, public_key, trust_score, withheld,
                            credential_types and valid_until.
``X-MolTrust-Timestamp``    unix seconds, the moment the proof was made.
``X-MolTrust-Proof``        base64url Ed25519 signature over the binding
                            string, made with the DID's own private key.

The binding string is built by :func:`binding_string` and ties the proof to
one method, one path, one DID and one moment. Signing a bare nonce would let
anyone who saw the proof replay it against a different route.

Deny by default
---------------
Every path that is not an explicit allow returns a denial, including the ones
that look like infrastructure problems: a malformed header, an unknown key id,
a JWKS with no usable key. An expired attestation is a denial. A withheld score
is a denial — a score we have not computed is not a low score and it is not a
pass either, and a gate that treats "unknown" as "fine" is the failure this
module exists to prevent.

On replay
---------
The proof is fresh within ``max_age_seconds`` (default 300). Inside that window
the same proof can be presented more than once unless the caller supplies
``seen``, a callable that records a proof and returns False if it has been
recorded before. Without it the gate is replay-resistant, not replay-proof, and
that is stated here rather than implied by silence.
"""
from __future__ import annotations

import base64
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

__all__ = [
    "Decision",
    "GateAttestation",
    "binding_string",
    "load_jwks",
    "require_moltrust",
    "verify_attestation",
]

BINDING_VERSION = "moltrust-gate/v1"
DEFAULT_MAX_AGE_SECONDS = 300

HEADER_ATTESTATION = "x-moltrust-attestation"
HEADER_TIMESTAMP = "x-moltrust-timestamp"
HEADER_PROOF = "x-moltrust-proof"


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    """The gate's answer. ``allowed`` is never True by omission."""

    allowed: bool
    reason: str
    detail: str = ""
    did: Optional[str] = None
    trust_score: Optional[float] = None
    credential_types: Sequence[str] = field(default_factory=tuple)

    def __bool__(self) -> bool:  # `if decision:` reads as "was it allowed"
        return self.allowed


@dataclass(frozen=True)
class GateAttestation:
    """The verified payload of a gate attestation."""

    did: str
    public_key: str
    trust_score: Optional[float]
    withheld: bool
    credential_types: Sequence[str]
    computed_at: str
    valid_until: str
    policy_version: str
    version: int


class AttestationError(Exception):
    """The attestation does not verify, with the reason in the message."""


# ---------------------------------------------------------------------------
# base64url (RFC 7515 §2, unpadded)
# ---------------------------------------------------------------------------

def _b64url_decode(s: str) -> bytes:
    if not isinstance(s, str) or not s:
        raise AttestationError("empty base64url value")
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except Exception as exc:  # noqa: BLE001 — surfaced as a denial reason
        raise AttestationError(f"not valid base64url: {exc}") from exc


# ---------------------------------------------------------------------------
# JWKS
# ---------------------------------------------------------------------------

def load_jwks(path_or_mapping) -> dict:
    """Read a JWKS from a file path or accept one already in memory.

    Deliberately not a fetch. The gate is offline by design, so refreshing the
    key set is an operational step with its own schedule — a cron that writes
    the file, a config-management run, a container rebuild. Putting an HTTP
    call here would quietly undo the property the whole module is for.

    The registry key set is published at
    ``https://api.moltrust.ch/.well-known/jwks.json``.
    """
    if isinstance(path_or_mapping, Mapping):
        jwks = dict(path_or_mapping)
    else:
        with open(path_or_mapping, "r", encoding="utf-8") as fh:
            jwks = json.load(fh)
    keys = jwks.get("keys")
    if not isinstance(keys, list) or not keys:
        raise ValueError("JWKS has no keys[]")
    return jwks


def _public_key_from_jwk(jwk: Mapping[str, Any]) -> Ed25519PublicKey:
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise AttestationError(
            f"unsupported key: kty={jwk.get('kty')!r} crv={jwk.get('crv')!r}"
        )
    raw = _b64url_decode(jwk.get("x") or "")
    if len(raw) != 32:
        raise AttestationError(f"Ed25519 key must be 32 bytes, got {len(raw)}")
    return Ed25519PublicKey.from_public_bytes(raw)


def _key_for_kid(jwks: Mapping[str, Any], kid: str) -> Ed25519PublicKey:
    for jwk in jwks.get("keys", []):
        if jwk.get("kid") == kid:
            return _public_key_from_jwk(jwk)
    raise AttestationError(
        f"no key for kid {kid!r} in the JWKS — refresh it, or the token was "
        "not issued by this registry"
    )


# ---------------------------------------------------------------------------
# Attestation
# ---------------------------------------------------------------------------

def _parse_rfc3339(value: str) -> float:
    """Seconds since the epoch, from the timestamps the registry emits."""
    import datetime as _dt

    if not isinstance(value, str) or not value:
        raise AttestationError("missing timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise AttestationError(f"not an RFC 3339 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.timestamp()


def verify_attestation(token: str, jwks: Mapping[str, Any],
                       now: Optional[float] = None) -> GateAttestation:
    """Verify a compact JWS gate attestation. Raises ``AttestationError``.

    The payload travels inside the token, so nothing is rebuilt and no
    canonicalization happens on this side — the bytes that were signed are the
    bytes that are checked.
    """
    if not isinstance(token, str):
        raise AttestationError("attestation is not a string")
    parts = token.split(".")
    if len(parts) != 3:
        raise AttestationError(f"not a compact JWS: {len(parts)} parts, expected 3")
    header_b64, payload_b64, sig_b64 = parts

    try:
        header = json.loads(_b64url_decode(header_b64))
    except ValueError as exc:
        raise AttestationError(f"undecodable header: {exc}") from exc
    if header.get("alg") != "EdDSA":
        raise AttestationError(f"alg={header.get('alg')!r}, expected EdDSA")
    kid = header.get("kid")
    if not kid:
        raise AttestationError("header carries no kid")

    key = _key_for_kid(jwks, kid)
    try:
        key.verify(_b64url_decode(sig_b64), f"{header_b64}.{payload_b64}".encode("ascii"))
    except InvalidSignature as exc:
        raise AttestationError("signature does not cover this payload") from exc

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except ValueError as exc:
        raise AttestationError(f"undecodable payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise AttestationError("payload is not an object")

    version = payload.get("v")
    if version != 2:
        raise AttestationError(
            f"payload version {version!r} is not a gate attestation — the v1 "
            "trust-score payload carries no public key and cannot gate anything"
        )
    for required in ("did", "public_key", "valid_until"):
        if not payload.get(required):
            raise AttestationError(f"payload has no {required}")

    valid_until = _parse_rfc3339(payload["valid_until"])
    current = time.time() if now is None else now
    if current > valid_until:
        raise AttestationError(
            f"expired at {payload['valid_until']}; ask the agent for a fresh one"
        )

    return GateAttestation(
        did=payload["did"],
        public_key=payload["public_key"],
        trust_score=payload.get("trust_score"),
        withheld=bool(payload.get("withheld")),
        credential_types=tuple(payload.get("credential_types") or ()),
        computed_at=payload.get("computed_at", ""),
        valid_until=payload["valid_until"],
        policy_version=payload.get("policy_version", ""),
        version=version,
    )


# ---------------------------------------------------------------------------
# Proof of control
# ---------------------------------------------------------------------------

def binding_string(method: str, path: str, did: str, timestamp: str) -> bytes:
    """What the calling agent signs.

    Method, path, DID and moment, in that order, newline-separated. Each one is
    there to stop a specific reuse: a proof made for a free route replayed
    against a paid one, a proof lifted from one agent and presented by another,
    a proof kept and used tomorrow.
    """
    return "\n".join((BINDING_VERSION, method.upper(), path, did, str(timestamp))).encode("utf-8")


def _verify_proof(att: GateAttestation, method: str, path: str,
                  timestamp: str, proof_b64: str, max_age: int,
                  now: Optional[float]) -> Optional[str]:
    """None when the proof is good, otherwise the reason it is not."""
    try:
        ts = float(str(timestamp).strip())
    except (TypeError, ValueError):
        return f"timestamp {timestamp!r} is not a number"
    current = time.time() if now is None else now
    age = current - ts
    if age > max_age:
        return f"proof is {int(age)}s old, limit {max_age}s"
    if age < -max_age:
        return f"proof is {int(-age)}s in the future, limit {max_age}s"

    try:
        raw_key = bytes.fromhex(att.public_key)
    except ValueError:
        return "public_key in the attestation is not hex"
    if len(raw_key) != 32:
        return f"public_key must be 32 bytes, got {len(raw_key)}"

    try:
        signature = _b64url_decode(proof_b64)
    except AttestationError as exc:
        return str(exc)
    try:
        Ed25519PublicKey.from_public_bytes(raw_key).verify(
            signature, binding_string(method, path, att.did, timestamp)
        )
    except InvalidSignature:
        return "proof does not verify under the attested public key"
    except Exception as exc:  # noqa: BLE001 — malformed key material
        return f"proof unusable: {type(exc).__name__}"
    return None


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def _header(headers: Mapping[str, Any], name: str) -> str:
    """Case-insensitive lookup that works for dicts and framework header maps."""
    getter = getattr(headers, "get", None)
    if getter is not None:
        value = getter(name) or getter(name.title()) or getter(name.upper())
        if value:
            return str(value)
    for key, value in dict(headers).items():
        if str(key).lower() == name:
            return str(value)
    return ""


def require_moltrust(
    min_score: Optional[float] = None,
    credential_type: Optional[str] = None,
    *,
    jwks: Mapping[str, Any],
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    allow_withheld: bool = False,
    seen: Optional[Callable[[str], bool]] = None,
    required_credentials: Optional[Iterable[str]] = None,
) -> Callable[..., Decision]:
    """Build a gate.

    ``min_score``   the lowest trust score that passes. ``None`` means the
                    score is not consulted at all — useful when the credential
                    is the whole requirement.
    ``credential_type``  a credential the DID must hold, by type. Several can
                    be required through ``required_credentials``.
    ``allow_withheld``  off by default. Turning it on lets an agent we have
                    never evaluated through, which is a real choice for a
                    discount tier and a bad one for a spend authorisation.
                    It has no effect on ``min_score``: a withheld score is
                    null, so any numeric threshold still denies.
    ``seen``        optional replay store. Called with the proof; return False
                    if it has been presented before.
    """
    jwks = load_jwks(jwks)
    wanted = list(required_credentials or ())
    if credential_type:
        wanted.append(credential_type)
    wanted = sorted(set(wanted))

    def gate(method: str, path: str, headers: Mapping[str, Any],
             now: Optional[float] = None) -> Decision:
        token = _header(headers, HEADER_ATTESTATION)
        proof = _header(headers, HEADER_PROOF)
        timestamp = _header(headers, HEADER_TIMESTAMP)
        if not token:
            return Decision(False, "attestation_missing",
                            f"send the gate_attestation from "
                            f"GET /skill/trust-score/<did> in {HEADER_ATTESTATION}")
        if not proof or not timestamp:
            return Decision(False, "proof_missing",
                            f"{HEADER_PROOF} and {HEADER_TIMESTAMP} are both required")

        try:
            att = verify_attestation(token, jwks, now=now)
        except AttestationError as exc:
            return Decision(False, "attestation_invalid", str(exc))

        problem = _verify_proof(att, method, path, timestamp, proof, max_age_seconds, now)
        if problem:
            return Decision(False, "proof_invalid", problem, did=att.did)

        if seen is not None and not seen(proof):
            return Decision(False, "proof_replayed",
                            "this proof has been presented before", did=att.did)

        if att.withheld and not allow_withheld:
            return Decision(False, "score_withheld",
                            "no score has been computed for this agent; that is "
                            "not a low score, and this gate does not read it as one",
                            did=att.did, credential_types=att.credential_types)

        if min_score is not None:
            if att.trust_score is None:
                return Decision(False, "score_missing",
                                "the attestation carries no score to compare",
                                did=att.did, credential_types=att.credential_types)
            if att.trust_score < min_score:
                return Decision(False, "score_below_minimum",
                                f"score {att.trust_score} is below {min_score}",
                                did=att.did, trust_score=att.trust_score,
                                credential_types=att.credential_types)

        missing = [c for c in wanted if c not in att.credential_types]
        if missing:
            return Decision(False, "credential_missing",
                            f"holds {list(att.credential_types)}, needs {missing}",
                            did=att.did, trust_score=att.trust_score,
                            credential_types=att.credential_types)

        return Decision(True, "ok", "", did=att.did, trust_score=att.trust_score,
                        credential_types=att.credential_types)

    gate.min_score = min_score  # type: ignore[attr-defined]
    gate.required_credentials = tuple(wanted)  # type: ignore[attr-defined]
    return gate


def constant_time_equals(a: str, b: str) -> bool:
    """Exported because a gate's own token comparisons should use it too."""
    return hmac.compare_digest(str(a), str(b))
