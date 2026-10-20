"""The agent card guard (scripts/agent_card_guard.py): four equal copies and a valid
served signature, each failure named.

Equality alone passes a hand edit made in both repos. A valid signature alone
passes a stale served copy, which is what happened from 12 to 18 October 2026.
These tests hold the judgement with the committed card and a key made here.
"""
import base64
import json
import os
import sys

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO, "scripts"))
sys.path.insert(0, _REPO)

import agent_card_guard as G  # noqa: E402
from lib.agent_card_verify import canonicalize_rfc8785  # noqa: E402


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


KEY = Ed25519PrivateKey.generate()
JWK = {"kty": "OKP", "crv": "Ed25519", "kid": "test-kid", "alg": "EdDSA",
       "x": _b64(KEY.public_key().public_bytes(serialization.Encoding.Raw,
                                                serialization.PublicFormat.Raw))}


def _signed(card: dict) -> bytes:
    body = {k: v for k, v in card.items() if k != "signatures"}
    protected = _b64(canonicalize_rfc8785({"alg": "EdDSA", "kid": "test-kid", "typ": "a2a-card+jws"}))
    sig = KEY.sign(f"{protected}.{_b64(canonicalize_rfc8785(body))}".encode())
    return (json.dumps({**body, "signatures": [{"protected": protected, "signature": _b64(sig)}]},
                       indent=2, ensure_ascii=False) + "\n").encode()


with open(os.path.join(_REPO, ".well-known", "agent-card.json")) as f:
    BASE = json.load(f)
CURRENT = _signed(BASE)
STALE = _signed({**BASE, "securityRequirements": BASE["securityRequirements"][:2]})


def _copies(api=CURRENT, web=CURRENT, site=CURRENT, apihost=CURRENT):
    return {"moltrust-api@main": api, "moltrust-web@main": web,
            "moltrust.ch": site, "api.moltrust.ch": apihost}


def _run(**kw):
    copies = _copies(**kw)
    err = G.served_signature(copies["moltrust.ch"], JWK)
    return G.judge(copies, err)


def test_four_equal_copies_and_valid_signature_are_green():
    ok, lines = _run()
    assert ok, lines
    assert lines[-1].startswith("agent-card-guard=ok 4 gleich")


def test_stale_served_copy_with_valid_signature_is_red():
    # The 18 October case: the served card verifies, but it is not the source.
    assert G.served_signature(STALE, JWK) is None
    ok, lines = _run(web=STALE, site=STALE, apihost=STALE)
    assert not ok
    assert lines[-1] == ("agent-card-guard=FAIL ungleich "
                         "moltrust-web@main,moltrust.ch,api.moltrust.ch != moltrust-api@main")


def test_web_repo_ahead_of_served_is_red():
    ok, lines = _run(web=STALE)
    assert not ok and "ungleich moltrust-web@main != moltrust-api@main" in lines[-1]


def test_one_host_differs_is_red():
    ok, lines = _run(apihost=STALE)
    assert not ok and "ungleich api.moltrust.ch" in lines[-1]


def test_equal_copies_with_broken_signature_are_red():
    # A hand edit carried into both repos and deployed: all four equal, signature void.
    edited = CURRENT.replace(b"POST /vc/aae/evaluate\"\n", b"POST /vc/aae/evaluatf\"\n", 1)
    assert edited != CURRENT
    ok, lines = _run(api=edited, web=edited, site=edited, apihost=edited)
    assert not ok
    assert lines[-1].startswith("agent-card-guard=FAIL signatur ")
    assert "ungleich" not in lines[-1]


def test_served_bytes_that_are_not_json_are_red():
    assert G.served_signature(b"<html>", JWK).startswith("keine JSON-Karte")


def test_blob_hash_is_git_hash_object():
    # git hash-object /dev/null
    assert G.git_blob(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
