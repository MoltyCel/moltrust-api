"""POST /enforce/check with a signed chain: AAE -02 §5 steps 1, 2, 3 and 9 before the kernel.

Until this change the endpoint took `ancestors` as plain JSON and checked only the
narrowing between them, so a caller could present any parent at all, and a delegated
mandate sent without parents was evaluated as a root. Here the chain is real: every
envelope is signed, keys come from a stand-in registry instead of the database, and the
cases are the ones a chain check exists for — a forged parent, a missing parent, a child
broader than its parent, an expired link.
"""
import json
import uuid
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.api_jws import PyJWS

from app.enforcement.enforce_check import action_digest, enforce_check
from app.enforcement.enforce_input import ChainRefused, verified_input

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
WIDE = ("2026-01-01T00:00:00Z", "2028-01-01T00:00:00Z")
ACTION = {"verb": "transfer", "asset": "USDC", "chain": "base"}


class _Registry:
    """Stands in for the agents table: the one query the gate makes."""

    def __init__(self):
        self.keys = {}

    async def fetchrow(self, sql, did):
        hexkey = self.keys.get(did)
        return {"public_key_hex": hexkey} if hexkey else None


def _agent(reg, register=True):
    priv = Ed25519PrivateKey.generate()
    did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    if register:
        reg.keys[did] = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    return priv, did


def _grant(hi):
    return {"action_binding": action_digest(ACTION), "type_fields": ["verb", "asset", "chain"],
            "disposition": "allow",
            "constraints": [{"type": "range", "field": "amount", "lo": 0, "hi": hi}]}


def _vc(issuer, subject, hi, validity=WIDE, delegation=None, policy=None, cap=None):
    mandate = {"actions": ["transfer"], "grants": [_grant(hi)]}
    if delegation:
        mandate["delegation"] = delegation
    if policy:
        mandate["delegation_policy"] = policy
    return {"id": f"urn:uuid:{uuid.uuid4()}", "issuer": issuer,
            "credentialSubject": {"id": subject, "aae": {
                "mandate": mandate,
                "constraints": [{"type": "max_transaction_value", "value": cap or hi,
                                 "currency": "USD", "required": True}],
                "validity": {"not_before": validity[0], "not_after": validity[1]}}}}


def _sign(priv, did, vc):
    return PyJWS().encode(json.dumps(vc).encode(), key=priv, algorithm="EdDSA",
                          headers={"cty": "aae+json", "kid": f"{did}#key-1"})


def _chain(reg, *, child_hi=100, root_signer=None, child_validity=WIDE):
    a_priv, a_did = _agent(reg)
    _b_priv, b_did = _agent(reg)
    root = _vc(a_did, a_did, 500, policy={"max_depth": 2})
    r_priv, r_did = root_signer or (a_priv, a_did)
    root["issuer"] = r_did
    root_jws = _sign(r_priv, r_did, root)
    child = _vc(a_did, b_did, child_hi, validity=child_validity, cap=100,
                delegation={"delegator_did": a_did, "delegator_aae_id": root["id"],
                            "depth": 1, "max_depth": 2})
    return root, root_jws, child, _sign(a_priv, a_did, child)


async def test_a_verified_chain_hands_the_signed_mandates_to_the_kernel():
    reg = _Registry()
    root, root_jws, child, child_jws = _chain(reg)
    mandate, ancestors, chain = await verified_input(child_jws, [root_jws], reg, now=NOW)
    assert mandate == child["credentialSubject"]["aae"]["mandate"]
    assert ancestors == [root["credentialSubject"]["aae"]["mandate"]]
    assert chain["verified"] and chain["delegated"] and chain["chain_length"] == 1
    out = enforce_check(mandate, {"action": ACTION, "amount": 50}, ancestors=ancestors)
    assert out["verdict"] == "PERMIT"
    hops = [p for p in out["trace"] if p["predicate"] == "grant_attenuation"]
    assert [p["result"] for p in hops] == ["PASS"]


async def test_an_envelope_without_delegation_has_no_ancestors():
    reg = _Registry()
    priv, did = _agent(reg)
    vc = _vc(did, did, 500)
    mandate, ancestors, chain = await verified_input(_sign(priv, did, vc), None, reg, now=NOW)
    assert ancestors == [] and chain == {**chain, "verified": True, "delegated": False}


async def test_a_child_broader_than_its_parent_is_refused():
    reg = _Registry()
    _root, root_jws, _child, child_jws = _chain(reg, child_hi=900)
    with pytest.raises(ChainRefused, match="grant attenuation"):
        await verified_input(child_jws, [root_jws], reg, now=NOW)


async def test_a_parent_signed_by_an_unknown_key_is_refused():
    reg = _Registry()
    forger = _agent(reg, register=False)
    _root, root_jws, _child, child_jws = _chain(reg, root_signer=forger)
    with pytest.raises(ChainRefused, match="not resolvable"):
        await verified_input(child_jws, [root_jws], reg, now=NOW)


async def test_a_delegated_envelope_without_its_parent_is_refused():
    reg = _Registry()
    _root, _root_jws, _child, child_jws = _chain(reg)
    with pytest.raises(ChainRefused, match="delegat|ancestor|parent"):
        await verified_input(child_jws, [], reg, now=NOW)


async def test_a_chain_outside_its_validity_window_is_refused():
    reg = _Registry()
    _root, root_jws, _child, child_jws = _chain(
        reg, child_validity=("2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z"))
    with pytest.raises(ChainRefused, match="validity"):
        await verified_input(child_jws, [root_jws], reg, now=NOW)


def test_the_endpoint_refuses_bare_ancestors_and_labels_a_bare_mandate():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    body = src[src.index('@app.post("/enforce/check"'):src.index('@app.post("/enforce/ratify"')]
    assert 'body.get("ancestors") is not None' in body
    assert "ancestors are checked only as a signed chain" in body
    assert "_ENFORCE_SCOPE_SIGNED if chain else _ENFORCE_SCOPE" in body
    assert "verified_input(" in body
