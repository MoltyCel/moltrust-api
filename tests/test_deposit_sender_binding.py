"""FIX 2 (K1) — /credits/deposit may only credit the DID that owns the sending wallet.

The on-chain leg is stubbed: verify_usdc_transfer talks to Base and there is no
way to fabricate a real transaction for a test. Everything below the stub — the
wallet-binding lookup, the 403/200 decision, the deposit row and the credit
grant — runs against the real database.

Binding anchor is agents.wallet_address, written by POST /identity/bind after an
ECDSA signature check. The table wallet_links referenced by monitor/poll_payments.py
does not exist in any database.
"""
import uuid

import pytest


def _fake_transfer(from_address: str, credits: int = 100):
    """Stub for verify_usdc_transfer — a confirmed 1 USDC transfer."""
    async def _stub(tx_hash: str):
        return {
            "valid": True,
            "from_address": from_address,
            "usdc_amount": 1.0,
            "credits": credits,
            "block_number": 45_000_000,
            "error": None,
        }
    return _stub


async def _bind_wallet(did: str, wallet: str):
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE agents SET wallet_address = $1, wallet_chain = 'base' WHERE did = $2",
            wallet, did,
        )


async def _cleanup_deposit(tx_hash: str):
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM usdc_deposits WHERE tx_hash = $1", tx_hash)


def _tx() -> str:
    """A syntactically valid 32-byte tx hash (DepositRequest wants 64-70 chars)."""
    return "0x" + uuid.uuid4().hex + uuid.uuid4().hex


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """/credits/deposit is capped at 5/minute; this module issues more than that.

    The cap itself is not under test here — test_billing_stripe_errors.py covers
    the 429 path.
    """
    import app.main as m
    monkeypatch.setattr(m.limiter, "enabled", False, raising=False)


# ---------------------------------------------------------------------------
# Test 1 — the attack: claiming a foreign wallet's transaction
# ---------------------------------------------------------------------------
async def test_foreign_sender_wallet_is_refused(async_client, credit_test_agent, monkeypatch):
    """Attacker reads a tx_hash off Basescan and claims it under its own DID."""
    import app.main as m

    victim_did, _ = await credit_test_agent(balance=0)
    attacker_did, attacker_key = await credit_test_agent(balance=0)

    victim_wallet = "0x" + uuid.uuid4().hex[:40]
    await _bind_wallet(victim_did, victim_wallet)

    monkeypatch.setattr(m, "verify_usdc_transfer", _fake_transfer(victim_wallet))

    tx_hash = _tx()
    resp = await async_client.post(
        "/credits/deposit",
        json={"tx_hash": tx_hash, "did": attacker_did},
        headers={"X-API-Key": attacker_key},
    )

    assert resp.status_code == 403, f"expected 403, got {resp.status_code} {resp.text[:200]}"
    assert "different DID" in resp.text

    async with m.db_pool.acquire() as conn:
        balance = await conn.fetchval(
            "SELECT balance FROM credit_balances WHERE did = $1", attacker_did
        )
        deposit = await conn.fetchval(
            "SELECT 1 FROM usdc_deposits WHERE tx_hash = $1", tx_hash
        )
    assert balance == 0, f"attacker balance moved to {balance}"
    assert deposit is None, "a refused claim must not leave a usdc_deposits row"


# ---------------------------------------------------------------------------
# Test 2 — the legitimate path still works
# ---------------------------------------------------------------------------
async def test_own_bound_wallet_is_credited(async_client, credit_test_agent, monkeypatch):
    import app.main as m

    did, api_key = await credit_test_agent(balance=0)
    wallet = "0x" + uuid.uuid4().hex[:40]
    await _bind_wallet(did, wallet)

    monkeypatch.setattr(m, "verify_usdc_transfer", _fake_transfer(wallet, credits=100))

    tx_hash = _tx()
    try:
        resp = await async_client.post(
            "/credits/deposit",
            json={"tx_hash": tx_hash, "did": did},
            headers={"X-API-Key": api_key},
        )

        assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
        body = resp.json()
        assert body["credits_granted"] == 100
        assert body["new_balance"] == 100

        async with m.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT to_did, from_address FROM usdc_deposits WHERE tx_hash = $1", tx_hash
            )
        assert row["to_did"] == did
        assert row["from_address"].lower() == wallet.lower()
    finally:
        await _cleanup_deposit(tx_hash)


# ---------------------------------------------------------------------------
# Test 3 — case-insensitive match (web3 returns checksummed addresses)
# ---------------------------------------------------------------------------
async def test_checksum_case_does_not_break_the_match(async_client, credit_test_agent, monkeypatch):
    import app.main as m

    did, api_key = await credit_test_agent(balance=0)
    wallet_lower = "0x" + uuid.uuid4().hex[:40]
    await _bind_wallet(did, wallet_lower)

    # Chain hands back the same address in a different case.
    monkeypatch.setattr(m, "verify_usdc_transfer", _fake_transfer(wallet_lower.upper().replace("0X", "0x")))

    tx_hash = _tx()
    try:
        resp = await async_client.post(
            "/credits/deposit",
            json={"tx_hash": tx_hash, "did": did},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
    finally:
        await _cleanup_deposit(tx_hash)


# ---------------------------------------------------------------------------
# Test 4 — E2 default: unbound sending wallet is refused
# ---------------------------------------------------------------------------
async def test_unbound_sender_wallet_is_refused(async_client, credit_test_agent, monkeypatch):
    """E2 (fail-closed). 95 of 98 live agents have no bound wallet — see FIX-REPORT."""
    import app.main as m

    did, api_key = await credit_test_agent(balance=0)
    # deliberately NOT bound
    unbound_wallet = "0x" + uuid.uuid4().hex[:40]

    monkeypatch.setattr(m, "verify_usdc_transfer", _fake_transfer(unbound_wallet))

    resp = await async_client.post(
        "/credits/deposit",
        json={"tx_hash": _tx(), "did": did},
        headers={"X-API-Key": api_key},
    )

    assert resp.status_code == 403, f"{resp.status_code} {resp.text[:200]}"
    assert "not bound to any DID" in resp.text


# ---------------------------------------------------------------------------
# Test 5 — double claim of an own transaction still 409, not 200
# ---------------------------------------------------------------------------
async def test_double_claim_still_conflicts(async_client, credit_test_agent, monkeypatch):
    import app.main as m

    did, api_key = await credit_test_agent(balance=0)
    wallet = "0x" + uuid.uuid4().hex[:40]
    await _bind_wallet(did, wallet)

    monkeypatch.setattr(m, "verify_usdc_transfer", _fake_transfer(wallet))

    tx_hash = _tx()
    try:
        first = await async_client.post(
            "/credits/deposit",
            json={"tx_hash": tx_hash, "did": did},
            headers={"X-API-Key": api_key},
        )
        assert first.status_code == 200, f"{first.status_code} {first.text[:200]}"

        second = await async_client.post(
            "/credits/deposit",
            json={"tx_hash": tx_hash, "did": did},
            headers={"X-API-Key": api_key},
        )
        assert second.status_code == 409, f"{second.status_code} {second.text[:200]}"

        async with m.db_pool.acquire() as conn:
            balance = await conn.fetchval(
                "SELECT balance FROM credit_balances WHERE did = $1", did
            )
        assert balance == 100, f"credits granted twice: {balance}"
    finally:
        await _cleanup_deposit(tx_hash)
