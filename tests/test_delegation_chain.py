"""Tests — AAE §5 Step 9 (delegation-chain walk over inline ancestors).

Chains are built from real signed envelopes: each AAE in a chain is signed by the
agent that issues it, so the walk exercises the same core verification the gate
runs. Unit tests hold a rollback transaction; the registered agents disappear with it.
"""
import copy
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.api_jws import PyJWS

from app.enforcement import delegation_chain as dc
from app.enforcement.acceptance_gate import AcceptanceError, _core_for_ancestor, verify_aae_jws
from app.enforcement.delegation_chain import DelegationChainError, verify_delegation_chain
from app.enforcement.subject_binding import RELYING_PARTY_AUD, issue_challenge

DB = dict(host="localhost", database=os.getenv("DB_NAME", "moltstack"), user="moltstack")

WIDE = ("2026-01-01T00:00:00Z", "2028-01-01T00:00:00Z")


def _new_agent():
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    return priv, f"did:moltrust:{uuid.uuid4().hex[:16]}", pub_hex


async def _register(conn, did, pub_hex):
    await conn.execute(
        "INSERT INTO agents (did, display_name, platform, agent_type, public_key_hex) "
        "VALUES ($1, $2, 'test', 'external', $3) "
        "ON CONFLICT (did) DO UPDATE SET public_key_hex = EXCLUDED.public_key_hex",
        did, f"tc-{did[-8:]}", pub_hex)


def _vc(*, issuer, subject, actions, constraints, validity=WIDE, delegation=None,
        delegation_policy=None, aae_id=None):
    mandate = {"actions": list(actions)}
    if delegation is not None:
        mandate["delegation"] = delegation
    if delegation_policy is not None:
        mandate["delegation_policy"] = delegation_policy
    return {
        "id": aae_id or f"urn:uuid:{uuid.uuid4()}",
        "issuer": issuer,
        "credentialSubject": {"id": subject, "aae": {
            "mandate": mandate,
            "constraints": constraints,
            "validity": {"not_before": validity[0], "not_after": validity[1]},
        }},
    }


def _sign(priv, did, vc):
    return PyJWS().encode(json.dumps(vc).encode(), key=priv, algorithm="EdDSA",
                          headers={"cty": "aae+json", "kid": f"{did}#key-1"})


def _mtv(value, currency="USD", required=True):
    return {"type": "max_transaction_value", "value": value,
            "currency": currency, "required": required}


def _domains(values, required=True):
    return {"type": "allowed_domains", "value": list(values), "required": required}


def _rate(value, window, required=True):
    return {"type": "rate_limit", "value": value, "window": window, "required": required}


def _challenge(priv, did, aae_id):
    ch = issue_challenge(aae_id)
    claims = {"nonce": ch["nonce"], "aud": RELYING_PARTY_AUD,
              "iat": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
              "aae_id": aae_id}
    return PyJWS().encode(json.dumps(claims).encode(), key=priv, algorithm="EdDSA",
                          headers={"kid": f"{did}#key-1"})


@pytest_asyncio.fixture
async def tx_conn():
    conn = await asyncpg.connect(**DB)
    tr = conn.transaction()
    await tr.start()
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


class Chain:
    """A root AAE held by A, delegated to B. Child fields are patchable per test."""

    def __init__(self, root_vc, root_jws, child_vc, child_jws, agents):
        self.root_vc, self.root_jws = root_vc, root_jws
        self.child_vc, self.child_jws = child_vc, child_jws
        self.agents = agents  # {"A": (priv, did), "B": (priv, did)}


async def _two_link(conn, *, child_patch=None, root_patch=None):
    a_priv, a_did, a_pub = _new_agent()
    b_priv, b_did, b_pub = _new_agent()
    await _register(conn, a_did, a_pub)
    await _register(conn, b_did, b_pub)

    root = _vc(issuer=a_did, subject=a_did, actions=["read", "book"],
               constraints=[_mtv(500), _domains(["a.example", "b.example"]), _rate(10, "1h")],
               delegation_policy={"max_depth": 2})
    if root_patch:
        root_patch(root)
    root_jws = _sign(a_priv, a_did, root)

    child = _vc(issuer=a_did, subject=b_did, actions=["read"],
                constraints=[_mtv(100), _domains(["a.example"]), _rate(5, "1h")],
                delegation={"delegator_did": a_did, "delegator_aae_id": root["id"],
                            "depth": 1, "max_depth": 2})
    if child_patch:
        child_patch(child, root, root_jws)
    child_jws = _sign(a_priv, a_did, child)
    return Chain(root, root_jws, child, child_jws,
                 {"A": (a_priv, a_did), "B": (b_priv, b_did)})


