"""Which credential type the registry signs is not the caller's choice (ops CRED-2410).

On 24.10.2026 three agents registered minutes earlier had POST /credentials/issue
sign AdminTrustCredential, OperatorCredential and RootCredential for themselves.
The route checked only that the name was alphanumeric. A gate attestation lists
every type a DID holds, so a self-named type could meet a gate's requirement.

And a revoked credential still answered valid on POST /credentials/verify,
because the route checked the signature and never the registry row.
"""
import datetime

import pytest

import app.main as m
from app import credentials
from app.leak_canary import CanaryIssueRequest
from tests.test_sign_guard import ADMIN, FOREIGN_DID, OWN_DID, env  # noqa: F401


def _issue(client, did, typ, headers=None):
    return client.post("/credentials/issue", json={"subject_did": did, "credential_type": typ},
                       headers=headers or {})


@pytest.mark.parametrize("typ", ["RootCredential", "AdminTrustCredential", "OperatorCredential",
                                 "SecurityAudit", "TrackRecordCredential", "SkillAuditCredential"])
def test_an_agent_cannot_name_another_type_for_itself(env, typ):  # noqa: F811
    pool, calls, client = env
    r = _issue(client, OWN_DID, typ)
    assert r.status_code == 400, r.text
    assert "AgentTrustCredential" in r.json()["detail"]
    assert calls["sign"] == []
    assert not [w for w in pool.writes if w.startswith("INSERT INTO credentials")]


def test_an_agent_still_gets_its_agent_trust_credential(env):  # noqa: F811
    pool, calls, client = env
    r = _issue(client, OWN_DID, "AgentTrustCredential")
    assert r.status_code == 200, r.text
    assert calls["sign"] == [(OWN_DID, "AgentTrustCredential")]


def test_admin_may_issue_security_audit_and_nothing_else(env):  # noqa: F811
    pool, calls, client = env
    pool.caller = None
    ok = _issue(client, FOREIGN_DID, "SecurityAudit", headers={"X-Admin-Key": ADMIN})
    assert ok.status_code == 200, ok.text
    no = _issue(client, FOREIGN_DID, "RootCredential", headers={"X-Admin-Key": ADMIN})
    assert no.status_code == 400, no.text
    assert calls["sign"] == [(FOREIGN_DID, "SecurityAudit")]


def test_a_foreign_did_is_still_403_before_the_type_is_looked_at(env):  # noqa: F811
    pool, calls, client = env
    r = _issue(client, FOREIGN_DID, "RootCredential")
    assert r.status_code == 403, r.text


def test_a_canary_carries_only_the_self_issuable_type():
    CanaryIssueRequest(counterparty_did=FOREIGN_DID)
    with pytest.raises(ValueError):
        CanaryIssueRequest(counterparty_did=FOREIGN_DID, credential_type="SecurityAudit")


def test_refusal_names_the_allowed_types():
    assert credentials.credential_type_refusal("AgentTrustCredential", admin=False) is None
    assert "SecurityAudit" in credentials.credential_type_refusal("RootCredential", admin=True)


# --- POST /credentials/verify reads the registry row --------------------------

class _RevokedConn:
    def __init__(self, revoked):
        self.revoked = revoked
        self.seen = []

    async def fetchrow(self, sql, *args):
        if "FROM credentials WHERE proof_value" in sql:
            self.seen.append(args[0])
            return {"revoked_at": datetime.datetime(2026, 10, 25, 9, 30)} if self.revoked else None
        return None

    async def fetchval(self, sql, *args):
        return None

    async def fetch(self, sql, *args):
        return []

    async def execute(self, sql, *args):
        return "OK"


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return _Ctx()


@pytest.mark.parametrize("revoked", [True, False])
def test_verify_answers_invalid_for_a_revoked_credential(env, monkeypatch, revoked):  # noqa: F811
    _, _, client = env
    vc = m.issue_credential(OWN_DID, "AgentTrustCredential", {"verified": True})
    conn = _RevokedConn(revoked)
    monkeypatch.setattr(m, "db_pool", _Pool(conn))
    r = client.post("/credentials/verify", json={"credential": vc})
    assert r.status_code == 200, r.text
    body = r.json()
    assert conn.seen, "the registry row was not looked up"
    if revoked:
        assert body["valid"] is False and body["revoked"] is True
        assert body["revoked_at"] == "2026-10-25T09:30:00Z"
    else:
        assert body["valid"] is True and "revoked" not in body
