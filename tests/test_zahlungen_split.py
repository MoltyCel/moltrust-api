"""Payments from our own wallets are internal/test in the daily stats.

21.11.2026, CB-BAZAAR: three x402 payments from the test wallet 0xd8f5 to
0xD63f would have shown up in the 19:00 digest as three paying customers.
"""
from __future__ import annotations

import pathlib
from datetime import datetime

from app import zahlungen_split as z
from app.own_addresses import OWN_ADDRESSES

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEST_WALLET = "0xd8f5bb747f7459bf3e1cc1ad041e2ca57b946c38"
NEW = "0xd63f988201a0bd184deab7d4e97b1717b25a5800"

REPORT = """Payments (12h, external):
  Count: {ext} | USDC: 0.00
  Total USDC all-time: 0.87
  Intern/Test (12h, own wallets): 3 Tx | USDC: 0.05 | all-time: 0.05

MCP Traffic (12h):
  Total: 5

2026-11-21 19:00 UTC
"""


def test_own_wallets_include_test_and_productive():
    eigen = z.eigene_adressen()
    assert TEST_WALLET in eigen and NEW in eigen
    assert set(a.lower() for a in OWN_ADDRESSES) <= set(eigen)
    assert eigen == sorted(eigen) and all(a == a.lower() for a in eigen)


def test_sql_splits_on_the_same_predicate():
    # external columns negate the own-wallet predicate, internal ones use it
    assert z.SQL_ZEILE.count("NOT " + z._EIGEN) == 3
    assert z.SQL_ZEILE.count(z._EIGEN) == 6
    assert "coalesce(from_address, '')" in z._EIGEN  # NULL counts as foreign


class _Cur:
    def __init__(self, sink):
        self.sink = sink
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, sql, params):
        self.sink.append((sql, params))
    def fetchone(self):
        return (0, "0.00", "0.87", 3, "0.05", "0.05")


class _Conn:
    def __init__(self):
        self.calls, self.closed = [], False
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def cursor(self):
        return _Cur(self.calls)
    def close(self):
        self.closed = True


def test_zeile_passes_own_wallets_and_formats_six_fields():
    conn = _Conn()
    out = z.zeile(conn)
    assert out == "0|0.00|0.87|3|0.05|0.05"
    assert conn.calls[0][1]["eigen"] == z.eigene_adressen()
    assert conn.closed


def test_pruefe_clean_when_external_not_inflated():
    seen = {}
    def fremd(von, bis):
        seen["von"], seen["bis"] = von, bis
        return 0
    rc, msg = z.pruefe(REPORT.format(ext=0), fremd)
    assert rc == 0, msg
    assert seen["bis"] - seen["von"] == z.FENSTER + 2 * z.TOLERANZ
    assert seen["bis"] == datetime(2026, 11, 21, 19, 0) + z.TOLERANZ


def test_pruefe_red_when_own_payments_counted_external():
    rc, msg = z.pruefe(REPORT.format(ext=3), lambda von, bis: 0)
    assert rc == 1 and "3 external" in msg


def test_pruefe_red_on_old_unsplit_format():
    old = "Payments (12h):\n  Count: 3 | USDC: 0.05\n\n2026-11-21 19:00 UTC\n"
    rc, _ = z.pruefe(old, lambda von, bis: 99)
    assert rc == 1


def test_daily_stats_reads_the_split_and_shows_internal_line():
    sh = (ROOT / "scripts/daily_stats.sh").read_text()
    assert "app.zahlungen_split --zeile" in sh
    assert "FROM payment_events" not in sh  # no unfiltered count left
    assert "Payments (12h, external):" in sh
    assert "Intern/Test (12h, own wallets): $PAYMENTS_INT_12H Tx" in sh


def test_invariant_runs_the_check():
    inv = (ROOT / "docs/invariants/e-zahlungen-eigene-wallets-intern.yaml").read_text()
    assert "app.zahlungen_split --pruefe" in inv