async def _walk(conn, chain, ancestors=None):
    return await verify_delegation_chain(
        chain.child_vc, aae_jws=chain.child_jws,
        ancestor_jws=[chain.root_jws] if ancestors is None else ancestors,
        conn=conn, verify_core=_core_for_ancestor, signing_did=chain.child_vc["issuer"])


# ---------------- the positive path ----------------

async def test_valid_chain_accepts(tx_conn):
    chain = await _two_link(tx_conn)
    out = await _walk(tx_conn, chain)
    assert out["chain_length"] == 1
    assert out["path"] == [chain.child_vc["id"], chain.root_vc["id"]]
    assert out["root_aae_id"] == chain.root_vc["id"]


async def test_three_link_chain_accepts(tx_conn):
    chain = await _two_link(tx_conn)
    b_priv, b_did = chain.agents["B"]
    c_priv, c_did, c_pub = _new_agent()
    await _register(tx_conn, c_did, c_pub)
    grand = _vc(issuer=b_did, subject=c_did, actions=["read"],
                constraints=[_mtv(50), _domains(["a.example"]), _rate(2, "1h")],
                delegation={"delegator_did": b_did, "delegator_aae_id": chain.child_vc["id"],
                            "depth": 2, "max_depth": 2})
    grand_jws = _sign(b_priv, b_did, grand)
    out = await verify_delegation_chain(
        grand, aae_jws=grand_jws, ancestor_jws=[chain.child_jws, chain.root_jws],
        conn=tx_conn, verify_core=_core_for_ancestor, signing_did=b_did)
    assert out["chain_length"] == 2 and out["root_aae_id"] == chain.root_vc["id"]


async def test_no_delegation_returns_none(tx_conn):
    chain = await _two_link(tx_conn)
    assert await verify_delegation_chain(
        chain.root_vc, aae_jws=chain.root_jws, ancestor_jws=[], conn=tx_conn,
        verify_core=_core_for_ancestor, signing_did=chain.root_vc["issuer"]) is None


# ---------------- cycles and depth ----------------

async def test_cycle_rejected(tx_conn):
    def patch(child, root, root_jws):
        child["credentialSubject"]["aae"]["mandate"]["delegation"]["delegator_aae_id"] = child["id"]
    chain = await _two_link(tx_conn, child_patch=patch)
    with pytest.raises(DelegationChainError, match="cycle"):
        await _walk(tx_conn, chain)


async def test_depth_beyond_max_depth_rejected(tx_conn):
    def patch(child, root, root_jws):
        d = child["credentialSubject"]["aae"]["mandate"]["delegation"]
        d["depth"], d["max_depth"] = 2, 1
    chain = await _two_link(tx_conn, child_patch=patch)
    with pytest.raises(DelegationChainError):
        await _walk(tx_conn, chain)


async def test_max_depth_above_parent_ceiling_rejected(tx_conn):
    def patch(child, root, root_jws):
        child["credentialSubject"]["aae"]["mandate"]["delegation"]["max_depth"] = 5
    chain = await _two_link(tx_conn, child_patch=patch)
    with pytest.raises(DelegationChainError, match="exceeds the parent"):
        await _walk(tx_conn, chain)


async def test_wrong_depth_increment_rejected(tx_conn):
    def patch(child, root, root_jws):
        child["credentialSubject"]["aae"]["mandate"]["delegation"]["depth"] = 2
    chain = await _two_link(tx_conn, child_patch=patch)
    with pytest.raises(DelegationChainError, match="plus 1"):
        await _walk(tx_conn, chain)


async def test_root_without_delegation_policy_rejected(tx_conn):
    def patch(root):
        root["credentialSubject"]["aae"]["mandate"].pop("delegation_policy")
    chain = await _two_link(tx_conn, root_patch=patch)
    with pytest.raises(DelegationChainError, match="delegation_policy"):
        await _walk(tx_conn, chain)


