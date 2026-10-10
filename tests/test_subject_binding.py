"""Tests — AAE §5 Step 4 (subject-binding challenge-response).

Unit tests run in a rollback transaction, so the used-nonce rows they write are
discarded. The one test that needs two independent connections to observe the
single-use invariant commits and cleans up after itself.
"""
import base64
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.api_jws import PyJWS

from app.enforcement import subject_binding as sb
from app.enforcement.subject_binding import (
    RELYING_PARTY_AUD,
    SubjectBindingError,
    issue_challenge,
    nonce_hash,
    purge_expired_nonces,
    verify_subject_binding,
)

DB = dict(host="localhost", database=os.getenv("DB_NAME", "moltstack"), user="moltstack")


def _new_did_key():
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    return priv, did, pub_hex


async def _register_agent(conn, did, pub_hex):
    await conn.execute(
        "INSERT INTO agents (did, display_name, platform, agent_type, public_key_hex) "
        "VALUES ($1, $2, 'test', 'external', $3) "
        "ON CONFLICT (did) DO UPDATE SET public_key_hex = EXCLUDED.public_key_hex",
        did, f"tc-{did[-8:]}", pub_hex)


def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat().replace(
        "+00:00", "Z")


def _sign_challenge(priv, did, claims, *, kid=None, alg="EdDSA", key=None, cty=None):
    headers = {"kid": kid if kid is not None else f"{did}#key-1"}
    if cty is not None:
        headers["cty"] = cty
    payload = claims if isinstance(claims, bytes) else json.dumps(claims).encode()
    sk = key if key is not None else (priv if alg == "EdDSA" else "")
    return PyJWS().encode(payload, key=sk, algorithm=alg, headers=headers)


def _response(priv, did, aae_id, *, aud=RELYING_PARTY_AUD, nonce=None, iat=None, **kw):
    ch = issue_challenge(aae_id, aud=aud)
    claims = {
        "nonce": nonce if nonce is not None else ch["nonce"],
        "aud": aud,
        "iat": iat if iat is not None else _now_iso(),
        "aae_id": aae_id,
    }
    return _sign_challenge(priv, did, claims, **kw)


@pytest_asyncio.fixture
async def tx_conn():
    conn = await asyncpg.connect(**DB)
    tr = conn.transaction()
    await tr.start()
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


@pytest_asyncio.fixture
async def subject(tx_conn):
    priv, did, pub_hex = _new_did_key()
    await _register_agent(tx_conn, did, pub_hex)
    return priv, did, pub_hex


# ---------------- challenge issuing ----------------

def test_challenge_nonce_is_128_bit_and_bound():
    ch = issue_challenge("urn:uuid:a")
    rand, exp, tag = ch["nonce"].split(".")
    assert len(bytes.fromhex(rand)) == 16          # 128 bits, the draft floor
    assert int(exp) > int(time.time())
    assert ch["aud"] == RELYING_PARTY_AUD and ch["aae_id"] == "urn:uuid:a"
    # a nonce minted for another AAE carries a different tag
    assert issue_challenge("urn:uuid:b")["nonce"].split(".")[2] != tag


def test_challenge_requires_aae_id():
    with pytest.raises(SubjectBindingError):
        issue_challenge("")


def test_clock_skew_matches_the_evaluator():
    from app.enforcement.evaluator import CLOCK_SKEW
    assert sb.CLOCK_SKEW_SECONDS == int(CLOCK_SKEW.total_seconds())


# ---------------- the positive path ----------------

async def test_valid_response_accepts(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    out = await verify_subject_binding(
        _response(priv, did, aae_id), tx_conn, aae_id=aae_id, subject_did=did)
    assert out["subject_did"] == did and out["aae_id"] == aae_id
    assert out["aud"] == RELYING_PARTY_AUD


# ---------------- (a) signature under the subject key ----------------

async def test_wrong_key_rejected(tx_conn, subject):
    priv, did, _ = subject
    other, _, _ = _new_did_key()
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(other, did, aae_id), tx_conn, aae_id=aae_id, subject_did=did)


async def test_alg_none_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, aae_id, alg="none", key=""), tx_conn,
            aae_id=aae_id, subject_did=did)


async def test_unregistered_subject_rejected(tx_conn):
    priv, did, _ = _new_did_key()  # never registered
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, aae_id), tx_conn, aae_id=aae_id, subject_did=did)


# ---------------- (b) verification method authorized for authentication ----------------

