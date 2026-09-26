"""Where every Base RPC call goes, in one place.

Four modules each held their own `BASE_RPC = "https://mainnet.base.org"`. Four
copies of an endpoint is four places to change when it moves, and it has to
move: the public endpoint promises nothing and throttles at its own discretion.
The load test for the track-record path measured what that costs — a hundred
concurrent issuances put about two thousand calls on it and **73 of 100 came
back unanswered**.

One environment variable now decides for all of them, with the public endpoint
as the fallback so nothing breaks before the variable is set.

    BASE_RPC=https://rpc.ankr.com/base/<key>

Sizing, for whoever picks the plan. The track-record path needs one
`eth_getTransactionCount` per issuance plus about twenty-two calls the first
time a wallet is seen, cached permanently after that. At a hundred issuances a
day with every wallet new, that is 2,300 calls a day and roughly 70,000 a
month — under one percent of the smallest free tier on offer. The reason to
move is not volume. It is that a free tier states a number and the public
endpoint does not.
"""

from __future__ import annotations

import os

#: The public endpoint. Correct, free, and entitled to refuse at any moment.
PUBLIC_BASE_RPC = "https://mainnet.base.org"


def base_rpc_url() -> str:
    """The Base endpoint this process should use.

    Read on every call rather than cached at import, so a restart is enough to
    move the whole service to a new provider and no module holds a stale copy.
    """
    return os.getenv("BASE_RPC", "").strip() or PUBLIC_BASE_RPC


def is_public() -> bool:
    """True when nothing has been configured and we are on the shared endpoint.

    Worth logging at startup: a service that believes it has a quota and does
    not is the case the load test found.
    """
    return base_rpc_url() == PUBLIC_BASE_RPC
