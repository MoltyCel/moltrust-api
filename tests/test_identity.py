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
    ClaimError,
    Identity,
    PROBE_DID_PREFIX,
    PROBE_KEY_PREFIX,
    _mint_probe,
    claim_probe,
    detect_source,
    env_api_keys,
    get_identity,
    get_probe_summary,
    hash_key,
    hash_session,
    increment_probe_call_count,
    maybe_extend_probe_ttl,
    record_probe_activity,
    record_probe_spawn,
    require_claimed,
    require_probe,
    resolve_identity,
    vertical_from_path,
)


CLEAN_MARKER = "pytest-identity"
CLAIM_DISPLAY_PREFIX = "pytest-claim-"


async def _cleanup(conn):
    """Tear down rows created by these tests.

    agents.did is referenced by many tables; this clears FK-dependent rows
    in the right order. credit_transactions is append-only by trigger so it
    is intentionally NOT deleted — its rows reference random unique DIDs
    that no real flow will reuse.
    """
    claimed_agents = await conn.fetch(
        "SELECT did FROM agents WHERE display_name LIKE $1 OR parent_probe_did IN "
        "(SELECT did FROM probe_agents WHERE first_seen_ua = $2)",
        CLAIM_DISPLAY_PREFIX + "%", CLEAN_MARKER,
    )
    dids = [r["did"] for r in claimed_agents]
    if dids:
        # Tables that FK to agents.did — clear referencing rows before deleting the agents.
        fk_tables = [
            ("agent_delegation_config", "did"),
            ("agent_messages", "to_did"),
            ("api_keys", "owner_did"),
            ("credentials", "subject_did"),
            ("credit_balances", "did"),
            ("did_bridges", "moltrust_did"),
            ("signal_providers", "agent_did"),
            ("spiffe_bindings", "did"),
            ("sports_predictions", "agent_did"),
            ("usdc_deposits", "to_did"),
        ]
        for tbl, col in fk_tables:
            await conn.execute(f"DELETE FROM {tbl} WHERE {col} = ANY($1)", dids)
        await conn.execute("DELETE FROM agents WHERE did = ANY($1)", dids)
    await conn.execute("DELETE FROM probe_agents WHERE first_seen_ua = $1", CLEAN_MARKER)
    # Defensive: probes from this test machine accumulated outside the suite
    # (e.g. via manual `curl` integration testing against a running server)
    # don't carry CLEAN_MARKER on first_seen_ua, but they still count toward
    # the per-IP/per-/24 spawn-rate guards in app.identity._enforce_spawn_rate
    # because those guards key on IP only. Drop any recent localhost probes
    # so the fixture leaves a sub-cap row count regardless of what else used
    # the DB in the last hour. See tests/KNOWN_FAILURES.md for context.
    await conn.execute(
        "DELETE FROM probe_agents "
        "WHERE first_seen_ip << '127.0.0.0/24'::inet "
        "AND created_at > now() - interval '1 hour'"
    )


@pytest.fixture
async def probe_db():
    conn = await asyncpg.connect(
        host="localhost",
        database=os.getenv("DB_NAME", "moltstack"),
        user="moltstack",
    )
    await _cleanup(conn)
    try:
        yield conn
    finally:
        await _cleanup(conn)
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


async def test_session_id_reuses_probe(probe_db):
    """Two keyless requests with the same Mcp-Session-Id resolve to the same probe."""
    session_id = "test-mcp-session-abc123"
    req1 = mock_request(headers={"Mcp-Session-Id": session_id, "User-Agent": CLEAN_MARKER})
    id1 = await resolve_identity(req1, probe_db)
    assert id1.kind == "probe-new"

    req2 = mock_request(headers={"Mcp-Session-Id": session_id, "User-Agent": CLEAN_MARKER})
    id2 = await resolve_identity(req2, probe_db)
    assert id2.kind == "probe"
    assert id2.did == id1.did
    # Verify only one row was minted
    count = await probe_db.fetchval(
        "SELECT COUNT(*) FROM probe_agents WHERE first_seen_ua = $1", CLEAN_MARKER
    )
    assert count == 1


