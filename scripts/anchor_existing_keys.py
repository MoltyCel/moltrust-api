#!/usr/bin/env python3
"""Retroactively anchor existing agent public keys on Base L2."""
import asyncio
import os
import sys
sys.path.insert(0, os.path.expanduser("~/moltstack"))

import asyncpg
from web3 import Web3
from eth_account import Account

BASE_RPC = "https://mainnet.base.org"
BASE_KEY = os.getenv("BASE_WALLET_KEY", "")
BASE_ADDR = Account.from_key(BASE_KEY).address if BASE_KEY else None


async def anchor_did_public_key(did: str, public_key_hex: str) -> dict:
    try:
        w3 = Web3(Web3.HTTPProvider(BASE_RPC))
        if not w3.is_connected():
            return {"hash": None, "blockNumber": None}
        identifier = did.split(":")[-1]
        calldata_str = f"MolTrust/DID/v1/{identifier}/{public_key_hex}"
        nonce = w3.eth.get_transaction_count(BASE_ADDR)
        tx = {
            "from": BASE_ADDR,
            "to": BASE_ADDR,
            "value": 0,
            "data": calldata_str.encode("utf-8"),
            "nonce": nonce,
            "chainId": 8453,
            "gas": 30000,
            "maxFeePerGas": w3.eth.gas_price + w3.to_wei(0.001, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
        }
        signed = w3.eth.account.sign_transaction(tx, BASE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
        return {"hash": w3.to_hex(tx_hash), "blockNumber": receipt["blockNumber"]}
    except Exception as e:
        print(f"  ❌ Anchor error: {e}")
        return {"hash": None, "blockNumber": None}


async def main():
    conn = await asyncpg.connect(
        host="localhost", database="moltstack",
        user="moltstack", password=os.environ.get("MOLTSTACK_DB_PW", "")
    )
    agents = await conn.fetch(
        "SELECT did, public_key_hex FROM agents WHERE public_key_hex IS NOT NULL AND key_anchor_tx IS NULL"
    )
    print(f"Anchoring {len(agents)} existing agent keys...")
    for agent in agents:
        print(f"  → {agent['did']} ({agent['public_key_hex'][:16]}...)")
        anchor = await anchor_did_public_key(agent["did"], agent["public_key_hex"])
        if anchor["hash"]:
            await conn.execute(
                "UPDATE agents SET key_anchor_tx = $1, key_anchor_block = $2 WHERE did = $3",
                anchor["hash"], anchor["blockNumber"], agent["did"]
            )
            print(f"  ✅ TX: {anchor['hash']} | Block: {anchor['blockNumber']}")
        else:
            print(f"  ❌ Failed to anchor {agent['did']}")
        await asyncio.sleep(2)
    await conn.close()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
