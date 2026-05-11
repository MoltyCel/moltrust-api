"""Tests for app.identity — probe auto-mint, claimed-key resolution, error paths.

Run against the live moltstack DB; uses first_seen_ua='pytest-identity' as a
cleanup marker so no production rows are touched.
"""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import asyncpg
import pytest

from fastapi import HTTPException

from app.identity import (
    AuthError,
    Identity,
    PROBE_DID_PREFIX,
    PROBE_KEY_PREFIX,
    _mint_probe,
    env_api_keys,
    get_identity,
    hash_key,
    hash_session,
    increment_probe_call_count,
    maybe_extend_probe_ttl,
    require_claimed,
    require_probe,
    resolve_identity,
)


CLEAN_MARKER = "pytest-identity"


@pytest.fixture
async def probe_db():
    conn = await asyncpg.connect(
        host="localhost",
        database=os.getenv("DB_NAME", "moltstack"),
        user="moltstack",
    )
    await conn.execute("DELETE FROM probe_agents WHERE first_seen_ua = $1", CLEAN_MARKER)
    try:
        yield conn
    finally:
        await conn.execute("DELETE FROM probe_agents WHERE first_seen_ua = $1", CLEAN_MARKER)
        await conn.close()


def mock_request(
    headers: dict | None = None,
    client_host: str | None = "127.0.0.1",
    identity: Identity | None = None,
):
    """Minimal FastAPI Request stand-in. `identity` is stored on request.state."""
    h = {k.lower(): v for k, v in (headers or {}).items()}

    class Headers:
        def get(self, key, default=None):
            return h.get(key.lower(), default)

    client = SimpleNamespace(host=client_host) if client_host else None
    state = SimpleNamespace()
    if identity is not None:
        state.identity = identity
    return SimpleNamespace(headers=Headers(), client=client, state=state)


async def test_no_key_mints_probe(probe_db):
    req = mock_request(headers={"User-Agent": CLEAN_MARKER})
    identity = await resolve_identity(req, probe_db)

    assert identity.kind == "probe-new"
    assert identity.did.startswith(PROBE_DID_PREFIX)
    assert len(identity.did) == len(PROBE_DID_PREFIX) + 8
    assert identity.probe_key is not None
    assert identity.probe_key.startswith(PROBE_KEY_PREFIX)
    assert identity.api_key == identity.probe_key
    assert identity.is_probe is True
    assert identity.is_claimed is False

    # Verify row landed in DB with hashed key (raw never stored)
    row = await probe_db.fetchrow(
        "SELECT probe_key_hash, expires_at, call_count, call_cap FROM probe_agents WHERE did = $1",
        identity.did,
    )
    assert row["probe_key_hash"] == hash_key(identity.probe_key)
    assert row["call_count"] == 0
    assert row["call_cap"] == 50
    # raw key should NOT be retrievable
    assert (
        await probe_db.fetchval("SELECT COUNT(*) FROM probe_agents WHERE probe_key_hash = $1", identity.probe_key)
    ) == 0


async def test_probe_key_resolves(probe_db):
    _, key, _ = await _mint_probe(probe_db, ip="1.2.3.4", ua=CLEAN_MARKER, smithery_session_hash=None)
    req = mock_request(headers={"X-API-Key": key, "User-Agent": CLEAN_MARKER})

    identity = await resolve_identity(req, probe_db)
    assert identity.kind == "probe"
    assert identity.api_key == key
    assert identity.probe_key is None  # only fresh-mint sets probe_key
    assert identity.is_probe is True


async def test_expired_probe_rejected(probe_db):
    did, key, _ = await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    await probe_db.execute(
        "UPDATE probe_agents SET expires_at = $1 WHERE did = $2",
        datetime.now(tz=timezone.utc) - timedelta(minutes=1), did,
    )
    req = mock_request(headers={"X-API-Key": key})
    with pytest.raises(AuthError) as exc:
        await resolve_identity(req, probe_db)
    assert exc.value.status == 410
    assert "expired" in exc.value.message.lower()


async def test_capped_probe_rejected(probe_db):
    did, key, _ = await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    await probe_db.execute("UPDATE probe_agents SET call_count = call_cap WHERE did = $1", did)
    req = mock_request(headers={"X-API-Key": key})
    with pytest.raises(AuthError) as exc:
        await resolve_identity(req, probe_db)
    assert exc.value.status == 429
    assert "cap" in exc.value.message.lower()


async def test_claimed_probe_rejected(probe_db):
    did, key, _ = await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    await probe_db.execute(
        "UPDATE probe_agents SET claimed_at = now(), claimed_did = 'did:moltrust:0123456789abcdef' WHERE did = $1",
        did,
    )
    req = mock_request(headers={"X-API-Key": key})
    with pytest.raises(AuthError) as exc:
        await resolve_identity(req, probe_db)
    assert exc.value.status == 410


async def test_env_key_resolves_claimed(probe_db, monkeypatch):
    monkeypatch.setenv("MOLTRUST_API_KEYS", "test_env_key_xyz")
    req = mock_request(headers={"X-API-Key": "test_env_key_xyz"})
    identity = await resolve_identity(req, probe_db)
    assert identity.kind == "claimed"
    assert identity.did == "legacy:env"
    assert identity.is_claimed is True


