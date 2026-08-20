"""Tests — Ratifikation (AAE -02-Kandidat §9).

Der Kern ist rein, die meisten Tests brauchen weder DB noch App. Die Endpunkt-Tests am Ende
laufen gegen die App.

Der sicherheitskritische Teil ist Guard 1: eine Autoritaet, die nicht aus dem Mandat kommt,
darf unter keinen Umstaenden ratifizieren. Dagegen laufen hier mehrere Angriffsformen.
"""
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.enforcement.enforce_check import DENY, PENDING, PERMIT, action_digest, enforce_check
from app.enforcement.ratify import (
    APPROVED, DISAPPROVED, RATIFIED, REJECTED, RatifyError,
    core_digest, mandate_authorities, ratify, recompute, statement_bytes,
)

ADDR = "0xABCDEF0123456789ABCDEF0123456789ABCDEF01"
PAY = {"verb": "transfer", "asset": "USDC", "chain": "base"}

PRINCIPAL_DID = "did:moltrust:1111111111111111"
OFFICER_DID = "did:moltrust:2222222222222222"
STRANGER_DID = "did:moltrust:3333333333333333"


def _key():
    sk = Ed25519PrivateKey.generate()
    pk_hex = sk.public_key().public_bytes_raw().hex()
    return sk, pk_hex


PRINCIPAL_SK, PRINCIPAL_PK = _key()
OFFICER_SK, OFFICER_PK = _key()
STRANGER_SK, STRANGER_PK = _key()


def _mandate(disposition="allow", constraints=None, with_officer=True):
    m = {
        "mandate_version": "1.0",
        "principal": {"did": PRINCIPAL_DID, "public_key": PRINCIPAL_PK},
        "grants": [{"action_binding": action_digest(PAY), "disposition": disposition,
                    "constraints": constraints if constraints is not None else []}],
    }
    if with_officer:
        m["ratification_authorities"] = [
            {"did": OFFICER_DID, "public_key": OFFICER_PK, "role": "compliance_officer"}]
    return m


def _tx(**over):
    t = {"action": dict(PAY), "to": ADDR, "amount": 500}
    t.update(over)
    return t


def _record(result):
    return {"core": result["core"], "core_digest": result["core_digest"]}


def _deny_record(mandate=None):
    """Ein echter DENY aus dem Verdikt-Kern: Betrag ausserhalb der Range."""
    m = mandate or _mandate("allow", [{"type": "range", "field": "amount", "lo": 0, "hi": 100}])
    r = enforce_check(m, _tx(amount=500))
    assert r["verdict"] == DENY
    return m, _record(r)


def _pending_record():
    m = _mandate("hold")
    r = enforce_check(m, _tx())
    assert r["verdict"] == PENDING
    return m, _record(r)


def _proof(mandate, did, sk, prior_digest, decision):
    sig = sk.sign(statement_bytes(prior_digest, decision, did)).hex()
    return {"mandate": mandate, "authority": did, "signature": sig}


def _preds(res, predicate):
    return [p for p in res["trace"] if p["predicate"] == predicate]


# ------------------------------------------------------------------ APPROVED / DISAPPROVED

def test_approved_by_issuing_principal():
    m, prior = _deny_record()
    proof = _proof(m, PRINCIPAL_DID, PRINCIPAL_SK, prior["core_digest"], APPROVED)
    res = ratify(prior, APPROVED, proof)
    assert res["status"] == RATIFIED
    assert res["decision"] == APPROVED
    assert res["authority"] == PRINCIPAL_DID
    assert res["ratifies"] == prior["core_digest"]
    assert "principal" in res["reason"]
    assert core_digest(res["core"]) == res["core_digest"]


def test_approved_by_role_named_in_the_mandate():
    m, prior = _deny_record()
    proof = _proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    res = ratify(prior, APPROVED, proof)
    assert res["status"] == RATIFIED
    assert res["authority"] == OFFICER_DID
    assert "compliance_officer" in res["reason"]


def test_disapproved():
    m, prior = _deny_record()
    proof = _proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], DISAPPROVED)
    res = ratify(prior, DISAPPROVED, proof)
    assert res["status"] == RATIFIED
    assert res["decision"] == DISAPPROVED
    assert res["core"]["decision"] == DISAPPROVED


def test_pending_predecessor_is_ratifiable():
    m, prior = _pending_record()
    proof = _proof(m, PRINCIPAL_DID, PRINCIPAL_SK, prior["core_digest"], APPROVED)
    res = ratify(prior, APPROVED, proof)
    assert res["status"] == RATIFIED
    assert res["core"]["prior_verdict"] == PENDING


