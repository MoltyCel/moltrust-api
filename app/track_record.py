"""Track-record credentials: a wallet's own history, signed and anchored.

Phase 2 withholds a trust score until an agent has three endorsers. No agent in
the registry has three, and a newly registered one has no way to get them, so
for every agent that arrived through a bounty the score is null and every gate
that reads it denies. The gate was not wrong — a score nobody computed is not a
low score — but the result was a discount no outsider could ever earn.

A track record is the way in. The agent binds a wallet it controls
(`POST /identity/bind`, a signature over a nonce), and we issue one credential
stating what that wallet has actually done on Base. The credential is anchored
like every other, and the anchoring transaction is what a gate checks against.

What it is worth rests on one thing: the wallet had to exist before the agent
wanted the discount. The thresholds below are deliberately low — they are not a
quality bar, they are a cost. A wallet that has sent a transaction and is a week
old cannot be conjured at the moment of asking.

The numbers are published in developers.html, so they are constants here and not
configuration. A threshold an operator can move quietly is not a threshold a
relying party can rely on.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import time
from typing import Any, Optional
from urllib.request import Request, urlopen

log = logging.getLogger("moltrust.track_record")

CREDENTIAL_TYPE = "TrackRecordCredential"

#: The wallet must have sent at least one transaction of its own. TaskMarket and
#: most agent platforms relay gaslessly, so a worker wallet that has only ever
#: been signed *for* still sits at nonce 0 — 81 of the 89 wallets in the first
#: bounty round did. Sending one costs gas and a decision, which is the point.
MIN_NONCE = 1

#: And it must not have been created for this request. Seven days, measured from
#: the first transaction Blockscout knows about.
MIN_AGE_DAYS = 7

#: Base only. The anchor is on Base, the x402 rail is on Base, and the history
#: fetcher reads Base. A Solana binding is a real binding and still not this.
REQUIRED_CHAIN = "base"

BASE_RPC = os.getenv("BASE_RPC", "https://mainnet.base.org")
HTTP_TIMEOUT_SECONDS = 8

#: How far back the binary search looks for a wallet's first transaction. Base
#: produces a block every two seconds, so this is about forty days — comfortably
#: past the seven-day threshold. A wallet already active at the start of the
#: window is older than the window, which is all the threshold needs to know.
SEARCH_WINDOW_BLOCKS = 1_800_000

#: How many first-transaction searches may run at once. Each is about twenty-one
#: RPC calls, so a hundred unbounded issuances put two thousand requests on the
#: node in a few seconds and the public endpoint rate-limits — measured, on
#: 2026-09-25: 73 of 100 came back "Base did not answer". Bounding the searches
#: turns a burst into a queue, which is slower for one caller and the difference
#: between an answer and an error for all of them.
MAX_CONCURRENT_SEARCHES = 3

#: Retries for a single RPC call, with a widening pause. A rate limit is a wait,
#: not a verdict.
RPC_ATTEMPTS = 6


class NotEligible(Exception):
    """The wallet does not clear the published threshold, with the reason."""


def _rpc(method: str, params: list) -> Optional[Any]:
    """One JSON-RPC call against Base, retried through a rate limit.

    A single attempt was enough until a hundred issuances arrived together and
    the public endpoint started refusing. A refusal under load is a wait, not an
    answer about the wallet, so treating it as one produced 73 failures out of
    100 in the load test that found this.
    """
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    last = ""
    for attempt in range(RPC_ATTEMPTS):
        req = Request(
            BASE_RPC,
            data=body.encode(),
            headers={"content-type": "application/json",
                     "User-Agent": "MolTrust-TrackRecord/1.0"},
        )
        try:
            # The URL is BASE_RPC, a module constant read from the environment
            # at import; no caller-supplied value reaches it, and the only
            # scheme it has ever held is https. The marker has to sit on the
            # call itself — on its own line above, bandit never sees it.
            with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as r:  # noqa: S310  # nosec B310
                payload = json.loads(r.read())
            if "result" in payload:
                return payload["result"]
            last = str(payload.get("error"))[:120]
        except Exception as exc:  # noqa: BLE001 — retried, then surfaced
            last = f"{type(exc).__name__}: {exc}"
        if attempt < RPC_ATTEMPTS - 1:
            time.sleep(min(0.4 * (2 ** attempt), 6.0))
    log.warning("track-record rpc %s failed after %d attempts: %s",
                method, RPC_ATTEMPTS, last)
    return None


def wallet_nonce(wallet: str) -> Optional[int]:
    """Transactions this wallet has sent, from the node's own state.

    Read as the nonce rather than counted from an explorer index. An index can
    lag or paginate; the nonce is what the chain itself answers, and it is
    exactly the number the threshold is about.
    """
    raw = _rpc("eth_getTransactionCount", [wallet, "latest"])
    if not isinstance(raw, str):
        return None
    try:
        return int(raw, 16)
    except ValueError:
        return None


def first_outgoing(wallet: str) -> dict:
    """When this wallet first sent a transaction, from the node alone.

    Binary search over `eth_getTransactionCount` at historical blocks: the
    nonce is monotonic, so the block where it first exceeds zero is the block
    of the first outgoing transaction. About twenty-five calls, no explorer.

    Returns `{"block", "ts", "source"}`. `source` is ``node`` for a located
    block, ``node-floor`` when the wallet was already active at the start of
    the window — then the age is at least the window, which is all the
    threshold needs. `block` and `ts` are None when the wallet has never sent.

    Raises NotEligible when the node cannot be reached, because deciding a
    threshold on an unread chain is worse than asking the caller to retry.
    """
    head_raw = _rpc("eth_blockNumber", [])
    if not isinstance(head_raw, str):
        raise NotEligible("Base did not answer for the current block; try again shortly")
    head = int(head_raw, 16)
    low = max(head - SEARCH_WINDOW_BLOCKS, 1)

    def nonce_at(block: int) -> int:
        raw = _rpc("eth_getTransactionCount", [wallet, hex(block)])
        if not isinstance(raw, str):
            raise NotEligible("Base did not answer for this wallet; try again shortly")
        return int(raw, 16)

    if nonce_at(head) == 0:
        return {"block": None, "ts": None, "source": "node"}
    if nonce_at(low) >= 1:
        # Older than the window. The exact block is not worth another search;
        # the threshold asks whether it is at least seven days old.
        blk = _rpc("eth_getBlockByNumber", [hex(low), False]) or {}
        ts = int(blk.get("timestamp", "0x0"), 16) or None
        return {"block": low, "ts": ts, "source": "node-floor"}

    hi = head
    while hi - low > 1:
        mid = (low + hi) // 2
        if nonce_at(mid) == 0:
            low = mid
        else:
            hi = mid
    blk = _rpc("eth_getBlockByNumber", [hex(hi), False]) or {}
    ts = int(blk.get("timestamp", "0x0"), 16) or None
    return {"block": hi, "ts": ts, "source": "node"}


def _age_days_from_ts(ts: Optional[int]) -> int:
    if not ts:
        return 0
    return int((datetime.datetime.now(datetime.timezone.utc).timestamp() - ts) / 86400)


def measure(wallet: str, first_tx: Optional[dict] = None) -> dict:
    """What the wallet has done, decided on the node and enriched if possible.

    The order is the point. Nonce and age both come from the node, which answers
    from its own state and cannot quietly serve a stale page. Blockscout is
    consulted afterwards for the figures that only it has — transfer count and
    USDC volume — and **a failure there does not fail the issuance**.

    That used to be the other way round, and it was a live hazard. A sweep over
    seventy-eight wallets on 2026-09-25 came back with sixty-eight unreadable
    because the explorer throttled; a hundred agents issuing in the same hour
    would have met the same wall and been told "could not measure" instead of
    yes or no.

    The node age is the **first outgoing** transaction, the explorer's is the
    first of any kind, incoming included. So the node figure is a lower bound on
    the real age, and deciding on it can only turn away a wallet that would have
    qualified — never admit one that would not. For a threshold, that is the
    right direction to be wrong in.

    `first_tx` is the cached result of `first_outgoing`; pass it to skip the
    search. Raises NotEligible only when the node itself is unreachable.
    """
    nonce = wallet_nonce(wallet)
    if nonce is None:
        raise NotEligible(
            "could not read the wallet's transaction count from Base; try again shortly"
        )

    if first_tx is None:
        first_tx = first_outgoing(wallet)
    node_age = _age_days_from_ts(first_tx.get("ts"))

    # Enrichment. Never load-bearing, never a reason to refuse.
    history = {}
    enriched = False
    try:
        from app.cold_start import fetch_blockscout_wallet
        history = fetch_blockscout_wallet(wallet) or {}
        enriched = bool(history)
    except Exception as exc:  # noqa: BLE001 — enrichment is optional by design
        log.warning("track-record enrichment unavailable for %s: %s", wallet, exc)

    explorer_age = int(history.get("age_days") or 0)
    age = max(node_age, explorer_age)

    return {
        "wallet_address": wallet,
        "wallet_chain": REQUIRED_CHAIN,
        "nonce": nonce,
        "wallet_age_days": age,
        "wallet_tx_count": int(history.get("tx_count") or 0),
        "usdc_volume": float(history.get("usdc_volume") or 0.0),
        "measured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "thresholds": {"min_nonce": MIN_NONCE, "min_age_days": MIN_AGE_DAYS},
        # How the age was arrived at, so a verifier can tell a measured figure
        # from a lower bound instead of guessing which it got.
        "age_source": "node+explorer" if enriched and explorer_age >= node_age
                      else ("node" if first_tx.get("source") == "node" else "node-floor"),
        "first_outgoing_block": first_tx.get("block"),
        "enriched": enriched,
    }


#: One search per wallet at a time, however many callers ask. Without this, a
#: hundred simultaneous issuances for the same wallet each ran their own search:
#: the load test recorded 48 cache writes for 78 wallets, which is the duplicate
#: work made visible.
_INFLIGHT: dict = {}
_SEARCH_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _semaphore() -> asyncio.Semaphore:
    """Created lazily, because a Semaphore binds to the running loop."""
    global _SEARCH_SEMAPHORE
    if _SEARCH_SEMAPHORE is None:
        _SEARCH_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)
    return _SEARCH_SEMAPHORE


async def cached_first_outgoing(conn, wallet: str) -> dict:
    """`first_outgoing`, remembered. A wallet's first transaction cannot move.

    Twenty-five RPC calls per issuance is fine once and wasteful a hundred
    times, and a hundred simultaneous issuances is exactly the case this has to
    survive. The row is written once and never updated; there is no TTL because
    there is nothing to expire.

    A nonce-0 wallet is cached too, as a row with a null block, so it is not
    re-searched on every attempt by an agent that has not sent anything yet.
    """
    row = await conn.fetchrow(
        "SELECT first_block, first_ts, source FROM wallet_first_tx WHERE wallet = $1",
        wallet,
    )
    if row is not None:
        ts = row["first_ts"]
        return {"block": row["first_block"],
                "ts": int(ts.timestamp()) if ts else None,
                "source": row["source"]}

    # One search per wallet, bounded overall. The first caller does the work
    # and everyone else waits for that same result instead of starting their
    # own — which is both cheaper and the difference between a queue and a
    # burst the node refuses.
    existing = _INFLIGHT.get(wallet)
    if existing is not None:
        return await asyncio.shield(existing)

    async def _search():
        async with _semaphore():
            # Blocking HTTP, so off the event loop.
            return await asyncio.to_thread(first_outgoing, wallet)

    task = asyncio.ensure_future(_search())
    _INFLIGHT[wallet] = task
    try:
        found = await task
    finally:
        _INFLIGHT.pop(wallet, None)

    await conn.execute(
        """INSERT INTO wallet_first_tx (wallet, chain, first_block, first_ts, source)
           VALUES ($1, $2, $3, to_timestamp($4), $5)
           ON CONFLICT (wallet) DO NOTHING""",
        wallet, REQUIRED_CHAIN, found.get("block"),
        found.get("ts"), found.get("source"),
    )
    return found


async def measure_async(conn, wallet: str) -> dict:
    """`measure`, with the cache in front and the blocking work off the loop.

    The semaphore covers the whole measurement, not only the search. A warm
    cache skips the twenty-one search calls and still makes one nonce call per
    issuance, and a hundred of those at once is enough on its own to make the
    public endpoint refuse — measured: 6 failures out of 100 with the search
    already bounded. Queueing the cheap calls too costs a caller some seconds
    and gets everyone an answer.

    Production is gentler than this test: the endpoint itself is capped at six
    issuances a minute per key, so a true hundred-at-once cannot arrive. The
    bound is here for the burst the limiter still lets through, and because a
    public RPC is a shared resource with no promise attached. A dedicated
    endpoint would remove the ceiling rather than raise it.
    """
    first_tx = await cached_first_outgoing(conn, wallet)
    async with _semaphore():
        return await asyncio.to_thread(measure, wallet, first_tx)


def check_eligible(measurement: dict) -> None:
    """Raise NotEligible unless the measurement clears both thresholds."""
    nonce = measurement.get("nonce", 0)
    if nonce < MIN_NONCE:
        raise NotEligible(
            f"this wallet has sent {nonce} transactions and the threshold is {MIN_NONCE}. "
            "A wallet that has only ever been signed for, as gasless relaying leaves it, "
            "does not clear it — send one transaction of your own first."
        )
    age = measurement.get("wallet_age_days", 0)
    if age < MIN_AGE_DAYS:
        raise NotEligible(
            f"this wallet is {age} days old and the threshold is {MIN_AGE_DAYS}. "
            "The credential says the wallet existed before you wanted it to."
        )


def build_claims(did: str, measurement: dict) -> dict:
    """The credential's subject claims.

    Every number a relying party would want to second-guess is in here, next to
    the thresholds it was judged against, so the judgement can be redone from
    the credential alone without asking us what the rule was that day.
    """
    return {
        "id": did,
        "type": CREDENTIAL_TYPE,
        "wallet_address": measurement["wallet_address"],
        "wallet_chain": measurement["wallet_chain"],
        "nonce": measurement["nonce"],
        "wallet_age_days": measurement["wallet_age_days"],
        "wallet_tx_count": measurement["wallet_tx_count"],
        "usdc_volume": measurement["usdc_volume"],
        "measured_at": measurement["measured_at"],
        "thresholds": measurement["thresholds"],
        # Which of the two sources the age rests on. A verifier that cannot tell
        # a measured figure from a lower bound cannot redo the judgement.
        "age_source": measurement.get("age_source", "node"),
        "source": "base-rpc+blockscout",
        # Said plainly inside the artifact, because the artifact outlives the
        # page that explains it: this is a statement about a wallet's public
        # history, not a judgement about the agent behind it.
        "note": (
            "Public on-chain history of a wallet bound to this DID, measured at "
            "issuance. It says the wallet existed and acted before this credential "
            "was asked for. It says nothing about what the agent does."
        ),
    }


async def anchored_track_record(conn, did: str) -> Optional[dict]:
    """The `track_record` object for the gate attestation, or None.

    None until three things hold at once: a credential of this type exists, it
    has not expired, and the anchoring batch has put it on chain. The last one
    is why a freshly issued credential does not open the gate immediately —
    `anchor_tx` is the field a relying party can check, and there is nothing
    honest to put there before the transaction exists.
    """
    row = await conn.fetchrow(
        """SELECT c.issued_at, a.tx_hash
             FROM credentials c
             JOIN credential_anchors a ON a.credential_id = c.id
            WHERE c.subject_did = $1
              AND c.credential_type = $2
              AND c.revoked IS FALSE
              AND c.expires_at > now()
            ORDER BY c.issued_at DESC
            LIMIT 1""",
        did,
        CREDENTIAL_TYPE,
    )
    if row is None or not row["tx_hash"]:
        return None
    issued_at = row["issued_at"]
    return {
        "issued_at": issued_at.isoformat() if hasattr(issued_at, "isoformat") else str(issued_at),
        "anchor_tx": row["tx_hash"],
    }