async def test_recursion_limit_applies(tx_conn, monkeypatch):
    chain = await _two_link(tx_conn)
    monkeypatch.setattr(dc, "MAX_RECURSION_LIMIT", 0)
    with pytest.raises(DelegationChainError, match="recursion limit"):
        await _walk(tx_conn, chain)


# ---------------- link integrity ----------------

async def test_delegator_did_not_parent_subject_rejected(tx_conn):
    def patch(child, root, root_jws):
        child["credentialSubject"]["aae"]["mandate"]["delegation"]["delegator_did"] = \
            child["credentialSubject"]["id"]
        child["issuer"] = child["credentialSubject"]["id"]
    chain = await _two_link(tx_conn, child_patch=patch)
    with pytest.raises((DelegationChainError, AcceptanceError)):
        await _walk(tx_conn, chain)


async def test_issuer_not_delegator_rejected(tx_conn):
    chain = await _two_link(tx_conn)
    forged = copy.deepcopy(chain.child_vc)
    forged["issuer"] = forged["credentialSubject"]["id"]  # issuer no longer the delegator
    with pytest.raises(DelegationChainError, match="signing authority"):
        await verify_delegation_chain(
            forged, aae_jws=chain.child_jws, ancestor_jws=[chain.root_jws], conn=tx_conn,
            verify_core=_core_for_ancestor, signing_did=chain.child_vc["issuer"])


async def test_parent_hash_match_accepts(tx_conn):
    import base64, hashlib

    def patch(child, root, root_jws):
        digest = hashlib.sha256(root_jws.encode("ascii")).digest()
        child["credentialSubject"]["aae"]["mandate"]["delegation"]["delegator_aae_hash"] = \
            "sha-256:" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    chain = await _two_link(tx_conn, child_patch=patch)
    assert (await _walk(tx_conn, chain))["chain_length"] == 1


async def test_parent_hash_mismatch_rejected(tx_conn):
    def patch(child, root, root_jws):
        child["credentialSubject"]["aae"]["mandate"]["delegation"]["delegator_aae_hash"] = \
            "sha-256:" + "A" * 43
    chain = await _two_link(tx_conn, child_patch=patch)
    with pytest.raises(DelegationChainError, match="delegator_aae_hash"):
        await _walk(tx_conn, chain)


async def test_missing_ancestor_rejected(tx_conn):
    chain = await _two_link(tx_conn)
    with pytest.raises(DelegationChainError, match="neither supplied inline nor named"):
        await _walk(tx_conn, chain, ancestors=[])


async def test_duplicate_ancestor_rejected(tx_conn):
    chain = await _two_link(tx_conn)
    with pytest.raises(DelegationChainError, match="more than once"):
        await _walk(tx_conn, chain, ancestors=[chain.root_jws, chain.root_jws])


async def test_too_many_ancestors_rejected(tx_conn):
    chain = await _two_link(tx_conn)
    with pytest.raises(DelegationChainError, match="inline ancestors"):
        await _walk(tx_conn, chain, ancestors=[chain.root_jws] * (dc.MAX_ANCESTORS + 1))


# ---------------- the URI branch stays deferred ----------------

async def test_uri_only_ancestor_is_deferred(tx_conn):
    def patch(child, root, root_jws):
        child["credentialSubject"]["aae"]["mandate"]["delegation"]["delegator_aae_uri"] = \
            "https://aae.example/p/parent"
    chain = await _two_link(tx_conn, child_patch=patch)
    with pytest.raises(NotImplementedError, match="egress"):
        await _walk(tx_conn, chain, ancestors=[])


# ---------------- monotonicity, one rule per test ----------------

async def _expect_monotonicity_error(tx_conn, patch, match):
    chain = await _two_link(tx_conn, child_patch=patch)
    with pytest.raises(DelegationChainError, match=match):
        await _walk(tx_conn, chain)


async def test_actions_superset_rejected(tx_conn):
    await _expect_monotonicity_error(
        tx_conn,
        lambda c, r, j: c["credentialSubject"]["aae"]["mandate"].__setitem__(
            "actions", ["read", "book", "transfer"]),
        "not a subset")


