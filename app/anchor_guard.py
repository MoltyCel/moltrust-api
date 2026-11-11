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

Two kinds of route reach the productive signer:

- register routes, which carry the new agent's platform in the request. They
  go through `anchor_registration` and `erc8004_register_for_platform` in
  app/main.py, which call `is_test_platform`.
- routes acting for an agent that already exists (sports commits, signal
  provider registration, music credentials, /anchors/batch, ratings and
  ERC-8004 validation feedback). The request carries no platform, so the doors
  for these (`anchor_for_agents`, `anchor_calldata_for_agents`,
  `reputation_feedback_for_agents`, `schedule_music_anchor`) look the platform
  up with `any_test_agent`: if any agent the transaction would be for is a test
  agent, nothing is signed.

tests/test_test_platform_no_anchor.py pins that no function in app/ reaches a
productive signer except through one of these doors (or a listed, justified
exception), and docs/invariants/c-test-platform-never-anchored.yaml checks the
tables those routes write their transaction hashes into.
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


async def is_test_agent(conn, did) -> bool:
    """True when `did` is a registered agent with platform `test`.

    An unknown DID, or none at all, is not a test agent: the question here is
    "is this one of our probes", and only a row in `agents` can answer yes. A
    database error is not swallowed: the caller's request fails, and nothing is
    signed on a guess.
    """
    if not did:
        return False
    platform = await conn.fetchval("SELECT platform FROM agents WHERE did = $1", did)
    return is_test_platform(platform)


async def any_test_agent(conn, dids) -> bool:
    """True when any of `dids` is a test agent (see `is_test_agent`)."""
    for did in dict.fromkeys(d for d in dids if d):
        if await is_test_agent(conn, did):
            return True
    return False
