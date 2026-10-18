"""The V6 live guard (scripts/v6_guard_probe.py): both directions, each named on red.

A guard that only checked "header alone is 401" would call a deploy healthy that
refuses everything; one that only checked the signed path would miss the bare
header coming back. A signed request that reaches a 200 with a DENY is red too,
because every such run logs a violation against the probe DID. These tests hold
the judgement and show that what the guard signs is what the server's verifier
accepts.
"""
import asyncio
import json
import os
import sys

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO, "scripts"))
sys.path.insert(0, _REPO)

import v6_guard_probe as G  # noqa: E402


def test_both_directions_hold():
    ok, lines = G.judge(401, 200, "ALLOW")
    assert ok and lines[-1] == "v6-guard=ok"


def test_bare_header_accepted_names_direction_1():
    # The state of production before V6 is deployed.
    ok, lines = G.judge(200, 200, "ALLOW")
    assert not ok
    assert lines[-1].startswith("v6-guard=FAIL richtung1")
    assert "richtung2" not in lines[-1]


def test_everything_refused_names_direction_2():
    # A deploy that refuses everything: direction 1 alone would look healthy.
    ok, lines = G.judge(401, 401, "")
    assert not ok
    assert "richtung2" in lines[-1] and "richtung1" not in lines[-1]
    assert "der Signaturweg ist zu" in lines[-1]


def test_both_broken_names_both():
    ok, lines = G.judge(200, 500, "")
    assert not ok and "richtung1" in lines[-1] and "richtung2" in lines[-1]


def test_no_answer_is_a_failure_not_a_pass():
    ok, lines = G.judge(0, 0, "")
    assert not ok and "keine Antwort" in lines[-1]


def test_deny_on_a_200_is_red_and_names_the_envelope():
    """The missing envelope before W-Env: 200, DENY, one violation row per run."""
    ok, lines = G.judge(401, 200, "DENY")
    assert not ok
    assert "verdikt=DENY" in lines[1] and "FAIL" in lines[1]
    assert G.AAE_ID in lines[-1] and "Verstoss" in lines[-1]
    assert "der Signaturweg ist zu" not in lines[-1]


def test_a_200_without_a_verdict_is_red():
    ok, lines = G.judge(401, 200, "")
    assert not ok and "verdikt=keins" in lines[1]


def test_verdict_of_reads_the_verdict_and_survives_garbage():
    assert G.verdict_of(b'{"verdict": "ALLOW"}') == "ALLOW"
    assert G.verdict_of(b"not json") == ""
    assert G.verdict_of(b'{"verdict": 7}') == ""
    assert G.verdict_of(b"") == ""


def test_body_binds_envelope_and_sends_the_allowlisted_host():
    ctx = json.loads(G.body_for(G.PROBE_DID, "n"))
    assert ctx["aae_ref"] == G.AAE_REF
    # vc_id has to bind to the envelope's aae_id, or /vc/aae/evaluate answers 422.
    assert ctx["action_context"]["vc_id"] == G.AAE_ID
    assert ctx["action_context"]["domain"] == G.PROBE_DOMAIN


def test_fault_domain_is_what_the_run_sends():
    ctx = json.loads(G.body_for(G.PROBE_DID, "n", domain="example.invalid"))
    assert ctx["action_context"]["domain"] == "example.invalid"


def test_run_sends_header_only_first_then_signed(monkeypatch):
    priv = Ed25519PrivateKey.generate()
    seen = []

    def send(url, body, headers):
        seen.append(headers)
        return (401, "") if "Signature" not in headers else (200, "ALLOW")

    ok, _ = G.run("https://example.invalid", priv, send=send)
    assert ok
    assert set(seen[0]) == {"X-MolTrust-DID"}
    assert {"Signature", "Signature-Input", "Content-Digest"} <= set(seen[1])
    assert seen[0]["X-MolTrust-DID"] == seen[1]["X-MolTrust-DID"] == G.PROBE_DID


def test_run_carries_the_fault_domain_into_both_directions():
    priv = Ed25519PrivateKey.generate()
    sent = []

    def send(url, body, headers):
        sent.append(json.loads(body)["action_context"]["domain"])
        return (401, "") if "Signature" not in headers else (200, "DENY")

    ok, lines = G.run("https://example.invalid", priv, send=send, domain="example.invalid")
    assert sent == ["example.invalid", "example.invalid"]
    assert not ok and "verdikt=DENY" in lines[1]


def test_guard_signature_verifies_with_the_server_verifier(monkeypatch):
    """What the guard signs, app/enforcement/request_signature.py accepts."""
    pytest.importorskip("asyncpg")
    from app.enforcement import request_signature as RS

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes_raw()

    async def current_key(conn, did, keyid):
        assert did == G.PROBE_DID and keyid == G.PROBE_DID + "#key-1"
        return pub

    class Conn:
        async def execute(self, *a):
            return None

        async def fetchval(self, *a):
            return 1          # nonce not seen before

    monkeypatch.setattr(RS, "require_current_key", current_key)
    body = G.body_for(G.PROBE_DID, "n")
    headers = {k.lower(): v for k, v in G.signed_headers(priv, G.PROBE_DID, G.PATH, body).items()}
    proof = asyncio.run(RS.verify_request(Conn(), method="POST", path=G.PATH,
                                          headers=headers, body=body))
    assert proof["did"] == G.PROBE_DID


def test_missing_key_file_exits_2(tmp_path, capsys):
    assert G.main(["--key-file", str(tmp_path / "absent")]) == 2
    assert "v6-guard=ERROR" in capsys.readouterr().out
