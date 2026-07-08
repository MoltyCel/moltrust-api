#!/usr/bin/env python3
"""
anchor_publication.py — Anchor a single MolTrust publication PDF on Base L2.

Wraps app.provenance.anchor.anchor_single_calldata() with:
- SHA-256 computation from the PDF bytes
- Tag-prefixed payload matching the MolTrust/arXiv/v1.0 convention
- Receipt wait to retrieve the block number
- Human-readable output + machine-readable JSON block for integrity.html

Usage:
    set -a && source ~/.moltrust_secrets && set +a
    cd ~/moltstack
    python3 anchor_publication.py <pdf-path> <tag>

Example:
    python3 anchor_publication.py \\
      ~/moltrust-publications-build/out/eu-ai-act-mapping.pdf \\
      MolTrust/regulatory/eu-ai-act-mapping-v1.0

Exit codes:
    0  anchor succeeded, block confirmed
    1  arg error
    2  BASE env not configured (anchor_single_calldata returned None)
    3  receipt timeout (tx submitted but block not confirmed within 60s;
       tx_hash is still printed — check Basescan manually)
"""
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

# Make sure we import from the moltstack code tree
MOLTSTACK = Path.home() / "moltstack"
sys.path.insert(0, str(MOLTSTACK))

from app.provenance.anchor import anchor_single_calldata  # noqa: E402


async def wait_for_block(tx_hash: str, timeout: int = 60) -> int | None:
    """Poll Base RPC for the receipt, return block number or None on timeout."""
    from web3 import Web3
    rpc = os.getenv("BASE_RPC", "https://mainnet.base.org")
    w3 = Web3(Web3.HTTPProvider(rpc))
    for _ in range(timeout):
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            if receipt and receipt.get("blockNumber"):
                return receipt["blockNumber"]
        except Exception:
            pass
        await asyncio.sleep(1)
    return None


async def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 1

    pdf_path = Path(sys.argv[1]).expanduser().resolve()
    tag = sys.argv[2]

    # Publication anchoring uses a DEDICATED wallet (BASE_ANCHOR_KEY), never the
    # revenue/MoltGuard (0x3802) or ERC-8004 (0x9068) wallets. Wire it into the
    # env vars anchor_single_calldata reads, in THIS process only; IPR anchoring
    # keeps its own BASE_WRITE_KEY. See docs/WALLET_STATE.md.
    anchor_key = os.getenv("BASE_ANCHOR_KEY")
    anchor_addr = os.getenv("BASE_ANCHOR_ADDR")
    if not anchor_key or not anchor_addr:
        print("ERROR: BASE_ANCHOR_KEY / BASE_ANCHOR_ADDR not set — publication "
              "anchoring requires the dedicated anchor wallet.", file=sys.stderr)
        return 2
    os.environ["BASE_WRITE_KEY"] = anchor_key
    os.environ["BASE_ADDR"] = anchor_addr

    if not pdf_path.is_file():
        print(f"ERROR: not a file: {pdf_path}", file=sys.stderr)
        return 1

    sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    payload = f"{tag} sha256:{sha}"

    print(f"File:       {pdf_path.name}")
    print(f"Bytes:      {pdf_path.stat().st_size:,}")
    print(f"SHA-256:    {sha}")
    print(f"Tag:        {tag}")
    print(f"Payload:    {payload}")
    print(f"            ({len(payload.encode('utf-8'))} bytes calldata)")
    print(f"RPC:        {os.getenv('BASE_RPC', 'https://mainnet.base.org')}")
    print(f"From:       {os.getenv('BASE_ADDR', '(unset)')}")
    print()
    print("→ submitting TX...")
    tx_hash = await anchor_single_calldata(payload)
    if tx_hash is None:
        print("ERROR: anchor_single_calldata returned None.", file=sys.stderr)
        print("       BASE_ADDR / BASE_WRITE_KEY (or BASE_KEY) not in env.", file=sys.stderr)
        print("       Did you `set -a && source ~/.moltrust_secrets && set +a`?", file=sys.stderr)
        return 2

    print(f"TX:         {tx_hash}")
    print(f"→ waiting for block confirmation (up to 60s)...")
    block = await wait_for_block(tx_hash)
    if block is None:
        print(f"WARNING: receipt not confirmed within 60s.", file=sys.stderr)
        print(f"         TX was submitted; check https://basescan.org/tx/{tx_hash}", file=sys.stderr)
        return 3

    print(f"Block:      {block:,}")
    print(f"Basescan:   https://basescan.org/tx/{tx_hash}")
    print()
    print("--- integrity.html fields ---")
    print(json.dumps({
        "file": pdf_path.name,
        "sha256": sha,
        "tx": tx_hash,
        "block": block,
        "tag": tag,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
