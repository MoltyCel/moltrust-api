"""moltrust_enforce.canary: offline check of leak events, presentation of tokens.

The events here are built with the SDK's own primitives and a key made for this
test, the way the server builds them (app/leak_canary.py build_aer/sign_event).
The server suite tests/test_leak_canary_token.py runs the same check on events
the server wrote.
"""
import base64
import hashlib
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import moltrust_enforce as me
from moltrust_enforce import canary

KID = "test-registry-key"
SK = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUB = SK.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
TRUST = {"trust_list_version": 1,
         "sources": {canary.SOURCE_ID: {"keys": [{"algorithm": "ed25519", "keyid": KID,
                                                  "public_key": base64.b64encode(PUB).decode()}]}}}
ISSUER = "did:moltrust:" + "a" * 16
COUNTERPARTY = "did:moltrust:" + "b" * 16
EVENT_ID = "00000000-0000-4000-8000-000000000001"
OBSERVED = "2026-10-20T12:00:00Z"
UNTIL = "2026-11-19T12:00:00Z"


def _aer(ref, attribution):
    query = {"kind": canary.QUERY_KIND, "canary_ref": ref, "counterparty_did": COUNTERPARTY,
             "attribution": attribution}
    statement = me.make_statement(canary.SOURCE_ID, query, True, OBSERVED, UNTIL, EVENT_ID)
    sig = SK.sign(me.pae(me.PAYLOAD_TYPE, me.evidence_payload_bytes(statement)))
    envelope = me.make_envelope(statement, [{"keyid": KID, "sig": base64.b64encode(sig).decode()}])
    action = {"verb": canary.HOLD_VERB}
    mandate = {"grants": [{"action_binding": me.action_digest(action), "disposition": "allow",
                           "type_fields": ["verb"],
                           "constraints": [{"type": "evidence_bool", "query": query,
                                            "expect": True}]}]}
    transaction = {"action": action, "counterparty_did": COUNTERPARTY, "event_id": EVENT_ID}
    bundle = me.build_bundle([envelope], mandate, transaction, OBSERVED)
    return {"mandate": mandate, "transaction": transaction, "bundle": bundle,
            "record": me.f_ext(mandate, transaction, bundle)}


def _sign(event):
    body = {k: v for k, v in event.items() if k != "signature"}
    sig = SK.sign(canary.event_signing_input(body))
    return {**body, "signature": {"alg": "Ed25519", "kid": KID, "sig": sig.hex()}}


def _event(trigger="token_presentation", attribution="open", holder_proof=None):
    ref = "sha256:" + "c" * 64
    return _sign({
        "event_version": 1, "event_id": EVENT_ID, "canary_id": "11111111-1111-4111-8111-111111111111",
        "issuer_did": ISSUER, "counterparty_did": COUNTERPARTY, "mode": "bound",
        "trigger": trigger, "observed_at": OBSERVED, "presenter_did": None,
        "credential_valid": True, "canary_ref": ref,
        "presentation": {"api_key_did": None, "holder_proof": holder_proof},
        "attribution": attribution, "aer": _aer(ref, attribution),
    })


def _holder_proof(path, did=COUNTERPARTY):
    holder = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
    pub = holder.public_key().public_bytes(serialization.Encoding.Raw,
                                           serialization.PublicFormat.Raw)
    body = b'{"token": "x.y.z"}'
    digest = "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"
    base = "\n".join(['"@method": POST', f'"@path": {path}', f'"x-moltrust-did": {did}',
                      f'"content-digest": {digest}', '"@signature-params": ()'])
    return {"scheme": "rfc9421", "did": did, "valid": True, "reason": None, "subject_match": True,
            "keyid": f"{did}#key-1",
            "record": {"public_key_hex": pub.hex(),
                       "signature_hex": holder.sign(base.encode()).hex(),
                       "signature_base_hex": base.encode().hex(),
                       "request_body_hex": body.hex()}}


# ---------------------------------------------------------------- offline

@pytest.mark.parametrize("trigger", sorted(canary.TRIGGER_PATHS))
def test_an_event_from_either_trigger_passes(trigger):
    report = canary.check(_event(trigger), trust_list=TRUST)
    assert report.ok, report.failures
    assert report.trigger == trigger and report.recomputed_verdict == me.PERMIT
    assert set(_event(trigger)) == canary.EVENT_FIELDS


def test_events_of_both_triggers_differ_only_in_trigger_and_signature():
    a, b = _event("verifier_presentation"), _event("token_presentation")
    assert {k for k in a if a[k] != b[k]} == {"trigger", "signature"}


@pytest.mark.parametrize("change", [
    {"counterparty_did": "did:moltrust:" + "d" * 16},
    {"trigger": "verifier_presentation"},
    {"attribution": "counterparty"},
    {"credential_valid": False},
])
def test_a_changed_member_fails_the_signature(change):
    report = canary.check({**_event(), **change}, trust_list=TRUST)
    assert not report.ok
    assert "event signature does not verify" in report.failures


