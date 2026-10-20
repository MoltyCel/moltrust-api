"""The keyless path has to say where the key comes from.

register-pop hands out a DID and no API key. The two endpoints an agent
reaches for next both require one, and until 2026-10-04 nothing in either
response said so: an agent registered, failed /identity/bind, guessed at
/auth/issue-key and /auth/api-key, and gave up after nine 403s.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_challenge_names_the_key_step():
    body = client.get("/identity/register-challenge").json()
    assert "after_registration" in body
    assert "/auth/signup-did" in body["after_registration"]


def test_missing_api_key_is_401_not_422():
    """A missing header is an auth problem. 422 sends the caller into its body."""
    r = client.post("/identity/bind", json={})
    assert r.status_code == 401, r.text
    assert "/auth/signup-did" in r.json()["detail"]


def test_bad_api_key_still_403(monkeypatch):
    """Unchanged: a key that is present but unknown stays 403.

    Without a database pool, as in the onboarding-routes job (--noconftest, no
    startup). In the whole suite the app's pool is one an earlier async suite
    created on its own event loop; a present key makes the middleware use it,
    and from TestClient's loop that ends in "Event loop is closed" and a 500
    before verify_api_key is reached. The key check itself needs no database.
    """
    import app.main as main
    monkeypatch.setattr(main, "db_pool", None)
    r = client.post("/identity/bind", headers={"X-API-Key": "nope"}, json={})
    assert r.status_code == 403, r.text
