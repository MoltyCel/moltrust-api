"""UCAN 0.10.0 delegation (Sprint 2: feat/delegation-ucan-batch).

Unit: attenuation, mint/verify roundtrip, chain alignment, tamper/expiry.
Integration: /delegation/create, /delegation/verify, /reputation/batch-sync
(happy + validation + auth) via async_client + credit_test_agent (sandbox DB).
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app import delegation as D

_TEST_SK = Ed25519PrivateKey.from_private_bytes(bytes((i * 7 + 3) % 256 for i in range(32)))
_TEST_PUB = _TEST_SK.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _install_test_key():
    D.get_private_key = lambda: _TEST_SK
    D.get_public_key_bytes = lambda: _TEST_PUB


_install_test_key()

DEL = "did:moltrust:" + "1" * 16
AUD = "did:moltrust:" + "2" * 16
CAP = {"moltrust://agent/task/": {"exec/run": [{}]}}


# --- Unit: attenuation ------------------------------------------------------
def test_attenuation_matrix():
    parent = {"r://x/": {"read": [{}]}}
    assert D.cap_is_narrower({"r://x/": {"read": [{}]}}, parent) is True
    assert D.cap_is_narrower({"r://x/": {"write": [{}]}}, parent) is False   # ability not in parent
    assert D.cap_is_narrower({"r://x/": {"read": [{"id": 1}]}}, parent) is True  # narrow {} -> {id}
    assert D.cap_is_narrower({"r://y/": {"read": [{}]}}, parent) is False    # new resource
    specific = {"r://x/": {"read": [{"id": 1}]}}
    assert D.cap_is_narrower({"r://x/": {"read": [{}]}}, specific) is False   # escalate [x]->[{}]


# --- Unit: mint / verify ----------------------------------------------------
def test_mint_verify_roundtrip():
    _install_test_key()
    tok = D.mint_ucan(delegator_did=DEL, audience_did=AUD, capabilities=CAP, ttl_seconds=600)
    res = D.verify_ucan(tok)
    assert res["valid"] is True, res["errors"]
    assert res["delegator"] == DEL and res["audience"] == AUD


def test_verify_expected_audience_mismatch():
    _install_test_key()
    tok = D.mint_ucan(delegator_did=DEL, audience_did=AUD, capabilities=CAP)
    res = D.verify_ucan(tok, expected_audience="did:moltrust:" + "9" * 16)
    assert res["valid"] is False
    assert res["checks"].get("audience") is False


def test_tampered_signature_fails():
    _install_test_key()
    tok = D.mint_ucan(delegator_did=DEL, audience_did=AUD, capabilities=CAP)
    h, p, s = tok.split(".")
    tampered = f"{h}.{p}.{('A' if s[0] != 'A' else 'B')}{s[1:]}"
    res = D.verify_ucan(tampered)
    assert res["valid"] is False
    assert res["checks"]["signature"] is False


def test_time_bounds_not_yet_valid():
    _install_test_key()
    # Deterministic: nbf far in the future -> not yet valid.
    future = int(time.time()) + 100000
    tok = D.mint_ucan(delegator_did=DEL, audience_did=AUD, capabilities=CAP,
                      ttl_seconds=3600, not_before=future)
    res = D.verify_ucan(tok)
    assert res["valid"] is False
    assert res["checks"]["time_bounds"] is False


def test_chain_valid_attenuated():
    _install_test_key()
    # parent: DEL delegates broad cap to MID; child: MID delegates narrower to AUD
    MID = "did:moltrust:" + "3" * 16
    parent = D.mint_ucan(delegator_did=DEL, audience_did=MID,
                         capabilities={"r://x/": {"read": [{}]}}, ttl_seconds=1000)
    child = D.mint_ucan(delegator_did=MID, audience_did=AUD,
                        capabilities={"r://x/": {"read": [{"scope": "a"}]}},
                        ttl_seconds=500, proofs=[parent])
    res = D.verify_ucan(child)
    assert res["valid"] is True, res["errors"]


def test_chain_escalation_rejected():
    _install_test_key()
    MID = "did:moltrust:" + "3" * 16
    parent = D.mint_ucan(delegator_did=DEL, audience_did=MID,
                         capabilities={"r://x/": {"read": [{"scope": "a"}]}}, ttl_seconds=1000)
    child = D.mint_ucan(delegator_did=MID, audience_did=AUD,
                        capabilities={"r://x/": {"read": [{}]}}, ttl_seconds=500, proofs=[parent])
    res = D.verify_ucan(child)
    assert res["valid"] is False
    assert res["checks"].get("chain") is False


def test_chain_broken_alignment_rejected():
    _install_test_key()
    # proof audience is SOMEONE ELSE, not the child's delegator
    other = "did:moltrust:" + "8" * 16
    parent = D.mint_ucan(delegator_did=DEL, audience_did=other,
                         capabilities={"r://x/": {"read": [{}]}}, ttl_seconds=1000)
    MID = "did:moltrust:" + "3" * 16
    child = D.mint_ucan(delegator_did=MID, audience_did=AUD,
                        capabilities={"r://x/": {"read": [{}]}}, ttl_seconds=500, proofs=[parent])
    res = D.verify_ucan(child)
    assert res["valid"] is False


def test_pricing_map_delegation_and_batch():
    from app.credits import get_endpoint_cost
    assert get_endpoint_cost("POST", "/delegation/create") == 2
    assert get_endpoint_cost("POST", "/delegation/verify") == 1
    assert get_endpoint_cost("POST", "/reputation/batch-sync") == 2


# --- Integration ------------------------------------------------------------
def _patch_app_delegation_key():
    from app import delegation as _d
    _d.get_private_key = lambda: _TEST_SK
    _d.get_public_key_bytes = lambda: _TEST_PUB


async def test_delegation_create_happy(async_client, credit_test_agent):
    _patch_app_delegation_key()
    did, api_key = await credit_test_agent(balance=1000)
    resp = await async_client.post(
        "/delegation/create", headers={"X-API-Key": api_key},
        json={"delegator_did": did, "audience_did": AUD, "capabilities": CAP, "ttl_seconds": 600})
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert body["ucan"].count(".") == 2
    assert body["ucv"] == "0.10.0"


async def test_delegation_create_validation(async_client, credit_test_agent):
    did, api_key = await credit_test_agent(balance=1000)
    resp = await async_client.post(
        "/delegation/create", headers={"X-API-Key": api_key},
        json={"delegator_did": did, "audience_did": AUD, "capabilities": {}})  # empty cap
    assert resp.status_code in (400, 422)


async def test_delegation_create_auth(async_client):
    resp = await async_client.post(
        "/delegation/create",
        json={"delegator_did": DEL, "audience_did": AUD, "capabilities": CAP})
    assert resp.status_code in (401, 403, 422)


async def test_delegation_create_rejects_non_delegator(async_client, credit_test_agent):
    _patch_app_delegation_key()
    did, api_key = await credit_test_agent(balance=1000)
    resp = await async_client.post(
        "/delegation/create", headers={"X-API-Key": api_key},
        json={"delegator_did": DEL, "audience_did": AUD, "capabilities": CAP})  # delegator != caller
    assert resp.status_code == 403


async def test_delegation_verify_happy(async_client, credit_test_agent):
    _patch_app_delegation_key()
    did, api_key = await credit_test_agent(balance=1000)
    tok = D.mint_ucan(delegator_did=did, audience_did=AUD, capabilities=CAP, ttl_seconds=600)
    resp = await async_client.post(
        "/delegation/verify", headers={"X-API-Key": api_key}, json={"token": tok})
    assert resp.status_code == 200, resp.text[:300]
    assert resp.json()["valid"] is True


async def test_delegation_verify_auth(async_client):
    resp = await async_client.post("/delegation/verify", json={"token": "x.y.z"})
    assert resp.status_code in (401, 403, 422)


async def test_batch_sync_happy(async_client, credit_test_agent):
    did, api_key = await credit_test_agent(balance=1000)
    resp = await async_client.post(
        "/reputation/batch-sync", headers={"X-API-Key": api_key},
        json={"dids": [did, "did:moltrust:" + "a" * 16]})
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert body["count"] == 2
    assert {r["did"] for r in body["results"]} == {did, "did:moltrust:" + "a" * 16}


async def test_batch_sync_validation(async_client, credit_test_agent):
    _, api_key = await credit_test_agent(balance=1000)
    resp = await async_client.post(
        "/reputation/batch-sync", headers={"X-API-Key": api_key}, json={"dids": []})
    assert resp.status_code == 422


async def test_batch_sync_auth(async_client):
    resp = await async_client.post("/reputation/batch-sync", json={"dids": ["did:moltrust:" + "a" * 16]})
    assert resp.status_code in (401, 403, 422)
