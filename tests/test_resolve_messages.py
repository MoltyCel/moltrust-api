"""GET /identity/resolve must not answer two different problems with one message.

Until 2026-10-06 anything that failed DID_PATTERN and was not did:web fell
through to a single `400 "Unsupported DID method"`. For did:moltrust:ambassador0001
that sentence is false - the method is ours and the row exists since 2026-02-20 -
and a Universal Resolver driver reported it as an internal error of ours on
decentralized-identity/universal-resolver#541:

    INTERNAL_ERROR (An internal error has occurred.) -> Registry returned 400

Section 2.2 of the method spec defines the method-specific identifier as
`[0-9a-f]{16}`, so a string outside that is a malformed identifier of a method we
do support. A foreign method is the other case and keeps its own message.
"""
import pytest


MALFORMED = [
    "did:moltrust:ambassador0001",        # letters outside a-f, the reported case
    "did:moltrust:vcone",                 # the other pre-convention identifier
    "did:moltrust:ABCDEF0123456789",      # uppercase
    "did:moltrust:0123456789abcde",       # 15 characters
    "did:moltrust:0123456789abcdef0",     # 17 characters
    "did:moltrust:",                      # empty tail
]

FOREIGN_METHOD = [
    "did:example:0123456789abcdef",
    "did:key:z6MkhaXgBZDvotDkL5257faiz",
    "did:aps:ef8bbf57911681e4a2965ee6",
]


@pytest.mark.parametrize("did", MALFORMED)
async def test_malformed_method_specific_id_is_invalid_did(async_client, did):
    r = await async_client.get(f"/identity/resolve/{did}")
    assert r.status_code == 400, f"{did}: {r.status_code} {r.text}"
    detail = r.json()["detail"]
    assert isinstance(detail, dict), f"{did}: detail is not an object: {detail!r}"
    assert detail["error"] == "invalid_did", f"{did}: {detail}"
    # The old sentence must not come back for an identifier of our own method.
    assert "unsupported" not in r.text.lower(), (
        f"{did} still answers with an unsupported-method message: {r.text}")


@pytest.mark.parametrize("did", FOREIGN_METHOD)
async def test_foreign_method_is_unsupported_method(async_client, did):
    r = await async_client.get(f"/identity/resolve/{did}")
    assert r.status_code == 400, f"{did}: {r.status_code} {r.text}"
    detail = r.json()["detail"]
    assert isinstance(detail, dict), f"{did}: detail is not an object: {detail!r}"
    assert detail["error"] == "unsupported_method", f"{did}: {detail}"
    assert detail["error"] != "invalid_did"


async def test_the_two_cases_do_not_share_an_answer(async_client):
    """The point of the change: one input per case, two different error codes."""
    bad_id = await async_client.get("/identity/resolve/did:moltrust:ambassador0001")
    bad_method = await async_client.get("/identity/resolve/did:example:0123456789abcdef")
    assert bad_id.status_code == bad_method.status_code == 400
    assert bad_id.json()["detail"]["error"] != bad_method.json()["detail"]["error"]


async def test_a_conformant_identifier_still_reaches_the_lookup(async_client):
    """Regression: a well-formed identifier must not be caught by either branch.

    An unregistered one answers 404 did_not_found, which is the existing
    behaviour and the proof that the new branches sit after the lookup.
    """
    r = await async_client.get("/identity/resolve/did:moltrust:0123456789abcdef")
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["error"] == "did_not_found"


async def test_did_web_root_still_resolves(async_client):
    r = await async_client.get("/identity/resolve/did:web:api.moltrust.ch")
    assert r.status_code == 200
    assert r.json()["id"] == "did:web:api.moltrust.ch"
