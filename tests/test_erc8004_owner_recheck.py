"""ERC-8004 links re-checked against ownerOf (BIND-CHECK 2).

Red: a token transferred after linking (mocked ownerOf) is marked
owner_changed, the DID document stops asserting the link, the reverse lookup
says not confirmed, and the watchdog counts it. Green: same owner, the link
stays and the document carries alsoKnownAs.

Needs the API rig in tests/conftest.py (sandbox database). No chain call:
ownerOf is injected.
"""
import uuid

import pytest

from app import erc8004_binding as eb

KMS = "0x8e4cd1e31b4d4114c811e41bd4a01002baec5ee6"
WALLET = "0x" + "ab" * 20
STRANGER = "0x" + "cd" * 20


@pytest.fixture(autouse=True)
def _no_rate_limit(app_with_lifespan):
    from app.main import limiter
    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


@pytest.fixture
async def linked(app_with_lifespan):
    """Factory: linked(agent_id, wallet=None, record=(owner, origin) | None) -> did."""
    from app.main import db_pool
    made = []

    async def _make(agent_id, wallet=None, record=None):
        did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO agents (did, display_name, platform, agent_type, erc8004_agent_id, "
                "wallet_address) VALUES ($1, 'tc-erc', 'test', 'external', $2, $3)",
                did, agent_id, wallet)
            await eb.ensure_table(conn)
            await conn.execute("DELETE FROM erc8004_link_owner WHERE agent_id = $1", agent_id)
            if record:
                await eb.record_link(conn, agent_id, did, record[0], record[1])
        made.append((did, agent_id))
        return did

    yield _make
    async with db_pool.acquire() as conn:
        for did, agent_id in made:
            await conn.execute("DELETE FROM erc8004_link_owner WHERE agent_id = $1", agent_id)
            await conn.execute("DELETE FROM agents WHERE did = $1", did)


async def _recheck(owners: dict):
    from app.main import db_pool

    def read(agent_id):
        v = owners.get(agent_id)
        if isinstance(v, type) and issubclass(v, Exception):
            raise v("mock")
        return v

    async with db_pool.acquire() as conn:
        # Only our test links: other rows in the sandbox are not ours to judge.
        r = await eb.recheck(conn, read_owner=lambda a: read(a) if a in owners else KMS)
    return r


def _base_id():
    return 900_000_000 + uuid.uuid4().int % 90_000_000


async def test_transferred_token_is_marked_and_resolution_shows_it(async_client, linked):
    aid = _base_id()
    did = await linked(aid, wallet=WALLET, record=(WALLET, eb.ORIGIN_AUTOLINK))
    r = await _recheck({aid: STRANGER})
    assert (aid, eb.OWNER_CHANGED) in r["deviating"]

    doc = (await async_client.get(f"/identity/resolve/{did}")).json()
    assert "alsoKnownAs" not in doc
    assert doc["metadata"]["erc8004Link"]["confirmed"] is False
    assert doc["metadata"]["erc8004Link"]["status"] == eb.OWNER_CHANGED

    reg = (await async_client.get(f"/agents/{did}/erc8004")).json()
    assert reg["registrations"] == [] and reg["moltrust_link"]["confirmed"] is False


async def test_same_owner_keeps_the_link(async_client, linked):
    aid = _base_id()
    did = await linked(aid, wallet=WALLET, record=(KMS, eb.ORIGIN_MINTED))
    r = await _recheck({aid: KMS})
    assert aid not in [a for a, _ in r["deviating"]]
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        assert (await eb.link_state(conn, did))["status"] == eb.CONFIRMED
    doc = (await async_client.get(f"/identity/resolve/{did}")).json()
    assert doc["alsoKnownAs"] == [f"eip155:8453:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:{aid}"]
    assert "erc8004Link" not in doc["metadata"]


async def test_baseline_for_links_older_than_the_table(linked):
    """A link without a recorded owner takes the current owner as baseline only
    when it is the registry signer or the DID's wallet; otherwise unrecorded."""
    a_wallet, a_kms, a_other = _base_id(), _base_id(), _base_id()
    await linked(a_wallet, wallet=WALLET)
    await linked(a_kms)
    await linked(a_other, wallet=WALLET)
    r = await _recheck({a_wallet: WALLET, a_kms: KMS, a_other: STRANGER})
    dev = dict(r["deviating"])
    assert a_wallet not in dev and a_kms not in dev
    assert dev[a_other] == eb.UNRECORDED
    # Second run: the baseline holds; a later transfer is a change, not a new baseline.
    r = await _recheck({a_wallet: STRANGER, a_kms: KMS, a_other: STRANGER})
    dev = dict(r["deviating"])
    assert dev[a_wallet] == eb.OWNER_CHANGED and a_kms not in dev


async def test_unreadable_owner_changes_nothing(linked):
    aid = _base_id()
    await linked(aid, record=(KMS, eb.ORIGIN_MINTED))
    r = await _recheck({aid: eb.OwnerUnreadable})
    assert r["unreadable"] >= 1 and aid not in [a for a, _ in r["deviating"]]
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        assert (await eb.link_state_by_agent_id(conn, aid))["status"] == eb.CONFIRMED


async def test_absent_token_is_a_deviation(linked):
    aid = _base_id()
    await linked(aid, record=(KMS, eb.ORIGIN_MINTED))
    r = await _recheck({aid: eb.TokenAbsent})
    assert dict(r["deviating"])[aid] == eb.TOKEN_ABSENT


async def test_reverse_lookup_names_the_link_state(async_client, linked, monkeypatch):
    aid = _base_id()
    await linked(aid, record=(WALLET, eb.ORIGIN_AUTOLINK))
    await _recheck({aid: STRANGER})
    import app.main as m

    async def fake_resolve(agent_id):
        return {"agent_id": agent_id, "owner": STRANGER, "agent_wallet": "0x" + "0" * 40, "agent_uri": ""}
    monkeypatch.setattr(m, "resolve_onchain_agent", fake_resolve)
    monkeypatch.setattr(m, "get_onchain_reputation", lambda a: {"agent_id": a, "count": 0})
    body = (await async_client.get(f"/resolve/erc8004/{aid}")).json()
    assert body["moltrust_link"]["confirmed"] is False
    assert body["moltrust_link"]["status"] == eb.OWNER_CHANGED


def test_watchdog_counts_a_deviation():
    from agents import watchdog

    async def red():
        return {"linked": 3, "checked": 3, "confirmed": 2, "deviations": 1, "unreadable": 0,
                "by_status": {}, "deviating": [(7, eb.OWNER_CHANGED)]}

    async def green():
        return {"linked": 3, "checked": 3, "confirmed": 3, "deviations": 0, "unreadable": 0,
                "by_status": {}, "deviating": []}

    async def blind():
        return {"linked": 3, "checked": 0, "confirmed": 0, "deviations": 0, "unreadable": 3,
                "by_status": {}, "deviating": []}

    r = watchdog.check_erc8004_owners(recheck=red)
    assert r["ok"] is False and r["deviations"] == 1 and "1 Abweichungen" in r["detail"]
    g = watchdog.check_erc8004_owners(recheck=green)
    assert g["ok"] is True and "0 Abweichungen" in g["detail"] and not g.get("unverifiable")
    assert watchdog.check_erc8004_owners(recheck=blind).get("unverifiable") is True


def test_owner_slot_every_six_hours():
    import datetime
    from agents import watchdog
    hours = [h for h in range(24)
             if watchdog._is_erc8004_owner_slot(datetime.datetime(2026, 11, 17, h, tzinfo=datetime.UTC))]
    assert hours == [0, 6, 12, 18]
