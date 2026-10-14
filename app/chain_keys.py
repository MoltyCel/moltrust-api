"""One accessor per purpose for the keys that sign on Base.

Until this module there were four names for two keys. `BASE_WALLET_KEY` and
`BASE_WRITE_KEY` carried the same 32 bytes under two names, `BASE_KEY` stood as
a fallback behind one of them, and six call sites read whichever name their
author happened to know (`app/main.py`, `app/erc8004.py`,
`app/provenance/anchor.py`, `scripts/anchor_existing_keys.py`). A key whose
readers cannot be listed cannot be rotated for one purpose and kept for
another, and a compromise of the loudest path takes the quietest one with it.

What a purpose is here: one answer to "whose signature is this, and what does
it cost us if it is forged". Three of them sign on Base today:

- `vc_anchor_signer` — credential and publication anchors, `MolTrust/VC/v1`.
  Own address since 2026-09, so running anchoring dry cannot stop the product.
- `ipr_anchor_signer` — interaction-proof and music anchors,
  `MolTrust/IPR/v1`.
- `registry_write_signer` — ERC-8004 writes: `register`, `setAgentWallet`,
  `giveFeedback`. Bound to the owner of the eight agent IDs, so it cannot move
  to another key without an on-chain `transferFrom`.

The last two still read the same variable. That is the open half of ops job S5,
and it is deliberately visible here rather than spread over the call sites:
the anchor sender and the registry owner are the same account today, and
separating them needs a second funded address plus a change to a published
signer table. When that is decided, it is one line in `ipr_anchor_signer`.

No fallback between purposes. A missing key raises instead of quietly signing
with the key of a neighbouring purpose — which is what a fallback is once two
purposes share a reader. Callers keep their own "not configured, skip" answer
by catching `ChainKeyNotConfigured`.

The reader map is pinned in `config/key_purposes.json` and checked by
`tests/test_key_purposes.py`: a new raw read of one of these variables fails
the required CI job.
"""

from __future__ import annotations

import os

from eth_account import Account

class ChainKeyNotConfigured(RuntimeError):
    """The key for this purpose is missing, or does not match its address."""


def _derive(env_name: str, key: str) -> tuple[str, str]:
    """Return (private key, derived address), or say which variable is wrong.

    The address comes from the key, never from a second variable: that is how
    `BASE_WALLET_ADDRESS` could sit in the config for months naming a wallet
    the key did not control (docs/WALLET_STATE.md, 2026-07-08).
    """
    if not key.strip():
        raise ChainKeyNotConfigured(f"{env_name} is not set")
    try:
        return key.strip(), Account.from_key(key.strip()).address
    except Exception as exc:  # malformed hex, wrong length
        raise ChainKeyNotConfigured(f"{env_name} is not a usable private key") from exc


def vc_anchor_signer() -> tuple[str, str]:
    """Credential and publication anchors (`MolTrust/VC/v1`).

    Read at call time, not at import: a module body defines, it does not act
    (WORKFLOW 18), and an import-time read would freeze the value of a
    variable that `systemctl restart` is supposed to change. Same below.
    """
    key, address = _derive("BASE_ANCHOR_KEY", os.getenv("BASE_ANCHOR_KEY", ""))
    declared = os.getenv("BASE_ANCHOR_ADDR", "").strip()
    if declared and declared.lower() != address.lower():
        # The declared address is what docs/spec-fakten/anchor-commitment.md
        # publishes as the VC signer. If the key stops deriving to it, the next
        # anchor would come from an address no verifier expects; stop instead.
        raise ChainKeyNotConfigured(
            f"BASE_ANCHOR_KEY derives to {address}, BASE_ANCHOR_ADDR says {declared}")
    return key, address


def ipr_anchor_signer() -> tuple[str, str]:
    """Interaction-proof, DID-key and music anchors (`MolTrust/IPR/v1`)."""
    return _derive("BASE_WRITE_KEY", os.getenv("BASE_WRITE_KEY", ""))


def registry_write_signer() -> tuple[str, str]:
    """ERC-8004 writes, signed by the owner of our agent IDs."""
    return _derive("BASE_WRITE_KEY", os.getenv("BASE_WRITE_KEY", ""))