async def test_session_id_no_reuse_after_expiry(probe_db):
    """Expired session probes don't get reused — a new probe is minted."""
    session_id = "test-mcp-session-expiring"
    req1 = mock_request(headers={"Mcp-Session-Id": session_id, "User-Agent": CLEAN_MARKER})
    id1 = await resolve_identity(req1, probe_db)
    await probe_db.execute(
        "UPDATE probe_agents SET expires_at = now() - interval '1 minute' WHERE did = $1", id1.did
    )
    req2 = mock_request(headers={"Mcp-Session-Id": session_id, "User-Agent": CLEAN_MARKER})
    id2 = await resolve_identity(req2, probe_db)
    assert id2.kind == "probe-new"
    assert id2.did != id1.did


async def test_x_real_ip_precedence(probe_db):
    # H6 from the AI security review: X-Real-IP must take precedence over
    # X-Forwarded-For because nginx sets X-Real-IP to $remote_addr (the
    # connecting client's IP, never client-supplied), while X-Forwarded-For
    # has the client's prepended value at [0]. The pre-fix code returned
    # XFF[0] = "10.0.0.1" — attacker-controlled. Post-fix the resolver
    # prefers X-Real-IP, falling back to XFF[-1] (the nginx-appended hop).
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
    assert str(row["first_seen_ip"]) == "8.8.8.8"  # X-Real-IP wins; XFF[0] is attacker-controlled


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


async def test_spawn_rate_limit_per_ip(probe_db):
    """6th probe from the same IP within an hour is rejected with 429."""
    ip = "192.0.2.42"  # documentation IP, safe for tests
    for i in range(5):
        await _mint_probe(probe_db, ip=ip, ua=CLEAN_MARKER, smithery_session_hash=None)
    with pytest.raises(AuthError) as exc:
        await _mint_probe(probe_db, ip=ip, ua=CLEAN_MARKER, smithery_session_hash=None)
    assert exc.value.status == 429
    assert "per IP" in exc.value.message


async def test_spawn_rate_limit_per_subnet(probe_db):
    """21st probe from a single IPv4 /24 within an hour is rejected with 429."""
    base = "198.51.100."  # documentation /24
    # 20 mints across the subnet should succeed
    for i in range(1, 21):
        await _mint_probe(probe_db, ip=f"{base}{i}", ua=CLEAN_MARKER, smithery_session_hash=None)
    with pytest.raises(AuthError) as exc:
        await _mint_probe(probe_db, ip=f"{base}99", ua=CLEAN_MARKER, smithery_session_hash=None)
    assert exc.value.status == 429
    assert "subnet" in exc.value.message


async def test_spawn_rate_limit_no_ip_passes(probe_db):
    """Requests with no resolvable IP skip the rate gate (legitimate localhost)."""
    for _ in range(6):
        await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    # No exception — rate gate is IP-aware only.


async def test_claim_with_valid_probe_email(probe_db):
    did, key, _ = await _mint_probe(probe_db, ip="127.0.0.1", ua=CLEAN_MARKER, smithery_session_hash=None)
    result = await claim_probe(
        probe_db,
        probe_key=key,
        email="claim-test@example.test",
        display_name=f"{CLAIM_DISPLAY_PREFIX}email",
        ip="127.0.0.1",
    )
    assert result["status"] == "claimed"
    assert result["did"].startswith("did:moltrust:")
    assert result["api_key"].startswith("mt_")
    assert result["tier"] == "standard"
    assert result["claimed_from_probe"] == did

    # probe_agents row marked claimed
    row = await probe_db.fetchrow(
        "SELECT claimed_at, claimed_did, claimed_email_hash FROM probe_agents WHERE did = $1", did,
    )
    assert row["claimed_at"] is not None
    assert row["claimed_did"] == result["did"]
    assert row["claimed_email_hash"] is not None

    # agents row inserted with parent_probe_did
    agent = await probe_db.fetchrow(
        "SELECT parent_probe_did, platform, agent_type FROM agents WHERE did = $1", result["did"],
    )
    assert agent["parent_probe_did"] == did

    # api_keys row inserted, tier=standard
    key_row = await probe_db.fetchrow(
        "SELECT email, owner_did, tier FROM api_keys WHERE key = $1", result["api_key"],
    )
    assert key_row["email"] == "claim-test@example.test"
    assert key_row["tier"] == "standard"
    assert key_row["owner_did"] == result["did"]

    # conversion_funnel entry exists
    funnel = await probe_db.fetchrow(
        "SELECT claim_state, claimed_at FROM conversion_funnel WHERE probe_did = $1", did,
    )
    assert funnel["claim_state"] == "claimed"


