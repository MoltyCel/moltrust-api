"""Group A + B tests for credit_middleware after schema-alignment fix (V2.1 spec).

Endpoint used throughout: GET /identity/verify/{did}, cost=1.

Group A (Tests 1-6) — single-request behavior matrix.
Group B (Test 7) — concurrent-requests race-condition invariant.
"""
import asyncio
import pytest


# ---------------------------------------------------------------------------
# Test 1 — smoke: paid call deducts exactly cost, logs exactly one api_call row
# ---------------------------------------------------------------------------
async def test_smoke_paid_call_deducts(async_client, credit_test_agent):
    from app.main import db_pool

    did, api_key = await credit_test_agent(balance=1000)

    resp = await async_client.get(
        f"/identity/verify/{did}",
        headers={"X-API-Key": api_key},
    )

    # Endpoint must succeed; otherwise the middleware skips deduct.
    assert resp.status_code < 400, (
        f"endpoint failed before deduct path: {resp.status_code} {resp.text[:200]}"
    )

    async with db_pool.acquire() as conn:
        new_balance = await conn.fetchval(
            "SELECT balance FROM credit_balances WHERE did = $1", did
        )
        assert new_balance == 999, (
            f"expected balance 999 after deduct, got {new_balance}"
        )

        tx_rows = await conn.fetch(
            "SELECT amount, tx_type, from_did, to_did, balance_after "
            "FROM credit_transactions "
            "WHERE from_did = $1 AND tx_type = 'api_call' "
            "ORDER BY created_at DESC",
            did,
        )
        assert len(tx_rows) == 1, (
            f"expected exactly 1 api_call ledger row, got {len(tx_rows)}"
        )
        row = tx_rows[0]
        assert row["amount"] == 1, f"amount: {row['amount']}"
        assert row["tx_type"] == "api_call"
        assert row["from_did"] == did
        assert row["to_did"] is None
        assert row["balance_after"] == 999


# ---------------------------------------------------------------------------
# Test 2 — insufficient balance returns 402, no deduct, no ledger row
# ---------------------------------------------------------------------------
async def test_deduct_insufficient_balance(async_client, credit_test_agent):
    """Balance=0 → request must be refused with 402, no balance change, no tx row.

    NOTE: With the current code this request hits the *pre-check* at line ~427
    of main.py (before call_next), not the fix-block's 402 path. The pre-check
    returns error='Insufficient credits' (capital, with space) and includes the
    balance value, while the fix-block returns error='insufficient_credits'
    (lower, underscore) and discloses no balance. Per Spec V2 the pre-check is
    intentionally left as-is (advisory). This test asserts the fix-block format
    as specified in the test plan — it WILL be red until the pre-check is
    harmonized or this test is amended. Either decision is a separate sprint.
    """
    from app.main import db_pool

    did, api_key = await credit_test_agent(balance=0)

    resp = await async_client.get(
        f"/identity/verify/{did}",
        headers={"X-API-Key": api_key},
    )

    assert resp.status_code == 402, f"expected 402, got {resp.status_code}"
    body = resp.json()
    assert body.get("error") == "insufficient_credits", (
        f"expected error='insufficient_credits', got {body!r}"
    )

    async with db_pool.acquire() as conn:
        balance = await conn.fetchval(
            "SELECT balance FROM credit_balances WHERE did = $1", did
        )
        assert balance == 0, f"balance must not change, got {balance}"

        tx_count = await conn.fetchval(
            "SELECT COUNT(*) FROM credit_transactions "
            "WHERE from_did = $1 AND tx_type = 'api_call'",
            did,
        )
        assert tx_count == 0, f"expected 0 api_call rows, got {tx_count}"


