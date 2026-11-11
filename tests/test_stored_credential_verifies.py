"""A stored credential verifies against its own verificationMethod (ops C1).

Until 2026-10-12 the anchor batch wrote `evidence` into `raw_vc` after the
issuer had signed it, and every stored credential stopped verifying: 0 of 716
as stored, 702 once `evidence` was removed. These tests hold three things:
the batch leaves the stored document byte for byte as issued, the issued
document verifies with the public key its proof names, and the live check in
scripts/credentials_verify.py reports a document with an appended field while
staying silent on a clean one.
"""
import asyncio
import copy
import json

import pytest
from nacl.signing import SigningKey

from app.crypto.hybrid import verify_proof
from app.provenance import anchor
from scripts import credentials_verify as cv

TEST_KEY = SigningKey(bytes(range(32)))


@pytest.fixture()
def issued(monkeypatch):
    from app import credentials
    monkeypatch.setattr(credentials, "get_signing_key", lambda: TEST_KEY)
    vc = credentials.issue_credential("did:moltrust:aaaaaaaaaaaaaaaa", "AgentTrustCredential",
                                      {"trustScore": 72})
    return vc


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    async def fetch(self, sql, *args):
        return self.rows

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


def run_batch(monkeypatch, rows):
    monkeypatch.setenv("BASE_RPC", "http://127.0.0.1:9")  # receipt lookup fails fast
    conn = FakeConn(rows)

    async def fake_anchor(calldata):
        return "0x" + "ab" * 32

    result = asyncio.run(anchor.anchor_credentials_batch(conn, fake_anchor))
    return conn, result


def test_the_batch_does_not_touch_the_stored_document(monkeypatch, issued):
    stored = copy.deepcopy(issued)
    rows = [{"id": 1, "subject_did": "did:moltrust:aaaaaaaaaaaaaaaa",
             "credential_type": "AgentTrustCredential", "issued_at": "2026-10-12T00:00:00",
             "proof_value": "00"}]
    conn, result = run_batch(monkeypatch, rows)
    assert result["status"] == "anchored" and result["batched"] == 1
    statements = [sql for sql, _ in conn.executed]
    assert any("INSERT INTO credential_anchors" in s for s in statements)
    assert not any("credentials" in s and "UPDATE" in s.upper() for s in statements)
    assert not any("raw_vc" in s or "evidence" in s for s in statements)
    assert json.dumps(stored, sort_keys=True) == json.dumps(issued, sort_keys=True)


def test_the_issued_document_verifies_with_the_key_its_proof_names(issued):
    proof = cv.first_proof(issued)
    keys = {proof["verificationMethod"]: bytes(TEST_KEY.verify_key)}
    assert cv.verifies(issued, keys)
    assert verify_proof(issued, TEST_KEY.verify_key)["valid"]


def test_the_check_reports_an_appended_field_and_stays_silent_on_a_clean_one(issued):
    proof = cv.first_proof(issued)
    keys = {proof["verificationMethod"]: bytes(TEST_KEY.verify_key)}
    with_evidence = dict(issued, evidence=[{"type": ["MerkleBatchAnchor2026"]}])
    rows = [(1, issued), (2, with_evidence)]
    assert cv.failing_ids(rows, keys, frozenset()) == [2]
    assert cv.failing_ids(rows, keys, frozenset({2})) == []


def test_a_proof_naming_an_unknown_method_fails(issued):
    assert not cv.verifies(issued, {"did:web:api.moltrust.ch#other": bytes(TEST_KEY.verify_key)})


def test_the_published_multibase_decodes_to_an_ed25519_key():
    key = cv.ed25519_key_from_multibase("z6MktwcfvxeKmXstWpyEr9wJkJE2xzzkpBkdCSghdvCzrqDC")
    assert len(key) == 32
    doc = {"verificationMethod": [
        {"id": "did:web:api.moltrust.ch#key-ed25519", "type": "Ed25519VerificationKey2020",
         "publicKeyMultibase": "z6MktwcfvxeKmXstWpyEr9wJkJE2xzzkpBkdCSghdvCzrqDC"},
        {"id": "did:web:api.moltrust.ch#pqc", "type": "JsonWebKey2020"}]}
    assert cv.keys_from_did_document(doc) == {"did:web:api.moltrust.ch#key-ed25519": key}


def test_the_fourteen_are_named_once():
    assert cv.KNOWN_UNVERIFIABLE == frozenset(list(range(925, 938)) + [940])
