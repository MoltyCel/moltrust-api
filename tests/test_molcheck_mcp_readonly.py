"""Read-only hard gate for the MolCheck MCP gateway tools (formerly MoltProof).

Mirrors, on the Python gateway, what test/no-secret-route.test.ts enforces in the
TypeScript service: no tool accepts a secret-shaped field, tools are a pure
pass-through to 127.0.0.1:3006/proof/*, and the GET tools never write. If any of
these regress, this test fails the build.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import molcheck_mcp_tools as mp  # noqa: E402


class FakeMCP:
    """Captures @mcp.tool()-decorated functions so we can call them directly."""

    def __init__(self):
        self.tools = {}
        self.descriptions = {}

    def tool(self, name=None, description=None):
        def deco(fn):
            self.tools[name or fn.__name__] = fn
            self.descriptions[name or fn.__name__] = description or fn.__doc__
            return fn
        return deco


@pytest.fixture()
def mcp():
    fake = FakeMCP()
    mp.register_molcheck_tools(fake)
    return fake


@pytest.fixture()
def tools(mcp):
    return mcp.tools


@pytest.fixture()
def calls(monkeypatch):
    """Record every REST call the tools make; never touch the network."""
    rec = {"get": [], "post": []}

    async def fake_get(path):
        rec["get"].append(path)
        return {"ok": True, "path": path}

    async def fake_post(path, body):
        rec["post"].append((path, body))
        return {"ok": True, "path": path}

    monkeypatch.setattr(mp, "_get", fake_get)
    monkeypatch.setattr(mp, "_post", fake_post)
    return rec


AGENT = "0x1111111111111111111111111111111111111111"

SECRETS = [
    {"private_key": "0x" + "a" * 64},
    {"seed": "b" * 40, "_named": True},
    {"value": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----"},
    {"value": "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"},
    {"value": "sk_live_deadbeefcafe1234"},
]


# --- the scanner itself ------------------------------------------------------
def test_scanner_rejects_secret_named_field():
    assert mp.reject_if_secret(private_key="0x" + "a" * 64) is not None
    assert mp.reject_if_secret(seed="x" * 32) is not None


def test_scanner_rejects_secret_shaped_values():
    assert mp.reject_if_secret(note="-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----") is not None
    assert mp.reject_if_secret(note="sk_live_deadbeefcafe1234") is not None
    assert mp.reject_if_secret(
        note="abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    ) is not None


def test_scanner_allows_public_inputs():
    # plain address, DID, and a tx-hash-shaped value are all public
    assert mp.reject_if_secret(agent=AGENT) is None
    assert mp.reject_if_secret(agent="did:moltrust:agent1") is None
    assert mp.reject_if_secret(txHash="0x" + "c" * 64) is None  # NOT a secret by value


def test_scanner_never_echoes_the_value():
    err = mp.reject_if_secret(private_key="0x" + "a" * 64)
    assert "a" * 64 not in err
    payload = json.loads(err)
    assert payload["error"] == "secret_shaped_input_rejected"
    assert payload["field"] == "private_key"  # field name only


# --- the tools: pass-through + read-only + gated -----------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["molcheck", "moltproof"])
async def test_get_tools_passthrough_to_proof(tools, calls, prefix):
    await tools[f"{prefix}_verdict"](agent=AGENT)
    await tools[f"{prefix}_mandate"](agent=AGENT)
    await tools[f"{prefix}_evidence"](agent=AGENT)
    await tools[f"{prefix}_registry"]()
    assert calls["get"] == [
        f"/proof/verdict/{AGENT}",
        f"/proof/mandate/{AGENT}",
        f"/proof/evidence/{AGENT}",
        "/proof/registry",
    ]
    # GET tools must never write
    assert calls["post"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["molcheck", "moltproof"])
async def test_verify_is_the_only_post_and_hits_verify(tools, calls, prefix):
    await tools[f"{prefix}_verify"](
        agent=AGENT,
        mandate={"credentialSubject": {"id": "did:moltrust:a"}},
        actions=[],
    )
    assert [p for p, _ in calls["post"]] == ["/proof/verify"]


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["molcheck", "moltproof"])
async def test_tools_reject_secret_before_forwarding(tools, calls, prefix):
    # secret in the agent argument
    out = await tools[f"{prefix}_verdict"](agent="sk_live_deadbeefcafe1234")
    assert json.loads(out)["error"] == "secret_shaped_input_rejected"
    # secret nested in the verify mandate body
    out2 = await tools[f"{prefix}_verify"](
        agent=AGENT,
        mandate={"private_key": "0x" + "a" * 64},
        actions=[],
    )
    assert json.loads(out2)["error"] == "secret_shaped_input_rejected"
    # NOTHING was forwarded
    assert calls["get"] == [] and calls["post"] == []


# --- the name: molcheck_* is current, moltproof_* stays as a marked alias ------
NAMES = ["verdict", "mandate", "evidence", "registry", "verify"]


def test_every_tool_exists_under_both_names_and_nothing_else(tools):
    expected = {f"{p}_{n}" for p in ("molcheck", "moltproof") for n in NAMES}
    assert set(tools) == expected


def test_alias_is_the_same_function(tools):
    for n in NAMES:
        assert tools[f"moltproof_{n}"] is tools[f"molcheck_{n}"]


def test_alias_description_says_deprecated_and_names_the_new_tool(mcp):
    for n in NAMES:
        alias = mcp.descriptions[f"moltproof_{n}"]
        assert alias.startswith(f"Deprecated alias of molcheck_{n}")
        assert "Deprecated" not in mcp.descriptions[f"molcheck_{n}"]
