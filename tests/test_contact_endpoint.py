"""Tests — POST /contact (contact-form intake).

Covers the three properties the endpoint has to hold:
  * a filled honeypot is indistinguishable from a genuine submission and
    stores nothing;
  * the server rejects what the browser rejects, plus the bounds the browser
    does not enforce;
  * a mail failure still leaves the enquiry in the table, flagged unsent.

No test in this module is allowed to send real mail: the autouse fixture below
stubs `aiosmtplib.send` for every one of them. SMTP_PASS *is* populated in this
environment (conftest sources ~/.moltrust_secrets), so without that stub a
genuine submission would put a real message in a real inbox.
"""
import uuid

import pytest
import pytest_asyncio

# Every row this module writes uses this domain, so cleanup is exact.
TEST_DOMAIN = "contact-test.local"


def _payload(**overrides):
    body = {
        "name": "Jane Doe",
        "email": f"jane-{uuid.uuid4().hex[:8]}@{TEST_DOMAIN}",
        "topic": "API & Integration",
        "message": "This is a genuine enquiry that is comfortably over twenty characters.",
    }
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def no_real_mail(monkeypatch):
    """Hard stop on outbound mail, and a record of what was attempted."""
    sent = []

    async def _fake_send(msg, **kwargs):
        sent.append(msg)
        return {}, "250 OK"

    monkeypatch.setattr("aiosmtplib.send", _fake_send)
    return sent


@pytest.fixture(autouse=True)
def no_rate_limit():
    """Disable slowapi for this module.

    Under ASGITransport `request.client.host` is the literal "testclient" for
    every request, so all of them share one rate-limit key and the sixth would
    get a 429 regardless of which test issued it. The limit itself is covered
    by `test_rate_limit_blocks_the_sixth_submission`, which re-enables it.
    """
    from app.main import limiter

    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


@pytest_asyncio.fixture
async def inbox(app_with_lifespan):
    """Cleaned-up access to contact_inbox."""
    from app.main import db_pool

    async def _rows():
        async with db_pool.acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM contact_inbox WHERE email LIKE $1 ORDER BY received_at",
                f"%@{TEST_DOMAIN}",
            )

    async def _clean():
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM contact_inbox WHERE email LIKE $1", f"%@{TEST_DOMAIN}"
            )

    await _clean()
    yield _rows
    await _clean()


# --- honeypot ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_honeypot_response_is_identical_to_a_genuine_one(async_client, inbox):
    genuine = await async_client.post("/contact", json=_payload())
    trapped = await async_client.post("/contact", json=_payload(website="http://spam.example"))

    assert genuine.status_code == 200
    assert trapped.status_code == genuine.status_code
    assert trapped.json() == genuine.json()
    # Byte-for-byte, not just structurally equal.
    assert trapped.content == genuine.content


@pytest.mark.asyncio
async def test_honeypot_stores_nothing(async_client, inbox):
    resp = await async_client.post("/contact", json=_payload(website="x"))
    assert resp.status_code == 200
    assert await inbox() == []


@pytest.mark.asyncio
async def test_honeypot_sends_no_mail(async_client, inbox, no_real_mail):
    await async_client.post("/contact", json=_payload(website="buy-followers"))
    assert no_real_mail == []


@pytest.mark.asyncio
async def test_honeypot_is_never_named_in_the_response(async_client, inbox):
    resp = await async_client.post("/contact", json=_payload(website="x"))
    assert "website" not in resp.text
    assert "honeypot" not in resp.text.lower()
    assert "spam" not in resp.text.lower()


# --- genuine path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_genuine_submission_is_stored_and_mailed(async_client, inbox, no_real_mail):
    body = _payload()
    resp = await async_client.post("/contact", json=body)
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"

    rows = await inbox()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == body["name"]
    assert row["email"] == body["email"]
    assert row["topic"] == body["topic"]
    assert row["message"] == body["message"]
    assert row["mail_sent"] is True
    assert row["is_spam"] is False
    assert row["received_at"] is not None

    assert len(no_real_mail) == 1
    msg = no_real_mail[0]
    assert msg["To"] == "kersten.kroehl@cryptokri.ch"
    assert msg["Reply-To"] == body["email"]


