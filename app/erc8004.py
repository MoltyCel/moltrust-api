"""
ERC-8004 Bridge Layer — Phase 1 (Read-Only)

Provides:
- Registration file generation (ERC-8004 compatible Agent Card)
- On-chain agent resolution via Base IdentityRegistry
- Well-known agent-registration.json for domain verification
"""

from web3 import Web3
import logging

logger = logging.getLogger("moltrust.erc8004")

# --- Constants ---

BASE_RPC = "https://mainnet.base.org"
BASE_CHAIN_ID = 8453

IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
REPUTATION_REGISTRY = "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63"

AGENT_REGISTRY_ID = f"eip155:{BASE_CHAIN_ID}:{IDENTITY_REGISTRY}"

# Minimal ABI — only read functions we need for Phase 1
IDENTITY_ABI = [
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "tokenURI",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "ownerOf",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "uint256", "name": "agentId", "type": "uint256"}],
        "name": "getAgentWallet",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "agentId", "type": "uint256"},
            {"internalType": "string", "name": "metadataKey", "type": "string"}
        ],
        "name": "getMetadata",
        "outputs": [{"internalType": "bytes", "name": "", "type": "bytes"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "getVersion",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "pure",
        "type": "function"
    },
]

REPUTATION_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "agentId", "type": "uint256"},
            {"internalType": "address[]", "name": "clientAddresses", "type": "address[]"},
            {"internalType": "string", "name": "tag1", "type": "string"},
            {"internalType": "string", "name": "tag2", "type": "string"}
        ],
        "name": "getSummary",
        "outputs": [
            {"internalType": "uint64", "name": "count", "type": "uint64"},
            {"internalType": "int128", "name": "summaryValue", "type": "int128"},
            {"internalType": "uint8", "name": "summaryValueDecimals", "type": "uint8"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "uint256", "name": "agentId", "type": "uint256"}],
        "name": "getClients",
        "outputs": [{"internalType": "address[]", "name": "", "type": "address[]"}],
        "stateMutability": "view",
        "type": "function"
    },
]


# --- Web3 Setup ---

_w3 = None
_identity_contract = None
_reputation_contract = None

def _get_w3():
    global _w3
    if _w3 is None:
        _w3 = Web3(Web3.HTTPProvider(BASE_RPC))
    return _w3

def get_identity_contract():
    global _identity_contract
    if _identity_contract is None:
        w3 = _get_w3()
        _identity_contract = w3.eth.contract(
            address=Web3.to_checksum_address(IDENTITY_REGISTRY),
            abi=IDENTITY_ABI
        )
    return _identity_contract

def get_reputation_contract():
    global _reputation_contract
    if _reputation_contract is None:
        w3 = _get_w3()
        _reputation_contract = w3.eth.contract(
            address=Web3.to_checksum_address(REPUTATION_REGISTRY),
            abi=REPUTATION_ABI
        )
    return _reputation_contract


# --- Registration File Generator ---

def build_registration_file(agent: dict, reputation: dict, erc8004_agent_id: int = None) -> dict:
    """
    Build an ERC-8004 compatible registration file (Agent Card) for a MolTrust agent.
    """
    did = agent["did"]
    display_name = agent.get("display_name", "Unknown Agent")
    score = reputation.get("score", 0.0)
    total = reputation.get("total_ratings", 0)

    services = [
        {"name": "DID", "endpoint": did, "version": "v1"},
        {"name": "web", "endpoint": f"https://api.moltrust.ch/identity/resolve/{did}"},
    ]

    # If agent has base_tx_hash, they're anchored on-chain
    if agent.get("base_tx_hash"):
        services.append({
            "name": "web",
            "endpoint": f"https://basescan.org/tx/{agent['base_tx_hash']}"
        })

    registrations = []
    if erc8004_agent_id is not None:
        registrations.append({
            "agentId": erc8004_agent_id,
            "agentRegistry": AGENT_REGISTRY_ID
        })

    description = f"AI agent on MolTrust."
    if total > 0:
        description = f"AI agent on MolTrust. Trust score: {score}/5 ({total} ratings)."

    return {
        "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
        "name": display_name,
        "description": description,
        "image": "https://moltrust.ch/og-image-v3.png",
        "services": services,
        "registrations": registrations,
        "supportedTrust": ["reputation"],
        "x402Support": False,
        "active": True
    }