def test_prior_record_is_never_modified():
    m, prior = _deny_record()
    before = dict(prior["core"]), prior["core_digest"]
    proof = _proof(m, PRINCIPAL_DID, PRINCIPAL_SK, prior["core_digest"], APPROVED)
    ratify(prior, APPROVED, proof)
    assert (prior["core"], prior["core_digest"]) == before
    assert prior["core"]["verdict"] == DENY   # der Vorgaenger bleibt ein DENY


# --------------------------------------------------------- ★ GUARD 1 (Witness-not-Ruler)

def test_guard1_authority_not_in_mandate_is_rejected():
    """Ein Fremder mit gueltiger eigener Signatur ratifiziert NICHT."""
    m, prior = _deny_record()
    proof = _proof(m, STRANGER_DID, STRANGER_SK, prior["core_digest"], APPROVED)
    res = ratify(prior, APPROVED, proof)
    assert res["status"] == REJECTED
    assert res["authority"] is None
    assert _preds(res, "authority_in_mandate")[0]["result"] == "FAIL"


def test_guard1_own_key_smuggled_in_the_proof_is_ignored():
    """Der Angreifer legt seinen eigenen Schluessel unter der DID der Autoritaet bei.
    Geprueft wird gegen den Schluessel AUS DEM MANDAT — der Versuch faellt durch."""
    m, prior = _deny_record()
    proof = _proof(m, OFFICER_DID, STRANGER_SK, prior["core_digest"], APPROVED)
    proof["public_key"] = STRANGER_PK           # wird bewusst nirgends gelesen
    res = ratify(prior, APPROVED, proof)
    assert res["status"] == REJECTED
    assert _preds(res, "authority_signature")[0]["result"] == "FAIL"


def test_guard1_forged_mandate_with_attacker_as_authority_is_rejected():
    """Der Angreifer baut ein Mandat, das ihn als Autoritaet nennt. Es passt dann nicht mehr
    zum mandate_digest des Vorgaengers — die Bindung faengt es."""
    m, prior = _deny_record()
    forged = _mandate()
    forged["ratification_authorities"].append(
        {"did": STRANGER_DID, "public_key": STRANGER_PK, "role": "self_appointed"})
    proof = _proof(forged, STRANGER_DID, STRANGER_SK, prior["core_digest"], APPROVED)
    res = ratify(prior, APPROVED, proof)
    assert res["status"] == REJECTED
    assert _preds(res, "mandate_binding")[0]["result"] == "FAIL"


def test_guard1_signature_over_a_different_prior_record_does_not_transfer():
    """Eine gueltige APPROVED-Signatur laesst sich nicht auf einen anderen Record umhaengen."""
    m, prior_a = _deny_record()
    other = enforce_check(m, _tx(amount=999))
    prior_b = _record(other)
    assert prior_a["core_digest"] != prior_b["core_digest"]
    proof = _proof(m, OFFICER_DID, OFFICER_SK, prior_a["core_digest"], APPROVED)
    res = ratify(prior_b, APPROVED, proof)
    assert res["status"] == REJECTED


def test_guard1_signature_does_not_transfer_between_decisions():
    """Eine APPROVED-Signatur wird nicht als DISAPPROVED gueltig — und umgekehrt."""
    m, prior = _deny_record()
    proof = _proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    assert ratify(prior, DISAPPROVED, proof)["status"] == REJECTED


def test_guard1_signature_does_not_transfer_between_authorities():
    m, prior = _deny_record()
    # Officer signiert, gibt sich aber als Principal aus.
    sig = OFFICER_SK.sign(statement_bytes(prior["core_digest"], APPROVED, PRINCIPAL_DID)).hex()
    res = ratify(prior, APPROVED, {"mandate": m, "authority": PRINCIPAL_DID, "signature": sig})
    assert res["status"] == REJECTED


@pytest.mark.parametrize("proof", [
    None, {}, "proof", 0, [],
    {"authority": OFFICER_DID, "signature": "aa" * 64},          # Mandat fehlt
    {"mandate": None, "authority": OFFICER_DID, "signature": "aa" * 64},
    {"mandate": {}, "authority": OFFICER_DID, "signature": "aa" * 64},
])
def test_guard1_malformed_proof_is_rejected(proof):
    _m, prior = _deny_record()
    res = ratify(prior, APPROVED, proof)
    assert res["status"] == REJECTED
    assert res["authority"] is None


