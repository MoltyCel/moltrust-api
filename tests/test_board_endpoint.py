"""Tests — POST /board und GET /board (ops G5).

The rules themselves are in `tests/test_board.py` and need nothing; this
module is about the two things only the endpoint can be wrong about: who an
entry may be filed under, and what a reader sees.

The first of those is the one the job names: an entry with somebody else's DID
is refused. Without it the board's only useful property is gone — that the
name on an entry is the name that will answer — and nothing else in the system
would notice, because a parked entry is a well-formed row.

Every assertion is scoped to the DIDs this module creates. `GET /board` serves
the whole board, and a suite that asserts on its length fails the day another
test leaves an entry behind.
"""
import datetime

import pytest
import pytest_asyncio

from app import board


@pytest_asyncio.fixture(autouse=True)
async def clean_entries(app_with_lifespan):
    """Remove the rows this module writes, before and after."""
    from app.main import db_pool

    dids: list[str] = []

    async def _drop():
        if not dids:
            return
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM board_entries WHERE did = ANY($1::text[])", dids)

    yield dids
    await _drop()


@pytest.fixture(autouse=True)
def no_rate_limit():
    """Off by default, like the contact suite.

    Under ASGITransport every request carries the same client host, and the
    board bucket is keyed on the API key — but a test that posts twice with
    one key would still spend two of five. The limit has its own test, which
    switches this back on.
    """
    from app.main import limiter

    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


def _payload(did, **over):
    body = {"did": did, "offers": ["endorsement"], "seeks": ["data"],
            "note": "Runs a lookup service, happy to vouch for a peer."}
    body.update(over)
    return body


