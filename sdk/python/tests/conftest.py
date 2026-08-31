"""Gemeinsame Bausteine der SDK-Tests.

Der Fake-Server ist kein handgeschriebenes Fixture-JSON, sondern der echte Kern hinter einem
httpx.MockTransport — er antwortet genau so, wie der Endpunkt es tut (Core und Digest unter
`record`). Damit testen die Client-Tests das Zusammenspiel und nicht meine Vorstellung davon.
"""
import base64
import hashlib
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from moltrust_enforce import action_digest, build_bundle, enforce_check, make_envelope
from moltrust_enforce import make_statement, pae, evidence_payload_bytes

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


# --- AER: Evidenz-Quellen, Items, Buendel ------------------------------------------
#
# Die Testquellen sind keine echte ESA, sondern das Minimum, das ein signiertes Item
# braucht. Ihre Schluessel entstehen deterministisch aus einem Namen — es liegt kein
# Schluesselmaterial in der Datei, und zwei Laeufe erzeugen dasselbe Buendel.

T0 = "2026-08-31T12:00:00Z"       # Entscheidungszeitpunkt der meisten Faelle
WINDOW = ("2026-08-31T11:00:00Z", "2026-08-31T13:00:00Z")

REVOKED_Q = {"kind": "revocation", "subject": "aae:0f3a"}
SANCTION_Q = {"kind": "sanction", "subject": ADDR}
JURIS_Q = {"kind": "jurisdiction", "subject": ADDR}
FX_Q = {"kind": "fx", "pair": "USDC/EUR"}


def source_key(name: str) -> Ed25519PrivateKey:
    """Deterministischer Testschluessel zu einem Quellnamen."""
    seed = hashlib.sha256(b"moltrust:aer-test-source:" + name.encode("utf-8")).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def source_id(name: str) -> str:
    return f"did:moltrust:test-{name}"


def public_key_b64(name: str) -> str:
    raw = source_key(name).public_key().public_bytes_raw()
    return base64.b64encode(raw).decode("ascii")


def trust_list(*names, version=1):
    return {"trust_list_version": version,
            "sources": {source_id(n): {"keys": [{"algorithm": "ed25519",
                                                 "keyid": f"key-{n}",
                                                 "public_key": public_key_b64(n)}]}
                        for n in names}}


def evidence_item(name, query, value, window=WINDOW, nonce="n-1", keyid=None,
                  sign_with=None, tamper=None):
    """Ein signiertes Evidenz-Item.

    `sign_with` laesst eine andere Quelle signieren (Fremdsignatur), `tamper` veraendert das
    Statement NACH dem Signieren — beides fuer die Negativfaelle von V2.
    """
    statement = make_statement(source_id(name), query, value, window[0], window[1], nonce)
    signer = source_key(sign_with if sign_with is not None else name)
    signature = signer.sign(pae(
        "application/vnd.moltrust.aer-evidence+json", evidence_payload_bytes(statement)))
    if tamper is not None:
        statement = tamper(dict(statement))
    return make_envelope(statement, [{"keyid": f"key-{keyid or name}",
                                      "sig": base64.b64encode(signature).decode("ascii")}])


def aer_bundle(items, mandate_obj, transaction_obj, decision_timestamp=T0):
    return build_bundle(items, mandate_obj, transaction_obj, decision_timestamp)


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
