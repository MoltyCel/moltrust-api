"""A badge answers REVOKED at once after a revocation, and no cache can hold a grade.

#882 made every badge route read `agents.revoked_at` itself instead of trusting
a score. What it left open was the HTTP layer: both SVG badges were sent with
`Cache-Control: max-age=3600, s-maxage=3600`, so a browser, an image proxy
(GitHub camo, a mail client) or any shared cache could keep showing
"85 / A" for up to an hour after the DID was revoked, without asking the API.

Now the three badge URLs send `Cache-Control: public, no-cache` and an ETag of
the body. A cache may keep a copy, but must ask before every use; the ETag
changes when the revocation state changes, so the answer to that question is a
200 with REVOKED, never a 304 for the old grade.

Each test asks the badge for a live DID with a grade, revokes it, and asks again
straight away: once through POST /identity/revoke, once through the bare row
update ops makes by hand (which skips the route and its trust_score_cache
delete). Invariant: docs/invariants/c-no-endpoint-affirms-a-revoked-did.yaml.

Needs the API rig in tests/conftest.py (sandbox database).
"""
import pytest

SEED_SCORE = 85.0
SVG_PATHS = ["/badge/{did}", "/identity/badge/{did}.svg"]
JSON_PATH = "/identity/badge/{did}"
ALL_PATHS = SVG_PATHS + [JSON_PATH]


@pytest.fixture(autouse=True)
def _no_rate_limit(app_with_lifespan):
    from app.main import limiter
    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


@pytest.fixture
async def graded_agent(credit_test_agent):
    """(did, api_key) for a live test agent whose badge shows 85 / A."""
    from app.main import db_pool
    made: list[str] = []

    async def _make():
        did, api_key = await credit_test_agent(balance=100)
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO swarm_seeds (did, base_score) VALUES ($1, $2) "
                "ON CONFLICT (did) DO UPDATE SET base_score = EXCLUDED.base_score",
                did, SEED_SCORE,
            )
        made.append(did)
        return did, api_key

    yield _make

    async with db_pool.acquire() as conn:
        for did in made:
            await conn.execute("DELETE FROM trust_score_cache WHERE did = $1", did)
            await conn.execute("DELETE FROM swarm_seeds WHERE did = $1", did)
            await conn.execute("DELETE FROM caep_events WHERE did = $1", did)


def _assert_graded(path, r):
    assert r.status_code == 200, (path, r.text)
    if path == JSON_PATH:
        body = r.json()
        assert body["verified"] is True
        assert body["grade"] == "A"
        assert body["revoked"] is None
    else:
        assert "85 / A" in r.text
        assert "REVOKED" not in r.text


def _assert_revoked(path, r):
    assert r.status_code == 200, (path, r.status_code, r.text)
    if path == JSON_PATH:
        body = r.json()
        assert body["verified"] is False
        assert body["grade"] == "REVOKED"
        assert body["trust_score"] is None
        assert body["revoked"] and body["revoked"]["at"]
    else:
        assert "REVOKED" in r.text
        assert " / " not in r.text, "no score / grade on a revoked badge"


def _assert_no_stale_reuse(path, r):
    """The response may be stored, but never reused without asking first."""
    cc = r.headers.get("cache-control", "")
    directives = {d.strip().split("=")[0].lower() for d in cc.split(",") if d.strip()}
    assert "no-cache" in directives, (path, cc)
    assert "s-maxage" not in directives, (path, cc)
    assert "stale-while-revalidate" not in directives, (path, cc)
    assert "stale-if-error" not in directives, (path, cc)
    assert not any(d.startswith("immutable") for d in directives), (path, cc)
    assert r.headers.get("etag"), (path, "an ETag is what makes no-cache cheap")


async def _fetch_all(client, did, etags=None):
    out = {}
    for path in ALL_PATHS:
        headers = {"If-None-Match": etags[path]} if etags else {}
        out[path] = await client.get(path.format(did=did), headers=headers)
    return out


async def _revoke_by_route(client, did, api_key):
    r = await client.post("/identity/revoke", json={"did": did, "reason": "test: badge cache"},
                          headers={"X-API-Key": api_key})
    assert r.status_code == 200, r.text


async def _revoke_by_sql(client, did, api_key):
    # The update ops makes by hand: no route, no trust_score_cache delete.
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE agents SET revoked_at = now(), revocation_reason = $1 WHERE did = $2",
            "test: badge cache", did,
        )


@pytest.mark.parametrize("revoke", [_revoke_by_route, _revoke_by_sql], ids=["route", "sql"])
async def test_badge_is_revoked_immediately_after_revocation(async_client, graded_agent, revoke):
    from app.main import db_pool
    did, api_key = await graded_agent()

    before = await _fetch_all(async_client, did)
    for path, r in before.items():
        _assert_graded(path, r)
        _assert_no_stale_reuse(path, r)
    # The first fetch computed and stored the score: a manual revoke leaves
    # that trust_score_cache row behind, and the badge must not read it.
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM trust_score_cache WHERE did = $1", did) == 1

    await revoke(async_client, did, api_key)

    # No wait. A plain fetch, and the conditional fetch a cache holding the
    # graded copy would make: both must answer 200 REVOKED, never 304.
    after = await _fetch_all(async_client, did)
    revalidated = await _fetch_all(async_client, did,
                                   etags={p: r.headers.get("etag", '"none"') for p, r in before.items()})
    for path in ALL_PATHS:
        _assert_revoked(path, after[path])
        _assert_no_stale_reuse(path, after[path])
        _assert_revoked(path, revalidated[path])
        assert after[path].headers["etag"] != before[path].headers["etag"], path


@pytest.mark.parametrize("path", ALL_PATHS)
async def test_unchanged_badge_revalidates_with_304(async_client, graded_agent, path):
    did, _ = await graded_agent()
    first = await async_client.get(path.format(did=did))
    _assert_graded(path, first)
    etag = first.headers["etag"]
    again = await async_client.get(path.format(did=did), headers={"If-None-Match": etag})
    assert again.status_code == 304, (path, again.status_code)
    assert again.content == b""
    assert again.headers["etag"] == etag
    assert "no-cache" in again.headers["cache-control"]
    # A weak validator from an intermediary still matches (RFC 9110 13.1.2).
    weak = await async_client.get(path.format(did=did), headers={"If-None-Match": f"W/{etag}"})
    assert weak.status_code == 304


async def test_attestation_of_a_revoked_did_prints_no_grade(async_client, graded_agent):
    """/attestations/{id} read trust_score_cache alone; a manual revoke leaves
    that row behind, so it kept printing the cached grade for a revoked DID."""
    from app.main import db_pool
    did, api_key = await graded_agent()
    first = await async_client.get(JSON_PATH.format(did=did))  # fills trust_score_cache
    _assert_graded(JSON_PATH, first)
    att_id = did.split(":")[-1]

    live = await async_client.get(f"/attestations/{att_id}")
    assert live.status_code == 200, live.text
    assert live.json()["grade"] == "A"

    await _revoke_by_sql(async_client, did, api_key)
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM trust_score_cache WHERE did = $1", did) == 1

    r = await async_client.get(f"/attestations/{att_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grade"] == "REVOKED"
    assert body["value"] is None
    assert body["revoked"] and body["revoked"]["reason"] == "test: badge cache"
