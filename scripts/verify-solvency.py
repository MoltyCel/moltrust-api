#!/usr/bin/env python3
"""Independently verify solvency_usdc_v0 for a MolTrust DID.

Third-party reference implementation. Standard library only — no web3, no
MolTrust code. It fetches the published inputs, re-derives the value straight
from a Base RPC, and checks it matches the API.

  python3 verify-solvency.py did:moltrust:<id>
  python3 verify-solvency.py did:moltrust:<id> --api-base https://api.moltrust.ch
  python3 verify-solvency.py --from-file response.json   # offline (e.g. a VC payload)

Spec: docs/solvency-usdc-v0.md (v0.1.0). Exit 0 on match, 1 on mismatch/error.
"""
import argparse
import json
import sys
import urllib.request

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


# Some public RPCs / CDNs reject the default "Python-urllib" UA with HTTP 403.
_UA = "verify-solvency/0.1 (+docs/solvency-usdc-v0.md)"


def _rpc(rpc_url, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        rpc_url, data=body,
        headers={"content-type": "application/json", "user-agent": _UA},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    if "error" in out:
        raise RuntimeError(f"{method} -> {out['error']}")
    return out["result"]


def _get_json(url):
    req = urllib.request.Request(url, headers={"user-agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def clamp_round(value, cap):
    return max(0, min(cap, round(value)))


def recompute(resp, rpc_url):
    wallet = resp["deposit_wallet"].lower()
    token = resp["token_contract"].lower()
    min_conf = int(resp["min_confirmations"])
    cap = int(resp["cap"])
    current_block = int(_rpc(rpc_url, "eth_blockNumber", []), 16)

    total = 0.0
    for dep in resp.get("inputs", []):
        receipt = _rpc(rpc_url, "eth_getTransactionReceipt", [dep["tx_hash"]])
        if receipt is None or int(receipt.get("status", "0x0"), 16) != 1:
            raise RuntimeError(f"tx {dep['tx_hash']} not found or reverted")
        confirmations = current_block - int(receipt["blockNumber"], 16)
        if confirmations < min_conf:
            print(f"  SKIP {dep['tx_hash']}: {confirmations} < {min_conf} confirmations")
            continue
        for log in receipt["logs"]:
            if log["address"].lower() != token:
                continue
            t = log["topics"]
            if len(t) < 3 or t[0].lower() != TRANSFER_TOPIC:
                continue
            to_addr = "0x" + t[2][-40:]
            if to_addr.lower() != wallet:
                continue
            amount = int(log["data"], 16) / 1e6
            total += amount
            print(f"  + {amount} USDC  tx={dep['tx_hash'][:18]}…  conf={confirmations}")
    return total, clamp_round(total, cap)


def main():
    ap = argparse.ArgumentParser(description="Verify solvency_usdc_v0 (v0.1.0) for a DID.")
    ap.add_argument("did", nargs="?", help="did:moltrust:<id>")
    ap.add_argument("--api-base", default="https://api.moltrust.ch")
    ap.add_argument("--rpc", default="https://mainnet.base.org")
    ap.add_argument("--from-file", help="read the endpoint response from a JSON file instead of HTTP")
    args = ap.parse_args()

    if args.from_file:
        resp = json.load(open(args.from_file))
    elif args.did:
        resp = _get_json(f"{args.api_base}/credits/solvency/{args.did}")
    else:
        ap.error("provide a DID or --from-file")

    claimed = int(resp["solvency_usdc_v0"])
    print(f"DID:        {resp.get('did')}")
    print(f"version:    {resp.get('version')}   cap={resp.get('cap')}   min_conf={resp.get('min_confirmations')}")
    print(f"inputs:     {resp.get('deposit_count')} deposit(s)")
    print(f"API claims: solvency_usdc_v0 = {claimed}  (raw sum {resp.get('onchain_usdc_sum')})")
    print("recomputing from Base RPC ...")

    raw_sum, recomputed = recompute(resp, args.rpc)
    print(f"\nrecomputed: raw sum {round(raw_sum, 6)} USDC -> solvency_usdc_v0 = {recomputed}")

    if recomputed == claimed:
        print(f"\n✅ MATCH — independently reproduced {recomputed} from on-chain data.")
        return 0
    print(f"\n❌ MISMATCH — API says {claimed}, chain says {recomputed}.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