# ---------------------------------------------------------------------------
# Test 3 — caller_did has no credit_balances row → 402, no tx
# ---------------------------------------------------------------------------
async def test_deduct_unknown_did(async_client, credit_test_agent):
    """Agent + api_key exist but credit_balances row is missing.

    Concrete case under test: caller_did resolves via api_keys table, but the
    credit_balances row was never created. With the current code, the pre-check
    fires first (line ~425) — get_balance() returns 0 for missing row, and the
    pre-check refuses with 402 *before* call_next. The actual atomic UPDATE-zero-
    rows path in the fix-block is therefore NOT exercised by this test — it
    requires a race condition (concurrent balance drop between pre-check and
    UPDATE) which is the territory of Group B's concurrency test. This test
    asserts only the outer-visible contract: 402 + no ledger pollution.
    """
    from app.main import db_pool

    did, api_key = await credit_test_agent(balance=1000)

    # Delete the credit_balances row so the agent has none.
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM credit_balances WHERE did = $1", did)

    resp = await async_client.get(
        f"/identity/verify/{did}",
        headers={"X-API-Key": api_key},
    )

    assert resp.status_code == 402, f"expected 402, got {resp.status_code}"

    async with db_pool.acquire() as conn:
        tx_count = await conn.fetchval(
            "SELECT COUNT(*) FROM credit_transactions "
            "WHERE from_did = $1 AND tx_type = 'api_call'",
            did,
        )
        assert tx_count == 0, f"expected 0 api_call rows, got {tx_count}"

    # Re-insert credit_balances row so the fixture cleanup can DELETE it cleanly.
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO credit_balances (did, balance) VALUES ($1, 0)", did
        )


