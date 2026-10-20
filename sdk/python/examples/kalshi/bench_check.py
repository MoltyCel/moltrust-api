"""Latency of POST /enforce/check for a Kalshi order, as the guard sees it.

Sequential calls through `EnforceClient.check` with the demo mandate and one allow and one
forbid transaction in turn, paced below the endpoint's 60/minute limit. Prints n, p50, p95,
min, max and the verdict counts. No Kalshi call is made.

    python bench_check.py --did did:moltrust:a2f9415d27094890 -n 200
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from moltrust_enforce import EnforceClient
from moltrust_enforce.adapters.kalshi import (
    KalshiOrderGuard, SeriesInfo, ed25519_attester,
)

from demo_run import make_profile, percentile

BTC = SeriesInfo(ticker="KXBTC15M", category="Crypto", categories=("Crypto",),
                 frequency="fifteen_min")


class NoKalshi:
    def create_order(self, order):  # pragma: no cover - the bench never sends
        raise AssertionError("bench sends no order")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="https://api.moltrust.ch")
    ap.add_argument("--did", required=True)
    ap.add_argument("-n", type=int, default=200)
    ap.add_argument("--interval", type=float, default=1.1, help="seconds between calls")
    args = ap.parse_args(argv)

    now = dt.datetime.now(dt.timezone.utc)
    key = Ed25519PrivateKey.generate()
    profile = make_profile(key, Ed25519PrivateKey.generate(), now)
    client = EnforceClient(args.url, did=args.did, timeout=10)
    guard = KalshiOrderGuard(client, profile, NoKalshi(), series_for=lambda _t: BTC,
                             attest=ed25519_attester("did:example:kg-enf-operator", key))
    orders = [{"ticker": "KXBTC15M-26OCT201215-15", "side": "bid", "count": "1",
               "price": "0.1000"},
              {"ticker": "KXBTC15M-26OCT201215-15", "side": "ask", "count": "1",
               "price": "0.1000"}]
    lat, verdicts, local = [], {}, 0
    for i in range(args.n):
        out = guard.decide(orders[i % 2])
        lat.append(out.check_ms)
        v = out.verdict.verdict if out.verdict.from_server else "LOCAL_" + out.verdict.verdict
        verdicts[v] = verdicts.get(v, 0) + 1
        local += not out.verdict.from_server
        time.sleep(args.interval)
    client.close()
    print(json.dumps({
        "date": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "url": args.url + "/enforce/check",
        "n": len(lat), "p50_ms": round(percentile(lat, 50), 1),
        "p95_ms": round(percentile(lat, 95), 1), "min_ms": round(min(lat), 1),
        "max_ms": round(max(lat), 1), "verdicts": verdicts,
        "not_from_server": local}, indent=2))
    return 0 if local == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
