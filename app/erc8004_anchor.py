"""Metadata anchor on the ERC-8004 IDs MolTrust mints (BIND-CHECK 3).

An ID MolTrust mints belongs to the registry signer, and its agentURI points at
MolTrust. The registry also offers per-token key/value metadata
(`setMetadata`/`getMetadata`, ERC-8004 "On-chain metadata"). This module writes
and reads one entry on those IDs that names the did:moltrust the ID belongs to,
so the link can be read from the chain alone, without the agentURI and without
asking MolTrust.

Key: "moltrust:did". Namespaced, because the statement is MolTrust's (the
owner writes it), not the agent's own claim; a bare "did" would read as the
latter to a third party, and the namespace keeps it clear of keys another
implementation defines. "agentWallet" is the only reserved key.

Value: the DID as UTF-8 bytes, no ABI encoding and no hash. Anyone decoding the
returned bytes as text reads the DID, and it equals the `DID` service endpoint
of the registration file (app/erc8004.py build_registration_file).

Sending is gated: `send_anchor` refuses unless BIND_CHECK_ANCHOR_ARMED=1 is set
in the environment AND the caller passes armed=True. Nothing in the request
path calls it. The planner (`python -m app.erc8004_anchor plan`) only reads:
the IDs, the signer, eth_estimateGas and the current fee data.
"""
from __future__ import annotations

import os
from typing import Callable, Optional

METADATA_KEY = "moltrust:did"

MATCH = "match"
MISSING = "missing"
MISMATCH = "mismatch"

METADATA_WRITE_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "agentId", "type": "uint256"},
            {"internalType": "string", "name": "metadataKey", "type": "string"},
            {"internalType": "bytes", "name": "metadataValue", "type": "bytes"},
        ],
        "name": "setMetadata",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