async def test_invalid_key_rejected(probe_db, monkeypatch):
    monkeypatch.setenv("MOLTRUST_API_KEYS", "")
    req = mock_request(headers={"X-API-Key": "not-a-real-key"})
    with pytest.raises(AuthError) as exc:
        await resolve_identity(req, probe_db)
    assert exc.value.status == 401


async def test_invalid_probe_key_rejected(probe_db):
    req = mock_request(headers={"X-API-Key": "mt_probe_deadbeef"})
    with pytest.raises(AuthError) as exc:
        await resolve_identity(req, probe_db)
    assert exc.value.status == 401


async def test_x_real_ip_precedence(probe_db):
    req = mock_request(
        headers={
            "X-Forwarded-For": "10.0.0.1, 10.0.0.2",
            "X-Real-IP": "8.8.8.8",
            "User-Agent": CLEAN_MARKER,
        },
        client_host="127.0.0.1",
    )
    identity = await resolve_identity(req, probe_db)
    row = await probe_db.fetchrow("SELECT first_seen_ip FROM probe_agents WHERE did = $1", identity.did)
    assert str(row["first_seen_ip"]) == "10.0.0.1"  # XFF takes precedence over X-Real-IP per spec §4.2


async def test_increment_call_count(probe_db):
    did, _, _ = await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    assert await increment_probe_call_count(probe_db, did) == 1
    assert await increment_probe_call_count(probe_db, did) == 2


async def test_ttl_extension_below_threshold_noop(probe_db):
    did, _, _ = await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    extended = await maybe_extend_probe_ttl(probe_db, did)
    assert extended is False
    row = await probe_db.fetchrow("SELECT ttl_extensions FROM probe_agents WHERE did = $1", did)
    assert row["ttl_extensions"] == 0


async def test_ttl_extension_above_threshold(probe_db):
    did, _, _ = await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    # Set up a 24h window with 22h elapsed (91.7%, above the 80% trigger)
    await probe_db.execute(
        "UPDATE probe_agents SET created_at = now() - interval '22 hours', "
        "expires_at = now() + interval '2 hours' WHERE did = $1",
        did,
    )
    extended = await maybe_extend_probe_ttl(probe_db, did)
    assert extended is True
    row = await probe_db.fetchrow("SELECT ttl_extensions, expires_at FROM probe_agents WHERE did = $1", did)
    assert row["ttl_extensions"] == 1

    # Set up a 36h window with 30h elapsed (83%, still above threshold)
    await probe_db.execute(
        "UPDATE probe_agents SET created_at = now() - interval '30 hours', "
        "expires_at = now() + interval '6 hours' WHERE did = $1",
        did,
    )
    assert await maybe_extend_probe_ttl(probe_db, did) is True
    row = await probe_db.fetchrow("SELECT ttl_extensions FROM probe_agents WHERE did = $1", did)
    assert row["ttl_extensions"] == 2

    # Third attempt — refused because ttl_extensions already at max
    await probe_db.execute(
        "UPDATE probe_agents SET created_at = now() - interval '40 hours', "
        "expires_at = now() + interval '8 hours' WHERE did = $1",
        did,
    )
    assert await maybe_extend_probe_ttl(probe_db, did) is False


def test_get_identity_missing_raises_500():
    req = mock_request()
    with pytest.raises(HTTPException) as exc:
        get_identity(req)
    assert exc.value.status_code == 500


def test_require_claimed_rejects_probe():
    probe = Identity(kind="probe", did="did:moltrust:probe:abc12345", api_key="mt_probe_xxx")
    req = mock_request(identity=probe)
    with pytest.raises(HTTPException) as exc:
        require_claimed(req)
    assert exc.value.status_code == 401
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert "claim_url" in detail
    assert "claim_curl" in detail
    assert detail["probe_did"] == probe.did


def test_require_claimed_rejects_fresh_probe():
    probe = Identity(kind="probe-new", did="did:moltrust:probe:abc12345", probe_key="mt_probe_xxx")
    req = mock_request(identity=probe)
    with pytest.raises(HTTPException) as exc:
        require_claimed(req)
    assert exc.value.status_code == 401


def test_require_claimed_accepts_claimed():
    claimed = Identity(kind="claimed", did="did:moltrust:0123456789abcdef", api_key="mt_xyz")
    req = mock_request(identity=claimed)
    result = require_claimed(req)
    assert result is claimed


def test_require_probe_accepts_both():
    probe = Identity(kind="probe", did="did:moltrust:probe:00112233")
    claimed = Identity(kind="claimed", did="did:moltrust:0123456789abcdef")
    assert require_probe(mock_request(identity=probe)) is probe
    assert require_probe(mock_request(identity=claimed)) is claimed


def test_hash_session_handles_none():
    assert hash_session(None) is None
    assert hash_session("") is None
    h = hash_session("abc")
    assert isinstance(h, str) and len(h) == 64


def test_env_api_keys_splits_and_strips():
    os.environ["MOLTRUST_API_KEYS"] = " a , b,  ,c "
    assert env_api_keys() == {"a", "b", "c"}
