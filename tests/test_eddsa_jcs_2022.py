"""eddsa-jcs-2022 (W3C Data Integrity) for MolTrust credentials, 2026-10-09.

The vector in tests/vectors/eddsa_jcs_2022_vector.json was produced by a
third-party implementation (Digital Bazaar vc 7.3 + data-integrity 2.5 +
eddsa-jcs-2022-cryptosuite 1.0) with a fixed seed. Our signer must produce
the same proofValue for the same input, and our verifier must accept it.
"""
import copy
import json
import pathlib

from nacl.signing import SigningKey

from app.crypto import hybrid
from app.crypto.proof_utils import get_ed25519_proof, is_eddsa_jcs_2022

VEC = json.loads((pathlib.Path(__file__).parent / "vectors" / "eddsa_jcs_2022_vector.json").read_text())
SK = SigningKey(bytes.fromhex(VEC["seed_hex"]))


def _unsigned():
    c = copy.deepcopy(VEC["credential"])
    c.pop("proof")
    return c


def test_our_signer_reproduces_the_third_party_vector():
    ours = hybrid.sign_eddsa_jcs_2022(_unsigned(), SK, created=VEC["credential"]["proof"]["created"])
    assert ours["proof"]["proofValue"] == VEC["credential"]["proof"]["proofValue"]
    assert ours["proof"] == VEC["credential"]["proof"]


def test_our_verifier_accepts_the_third_party_vector():
    r = hybrid.verify_proof(copy.deepcopy(VEC["credential"]), SK.verify_key)
    assert r["valid"] is True, r


def test_a_changed_claim_fails():
    c = copy.deepcopy(VEC["credential"])
    c["credentialSubject"]["verified"] = False
    assert hybrid.verify_proof(c, SK.verify_key)["valid"] is False


def test_evidence_added_after_signing_fails():
    c = copy.deepcopy(VEC["credential"])
    c["evidence"] = [{"type": "MerkleBatchAnchor2026"}]
    assert hybrid.verify_proof(c, SK.verify_key)["valid"] is False


def test_foreign_key_and_mismatched_context_fail():
    other = SigningKey(bytes(32))
    assert hybrid.verify_proof(copy.deepcopy(VEC["credential"]), other.verify_key)["valid"] is False
    c = copy.deepcopy(VEC["credential"])
    c["proof"]["verificationMethod"] = "did:web:api.moltrust.ch#key-ed25519-attacker"
    assert hybrid.verify_proof(c, SK.verify_key)["valid"] is False
    c = copy.deepcopy(VEC["credential"])
    c["proof"]["@context"] = ["https://www.w3.org/ns/credentials/v2"]
    assert hybrid.verify_proof(c, SK.verify_key)["valid"] is False


def test_hex_or_malformed_proof_value_fails():
    c = copy.deepcopy(VEC["credential"])
    c["proof"]["proofValue"] = "ab" * 64
    assert hybrid.verify_proof(c, SK.verify_key)["valid"] is False


def test_an_eddsa_proof_beside_another_proof_is_refused():
    c = copy.deepcopy(VEC["credential"])
    c["proof"] = [c["proof"], {"type": "Ed25519Signature2020", "verificationMethod": "did:web:api.moltrust.ch#key-ed25519", "proofValue": "00"}]
    r = hybrid.verify_proof(c, SK.verify_key)
    assert r["valid"] is False and "only proof" in r["error"]


def test_dual_sign_without_dilithium_emits_eddsa_jcs_2022(monkeypatch):
    monkeypatch.setattr(hybrid.dilithium, "is_available", lambda: False)
    c = hybrid.dual_sign(_unsigned(), SK)
    assert is_eddsa_jcs_2022(c["proof"])
    assert c["proof"]["proofValue"].startswith("z")
    assert get_ed25519_proof(c) is c["proof"]
    assert hybrid.verify_proof(c, SK.verify_key)["valid"] is True


def test_base58_roundtrip_with_leading_zeros():
    for b in (b"", b"\x00", b"\x00\x00\x01", bytes(range(64))):
        assert hybrid._b58decode(hybrid._b58encode(b)) == b
