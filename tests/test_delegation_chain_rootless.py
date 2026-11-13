"""Regression — Step 9 rejects a delegated AAE whose chain never reaches a root.

Origin: 09.11.2026, job AAE-F1. The walk follows `delegator_aae_id` from the
presented AAE upwards and stops only at an AAE without `mandate.delegation`. Four
shapes of chain that never get there must each be rejected:

1. a sub-grant above its own parent's cap, presented with no ancestors at all;
2. a three-link chain with the root left out;
3. two delegated AAEs that name each other as parent, with no root;
4. a delegated AAE that names itself as parent, with no root.

The honest chain next to them must still be accepted, so a fix that rejects
everything cannot pass this file. Envelopes are real signed JWS, built with the
helpers of tests/test_delegation_chain.py and held in a rollback transaction.
"""
import uuid

import pytest

from app.enforcement.acceptance_gate import _core_for_ancestor
from app.enforcement.delegation_chain import DelegationChainError, verify_delegation_chain
from tests.test_delegation_chain import (  # noqa: F401  (tx_conn is a fixture)
    _domains, _mtv, _new_agent, _rate, _register, _sign, _two_link, _vc, tx_conn,
)


async def _verify(conn, vc, jws, ancestors):
    return await verify_delegation_chain(
        vc, aae_jws=jws, ancestor_jws=ancestors, conn=conn,
        verify_core=_core_for_ancestor, signing_did=vc["issuer"])


async def _grandchild(conn, chain):
    """C holds a third link under the chain's child B."""
    b_priv, b_did = chain.agents["B"]
    c_priv, c_did, c_pub = _new_agent()
    await _register(conn, c_did, c_pub)
    grand = _vc(issuer=b_did, subject=c_did, actions=["read"],
                constraints=[_mtv(50), _domains(["a.example"]), _rate(2, "1h")],
                delegation={"delegator_did": b_did, "delegator_aae_id": chain.child_vc["id"],
                            "depth": 2, "max_depth": 2})
    return grand, _sign(b_priv, b_did, grand)


# ---------------- the honest chain still passes ----------------

async def test_honest_two_link_chain_is_accepted(tx_conn):
    chain = await _two_link(tx_conn)
    out = await _verify(tx_conn, chain.child_vc, chain.child_jws, [chain.root_jws])
    assert out["root_aae_id"] == chain.root_vc["id"]
    assert out["path"][-1] == chain.root_vc["id"]


async def test_honest_three_link_chain_is_accepted(tx_conn):
    chain = await _two_link(tx_conn)
    grand, grand_jws = await _grandchild(tx_conn, chain)
    out = await _verify(tx_conn, grand, grand_jws, [chain.child_jws, chain.root_jws])
    assert out["chain_length"] == 2
    assert out["root_aae_id"] == chain.root_vc["id"]


async def test_an_unrelated_supplied_ancestor_does_not_change_the_result(tx_conn):
    chain = await _two_link(tx_conn)
    other = await _two_link(tx_conn)
    out = await _verify(tx_conn, chain.child_vc, chain.child_jws,
                        [other.root_jws, chain.root_jws])
    assert out["path"] == [chain.child_vc["id"], chain.root_vc["id"]]


# ---------------- case 1: over-cap sub-grant, no ancestors ----------------

async def test_case1_over_cap_subgrant_without_ancestors_is_rejected(tx_conn):
    def over_cap(child, root, root_jws):
        # 10 000 against the root's 500; with the root supplied this fails monotonicity.
        child["credentialSubject"]["aae"]["constraints"][0] = _mtv(10_000)
    chain = await _two_link(tx_conn, child_patch=over_cap)
    with pytest.raises(DelegationChainError, match="neither supplied inline nor named"):
        await _verify(tx_conn, chain.child_vc, chain.child_jws, [])
    with pytest.raises(DelegationChainError, match="numeric upper bound"):
        await _verify(tx_conn, chain.child_vc, chain.child_jws, [chain.root_jws])


# ---------------- case 2: root left out ----------------

async def test_case2_root_left_out_is_rejected(tx_conn):
    chain = await _two_link(tx_conn)
    grand, grand_jws = await _grandchild(tx_conn, chain)
    with pytest.raises(DelegationChainError, match=chain.root_vc["id"]):
        await _verify(tx_conn, grand, grand_jws, [chain.child_jws])


# ---------------- case 3: two AAEs naming each other, no root ----------------

async def _mutual_pair(conn):
    p_priv, p_did, p_pub = _new_agent()
    q_priv, q_did, q_pub = _new_agent()
    await _register(conn, p_did, p_pub)
    await _register(conn, q_did, q_pub)
    x_id, y_id = f"urn:uuid:{uuid.uuid4()}", f"urn:uuid:{uuid.uuid4()}"
    constraints = [_mtv(100), _domains(["a.example"]), _rate(5, "1h")]
    # X is held by P and issued by Q, who holds Y; Y is issued by P, who holds X.
    # Depths are chosen so the X -> Y link passes on its own.
    x = _vc(aae_id=x_id, issuer=q_did, subject=p_did, actions=["read"],
            constraints=constraints,
            delegation={"delegator_did": q_did, "delegator_aae_id": y_id,
                        "depth": 2, "max_depth": 2})
    y = _vc(aae_id=y_id, issuer=p_did, subject=q_did, actions=["read"],
            constraints=constraints,
            delegation={"delegator_did": p_did, "delegator_aae_id": x_id,
                        "depth": 1, "max_depth": 2})
    return x, _sign(q_priv, q_did, x), y, _sign(p_priv, p_did, y)


async def test_case3_mutual_reference_with_counterpart_is_rejected(tx_conn):
    x, x_jws, y, y_jws = await _mutual_pair(tx_conn)
    with pytest.raises(DelegationChainError, match="cycle"):
        await _verify(tx_conn, x, x_jws, [y_jws])


async def test_case3_mutual_reference_without_counterpart_is_rejected(tx_conn):
    x, x_jws, y, y_jws = await _mutual_pair(tx_conn)
    with pytest.raises(DelegationChainError, match="neither supplied inline nor named"):
        await _verify(tx_conn, x, x_jws, [])


async def test_case3_presenting_either_side_is_rejected(tx_conn):
    x, x_jws, y, y_jws = await _mutual_pair(tx_conn)
    with pytest.raises(DelegationChainError):
        await _verify(tx_conn, y, y_jws, [x_jws])


# ---------------- case 4: self-reference, no root ----------------

async def test_case4_self_reference_without_root_is_rejected(tx_conn):
    def self_ref(child, root, root_jws):
        child["credentialSubject"]["aae"]["mandate"]["delegation"]["delegator_aae_id"] = child["id"]
    chain = await _two_link(tx_conn, child_patch=self_ref)
    with pytest.raises(DelegationChainError, match="cycle"):
        await _verify(tx_conn, chain.child_vc, chain.child_jws, [])