async def test_non_authentication_vm_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, aae_id, kid=f"{did}#key-2"), tx_conn,
            aae_id=aae_id, subject_did=did)


async def test_kid_of_another_did_rejected(tx_conn, subject):
    priv, did, _ = subject
    other_did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, aae_id, kid=f"{other_did}#key-1"), tx_conn,
            aae_id=aae_id, subject_did=did)


async def test_kid_path_traversal_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, aae_id, kid="did:moltrust:../../etc#key-1"), tx_conn,
            aae_id=aae_id, subject_did=did)


def _handmade(priv, header_raw: bytes, payload: bytes) -> str:
    """A compact JWS from a raw header segment, correctly signed.

    PyJWS().encode() takes a dict, so it cannot express a duplicate member. This builds
    the segments directly and signs over them, so the only thing wrong with the token is
    the one thing under test.
    """
    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    h, p = b64(header_raw), b64(payload)
    return f"{h}.{p}.{b64(priv.sign(f'{h}.{p}'.encode()))}"


def _claims(aae_id, nonce):
    return json.dumps({"nonce": nonce, "aud": RELYING_PARTY_AUD,
                       "iat": _now_iso(), "aae_id": aae_id}).encode()


async def test_handmade_single_kid_accepted(tx_conn, subject):
    """Positive control for the builder below: without the duplicate this must pass."""
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    ch = issue_challenge(aae_id)
    token = _handmade(priv, f'{{"alg":"EdDSA","kid":"{did}#key-1"}}'.encode(),
                      _claims(aae_id, ch["nonce"]))
    out = await verify_subject_binding(token, tx_conn, aae_id=aae_id, subject_did=did)
    assert out["subject_did"] == did


async def test_duplicate_kid_in_the_protected_header_rejected(tx_conn, subject):
    """Both kid values are well formed and the signature is valid; the duplicate is the flaw.

    Under a last-wins parser this token verifies, and which verification method it claims
    depends on the parser rather than on the token.
    """
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    ch = issue_challenge(aae_id)
    header = (f'{{"alg":"EdDSA","kid":"did:moltrust:0000111122223333#key-1",'
              f'"kid":"{did}#key-1"}}').encode()
    token = _handmade(priv, header, _claims(aae_id, ch["nonce"]))
    with pytest.raises(SubjectBindingError, match="duplicate JSON member: kid"):
        await verify_subject_binding(token, tx_conn, aae_id=aae_id, subject_did=did)


async def test_duplicate_alg_in_the_protected_header_rejected(tx_conn, subject):
    """The alg-confusion shape of the same flaw: EdDSA first, none second."""
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    ch = issue_challenge(aae_id)
    header = f'{{"alg":"EdDSA","alg":"none","kid":"{did}#key-1"}}'.encode()
    token = _handmade(priv, header, _claims(aae_id, ch["nonce"]))
    with pytest.raises(SubjectBindingError, match="duplicate JSON member: alg"):
        await verify_subject_binding(token, tx_conn, aae_id=aae_id, subject_did=did)


# ---------------- (c) nonce origin and single use ----------------

async def test_foreign_nonce_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    forged = "a" * 32 + ".9999999999." + "B" * 43
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, aae_id, nonce=forged), tx_conn,
            aae_id=aae_id, subject_did=did)


async def test_nonce_minted_for_another_aae_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    other_nonce = issue_challenge(f"test:vc:{uuid.uuid4().hex[:12]}")["nonce"]
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, aae_id, nonce=other_nonce), tx_conn,
            aae_id=aae_id, subject_did=did)


async def test_expired_nonce_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    stale = issue_challenge(aae_id, now=int(time.time()) - sb.NONCE_TTL_SECONDS - 60)["nonce"]
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, aae_id, nonce=stale), tx_conn,
            aae_id=aae_id, subject_did=did)


async def test_nonce_reuse_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    nonce = issue_challenge(aae_id)["nonce"]
    first = _response(priv, did, aae_id, nonce=nonce)
    await verify_subject_binding(first, tx_conn, aae_id=aae_id, subject_did=did)
    second = _response(priv, did, aae_id, nonce=nonce)  # freshly signed, same nonce
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(second, tx_conn, aae_id=aae_id, subject_did=did)


