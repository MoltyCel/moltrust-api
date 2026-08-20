"""Tests — SDK-Ratifikation: ratify() ueber HTTP und verify_ratification() lokal."""
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from conftest import ADDR, PAY, broken_transport, tx
from moltrust_enforce import (
    APPROVED, DISAPPROVED, DENY, PERMIT, RATIFIED, REJECTED,
    EnforceTransportError, action_digest, enforce_check, ratify,
    ratification_statement, statement_bytes,
)
from moltrust_enforce.client import Ratification

PRINCIPAL_DID = "did:moltrust:1111111111111111"
OFFICER_DID = "did:moltrust:2222222222222222"
STRANGER_DID = "did:moltrust:3333333333333333"


def _key():
    sk = Ed25519PrivateKey.generate()
    return sk, sk.public_key().public_bytes_raw().hex()


PRINCIPAL_SK, PRINCIPAL_PK = _key()
OFFICER_SK, OFFICER_PK = _key()
STRANGER_SK, STRANGER_PK = _key()

RANGE = {"type": "range", "field": "amount", "lo": 0, "hi": 100}


def mandate():
    return {
        "mandate_version": "1.0",
        "principal": {"did": PRINCIPAL_DID, "public_key": PRINCIPAL_PK},
        "ratification_authorities": [
            {"did": OFFICER_DID, "public_key": OFFICER_PK, "role": "compliance_officer"}],
        "grants": [{"action_binding": action_digest(PAY), "disposition": "allow",
                    "constraints": [RANGE]}],
    }


def deny_record(m=None):
    m = m or mandate()
    r = enforce_check(m, tx(amount=500))
    assert r["verdict"] == DENY
    return m, {"core": r["core"], "core_digest": r["core_digest"]}


def proof(m, did, sk, prior_digest, decision):
    return {"mandate": m, "authority": did,
            "signature": sk.sign(statement_bytes(prior_digest, decision, did)).hex()}


