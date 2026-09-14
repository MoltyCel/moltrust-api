"""Hex normalisation for on-chain identifiers.

Kept free of imports with side effects so it can be tested without the module
bootstrap around it: poll_payments reads ~/.moltrust_secrets at import time,
which does not exist in CI.
"""
from __future__ import annotations

from web3 import Web3


def hex0x(value) -> str:
    """Return a lowercase 0x-prefixed hex string.

    web3 6 returned ``HexBytes.hex()`` with the 0x prefix; web3 7 dropped it.
    The unprefixed form is what reached ``payment_events.tx_hash`` and
    ``usdc_deposits``, while every other writer stores the prefixed form, so the
    duplicate checks in the poller compared two spellings of the same
    transaction and never matched.
    """
    raw = value.hex() if hasattr(value, "hex") else str(value)
    raw = raw.lower()
    return raw if raw.startswith("0x") else "0x" + raw


# The same constant the x402 verifier matches on. If these ever disagree, the
# poller and the verifier are watching different events.
TRANSFER_TOPIC = hex0x(Web3.keccak(text="Transfer(address,address,uint256)"))
