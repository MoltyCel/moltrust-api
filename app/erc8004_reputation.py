"""MolTrust verdicts as ERC-8004 reputation feedback.

The ValidationRegistry this was originally meant for is deployed on no network
(docs/spec-fakten/erc-8004.md). The ReputationRegistry is deployed on Base, so
that is where MolTrust evidence can be written today.

Three gates stand between a verdict and a transaction, and all three default to
closed:

1. ``ERC8004_REPUTATION_ENABLED`` — off unless set. A registry write spends real
   gas from a real wallet; nothing here happens because a code path was reached.
2. A daily cap on writes. A verdict loop without one drains the wallet at the
   speed of a for-loop.
3. ``execute=True``, which only the CLI passes and only when a human types
   ``--execute``. Everything else is a dry run that returns the transaction it
   would have sent.

Every feedback carries ``feedbackURI`` and ``feedbackHash`` — the document the
verdict came from, and its SHA-256. A reader can fetch the document and check
the hash rather than taking our word for it. That is the difference between
evidence and an assertion, and it is the entire point of writing on-chain.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.moltrust.ch"


def enabled() -> bool:
    """Read at call time, not import time, so the flag can be flipped with a
    restart rather than a redeploy."""
    return os.environ.get("ERC8004_REPUTATION_ENABLED", "").lower() == "true"


def daily_cap() -> int:
    try:
        return max(0, int(os.environ.get("ERC8004_REPUTATION_DAILY_CAP", "20")))
    except ValueError:
        return 20


# kind -> (tag1, URI template, how the verdict maps onto 0..100)
#
# tag1 is what a reader filters on; tag2 stays "moltrust" so every entry we
# write is attributable to us in one query.
VERDICT_KINDS = {
    "score": {
        "tag1": "moltrust:score",
        "uri": "{base}/identity/badge/{subject}",
        "doc": "trust score, 0-100",
    },
    "mandate": {
        "tag1": "moltrust:mandate",
        "uri": "{base}/moltproof/verdict/{subject}",
        "doc": "mandate conformance, 100 conformant / 0 breached",
    },
    "prediction": {
        "tag1": "moltrust:prediction",
        "uri": "{base}/prediction/integrity/{subject}",
        "doc": "prediction verdict, 100 met / 0 missed",
    },
}


class VerdictError(RuntimeError):
    pass


def verdict_uri(kind: str, subject: str) -> str:
    if kind not in VERDICT_KINDS:
        raise VerdictError(f"unknown verdict kind {kind!r}; expected one of {sorted(VERDICT_KINDS)}")
    return VERDICT_KINDS[kind]["uri"].format(base=API_BASE, subject=subject)


def fetch_and_hash(uri: str, timeout: float = 10.0) -> tuple[str, bytes]:
    """Fetch the evidence document and hash exactly the bytes served.

    Hashing a re-serialised copy would produce a digest that does not match what
    a verifier downloads, which makes the hash worse than useless: it would look
    like tampering.
    """
    r = httpx.get(uri, timeout=timeout, headers={"User-Agent": "MolTrust-erc8004/1.0"})
    r.raise_for_status()
    return uri, hashlib.sha256(r.content).digest()


def clamp_value(value: float) -> int:
    """ERC-8004 feedback is an int128 with declared decimals; we publish whole
    points on 0..100 so a consumer needs no scale conversion."""
    return max(0, min(100, int(round(value))))


def build_feedback(kind: str, subject: str, agent_id: int, value: float,
                   fetch: bool = True) -> dict:
    """Assemble the giveFeedback arguments for one verdict."""
    uri = verdict_uri(kind, subject)
    digest = b"\x00" * 32
    if fetch:
        try:
            uri, digest = fetch_and_hash(uri)
        except Exception as e:
            # An unreachable evidence document is a reason not to publish, not a
            # reason to publish without one. A feedback whose hash is zero says
            # "trust me", which is the opposite of the point.
            raise VerdictError(f"evidence document unreachable ({type(e).__name__}): {uri}") from e
    return {
        "agentId": int(agent_id),
        "value": clamp_value(value),
        "valueDecimals": 0,
        "tag1": VERDICT_KINDS[kind]["tag1"],
        "tag2": "moltrust",
        "endpoint": f"{API_BASE}/resolve/erc8004/{int(agent_id)}",
        "feedbackURI": uri,
        "feedbackHash": digest,
    }


async def budget_remaining(conn) -> int:
    """Writes still allowed today."""
    used = await conn.fetchval(
        "SELECT count FROM erc8004_write_budget WHERE day = CURRENT_DATE"
    ) or 0
    return max(0, daily_cap() - int(used))


async def claim_budget(conn) -> bool:
    """Take one write from today's allowance. False when the cap is reached.

    Claimed before the transaction is sent, not after: a send that times out
    may still land, and a budget that only counts confirmed writes would let a
    retry loop spend past the cap.
    """
    if daily_cap() <= 0:
        return False
    row = await conn.fetchrow(
        "INSERT INTO erc8004_write_budget (day, count) VALUES (CURRENT_DATE, 1) "
        "ON CONFLICT (day) DO UPDATE SET count = erc8004_write_budget.count + 1 "
        "WHERE erc8004_write_budget.count < $1 "
        "RETURNING count",
        daily_cap(),
    )
    return row is not None


async def release_budget(conn) -> None:
    """Give back a claim the transaction never used."""
    try:
        await conn.execute(
            "UPDATE erc8004_write_budget SET count = GREATEST(count - 1, 0) "
            "WHERE day = CURRENT_DATE"
        )
    except Exception:
        # One unusable slot out of the daily allowance is cheaper than failing
        # the caller over bookkeeping.
        pass


async def publish_verdict(conn, kind: str, subject: str, agent_id: int, value: float,
                          execute: bool = False) -> dict:
    """Publish one verdict, or describe what publishing it would do.

    Returns a dict that always says what happened and why, so a dry run and a
    refusal are distinguishable from a success without reading logs.
    """
    feedback = build_feedback(kind, subject, agent_id, value)
    preview = {
        **{k: v for k, v in feedback.items() if k != "feedbackHash"},
        "feedbackHash": "0x" + feedback["feedbackHash"].hex(),
    }

    if not enabled():
        return {"status": "disabled", "detail": "ERC8004_REPUTATION_ENABLED is not true",
                "would_send": preview}

    if not execute:
        return {"status": "dry-run", "detail": "pass execute=True to send", "would_send": preview}

    if not await claim_budget(conn):
        return {"status": "budget-exhausted",
                "detail": f"daily cap of {daily_cap()} writes is used up",
                "would_send": preview}

    try:
        from app.erc8004 import _get_w3, _get_reputation_write_contract, BASE_CHAIN_ID, _WRITE_ADDR, _WRITE_KEY
        w3 = _get_w3()
        contract = _get_reputation_write_contract()
        nonce = w3.eth.get_transaction_count(_WRITE_ADDR, "pending")
        gas_price = w3.eth.gas_price
        tx = contract.functions.giveFeedback(
            feedback["agentId"], feedback["value"], feedback["valueDecimals"],
            feedback["tag1"], feedback["tag2"], feedback["endpoint"],
            feedback["feedbackURI"], feedback["feedbackHash"],
        ).build_transaction({
            "from": _WRITE_ADDR, "nonce": nonce, "chainId": BASE_CHAIN_ID,
            "gas": 300000,
            "maxFeePerGas": gas_price * 3,
            "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
        })
        signed = w3.eth.account.sign_transaction(tx, _WRITE_KEY)
        tx_hash = w3.to_hex(w3.eth.send_raw_transaction(signed.raw_transaction))
        logger.info("erc8004 verdict published kind=%s agent=%s tx=%s", kind, agent_id, tx_hash)
        return {"status": "sent", "tx_hash": tx_hash,
                "basescan": f"https://basescan.org/tx/{tx_hash}", "sent": preview}
    except Exception as e:
        await release_budget(conn)
        logger.error("erc8004 verdict failed kind=%s agent=%s: %s", kind, agent_id, type(e).__name__)
        return {"status": "error", "detail": f"{type(e).__name__}: {e}", "would_send": preview}


CREATE_BUDGET_TABLE = """
CREATE TABLE IF NOT EXISTS erc8004_write_budget (
    day   date    NOT NULL PRIMARY KEY,
    count integer NOT NULL DEFAULT 0
)
"""


async def ensure_budget_table(conn) -> None:
    await conn.execute(CREATE_BUDGET_TABLE)
