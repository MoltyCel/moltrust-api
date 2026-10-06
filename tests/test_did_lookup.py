"""Read endpoints must accept lookup-style (seed) DIDs; writes stay strict.

Bug class (discovered 2026-05-30, marketplace endpoint audit): nine GET
endpoints called `validate_did()` (strict `^did:moltrust:[a-f0-9]{16}$`)
on their path-parameter DID. That rejected legacy seed DIDs like
`did:moltrust:ambassador0001` even though `DID_LOOKUP_PATTERN` was
already defined for exactly this purpose. /trust/gate and
/skill/trust-score never had the bug because they don't validate the
path — they go straight to the DB.

Fix: `validate_did_lookup()` parallel helper using DID_LOOKUP_PATTERN;
all nine read endpoints routed through it. Write paths (register, rate,
issue) keep `validate_did()` so new identities still follow the strict
16-hex convention.

This file covers:
  - Pure validator behaviour (both helpers, seed + 16-hex + bad input)
  - Each of the 9 read endpoints: lookup DID must NOT yield 400
    (status past the validator is endpoint's own concern — 200, 404,
    etc. all fine)
  - Regression: a write endpoint still rejects the lookup-style DID
"""
import uuid

import pytest


LEGACY_SEED_DID = "did:moltrust:ambassador0001"   # lookup-pattern only
STRICT_DID      = "did:moltrust:0123456789abcdef"  # exactly 16 hex chars


# ---------------------------------------------------------------------------
# Pure unit tests on the two validators
# ---------------------------------------------------------------------------

def test_validate_did_lookup_accepts_seed():
    from app.main import validate_did_lookup
    assert validate_did_lookup(LEGACY_SEED_DID) == LEGACY_SEED_DID


def test_validate_did_lookup_accepts_16hex():
    from app.main import validate_did_lookup
    assert validate_did_lookup(STRICT_DID) == STRICT_DID


def test_validate_did_lookup_rejects_non_moltrust():
    from app.main import validate_did_lookup
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        validate_did_lookup("did:web:example.com")
    assert exc.value.status_code == 400


def test_validate_did_lookup_rejects_garbage():
    from app.main import validate_did_lookup
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        validate_did_lookup("not-a-did-at-all")


def test_validate_did_strict_still_rejects_seed():
    """Regression: the strict validator must KEEP rejecting seed DIDs.

    Writes (register, rate, issue) depend on this to enforce the canonical
    16-hex format on new identities."""
    from app.main import validate_did
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        validate_did(LEGACY_SEED_DID)
    assert exc.value.status_code == 400


def test_validate_did_strict_accepts_16hex():
    from app.main import validate_did
    assert validate_did(STRICT_DID) == STRICT_DID


# ---------------------------------------------------------------------------
# Integration — the nine read endpoints must NOT 400 on the seed DID
# ---------------------------------------------------------------------------
# Past the validator, each endpoint's own logic decides 200/404/503/etc.
# That's not what this PR is fixing; we only assert the validator gate
# now lets the seed DID through.

READ_ENDPOINTS_PUBLIC = [
    # Added 2026-10-06. The 2026-05-30 audit built its list from the endpoints
    # that called validate_did(), and this one calls no validator at all - it
    # matches DID_PATTERN inline - so it was never on the list and kept
    # answering 400 "Unsupported DID method" for the two DIDs registered before
    # the 16-hex convention. Reported from outside on
    # decentralized-identity/universal-resolver#541.
    f"/identity/resolve/{LEGACY_SEED_DID}",
    f"/identity/verify/{LEGACY_SEED_DID}",
    f"/identity/badge/{LEGACY_SEED_DID}",
    f"/reputation/query/{LEGACY_SEED_DID}",
    f"/credits/balance/{LEGACY_SEED_DID}",
    f"/agents/{LEGACY_SEED_DID}/erc8004",
    f"/sports/predictions/history/{LEGACY_SEED_DID}",
    f"/sports/fantasy/history/{LEGACY_SEED_DID}",
]

READ_ENDPOINTS_AUTHED = [
    f"/credits/transactions/{LEGACY_SEED_DID}",
    f"/credits/deposits/{LEGACY_SEED_DID}",
]


@pytest.mark.parametrize("path", READ_ENDPOINTS_PUBLIC)
async def test_read_endpoint_accepts_seed_did_public(async_client, path):
    r = await async_client.get(path)
    assert r.status_code != 400, (
        f"{path} returned 400 on lookup-pattern DID — "
        f"validator gate still strict. Body: {r.text}"
    )


@pytest.mark.parametrize("path", READ_ENDPOINTS_AUTHED)
async def test_read_endpoint_accepts_seed_did_authed(async_client, credit_test_agent, path):
    _, api_key = await credit_test_agent(balance=1)
    r = await async_client.get(path, headers={"X-API-Key": api_key})
    assert r.status_code != 400, (
        f"{path} returned 400 on lookup-pattern DID — "
        f"validator gate still strict. Body: {r.text}"
    )


# ---------------------------------------------------------------------------
# Regression — a write endpoint still rejects the lookup-style DID
# ---------------------------------------------------------------------------

async def test_write_endpoint_still_rejects_lookup_did(async_client, credit_test_agent):
    """`POST /reputation/rate` is a write — pydantic validators on
    RateRequest use `DID_PATTERN.match` directly, not `validate_did_lookup`.
    Sending a lookup-pattern DID for `to_did` must be rejected (FastAPI
    surfaces pydantic ValueErrors as 422)."""
    _, api_key = await credit_test_agent(balance=1)
    r = await async_client.post(
        "/reputation/rate",
        json={"to_did": LEGACY_SEED_DID, "score": 5},
        headers={"X-API-Key": api_key},
    )
    # 422 is the pydantic validation rejection; 400 is also acceptable
    # if the endpoint short-circuits earlier. Both indicate "format
    # was rejected", which is the regression we're guarding.
    assert r.status_code in (400, 422), (
        f"write endpoint accepted lookup-pattern DID: "
        f"{r.status_code} {r.text}"
    )
