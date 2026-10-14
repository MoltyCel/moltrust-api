"""registry-proof.json: an agent row carries its proof, not its bucket.

Which round a DID was registered in is a statement about a third party. The
file keeps the split as an aggregate (`by_bucket`, `totals`) and drops it from
the row, while everything a reader needs to check a DID against the chain stays
on that row: the DID, the Merkle path, the root and the Base transaction.

No database: `_psql` is replaced by a fixed set of query rows.
"""
import hashlib
import json
import pathlib
import sys

import pytest

from app import registry_export

BUCKET_VALUES = {"bounty", "organic", "own_test"}


def _anchor(n, ctype="TrustScoreCredential"):
    # A one-step path that replays: root = sha256(leaf || sibling).
    leaf, sibling = f"{n:064x}", f"{n + 2000:064x}"
    root = hashlib.sha256(bytes.fromhex(leaf) + bytes.fromhex(sibling)).hexdigest()
    return {
        "credential_id": n,
        "credential_type": ctype,
        "issued_at": f"2026-10-0{n % 9 + 1}T00:00:00+00:00",
        "anchor_tx": f"0x{n:064x}",
        "merkle_proof": {"leaf": leaf, "root": root,
                         "path": [{"hash": sibling, "position": "right"}]},
    }


def _raw(bucket, n, activated=False):
    return {
        "bucket": bucket,
        "did": f"did:moltrust:{n:016x}",
        "registered": "2026-09-01T00:00:00+00:00",
        "before_telemetry_cutoff": False,
        "activated": activated,
        "activated_endpoint_prefix": "/skill" if activated else None,
        "last_independent_call_day": "2026-10-01" if activated else None,
        "anchors": [_anchor(n)],
    }


RAW = [
    _raw("bounty", 1, activated=True),
    _raw("bounty", 2),
    _raw("organic", 3, activated=True),
    _raw("own_test", 4),
    _raw("partner", 5),
    _raw("partner", 6, activated=True),
]


@pytest.fixture
def doc(monkeypatch):
    monkeypatch.setattr(registry_export, "_psql",
                        lambda as_of, sql_path=None: json.loads(json.dumps(RAW)))
    return registry_export.build("2026-10-13T00:00:00+00:00")


def _agent_rows(doc):
    rows = [r for r in doc["rows"] if r["kind"] == "agent"]
    assert len(rows) == 4
    return rows


def test_no_agent_row_carries_a_bucket_label(doc):
    for row in _agent_rows(doc):
        assert "bucket" not in row
        assert not BUCKET_VALUES & {v for v in row.values() if isinstance(v, str)}
        assert not any(v in json.dumps(row) for v in
                       ('"bounty"', '"organic"', '"own_test"'))


def test_agent_rows_keep_the_did_and_the_full_proof(doc):
    for row in _agent_rows(doc):
        assert row["did"].startswith("did:moltrust:")
        assert row["anchors"]
        for a in row["anchors"]:
            assert a["anchor_tx"]
            mp = a["merkle_proof"]
            assert mp["leaf"] and mp["root"] and mp["path"]
            assert a["credential_type"] and a["credential_id"] and a["issued_at"]
        for key in ("registered", "before_telemetry_cutoff", "activated",
                    "activated_endpoint_prefix", "last_independent_call_day"):
            assert key in row


def test_the_split_stays_as_an_aggregate(doc):
    assert doc["by_bucket"]["bounty"]["dids"] == 2
    assert doc["by_bucket"]["organic"]["dids"] == 1
    assert doc["by_bucket"]["own_test"]["dids"] == 1
    assert doc["by_bucket"]["partner"]["dids"] == 2
    assert doc["totals"]["registered_with_anchor"] == 5
    assert doc["totals"]["activated_counted"] == 3


def test_the_partner_row_is_unchanged(doc):
    agg = [r for r in doc["rows"] if r["kind"] == "aggregate"]
    assert len(agg) == 1
    assert agg[0]["bucket"] == "partner"
    assert agg[0]["dids"] == 2 and agg[0]["activated"] == 1
    assert "did" not in agg[0]
    assert all("credential_id" not in a for a in agg[0]["anchors"])


def _script(monkeypatch):
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).resolve().parents[1]))
    from scripts import registry_proof_export
    return registry_proof_export


def test_the_export_script_writes_rows_without_a_bucket(doc, monkeypatch, tmp_path):
    registry_proof_export = _script(monkeypatch)
    monkeypatch.setattr(registry_proof_export.registry_export, "build",
                        lambda as_of=None: doc)
    out = tmp_path / "registry-proof.json"
    monkeypatch.setattr(sys, "argv", ["registry_proof_export.py", "--out", str(out)])
    assert registry_proof_export.main() == 0
    written = json.loads(out.read_text())
    assert all("bucket" not in r for r in written["rows"] if r["kind"] == "agent")


def test_the_export_script_refuses_an_agent_row_with_a_bucket(doc, monkeypatch, tmp_path):
    registry_proof_export = _script(monkeypatch)
    for row in doc["rows"]:
        if row["kind"] == "agent":
            row["bucket"] = "bounty"
            break
    monkeypatch.setattr(registry_proof_export.registry_export, "build",
                        lambda as_of=None: doc)
    out = tmp_path / "registry-proof.json"
    monkeypatch.setattr(sys, "argv", ["registry_proof_export.py", "--out", str(out)])
    with pytest.raises(SystemExit):
        registry_proof_export.main()
    assert not out.exists()


def test_public_count_still_excludes_own_test_from_the_series(monkeypatch):
    from app import public_count

    raw = RAW + [dict(_raw("own_test", 7), before_telemetry_cutoff=True,
                      registered="2026-09-02T00:00:00+00:00")]
    monkeypatch.setattr(registry_export, "_psql",
                        lambda as_of, sql_path=None: json.loads(json.dumps(raw)))
    got = public_count.read("2026-10-13T00:00:00+00:00")
    assert got["per_day"] == [{"day": "2026-09-01T00:00:00+00:00", "registered": 3}]
    assert got["counts"]["unmeasured"] == 0
    assert got["counts"]["deducted"] == 2


def test_the_public_verifier_accepts_rows_without_a_bucket(doc, monkeypatch, tmp_path, capsys):
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import registry_proof

    path = tmp_path / "registry-proof.json"
    path.write_text(json.dumps(doc))
    monkeypatch.setattr(sys, "argv", ["registry_proof.py", "--file", str(path), "--skip-chain"])
    assert registry_proof.main() == 0, capsys.readouterr().out


def test_the_public_verifier_still_catches_a_wrong_anchor_count(doc, monkeypatch, tmp_path, capsys):
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import registry_proof

    doc["by_bucket"]["partner"]["anchors"] += 1
    path = tmp_path / "registry-proof.json"
    path.write_text(json.dumps(doc))
    monkeypatch.setattr(sys, "argv", ["registry_proof.py", "--file", str(path), "--skip-chain"])
    assert registry_proof.main() == 1
    out = capsys.readouterr().out
    assert "by_bucket counts 7 anchors, rows hold 6" in out
    assert "bucket partner: header counts 3 anchors, its aggregate row holds 2" in out
