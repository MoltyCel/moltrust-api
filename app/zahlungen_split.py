"""Payments for the daily stats, split into external and our own.

On 2026-11-21 the CB-BAZAAR job paid three of our x402 endpoints from the test
wallet 0xd8f5 to the productive address 0xD63f (0.01 + 0.02 + 0.02 USDC). The
x402 settle path books every settled payment in payment_events, whoever paid,
so the 19:00 digest would have shown "Payments 3 | USDC 0.05" as if three
customers had paid. Lars, same day: payments from our own wallets are listed
as internal/test, never as paid calls or paying customers.

The own wallets come from app/own_addresses.py, the one list the USDC pollers
already use (ops C4). This module only applies it to the digest.

    python -m app.zahlungen_split --zeile
        ext_n|ext_usdc|ext_total|int_n|int_usdc|int_total for daily_stats.sh

    python -m app.zahlungen_split --pruefe [--datei ~/selftest/daily-stats-last.txt]
        Exit 0 clean, 1 finding, 2 not checkable. Checks the delivered digest:
        the internal line is there, and the external count is not larger than
        the payments from foreign wallets in that window.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta

from app.own_addresses import OWN_ADDRESSES
from app.payment_address import payment_address

DB_DSN = os.environ.get("DATABASE_URL", "dbname=moltstack user=moltstack")
VORGABE = os.path.expanduser("~/selftest/daily-stats-last.txt")
FENSTER = timedelta(hours=12)
# The digest stamps the minute it started; the queries run right after. Two
# minutes either side keeps a payment at the window edge from making the check
# red. The check is one-sided, so a wider window can only make it more lenient.
TOLERANZ = timedelta(minutes=2)

# from_address may be NULL (rows from before the poller stored it). NULL is
# foreign: unknown money is not ours, and the old totals stay where they were.
_EIGEN = "lower(coalesce(from_address, '')) = ANY(%(eigen)s)"

# Written out, not built with an f-string: the predicate is _EIGEN verbatim
# (tests/test_zahlungen_split.py holds them together).
SQL_ZEILE = """
SELECT
  count(*)                     FILTER (WHERE NOT lower(coalesce(from_address, '')) = ANY(%(eigen)s) AND received_at > now() - interval '12 hours'),
  coalesce(sum(amount_usdc)    FILTER (WHERE NOT lower(coalesce(from_address, '')) = ANY(%(eigen)s) AND received_at > now() - interval '12 hours'), 0)::numeric(18,2),
  coalesce(sum(amount_usdc)    FILTER (WHERE NOT lower(coalesce(from_address, '')) = ANY(%(eigen)s)), 0)::numeric(18,2),
  count(*)                     FILTER (WHERE lower(coalesce(from_address, '')) = ANY(%(eigen)s) AND received_at > now() - interval '12 hours'),
  coalesce(sum(amount_usdc)    FILTER (WHERE lower(coalesce(from_address, '')) = ANY(%(eigen)s) AND received_at > now() - interval '12 hours'), 0)::numeric(18,2),
  coalesce(sum(amount_usdc)    FILTER (WHERE lower(coalesce(from_address, '')) = ANY(%(eigen)s)), 0)::numeric(18,2)
FROM payment_events
"""

SQL_FREMD_IM_FENSTER = """
SELECT count(*) FROM payment_events
 WHERE NOT lower(coalesce(from_address, '')) = ANY(%(eigen)s) AND received_at > %(von)s AND received_at <= %(bis)s
"""


def eigene_adressen() -> list[str]:
    """Our wallets, lower case, including whatever BASE_ADDR names now."""
    adressen = set(OWN_ADDRESSES)
    try:
        adressen.add(payment_address().lower())
    except Exception:
        pass  # the list above already holds the productive address
    return sorted(a.lower() for a in adressen if a)


def _verbinden():
    import psycopg2
    return psycopg2.connect(DB_DSN)


def zeile(conn=None) -> str:
    eigen = eigene_adressen()
    if not eigen:
        raise RuntimeError("no own addresses, refusing to count our money as paid")
    conn = conn or _verbinden()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SQL_ZEILE, {"eigen": eigen})
            werte = cur.fetchone()
    finally:
        conn.close()
    return "|".join(str(w) for w in werte)


_RE_EXT = re.compile(r"^Payments \(12h, external\):\s*\n\s*Count: (\d+) \|", re.M)
_RE_INT = re.compile(r"^\s*Intern/Test \(12h, own wallets\): (\d+) Tx", re.M)
_RE_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\s*$", re.M)


def pruefe(text: str, fremd_im_fenster) -> tuple[int, str]:
    """(exit code, finding). `fremd_im_fenster(von, bis)` counts foreign rows."""
    m_ext, m_int = _RE_EXT.search(text), _RE_INT.search(text)
    if not m_int or not m_ext:
        return 1, "digest has no external/internal payment split"
    stempel = _RE_TS.findall(text)
    if not stempel:
        return 2, "digest has no timestamp"
    ts = datetime.strptime(stempel[-1], "%Y-%m-%d %H:%M")
    gemeldet = int(m_ext.group(1))
    fremd = fremd_im_fenster(ts - FENSTER - TOLERANZ, ts + TOLERANZ)
    if gemeldet > fremd:
        return 1, (f"digest {stempel[-1]} counts {gemeldet} external payments, "
                   f"foreign wallets paid {fremd} in that window")
    return 0, f"ok: {gemeldet} external <= {fremd} foreign, internal {m_int.group(1)}"


def _fremd_im_fenster_db(von, bis) -> int:
    conn = _verbinden()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SQL_FREMD_IM_FENSTER,
                        {"eigen": eigene_adressen(), "von": von, "bis": bis})
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--zeile", action="store_true")
    g.add_argument("--pruefe", action="store_true")
    p.add_argument("--datei", default=VORGABE)
    args = p.parse_args(argv)
    if args.zeile:
        print(zeile())
        return 0
    try:
        with open(args.datei) as fh:
            text = fh.read()
    except OSError as e:
        print(f"not checkable: {type(e).__name__}")
        return 2
    rc, befund = pruefe(text, _fremd_im_fenster_db)
    print(befund)
    return rc


if __name__ == "__main__":
    sys.exit(main())
