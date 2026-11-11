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

# DID Resolution v1, section 11.
NOT_FOUND = "https://www.w3.org/ns/did#NOT_FOUND"


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

    An unregistered one answers 404 as a DID resolution result, which is the
    proof that the new branches sit after the lookup.
    """
    r = await async_client.get("/identity/resolve/did:moltrust:0123456789abcdef")
    assert r.status_code == 404, r.text
    assert r.json()["didResolutionMetadata"]["error"]["type"] == NOT_FOUND


async def test_did_web_root_still_resolves(async_client):
    r = await async_client.get("/identity/resolve/did:web:api.moltrust.ch")
    assert r.status_code == 200
    assert r.json()["id"] == "did:web:api.moltrust.ch"


async def test_a_miss_is_a_did_resolution_result(async_client):
    """The shape a resolver expects, not FastAPI's detail envelope.

    Section 4.4 of DID Resolution v1 (CR Draft 2026-10-01): didDocument null,
    didResolutionMetadata with an error object whose type is NOT_FOUND,
    didDocumentMetadata an empty map. The error object follows RFC 9457
    (section 11): type is a URL, title and detail are strings. The register
    hint survives nested under the resolution metadata: DID Resolution allows
    additional properties there, and the hint is the one thing this 404 says
    that a caller can act on.
    """
    r = await async_client.get("/identity/resolve/did:moltrust:0000000000000000")
    assert r.status_code == 404, r.text
    body = r.json()
    # `detail` is the deprecated field, see the test below.
    assert set(body) == {"didDocument", "didResolutionMetadata",
                         "didDocumentMetadata", "detail"}
    assert body["didDocument"] is None
    assert body["didDocumentMetadata"] == {}
    error = body["didResolutionMetadata"]["error"]
    assert isinstance(error, dict), f"error is not an object: {error!r}"
    assert error["type"] == NOT_FOUND
    assert error["type"].startswith("https://")
    assert isinstance(error["title"], str) and error["title"]
    assert isinstance(error["detail"], str) and error["detail"]
    # The string of #741 must not come back in any member.
    assert "notFound" not in r.text
    hint = body["didResolutionMetadata"]["moltrust"]
    assert hint["register_hint"]["via"] == "resolve-404"
    assert hint["register_hint"]["steps"][0]["path"] == "/identity/register-pop"


async def test_a_miss_carries_both_forms_until_the_removal_date(async_client):
    """The new form and the old code in one body, until 2027-01-10.

    Until 2026-10-12 a miss said detail.error = "did_not_found". The path is
    public and we cannot see who reads that field, so it stays next to the
    DID resolution result for 90 days. Both must hold in the same response:
    a client that reads either one gets the same answer.

    REMOVE ON 2027-01-10 together with the `detail` key in
    app.main._resolve_miss_body.
    """
    r = await async_client.get("/identity/resolve/did:moltrust:0000000000000000")
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["didResolutionMetadata"]["error"]["type"] == NOT_FOUND
    assert body["detail"] == {"error": "did_not_found"}


def test_the_removal_date_is_ninety_days_after_the_change():
    """The date in the code is the one the changelog promises."""
    import datetime as dt
    from app.main import RESOLVE_MISS_LEGACY_UNTIL
    until = dt.date.fromisoformat(RESOLVE_MISS_LEGACY_UNTIL)
    assert until - dt.date(2026, 10, 12) == dt.timedelta(days=90)


async def test_a_registered_did_still_answers_with_the_document(async_client):
    """Unchanged: a hit returns the DID document itself, not an envelope.

    Only the miss changes shape here. Wrapping the 200 as well would be a
    different endpoint, and the Universal Resolver driver reads this one.
    """
    r = await async_client.get("/identity/resolve/did:web:api.moltrust.ch")
    assert r.status_code == 200
    assert r.json()["id"] == "did:web:api.moltrust.ch"
    assert "didResolutionMetadata" not in r.json()
