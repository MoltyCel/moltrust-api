"""The signing paths refuse integers outside +-(2**53-1), as the verify path does.

``app.signature.canonicalize`` forwards to jcs 0.2.1, which writes every number
through a double. Above 2**53-1 distinct integer literals share one double, so
the library signed a value other than the one it was given:
``{"n": 9007199254740993}`` came out as ``9007199254740992`` and ``10**21`` as
``1e+21``. ``lib/agent_card_verify.py`` refuses the same input (RFC 7493
section 2.2). These tests hold the two signing paths, ``canonicalize`` and
``sign_agent_card``, to the same bound, and check that integers inside it still
canonicalize byte for byte as before.
"""
import json

import jcs
import pytest

from app import signature

LIMIT = 2**53 - 1

# Each value as it arrives from JSON text, digits only, no exponent.
OUTSIDE = [
    ("2**53+1", json.loads('{"n":9007199254740993}')),
    ("-(2**53+1)", json.loads('{"n":-9007199254740993}')),
    ("10**21 in digits", json.loads('{"n":1000000000000000000000}')),
]


class _RecordingKey:
    def __init__(self) -> None:
        self.seen: list[bytes] = []

    def sign(self, data: bytes) -> bytes:
        self.seen.append(data)
        return bytes(64)


@pytest.fixture
def key(monkeypatch):
    k = _RecordingKey()
    monkeypatch.setattr(signature, "get_private_key", lambda: k)
    return k


# --- trigger: outside the range is refused, with value and bound named ------


@pytest.mark.parametrize("label,payload", OUTSIDE, ids=[o[0] for o in OUTSIDE])
def test_canonicalize_refuses_integer_outside_safe_range(label, payload):
    with pytest.raises(ValueError) as exc:
        signature.canonicalize(payload)
    # Exactly ValueError: the a2a-tck runner classifies refusals by type
    # identity (tests/conformance/score_a2a_jcs.py, LIBRARY_REFUSALS).
    assert type(exc.value) is ValueError
    message = str(exc.value)
    assert str(payload["n"]) in message
    assert str(LIMIT) in message


@pytest.mark.parametrize("label,payload", OUTSIDE, ids=[o[0] for o in OUTSIDE])
def test_sign_agent_card_refuses_and_signs_nothing(label, payload, key):
    card = {"name": "probe", "version": "1.0.1", **payload}
    with pytest.raises(ValueError) as exc:
        signature.sign_agent_card(card)
    assert str(payload["n"]) in str(exc.value)
    assert str(LIMIT) in str(exc.value)
    assert key.seen == []


@pytest.mark.parametrize(
    "payload",
    [
        {"a": [1, {"b": 2**53 + 1}]},
        [{"deep": [[-(2**53 + 1)]]}],
        {"protected": {"x": 10**21}},
    ],
)
def test_nested_integer_outside_range_is_found(payload):
    with pytest.raises(ValueError):
        signature.canonicalize(payload)


def test_registry_jws_refuses_too(key):
    with pytest.raises(ValueError):
        signature.build_registry_jws({"n": 2**53 + 1})
    assert key.seen == []


# --- silence: inside the range nothing changes ------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"n": LIMIT},
        {"n": -LIMIT},
        {"n": 0},
        {"n": 52573329, "ok": True, "no": False, "none": None},
        {"list": [1, -1, LIMIT, {"x": -LIMIT}]},
        {"f": 1.5, "g": 1e21},  # floats are out of scope and pass unchanged
    ],
)
def test_inside_range_is_byte_identical_to_the_library(payload):
    assert signature.canonicalize(payload) == jcs.canonicalize(payload)


def test_limit_keeps_its_digits():
    assert signature.canonicalize({"n": LIMIT}) == b'{"n":9007199254740991}'
    assert signature.canonicalize({"n": -LIMIT}) == b'{"n":-9007199254740991}'


def test_sign_agent_card_inside_range_signs_the_digits(key):
    signature.sign_agent_card({"name": "probe", "n": LIMIT})
    (signing_input,) = key.seen
    payload_b64 = signing_input.split(b".")[1]
    import base64

    payload = base64.urlsafe_b64decode(payload_b64 + b"=" * (-len(payload_b64) % 4))
    assert b'"n":9007199254740991' in payload


