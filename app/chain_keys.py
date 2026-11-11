"""One accessor per purpose for the keys that sign on Base.

Until this module there were four names for two keys. `BASE_WALLET_KEY` and
`BASE_WRITE_KEY` carried the same 32 bytes under two names, `BASE_KEY` stood as
a fallback behind one of them, and six call sites read whichever name their
author happened to know (`app/main.py`, `app/erc8004.py`,
`app/provenance/anchor.py`, `scripts/anchor_existing_keys.py`). A key whose
readers cannot be listed cannot be rotated for one purpose and kept for
another, and a compromise of the loudest path takes the quietest one with it.

What a purpose is here: one answer to "whose signature is this, and what does
it cost us if it is forged". Four of them sign on Base today:

- `vc_anchor_signer` — credential and publication anchors, `MolTrust/VC/v1`.
  Own address since 2026-09, so running anchoring dry cannot stop the product.
- `ipr_anchor_signer` — interaction-proof and music anchors,
  `MolTrust/IPR/v1`.
- `registry_write_signer` — ERC-8004 identity writes: `register`,
  `setAgentWallet`, `setTokenURI`. Bound to the owner of the eight agent IDs,
  so it cannot move to another key without an on-chain `transferFrom`. Signed
  by a managed key (AWS KMS, `ECC_SECG_P256K1`): the process holds credentials
  that may ask for a signature, never the private key, and there is no
  fallback to `BASE_WRITE_KEY`.
- `reputation_feedback_signer` — ERC-8004 `giveFeedback`. Not an identity
  write: the feedback client is `msg.sender`, it needs no ownership, and the
  call is reached from routes any authenticated agent can hit. It stays off
  the managed owner key on purpose, so a burst of ratings spends the gas of
  the feedback address and not the gas the registry owner needs.

What is still open of ops job S5, deliberately visible here rather than spread
over the call sites: `ipr_anchor_signer` and `reputation_feedback_signer` read
the same variable. Separating them needs a second funded address plus a change
to a published signer table; when that is decided, it is one line here. The
identity half is closed: the owner of the agent IDs signs through KMS.

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

# secp256k1 group order. Ethereum accepts only the lower half of s (EIP-2); a
# KMS signature carries either half, so every one is normalised before use.
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

# The managed signer's configuration, all four required.
KMS_SIGNER_VARIABLES = ("SIGNER_AWS_ACCESS_KEY_ID", "SIGNER_AWS_SECRET_ACCESS_KEY",
                        "SIGNER_AWS_REGION", "SIGNER_KMS_KEY_ID")
# Optional. When set, the address derived from the KMS public key must equal it.
KMS_SIGNER_DECLARED_ADDRESS = "SIGNER_ADDRESS"


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


def reputation_feedback_signer() -> tuple[str, str]:
    """ERC-8004 `giveFeedback`, signed by the feedback client address."""
    return _derive("BASE_WRITE_KEY", os.getenv("BASE_WRITE_KEY", ""))


class KmsSigner:
    """A secp256k1 signer whose private key never leaves AWS KMS.

    The address is derived from the public key KMS returns, never taken from a
    variable (same rule as `_derive`). Every signature is checked to recover to
    that address before it is handed out, so a key id that names another key
    fails here and not on chain.
    """

    def __init__(self, client, key_id: str):
        self._kms = client
        self._key_id = key_id
        self.address = self._address_from_kms()

    def _address_from_kms(self) -> str:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
            load_der_public_key,
        )
        from eth_utils import keccak, to_checksum_address

        der = self._kms.get_public_key(KeyId=self._key_id)["PublicKey"]
        pub = load_der_public_key(der)
        if not isinstance(pub, ec.EllipticCurvePublicKey) or pub.curve.name != "secp256k1":
            raise ChainKeyNotConfigured("SIGNER_KMS_KEY_ID does not name a secp256k1 key")
        raw = pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        return to_checksum_address(keccak(raw[1:])[-20:])

    def sign_digest(self, digest: bytes) -> tuple[int, int, int]:
        """Return (y_parity, r, s) over a 32-byte digest, with s in the lower half.

        Serves a transaction hash and an EIP-712 digest alike: KMS signs the
        bytes as given (MessageType DIGEST) and hashes nothing itself.
        """
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        from eth_keys import keys
        from eth_utils import to_checksum_address

        if len(digest) != 32:
            raise ValueError("digest must be 32 bytes")
        der = self._kms.sign(KeyId=self._key_id, Message=digest, MessageType="DIGEST",
                             SigningAlgorithm="ECDSA_SHA_256")["Signature"]
        r, s = decode_dss_signature(der)
        if s > SECP256K1_N // 2:
            s = SECP256K1_N - s
        for v in (0, 1):
            pk = keys.Signature(vrs=(v, r, s)).recover_public_key_from_msg_hash(digest)
            if to_checksum_address(pk.to_canonical_address()) == self.address:
                return v, r, s
        raise ChainKeyNotConfigured("KMS signature does not recover to the signer address")

    def sign_transaction(self, tx: dict) -> bytes:
        """Sign an EIP-1559 transaction dict and return the raw bytes to broadcast."""
        from eth_account.typed_transactions import TypedTransaction

        tx = dict(tx)
        sender = tx.pop("from", None)
        if sender is not None and sender.lower() != self.address.lower():
            raise ValueError(f"transaction is from {sender}, signer is {self.address}")
        tx.setdefault("type", 2)
        v, r, s = self.sign_digest(TypedTransaction.from_dict(tx).hash())
        raw = TypedTransaction.from_dict({**tx, "v": v, "r": r, "s": s}).encode()
        if Account.recover_transaction(raw) != self.address:
            raise ChainKeyNotConfigured("signed transaction does not recover to the signer address")
        return raw


def registry_write_signer() -> KmsSigner:
    """ERC-8004 identity writes, signed by the owner of our agent IDs.

    A managed key, not a private key, and no `BASE_WRITE_KEY` fallback: a
    registration signed by the neighbouring purpose would mint the new agent ID
    to an address that is not the registry owner.
    """
    # Spelled out, not looped over KMS_SIGNER_VARIABLES: scripts/key_purposes_check.py
    # finds readers by their literal form, and a loop would hide all four.
    values = {
        "SIGNER_AWS_ACCESS_KEY_ID": os.getenv("SIGNER_AWS_ACCESS_KEY_ID", "").strip(),
        "SIGNER_AWS_SECRET_ACCESS_KEY": os.getenv("SIGNER_AWS_SECRET_ACCESS_KEY", "").strip(),
        "SIGNER_AWS_REGION": os.getenv("SIGNER_AWS_REGION", "").strip(),
        "SIGNER_KMS_KEY_ID": os.getenv("SIGNER_KMS_KEY_ID", "").strip(),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ChainKeyNotConfigured(f"{', '.join(missing)} not set")
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise ChainKeyNotConfigured("boto3 is not installed") from exc
    client = boto3.client(
        "kms",
        aws_access_key_id=values["SIGNER_AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=values["SIGNER_AWS_SECRET_ACCESS_KEY"],
        region_name=values["SIGNER_AWS_REGION"],
        config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 3}),
    )
    try:
        signer = KmsSigner(client, values["SIGNER_KMS_KEY_ID"])
    except ChainKeyNotConfigured:
        raise
    except Exception as exc:  # credentials refused, unknown key, network
        raise ChainKeyNotConfigured(f"KMS public key unavailable ({type(exc).__name__})") from exc
    declared = os.getenv("SIGNER_ADDRESS", "").strip()
    if declared and declared.lower() != signer.address.lower():
        raise ChainKeyNotConfigured(
            f"KMS key derives to {signer.address}, {KMS_SIGNER_DECLARED_ADDRESS} says {declared}")
    return signer
