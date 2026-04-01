"""
Output Provenance — Merkle Tree Batch Anchoring (Spec v0.4)

Reuses anchor_to_base() pattern from main.py for Base L2 transactions.
Extends with Merkle batching for cost-efficient multi-IPR anchoring.
Pure-Python Merkle tree (no external dependency).
"""
import hashlib
import json
from typing import Optional


# --- Pure-Python Merkle Tree ---

def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _build_tree(leaves: list[bytes]) -> list[list[bytes]]:
    """Build a Merkle tree from leaf hashes. Returns list of levels (bottom-up)."""
    if not leaves:
        return []
    # Pad to even number
    if len(leaves) % 2 == 1:
        leaves = leaves + [leaves[-1]]
    levels = [leaves]
    current = leaves
    while len(current) > 1:
        if len(current) % 2 == 1:
            current = current + [current[-1]]
        next_level = []
        for i in range(0, len(current), 2):
            next_level.append(_sha256(current[i] + current[i + 1]))
        levels.append(next_level)
        current = next_level
    return levels


def merkle_root(leaves: list[bytes]) -> bytes:
    """Compute Merkle root from leaf hashes."""
    if not leaves:
        return b""
    tree = _build_tree(leaves)
    return tree[-1][0]


def merkle_proof(leaves: list[bytes], index: int) -> list[dict]:
    """
    Get Merkle proof for leaf at index.
    Returns list of {hash, position} where position is 'left' or 'right'.
    """
    tree = _build_tree(leaves)
    proof = []
    idx = index
    for level in tree[:-1]:
        if idx % 2 == 0:
            sibling_idx = idx + 1
            position = "right"
        else:
            sibling_idx = idx - 1
            position = "left"
        if sibling_idx < len(level):
            proof.append({"hash": level[sibling_idx].hex(), "position": position})
        idx //= 2
    return proof


# --- IPR-specific ---

def compute_leaf(output_hash: str, agent_did: str, produced_at: str, confidence: float) -> str:
    """
    Compute Merkle leaf hash for an IPR.
    Includes output_hash, agent_did, produced_at, confidence for full IPR integrity.
    """
    data = f"{output_hash}|{agent_did}|{produced_at}|{confidence}"
    return hashlib.sha256(data.encode()).hexdigest()


def build_merkle_tree_from_records(records: list[dict]) -> tuple[str, list[str]]:
    """
    Build Merkle tree from IPR records.
    Returns (merkle_root_hex, leaf_hashes).
    """
    leaves_hex = []
    for r in records:
        produced = r["produced_at"] if isinstance(r["produced_at"], str) else r["produced_at"].isoformat()
        leaf = compute_leaf(r["output_hash"], r["agent_did"], produced, r["confidence"])
        leaves_hex.append(leaf)

    if not leaves_hex:
        return None, []

    leaves_bytes = [bytes.fromhex(h) for h in leaves_hex]
    root = merkle_root(leaves_bytes).hex()
    return root, leaves_hex


def get_merkle_proof_for_record(records: list[dict], index: int) -> dict:
    """Get Merkle proof for a specific record in the batch."""
    leaves_hex = []
    for r in records:
        produced = r["produced_at"] if isinstance(r["produced_at"], str) else r["produced_at"].isoformat()
        leaf = compute_leaf(r["output_hash"], r["agent_did"], produced, r["confidence"])
        leaves_hex.append(leaf)

    leaves_bytes = [bytes.fromhex(h) for h in leaves_hex]
    root = merkle_root(leaves_bytes).hex()
    proof = merkle_proof(leaves_bytes, index)

    return {
        "leaf": leaves_hex[index],
        "index": index,
        "siblings": proof,
        "root": root,
    }


# --- Batch Anchor ---

