from app.base_rpc import base_rpc_url
"""SKALE L2 Anchoring Support — chain-agnostic extension of Base L2 anchoring."""

CHAIN_CONFIG = {
    "base-mainnet": {
        "rpc": base_rpc_url(),
        "chain_id": 8453,
        "explorer": "https://basescan.org",
    },
    "skale-nebula": {
        "rpc": "https://mainnet.skalenodes.com/v1/green-giddy-denebola",
        "chain_id": 1482601649,
        "explorer": "https://nebula.explorer.skale.network",
        # sFUEL required — no monetary value, obtain via https://sfuel.skale.network/
    },
}

# Anchor format (identical for all chains):
# MolTrust/<event-type>/1 SHA256:<64-char-hex-hash>
