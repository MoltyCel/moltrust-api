"""Second factor for /admin/login (2026-10-08).

Uses the TOTP secret a user already confirmed for reseller-admin elevation
(reseller_admin_2fa), so nobody enrolls twice. Two things the elevation path
does not do: a code is accepted once only — every accepted time step is
recorded in admin_totp_used and a second use of it is refused — and the
factor is switched by ADMIN_TOTP_REQUIRED.

ADMIN_TOTP_REQUIRED unset or "0": a login without a code still works (the
transition, until the first login with a code has been confirmed); a login
that sends a code must send a valid, unused one. "1": no session without a
valid code, and a user without a confirmed secret cannot log in. Without
RESELLER_ADMIN_TOTP_KEY the second factor cannot be checked, and with the
switch on that locks everyone out — fail-closed, as the elevation path does.
"""
from __future__ import annotations

import hmac
import os
import time

from app import reseller_admin as ra

STEP = 30


def required() -> bool:
    return os.getenv("ADMIN_TOTP_REQUIRED", "0").strip() == "1"


def matching_step(secret_b32: str, code: str, window: int = 1,
                  now: float | None = None) -> int | None:
    """The time step the code belongs to, or None. Same window as totp_verify."""
    code = str(code or "").strip()
    if not (code.isdigit() and len(code) <= 6):
        return None
    code = code.zfill(6)
    now = time.time() if now is None else now
    for w in range(-window, window + 1):
        ts = now + w * STEP
        if hmac.compare_digest(ra.totp_at(secret_b32, ts), code):
            return int(ts // STEP)
    return None


async def ensure_table(conn) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS admin_totp_used ("
        " username TEXT NOT NULL,"
        " step BIGINT NOT NULL,"
        " used_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        " PRIMARY KEY (username, step))")


async def check(username: str, code: str | None) -> tuple[bool, str]:
    """(accepted, reason). reason: ok, no-key, not-enrolled, missing, invalid,
    replayed, or error (database not reachable — treated like a failed factor)."""
    try:
        return await _check(username, code)
    except Exception as e:  # noqa: BLE001 - the caller decides; never a 500
        return False, f"error-{type(e).__name__}"


async def _check(username: str, code: str | None) -> tuple[bool, str]:
    key = ra._totp_key()
    if not key:
        return False, "no-key"
    user = (username or "").lower()
    async with ra._pool().acquire() as conn:
        secret, confirmed = await ra._read_secret(conn, user, key)
        if not (secret and confirmed):
            return False, "not-enrolled"
        if not code:
            return False, "missing"
        step = matching_step(secret, code)
        if step is None:
            return False, "invalid"
        await ensure_table(conn)
        # The insert is the check: a step already recorded returns nothing.
        got = await conn.fetchval(
            "INSERT INTO admin_totp_used (username, step) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING RETURNING step", user, step)
        if got is None:
            return False, "replayed"
        await conn.execute(
            "DELETE FROM admin_totp_used WHERE used_at < now() - interval '1 day'")
    return True, "ok"
