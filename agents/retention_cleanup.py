#!/usr/bin/env python3
"""DSGVO Retention: delete request_log entries older than 30 days. Daily cron.

Before pruning, every external IP MIN(ts) is frozen into the known_callers
ledger (ON CONFLICT DO NOTHING) so retention can never strip an IP history
and let it re-float as "truly new" in traffic_monitor.
"""
import asyncio, logging, os

import sys

# Der Pfad zum Repo, aus der Datei selbst. Python legt beim Skriptaufruf
# das Verzeichnis des Skripts auf sys.path, nicht das
# Arbeitsverzeichnis — ohne diese Zeile braucht der Aufruf ein
# PYTHONPATH aus der Crontab, und eine Crontab, die den Suchpfad setzt,
# ist dieselbe unsichtbare Ueberstimmung wie POLL_RPC_URL am 09.10.2026.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify
from scripts import retention_status

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("retention")

# Contact submissions hold a name, an e-mail address and free text. Twelve
# months is long enough for a follow-up and for any dispute about whether a
# message arrived, and short enough not to become a standing liability.
CONTACT_RETENTION_MONTHS = 12



def _record(**kw):
    """Das Ergebnis fuer die Sammelmeldung festhalten (ops KO1).

    Ein Schreibfehler hier darf den Lauf nicht kippen: die Loeschung ist dann
    schon geschehen. Die Sammelmeldung sagt in dem Fall "kein Lauf seit",
    und genau das soll sie dann auch sagen.
    """
    try:
        retention_status.record(**kw)
    except Exception as exc:  # noqa: BLE001
        log.error("Retention status not written (%s)", type(exc).__name__)


async def main():
    import asyncpg
    conn = await asyncpg.connect(user="moltstack", database="moltstack")
    try:
        # Freeze every external IP true first_seen into the known_callers ledger
        # BEFORE pruning, so retention can never strip an IP history and let it
        # re-float as "truly new". ON CONFLICT DO NOTHING keeps curated/existing
        # rows. Trusted-prefix list mirrors traffic_monitor.TRUSTED_PREFIXES.
        backfill = await conn.execute("""
            INSERT INTO known_callers (ip, first_seen, label, category)
            SELECT ip, MIN(ts), NULL, 'auto'
            FROM request_log
            WHERE ip IS NOT NULL
              AND ip NOT LIKE '127.%'     AND ip <> '::1'
              AND ip NOT LIKE '10.%'      AND ip NOT LIKE '172.16.%'
              AND ip NOT LIKE '192.168.%' AND ip NOT LIKE '88.99.%'
              AND ip NOT LIKE '116.202.%' AND ip NOT LIKE '46.225.175.%'
            GROUP BY ip
            ON CONFLICT (ip) DO NOTHING
        """)
        backfilled = int(backfill.split()[-1]) if backfill else 0
        log.info("Ledger backfill: %d new IP(s) frozen before pruning", backfilled)

        # Aggregate before deleting. Scheduling the rollup as its own cron entry
        # would make the ordering a matter of clock luck; calling it here makes
        # it a property of the code. If the rollup fails, nothing is pruned —
        # losing a day of detail is recoverable, losing it permanently is not.
        try:
            from app.usage import rollup_days, prune_rollups
            days = await rollup_days(conn, days_back=31)
            log.info("Rollup: %d day(s) written to usage_daily", days)
        except Exception as exc:
            log.error("Rollup FAILED (%s) — skipping the prune", type(exc).__name__)
            _record(ok=False, error=f"usage rollup failed: {type(exc).__name__}")
            # Der Abbruch ist ein Alarm und geht sofort raus, ueber notify:
            # Gate, Drosselung und Sendeprotokoll wie jede andere Meldung.
            notify.send_telegram(
                "Retention aborted: usage rollup failed "
                f"({type(exc).__name__}). request_log was NOT pruned.",
                channel=notify.ALERTS,
            )
            return

        result = await conn.execute("DELETE FROM request_log WHERE ts < NOW() - INTERVAL '30 days'")
        deleted = int(result.split()[-1]) if result else 0
        log.info("Deleted %d old request_log entries", deleted)

        pruned = await prune_rollups(conn)
        if pruned:
            log.info("Rollup retention: %d row(s) older than 24 months deleted", pruned)

        # Contact submissions are personal data with no business reason to be
        # kept indefinitely: a name, an e-mail address and free text somebody
        # typed into a form. Twelve months covers a follow-up conversation and
        # any dispute about whether a message arrived, which is what the table
        # is for.
        contact_deleted = 0
        try:
            contact_result = await conn.execute(
                "DELETE FROM contact_inbox "
                "WHERE received_at < NOW() - ($1::int * INTERVAL '1 month')",
                CONTACT_RETENTION_MONTHS,
            )
            contact_deleted = int(contact_result.split()[-1]) if contact_result else 0
            log.info(
                "Contact retention: %d row(s) older than %d months deleted",
                contact_deleted, CONTACT_RETENTION_MONTHS,
            )
        except Exception as exc:
            # request_log has already been pruned at this point. Letting a
            # missing or unreachable contact_inbox abort the run would report
            # the whole job as failed when the part that matters succeeded.
            log.error("Contact retention skipped (%s)", type(exc).__name__)

        # Kein eigener Telegram-Versand mehr (ops KO1, 19.10.2026): die Zahlen
        # sind Routine und stehen als eine Zeile in der Sammelmeldung
        # (scripts/selftest.py --digest, ueber scripts/retention_status.py).
        _record(ok=True, request_log=deleted, contact_inbox=contact_deleted,
                days=days)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
