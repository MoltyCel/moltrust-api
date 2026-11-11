"""Our own Base addresses, for readers that must not count our own money.

A transfer from one of these is money moving between our own wallets, not a
payment. On 2026-10-12 the rotation moved 0.39 USDC from OLD to NEW, and the
USDC poller booked it in payment_events as a payment received (ops job C4).

The same addresses as the lists in scripts/gate_measure.py (OUR_SUBJECTS),
scripts/runden_auswertung.py (OURS) and scripts/bazaar_index_check.py
(NEEDLES). tests/test_poller_own_addresses.py reads those three files and fails
when one of them names an address missing here. The placeholders 0x0 and 0x1
from gate_measure.py are left out on purpose: a USDC transfer from 0x0 is a
mint, which is how USDC bridged in from another chain arrives.

The list lives here and not in the readers: tests/test_payment_address.py keeps
app/usdc.py, monitor/poll_payments.py and agents/poll_payments.py free of
address literals (ops job S2), so the receiving address has exactly one source,
BASE_ADDR.
"""

from __future__ import annotations

from app.payment_address import payment_address

OWN_ADDRESSES = frozenset({
    "0x380238347e58435f40b4da1f1a045a271d5838f5",  # OLD, productive signer until the 2026-10 rotation
    "0xd63f988201a0bd184deab7d4e97b1717b25a5800",  # NEW, productive signer since the 2026-10 rotation
    "0xd8f5bb747f7459bf3e1cc1ad041e2ca57b946c38",  # test wallet
    "0xa175d51bfe0170738720daaec627a84d44dc9eb9",  # taskmarket escrow
    "0x9068e25d8ea247a24abf9ff42bfa084931b3ba91",  # stale, unreferenced
    "0x8e4cd1e31b4d4114c811e41bd4a01002baec5ee6",  # KMS registry signer, owner of the eight ERC-8004 IDs since 2026-10-23
})


def is_own_address(addr: str) -> bool:
    """True for one of our wallets, including whatever BASE_ADDR names now."""
    a = (addr or "").lower()
    return a in OWN_ADDRESSES or a == payment_address().lower()
