"""MoltProof MCP Tools — read-only agent mandate verification.

These five tools are a PURE PASS-THROUGH to the MoltProof REST service on
127.0.0.1:3006/proof/*. They inherit MoltProof's read-only hard gate:

  * public inputs only — a secret-shaped field (private key / seed / API key /
    mnemonic) is rejected here BEFORE anything is forwarded, and the offending
    value is never echoed or logged;
  * no own data path — each tool only calls the corresponding /proof/* endpoint;
  * no writes — verdict/mandate/evidence/registry are GET; verify is a POST that
    recomputes offline and returns a value (the REST side writes nothing).

This mirrors, on the Python gateway, what test/no-secret-route.test.ts enforces
in the TypeScript service — so the gate holds on both surfaces (defence in depth;
the REST service also rejects secret-shaped input independently).
"""

import json
import re
from typing import Any, Optional

import httpx

MOLTPROOF_URL = "http://127.0.0.1:3006"

# --- read-only hard gate (mirror of the TS noSecrets scanner) ----------------
_SECRET_KEY_NAMES = {
    "privatekey", "private_key", "priv", "secret", "secretkey", "seed",
    "mnemonic", "apikey", "api_key", "password", "passwd", "keystore",
    "pkcs8", "signingkey", "signing_key", "sessionkey", "wc_uri",
    "walletconnect", "x-api-key",
}
_UNAMBIGUOUS_VALUE = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"-----BEGIN [A-Z ]*KEY-----"),
    re.compile(r"\bxprv[a-km-zA-HJ-NP-Z1-9]{50,}"),
    re.compile(r"\b(sk_live_|sk-ant-|ghp_|github_pat_|whsec_|sk_test_)[A-Za-z0-9_]{8,}"),
    re.compile(r"\bwc:[0-9a-f]{6,}@\d"),
]


class SecretShapedInput(Exception):
    """Raised (and caught) when a secret-shaped field reaches a tool. Carries a
    field name only — never the value."""


def _looks_like_mnemonic(value: str) -> bool:
    words = value.strip().split()
    return 12 <= len(words) <= 24 and all(re.fullmatch(r"[a-z]{3,8}", w) for w in words)


def _scan(value: Any, key_path: str = "") -> None:
    """Recursively reject secret-shaped keys/values. A bare 0x+64hex is NOT a
    secret by value (it is indistinguishable from a public tx/block hash); it is
    only rejected under a secret-named key."""
    if value is None or isinstance(value, (int, float, bool)):
        return
    if isinstance(value, str):
        if any(rx.search(value) for rx in _UNAMBIGUOUS_VALUE) or _looks_like_mnemonic(value):
            raise SecretShapedInput(key_path or "(value)")
        return
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _scan(item, f"{key_path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).lower() in _SECRET_KEY_NAMES:
                raise SecretShapedInput(str(k))
            _scan(v, f"{key_path}.{k}" if key_path else str(k))


def reject_if_secret(**inputs: Any) -> Optional[str]:
    """Return a generic error string (field name only) if any input is
    secret-shaped, else None. The value is never included."""
    try:
        for k, v in inputs.items():
            if k.lower() in _SECRET_KEY_NAMES:
                raise SecretShapedInput(k)
            _scan(v, k)
        return None
    except SecretShapedInput as e:
        return json.dumps({
            "error": "secret_shaped_input_rejected",
            "detail": "MoltProof MCP tools accept only public inputs (agent address / DID / mandate ref). A secret-shaped field was rejected.",
            "field": str(e),
        })


async def _get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{MOLTPROOF_URL}{path}")
        r.raise_for_status()
        return r.json()


async def _post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{MOLTPROOF_URL}{path}", json=body)
        r.raise_for_status()
        return r.json()


def register_moltproof_tools(mcp) -> None:
    """Register the five read-only MoltProof tools on the shared MCP server."""

    @mcp.tool()
    async def moltproof_verdict(agent: str) -> str:
        """Verdict + per-check breakdown for an agent against its committed mandate.

        Read-only. Recomputable from public chain data + the public mandate.

        Args:
            agent: agent address, did:moltrust / did:web, or ERC-8004 reference (public)
        """
        err = reject_if_secret(agent=agent)
        if err:
            return err
        return json.dumps(await _get(f"/proof/verdict/{agent}"), indent=2)

    @mcp.tool()
    async def moltproof_mandate(agent: str) -> str:
        """The committed AAE mandate for an agent (venues, position cap, validity).

        Read-only.

        Args:
            agent: public agent identifier (address / DID / ERC-8004 ref)
        """
        err = reject_if_secret(agent=agent)
        if err:
            return err
        return json.dumps(await _get(f"/proof/mandate/{agent}"), indent=2)

    @mcp.tool()
    async def moltproof_evidence(agent: str) -> str:
        """Verdict plus the decoded transactions that breached the mandate.

        Read-only.

        Args:
            agent: public agent identifier (address / DID / ERC-8004 ref)
        """
        err = reject_if_secret(agent=agent)
        if err:
            return err
        return json.dumps(await _get(f"/proof/evidence/{agent}"), indent=2)

    @mcp.tool()
    async def moltproof_registry() -> str:
        """Agents with committed mandates and their current verdict.

        Read-only. Ranks NO_MANDATE / NEEDS_REVIEW lower.
        """
        return json.dumps(await _get("/proof/registry"), indent=2)

    @mcp.tool()
    async def moltproof_verify(
        agent: str,
        mandate: dict,
        actions: list,
        signature: Optional[dict] = None,
    ) -> str:
        """Recompute a verdict from public inputs and check its signature.

        Offline and read-only: no DID/mandate/context is fetched here.

        Args:
            agent: public agent identifier
            mandate: the public AAE Verifiable Credential
            actions: public, already-decoded on-chain actions
            signature: optional prior MoltProof signature to check
        """
        err = reject_if_secret(agent=agent, mandate=mandate, actions=actions, signature=signature)
        if err:
            return err
        body: dict = {"agent": agent, "mandate": mandate, "actions": actions}
        if signature is not None:
            body["signature"] = signature
        return json.dumps(await _post("/proof/verify", body), indent=2)
