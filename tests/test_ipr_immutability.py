"""The April case, and the checks that would have caught it.

On 2026-04-20 commit 7f3c4d1 corrected the test-harness DID from
`did:moltrust:te5tharne550001` (not valid hex) to `did:moltrust:7e57da001e550001`
and applied it to rows that had already been anchored. Three leaves stopped
reproducing. Two further records in the same batch lost their proofs without
being touched at all, because a leaf sits in a tree.

Nothing recorded the change. These tests pin the two halves of the answer: a
verdict that is computed rather than asserted, and a leaf that cannot be edited
out from under its anchor.
"""
import json

from app.provenance.anchor import compute_leaf, merkle_root, merkle_proof, replay_proof
from app.provenance.ipr import LEAF_FIELDS, leaf_reproduces


OLD_DID = "did:moltrust:te5tharne550001"
NEW_DID = "did:moltrust:7e57da001e550001"
PRODUCED = "2026-04-19T22:20:29.273909+00:00"
OUTPUT = "sha256:e6b49ef46f99ebc230b2ad8c631c599b8e86413c9e2865d472e8b02c2dea0b4c"


def _anchored_record(did):
    """A record as it looked when anchored, with the proof it was given."""
    leaf = compute_leaf(OUTPUT, OLD_DID, PRODUCED, 1.0)
    leaves = [bytes.fromhex(leaf)]
    return {
        "output_hash": OUTPUT, "agent_did": did, "produced_at": PRODUCED,
        "confidence": 1.0, "anchor_status": "anchored",
        "merkle_proof": {"leaf": leaf, "root": merkle_root(leaves).hex(),
                         "index": 0, "siblings": merkle_proof(leaves, 0)},
    }


def test_the_april_case_is_detected():
    """The DID was rewritten after anchoring. The leaf stops following from the
    record, and that is exactly what the old verify never looked at."""
    untouched = _anchored_record(OLD_DID)
    assert leaf_reproduces(untouched) is True

    rewritten = _anchored_record(NEW_DID)
    assert leaf_reproduces(rewritten) is False


def test_the_old_verdict_would_have_passed_the_rewritten_record():
    """anchor_status plus a non-null proof column — both still true after the
    rewrite. That is why it went unnoticed for five months."""
    rewritten = _anchored_record(NEW_DID)
    assert rewritten["anchor_status"] == "anchored"
    assert rewritten["merkle_proof"] is not None
    # And yet:
    assert leaf_reproduces(rewritten) is False


def test_the_proof_itself_still_replays_after_a_rewrite():
    """The subtle part. Rewriting the record does not corrupt the proof — the
    proof is about the leaf it was issued for, and it still walks back to its
    root. Only the link between record and leaf is broken, so replaying the
    proof alone is not enough."""
    rewritten = _anchored_record(NEW_DID)
    assert replay_proof(rewritten["merkle_proof"]) is True
    assert leaf_reproduces(rewritten) is False


def test_collateral_damage_in_the_same_batch():
    """Two records in the April batch were never touched and still lost their
    proofs, because their sibling's leaf changed and the root moved with it."""
    good_a = compute_leaf("sha256:aaa", "did:moltrust:b5e9021b277d443c", PRODUCED, 1.0)
    good_b = compute_leaf("sha256:bbb", "did:moltrust:b5e9021b277d443c", PRODUCED, 1.0)
    tampered = compute_leaf(OUTPUT, OLD_DID, PRODUCED, 1.0)

    as_anchored = [bytes.fromhex(h) for h in (good_a, good_b, tampered)]
    anchored_root = merkle_root(as_anchored).hex()

    after = [bytes.fromhex(h) for h in
             (good_a, good_b, compute_leaf(OUTPUT, NEW_DID, PRODUCED, 1.0))]
    assert merkle_root(after).hex() != anchored_root, "the root has to move, or there is no damage"

    # The untouched record's proof was issued against the anchored root and no
    # longer reconstructs it from the batch as it now stands.
    reissued = {"leaf": good_a, "root": merkle_root(after).hex(),
                "index": 0, "siblings": merkle_proof(after, 0)}
    assert replay_proof(reissued) is True
    assert reissued["root"] != anchored_root


def test_replay_is_honest_about_what_it_cannot_check():
    assert replay_proof(None) is None
    assert replay_proof("not json") is None
    assert replay_proof({"leaf": "aa"}) is None
    assert leaf_reproduces({"merkle_proof": None, "output_hash": OUTPUT,
                            "agent_did": OLD_DID, "produced_at": PRODUCED,
                            "confidence": 1.0}) is None


def test_leaf_fields_are_the_four_the_trigger_guards():
    """The database trigger names these four. If the leaf formula grows a field
    and this list does not, the guard silently stops covering it."""
    assert LEAF_FIELDS == ("output_hash", "agent_did", "produced_at", "confidence")
    base = compute_leaf(OUTPUT, OLD_DID, PRODUCED, 1.0)
    assert compute_leaf("sha256:other", OLD_DID, PRODUCED, 1.0) != base
    assert compute_leaf(OUTPUT, NEW_DID, PRODUCED, 1.0) != base
    assert compute_leaf(OUTPUT, OLD_DID, "2026-04-20T00:00:00+00:00", 1.0) != base
    assert compute_leaf(OUTPUT, OLD_DID, PRODUCED, 0.9) != base
