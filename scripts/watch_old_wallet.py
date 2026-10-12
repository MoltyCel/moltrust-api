#!/usr/bin/env python3
"""Watch incoming value on the retired wallet OLD for 30 days after the rotation.

Ops backlog R2, rotation step 9 (2026-10-12). The productive signer moved from
OLD 0x3802… to NEW 0xD63f…; IDs and payment addresses point to NEW. Anyone who
still pays OLD from a stale config, or any token sent there, should be seen
before the old keystore is removed. The plan says: watch incoming transfers on
OLD for 30 days, then decide.

Each run reads ERC-20 Transfer logs to OLD (any token contract) from the last
checked block to the chain head, in chunks, plus OLD's native balance against
the last run. Findings go to ALERTS through app/notify.py, with tx hash, token
contract and amount. Every run writes a heartbeat {timestamp, status, detail}.

    python3 scripts/watch_old_wallet.py          # cron, daily
    python3 scripts/watch_old_wallet.py --dry-run

Status values: ok (read to head, nothing in), finding (something came in),
expired (the 30 days are over; the run does nothing and says so), error
(the chain could not be read to the head: no result, never a quiet zero).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.base_rpc import base_rpc_url  # noqa: E402

OLD = "0x380238347e58435f40B4da1F1A045A271D5838F5"
# 30 days from the rotation on 2026-10-12.
WINDOW_END = dt.datetime(2026, 11, 11, 23, 59, 59, tzinfo=dt.timezone.utc)
# Block of the first NEW-signed anchor after the rotation (tx 0xa3b9d7f4…, 2026-10-12
# 22:20 UTC) minus a margin; the first run reads from here.
START_BLOCK = 52_500_000
CHUNK = 2000
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "watch_old_wallet_state.json"
HEARTBEAT = ROOT / "data" / "watch_old_wallet_heartbeat.json"


def rpc(method: str, params: list):
    req = urllib.request.Request(base_rpc_url(), headers={"content-type": "application/json"},
                                 data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                                  "params": params}).encode())
    with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310 - https enforced by base_rpc_url
        body = json.load(r)
    if "error" in body:
        raise RuntimeError(f"{method}: {body['error'].get('message', 'rpc error')}")
    return body["result"]


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def heartbeat(status: str, detail: str) -> None:
    write_json(HEARTBEAT, {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                           "status": status, "detail": detail})
    print(f"{status}: {detail}")


def incoming_transfers(frm: int, to: int) -> list[dict]:
    topic_to = "0x" + "0" * 24 + OLD[2:].lower()
    found = []
    block = frm
    while block <= to:
        end = min(block + CHUNK - 1, to)
        logs = rpc("eth_getLogs", [{"fromBlock": hex(block), "toBlock": hex(end),
                                    "topics": [TRANSFER, None, topic_to]}])
        for lg in logs:
            found.append({"tx": lg["transactionHash"], "block": int(lg["blockNumber"], 16),
                          "token": lg["address"], "raw_amount": int(lg["data"], 16) if lg["data"] != "0x" else 0})
        block = end + 1
    return found


def main(argv=None, now: dt.datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="read and print, no state, no message")
    args = ap.parse_args(argv)
    now = now or dt.datetime.now(dt.timezone.utc)
    if now > WINDOW_END:
        heartbeat("expired", f"30-day watch on OLD ended {WINDOW_END:%Y-%m-%d}; decision on the keystore is Lars'")
        return 0

    state = json.loads(STATE.read_text()) if STATE.exists() else {"last_block": START_BLOCK - 1,
                                                                    "native_wei": None}
    try:
        head = int(rpc("eth_blockNumber", []), 16)
        transfers = incoming_transfers(state["last_block"] + 1, head)
        native = int(rpc("eth_getBalance", [OLD, "latest"]), 16)
    except Exception as exc:  # the chain was not read to the head: no result
        heartbeat("error", f"chain not read to head: {type(exc).__name__}: {str(exc)[:120]}")
        return 1

    native_up = state["native_wei"] is not None and native > state["native_wei"]
    lines = [f"{t['tx']} block {t['block']} token {t['token']} raw {t['raw_amount']}" for t in transfers]
    if native_up:
        lines.append(f"native balance rose {state['native_wei']} -> {native} wei")
    detail = (f"blocks {state['last_block'] + 1}..{head}: {len(transfers)} token transfer(s) in"
              + ("; native balance rose" if native_up else ""))

    if args.dry_run:
        print(detail)
        for line in lines:
            print("  " + line)
        return 0

    if lines:
        from app import notify
        notify.send_telegram("Retired wallet OLD 0x3802… received value (ops R2 watch, until "
                             f"{WINDOW_END:%Y-%m-%d}):\n" + "\n".join(lines), channel=notify.ALERTS)
        status = "finding"
    else:
        status = "ok"
    write_json(STATE, {"last_block": head, "native_wei": native})
    heartbeat(status, detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
