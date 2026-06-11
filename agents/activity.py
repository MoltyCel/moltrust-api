"""Shared agent-activity helper.

Bumps agents.last_active_at/last_seen after a real action (Moltbook/X post).
These agents post over HTTP and never traverse the FastAPI app, so the app's
own update_last_active() never fires for them -> they show up as "ghosts".
"""
import os
import logging

log = logging.getLogger("activity")


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
