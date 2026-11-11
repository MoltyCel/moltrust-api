"""renew_at — the date from which renewing a credential starts to make sense.

A credential runs for 365 days (app/credentials.py). Nothing an agent ever
receives from us says so, and nothing says that renewing it is free. Of the 525
agents in the registry on 2026-10-16, 241 made their one independent call on the
day they registered and were never seen again; the answer they got that day
carried no date to come back for.

This module is that date. One field, `renew_at`, in every 200 and every 402 from
an endpoint that issues or reads credentials.

The date is the earliest expiry among the subject's live credentials, minus the
lead below. It may sit in the past, and it is published that way: an agent whose
credential is already due is exactly the case the field exists for, and clamping
the value to "now" would hide it.
"""
from __future__ import annotations

import datetime
import json
from typing import Any, Optional

#: How long before expiry a renewal is worth making. A credential runs a year,
#: so a month is both long enough for an agent that calls monthly to see the
#: date before it matters, and short enough that renewing is not a way to hold a
#: permanently fresh validity window at no cost.
RENEW_LEAD_DAYS = 30

#: The endpoint that performs the renewal. Not in the scripted-endpoint list of
#: app/sql/registry_export.sql, so a call to it counts as activity — which is
#: the point of making it free.
RENEW_PATH = "/credentials/renew"

#: Where the field appears. Everything under these three prefixes either hands
#: out a credential or answers a question about one.
PREFIXES = ("/credentials/", "/identity/verify/", "/vc/")

#: Status codes the field is added to. A 4xx that is not a 402 is an error about
#: the request, and a renewal date would answer a question nobody asked.
STATUSES = (200, 402)

#: Bodies larger than this are passed through untouched. The field is worth one
#: parse of a small JSON answer, not of a megabyte.
MAX_BODY_BYTES = 256 * 1024


def applies_to(path: str) -> bool:
    """Is this one of the credential-bearing endpoints?"""
    return path.startswith(PREFIXES)


def subject_of(path: str, caller_did: Optional[str]) -> Optional[str]:
    """Whose renewal the answer is about.

    `/identity/verify/<did>` answers a question about the DID in the path, so
    the date belongs to that DID. Everywhere else the answer is about the caller
    and the date is the caller's.
    """
    prefix = "/identity/verify/"
    if path.startswith(prefix):
        rest = path[len(prefix):].split("?", 1)[0].strip("/")
        return rest or None
    return caller_did


def renew_at(expires_at: Optional[datetime.datetime]) -> Optional[str]:
    """The renewal date for a credential that expires at `expires_at`.

    None when there is nothing to renew. ISO-8601 in UTC with a `Z`, because a
    date whose zone a reader has to infer is a date two readers read differently.
    """
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    due = expires_at.astimezone(datetime.timezone.utc) - datetime.timedelta(days=RENEW_LEAD_DAYS)
    return due.replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def earliest_expiry(conn, did: str) -> Optional[datetime.datetime]:
    """When the subject's first credential falls due, or None if it holds none.

    Revoked credentials are out: a revoked credential is not renewed, it is
    replaced, and letting one set the date would send every holder of a revoked
    credential to an endpoint that will refuse.
    """
    return await conn.fetchval(
        "SELECT min(expires_at) FROM credentials "
        "WHERE subject_did = $1 AND NOT revoked AND expires_at IS NOT NULL",
        did,
    )


async def renew_at_for_did(conn, did: Optional[str]) -> Optional[str]:
    """`renew_at` for a DID, read from its credentials."""
    if not did:
        return None
    return renew_at(await earliest_expiry(conn, did))


def gate_reason(payload: Any) -> str:
    """The reason code for the gate block, taken from the body's own error.

    The 402 already names why it was refused. Repeating that name inside the
    block keeps one answer from carrying two different reasons.
    """
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, str) and err:
            return err
    return "payment_required"


def with_renew_at(payload: Any, value: Optional[str], status_code: int) -> Any:
    """Add the renewal date to a response body.

    On a 402 it goes inside a `moltrustGate` block, the same name the gate in
    moltguard uses for what it decided, so a caller that reads one reads the
    other. On a 200 it sits at the top level.

    `None` is written out rather than omitted: a subject that holds no
    credential has no renewal date, and a parser that has to tell "no date" from
    "field forgotten" cannot.
    """
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    if status_code == 402:
        block = out.get("moltrustGate")
        block = dict(block) if isinstance(block, dict) else {"reason": gate_reason(payload)}
        block.setdefault("renew_at", value)
        out["moltrustGate"] = block
    else:
        out.setdefault("renew_at", value)
    return out


def rewrite_body(body: bytes, value: Optional[str], status_code: int) -> Optional[bytes]:
    """The response body with the date in it, or None if it stays as it is.

    A body that is not a JSON object — a list, a bare string, anything that
    failed to parse — comes back as None and is passed through. There is no
    place in it to put a field without changing what it is.
    """
    if len(body) > MAX_BODY_BYTES:
        return None
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    out = with_renew_at(payload, value, status_code)
    if out == payload:
        return None
    return json.dumps(out).encode()
