"""Money path — the USDC poller.

Two things are under test, matching the two ways this broke:

  1. A simulated deposit is recorded and credited correctly, against the real
     sandbox database.
  2. A simulated RPC failure is LOUD and does not move the cursor past blocks
     it never read. The silent variant — abort, leave the cursor frozen, log
     "Done. 0 new payment(s)" — ran unnoticed from 2026-05-14 to 2026-09-03.

The chain is stubbed; everything below it is real.
"""
import importlib
import json
import types
import uuid

import pytest


@pytest.fixture
def poller(monkeypatch, tmp_path):
    """Import the poller with an isolated state file and a stubbed chain."""
    # import_module, not `import monitor.poll_payments as pp`: the latter binds
    # the `monitor` package attribute, and suites that monkeypatch
    # sys.modules["monitor.poll_payments"] leave that attribute pointing at a
    # module sys.modules no longer holds — reload() then raises
    # "module monitor.poll_payments not in sys.modules".
    pp = importlib.import_module("monitor.poll_payments")
    importlib.reload(pp)
    # The receiving address is read from BASE_ADDR at the moment of use and
    # has no fallback (app/payment_address.py). A runner has no secrets file,
    # so the test names one; it is not a real wallet.
    monkeypatch.setenv("BASE_ADDR", "0x" + "11" * 20)
    # Same for the RPC endpoint (app/base_rpc.py, no fallback): main() names
    # the host in its log line. The chain itself is stubbed per test, so the
    # reserved .invalid name is never contacted.
    monkeypatch.setenv("BASE_RPC", "https://base-rpc.invalid/test")
    monkeypatch.setattr(pp, "STATE_FILE", tmp_path / "poll_state.json")
    sent: list[str] = []
    monkeypatch.setattr(pp, "send_telegram", lambda text, **kw: sent.append(text))
    pp._sent = sent
    return pp


async def _db():
    import asyncpg
    import os
    return await asyncpg.connect(
        host="localhost",
        database=os.getenv("DB_NAME", "moltstack_sandbox"),
        user="moltstack",
    )


def _chain(tip: int):
    """A w3 stand-in whose only answer is the chain tip. The poller reaches the
    chain through w3_client() since the client is built at first use, not at
    import, so that is what gets replaced."""
    class _Eth:
        block_number = tip
    return types.SimpleNamespace(eth=_Eth())


def _tx() -> str:
    return "0x" + uuid.uuid4().hex + uuid.uuid4().hex


def _wallet() -> str:
    return "0x" + uuid.uuid4().hex[:40]


# ---------------------------------------------------------------------------
# 1 — a simulated deposit is recorded and credited
# ---------------------------------------------------------------------------
async def test_bound_sender_is_recorded_and_credited(poller):
    did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    wallet = _wallet()
    tx = _tx()
    conn = await _db()
    try:
        await conn.execute(
            "INSERT INTO agents (did, display_name, platform, agent_type, wallet_address) "
            "VALUES ($1, $2, 'test', 'external', $3)",
            did, f"tc-poll-{did[-6:]}", wallet,
        )
        await conn.execute("INSERT INTO credit_balances (did, balance) VALUES ($1, 0)", did)

        ok = await poller.record_to_db(tx, wallet, 2.5, 45_000_000, 1_756_000_000)
        assert ok is True, "a bound sender must be recorded"

        dep = await conn.fetchrow(
            "SELECT to_did, credits_granted FROM usdc_deposits WHERE tx_hash = $1", tx)
        assert dep["to_did"] == did
        assert dep["credits_granted"] == 250          # 2.5 USDC x 100

        bal = await conn.fetchval("SELECT balance FROM credit_balances WHERE did = $1", did)
        assert bal == 250, f"credits not granted: {bal}"

        ev = await conn.fetchval("SELECT did FROM payment_events WHERE tx_hash = $1", tx)
        assert ev == did
    finally:
        await conn.execute("DELETE FROM graph_edges WHERE from_did = $1", did)
        await conn.execute("DELETE FROM usdc_deposits WHERE tx_hash = $1", tx)
        await conn.execute("DELETE FROM payment_events WHERE tx_hash = $1", tx)
        await conn.execute("DELETE FROM credit_balances WHERE did = $1", did)
        await conn.execute("DELETE FROM agents WHERE did = $1", did)
        await conn.close()


async def test_same_tx_is_not_credited_twice(poller):
    did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    wallet = _wallet()
    tx = _tx()
    conn = await _db()
    try:
        await conn.execute(
            "INSERT INTO agents (did, display_name, platform, agent_type, wallet_address) "
            "VALUES ($1, $2, 'test', 'external', $3)",
            did, f"tc-poll-{did[-6:]}", wallet,
        )
        await conn.execute("INSERT INTO credit_balances (did, balance) VALUES ($1, 0)", did)

        assert await poller.record_to_db(tx, wallet, 1.0, 45_000_000, 1_756_000_000) is True
        assert await poller.record_to_db(tx, wallet, 1.0, 45_000_000, 1_756_000_000) is False

        bal = await conn.fetchval("SELECT balance FROM credit_balances WHERE did = $1", did)
        assert bal == 100, f"credited twice: {bal}"
    finally:
        await conn.execute("DELETE FROM graph_edges WHERE from_did = $1", did)
        await conn.execute("DELETE FROM usdc_deposits WHERE tx_hash = $1", tx)
        await conn.execute("DELETE FROM payment_events WHERE tx_hash = $1", tx)
        await conn.execute("DELETE FROM credit_balances WHERE did = $1", did)
        await conn.execute("DELETE FROM agents WHERE did = $1", did)
        await conn.close()


