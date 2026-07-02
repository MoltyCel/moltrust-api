"""Lazy, cached Org (ASN) resolution via IPinfo Lite.

Used by the admin callers dashboard ONLY — never on the request hot path
(``RequestLoggerMiddleware``). Results are cached per /24 in ``ip_org_cache``
with a 24h TTL (orgs change rarely). Any error / timeout / missing
``IPINFO_TOKEN`` yields empty strings and never raises.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_LITE_URL = "https://api.ipinfo.io/lite/{ip}"
# Bound concurrent IPinfo calls on a cold-cache dashboard load.
_SEM = asyncio.Semaphore(8)

_pool = None


def set_pool(pool) -> None:
    """Wire the shared asyncpg pool (called once at app startup)."""
    global _pool
    _pool = pool


async def ensure_table(conn) -> None:
    """Create ip_org_cache if absent (idempotent; mirrors the migration)."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ip_org_cache (
            ip_prefix  varchar(45) PRIMARY KEY,
            as_name    varchar(200),
            country    varchar(100),
            updated_at timestamptz DEFAULT now()
        )
        """
    )


async def get_org(ip: str) -> tuple[str, str]:
    """Return ``(as_name, country)`` for an IP/prefix — cached 24h via IPinfo Lite.

    Cache-first; on miss/stale performs one IPinfo Lite lookup (bounded
    concurrency) and upserts. Returns ``("", "")`` on any failure or when
    ``IPINFO_TOKEN`` is unset — never raises.
    """
    if not ip or _pool is None:
        return "", ""

    # 1) fresh cache hit (< 24h)
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT as_name, country FROM ip_org_cache "
                "WHERE ip_prefix = $1 AND updated_at > now() - interval '24 hours'",
                ip,
            )
        if row is not None:
            return row["as_name"] or "", row["country"] or ""
    except Exception:
        return "", ""  # table missing / DB issue -> degrade to empty, no crash

    # 2) miss or stale -> IPinfo Lite lookup
    token = os.environ.get("IPINFO_TOKEN")
    if not token:
        return "", ""

    as_name = country = ""
    cacheable = False
    try:
        async with _SEM:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(
                    _LITE_URL.format(ip=ip),
                    params={"token": token},
                    headers={"User-Agent": "moltrust-api/1.0"},
                )
        if r.status_code == 200:
            cacheable = True
            d = r.json()
            if not d.get("bogon"):  # private/reserved -> definitive empty
                as_name = (d.get("as_name") or d.get("asn") or "")[:200]
                country = (d.get("country") or "")[:100]
        elif r.status_code in (400, 404):
            cacheable = True  # definitive "no data" -> cache empty, stop re-hitting
        # 401/403/429/5xx -> transient/config -> do not cache, retry next time
    except Exception:
        return "", ""

    if cacheable:
        try:
            async with _pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO ip_org_cache (ip_prefix, as_name, country, updated_at) "
                    "VALUES ($1, $2, $3, now()) "
                    "ON CONFLICT (ip_prefix) DO UPDATE SET "
                    "as_name=EXCLUDED.as_name, country=EXCLUDED.country, updated_at=now()",
                    ip, as_name or None, country or None,
                )
        except Exception:
            pass

    return as_name, country
