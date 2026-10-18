"""Proof of possession for a DID, carried in the request (ops V6).

X-MolTrust-DID alone names a DID and proves nothing about who sends it. On the
endpoints that write into a DID's record (/vc/aae/submit, /vc/aae/evaluate)
the caller now signs the request with the key of that DID, as an HTTP Message
Signature (RFC 9421), and the server checks it against the key in force from
the pinned registry (app/enforcement/did_key.py). No DID document is fetched
at decision time; a DID that does not resolve there is refused.

The proof stays in the decision record, so a third party can check it later
from the record alone: signature, Signature-Input, keyid, the public key used,
the signature base and the request body (see proof() below).

Profile, deliberately narrow so that two implementations cannot disagree:

- exactly one signature in Signature-Input and Signature;
- covered components exactly "@method", "@path", "x-moltrust-did" and
  "content-digest", in any order;
- parameters created (integer, required), nonce (string, required),
  keyid (string, required, the DID URL of the key in force),
  alg (required, "ed25519"), and optionally expires (integer) and tag (string);
  nothing else;
- Content-Digest (RFC 9530) carries sha-256 over the exact request body;
- created no more than MAX_AGE seconds old and no more than MAX_SKEW seconds
  ahead; a nonce is accepted once per key within that window.

Example, for a request whose body is b'{}':

  X-MolTrust-DID: did:moltrust:0123456789abcdef
  Content-Digest: sha-256=:RBNvo1WzZ4oRRq0W9+hknpT7T8If536DEMBg9hyq/4o=:
  Signature-Input: sig1=("@method" "@path" "x-moltrust-did" "content-digest");created=1760000000;nonce="n-1";keyid="did:moltrust:0123456789abcdef#key-1";alg="ed25519"
  Signature: sig1=:<base64 Ed25519 signature over the signature base>:
"""

from __future__ import annotations

import base64
import hashlib
import re
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.did_patterns import DID_RESOLVE_PATTERN
from app.enforcement.did_key import KeyResolutionError, require_current_key

REQUIRED_COMPONENTS = frozenset({"@method", "@path", "x-moltrust-did", "content-digest"})
MAX_AGE = 300
MAX_SKEW = 30
_NONCE_RE = re.compile(r"^[\x21\x23-\x5b\x5d-\x7e]{8,128}$")  # printable, no quote or backslash
_LABEL = r"[a-z*][a-z0-9_\-.*]*"
_INPUT_RE = re.compile(rf"^({_LABEL})=\(([^()]*)\)((?:;[a-z*][a-z0-9_\-.*]*=(?:-?\d{{1,15}}|\"[^\"\\]*\"))*)$")
_PARAM_RE = re.compile(r";([a-z*][a-z0-9_\-.*]*)=(-?\d{1,15}|\"[^\"\\]*\")")
_SIG_RE = re.compile(rf"^({_LABEL})=:([A-Za-z0-9+/]+={{0,2}}):$")
_DIGEST_RE = re.compile(r"(?:^|,)\s*sha-256=:([A-Za-z0-9+/]+={0,2}):\s*(?:,|$)")
_ALLOWED_PARAMS = {"created", "nonce", "keyid", "alg", "expires", "tag"}


class SignatureError(ValueError):
    """The request signature does not prove possession. The message names why."""


def _parse_input(value: str) -> tuple[str, list[str], list[tuple[str, object]]]:
    m = _INPUT_RE.match(value.strip())
    if not m:
        raise SignatureError("Signature-Input must hold exactly one signature in RFC 9421 form")
    label, inner, params_raw = m.groups()
    items = inner.split(" ") if inner else []
    components = []
    for it in items:
        if len(it) < 2 or it[0] != '"' or it[-1] != '"' or '"' in it[1:-1] or "\\" in it:
            raise SignatureError("covered components must be plain quoted strings")
        components.append(it[1:-1])
    if len(set(components)) != len(components) or set(components) != REQUIRED_COMPONENTS:
        raise SignatureError(
            "covered components must be exactly "
            '"@method" "@path" "x-moltrust-did" "content-digest"')
    params: list[tuple[str, object]] = []
    for name, raw in _PARAM_RE.findall(params_raw):
        if name not in _ALLOWED_PARAMS:
            raise SignatureError(f"signature parameter {name!r} is not accepted")
        if any(n == name for n, _ in params):
            raise SignatureError(f"signature parameter {name!r} appears twice")
        params.append((name, raw[1:-1] if raw.startswith('"') else int(raw)))
    return label, components, params


