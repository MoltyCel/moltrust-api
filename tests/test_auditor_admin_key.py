"""agents/auditor.py issues its SecurityAudit VC with the admin key.

Since PR #887 POST /credentials/issue answers 403 unless the API key owns the
subject DID or the request carries X-Admin-Key. The auditor attests MolTrust's
own DID with a key that does not own it. No network: every request goes to an
httpx.MockTransport.
"""
import json
import logging
import sys
from pathlib import Path

import httpx
import pytest

# auditor.py imports its neighbour `activity` by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents"))
import auditor  # noqa: E402

ADMIN = "adm-test-value-7f3c"
API = "mt-test-api-key-91b2"
DID = auditor.AGENT_DID


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("ADMIN_KEY", ADMIN)
    monkeypatch.setattr(auditor, "MOLTRUST_API_KEY", API)


_RealClient = httpx.Client


def _client(handler):
    return _RealClient(transport=httpx.MockTransport(handler))


def _vc_response(request):
    return httpx.Response(200, json={
        "type": ["VerifiableCredential", "SecurityAudit"],
        "issuer": "did:web:api.moltrust.ch",
        "credentialSubject": {"id": json.loads(request.content)["subject_did"]},
        "proof": {"type": "Ed25519Signature2020"},
    })


def test_request_carries_the_admin_header(keys):
    seen = []

    def handler(request):
        seen.append(request)
        return _vc_response(request)

    with _client(handler) as client:
        vc = auditor.issue_audit_vc(client, DID)

    assert vc is not None
    assert len(seen) == 1
    req = seen[0]
    assert req.method == "POST"
    assert req.url.host == "api.moltrust.ch" and req.url.path == "/credentials/issue"
    assert req.headers["x-admin-key"] == ADMIN
    assert req.headers["x-api-key"] == API
    assert json.loads(req.content) == {"subject_did": DID, "credential_type": "SecurityAudit"}


def test_unset_admin_key_skips_without_a_request(monkeypatch, caplog):
    monkeypatch.delenv("ADMIN_KEY", raising=False)
    monkeypatch.setattr(auditor, "MOLTRUST_API_KEY", API)

    def handler(request):
        raise AssertionError("no request may be sent without ADMIN_KEY")

    with caplog.at_level(logging.WARNING, logger="auditor"), _client(handler) as client:
        assert auditor.issue_audit_vc(client, DID) is None

    assert "ADMIN_KEY not set" in caplog.text
    assert API not in caplog.text


def test_a_refusal_never_logs_the_key_values(keys, caplog):
    # A server that echoes the headers back must not get them into our log.
    def handler(request):
        return httpx.Response(403, text=f"echo {ADMIN} {API}")

    with caplog.at_level(logging.INFO, logger="auditor"), _client(handler) as client:
        assert auditor.issue_audit_vc(client, DID) is None

    assert "HTTP 403" in caplog.text
    assert ADMIN not in caplog.text and API not in caplog.text


def test_an_exception_never_logs_the_key_values(keys, caplog):
    def handler(request):
        raise httpx.ConnectError(f"boom {ADMIN} {API}", request=request)

    with caplog.at_level(logging.INFO, logger="auditor"), _client(handler) as client:
        assert auditor.issue_audit_vc(client, DID) is None

    assert "ConnectError" in caplog.text
    assert ADMIN not in caplog.text and API not in caplog.text


def test_issue_vc_only_issues_once_and_touches_nothing_else(keys, monkeypatch, capsys):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "api.moltrust.ch", "only the MolTrust API may be called"
        return _vc_response(request)

    monkeypatch.setattr(auditor, "setup_logging", lambda: None)
    monkeypatch.setattr(auditor, "init_keys", lambda: None)
    monkeypatch.setattr(auditor.httpx, "Client", lambda *a, **k: _client(handler))
    monkeypatch.setattr(auditor, "run", lambda *a, **k: pytest.fail("full run started"))
    monkeypatch.setattr(auditor, "moltbook_post", lambda *a, **k: pytest.fail("posted"))
    monkeypatch.setattr(auditor, "generate_report", lambda *a, **k: pytest.fail("Anthropic"))

    assert auditor.main(["--issue-vc-only", DID]) == 0
    assert [r.url.path for r in seen] == ["/credentials/issue"]
    out = capsys.readouterr().out
    assert f"issue-vc-only: OK HTTP 200 subject={DID}" in out
    assert ADMIN not in out and API not in out


def test_issue_vc_only_fails_loudly_without_admin_key(monkeypatch, capsys):
    monkeypatch.delenv("ADMIN_KEY", raising=False)
    monkeypatch.setattr(auditor, "MOLTRUST_API_KEY", API)
    monkeypatch.setattr(auditor, "setup_logging", lambda: None)
    monkeypatch.setattr(auditor, "init_keys", lambda: None)
    monkeypatch.setattr(auditor.httpx, "Client",
                        lambda *a, **k: _client(lambda r: pytest.fail("request sent")))

    assert auditor.main(["--issue-vc-only", DID]) == 1
    assert "issue-vc-only: FAILED" in capsys.readouterr().out


def test_issue_vc_only_refuses_publish():
    with pytest.raises(SystemExit):
        auditor.main(["--issue-vc-only", DID, "--publish"])


def test_init_keys_takes_one_key_from_the_allow_list(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))  # no secrets file: env only
    monkeypatch.setenv("MOLTRUST_API_KEYS", " first-key , second-key")
    for name in ("MOLTRUST_API_KEY", "ANTHROPIC_KEY", "MOLTBOOK_KEY"):
        monkeypatch.setattr(auditor, name, "")
    auditor.init_keys()
    assert auditor.MOLTRUST_API_KEY == "first-key"