@pytest.mark.parametrize("sig", [None, "", "zz", "aa" * 64, "aa" * 63, 12345, b"bytes"])
def test_guard1_broken_signature_never_ratifies(sig):
    m, prior = _deny_record()
    res = ratify(prior, APPROVED, {"mandate": m, "authority": OFFICER_DID, "signature": sig})
    assert res["status"] == REJECTED


def test_guard1_mandate_without_key_grants_nobody():
    """Eine im Mandat benannte Rolle ohne Schluessel ist keine Autoritaet — es gaebe nichts
    zu pruefen."""
    m = _mandate("allow", [{"type": "range", "field": "amount", "lo": 0, "hi": 100}],
                 with_officer=False)
    m["ratification_authorities"] = [{"did": OFFICER_DID, "role": "compliance_officer"}]
    _m2, prior = _deny_record(m)
    assert (OFFICER_DID, OFFICER_PK, "compliance_officer") not in mandate_authorities(m)
    proof = _proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    assert ratify(prior, APPROVED, proof)["status"] == REJECTED


def test_mandate_authorities_lists_only_mandate_derived_entries():
    m = _mandate()
    dids = [d for d, _k, _r in mandate_authorities(m)]
    assert dids == [PRINCIPAL_DID, OFFICER_DID]
    assert STRANGER_DID not in dids


# ------------------------------------------------------------------------- ★ GUARD 2

def test_guard2_permit_is_not_ratifiable():
    m = _mandate("allow", [{"type": "range", "field": "amount", "lo": 0, "hi": 1000}])
    r = enforce_check(m, _tx(amount=500))
    assert r["verdict"] == PERMIT
    proof = _proof(m, PRINCIPAL_DID, PRINCIPAL_SK, r["core_digest"], APPROVED)
    with pytest.raises(RatifyError, match="PERMIT is not ratifiable"):
        ratify(_record(r), APPROVED, proof)


@pytest.mark.parametrize("decision", [None, "", "approved", "OK", "REJECTED", 1, True])
def test_invalid_decision_raises(decision):
    _m, prior = _deny_record()
    with pytest.raises(RatifyError):
        ratify(prior, decision, {})


@pytest.mark.parametrize("prior", [
    None, {}, "record", {"core": {}}, {"core_digest": "sha256:" + "a" * 64},
    {"core": {"verdict": DENY}, "core_digest": "sha256:" + "a" * 64},   # Digest passt nicht
])
def test_malformed_prior_record_raises(prior):
    with pytest.raises(RatifyError):
        ratify(prior, APPROVED, {})


def test_prior_record_with_tampered_core_is_refused():
    """Ein Vorgaenger, dessen Digest nicht zu seinem Core passt, ist keine Grundlage."""
    m, prior = _deny_record()
    prior["core"]["verdict"] = PERMIT          # Core veraendert, Digest stehen gelassen
    with pytest.raises(RatifyError, match="does not match its own core"):
        ratify(prior, APPROVED, {"mandate": m, "authority": PRINCIPAL_DID, "signature": "aa" * 64})


# ------------------------------------------------------------------------ Verkettung

def test_chain_links_to_the_prior_record_by_default():
    m, prior = _deny_record()
    proof = _proof(m, PRINCIPAL_DID, PRINCIPAL_SK, prior["core_digest"], APPROVED)
    res = ratify(prior, APPROVED, proof)
    assert res["core"]["prev_core_digest"] == prior["core_digest"]
    assert res["core"]["ratifies"] == prior["core_digest"]


def test_chain_link_can_be_set_explicitly():
    m, prior = _deny_record()
    earlier = "sha256:" + "e" * 64
    proof = _proof(m, PRINCIPAL_DID, PRINCIPAL_SK, prior["core_digest"], APPROVED)
    res = ratify(prior, APPROVED, proof, prev_core_digest=earlier)
    assert res["core"]["prev_core_digest"] == earlier
    assert res["core"]["ratifies"] == prior["core_digest"]   # ratifiziert bleibt der Vorgaenger
    assert res["core_digest"] != ratify(prior, APPROVED, proof)["core_digest"]


def test_ratification_digest_differs_from_the_verdict_digest():
    """Eigene Domain-Tags: ein Ratifikations-Core kollidiert nie mit einem Verdikt-Core."""
    m, prior = _deny_record()
    proof = _proof(m, PRINCIPAL_DID, PRINCIPAL_SK, prior["core_digest"], APPROVED)
    res = ratify(prior, APPROVED, proof)
    assert res["core_digest"] != prior["core_digest"]


# --------------------------------------------------------------------- Determinismus