async def test_numeric_bound_widened_rejected(tx_conn):
    await _expect_monotonicity_error(
        tx_conn,
        lambda c, r, j: c["credentialSubject"]["aae"].__setitem__(
            "constraints", [_mtv(900), _domains(["a.example"]), _rate(5, "1h")]),
        "widens the numeric upper bound")


async def test_currency_change_rejected(tx_conn):
    await _expect_monotonicity_error(
        tx_conn,
        lambda c, r, j: c["credentialSubject"]["aae"].__setitem__(
            "constraints", [_mtv(100, "EUR"), _domains(["a.example"]), _rate(5, "1h")]),
        "changes currency")


async def test_rate_limit_window_change_rejected(tx_conn):
    await _expect_monotonicity_error(
        tx_conn,
        lambda c, r, j: c["credentialSubject"]["aae"].__setitem__(
            "constraints", [_mtv(100), _domains(["a.example"]), _rate(5, "24h")]),
        "different window")


async def test_rate_limit_raised_rejected(tx_conn):
    await _expect_monotonicity_error(
        tx_conn,
        lambda c, r, j: c["credentialSubject"]["aae"].__setitem__(
            "constraints", [_mtv(100), _domains(["a.example"]), _rate(50, "1h")]),
        "raises the rate limit")


async def test_allowlist_not_subset_rejected(tx_conn):
    await _expect_monotonicity_error(
        tx_conn,
        lambda c, r, j: c["credentialSubject"]["aae"].__setitem__(
            "constraints", [_mtv(100), _domains(["a.example", "evil.example"]), _rate(5, "1h")]),
        "not a subset of the parent allowlist")


async def test_required_constraint_omitted_rejected(tx_conn):
    await _expect_monotonicity_error(
        tx_conn,
        lambda c, r, j: c["credentialSubject"]["aae"].__setitem__(
            "constraints", [_mtv(100), _rate(5, "1h")]),
        "omits the parent's required constraint")


async def test_required_constraint_downgraded_rejected(tx_conn):
    await _expect_monotonicity_error(
        tx_conn,
        lambda c, r, j: c["credentialSubject"]["aae"].__setitem__(
            "constraints", [_mtv(100), _domains(["a.example"], required=False), _rate(5, "1h")]),
        "downgrades the parent's required constraint")


async def test_not_before_earlier_rejected(tx_conn):
    await _expect_monotonicity_error(
        tx_conn,
        lambda c, r, j: c["credentialSubject"]["aae"]["validity"].__setitem__(
            "not_before", "2025-01-01T00:00:00Z"),
        "not_before precedes")


async def test_not_after_later_rejected(tx_conn):
    await _expect_monotonicity_error(
        tx_conn,
        lambda c, r, j: c["credentialSubject"]["aae"]["validity"].__setitem__(
            "not_after", "2030-01-01T00:00:00Z"),
        "not_after outlasts")


async def test_unknown_constraint_differing_rejected(tx_conn):
    def root_patch(root):
        root["credentialSubject"]["aae"]["constraints"].append(
            {"type": "geo_fence", "value": "EU", "required": True})

    def child_patch(child, root, root_jws):
        child["credentialSubject"]["aae"]["constraints"].append(
            {"type": "geo_fence", "value": "GLOBAL", "required": True})

    chain = await _two_link(tx_conn, root_patch=root_patch, child_patch=child_patch)
    with pytest.raises(DelegationChainError, match="no defined comparison"):
        await _walk(tx_conn, chain)


async def test_unknown_constraint_identical_accepts(tx_conn):
    fence = {"type": "geo_fence", "value": "EU", "required": True}
    chain = await _two_link(
        tx_conn,
        root_patch=lambda r: r["credentialSubject"]["aae"]["constraints"].append(dict(fence)),
        child_patch=lambda c, r, j: c["credentialSubject"]["aae"]["constraints"].append(dict(fence)))
    assert (await _walk(tx_conn, chain))["chain_length"] == 1


async def test_added_constraint_accepts(tx_conn):
    chain = await _two_link(
        tx_conn,
        child_patch=lambda c, r, j: c["credentialSubject"]["aae"]["constraints"].append(
            {"type": "geo_fence", "value": "EU", "required": True}))
    assert (await _walk(tx_conn, chain))["chain_length"] == 1


