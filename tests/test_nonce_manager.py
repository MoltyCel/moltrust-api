"""Unit tests for app.nonce_manager — no external dependencies needed."""
import asyncio
import sys
from unittest.mock import MagicMock

# Mock web3 before importing nonce_manager
sys.modules["web3"] = MagicMock()

import pytest
from app.nonce_manager import get_nonce, reset_nonce, _nonces, _locks


@pytest.fixture(autouse=True)
def _clear_state():
    """Reset module-level state between tests."""
    _nonces.clear()
    _locks.clear()
    yield
    _nonces.clear()
    _locks.clear()


def _mock_w3(starting_nonce=42):
    """Create a mock Web3 instance that returns a configurable nonce."""
    w3 = MagicMock()
    w3.eth.get_transaction_count.return_value = starting_nonce
    return w3


@pytest.mark.asyncio
async def test_first_call_fetches_from_chain():
    w3 = _mock_w3(starting_nonce=10)
    nonce = await get_nonce(w3, "0xAAA")
    assert nonce == 10


@pytest.mark.asyncio
async def test_second_call_increments_locally():
    w3 = _mock_w3(starting_nonce=10)
    n1 = await get_nonce(w3, "0xAAA")
    n2 = await get_nonce(w3, "0xAAA")
    assert n1 == 10
    assert n2 == 11
    # Chain should only be called once (via to_thread)
    assert w3.eth.get_transaction_count.call_count == 1


@pytest.mark.asyncio
async def test_sequential_calls_produce_unique_nonces():
    w3 = _mock_w3(starting_nonce=0)
    nonces = []
    for _ in range(10):
        nonces.append(await get_nonce(w3, "0xAAA"))
    assert nonces == list(range(10))


@pytest.mark.asyncio
async def test_different_addresses_are_independent():
    w3 = _mock_w3(starting_nonce=100)
    n_a = await get_nonce(w3, "0xAAA")
    n_b = await get_nonce(w3, "0xBBB")
    assert n_a == 100
    assert n_b == 100  # Independent — both start at 100


@pytest.mark.asyncio
async def test_reset_forces_refetch():
    w3 = _mock_w3(starting_nonce=5)
    n1 = await get_nonce(w3, "0xAAA")
    n2 = await get_nonce(w3, "0xAAA")
    assert n1 == 5
    assert n2 == 6

    # Simulate chain advancing
    w3.eth.get_transaction_count.return_value = 20
    await reset_nonce("0xAAA")

    n3 = await get_nonce(w3, "0xAAA")
    assert n3 == 20  # Re-fetched from chain


@pytest.mark.asyncio
async def test_reset_nonexistent_address_is_noop():
    await reset_nonce("0xNONE")  # Should not raise


@pytest.mark.asyncio
async def test_concurrent_calls_get_unique_nonces():
    """Simulate concurrent async handlers all requesting nonces at once."""
    w3 = _mock_w3(starting_nonce=0)

    async def grab_nonce():
        return await get_nonce(w3, "0xAAA")

    results = await asyncio.gather(*[grab_nonce() for _ in range(20)])

    # All nonces must be unique (no collisions)
    assert len(set(results)) == 20
    assert sorted(results) == list(range(20))
