"""Who brought whom, and the credential that says so (ops G2, strategy ops/wachstum.md).

A registration may name the DID that sent it (`referrer_did` on
/identity/register-pop and, since ops KM1, on /identity/register). The referrer then carries a signed, anchored
`ReferralCredential` saying how many agents it brought and how many of those
activated. Reputation, not money — the paid side is a separate decision (G4).

Three things live here, and the split is deliberate:

**The rule that says which referral counts** is a pure function over facts
(`counts`). Everything that states a referral figure — the registration path,
/identity/verify, the registry proof, the cohort figures — reads this one
function. The SQL gathers facts and decides nothing, because the registry proof
and a report said two different things on 2026-09-21 and the cause was a second
query.

**What does not count:** a DID referring itself, a mutual pair (A says B, B says
A), a referred DID that is not in `agents`, and a referred DID that is revoked.
The last one keeps the figure consistent with every other published figure: the
registry proof drops revoked agents everywhere, so counting one as brought would
be a number the proof contradicts. The registration path cannot produce a mutual
pair — it refuses a referrer that does not exist yet, so the second half of a
ring can never be written — but the rule does not rely on that, because a row
can also arrive from a backfill.

**At most one credential a day per referrer.** The numbers change when a new
agent names the referrer (immediately, on the registration) and when one of the
referred agents makes its first independent call (the next pass of
scripts/referral_refresh.py). An agent that brings five in an afternoon gets one
credential, not five, and the rest of the change is carried by the next pass.

Queryable per registration (ops Z2): one row per referred agent in
`agent_referrals`, keyed by the referred DID, so a registrations-per-day query
joins it directly::

    SELECT a.created_at::date AS tag, count(*) AS registriert,
           count(f.referrer_did) AS davon_empfohlen
      FROM agents a LEFT JOIN agent_referrals f ON f.did = a.did
     WHERE a.created_at >= '2026-10-01' GROUP BY 1 ORDER BY 1;
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Iterable, Optional

from app import track_record_score as _trs

logger = logging.getLogger(__name__)

#: The credential type. One per referrer, re-issued when a number changes.
CREDENTIAL_TYPE = "ReferralCredential"

#: Refusal reasons for a named referrer, in the order they are checked.
REFUSE_OWN = "referrer_did is your own DID"
REFUSE_UNKNOWN = "referrer_did is not registered here"
REFUSE_REVOKED = "referrer_did is revoked"

#: What the rule drops, published beside the figure so a reader does not have to
#: guess which referrals were counted.
COUNTING_RULE = ("one row per referred agent; a self-referral, a mutual pair, an "
                 "unknown DID and a revoked DID do not count")

#: When referrals started being recorded: the deploy of #807 (fc856b0) that
#: created agent_referrals, `OK` at this second in ~/logs/deploy.log. Before it
#: no registration could leave a row, so a zero over a window that starts
#: earlier is partly a zero nobody could have measured (ops KM1).
CAPTURE_SINCE = datetime.datetime(2026, 10, 16, 18, 49, 3,
                                  tzinfo=datetime.timezone.utc)


def referrer_did_shape(v: Optional[str]) -> Optional[str]:
    """The request-model check on `referrer_did`, shared by both registration
    paths so they cannot drift apart.

    Shape only. Whether we issued it, and whether it is still valid, is a
    question for the register and is asked there — a well-formed DID we never
    issued is not a malformed request.
    """
    if v is None:
        return v
    v = v.strip()
    if not v:
        return None
    if not v.startswith("did:"):
        raise ValueError("referrer_did must be a DID, e.g. did:moltrust:1234567890abcdef")
    return v


# --- The rule ----------------------------------------------------------------

def pair_counts(fact: dict) -> bool:
    """Does this one referral count?

    `fact` carries what the database was asked and nothing derived: `did` (the
    referred agent), `self`, `mutual`, `unknown`, `revoked`, `activated`.
    """
    if fact.get("self") or fact.get("mutual"):
        return False
    if fact.get("unknown") or fact.get("revoked"):
        return False
    return True


def facts_of(row: dict) -> list:
    """The referral facts the counting query handed over for one agent row.

    `referred` is json in app/sql/registry_export.sql. The rows reach this
    function through json.loads of the psql output, so a list arrives; a driver
    that leaves json alone hands over a str, and a shape assumption about a
    column belongs next to a look at the column.
    """
    raw = row.get("referred")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    return raw if isinstance(raw, list) else []


def counts(facts: Optional[Iterable[dict]]) -> dict:
    """`brought` and `activated` for one referrer, over the facts of its rows.

    An empty list is zero, and zero is a figure rather than a gap: an agent that
    brought nobody has brought nobody.
    """
    brought = activated = 0
    for fact in facts or []:
        if not pair_counts(fact):
            continue
        brought += 1
        activated += 1 if fact.get("activated") else 0
    return {"brought": brought, "activated": activated}


def refusal(referrer_did: Optional[str], own_did: str,
            exists: bool, revoked: bool) -> Optional[str]:
    """Why a named referrer is refused, or None when it is taken.

    Own DID first: an agent that names itself has made a mistake worth naming,
    and answering "not registered here" about its own fresh DID would send it
    looking in the wrong place.
    """
    if not referrer_did:
        return None
    if referrer_did == own_did:
        return REFUSE_OWN
    if not exists:
        return REFUSE_UNKNOWN
    if revoked:
        return REFUSE_REVOKED
    return None


# --- The credential ----------------------------------------------------------

def claims(figures: dict, as_of: datetime.datetime) -> dict:
    """The credentialSubject claims of a ReferralCredential."""
    return {
        "referrals": {
            "brought": figures["brought"],
            "activated": figures["activated"],
            "as_of": _as_utc(as_of).replace(
                microsecond=0, tzinfo=None).isoformat() + "Z",
            "counting_rule": COUNTING_RULE,
        },
    }


def figures_of(raw_vc: Any) -> Optional[dict]:
    """The two numbers a stored ReferralCredential states, or None."""
    if isinstance(raw_vc, (bytes, bytearray)):
        raw_vc = raw_vc.decode("utf-8", "replace")
    if isinstance(raw_vc, str):
        try:
            raw_vc = json.loads(raw_vc)
        except Exception:
            return None
    if not isinstance(raw_vc, dict):
        return None
    block = (raw_vc.get("credentialSubject") or {}).get("referrals")
    if not isinstance(block, dict):
        return None
    try:
        return {"brought": int(block["brought"]), "activated": int(block["activated"])}
    except (KeyError, TypeError, ValueError):
        return None


def should_issue(figures: dict, last: Optional[dict],
                 now: datetime.datetime) -> bool:
    """Is a new credential due?

    Three nos, and each is a case that would otherwise mint a document for
    nothing: nobody brought, nothing changed, and one already issued today.
    """
    if figures["brought"] <= 0:
        return False
    if last is None:
        return True
    if (last.get("figures") or {}) == {"brought": figures["brought"],
                                       "activated": figures["activated"]}:
        return False
    issued_at = last.get("issued_at")
    if issued_at is not None and _utc_date(issued_at) == _utc_date(now):
        return False
    return True


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    """The same instant in UTC. A naive value is read as UTC, not as local
    time: everything that writes a time here writes utcnow(), and reading it as
    Zurich would move a late-evening credential into the next day."""
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


def _utc_date(value: datetime.datetime) -> datetime.date:
    """The UTC calendar day of an instant."""
    return _as_utc(value).date()


# --- The database ------------------------------------------------------------

#: One row per referral of one referrer, with the facts the rule asks for. The
#: activation test is the registry's own — a call to an endpoint no bounty task
#: text prescribed (app/sql/registry_export.sql, read through
#: app.track_record_score so the list stays in one place).
FACTS_SQL = """
SELECT f.did,
       (f.did = f.referrer_did)                        AS "self",
       EXISTS (SELECT 1 FROM agent_referrals b
                WHERE b.did = f.referrer_did AND b.referrer_did = f.did) AS mutual,
       (a.did IS NULL)                                 AS unknown,
       (a.revoked_at IS NOT NULL)                      AS revoked,
       EXISTS (SELECT 1 FROM request_log r
                WHERE r.agent_did = f.did
                  AND NOT (r.endpoint LIKE ANY ($2::text[]))) AS activated
  FROM agent_referrals f
  LEFT JOIN agents a ON a.did = f.did
 WHERE f.referrer_did = $1