async def test_claim_anonymous(probe_db):
    did, key, _ = await _mint_probe(probe_db, ip="10.0.0.1", ua=CLEAN_MARKER, smithery_session_hash=None)
    result = await claim_probe(
        probe_db,
        probe_key=key,
        email=None,
        display_name=f"{CLAIM_DISPLAY_PREFIX}anon",
        ip="10.0.0.1",
    )
    assert result["status"] == "claimed"
    assert result["tier"] == "anonymous_claimed"

    key_row = await probe_db.fetchrow(
        "SELECT email, tier FROM api_keys WHERE key = $1", result["api_key"]
    )
    assert key_row["tier"] == "anonymous_claimed"
    assert "anonymous+" in key_row["email"]

    funnel = await probe_db.fetchval(
        "SELECT claim_state FROM conversion_funnel WHERE probe_did = $1", did
    )
    assert funnel == "anonymous-claimed"


async def test_claim_idempotent_on_email_collision(probe_db):
    # First claim
    _, key1, _ = await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    first = await claim_probe(
        probe_db, probe_key=key1, email="dup@example.test",
        display_name=f"{CLAIM_DISPLAY_PREFIX}first", ip=None,
    )
    # Second claim with same email — should return existing identity
    _, key2, _ = await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    second = await claim_probe(
        probe_db, probe_key=key2, email="DUP@example.TEST",  # case-insensitive
        display_name=f"{CLAIM_DISPLAY_PREFIX}second", ip=None,
    )
    assert second["status"] == "existing_identity_returned"
    assert second["did"] == first["did"]
    assert second["api_key"] == first["api_key"]


async def test_claim_invalid_probe(probe_db):
    with pytest.raises(ClaimError) as exc:
        await claim_probe(probe_db, probe_key="mt_probe_doesnotexist", email=None, display_name=None, ip=None)
    assert exc.value.status == 401


async def test_claim_already_claimed(probe_db):
    _, key, _ = await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    await claim_probe(probe_db, probe_key=key, email=None, display_name=f"{CLAIM_DISPLAY_PREFIX}first", ip=None)
    with pytest.raises(ClaimError) as exc:
        await claim_probe(probe_db, probe_key=key, email=None, display_name=f"{CLAIM_DISPLAY_PREFIX}retry", ip=None)
    assert exc.value.status == 410


async def test_claim_expired_within_grace_succeeds(probe_db):
    did, key, _ = await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    # Expired 3 days ago — within 7-day grace window
    await probe_db.execute(
        "UPDATE probe_agents SET expires_at = now() - interval '3 days' WHERE did = $1", did,
    )
    result = await claim_probe(
        probe_db, probe_key=key, email=None,
        display_name=f"{CLAIM_DISPLAY_PREFIX}grace", ip=None,
    )
    assert result["status"] == "claimed"


async def test_claim_expired_past_grace_rejected(probe_db):
    did, key, _ = await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    await probe_db.execute(
        "UPDATE probe_agents SET expires_at = now() - interval '14 days' WHERE did = $1", did,
    )
    with pytest.raises(ClaimError) as exc:
        await claim_probe(probe_db, probe_key=key, email=None, display_name=f"{CLAIM_DISPLAY_PREFIX}stale", ip=None)
    assert exc.value.status == 410


async def test_claim_invalid_email_format(probe_db):
    _, key, _ = await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    with pytest.raises(ClaimError) as exc:
        await claim_probe(probe_db, probe_key=key, email="not-an-email", display_name=None, ip=None)
    assert exc.value.status == 400


