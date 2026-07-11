"""Sprint 3 (feat/anchors-incidents): /anchors/batch + /compliance/incident.

Unit: Art 73 deadline mapping, deadline status, Merkle root/proof.
Integration: both endpoints — happy + validation + auth (sandbox DB).
"""
import datetime
import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import compliance as C
from app.provenance.anchor import merkle_root, merkle_proof


# --- Unit: Art 73 deadlines -------------------------------------------------
def test_incident_deadlines_match_spec():
    base = datetime.datetime(2026, 7, 11, 12, 0, 0)
    assert C.incident_deadline("death", base)["deadline_days"] == 10          # Art 73(4)
    assert C.incident_deadline("critical_infrastructure", base)["deadline_days"] == 2  # 73(3)
    assert C.incident_deadline("widespread_infringement", base)["deadline_days"] == 2
    assert C.incident_deadline("fundamental_rights", base)["deadline_days"] == 15  # 73(2)
    assert C.incident_deadline("health_harm", base)["deadline_days"] == 15
    d = C.incident_deadline("critical_infrastructure", base)
    assert "Art 73(3)" in d["art73_rule"] and "Art 3(49)(b)" in d["art3_definition"]
    assert d["reporting_deadline"].startswith("2026-07-13")  # +2 days


def test_deadline_status():
    now = datetime.datetime(2026, 7, 11, 12, 0, 0)
    future = (now + datetime.timedelta(days=5)).isoformat() + "Z"
    past = (now - datetime.timedelta(days=1)).isoformat() + "Z"
    assert C.deadline_status(future, now)["status"] == "on_track"
    s = C.deadline_status(past, now)
    assert s["overdue"] is True and s["status"] == "overdue"


# --- Unit: Merkle -----------------------------------------------------------
def _h(x):
    return hashlib.sha256(x).hexdigest()


def test_merkle_root_and_proof_reconstruct():
    hexes = [_h(f"leaf{i}".encode()) for i in range(3)]
    leaves = [bytes.fromhex(h) for h in hexes]
    root = merkle_root(leaves).hex()
    # verify proof for leaf 0 reconstructs the root
    proof = merkle_proof(leaves, 0)
    h = leaves[0]
    for step in proof:
        sib = bytes.fromhex(step["hash"])
        h = hashlib.sha256(sib + h).digest() if step["position"] == "left" else hashlib.sha256(h + sib).digest()
    assert h.hex() == root


def test_pricing_map_sprint3():
    from app.credits import get_endpoint_cost
    assert get_endpoint_cost("POST", "/anchors/batch") == 2
    assert get_endpoint_cost("POST", "/compliance/incident") == 2


# --- Integration ------------------------------------------------------------
async def test_incident_happy(async_client, credit_test_agent):
    did, api_key = await credit_test_agent(balance=1000)
    resp = await async_client.post(
        "/compliance/incident", headers={"X-API-Key": api_key},
        json={"did": did, "category": "death", "severity": "critical",
              "description": "test", "awareness_date": "2026-07-11T00:00:00"})
    assert resp.status_code == 200, resp.text[:300]
    b = resp.json()
    assert b["deadline_days"] == 10
    assert b["reporting_deadline"].startswith("2026-07-21")
    assert "Art 73(4)" in b["art73_rule"]
    assert "deadline_status" in b
    # cleanup
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM compliance_incidents WHERE did = $1", did)


async def test_incident_validation(async_client, credit_test_agent):
    did, api_key = await credit_test_agent(balance=1000)
    resp = await async_client.post(
        "/compliance/incident", headers={"X-API-Key": api_key},
        json={"did": did, "category": "not_a_category"})
    assert resp.status_code == 422


async def test_incident_auth(async_client):
    resp = await async_client.post(
        "/compliance/incident",
        json={"did": "did:moltrust:" + "a" * 16, "category": "death"})
    assert resp.status_code in (401, 403, 422)


async def test_anchors_batch_happy(async_client, credit_test_agent):
    _, api_key = await credit_test_agent(balance=1000)
    hexes = [_h(f"x{i}".encode()) for i in range(4)]
    resp = await async_client.post(
        "/anchors/batch", headers={"X-API-Key": api_key},
        json={"hashes": hexes})
    assert resp.status_code == 200, resp.text[:300]
    b = resp.json()
    assert len(b["merkle_root"]) == 64
    assert b["leaf_count"] == 4 and len(b["proofs"]) == 4
    assert b["tx_hash"] is None and b["anchor_status"] == "computed"


async def test_anchors_batch_bad_hex(async_client, credit_test_agent):
    _, api_key = await credit_test_agent(balance=1000)
    resp = await async_client.post(
        "/anchors/batch", headers={"X-API-Key": api_key}, json={"hashes": ["nothex!!"]})
    assert resp.status_code == 400


async def test_anchors_batch_auth(async_client):
    resp = await async_client.post("/anchors/batch", json={"hashes": [_h(b"a")]})
    assert resp.status_code in (401, 403, 422)