async def anchor_batch(conn, anchor_fn) -> dict:
    """
    Anchor all pending IPRs in a single Merkle-batched Base L2 transaction.

    Args:
        conn: asyncpg connection
        anchor_fn: async function(calldata_str) -> tx_hash

    Returns: dict with batch stats
    """
    rows = await conn.fetch(
        """SELECT id, agent_did, output_hash, produced_at, confidence
           FROM interaction_proof_records
           WHERE anchor_status = 'pending'
           ORDER BY created_at ASC
           LIMIT 100"""
    )

    if not rows:
        return {"batched": 0, "status": "no_pending"}

    records = [dict(r) for r in rows]

    # Build Merkle tree
    root, leaves = build_merkle_tree_from_records(records)
    if not root:
        return {"batched": 0, "status": "empty_tree"}

    # Anchor root on Base L2
    calldata = f"MolTrust/IPR/v1/{root}"
    tx_hash = await anchor_fn(calldata)

    if not tx_hash:
        # Increment retry counters
        for r in records:
            await conn.execute(
                """UPDATE interaction_proof_records
                   SET anchor_retries = anchor_retries + 1,
                       anchor_status = CASE
                           WHEN anchor_retries >= 2 THEN 'failed'
                           ELSE 'pending'
                       END
                   WHERE id = $1""",
                r["id"]
            )
        return {"batched": 0, "status": "anchor_failed", "retried": len(records)}

    # Get block number (best-effort)
    block_number = None
    try:
        from web3 import Web3
        import asyncio
        import os
        w3 = Web3(Web3.HTTPProvider(os.getenv("BASE_RPC", "https://mainnet.base.org")))
        receipt = await asyncio.to_thread(w3.eth.wait_for_transaction_receipt, tx_hash, 30)
        block_number = receipt.blockNumber
    except Exception:
        pass

    # Update all records with anchor data + individual Merkle proofs
    for i, r in enumerate(records):
        proof = get_merkle_proof_for_record(records, i)
        await conn.execute(
            """UPDATE interaction_proof_records
               SET anchor_tx = $1, anchor_block = $2,
                   merkle_proof = $3, anchor_status = 'anchored'
               WHERE id = $4""",
            tx_hash, block_number,
            json.dumps(proof), r["id"]
        )

    return {
        "batched": len(records),
        "merkle_root": root,
        "tx_hash": tx_hash,
        "block": block_number,
        "status": "anchored",
    }


async def anchor_single_calldata(calldata: str) -> Optional[str]:
    """
    Anchor calldata on Base L2. Reuses anchor_to_base() pattern from main.py.
    Self-send TX with calldata.
    """
    try:
        from web3 import Web3
        import asyncio
        import os
        from app.nonce_manager import get_nonce, reset_nonce

        BASE_RPC = os.getenv("BASE_RPC", "https://mainnet.base.org")
        BASE_ADDR = os.getenv("BASE_ADDR", "")
        BASE_KEY = os.getenv("BASE_WRITE_KEY", os.getenv("BASE_KEY", ""))

        if not BASE_ADDR or not BASE_KEY:
            print("IPR anchor: BASE_ADDR/BASE_KEY not configured")
            return None

        w3 = Web3(Web3.HTTPProvider(BASE_RPC))
        connected = await asyncio.to_thread(w3.is_connected)
        if not connected:
            return None

        nonce = await get_nonce(w3, BASE_ADDR)
        gas_price = await asyncio.to_thread(lambda: w3.eth.gas_price)
        tx = {
            "from": BASE_ADDR,
            "to": BASE_ADDR,
            "value": 0,
            "data": w3.to_bytes(text=calldata),
            "nonce": nonce,
            "chainId": 8453,
            "gas": 30000,
            "maxFeePerGas": gas_price + w3.to_wei(0.001, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
        }
        signed = w3.eth.account.sign_transaction(tx, BASE_KEY)
        tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed.raw_transaction)
        return w3.to_hex(tx_hash)
    except Exception as e:
        await reset_nonce(BASE_ADDR)
        print(f"IPR anchor error: {e}")
        return None