# ---------------- effective validity window ----------------

async def test_effective_window_is_the_narrowest_over_the_chain(tx_conn):
    past = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")

    def root_patch(root):
        root["credentialSubject"]["aae"]["validity"] = {
            "not_before": past[0], "not_after": past[1]}

    def child_patch(child, root, root_jws):
        child["credentialSubject"]["aae"]["validity"] = {
            "not_before": "2026-01-15T00:00:00Z", "not_after": past[1]}

    chain = await _two_link(tx_conn, root_patch=root_patch, child_patch=child_patch)
    out = await _walk(tx_conn, chain)
    # An expired chain is not the acceptance gate's rejection to make: Step 3 belongs to
    # the Evaluator. The walk reports the narrowest window it found.
    assert out["effective_not_after"] == "2026-02-01T00:00:00Z"
    assert out["effective_not_before"] == "2026-01-15T00:00:00Z"


# ---------------- through the gate ----------------

async def test_gate_accepts_delegated_aae_with_inline_ancestor(tx_conn):
    chain = await _two_link(tx_conn)
    b_priv, b_did = chain.agents["B"]
    v = await verify_aae_jws(
        chain.child_jws, tx_conn,
        subject_challenge_jws=_challenge(b_priv, b_did, chain.child_vc["id"]),
        ancestor_jws=[chain.root_jws])
    assert v["delegation_chain"]["chain_length"] == 1
    assert v["subject_did"] == b_did


async def test_gate_rejects_broken_chain(tx_conn):
    def patch(child, root, root_jws):
        child["credentialSubject"]["aae"]["mandate"]["actions"] = ["read", "transfer"]
    chain = await _two_link(tx_conn, child_patch=patch)
    b_priv, b_did = chain.agents["B"]
    with pytest.raises(AcceptanceError, match="delegation chain rejected"):
        await verify_aae_jws(
            chain.child_jws, tx_conn,
            subject_challenge_jws=_challenge(b_priv, b_did, chain.child_vc["id"]),
            ancestor_jws=[chain.root_jws])


async def test_gate_leaves_non_delegated_chain_none(tx_conn):
    priv, did, pub = _new_agent()
    await _register(tx_conn, did, pub)
    vc = _vc(issuer=did, subject=did, actions=["read"], constraints=[_mtv(10)])
    jws = _sign(priv, did, vc)
    v = await verify_aae_jws(jws, tx_conn,
                             subject_challenge_jws=_challenge(priv, did, vc["id"]))
    assert v["delegation_chain"] is None


# ---------------- endpoint boundary ----------------

async def _commit_agent(did, pub_hex):
    c = await asyncpg.connect(**DB)
    try:
        await _register(c, did, pub_hex)
    finally:
        await c.close()


async def _delete_agent(did):
    c = await asyncpg.connect(**DB)
    try:
        await c.execute("DELETE FROM agents WHERE did = $1", did)
    finally:
        await c.close()


async def test_submit_rejects_non_array_ancestors(async_client):
    priv, did, pub = _new_agent()
    await _commit_agent(did, pub)
    try:
        vc = _vc(issuer=did, subject=did, actions=["read"], constraints=[_mtv(10)])
        r = await async_client.post("/vc/aae/submit", json={
            "aae_jws": _sign(priv, did, vc),
            "subject_challenge_jws": _challenge(priv, did, vc["id"]),
            "ancestor_jws": "not-an-array",
        }, headers={"X-MolTrust-DID": did})
        assert r.status_code == 422 and "ancestor_jws" in r.text
    finally:
        await _delete_agent(did)


async def test_submit_rejects_too_many_ancestors(async_client):
    priv, did, pub = _new_agent()
    await _commit_agent(did, pub)
    try:
        vc = _vc(issuer=did, subject=did, actions=["read"], constraints=[_mtv(10)])
        r = await async_client.post("/vc/aae/submit", json={
            "aae_jws": _sign(priv, did, vc),
            "subject_challenge_jws": _challenge(priv, did, vc["id"]),
            "ancestor_jws": ["x.y.z"] * (dc.MAX_ANCESTORS + 1),
        }, headers={"X-MolTrust-DID": did})
        assert r.status_code == 422 and "inline ancestors" in r.text
    finally:
        await _delete_agent(did)
