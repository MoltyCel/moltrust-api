"""A stored credential verifies against its own verificationMethod.

From 20.09. to 12.10.2026 the anchor batch wrote `evidence` into the stored VC
after signing. The signature does not cover it, so every stored credential
failed as stored (0 of 716 on 12.10.2026), and the reuse branch of
/credentials/track-record handed out 15 such copies. These tests pin the two
halves of the fix: the batch leaves the stored document alone, and the daily
check finds a document that no longer verifies.
"""
import asyncio
import copy
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nacl.signing import SigningKey  # noqa: E402

from app.crypto.hybrid import dual_sign, verify_proof  # noqa: E402
from app.crypto.proof_utils import get_primary_proof_value  # noqa: E402
from app.provenance import anchor  # noqa: E402
from scripts.credentials_verify_check import _method, check, keys_from_did_doc  # noqa: E402

ISSUER = "did:web:api.moltrust.ch"
SK = SigningKey(bytes.fromhex("22" * 32))
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _multibase(pub: bytes) -> str:
    raw = b"\xed\x01" + pub
    n, s = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    return "z" + "1" * (len(raw) - len(raw.lstrip(b"\0"))) + s


def _did_doc(sk=SK):
    mb = _multibase(bytes(sk.verify_key))
    return {"id": ISSUER, "verificationMethod": [
        {"id": f"{ISSUER}#key-ed25519", "type": "Ed25519VerificationKey2020",
         "controller": ISSUER, "publicKeyMultibase": mb},
        {"id": f"{ISSUER}#key-1", "type": "Ed25519VerificationKey2020",
         "controller": ISSUER, "publicKeyMultibase": mb},
    ]}


def _vc(subject="did:moltrust:00000000000000a1", sk=SK):
    vc = {
        "@context": ["https://www.w3.org/ns/credentials/v2",
                     "https://api.moltrust.ch/contexts/trust/v1"],
        "type": ["VerifiableCredential", "TrackRecordCredential"],
        "issuer": ISSUER,
        "validFrom": "2026-10-12T10:00:00Z",
        "validUntil": "2027-10-12T10:00:00Z",
        "credentialSubject": {"id": subject, "nonce": 7},
    }
    return dual_sign(vc, sk)


class _FakeConn:
    """Holds one stored row and records every statement the batch runs."""

    def __init__(self, cid, vc):
        self.sql = []
        self.stored = json.dumps(vc)
        self.row = {"id": cid, "subject_did": vc["credentialSubject"]["id"],
                    "credential_type": "TrackRecordCredential",
                    "issued_at": "2026-10-12T10:00:00",
                    "proof_value": get_primary_proof_value(vc)}

    async def fetch(self, q, *a):
        self.sql.append(q)
        return [self.row]

    async def execute(self, q, *a):
        self.sql.append(q)


@pytest.fixture
def no_chain(monkeypatch):
    """The batch asks a node for the block number; here there is none."""
    stub = types.ModuleType("web3")

    class Web3:
        HTTPProvider = staticmethod(lambda *a, **k: None)

        def __init__(self, *a, **k):
            raise RuntimeError("no node in tests")

    stub.Web3 = Web3
    monkeypatch.setitem(sys.modules, "web3", stub)


def _anchor_once(conn):
    async def fake_anchor(calldata):
        return "0x" + "ab" * 32
    return asyncio.run(anchor.anchor_credentials_batch(conn, fake_anchor))


def test_the_batch_leaves_the_stored_document_alone(no_chain):
    vc = _vc()
    conn = _FakeConn(1594, vc)
    before = conn.stored
    res = _anchor_once(conn)
    assert res["status"] == "anchored"
    assert not [q for q in conn.sql if "UPDATE credentials" in q or "raw_vc" in q]
    assert conn.stored == before


def test_the_anchor_still_goes_into_credential_anchors(no_chain):
    conn = _FakeConn(1594, _vc())
    _anchor_once(conn)
    assert [q for q in conn.sql if "INSERT INTO credential_anchors" in q]


def test_a_stored_copy_verifies_against_its_own_method():
    keys = keys_from_did_doc(_did_doc())
    vc = json.loads(json.dumps(_vc()))                # as it comes back from jsonb
    method = _method(vc)
    assert verify_proof(vc, keys[method])["valid"] is True


def test_the_check_finds_a_copy_with_evidence_appended():
    """The defect itself: one field after signing and the copy no longer verifies."""
    keys = keys_from_did_doc(_did_doc())
    good = _vc()
    bad = copy.deepcopy(good)
    bad["evidence"] = [{"type": ["MerkleBatchAnchor2026"], "chain": "eip155:8453"}]
    res = check([(1, good), (2, bad)], keys, known=frozenset())
    assert res["bad"] == [2]
    assert res["bad_with_evidence"] == 1


def test_the_check_is_silent_when_everything_verifies():
    keys = keys_from_did_doc(_did_doc())
    res = check([(i, _vc(f"did:moltrust:{i:016x}")) for i in range(1, 6)], keys,
                known=frozenset())
    assert res["bad"] == []
    assert res["checked"] == 5


def test_a_key_the_document_does_not_publish_fails():
    """Verifying with the key in force would pass this; an outside verifier would not."""
    keys = keys_from_did_doc(_did_doc())
    foreign = _vc(sk=SigningKey(bytes.fromhex("33" * 32)))
    assert check([(9, foreign)], keys, known=frozenset())["bad"] == [9]


def test_the_known_fourteen_are_left_out_and_counted():
    keys = keys_from_did_doc(_did_doc())
    bad = _vc()
    bad["evidence"] = []
    res = check([(925, bad), (1, _vc())], keys)
    assert res["bad"] == [] and res["known"] == 1


def test_a_document_without_ed25519_methods_yields_no_keys():
    assert keys_from_did_doc({"verificationMethod": [
        {"id": "x#pq", "type": "JsonWebKey2020", "publicKeyMultibase": "zabc"}]}) == {}
