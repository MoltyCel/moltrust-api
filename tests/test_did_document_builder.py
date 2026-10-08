"""The DID document marks deactivation (section 4.4) and keeps retired keys (4.3).

Needs app.main importable but no database: the builder takes a row.
"""
import datetime

DID = "did:moltrust:21fe31dfa154a261"


class _Row(dict):
    def keys(self):
        return super().keys()


def _row(**over):
    base = dict(did=DID, display_name="t", platform="test",
                created_at=datetime.datetime(2026, 10, 1), wallet_address=None,
                wallet_chain=None, wallet_bound_at=None, public_key_hex="aa" * 32,
                key_anchor_tx=None, key_anchor_block=None, erc8004_agent_id=None,
                revoked_at=None)
    base.update(over)
    return _Row(base)


def test_a_revoked_agent_is_marked_deactivated():
    from app.main import _build_did_document
    doc = _build_did_document(_row(revoked_at=datetime.datetime(2026, 10, 8)))
    assert doc["deactivated"] is True


def test_a_live_agent_carries_no_deactivated_member():
    from app.main import _build_did_document
    assert "deactivated" not in _build_did_document(_row())


def test_without_history_the_document_is_unchanged_in_shape():
    from app.main import _build_did_document
    doc = _build_did_document(_row())
    assert doc["verificationMethod"] == [{
        "id": f"{DID}#key-1", "type": "Ed25519VerificationKey2020",
        "controller": DID, "publicKeyHex": "aa" * 32}]
    assert doc["authentication"] == doc["assertionMethod"] == [f"{DID}#key-1"]


def test_a_retired_key_stays_and_only_the_key_in_force_is_referenced():
    from app.main import _build_did_document
    t = datetime.datetime(2026, 10, 8, tzinfo=datetime.timezone.utc)
    doc = _build_did_document(_row(public_key_hex="bb" * 32),
                              [{"key_index": 1, "public_key_hex": "aa" * 32, "revoked_at": t}])
    ids = [m["id"] for m in doc["verificationMethod"]]
    assert ids == [f"{DID}#key-2", f"{DID}#key-1"]
    assert doc["verificationMethod"][1]["revoked"] is True
    assert doc["authentication"] == [f"{DID}#key-2"]


def test_an_agent_without_a_key_has_no_verification_method():
    from app.main import _build_did_document
    doc = _build_did_document(_row(public_key_hex=None))
    assert "verificationMethod" not in doc and "authentication" not in doc
