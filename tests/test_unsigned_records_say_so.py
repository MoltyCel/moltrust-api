"""Records without a signature carry no proof (2026-10-09).

Violation records, music credentials and attestations were returned with a
proof block that held no signature under did:moltrust:registry#keys-1, a DID
that does not resolve. A block that looks like a proof is read as one.
"""
import pathlib
import re

SRC = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()


def test_the_unresolvable_registry_did_is_gone():
    assert "did:moltrust:registry" not in SRC


def test_no_placeholder_proof_value():
    assert not re.search(r'"proofValue"\s*:\s*"placeholder"', SRC)


def test_the_four_places_name_their_state():
    assert SRC.count('"signatureStatus": UNSIGNED_STATE') == 3   # violation, music build, music issue
    assert SRC.count('"signature_status": UNSIGNED_STATE') == 1  # attestation
    assert '"state": "unsigned"' in SRC


def test_the_real_trust_score_signature_is_untouched():
    assert 'score_response["registry_signature"] = sign_payload(signing_payload)' in SRC
