"""Thread-safe nonce manager for Base L2 transactions.

Prevents nonce collisions when multiple async handlers submit
transactions concurrently from the same wallet address.
"""
import asyncio
import logging
from web3 import Web3

logger = logging.getLogger("moltrust.nonce")

_locks: dict[str, asyncio.Lock] = {}
_nonces: dict[str, int] = {}


def _get_lock(address: str) -> asyncio.Lock:
    """Get or create a per-address lock."""
    if address not in _locks:
        _locks[address] = asyncio.Lock()
    return _locks[address]


async def get_nonce(w3: Web3, address: str) -> int:
    """Get the next nonce for an address, serialized via asyncio.Lock.

    On first call (or after a reset), fetches the pending nonce from the
    chain. Subsequent calls increment locally to avoid collisions when
    multiple transactions are submitted before the first is mined.
    """
    lock = _get_lock(address)
    async with lock:
        if address not in _nonces:
            _nonces[address] = await asyncio.to_thread(
                w3.eth.get_transaction_count, address, "pending"
            )
        else:
            _nonces[address] += 1
        nonce = _nonces[address]
    logger.debug(f"Nonce for {address}: {nonce}")
    return nonce


async def reset_nonce(address: str) -> None:
    """Reset cached nonce after a known failure, forcing a re-fetch."""
    lock = _get_lock(address)
    async with lock:
        _nonces.pop(address, None)
