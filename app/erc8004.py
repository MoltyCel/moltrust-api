"""
ERC-8004 Bridge Layer — Phase 1 (Read-Only)

Provides:
- Registration file generation (ERC-8004 compatible Agent Card)
- On-chain agent resolution via Base IdentityRegistry
- Well-known agent-registration.json for domain verification
"""

from web3 import Web3
import httpx
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

    # Both identifiers for the same agent, so a reader arriving from either
    # side can reach the other without a lookup table. The DID is the MolTrust
    # name; the CAIP-style string is the on-chain one. Only the identifiers we
    # actually hold go in — an empty or half-built entry is worse than none,
    # because a consumer cannot tell a missing link from a broken one.
    also_known_as = [did]
    if erc8004_agent_id is not None:
        also_known_as.append(f"{AGENT_REGISTRY_ID}:{erc8004_agent_id}")

    return {
        "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
        "name": display_name,
        "description": description,
        "image": "https://moltrust.ch/og-image-v3.png",
        "alsoKnownAs": also_known_as,
        "services": services,
        "registrations": registrations,
        "supportedTrust": ["reputation"],
        "x402Support": False,
        "active": True
    }


# --- On-Chain Resolver ---

# The public Base endpoint rate-limits, and a 429 is not an answer about the
# agent. Two tries with a short pause cover the burst; a third would be us
# leaning on an endpoint that has already said no.
RPC_ATTEMPTS = 2
RPC_BACKOFF_SECONDS = 0.6

# web3 wraps transport failures in its own exception types and sometimes passes
# the requests error straight through, so this reads the text rather than
# guessing at a class hierarchy that changes between releases.
_TRANSPORT_MARKERS = (
    "429", "too many requests", "rate limit",
    "timeout", "timed out", "connection", "temporarily unavailable",
    "502", "503", "504", "bad gateway", "service unavailable",
)


def _is_transport_error(exc: Exception) -> bool:
    """Did the chain answer, or did we never reach it?

    A revert or a nonexistent token is an answer: the agent is not registered.
    A 429, a timeout or a dropped connection is not, and reporting it as
    absence is how a rate limit turned into "this agent does not exist".
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _TRANSPORT_MARKERS)


async def resolve_onchain_agent(agent_id: int) -> dict:
    """
    Resolve an ERC-8004 agentId on Base to its registration data.
    Returns owner, wallet, tokenURI, and parsed metadata.
    """
    import asyncio

    contract = get_identity_contract()

    # An agent that is not registered and an RPC that will not answer are two
    # different findings, and this returned the first for both. BASE_RPC is the
    # public endpoint and rate-limits: on 2026-09-22 one call in three to
    # /resolve/erc8004/21351 came back 404 for an agent that has been on chain
    # since registration, because `ownerOf` had raised 429. A caller reading
    # that has been told the agent does not exist.
    owner = None
    last_transport_error = None
    for attempt in range(RPC_ATTEMPTS):
        try:
            owner = await asyncio.to_thread(contract.functions.ownerOf(agent_id).call)
            break
        except Exception as exc:
            if not _is_transport_error(exc):
                # The contract answered, and the answer is that there is no
                # such token. That is a real absence.
                return {"error": f"Agent ID {agent_id} not found on Base IdentityRegistry",
                        "absent": True, "detail": str(exc)}
            last_transport_error = exc
            if attempt + 1 < RPC_ATTEMPTS:
                await asyncio.sleep(RPC_BACKOFF_SECONDS * (attempt + 1))
    if owner is None:
        return {"error": f"Base RPC did not answer for agent ID {agent_id}",
                "unavailable": True, "detail": str(last_transport_error)}

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

# The platform holds two agent IDs for the same identity,
# did:web:api.moltrust.ch. 21023 is the canonical one; 33553 stays registered
# and points at it.
#
# Before this, token 21023's own registration file declared agentId 33553 — a
# different token — so anyone resolving 21023 got a number that did not match
# the token in their hand. One identity may carry two registrations; it may not
# disagree with itself about which one it is.
MOLTRUST_PLATFORM_AGENT_ID = 21023
MOLTRUST_PLATFORM_SECONDARY_AGENT_IDS = [33553]

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

        # "pending" (not "latest") so back-to-back txs do not reuse a nonce and
        # get dropped/replaced (v0.8.1 nonce-race lesson; see PR #148 / anchor.py).
        nonce = w3.eth.get_transaction_count(_WRITE_ADDR, "pending")
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

        # "pending" (not "latest") so back-to-back txs do not reuse a nonce and
        # get dropped/replaced (v0.8.1 nonce-race lesson; see PR #148 / anchor.py).
        nonce = w3.eth.get_transaction_count(_WRITE_ADDR, "pending")
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


# --- Onboarding auto-link (D) ---

# Two seconds for the whole lookup, chain call included. Registration is the
# path a new agent meets first; it does not get slower because a block explorer
# is having a bad afternoon.
AUTOLINK_BUDGET_SECONDS = 2.0


async def find_existing_agent_id(wallet: str, budget: float = AUTOLINK_BUDGET_SECONDS):
    """Return an ERC-8004 agentId already held by `wallet`, or None.

    Fail-open by construction: every failure path returns None, which leaves the
    agent registered and unlinked. An unlinked agent can be linked later; a
    failed registration is lost.

    The chain offers no wallet -> tokenId index, so the candidate comes from
    Blockscout and is then confirmed with ownerOf against Base. Blockscout alone
    is not enough: its NFT instance list was observed on 2026-09-18 returning
    three tokens for an address whose balance it reported as four. The transfer
    feed was complete, and ownerOf is what decides.
    """
    import asyncio
    import time

    deadline = time.monotonic() + budget

    def _left() -> float:
        return max(0.0, deadline - time.monotonic())

    try:
        url = (f"https://base.blockscout.com/api/v2/addresses/{wallet}"
               f"/token-transfers?token={IDENTITY_REGISTRY}")
        async with httpx.AsyncClient(timeout=_left()) as client:
            r = await client.get(url, headers={"User-Agent": "MolTrust-autolink/1.0"})
            if r.status_code != 200:
                return None
            items = r.json().get("items", [])
    except Exception:
        return None

    # Mints to this wallet, newest first. A transfer away is ignored here
    # because ownerOf settles it below.
    candidates = []
    for it in items:
        tid = (it.get("total") or {}).get("token_id") or it.get("token_id")
        to = ((it.get("to") or {}).get("hash") or "").lower()
        if tid and to == wallet.lower():
            try:
                candidates.append(int(tid))
            except (TypeError, ValueError):
                continue

    for agent_id in candidates:
        if _left() <= 0:
            return None
        try:
            contract = get_identity_contract()
            owner = await asyncio.wait_for(
                asyncio.to_thread(contract.functions.ownerOf(agent_id).call),
                timeout=_left(),
            )
        except Exception:
            return None
        if owner and owner.lower() == wallet.lower():
            return agent_id
    return None
