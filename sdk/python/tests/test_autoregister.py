"""DID registration on the first call of a client without credentials."""
import base64
import hashlib
import json
import os
import stat

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from moltrust_enforce import EnforceClient, autoregister

API = "https://enforce.test"
BITS = 6


def _zero_bits(digest):
    n = 0
    for b in digest:
        if b == 0:
            n += 8
            continue
        return n + 8 - b.bit_length()
    return n


class FakeServer:
    """register-challenge, register-pop (signature and proof-of-work checked), enforce/check."""

    def __init__(self, fail_register=None):
        self.fail_register = fail_register
        self.registrations = []
        self.check_dids = []

    def __call__(self, request):
        path = request.url.path
        if path == "/identity/register-challenge":
            return httpx.Response(200, json={"challenge": "seed99.9999999999.tag",
                                             "pow": {"difficulty_bits": BITS, "seed": "seed99"}})
        if path == "/identity/register-pop":
            body = json.loads(request.content)
            self.registrations.append(body)
            if self.fail_register:
                return httpx.Response(self.fail_register, json={"detail": "no"})
            sig = base64.urlsafe_b64decode(body["signature"] + "=" * (-len(body["signature"]) % 4))
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(body["public_key"])).verify(
                sig, body["challenge"].encode("ascii"))
            assert _zero_bits(hashlib.sha256(f"seed99{body['pow_nonce']}".encode()).digest()) >= BITS
            return httpx.Response(200, json={"did": "did:moltrust:" + body["public_key"][:16]})
        if path == "/enforce/check":
            self.check_dids.append(request.headers.get("x-moltrust-did"))
            return httpx.Response(401, json={"detail": "fake"})
        return httpx.Response(404)


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ("MOLTRUST_NO_AUTO_REGISTER", "CI", "MOLTRUST_REGISTER_PLATFORM"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "identity.json"
    monkeypatch.setenv("MOLTRUST_IDENTITY_FILE", str(path))
    return path


def _client(server, **kw):
    return EnforceClient(API, transport=httpx.MockTransport(server), **kw)


def test_a_fresh_client_registers_once_and_sends_the_did(env):
    server = FakeServer()
    c = _client(server)
    c.check({}, {})
    c.check({}, {})
    assert len(server.registrations) == 1
    did = "did:moltrust:" + server.registrations[0]["public_key"][:16]
    assert server.check_dids == [did, did] and c.did == did
    body = server.registrations[0]
    assert body["platform"] == "usage-enforce" and body["source"] == "moltrust-enforce"
    assert "private_key" not in json.dumps(body)


def test_a_second_client_on_the_same_machine_reuses_the_identity(env):
    server = FakeServer()
    _client(server).check({}, {})
    _client(server).check({}, {})
    assert len(server.registrations) == 1
    assert server.check_dids[0] == server.check_dids[1]


def test_the_identity_file_is_private(env):
    _client(FakeServer()).check({}, {})
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600
    assert len(json.loads(env.read_text())["identities"][API]["private_key"]) == 64


def test_a_client_with_a_credential_never_registers(env):
    server = FakeServer()
    _client(server, api_key="k").check({}, {})
    _client(server, did="did:moltrust:0123456789abcdef").check({}, {})
    assert server.registrations == []


@pytest.mark.parametrize("how", ["flag", "env", "ci"])
def test_opted_out_without_a_credential_is_the_old_error(env, monkeypatch, how):
    kw = {}
    if how == "flag":
        kw["auto_register"] = False
    elif how == "env":
        monkeypatch.setenv("MOLTRUST_NO_AUTO_REGISTER", "1")
    else:
        monkeypatch.setenv("CI", "true")
    with pytest.raises(ValueError):
        _client(FakeServer(), **kw)


def test_a_failed_registration_does_not_fail_the_call(env):
    server = FakeServer(fail_register=500)
    v = _client(server).check({}, {})
    assert v.denied and not v.from_server   # the endpoint's own 401, fail-closed as always
    assert server.check_dids == [None]
    assert not env.exists()


def test_ensure_never_raises_on_an_unreachable_api(env):
    def boom(request):
        raise httpx.ConnectError("down")
    assert autoregister.ensure(API, "0", transport=httpx.MockTransport(boom)) is None
