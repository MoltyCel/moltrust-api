"""The gate denies unless everything checks out, and it never calls home.

The tests build their own registry key and their own agent key, so the whole
flow is exercised without a server. That is also the property under test: if
any of this needed MolTrust to be reachable, these tests could not run.
"""
import base64
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from moltrust_enforce.gate import (
    AttestationError,
    Decision,
    binding_string,
    check_track_record,
    require_moltrust,
    verify_attestation,
)

KID = "test-registry-1"
METHOD = "POST"
PATH = "/guard/vc/skill/issue"


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.fixture(scope="module")
def registry():
    return Ed25519PrivateKey.generate()


@pytest.fixture(scope="module")
def jwks(registry):
    from cryptography.hazmat.primitives import serialization

    raw = registry.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": KID, "x": b64(raw),
                      "use": "sig", "alg": "EdDSA"}]}


@pytest.fixture(scope="module")
def agent():
    return Ed25519PrivateKey.generate()


@pytest.fixture(scope="module")
def agent_public_hex(agent):
    from cryptography.hazmat.primitives import serialization

    return agent.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()


def make_attestation(registry, *, did="did:moltrust:abc123", public_key,
                     trust_score=75.0, withheld=False, credential_types=("AgentTrustCredential",),
                     valid_for=3600, kid=KID, version=2):
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "v": version,
        "did": did,
        "public_key": public_key,
        "trust_score": trust_score,
        "withheld": withheld,
        "credential_types": sorted(credential_types),
        "computed_at": now.isoformat(),
        "valid_until": (now + dt.timedelta(seconds=valid_for)).isoformat(),
        "policy_version": "phase2",
    }
    header = {"alg": "EdDSA", "typ": "JWT", "kid": kid}
    h = b64(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    p = b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = registry.sign(f"{h}.{p}".encode("ascii"))
    return f"{h}.{p}.{b64(sig)}"


def make_headers(agent, token, did="did:moltrust:abc123", *, method=METHOD, path=PATH,
                 timestamp=None):
    ts = str(int(time.time())) if timestamp is None else str(timestamp)
    proof = agent.sign(binding_string(method, path, did, ts))
    return {
        "X-MolTrust-Attestation": token,
        "X-MolTrust-Timestamp": ts,
        "X-MolTrust-Proof": b64(proof),
    }


# ---------------------------------------------------------------------------
# The happy path, and that it is offline
# ---------------------------------------------------------------------------

def test_a_good_request_passes(registry, jwks, agent, agent_public_hex):
    token = make_attestation(registry, public_key=agent_public_hex)
    gate = require_moltrust(min_score=60, jwks=jwks)
    d = gate(METHOD, PATH, make_headers(agent, token))
    assert d.allowed, d.detail
    assert d.did == "did:moltrust:abc123"
    assert d.trust_score == 75.0
    assert bool(d) is True


def test_the_gate_makes_no_network_call(registry, jwks, agent, agent_public_hex, monkeypatch):
    """The whole design rests on this. If a socket is opened in the request
    path, an outage on our side becomes an outage on the caller's."""
    import socket

    def forbidden(*a, **k):
        raise AssertionError("the gate opened a socket")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    token = make_attestation(registry, public_key=agent_public_hex)
    gate = require_moltrust(min_score=60, jwks=jwks)
    assert gate(METHOD, PATH, make_headers(agent, token)).allowed


# ---------------------------------------------------------------------------
# Deny by default
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("drop", ["X-MolTrust-Attestation", "X-MolTrust-Proof",
                                  "X-MolTrust-Timestamp"])
def test_a_missing_header_denies(registry, jwks, agent, agent_public_hex, drop):
    token = make_attestation(registry, public_key=agent_public_hex)
    headers = make_headers(agent, token)
    del headers[drop]
    d = require_moltrust(min_score=60, jwks=jwks)(METHOD, PATH, headers)
    assert not d.allowed
    assert d.reason in ("attestation_missing", "proof_missing")


def test_no_headers_at_all_denies(jwks):
    d = require_moltrust(min_score=0, jwks=jwks)(METHOD, PATH, {})
    assert not d.allowed


def test_a_foreign_registry_key_denies(jwks, agent, agent_public_hex):
    other = Ed25519PrivateKey.generate()
    token = make_attestation(other, public_key=agent_public_hex)
    d = require_moltrust(min_score=60, jwks=jwks)(METHOD, PATH, make_headers(agent, token))
    assert not d.allowed
    assert d.reason == "attestation_invalid"


def test_an_unknown_kid_denies(registry, jwks, agent, agent_public_hex):
    token = make_attestation(registry, public_key=agent_public_hex, kid="rotated-away")
    d = require_moltrust(min_score=60, jwks=jwks)(METHOD, PATH, make_headers(agent, token))
    assert not d.allowed
    assert "no key for kid" in d.detail


def test_a_tampered_payload_denies(registry, jwks, agent, agent_public_hex):
    token = make_attestation(registry, public_key=agent_public_hex, trust_score=10.0)
    h, p, s = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    payload["trust_score"] = 99.0
    forged = b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    d = require_moltrust(min_score=60, jwks=jwks)(
        METHOD, PATH, make_headers(agent, f"{h}.{forged}.{s}"))
    assert not d.allowed
    assert d.reason == "attestation_invalid"


def test_an_expired_attestation_denies(registry, jwks, agent, agent_public_hex):
    token = make_attestation(registry, public_key=agent_public_hex, valid_for=-1)
    d = require_moltrust(min_score=60, jwks=jwks)(METHOD, PATH, make_headers(agent, token))
    assert not d.allowed
    assert "expired" in d.detail


def test_a_v1_payload_is_not_a_gate_attestation(registry, jwks, agent, agent_public_hex):
    """The v1 trust-score payload has no public key. Accepting it would mean
    gating on a score without ever checking who presented it."""
    token = make_attestation(registry, public_key=agent_public_hex, version=1)
    d = require_moltrust(min_score=60, jwks=jwks)(METHOD, PATH, make_headers(agent, token))
    assert not d.allowed
    assert "not a gate attestation" in d.detail


# ---------------------------------------------------------------------------
# Proof of control
# ---------------------------------------------------------------------------

def test_a_proof_from_another_key_denies(registry, jwks, agent_public_hex):
    """Someone else's proof with this agent's attestation."""
    token = make_attestation(registry, public_key=agent_public_hex)
    impostor = Ed25519PrivateKey.generate()
    d = require_moltrust(min_score=60, jwks=jwks)(
        METHOD, PATH, make_headers(impostor, token))
    assert not d.allowed
    assert d.reason == "proof_invalid"


def test_a_proof_for_another_route_denies(registry, jwks, agent, agent_public_hex):
    """A proof made for a free endpoint, replayed against a paid one."""
    token = make_attestation(registry, public_key=agent_public_hex)
    headers = make_headers(agent, token, path="/guard/api/market/feed")
    d = require_moltrust(min_score=60, jwks=jwks)(METHOD, PATH, headers)
    assert not d.allowed
    assert d.reason == "proof_invalid"


def test_a_proof_for_another_method_denies(registry, jwks, agent, agent_public_hex):
    token = make_attestation(registry, public_key=agent_public_hex)
    headers = make_headers(agent, token, method="GET")
    d = require_moltrust(min_score=60, jwks=jwks)("POST", PATH, headers)
    assert not d.allowed


def test_a_stale_proof_denies(registry, jwks, agent, agent_public_hex):
    token = make_attestation(registry, public_key=agent_public_hex)
    headers = make_headers(agent, token, timestamp=int(time.time()) - 3600)
    d = require_moltrust(min_score=60, jwks=jwks, max_age_seconds=300)(
        METHOD, PATH, headers)
    assert not d.allowed
    assert "old" in d.detail


def test_a_proof_from_the_future_denies(registry, jwks, agent, agent_public_hex):
    """Clock skew is bounded in both directions; an unbounded future timestamp
    is a proof that never goes stale."""
    token = make_attestation(registry, public_key=agent_public_hex)
    headers = make_headers(agent, token, timestamp=int(time.time()) + 4000)
    d = require_moltrust(min_score=60, jwks=jwks, max_age_seconds=300)(
        METHOD, PATH, headers)
    assert not d.allowed
    assert "future" in d.detail


def test_replay_is_caught_when_a_store_is_supplied(registry, jwks, agent, agent_public_hex):
    token = make_attestation(registry, public_key=agent_public_hex)
    headers = make_headers(agent, token)
    used = set()

    def seen(proof: str) -> bool:
        if proof in used:
            return False
        used.add(proof)
        return True

    gate = require_moltrust(min_score=60, jwks=jwks, seen=seen)
    assert gate(METHOD, PATH, headers).allowed
    second = gate(METHOD, PATH, headers)
    assert not second.allowed
    assert second.reason == "proof_replayed"


# ---------------------------------------------------------------------------
# Score and credentials
# ---------------------------------------------------------------------------

def test_a_score_below_the_minimum_denies(registry, jwks, agent, agent_public_hex):
    token = make_attestation(registry, public_key=agent_public_hex, trust_score=40.0)
    d = require_moltrust(min_score=60, jwks=jwks)(METHOD, PATH, make_headers(agent, token))
    assert not d.allowed
    assert d.reason == "score_below_minimum"
    assert d.trust_score == 40.0


def test_a_withheld_score_denies(registry, jwks, agent, agent_public_hex):
    """A freshly registered agent. Withheld is not a low score and it is not a
    pass — the gate says so in the denial rather than returning a bare 403."""
    token = make_attestation(registry, public_key=agent_public_hex,
                             trust_score=None, withheld=True)
    d = require_moltrust(min_score=60, jwks=jwks)(METHOD, PATH, make_headers(agent, token))
    assert not d.allowed
    assert d.reason == "score_withheld"
    assert "not a low score" in d.detail


def test_withheld_can_be_allowed_deliberately(registry, jwks, agent, agent_public_hex):
    """A discount tier may want new agents in. It has to be asked for."""
    token = make_attestation(registry, public_key=agent_public_hex,
                             trust_score=None, withheld=True)
    d = require_moltrust(jwks=jwks, allow_withheld=True)(
        METHOD, PATH, make_headers(agent, token))
    assert d.allowed


def test_allow_withheld_does_not_bypass_a_score_threshold(registry, jwks, agent,
                                                          agent_public_hex):
    """Opening the withheld door must not also open the score door."""
    token = make_attestation(registry, public_key=agent_public_hex,
                             trust_score=None, withheld=True)
    d = require_moltrust(min_score=60, jwks=jwks, allow_withheld=True)(
        METHOD, PATH, make_headers(agent, token))
    assert not d.allowed
    assert d.reason == "score_missing"


def test_a_required_credential_must_be_held(registry, jwks, agent, agent_public_hex):
    token = make_attestation(registry, public_key=agent_public_hex,
                             credential_types=("AgentTrustCredential",))
    gate = require_moltrust(credential_type="SkillAuditCredential", jwks=jwks)
    d = gate(METHOD, PATH, make_headers(agent, token))
    assert not d.allowed
    assert d.reason == "credential_missing"


def test_a_held_credential_passes(registry, jwks, agent, agent_public_hex):
    token = make_attestation(registry, public_key=agent_public_hex,
                             credential_types=("AgentTrustCredential", "SkillAuditCredential"))
    gate = require_moltrust(credential_type="SkillAuditCredential", jwks=jwks)
    assert gate(METHOD, PATH, make_headers(agent, token)).allowed


def test_credential_only_gating_ignores_the_score(registry, jwks, agent, agent_public_hex):
    """min_score=None means the score is not consulted, not that it passes."""
    token = make_attestation(registry, public_key=agent_public_hex, trust_score=1.0,
                             credential_types=("SkillAuditCredential",))
    gate = require_moltrust(credential_type="SkillAuditCredential", jwks=jwks)
    assert gate(METHOD, PATH, make_headers(agent, token)).allowed


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------

def test_every_denial_names_a_reason(registry, jwks, agent, agent_public_hex):
    """A 403 with no reason costs the caller a support round-trip."""
    cases = [
        ({}, "attestation_missing"),
    ]
    for headers, expected in cases:
        d = require_moltrust(min_score=60, jwks=jwks)(METHOD, PATH, headers)
        assert d.reason == expected
        assert isinstance(d, Decision)
        assert d.allowed is False


def test_jwks_without_keys_is_refused_at_build_time(agent_public_hex):
    """Better a crash at startup than a gate that denies everything at 3am."""
    with pytest.raises(ValueError, match="no keys"):
        require_moltrust(min_score=60, jwks={"keys": []})


def test_verify_attestation_is_usable_on_its_own(registry, jwks, agent_public_hex):
    token = make_attestation(registry, public_key=agent_public_hex)
    att = verify_attestation(token, jwks)
    assert att.did == "did:moltrust:abc123"
    assert att.public_key == agent_public_hex
    assert att.version == 2
    with pytest.raises(AttestationError):
        verify_attestation("not.a.jws", jwks)


# ---------------------------------------------------------------------------
# Parity with the reference implementation
# ---------------------------------------------------------------------------

def test_track_record_shape_is_checked():
    """The shape check is the whole offline test; the chain is not consulted."""
    good = {"issued_at": "2026-09-23T00:00:00Z", "anchor_tx": "0x" + "ab" * 32}
    assert check_track_record(good) is None
    assert check_track_record([good]) == "track_record is not an object"
    assert check_track_record({"issued_at": good["issued_at"]}) \
        == "track_record has no anchor_tx"
    assert "32-byte hex" in check_track_record(
        {"issued_at": good["issued_at"], "anchor_tx": "0xdeadbeef"})
    assert "RFC 3339" in check_track_record(
        {"issued_at": "last tuesday", "anchor_tx": good["anchor_tx"]})


def test_allow_says_which_door_it_came_through():
    """A gate that cannot separate the two paths cannot price either of them."""
    import json
    import os

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "parity-vectors.json")
    fixture = json.load(open(path, encoding="utf-8"))
    now = fixture["now_ms"] / 1000.0
    by_name = {v["name"]: v for v in fixture["vectors"]}

    gate = require_moltrust(min_score=50, jwks=fixture["jwks"],
                            allow_track_record=True)

    scored = by_name["good request, score above the threshold"]
    d = gate(scored["method"], scored["path"], scored["headers"], now=now)
    assert d.allowed and d.via == "score" and d.track_record is None

    carried = by_name["withheld score carried by a track record"]
    d = gate(carried["method"], carried["path"], carried["headers"], now=now)
    assert d.allowed, d.detail
    assert d.via == "track_record"
    assert d.track_record["anchor_tx"].startswith("0x")


def test_the_python_gate_replays_the_reference_vectors():
    """The gate exists three times: here, in @moltrust/x402, and vendored into
    moltguard, which is a separate repository and cannot import the package
    until it is on npm. Three implementations of a security check drift, and
    the drift is invisible — each one passes its own tests.

    So the reference emits fixed vectors, from constant key seeds and a pinned
    clock, and every port replays them. A failure here means the two have
    diverged, whichever one is wrong.
    """
    import json
    import os

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "parity-vectors.json")
    fixture = json.load(open(path, encoding="utf-8"))
    assert len(fixture["vectors"]) >= 22

    now = fixture["now_ms"] / 1000.0
    for v in fixture["vectors"]:
        opts = dict(v["options"])
        gate = require_moltrust(
            min_score=opts.get("minScore"),
            credential_type=opts.get("credentialType"),
            jwks=fixture["jwks"],
            allow_withheld=bool(opts.get("allowWithheld", False)),
            allow_track_record=bool(opts.get("allowTrackRecord", False)),
        )
        d = gate(v["method"], v["path"], v["headers"], now=now)
        assert d.allowed == v["expected"]["allowed"], f"{v['name']}: {d.detail}"
        assert d.reason == v["expected"]["reason"], f"{v['name']}: {d.detail}"