def _serialize_params(components: list[str], params: list[tuple[str, object]]) -> str:
    out = "(" + " ".join(f'"{c}"' for c in components) + ")"
    for name, val in params:
        out += f";{name}=" + (f'"{val}"' if isinstance(val, str) else str(val))
    return out


def signature_base(method: str, path: str, did: str, content_digest: str,
                   components: list[str], params: list[tuple[str, object]]) -> str:
    """RFC 9421 section 2.5 signature base for this profile."""
    values = {"@method": method.upper(), "@path": path,
              "x-moltrust-did": did.strip(), "content-digest": content_digest.strip()}
    lines = [f'"{c}": {values[c]}' for c in components]
    lines.append(f'"@signature-params": {_serialize_params(components, params)}')
    return "\n".join(lines)


async def verify_request(conn, *, method: str, path: str, headers, body: bytes,
                         now: float | None = None) -> dict:
    """Check the request signature; return the proof for the decision record.

    `headers` is a case-insensitive mapping. Raises SignatureError with the
    reason; spends the nonce only once everything else has passed.
    """
    now = time.time() if now is None else now
    sig_input = headers.get("signature-input")
    sig = headers.get("signature")
    did = headers.get("x-moltrust-did")
    digest = headers.get("content-digest")
    if not sig_input or not sig:
        raise SignatureError("Signature-Input and Signature headers are both required")
    if not did or not DID_RESOLVE_PATTERN.match(did.strip()):
        raise SignatureError("X-MolTrust-DID must name the signing did:moltrust")
    did = did.strip()
    if not digest:
        raise SignatureError("Content-Digest header is required (RFC 9530, sha-256)")

    label, components, params = _parse_input(sig_input)
    sm = _SIG_RE.match(sig.strip())
    if not sm or sm.group(1) != label:
        raise SignatureError("Signature must hold exactly one signature with the label of Signature-Input")
    p = dict(params)
    if p.get("alg") != "ed25519":
        raise SignatureError('signature parameter alg must be "ed25519"')
    created, nonce, keyid = p.get("created"), p.get("nonce"), p.get("keyid")
    if not isinstance(created, int):
        raise SignatureError("signature parameter created (integer) is required")
    if not isinstance(nonce, str) or not _NONCE_RE.match(nonce):
        raise SignatureError("signature parameter nonce is required, 8 to 128 printable characters")
    if not isinstance(keyid, str) or not keyid.startswith(did + "#"):
        raise SignatureError("signature parameter keyid must be a key of the DID in X-MolTrust-DID")
    if created > now + MAX_SKEW:
        raise SignatureError("signature created lies in the future")
    if now - created > MAX_AGE:
        raise SignatureError(f"signature created is older than {MAX_AGE} seconds")
    expires = p.get("expires")
    if expires is not None and (not isinstance(expires, int) or expires <= now):
        raise SignatureError("signature has expired")

    dm = _DIGEST_RE.search(digest)
    if not dm:
        raise SignatureError("Content-Digest must carry sha-256")
    if base64.b64decode(dm.group(1)) != hashlib.sha256(body).digest():
        raise SignatureError("Content-Digest does not match the request body")

    try:
        pub_raw = await require_current_key(conn, did, keyid)
    except KeyResolutionError as e:
        raise SignatureError(str(e))

    base = signature_base(method, path, did, digest, components, params)
    try:
        Ed25519PublicKey.from_public_bytes(pub_raw).verify(
            base64.b64decode(sm.group(2)), base.encode("utf-8"))
    except (InvalidSignature, ValueError):
        raise SignatureError("signature does not verify against the key in force")

    nonce_hash = hashlib.sha256(f"{keyid}\n{nonce}".encode()).hexdigest()
    await conn.execute("DELETE FROM request_signature_nonces WHERE expires_at < now()")
    spent = await conn.fetchval(
        "INSERT INTO request_signature_nonces (nonce_hash, keyid, expires_at) "
        "VALUES ($1, $2, to_timestamp($3)) ON CONFLICT (nonce_hash) DO NOTHING RETURNING 1",
        nonce_hash, keyid, float(created + MAX_AGE + MAX_SKEW))
    if spent is None:
        raise SignatureError("nonce already used")

    return {
        "scheme": "rfc9421",
        "did": did,
        "keyid": keyid,
        "alg": "ed25519",
        "public_key_hex": pub_raw.hex(),
        "signature_input": sig_input.strip(),
        "signature": sig.strip(),
        "content_digest": digest.strip(),
        "signature_base": base,
        "created": created,
        "nonce": nonce,
    }
