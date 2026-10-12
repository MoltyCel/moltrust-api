"""Issue #743: an unreachable upstream is UNAVAILABLE, an answered empty lookup NONE.

Runs find_existing_agent_id against a fake Blockscout and a fake ownerOf, then
checks that no call site collapses the two outcomes.
"""
import asyncio
import pathlib
import re

import pytest

from app import erc8004
from app.erc8004 import AutolinkStatus, find_existing_agent_id

WALLET = "0x" + "ab" * 20
ROOT = pathlib.Path(__file__).resolve().parent.parent


class FakeResponse:
    def __init__(self, status, body=None, bad_json=False):
        self.status_code = status
        self._body = body if body is not None else {}
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._body


def fake_client(response=None, exc=None):
    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            if exc is not None:
                raise exc
            return response
    return Client


def run(monkeypatch, client, owner=None, owner_exc=None):
    monkeypatch.setattr(erc8004.httpx, "AsyncClient", client)

    class Fn:
        def __init__(self, aid):
            self.aid = aid

        def call(self):
            if owner_exc:
                raise owner_exc
            return owner

    class Contract:
        class functions:
            @staticmethod
            def ownerOf(aid):
                return Fn(aid)

    monkeypatch.setattr(erc8004, "get_identity_contract", lambda: Contract())
    return asyncio.run(find_existing_agent_id(WALLET, budget=2.0))


MINT = {"items": [{"total": {"token_id": "21023"}, "to": {"hash": WALLET}}]}


@pytest.mark.parametrize("status", [403, 429, 500, 502, 503])
def test_upstream_status_without_result_is_unavailable(monkeypatch, status):
    r = run(monkeypatch, fake_client(FakeResponse(status)))
    assert r.status == AutolinkStatus.UNAVAILABLE
    assert f"HTTP {status}" in r.reason


def test_empty_200_is_none(monkeypatch):
    r = run(monkeypatch, fake_client(FakeResponse(200, {"items": []})))
    assert r.status == AutolinkStatus.NONE and r.agent_id is None


def test_connection_error_and_unreadable_body_are_unavailable(monkeypatch):
    import httpx
    assert run(monkeypatch, fake_client(exc=httpx.ConnectError("x"))).status == AutolinkStatus.UNAVAILABLE
    assert run(monkeypatch, fake_client(FakeResponse(200, bad_json=True))).status == AutolinkStatus.UNAVAILABLE


def test_owned_mint_is_found(monkeypatch):
    r = run(monkeypatch, fake_client(FakeResponse(200, MINT)), owner=WALLET)
    assert r.status == AutolinkStatus.FOUND and r.agent_id == 21023


def test_transferred_away_is_none(monkeypatch):
    r = run(monkeypatch, fake_client(FakeResponse(200, MINT)), owner="0x" + "cd" * 20)
    assert r.status == AutolinkStatus.NONE


def test_owner_of_failure_is_unavailable(monkeypatch):
    r = run(monkeypatch, fake_client(FakeResponse(200, MINT)), owner_exc=RuntimeError("rpc"))
    assert r.status == AutolinkStatus.UNAVAILABLE and "ownerOf" in r.reason


def test_no_call_site_collapses_the_two():
    """Every caller branches on the status; none tests the result for None/falsy."""
    for p in list((ROOT / "app").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")) \
            + list((ROOT / "agents").rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r"(\w+)\s*=\s*await\s+find_existing_agent_id\(", text):
            name = m.group(1)
            rest = text[m.end():m.end() + 3000]
            assert not re.search(rf"\b{name}\s+is\s+(not\s+)?None\b|\bif\s+(not\s+)?{name}\s*:", rest), p
            assert f"{name}.status" in rest, p
            assert "AutolinkStatus.NONE" in rest and "UNAVAILABLE" in rest, p


def test_the_bind_logs_unavailable_with_its_reason():
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert 'logger.warning("erc8004 autolink: %s UNAVAILABLE (%s)", body.did, lookup.reason)' in main