def ratify_transport(mutate=None):
    """MockTransport, der /enforce/ratify mit dem echten Kern beantwortet."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/enforce/ratify"
        b = json.loads(request.content)
        r = ratify(b["prior_record"], b["decision"], b.get("authority_proof"),
                   prev_core_digest=b.get("prev_core_digest"))
        payload = {"status": r["status"], "decision": r["decision"], "ratifies": r["ratifies"],
                   "authority": r["authority"], "reason": r["reason"], "trace": r["trace"],
                   "record": {"core": r["core"], "core_digest": r["core_digest"]}}
        if mutate is not None:
            payload = mutate(payload)
        return httpx.Response(200, json=payload)
    return httpx.MockTransport(handler)


# ------------------------------------------------------------------------- ratify()

def test_ratify_approved(client_factory):
    c = client_factory(ratify_transport())
    m, prior = deny_record()
    r = c.ratify(prior, APPROVED, proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED))
    assert r.status == RATIFIED and r.decision == APPROVED
    assert r.ratified is True and r.approved is True
    assert r.authority == OFFICER_DID
    assert r.ratifies == prior["core_digest"]
    assert r.from_server is True


def test_ratify_disapproved_is_ratified_but_not_approved(client_factory):
    c = client_factory(ratify_transport())
    m, prior = deny_record()
    r = c.ratify(prior, DISAPPROVED,
                 proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], DISAPPROVED))
    assert r.status == RATIFIED and r.ratified is True
    assert r.approved is False          # ratifiziert, aber als DISAPPROVED


def test_ratify_stranger_is_rejected(client_factory):
    c = client_factory(ratify_transport())
    m, prior = deny_record()
    r = c.ratify(prior, APPROVED, proof(m, STRANGER_DID, STRANGER_SK, prior["core_digest"], APPROVED))
    assert r.status == REJECTED and r.ratified is False and r.approved is False


# --------------------------------------------------------------------- ★ fail-closed

@pytest.mark.parametrize("transport", [
    broken_transport(httpx.ConnectError("refused")),
    broken_transport(httpx.ReadTimeout("timeout")),
])
def test_transport_failure_never_ratifies(client_factory, transport):
    c = client_factory(transport)
    _m, prior = deny_record()
    r = c.ratify(prior, APPROVED, {})
    assert r.status == REJECTED
    assert r.ratified is False and r.approved is False
    assert r.from_server is False
    assert "transport failure" in r.reason


@pytest.mark.parametrize("status", [401, 403, 422, 500, 503])
def test_error_status_never_ratifies(client_factory, status):
    c = client_factory(httpx.MockTransport(lambda _r: httpx.Response(status, text="no")))
    _m, prior = deny_record()
    r = c.ratify(prior, APPROVED, {})
    assert r.status == REJECTED and r.approved is False and r.from_server is False


def test_raise_mode_forces_handling(client_factory):
    c = client_factory(broken_transport(httpx.ConnectError("down")), on_transport_error="raise")
    _m, prior = deny_record()
    with pytest.raises(EnforceTransportError):
        c.ratify(prior, APPROVED, {})


@pytest.mark.parametrize("payload", [
    {"status": "OK", "decision": APPROVED},
    {"status": RATIFIED, "decision": "yes"},
    {"status": "PERMIT", "decision": APPROVED},
    {"decision": APPROVED},
    ["nope"],
])
def test_unknown_status_never_ratifies(client_factory, payload):
    c = client_factory(httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)))
    _m, prior = deny_record()
    r = c.ratify(prior, APPROVED, {})
    assert r.status == REJECTED and r.from_server is False


# ------------------------------------------------------------- verify_ratification()

def test_verify_accepts_an_honest_ratification(client_factory):
    c = client_factory(ratify_transport())
    m, prior = deny_record()
    p = proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    r = c.ratify(prior, APPROVED, p)
    res = c.verify_ratification(r, prior, m, authority_proof=p)
    assert res.ok is True, res.mismatches
    assert res.full_recompute is True


def test_verify_without_proof_is_structural_and_says_so(client_factory):
    c = client_factory(ratify_transport())
    m, prior = deny_record()
    p = proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    res = c.verify_ratification(c.ratify(prior, APPROVED, p), prior, m)
    assert res.ok is True
    assert res.full_recompute is False      # die Signatur wurde nicht nachgerechnet


def test_verify_catches_an_invented_authority(client_factory):
    """Der Server behauptet RATIFIED durch jemanden, den das Mandat nicht kennt."""
    def forge(payload):
        payload["status"] = RATIFIED
        payload["authority"] = STRANGER_DID
        payload["record"]["core"]["status"] = RATIFIED
        payload["record"]["core"]["authority"] = STRANGER_DID
        from moltrust_enforce.client import ratification_digest
        payload["record"]["core_digest"] = ratification_digest(payload["record"]["core"])
        return payload

    c = client_factory(ratify_transport(mutate=forge))
    m, prior = deny_record()
    r = c.ratify(prior, APPROVED, proof(m, STRANGER_DID, STRANGER_SK, prior["core_digest"], APPROVED))
    assert r.status == RATIFIED             # der Server luegt sauber
    res = c.verify_ratification(r, prior, m)
    assert res.ok is False
    assert any("does not derive from the mandate" in s for s in res.mismatches)


def test_verify_catches_a_forged_status_with_a_real_authority(client_factory):
    """Autoritaet steht im Mandat, aber sie hat nie signiert. Nur die volle Nachrechnung
    faengt das — und der Test zeigt beide Seiten."""
    def forge(payload):
        payload["status"] = RATIFIED
        payload["authority"] = OFFICER_DID
        payload["record"]["core"]["status"] = RATIFIED
        payload["record"]["core"]["authority"] = OFFICER_DID
        from moltrust_enforce.client import ratification_digest
        payload["record"]["core_digest"] = ratification_digest(payload["record"]["core"])
        return payload

    c = client_factory(ratify_transport(mutate=forge))
    m, prior = deny_record()
    bad = {"mandate": m, "authority": OFFICER_DID, "signature": "aa" * 64}
    r = c.ratify(prior, APPROVED, bad)
    assert r.status == RATIFIED

    structural = c.verify_ratification(r, prior, m)
    assert structural.ok is True and structural.full_recompute is False

    full = c.verify_ratification(r, prior, m, authority_proof=bad)
    assert full.ok is False and full.full_recompute is True
    assert any("local recompute disagrees" in s for s in full.mismatches)


def test_verify_catches_a_broken_chain(client_factory):
    c = client_factory(ratify_transport())
    m, prior_a = deny_record()
    other = enforce_check(m, tx(amount=999))
    prior_b = {"core": other["core"], "core_digest": other["core_digest"]}
    p = proof(m, OFFICER_DID, OFFICER_SK, prior_a["core_digest"], APPROVED)
    r = c.ratify(prior_a, APPROVED, p)
    res = c.verify_ratification(r, prior_b, m)      # gegen den falschen Vorgaenger geprueft
    assert res.ok is False
    assert any("is not the prior record" in s for s in res.mismatches)


def test_verify_catches_a_tampered_digest(client_factory):
    def tamper(payload):
        payload["record"]["core_digest"] = "sha256:" + "0" * 64
        return payload

    c = client_factory(ratify_transport(mutate=tamper))
    m, prior = deny_record()
    p = proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    res = c.verify_ratification(c.ratify(prior, APPROVED, p), prior, m)
    assert res.ok is False
    assert any("does not match the core it ships with" in s for s in res.mismatches)


def test_verify_catches_a_substituted_mandate(client_factory):
    c = client_factory(ratify_transport())
    m, prior = deny_record()
    p = proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    r = c.ratify(prior, APPROVED, p)
    wider = mandate()
    wider["grants"][0]["constraints"] = [{"type": "range", "field": "amount",
                                          "lo": 0, "hi": 10 ** 9}]
    res = c.verify_ratification(r, prior, wider)
    assert res.ok is False
    assert any("supplied mandate does not match" in s for s in res.mismatches)


def test_verify_of_a_transport_level_reject_has_nothing_to_check(client_factory):
    c = client_factory(broken_transport(httpx.ConnectError("down")))
    m, prior = deny_record()
    r = c.ratify(prior, APPROVED, {})
    res = c.verify_ratification(r, prior, m)
    assert res.ok is False
    assert any("produced locally" in s for s in res.mismatches)


def test_verify_needs_no_network(client_factory):
    c = client_factory(ratify_transport())
    m, prior = deny_record()
    p = proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    r = c.ratify(prior, APPROVED, p)
    dead = client_factory(broken_transport(httpx.ConnectError("down")))
    assert dead.verify_ratification(r, prior, m, authority_proof=p).ok is True


def test_verify_is_deterministic(client_factory):
    c = client_factory(ratify_transport())
    m, prior = deny_record()
    p = proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    r = c.ratify(prior, APPROVED, p)
    a = c.verify_ratification(r, prior, m, authority_proof=p)
    b = c.verify_ratification(r, prior, m, authority_proof=p)
    assert (a.ok, a.mismatches, a.full_recompute) == (b.ok, b.mismatches, b.full_recompute)


def test_statement_binds_prior_decision_and_authority():
    s = ratification_statement("sha256:" + "a" * 64, APPROVED, OFFICER_DID)
    assert set(s) == {"ratify_version", "ratifies", "decision", "authority"}
    other = ratification_statement("sha256:" + "a" * 64, DISAPPROVED, OFFICER_DID)
    assert statement_bytes("sha256:" + "a" * 64, APPROVED, OFFICER_DID) != \
        statement_bytes("sha256:" + "a" * 64, DISAPPROVED, OFFICER_DID)
    assert s != other
