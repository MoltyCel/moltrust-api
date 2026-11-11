"""Method specification section 2.2 (v0.2): the identifier follows from the key.

The expected value is computed outside this repository, from the public key of
RFC 8032 section 7.1 test 1, with `openssl dgst -sha256` over the raw 32 bytes:
21fe31dfa154a261626bf854046fd2271b7bed4b6abe45aa58877ef47f9721b9. A test that
recomputed it with hashlib would check the code against itself.
"""
import ast
import pathlib

import pytest

from app.did_derivation import (
    RULE_DERIVED,
    RULE_ASSIGNED,
    derive_did,
    derive_method_specific_id,
    identifier_rule,
)

RFC8032_TEST1_PUBLIC_KEY = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
EXPECTED_MSI = "21fe31dfa154a261"


def test_the_identifier_is_the_first_eight_bytes_of_sha256_over_the_key():
    assert derive_method_specific_id(RFC8032_TEST1_PUBLIC_KEY) == EXPECTED_MSI
    assert derive_did(RFC8032_TEST1_PUBLIC_KEY) == "did:moltrust:" + EXPECTED_MSI


def test_the_derivation_hashes_the_raw_bytes_not_the_hex_text():
    import hashlib

    over_text = hashlib.sha256(RFC8032_TEST1_PUBLIC_KEY.encode()).hexdigest()[:16]
    assert derive_method_specific_id(RFC8032_TEST1_PUBLIC_KEY) != over_text


@pytest.mark.parametrize("key", ["", "00" * 31, "00" * 33])
def test_a_key_that_is_not_32_bytes_is_refused(key):
    with pytest.raises(ValueError):
        derive_method_specific_id(key)


def test_a_derived_identifier_reads_as_derived():
    did = derive_did(RFC8032_TEST1_PUBLIC_KEY)
    assert identifier_rule(did, RFC8032_TEST1_PUBLIC_KEY) == RULE_DERIVED
    assert identifier_rule(did, RFC8032_TEST1_PUBLIC_KEY.upper()) == RULE_DERIVED


def test_an_assigned_identifier_reads_as_assigned_with_or_without_a_key():
    did = "did:moltrust:d34ed796a4dc4698"
    assert identifier_rule(did, None) == RULE_ASSIGNED
    assert identifier_rule(did, RFC8032_TEST1_PUBLIC_KEY) == RULE_ASSIGNED


@pytest.mark.parametrize("did", [
    "did:moltrust:ambassador0001",
    "did:moltrust:vcone",
    "did:moltrust:ext_516a656bafa39e5c",
    "did:moltrust:D34ED796A4DC4698",
    "did:web:example.com",
])
def test_identifiers_outside_the_section_2_2_syntax_have_no_rule(did):
    assert identifier_rule(did, RFC8032_TEST1_PUBLIC_KEY) is None


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in app/main.py")


def test_register_pop_derives_and_mints_no_uuid():
    """Read from the source so the test needs neither the API nor a database."""
    source = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    route = _function(ast.parse(source), "register_agent_pop")
    calls = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        for node in ast.walk(route)
        if isinstance(node, ast.Call)
    }
    assert "derive_did" in calls
    assert "uuid4" not in calls
