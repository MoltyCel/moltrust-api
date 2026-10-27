"""/prediction/integrity/{market_id} is the evidence document behind moltrust:prediction
ERC-8004 feedback (ops MG-URI). A reader fetches it and recomputes feedbackHash,
so the route must answer without payment and return the same bytes for the same
data. The database is replaced by a stub; nothing here leaves the process.
"""
import hashlib
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import erc8004_reputation as rep
from app import prediction_integrity as pi

ROWS = [
    {"address": "0xbbb", "prediction_score": 70, "linked_did": None, "position": "yes",
     "amount_in": Decimal("12.5")},
    {"address": "0xaaa", "prediction_score": 90, "linked_did": "did:moltrust:0123456789abcdef",
     "position": "no", "amount_in": None},
]


def _client() -> TestClient:
    # No `with`: the lifespan opens the database pool, and fetch_rows is stubbed.
    return TestClient(main.app)


@pytest.fixture()
def rows(monkeypatch):
    seen = []

    async def fake(market_id):
        seen.append(market_id)
        return [dict(r) for r in ROWS]

    monkeypatch.setattr(pi, "fetch_rows", fake)
    return seen


def test_route_answers_free_and_without_fetch_time(rows):
    r = _client().get("/prediction/integrity/m-1")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    doc = r.json()
    assert rows == ["m-1"]
    assert doc["marketId"] == "m-1"
    assert doc["trackedWallets"] == 2
    assert doc["verifiedWallets"] == 1
    assert doc["averagePredictionScore"] == 80
    assert doc["positionBreakdown"] == {"yes": 1, "no": 1}
    assert doc["herdingDetected"] is False
    assert doc["topParticipants"][0]["amountIn"] == 12.5
    assert "_meta" not in doc and "timestamp" not in json.dumps(doc).lower()


def test_two_fetches_hash_the_same(rows):
    a = _client().get("/prediction/integrity/m-1").content
    b = _client().get("/prediction/integrity/m-1").content
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()


def test_body_is_canonical_json(rows):
    body = _client().get("/prediction/integrity/m-1").content
    assert body == pi.serialize(json.loads(body))


def test_adapter_hash_matches_served_bytes(rows, monkeypatch):
    body = _client().get("/prediction/integrity/m-1").content

    class Resp:
        content = body

        def raise_for_status(self):
            pass

    monkeypatch.setattr(rep.httpx, "get", lambda uri, **k: Resp())
    fb = rep.build_feedback("prediction", "m-1", 1, 100)
    assert fb["feedbackURI"].endswith("/prediction/integrity/m-1")
    assert fb["feedbackHash"] == hashlib.sha256(body).digest()


def test_database_error_is_503_not_an_empty_document(monkeypatch):
    async def broken(market_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(pi, "fetch_rows", broken)
    r = _client().get("/prediction/integrity/m-1")
    assert r.status_code == 503


def test_unknown_market_is_an_empty_document(monkeypatch):
    async def none(market_id):
        return []

    monkeypatch.setattr(pi, "fetch_rows", none)
    doc = _client().get("/prediction/integrity/nope").json()
    assert doc["trackedWallets"] == 0 and doc["topParticipants"] == []


def test_herding_needs_more_than_five_wallets_on_one_side():
    many = [{"address": f"0x{i}", "prediction_score": 50, "linked_did": None,
             "position": "yes", "amount_in": None} for i in range(6)]
    assert pi.build_document("m", many)["herdingDetected"] is True
    assert pi.build_document("m", many[:5])["herdingDetected"] is False
    mixed = many[:4] + [dict(many[0], address="0xn1", position="no"),
                        dict(many[0], address="0xn2", position="no")]
    assert pi.build_document("m", mixed)["herdingDetected"] is False


def test_query_reads_one_platform_with_a_total_order():
    q = " ".join(pi._QUERY.split())
    assert "pw.platform = $2" in q
    assert "ORDER BY pw.prediction_score DESC, pw.address ASC" in q