@pytest.mark.asyncio
async def test_stored_ip_is_anonymised(async_client, inbox):
    """Whatever lands in `ip`, it is the output of _anonymize_ip, never a raw host."""
    from app.main import _anonymize_ip

    await async_client.post("/contact", json=_payload())
    row = (await inbox())[0]
    assert row["ip"] == _anonymize_ip(row["ip"]), "ip column holds a non-anonymised value"


# --- mail failure -----------------------------------------------------------


@pytest.mark.asyncio
async def test_mail_failure_still_persists_the_row(async_client, inbox, monkeypatch):
    async def _boom(msg, **kwargs):
        raise OSError("smtp unreachable")

    monkeypatch.setattr("aiosmtplib.send", _boom)

    body = _payload()
    resp = await async_client.post("/contact", json=body)

    # The visitor is not punished for our SMTP problem.
    assert resp.status_code == 200

    rows = await inbox()
    assert len(rows) == 1, "a failing mail must not discard the enquiry"
    assert rows[0]["message"] == body["message"]
    # ...and the row is queryable as "nobody was told about this one".
    assert rows[0]["mail_sent"] is False


@pytest.mark.asyncio
async def test_mail_failure_does_not_leak_the_reason(async_client, inbox, monkeypatch):
    async def _boom(msg, **kwargs):
        raise OSError("smtp auth failed for hunter2")

    monkeypatch.setattr("aiosmtplib.send", _boom)
    resp = await async_client.post("/contact", json=_payload())
    assert "hunter2" not in resp.text
    assert "smtp" not in resp.text.lower()


# --- validation -------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides, why",
    [
        ({"message": "too short"}, "message under 20 characters"),
        ({"message": "   " + "x" * 5 + "   "}, "message under 20 after stripping"),
        ({"message": "x" * 5001}, "message over the 5000-character cap"),
        ({"email": "not-an-email"}, "malformed email"),
        ({"email": "a@b"}, "email with no TLD"),
        ({"name": ""}, "empty name"),
        ({"name": "   "}, "whitespace-only name"),
        ({"name": "x" * 121}, "name over the cap"),
        ({"topic": ""}, "empty topic"),
        ({"topic": "x" * 65}, "topic over the cap"),
        ({"name": "Jane\r\nBcc: victim@example.com"}, "header injection via name"),
        ({"topic": "Press\nSubject: spam"}, "header injection via topic"),
    ],
)
@pytest.mark.asyncio
async def test_validation_rejects(async_client, inbox, overrides, why):
    resp = await async_client.post("/contact", json=_payload(**overrides))
    assert resp.status_code == 422, why
    assert await inbox() == [], f"rejected submission was stored: {why}"


@pytest.mark.asyncio
async def test_missing_field_is_rejected(async_client, inbox):
    body = _payload()
    del body["topic"]
    resp = await async_client.post("/contact", json=body)
    assert resp.status_code == 422
    assert await inbox() == []


@pytest.mark.asyncio
async def test_oversized_body_is_refused_before_the_handler(async_client, inbox):
    """The global body-size middleware, not the field caps, catches this one."""
    from app.main import MAX_REQUEST_BODY_BYTES

    huge = _payload(message="x" * (MAX_REQUEST_BODY_BYTES + 1024))
    resp = await async_client.post("/contact", json=huge)
    assert resp.status_code == 413
    assert await inbox() == []


# --- rate limit -------------------------------------------------------------


def test_rate_limit_key_is_the_slash_24():
    from app.main import _contact_ratelimit_key

    class _Req:
        def __init__(self, peer):
            self.client = type("C", (), {"host": peer})()
            self.headers = {}

    assert _contact_ratelimit_key(_Req("203.0.113.77")) == "203.0.113.0"
    assert _contact_ratelimit_key(_Req("203.0.113.9")) == "203.0.113.0"
    # ...so two addresses in the same /24 share one budget.
    assert _contact_ratelimit_key(_Req("203.0.114.9")) == "203.0.114.0"


@pytest.mark.asyncio
async def test_rate_limit_blocks_the_sixth_submission(async_client, inbox):
    from app.main import limiter

    limiter.enabled = True
    try:
        limiter.reset()
        codes = [
            (await async_client.post("/contact", json=_payload())).status_code
            for _ in range(6)
        ]
    finally:
        limiter.reset()
        limiter.enabled = False

    assert codes[:5] == [200] * 5
    assert codes[5] == 429
    assert len(await inbox()) == 5, "the blocked submission must not have been stored"
