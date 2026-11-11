"""The /a2a discovery hint names the join link (ops A2A-INVITE, 30.10.2026).

The hint takes the invite from the served card, so a crawler that reads /a2a
and one that reads the card cannot be told two different join links.
"""
import json
import pathlib

from fastapi.testclient import TestClient

import app.main as main

CARD = pathlib.Path(__file__).resolve().parents[1] / ".well-known" / "agent-card.json"


def _client() -> TestClient:
    # No `with`: the lifespan opens the database pool; this route needs none.
    return TestClient(main.app)


def _serve_card(monkeypatch, card: dict) -> None:
    monkeypatch.setattr(main, "_AGENT_CARD_CACHE", card, raising=False)


class TestA2aInviteHint:
    def test_the_hint_carries_the_invite_of_the_served_card(self, monkeypatch):
        card = json.loads(CARD.read_text())
        _serve_card(monkeypatch, card)
        r = _client().get("/a2a")
        assert r.status_code == 200
        body = r.json()
        assert body["invite"] == card["moltrust"]["invite"]
        # The point of the criterion: the join link is in the text.
        assert "join?ref=" in body["invite"]["join_url"]
        assert body["invite"]["referrer_did"] in body["invite"]["join_url"]

    def test_a_card_without_the_field_leaves_the_hint_as_it_was(self, monkeypatch):
        _serve_card(monkeypatch, {"name": "MolTrust", "version": "1.0.1"})
        r = _client().get("/a2a")
        assert r.status_code == 200
        body = r.json()
        assert "invite" not in body
        assert body["transport"] == "jsonrpc-2.0"
        assert body["agentCard"].endswith("/.well-known/agent-card.json")

    def test_an_unreadable_card_does_not_break_the_route(self, monkeypatch):
        def boom():
            raise OSError("no card here")

        monkeypatch.setattr(main, "_load_public_agent_card", boom)
        r = _client().get("/a2a")
        assert r.status_code == 200
        assert "invite" not in r.json()
