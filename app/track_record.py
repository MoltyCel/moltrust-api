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

import datetime
import json
import logging
import os
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


class NotEligible(Exception):
    """The wallet does not clear the published threshold, with the reason."""


def _rpc(method: str, params: list) -> Optional[Any]:
    """One JSON-RPC call against Base. None on any failure."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    req = Request(
        BASE_RPC,
        data=body.encode(),
        headers={"content-type": "application/json", "User-Agent": "MolTrust-TrackRecord/1.0"},
    )
    try:
        # The URL is BASE_RPC, a module constant read from the environment at
        # import; no caller-supplied value reaches it, and the only scheme it
        # has ever held is https. The marker has to sit on the call itself —
        # on its own line above, bandit never sees it.
        with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as r:  # noqa: S310  # nosec B310
            return json.loads(r.read()).get("result")
    except Exception as exc:  # noqa: BLE001 — surfaced as "could not measure"
        log.warning("track-record rpc %s failed: %s", method, exc)
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


def measure(wallet: str) -> dict:
    """What the wallet has done. Raises NotEligible when it cannot be measured.

    The history itself comes from the cold-start fetcher, which already reads
    Blockscout and already caps and shapes what it returns. Duplicating it here
    would give the two paths two answers about the same wallet.
    """
    from app.cold_start import fetch_blockscout_wallet

    nonce = wallet_nonce(wallet)
    if nonce is None:
        raise NotEligible(
            "could not read the wallet's transaction count from Base; try again shortly"
        )

    history = fetch_blockscout_wallet(wallet) or {}
    return {
        "wallet_address": wallet,
        "wallet_chain": REQUIRED_CHAIN,
        "nonce": nonce,
        "wallet_age_days": int(history.get("age_days") or 0),
        "wallet_tx_count": int(history.get("tx_count") or 0),
        "usdc_volume": float(history.get("usdc_volume") or 0.0),
        "measured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "thresholds": {"min_nonce": MIN_NONCE, "min_age_days": MIN_AGE_DAYS},
    }


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