def test_double_ratification_is_identical():
    m, prior = _deny_record()
    proof = _proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    a, b = ratify(prior, APPROVED, proof), ratify(prior, APPROVED, proof)
    assert a["core"] == b["core"] and a["core_digest"] == b["core_digest"]


def test_third_party_recomputes_the_ratification():
    m, prior = _deny_record()
    proof = _proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    res = ratify(prior, APPROVED, proof)
    assert recompute(prior, APPROVED, proof, _record(res)) is True


def test_recompute_detects_a_flipped_decision():
    m, prior = _deny_record()
    proof = _proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    res = ratify(prior, APPROVED, proof)
    forged = _record(res)
    forged["core"]["decision"] = DISAPPROVED
    forged["core_digest"] = core_digest(forged["core"])   # in sich stimmig gemacht
    assert recompute(prior, APPROVED, proof, forged) is False


def test_recompute_detects_a_forged_status():
    m, prior = _deny_record()
    proof = _proof(m, STRANGER_DID, STRANGER_SK, prior["core_digest"], APPROVED)
    res = ratify(prior, APPROVED, proof)
    assert res["status"] == REJECTED
    forged = _record(res)
    forged["core"]["status"] = RATIFIED
    forged["core"]["authority"] = OFFICER_DID
    forged["core_digest"] = core_digest(forged["core"])
    assert recompute(prior, APPROVED, proof, forged) is False


def test_core_has_no_wallclock_or_random_field():
    m, prior = _deny_record()
    proof = _proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    res = ratify(prior, APPROVED, proof)
    assert set(res["core"]) == {
        "ratify_version", "ratifies", "prior_verdict", "decision", "status",
        "authority", "mandate_digest", "reason", "trace", "prev_core_digest",
    }


def test_core_carries_no_database_state():
    import inspect
    from app.enforcement import ratify as mod
    src = inspect.getsource(mod)
    for forbidden in ("asyncpg", "SELECT ", "INSERT ", "datetime", "time."):
        assert forbidden not in src, f"ratify core references {forbidden!r}"


# ============================================================================
# Endpunkt
# ============================================================================

async def test_endpoint_ratifies(async_client, credit_test_agent):
    did, key = await credit_test_agent()
    m, prior = _deny_record()
    proof = _proof(m, OFFICER_DID, OFFICER_SK, prior["core_digest"], APPROVED)
    r = await async_client.post("/enforce/ratify", headers={"X-API-Key": key},
                                json={"prior_record": prior, "decision": APPROVED,
                                      "authority_proof": proof})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == RATIFIED and b["authority"] == OFFICER_DID
    assert b["ratifies"] == prior["core_digest"]
    assert b["record"]["core_digest"] == core_digest(b["record"]["core"])


async def test_endpoint_rejects_a_stranger_with_200_and_no_status_change(async_client,
                                                                        credit_test_agent):
    """Guard 1 am Endpunkt: abgelehnte Autoritaet ist ein Ergebnis, kein Fehler."""
    did, key = await credit_test_agent()
    m, prior = _deny_record()
    proof = _proof(m, STRANGER_DID, STRANGER_SK, prior["core_digest"], APPROVED)
    r = await async_client.post("/enforce/ratify", headers={"X-API-Key": key},
                                json={"prior_record": prior, "decision": APPROVED,
                                      "authority_proof": proof})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == REJECTED
    assert r.json()["authority"] is None


async def test_endpoint_422_on_permit_predecessor(async_client, credit_test_agent):
    did, key = await credit_test_agent()
    m = _mandate("allow", [{"type": "range", "field": "amount", "lo": 0, "hi": 1000}])
    res = enforce_check(m, _tx(amount=500))
    r = await async_client.post("/enforce/ratify", headers={"X-API-Key": key},
                                json={"prior_record": _record(res), "decision": APPROVED,
                                      "authority_proof": {}})
    assert r.status_code == 422
    assert "not ratifiable" in r.text


async def test_endpoint_requires_auth(async_client):
    r = await async_client.post("/enforce/ratify", json={"prior_record": {}, "decision": APPROVED})
    assert r.status_code in (401, 403)


async def test_enforce_check_endpoint_unchanged(async_client, credit_test_agent):
    """Regression: der Verdikt-Endpunkt verhaelt sich wie vorher."""
    did, key = await credit_test_agent()
    m = _mandate("allow", [{"type": "range", "field": "amount", "lo": 0, "hi": 1000}])
    r = await async_client.post("/enforce/check", headers={"X-API-Key": key},
                                json={"mandate": m, "transaction": _tx(amount=500)})
    assert r.status_code == 200
    assert r.json()["verdict"] == "PERMIT"