# --- On-Chain Resolver ---

async def resolve_onchain_agent(agent_id: int) -> dict:
    """
    Resolve an ERC-8004 agentId on Base to its registration data.
    Returns owner, wallet, tokenURI, and parsed metadata.
    """
    contract = get_identity_contract()

    try:
        import asyncio
        owner = await asyncio.to_thread(contract.functions.ownerOf(agent_id).call)
    except Exception as e:
        return {"error": f"Agent ID {agent_id} not found on Base IdentityRegistry", "detail": str(e)}

    agent_uri = ""
    try:
        agent_uri = await asyncio.to_thread(contract.functions.tokenURI(agent_id).call)
    except Exception:
        pass

    agent_wallet = "0x" + "0" * 40
    try:
        agent_wallet = await asyncio.to_thread(contract.functions.getAgentWallet(agent_id).call)
    except Exception:
        pass

    return {
        "agent_id": agent_id,
        "chain": "base",
        "chain_id": BASE_CHAIN_ID,
        "registry": IDENTITY_REGISTRY,
        "agent_registry": AGENT_REGISTRY_ID,
        "owner": owner,
        "agent_wallet": agent_wallet,
        "agent_uri": agent_uri,
    }


def get_onchain_reputation(agent_id: int, clients: list = None) -> dict:
    """
    Fetch on-chain reputation summary for an agent from the ERC-8004 Reputation Registry.
    """
    contract = get_reputation_contract()

    try:
        if not clients:
            clients = contract.functions.getClients(agent_id).call()
        if not clients:
            return {"agent_id": agent_id, "count": 0, "summary_value": 0, "decimals": 0, "clients": 0}

        count, value, decimals = contract.functions.getSummary(agent_id, clients, "", "").call()
        return {
            "agent_id": agent_id,
            "count": count,
            "summary_value": int(value),
            "decimals": int(decimals),
            "clients": len(clients)
        }
    except Exception as e:
        return {"agent_id": agent_id, "error": str(e)}


# --- Well-Known ---

MOLTRUST_PLATFORM_AGENT_ID = 33553  # Set after we register MolTrust on-chain (Phase 2)

def get_well_known_registration() -> dict:
    """
    Returns the .well-known/agent-registration.json for domain verification.
    """
    registrations = []
    if MOLTRUST_PLATFORM_AGENT_ID is not None:
        registrations.append({
            "agentId": MOLTRUST_PLATFORM_AGENT_ID,
            "agentRegistry": AGENT_REGISTRY_ID
        })
    return {"registrations": registrations}


# --- Phase 2: Write Functions ---

import os
from eth_account import Account

# Write-capable ABI entries
REPUTATION_WRITE_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "agentId", "type": "uint256"},
            {"internalType": "int128", "name": "value", "type": "int128"},
            {"internalType": "uint8", "name": "valueDecimals", "type": "uint8"},
            {"internalType": "string", "name": "tag1", "type": "string"},
            {"internalType": "string", "name": "tag2", "type": "string"},
            {"internalType": "string", "name": "endpoint", "type": "string"},
            {"internalType": "string", "name": "feedbackURI", "type": "string"},
            {"internalType": "bytes32", "name": "feedbackHash", "type": "bytes32"},
        ],
        "name": "giveFeedback",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
]

# Wallet for on-chain writes (the funded 0x380... wallet)
_WRITE_KEY = os.getenv("BASE_WRITE_KEY", "")
_WRITE_ADDR = Account.from_key(_WRITE_KEY).address if _WRITE_KEY else None

_reputation_write_contract = None

def _get_reputation_write_contract():
    global _reputation_write_contract
    if _reputation_write_contract is None:
        w3 = _get_w3()
        _reputation_write_contract = w3.eth.contract(
            address=Web3.to_checksum_address(REPUTATION_REGISTRY),
            abi=REPUTATION_ABI + REPUTATION_WRITE_ABI
        )
    return _reputation_write_contract