# --- every JCS signer, from the shared vectors (ops V2) ---------------------
#
# app/signature.py was guarded first (ops N1). Two more signers called
# jcs.canonicalize directly and still signed the rounded value on d2b10d5:
# app/crypto/hybrid.py (dual_sign, every W3C credential and endorsement) and
# app/provenance/ipr.py (interaction proof records). The vectors are one file so
# each signer is held to the same inputs.

from pathlib import Path

from app.crypto import dilithium, hybrid
from app.provenance import ipr

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "unsafe_integers.json").read_text(encoding="utf-8")
)
REFUSE = [(v["label"], v["json"]) for v in VECTORS["refuse"]]
KEEP = [(v["label"], v["json"], v["canonical"]) for v in VECTORS["keep"]]


def _ipr_payload(n):
    return {
        "agent_did": "did:moltrust:probe",
        "confidence": n,
        "confidence_basis": "declared",
        "output_hash": "sha256:" + "0" * 64,
        "output_type": "text",
        "produced_at": "2026-10-17T00:00:00Z",
    }


def _credential(n):
    return {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "issuer": "did:web:api.moltrust.ch",
        "validFrom": "2026-10-17T00:00:00Z",
        "credentialSubject": {"id": "did:web:probe", "n": n},
    }


@pytest.fixture
def ed25519_key(monkeypatch):
    from nacl.signing import SigningKey

    monkeypatch.setattr(dilithium, "is_available", lambda: False)
    return SigningKey.generate()


def test_vectors_bound_is_the_code_bound():
    assert VECTORS["bound"] == LIMIT == signature.MAX_SAFE_INTEGER


@pytest.mark.parametrize("label,text", REFUSE, ids=[r[0] for r in REFUSE])
def test_vectors_refused_by_every_signer(label, text, key, ed25519_key):
    value = json.loads(text)
    n = value["n"]
    signers = {
        "canonicalize": lambda: signature.canonicalize(value),
        "sign_agent_card": lambda: signature.sign_agent_card({"name": "probe", **value}),
        "build_registry_jws": lambda: signature.build_registry_jws(value),
        "hybrid JCS": lambda: hybrid._canonicalize(value, "JCS"),
        "dual_sign": lambda: hybrid.dual_sign(_credential(n), ed25519_key),
        "ipr": lambda: ipr.build_canonical_payload(_ipr_payload(n)),
    }
    for name, call in signers.items():
        with pytest.raises(ValueError) as exc:
            call()
        assert type(exc.value) is ValueError, name
        assert str(n) in str(exc.value), name
        assert str(LIMIT) in str(exc.value), name
    assert key.seen == []


@pytest.mark.parametrize("label,text,canonical", KEEP, ids=[k[0] for k in KEEP])
def test_vectors_kept_by_every_signer(label, text, canonical, ed25519_key):
    value = json.loads(text)
    assert signature.canonicalize(value) == canonical.encode()
    assert hybrid._canonicalize(value, "JCS") == canonical.encode()
    n = value["n"]
    assert f'"confidence":{n},'.encode() in ipr.build_canonical_payload(_ipr_payload(n))
    vc = hybrid.dual_sign(_credential(n), ed25519_key)
    assert vc["credentialSubject"]["n"] == n
    assert hybrid.verify_proof(vc, ed25519_key.verify_key)["valid"] is True


def test_credential_signed_before_the_guard_no_longer_verifies(ed25519_key, monkeypatch):
    """A credential carrying 2**53+1, issued before the guard, does not verify.

    Before the guard jcs wrote 2**53+1 as 9007199254740992, so the issuer's
    signature over that credential equally vouched for 2**53 and for 2**53+1.
    Verification now refuses the payload and reports the proof invalid.
    """
    monkeypatch.setattr(hybrid, "require_safe_integers", lambda _v: None)
    vc = hybrid.dual_sign(_credential(2**53 + 1), ed25519_key)
    assert hybrid.verify_proof(vc, ed25519_key.verify_key)["valid"] is True
    monkeypatch.undo()
    monkeypatch.setattr(dilithium, "is_available", lambda: False)
    assert hybrid.verify_proof(vc, ed25519_key.verify_key)["valid"] is False
