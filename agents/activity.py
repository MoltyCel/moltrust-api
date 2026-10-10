"""Shared agent-activity helper.

Bumps agents.last_active_at/last_seen after a real action (Moltbook/X post).
These agents post over HTTP and never traverse the FastAPI app, so the app's
own update_last_active() never fires for them -> they show up as "ghosts".
"""
import os
import logging

log = logging.getLogger("activity")

# The Ambassador's DID, in one place because a count that is keyed on a literal
# string reads zero the day the string changes -- and a zero that measures a
# rename looks exactly like a zero that measures a dead agent.
#
# `did:moltrust:ambassador0001` predates the strict convention. It fails
# `DID_PATTERN` in app/main.py and the method spec's 2.2
# (`did:moltrust:[0-9a-f]{16}`); only `validate_did_lookup` resolves it, and
# only because it was grandfathered. A re-issue would therefore produce a
# conformant 16-hex DID -- a different string -- and `mark_active` would update
# zero rows from then on, logging one WARNING per run into a file nobody reads.
# That is the whole failure mode: the Ambassador keeps posting and starts
# reading as inactive.
#
# So the DID comes from the environment, and the literal is only the fallback.
# Set AMBASSADOR_DID in ~/.moltrust_secrets on the day it is re-issued and
# every counter that goes through here follows, without a code change.
#
# Not covered here, and known: `agent/ambassador.py:17` carries the literal as
# its own constant. That is the Mac branch, disabled 2026-06-20
# (ADR-ambassador-did-identity, "Offen / nicht geprueft"), so it counts
# nothing -- but it is a second copy of the same string and will mislead
# whoever reads it next.
_LEGACY_AMBASSADOR_DID = "did:moltrust:ambassador0001"


def ambassador_did() -> str:
    """The Ambassador's current DID. Environment first, legacy literal second."""
    return (os.environ.get("AMBASSADOR_DID") or "").strip() or _LEGACY_AMBASSADOR_DID


def mark_active(did: str) -> bool:
    """Best-effort: set last_seen/last_active_at = now() for `did`.

    Returns True iff exactly one row was updated. Logs a WARNING on 0 rows
    (unknown/wrong DID) so a silent no-op can't hide. Never raises — it must
    not break the calling agent on a DB hiccup.
    """
    if not did:
        log.warning("mark_active: empty DID — skipped")
        return False
    pw = os.environ.get("MOLTSTACK_DB_PW")
    if not pw:
        log.warning("mark_active: MOLTSTACK_DB_PW not in env — skipped for %s", did)
        return False
    try:
        import psycopg2
        conn = psycopg2.connect(host="localhost", dbname="moltstack",
                                user="moltstack", password=pw, connect_timeout=5)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE agents SET last_seen = now(), last_active_at = now() WHERE did = %s",
                    (did,),
                )
                n = cur.rowcount
        finally:
            conn.close()
        if n == 0:
            log.warning("mark_active: no agents row for %s — last_active_at NOT updated", did)
            return False
        log.info("mark_active: last_active_at bumped for %s", did)
        return True
    except Exception as e:
        log.warning("mark_active failed for %s: %s", did, e)
        return False