"""


async def known_referrer(conn, referrer_did: str) -> tuple[bool, bool]:
    """Is this DID registered here, and is it revoked?"""
    row = await conn.fetchrow(
        "SELECT (revoked_at IS NOT NULL) AS revoked FROM agents WHERE did = $1",
        referrer_did,
    )
    return (row is not None, bool(row["revoked"]) if row is not None else False)


async def record(conn, did: str, referrer_did: str) -> bool:
    """Store the referral of a fresh registration. Written once, never updated."""
    status = await conn.execute(
        "INSERT INTO agent_referrals (did, referrer_did) VALUES ($1, $2) "
        "ON CONFLICT (did) DO NOTHING",
        did, referrer_did,
    )
    return status.endswith(" 1")


async def facts_for(conn, referrer_did: str) -> list[dict]:
    rows = await conn.fetch(FACTS_SQL, referrer_did,
                            _trs.scripted_endpoint_patterns())
    return [dict(r) for r in rows]


async def counts_for(conn, referrer_did: str) -> dict:
    """The two numbers for one referrer, by the rule above."""
    return counts(await facts_for(conn, referrer_did))


async def last_credential(conn, referrer_did: str) -> Optional[dict]:
    row = await conn.fetchrow(
        "SELECT id, issued_at, raw_vc FROM credentials "
        "WHERE subject_did = $1 AND credential_type = $2 "
        "AND NOT coalesce(revoked, false) "
        "ORDER BY issued_at DESC LIMIT 1",
        referrer_did, CREDENTIAL_TYPE,
    )
    if row is None:
        return None
    return {"id": row["id"], "issued_at": row["issued_at"],
            "figures": figures_of(row["raw_vc"])}


async def refresh(conn, referrer_did: str,
                  now: Optional[datetime.datetime] = None) -> dict:
    """Issue a ReferralCredential for this referrer if one is due.

    Returns what it decided, so a caller logs a decision rather than a guess.
    The anchor is not written here: scripts/anchor_iprs.sh batches every
    unanchored credential into one Base transaction every two hours, and one
    transaction per credential would be a gas payment for a claim the root
    already carries.
    """
    from app.credentials import issue_credential, vc_valid_from, vc_valid_until
    from app.crypto.proof_utils import get_primary_proof_value

    now = now or datetime.datetime.now(datetime.timezone.utc)
    figures = await counts_for(conn, referrer_did)
    last = await last_credential(conn, referrer_did)
    if not should_issue(figures, last, now):
        return {"issued": False, **figures}

    vc = issue_credential(referrer_did, CREDENTIAL_TYPE, claims(figures, now))
    cred_id = await conn.fetchval(
        "INSERT INTO credentials (subject_did, credential_type, issuer, issued_at, "
        "expires_at, proof_value, raw_vc) VALUES ($1, $2, $3, $4, $5, $6, $7) "
        "RETURNING id",
        referrer_did, CREDENTIAL_TYPE, vc["issuer"],
        datetime.datetime.fromisoformat(vc_valid_from(vc).replace("Z", "")),
        datetime.datetime.fromisoformat(vc_valid_until(vc).replace("Z", "")),
        get_primary_proof_value(vc), json.dumps(vc),
    )
    from app import vc_archive
    await vc_archive.archive(conn, vc, "referral")
    return {"issued": True, "credential_id": cred_id, **figures}


async def refresh_quietly(pool, referrer_did: str) -> None:
    """refresh() for a caller that must not fail because of it.

    A registration is not undone by a credential that could not be issued, and
    the numbers are recomputed from the rows by the next pass either way.
    """
    try:
        async with pool.acquire() as conn:
            result = await refresh(conn, referrer_did)
        logger.info("referral credential for %s: %s", referrer_did, result)
    except Exception as e:
        logger.error("referral credential for %s failed: %s", referrer_did,
                     type(e).__name__)
