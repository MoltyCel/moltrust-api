"""The answer of POST /credentials/issue verifies unchanged (ops VC-1).

The issuer signs the credential, then two things are added to the answer next
to it: `ipfs_cid`/`ipfs_url` when the IPFS publish succeeds, and `renew_at`
from the renewal middleware on every /credentials/ answer. None of the three
is covered by the proof. Until VC-1, a holder that sent the answer back to
POST /credentials/verify as it came got valid=false, for every credential.

Runs against the sandbox database: issuing writes a row to `credentials` and
the issuer's credit balance, as in production.
"""
import os

import pytest

ISSUE_FIELDS = ("renew_at", "ipfs_cid", "ipfs_url")


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    # The credential key falls back to ~/.moltrust_did_private_key when this is
    # unset; on the server that file is the real issuer key.
    monkeypatch.setenv("DID_PRIVATE_KEY_HEX", os.urandom(32).hex())
    monkeypatch.delenv("DID_PRIVATE_KEY_ENCRYPTED", raising=False)


@pytest.fixture(autouse=True)
def _no_ip_limit():
    from app.main import limiter
    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


def _ipfs(monkeypatch, cid):
    import app.main as m
    monkeypatch.setattr(m, "publish_to_ipfs", lambda vc: cid)


async def _issue(client, key, did):
    r = await client.post("/credentials/issue", json={"subject_did": did},
                          headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return r.json()


async def _verify(client, credential):
    r = await client.post("/credentials/verify", json={"credential": credential})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_the_answer_with_ipfs_fields_verifies_unchanged(async_client, credit_test_agent,
                                                             monkeypatch):
    _ipfs(monkeypatch, "bafyroundtriptestcid")
    did, key = await credit_test_agent()
    answer = await _issue(async_client, key, did)
    assert set(ISSUE_FIELDS) <= set(answer)

    result = await _verify(async_client, answer)
    assert result["valid"] is True, result
    assert result["subject"] == did
    assert result["unsigned_fields"] == sorted(ISSUE_FIELDS)


@pytest.mark.asyncio
async def test_the_answer_without_ipfs_fields_verifies_unchanged(async_client, credit_test_agent,
                                                                monkeypatch):
    _ipfs(monkeypatch, None)
    did, key = await credit_test_agent()
    answer = await _issue(async_client, key, did)
    assert "ipfs_cid" not in answer and "ipfs_url" not in answer
    assert "renew_at" in answer

    result = await _verify(async_client, answer)
    assert result["valid"] is True, result
    assert result["unsigned_fields"] == ["renew_at"]


@pytest.mark.asyncio
async def test_the_signed_credential_alone_verifies_with_nothing_set_aside(
        async_client, credit_test_agent, monkeypatch):
    _ipfs(monkeypatch, "bafyroundtriptestcid")
    did, key = await credit_test_agent()
    answer = await _issue(async_client, key, did)
    signed = {k: v for k, v in answer.items() if k not in ISSUE_FIELDS}

    result = await _verify(async_client, signed)
    assert result["valid"] is True, result
    assert result["unsigned_fields"] == []


@pytest.mark.asyncio
async def test_a_changed_signed_field_still_fails(async_client, credit_test_agent, monkeypatch):
    _ipfs(monkeypatch, "bafyroundtriptestcid")
    did, key = await credit_test_agent()
    answer = await _issue(async_client, key, did)
    answer["credentialSubject"] = {**answer["credentialSubject"], "verified": False}

    result = await _verify(async_client, answer)
    assert result["valid"] is False


@pytest.mark.asyncio
async def test_any_other_added_field_still_fails(async_client, credit_test_agent, monkeypatch):
    """Only the three fields the issue answer adds are set aside, nothing else."""
    _ipfs(monkeypatch, "bafyroundtriptestcid")
    did, key = await credit_test_agent()
    answer = await _issue(async_client, key, did)
    answer["evidence"] = [{"type": "Anything"}]

    result = await _verify(async_client, answer)
    assert result["valid"] is False


def test_a_credential_that_signs_one_of_the_fields_keeps_it_signed(monkeypatch):
    """A credential whose proof covers `renew_at` verifies as signed; changing
    the field breaks it, and nothing is reported as set aside."""
    monkeypatch.setenv("DID_PRIVATE_KEY_HEX", os.urandom(32).hex())
    from app import credentials
    from app.crypto.hybrid import dual_sign
    vc = credentials.issue_credential("did:moltrust:aaaaaaaaaaaaaaaa", "AgentTrustCredential", {})
    body = {k: v for k, v in vc.items() if k != "proof"}
    body["renew_at"] = "2027-01-01T00:00:00Z"
    signed = dual_sign(body, credentials.get_signing_key())

    result = credentials.verify_credential(signed)
    assert result["valid"] is True, result
    assert result["unsigned_fields"] == []

    signed["renew_at"] = "2099-01-01T00:00:00Z"
    assert credentials.verify_credential(signed)["valid"] is False


def _stored_vc(monkeypatch):
    monkeypatch.setenv("DID_PRIVATE_KEY_HEX", os.urandom(32).hex())
    from app import credentials
    return credentials.issue_credential("did:moltrust:aaaaaaaaaaaaaaaa", "AgentTrustCredential",
                                        {"verified": True})


def test_the_probe_is_silent_on_the_fixed_verifier(monkeypatch):
    from app.credentials import verify_credential
    from scripts.issue_answer_verifies import check
    assert check(_stored_vc(monkeypatch), verify_credential) == []


def test_the_probe_reports_the_verifier_before_the_fix(monkeypatch):
    """The verifier before VC-1 checked the answer as it stood."""
    from app.credentials import _verify_signed
    from scripts.issue_answer_verifies import check
    assert check(_stored_vc(monkeypatch), _verify_signed) == ["ipfs", "no-ipfs"]


def test_the_probe_reports_a_verifier_that_ignores_the_signature(monkeypatch):
    from scripts.issue_answer_verifies import check
    assert check(_stored_vc(monkeypatch), lambda vc: {"valid": True}) == ["changed-field-passes"]