async def test_an_agent_posts_its_own_entry(async_client, credit_test_agent, clean_entries):
    did, key = await credit_test_agent()
    clean_entries.append(did)

    r = await async_client.post("/board", json=_payload(did), headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "posted"
    assert out["did"] == did
    assert out["offers"] == ["endorsement"] and out["seeks"] == ["data"]
    assert out["ttl_hours"] == board.TTL_HOURS
    # 72 hours, within a minute of now — the span, not the wall clock.
    posted = datetime.datetime.fromisoformat(out["posted_at"])
    expires = datetime.datetime.fromisoformat(out["expires_at"])
    assert abs((expires - posted).total_seconds() - board.TTL_HOURS * 3600) < 60


async def test_an_entry_with_a_foreign_did_is_refused(async_client, credit_test_agent,
                                                      clean_entries):
    """The rule the job names. Both agents are real and only the key differs."""
    mine, my_key = await credit_test_agent()
    theirs, _ = await credit_test_agent()
    clean_entries.extend([mine, theirs])

    r = await async_client.post("/board", json=_payload(theirs),
                                headers={"X-API-Key": my_key})
    assert r.status_code == 403
    assert "does not match" in r.json()["detail"]

    # And nothing was written under either name. A refusal that still stores
    # the row would be the same defect with a nicer status code.
    read = (await async_client.get("/board")).json()
    assert [e for e in read["entries"] if e["did"] in (mine, theirs)] == []


async def test_the_same_agent_posting_again_replaces_its_entry(async_client,
                                                               credit_test_agent,
                                                               clean_entries):
    did, key = await credit_test_agent()
    clean_entries.append(did)

    first = await async_client.post("/board", json=_payload(did),
                                    headers={"X-API-Key": key})
    second = await async_client.post(
        "/board", json=_payload(did, offers=["compute"], seeks=["code"], note=""),
        headers={"X-API-Key": key})
    assert first.json()["status"] == "posted"
    assert second.json()["status"] == "replaced"
    assert second.json()["offers"] == ["compute"]
    # One voice per agent: the board shows the new entry and not both.
    mine = [e for e in (await async_client.get("/board")).json()["entries"]
            if e["did"] == did]
    assert len(mine) == 1
    assert mine[0]["offers"] == ["compute"]
    # The replacement refreshed the clock, which is what makes renewing the
    # entry the liveness signal it is meant to be.
    assert second.json()["expires_at"] > first.json()["expires_at"]


async def test_a_key_bound_to_no_did_cannot_post(async_client, app_with_lifespan):
    """Authenticated is not the same as owning a DID."""
    import uuid
    from app.main import API_KEYS

    loose = f"mt_tc_{uuid.uuid4().hex}"
    API_KEYS.add(loose)
    try:
        r = await async_client.post(
            "/board", json=_payload("did:moltrust:aaaaaaaaaaaaaaaa"),
            headers={"X-API-Key": loose})
        assert r.status_code == 403
        assert "not bound" in r.json()["detail"]
    finally:
        API_KEYS.discard(loose)


async def test_no_key_no_entry(async_client):
    r = await async_client.post("/board",
                                json=_payload("did:moltrust:aaaaaaaaaaaaaaaa"))
    assert r.status_code == 401


async def test_an_entry_that_neither_offers_nor_seeks_is_refused(async_client,
                                                                 credit_test_agent,
                                                                 clean_entries):
    did, key = await credit_test_agent()
    clean_entries.append(did)
    r = await async_client.post("/board", json=_payload(did, offers=[], seeks=[]),
                                headers={"X-API-Key": key})
    assert r.status_code == 422
    assert "at least one" in r.json()["detail"]


async def test_a_word_that_is_not_a_tag_is_refused_at_the_endpoint(async_client,
                                                                   credit_test_agent,
                                                                   clean_entries):
    did, key = await credit_test_agent()
    clean_entries.append(did)
    r = await async_client.post("/board", json=_payload(did, offers=["seo"]),
                                headers={"X-API-Key": key})
    assert r.status_code == 422
    # The allowed set travels with the refusal, so the next attempt can be right.
    assert "endorsement" in r.json()["detail"]


async def test_a_note_longer_than_the_limit_is_refused(async_client, credit_test_agent,
                                                       clean_entries):
    did, key = await credit_test_agent()
    clean_entries.append(did)
    r = await async_client.post(
        "/board", json=_payload(did, note="x" * (board.NOTE_MAX + 1)),
        headers={"X-API-Key": key})
    assert r.status_code == 422


async def test_the_board_reads_without_a_key(async_client, credit_test_agent,
                                             clean_entries):
    did, key = await credit_test_agent()
    clean_entries.append(did)
    await async_client.post("/board", json=_payload(did), headers={"X-API-Key": key})

    r = await async_client.get("/board")
    assert r.status_code == 200
    out = r.json()
    assert out["order"] == "expires_at desc"
    assert out["tags"] == list(board.TAGS)
    assert out["count"] == len(out["entries"])
    mine = [e for e in out["entries"] if e["did"] == did]
    assert len(mine) == 1
    assert mine[0]["note"].startswith("Runs a lookup service")


async def test_an_expired_entry_is_absent(async_client, credit_test_agent,
                                          clean_entries):
    """Absent, not flagged: a reader that has to filter will one day forget."""
    from app.main import db_pool

    did, _ = await credit_test_agent()
    clean_entries.append(did)
    now = datetime.datetime.now(datetime.timezone.utc)
    async with db_pool.acquire() as conn:
        await board.put(conn, did, ["data"], [], "", now - datetime.timedelta(hours=80))
        rows = await board.active(conn, now)
    assert [r for r in rows if r["did"] == did] == []

    read = (await async_client.get("/board")).json()
    assert [e for e in read["entries"] if e["did"] == did] == []


async def test_the_newest_entry_comes_first(async_client, credit_test_agent,
                                            clean_entries):
    from app.main import db_pool

    older, _ = await credit_test_agent()
    newer, _ = await credit_test_agent()
    clean_entries.extend([older, newer])
    now = datetime.datetime.now(datetime.timezone.utc)
    async with db_pool.acquire() as conn:
        await board.put(conn, older, ["data"], [], "", now - datetime.timedelta(hours=5))
        await board.put(conn, newer, ["code"], [], "", now)

    order = [e["did"] for e in (await async_client.get("/board")).json()["entries"]
             if e["did"] in (older, newer)]
    assert order == [newer, older]


async def test_the_sixth_entry_in_an_hour_is_refused(async_client, credit_test_agent,
                                                     clean_entries):
    """Five an hour, from the job. Re-enables the limiter this module disables."""
    from app.main import limiter

    did, key = await credit_test_agent()
    clean_entries.append(did)
    limiter.enabled = True
    try:
        codes = []
        for _ in range(6):
            r = await async_client.post("/board", json=_payload(did),
                                        headers={"X-API-Key": key})
            codes.append(r.status_code)
    finally:
        limiter.enabled = False
    assert codes[:5] == [200] * 5
    assert codes[5] == 429


async def test_one_agents_budget_is_not_anothers(async_client, credit_test_agent,
                                                 clean_entries):
    """The silence probe for the limit: it is per key, so a neighbour is unaffected.

    Keyed on the address instead, two agents behind one hoster would spend
    each other's five.
    """
    from app.main import limiter

    first, first_key = await credit_test_agent()
    second, second_key = await credit_test_agent()
    clean_entries.extend([first, second])
    limiter.enabled = True
    try:
        for _ in range(5):
            await async_client.post("/board", json=_payload(first),
                                    headers={"X-API-Key": first_key})
        blocked = await async_client.post("/board", json=_payload(first),
                                          headers={"X-API-Key": first_key})
        neighbour = await async_client.post("/board", json=_payload(second),
                                            headers={"X-API-Key": second_key})
    finally:
        limiter.enabled = False
    assert blocked.status_code == 429
    assert neighbour.status_code == 200


async def test_the_sweep_removes_what_the_read_already_hides(app_with_lifespan,
                                                             credit_test_agent,
                                                             clean_entries):
    from app.main import db_pool

    did, _ = await credit_test_agent()
    clean_entries.append(did)
    now = datetime.datetime.now(datetime.timezone.utc)
    async with db_pool.acquire() as conn:
        await board.put(conn, did, ["data"], [], "", now - datetime.timedelta(hours=80))
        removed = await board.sweep(conn, now)
        left = await conn.fetchval("SELECT count(*) FROM board_entries WHERE did = $1", did)
    assert removed >= 1
    assert left == 0
