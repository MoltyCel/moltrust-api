"""The reuse answer of POST /credentials/track-record (ops C1, 2026-10-12).

The credential goes back as it was signed, without `evidence`; the anchor sits
beside it and carries what credential_anchors holds.
"""
import datetime as dt
import json

from app.main import _reused_track_record_response

PROOF = {"leaf": "aa" * 32, "path": [{"hash": "bb" * 32, "position": "right"}], "root": "cc" * 32}


def existing(tx="0x" + "dd" * 32):
    return {"issued_at": dt.datetime(2026, 10, 10, 12, 0), "tx_hash": tx,
            "anchored_at": dt.datetime(2026, 10, 10, 14, 0) if tx else None,
            "block": 51900000 if tx else None, "merkle_root": PROOF["root"] if tx else None,
            "merkle_proof": json.dumps(PROOF) if tx else None}


SIGNED = {"type": ["VerifiableCredential", "TrackRecordCredential"],
          "credentialSubject": {"id": "did:moltrust:x", "nonce": 3}, "proof": {"proofValue": "00"}}


def test_credential_goes_back_without_evidence_and_unchanged_otherwise():
    stored = dict(SIGNED, evidence=[{"type": ["MerkleBatchAnchor2026"]}])
    r = _reused_track_record_response(existing(), stored, {"nonce": 3})
    assert r["credential"] == SIGNED
    assert "evidence" in stored  # the caller's object is not mutated


def test_anchor_carries_the_table_fields():
    r = _reused_track_record_response(existing(), dict(SIGNED), {"nonce": 3})
    a = r["anchor"]
    assert a["status"] == "anchored"
    assert a["tx"] == a["tx_hash"] == "0x" + "dd" * 32
    assert a["block"] == 51900000
    assert a["merkle_root"] == PROOF["root"]
    assert a["leaf"] == PROOF["leaf"]
    assert a["chain"] == "eip155:8453" and a["type"] == "MerkleBatchAnchor2026"
    assert a["explorer"].endswith("0x" + "dd" * 32)


def test_pending_anchor_has_no_invented_values():
    r = _reused_track_record_response(existing(tx=None), dict(SIGNED), {"nonce": 3})
    a = r["anchor"]
    assert a["status"] == "pending"
    assert a["tx_hash"] is None and a["merkle_proof"] is None and a["leaf"] is None
    assert a["explorer"] is None
