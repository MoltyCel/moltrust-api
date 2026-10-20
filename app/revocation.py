"""One definition of "this DID is revoked", for every route that speaks about one.

A DID is revoked when `agents.revoked_at` is set. Revocation is the row update
`UPDATE agents SET revoked_at = now(), revocation_reason = ...` (POST
/identity/revoke); nothing else marks it.

Until 2026-10-07 each route answered this question on its own, and most did not
ask it: /identity/verify and the JSON badge said `verified: true` for every
revoked DID while /skill/trust-score said `grade: REVOKED` for the same ones.
The SVG badges, the wallet lookups, the ERC-8004 lookups, the trust gate and
x402/verify kept printing a letter grade, an ALLOW or `verified: true`. Every
route that prints a grade, a score, a badge or a verification status for a DID
now asks here, so the answer cannot differ between two of them.

What a route does with a revoked DID:
  - `verified` (or `active`) is false,
  - `revoked` carries `{at, reason}`,
  - no letter grade and no score: `grade` is REVOKED_GRADE, the score is None
    (or 0.0 where an existing contract already promised a number),
  - a decision (trust gate, payment readiness) is a refusal.

Invariant: docs/invariants/c-no-endpoint-affirms-a-revoked-did.yaml.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

REVOKED_GRADE = "REVOKED"


def _get(row: Any, key: str) -> Any:
    # asyncpg.Record and dict both have .get; a Record without the column
    # raises on [] but answers .get with the default.
    if row is None:
        return None
    try:
        return row.get(key)
    except AttributeError:
        return None


def revocation_of(row: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """`{at, reason}` when the agents row is revoked, else None.

    `row` is an agents row (or any mapping) that selected `revoked_at` and,
    ideally, `revocation_reason`. A row that did not select `revoked_at`
    cannot be judged; that is a bug in the caller, so it raises instead of
    reading as "not revoked".
    """
    if row is None:
        return None
    if "revoked_at" not in row.keys():
        raise KeyError("revocation_of needs a row that selected agents.revoked_at")
    at = row["revoked_at"]
    if at is None:
        return None
    return {
        "at": at.isoformat() if hasattr(at, "isoformat") else str(at),
        "reason": _get(row, "revocation_reason"),
    }


async def revocation_for_did(conn, did: str) -> Optional[dict]:
    """`{at, reason}` for a revoked DID, None for a live or unknown one."""
    row = await conn.fetchrow(
        "SELECT revoked_at, revocation_reason FROM agents WHERE did = $1", did,
    )
    return revocation_of(row)


async def revoked_among(conn, dids: Iterable[str]) -> dict[str, dict]:
    """The revoked DIDs among `dids`, each mapped to its `{at, reason}`."""
    dids = list({d for d in dids if d})
    if not dids:
        return {}
    rows = await conn.fetch(
        "SELECT did, revoked_at, revocation_reason FROM agents "
        "WHERE did = ANY($1::text[]) AND revoked_at IS NOT NULL",
        dids,
    )
    return {r["did"]: revocation_of(r) for r in rows}
