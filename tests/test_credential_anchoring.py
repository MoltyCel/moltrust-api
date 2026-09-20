"""The anchor has to be recomputable by someone who does not trust us.

That is the whole argument for issuing a credential rather than asserting one,
so the Merkle proof is what these tests are about: given the leaf and the path
that ship inside the VC, a stranger reconstructs the root that went on chain.
"""
import hashlib

from app.provenance.anchor import (
    CREDENTIAL_CALLDATA_PREFIX,
    compute_credential_leaf,
    credential_merkle_proof,
    merkle_root,
    _credential_leaves,
)


def _records(n):
    return [{
        "id": i,
        "subject_did": f"did:moltrust:{i:016x}",
        "credential_type": "AgentTrustCredential",
        "issued_at": f"2026-09-{(i % 28) + 1:02d}T00:00:00",
        "proof_value": f"sig{i}",
    } for i in range(1, n + 1)]


def _replay(proof):
    cur = bytes.fromhex(proof["leaf"])
    for step in proof["path"]:
        sib = bytes.fromhex(step["hash"])
        cur = hashlib.sha256(sib + cur if step["position"] == "left" else cur + sib).digest()
    return cur.hex()


def test_a_proof_reconstructs_the_root():
    recs = _records(134)
    root = merkle_root([bytes.fromhex(h) for h in _credential_leaves(recs)]).hex()
    for idx in (0, 1, 77, 132, 133):
        p = credential_merkle_proof(recs, idx)
        assert p["root"] == root, f"index {idx} reports a different root"
        assert _replay(p) == root, f"index {idx} does not replay to the root"


def test_a_single_credential_still_has_a_root():
    """A batch of one is the common case right after a quiet period.

    A lone leaf is padded against itself, so the root is sha256(leaf+leaf) and
    the proof carries that duplicate as its one sibling. It still replays.
    """
    recs = _records(1)
    p = credential_merkle_proof(recs, 0)
    assert p["root"] != p["leaf"]
    assert len(p["path"]) == 1
    assert _replay(p) == p["root"]


def test_the_leaf_binds_every_field_a_verifier_compares():
    """Drop a field from the leaf and the anchor stops binding what it claims
    to bind — the same tx would then vouch for a different credential."""
    base = dict(cred_id="7", subject_did="did:moltrust:abc", credential_type="AgentTrustCredential",
                issued_at="2026-09-20T00:00:00", proof_value="sig")
    original = compute_credential_leaf(**base)
    for field, other in (("cred_id", "8"), ("subject_did", "did:moltrust:def"),
                         ("credential_type", "OtherCredential"),
                         ("issued_at", "2026-09-21T00:00:00"), ("proof_value", "forged")):
        changed = dict(base)
        changed[field] = other
        assert compute_credential_leaf(**changed) != original, f"{field} does not affect the leaf"


def test_calldata_prefix_is_distinct_from_the_ipr_one():
    """Both anchor kinds are self-sends carrying text. If they shared a prefix,
    nothing on chain would say which tree a root belongs to."""
    assert CREDENTIAL_CALLDATA_PREFIX == "MolTrust/VC/v1"
    assert "IPR" not in CREDENTIAL_CALLDATA_PREFIX


def test_every_leaf_of_an_odd_level_replays():
    """Regression for the padding bug in merkle_proof.

    _build_tree padded `current` but left the unpadded level in `levels`, so
    merkle_proof could not see the duplicated sibling and dropped it through its
    `sibling_idx < len(level)` guard. Any leaf at the end of an odd level then
    produced a proof that did not replay to the root it was issued against —
    IPRs included, since they share this code.

    Sizes chosen to force an odd level at several depths.
    """
    for n in (3, 5, 7, 9, 17, 133, 134, 135):
        recs = _records(n)
        root = merkle_root([bytes.fromhex(h) for h in _credential_leaves(recs)]).hex()
        for idx in range(n):
            p = credential_merkle_proof(recs, idx)
            assert _replay(p) == root, f"n={n} idx={idx} does not replay"


def test_the_fix_did_not_move_any_root():
    """Roots are already on chain. The proof had to be corrected without
    changing what a root is, or every anchored IPR would stop verifying."""
    import hashlib as _h

    def original_root(leaves):
        if not leaves:
            return b""
        if len(leaves) % 2 == 1:
            leaves = leaves + [leaves[-1]]
        current = leaves
        while len(current) > 1:
            if len(current) % 2 == 1:
                current = current + [current[-1]]
            current = [_h.sha256(current[i] + current[i + 1]).digest()
                       for i in range(0, len(current), 2)]
        return current[0]

    for n in list(range(1, 40)) + [133, 134, 135]:
        leaves = [_h.sha256(str(i).encode()).digest() for i in range(n)]
        assert merkle_root(leaves) == original_root(leaves), f"root moved at n={n}"