def post_reputation_feedback(erc8004_agent_id: int, moltrust_did: str, score: int) -> dict:
    """
    Post a MolTrust rating as an ERC-8004 feedback signal on-chain.

    Maps MolTrust 1-5 scale to ERC-8004 value (20-100):
      1 -> 20, 2 -> 40, 3 -> 60, 4 -> 80, 5 -> 100

    Args:
        erc8004_agent_id: The on-chain agentId
        moltrust_did: The MolTrust DID being rated (for endpoint reference)
        score: MolTrust rating 1-5

    Returns:
        dict with tx_hash on success, or error on failure
    """
    try:
        w3 = _get_w3()
        contract = _get_reputation_write_contract()

        erc8004_value = score * 20  # 1->20, 2->40, 3->60, 4->80, 5->100
        endpoint = f"https://api.moltrust.ch/reputation/query/{moltrust_did}"

        nonce = w3.eth.get_transaction_count(_WRITE_ADDR)
        gas_price = w3.eth.gas_price

        tx = contract.functions.giveFeedback(
            erc8004_agent_id,
            erc8004_value,     # int128 value
            0,                 # uint8 valueDecimals
            "starred",         # tag1
            "moltrust",        # tag2
            endpoint,          # endpoint
            "",                # feedbackURI (optional)
            b"\x00" * 32     # feedbackHash (optional)
        ).build_transaction({
            "from": _WRITE_ADDR,
            "nonce": nonce,
            "chainId": BASE_CHAIN_ID,
            "gas": 300000,
            "maxFeePerGas": gas_price * 3,
            "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
        })

        signed = w3.eth.account.sign_transaction(tx, _WRITE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        hex_hash = w3.to_hex(tx_hash)

        logger.info(f"ERC-8004 feedback posted: agent={erc8004_agent_id} score={score} tx={hex_hash}")
        return {"tx_hash": hex_hash, "chain": "base", "basescan": f"https://basescan.org/tx/{hex_hash}"}

    except Exception as e:
        logger.error(f"ERC-8004 feedback error: {e}")
        return {"error": str(e)}


# --- Phase 3: Dual Registration ---

IDENTITY_WRITE_ABI = [
    {
        "inputs": [
            {"internalType": "string", "name": "agentURI", "type": "string"}
        ],
        "name": "register",
        "outputs": [{"internalType": "uint256", "name": "agentId", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "agentId", "type": "uint256"},
            {"internalType": "string", "name": "_tokenURI", "type": "string"}
        ],
        "name": "setTokenURI",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
]

_identity_write_contract = None

def _get_identity_write_contract():
    global _identity_write_contract
    if _identity_write_contract is None:
        w3 = _get_w3()
        _identity_write_contract = w3.eth.contract(
            address=Web3.to_checksum_address(IDENTITY_REGISTRY),
            abi=IDENTITY_ABI + IDENTITY_WRITE_ABI
        )
    return _identity_write_contract


def register_onchain_agent(agent_did: str) -> dict:
    """
    Register a MolTrust agent on the ERC-8004 IdentityRegistry on Base.

    The agentURI points to the agent's ERC-8004 registration file on MolTrust.

    Args:
        agent_did: The MolTrust DID for the agent

    Returns:
        dict with agent_id and tx_hash on success, or error on failure
    """
    try:
        w3 = _get_w3()
        contract = _get_identity_write_contract()

        agent_uri = f"https://api.moltrust.ch/agents/{agent_did}/erc8004"

        nonce = w3.eth.get_transaction_count(_WRITE_ADDR)
        gas_price = w3.eth.gas_price

        tx = contract.functions.register(agent_uri).build_transaction({
            "from": _WRITE_ADDR,
            "nonce": nonce,
            "chainId": BASE_CHAIN_ID,
            "gas": 300000,
            "maxFeePerGas": gas_price * 3,
            "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
        })

        signed = w3.eth.account.sign_transaction(tx, _WRITE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

        if receipt.status != 1:
            return {"error": "Transaction reverted", "tx_hash": w3.to_hex(tx_hash)}

        # Parse agentId from Transfer event (topics[3] = tokenId)
        agent_id = None
        for log in receipt.logs:
            if log.address.lower() == IDENTITY_REGISTRY.lower() and len(log.topics) >= 4:
                agent_id = int(log.topics[3].hex(), 16)
                break

        hex_hash = w3.to_hex(tx_hash)
        logger.info(f"ERC-8004 registered: did={agent_did} agentId={agent_id} tx={hex_hash}")
        return {
            "agent_id": agent_id,
            "tx_hash": hex_hash,
            "chain": "base",
            "basescan": f"https://basescan.org/tx/{hex_hash}"
        }

    except Exception as e:
        logger.error(f"ERC-8004 registration error: {e}")
        return {"error": str(e)}
