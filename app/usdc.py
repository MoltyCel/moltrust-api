"""MolTrust — USDC Deposit Verification on Base (L2)."""

from web3 import Web3
import logging

log = logging.getLogger("moltrust.usdc")

# --- Config ---
BASE_RPC = "https://mainnet.base.org"
USDC_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
MOLTRUST_WALLET = "0x380238347e58435f40B4da1F1A045A271D5838F5"
USDC_DECIMALS = 6
CREDITS_PER_USDC = 100
MIN_CONFIRMATIONS = 5

# ERC-20 Transfer event topic
TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()

w3 = Web3(Web3.HTTPProvider(BASE_RPC))


def verify_usdc_transfer(tx_hash: str) -> dict:
    """Verify a USDC transfer to MolTrust wallet on Base.
    
    Returns dict with: valid, from_address, usdc_amount, credits, block_number, error
    """
    result = {
        "valid": False, "from_address": None, "usdc_amount": 0.0,
        "credits": 0, "block_number": None, "error": None,
    }

    try:
        # Normalize tx hash
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash

        # Get transaction receipt
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        if receipt is None:
            result["error"] = "Transaction not found. Is it confirmed?"
            return result

        # Check if tx was successful
        if receipt["status"] != 1:
            result["error"] = "Transaction failed (reverted)"
            return result

        # Check confirmations
        current_block = w3.eth.block_number
        confirmations = current_block - receipt["blockNumber"]
        if confirmations < MIN_CONFIRMATIONS:
            result["error"] = f"Need {MIN_CONFIRMATIONS} confirmations, have {confirmations}. Try again shortly."
            return result

        # Find USDC Transfer event to MolTrust wallet
        moltrust_addr = MOLTRUST_WALLET.lower()
        usdc_addr = USDC_CONTRACT.lower()

        for log_entry in receipt["logs"]:
            # Must be from USDC contract
            if log_entry["address"].lower() != usdc_addr:
                continue

            # Must be Transfer event
            if len(log_entry["topics"]) < 3:
                continue
            if log_entry["topics"][0].hex() != TRANSFER_TOPIC:
                continue

            # Decode from and to addresses from topics
            from_addr = "0x" + log_entry["topics"][1].hex()[-40:]
            to_addr = "0x" + log_entry["topics"][2].hex()[-40:]

            # Must be sent TO MolTrust wallet
            if to_addr.lower() != moltrust_addr:
                continue

            # Decode amount from data (uint256)
            raw_amount = int(log_entry["data"].hex(), 16)
            usdc_amount = raw_amount / (10 ** USDC_DECIMALS)
            credits = int(usdc_amount * CREDITS_PER_USDC)

            if credits < 1:
                result["error"] = f"Amount too small: {usdc_amount} USDC = {credits} credits"
                return result

            result["valid"] = True
            result["from_address"] = Web3.to_checksum_address(from_addr)
            result["usdc_amount"] = usdc_amount
            result["credits"] = credits
            result["block_number"] = receipt["blockNumber"]
            return result

        result["error"] = "No USDC transfer to MolTrust wallet found in this transaction"
        return result

    except Exception as e:
        log.error(f"USDC verification error: {e}")
        result["error"] = f"Verification failed: {str(e)}"
        return result


async def record_deposit(conn, tx_hash: str, from_address: str, to_did: str,
                         usdc_amount: float, credits: int, block_number: int) -> bool:
    """Record a deposit in the database. Returns False if tx_hash already claimed."""
    try:
        await conn.execute(
            """INSERT INTO usdc_deposits
               (tx_hash, from_address, to_did, usdc_amount, credits_granted, block_number)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            tx_hash, from_address, to_did, usdc_amount, credits, block_number,
        )
        return True
    except Exception:
        # UNIQUE constraint on tx_hash = already claimed
        return False


async def get_deposits(conn, did: str, limit: int = 50) -> list[dict]:
    """Return deposit history for a DID."""
    rows = await conn.fetch(
        """SELECT tx_hash, from_address, usdc_amount, credits_granted,
                  block_number, claimed_at
           FROM usdc_deposits WHERE to_did = $1
           ORDER BY claimed_at DESC LIMIT $2""",
        did, limit,
    )
    return [
        {
            "tx_hash": r["tx_hash"],
            "basescan_url": f"https://basescan.org/tx/{r['tx_hash']}",
            "from_address": r["from_address"],
            "usdc_amount": float(r["usdc_amount"]),
            "credits_granted": r["credits_granted"],
            "block_number": r["block_number"],
            "claimed_at": r["claimed_at"].isoformat() if r["claimed_at"] else None,
        }
        for r in rows
    ]
