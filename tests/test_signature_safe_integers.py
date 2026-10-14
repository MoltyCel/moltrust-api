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
