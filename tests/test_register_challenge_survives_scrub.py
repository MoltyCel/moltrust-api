"""A register-pop challenge reaches the caller intact (ops REG-CHALLENGE).

POST /identity/register-pop refused a challenge fetched a moment earlier with
"invalid challenge: bad challenge signature" — one of three parallel
registrations on 06.11.2026. Not a second worker and not the secret: the
response of GET /identity/register-challenge passes scrub_secrets() like every
JSON response, and its 40-character AWS pattern matched inside the base64url
HMAC tag about 1.9 % of the time. The caller got `<rand>.<exp>.<...>[REDACTED]`,
signed it faithfully, and the server could not recompute the tag.

The tag is hex now; hex cannot form a 40-character run bounded by non-base64
characters, and it has no "sk-" either. Same lesson as
tests/test_session_tokens_survive_scrub.py.
"""
import base64
import hashlib
import hmac

from fastapi.testclient import TestClient

import app.keyless_register as kr
import app.main as m
from app.main import scrub_secrets

ROUNDS = 5000  # at the old rate about 94 redacted challenges, never zero


def test_a_challenge_is_never_redacted_and_still_verifies():
    for _ in range(ROUNDS):
        ch = kr.make_challenge()["challenge"]
        out = scrub_secrets({"challenge": ch})["challenge"]
        assert out == ch
        assert kr.verify_challenge(out) == (True, "")


def test_the_old_tag_encoding_was_redacted_sometimes():
    """The negative case: without it the test above would pass against a
    scrubber that redacts nothing."""
    hits = 0
    for _ in range(ROUNDS):
        body = kr.make_challenge()["challenge"].rsplit(".", 1)[0]
        raw = hmac.new(kr._secret(), body.encode("ascii"), hashlib.sha256).digest()
        old = f"{body}." + base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        hits += scrub_secrets({"challenge": old})["challenge"] != old
    assert hits > 0


def test_the_route_hands_out_challenges_that_verify(monkeypatch):
    """Through the HTTP middleware chain, where the redaction happened."""
    monkeypatch.setattr(m.limiter, "enabled", False)
    client = TestClient(m.app)
    for _ in range(300):
        r = client.get("/identity/register-challenge")
        assert r.status_code == 200
        doc = r.json()
        assert "[REDACTED]" not in doc["challenge"]
        assert kr.verify_challenge(doc["challenge"]) == (True, "")
        assert doc["pow"]["seed"] == kr.pow_seed(doc["challenge"])
