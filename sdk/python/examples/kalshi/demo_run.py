"""KG-ENF demo run: scripted orders on Kalshi 15-minute crypto markets, each checked first.

Every order goes through `KalshiOrderGuard`: `POST /enforce/check` decides, the order is sent
to the Kalshi DEMO only on allow (or an approved hold), and every decision writes a receipt.
At the end every receipt is recomputed offline with `verify_record` (V1-V4), the same check
`moltrust-verify` runs, and the latency of each check is summarised.

    KALSHI_DEMO_KEY_ID=...  KALSHI_DEMO_PRIVATE_KEY_PATH=~/kalshi-demo.pem \\
    MOLTRUST_API_KEY=...    python demo_run.py --out ./kg-enf-run

Exit codes: 0 every receipt verifies and no forbid was sent; 1 otherwise; 2 setup problem
(missing credentials, no open market). Never runs against a non-demo host: `KalshiClient`
refuses it, and this script does not offer the switch.

Plan (26 orders): 12 small allows across BTC and ETH ($1.00 of them on the second BTC market),
4 allows of $4.50 on that market that bring its $20 day budget to $19.00, then a fifth that
would reach $23.50 (forbid), 3 holds of
$6.00 (Telegram approval with --telegram, otherwise unsent after the timeout), and 6 more
forbids: 11 contracts, price 0.04, price 0.96, side ask, a series outside the mandate
(KXDOGE15M), and an order without a price. All orders are immediate-or-cancel limit orders,
so nothing rests on the book after the run.

--telegram is for the MolTrust server only: it sends through `app.notify` and reads the
button press from the `telegram_inbox` table that the webhook fills. Run it from a checkout
of moltrust-api so `app` is importable. Known limit: the reply radar claims every
callback_query when it runs and drops prefixes it does not know, so a press that lands in
the same second as a reply-radar run can be lost; the hold then times out and sends nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from moltrust_enforce import EnforceClient
from moltrust_enforce.adapters.kalshi import (
    ALLOW, FORBID, HOLD, KalshiClient, KalshiOrderGuard, KalshiProfile, TelegramApprover,
    ed25519_attester, trust_list_for,
)
from moltrust_enforce.verify import verify_record

ALLOWED_SERIES = ("KXBTC15M", "KXETH15M")
OUTSIDE_SERIES = "KXDOGE15M"
# Demo identities with per-run keys. did:example on purpose: these keys belong to no
# registered DID, and the mandate must not claim otherwise.
PRINCIPAL_DID = "did:example:kg-enf-operator"
APPROVER_DID = "did:example:kg-enf-risk-desk"


def percentile(values: List[float], q: float) -> float:
    """Nearest-rank percentile; q in (0, 100]."""
    s = sorted(values)
    k = max(1, -(-len(s) * q // 100))  # ceil(n*q/100), at least 1
    return s[int(k) - 1]


def make_profile(principal_key, approver_key, now: dt.datetime) -> KalshiProfile:
    return KalshiProfile(
        mandate_id=f"kg-enf-demo-{now.strftime('%Y%m%dT%H%M%SZ')}",
        principal={"did": PRINCIPAL_DID,
                   "public_key": principal_key.public_key().public_bytes_raw().hex()},
        ratification_authorities=({"did": APPROVER_DID, "role": "risk_desk",
                                   "public_key": approver_key.public_key()
                                   .public_bytes_raw().hex()},),
        series=ALLOWED_SERIES, categories=("Crypto",), sides=("bid",),
        max_contracts="10", min_price="0.05", max_price="0.95",
        max_market_day_exposure="20.00", hold_above="5.00",
        valid_from=(now - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        valid_until=(now + dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))


def open_markets(kalshi: KalshiClient, series: str, n: int) -> List[str]:
    """Up to n open market tickers of one series (Get Markets, status=open)."""
    r = kalshi.request("GET", "/markets", params={"series_ticker": series, "status": "open",
                                                  "limit": max(n, 5)})
    if r.status_code != 200:
        return []
    ms = r.json().get("markets") or []
    return [m["ticker"] for m in ms if isinstance(m, dict) and isinstance(m.get("ticker"), str)][:n]


def plan(btc: List[str], eth: List[str], doge: str) -> List[Dict[str, Any]]:
    def o(ticker, count="1", price="0.1000", side="bid", **kw):
        d = {"ticker": ticker, "side": side, "count": count, "price": price,
             "time_in_force": "immediate_or_cancel",
             "self_trade_prevention_type": "taker_at_cross"}
        d.update(kw)
        return d
    a, b = btc[0], (btc[1] if len(btc) > 1 else btc[0])
    e = eth[0]
    orders = []
    for i in range(12):                                   # 12 allows, $0.10 to $0.50
        orders.append(("allow", o([a, e, b][i % 3], count=str(1 + i % 5))))
    for _ in range(4):                                    # 4 x $4.50 on b: $19.00 with the $1 above
        orders.append(("allow", o(b, count="10", price="0.4500")))
    orders.append(("forbid", o(b, count="10", price="0.4500")))    # would be $23.50 > $20
    for _ in range(3):                                    # $6.00 > $5.00 -> hold
        orders.append(("hold", o(e, count="10", price="0.6000")))
    orders += [("forbid", o(a, count="11")),               # > 10 contracts
               ("forbid", o(a, price="0.0400")),           # below the band
               ("forbid", o(a, price="0.9600")),           # above the band
               ("forbid", o(a, side="ask")),               # side not in the mandate
               ("forbid", o(doge)),                        # series not in the mandate
               ("forbid", {k: v for k, v in o(a).items() if k != "price"})]  # no price
    return [dict(expect=x, order=y) for x, y in orders]


def server_telegram(timeout_s: float) -> Callable:
    """send/wait over app.notify and telegram_inbox. MolTrust server only."""
    from app import notify  # noqa: WPS433 - only importable inside a moltrust-api checkout
    import psycopg2

    def send(text, buttons):
        markup = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in buttons]]}
        ok = notify.send_telegram(text, channel=notify.WORKLOG, reply_markup=markup)
        if not ok:
            raise RuntimeError("telegram send failed")

    def wait(token, timeout):
        wanted = [f"kg|approve|{token}", f"kg|reject|{token}"]
        deadline = time.monotonic() + min(timeout, timeout_s)
        dsn = os.environ.get("DATABASE_URL", "dbname=moltstack user=moltstack")
        while time.monotonic() < deadline:
            conn = psycopg2.connect(dsn)
            try:
                with conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE telegram_inbox SET consumed_by = 'kg_enf', consumed_at = now() "
                        "WHERE consumed_by IS NULL "
                        "AND payload->'callback_query'->>'data' = ANY(%s) "
                        "RETURNING payload->'callback_query'->>'data'", (wanted,))
                    row = cur.fetchone()
            finally:
                conn.close()
            if row:
                return row[0].split("|")[1]
            time.sleep(2)
        return None
    return send, wait


def run(out_dir: str, enforce: EnforceClient, kalshi: KalshiClient, *,
        approver_factory: Optional[Callable] = None,
        clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc),
        log: Callable[[str], None] = print) -> int:
    now = clock()
    principal_key, approver_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    profile = make_profile(principal_key, approver_key, now)
    btc = open_markets(kalshi, "KXBTC15M", 2)
    eth = open_markets(kalshi, "KXETH15M", 1)
    doge = open_markets(kalshi, OUTSIDE_SERIES, 1)
    if not btc or not eth or not doge:
        log(f"setup: no open market (KXBTC15M {len(btc)}, KXETH15M {len(eth)}, "
            f"{OUTSIDE_SERIES} {len(doge)})")
        return 2
    approver = approver_factory(APPROVER_DID, approver_key) if approver_factory else None
    receipts = os.path.join(out_dir, "receipts")
    guard = KalshiOrderGuard(enforce, profile, kalshi, approver=approver,
                             attest=ed25519_attester(PRINCIPAL_DID, principal_key,
                                                     keyid="kg-enf-demo"),
                             receipts_dir=receipts, clock=clock)
    trust = trust_list_for(PRINCIPAL_DID, principal_key.public_key(), keyid="kg-enf-demo")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "trust.json"), "w", encoding="utf-8") as fh:
        json.dump(trust, fh, indent=2)
    with open(os.path.join(out_dir, "mandate.json"), "w", encoding="utf-8") as fh:
        json.dump(guard.mandate, fh, indent=2)

    rows, failures = [], 0
    for i, step in enumerate(plan(btc, eth, doge[0]), 1):
        out = guard.place_order(step["order"])
        v = verify_record(out.aer_record, out.aer_bundle, out.mandate, out.transaction, trust)
        checks = " ".join(f"{k}:{v.checks[k]['result']}" for k in ("V1", "V2", "V3", "V4"))
        bad = (not v.ok) or (out.disposition == FORBID and out.sent)
        failures += bad
        rows.append({"n": i, "expect": step["expect"], "disposition": out.disposition,
                     "sent": out.sent, "kalshi_status": out.kalshi_status,
                     "check_ms": round(out.check_ms, 1), "aer": "PASS" if v.ok else "FAIL",
                     "recomputed": v.recomputed_verdict, "reason": out.reason,
                     "receipt": os.path.relpath(out.receipt_path, out_dir)})
        log(f"{i:2d} expect={step['expect']:<6} got={out.disposition:<6} sent={out.sent!s:<5} "
            f"kalshi={out.kalshi_status} check={out.check_ms:6.1f}ms {checks} "
            f"-> {v.recomputed_verdict} | {out.reason}")

    lat = [r["check_ms"] for r in rows]
    count = {d: sum(1 for r in rows if r["disposition"] == d) for d in (ALLOW, HOLD, FORBID)}
    summary = {"date": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "orders": len(rows),
               "dispositions": count, "sent": sum(r["sent"] for r in rows),
               "aer_pass": sum(r["aer"] == "PASS" for r in rows),
               "unexpected": [r["n"] for r in rows if r["expect"] != r["disposition"]],
               "check_ms": {"n": len(lat), "p50": percentile(lat, 50),
                            "p95": percentile(lat, 95)},
               "enforce_url": enforce.base_url, "kalshi_url": kalshi.base_url}
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "rows": rows}, fh, indent=2)
    log(json.dumps(summary, indent=2))
    log(f"offline: moltrust-verify --input {receipts}/<file> --trust-list {out_dir}/trust.json")
    return 1 if failures or count[FORBID] < 5 or len(rows) < 20 else 0


def _secret(name: str) -> Optional[str]:
    """Environment first, then ~/.moltrust_secrets (KEY=value lines). Never printed."""
    if os.environ.get(name):
        return os.environ[name]
    path = os.path.expanduser(os.environ.get("MOLTRUST_SECRETS_FILE", "~/.moltrust_secrets"))
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip().removeprefix("export ")
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="kg-enf-run")
    ap.add_argument("--enforce-url", default="https://api.moltrust.ch")
    ap.add_argument("--did", default=None, help="X-MolTrust-DID instead of an API key")
    ap.add_argument("--telegram", action="store_true", help="server only, see module doc")
    ap.add_argument("--hold-timeout", type=float, default=300.0)
    args = ap.parse_args(argv)

    key_id = _secret("KALSHI_DEMO_KEY_ID")
    pem_path = _secret("KALSHI_DEMO_PRIVATE_KEY_PATH")
    if not key_id or not pem_path:
        print("setup: KALSHI_DEMO_KEY_ID and KALSHI_DEMO_PRIVATE_KEY_PATH are required",
              file=sys.stderr)
        return 2
    with open(os.path.expanduser(pem_path), "rb") as fh:
        pem = fh.read()
    api_key = None if args.did else _secret("MOLTRUST_API_KEY")
    if not api_key and not args.did:
        print("setup: MOLTRUST_API_KEY or --did is required", file=sys.stderr)
        return 2
    enforce = EnforceClient(args.enforce_url, api_key=api_key, did=args.did, timeout=10)
    kalshi = KalshiClient(key_id, pem)  # default demo host; non-demo is refused
    factory = None
    if args.telegram:
        send, wait = server_telegram(args.hold_timeout)
        factory = lambda did, key: TelegramApprover(  # noqa: E731
            send, wait, authority_did=did, signing_key=key, timeout_s=args.hold_timeout)
    try:
        return run(args.out, enforce, kalshi, approver_factory=factory)
    finally:
        kalshi.close()
        enforce.close()


if __name__ == "__main__":
    sys.exit(main())
