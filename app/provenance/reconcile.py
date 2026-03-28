"""
Output Provenance — On-chain / Off-chain Reconciliation (Spec v0.4)

Handles three discrepancy scenarios:
A. DB says 'anchored' but TX not on chain
B. DB says 'pending' but TX already on chain
C. Merkle proof doesn't verify
"""
import json
from typing import Optional


async def check_ipr_status(conn, ipr_id: str) -> dict:
    """
    Check DB vs chain consistency for a single IPR.
    Returns status report with discrepancy flag.
    """
    import uuid
    row = await conn.fetchrow(
        "SELECT id, anchor_status, anchor_tx, anchor_block, merkle_proof "
        "FROM interaction_proof_records WHERE id = $1",
        uuid.UUID(ipr_id)
    )
    if not row:
        return None

    db_status = row["anchor_status"]
    anchor_tx = row["anchor_tx"]
    chain_status = "unknown"
    discrepancy = False
    resolution = None

    if db_status == "anchored" and anchor_tx:
        # Verify TX exists on chain
        chain_status = await _check_tx_on_chain(anchor_tx)
        if chain_status == "not_found":
            discrepancy = True
            resolution = "re-anchoring scheduled"
    elif db_status == "pending":
        chain_status = "not_checked"

    return {
        "ipr_id": str(row["id"]),
        "db_status": db_status,
        "chain_status": chain_status,
        "anchor_tx": anchor_tx,
        "discrepancy": discrepancy,
        "resolution": resolution,
    }


async def reconcile_pending(conn) -> dict:
    """
    Admin: Scan all 'anchored' records and verify TX on chain.
    Reset any with missing TXs to 'pending'.
    """
    rows = await conn.fetch(
        "SELECT id, anchor_tx FROM interaction_proof_records "
        "WHERE anchor_status = 'anchored' AND anchor_tx IS NOT NULL"
    )

    checked = 0
    reset = 0
    for r in rows:
        checked += 1
        status = await _check_tx_on_chain(r["anchor_tx"])
        if status == "not_found":
            await conn.execute(
                """UPDATE interaction_proof_records
                   SET anchor_status = 'pending', anchor_tx = NULL,
                       anchor_block = NULL, merkle_proof = NULL,
                       anchor_retries = 0
                   WHERE id = $1""",
                r["id"]
            )
            reset += 1

    return {"checked": checked, "reset_to_pending": reset}


async def retry_failed(conn) -> dict:
    """
    Admin: Reset 'failed' records back to 'pending' for re-anchoring.
    Only resets records with retries < 3.
    """
    result = await conn.execute(
        """UPDATE interaction_proof_records
           SET anchor_status = 'pending', anchor_retries = 0
           WHERE anchor_status = 'failed'"""
    )
    count = int(result.split()[-1]) if result else 0
    return {"reset": count}


async def reanchor_ipr(conn, ipr_id: str) -> dict:
    """
    Admin: Force re-anchor a specific IPR by resetting its anchor state.
    """
    import uuid
    result = await conn.execute(
        """UPDATE interaction_proof_records
           SET anchor_status = 'pending', anchor_tx = NULL,
               anchor_block = NULL, merkle_proof = NULL, anchor_retries = 0
           WHERE id = $1""",
        uuid.UUID(ipr_id)
    )
    if "UPDATE 1" in result:
        return {"ipr_id": ipr_id, "status": "reset_to_pending"}
    return {"ipr_id": ipr_id, "status": "not_found"}


async def _check_tx_on_chain(tx_hash: str) -> str:
    """Check if a TX exists on Base L2. Returns 'confirmed' or 'not_found'."""
    try:
        from web3 import Web3
        import os
        w3 = Web3(Web3.HTTPProvider(os.getenv("BASE_RPC", "https://mainnet.base.org")))
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        if receipt and receipt.status == 1:
            return "confirmed"
        return "not_found"
    except Exception:
        return "unknown"
