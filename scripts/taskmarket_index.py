#!/usr/bin/env python3
"""Read the bounty submissions straight off Base, not from the CLI's count.

Taskmarket runs as an EIP-2535 diamond, so the event lives in a facet and not in
the proxy's own ABI. TaskSubmitted(bytes32 taskId, address worker, bytes32
deliverable) is what a submission looks like on chain.

What this gives, exactly: how many submissions, from which wallets, at what
time. The CLI's submissionCount disagrees with the chain — it reported 17 while
the chain had 30 — so the chain is what this reads.

What it cannot give: the submission text. `deliverable` is a bytes32 hash. The
body lives off-chain and neither the CLI nor the public API exposes it, so the
DID a submission claims cannot be read this way. That field stays null in the
measurement rather than being guessed from registrations, which is a different
number.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

from web3 import Web3

RPC = "https://mainnet.base.org"
DIAMOND = "0xDDc6cC3e4D11c1f3527B867C7DAD4ED9869C33f7"
TASK_SUBMITTED = "TaskSubmitted(bytes32,address,bytes32)"
FIRST_BLOCK = 51558900  # the block the first bounty was escrowed in

TASKS = {
    "score":  "0xea9b5bd5310567979355dcc4a14995a21769cc413c4f29a3b83b03429f461168",
    "verify": "0xbe177536acec7b15bed66e92738d29e70073f43e98b0c36cffcd8a383019b80c",
}


def index(w3: Web3, task_id: str, from_block: int) -> list[dict]:
    topic0 = w3.keccak(text=TASK_SUBMITTED).hex()
    if not topic0.startswith("0x"):
        topic0 = "0x" + topic0
    logs = w3.eth.get_logs({
        "address": w3.to_checksum_address(DIAMOND),
        "fromBlock": from_block, "toBlock": w3.eth.block_number,
        "topics": [topic0, task_id],
    })
    out = []
    blocks: dict[int, int] = {}
    for lg in logs:
        bn = lg["blockNumber"]
        if bn not in blocks:
            blocks[bn] = w3.eth.get_block(bn).timestamp
        data = lg["data"]
        out.append({
            "block": bn,
            "at": dt.datetime.fromtimestamp(blocks[bn], dt.UTC).isoformat(),
            "worker": "0x" + lg["topics"][2].hex()[-40:],
            "deliverable": data.hex() if hasattr(data, "hex") else str(data),
            "tx": lg["transactionHash"].hex(),
        })
    return sorted(out, key=lambda r: r["block"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-block", type=int, default=FIRST_BLOCK)
    args = ap.parse_args()

    w3 = Web3(Web3.HTTPProvider(RPC))
    report = {"indexed_at": dt.datetime.now(dt.UTC).isoformat(), "tasks": {}}
    all_workers: set[str] = set()

    for name, tid in TASKS.items():
        subs = index(w3, tid, args.from_block)
        workers = {s["worker"] for s in subs}
        all_workers |= workers
        report["tasks"][name] = {
            "task_id": tid,
            "submissions": len(subs),
            "distinct_workers": len(workers),
            "first": subs[0]["at"] if subs else None,
            "last": subs[-1]["at"] if subs else None,
            "entries": subs,
        }

    report["submissions_total"] = sum(t["submissions"] for t in report["tasks"].values())
    report["distinct_workers_total"] = len(all_workers)
    report["submission_text"] = None
    report["submission_text_note"] = (
        "deliverable is a bytes32 hash; the body is off-chain and not exposed by "
        "the CLI or the public API, so the DID a submission claims cannot be read"
    )
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