# OP-stack predeploy on Base: the L1 data fee is charged on top of L2 gas.
GAS_PRICE_ORACLE = "0x420000000000000000000000000000000000000F"
GAS_PRICE_ORACLE_ABI = [
    {"inputs": [{"internalType": "uint256", "name": "_unsignedTxSize", "type": "uint256"}],
     "name": "getL1FeeUpperBound", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]


class AnchorNotArmed(RuntimeError):
    """Sending was requested without both arming conditions."""


def anchor_value(did: str) -> bytes:
    if not isinstance(did, str) or not did.startswith("did:moltrust:"):
        raise ValueError("anchor value must be a did:moltrust")
    return did.encode("utf-8")


def check_anchor(stored: Optional[bytes], did: str) -> str:
    """MATCH when the stored bytes are exactly the DID, MISSING when empty, else MISMATCH."""
    if not stored:
        return MISSING
    return MATCH if bytes(stored) == anchor_value(did) else MISMATCH


def _contract(abi_extra=()):
    from web3 import Web3
    from app.erc8004 import IDENTITY_ABI, IDENTITY_REGISTRY, _get_w3
    return _get_w3().eth.contract(address=Web3.to_checksum_address(IDENTITY_REGISTRY),
                                  abi=IDENTITY_ABI + list(abi_extra))


def read_anchor(agent_id: int) -> bytes:
    """getMetadata(agentId, METADATA_KEY), read only."""
    return bytes(_contract().functions.getMetadata(int(agent_id), METADATA_KEY).call())


def verify_anchor(agent_id: int, did: str, read: Callable[[int], bytes] = read_anchor) -> str:
    return check_anchor(read(agent_id), did)


def build_anchor_call(agent_id: int, did: str):
    """The contract call, unsigned. Used by the planner and by send_anchor."""
    return _contract(METADATA_WRITE_ABI).functions.setMetadata(int(agent_id), METADATA_KEY, anchor_value(did))


def send_anchor(agent_id: int, did: str, *, armed: bool = False) -> dict:
    """Sign with the registry signer (owner of the ID) and send. Gated twice."""
    if not armed or os.getenv("BIND_CHECK_ANCHOR_ARMED") != "1":
        raise AnchorNotArmed("anchor sending needs BIND_CHECK_ANCHOR_ARMED=1 and armed=True")
    from app.chain_keys import registry_write_signer
    from app.erc8004 import BASE_CHAIN_ID, _get_w3
    signer = registry_write_signer()
    w3 = _get_w3()
    call = build_anchor_call(agent_id, did)
    gas = call.estimate_gas({"from": signer.address})
    gas_price = w3.eth.gas_price
    tx = call.build_transaction({
        "from": signer.address,
        "nonce": w3.eth.get_transaction_count(signer.address, "pending"),
        "chainId": BASE_CHAIN_ID,
        "gas": int(gas * 1.2),
        "maxFeePerGas": gas_price * 3,
        "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
    })
    tx_hash = w3.eth.send_raw_transaction(signer.sign_transaction(tx))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return {"agent_id": agent_id, "tx_hash": w3.to_hex(tx_hash), "status": receipt.status,
            "gas_used": receipt.gasUsed}


async def self_minted_links(conn, owner_of: Callable[[int], str], registry_owner: str) -> list:
    """Linked (agent_id, did) whose token the registry signer owns, i.e. the IDs MolTrust minted."""
    import asyncio
    rows = await conn.fetch(
        "SELECT erc8004_agent_id AS agent_id, did, platform FROM agents "
        "WHERE erc8004_agent_id IS NOT NULL ORDER BY erc8004_agent_id")
    out = []
    for r in rows:
        owner = (await asyncio.to_thread(owner_of, r["agent_id"])).lower()
        if owner == registry_owner.lower():
            out.append((int(r["agent_id"]), r["did"], r["platform"]))
    return out


async def _plan() -> int:
    """Read-only cost plan: IDs, signer, gas estimate, fee data. Sends nothing."""
    import asyncpg
    import datetime
    from app.chain_keys import registry_write_signer
    from app.erc8004 import _get_w3, get_identity_contract
    w3 = _get_w3()
    signer = registry_write_signer()  # derives the address from KMS; signs nothing
    conn = await asyncpg.connect(host=os.getenv("DB_HOST", "localhost"), user="moltstack",
                                 database=os.getenv("DB_NAME", "moltstack"),
                                 password=os.getenv("MOLTSTACK_DB_PW"),
                                 server_settings={"default_transaction_read_only": "on"})
    try:
        ids = await self_minted_links(conn, lambda a: get_identity_contract().functions.ownerOf(a).call(),
                                      signer.address)
    finally:
        await conn.close()
    oracle = w3.eth.contract(address=GAS_PRICE_ORACLE, abi=GAS_PRICE_ORACLE_ABI)
    block = w3.eth.get_block("latest")
    base_fee = block.get("baseFeePerGas", 0)
    gas_price = w3.eth.gas_price
    print(f"at {datetime.datetime.now(datetime.UTC).isoformat()} block {block['number']}")
    print(f"signer {signer.address}")
    print(f"gas_price_wei {gas_price} base_fee_wei {base_fee}")
    total_wei = 0
    for agent_id, did, platform in ids:
        call = build_anchor_call(agent_id, did)
        gas = call.estimate_gas({"from": signer.address})
        data = call._encode_transaction_data()
        l1 = oracle.functions.getL1FeeUpperBound(len(bytes.fromhex(data[2:])) + 68).call()
        current = read_anchor(agent_id)
        state = check_anchor(current, did)
        fee = gas * gas_price + l1
        total_wei += fee
        print(f"id {agent_id} platform {platform} anchor_now {state} gas {gas} l1_upper_wei {l1} fee_wei {fee}")
    print(f"tx {len(ids)} total_wei {total_wei} total_eth {total_wei / 1e18:.10f}")
    return 0


if __name__ == "__main__":
    import asyncio
    import sys
    if sys.argv[1:] != ["plan"]:
        print("usage: python -m app.erc8004_anchor plan   (read only; sending is not offered here)")
        sys.exit(2)
    sys.exit(asyncio.run(_plan()))
