#!/usr/bin/env python3
"""DSGVO Retention: delete request_log entries older than 30 days. Daily cron.

Before pruning, every external IP MIN(ts) is frozen into the known_callers
ledger (ON CONFLICT DO NOTHING) so retention can never strip an IP history
and let it re-float as "truly new" in traffic_monitor.
"""
import asyncio, logging, os
from urllib.request import Request, urlopen
import json

from app import notify

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("retention")

# Contact submissions hold a name, an e-mail address and free text. Twelve
# months is long enough for a follow-up and for any dispute about whether a
# message arrived, and short enough not to become a standing liability.
CONTACT_RETENTION_MONTHS = 12

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")


def send_telegram(msg, *, channel: str = notify.WORKLOG):
    if not notify.telegram_allowed("retention_cleanup.send_telegram", logger=log):
        return
    if not TG_TOKEN or not notify.chat_id_for(channel):
        return
    try:
        data = json.dumps({"chat_id": notify.chat_id_for(channel), "text": msg}).encode()
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        if not url.startswith(("http://", "https://")):
            return
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        urlopen(req, timeout=10)  # noqa: S310 — scheme validated above  # nosec B310 - host is the literal Telegram API, only the bot token comes from env
    except Exception:
        pass


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
            send_telegram(
                "Retention aborted: usage rollup failed "
                f"({type(exc).__name__}). request_log was NOT pruned."
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

        if deleted > 0 or contact_deleted > 0:
            send_telegram(
                f"DSGVO Retention: {deleted} request_log entries deleted (>30 days), "
                f"{contact_deleted} contact_inbox entries deleted "
                f"(>{CONTACT_RETENTION_MONTHS} months); {days} day(s) rolled up first"
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