async def test_used_nonce_row_is_written(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    nonce = issue_challenge(aae_id)["nonce"]
    await verify_subject_binding(
        _response(priv, did, aae_id, nonce=nonce), tx_conn, aae_id=aae_id, subject_did=did)
    row = await tx_conn.fetchrow(
        "SELECT aae_id, aud, subject_did FROM aae_subject_nonces WHERE nonce_hash = $1",
        nonce_hash(nonce))
    assert row["aae_id"] == aae_id and row["subject_did"] == did
    assert row["aud"] == RELYING_PARTY_AUD


async def test_purge_removes_only_expired_rows(tx_conn):
    fresh = b"f" * 32
    stale = b"s" * 32
    for h, delta in ((fresh, timedelta(hours=1)), (stale, timedelta(hours=-1))):
        await tx_conn.execute(
            "INSERT INTO aae_subject_nonces (nonce_hash, aae_id, aud, subject_did, expires_at) "
            "VALUES ($1, 'x', 'y', 'z', now() + $2)", h, delta)
    await purge_expired_nonces(tx_conn)
    assert await tx_conn.fetchval(
        "SELECT count(*) FROM aae_subject_nonces WHERE nonce_hash = $1", stale) == 0
    assert await tx_conn.fetchval(
        "SELECT count(*) FROM aae_subject_nonces WHERE nonce_hash = $1", fresh) == 1


# ---------------- (d) audience ----------------

async def test_wrong_aud_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, aae_id, aud="did:web:someone-else.example"), tx_conn,
            aae_id=aae_id, subject_did=did)


# ---------------- (e) aae_id ----------------

async def test_wrong_aae_id_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    other = f"test:vc:{uuid.uuid4().hex[:12]}"
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, other), tx_conn, aae_id=aae_id, subject_did=did)


# ---------------- (f) iat inside the skew ----------------

async def test_future_iat_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, aae_id, iat=_now_iso(sb.CLOCK_SKEW_SECONDS + 120)), tx_conn,
            aae_id=aae_id, subject_did=did)


async def test_ancient_iat_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    old = _now_iso(-(sb.NONCE_TTL_SECONDS + sb.CLOCK_SKEW_SECONDS + 120))
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, aae_id, iat=old), tx_conn, aae_id=aae_id, subject_did=did)


async def test_iat_numericdate_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, aae_id, iat=int(time.time())), tx_conn,
            aae_id=aae_id, subject_did=did)


# ---------------- payload shape ----------------

async def test_extra_member_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    ch = issue_challenge(aae_id)
    claims = {"nonce": ch["nonce"], "aud": RELYING_PARTY_AUD, "iat": _now_iso(),
              "aae_id": aae_id, "extra": 1}
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _sign_challenge(priv, did, claims), tx_conn, aae_id=aae_id, subject_did=did)


async def test_missing_member_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    ch = issue_challenge(aae_id)
    claims = {"nonce": ch["nonce"], "aud": RELYING_PARTY_AUD, "aae_id": aae_id}
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _sign_challenge(priv, did, claims), tx_conn, aae_id=aae_id, subject_did=did)


async def test_duplicate_json_key_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    ch = issue_challenge(aae_id)
    raw = ('{"nonce":"' + ch["nonce"] + '","aud":"' + RELYING_PARTY_AUD + '",'
           '"iat":"' + _now_iso() + '","aae_id":"' + aae_id + '","aae_id":"evil"}').encode()
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _sign_challenge(priv, did, raw), tx_conn, aae_id=aae_id, subject_did=did)


async def test_envelope_cty_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _response(priv, did, aae_id, cty="aae+json"), tx_conn,
            aae_id=aae_id, subject_did=did)


async def test_oversized_challenge_rejected(tx_conn, subject):
    priv, did, _ = subject
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    ch = issue_challenge(aae_id)
    claims = {"nonce": ch["nonce"], "aud": RELYING_PARTY_AUD,
              "iat": _now_iso(), "aae_id": aae_id + "x" * 12000}
    with pytest.raises(SubjectBindingError):
        await verify_subject_binding(
            _sign_challenge(priv, did, claims), tx_conn, aae_id=aae_id, subject_did=did)


# ---------------- did:web subject stays deferred ----------------

async def test_did_web_subject_not_implemented(tx_conn):
    priv, _, _ = _new_did_key()
    wdid = "did:web:example.com"
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    jws = _response(priv, wdid, aae_id, kid=f"{wdid}#key-1")
    with pytest.raises(NotImplementedError):
        await verify_subject_binding(jws, tx_conn, aae_id=aae_id, subject_did=wdid)
