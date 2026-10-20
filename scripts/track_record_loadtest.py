#!/usr/bin/env python3
"""A hundred issuances at once, against the measurement path that used to break.

There is no staging environment — "staging" in this repository is the blog
deploy directory, not an app tier — so this exercises the code under change
directly instead of a copy of the service: `measure_async` with a stand-in for
the cache, the real Base node, and the real explorer. No database is touched
and no credential is issued. What it does not cover is the HTTP and Postgres
layers around the call; what it does cover is the part that failed.

The failure it exists to prevent: on 2026-09-25 a sweep over 78 wallets came
back with 68 unreadable because Blockscout throttled. Issuance depended on that
explorer, so a hundred agents in the same hour would have been told "could not
measure" instead of yes or no.

    python3 scripts/track_record_loadtest.py --wallets wallets.txt
    python3 scripts/track_record_loadtest.py --wallets wallets.txt --no-explorer
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time


class FakeConn:
    """The cache, without a database. Models the row that would be there."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.reads = 0
        self.writes = 0

    async def fetchrow(self, _sql, wallet):
        self.reads += 1
        return self.rows.get(wallet)

    async def execute(self, _sql, wallet, chain, block, ts, source):
        self.writes += 1
        self.rows[wallet] = {"first_block": block, "first_ts": None, "source": source}


async def one(conn, wallet, results):
    import app.track_record as tr
    started = time.perf_counter()
    try:
        m = await tr.measure_async(conn, wallet)
        results.append({"wallet": wallet, "ok": True,
                        "secs": time.perf_counter() - started,
                        "nonce": m["nonce"], "age": m["wallet_age_days"],
                        "enriched": m["enriched"], "age_source": m["age_source"]})
    except Exception as exc:  # noqa: BLE001 — the point is to count these
        results.append({"wallet": wallet, "ok": False,
                        "secs": time.perf_counter() - started,
                        "error": f"{type(exc).__name__}: {exc}"})


async def wave(conn, wallets, label):
    results: list[dict] = []
    started = time.perf_counter()
    await asyncio.gather(*(one(conn, w, results) for w in wallets))
    elapsed = time.perf_counter() - started

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    secs = sorted(r["secs"] for r in results)
    print(f"\n--- {label} ---")
    print(f"  Aufrufe            {len(results)} gleichzeitig")
    print(f"  Wanduhr            {elapsed:.1f} s")
    print(f"  erfolgreich        {len(ok)}")
    print(f"  fehlgeschlagen     {len(bad)}")
    if secs:
        print(f"  Latenz p50/p95/max {statistics.median(secs):.2f} / "
              f"{secs[int(len(secs)*0.95)-1]:.2f} / {max(secs):.2f} s")
    if ok:
        enriched = sum(1 for r in ok if r["enriched"])
        print(f"  mit Blockscout     {enriched}")
        print(f"  nur Node           {len(ok)-enriched}")
        from collections import Counter
        for src, n in Counter(r["age_source"] for r in ok).most_common():
            print(f"    age_source {src}: {n}")
    for r in bad[:5]:
        print(f"    FEHLER {r['wallet']}: {r['error']}")
    print(f"  Cache: {conn.reads} Lesungen, {conn.writes} Schreibungen")
    return len(bad)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wallets", required=True, help="One address per line.")
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--no-explorer", action="store_true",
                    help="Force the enrichment to fail, as a throttled explorer does.")
    args = ap.parse_args()

    wallets = [l.strip() for l in open(args.wallets) if l.strip().startswith("0x")]
    if not wallets:
        print("Keine Adressen in der Datei.")
        return 1
    # Repeat the list up to --count so the second half is cache hits, which is
    # what production looks like after the first wave.
    load = (wallets * ((args.count // len(wallets)) + 1))[:args.count]

    if args.no_explorer:
        import app.cold_start as cs

        def throttled(_w):
            raise RuntimeError("HTTP 429 Too Many Requests")

        cs.fetch_blockscout_wallet = throttled
        print("Blockscout: künstlich gedrosselt (429 auf jeden Aufruf)")

    conn = FakeConn()
    failed = await wave(conn, load, f"{args.count} gleichzeitig, kalter Cache")
    failed += await wave(conn, load, f"{args.count} gleichzeitig, warmer Cache")

    print("\nErgebnis:", "alle Aufrufe haben entschieden" if failed == 0
          else f"{failed} Aufrufe ohne Entscheidung")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
