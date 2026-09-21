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
            # Record the padding in `levels`, not only in `current`. The root
            # was always computed over the padded level; `levels` kept the
            # unpadded one, so merkle_proof could not see the duplicated
            # sibling and dropped it via its `sibling_idx < len(level)` guard.
            # The resulting proof did not replay to the root it was issued
            # against — for any leaf that sat at the end of an odd level.
            current = current + [current[-1]]
            levels[-1] = current
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
        import os
        w3 = Web3(Web3.HTTPProvider(os.getenv("BASE_RPC", "https://mainnet.base.org")))
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
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
        import os

        BASE_RPC = os.getenv("BASE_RPC", "https://mainnet.base.org")
        BASE_ADDR = os.getenv("BASE_ADDR", "")
        BASE_KEY = os.getenv("BASE_WRITE_KEY", os.getenv("BASE_KEY", ""))

        if not BASE_ADDR or not BASE_KEY:
            print("IPR anchor: BASE_ADDR/BASE_KEY not configured")
            return None

        w3 = Web3(Web3.HTTPProvider(BASE_RPC))
        if not w3.is_connected():
            return None

        # "pending" (not "latest") so back-to-back anchors do not reuse a nonce
        # and get dropped/replaced (v0.8.1 nonce-race lesson; the v0.9 anchor
        # needed a manual pending-nonce one-off because this path used "latest").
        nonce = w3.eth.get_transaction_count(BASE_ADDR, "pending")
        tx = {
            "from": BASE_ADDR,
            "to": BASE_ADDR,
            "value": 0,
            "data": w3.to_bytes(text=calldata),
            "nonce": nonce,
            "chainId": 8453,
            "gas": 30000,
            "maxFeePerGas": w3.eth.gas_price + w3.to_wei(0.001, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
        }
        signed = w3.eth.account.sign_transaction(tx, BASE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        return w3.to_hex(tx_hash)
    except Exception as e:
        print(f"IPR anchor error: {e}")
        return None


# --- Credential-specific -----------------------------------------------------
# Issued credentials were never anchored: /credentials/issue had no anchor path,
# the credentials table had no column for one, and the only anchoring cron
# covered IPRs. A credential whose anchor nobody can point at is a claim, not
# evidence — the whole argument for issuing it is that a third party can
# recompute the check without asking us.
#
# The Merkle machinery above is reused as-is. What differs is the leaf, the
# table, and the wallet: credential anchoring is paid for out of BASE_ANCHOR_KEY
# (the dedicated anchoring address), not the productive wallet.

CREDENTIAL_CALLDATA_PREFIX = "MolTrust/VC/v1"


def compute_credential_leaf(cred_id: str, subject_did: str, credential_type: str,
                            issued_at: str, proof_value: str) -> str:
    """Merkle leaf for one credential.

    Everything a verifier would compare against: which credential, about whom,
    of what kind, when, and the signature over it. Leave any of those out and
    the anchor stops binding the thing it is supposed to bind.
    """
    data = f"{cred_id}|{subject_did}|{credential_type}|{issued_at}|{proof_value}"
    return hashlib.sha256(data.encode()).hexdigest()


def _credential_leaves(records: list[dict]) -> list[str]:
    out = []
    for r in records:
        issued = r["issued_at"] if isinstance(r["issued_at"], str) else r["issued_at"].isoformat()
        out.append(compute_credential_leaf(
            str(r["id"]), r["subject_did"], r["credential_type"], issued, r["proof_value"] or "",
        ))
    return out


def credential_merkle_proof(records: list[dict], index: int) -> dict:
    leaves_hex = _credential_leaves(records)
    leaves = [bytes.fromhex(h) for h in leaves_hex]
    return {
        "leaf": leaves_hex[index],
        "path": merkle_proof(leaves, index),
        "root": merkle_root(leaves).hex(),
    }


async def anchor_credentials_batch(conn, anchor_fn, limit: int = 200) -> dict:
    """Anchor every unanchored credential in one Merkle-batched Base L2 tx.

    One transaction per batch rather than one per credential: 134 separate
    anchors would be 134 gas payments for a claim the root already carries.
    """
    rows = await conn.fetch(
        """SELECT c.id, c.subject_did, c.credential_type, c.issued_at, c.proof_value
             FROM credentials c
             LEFT JOIN credential_anchors a ON a.credential_id = c.id
            WHERE a.credential_id IS NULL
            ORDER BY c.issued_at ASC
            LIMIT $1""",
        limit,
    )
    if not rows:
        return {"batched": 0, "status": "no_pending"}

    records = [dict(r) for r in rows]
    leaves_hex = _credential_leaves(records)
    root = merkle_root([bytes.fromhex(h) for h in leaves_hex]).hex()

    tx_hash = await anchor_fn(f"{CREDENTIAL_CALLDATA_PREFIX}/{root}")
    if not tx_hash:
        return {"batched": 0, "status": "anchor_failed", "pending": len(records)}

    block_number = None
    try:
        from web3 import Web3
        import os as _os
        w3 = Web3(Web3.HTTPProvider(_os.getenv("BASE_RPC", "https://mainnet.base.org")))
        block_number = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60).blockNumber
    except Exception:
        pass

    for i, r in enumerate(records):
        proof = credential_merkle_proof(records, i)
        # The anchor goes into the stored VC as well, under `evidence`, where a
        # W3C verifier already looks. A credential that only carries its anchor
        # in our database has not gained much — the point is that the holder can
        # hand the document to someone else.
        evidence = json.dumps([{
            "type": ["MerkleBatchAnchor2026"],
            "chain": "eip155:8453",
            # First field of the leaf preimage, and the one the credential
            # document did not otherwise carry. Without it a holder can replay
            # the sibling path but cannot recompute the leaf it starts from —
            # so the leaf below was ours to assert rather than theirs to check.
            # Encoding: docs/spec-fakten/anchor-commitment.md.
            "credentialId": r["id"],
            "txHash": tx_hash,
            "blockNumber": block_number,
            "merkleRoot": proof["root"],
            "merkleProof": proof["path"],
            "leaf": proof["leaf"],
        }])
        await conn.execute(
            """INSERT INTO credential_anchors
                   (credential_id, tx_hash, block, merkle_root, merkle_proof)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (credential_id) DO NOTHING""",
            r["id"], tx_hash, block_number, proof["root"], json.dumps(proof),
        )
        # raw_vc is DML, which this role does have on credentials — only the
        # DDL was out of reach.
        await conn.execute(
            """UPDATE credentials
                  SET raw_vc = CASE WHEN raw_vc IS NULL THEN raw_vc
                                    ELSE jsonb_set(raw_vc, '{evidence}', $1::jsonb, true) END
                WHERE id = $2""",
            evidence, r["id"],
        )

    return {
        "batched": len(records),
        "merkle_root": root,
        "tx_hash": tx_hash,
        "block": block_number,
        "status": "anchored",
    }


async def anchor_calldata_from_anchor_wallet(calldata: str) -> Optional[str]:
    """Same self-send as anchor_single_calldata, paid from BASE_ANCHOR_KEY.

    The productive wallet signs what the product charges for. Anchoring is
    bookkeeping, and it has its own address so that running it dry cannot stop
    the thing people paid for.
    """
    try:
        from web3 import Web3
        import os

        BASE_RPC = os.getenv("BASE_RPC", "https://mainnet.base.org")
        addr = os.getenv("BASE_ANCHOR_ADDR", "")
        key = os.getenv("BASE_ANCHOR_KEY", "")
        if not addr or not key:
            print("VC anchor: BASE_ANCHOR_ADDR/BASE_ANCHOR_KEY not configured")
            return None

        w3 = Web3(Web3.HTTPProvider(BASE_RPC))
        if not w3.is_connected():
            return None

        # "pending", for the same nonce-race reason as anchor_single_calldata.
        nonce = w3.eth.get_transaction_count(addr, "pending")
        tx = {
            "from": addr, "to": addr, "value": 0,
            "data": w3.to_bytes(text=calldata),
            "nonce": nonce, "chainId": 8453, "gas": 30000,
            "maxFeePerGas": w3.eth.gas_price + w3.to_wei(0.001, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
        }
        signed = w3.eth.account.sign_transaction(tx, key)
        return w3.to_hex(w3.eth.send_raw_transaction(signed.raw_transaction))
    except Exception as e:
        print(f"VC anchor error: {e}")
        return None


def replay_proof(proof) -> bool | None:
    """Walk a stored proof back to its own root. None when there is none.

    The check nobody was doing: a proof column that is merely non-null says
    nothing, and the padding bug produced proofs that looked complete and did
    not reconstruct.
    """
    if isinstance(proof, str):
        try:
            proof = json.loads(proof)
        except Exception:
            return None
    if not isinstance(proof, dict):
        return None
    leaf, root = proof.get("leaf"), proof.get("root")
    siblings = proof.get("siblings") or proof.get("path")
    if not leaf or not root or not isinstance(siblings, list):
        return None
    cur = bytes.fromhex(leaf)
    for step in siblings:
        sib = bytes.fromhex(step["hash"])
        cur = _sha256(sib + cur if step.get("position") == "left" else cur + sib)
    return cur.hex() == root
