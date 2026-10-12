"""The reuse branch of /credentials/track-record: credential as signed, anchor beside it.

Between 03. and 04.10.2026 the branch returned 80 stored copies, 15 of them
with an `evidence` block the anchor batch had written in after signing. Those
15 do not verify for their holders. The anchor now travels in `anchor`, with
the two constants the old block carried, and the credential goes back as signed.
"""
import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "app" / "main.py").read_text()


def _ns():
    ns = {"json": json}
    exec(MAIN[MAIN.index("ANCHOR_CALLDATA_FORMAT = "):MAIN.index("def _stored_anchor")], ns)
    exec(MAIN[MAIN.index("def _stored_anchor"):MAIN.index("def _stored_anchor")
              + MAIN[MAIN.index("def _stored_anchor"):].index("\n\n\n")], ns)
    return ns


ROW = {
    "id": 1594,
    "tx_hash": "0x6335958a04770c5bcf31bdb27a168c2affcc8eb99ec8037a34679d367971d943",
    "block": 52296129,
    "anchored_at": datetime.datetime(2026, 10, 4, 12, 0, tzinfo=datetime.timezone.utc),
    "merkle_root": "828871428c0635f6b3bfc08444250e3a8093216cbf9dc51b5e1c4d1e760bb1bc",
    "merkle_proof": json.dumps({"leaf": "b3b4", "path": [{"hash": "a2a1", "position": "right"}],
                                "root": "8288"}),
}


def test_the_anchor_carries_what_evidence_carried():
    a = _ns()["_stored_anchor"](ROW)
    assert a["type"] == "MerkleBatchAnchor2026"
    assert a["chain"] == "eip155:8453"
    assert a["credential_id"] == 1594
    assert a["tx_hash"] == ROW["tx_hash"] and a["block"] == 52296129
    assert a["merkle_root"] == ROW["merkle_root"]
    assert a["merkle_proof"]["path"] == [{"hash": "a2a1", "position": "right"}]
    assert a["leaf"] == "b3b4"


def test_the_first_field_name_stays_for_existing_callers():
    a = _ns()["_stored_anchor"](ROW)
    assert a["tx"] == a["tx_hash"]
    assert a["status"] == "anchored"


def test_names_follow_identity_resolve():
    a = _ns()["_stored_anchor"](ROW)
    for k in ("tx_hash", "block", "merkle_root", "merkle_proof", "leaf_rule",
              "calldata_format", "explorer"):
        assert k in a


def test_an_unanchored_credential_says_pending_and_names_nothing():
    row = dict(ROW, tx_hash=None, block=None, anchored_at=None, merkle_root=None,
               merkle_proof=None)
    a = _ns()["_stored_anchor"](row)
    assert a["status"] == "pending"
    assert a["tx_hash"] is None and a["merkle_proof"] is None and a["leaf"] is None
    assert a["explorer"] is None


def test_the_credential_goes_back_without_evidence():
    as_signed = _ns()["_as_signed"]
    vc = {"issuer": "did:web:api.moltrust.ch", "proof": {"proofValue": "ab"},
          "evidence": [{"type": ["MerkleBatchAnchor2026"]}]}
    out = as_signed(vc)
    assert "evidence" not in out
    assert out["proof"] == vc["proof"] and "evidence" in vc   # input left alone


def test_a_credential_without_evidence_comes_back_unchanged():
    as_signed = _ns()["_as_signed"]
    vc = {"issuer": "did:web:api.moltrust.ch", "proof": {"proofValue": "ab"}}
    assert as_signed(vc) == vc


def test_the_reuse_branch_uses_both():
    block = MAIN[MAIN.index("if same:"):]
    block = block[:block.index("vc = issue_credential(did, CREDENTIAL_TYPE")]
    assert '"credential": _as_signed(prior)' in block
    assert "_stored_anchor(existing)" in block
    assert '"credential": prior,' not in block


def test_the_query_selects_what_the_anchor_needs():
    q = MAIN[MAIN.index("SELECT c.id, c.raw_vc, c.issued_at, k.tx_hash, k.anchored_at"):]
    q = q[:q.index("FROM credentials c")]
    for col in ("k.block", "k.merkle_root", "k.merkle_proof"):
        assert col in q
