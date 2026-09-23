#!/usr/bin/env python3
"""Tell us when the round-2 funding lands on 0xa175, and say what landed.

Read-only. It moves nothing and books nothing: the booking is a decision with a
hash in it, and a cron job is the wrong place to make one. This only removes the
need to ask the chain by hand every few minutes.

Balances come from the node's own state rather than an explorer index. On
2026-09-21 Blockscout reported a complete page while sitting 500 blocks behind,
and every figure derived from it was wrong without saying so.

Silent unless something changed, so it can run often without becoming noise.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

RPC = "https://mainnet.base.org"
WALLET = "0xa175d51bfe0170738720DAAEc627A84d44dc9Eb9"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WANT_USDC = 11.0
WANT_ETH = 0.002
STATE = os.path.expanduser("~/.a175-funding-state")


def rpc(method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    req = urllib.request.Request(
        RPC, data=body.encode(),
        headers={"content-type": "application/json", "User-Agent": "moltrust-watch/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310  # nosec B310 - RPC is a module constant
        return json.load(r).get("result")


def telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID_ALERTS") or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        urllib.request.urlopen(req, timeout=20).read()  # noqa: S310  # nosec B310 - api.telegram.org, literal
    except Exception as exc:  # noqa: BLE001 - a failed alert must not fail the check
        print(f"telegram failed: {exc}")


def main() -> int:
    usdc_raw = rpc("eth_call", [{"to": USDC,
                                 "data": "0x70a08231" + WALLET[2:].lower().rjust(64, "0")},
                                "latest"])
    eth_raw = rpc("eth_getBalance", [WALLET, "latest"])
    if not usdc_raw or not eth_raw:
        print("rpc gave no answer; nothing reported")
        return 0

    usdc = int(usdc_raw, 16) / 1e6
    eth = int(eth_raw, 16) / 1e18
    now = f"{usdc:.6f}/{eth:.6f}"

    previous = ""
    if os.path.exists(STATE):
        previous = open(STATE).read().strip()
    if now == previous:
        return 0
    with open(STATE, "w") as f:
        f.write(now)

    complete = usdc >= WANT_USDC and eth >= WANT_ETH
    head = "Runde-2-Finanzierung angekommen" if complete else "0xa175 hat sich veraendert"
    tail = ("Vollstaendig. Naechster Schritt: Eingang mit Hash buchen, dann Task 1 anlegen."
            if complete else
            "Noch nicht vollstaendig. Es wurde nichts ausgeloest.")
    msg = (f"MolTrust — {head}\n\n"
           f"USDC  {usdc:.6f}   erwartet {WANT_USDC}\n"
           f"ETH   {eth:.6f}   erwartet {WANT_ETH}\n\n{tail}")
    print(msg)
    telegram(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