def test_a_resigned_event_with_foreign_evidence_fails_the_binding():
    event = _event()
    other = _aer("sha256:" + "e" * 64, "open")
    forged = _sign({**event, "aer": other})
    report = canary.check(forged, trust_list=TRUST)
    assert not report.ok
    assert any("names another canary" in f for f in report.failures)


def test_an_unknown_trigger_or_missing_member_fails():
    assert not canary.check(_sign({**_event(), "trigger": "elsewhere"}), trust_list=TRUST).ok
    event = _event()
    del event["presentation"]
    report = canary.check(event, trust_list=TRUST)
    assert not report.ok and "missing ['presentation']" in report.failures[0]


def test_a_key_outside_the_trust_list_fails():
    other = {"trust_list_version": 1, "sources": {canary.SOURCE_ID: {"keys": [{
        "algorithm": "ed25519", "keyid": KID,
        "public_key": base64.b64encode(b"\x01" * 32).decode()}]}}}
    report = canary.check(_event(), trust_list=other)
    assert not report.ok


def test_attribution_counterparty_needs_the_holder_proof_for_its_path():
    for trigger, path in canary.TRIGGER_PATHS.items():
        good = _event(trigger, "counterparty", _holder_proof(path))
        assert canary.check(good, trust_list=TRUST).ok, trigger
        wrong_path = _event(trigger, "counterparty", _holder_proof("/other"))
        assert not canary.check(wrong_path, trust_list=TRUST).ok
        foreign = _event(trigger, "counterparty", _holder_proof(path, ISSUER))
        assert not canary.check(foreign, trust_list=TRUST).ok
    assert not canary.check(_event("token_presentation", "counterparty", None),
                            trust_list=TRUST).ok


def test_check_dispatches_and_refuses_other_input():
    with pytest.raises(TypeError):
        canary.check(_event(), trust_list=TRUST, api_key="k")
    with pytest.raises(TypeError):
        canary.check(42)


# ----------------------------------------------------------------- online

def _transport(seen, status=200, payload=None):
    def handler(request):
        seen.append(request)
        body = payload if payload is not None else {
            "valid": True, "checks": {"signature": True}, "errors": [],
            "delegator": ISSUER, "audience": COUNTERPARTY,
            "presentation": {"api_key_did": None, "holder_proof": None}}
        return httpx.Response(status, json=body)
    return httpx.MockTransport(handler)


def test_check_presents_the_token_at_the_token_endpoint():
    seen = []
    result = canary.check("a.b.c", base_url="https://api.example/", api_key="k1",
                          expected_audience=COUNTERPARTY, transport=_transport(seen))
    assert result.valid is True and result.from_server is True
    assert result.presentation == {"api_key_did": None, "holder_proof": None}
    (req,) = seen
    assert req.method == "POST" and req.url.path == canary.TOKEN_PATH
    assert req.headers["x-api-key"] == "k1"
    assert json.loads(req.content) == {"token": "a.b.c", "expected_audience": COUNTERPARTY}


def test_check_forwards_a_holder_signed_request_unchanged():
    seen = []
    body = b'{"token": "a.b.c"}'
    headers = {"Signature-Input": "sig1=()", "Signature": "sig1=:AA==:",
               "Content-Digest": "sha-256=:x:", "X-MolTrust-DID": COUNTERPARTY}
    canary.check("a.b.c", base_url="https://api.example", api_key="k1",
                 signed_request=(body, headers), transport=_transport(seen))
    (req,) = seen
    assert req.content == body
    assert req.headers["signature"] == "sig1=:AA==:" and req.headers["x-api-key"] == "k1"
    with pytest.raises(ValueError):
        canary.check("other.token.here", base_url="https://api.example", api_key="k1",
                     signed_request=(body, headers), transport=_transport([]))


@pytest.mark.parametrize("status,payload", [(500, {"detail": "x"}), (401, {"detail": "x"}),
                                            (200, {"checks": {}}), (200, ["valid"])])
def test_check_is_fail_closed(status, payload):
    result = canary.check("a.b.c", base_url="https://api.example", api_key="k1",
                          transport=_transport([], status, payload))
    assert result.valid is False and result.from_server is False and not result


def test_check_turns_a_transport_failure_into_invalid():
    def boom(request):
        raise httpx.ConnectError("down", request=request)
    result = canary.check("a.b.c", base_url="https://api.example", api_key="k1",
                          transport=httpx.MockTransport(boom))
    assert result.valid is False and result.from_server is False
    assert result.errors == ("transport failure: ConnectError",)


def test_importing_canary_loads_no_http_client():
    import subprocess
    import sys
    code = "import sys, moltrust_enforce.canary; print('httpx' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
