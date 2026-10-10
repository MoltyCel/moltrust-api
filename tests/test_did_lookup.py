"""Read endpoints use the section 2.2 syntax, the same as writes.

History. On 2026-05-30 nine GET endpoints were found calling the strict
`validate_did()` on their path DID, which refused `did:moltrust:ambassador0001`.
They were moved to `validate_did_lookup()` with a permissive pattern so the
pre-convention identifiers stayed readable.

Since #631 `/identity/resolve` refuses those identifiers under section 2.2,
and the revised section 2.2 keeps resolvable exactly the 16-hex identifiers of
method version 1.0. A lookup that answered for an identifier the resolver calls
malformed contradicted the resolver, so the lookups now use DID_PATTERN.

This file covers:
  - the validator: 16 hex (1.0 and 1.1) passes, `ext_` passes while
    DID_PATTERN carries it, the pre-convention forms answer 400
  - the read endpoints answer 400 on a pre-convention identifier
"""
import pytest


LEGACY_SEED_DID = "did:moltrust:ambassador0001"   # pre-convention, outside section 2.2
STRICT_DID      = "did:moltrust:0123456789abcdef"  # exactly 16 hex chars
EXT_DID         = "did:moltrust:ext_516a656bafa39e5c"


# ---------------------------------------------------------------------------
# Pure unit tests on the two validators
# ---------------------------------------------------------------------------

def test_lookup_and_write_patterns_are_the_same():
    from app.main import DID_LOOKUP_PATTERN, DID_PATTERN
    assert DID_LOOKUP_PATTERN is DID_PATTERN


def test_validate_did_lookup_accepts_16hex():
    from app.main import validate_did_lookup
    assert validate_did_lookup(STRICT_DID) == STRICT_DID


def test_validate_did_lookup_follows_did_pattern_on_ext():
    """`ext_` is frozen in DID_PATTERN (see the note there); lookups follow it."""
    from app.main import validate_did_lookup
    assert validate_did_lookup(EXT_DID) == EXT_DID


@pytest.mark.parametrize("did", [
    LEGACY_SEED_DID,
    "did:moltrust:vcone",
    "did:moltrust:0123456789ABCDEF",
    "did:moltrust:0123456789abcde",
    "did:web:example.com",
    "not-a-did-at-all",
])
def test_validate_did_lookup_rejects_outside_section_2_2(did):
    from app.main import validate_did_lookup
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        validate_did_lookup(did)
    assert exc.value.status_code == 400


def test_validate_did_strict_still_rejects_seed():
    from app.main import validate_did
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        validate_did(LEGACY_SEED_DID)
    assert exc.value.status_code == 400


def test_validate_did_strict_accepts_16hex():
    from app.main import validate_did
    assert validate_did(STRICT_DID) == STRICT_DID


# ---------------------------------------------------------------------------
# Integration — the read endpoints answer 400 on the pre-convention DID
# ---------------------------------------------------------------------------

READ_ENDPOINTS_PUBLIC = [
    f"/identity/badge/{LEGACY_SEED_DID}",
    f"/reputation/query/{LEGACY_SEED_DID}",
    f"/credits/balance/{LEGACY_SEED_DID}",
    f"/agents/{LEGACY_SEED_DID}/erc8004",
    f"/sports/predictions/history/{LEGACY_SEED_DID}",
    f"/sports/fantasy/history/{LEGACY_SEED_DID}",
    f"/a2a/agent-card/{LEGACY_SEED_DID}",
]

READ_ENDPOINTS_AUTHED = [
    f"/credits/transactions/{LEGACY_SEED_DID}",
    f"/credits/deposits/{LEGACY_SEED_DID}",
]


@pytest.mark.parametrize("path", READ_ENDPOINTS_PUBLIC)
async def test_read_endpoint_refuses_seed_did_public(async_client, path):
    r = await async_client.get(path)
    assert r.status_code == 400, f"{path}: {r.status_code} {r.text}"


@pytest.mark.parametrize("path", READ_ENDPOINTS_AUTHED)
async def test_read_endpoint_refuses_seed_did_authed(async_client, credit_test_agent, path):
    _, api_key = await credit_test_agent(balance=1)
    r = await async_client.get(path, headers={"X-API-Key": api_key})
    assert r.status_code == 400, f"{path}: {r.status_code} {r.text}"


async def test_write_endpoint_still_rejects_lookup_did(async_client, credit_test_agent):
    _, api_key = await credit_test_agent(balance=1)
    r = await async_client.post(
        "/reputation/rate",
        json={"to_did": LEGACY_SEED_DID, "score": 5},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code in (400, 422), f"{r.status_code} {r.text}"
