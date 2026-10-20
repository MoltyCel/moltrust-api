"""Which registrations may spend the productive signer.

A registration with platform `test` is our own probe (scripts/funnel_test.py,
scripts/gate_proof.py, the test suite). It must never send an on-chain
transaction from the productive signer (`ipr_anchor_signer` /
`registry_write_signer`, both BASE_WRITE_KEY): every such anchor costs gas from
the wallet that carries the eight ERC-8004 IDs, and leaves a permanent record
for an agent that does not exist.

The comparison is the one the funnel already uses for its internal platforms
(app/funnel.py `is_internal_platform`): trimmed and case-insensitive, so
" Test " is a test registration too. Only `test` is matched here; `system` and
`moltrust-internal` are not registered through the public register routes.

The routes call this through `anchor_registration` and
`erc8004_register_for_platform` in app/main.py; tests/test_test_platform_no_anchor.py
pins that no register route reaches `anchor_to_base` or `register_onchain_agent`
directly.
"""

from __future__ import annotations

TEST_PLATFORM = "test"

# The `status` a response carries when the anchor was skipped on purpose.
ANCHOR_SKIPPED_TEST = "skipped: platform=test"
ANCHOR_DONE = "anchored"
# anchor_to_base answered None: key not configured, RPC down, budget exhausted.
ANCHOR_NOT_DONE = "not_anchored"


def is_test_platform(platform) -> bool:
    """True when `platform` names our own test surface."""
    if platform is None:
        return False
    return str(platform).strip().lower() == TEST_PLATFORM
