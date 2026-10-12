"""The evidence migration: what it counts, what it changes, when it refuses."""
import copy
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nacl.signing import SigningKey  # noqa: E402

from app.crypto.hybrid import dual_sign  # noqa: E402
from scripts import migrate_credentials_drop_evidence as m  # noqa: E402

ISSUER = "did:web:api.moltrust.ch"
SK = SigningKey(bytes.fromhex("44" * 32))
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _mb(pub):
    raw = b"\xed\x01" + pub
    n, s = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    return "z" + s


KEYS = m.keys_from_did_doc({"verificationMethod": [
    {"id": f"{ISSUER}#key-ed25519", "type": "Ed25519VerificationKey2020",
     "publicKeyMultibase": _mb(bytes(SK.verify_key))},
    {"id": f"{ISSUER}#key-1", "type": "Ed25519VerificationKey2020",
     "publicKeyMultibase": _mb(bytes(SK.verify_key))},
]})


def _vc(i, ctype="TrackRecordCredential", sk=SK):
    return dual_sign({
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "type": ["VerifiableCredential", ctype],
        "issuer": ISSUER,
        "validFrom": "2026-10-01T00:00:00Z",
        "credentialSubject": {"id": f"did:moltrust:{i:016x}"},
    }, sk)


def _with_evidence(vc):
    out = copy.deepcopy(vc)
    out["evidence"] = [{"type": ["MerkleBatchAnchor2026"], "chain": "eip155:8453", "credentialId": 1}]
    return out


def test_the_did_document_yields_both_methods():
    assert set(KEYS) == {f"{ISSUER}#key-ed25519", f"{ISSUER}#key-1"}


def test_a_stored_copy_with_evidence_fails_and_without_passes():
    signed = _vc(1)
    assert m.verifies(signed, KEYS)
    assert not m.verifies(_with_evidence(signed), KEYS)
    assert m.verifies(m.without_evidence(_with_evidence(signed)), KEYS)


def test_plan_updates_only_rows_with_evidence():
    rows = [(1, _with_evidence(_vc(1))), (2, _vc(2))]
    update, known_bad, bad = m.plan(rows, KEYS)
    assert update == [1] and known_bad == [] and bad == []


def test_plan_names_an_unknown_failure():
    """A row that does not verify after the removal and is not one of the 14 stops the run."""
    foreign = _with_evidence(_vc(3, sk=SigningKey(bytes.fromhex("55" * 32))))
    _, _, bad = m.plan([(3, foreign)], KEYS)
    assert bad == [3]


def test_plan_lets_the_known_fourteen_through():
    foreign = _with_evidence(_vc(925, sk=SigningKey(bytes.fromhex("55" * 32))))
    update, known_bad, bad = m.plan([(925, foreign)], KEYS)
    assert update == [925] and known_bad == [925] and bad == []


def test_ipfs_counts_show_the_gap_closing():
    signed = _vc(1)
    pinned = {1: signed}
    assert m.ipfs_counts([(1, _with_evidence(signed))], pinned)["differ"] == 1
    assert m.ipfs_counts([(1, signed)], pinned) == {
        "pinned": 1, "equal_to_stored": 1, "differ": 0, "differing_ids": []}


def test_ipfs_counts_skip_rows_without_a_pin():
    assert m.ipfs_counts([(1, _vc(1))], {})["pinned"] == 0


def test_the_commitment_is_the_one_main_computes():
    """sha256(json.dumps(vc, sort_keys=True)), app/main.py /compliance/declaration."""
    import hashlib
    import json
    vc = _vc(1, "MolTrustConformityDeclaration")
    assert m.commitment(vc) == hashlib.sha256(json.dumps(vc, sort_keys=True).encode()).hexdigest()


def test_conformity_counts_show_the_gap_closing():
    signed = _vc(7, "MolTrustConformityDeclaration")
    types = {7: "MolTrustConformityDeclaration", 8: "TrackRecordCredential"}
    before = m.conformity_counts([(7, _with_evidence(signed)), (8, _with_evidence(_vc(8)))], types)
    after = m.conformity_counts([(7, signed), (8, _vc(8))], types)
    assert before == {"declarations": 1, "equal_to_signed_form": 0, "differ": 1}
    assert after == {"declarations": 1, "equal_to_signed_form": 1, "differ": 0}


def test_signature_counts():
    rows = [(1, _with_evidence(_vc(1))), (2, _vc(2))]
    assert m.signature_counts(rows, KEYS) == {"rows": 2, "with_evidence": 1, "verify_as_stored": 1}


def test_the_known_list_is_the_fourteen():
    assert sorted(m.KNOWN_UNVERIFIABLE) == [925, 926, 927, 928, 929, 930, 931, 932, 933,
                                            934, 935, 936, 937, 940]


def test_default_is_a_dry_run():
    src = open(m.__file__).read()
    assert 'add_argument("--apply", action="store_true"' in src
    assert "if not apply:\n        con.rollback()" in src