def test_detect_source_smithery_session_id():
    assert detect_source(None, "session-abc") == "smithery"


def test_detect_source_smithery_user_agent():
    assert detect_source("Smithery-Proxy/1.0", None) == "smithery"
    assert detect_source("run.tools-cli/0.5", None) == "smithery"


def test_detect_source_direct_default():
    assert detect_source("curl/8.0", None) == "direct"
    assert detect_source(None, None) == "direct"


def test_vertical_from_path():
    assert vertical_from_path("/credits/balance/did:moltrust:abc") == "moltrust"
    assert vertical_from_path("/guard/api/agent/score/0x123") == "moltguard"
    assert vertical_from_path("/skill/audit") == "skill"
    assert vertical_from_path("/shopping/verify") == "shopping"
    assert vertical_from_path("/travel/info") == "travel"
    assert vertical_from_path("/salesguard/verify") == "salesguard"
    assert vertical_from_path("/swarm/graph/did:abc") == "swarm"
    assert vertical_from_path("/endorse/foo") == "swarm"
    assert vertical_from_path("/identity/register") == "moltrust"
    assert vertical_from_path("/stats") == "moltrust"
    assert vertical_from_path("/random-path") is None


async def test_record_probe_spawn_and_activity(probe_db):
    did, _, _ = await _mint_probe(probe_db, ip="1.2.3.4", ua=CLEAN_MARKER, smithery_session_hash=None)
    await record_probe_spawn(probe_db, probe_did=did, source="smithery", first_path="/stats")

    funnel = await probe_db.fetchrow(
        "SELECT source, first_tool, tool_count FROM conversion_funnel WHERE probe_did = $1", did
    )
    assert funnel["source"] == "smithery"
    assert funnel["first_tool"] == "/stats"
    assert funnel["tool_count"] == 0

    # Idempotent: second spawn-record is a no-op
    await record_probe_spawn(probe_db, probe_did=did, source="direct", first_path="/other")
    funnel2 = await probe_db.fetchrow(
        "SELECT source, first_tool FROM conversion_funnel WHERE probe_did = $1", did
    )
    assert funnel2["source"] == "smithery"  # not overwritten

    # Each activity bumps tool_count and appends to probe_activity
    await record_probe_activity(probe_db, probe_did=did, path="/stats")
    await record_probe_activity(probe_db, probe_did=did, path="/credits/balance/did:test")
    await record_probe_activity(probe_db, probe_did=did, path="/guard/api/market/feed")

    funnel3 = await probe_db.fetchval(
        "SELECT tool_count FROM conversion_funnel WHERE probe_did = $1", did
    )
    assert funnel3 == 3

    activity_count = await probe_db.fetchval(
        "SELECT COUNT(*) FROM probe_activity WHERE probe_did = $1", did
    )
    assert activity_count == 3


async def test_get_probe_summary_aggregates(probe_db):
    did, _, _ = await _mint_probe(probe_db, ip=None, ua=CLEAN_MARKER, smithery_session_hash=None)
    await record_probe_spawn(probe_db, probe_did=did, source="direct", first_path="/stats")
    # Three calls across two verticals (moltrust + moltguard); 2 unique tool names
    await record_probe_activity(probe_db, probe_did=did, path="/stats")
    await record_probe_activity(probe_db, probe_did=did, path="/stats")
    await record_probe_activity(probe_db, probe_did=did, path="/guard/api/market/feed")

    summary = await get_probe_summary(probe_db, did)
    assert summary["tool_calls"] == 3
    assert summary["unique_tools"] == 2
    assert summary["verticals_touched"] == 2


def test_hash_session_handles_none():
    assert hash_session(None) is None
    assert hash_session("") is None
    h = hash_session("abc")
    assert isinstance(h, str) and len(h) == 64


def test_env_api_keys_splits_and_strips():
    os.environ["MOLTRUST_API_KEYS"] = " a , b,  ,c "
    assert env_api_keys() == {"a", "b", "c"}
