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


# ── HTTP caching of anything that shows a revocation state ──────────────────
#
# A revocation that the API knows and a cache does not is not in effect. Until
# 2026-10-20 both SVG badges went out with `max-age=3600, s-maxage=3600`: a
# browser, an image proxy (GitHub camo, a mail client) or any shared cache could
# keep drawing "85 / A" for an hour after the DID was revoked, without asking.
#
# `no-cache` lets a cache keep the copy but obliges it to revalidate before
# every use; the ETag is a hash of the body, and the body carries the
# revocation state, so after a revocation the validator no longer matches and
# the answer is a fresh 200 REVOKED, never a 304 for the old grade. No
# `s-maxage`, no `stale-while-revalidate`, no `stale-if-error`: each of them
# would let some cache show the old grade once more.
#
# The cost: every view of an embedded badge reaches the origin. An unchanged
# badge costs a 304 without a body. What this cannot reach is a response
# stored before this change under the old header; that tail ends one hour
# after the deploy, and no one can purge a browser cache.
REVOCATION_SENSITIVE_CACHE_CONTROL = "public, no-cache"


def _etag_matches(if_none_match: Optional[str], etag: str) -> bool:
    # RFC 9110 13.1.2: If-None-Match uses the weak comparison.
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    opaque = etag[2:] if etag.startswith("W/") else etag
    for tag in if_none_match.split(","):
        tag = tag.strip()
        if tag.startswith("W/"):
            tag = tag[2:]
        if tag == opaque:
            return True
    return False


def revalidated_response(request, body: bytes, media_type: str,
                         headers: Optional[Mapping[str, str]] = None):
    """A response for a route whose body states a revocation status.

    `Cache-Control: public, no-cache` plus a strong ETag over `body`; a
    matching If-None-Match gets a 304. Build `body` after the revocation check,
    so the ETag cannot outlive the state it was computed from.
    """
    import hashlib
    from starlette.responses import Response

    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    out = dict(headers or {})
    out["Cache-Control"] = REVOCATION_SENSITIVE_CACHE_CONTROL
    out["ETag"] = etag
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=out)
    return Response(content=body, media_type=media_type, headers=out)