async def test_unbound_sender_leaves_the_tx_claimable(poller):
    """No usdc_deposits row, so /credits/deposit can still honour the claim."""
    tx = _tx()
    wallet = _wallet()
    conn = await _db()
    try:
        ok = await poller.record_to_db(tx, wallet, 1.0, 45_000_000, 1_756_000_000)
        assert ok is True, "the payment event is still recorded"

        dep = await conn.fetchval("SELECT 1 FROM usdc_deposits WHERE tx_hash = $1", tx)
        assert dep is None, "an unbound sender must not consume the tx_hash"
    finally:
        await conn.execute("DELETE FROM payment_events WHERE tx_hash = $1", tx)
        await conn.execute("DELETE FROM usdc_deposits WHERE tx_hash = $1", tx)
        await conn.close()


# ---------------------------------------------------------------------------
# 2 — a simulated RPC failure is loud and does not fake progress
# ---------------------------------------------------------------------------
def test_rpc_failure_alerts_and_does_not_advance_the_cursor(poller, monkeypatch):
    start = 45_000_000
    poller.STATE_FILE.write_text(json.dumps({"last_block": start}))

    monkeypatch.setattr(poller, "w3_client", lambda: _chain(start + 500))

    def _boom(from_block, to_block):
        raise RuntimeError("eth_getLogs is limited to 0 - 50 blocks range")
    monkeypatch.setattr(poller, "get_usdc_transfers", _boom)

    rc = poller.main()

    assert rc == 1, "a failed poll must exit non-zero"
    assert poller._sent, "a failed poll must alert"
    assert "failed" in poller._sent[-1].lower()

    after = json.loads(poller.STATE_FILE.read_text())["last_block"]
    assert after == start, f"cursor moved over unread blocks: {start} -> {after}"


def test_failure_after_partial_progress_keeps_only_what_was_read(poller, monkeypatch):
    start = 45_000_000
    poller.STATE_FILE.write_text(json.dumps({"last_block": start}))
    # Several chunks behind, so two can succeed before the third fails. A
    # fixed +500 stopped meaning that when CHUNK_BLOCKS went to 2000: the
    # whole gap fitted into one chunk and the third call never came.
    tip = start + 5 * poller.CHUNK_BLOCKS
    monkeypatch.setattr(poller, "w3_client", lambda: _chain(tip))

    calls = {"n": 0}

    def _two_then_boom(from_block, to_block):
        calls["n"] += 1
        if calls["n"] <= 2:
            return []
        raise RuntimeError("transient RPC error")
    monkeypatch.setattr(poller, "get_usdc_transfers", _two_then_boom)

    rc = poller.main()

    assert rc == 1
    after = json.loads(poller.STATE_FILE.read_text())["last_block"]
    expected = start + 2 * poller.CHUNK_BLOCKS
    assert after == expected, f"expected cursor at {expected}, got {after}"
    assert after < tip, "cursor must not reach the tip after a failure"


def test_clean_run_is_not_reported_as_a_failure(poller, monkeypatch):
    start = 45_000_000
    poller.STATE_FILE.write_text(json.dumps({"last_block": start}))
    monkeypatch.setattr(poller, "w3_client", lambda: _chain(start + poller.CHUNK_BLOCKS))
    monkeypatch.setattr(poller, "get_usdc_transfers", lambda f, t: [])

    rc = poller.main()

    assert rc == 0
    assert not poller._sent, "a clean run must stay quiet"
    assert json.loads(poller.STATE_FILE.read_text())["last_block"] == start + poller.CHUNK_BLOCKS


def test_a_large_backlog_alerts_and_is_worked_off_in_bounded_steps(poller, monkeypatch):
    start = 45_000_000
    poller.STATE_FILE.write_text(json.dumps({"last_block": start}))
    # Far enough behind to trip the lag alert.
    monkeypatch.setattr(poller, "w3_client", lambda: _chain(start + 4_000_000))
    monkeypatch.setattr(poller, "get_usdc_transfers", lambda f, t: [])

    rc = poller.main()

    assert rc == 0
    # The lag alert has been worded in German since #706 ("USDC-Poller haengt
    # zurueck ... Abstand: N Bloecke").
    assert any("haengt zurueck" in m.lower() for m in poller._sent), "a large lag must alert"

    after = json.loads(poller.STATE_FILE.read_text())["last_block"]
    advanced = after - start
    assert advanced == poller.CHUNK_BLOCKS * poller.MAX_CHUNKS_PER_RUN, (
        f"run should be capped at {poller.MAX_CHUNKS_PER_RUN} chunks, advanced {advanced}"
    )
    assert after < start + 4_000_000, "must not claim to have caught up"
