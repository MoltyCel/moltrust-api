"""SKALE L2 Anchoring Support — chain-agnostic extension of Base L2 anchoring.

Anchor format (identical for all chains):
MolTrust/<event-type>/1 SHA256:<64-char-hex-hash>
"""
from app.base_rpc import base_rpc_url

# A function, not a dict. As a module-level dict this read BASE_RPC the moment
# anything imported the module — and the Base entry is the only one that needs
# reading at all; the two SKALE endpoints are public and fixed.
_FEST = {
    "skale-nebula": {
        "rpc": "https://mainnet.skalenodes.com/v1/green-giddy-denebola",
        "chain_id": 1482601649,
        "explorer": "https://nebula.explorer.skale.network",
        # sFUEL required — no monetary value, obtain via https://sfuel.skale.network/
    },
}


def chain_config(name: str) -> dict:
    """Endpoint, chain id and explorer for one chain.

    Raises KeyError for an unknown name, and BaseRpcNotConfigured for
    base-mainnet when BASE_RPC is not set.
    """
    if name == "base-mainnet":
        return {
            "rpc": base_rpc_url(),
            "chain_id": 8453,
            "explorer": "https://basescan.org",
        }
    return dict(_FEST[name])


def chain_names() -> list:
    return ["base-mainnet", *_FEST]
