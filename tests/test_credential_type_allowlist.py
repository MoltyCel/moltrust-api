"""Which credential type the registry signs is not the caller's choice (ops CRED-2410).

The rule itself, without a server. The routes are exercised at the end of
tests/test_sign_guard.py, which already runs the app against a stand-in pool;
a second TestClient module earlier in the run closed the event loop that later
async tests share.
"""
import pytest

from app import credentials
from app.leak_canary import CanaryIssueRequest

FOREIGN_DID = "did:moltrust:" + "0b0b" * 4


def test_a_canary_carries_only_the_self_issuable_type():
    CanaryIssueRequest(counterparty_did=FOREIGN_DID)
    with pytest.raises(ValueError):
        CanaryIssueRequest(counterparty_did=FOREIGN_DID, credential_type="SecurityAudit")


def test_refusal_names_the_allowed_types():
    assert credentials.credential_type_refusal("AgentTrustCredential", admin=False) is None
    assert credentials.credential_type_refusal("SecurityAudit", admin=True) is None
    assert "SecurityAudit" in credentials.credential_type_refusal("RootCredential", admin=True)
    assert "AgentTrustCredential" in credentials.credential_type_refusal("SecurityAudit", admin=False)
