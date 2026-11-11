"""The revocation lookup of AAE -02 §2.4 / §5 step 8 (ops EGRESS).

No database: _eval_revocation_check is called directly, with app.egress.afetch
replaced. The end-to-end case with a stored envelope is in test_evaluator.py.
"""
import json

import pytest

from app import egress
from app.enforcement import evaluator
from app.enforcement.evaluator import _eval_revocation_check

TEMPLATE = "https://status.example/aae/revocation/{id}"


@pytest.fixture
def lookup(monkeypatch):
    monkeypatch.setattr(evaluator, "REVOCATION_FETCH", True)
    calls = []

    def install(answer):
        async def afetch(url, **kwargs):
            calls.append(url)
            if isinstance(answer, Exception):
                raise answer
            status, body = answer
            return egress.Response(status=status, body=body, url=url)
        monkeypatch.setattr(evaluator.egress, "afetch", afetch)
        return calls
    return install


def body(**doc):
    return json.dumps(doc).encode()


async def test_switched_off_denies_without_a_lookup(monkeypatch):
    monkeypatch.setattr(evaluator, "REVOCATION_FETCH", False)

    async def afetch(*a, **k):
        pytest.fail("looked up while switched off")
    monkeypatch.setattr(evaluator.egress, "afetch", afetch)
    v = await _eval_revocation_check({"revocation_check": TEMPLATE}, "urn:uuid:1")
    assert v["verdict"] == "DENY" and "not yet enforceable" in v["reason"]


def test_the_switch_ships_off():
    assert evaluator.REVOCATION_FETCH is False


async def test_no_revocation_check_means_no_verdict(lookup):
    calls = lookup((200, body(id="x", revoked=False)))
    assert await _eval_revocation_check({}, "urn:uuid:1") is None
    assert calls == []


async def test_not_revoked_allows(lookup):
    calls = lookup((200, body(id="urn:uuid:1", revoked=False)))
    v = await _eval_revocation_check({"revocation_check": TEMPLATE}, "urn:uuid:1")
    assert v["verdict"] == "ALLOW"
    assert calls == ["https://status.example/aae/revocation/urn%3Auuid%3A1"]


async def test_revoked_denies(lookup):
    lookup((200, body(id="urn:uuid:1", revoked=True)))
    v = await _eval_revocation_check({"revocation_check": TEMPLATE}, "urn:uuid:1")
    assert v["verdict"] == "DENY" and v["code"] == "revoked"


@pytest.mark.parametrize("answer", [
    (500, b""), (503, b"{}"), (404, body(id="x", revoked=False)),
    (200, b"not json"), (200, body(revoked=False)), (200, body(id="x")),
    (200, body(id="x", revoked="false")), (200, b"[]"),
    egress.EgressFailed("no address answered"), egress.EgressRefused("non-public address"),
])
async def test_every_way_of_not_knowing_denies(answer, lookup):
    lookup(answer)
    v = await _eval_revocation_check({"revocation_check": TEMPLATE}, "urn:uuid:1")
    assert v["verdict"] == "DENY" and "fail-closed" in v["reason"]


async def test_values_cannot_leave_their_segment(lookup):
    calls = lookup((200, body(id="x", revoked=False)))
    await _eval_revocation_check(
        {"revocation_check": "https://status.example/r/{id}?did={did}"},
        "a/../b?c#d", "did:web:x.example")
    assert calls == ["https://status.example/r/a%2F..%2Fb%3Fc%23d?did=did%3Aweb%3Ax.example"]


@pytest.mark.parametrize("template,subject", [
    ("https://status.example/r/{did}", None),
    ("https://status.example/r/{+id}", "did:x"),
    ("https://status.example/r/{host}", "did:x"),
    (["https://status.example/r/{id}"], "did:x"),
])
async def test_templates_it_cannot_expand_deny(template, subject, lookup):
    calls = lookup((200, body(id="x", revoked=False)))
    v = await _eval_revocation_check({"revocation_check": template}, "urn:uuid:1", subject)
    assert v["verdict"] == "DENY" and calls == []


async def test_the_lookup_goes_through_egress(monkeypatch):
    # With the real afetch: an internal target is refused by app/egress.py,
    # and the refusal is a DENY.
    monkeypatch.setattr(evaluator, "REVOCATION_FETCH", True)
    v = await _eval_revocation_check(
        {"revocation_check": "https://127.0.0.1/r/{id}"}, "urn:uuid:1")
    assert v["verdict"] == "DENY" and "not public" in v["reason"]
