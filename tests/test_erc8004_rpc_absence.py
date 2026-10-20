"""A rate limit is not an answer about the agent.

`/resolve/erc8004/{id}` returned 404 for anything `ownerOf` raised on. BASE_RPC
is the public Base endpoint and it rate-limits: on 2026-09-22, six calls in a
row to agent 21351 — on chain since registration, owner 0x3802… — produced
`200 404 404 200 404 404`, and the 404s were `429 Too Many Requests` wearing
the words "not found on Base IdentityRegistry".

That is the same defect the MCP tools had the same day, one layer down. An
absence of information was reported as a checked negative, and the caller had
no way to tell the two apart.
"""

import asyncio

import pytest

from app.erc8004 import (
    RPC_ATTEMPTS,
    _is_transport_error,
    resolve_onchain_agent,
)


class _Rate(Exception):
    pass


TRANSPORT = [
    Exception("429 Client Error: Too Many Requests for url: https://mainnet.base.org/"),
    Exception("HTTPSConnectionPool(host='mainnet.base.org'): Read timed out."),
    Exception("502 Server Error: Bad Gateway"),
    Exception("503 Service Unavailable"),
    ConnectionError("Connection aborted."),
    TimeoutError("timed out"),
]

ANSWERED = [
    Exception("execution reverted: ERC721: invalid token ID"),
    Exception("Could not transact with/call contract function, is contract deployed?"),
    ValueError("{'code': 3, 'message': 'execution reverted'}"),
]


@pytest.mark.parametrize("exc", TRANSPORT, ids=lambda e: str(e)[:28])
def test_a_transport_failure_is_recognised(exc):
    assert _is_transport_error(exc) is True


@pytest.mark.parametrize("exc", ANSWERED, ids=lambda e: str(e)[:28])
def test_a_contract_answer_is_not_a_transport_failure(exc):
    """A revert means the chain replied and there is no such token. Treating it
    as transport would retry forever and then report 503 for an agent that
    genuinely is not registered."""
    assert _is_transport_error(exc) is False


class _Contract:
    """Stands in for the identity contract: `ownerOf(id).call` raises or returns."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.functions = self

    def ownerOf(self, agent_id):  # noqa: N802 — mirrors the ABI name
        return self

    def call(self):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    def tokenURI(self, agent_id):  # noqa: N802
        raise Exception("no uri")


def _run(monkeypatch, outcomes):
    c = _Contract(outcomes)
    monkeypatch.setattr("app.erc8004.get_identity_contract", lambda: c)
    monkeypatch.setattr("app.erc8004.RPC_BACKOFF_SECONDS", 0)
    return asyncio.run(resolve_onchain_agent(21351)), c


def test_a_rate_limit_is_reported_as_unavailable_not_absent(monkeypatch):
    """The whole point. 429 must never read as "this agent does not exist"."""
    rate = Exception("429 Client Error: Too Many Requests")
    result, _ = _run(monkeypatch, [rate] * RPC_ATTEMPTS)
    assert result.get("unavailable") is True
    assert result.get("absent") is not True
    assert "not found" not in result["error"].lower()


def test_a_rate_limit_is_retried(monkeypatch):
    """One 429 then an answer: the caller sees the agent, not an error."""
    result, contract = _run(
        monkeypatch,
        [Exception("429 Too Many Requests"), "0x380238347e58435f40B4da1F1A045A271D5838F5"],
    )
    assert "error" not in result
    assert result["owner"] == "0x380238347e58435f40B4da1F1A045A271D5838F5"
    assert contract.calls == 2


def test_a_missing_token_is_still_absent(monkeypatch):
    """The 404 that was always correct has to survive the fix."""
    result, contract = _run(monkeypatch, [Exception("execution reverted: invalid token ID")])
    assert result.get("absent") is True
    assert result.get("unavailable") is not True
    assert contract.calls == 1, "a revert must not be retried"


def test_a_healthy_call_is_not_retried(monkeypatch):
    result, contract = _run(monkeypatch, ["0xabc"])
    assert "error" not in result
    assert contract.calls == 1
