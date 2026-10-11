"""Rate-limit bucket for POST /enforce/check: per facilitator key, else per client IP.

The global limiter keys every route by client IP (`_ratelimit_key`). A facilitator that
runs the enforce check as a counter-check on every payment then shares one bucket with
everything else behind its egress IP, and a busy one runs into 60/minute on its own.

Keys with `api_keys.tier = 'facilitator'` get their own bucket and their own limit. The
set is held as SHA-256 hashes, refreshed from the database in the background; the key
function is synchronous and never touches the database. A header that is not a known
facilitator key falls back to the IP bucket, so sending a random key buys nothing.
Authentication is unchanged: the route still depends on `verify_api_key_or_did`.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Callable, FrozenSet

logger = logging.getLogger("moltrust")

DEFAULT_LIMIT = "60/minute"
FACILITATOR_LIMIT = os.getenv("ENFORCE_FACILITATOR_LIMIT", "600/minute")
REFRESH_SECONDS = 60
_PREFIX = "fac:"

_facilitator_hashes: FrozenSet[str] = frozenset()


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def make_rate_key(ip_key: Callable) -> Callable:
    """Key function for the enforce routes. `ip_key` is the existing `_ratelimit_key`."""
    def enforce_rate_key(request) -> str:
        presented = request.headers.get("X-API-Key")
        if presented:
            h = _hash(presented)
            if h in _facilitator_hashes:
                return _PREFIX + h[:32]
        return ip_key(request)
    return enforce_rate_key


def enforce_limit(key: str) -> str:
    """Limit provider; slowapi passes the bucket key because the parameter is named `key`."""
    return FACILITATOR_LIMIT if key.startswith(_PREFIX) else DEFAULT_LIMIT


async def refresh(pool) -> int:
    global _facilitator_hashes
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT key FROM api_keys WHERE tier = 'facilitator' AND active = true")
    _facilitator_hashes = frozenset(_hash(r["key"]) for r in rows)
    return len(_facilitator_hashes)


async def refresh_forever(get_pool) -> None:
    while True:
        pool = get_pool()
        if pool is not None:
            try:
                n = await refresh(pool)
                logger.info("enforce rate limit: %d facilitator keys", n)
            except Exception as exc:  # keep the last set; never crash the loop
                logger.warning("enforce rate limit refresh failed: %s", type(exc).__name__)
        await asyncio.sleep(REFRESH_SECONDS)
