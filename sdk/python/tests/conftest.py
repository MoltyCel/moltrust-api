"""Gemeinsame Bausteine der SDK-Tests.

Der Fake-Server ist kein handgeschriebenes Fixture-JSON, sondern der echte Kern hinter einem
httpx.MockTransport — er antwortet genau so, wie der Endpunkt es tut (Core und Digest unter
`record`). Damit testen die Client-Tests das Zusammenspiel und nicht meine Vorstellung davon.
"""
import json

import httpx
import pytest

from moltrust_enforce import action_digest, enforce_check

ADDR = "0xABCDEF0123456789ABCDEF0123456789ABCDEF01"
ADDR_VANITY = "0xABCDEF0123456789ABCDEF0123456789ABCDEFff"
PAY = {"verb": "transfer", "asset": "USDC", "chain": "base"}
PAY_FIELDS = ["verb", "asset", "chain"]


def tx(**over):
    t = {"action": dict(PAY), "to": ADDR, "amount": 500, "region": "CH"}
    t.update(over)
    return t


_UNSET = object()  # „nicht uebergeben" — unterscheidbar von einem uebergebenen None


def grant(disposition="allow", constraints=None, action=None, type_fields=_UNSET):
    act = action if action is not None else PAY
    if type_fields is _UNSET:
        type_fields = list(act) if isinstance(act, dict) else []
    return {"action_binding": action_digest(act),
            "disposition": disposition,
            "type_fields": type_fields,
            "constraints": constraints if constraints is not None else []}


def mandate(*grants):
    return {"mandate_version": "1.0", "grants": list(grants)}


def as_endpoint_payload(result: dict) -> dict:
    """Der Kern gibt core/core_digest flach zurueck, der Endpunkt verschachtelt sie
    unter `record`. Das SDK muss die Endpunkt-Form lesen."""
    return {
        "verdict": result["verdict"],
        "reason": result["reason"],
        "grant_index": result["grant_index"],
        "trace": result["trace"],
        "record": {"core": result["core"], "core_digest": result["core_digest"]},
    }


def core_transport(mutate=None):
    """MockTransport, der /enforce/check mit dem echten Kern beantwortet.

    `mutate(payload) -> payload` haengt sich dazwischen und faelscht die Antwort — so
    entstehen die MISMATCH-Faelle ohne einen boesartigen Server.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/enforce/check"
        body = json.loads(request.content)
        result = enforce_check(body.get("mandate"), body.get("transaction"),
                               prev_core_digest=body.get("prev_core_digest"))
        payload = as_endpoint_payload(result)
        if mutate is not None:
            payload = mutate(payload)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def status_transport(status: int, text: str = "nope"):
    return httpx.MockTransport(lambda _r: httpx.Response(status, text=text))


def broken_transport(exc: Exception):
    def handler(_request):
        raise exc
    return httpx.MockTransport(handler)


@pytest.fixture
def client_factory():
    """Baut einen Client gegen einen gegebenen Transport."""
    from moltrust_enforce import EnforceClient

    made = []

    def _make(transport, **kw):
        kw.setdefault("api_key", "mt_test_key")
        c = EnforceClient("https://enforce.test", transport=transport, **kw)
        made.append(c)
        return c

    yield _make
    for c in made:
        c.close()
