"""Section 4.3 key rotation and section 4.4 deactivation, without a database.

The helpers in app/key_rotation.py and the DID document builder are pure; the
routes are checked for existence and shape from the source, so this file runs
in the no-database unit job.
"""
import ast
import base64
import datetime
import pathlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.key_rotation import (
    PAYLOAD_TAG,
    decode_public_key,
    rotation_payload,
    verification_methods,
    verify_rotation,
)

DID = "did:moltrust:21fe31dfa154a261"


def _key():
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return sk, pk


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_the_payload_is_the_documented_four_lines():
    assert rotation_payload(DID, "AA" * 32, "bb" * 32) == (
        f"{PAYLOAD_TAG}\n{DID}\n{'aa' * 32}\n{'bb' * 32}".encode()
    )


def test_a_signature_by_the_current_key_verifies():
    sk, pk = _key()
    _, new = _key()
    payload = rotation_payload(DID, pk.hex(), new.hex())
    assert verify_rotation(pk.hex(), payload, _b64url(sk.sign(payload)))


def test_a_signature_by_the_new_key_does_not():
    sk, pk = _key()
    new_sk, new = _key()
    payload = rotation_payload(DID, pk.hex(), new.hex())
    assert not verify_rotation(pk.hex(), payload, _b64url(new_sk.sign(payload)))


def test_a_signature_from_an_earlier_step_does_not_replay():
    """Key A rotated to B; the request A signed must not verify once B is in force."""
    sk_a, pk_a = _key()
    _, pk_b = _key()
    _, pk_c = _key()
    old = _b64url(sk_a.sign(rotation_payload(DID, pk_a.hex(), pk_b.hex())))
    assert not verify_rotation(pk_b.hex(), rotation_payload(DID, pk_b.hex(), pk_c.hex()), old)


@pytest.mark.parametrize("garbage", ["", "not base64 at all", "AA", _b64url(b"x" * 64)])
def test_a_malformed_signature_is_a_no_not_an_exception(garbage):
    _, pk = _key()
    assert not verify_rotation(pk.hex(), rotation_payload(DID, pk.hex(), pk.hex()), garbage)


def test_the_new_key_is_accepted_as_base64url_and_as_hex():
    _, pk = _key()
    assert decode_public_key(_b64url(pk)) == pk.hex()
    assert decode_public_key(pk.hex()) == pk.hex()
    assert decode_public_key(pk.hex().upper()) == pk.hex()


@pytest.mark.parametrize("bad", ["", "00" * 31, _b64url(b"x" * 31), "z" * 64])
def test_a_key_that_is_not_32_bytes_is_refused(bad):
    with pytest.raises(ValueError, match="32 bytes"):
        decode_public_key(bad)


def test_without_history_the_registration_key_is_key_1():
    methods, current = verification_methods(DID, "aa" * 32, [])
    assert current == f"{DID}#key-1"
    assert [m["id"] for m in methods] == [f"{DID}#key-1"]
    assert "revoked" not in methods[0]


def test_after_two_rotations_the_key_in_force_is_key_3_and_the_old_ones_stay_revoked():
    t1 = datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc)
    t2 = datetime.datetime(2026, 10, 2, tzinfo=datetime.timezone.utc)
    history = [
        {"key_index": 2, "public_key_hex": "bb" * 32, "revoked_at": t2},
        {"key_index": 1, "public_key_hex": "aa" * 32, "revoked_at": t1},
    ]
    methods, current = verification_methods(DID, "cc" * 32, history)
    assert current == f"{DID}#key-3"
    assert [m["id"] for m in methods] == [f"{DID}#key-3", f"{DID}#key-1", f"{DID}#key-2"]
    assert methods[1]["revoked"] is True and methods[1]["revokedDate"] == t1.isoformat()
    assert methods[2]["publicKeyHex"] == "bb" * 32


def test_no_key_means_no_method():
    assert verification_methods(DID, None, []) == ([], None)


def _routes():
    source = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and getattr(dec.func, "attr", None) == "post"
                        and dec.args and isinstance(dec.args[0], ast.Constant)):
                    found.add(dec.args[0].value)
    return found


@pytest.mark.parametrize("path", ["/identity/revoke", "/identity/rotate-key", "/identity/revoke/{did}"])
def test_the_paths_the_specification_documents_exist(path):
    assert path in _routes()