# ---------------------------------------------------------------------------
# Test 4 — ledger consistency across N sequential calls
# ---------------------------------------------------------------------------
async def test_ledger_consistency(async_client, credit_test_agent):
    """5 sequential paid calls drain balance 1000→995, and ledger reflects it
    monotonically (999, 998, 997, 996, 995)."""
    from app.main import db_pool

    did, api_key = await credit_test_agent(balance=1000)

    for _ in range(5):
        resp = await async_client.get(
            f"/identity/verify/{did}",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code < 400, f"unexpected {resp.status_code}: {resp.text[:120]}"

    async with db_pool.acquire() as conn:
        final_balance = await conn.fetchval(
            "SELECT balance FROM credit_balances WHERE did = $1", did
        )
        assert final_balance == 995, f"expected final balance 995, got {final_balance}"

        rows = await conn.fetch(
            "SELECT amount, balance_after FROM credit_transactions "
            "WHERE from_did = $1 AND tx_type = 'api_call' "
            "ORDER BY created_at ASC",
            did,
        )
        assert len(rows) == 5, f"expected 5 api_call rows, got {len(rows)}"

        total_amount = sum(r["amount"] for r in rows)
        assert total_amount == 5, f"expected sum(amount)=5, got {total_amount}"

        balance_afters = [r["balance_after"] for r in rows]
        assert balance_afters == [999, 998, 997, 996, 995], (
            f"expected monotonically decreasing [999..995], got {balance_afters}"
        )


# ---------------------------------------------------------------------------
# Test 5 — unexpected DB-side error in deduct path returns 500
# ---------------------------------------------------------------------------
async def test_db_error_returns_500(async_client, credit_test_agent, monkeypatch):
    """Force the deduct path's first DB call to raise → 500 + balance unchanged.

    Monkeypatch point: `app.credits.resolve_endpoint_key`. It is called twice
    per request lifecycle in the credit middleware:
      Call 1 — pre-check via `get_endpoint_cost(method, path)` at line ~393
      Call 2 — deduct block local import + invocation at line ~455

    The patched function uses a call counter: 1st call returns the real key
    (so pre-check + balance check pass normally), 2nd call raises a synthetic
    RuntimeError. The exception fires inside the deduct path's try-block,
    is caught by the except at line ~482, and returns the spec-defined
    JSONResponse(500, error='credit_processing_error').

    The asyncpg transaction in the deduct path was open by the time the raise
    fires (UPDATE has already been issued but not yet committed via the
    `async with conn.transaction()` exit) — the transaction rolls back on
    exception, leaving the balance unchanged.
    """
    from app.main import db_pool
    import app.credits as credits_mod

    did, api_key = await credit_test_agent(balance=1000)

    real_resolve = credits_mod.resolve_endpoint_key
    call_count = {"n": 0}

    def failing_resolve(method, path):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("simulated DB error in deduct path (test_db_error_returns_500)")
        return real_resolve(method, path)

    monkeypatch.setattr(credits_mod, "resolve_endpoint_key", failing_resolve)

    resp = await async_client.get(
        f"/identity/verify/{did}",
        headers={"X-API-Key": api_key},
    )

    assert resp.status_code == 500, f"expected 500, got {resp.status_code} {resp.text[:200]}"
    body = resp.json()
    assert body.get("error") == "credit_processing_error", (
        f"expected error='credit_processing_error', got {body!r}"
    )

    async with db_pool.acquire() as conn:
        balance = await conn.fetchval(
            "SELECT balance FROM credit_balances WHERE did = $1", did
        )
        assert balance == 1000, (
            f"expected balance unchanged at 1000 (rollback), got {balance}"
        )


# ---------------------------------------------------------------------------
# Test 6 — 402 body must not leak balance value or other state
# ---------------------------------------------------------------------------
async def test_error_body_no_balance_disclosure(async_client, credit_test_agent):
    """Refused-for-insufficient-credits responses must NOT reveal the balance.

    Per Spec V2 Section 5, the new error contract for the deduct path is
    {error: 'insufficient_credits', detail: '...'} — explicitly without a
    balance or required-amount disclosure (those would leak internal state
    to unauthenticated-style probes).

    NOTE (same caveat as Test 2): with balance=0 the pre-check at line ~427
    fires first and returns the OLD error shape, which DOES include 'balance'
    and 'required' keys. This test asserts the V2-Section-5 contract — it
    will fail in the current code until the pre-check is harmonized with the
    fix-block. The failure surfaces the inconsistency.
    """
    did, api_key = await credit_test_agent(balance=0)

    resp = await async_client.get(
        f"/identity/verify/{did}",
        headers={"X-API-Key": api_key},
    )

    assert resp.status_code == 402, f"expected 402, got {resp.status_code}"
    body = resp.json()
    # Body must NOT include a balance key with a numeric value.
    assert "balance" not in body, (
        f"402 body discloses balance: {body!r}"
    )
    # Required-amount (cost) disclosure also leaks pricing state.
    assert "required" not in body, (
        f"402 body discloses required-amount: {body!r}"
    )
    # And the error code must be the V2 lower-underscore form, not the legacy
    # 'Insufficient credits' string.
    assert body.get("error") == "insufficient_credits", (
        f"expected error='insufficient_credits', got {body!r}"
    )


# ---------------------------------------------------------------------------
# Group B — Test 7 — concurrency invariant: N parallel requests respect balance
# ---------------------------------------------------------------------------
async def test_concurrent_deducts_respect_balance(async_client, credit_test_agent):
    """Race-Test: balance=3, N=10 parallele GETs an /identity/verify/{did}.

    Alle 10 Requests nutzen dieselbe DID + denselben api_key. Alle passieren den
    advisory Pre-Check (zum Zeitpunkt des Pre-Checks ist balance noch > 0). Im
    Deduct-Block kaempft das atomare UPDATE ... WHERE balance >= cost: nur 3
    koennen gewinnen, weil nur 3 Credits da sind.

    Invariante (muss gelten, egal wie das Scheduling laeuft):
    - genau 3 Requests → HTTP 200, genau 7 → HTTP 402
    - credit_balances.balance == 0 am Ende
    - genau 3 credit_transactions api_call-Rows fuer die DID
    - die balance_after-Werte der 3 Rows sind exakt {2, 1, 0} — jeder Wert genau
      einmal, keine Dublette (eine Dublette wuerde eine nicht-atomare Race im
      UPDATE beweisen)
    - Summe der amount ueber die 3 Rows == 3

    Warum N=10 gegen balance=3 statt N=2 gegen balance=1: ein N=2-Test trifft die
    Race nur bei perfektem Timing — gruen koennte auch zufaellige Serialisierung
    bedeuten. N deutlich groesser als balance erzwingt echte Konkurrenz und macht
    die Invariante zum harten Beweis.
    """
    from app.main import db_pool

    did, api_key = await credit_test_agent(balance=3)

    coros = [
        async_client.get(
            f"/identity/verify/{did}",
            headers={"X-API-Key": api_key},
        )
        for _ in range(10)
    ]
    responses = await asyncio.gather(*coros)

    status_codes = sorted(r.status_code for r in responses)
    # Strict invariant: only 200 and 402 codes are valid outcomes here.
    assert set(status_codes) <= {200, 402}, (
        f"unexpected status codes (no 500s allowed): {status_codes}"
    )
    assert status_codes.count(200) == 3, (
        f"expected exactly 3 successes, got {status_codes.count(200)}; "
        f"all codes: {status_codes}"
    )
    assert status_codes.count(402) == 7, (
        f"expected exactly 7 refusals, got {status_codes.count(402)}; "
        f"all codes: {status_codes}"
    )

    # Every 402 must use the harmonized error contract; no balance disclosure.
    for r in responses:
        if r.status_code == 402:
            body = r.json()
            assert body.get("error") == "insufficient_credits", (
                f"402 body has wrong error: {body!r}"
            )
            assert "balance" not in body, (
                f"402 body discloses balance: {body!r}"
            )

    async with db_pool.acquire() as conn:
        final_balance = await conn.fetchval(
            "SELECT balance FROM credit_balances WHERE did = $1", did
        )
        assert final_balance == 0, f"expected final balance 0, got {final_balance}"

        rows = await conn.fetch(
            "SELECT amount, balance_after FROM credit_transactions "
            "WHERE from_did = $1 AND tx_type = 'api_call' "
            "ORDER BY balance_after DESC",
            did,
        )
        assert len(rows) == 3, (
            f"expected exactly 3 api_call ledger rows, got {len(rows)}"
        )

        # The three balance_after values must be exactly {2, 1, 0} — every value
        # once, no duplicates. A duplicate would prove a non-atomic UPDATE race.
        balance_afters = sorted(r["balance_after"] for r in rows)
        assert balance_afters == [0, 1, 2], (
            f"expected balance_after set [0,1,2], got {balance_afters} — "
            f"duplicates indicate the atomic UPDATE failed to serialize concurrent "
            f"deducts (WHERE balance >= cost did not prevent over-spend)"
        )

        total_amount = sum(r["amount"] for r in rows)
        assert total_amount == 3, f"expected sum(amount)=3, got {total_amount}"


async def test_key_without_an_agent_is_401_and_names_both_ways_out(async_client):
    """A key with no agent behind it is an authentication problem, not a bill.

    402 sent the caller to look at a balance that had nothing to do with it —
    the funnel test walked into exactly that after registering through
    /identity/register-pop, which hands back a DID and no key.
    """
    import uuid as _uuid
    from app.main import db_pool, API_KEYS

    api_key = f"mt_noagent_{_uuid.uuid4().hex}"
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_keys (key, email) VALUES ($1, $2)",
            api_key, f"noagent+{api_key[-8:]}@test.local",
        )
    API_KEYS.add(api_key)
    try:
        resp = await async_client.get(
            f"/identity/verify/did:moltrust:{_uuid.uuid4().hex[:16]}",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 401, f"expected 401, got {resp.status_code}"
        body = resp.json()
        # Both routes out, because which one applies depends on whether the
        # caller already holds a DID.
        assert "/auth/signup-did" in body["if_you_have_a_did"]
        assert "/identity/register-challenge" in body["if_you_have_no_did_yet"]
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM api_keys WHERE key = $1", api_key)
        API_KEYS.discard(api_key)
